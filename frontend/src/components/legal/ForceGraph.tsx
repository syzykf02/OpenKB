import { useEffect, useRef, useState } from 'react'
import * as d3 from 'd3'
import type { GraphNode, LegalGraphEdge } from '@/api/legal'
import { graphNodeStyle } from './graphNodeStyle'

/** d3 simulation node: graph node + force-layout position fields. */
type SimNode = GraphNode & d3.SimulationNodeDatum
/** d3 simulation link: edge endpoints get resolved to SimNode refs by forceLink. */
type SimLink = d3.SimulationLinkDatum<SimNode> & { relation_type: string; edge_id: string }

// Precise selection types held in refs (the joined group's parent is <g>).
type NodeSel = d3.Selection<SVGGElement, SimNode, SVGGElement, unknown>
type LinkSel = d3.Selection<SVGLineElement, SimLink, SVGGElement, unknown>
type LinkLabelSel = d3.Selection<SVGTextElement, SimLink, SVGGElement, unknown>

const trunc = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + '…' : s)

const W = 900
const H = 560

/** Interactive d3 force-directed graph for the legal knowledge graph.
 *
 *  Replaces the old static mermaid `flowchart LR`: nodes are draggable, the
 *  canvas pans/zooms, clicking a node selects it (wired to the parent's
 *  impact analysis), and hovering a node dims unrelated nodes/edges and
 *  surfaces the relation labels on its incident edges. Node radius scales
 *  with degree so hubs stand out. */
export function ForceGraph({
  nodes,
  edges,
  selectedId,
  onSelect,
}: {
  nodes: GraphNode[]
  edges: LegalGraphEdge[]
  selectedId?: string | null
  onSelect: (n: GraphNode | null) => void
}) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [hovered, setHovered] = useState<string | null>(null)
  // Keep latest onSelect without re-running the layout effect.
  const onSelectRef = useRef(onSelect)
  useEffect(() => {
    onSelectRef.current = onSelect
  }, [onSelect])

  const neighborsRef = useRef<Map<string, Set<string>>>(new Map())
  const nodeSelRef = useRef<NodeSel | null>(null)
  const linkSelRef = useRef<LinkSel | null>(null)
  const linkLabelSelRef = useRef<LinkLabelSel | null>(null)

  // (Re)build the simulation whenever the filtered node/edge set changes.
  useEffect(() => {
    const svgEl = svgRef.current
    if (!svgEl) return
    const svg = d3.select(svgEl)
    svg.selectAll('*').remove()

    const defs = svg.append('defs')
    defs
      .append('marker')
      .attr('id', 'arrow')
      .attr('viewBox', '0 -5 10 10')
      .attr('refX', 14)
      .attr('refY', 0)
      .attr('markerWidth', 5)
      .attr('markerHeight', 5)
      .attr('orient', 'auto')
      .append('path')
      .attr('d', 'M0,-4L8,0L0,4')
      .attr('fill', '#94a3b8')

    const g = svg.append('g')
    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.2, 4])
      .on('zoom', (ev) => {
        g.attr('transform', ev.transform.toString())
      })
    svg.call(zoom)

    const simNodes: SimNode[] = nodes.map((n) => ({ ...n }))
    const idMap = new Map(simNodes.map((n) => [n.node_id, n] as const))
    const simLinks: SimLink[] = edges
      .filter((e) => idMap.has(e.source_id) && idMap.has(e.target_id))
      .map((e) => ({
        source: e.source_id,
        target: e.target_id,
        relation_type: e.relation_type,
        edge_id: e.edge_id,
      }))

    // Adjacency for hover/selection highlighting.
    const nb = new Map<string, Set<string>>()
    const link = (a: string, b: string) => {
      if (!nb.has(a)) nb.set(a, new Set())
      nb.get(a)!.add(b)
    }
    simLinks.forEach((l) => {
      const s = typeof l.source === 'string' ? l.source : (l.source as SimNode).node_id
      const t = typeof l.target === 'string' ? l.target : (l.target as SimNode).node_id
      link(s, t)
      link(t, s)
    })
    neighborsRef.current = nb

    const radius = (id: string) => 7 + Math.sqrt(nb.get(id)?.size ?? 0) * 3.5

    // d3's selectAll/join generics don't round-trip to a precise element type,
    // so each selection is double-asserted to the type the refs hold. The datum
    // type is still bound via .data(), so callbacks stay typed.
    const linkSel = g
      .append('g')
      .attr('class', 'links')
      .selectAll<SVGLineElement, SimLink>('line')
      .data(simLinks)
      .join('line')
      .attr('stroke', '#94a3b8')
      .attr('stroke-opacity', 0.35)
      .attr('stroke-width', 1)
      .attr('marker-end', 'url(#arrow)') as unknown as LinkSel
    linkSelRef.current = linkSel

    const linkLabelSel = g
      .append('g')
      .attr('class', 'link-labels')
      .selectAll<SVGTextElement, SimLink>('text')
      .data(simLinks)
      .join('text')
      .attr('font-size', 8)
      .attr('fill', 'currentColor')
      .attr('fill-opacity', 0)
      .attr('text-anchor', 'middle')
      .attr('dy', -3)
      .text((d: SimLink) => d.relation_type) as unknown as LinkLabelSel
    linkLabelSelRef.current = linkLabelSel

    const nodeSel = g
      .append('g')
      .attr('class', 'nodes')
      .selectAll<SVGGElement, SimNode>('g')
      .data(simNodes)
      .join('g')
      .style('cursor', 'pointer') as unknown as NodeSel
    nodeSelRef.current = nodeSel

    nodeSel.call(
      d3
        .drag<SVGGElement, SimNode>()
        .on('start', (ev, d) => {
          if (!ev.active) sim.alphaTarget(0.3).restart()
          d.fx = d.x
          d.fy = d.y
        })
        .on('drag', (ev, d) => {
          d.fx = ev.x
          d.fy = ev.y
        })
        .on('end', (ev, d) => {
          if (!ev.active) sim.alphaTarget(0)
          d.fx = null
          d.fy = null
        }),
    )

    nodeSel
      .append('circle')
      .attr('r', (d: SimNode) => radius(d.node_id))
      .attr('fill', (d: SimNode) => graphNodeStyle(d.node_type).fill)
      .attr('stroke', (d: SimNode) => graphNodeStyle(d.node_type).stroke)
      .attr('stroke-width', 1.5)

    nodeSel
      .append('text')
      .attr('dy', (d: SimNode) => radius(d.node_id) + 12)
      .attr('text-anchor', 'middle')
      .attr('font-size', 10)
      .attr('fill', 'currentColor')
      .text((d: SimNode) => trunc(d.label, 18))

    nodeSel
      .on('click', (ev, d: SimNode) => {
        ev.stopPropagation()
        onSelectRef.current(d)
      })
      .on('mouseenter', (_ev, d: SimNode) => setHovered(d.node_id))
      .on('mouseleave', () => setHovered(null))

    // Click empty canvas to deselect.
    svg.on('click', () => onSelectRef.current(null))

    const sim = d3
      .forceSimulation(simNodes)
      .force(
        'link',
        d3
          .forceLink<SimNode, SimLink>(simLinks)
          .id((d) => d.node_id)
          .distance(300)
          .strength(0.2),
      )
      .force('charge', d3.forceManyBody().strength(-360))
      .force('center', d3.forceCenter(W / 2, H / 2))
      .force('collide', d3.forceCollide<SimNode>().radius((d) => radius(d.node_id) + 12))

    sim.on('tick', () => {
      linkSel
        .attr('x1', (d: SimLink) => (d.source as SimNode).x!)
        .attr('y1', (d: SimLink) => (d.source as SimNode).y!)
        .attr('x2', (d: SimLink) => (d.target as SimNode).x!)
        .attr('y2', (d: SimLink) => (d.target as SimNode).y!)
      linkLabelSel
        .attr('x', (d: SimLink) => ((d.source as SimNode).x! + (d.target as SimNode).x!) / 2)
        .attr('y', (d: SimLink) => ((d.source as SimNode).y! + (d.target as SimNode).y!) / 2)
      nodeSel.attr('transform', (d: SimNode) => `translate(${d.x},${d.y})`)
    })

    return () => {
      sim.stop()
      svg.on('.zoom', null)
    }
  }, [nodes, edges])

  // Highlight on hover/selection: dim unrelated nodes, emphasize incident edges
  // and reveal their relation labels.
  useEffect(() => {
    const nodeSel = nodeSelRef.current
    const linkSel = linkSelRef.current
    const linkLabelSel = linkLabelSelRef.current
    const active = hovered ?? selectedId ?? null
    const nb = neighborsRef.current

    if (!active) {
      nodeSel?.attr('opacity', 1).select('circle').attr('stroke-width', 1.5)
      linkSel?.attr('stroke-opacity', 0.35)
      linkLabelSel?.attr('fill-opacity', 0)
      return
    }
    const related = (id: string) => id === active || nb.get(active)?.has(id) === true
    nodeSel
      ?.attr('opacity', (d: SimNode) => (related(d.node_id) ? 1 : 0.2))
      .select('circle')
      .attr('stroke-width', (d: SimNode) => (d.node_id === active ? 3 : 1.5))
    linkSel?.attr('stroke-opacity', (d: SimLink) => {
      const s = typeof d.source === 'string' ? d.source : (d.source as SimNode).node_id
      const t = typeof d.target === 'string' ? d.target : (d.target as SimNode).node_id
      return s === active || t === active ? 0.9 : 0.08
    })
    linkLabelSel?.attr('fill-opacity', (d: SimLink) => {
      const s = typeof d.source === 'string' ? d.source : (d.source as SimNode).node_id
      const t = typeof d.target === 'string' ? d.target : (d.target as SimNode).node_id
      return s === active || t === active ? 0.85 : 0
    })
  }, [hovered, selectedId])

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="xMidYMid meet"
      className="h-[52vh] min-h-[340px] w-full text-foreground"
    />
  )
}

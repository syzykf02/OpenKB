import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  getLegalGraph,
  getGraphImpact,
  type GraphNode,
  type LegalGraphEdge,
  type LegalGraphData,
  type ImpactNode,
} from '@/api/legal'
import { ApiError } from '@/api/client'
import { ForceGraph } from './ForceGraph'
import { graphNodeStyle } from './graphNodeStyle'

/** Legal knowledge graph: mermaid visualization + filters + node detail/impact (UI §3.1). */
export default function LegalGraphView({ kb }: { kb: string }) {
  const { t } = useTranslation('legal')
  const [data, setData] = useState<LegalGraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [typeFilter, setTypeFilter] = useState('')
  const [relFilter, setRelFilter] = useState('')
  const [selected, setSelected] = useState<GraphNode | null>(null)
  const [impact, setImpact] = useState<ImpactNode[] | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    getLegalGraph(kb)
      .then((r) => {
        if (!cancelled) setData(r)
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ApiError ? e.message : String(e))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [kb])

  useEffect(() => {
    if (!selected) {
      setImpact(null)
      return
    }
    let cancelled = false
    getGraphImpact(kb, selected.node_id)
      .then((r) => {
        if (!cancelled) setImpact(r.affected)
      })
      .catch(() => {
        if (!cancelled) setImpact([])
      })
    return () => {
      cancelled = true
    }
  }, [kb, selected])

  const types = useMemo(
    () => (data ? Array.from(new Set(data.nodes.map((n) => n.node_type))).sort() : []),
    [data],
  )
  const rels = useMemo(
    () => (data ? Array.from(new Set(data.edges.map((e) => e.relation_type))).sort() : []),
    [data],
  )

  // Filtered node/edge set for the force graph: edges by relation type, nodes
  // by participation in a filtered edge (or an explicit type filter).
  const filtered = useMemo(() => {
    if (!data) return { nodes: [] as GraphNode[], edges: [] as LegalGraphEdge[] }
    const edges = data.edges.filter((e) => !relFilter || e.relation_type === relFilter)
    const edgeNodeIds = new Set<string>()
    edges.forEach((e) => {
      edgeNodeIds.add(e.source_id)
      edgeNodeIds.add(e.target_id)
    })
    const visible = data.nodes.filter(
      (n) => edgeNodeIds.has(n.node_id) || (!!typeFilter && n.node_type === typeFilter),
    )
    const visibleIds = new Set(visible.map((n) => n.node_id))
    const finalEdges = edges.filter((e) => visibleIds.has(e.source_id) && visibleIds.has(e.target_id))
    return { nodes: visible, edges: finalEdges }
  }, [data, typeFilter, relFilter])

  return (
    <div className="space-y-3">
      <div className="rounded-apple-md glass-2 border border-[hsl(var(--glass-border))] p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold">{t('graph.title')}</h3>
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="ml-auto rounded-md border border-[hsl(var(--glass-border))] bg-transparent px-2 py-1 text-xs"
          >
            <option value="">{t('graph.allTypes')}</option>
            {types.map((tp) => (
              <option key={tp} value={tp}>
                {tp}
              </option>
            ))}
          </select>
          <select
            value={relFilter}
            onChange={(e) => setRelFilter(e.target.value)}
            className="rounded-md border border-[hsl(var(--glass-border))] bg-transparent px-2 py-1 text-xs"
          >
            <option value="">{t('graph.allRelations')}</option>
            {rels.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </div>
        {loading ? (
          <p className="py-6 text-center text-xs text-muted-foreground">…</p>
        ) : error ? (
          <p className="py-6 text-center text-xs text-red-500">{error}</p>
        ) : !data || data.nodes.length === 0 ? (
          <p className="py-6 text-center text-xs text-muted-foreground">{t('graph.empty')}</p>
        ) : filtered.nodes.length === 0 ? (
          <p className="py-6 text-center text-xs text-muted-foreground">{t('graph.empty')}</p>
        ) : (
          <ForceGraph
            nodes={filtered.nodes}
            edges={filtered.edges}
            selectedId={selected?.node_id}
            onSelect={(n) => setSelected(n)}
          />
        )}
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div className="min-h-0 rounded-apple-md glass-2 border border-[hsl(var(--glass-border))] p-3">
          <h4 className="mb-1 text-xs font-semibold uppercase text-muted-foreground">{t('graph.nodes')}</h4>
          {!data ? null : data.nodes.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t('graph.empty')}</p>
          ) : (
            <ul className="max-h-[40vh] space-y-1 overflow-y-auto">
              {data.nodes.map((n) => (
                <li key={n.node_id}>
                  <button
                    onClick={() => setSelected(n)}
                    className={`w-full rounded-md px-2 py-1.5 text-left text-xs transition hover:bg-[hsl(var(--glass-hover))] ${selected?.node_id === n.node_id ? 'bg-[hsl(var(--glass-hover))]' : ''}`}
                  >
                    <span className="font-medium">{n.label}</span>
                    <span className={`ml-2 rounded px-1.5 py-0.5 text-[10px] font-medium ${graphNodeStyle(n.node_type).badgeClass}`}>
                      {n.node_type}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="min-h-0 rounded-apple-md glass-2 border border-[hsl(var(--glass-border))] p-3">
          {selected ? (
            <>
              <h3 className="text-sm font-semibold">{selected.label}</h3>
              <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${graphNodeStyle(selected.node_type).badgeClass}`}>
                  {selected.node_type}
                </span>
                {selected.description && <span>· {selected.description}</span>}
                {selected.authority_level ? ` · ${selected.authority_level}` : ''}
              </p>
              <h4 className="mt-3 text-xs font-semibold uppercase text-muted-foreground">
                {t('graph.impact')}
              </h4>
              {impact === null ? (
                <p className="text-xs">…</p>
              ) : impact.length === 0 ? (
                <p className="text-xs text-muted-foreground">{t('graph.noImpact')}</p>
              ) : (
                <ul className="mt-1 max-h-[32vh] space-y-1 overflow-y-auto overscroll-contain pr-1">
                  {impact.map((r) => (
                    <li
                      key={r.node.node_id}
                      className="rounded-md bg-[hsl(var(--glass-hover))] px-2 py-1 text-xs"
                    >
                      <span className="font-medium">{r.node.label}</span>
                      <span className={`ml-2 rounded px-1.5 py-0.5 text-[10px] font-medium ${graphNodeStyle(r.node.node_type).badgeClass}`}>
                        {r.node.node_type}
                      </span>
                      <span className="ml-1 text-[10px] text-muted-foreground">
                        · depth {r.depth}
                        {r.via.length ? ` · ${r.via.join('→')}` : ''}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          ) : (
            <p className="text-xs text-muted-foreground">{t('graph.selectHint')}</p>
          )}
        </div>
      </div>
    </div>
  )
}

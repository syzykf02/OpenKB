import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  getLegalGraph,
  getGraphImpact,
  type GraphNode,
  type LegalGraphData,
  type ImpactNode,
} from '@/api/legal'
import { ApiError } from '@/api/client'

// Per node_type mermaid classDef colors (UI_INTEGRATION_PLAN §3.1.2 node coloring).
const TYPE_COLORS: Record<string, string> = {
  statute: 'fill:#dbeafe,stroke:#2563eb,color:#1e3a8a',
  regulation: 'fill:#e0e7ff,stroke:#4f46e5,color:#312e81',
  case: 'fill:#dcfce7,stroke:#16a34a,color:#14532d',
  concept: 'fill:#f3e8ff,stroke:#9333ea,color:#581c87',
  court: 'fill:#ffedd5,stroke:#ea580c,color:#7c2d12',
  doctrine: 'fill:#cffafe,stroke:#0891b2,color:#164e63',
  document: 'fill:#f1f5f9,stroke:#64748b,color:#334155',
}
const DEFAULT_COLOR = 'fill:#f1f5f9,stroke:#64748b,color:#334155'

/** Escape a label for safe embedding in a mermaid `["..."]` node. */
function ml(label: string): string {
  return label.replace(/["[\]{}()#&<>|]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 48)
}

/** Lazily render a mermaid diagram string into a div (theme-aware via <html> class). */
function MermaidDiagram({ code }: { code: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [error, setError] = useState(false)
  const id = 'legal-mmd-' + useId().replace(/[^a-zA-Z0-9]/g, '')
  useEffect(() => {
    let cancelled = false
    setError(false)
    import('mermaid')
      .then(async ({ default: mermaid }) => {
        const dark = document.documentElement.classList.contains('dark')
        mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: dark ? 'dark' : 'default' })
        const { svg } = await mermaid.render(id, code)
        if (!cancelled && ref.current) ref.current.innerHTML = svg
      })
      .catch(() => {
        if (!cancelled) setError(true)
      })
    return () => {
      cancelled = true
    }
  }, [code, id])
  if (error)
    return <pre className="overflow-auto rounded-md bg-muted/40 p-2 text-[11px]">{code}</pre>
  return <div ref={ref} className="flex justify-center overflow-auto [&_svg]:h-auto [&_svg]:max-w-full" />
}

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

  // Build the mermaid source from filtered nodes + edges.
  const mermaidCode = useMemo(() => {
    if (!data) return ''
    const edges = data.edges.filter((e) => !relFilter || e.relation_type === relFilter)
    const edgeNodeIds = new Set<string>()
    edges.forEach((e) => {
      edgeNodeIds.add(e.source_id)
      edgeNodeIds.add(e.target_id)
    })
    // Visible nodes: those in filtered edges, or matching the type filter.
    const visible = data.nodes.filter(
      (n) => edgeNodeIds.has(n.node_id) || (!!typeFilter && n.node_type === typeFilter),
    )
    const visibleIds = new Set(visible.map((n) => n.node_id))
    const usedTypes = new Set(visible.map((n) => n.node_type))
    const lines = ['flowchart LR']
    for (const n of visible) {
      lines.push(`  ${n.node_id}["${ml(n.label)}"]`)
    }
    for (const e of edges) {
      if (visibleIds.has(e.source_id) && visibleIds.has(e.target_id)) {
        lines.push(`  ${e.source_id} -->|${e.relation_type}| ${e.target_id}`)
      }
    }
    for (const tp of usedTypes) {
      lines.push(`  classDef t_${tp.replace(/[^a-z0-9]/gi, '')} ${TYPE_COLORS[tp] || DEFAULT_COLOR}`)
    }
    for (const n of visible) {
      lines.push(`  class ${n.node_id} t_${n.node_type.replace(/[^a-z0-9]/gi, '')}`)
    }
    return lines.join('\n')
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
        ) : mermaidCode ? (
          <MermaidDiagram code={mermaidCode} />
        ) : (
          <p className="py-6 text-center text-xs text-muted-foreground">{t('graph.empty')}</p>
        )}
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-apple-md glass-2 border border-[hsl(var(--glass-border))] p-3">
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
                    <span className="ml-2 rounded bg-[hsl(var(--glass-border))] px-1.5 py-0.5 text-[10px] text-muted-foreground">
                      {n.node_type}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="rounded-apple-md glass-2 border border-[hsl(var(--glass-border))] p-3">
          {selected ? (
            <>
              <h3 className="text-sm font-semibold">{selected.label}</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                {selected.node_type}
                {selected.description ? ` · ${selected.description}` : ''}
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
                <ul className="mt-1 space-y-1">
                  {impact.map((r) => (
                    <li
                      key={r.node.node_id}
                      className="rounded-md bg-[hsl(var(--glass-hover))] px-2 py-1 text-xs"
                    >
                      <span className="font-medium">{r.node.label}</span>
                      <span className="ml-2 text-[10px] text-muted-foreground">
                        {r.node.node_type} · depth {r.depth}
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

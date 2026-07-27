import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { listLifecycle, supersedePage, confirmPage, getLifecycleDetail, type LifecycleEntry, type LifecycleDetail } from '@/api/legal'
import { ApiError } from '@/api/client'

const STATUS_COLORS: Record<string, string> = {
  active: 'bg-green-100 text-green-700 dark:bg-green-500/15 dark:text-green-400',
  superseded: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-500/15 dark:text-yellow-400',
  repealed: 'bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-400',
  amended: 'bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-400',
}

/** Knowledge lifecycle view (UI_INTEGRATION_PLAN §3.3).
 *  Lists pages with confidence + status; supports supersede + confirm actions. */
export default function LifecycleView({ kb }: { kb: string }) {
  const { t } = useTranslation('legal')
  const [pages, setPages] = useState<LifecycleEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [feedback, setFeedback] = useState<string | null>(null)
  const [history, setHistory] = useState<{ path: string; detail: LifecycleDetail | null } | null>(null)

  const load = () => {
    setLoading(true); setError(null)
    listLifecycle(kb, statusFilter || undefined)
      .then((r) => setPages(r.pages))
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => setLoading(false))
  }
  useEffect(load, [kb, statusFilter])

  const doSupersede = async (p: LifecycleEntry) => {
    const by = window.prompt(t('lifecycle.supersedePromptBy', { page: p.page_path }), '')
    if (!by) return
    const reason = window.prompt(t('lifecycle.supersedePromptReason'), '') || ''
    setBusy(p.page_path)
    try {
      await supersedePage(kb, p.page_path, by, reason, 'manual')
      setFeedback(`✓ ${p.page_path} → ${by}`)
      load()
    } catch (e) { setFeedback(`✗ ${e instanceof ApiError ? e.message : e}`) }
    finally { setBusy(null) }
  }
  const doConfirm = async (p: LifecycleEntry) => {
    setBusy(p.page_path)
    try {
      await confirmPage(kb, p.page_path, { add_source: true })
      setFeedback(`✓ ${t('lifecycle.confirmed', { page: p.page_path })}`)
      load()
    } catch (e) { setFeedback(`✗ ${e instanceof ApiError ? e.message : e}`) }
    finally { setBusy(null) }
  }
  const toggleHistory = async (p: LifecycleEntry) => {
    if (history?.path === p.page_path) { setHistory(null); return }
    try {
      const detail = await getLifecycleDetail(kb, p.page_path)
      setHistory({ path: p.page_path, detail })
    } catch (e) { setFeedback(`✗ ${e instanceof ApiError ? e.message : e}`) }
  }

  return (
    <div className="rounded-apple-md glass-2 border border-[hsl(var(--glass-border))] p-3">
      <div className="mb-2 flex items-center gap-2">
        <h3 className="text-sm font-semibold">{t('lifecycle.title')}</h3>
        <select
          value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
          className="ml-auto rounded-md border border-[hsl(var(--glass-border))] bg-transparent px-2 py-1 text-xs"
        >
          <option value="">{t('lifecycle.allStatus')}</option>
          {['active', 'superseded', 'repealed', 'amended'].map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>
      {feedback && <p className="mb-2 text-xs">{feedback}</p>}
      {loading ? <p className="text-xs text-muted-foreground">…</p>
        : error ? <p className="text-xs text-red-500">{error}</p>
        : pages.length === 0 ? <p className="text-xs text-muted-foreground">{t('lifecycle.empty')}</p>
        : (
          <ul className="max-h-[65vh] space-y-1.5 overflow-y-auto">
            {pages.map((p) => (
              <li key={p.page_path} className="rounded-md border border-[hsl(var(--glass-border))] p-2">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium">{p.page_path}</span>
                  <span className={`rounded px-1.5 py-0.5 text-[10px] ${STATUS_COLORS[p.status] || 'bg-gray-100 text-gray-600'}`}>{p.status}</span>
                  <span className="ml-auto text-[10px] text-muted-foreground">{t('lifecycle.confidence')}: {p.confidence.toFixed(2)}</span>
                </div>
                <div className="mt-1 flex items-center gap-2 text-[10px] text-muted-foreground">
                  <span>{t('lifecycle.sources')}: {p.sources_count}</span>
                  <span>· {t('lifecycle.decay')}: {p.decay_rate}</span>
                  {p.superseded_by && <span>→ {p.superseded_by}</span>}
                </div>
                <div className="mt-1.5 flex gap-1.5">
                  <button
                    disabled={busy === p.page_path}
                    onClick={() => doConfirm(p)}
                    className="rounded-md border border-[hsl(var(--glass-border))] px-2 py-0.5 text-[10px] hover:bg-[hsl(var(--glass-hover))] disabled:opacity-50"
                  >{t('lifecycle.confirm')}</button>
                  <button
                    disabled={busy === p.page_path}
                    onClick={() => doSupersede(p)}
                    className="rounded-md border border-[hsl(var(--glass-border))] px-2 py-0.5 text-[10px] hover:bg-[hsl(var(--glass-hover))] disabled:opacity-50"
                  >{t('lifecycle.supersede')}</button>
                  <button
                    onClick={() => toggleHistory(p)}
                    className="rounded-md border border-[hsl(var(--glass-border))] px-2 py-0.5 text-[10px] hover:bg-[hsl(var(--glass-hover))]"
                  >{t('lifecycle.history')}</button>
                </div>
                {history?.path === p.page_path && history.detail && (
                  <ul className="mt-1.5 space-y-1 border-l-2 border-[hsl(var(--glass-border))] pl-2">
                    {history.detail.history.length === 0 ? (
                      <li className="text-[10px] text-muted-foreground">{t('lifecycle.noHistory')}</li>
                    ) : (
                      [...history.detail.history].reverse().map((h, i) => (
                        <li key={i} className="text-[10px] text-muted-foreground">
                          <span className="font-mono2">{String(h.at).replace('T', ' ').slice(0, 19)}</span>
                          <span className="ml-1.5 rounded bg-[hsl(var(--glass-border))] px-1 text-foreground">{h.type}</span>
                          {typeof h.new_confidence === 'number' && (
                            <span className="ml-1">→ {Number(h.new_confidence).toFixed(2)}</span>
                          )}
                          {typeof h.reason === 'string' && <span className="ml-1">· {h.reason}</span>}
                        </li>
                      ))
                    )}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        )}
    </div>
  )
}

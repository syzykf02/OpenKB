import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { listSyncSources, addSyncSource, scanSyncSource, applySyncSource, type SyncSourceStat } from '@/api/legal'
import { ApiError } from '@/api/client'

/** Sync source management (UI_INTEGRATION_PLAN §3.2).
 *  Lists sources; scan shows the diff, apply ingests new/modified files. */
export default function SyncSourcesView({ kb }: { kb: string }) {
  const { t } = useTranslation('legal')
  const [sources, setSources] = useState<SyncSourceStat[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [feedback, setFeedback] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [newId, setNewId] = useState('')
  const [newPath, setNewPath] = useState('')
  const [diff, setDiff] = useState<
    | {
        id: string
        new_files: string[]
        modified_files: string[]
        deleted_files: string[]
      }
    | null
  >(null)

  const load = () => {
    setLoading(true); setError(null)
    listSyncSources(kb)
      .then((r) => setSources(r.sources))
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => setLoading(false))
  }
  useEffect(load, [kb])

  const doAdd = async () => {
    if (!newId || !newPath) return
    setAdding(true)
    try {
      await addSyncSource(kb, { source_id: newId, path: newPath, name: newId })
      setNewId(''); setNewPath('')
      setFeedback(`✓ ${t('sync.added', { id: newId })}`)
      load()
    } catch (e) { setFeedback(`✗ ${e instanceof ApiError ? e.message : e}`) }
    finally { setAdding(false) }
  }
  const doScan = async (id: string) => {
    setFeedback(`… scan ${id}`)
    try {
      const r = await scanSyncSource(kb, id)
      if (r.error) { setFeedback(`✗ ${r.error}`); return }
      setDiff({ id, new_files: r.new_files, modified_files: r.modified_files, deleted_files: r.deleted_files })
      setFeedback(`✓ ${id}: +${r.new_files.length} ~${r.modified_files.length} -${r.deleted_files.length} (${r.total_scanned} scanned)`)
    } catch (e) { setFeedback(`✗ ${e instanceof ApiError ? e.message : e}`) }
  }
  const doApply = async (id: string) => {
    setFeedback(`… apply ${id}`)
    try {
      const r = await applySyncSource(kb, id, false)
      if (r.error) { setFeedback(`✗ ${r.error}`); return }
      setFeedback(`✓ ${id}: ${r.ingested.length} ingested, ${r.deleted.length} deleted`)
      load()
    } catch (e) { setFeedback(`✗ ${e instanceof ApiError ? e.message : e}`) }
  }

  return (
    <div className="rounded-apple-md glass-2 border border-[hsl(var(--glass-border))] p-3">
      <div className="mb-2 flex items-center gap-2">
        <h3 className="text-sm font-semibold">{t('sync.title')}</h3>
        <button
          onClick={() => setAdding((v) => !v)}
          className="ml-auto rounded-md border border-[hsl(var(--glass-border))] px-2 py-1 text-xs hover:bg-[hsl(var(--glass-hover))]"
        >+ {t('sync.add')}</button>
      </div>
      {adding && (
        <div className="mb-2 flex gap-2">
          <input value={newId} onChange={(e) => setNewId(e.target.value)} placeholder={t('sync.idPlaceholder')} className="flex-1 rounded-md border border-[hsl(var(--glass-border))] bg-transparent px-2 py-1 text-xs" />
          <input value={newPath} onChange={(e) => setNewPath(e.target.value)} placeholder={t('sync.pathPlaceholder')} className="flex-[2] rounded-md border border-[hsl(var(--glass-border))] bg-transparent px-2 py-1 text-xs" />
          <button disabled={adding && !newId} onClick={doAdd} className="rounded-md border border-[hsl(var(--glass-border))] px-2 py-1 text-xs hover:bg-[hsl(var(--glass-hover))] disabled:opacity-50">{t('sync.add')}</button>
        </div>
      )}
      {feedback && <p className="mb-2 text-xs">{feedback}</p>}
      {loading ? <p className="text-xs text-muted-foreground">…</p>
        : error ? <p className="text-xs text-red-500">{error}</p>
        : sources.length === 0 ? <p className="text-xs text-muted-foreground">{t('sync.empty')}</p>
        : (
          <ul className="space-y-1.5">
            {sources.map((s) => (
              <li key={s.source_id} className="rounded-md border border-[hsl(var(--glass-border))] p-2">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium">{s.name || s.source_id}</span>
                  <span className="rounded bg-[hsl(var(--glass-border))] px-1.5 py-0.5 text-[10px] text-muted-foreground">{s.type}</span>
                  <span className="ml-auto text-[10px] text-muted-foreground">{s.file_count} files</span>
                </div>
                <div className="mt-1.5 flex gap-1.5">
                  <button onClick={() => doScan(s.source_id)} className="rounded-md border border-[hsl(var(--glass-border))] px-2 py-0.5 text-[10px] hover:bg-[hsl(var(--glass-hover))]">{t('sync.scan')}</button>
                  <button onClick={() => doApply(s.source_id)} className="rounded-md border border-[hsl(var(--glass-border))] px-2 py-0.5 text-[10px] hover:bg-[hsl(var(--glass-hover))]">{t('sync.apply')}</button>
                </div>
              </li>
            ))}
          </ul>
        )}
      {diff && (
        <div className="mt-3 rounded-md border border-[hsl(var(--glass-border))] p-2">
          <h4 className="mb-1 text-xs font-semibold">{t('sync.diffTitle', { id: diff.id })}</h4>
          <DiffGroup label={t('sync.newFiles')} files={diff.new_files} color="text-green-600 dark:text-green-400" sign="+" />
          <DiffGroup label={t('sync.modifiedFiles')} files={diff.modified_files} color="text-amber-600 dark:text-amber-400" sign="~" />
          <DiffGroup label={t('sync.deletedFiles')} files={diff.deleted_files} color="text-red-600 dark:text-red-400" sign="-" />
        </div>
      )}
    </div>
  )
}

function DiffGroup({ label, files, color, sign }: { label: string; files: string[]; color: string; sign: string }) {
  return (
    <div className="mt-1">
      <p className={`text-[11px] font-medium ${color}`}>
        {sign} {label} ({files.length})
      </p>
      {files.length === 0 ? null : (
        <ul className="ml-3 mt-0.5 max-h-32 space-y-0.5 overflow-y-auto">
          {files.map((f) => (
            <li key={f} className="truncate font-mono2 text-[11px] text-muted-foreground" title={f}>
              {f}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

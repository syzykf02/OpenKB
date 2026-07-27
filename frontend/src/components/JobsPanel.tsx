import { useEffect, useMemo, useRef, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Ban,
  CheckCircle2,
  Circle,
  CircleSlash2,
  Clock,
  FileText,
  ListTree,
  Loader2,
  XCircle,
} from 'lucide-react'
import type { JobSummary } from '@/api/maintenance'
import type { UploadFileState, UploadStatus } from '@/hooks/useJobs'
import { cn } from '@/lib/utils'

/** Status glyph for one per-file upload row. */
function UploadStatusIcon({ status }: { status: UploadStatus }) {
  switch (status) {
    case 'processing':
      return <Loader2 className="w-3.5 h-3.5 animate-spin text-muted-foreground" />
    case 'added':
      return <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 dark:text-emerald-400" />
    case 'skipped':
      return <CircleSlash2 className="w-3.5 h-3.5 text-muted-foreground" />
    case 'exists':
      return <CircleSlash2 className="w-3.5 h-3.5 text-muted-foreground" />
    case 'failed':
      return <XCircle className="w-3.5 h-3.5 text-red-500 dark:text-red-400" />
    case 'cancelled':
      return <Ban className="w-3.5 h-3.5 text-amber-500 dark:text-amber-400" />
    default:
      return <Circle className="w-3.5 h-3.5 text-muted-foreground/50" />
  }
}

/** Compact relative time ("just now" / "3m" / "2h" / "5d") for a job's
 *  created_at (seconds since epoch). Frozen when the pane is idle (no polling);
 *  updates each render while jobs are active. */
function formatRelative(ts: number | null | undefined, nowSec: number): string {
  if (!ts) return ''
  const s = Math.max(0, Math.round(nowSec - ts))
  if (s < 45) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)}m`
  if (s < 86400) return `${Math.round(s / 3600)}h`
  return `${Math.round(s / 86400)}d`
}

/** One colored status badge for a job row. */
function StatusBadge({ status }: { status: JobSummary['status'] }) {
  const { t } = useTranslation('kb')
  const map: Record<JobSummary['status'], { cls: string; icon: ReactNode }> = {
    queued: {
      cls: 'text-muted-foreground bg-muted',
      icon: <Clock className="w-3 h-3" />,
    },
    running: {
      cls: 'text-accent-brand bg-accent-brand/10',
      icon: <Loader2 className="w-3 h-3 animate-spin" />,
    },
    done: {
      cls: 'text-emerald-600 dark:text-emerald-400 bg-emerald-500/10',
      icon: <CheckCircle2 className="w-3 h-3" />,
    },
    failed: {
      cls: 'text-red-600 dark:text-red-400 bg-red-500/10',
      icon: <XCircle className="w-3 h-3" />,
    },
    cancelled: {
      cls: 'text-amber-600 dark:text-amber-400 bg-amber-500/10',
      icon: <Ban className="w-3 h-3" />,
    },
  }
  const s = map[status] ?? map.queued
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium',
        s.cls,
      )}
    >
      {s.icon}
      {t(`kb:jobs.status.${status}`)}
    </span>
  )
}

export interface JobsPanelProps {
  jobs: JobSummary[]
  selectedJobId: string | null
  selectedFiles: UploadFileState[]
  selectedLogs: string[]
  /** True while the SELECTED job is non-terminal - drives the Cancel button. */
  selectedRunning: boolean
  onSelectJob: (id: string) => void
  onCancelUpload: () => void
}

/**
 * Compile-jobs panel for the Documents pane: a selectable list of this KB's
 * recent jobs (title + status badge + relative time) above the selected job's
 * per-file rows + live compile log. Presentational - all state lives in
 * `useJobs`. Newest job first; clicking a row selects it (re-attaching its SSE
 * stream to replay rows + logs from the server's event ring).
 */
export default function JobsPanel({
  jobs,
  selectedJobId,
  selectedFiles,
  selectedLogs,
  selectedRunning,
  onSelectJob,
  onCancelUpload,
}: JobsPanelProps) {
  const { t } = useTranslation(['kb', 'common'])
  const nowSec = Date.now() / 1000
  // Newest first for display (listJobs returns oldest first).
  const ordered = useMemo(() => [...jobs].reverse(), [jobs])

  // Pin the compile-log panel to the bottom as new lines stream in.
  const logRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [selectedLogs])

  return (
    <div className="mt-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-[13.5px] font-semibold text-foreground">
          {t('kb:jobs.heading', { count: jobs.length })}
        </h2>
      </div>

      {jobs.length === 0 ? (
        <div className="mt-2 rounded-2xl border border-dashed border-[hsl(var(--glass-border))] py-8 text-center text-[13px] text-muted-foreground">
          {t('kb:jobs.empty')}
        </div>
      ) : (
        <div className="mt-2 space-y-1.5">
          {ordered.map((job) => {
            const selected = job.id === selectedJobId
            return (
              <button
                key={job.id}
                type="button"
                onClick={() => onSelectJob(job.id)}
                className={cn(
                  'anim-fade-up w-full rounded-xl border px-3 py-2 text-left transition-colors',
                  selected
                    ? 'border-accent-brand/50 bg-accent-brand/5'
                    : 'border-[hsl(var(--glass-border))] glass-2 hover:border-foreground/20',
                )}
              >
                <div className="flex items-center gap-2">
                  <FileText className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate text-[13px] text-foreground">
                    {job.title}
                  </span>
                  <StatusBadge status={job.status} />
                </div>
                <div className="mt-1 flex items-center gap-2 pl-5 text-[11.5px] text-muted-foreground">
                  <span>{formatRelative(job.created_at, nowSec)}</span>
                  {job.result && (
                    <span className="inline-flex items-center gap-1">
                      ·{' '}
                      {t('kb:jobs.filesLabel', {
                        count:
                          (job.result.added_count ?? 0) +
                          (job.result.skipped_count ?? 0) +
                          (job.result.failed_count ?? 0),
                      })}
                    </span>
                  )}
                </div>
              </button>
            )
          })}
        </div>
      )}

      {/* Selected job detail: per-file rows + compile log. */}
      {selectedJobId && (
        <div className="mt-3">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-[12px] font-medium text-muted-foreground">
              {t('kb:upload.progressHeading', { count: selectedFiles.length })}
            </h3>
            {selectedRunning && (
              <button
                type="button"
                onClick={onCancelUpload}
                className="inline-flex items-center gap-1.5 rounded-lg border border-[hsl(var(--glass-border))] glass-2 px-2.5 py-1 text-[12px] font-medium text-muted-foreground transition-colors hover:text-red-500 hover:border-red-500/40"
              >
                <Ban className="w-3.5 h-3.5" />
                {t('kb:upload.cancel')}
              </button>
            )}
          </div>
          {selectedFiles.length === 0 ? (
            <div className="mt-2 flex items-center gap-2 text-[12px] text-muted-foreground">
              <Loader2 className="w-3 h-3 animate-spin" />
              {t('kb:jobs.selectHint')}
            </div>
          ) : (
            <div className="mt-2 space-y-1.5">
              {selectedFiles.map((f) => (
                <div
                  key={f.id}
                  className="rounded-xl border border-[hsl(var(--glass-border))] glass-2 px-3 py-2 flex items-center gap-2.5"
                >
                  <UploadStatusIcon status={f.status} />
                  <span className="text-[13px] text-foreground truncate">{f.name}</span>
                  <span className="ml-auto text-[11.5px] text-muted-foreground shrink-0">
                    {t(`kb:upload.fileStatus.${f.status}`)}
                  </span>
                </div>
              ))}
            </div>
          )}
          {/* Live compile log streamed from the backend (`log` SSE frames). */}
          {selectedLogs.length > 0 && (
            <div className="mt-3">
              <h3 className="flex items-center gap-1.5 text-[12px] font-medium text-muted-foreground">
                <ListTree className="w-3.5 h-3.5" />
                {t('kb:upload.logHeading')}
              </h3>
              <div
                ref={logRef}
                className="mt-1.5 max-h-56 overflow-y-auto rounded-xl border border-[hsl(var(--glass-border))] bg-muted/40 px-3 py-2 font-mono text-[11px] leading-relaxed text-muted-foreground"
              >
                {selectedLogs.map((line, i) => (
                  <div key={i} className="whitespace-pre-wrap break-all">
                    {line}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Ban,
  CheckCircle2,
  Circle,
  CircleSlash2,
  ListTree,
  Loader2,
  RotateCcw,
  XCircle,
} from 'lucide-react'
import type { WikiDocument } from '@/api/wiki'
import type { CompileTaskFile, UploadFileState, UploadLogLine, UploadStatus } from '@/hooks/useJobs'
import { cn } from '@/lib/utils'

function UploadStatusIcon({ status }: { status: UploadStatus }) {
  switch (status) {
    case 'processing':
      return <Loader2 className="h-4 w-4 animate-spin text-accent-brand" />
    case 'added':
      return <CheckCircle2 className="h-4 w-4 text-emerald-500 dark:text-emerald-400" />
    case 'skipped':
    case 'exists':
      return <CircleSlash2 className="h-4 w-4 text-muted-foreground" />
    case 'failed':
      return <XCircle className="h-4 w-4 text-red-500 dark:text-red-400" />
    case 'cancelled':
      return <Ban className="h-4 w-4 text-amber-500 dark:text-amber-400" />
    default:
      return <Circle className="h-4 w-4 text-muted-foreground/50" />
  }
}

function FileStatus({ file }: { file: UploadFileState }) {
  const { t } = useTranslation('kb')
  const total = Math.max(file.totalSteps, 1)
  const completed = Math.min(file.completedSteps, total)
  return (
    <div className="min-w-[82px] text-right">
      <div className="text-[10.5px] font-medium text-muted-foreground">{t(`upload.fileStatus.${file.status}`)}</div>
      <div className="mt-1 flex items-center justify-end gap-1.5">
        <div className="h-1 w-10 overflow-hidden rounded-full bg-muted">
          <div className="h-full rounded-full bg-accent-brand transition-[width] duration-300" style={{ width: `${(completed / total) * 100}%` }} />
        </div>
        <span className="tabular-nums text-[10px] text-muted-foreground">{t('jobs.stepCount', { completed, total })}</span>
      </div>
    </div>
  )
}

function LogLine({ line }: { line: UploadLogLine }) {
  const tone = line.level === 'error' ? 'text-red-500' : line.level === 'warning' ? 'text-amber-500' : 'text-muted-foreground'
  return (
    <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 py-1">
      <span className={cn('pt-0.5 text-[9px] font-bold uppercase tracking-wide', tone)}>{line.level}</span>
      <span className="min-w-0 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-foreground/80">{line.message}</span>
    </div>
  )
}

export interface JobsPanelProps {
  taskFiles: CompileTaskFile[]
  documents: WikiDocument[]
  selectedLogs: UploadLogLine[]
  selectedRunning: boolean
  selectedCancelling: boolean
  onCancelUpload: () => void
  onRetryFile: (file: CompileTaskFile) => void
}

/** File-first compile workspace. Live, retained, and already-compiled files
 * share one scrollable list; the log remains a unified live task transcript. */
export default function JobsPanel({
  taskFiles,
  documents,
  selectedLogs,
  selectedRunning,
  selectedCancelling,
  onCancelUpload,
  onRetryFile,
}: JobsPanelProps) {
  const { t } = useTranslation(['kb', 'common'])
  const logRef = useRef<HTMLDivElement>(null)
  const finishedNames = new Set(
    taskFiles
      .filter((file) => file.status === 'added')
      .map((file) => file.name),
  )
  const completedFiles: CompileTaskFile[] = documents
    .filter((document) => !finishedNames.has(document.name))
    .map((document) => ({
      id: `document:${document.hash || document.name}`,
      name: document.name,
      status: 'added',
      message: document.display_type,
      uploadIndex: null,
      completedSteps: 3,
      totalSteps: 3,
      step: 'finalize',
      jobId: '',
      createdAt: 0,
    }))
  const files = [...taskFiles, ...completedFiles]

  useEffect(() => {
    const element = logRef.current
    if (element) element.scrollTop = element.scrollHeight
  }, [selectedLogs])

  return (
    <div className="min-w-0">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-[14px] font-semibold text-foreground">{t('jobs.heading', { count: files.length })}</h3>
          <p className="mt-1 text-[12px] text-muted-foreground">{t('jobs.fileFirstNote')}</p>
        </div>
        {selectedRunning && (
          <button
            type="button"
            onClick={onCancelUpload}
            disabled={selectedCancelling}
            className="inline-flex h-7 items-center gap-1.5 rounded-lg px-2 text-[11.5px] font-medium text-muted-foreground transition-colors hover:bg-red-50 hover:text-red-600 disabled:cursor-wait disabled:opacity-60 dark:hover:bg-red-500/10 dark:hover:text-red-400"
          >
            {selectedCancelling ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Ban className="h-3.5 w-3.5" />}
            {selectedCancelling ? t('upload.cancelling') : t('upload.cancel')}
          </button>
        )}
      </div>

      {files.length === 0 ? (
        <div className="mt-4 border-y border-dashed border-[hsl(var(--glass-border))] py-12 text-center text-[13px] text-muted-foreground">{t('jobs.empty')}</div>
      ) : (
        <>
            <div className="mt-4">
              <section className="min-w-0">
                <div className="flex items-center justify-between gap-3 border-b border-[hsl(var(--glass-border))] pb-2">
                  <h4 className="text-[12px] font-semibold text-muted-foreground">{t('upload.progressHeading', { count: files.length })}</h4>
                </div>
                <div className="max-h-[420px] overflow-y-auto border-b border-[hsl(var(--glass-border))]">
                  {files.map((file) => (
                      <div key={file.id} className="group flex items-center gap-2 border-b border-[hsl(var(--glass-border))] py-2.5 last:border-b-0">
                        <div className="flex min-w-0 flex-1 items-center gap-2.5">
                          <UploadStatusIcon status={file.status} />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-[12.5px] font-medium text-foreground">{file.name}</span>
                            <span className="mt-0.5 block truncate text-[10.5px] text-muted-foreground">{file.message || t(`jobs.steps.${file.step}`)}</span>
                          </span>
                          <FileStatus file={file} />
                        </div>
                        {file.status === 'failed' && file.uploadIndex != null && file.jobId && (
                          <button
                            type="button"
                            onClick={() => onRetryFile(file)}
                            className="inline-flex h-7 shrink-0 items-center gap-1 rounded-md px-2 text-[11px] font-medium text-red-600 transition-colors hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-500/10"
                          >
                            <RotateCcw className="h-3 w-3" />
                            {t('upload.retry')}
                          </button>
                        )}
                      </div>
                  ))}
                </div>
              </section>

              <section className="mt-6 min-w-0 border-t border-[hsl(var(--glass-border))] pt-4">
                <h4 className="flex items-center gap-1.5 text-[12px] font-semibold text-muted-foreground">
                  <ListTree className="h-3.5 w-3.5" />
                  {t('upload.logHeading')}
                </h4>
                <div ref={logRef} className="mt-3 max-h-72 overflow-y-auto rounded-xl bg-muted/40 px-3 py-2 font-mono2">
                  {selectedLogs.length > 0 ? selectedLogs.map((line, index) => <LogLine key={`${line.message}-${index}`} line={line} />) : (
                    <div className="py-4 text-center text-[11px] text-muted-foreground">{t('upload.logEmpty')}</div>
                  )}
                </div>
              </section>
            </div>
        </>
      )}
    </div>
  )
}

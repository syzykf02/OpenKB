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
  Trash2,
  XCircle,
} from 'lucide-react'
import type { PendingDocument, WikiDocument } from '@/api/wiki'
import type { CompileTaskFile, UploadFileState, UploadLogLine, UploadStatus } from '@/hooks/useJobs'
import { cn } from '@/lib/utils'

function UploadStatusIcon({ status }: { status: UploadStatus }) {
  switch (status) {
    case 'uploading':
    case 'processing':
      return <Loader2 className="h-4 w-4 animate-spin text-accent-brand" />
    case 'uploaded':
    case 'added':
      return <CheckCircle2 className="h-4 w-4 text-emerald-500 dark:text-emerald-400" />
    case 'skipped':
    case 'exists':
      return <CircleSlash2 className="h-4 w-4 text-muted-foreground" />
    case 'failed':
    case 'interrupted':
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
    <div className="w-[112px] shrink-0 text-right">
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
  pendingDocuments: PendingDocument[]
  selectedLogs: UploadLogLine[]
  cancellingJobIds: ReadonlySet<string>
  onCancelFile: (file: CompileTaskFile) => void
  onRetryFile: (file: CompileTaskFile) => void
  onCompilePendingFile: (document: PendingDocument) => void
  onDeleteFile: (file: CompileTaskFile) => void
  onPreviewDocument: (document: WikiDocument) => void
  recompilingDocumentNames: ReadonlySet<string>
  onRecompileDocument: (document: WikiDocument) => void
  onRequestDeleteDocument: (document: WikiDocument) => void
  onRequestDeletePending: (document: PendingDocument) => void
  deletingDocumentName: string | null
}

/** File-first compile workspace. Live, retained, and already-compiled files
 * share one scrollable list; the log remains a unified live task transcript. */
export default function JobsPanel({
  taskFiles,
  documents,
  pendingDocuments,
  selectedLogs,
  cancellingJobIds,
  onCancelFile,
  onRetryFile,
  onCompilePendingFile,
  onDeleteFile,
  onPreviewDocument,
  recompilingDocumentNames,
  onRecompileDocument,
  onRequestDeleteDocument,
  onRequestDeletePending,
  deletingDocumentName,
}: JobsPanelProps) {
  const { t } = useTranslation(['kb', 'common'])
  const logRef = useRef<HTMLDivElement>(null)
  const documentsByName = new Map(documents.map((document) => [document.name, document]))
  const documentsByHash = new Map(documents.map((document) => [document.hash, document]))
  const pendingByPath = new Map(pendingDocuments.map((document) => [document.path, document]))
  const pendingByName = new Map(pendingDocuments.map((document) => [document.name, document]))
  // Persisted file-task rows are the source of truth. `documents` only adds
  // reader affordances, and pending rows remain a compatibility fallback for
  // sources uploaded before the task-state file existed.
  const legacyPendingFiles: CompileTaskFile[] = pendingDocuments.map((document) => ({
        id: `pending:${document.path}`,
        name: document.name,
        sourcePath: document.path,
        status: 'uploaded' as UploadStatus,
        message: document.display_type,
        uploadIndex: null,
        completedSteps: 1,
        totalSteps: 4,
        step: 'prepare',
        jobId: '',
        jobStatus: 'done' as const,
        createdAt: 0,
      }))
  const files: CompileTaskFile[] = taskFiles.length > 0 ? taskFiles : legacyPendingFiles

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
                  {files.map((file) => {
                    const document = documentsByName.get(file.name) || (file.sourceHash ? documentsByHash.get(file.sourceHash) : undefined)
                    const pendingDocument = file.sourcePath
                      ? pendingByPath.get(file.sourcePath)
                      : pendingByName.get(file.name)
                    const recompiling = !!document && recompilingDocumentNames.has(document.name)
                    const activeJob =
                      file.persistentStatus === 'queued' || file.persistentStatus === 'running' ||
                      (!!file.jobId && (file.jobStatus === 'queued' || file.jobStatus === 'running'))
                    const canCompile = !!file.actions?.includes('compile')
                    const canDelete = !!file.actions?.includes('delete')
                    const canDeleteDocument = !activeJob && !canDelete && !!document
                    const canDeletePending = !activeJob && !canDelete && !document && !!pendingDocument
                    const displayFile = recompiling
                      ? { ...file, status: 'processing' as UploadStatus, completedSteps: 2, totalSteps: 4, step: 'compile' }
                      : file
                    return (
                      <div key={file.id} className="group grid grid-cols-[minmax(0,1fr)_112px_auto] items-center gap-x-3 border-b border-[hsl(var(--glass-border))] py-2.5 last:border-b-0">
                        <div className="flex min-w-0 items-center gap-2.5">
                          <UploadStatusIcon status={displayFile.status} />
                          <span className="min-w-0 flex-1">
                            {document ? (
                              <button
                                type="button"
                                onClick={() => onPreviewDocument(document)}
                                title={t('docs.reader.open')}
                                className="block max-w-full truncate text-left text-[12.5px] font-medium text-foreground transition-colors hover:text-accent-brand hover:underline focus:outline-none focus-visible:rounded focus-visible:ring-2 focus-visible:ring-accent-brand/50"
                              >
                                {file.name}
                              </button>
                            ) : (
                              <span className="block truncate text-[12.5px] font-medium text-foreground">{file.name}</span>
                            )}
                            <span className="mt-0.5 block truncate text-[10.5px] text-muted-foreground">{recompiling ? t('jobs.steps.compile') : file.message || t(`jobs.steps.${file.step}`)}</span>
                          </span>
                        </div>
                        <FileStatus file={displayFile} />
                        <div className="flex min-w-[112px] shrink-0 items-center justify-end gap-1">
                          {!activeJob && canCompile ? (
                            <button
                              type="button"
                              onClick={() => onRetryFile(file)}
                              className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-[11px] font-medium text-red-600 transition-colors hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-500/10"
                            >
                              <RotateCcw className="h-3 w-3" />
                              {t('upload.compile')}
                            </button>
                          ) : !activeJob && document ? (
                            <button
                              type="button"
                              onClick={() => onRecompileDocument(document)}
                              disabled={recompiling}
                              className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:cursor-wait disabled:opacity-60"
                            >
                              {recompiling ? <Loader2 className="h-3 w-3 animate-spin" /> : <RotateCcw className="h-3 w-3" />}
                              {t('upload.compile')}
                            </button>
                          ) : !activeJob && pendingDocument ? (
                            <button
                              type="button"
                              onClick={() => onCompilePendingFile(pendingDocument)}
                              className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                            >
                              <RotateCcw className="h-3 w-3" />
                              {t('upload.compile')}
                            </button>
                          ) : null}
                          {canDelete ? (
                            <button
                              type="button"
                              onClick={() => onDeleteFile(file)}
                              title={t('docs.delete.action')}
                              aria-label={t('docs.delete.action')}
                              className="grid h-7 w-7 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-500/10 dark:hover:text-red-400"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          ) : canDeleteDocument && document ? (
                            <button
                              type="button"
                              onClick={() => onRequestDeleteDocument(document)}
                              disabled={recompiling || deletingDocumentName === document.name}
                              title={t('docs.delete.action')}
                              aria-label={t('docs.delete.action')}
                              className="grid h-7 w-7 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-red-50 hover:text-red-600 disabled:cursor-wait disabled:opacity-60 dark:hover:bg-red-500/10 dark:hover:text-red-400"
                            >
                              {deletingDocumentName === document.name ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                            </button>
                          ) : canDeletePending && pendingDocument ? (
                            <button
                              type="button"
                              onClick={() => onRequestDeletePending(pendingDocument)}
                              disabled={deletingDocumentName === pendingDocument.name}
                              title={t('docs.delete.action')}
                              aria-label={t('docs.delete.action')}
                              className="grid h-7 w-7 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-red-50 hover:text-red-600 disabled:cursor-wait disabled:opacity-60 dark:hover:bg-red-500/10 dark:hover:text-red-400"
                            >
                              {deletingDocumentName === pendingDocument.name ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                            </button>
                          ) : null}
                          {activeJob && (
                            <button
                              type="button"
                              onClick={() => onCancelFile(file)}
                              disabled={cancellingJobIds.has(file.jobId)}
                              className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-red-50 hover:text-red-600 disabled:cursor-wait disabled:opacity-60 dark:hover:bg-red-500/10 dark:hover:text-red-400"
                            >
                              {cancellingJobIds.has(file.jobId) ? <Loader2 className="h-3 w-3 animate-spin" /> : <Ban className="h-3 w-3" />}
                              {cancellingJobIds.has(file.jobId) ? t('upload.cancelling') : t('upload.cancel')}
                            </button>
                          )}
                        </div>
                      </div>
                    )
                  })}
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

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { toast } from 'sonner'
import {
  cancelJob,
  compileFileTask,
  compilePendingDocument,
  deleteFileTask,
  listFileTasks,
  listJobs,
  retryJobFile,
  startUpload,
  streamJobEvents,
  type AddResult,
  type FileTask,
  type JobSummary,
  type UploadEvent,
} from '@/api/maintenance'
import type { PendingDocument } from '@/api/wiki'

/** Per-file lifecycle during an upload. `uploading` -> `processing` (backend
 *  `file_start`) -> terminal `added`/`skipped`/`failed` (`file_done`), or
 *  `cancelled` when the user aborts the batch mid-upload (the server rolls the
 *  in-flight file back). `exists` is client-only: a duplicate whose hash was
 *  already in the KB, deduped before upload (never sent). Such rows exist only
 *  in the session that performed the upload; after a refresh only the
 *  server-processed files replay from the job's event ring. */
export type UploadStatus =
  | 'uploading'
  | 'uploaded'
  | 'pending'
  | 'processing'
  | 'added'
  | 'skipped'
  | 'exists'
  | 'failed'
  | 'cancelled'
  | 'interrupted'

export interface UploadFileState {
  /** Stable generated id, assigned once at seed time - the React key. Rows are
   *  correlated to backend events by `uploadIndex`, not this id or the basename
   *  (two same-basename files from different folders must not collide). */
  id: string
  name: string
  /** SHA-256 of a client-deduped source. It lets the UI resolve a duplicate
   * upload back to its canonical KB document even if the local filename has
   * changed. Worker-driven rows omit it because their document does not exist
   * until compilation completes. */
  sourceHash?: string
  /** Safe basename of a raw source awaiting its first compilation. */
  sourcePath?: string
  status: UploadStatus
  message?: string
  /** Position within the uploaded batch (the files actually sent), or `null`
   *  for a client-deduped duplicate that was never sent. Job `file_start` /
   *  `file_done` events carry this index; `null` rows are never matched by
   *  events. */
  uploadIndex: number | null
  /** Structured worker progress. Each add uses prepare → compile → finalize. */
  completedSteps: number
  totalSteps: number
  step: string
}

export interface UploadLogLine {
  message: string
  level: string
  logger: string
}

/** One file in the all-history compile list, annotated with its source job. */
export interface CompileTaskFile extends UploadFileState {
  jobId: string
  jobStatus: JobSummary['status']
  createdAt: number
  /** Persisted rows are restored from `.openkb/file-tasks.json`, not SSE. */
  persistent?: boolean
  actions?: ReadonlyArray<'cancel' | 'compile' | 'delete'>
  persistentStatus?: FileTask['status']
}

interface JobData {
  files: UploadFileState[]
  logs: UploadLogLine[]
}

const TERMINAL = new Set<JobSummary['status']>(['done', 'failed', 'cancelled', 'interrupted'])
const isTerminal = (j: JobSummary) => TERMINAL.has(j.status)

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** SHA-256 hex digest of a File, via SubtleCrypto. Sequential (caller loops) so
 *  a multi-file drop bounds peak memory. Returns '' when SubtleCrypto is
 *  unavailable (e.g. a non-secure http context) so the caller falls back to
 *  uploading - the server's registry dedup remains the safety net. */
async function sha256Hex(file: File): Promise<string> {
  try {
    if (!crypto?.subtle?.digest) return ''
    const buf = await file.arrayBuffer()
    const digest = await crypto.subtle.digest('SHA-256', buf)
    return Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, '0'))
      .join('')
  } catch {
    return ''
  }
}

/** Fire the completion toast for one job (mirrors the old `final`-event handler
 *  in KbDetail). `status` is the job's terminal status; `result` is the
 *  AddResponse payload (null when the job failed before producing one). */
function fireCompletionToast(
  t: TFunction,
  status: string,
  result: AddResult | null,
  error: string,
) {
  if (status === 'cancelled') {
    toast.info(t('kb:upload.cancelledToast'))
    return
  }
  if (status === 'failed') {
    toast.error(t('kb:upload.errorToast', { summary: error || t('kb:upload.failed', { count: 0 }) }))
    return
  }
  if (!result) {
    toast.success(t('kb:upload.successToast', { summary: '' }))
    return
  }
  const parts = [t('kb:upload.added', { count: result.added_count })]
  if (result.skipped_count) parts.push(t('kb:upload.skipped', { count: result.skipped_count }))
  if (result.failed_count) parts.push(t('kb:upload.failed', { count: result.failed_count }))
  const line = parts.join(' · ')
  if (result.added_count === 0 && result.failed_count > 0) {
    const reason = result.files.find((f) => f.status === 'failed')?.message
    toast.error(
      t('kb:upload.errorToast', { summary: line }) +
        (reason ? t('kb:upload.reasonSuffix', { reason }) : ''),
    )
  } else if (result.failed_count > 0) {
    toast.warning(t('kb:upload.partialToast', { summary: line }))
  } else if (result.added_count > 0) {
    toast.success(t('kb:upload.successToast', { summary: line }))
  } else {
    toast.info(t('kb:upload.existsToast', { summary: line }))
  }
}

export interface UseJobsOptions {
  /** Read the current set of document hashes known to the KB (from the loaded
   *  inventory). Called at upload time so client-side dedup uses fresh data. */
  getKnownHashes: () => Set<string>
  /** Invoked once when a job reaches a terminal state (the wiki may have
   *  changed) so the caller can refresh its inventory. */
  onCompleted: () => void
}

export interface UseJobsResult {
  /** All known jobs for this KB (polled). The just-started job is added
   *  optimistically until the next poll confirms it. */
  jobs: JobSummary[]
  selectedJobId: string | null
  /** Files from live jobs and retained completed-job results, newest first. */
  taskFiles: CompileTaskFile[]
  selectedLogs: UploadLogLine[]
  /** True while any job is non-terminal (queued/running) - drives the dropzone
   *  disabled state and the "compiling" indicator. */
  uploading: boolean
  /** Select a job to view its files + log. Re-attaches the SSE stream (replays
   *  history from the ring, then tails live if still running). */
  selectJob: (id: string | null) => void
  /** Hash each file client-side, skip duplicates already in the KB, upload the
   *  rest as a new server-owned job, and auto-select it. */
  doUpload: (files: File[]) => void
  /** Cancel a file's containing job (cooperative; the in-flight mutation rolls back). */
  cancelFile: (file: CompileTaskFile) => void
  /** Job ids with an in-flight cancellation request. */
  cancellingJobIds: ReadonlySet<string>
  /** Re-run one failed file from its retained source file. */
  retryFile: (file: CompileTaskFile) => void
  /** Start compilation for a source that was uploaded previously and is still
   * present in the KB's raw directory. */
  compilePendingFile: (document: PendingDocument) => void
  /** Permanently remove the source/artifacts and retain a deleted history row. */
  deleteFile: (file: CompileTaskFile) => void
}

/**
 * Stateful engine for the Documents pane's compile-jobs UI.
 *
 * Generalizes the old "one watched add job" model into a selectable list of
 * jobs: `listJobs` is polled while any job is active (status badges + completion
 * detection), and the SELECTED job's SSE stream is tailed (re-attaching replays
 * the server's event ring from `last_seq=-1`, so a refresh or a click on an old
 * job restores its exact rows + logs). Only the selected job holds an open SSE
 * connection; switching jobs aborts the previous view (the server-owned job
 * itself is unaffected).
 *
 * Upload dedup: each file is SHA-256 hashed in the browser and compared to the
 * KB's known document hashes; duplicates are shown as `skipped` immediately and
 * never sent. The server's `HashRegistry` dedup stays as the safety net for the
 * race where a file was added by another session after this client loaded its
 * inventory.
 */
export function useJobs(kb: string, opts: UseJobsOptions): UseJobsResult {
  const { t } = useTranslation(['kb', 'common'])

  const [serverJobs, setServerJobs] = useState<JobSummary[]>([])
  const [fileTasks, setFileTasks] = useState<FileTask[]>([])
  const [jobData, setJobData] = useState<Record<string, JobData>>({})
  // Files deduped in the browser never create a server job, but still belong
  // in the visible task list so the user can see why they were not uploaded.
  const [clientOnlyFiles, setClientOnlyFiles] = useState<CompileTaskFile[]>([])
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)
  const [cancellingJobIds, setCancellingJobIds] = useState<Set<string>>(new Set())

  // Refs mirror state for use inside stable callbacks / async loops without
  // re-creating them (which would restart the attach effect).
  const tRef = useRef(t)
  tRef.current = t
  const knownHashesRef = useRef(opts.getKnownHashes)
  knownHashesRef.current = opts.getKnownHashes
  const onCompletedRef = useRef(opts.onCompleted)
  onCompletedRef.current = opts.onCompleted
  const serverJobsRef = useRef<JobSummary[]>([])
  serverJobsRef.current = serverJobs
  // The job whose SSE stream is currently open (the selected one), and the
  // AbortController that detaches THIS view on switch/unmount (the job keeps
  // running server-side until it finishes or is cancelled explicitly).
  const attachAbortRef = useRef<AbortController | null>(null)
  // The job whose completion fires a toast - the one THIS session started via
  // doUpload. Restored history from a refresh replays silently.
  const notifyJobIdRef = useRef<string | null>(null)
  // Jobs already toasted (dedup: the SSE end and the polling transition can
  // both observe the same completion).
  const notifiedRef = useRef<Set<string>>(new Set())
  // Last observed status per job, for polling transition detection.
  const prevStatusRef = useRef<Record<string, JobSummary['status']>>({})
  // Monotonic counter for per-row React keys.
  const rowIdSeq = useRef(0)

  const refreshFileTasks = useCallback(() => {
    return listFileTasks(kb).then(setFileTasks).catch(() => {})
  }, [kb])

  /** Fold one add-job SSE event into the job's rows + log panel. Shared by live
   *  attaches and post-refresh re-attach (which replays history, so the same
   *  fold rebuilds the exact same view from the server's event ring). Rows are
   *  matched by `uploadIndex`; an event whose index has no seeded row (replay
   *  after a refresh, when client-deduped rows are gone) appends one. */
  const applyJobEvent = useCallback((jobId: string, ev: UploadEvent) => {
    if (ev.type === 'uploaded') {
      setJobData((prev) => {
        const cur = prev[jobId] ?? { files: [], logs: [] }
        const idx = cur.files.findIndex((file) => file.uploadIndex === ev.index)
        const files =
          idx >= 0
            ? cur.files.map((file, index) =>
                index === idx
                  ? { ...file, status: 'uploaded' as UploadStatus, step: 'prepare' }
                  : file,
              )
            : [
                ...cur.files,
                {
                  id: String(rowIdSeq.current++),
                  name: ev.original_name,
                  status: 'uploaded' as UploadStatus,
                  uploadIndex: ev.index,
                  completedSteps: 0,
                  totalSteps: 4,
                  step: 'prepare',
                },
              ]
        return { ...prev, [jobId]: { ...cur, files } }
      })
    } else if (ev.type === 'file_start') {
      setJobData((prev) => {
        const cur = prev[jobId] ?? { files: [], logs: [] }
        const idx = cur.files.findIndex((f) => f.uploadIndex === ev.index)
        const files =
          idx >= 0
            ? cur.files.map((f, i) =>
                i === idx
                  ? {
                      ...f,
                      status: 'processing' as UploadStatus,
                      completedSteps: ev.completed_steps ?? 0,
                      totalSteps: ev.total_steps || f.totalSteps || 4,
                      step: ev.step ?? 'prepare',
                    }
                  : f,
              )
            : [
                ...cur.files,
                {
                  id: String(rowIdSeq.current++),
                  name: ev.original_name,
                  status: 'processing' as UploadStatus,
                  uploadIndex: ev.index,
                  completedSteps: ev.completed_steps ?? 0,
                  totalSteps: ev.total_steps || 4,
                  step: ev.step ?? 'prepare',
                },
              ]
        return { ...prev, [jobId]: { ...cur, files } }
      })
    } else if (ev.type === 'file_progress') {
      setJobData((prev) => {
        const cur = prev[jobId] ?? { files: [], logs: [] }
        const files = cur.files.map((f) =>
          f.uploadIndex === ev.index
            ? {
                ...f,
                completedSteps: ev.completed_steps,
                totalSteps: ev.total_steps,
                step: ev.step,
                message: ev.message || f.message,
              }
            : f,
        )
        return { ...prev, [jobId]: { ...cur, files } }
      })
    } else if (ev.type === 'file_done') {
      setJobData((prev) => {
        const cur = prev[jobId] ?? { files: [], logs: [] }
        const idx = cur.files.findIndex((f) => f.uploadIndex === ev.index)
        const status = ev.file.status as UploadStatus
        const files =
          idx >= 0
            ? cur.files.map((f, i) =>
                i === idx
                  ? {
                      ...f,
                      status,
                      message: ev.file.message,
                      completedSteps: ev.completed_steps ?? (status === 'added' || status === 'skipped' ? 4 : f.completedSteps),
                      totalSteps: ev.total_steps || f.totalSteps || 4,
                      step: ev.step ?? (status === 'added' || status === 'skipped' ? 'finalize' : f.step),
                    }
                  : f,
              )
            : [
                ...cur.files,
                {
                  id: String(rowIdSeq.current++),
                  name: ev.file.original_name,
                  status,
                  message: ev.file.message,
                  uploadIndex: ev.index,
                  completedSteps: ev.completed_steps ?? (status === 'added' || status === 'skipped' ? 4 : 0),
                  totalSteps: ev.total_steps || 4,
                  step: ev.step ?? (status === 'added' || status === 'skipped' ? 'finalize' : 'compile'),
                },
              ]
        return { ...prev, [jobId]: { ...cur, files } }
      })
    } else if (ev.type === 'log') {
      setJobData((prev) => {
        const cur = prev[jobId] ?? { files: [], logs: [] }
        // Keep the panel bounded on very long compiles.
        const logs =
          cur.logs.length >= 500
            ? [...cur.logs.slice(cur.logs.length - 499), { message: ev.message, level: ev.level, logger: ev.logger }]
            : [...cur.logs, { message: ev.message, level: ev.level, logger: ev.logger }]
        return { ...prev, [jobId]: { ...cur, logs } }
      })
    } else if (ev.type === 'cancelled') {
      // Settle any rows the cancel left spinning.
      setJobData((prev) => {
        const cur = prev[jobId] ?? { files: [], logs: [] }
        const files = cur.files.map((f) =>
          f.status === 'uploading' || f.status === 'uploaded' || f.status === 'pending' || f.status === 'processing'
            ? { ...f, status: 'cancelled' as UploadStatus }
            : f,
        )
        return { ...prev, [jobId]: { ...cur, files } }
      })
    }
    // `start` / `uploaded` / `final` / `error` / `done` carry no row/log state
    // here; completion toasts + inventory refresh are handled by maybeComplete.
  }, [])

  /** Fire the completion toast (only for jobs this session started) and refresh
   *  the inventory, once per job. Called from both the SSE stream end and the
   *  polling transition; `notifiedRef` dedupes. */
  const maybeComplete = useCallback(
    (jobId: string, status: string, result: AddResult | null, error: string) => {
      if (notifiedRef.current.has(jobId)) return
      notifiedRef.current.add(jobId)
      setCancellingJobIds((ids) => {
        if (!ids.has(jobId)) return ids
        const next = new Set(ids)
        next.delete(jobId)
        return next
      })
      if (notifyJobIdRef.current === jobId) {
        fireCompletionToast(tRef.current, status, result, error)
      }
      onCompletedRef.current()
    },
    [],
  )

  /** Attach the UI to one server-owned job: resets its log (replay rebuilds
   *  it), keeps any seeded rows (client-deduped duplicates), then tails live
   *  frames until the job's terminal `done`. Aborting detaches THIS VIEW only. */
  const attach = useCallback(
    async (jobId: string, signal: AbortSignal) => {
      // Reset logs so a re-attach replays cleanly without duplicates; keep files
      // so client-deduped rows (never sent, so no events for them) survive.
      setJobData((prev) => ({
        ...prev,
        [jobId]: { files: prev[jobId]?.files ?? [], logs: [] },
      }))
      let finalResult: AddResult | null = null
      let finalStatus = ''
      let errorMsg = ''
      try {
        await streamJobEvents(
          jobId,
          (ev) => {
            if (ev.type === 'final') finalResult = ev.result
            else if (ev.type === 'done') finalStatus = ev.status
            else if (ev.type === 'cancelled') finalStatus = 'cancelled'
            else if (ev.type === 'error') {
              finalStatus = 'failed'
              errorMsg = ev.message
            }
            applyJobEvent(jobId, ev)
          },
          signal,
        )
      } catch (e) {
        // Abort = the view detached (unmount/switch); the job is unaffected and
        // a later select re-attaches. Real stream failures surface.
        if (!signal.aborted) toast.error(errMsg(e))
        return
      }
      if (signal.aborted) return
      if (finalStatus) maybeComplete(jobId, finalStatus, finalResult, errorMsg)
    },
    [applyJobEvent, maybeComplete],
  )

  const selectJob = useCallback((id: string | null) => {
    setSelectedJobId(id)
  }, [])

  // Initial load on mount / KB switch: list jobs, record their statuses, and
  // re-attach the most recent add job so a refresh restores its rows + logs
  // (silently - notifyJobIdRef is null, so no completion toast replays).
  useEffect(() => {
    let stale = false
    setJobData({})
    setClientOnlyFiles([])
    setServerJobs([])
    setFileTasks([])
    setSelectedJobId(null)
    setCancellingJobIds(new Set())
    notifyJobIdRef.current = null
    notifiedRef.current = new Set()
    prevStatusRef.current = {}
    listJobs(kb)
      .then((jobs) => {
        if (stale) return
        setServerJobs(jobs)
        for (const j of jobs) prevStatusRef.current[j.id] = j.status
        // A restarted server restores task history from JSON but cannot replay
        // the old in-memory SSE ring. Attach only to a live job.
        const latest = [...jobs].reverse().find((j) => j.kind === 'add' && !isTerminal(j))
        if (latest) selectJob(latest.id)
      })
      .catch(() => {})
    void refreshFileTasks()
    return () => {
      stale = true
    }
  }, [kb, refreshFileTasks, selectJob])

  // Poll while any job is active: refreshes status badges and detects
  // completions for jobs whose SSE view was switched away (the toast + inventory
  // refresh then fire via maybeComplete). Stops when every job is terminal.
  const hasActive = serverJobs.some((j) => !isTerminal(j))
  useEffect(() => {
    if (!hasActive) return
    const tick = () => {
      listJobs(kb)
        .then((jobs) => {
          setServerJobs(jobs)
          for (const j of jobs) {
            const prev = prevStatusRef.current[j.id]
            if (prev && !TERMINAL.has(prev) && TERMINAL.has(j.status)) {
              maybeComplete(j.id, j.status, j.result, j.error ?? '')
            }
            prevStatusRef.current[j.id] = j.status
          }
          setCancellingJobIds((ids) => {
            const active = new Set(jobs.filter((j) => !isTerminal(j)).map((j) => j.id))
            const next = new Set([...ids].filter((id) => active.has(id)))
            return next.size === ids.size ? ids : next
          })
        })
        .catch(() => {})
      void refreshFileTasks()
    }
    const h = setInterval(tick, 1500)
    return () => clearInterval(h)
  }, [hasActive, kb, maybeComplete, refreshFileTasks])

  // Attach the selected job's SSE stream. Switching selection (or unmount)
  // aborts the previous view; the new selection re-attaches from the ring.
  useEffect(() => {
    if (!selectedJobId) return
    const controller = new AbortController()
    attachAbortRef.current = controller
    void attach(selectedJobId, controller.signal)
    return () => controller.abort()
  }, [selectedJobId, attach])

  const doUpload = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return
      // Hash each file (sequential -> bounded memory) and partition against the
      // KB's known document hashes. A hash failure (no SubtleCrypto) degrades to
      // "upload anyway" - the server dedups.
      const known = knownHashesRef.current()
      const hashed: { file: File; hash: string; known: boolean }[] = []
      for (const f of files) {
        const hash = await sha256Hex(f)
        hashed.push({ file: f, hash, known: !!hash && known.has(hash) })
      }
      const toUpload = hashed.filter((h) => !h.known).map((h) => h.file)

      // Seed one row per dropped file in selection order: client-deduped files
      // show `exists` immediately (never sent); the rest are `uploading` with an
      // uploadIndex that correlates them to the job's file_start/file_done.
      let uploadIdx = 0
      const rows: UploadFileState[] = hashed.map((h) =>
        h.known
          ? {
              id: String(rowIdSeq.current++),
              name: h.file.name,
              sourceHash: h.hash,
              status: 'exists',
              uploadIndex: null,
              completedSteps: 4,
              totalSteps: 4,
              step: 'finalize',
            }
          : {
              id: String(rowIdSeq.current++),
              name: h.file.name,
              status: 'uploading',
              uploadIndex: uploadIdx++,
              completedSteps: 0,
              totalSteps: 4,
              step: 'prepare',
            },
      )

      if (toUpload.length === 0) {
        // Every dropped file is already in the KB - nothing to send.
        const createdAt = Date.now() / 1000
        setClientOnlyFiles((previous) => [
          ...rows.map((row) => ({ ...row, jobId: '', jobStatus: 'done' as const, createdAt })),
          ...previous,
        ])
        toast.info(tRef.current('kb:jobs.allExist', { count: files.length }))
        onCompletedRef.current()
        return
      }

      let accepted: { job_id: string }
      try {
        accepted = await startUpload(kb, toUpload)
      } catch (e) {
        toast.error(errMsg(e))
        return
      }
      const jobId = accepted.job_id
      void refreshFileTasks()
      const first = toUpload[0]?.name ?? '?'
      const title =
        `add: ${first}` + (toUpload.length > 1 ? ` (+${toUpload.length - 1})` : '')
      // Optimistic entry so the job appears in the list immediately; the next
      // poll replaces it with the server's record (same id -> selection holds).
      setServerJobs((prev) => [
        ...prev,
        {
          id: jobId,
          kind: 'add',
          kb,
          title,
          status: 'queued',
          created_at: Date.now() / 1000,
          started_at: null,
          finished_at: null,
          result: null,
          error: null,
          last_seq: -1,
        },
      ])
      prevStatusRef.current[jobId] = 'queued'
      setJobData((prev) => ({ ...prev, [jobId]: { files: rows, logs: [] } }))
      notifyJobIdRef.current = jobId
      notifiedRef.current.delete(jobId)
      selectJob(jobId)
    },
    [kb, refreshFileTasks, selectJob],
  )

  const cancelFile = useCallback((file: CompileTaskFile) => {
    const id = file.jobId
    if (!id) return
    const job = serverJobsRef.current.find((candidate) => candidate.id === id)
    if (!job || isTerminal(job)) return
    setCancellingJobIds((ids) => new Set(ids).add(id))
    cancelJob(id).catch((e) => {
      setCancellingJobIds((ids) => {
        const next = new Set(ids)
        next.delete(id)
        return next
      })
      toast.error(errMsg(e))
    })
  }, [])

  const retryFile = useCallback(
    async (file: CompileTaskFile) => {
      if (file.persistent) {
        try {
          const accepted = await compileFileTask(kb, file.id)
          setServerJobs((prev) => [
            ...prev,
            {
              id: accepted.job_id,
              kind: 'recompile',
              kb,
              title: `compile: ${file.name}`,
              status: 'queued',
              created_at: Date.now() / 1000,
              started_at: null,
              finished_at: null,
              result: null,
              error: null,
              last_seq: -1,
            },
          ])
          prevStatusRef.current[accepted.job_id] = 'queued'
          notifyJobIdRef.current = accepted.job_id
          notifiedRef.current.delete(accepted.job_id)
          void refreshFileTasks()
        } catch (e) {
          toast.error(errMsg(e))
        }
        return
      }
      const sourceJobId = file.jobId
      if (file.status !== 'failed' || file.uploadIndex == null) return
      let accepted: { job_id: string; status: string }
      try {
        accepted = await retryJobFile(sourceJobId, kb, file.uploadIndex)
      } catch (e) {
        toast.error(errMsg(e))
        return
      }
      const jobId = accepted.job_id
      const row: UploadFileState = {
        id: String(rowIdSeq.current++),
        name: file.name,
        status: 'uploading',
        uploadIndex: 0,
        completedSteps: 0,
        totalSteps: 4,
        step: 'prepare',
      }
      setServerJobs((prev) => [
        ...prev,
        {
          id: jobId,
          kind: 'add',
          kb,
          title: `retry: ${file.name}`,
          status: accepted.status as JobSummary['status'],
          created_at: Date.now() / 1000,
          started_at: null,
          finished_at: null,
          result: null,
          error: null,
          last_seq: -1,
        },
      ])
      prevStatusRef.current[jobId] = accepted.status as JobSummary['status']
      setJobData((prev) => ({ ...prev, [jobId]: { files: [row], logs: [] } }))
      notifyJobIdRef.current = jobId
      notifiedRef.current.delete(jobId)
      selectJob(jobId)
    },
    [kb, refreshFileTasks, selectJob],
  )

  const compilePendingFile = useCallback(
    async (document: PendingDocument) => {
      let accepted: { job_id: string; status: string }
      try {
        accepted = await compilePendingDocument(kb, document)
      } catch (e) {
        toast.error(errMsg(e))
        return
      }
      const jobId = accepted.job_id
      const row: UploadFileState = {
        id: String(rowIdSeq.current++),
        name: document.name,
        sourcePath: document.path,
        status: 'uploaded',
        uploadIndex: 0,
        completedSteps: 1,
        totalSteps: 4,
        step: 'prepare',
      }
      setServerJobs((prev) => [
        ...prev,
        {
          id: jobId,
          kind: 'add',
          kb,
          title: `compile: ${document.name}`,
          status: accepted.status as JobSummary['status'],
          created_at: Date.now() / 1000,
          started_at: null,
          finished_at: null,
          result: null,
          error: null,
          last_seq: -1,
        },
      ])
      prevStatusRef.current[jobId] = accepted.status as JobSummary['status']
      setJobData((prev) => ({ ...prev, [jobId]: { files: [row], logs: [] } }))
      notifyJobIdRef.current = jobId
      notifiedRef.current.delete(jobId)
      selectJob(jobId)
    },
    [kb, selectJob],
  )

  const taskFiles = useMemo(() => {
    const statusMap: Record<FileTask['status'], UploadStatus> = {
      queued: 'pending',
      running: 'processing',
      pending: 'uploaded',
      succeeded: 'added',
      skipped: 'skipped',
      failed: 'failed',
      cancelled: 'cancelled',
      interrupted: 'interrupted',
      deleted: 'cancelled',
    }
    const persisted: CompileTaskFile[] = fileTasks.map((file) => ({
      id: file.id,
      name: file.name,
      sourceHash: file.source_hash ?? undefined,
      sourcePath: file.raw_path ?? undefined,
      status: statusMap[file.status],
      message: file.error || file.message || undefined,
      uploadIndex: null,
      completedSteps: file.completed_steps,
      totalSteps: file.total_steps,
      step: file.step,
      jobId: file.last_job_id ?? '',
      jobStatus: file.status === 'queued' ? 'queued' : file.status === 'running' ? 'running' : 'done',
      createdAt: file.updated_at,
      persistent: true,
      actions: file.actions,
      persistentStatus: file.status,
    }))
    return [...clientOnlyFiles, ...persisted].sort((a, b) => b.createdAt - a.createdAt)
  }, [clientOnlyFiles, fileTasks])

  const deleteFile = useCallback((file: CompileTaskFile) => {
    if (!file.persistent) return
    deleteFileTask(kb, file.id)
      .then(() => {
        toast.success(tRef.current('kb:docs.delete.success', { name: file.name }))
        void refreshFileTasks()
        onCompletedRef.current()
      })
      .catch((e) => toast.error(errMsg(e)))
  }, [kb, refreshFileTasks])

  const selectedData = selectedJobId ? jobData[selectedJobId] : undefined
  return {
    jobs: serverJobs,
    selectedJobId,
    taskFiles,
    selectedLogs: selectedData?.logs ?? [],
    uploading: hasActive,
    selectJob,
    doUpload,
    cancelFile,
    cancellingJobIds,
    retryFile,
    compilePendingFile,
    deleteFile,
  }
}

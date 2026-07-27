import { apiFetch, apiStream, getToken, getApiBase } from "./client"
import i18n from "@/lib/i18n"

/** One file's outcome in an `/api/v1/add` response (`AddFileItem`). */
export interface AddFileItem {
  original_name: string
  saved_path: string | null
  status: string
  message: string
}

/** `/api/v1/add` JSON response (`AddResponse`). */
export interface AddResult {
  kb: string
  files: AddFileItem[]
  added_count: number
  skipped_count: number
  failed_count: number
}

/** One entry in a watcher's `recent_events` ring buffer (`WatchEventItem`). */
export interface WatchEventItem {
  ts: number
  event: string
  data: Record<string, unknown>
}

/**
 * `/api/v1/watch/{start,stop,status}` response (`WatchStatusResponse`).
 * When no watcher exists the backend returns just `{ kb, active: false }`;
 * the optional fields are only present while a watcher is (or was) live.
 */
export interface WatchStatus {
  kb: string
  active: boolean
  started_at?: number | null
  raw_dir?: string | null
  debounce?: number | null
  counters: Record<string, number>
  recent_events: WatchEventItem[]
}

/**
 * One event surfaced by an add job's SSE stream (`/api/v1/jobs/{id}/events`,
 * fed by `openkb.api_ingest.run_add_worker`). The backend also emits `start`
 * / `uploaded` frames that carry no per-file UI signal, so they are collapsed
 * away here; `done` terminates the stream.
 */
export type UploadEvent =
  | {
      type: "file_start"
      index: number
      original_name: string
      completed_steps?: number
      total_steps?: number
      step?: string
    }
  | {
      type: "file_progress"
      index: number
      completed_steps: number
      total_steps: number
      step: string
      message: string
    }
  | {
      type: "file_done"
      index: number
      file: AddFileItem
      completed_steps?: number
      total_steps?: number
      step?: string
    }
  /** Live compile log line captured from the worker thread (`openkb.*`
   *  loggers) — the UI shows these in the upload log panel. */
  | {
      type: "log"
      message: string
      level: string
      logger: string
    }
  | { type: "final"; result: AddResult }
  /** The job was cancelled (explicit cancel endpoint); the in-flight mutation
   *  was rolled back server-side. Emitted instead of `final`. */
  | { type: "cancelled"; message: string }
  | { type: "error"; message: string }
  | { type: "done"; status: string }

/** Server-side job snapshot from `GET /api/v1/jobs` (`openkb.jobs.Job.summary`). */
export interface JobSummary {
  id: string
  kind: string
  kb: string
  title: string
  status: "queued" | "running" | "done" | "failed" | "cancelled"
  created_at: number
  started_at: number | null
  finished_at: number | null
  result: AddResult | null
  error: string | null
  last_seq: number
}

/**
 * Upload documents: multipart `POST /api/v1/add` with `stream=true` starts a
 * SERVER-OWNED job and returns its id immediately. The compile then runs
 * independently of this HTTP request — watch it with `streamJobEvents`, and a
 * page refresh can rediscover it via `listJobs` and re-attach. Returns the
 * `{ job_id, kb, status }` acceptance payload.
 */
export async function startUpload(
  kb: string,
  files: File[],
): Promise<{ job_id: string; kb: string; status: string }> {
  const form = new FormData()
  form.append("kb", kb)
  form.append("stream", "true")
  files.forEach((f) => form.append("files", f))
  const token = getToken()
  const res = await fetch(getApiBase().replace(/\/$/, "") + "/api/v1/add", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  })
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const j = await res.json()
      detail = typeof j.detail === "string" ? j.detail : detail
    } catch {
      // keep default message
    }
    throw new Error(i18n.t("common:errors.uploadFailed", { detail }))
  }
  return res.json()
}

export function listJobs(kb: string): Promise<JobSummary[]> {
  return apiFetch<{ jobs: JobSummary[] }>(`/api/v1/jobs?kb=${encodeURIComponent(kb)}`).then(
    (r) => r.jobs,
  )
}

/**
 * Request cancellation of a job (`POST /api/v1/jobs/{id}/cancel`). Cooperative:
 * the worker stops at its next checkpoint and the in-flight wiki mutation is
 * rolled back. Idempotent — cancelling a finished job reports its status.
 */
export function cancelJob(jobId: string): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/api/v1/jobs/${jobId}/cancel`, { body: {} })
}

/** Re-run one failed file from the retained raw source of an add job. */
export function retryJobFile(
  jobId: string,
  kb: string,
  fileIndex: number,
): Promise<{ job_id: string; kb: string; status: string }> {
  return apiFetch<{ job_id: string; kb: string; status: string }>(`/api/v1/jobs/${jobId}/retry`, {
    body: { kb, file_index: fileIndex },
  })
}

/**
 * Tail one job's SSE event stream, calling `onEvent` per frame. Re-attachable:
 * frames carry monotonic SSE `id:` cursors; pass `lastSeq` to resume after the
 * last frame a previous view saw (the server replays its ring buffer). The
 * stream ends on the job's terminal `done` frame — which is also delivered to
 * `onEvent` — or when `signal` aborts (aborting only drops THIS view; the job
 * itself keeps running server-side until it finishes or is explicitly
 * cancelled via `cancelJob`).
 *
 * Per-file events arrive strictly in upload order (one `file_start` then one
 * `file_done` per file), so they carry a running `index` the caller uses to
 * correlate events to rows by position — robust to duplicate basenames.
 */
export async function streamJobEvents(
  jobId: string,
  onEvent: (ev: UploadEvent) => void,
  signal?: AbortSignal,
  lastSeq = -1,
): Promise<void> {
  const token = getToken()
  const url =
    getApiBase().replace(/\/$/, "") +
    `/api/v1/jobs/${jobId}/events?last_seq=${lastSeq}`
  const res = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal,
  })
  if (!res.ok || !res.body) {
    throw new Error(i18n.t("common:errors.uploadFailed", { detail: `${res.status}` }))
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ""
  // Running cursor over the rows: `file_start` advances it, `file_done` reuses.
  let fileCursor = -1
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const blocks = buf.split("\n\n")
    buf = blocks.pop() ?? ""
    for (const block of blocks) {
      const lines = block.split("\n")
      const eventLine = lines.find((l) => l.startsWith("event: "))
      const dataLine = lines.find((l) => l.startsWith("data: "))
      if (!eventLine || !dataLine) continue // keep-alive comment / malformed
      const event = eventLine.slice("event: ".length)
      let data: any
      try {
        data = JSON.parse(dataLine.slice("data: ".length))
      } catch {
        continue // robust SSE reader: skip a malformed frame, keep streaming
      }
      switch (event) {
        case "file_start":
          fileCursor = Number.isInteger(data.file_index) ? data.file_index : fileCursor + 1
          onEvent({
            type: "file_start",
            index: fileCursor,
            original_name: String(data.original_name ?? ""),
            completed_steps: Number(data.completed_steps ?? 0),
            total_steps: Number(data.total_steps ?? 0),
            step: typeof data.step === "string" ? data.step : undefined,
          })
          break
        case "file_progress":
          onEvent({
            type: "file_progress",
            index: Number.isInteger(data.file_index) ? data.file_index : fileCursor,
            completed_steps: Number(data.completed_steps ?? 0),
            total_steps: Number(data.total_steps ?? 0),
            step: String(data.step ?? "compile"),
            message: String(data.message ?? ""),
          })
          break
        case "file_done":
          onEvent({
            type: "file_done",
            index: Number.isInteger(data.file_index) ? data.file_index : fileCursor,
            file: data as AddFileItem,
            completed_steps: Number(data.completed_steps ?? 0),
            total_steps: Number(data.total_steps ?? 0),
            step: typeof data.step === "string" ? data.step : undefined,
          })
          break
        case "log":
          onEvent({
            type: "log",
            message: String(data.message ?? ""),
            level: String(data.level ?? "info"),
            logger: String(data.logger ?? ""),
          })
          break
        case "cancelled":
          onEvent({ type: "cancelled", message: String(data.message ?? "") })
          break
        case "final":
          onEvent({ type: "final", result: data as AddResult })
          break
        case "error":
          onEvent({ type: "error", message: String(data.message ?? "") })
          break
        case "done":
          onEvent({ type: "done", status: String(data.status ?? "") })
          break
        // `start` / `uploaded` carry no per-file UI signal.
      }
    }
  }
}

/** `/api/v1/remove` response (`RemoveResponse`) — the subset the UI reads.
 *  `status` is `removed` on full success or `partial` when the local wiki files
 *  were removed but PageIndex cleanup failed (both are HTTP 200); on `partial`,
 *  `pageindex_error` carries why the remote cleanup failed. */
export interface RemoveResult {
  status: string
  name?: string | null
  doc_name?: string | null
  pageindex_error?: string | null
  message?: string | null
}

/**
 * Remove one document from a KB via `POST /api/v1/remove`.
 *
 * `identifier` is resolved by the backend (`_resolve_doc_identifier`) against
 * the original filename first (`metadata['name']`), then the slug
 * (`doc_name`), then a case-insensitive substring — so passing a document's
 * exact original `name` gives a deterministic single hit. A 404 means no
 * match; a 409 means the identifier matched multiple documents. Both surface
 * as an `ApiError` from `apiFetch`.
 */
export function removeDocument(kb: string, identifier: string): Promise<RemoveResult> {
  return apiFetch<RemoveResult>("/api/v1/remove", { body: { kb, identifier } })
}

export function watchStart(kb: string, debounce?: number): Promise<WatchStatus> {
  return apiFetch<WatchStatus>("/api/v1/watch/start", { body: { kb, debounce } })
}
export function watchStop(kb: string): Promise<WatchStatus> {
  return apiFetch<WatchStatus>("/api/v1/watch/stop", { body: { kb } })
}
export function watchStatus(kb: string): Promise<WatchStatus> {
  return apiFetch<WatchStatus>("/api/v1/watch/status", { body: { kb } })
}

/**
 * Stream a recompile (`POST /api/v1/recompile`). Emits SSE events
 * `start` / `doc` / `final` / `error` / `done` (see `_stream_recompile`).
 * With no `docName` it recompiles every indexed doc (`all_docs: true`).
 *
 * Pass an optional `signal` to make the stream cancellable — the caller aborts
 * it to stop the SSE (e.g. when the settings sheet closes) without leaving the
 * recompile running headless. Backward-compatible: omit it and nothing changes.
 */
export function runRecompile(kb: string, docName?: string, signal?: AbortSignal) {
  return apiStream(
    "/api/v1/recompile",
    {
      kb,
      doc_name: docName,
      all_docs: !docName,
      stream: true,
    },
    signal,
  )
}

export function runLint(kb: string, fix: boolean): Promise<unknown> {
  return apiFetch<unknown>("/api/v1/lint", { body: { kb, fix } })
}

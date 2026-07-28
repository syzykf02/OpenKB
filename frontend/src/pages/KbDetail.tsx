import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from 'react'
import { useNavigate, useParams } from 'react-router'
import { useTranslation } from 'react-i18next'
import * as Dialog from '@radix-ui/react-dialog'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { Cloud, ChevronDown, ChevronLeft, ChevronRight, Eye, FileText, Link2, ListChecks, ListTree, Loader2, Pencil, Plus, RefreshCw, Upload, Settings2, Trash2, X, type LucideIcon } from 'lucide-react'
import { toast } from 'sonner'
import { deletePage, editPage, getDocumentSource, getKbInventory, getPage, getPageLinks, type DocumentSource, type KbInventory, type PendingDocument, type WikiDocument } from '@/api/wiki'
import { getDocirByHash, type DocirNode } from '@/api/legal'
import { ApiError } from '@/api/client'
import MarkdownView from '@/components/MarkdownView'
import PageList from '@/components/PageList'
import ConnectorCards from '@/components/ConnectorCards'
import KbOverviewCards, { type Section } from '@/components/KbOverviewCards'
import KbSettingsSheet from '@/components/KbSettingsSheet'
import LegalGraphView from '@/components/legal/LegalGraphView'
import LifecycleView from '@/components/legal/LifecycleView'
import SyncSourcesView from '@/components/legal/SyncSourcesView'
import JobsPanel from '@/components/JobsPanel'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useJobs, type CompileTaskFile, type UploadLogLine } from '@/hooks/useJobs'
import { useAnimatedSwitch } from '@/hooks/useAnimatedSwitch'
import { cn } from '@/lib/utils'
import { deletePendingDocument, removeDocument, runRecompile } from '@/api/maintenance'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'

/** True when `line` looks like a line of a YAML frontmatter block: a blank line,
 *  a `#` comment, a `- ` list item, an indented continuation, or a `key: value`
 *  mapping entry whose value is YAML-shaped (empty, quoted, a `[`/`{` flow
 *  collection, or a single bare token). A mapping value that is free prose
 *  (multiple unquoted words, e.g. `see below`) is NOT YAML-shaped — that is what
 *  separates real OKF frontmatter (values are always JSON-quoted) from a prose
 *  line like `Note: see below`. ASCII-only. */
function looksLikeYamlLine(line: string): boolean {
  if (line.trim() === '') return true
  if (/^[ \t]*#/.test(line)) return true // comment
  if (/^[ \t]*-([ \t]|$)/.test(line)) return true // list item
  if (/^[ \t]+\S/.test(line)) return true // indented continuation / nested block
  const m = /^[ \t]*[\w.-]+[ \t]*:([ \t]+(.*))?$/.exec(line)
  if (!m) return false // no `key:` mapping — a prose line
  const value = (m[2] ?? '').trim()
  if (value === '') return true // `key:` with an empty / block value
  if (/^["'[{]/.test(value)) return true // quoted string or flow collection
  return !/\s/.test(value) // a single bare scalar (Concept / 42 / true), not prose
}

/** Strip a leading YAML frontmatter block (`--- ... ---`) from a raw wiki page.
 *  Pages are served verbatim by `GET /api/v1/page`, so an OKF frontmatter block
 *  would otherwise render in the reader as junk metadata lines — and, now that
 *  MarkdownView renders thematic breaks, its `---` delimiters as horizontal
 *  rules. The block is stripped ONLY when it genuinely looks like frontmatter:
 *  it opens at the VERY START of the document, is closed by a line-anchored
 *  `---`, and EVERY non-blank inner line looks like YAML (see `looksLikeYamlLine`).
 *  A block containing a prose line is left intact, so a body that legitimately
 *  opens with a `---` thematic break followed by prose (`---\nIntro paragraph\n---`
 *  or `---\nNote: see below\n---`) is NOT mistaken for frontmatter. Real OKF
 *  frontmatter (`title:`/`type:`/`links:`, values JSON-quoted) still strips.
 *  No-op when there is no leading frontmatter. Only the reader strips it; chat
 *  answers (which carry no frontmatter) go through MarkdownView untouched.
 *  ASCII-only. */
function stripFrontmatter(md: string): string {
  const m = /^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/.exec(md)
  if (!m) return md
  if (!m[1].split(/\r?\n/).every(looksLikeYamlLine)) return md
  return md.slice(m[0].length)
}

/** One selected wiki page, derived from its `<type>/<name>` path. */
interface SelectedPage {
  /** Path passed to `/api/v1/page` (relative to `wiki/`). */
  path: string
  /** Folder label with trailing slash, e.g. `concepts/`. */
  group: string
  /** Display filename, always with `.md`. */
  title: string
}

/**
 * Parse a `<type>/<name>` wiki path into its display parts. `reports` names
 * already carry `.md`; `summaries`/`concepts`/`entities` are stems, so append
 * `.md` for display only.
 */
function parseSelected(path: string | null): SelectedPage | null {
  if (!path) return null
  const slash = path.indexOf('/')
  if (slash < 0) return { path, group: '', title: path }  // root file, e.g. index.md
  const type = path.slice(0, slash)
  const name = path.slice(slash + 1)
  return { path, group: `${type}/`, title: type === 'reports' ? name : `${name}.md` }
}

/** Total compiled pages across all wiki types. */
function pageTotal(inv: KbInventory): number {
  return inv.concepts.length + inv.entities.length + inv.summaries.length + inv.reports.length
}

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

export default function KbDetail() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const { t } = useTranslation(['kb', 'common'])

  const [inv, setInv] = useState<KbInventory | null>(null)
  const [invError, setInvError] = useState<string | null>(null)
  const [selectedPath, setSelectedPath] = useState<string | null>(null)

  // Nav-card selection (Sub-project G): the six cards ARE the tab bar. The
  // active card drives which below-area layout renders (Task 13).
  const [section, setSection] = useState<Section>('index')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const reduce = useReducedMotion()
  // Frequency-gated enter spring (Apple §3): rapid card clicking renders the
  // next section instantly; a settled selection gets the gentle spring.
  const animateSwitch = useAnimatedSwitch(section)

  // Page content and error are tagged with the path they belong to so a stale
  // response never renders under a newly selected page.
  const [page, setPage] = useState<{ path: string; content: string } | null>(null)
  const [pageError, setPageError] = useState<{ path: string; message: string } | null>(null)

  // Documents section: upload + compile-jobs state. The jobs engine (polling,
  // selectable per-job logs, client-side hash dedup) lives in `useJobs` below;
  // only the dropzone's drag state and file input stay here.
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [dragActive, setDragActive] = useState(false)

  const selected = useMemo(() => parseSelected(selectedPath), [selectedPath])
  const openPath = useCallback((path: string) => setSelectedPath(path), [])

  /** Navigate a `[[type/name]]` wikilink: open its exact page, and if the
   *  type matches a section card (concepts/entities/summaries/reports),
   *  switch the active card so the Overview highlight follows the link —
   *  mirrors `selectSection`'s card-highlight behavior, but opens the
   *  clicked target instead of the section's first page. */
  const onWikiLink = useCallback(
    (target: string) => {
      const slash = target.indexOf('/')
      const type = slash < 0 ? '' : target.slice(0, slash)
      if (type === 'concepts' || type === 'entities' || type === 'summaries' || type === 'reports') {
        setSection(type)
      }
      openPath(target)
    },
    [openPath],
  )

  // Load the inventory, then auto-select the first page. State is only ever
  // set inside the async callbacks (never synchronously in the effect body).
  // The component is remounted per KB via `key` in App, so no reset is needed.
  useEffect(() => {
    let cancelled = false
    getKbInventory(id)
      .then((r) => {
        if (cancelled) return
        setInv(r)
        // Land on the wiki home (index.md) like a real wiki, not the first concept.
        setSection('index')
        setSelectedPath('index.md')
      })
      .catch((e) => {
        if (!cancelled) setInvError(errMsg(e))
      })
    return () => {
      cancelled = true
    }
  }, [id])

  // Fetch the selected page's Markdown from the real endpoint.
  useEffect(() => {
    if (!selectedPath) return
    const path = selectedPath
    let cancelled = false
    getPage(id, path)
      .then((r) => {
        if (cancelled) return
        setPage({ path, content: stripFrontmatter(r.content) })
        setPageError(null)
      })
      .catch((e) => {
        if (!cancelled) setPageError({ path, message: errMsg(e) })
      })
    return () => {
      cancelled = true
    }
  }, [id, selectedPath])

  /** Re-fetch the inventory (after an upload / recompile that changed docs). */
  const refreshInventory = useCallback(async () => {
    try {
      const r = await getKbInventory(id)
      setInv(r)
      setInvError(null)
    } catch (e) {
      setInvError(errMsg(e))
    }
  }, [id])

  /** The open page was deleted (F2): close the now-gone page and refresh the
   *  inventory — backlink pages changed on disk too (their [[links]] were
   *  demoted to plain text). */
  const onPageDeleted = useCallback(() => {
    setSelectedPath(null)
    void refreshInventory()
  }, [refreshInventory])

  /** The open page was saved (F3): adopt the returned file content (stripping
   *  frontmatter exactly like the load effect) or re-fetch when the backend
   *  didn't return it, then refresh the inventory (edits can change the link
   *  graph). The functional set never clobbers a page that loaded for a
   *  DIFFERENT selection while the save was in flight. */
  const onPageSaved = useCallback(
    (path: string, content: string | null) => {
      // editPage always returns the saved content on 200 (PageEditResponse), so
      // adopt it directly — frontmatter stripped exactly like the load effect.
      // The functional set never clobbers a page loaded for a DIFFERENT
      // selection while the save was in flight.
      if (content != null) {
        setPage((prev) => (prev && prev.path !== path ? prev : { path, content: stripFrontmatter(content) }))
      }
      void refreshInventory()
    },
    [refreshInventory],
  )

  /** Remove an ingested source, then reconcile the wiki inventory. The API
   * resolves original filenames exactly before falling back to fuzzy matching,
   * so a source row's `name` is the safest identifier to send. */
  const onDeleteDocument = useCallback(
    async (identifier: string) => {
      try {
        const result = await removeDocument(id, identifier)
        if (result.status === 'partial') {
          const reason = result.pageindex_error || result.message || ''
          toast.warning(
            t('kb:docs.delete.partial', { name: result.name || identifier }) +
              (reason ? t('kb:docs.delete.reasonSuffix', { reason }) : ''),
          )
        } else {
          toast.success(t('kb:docs.delete.success', { name: result.name || identifier }))
        }
        await refreshInventory()
      } catch (cause) {
        const detail =
          cause instanceof ApiError
            ? (cause.detail as { message?: string; candidates?: Array<{ name?: string; doc_name?: string }> } | undefined)
            : undefined
        const candidates = detail?.candidates
        if (candidates?.length) {
          const names = candidates.map((candidate) => candidate.name || candidate.doc_name || '?').join(', ')
          toast.error(t('kb:docs.delete.multiple', { message: detail?.message || errMsg(cause), names }))
        } else {
          toast.error(t('kb:docs.delete.error', { error: errMsg(cause) }))
        }
      }
    },
    [id, refreshInventory, t],
  )

  // Compile-jobs engine: polls `listJobs`, tails the selected job's SSE stream
  // (re-attachable - a refresh or a click on an old job replays its rows + log
  // from the server's event ring), and dedups uploads by SHA-256 against the
  // KB's known document hashes so already-uploaded files aren't re-sent.
  const jobs = useJobs(id, {
    getKnownHashes: () =>
      new Set((inv?.documents ?? []).map((d) => d.hash).filter(Boolean)),
    onCompleted: refreshInventory,
  })


  // Card selection handler: Index opens index.md, a type card auto-selects
  // its first page, Documents/legal views show no reader.
  const selectSection = useCallback(
    (next: Section) => {
      setSection(next)
      if (next === 'index') {
        openPath('index.md')
      } else if (next === 'documents' || next === 'legal-graph' || next === 'lifecycle' || next === 'sync-sources') {
        setSelectedPath(null)
      } else {
        const first = inv?.[next]?.[0]
        setSelectedPath(first ? `${next}/${first}` : null)
      }
    },
    [inv, openPath],
  )

  const docCount = inv?.document_count ?? 0
  const documents = inv?.documents ?? []
  const pendingDocuments = inv?.pending_documents ?? []
  const hasPages = inv ? pageTotal(inv) > 0 : false

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="shrink-0 px-6 pt-5 pb-3 glass-2 relative">
        {/* pr-28 reserves the global top-right chrome lane (theme pill + future
            i18n switcher, see App.tsx) so the ml-auto gear clears the pill with
            room to spare. The reserve lives on this control row (not the header
            div) so the overview cards below keep symmetric px-6 width. */}
        <div className="flex items-center gap-3 pr-28">
          <span className="w-3 h-3 rounded-full bg-accent-brand" />
          <h1 className="text-[19px] font-extrabold tracking-tight text-foreground">{id}</h1>
          <button
            onClick={() => setSettingsOpen(true)}
            title={t('kb:settingsButton')}
            aria-label={t('kb:settingsButton')}
            className="ml-auto grid h-8 w-8 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <Settings2 className="w-4 h-4" />
          </button>
        </div>
        {inv && (
          <KbOverviewCards inv={inv} docCount={docCount} active={section} onSelect={selectSection} />
        )}
      </div>

      <motion.section
        key={section}
        className="flex-1 min-h-0"
        initial={reduce || !animateSwitch ? false : { opacity: 0, y: 8, scale: 0.995 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={reduce ? { duration: 0.12 } : { type: 'spring', bounce: 0, duration: 0.3 }}
      >
        {section === 'index' ? (
          <IndexReader
            kb={id}
            selected={selected}
            page={page}
            pageError={pageError}
            selectedPath={selectedPath}
            hasPages={hasPages}
            inv={inv}
            onWikiLink={onWikiLink}
            onDeleted={onPageDeleted}
            onSaved={onPageSaved}
          />
        ) : section === 'documents' ? (
          <DocumentsPane
            kb={id}
            documents={documents}
            pendingDocuments={pendingDocuments}
            uploading={jobs.uploading}
            dragActive={dragActive}
            fileInputRef={fileInputRef}
            onDragActiveChange={setDragActive}
            onUpload={jobs.doUpload}
            onRefresh={refreshInventory}
            onDelete={onDeleteDocument}
            taskFiles={jobs.taskFiles}
            selectedLogs={jobs.selectedLogs}
            cancellingJobIds={jobs.cancellingJobIds}
            onCancelFile={jobs.cancelFile}
            onRetryFile={jobs.retryFile}
            onCompilePendingFile={jobs.compilePendingFile}
            onDeleteFile={jobs.deleteFile}
          />
        ) : section === 'legal-graph' ? (
          <LegalGraphView kb={id} />
        ) : section === 'lifecycle' ? (
          <LifecycleView kb={id} />
        ) : section === 'sync-sources' ? (
          <SyncSourcesView kb={id} />
        ) : (
          <div className="h-full flex">
            <div className="w-[300px] shrink-0 border-r border-[hsl(var(--glass-border))] glass-2 flex flex-col min-h-0">
              {invError ? (
                <div className="m-2 rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-200/70 dark:border-red-500/25 px-3 py-2 text-[12px] text-red-600 dark:text-red-400">
                  {t('kb:loadError', { error: invError })}
                </div>
              ) : !inv ? (
                <div className="px-4 py-3 text-[12px] text-muted-foreground">{t('common:loading')}</div>
              ) : (
                <div className="flex-1 min-h-0">
                  <PageList key={section} inv={inv} type={section} activePath={selected?.path ?? null} onOpen={openPath} />
                </div>
              )}
              <div className="shrink-0 m-2 mt-1 rounded-lg border border-dashed border-[hsl(var(--glass-border))] px-3 py-2 text-[11px] text-muted-foreground leading-relaxed">
                {t('kb:wikiNote')}
              </div>
            </div>
            <Reader
              kb={id}
              selected={selected}
              page={page}
              pageError={pageError}
              selectedPath={selectedPath}
              hasPages={hasPages}
              inv={inv}
              onWikiLink={onWikiLink}
              onDeleted={onPageDeleted}
              onSaved={onPageSaved}
            />
          </div>
        )}
      </motion.section>

      <KbSettingsSheet
        kb={id}
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        docCount={docCount}
        onChanged={refreshInventory}
        onDeleted={() => {
          setSettingsOpen(false)
          // Refresh the sidebar's KB list (same event CreateKbDialog fires) so
          // the just-deleted KB disappears without a manual reload.
          window.dispatchEvent(new CustomEvent('openkb:reload-kbs'))
          navigate('/kb')
        }}
      />
    </div>
  )
}

/** Shared props for the page-content column, whether it renders full-width
 *  (Index) or beside the 300px `PageList` sidebar (a type card). */
interface ReaderProps {
  /** KB name — the `kb` param for the page edit/delete/links endpoints. */
  kb: string
  selected: SelectedPage | null
  page: { path: string; content: string } | null
  pageError: { path: string; message: string } | null
  selectedPath: string | null
  hasPages: boolean
  inv: KbInventory | null
  /** Navigate to a `[[wikilink]]` target clicked inside the rendered page. */
  onWikiLink: (target: string) => void
  /** The open page was deleted: close it and refresh the inventory. */
  onDeleted: () => void
  /** The open page was saved: adopt `content` (the saved file, frontmatter and
   *  all) or re-fetch when null, then refresh the inventory. */
  onSaved: (path: string, content: string | null) => void
}

/** The actual page body: breadcrumb + Markdown, or an empty/loading state.
 *  Shared verbatim by `Reader` (Browse) and `IndexReader` (Index, full width) —
 *  they differ only in their outer scroll container. Concept/entity pages also
 *  carry inline Edit/Delete controls (F2/F3): delete previews its backlink
 *  impact via a dry-run before the destructive call; edit swaps the rendered
 *  Markdown for a body-only textarea plus a read-only outlink/backlink panel. */
function ReaderBody({ kb, selected, page, pageError, selectedPath, hasPages, inv, onWikiLink, onDeleted, onSaved }: ReaderProps) {
  const { t } = useTranslation(['kb', 'common'])

  // Edit mode (F3). `links`/`linksError` are tagged with the path they belong
  // to (same discipline as `page`/`pageError` in KbDetail) so a slow response
  // never renders under a different page.
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [links, setLinks] = useState<{ path: string; outlinks: string[]; backlinks: string[] } | null>(null)
  const [linksError, setLinksError] = useState<{ path: string; message: string } | null>(null)

  // Delete confirm (F2): `deleteImpact` holds the dry-run backlink preview for
  // the page awaiting confirmation (path-tagged like the edit state above).
  const [checkingDelete, setCheckingDelete] = useState(false)
  const [deleteImpact, setDeleteImpact] = useState<{ path: string; backlinks: string[] } | null>(null)
  const [deleting, setDeleting] = useState(false)

  // Switching pages must never leak edit/confirm state into the next page.
  useEffect(() => {
    setEditing(false)
    setDraft('')
    setSaving(false)
    setLinks(null)
    setLinksError(null)
    setCheckingDelete(false)
    setDeleteImpact(null)
    setDeleting(false)
  }, [selectedPath])

  const pageReady = page && page.path === selectedPath
  const pageFailed = pageError && pageError.path === selectedPath

  if (!selected) {
    return (
      <div className="h-full grid place-items-center text-[13px] text-muted-foreground">
        {inv && !hasPages ? t('kb:reader.empty') : t('kb:reader.selectPage')}
      </div>
    )
  }

  // Editable: concept/entity synthesis + per-document summaries (all compiled
  // markdown a recompile can regenerate). Deletable is narrower — a summary is
  // removed by deleting its source document, never on its own. reports/index.md
  // are generated artifacts the backend refuses to mutate either way.
  const canEdit =
    selected.group === 'concepts/' ||
    selected.group === 'entities/' ||
    selected.group === 'summaries/'
  const canDelete = selected.group === 'concepts/' || selected.group === 'entities/'
  const confirmActive = deleteImpact !== null && deleteImpact.path === selected.path
  const linksReady = links && links.path === selected.path
  const linksFailed = linksError && linksError.path === selected.path

  const startEdit = () => {
    if (!page || page.path !== selected.path) return
    setDraft(page.content)
    setEditing(true)
    const path = selected.path
    setLinks(null)
    setLinksError(null)
    getPageLinks(kb, path)
      .then((r) => setLinks({ path, outlinks: r.outlinks, backlinks: r.backlinks }))
      .catch((e) => setLinksError({ path, message: errMsg(e) }))
  }

  const doSave = async () => {
    if (saving) return
    setSaving(true)
    try {
      const res = await editPage(kb, selected.path, draft)
      const ghosts = res.ghosts_stripped ?? []
      if (ghosts.length > 0) {
        toast.warning(t('kb:pageOps.ghostsDemoted', { links: ghosts.join(', ') }))
      } else {
        toast.success(t('kb:pageOps.saveSuccess'))
      }
      onSaved(selected.path, res.content)
      setEditing(false)
      setDraft('')
    } catch (e) {
      // Keep edit mode (and the draft) so a transient failure loses nothing.
      toast.error(t('kb:pageOps.saveError', { error: errMsg(e) }))
    } finally {
      setSaving(false)
    }
  }

  const startDelete = async () => {
    if (checkingDelete) return
    setCheckingDelete(true)
    const path = selected.path
    try {
      const res = await deletePage(kb, path, true)
      setDeleteImpact({ path, backlinks: res.backlinks ?? [] })
    } catch (e) {
      toast.error(t('kb:pageOps.deleteError', { error: errMsg(e) }))
    } finally {
      setCheckingDelete(false)
    }
  }

  const doDelete = async () => {
    if (deleting) return
    setDeleting(true)
    try {
      await deletePage(kb, selected.path)
      toast.success(t('kb:pageOps.deleteSuccess', { title: selected.title }))
      onDeleted()
    } catch (e) {
      toast.error(t('kb:pageOps.deleteError', { error: errMsg(e) }))
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="w-full max-w-[1600px] mx-auto px-8 lg:px-12 py-7 anim-fade-up" key={selected.path}>
      <div className="flex items-center gap-2 text-[11.5px] text-muted-foreground mb-4">
        <span className="font-mono2 bg-muted rounded px-1.5 py-0.5">
          wiki/{selected.group}
          {selected.title}
        </span>
        {(canEdit || canDelete) && pageReady && !editing && !confirmActive && (
          <div className="ml-auto flex items-center gap-1.5">
            {canEdit && (
              <button
                onClick={startEdit}
                className="inline-flex items-center gap-1 h-7 px-2 rounded-lg text-[12px] font-medium text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
              >
                <Pencil className="w-3 h-3" />
                {t('kb:pageOps.edit')}
              </button>
            )}
            {canDelete && (
              <button
                onClick={startDelete}
                disabled={checkingDelete}
                className="inline-flex items-center gap-1 h-7 px-2 rounded-lg text-[12px] font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 transition-colors disabled:opacity-60"
              >
                {checkingDelete ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
                {t('kb:pageOps.delete')}
              </button>
            )}
          </div>
        )}
      </div>

      {/* Delete confirm (F2): dry-run impact preview + Confirm/Cancel. */}
      {confirmActive && (
        <div className="mb-4 rounded-2xl border border-red-200/70 dark:border-red-500/25 bg-red-50/50 dark:bg-red-500/5 px-4 py-3.5 space-y-2">
          <p className="text-[13px] font-medium text-foreground">{t('kb:pageOps.deletePrompt', { title: selected.title })}</p>
          <p className="text-[12px] text-muted-foreground">
            {deleteImpact.backlinks.length > 0
              ? t('kb:pageOps.deleteImpact', { count: deleteImpact.backlinks.length })
              : t('kb:pageOps.deleteNoBacklinks')}
          </p>
          {deleteImpact.backlinks.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {deleteImpact.backlinks.map((b) => (
                <span key={b} className="font-mono2 text-[11px] text-muted-foreground bg-muted rounded px-1.5 py-0.5">
                  {b}
                </span>
              ))}
            </div>
          )}
          <div className="flex items-center gap-2 pt-1">
            <button
              onClick={doDelete}
              disabled={deleting}
              className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg bg-red-600 text-white text-[12.5px] font-medium hover:bg-red-700 transition-colors disabled:opacity-50"
            >
              {deleting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
              {t('kb:pageOps.deleteConfirm')}
            </button>
            <button
              onClick={() => setDeleteImpact(null)}
              disabled={deleting}
              className="inline-flex items-center h-8 px-3 rounded-lg border border-[hsl(var(--glass-border))] text-[12.5px] font-medium text-muted-foreground hover:bg-accent transition-colors disabled:opacity-60"
            >
              {t('common:actions.cancel')}
            </button>
          </div>
        </div>
      )}

      {pageFailed ? (
        <div className="rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-200/70 dark:border-red-500/25 px-3 py-2 text-[13px] text-red-600 dark:text-red-400">
          {t('common:pageLoadError', { error: pageError.message })}
        </div>
      ) : editing && pageReady ? (
        /* Edit mode (F3): body-only textarea + save/cancel + links panel. */
        <div className="space-y-3">
          <textarea
            value={draft}
            disabled={saving}
            spellCheck={false}
            onChange={(e) => setDraft(e.target.value)}
            aria-label={t('kb:pageOps.editorAria')}
            className="w-full min-h-[420px] rounded-xl border border-input bg-transparent px-4 py-3 text-[13px] leading-relaxed font-mono2 outline-none focus-visible:ring-2 focus-visible:ring-ring focus:border-accent-brand resize-y"
          />
          <p className="text-[11.5px] text-muted-foreground">{t('kb:pageOps.editRecompileNote')}</p>
          <div className="flex items-center gap-2">
            <button
              onClick={doSave}
              disabled={saving}
              className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg bg-accent-brand text-white text-[12.5px] font-medium hover:bg-accent-brand/90 transition-colors disabled:opacity-50"
            >
              {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
              {t('kb:pageOps.save')}
            </button>
            <button
              onClick={() => {
                setEditing(false)
                setDraft('')
              }}
              disabled={saving}
              className="inline-flex items-center h-8 px-3 rounded-lg border border-[hsl(var(--glass-border))] text-[12.5px] font-medium text-muted-foreground hover:bg-accent transition-colors disabled:opacity-60"
            >
              {t('common:actions.cancel')}
            </button>
          </div>
          <div className="rounded-2xl border border-[hsl(var(--glass-border))] glass-2 px-4 py-3.5">
            <h3 className="flex items-center gap-1.5 text-[12px] font-semibold text-muted-foreground tracking-wide">
              <Link2 className="w-3.5 h-3.5" />
              {t('kb:pageOps.links.heading')}
            </h3>
            {linksFailed ? (
              <div className="mt-2 text-[12px] text-red-600 dark:text-red-400">
                {t('kb:pageOps.links.error', { error: linksError.message })}
              </div>
            ) : linksReady ? (
              <div className="mt-2.5 grid gap-3 sm:grid-cols-2">
                <LinkRefList label={t('kb:pageOps.links.outlinks')} refs={links.outlinks} />
                <LinkRefList label={t('kb:pageOps.links.backlinks')} refs={links.backlinks} />
              </div>
            ) : (
              <div className="mt-2 flex items-center gap-2 text-[12px] text-muted-foreground">
                <Loader2 className="w-3 h-3 animate-spin" />
                {t('common:loading')}
              </div>
            )}
          </div>
        </div>
      ) : pageReady ? (
        <MarkdownView source={page.content} onWikiLink={onWikiLink} />
      ) : (
        <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />{t('common:loading')}
        </div>
      )}
    </div>
  )
}

/** One read-only column of `section/stem` page refs (outlinks or backlinks)
 *  in the edit-mode links panel. */
function LinkRefList({ label, refs }: { label: string; refs: string[] }) {
  const { t } = useTranslation(['kb'])
  return (
    <div>
      <div className="text-[11.5px] font-medium text-muted-foreground">{label}</div>
      {refs.length === 0 ? (
        <div className="mt-1 text-[12px] text-muted-foreground/70">{t('kb:pageOps.links.none')}</div>
      ) : (
        <div className="mt-1 flex flex-wrap gap-1.5">
          {refs.map((r) => (
            <span key={r} className="font-mono2 text-[11px] text-muted-foreground bg-muted rounded px-1.5 py-0.5">
              {r}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

/** Reader column next to the `PageList` sidebar (a type card section). */
function Reader(props: ReaderProps) {
  return (
    <div className="flex-1 min-w-0 overflow-y-auto scroll-edge-top">
      <ReaderBody {...props} />
    </div>
  )
}

/** Same reader, full width, with no `PageList` sidebar — Index section. */
function IndexReader(props: ReaderProps) {
  return (
    <div className="h-full overflow-y-auto scroll-edge-top">
      <ReaderBody {...props} />
    </div>
  )
}

type DocumentsTab = 'jobs' | 'remote'

type SourceDeleteTarget =
  | { kind: 'compiled'; document: WikiDocument }
  | { kind: 'pending'; document: PendingDocument }

/** Documents workspace: compiling work, the source library, and planned remote
 * sources each get a focused tab instead of competing in one long scroll. */

function DocumentsPane({
  kb,
  documents,
  pendingDocuments,
  uploading,
  dragActive,
  fileInputRef,
  onDragActiveChange,
  onUpload,
  onRefresh,
  onDelete,
  taskFiles,
  selectedLogs,
  cancellingJobIds,
  onCancelFile,
  onRetryFile,
  onCompilePendingFile,
  onDeleteFile,
}: {
  kb: string
  documents: WikiDocument[]
  pendingDocuments: PendingDocument[]
  uploading: boolean
  dragActive: boolean
  fileInputRef: RefObject<HTMLInputElement | null>
  onDragActiveChange: (active: boolean) => void
  onUpload: (files: File[]) => void
  onRefresh: () => void
  onDelete: (identifier: string) => Promise<void>
  taskFiles: CompileTaskFile[]
  selectedLogs: UploadLogLine[]
  cancellingJobIds: ReadonlySet<string>
  onCancelFile: (file: CompileTaskFile) => void
  onRetryFile: (file: CompileTaskFile) => void
  onCompilePendingFile: (document: PendingDocument) => void
  onDeleteFile: (file: CompileTaskFile) => void
}) {
  const { t } = useTranslation(['kb', 'common'])
  const reduce = useReducedMotion()
  const [activeTab, setActiveTab] = useState<DocumentsTab>('jobs')
  const [previewDocument, setPreviewDocument] = useState<WikiDocument | null>(null)
  const [recompilingDocumentNames, setRecompilingDocumentNames] = useState<Set<string>>(new Set())
  const [deleteTarget, setDeleteTarget] = useState<SourceDeleteTarget | null>(null)
  const [deletingDocumentName, setDeletingDocumentName] = useState<string | null>(null)
  const [hiddenTaskFileIds, setHiddenTaskFileIds] = useState<Set<string>>(new Set())
  const visibleTaskFiles = useMemo(
    () => taskFiles.filter((file) => !hiddenTaskFileIds.has(file.id)),
    [hiddenTaskFileIds, taskFiles],
  )
  const fileCount = useMemo(
    () => new Set([...visibleTaskFiles.map((file) => file.name), ...documents.map((document) => document.name), ...pendingDocuments.map((document) => document.name)]).size,
    [documents, pendingDocuments, visibleTaskFiles],
  )
  const beginUpload = useCallback(
    (files: File[]) => {
      if (files.length === 0) return
      onUpload(files)
    },
    [onUpload],
  )
  const recompileDocument = useCallback(async (document: WikiDocument) => {
    if (recompilingDocumentNames.has(document.name)) return
    setRecompilingDocumentNames((names) => new Set(names).add(document.name))
    try {
      for await (const event of runRecompile(kb, document.name)) {
        if (event.event === 'error') throw new Error(String(event.data?.message ?? 'Recompile failed'))
      }
      onRefresh()
    } catch (cause) {
      toast.error(errMsg(cause))
    } finally {
      setRecompilingDocumentNames((names) => {
        const next = new Set(names)
        next.delete(document.name)
        return next
      })
    }
  }, [kb, onRefresh, recompilingDocumentNames])
  const confirmDeleteDocument = useCallback(async () => {
    if (!deleteTarget || deletingDocumentName) return
    setDeletingDocumentName(deleteTarget.document.name)
    try {
      if (deleteTarget.kind === 'compiled') {
        const document = deleteTarget.document
        await onDelete(document.name)
        if (previewDocument?.hash === document.hash) setPreviewDocument(null)
      } else {
        const document = deleteTarget.document
        const result = await deletePendingDocument(kb, document)
        setHiddenTaskFileIds((ids) => {
          const next = new Set(ids)
          taskFiles
            .filter((file) => file.sourcePath === document.path || file.name === document.name)
            .forEach((file) => next.add(file.id))
          return next
        })
        toast.success(t('kb:docs.delete.success', { name: result.name || document.name }))
        await onRefresh()
      }
      setDeleteTarget(null)
    } catch (cause) {
      toast.error(t('kb:docs.delete.error', { error: errMsg(cause) }))
    } finally {
      setDeletingDocumentName(null)
    }
  }, [deleteTarget, deletingDocumentName, kb, onDelete, onRefresh, previewDocument, t, taskFiles])

  return (
    <>
      <div className="h-full overflow-y-auto scroll-edge-top">
        <div className="mx-auto max-w-[1120px] px-6 py-7 lg:px-10">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => {
              beginUpload(Array.from(e.target.files ?? []))
              e.target.value = ''
            }}
          />

          <div className="flex flex-col gap-5 border-b border-[hsl(var(--glass-border))] pb-5 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-accent-brand">
                <span className="h-1.5 w-1.5 rounded-full bg-accent-brand" />
                {t('kb:workspace.eyebrow')}
              </div>
              <h2 className="mt-2 text-[25px] font-bold tracking-tight text-foreground">{t('kb:workspace.title')}</h2>
              <p className="mt-1.5 max-w-xl text-[13px] leading-relaxed text-muted-foreground">{t('kb:upload.note')}</p>
            </div>
            <button
              type="button"
              onClick={() => !uploading && fileInputRef.current?.click()}
              disabled={uploading}
              className="inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-accent-brand px-3.5 text-[12.5px] font-semibold text-white shadow-sm transition hover:bg-accent-brand/90 disabled:cursor-wait disabled:opacity-60"
            >
              {uploading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
              {uploading ? t('kb:upload.inProgress') : t('kb:workspace.addFiles')}
            </button>
          </div>

          <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as DocumentsTab)} className="mt-5 gap-0">
            <TabsList className="h-auto w-full justify-start gap-1 overflow-x-auto rounded-none border-b border-[hsl(var(--glass-border))] bg-transparent p-0">
              <WorkspaceTab value="jobs" icon={ListChecks} label={t('kb:workspace.tabs.jobs')} count={fileCount} />
              <WorkspaceTab value="remote" icon={Cloud} label={t('kb:workspace.tabs.remote')} />
            </TabsList>
          </Tabs>

          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={activeTab}
              initial={reduce ? false : { opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reduce ? undefined : { opacity: 0, y: -5 }}
              transition={reduce ? { duration: 0.1 } : { duration: 0.18, ease: 'easeOut' }}
              className="py-6"
            >
              {activeTab === 'jobs' && (
                <CompileJobsTab
                  dragActive={dragActive}
                  uploading={uploading}
                  onDragActiveChange={onDragActiveChange}
                  onUpload={beginUpload}
                  onChooseFiles={() => fileInputRef.current?.click()}
                  taskFiles={visibleTaskFiles}
                  documents={documents}
                  pendingDocuments={pendingDocuments}
                  selectedLogs={selectedLogs}
                  cancellingJobIds={cancellingJobIds}
                  onCancelFile={onCancelFile}
                  onRetryFile={onRetryFile}
                  onCompilePendingFile={onCompilePendingFile}
                  onDeleteFile={onDeleteFile}
                  onPreviewDocument={setPreviewDocument}
                  recompilingDocumentNames={recompilingDocumentNames}
                  onRecompileDocument={recompileDocument}
                  onRequestDeleteDocument={(document) => setDeleteTarget({ kind: 'compiled', document })}
                  onRequestDeletePending={(document) => setDeleteTarget({ kind: 'pending', document })}
                  deletingDocumentName={deletingDocumentName}
                />
              )}
              {activeTab === 'remote' && <RemoteSourcesTab />}
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
      <DocumentPreviewDrawer kb={kb} document={previewDocument} onClose={() => setPreviewDocument(null)} />
      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open && !deletingDocumentName) setDeleteTarget(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('kb:docs.delete.prompt')}</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget?.document.name}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={!!deletingDocumentName}>{t('kb:docs.delete.cancel')}</AlertDialogCancel>
            <AlertDialogAction
              disabled={!!deletingDocumentName}
              onClick={() => void confirmDeleteDocument()}
              className="bg-red-600 text-white hover:bg-red-700 dark:bg-red-600 dark:hover:bg-red-700"
            >
              {deletingDocumentName ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
              {t('kb:docs.delete.confirm')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

/** Collapsible DocIR outline for the source preview. Clicking a numbered node
 * switches the preview to that source page. */
function DocirOutline({ kb, hash, onJumpPage }: { kb: string; hash: string; onJumpPage: (page: number) => void }) {
  const { t } = useTranslation('legal')
  const [open, setOpen] = useState(false)
  const [root, setRoot] = useState<DocirNode | null>(null)

  useEffect(() => {
    let stale = false
    setRoot(null)
    getDocirByHash(kb, hash)
      .then((result) => {
        if (!stale) setRoot(result.docir?.root ?? null)
      })
      .catch(() => {
        if (!stale) setRoot(null)
      })
    return () => {
      stale = true
    }
  }, [kb, hash])

  if (!root) return null
  return (
    <div className="mb-5 rounded-lg border border-[hsl(var(--glass-border))] bg-muted/20">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-[12px] font-semibold text-foreground"
      >
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        <ListTree className="h-3.5 w-3.5 text-muted-foreground" />
        {t('reader.structure')}
      </button>
      {open && <div className="border-t border-[hsl(var(--glass-border))] px-2 py-2"><DocirNodeRow node={root} depth={0} onJumpPage={onJumpPage} /></div>}
    </div>
  )
}

function DocirNodeRow({
  node,
  depth,
  onJumpPage,
}: {
  node: DocirNode
  depth: number
  onJumpPage: (page: number) => void
}): ReactNode {
  const [expanded, setExpanded] = useState(depth < 1)
  const hasChildren = !!node.children?.length
  const page = node.loc?.page ?? null
  const visual = node.kind === 'figure_anchor'
  const label = node.title || node.vision?.text_anchor || (visual ? node.vision?.type : '') || node.kind
  return (
    <div>
      <div
        className="flex items-center gap-1 rounded px-1 py-0.5 hover:bg-[hsl(var(--glass-hover))]"
        style={{ paddingLeft: depth * 12 }}
      >
        {hasChildren ? (
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            className="grid h-3 w-3 shrink-0 place-items-center text-muted-foreground"
            aria-label={expanded ? 'collapse' : 'expand'}
          >
            {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          </button>
        ) : <span className="w-3 shrink-0" />}
        {visual ? <Eye className="h-3 w-3 shrink-0 text-amber-500" /> : <span className="w-3 shrink-0" />}
        {page != null ? (
          <button
            type="button"
            onClick={() => onJumpPage(page)}
            className="min-w-0 flex-1 truncate text-left text-[11.5px] text-foreground hover:underline"
            title={String(label)}
          >
            {label}<span className="ml-1 text-[10px] text-muted-foreground">p{page}</span>
          </button>
        ) : <span className="min-w-0 flex-1 truncate text-[11.5px] text-muted-foreground" title={String(label)}>{label}</span>}
      </div>
      {expanded && node.children?.map((child) => <DocirNodeRow key={child.id} node={child} depth={depth + 1} onJumpPage={onJumpPage} />)}
    </div>
  )
}

/** Source preview is intentionally attached to the unified task list: a
 * document remains readable without bringing back a separate library tab. */
function DocumentPreviewDrawer({
  kb,
  document,
  onClose,
}: {
  kb: string
  document: WikiDocument | null
  onClose: () => void
}) {
  const { t } = useTranslation('kb')
  const [source, setSource] = useState<DocumentSource | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    setPage(1)
  }, [document?.hash])

  useEffect(() => {
    if (!document) return
    let stale = false
    setLoading(true)
    setError(null)
    setSource(null)
    getDocumentSource(kb, document.hash, page)
      .then((result) => {
        if (!stale) setSource(result)
      })
      .catch((cause) => {
        if (!stale) setError(errMsg(cause))
      })
      .finally(() => {
        if (!stale) setLoading(false)
      })
    return () => {
      stale = true
    }
  }, [document, kb, page, reload])

  const totalPages = source?.total_pages ?? 1
  return (
    <Dialog.Root open={document !== null} onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30 backdrop-blur-[1px]" />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed inset-y-0 right-0 z-50 flex w-[min(92vw,860px)] flex-col border-l border-[hsl(var(--glass-border))] bg-background shadow-2xl focus:outline-none"
        >
          <div className="flex shrink-0 items-center gap-3 border-b border-[hsl(var(--glass-border))] px-5 py-3">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
              <FileText className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <Dialog.Title className="truncate text-[13.5px] font-medium text-foreground">{document?.name}</Dialog.Title>
              <p className="mt-0.5 text-[11.5px] text-muted-foreground">
                {document?.display_type} · {t('docs.reader.title')}
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              title={t('docs.reader.close')}
              aria-label={t('docs.reader.close')}
              className="ml-auto grid h-8 w-8 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
            <div className="mx-auto max-w-[72ch] px-6 py-6">
              {loading && (
                <div className="flex items-center justify-center gap-2 py-16 text-[13px] text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {t('docs.reader.loading')}
                </div>
              )}
              {error && (
                <div className="py-16 text-center">
                  <p className="text-[13px] text-red-600 dark:text-red-400">{t('docs.reader.error', { error })}</p>
                  <button
                    type="button"
                    onClick={() => setReload((value) => value + 1)}
                    className="mt-3 inline-flex h-8 items-center gap-1.5 rounded-lg border border-[hsl(var(--glass-border))] px-3 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-accent"
                  >
                    <RefreshCw className="h-3 w-3" />
                    {t('docs.reader.retry')}
                  </button>
                </div>
              )}
              {!loading && !error && source && (
                source.content.trim() ? (
                  <>
                    {document && <DocirOutline kb={kb} hash={document.hash} onJumpPage={setPage} />}
                    <MarkdownView source={source.content} />
                  </>
                ) : (
                  <div className="py-16 text-center text-[13px] text-muted-foreground">
                    {totalPages > 1 ? t('docs.reader.emptyPage') : t('docs.reader.emptyDoc')}
                  </div>
                )
              )}
            </div>
          </div>

          {totalPages > 1 && (
            <div className="flex shrink-0 items-center justify-center gap-2 border-t border-[hsl(var(--glass-border))] px-5 py-2.5">
              <button
                type="button"
                onClick={() => setPage((value) => Math.max(1, value - 1))}
                disabled={page <= 1}
                aria-label={t('docs.reader.prevPage')}
                className="grid h-8 w-8 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <span className="min-w-[5.5rem] text-center text-[12px] tabular-nums text-muted-foreground">
                {t('docs.reader.pageOf', { page, total: totalPages })}
              </span>
              <button
                type="button"
                onClick={() => setPage((value) => Math.min(totalPages, value + 1))}
                disabled={page >= totalPages}
                aria-label={t('docs.reader.nextPage')}
                className="grid h-8 w-8 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function WorkspaceTab({
  value,
  icon: Icon,
  label,
  count,
}: {
  value: DocumentsTab
  icon: LucideIcon
  label: string
  count?: number
}) {
  return (
    <TabsTrigger
      value={value}
      className="group h-10 flex-none gap-2 rounded-none border-x-0 border-t-0 border-b-2 border-transparent px-3 text-[12.5px] font-medium text-muted-foreground shadow-none transition-colors hover:text-foreground data-[state=active]:border-accent-brand data-[state=active]:bg-transparent data-[state=active]:text-foreground data-[state=active]:shadow-none"
    >
      <Icon className="h-3.5 w-3.5" />
      {label}
      {count != null && (
        <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10.5px] leading-none text-muted-foreground group-data-[state=active]:bg-accent-brand/10 group-data-[state=active]:text-accent-brand">
          {count}
        </span>
      )}
    </TabsTrigger>
  )
}

function CompileJobsTab({
  dragActive,
  uploading,
  onDragActiveChange,
  onUpload,
  onChooseFiles,
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
}: {
  dragActive: boolean
  uploading: boolean
  onDragActiveChange: (active: boolean) => void
  onUpload: (files: File[]) => void
  onChooseFiles: () => void
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
}) {
  const { t } = useTranslation('kb')
  return (
    <div className="flex flex-col gap-8">
      <section>
        <div className="flex items-baseline justify-between gap-4">
          <div>
            <h3 className="text-[14px] font-semibold text-foreground">{t('workspace.createTask')}</h3>
            <p className="mt-1 text-[12px] text-muted-foreground">{t('workspace.createTaskNote')}</p>
          </div>
        </div>
        <div
          onDragOver={(e) => {
            e.preventDefault()
            onDragActiveChange(true)
          }}
          onDragLeave={() => onDragActiveChange(false)}
          onDrop={(e) => {
            e.preventDefault()
            onDragActiveChange(false)
            onUpload(Array.from(e.dataTransfer.files))
          }}
          onClick={() => !uploading && onChooseFiles()}
          className={cn(
            'mt-3 min-h-[88px] cursor-pointer rounded-xl border-2 border-dashed px-3 py-3 text-left transition-all duration-200',
            'flex items-center gap-3',
            dragActive
              ? 'border-accent-brand bg-accent-brand/[0.07] shadow-[inset_0_0_0_1px_hsl(var(--accent-brand)/0.1)]'
              : 'border-[hsl(var(--glass-border))] bg-muted/[0.18] hover:border-accent-brand/50 hover:bg-accent-brand/[0.025]',
            uploading && 'pointer-events-none opacity-70',
          )}
        >
          <span className={cn('grid h-8 w-8 shrink-0 place-items-center rounded-lg transition-colors', dragActive ? 'bg-accent-brand text-white' : 'bg-background text-accent-brand shadow-sm')}>
            {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
          </span>
          <div className="min-w-0">
            <div className="text-[12px] font-semibold text-foreground">{uploading ? t('upload.inProgress') : t('upload.dropzone')}</div>
            <div className="mt-0.5 text-[10.5px] leading-relaxed text-muted-foreground">{t('upload.dropzoneHint')}</div>
          </div>
        </div>
      </section>

      <section className="min-w-0 border-t border-[hsl(var(--glass-border))] pt-6">
        <JobsPanel
          taskFiles={taskFiles}
          documents={documents}
          pendingDocuments={pendingDocuments}
          selectedLogs={selectedLogs}
          cancellingJobIds={cancellingJobIds}
          onCancelFile={onCancelFile}
          onRetryFile={onRetryFile}
          onCompilePendingFile={onCompilePendingFile}
          onDeleteFile={onDeleteFile}
          onPreviewDocument={onPreviewDocument}
          recompilingDocumentNames={recompilingDocumentNames}
          onRecompileDocument={onRecompileDocument}
          onRequestDeleteDocument={onRequestDeleteDocument}
          onRequestDeletePending={onRequestDeletePending}
          deletingDocumentName={deletingDocumentName}
        />
      </section>
    </div>
  )
}

function RemoteSourcesTab() {
  const { t } = useTranslation('kb')
  return (
    <section>
      <div className="max-w-2xl">
        <h3 className="text-[14px] font-semibold text-foreground">{t('remote.heading')}</h3>
        <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">{t('remote.note')}</p>
      </div>
      <div className="mt-6 border-y border-[hsl(var(--glass-border))] py-5">
        <ConnectorCards />
      </div>
    </section>
  )
}

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from 'react'
import { useNavigate, useParams } from 'react-router'
import { useTranslation } from 'react-i18next'
import * as Dialog from '@radix-ui/react-dialog'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { Cloud, FileText, FolderOpen, Link2, ListChecks, Loader2, Pencil, Plus, Upload, RefreshCw, Settings2, Trash2, X, BookOpen, ChevronLeft, ChevronRight, ChevronDown, ChevronsLeft, ChevronsRight, Eye, ListTree, type LucideIcon } from 'lucide-react'
import { toast } from 'sonner'
import { deletePage, editPage, getDocumentSource, getKbInventory, getPage, getPageLinks, type DocumentSource, type KbInventory, type WikiDocument } from '@/api/wiki'
import { getDocirByHash, type DocirNode } from '@/api/legal'
import { removeDocument } from '@/api/maintenance'
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

  // Compile-jobs engine: polls `listJobs`, tails the selected job's SSE stream
  // (re-attachable - a refresh or a click on an old job replays its rows + log
  // from the server's event ring), and dedups uploads by SHA-256 against the
  // KB's known document hashes so already-uploaded files aren't re-sent.
  const jobs = useJobs(id, {
    getKnownHashes: () =>
      new Set((inv?.documents ?? []).map((d) => d.hash).filter(Boolean)),
    onCompleted: refreshInventory,
  })


  /** Remove one document via `/api/v1/remove`, then refresh + toast. The
   *  identifier is the document's original filename (`WikiDocument.name`),
   *  which the backend resolves by exact-name match first. `/api/v1/remove`
   *  returns HTTP 200 for both `removed` (full success) and `partial` (local
   *  files gone, PageIndex cleanup failed), so success is claimed ONLY on
   *  `removed`; `partial` warns and surfaces the PageIndex error. A 409
   *  multiple-match carries a structured `{ message, candidates }` detail — its
   *  candidate names are shown so the user can disambiguate. */
  const onDeleteDocument = useCallback(
    async (identifier: string) => {
      try {
        const res = await removeDocument(id, identifier)
        if (res.status === 'partial') {
          // HTTP 200, but PageIndex cleanup failed: local wiki files were removed
          // while the remote index was not. Warn (not success) and say why.
          const reason = res.pageindex_error || res.message || ''
          toast.warning(
            t('kb:docs.delete.partial', { name: res.name || identifier }) +
              (reason ? t('kb:docs.delete.reasonSuffix', { reason }) : ''),
          )
        } else {
          toast.success(t('kb:docs.delete.success', { name: res.name || identifier }))
        }
        await refreshInventory()
      } catch (e) {
        // A 409 multiple-match carries a structured detail `{ message, candidates }`
        // (see client.ts `ApiError.detail`); show the message + candidate names so
        // the user can pick a more specific identifier, instead of a raw JSON blob.
        const detail =
          e instanceof ApiError
            ? (e.detail as { message?: string; candidates?: Array<{ name?: string; doc_name?: string }> } | undefined)
            : undefined
        const candidates = detail?.candidates
        if (candidates && candidates.length > 0) {
          const names = candidates.map((c) => c.name || c.doc_name || '?').join(', ')
          toast.error(t('kb:docs.delete.multiple', { message: detail?.message || errMsg(e), names }))
        } else {
          toast.error(t('kb:docs.delete.error', { error: errMsg(e) }))
        }
      }
    },
    [id, refreshInventory, t],
  )

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
            invError={invError}
            uploading={jobs.uploading}
            dragActive={dragActive}
            fileInputRef={fileInputRef}
            onDragActiveChange={setDragActive}
            onUpload={jobs.doUpload}
            onCancelUpload={jobs.cancelUpload}
            onRefresh={refreshInventory}
            onDelete={onDeleteDocument}
            taskFiles={jobs.taskFiles}
            selectedLogs={jobs.selectedLogs}
            selectedRunning={jobs.selectedRunning}
            selectedCancelling={jobs.selectedCancelling}
            onRetryFile={jobs.retryFile}
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

/** Read-only slide-out reader for a document's converted source text.
 *
 * Presentational: the parent (DocumentsPane) owns the fetch, per-hash cache,
 * and memoized body, so this can unmount on close (Radix Dialog) yet re-open
 * instantly with un-reparsed Markdown. Radix Dialog supplies the modal a11y —
 * focus trap, initial + return focus, Escape, and background inert — and
 * `shown` keeps the last doc rendered through the exit animation (doc goes null
 * on close). The body scrolls independently and native find-in-page works (the
 * whole document is rendered, not virtualized). Documents are read-only
 * ingestion artifacts, so there is no edit affordance. */
/** DocIR structure outline for the reader (UI_INTEGRATION_PLAN §5).
 *  Fetches the document's DocIR by content hash and renders the recursive
 *  section tree (part/chapter/section) with visual-node markers; clicking a node that has
 *  a page jumps the reader to it. Hidden when no DocIR exists for the doc. */
function DocirOutline({ kb, hash, onJumpPage }: { kb: string; hash: string; onJumpPage: (p: number) => void }) {
  const { t } = useTranslation('legal')
  const [open, setOpen] = useState(false)
  const [root, setRoot] = useState<DocirNode | null>(null)
  const [loaded, setLoaded] = useState(false)
  useEffect(() => {
    let cancelled = false
    setLoaded(false)
    setRoot(null)
    getDocirByHash(kb, hash)
      .then((r) => {
        if (!cancelled) setRoot(r.docir?.root ?? null)
      })
      .catch(() => {
        if (!cancelled) setRoot(null)
      })
      .finally(() => {
        if (!cancelled) setLoaded(true)
      })
    return () => {
      cancelled = true
    }
  }, [kb, hash])
  if (!loaded || !root) return null
  const renderNode = (n: DocirNode, depth: number): ReactNode => {
    const isVisual = n.kind === 'figure_anchor'
    const page = n.loc?.page ?? null
    const hasChildren = !!n.children && n.children.length > 0
    const label = n.title || n.vision?.text_anchor || (isVisual ? n.vision?.type : '') || n.kind
    if (hasChildren) {
      return (
        <DocirToggle
          key={n.id}
          node={n}
          depth={depth}
          isVisual={isVisual}
          page={page}
          label={label}
          onJumpPage={onJumpPage}
          renderNode={renderNode}
        />
      )
    }
    // Leaf: a single row indented by depth. No toggle, no children.
    return (
      <div
        key={n.id}
        className="flex items-center gap-1 rounded px-1 py-0.5 hover:bg-[hsl(var(--glass-hover))]"
        style={{ paddingLeft: depth * 12 }}
      >
        <span className="w-3 shrink-0" />
        {isVisual ? (
          <Eye className="h-3 w-3 shrink-0 text-amber-500" />
        ) : (
          <span className="w-3 shrink-0" />
        )}
        {page != null ? (
          <button
            onClick={() => onJumpPage(page)}
            className="min-w-0 flex-1 truncate text-left text-[11.5px] text-foreground hover:underline"
            title={String(label)}
          >
            {label}
            <span className="ml-1 text-[10px] text-muted-foreground">p{page}</span>
          </button>
        ) : (
          <span className="min-w-0 flex-1 truncate text-[11.5px] text-muted-foreground" title={String(label)}>
            {label}
          </span>
        )}
      </div>
    )
  }
  return (
    <div className="mb-4 rounded-lg border border-[hsl(var(--glass-border))] bg-muted/20">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-[12px] font-semibold text-foreground"
      >
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        <ListTree className="h-3.5 w-3.5 text-muted-foreground" />
        {t('reader.structure')}
      </button>
      {open && <div className="border-t border-[hsl(var(--glass-border))] px-2 py-2">{renderNode(root, 0)}</div>}
    </div>
  )
}

/** Expandable node with children: renders its own row, then stacks descendants
 *  vertically below it. Children MUST be siblings of the row (block flow), not
 *  nested inside the row's flex container - otherwise they lay out horizontally
 *  beside the toggle instead of dropping down as a sub-tree. */
function DocirToggle({
  node,
  depth,
  isVisual,
  page,
  label,
  onJumpPage,
  renderNode,
}: {
  node: DocirNode
  depth: number
  isVisual: boolean
  page: number | null
  label: string
  onJumpPage: (p: number) => void
  renderNode: (n: DocirNode, d: number) => ReactNode
}) {
  const [exp, setExp] = useState(depth < 1)
  return (
    <div>
      <div
        className="flex items-center gap-1 rounded px-1 py-0.5 hover:bg-[hsl(var(--glass-hover))]"
        style={{ paddingLeft: depth * 12 }}
      >
        <button
          onClick={() => setExp((v) => !v)}
          className="grid h-3 w-3 shrink-0 place-items-center text-muted-foreground"
          aria-label={exp ? 'collapse' : 'expand'}
        >
          {exp ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        </button>
        {isVisual ? (
          <Eye className="h-3 w-3 shrink-0 text-amber-500" />
        ) : (
          <span className="w-3 shrink-0" />
        )}
        {page != null ? (
          <button
            onClick={() => onJumpPage(page)}
            className="min-w-0 flex-1 truncate text-left text-[11.5px] text-foreground hover:underline"
            title={String(label)}
          >
            {label}
            <span className="ml-1 text-[10px] text-muted-foreground">p{page}</span>
          </button>
        ) : (
          <span className="min-w-0 flex-1 truncate text-[11.5px] text-muted-foreground" title={String(label)}>
            {label}
          </span>
        )}
      </div>
      {exp && node.children?.map((c) => renderNode(c, depth + 1))}
    </div>
  )
}

function DocumentReaderDrawer({
  doc,
  body,
  loading,
  error,
  isEmpty,
  kb,
  onJumpPage,
  page,
  totalPages,
  onFirst,
  onPrev,
  onNext,
  onLast,
  onRetry,
  onClose,
}: {
  doc: WikiDocument | null
  body: ReactNode
  loading: boolean
  error: string | null
  isEmpty: boolean
  kb?: string
  onJumpPage?: (p: number) => void
  page: number
  totalPages: number
  onFirst: () => void
  onPrev: () => void
  onNext: () => void
  onLast: () => void
  onRetry: () => void
  onClose: () => void
}) {
  const { t } = useTranslation(['kb', 'common'])
  const reduce = useReducedMotion()
  // The control that opened the drawer, captured before Radix moves focus in,
  // so focus returns to it on close (mirrors KbSettingsSheet).
  const openerRef = useRef<HTMLElement | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const open = doc !== null
  // Keep the last doc visible during the exit animation (doc is null once closed).
  const [shown, setShown] = useState<WikiDocument | null>(doc)
  useEffect(() => {
    if (doc) setShown(doc)
  }, [doc])
  // Reset scroll to top when switching documents or turning pages.
  useEffect(() => {
    if (doc) scrollRef.current?.scrollTo(0, 0)
  }, [doc, page])
  // `modal={false}` (below) sidesteps Radix's react-remove-scroll, whose
  // document-level wheel listener cancels wheel over the reader body via a
  // React-18 timing gap. The trade-off: Radix no longer applies its own
  // body-scroll lock or Esc-to-close (the DismissableLayer escape listener
  // only attaches when it is the highest layer, which is unreliable with
  // `forceMount` + `AnimatePresence` ref timing). Restore both here.
  useEffect(() => {
    if (!open) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      const t = e.target as HTMLElement | null
      const tag = t?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || t?.isContentEditable) return
      onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => {
      document.body.style.overflow = prev
      window.removeEventListener('keydown', onKey)
    }
  }, [open, onClose])

  return (
    <Dialog.Root
      open={open}
      modal={false}
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
    >
      <AnimatePresence>
        {open && (
          <Dialog.Portal forceMount>
            {/* Radix `Dialog.Overlay` renders null when `modal={false}`
                (see @radix-ui/react-dialog: `context.modal ? ... : null`),
                so render the backdrop as a plain motion.div instead - it
                still portals above the page and `onClick` closes. */}
            <motion.div
              className="fixed inset-0 z-40 bg-black/30"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={reduce ? { duration: 0.12 } : { duration: 0.2 }}
              onClick={(e) => {
                // Only close on the backdrop itself; clicks that originate in
                // the content must not dismiss the drawer.
                if (e.target === e.currentTarget) onClose()
              }}
            />
            <Dialog.Content
              asChild
              forceMount
              aria-describedby={undefined}
              onOpenAutoFocus={() => {
                openerRef.current = document.activeElement as HTMLElement | null
              }}
              onCloseAutoFocus={(e) => {
                e.preventDefault()
                openerRef.current?.focus()
              }}
              // Route outside-dismiss only through the overlay's own
              // `onClick` below: the backdrop is a sibling of the content
              // (hence "outside" to Radix), and `modal={false}` no longer
              // auto-closes on focus loss, so these guards avoid a redundant
              // DismissableLayer dismissal competing with the backdrop click.
              onPointerDownOutside={(e) => e.preventDefault()}
              onInteractOutside={(e) => e.preventDefault()}
            >
              <motion.aside
                className="fixed inset-y-0 right-0 z-50 flex w-[min(70vw,900px)] max-w-full flex-col glass border-l border-[hsl(var(--glass-border))] shadow-glass-lg rounded-l-apple-lg"
                initial={reduce ? { opacity: 0 } : { opacity: 0, x: 24 }}
                animate={reduce ? { opacity: 1 } : { opacity: 1, x: 0 }}
                exit={reduce ? { opacity: 0 } : { opacity: 0, x: 24 }}
                transition={reduce ? { duration: 0.12 } : { type: 'spring', bounce: 0, duration: 0.3 }}
                onClick={(e) => e.stopPropagation()}
              >
                <div className="flex shrink-0 items-center gap-3 border-b border-[hsl(var(--glass-border))] px-5 py-3">
                  <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-[hsl(var(--glass-border))] bg-muted">
                    <FileText className="h-4 w-4 text-muted-foreground" />
                  </span>
                  <div className="min-w-0">
                    <Dialog.Title asChild>
                      <div className="truncate text-[13.5px] font-medium text-foreground">{shown?.name}</div>
                    </Dialog.Title>
                    <div className="mt-0.5 flex items-center gap-2 text-[12px] text-muted-foreground">
                      {shown?.display_type && <span>{shown.display_type}</span>}
                      {shown?.pages != null && <span>· {t('kb:docs.pages', { count: shown.pages })}</span>}
                      <span className="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-px text-[11px]">
                        <BookOpen className="h-3 w-3" />
                        {t('kb:docs.reader.readonly')}
                      </span>
                    </div>
                  </div>
                  <button
                    onClick={onClose}
                    title={t('kb:docs.reader.close')}
                    aria-label={t('kb:docs.reader.close')}
                    className="ml-auto grid h-8 w-8 shrink-0 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>

                <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
                  <div className="mx-auto max-w-[72ch] px-6 py-6">
                    {loading && (
                      <div className="flex items-center justify-center gap-2 py-16 text-[13px] text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        {t('kb:docs.reader.loading')}
                      </div>
                    )}
                    {error && (
                      <div className="py-16 text-center">
                        <p className="text-[13px] text-red-600 dark:text-red-400">
                          {t('kb:docs.reader.error', { error })}
                        </p>
                        <button
                          onClick={onRetry}
                          className="mt-3 inline-flex h-8 items-center gap-1.5 rounded-lg border border-[hsl(var(--glass-border))] px-3 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-accent"
                        >
                          <RefreshCw className="h-3 w-3" />
                          {t('kb:docs.reader.retry')}
                        </button>
                      </div>
                    )}
                    {!loading && !error && isEmpty && (
                      <div className="py-16 text-center text-[13px] text-muted-foreground">
                        {totalPages > 1
                          ? t('kb:docs.reader.emptyPage')
                          : t('kb:docs.reader.emptyDoc')}
                      </div>
                    )}
                    {!loading && !error && !isEmpty && kb && shown?.hash && onJumpPage && (
                      <DocirOutline kb={kb} hash={shown.hash} onJumpPage={onJumpPage} />
                    )}
                    {!loading && !error && !isEmpty && (
                      <div className="text-[14px] leading-relaxed text-foreground">{body}</div>
                    )}
                  </div>
                </div>

                {/* Page navigation: hidden for single-page (short) docs. Prev/next
                    disabled at the ends so keyboard activation is a no-op, not a
                    wrap-around. */}
                {totalPages > 1 && (
                  <div className="flex shrink-0 items-center justify-center gap-1.5 border-t border-[hsl(var(--glass-border))] px-5 py-2.5">
                    <button
                      type="button"
                      onClick={onFirst}
                      disabled={page <= 1}
                      aria-label={t('kb:docs.reader.firstPage')}
                      title={t('kb:docs.reader.firstPage')}
                      className="grid h-8 w-8 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
                    >
                      <ChevronsLeft className="h-4 w-4" />
                    </button>
                    <button
                      type="button"
                      onClick={onPrev}
                      disabled={page <= 1}
                      aria-label={t('kb:docs.reader.prevPage')}
                      title={t('kb:docs.reader.prevPage')}
                      className="grid h-8 w-8 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
                    >
                      <ChevronLeft className="h-4 w-4" />
                    </button>
                    <span className="min-w-[5.5rem] text-center text-[12px] tabular-nums text-muted-foreground">
                      {t('kb:docs.reader.pageOf', { page, total: totalPages })}
                    </span>
                    <button
                      type="button"
                      onClick={onNext}
                      disabled={page >= totalPages}
                      aria-label={t('kb:docs.reader.nextPage')}
                      title={t('kb:docs.reader.nextPage')}
                      className="grid h-8 w-8 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
                    >
                      <ChevronRight className="h-4 w-4" />
                    </button>
                    <button
                      type="button"
                      onClick={onLast}
                      disabled={page >= totalPages}
                      aria-label={t('kb:docs.reader.lastPage')}
                      title={t('kb:docs.reader.lastPage')}
                      className="grid h-8 w-8 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
                    >
                      <ChevronsRight className="h-4 w-4" />
                    </button>
                  </div>
                )}
              </motion.aside>
            </Dialog.Content>
          </Dialog.Portal>
        )}
      </AnimatePresence>
    </Dialog.Root>
  )
}

type DocumentsTab = 'jobs' | 'documents' | 'remote'

/** Documents workspace: compiling work, the source library, and planned remote
 * sources each get a focused tab instead of competing in one long scroll. */

function DocumentsPane({
  kb,
  documents,
  invError,
  uploading,
  dragActive,
  fileInputRef,
  onDragActiveChange,
  onUpload,
  onCancelUpload,
  onRefresh,
  onDelete,
  taskFiles,
  selectedLogs,
  selectedRunning,
  selectedCancelling,
  onRetryFile,
}: {
  kb: string
  documents: WikiDocument[]
  invError: string | null
  uploading: boolean
  dragActive: boolean
  fileInputRef: RefObject<HTMLInputElement | null>
  onDragActiveChange: (active: boolean) => void
  onUpload: (files: File[]) => void
  onCancelUpload: () => void
  onRefresh: () => void
  onDelete: (identifier: string) => Promise<void>
  taskFiles: CompileTaskFile[]
  selectedLogs: UploadLogLine[]
  selectedRunning: boolean
  selectedCancelling: boolean
  onRetryFile: (file: CompileTaskFile) => void
}) {
  const { t } = useTranslation(['kb', 'common'])
  const reduce = useReducedMotion()
  const [activeTab, setActiveTab] = useState<DocumentsTab>('jobs')
  // Inline delete confirm: `confirmName` is the row awaiting confirmation;
  // `deletingName` is the row whose remove request is in flight.
  const [confirmName, setConfirmName] = useState<string | null>(null)
  const [deletingName, setDeletingName] = useState<string | null>(null)
  // Document reader drawer. State lives HERE (not in the drawer, which unmounts
  // on close via Radix) so the per-hash cache and memoized body survive
  // open/close and re-opening is instant without re-parsing Markdown.
  const [openDoc, setOpenDoc] = useState<WikiDocument | null>(null)
  const [docSource, setDocSource] = useState<DocumentSource | null>(null)
  const [docLoading, setDocLoading] = useState(false)
  const [docError, setDocError] = useState<string | null>(null)
  const [docReloadSeq, setDocReloadSeq] = useState(0)
  // Current source page (1-indexed) for the open doc. Long docs paginate one
  // page per request; short docs are a single page.
  const [docPage, setDocPage] = useState(1)
  // Last known total page count for the open doc. Persisted across page-change
  // fetches (which null `docSource` while loading) so the footer - and the
  // focused prev/next button inside it - stays mounted; otherwise Radix Dialog
  // closes when the focused button unmounts mid-click.
  const [docTotalPages, setDocTotalPages] = useState(1)
  const sourceCache = useRef<Map<string, DocumentSource>>(new Map())
  const closeDrawer = useCallback(() => setOpenDoc(null), [])
  // Opening a (different) doc resets to page 1 and clears the cached total so
  // the footer doesn't briefly show the previous doc's page count. Done here
  // in the open handler (not an effect) to avoid a set-state-in-effect render
  // cascade and a flash of the wrong page.
  const openDocAt = useCallback((d: WikiDocument) => {
    setDocPage(1)
    setDocTotalPages(1)
    setOpenDoc(d)
  }, [])
  // Jump the open reader to a specific page (used by the DocIR structure tree).
  const jumpToPage = useCallback((p: number) => {
    setDocPage(p)
    setDocReloadSeq((s) => s + 1)
  }, [])
  const openHash = openDoc?.hash ?? null

  // Drop cached content when the inventory changes (a recompile can rewrite a
  // document's converted text under the same raw hash), so the next open
  // refetches instead of serving stale text.
  useEffect(() => {
    sourceCache.current.clear()
  }, [documents])

  // Fetch the open document's current page (per-`hash:page` cache; retry via
  // docReloadSeq). Cached pages render instantly without a refetch.
  useEffect(() => {
    if (!openHash) return
    const cacheKey = `${openHash}:${docPage}`
    const cached = sourceCache.current.get(cacheKey)
    if (cached) {
      setDocSource(cached)
      setDocError(null)
      setDocLoading(false)
      return
    }
    let cancelled = false
    setDocLoading(true)
    setDocSource(null)
    setDocError(null)
    getDocumentSource(kb, openHash, docPage)
      .then((r) => {
        if (cancelled) return
        sourceCache.current.set(cacheKey, r)
        setDocSource(r)
        setDocTotalPages(r.total_pages)
      })
      .catch((e) => {
        if (!cancelled) setDocError(errMsg(e))
      })
      .finally(() => {
        if (!cancelled) setDocLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [kb, openHash, docPage, docReloadSeq])

  // Parse Markdown once per fetched source (stable cache ref → no re-parse).
  const readerBody = useMemo(
    () =>
      docSource && docSource.content.trim() ? <MarkdownView source={docSource.content} /> : null,
    [docSource],
  )
  const readerEmpty = docSource != null && docSource.content.trim().length === 0

  const handleDelete = async (name: string) => {
    setDeletingName(name)
    try {
      await onDelete(name)
    } finally {
      setDeletingName(null)
      setConfirmName(null)
    }
  }

  const beginUpload = useCallback(
    (files: File[]) => {
      if (files.length === 0) return
      setActiveTab('jobs')
      onUpload(files)
    },
    [onUpload],
  )

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
              <WorkspaceTab value="jobs" icon={ListChecks} label={t('kb:workspace.tabs.jobs')} count={taskFiles.length} />
              <WorkspaceTab value="documents" icon={FolderOpen} label={t('kb:workspace.tabs.documents')} count={documents.length} />
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
                  taskFiles={taskFiles}
                  documents={documents}
                  selectedLogs={selectedLogs}
                  selectedRunning={selectedRunning}
                  selectedCancelling={selectedCancelling}
                  onCancelUpload={onCancelUpload}
                  onRetryFile={onRetryFile}
                />
              )}
              {activeTab === 'documents' && (
                <UploadedDocumentsTab
                  documents={documents}
                  invError={invError}
                  confirmName={confirmName}
                  deletingName={deletingName}
                  onRefresh={onRefresh}
                  onOpen={openDocAt}
                  onConfirmDelete={setConfirmName}
                  onDelete={handleDelete}
                />
              )}
              {activeTab === 'remote' && <RemoteSourcesTab />}
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
    <DocumentReaderDrawer
      doc={openDoc}
      body={readerBody}
      loading={docLoading}
      error={docError}
      isEmpty={readerEmpty}
      kb={kb}
      onJumpPage={jumpToPage}
      page={docSource?.page ?? docPage}
      totalPages={docSource?.total_pages ?? docTotalPages}
      onFirst={() => setDocPage(1)}
      onPrev={() => setDocPage((p) => Math.max(1, p - 1))}
      onNext={() => setDocPage((p) => p + 1)}
      onLast={() => setDocPage(docSource?.total_pages ?? docTotalPages)}
      onRetry={() => setDocReloadSeq((s) => s + 1)}
      onClose={closeDrawer}
    />
    </>
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
  selectedLogs,
  selectedRunning,
  selectedCancelling,
  onCancelUpload,
  onRetryFile,
}: {
  dragActive: boolean
  uploading: boolean
  onDragActiveChange: (active: boolean) => void
  onUpload: (files: File[]) => void
  onChooseFiles: () => void
  taskFiles: CompileTaskFile[]
  documents: WikiDocument[]
  selectedLogs: UploadLogLine[]
  selectedRunning: boolean
  selectedCancelling: boolean
  onCancelUpload: () => void
  onRetryFile: (file: CompileTaskFile) => void
}) {
  const { t } = useTranslation('kb')
  return (
    <div className="grid gap-6 lg:grid-cols-[240px_minmax(0,1fr)]">
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

      <section className="min-w-0 border-t border-[hsl(var(--glass-border))] pt-5 lg:border-l lg:border-t-0 lg:pl-8 lg:pt-0">
        <JobsPanel
          taskFiles={taskFiles}
          documents={documents}
          selectedLogs={selectedLogs}
          selectedRunning={selectedRunning}
          selectedCancelling={selectedCancelling}
          onCancelUpload={onCancelUpload}
          onRetryFile={onRetryFile}
        />
      </section>
    </div>
  )
}

function UploadedDocumentsTab({
  documents,
  invError,
  confirmName,
  deletingName,
  onRefresh,
  onOpen,
  onConfirmDelete,
  onDelete,
}: {
  documents: WikiDocument[]
  invError: string | null
  confirmName: string | null
  deletingName: string | null
  onRefresh: () => void
  onOpen: (document: WikiDocument) => void
  onConfirmDelete: (name: string | null) => void
  onDelete: (name: string) => Promise<void>
}) {
  const { t } = useTranslation(['kb', 'common'])
  return (
    <section>
      <div className="flex items-end justify-between gap-4">
        <div>
          <h3 className="text-[14px] font-semibold text-foreground">{t('docs.heading', { count: documents.length })}</h3>
          <p className="mt-1 text-[12px] text-muted-foreground">{t('workspace.libraryNote')}</p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <RefreshCw className="h-3.5 w-3.5" />{t('common:actions.refresh')}
        </button>
      </div>
      <div className="mt-5 border-y border-[hsl(var(--glass-border))]">
        {invError && (
          <div className="my-3 rounded-lg border border-red-200/70 bg-red-50 px-3 py-2 text-[12px] text-red-600 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-400">
            {t('loadError', { error: invError })}
          </div>
        )}
        {!invError && documents.length === 0 && (
          <div className="py-14 text-center text-[13px] text-muted-foreground">{t('docs.empty')}</div>
        )}
        {documents.map((d, i) => (
          <div
            key={d.hash || d.name || i}
            className="group flex items-center gap-3 border-b border-[hsl(var(--glass-border))] py-3 last:border-b-0"
          >
            <button
              type="button"
              onClick={() => onOpen(d)}
              title={t('docs.reader.open')}
              className="flex min-w-0 flex-1 items-center gap-3 rounded-lg text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-brand/50"
            >
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-muted text-muted-foreground transition-colors group-hover:bg-accent-brand/10 group-hover:text-accent-brand">
                <FileText className="h-4 w-4" />
              </span>
              <span className="min-w-0">
                <span className="block truncate text-[13px] font-medium text-foreground">{d.name}</span>
                <span className="mt-0.5 block text-[11.5px] text-muted-foreground">
                  {d.display_type}
                  {d.pages != null && <> · {t('docs.pages', { count: d.pages })}</>}
                </span>
              </span>
            </button>
            <div className="flex shrink-0 items-center gap-2">
              {d.hash && <span className="hidden rounded bg-muted px-1.5 py-0.5 font-mono2 text-[10.5px] text-muted-foreground sm:inline">{d.hash.slice(0, 8)}</span>}
              {d.name && (confirmName === d.name ? (
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => void onDelete(d.name)}
                    disabled={deletingName === d.name}
                    className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-[11.5px] font-medium text-red-600 transition-colors hover:bg-red-50 disabled:opacity-60 dark:text-red-400 dark:hover:bg-red-500/10"
                  >
                    {deletingName === d.name && <Loader2 className="h-3 w-3 animate-spin" />}
                    {t('docs.delete.confirm')}
                  </button>
                  <button type="button" onClick={() => onConfirmDelete(null)} className="h-7 rounded-md px-2 text-[11.5px] text-muted-foreground hover:bg-accent">
                    {t('docs.delete.cancel')}
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => onConfirmDelete(d.name)}
                  title={t('docs.delete.action')}
                  aria-label={t('docs.delete.action')}
                  className="grid h-7 w-7 place-items-center rounded-lg text-muted-foreground opacity-0 transition-all hover:bg-red-50 hover:text-red-600 group-hover:opacity-100 focus:opacity-100 dark:hover:bg-red-500/10 dark:hover:text-red-400"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
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

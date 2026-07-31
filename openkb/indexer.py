"""PageIndex indexer for long documents."""

from __future__ import annotations

import json as json_mod
import logging
import multiprocessing
import os
import traceback
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pageindex import IndexConfig, PageIndexClient

from openkb.config import (
    DEFAULT_CONFIG,
    LlmCredentialBundle,
    get_base_url,
    resolve_concurrency,
    resolve_effective_config,
)
from openkb.ingest_cancel import cancel_event_var, check_cancelled
from openkb.tree_renderer import render_summary_md

logger = logging.getLogger(__name__)

# PageIndex currently exposes only a synchronous ``Collection.add()`` API. A
# running add can spend many minutes in its own LLM calls, which a Python
# thread cannot safely interrupt. API jobs therefore run that one operation in
# a child process and poll the normal ingest cancellation flag while it runs.
# The CLI does not install that flag and keeps the inexpensive direct call.
_PAGEINDEX_CANCEL_POLL_SECONDS = 0.1
_PAGEINDEX_PROCESS_STOP_SECONDS = 2.0


@dataclass
class IndexResult:
    """Result of indexing a long document via PageIndex."""

    doc_id: str
    description: str
    tree: dict


@dataclass
class CloudImportResult:
    """Result of importing an existing PageIndex Cloud document."""

    doc_id: str
    doc_name: str  # collision-resistant wiki slug
    name: str  # cloud display name (original filename in the cloud)
    description: str


@dataclass
class CloudImportData:
    """A fetched cloud doc + its resolved wiki name, before any KB write.

    Returned by :func:`prepare_cloud_import` so the caller can snapshot this
    doc's specific paths (O(1)) before :func:`_write_long_doc_artifacts` writes
    them — instead of copying the whole summaries/sources trees on every import.
    """

    doc_id: str
    doc_name: str  # collision-resistant wiki slug (resolved, not yet written)
    cloud_name: str  # cloud display name (original filename in the cloud)
    description: str
    tree: dict
    all_pages: list


def _index_config_payload(index_config: IndexConfig) -> dict[str, Any]:
    """Return a spawn-safe PageIndex config payload.

    ``IndexConfig`` is a Pydantic model in the supported PageIndex release.
    Keeping the small ``__dict__`` fallback makes this boundary compatible
    with lightweight stand-ins used by callers and tests.
    """
    model_dump = getattr(index_config, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="python")
    return dict(vars(index_config))


def _pageindex_add_process(
    send_connection,
    pdf_path: str,
    *,
    api_key: str | None,
    model: str,
    storage_path: str,
    index_config: dict[str, Any],
) -> None:
    """Run PageIndex's uninterruptible add in a disposable child process."""
    try:
        client = PageIndexClient(
            api_key=api_key,
            model=model,
            storage_path=storage_path,
            index_config=index_config,
        )
        doc_id = client.collection().add(pdf_path)
        send_connection.send(("ok", doc_id))
    except BaseException as exc:
        # Exception instances and tracebacks are not guaranteed pickle-safe,
        # especially for errors raised by optional LLM providers.
        send_connection.send(("error", type(exc).__name__, str(exc), traceback.format_exc()))
    finally:
        send_connection.close()


def _stop_pageindex_process(process) -> None:
    """Stop and reap a PageIndex child, escalating only when it ignores TERM."""
    if not process.is_alive():
        process.join()
        return
    process.terminate()
    process.join(_PAGEINDEX_PROCESS_STOP_SECONDS)
    if process.is_alive():
        # ``kill`` exists on supported Python versions. Keep the guard for
        # alternate process implementations used by embedding applications.
        kill = getattr(process, "kill", None)
        if callable(kill):
            kill()
        process.join()


def _add_document_interruptibly(
    pdf_path: Path,
    *,
    api_key: str | None,
    model: str,
    storage_path: Path,
    index_config: IndexConfig,
    collection=None,
) -> str:
    """Add a PageIndex document, force-stopping it when an API job cancels.

    PageIndex has no cancellation hook. The process boundary is deliberately
    limited to ``add``: once it returns, the parent owns all reads/writes and
    can use the existing cooperative checkpoints and mutation rollback.
    """
    check_cancelled()
    if cancel_event_var.get() is None:
        if collection is None:
            raise RuntimeError("A PageIndex collection is required outside an API job.")
        return collection.add(str(pdf_path))

    context = multiprocessing.get_context("spawn")
    receive_connection, send_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_pageindex_add_process,
        args=(send_connection, str(pdf_path)),
        kwargs={
            "api_key": api_key,
            "model": model,
            "storage_path": str(storage_path),
            "index_config": _index_config_payload(index_config),
        },
        daemon=False,
    )
    process.start()
    send_connection.close()
    try:
        while True:
            check_cancelled()
            if receive_connection.poll(_PAGEINDEX_CANCEL_POLL_SECONDS):
                outcome = receive_connection.recv()
                if outcome[0] == "ok":
                    check_cancelled()
                    return outcome[1]
                _, error_type, message, child_traceback = outcome
                raise RuntimeError(
                    f"PageIndex indexing failed in child process ({error_type}): {message}\n"
                    f"{child_traceback}"
                )
            if not process.is_alive():
                # A hard crash may close the pipe before the child can publish
                # its exception. Surface a useful error instead of polling
                # forever.
                raise RuntimeError(
                    "PageIndex indexing process exited unexpectedly "
                    f"(exit code {process.exitcode})."
                )
    finally:
        receive_connection.close()
        _stop_pageindex_process(process)


def _cloud_display_stem(cloud_name: str, fallback: str) -> str:
    """Return a platform-independent stem for a PageIndex Cloud display name."""
    normalized = cloud_name.replace("\\", "/").rstrip("/")
    leaf = normalized.rsplit("/", 1)[-1] if normalized else ""
    return PurePosixPath(leaf).stem or fallback


def _normalize_page_content(raw_pages: Any) -> list[dict[str, Any]]:
    """Normalize PageIndex/local PDF page content into OpenKB's JSON shape."""
    if not isinstance(raw_pages, list):
        return []

    pages: list[dict[str, Any]] = []
    for index, item in enumerate(raw_pages, start=1):
        if isinstance(item, str):
            content = item.strip()
            if content:
                pages.append({"page": index, "content": content, "images": []})
            continue

        if not isinstance(item, dict):
            continue

        raw_page = item.get("page", item.get("page_number", item.get("page_num", index)))
        try:
            page_number = int(raw_page)
        except (TypeError, ValueError):
            page_number = index
        if page_number < 1:
            page_number = index

        content = item.get("content", item.get("markdown", item.get("text", "")))
        if content is None:
            content = ""
        content = str(content).strip()

        images = item.get("images", [])
        if not isinstance(images, list):
            images = []
        normalized_images = [
            image
            for image in images
            if isinstance(image, dict) and isinstance(image.get("path"), str)
        ]

        if content or normalized_images:
            pages.append(
                {
                    "page": page_number,
                    "content": content,
                    "images": normalized_images,
                }
            )

    return pages


def _get_pdf_page_count(pdf_path: Path) -> int:
    from openkb.converter import get_pdf_page_count

    return get_pdf_page_count(pdf_path)


def _convert_pdf_to_pages(pdf_path: Path, doc_name: str, images_dir: Path) -> list[dict[str, Any]]:
    from openkb.images import convert_pdf_to_pages

    return convert_pdf_to_pages(pdf_path, doc_name, images_dir)


def _write_long_doc_artifacts(
    tree: dict,
    pages: list[dict[str, Any]],
    doc_name: str,
    doc_id: str,
    kb_dir: Path,
    description: str = "",
) -> Path:
    """Write ``wiki/sources/<doc_name>.json`` + ``wiki/summaries/<doc_name>.md``.

    Returns the summary path. Shared by :func:`index_long_document` (local)
    and :func:`import_cloud_document` (cloud) so both produce identical
    artifacts. Page images, when present, are written separately by the
    caller's page extractor — this helper only persists page text + summary.
    """
    sources_dir = kb_dir / "wiki" / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    (sources_dir / f"{doc_name}.json").write_text(
        json_mod.dumps(pages, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summaries_dir = kb_dir / "wiki" / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    summary_path = summaries_dir / f"{doc_name}.md"
    summary_path.write_text(
        render_summary_md(tree, doc_name, doc_id, description=description), encoding="utf-8"
    )

    # Emit the canonical DocIR (deep tree from the PageIndex structure + per-page
    # visual nodes). See spec/docir-format.md section 9.3 - the PageIndex tree is
    # already close to DocIR root; we add id / loc / provenance and register page
    # images as figure_anchor nodes (render pointer only, no pre-analysis).
    _emit_long_doc_docir(tree, pages, doc_name, doc_id, sources_dir, description)

    return summary_path


def _emit_long_doc_docir(
    tree: dict,
    pages: list[dict[str, Any]],
    doc_name: str,
    doc_id: str,
    sources_dir: Path,
    description: str,
) -> None:
    """Build and persist the DocIR for a long (PageIndex-indexed) document.

    Deep tree: document root -> recursive sections from ``tree.structure``
    (title + summary as text, ``loc.page`` from the node's ``start_index``),
    with ``provenance.extractor = pageindex-tree``. Each page's extracted
    images register as ``figure_anchor`` visual nodes (page + render_ref).
    Writes ``wiki/sources/<doc_name>.docir.json`` atomically.
    """
    from openkb.docir import (
        EXTRACTOR_PAGEINDEX_TREE,
        EXTRACTOR_PDF_FIGURE_DETECT,
        KIND_DOCUMENT,
        KIND_SECTION,
        VISION_PHOTO,
        DocIRBuilder,
    )

    builder = DocIRBuilder(
        doc_name,
        doc_id=doc_id,
        input_type="pdf",
        converter=EXTRACTOR_PAGEINDEX_TREE,
        origin_uri=f"raw/{doc_name}.pdf",
        title=tree.get("doc_name") or doc_name,
    )
    builder.set_input_meta(page_count=len(pages))
    if description:
        builder.set_input_meta(description=description)

    root_id = builder.add_node(
        kind=KIND_DOCUMENT,
        title=tree.get("doc_name") or doc_name,
        text=description or "",
        extractor=EXTRACTOR_PAGEINDEX_TREE,
        confidence=1.0,
    )

    def _add_structure(nodes: list[dict], parent_id: str) -> None:
        for node in nodes:
            title = node.get("title", "") or ""
            summary = node.get("summary", "") or ""
            start = node.get("start_index")
            page: int | None = None
            if start is not None and str(start).strip().isdigit():
                page = int(str(start).strip())
            nid = builder.add_node(
                kind=KIND_SECTION,
                title=title,
                text=summary,
                page=page,
                parent_id=parent_id,
                extractor=EXTRACTOR_PAGEINDEX_TREE,
                confidence=0.9,
            )
            children = node.get("nodes", []) or []
            if children:
                _add_structure(children, nid)

    _add_structure(tree.get("structure", []) or [], root_id)

    # Register per-page extracted images as visual nodes (render pointer only).
    for page_entry in pages:
        page_num = page_entry.get("page")
        if not isinstance(page_num, int):
            continue
        for img in page_entry.get("images", []) or []:
            path = img.get("path") if isinstance(img, dict) else None
            if not isinstance(path, str):
                continue
            builder.add_visual_node(
                page=page_num,
                visual_type=VISION_PHOTO,
                render_ref=path,
                extractor=EXTRACTOR_PDF_FIGURE_DETECT,
            )

    doc = builder.build()
    doc.save(sources_dir / f"{doc_name}.docir.json")


def _build_index_config(
    config: dict[str, Any], *, bundle: LlmCredentialBundle | None = None
) -> IndexConfig:
    """Build the PageIndex ``IndexConfig`` for local indexing.

    Forwards the KB's ``concurrency`` setting to PageIndex, which caps how many
    indexing LLM calls run at once (guarding against "too many open files" fd
    exhaustion on large documents). The value is only passed when set *and* the
    installed PageIndex's ``IndexConfig`` declares the field, so OpenKB keeps
    working against a pinned PageIndex that predates it (``IndexConfig``
    forbids unknown kwargs).

    ``bundle`` carries the per-request LLM credentials on the REST API path
    (which never touches process-wide state); the CLI path leaves it ``None``
    and relies on the process-wide base URL set by ``_setup_llm_key``.
    """
    kwargs: dict[str, Any] = {
        "if_add_node_text": True,
        "if_add_node_summary": True,
        "if_add_doc_description": True,
    }
    concurrency = resolve_concurrency(config)
    if concurrency is not None:
        if "max_concurrency" in IndexConfig.model_fields:
            kwargs["max_concurrency"] = concurrency
        else:
            logger.warning(
                "config: 'concurrency' is set but the installed PageIndex "
                "version does not support it yet — ignoring it."
            )
    # Route the resolved LLM credentials into PageIndex's own litellm calls.
    # PageIndex indexes via its internal litellm.completion (not openkb's
    # _llm_call), so without this a provider-prefixed model like deepseek/...
    # ignores litellm.api_base and falls back to the provider's default
    # endpoint.
    llm_params: dict[str, Any] = {}
    base_url = (bundle.base_url if bundle else None) or get_base_url()
    if base_url:
        llm_params["base_url"] = base_url
    if bundle is not None:
        if bundle.extra_headers:
            llm_params["extra_headers"] = bundle.extra_headers
        if bundle.timeout is not None:
            llm_params["timeout"] = bundle.timeout
    if llm_params:
        if "llm_params" in IndexConfig.model_fields:
            kwargs["llm_params"] = llm_params
        else:
            logger.warning(
                "config: LLM overrides are configured but the installed "
                "PageIndex version does not support llm_params; PageIndex LLM "
                "calls will use the provider's default endpoint."
            )
    return IndexConfig(**kwargs)


def index_long_document(
    pdf_path: Path,
    kb_dir: Path,
    doc_name: str | None = None,
    *,
    bundle: LlmCredentialBundle | None = None,
) -> IndexResult:
    """Index a long PDF document using PageIndex and write wiki pages.

    ``doc_name`` is the collision-resistant wiki name used for all written
    artifacts; defaults to the PDF's stem for backward compatibility.
    ``bundle`` carries per-request LLM credentials (REST API path); when
    ``None`` (CLI path) the process-wide base URL set by ``_setup_llm_key``
    applies instead.
    """
    source_name = doc_name or pdf_path.stem
    openkb_dir = kb_dir / ".openkb"
    config = resolve_effective_config(kb_dir)[0]

    model: str = config.get("model", DEFAULT_CONFIG["model"])
    pageindex_api_key = os.environ.get("PAGEINDEX_API_KEY", "")

    index_config = _build_index_config(config, bundle=bundle)
    # Keep the CLI/direct path exactly as before (including its single client
    # instance). API jobs have a cancellation flag and must defer creation to
    # the child process so its SQLite connection is never inherited.
    client = None
    col = None
    if cancel_event_var.get() is None:
        client = PageIndexClient(
            api_key=pageindex_api_key or None,
            model=model,
            storage_path=str(openkb_dir),
            index_config=index_config,
        )
        col = client.collection()

    # Add PDF (retry up to 3 times — PageIndex TOC accuracy is stochastic)
    max_retries = 3
    doc_id = None
    for attempt in range(1, max_retries + 1):
        try:
            doc_id = _add_document_interruptibly(
                pdf_path,
                api_key=pageindex_api_key or None,
                model=model,
                storage_path=openkb_dir,
                index_config=index_config,
                collection=col,
            )
            logger.info(
                "PageIndex added %s → doc_id=%s (attempt %d)", pdf_path.name, doc_id, attempt
            )
            break
        except Exception as exc:
            logger.warning(
                "PageIndex attempt %d/%d failed for %s: %s",
                attempt,
                max_retries,
                pdf_path.name,
                exc,
            )
            if attempt == max_retries:
                raise RuntimeError(
                    f"Failed to index {pdf_path.name} after {max_retries} attempts: {exc}"
                ) from exc

    # The cancellable add may have run in a child process. Open a fresh parent
    # client only after it finishes so SQLite connections are never inherited
    # across a process boundary.
    if client is None:
        client = PageIndexClient(
            api_key=pageindex_api_key or None,
            model=model,
            storage_path=str(openkb_dir),
            index_config=index_config,
        )
        col = client.collection()
    assert col is not None

    # The PageIndex blob for doc_id is now durably on disk. The add mutation no
    # longer eagerly snapshots .openkb/files — it registers the new blob via
    # snapshot.track_new() only on a successful return — so if any step below
    # fails, delete the document we just added. Otherwise the blob leaks as an
    # orphan that pageindex.db (rolled back by the snapshot) no longer refs and
    # no reaper reclaims.
    try:
        # Fetch complete document (metadata + structure + text)
        doc = col.get_document(doc_id, include_text=True)
        indexed_doc_name: str = doc.get("doc_name", pdf_path.stem)
        description: str = doc.get("doc_description", "")
        structure: list = doc.get("structure", [])

        # Debug: print doc keys and page_count to diagnose get_page_content range
        logger.info("Doc keys: %s", list(doc.keys()))
        logger.info("page_count from doc: %s", doc.get("page_count", "NOT PRESENT"))

        tree = {
            "doc_name": indexed_doc_name,
            "doc_description": description,
            "structure": structure,
        }

        # Write wiki/sources/ — per-page content
        sources_dir = kb_dir / "wiki" / "sources"
        sources_dir.mkdir(parents=True, exist_ok=True)
        images_dir = sources_dir / "images" / source_name

        all_pages: list[dict[str, Any]] = []
        if pageindex_api_key:
            # Cloud mode: fetch OCR'd markdown from PageIndex. get_page_content
            # requires a page range, so pass "1-N".
            page_count = _get_pdf_page_count(pdf_path)
            try:
                all_pages = _normalize_page_content(col.get_page_content(doc_id, f"1-{page_count}"))
            except Exception as exc:
                logger.warning("Cloud get_page_content failed for %s: %s", pdf_path.name, exc)

        if not all_pages:
            if pageindex_api_key:
                logger.warning(
                    "Cloud returned no pages for %s; falling back to local pymupdf", pdf_path.name
                )
            all_pages = _normalize_page_content(
                _convert_pdf_to_pages(pdf_path, source_name, images_dir)
            )

        if not all_pages:
            raise RuntimeError(f"No page content extracted for {pdf_path.name}")

        _write_long_doc_artifacts(
            tree, all_pages, source_name, doc_id, kb_dir, description=description
        )
        return IndexResult(doc_id=doc_id, description=description, tree=tree)
    except BaseException:
        # Best-effort: remove the blob this add created. A failure here (e.g. a
        # second interrupt) only means the blob may stay orphaned — the original
        # error still propagates so the caller (mutation coordinator) rolls back
        # everything else it snapshotted.
        try:
            col.delete_document(doc_id)
        except Exception:
            logger.warning(
                "PageIndex cleanup of %s failed after error; blob may be orphaned", doc_id
            )
        raise


# PageIndex's get_page_content rejects a single page range covering more than
# this many pages (``parse_pages`` raises "Page range too large (max 1000)"),
# so cloud page fetches are windowed in chunks of this size.
_CLOUD_PAGE_WINDOW = 1000
# Safety bound on the windowed fetch (in pages) in case a backend never returns
# a short window — caps the loop at _CLOUD_PAGE_MAX / _CLOUD_PAGE_WINDOW calls.
_CLOUD_PAGE_MAX = 1_000_000


def _fetch_cloud_pages(col, doc_id: str) -> list[dict[str, Any]]:
    """Fetch all OCR pages of a cloud doc, windowing around the 1000-page cap.

    ``get_page_content`` returns the whole document and uses its ``pages`` arg
    only as a client-side filter that ``parse_pages`` caps at 1000 pages — so a
    single ``"1-<N>"`` request fails for any doc over 1000 pages. Request fixed
    ``1000``-page windows and stop as soon as a window comes back SHORT (fewer
    than a full window): PageIndex page numbers are sequential, so a short window
    means we've passed the last page. This is what makes the common (≤1000-page)
    doc a single request, while still fetching every page of a larger one — and,
    unlike bounding the loop by the tree's max page index, it never truncates a
    doc whose tree under-reports its page count (a real case: a paper whose tree
    stops a couple pages short of the references). A wide safety bound guards
    against a backend that never narrows the window.
    """
    pages: list[dict[str, Any]] = []
    start = 1
    while start <= _CLOUD_PAGE_MAX:
        window = _normalize_page_content(
            col.get_page_content(doc_id, f"{start}-{start + _CLOUD_PAGE_WINDOW - 1}")
        )
        pages.extend(window)
        if len(window) < _CLOUD_PAGE_WINDOW:
            break
        start += _CLOUD_PAGE_WINDOW
    return pages


def prepare_cloud_import(doc_id: str, kb_dir: Path, path_key: str) -> CloudImportData:
    """Fetch a PageIndex Cloud doc and resolve its wiki name WITHOUT writing.

    Cloud fetch + collision-resistant name resolution only — no KB mutation —
    so the caller knows ``doc_name`` before writing and can snapshot just this
    doc's paths instead of copying the whole summaries/sources trees. Name
    resolution reads the registry but does not mutate it.
    """
    from openkb.converter import resolve_doc_name_from_key
    from openkb.state import HashRegistry

    pageindex_api_key = os.environ.get("PAGEINDEX_API_KEY", "")
    if not pageindex_api_key:
        raise RuntimeError(
            "Importing from PageIndex Cloud requires the PAGEINDEX_API_KEY environment variable."
        )

    client = PageIndexClient(api_key=pageindex_api_key)
    col = client.collection()

    doc = col.get_document(doc_id, include_text=True)
    cloud_name: str = doc.get("doc_name") or doc_id
    description: str = doc.get("doc_description", "")
    structure: list = doc.get("structure", [])

    registry = HashRegistry(kb_dir / ".openkb" / "hashes.json")
    stem = _cloud_display_stem(cloud_name, doc_id)
    doc_name = resolve_doc_name_from_key(stem, path_key, registry)

    tree = {
        "doc_name": cloud_name,
        "doc_description": description,
        "structure": structure,
    }

    all_pages = _fetch_cloud_pages(col, doc_id)
    if not all_pages:
        raise RuntimeError(f"No page content returned from PageIndex Cloud for doc_id={doc_id}")

    return CloudImportData(
        doc_id=doc_id,
        doc_name=doc_name,
        cloud_name=cloud_name,
        description=description,
        tree=tree,
        all_pages=all_pages,
    )


def import_cloud_document(doc_id: str, kb_dir: Path, path_key: str) -> CloudImportResult:
    """Import an already-indexed PageIndex Cloud document by ``doc_id``.

    Fetches structure + OCR'd page content from the cloud (no local PDF) and
    writes the same wiki artifacts as :func:`index_long_document`. Requires
    ``PAGEINDEX_API_KEY``. ``path_key`` is the synthetic identity key
    (``pageindex-cloud:<doc_id>``) used to resolve a collision-resistant
    wiki name.

    Writes immediately. Callers that need to snapshot before writing (e.g. the
    crash-safe CLI path) should call :func:`prepare_cloud_import` then
    :func:`_write_long_doc_artifacts`, so the snapshot can cover only this
    doc's paths.
    """
    cloud = prepare_cloud_import(doc_id, kb_dir, path_key)
    _write_long_doc_artifacts(
        cloud.tree,
        cloud.all_pages,
        cloud.doc_name,
        cloud.doc_id,
        kb_dir,
        description=cloud.description,
    )
    return CloudImportResult(
        doc_id=cloud.doc_id,
        doc_name=cloud.doc_name,
        name=cloud.cloud_name,
        description=cloud.description,
    )

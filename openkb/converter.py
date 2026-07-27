"""Document conversion pipeline for OpenKB."""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from openkb.config import resolve_effective_config
from openkb.images import convert_pdf_with_images, copy_relative_images, extract_base64_images
from openkb.locks import atomic_write_text, kb_ingest_lock
from openkb.state import HashRegistry

logger = logging.getLogger(__name__)


@dataclass
class ConvertResult:
    """Result returned by :func:`convert_document`."""

    raw_path: Path | None = None
    source_path: Path | None = None
    is_long_doc: bool = False
    skipped: bool = False
    file_hash: str | None = None  # For deferred hash registration
    doc_name: str | None = None  # Stable wiki name (collision-resistant)


def _registry_path(path: Path, kb_dir: Path) -> str:
    """Portable path string used as the registry's identity key.

    Relative-to-KB posix when the file lives inside the KB (stable across
    machines/checkouts), absolute posix otherwise. Both paths are fully
    resolved (symlinks followed) before comparison.
    """
    resolved_path = path.resolve()
    resolved_kb = kb_dir.resolve()
    if resolved_path.is_relative_to(resolved_kb):
        return resolved_path.relative_to(resolved_kb).as_posix()
    return resolved_path.as_posix()


_SAFE_STEM_RE = re.compile(r"[^\w\-]+")
_SUFFIX_LEN = 8


def _sanitize_stem(stem: str) -> str:
    normalized = unicodedata.normalize("NFKC", stem)
    return _SAFE_STEM_RE.sub("-", normalized).strip("-") or "document"


def _name_taken(candidate: str, registry: HashRegistry) -> bool:
    """True when ``candidate`` is claimed by another registered document.

    The registry is the single authority on ownership: artifacts on disk
    without a registry entry are either leftovers of a failed ingest of
    this same source (must be adoptable so a retry keeps its clean name)
    or out-of-contract manual drops — both are overwritten, matching
    pre-collision-fix behaviour for unclaimed files.
    """
    for meta in registry.all_entries().values():
        entry_name = meta.get("doc_name") or Path(meta.get("name", "")).stem
        if unicodedata.normalize("NFKC", entry_name) == candidate:
            return True
    return False


def resolve_doc_name(
    src: Path,
    kb_dir: Path,
    registry: HashRegistry,
    *,
    persist_legacy: bool = True,
) -> str:
    """Resolve the stable wiki name for ``src`` (Scheme A).

    Identity is keyed by path: a source we've seen before (same path, even
    with new content) keeps its name so re-ingest overwrites in place.
    Legacy registry entries (written before the path index) are matched by
    stem and backfilled with the path. A brand-new source keeps the clean
    sanitized stem unless another document already owns that name, in which
    case it gets a deterministic ``-{sha256(path)[:8]}`` suffix.
    """
    path_key = _registry_path(src, kb_dir)

    known = registry.get_by_path(path_key)
    if known is not None:
        stored = known.get("doc_name") or Path(known.get("name", "")).stem
        if stored:
            return stored

    legacy = registry.find_legacy_by_stem(src.stem)
    if legacy is not None:
        file_hash, meta = legacy
        meta = dict(meta)
        name = meta.get("doc_name") or Path(meta.get("name", "")).stem
        if persist_legacy:
            meta["doc_name"] = name
            meta["path"] = path_key
            registry.add(file_hash, meta)  # backfill + persist
        return name

    return resolve_doc_name_from_key(src.stem, path_key, registry)


def resolve_doc_name_from_key(stem: str, path_key: str, registry: HashRegistry) -> str:
    """Collision-resistant wiki name for a synthetic identity ``path_key``.

    Same rules as :func:`resolve_doc_name` minus the legacy-by-stem
    backfill (a filesystem-migration concern that must not fire for sources
    with no real path, e.g. cloud imports). A source already registered
    under ``path_key`` keeps its stored ``doc_name``; otherwise the
    sanitized ``stem`` is used, with a deterministic
    ``-{sha256(path_key)[:8]}`` suffix when another document owns it.
    """
    known = registry.get_by_path(path_key)
    if known is not None:
        stored = known.get("doc_name") or Path(known.get("name", "")).stem
        if stored:
            return stored

    candidate = _sanitize_stem(stem)
    if _name_taken(candidate, registry):
        digest = hashlib.sha256(path_key.encode("utf-8")).hexdigest()[:_SUFFIX_LEN]
        return f"{candidate}-{digest}"
    return candidate


def get_pdf_page_count(path: Path) -> int:
    """Return the number of pages in the PDF at *path* using pymupdf."""
    with pymupdf.open(str(path)) as doc:
        return doc.page_count


def _docir_converter_for(src: Path) -> tuple[str, str]:
    """Map a source suffix to (DocIR input_type, converter extractor)."""
    suffix = src.suffix.lower()
    if suffix == ".md":
        return "md", "md-parser"
    if suffix == ".pdf":
        return "pdf", "pymupdf-text"
    if suffix in (".txt",):
        return "txt", "passthrough"
    if suffix in (".html", ".htm"):
        return "html", "markitdown"
    return suffix.lstrip(".") or "unknown", "markitdown"


# Image files registered as visual nodes. PNG/JPEG/GIF/WebP/BMP.
_VISUAL_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_PAGE_IN_FILENAME = re.compile(r"p(\d+)_img", re.IGNORECASE)


def _emit_short_doc_docir(
    *,
    markdown: str,
    doc_name: str,
    src: Path,
    images_dir: Path,
    sources_dir: Path,
    file_hash: str | None,
) -> None:
    """Build and persist the DocIR for a short (non-long-PDF) document.

    Shallow tree from the markdown + one figure_anchor visual node per
    extracted image (page parsed from the ``pN_img`` filename convention when
    present). Writes ``wiki/sources/<doc_name>.docir.json`` atomically. The
    ``.md`` continues to be produced for backward compatibility.
    """
    from openkb.docir import (
        EXTRACTOR_PDF_FIGURE_DETECT,
        VISION_PHOTO,
        create_docir_from_markdown,
    )

    input_type, converter = _docir_converter_for(src)
    origin_uri = f"raw/{doc_name}{src.suffix.lower()}"
    doc = create_docir_from_markdown(
        markdown,
        doc_name,
        input_type=input_type,
        converter=converter,
        origin_uri=origin_uri,
        content_hash=f"sha256:{file_hash}" if file_hash else None,
    )

    # Register extracted images as visual nodes (render pointer only).
    if images_dir.exists():
        for img in sorted(images_dir.iterdir()):
            if img.suffix.lower() not in _VISUAL_IMAGE_SUFFIXES:
                continue
            page_match = _PAGE_IN_FILENAME.search(img.name)
            page = int(page_match.group(1)) if page_match else None
            render_ref = f"sources/images/{doc_name}/{img.name}"
            doc.attach_visual_node(
                page=page,
                visual_type=VISION_PHOTO,
                render_ref=render_ref,
                title=img.stem,
                extractor=EXTRACTOR_PDF_FIGURE_DETECT,
            )

    docir_path = sources_dir / f"{doc_name}.docir.json"
    doc.save(docir_path)


def convert_document(
    src: Path,
    kb_dir: Path,
    *,
    staging_dir: Path | None = None,
) -> ConvertResult:
    """Convert a document and integrate it into the knowledge base.

    Steps:
    1. Hash-check — skip if already known.
    2. Copy source to ``raw/``.
    3. If PDF and page count >= threshold → return :attr:`ConvertResult.is_long_doc`.
    4. If ``.md`` — read, process relative images, save to ``wiki/sources/``.
    5. Otherwise — run MarkItDown, extract base64 images, save to ``wiki/sources/``.
    6. Register hash in the registry.
    """
    with kb_ingest_lock(kb_dir / ".openkb"):
        # ------------------------------------------------------------------
        # Load config & state
        # ------------------------------------------------------------------
        openkb_dir = kb_dir / ".openkb"
        config = resolve_effective_config(kb_dir)[0]
        threshold: int = config.get("pageindex_threshold", 20)
        artifact_root = staging_dir if staging_dir is not None else kb_dir
        registry = HashRegistry(openkb_dir / "hashes.json")

        # ------------------------------------------------------------------
        # 1. Hash check + identity resolution
        # ------------------------------------------------------------------
        file_hash = HashRegistry.hash_file(src)
        if registry.is_known(file_hash):
            logger.info("Skipping already-known file: %s", src.name)
            stored = registry.get(file_hash) or {}
            return ConvertResult(
                skipped=True,
                file_hash=file_hash,
                doc_name=stored.get("doc_name") or Path(stored.get("name", src.name)).stem,
            )
        doc_name = resolve_doc_name(
            src,
            kb_dir,
            registry,
            persist_legacy=staging_dir is None,
        )

        # ------------------------------------------------------------------
        # 2. Copy to raw/
        # ------------------------------------------------------------------
        raw_dir = artifact_root / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        if staging_dir is None and src.resolve().is_relative_to(raw_dir.resolve()):
            # Watch mode: the file already lives in raw/ — don't copy/rename.
            raw_dest = src
        else:
            raw_dest = raw_dir / f"{doc_name}{src.suffix.lower()}"
            shutil.copy2(src, raw_dest)

        # ------------------------------------------------------------------
        # 3. PDF long-doc detection
        # ------------------------------------------------------------------
        if src.suffix.lower() == ".pdf":
            page_count = get_pdf_page_count(src)
            if page_count >= threshold:
                logger.info(
                    "Long PDF detected (%d pages >= %d threshold): %s",
                    page_count,
                    threshold,
                    src.name,
                )
                return ConvertResult(
                    raw_path=raw_dest,
                    is_long_doc=True,
                    file_hash=file_hash,
                    doc_name=doc_name,
                )

        # ------------------------------------------------------------------
        # 4/5. Convert to Markdown
        # ------------------------------------------------------------------
        sources_dir = artifact_root / "wiki" / "sources"
        sources_dir.mkdir(parents=True, exist_ok=True)
        images_dir = artifact_root / "wiki" / "sources" / "images" / doc_name
        images_dir.mkdir(parents=True, exist_ok=True)

        if src.suffix.lower() == ".md":
            markdown = src.read_text(encoding="utf-8")
            markdown = copy_relative_images(markdown, src.parent, doc_name, images_dir)
        elif src.suffix.lower() == ".pdf":
            # Use pymupdf dict-mode for PDFs: text + images inline at correct positions
            markdown = convert_pdf_with_images(src, doc_name, images_dir)
        else:
            # Non-PDF, non-MD: use markitdown (docx, pptx, html, etc.).
            # Imported lazily: markitdown pulls in magika → onnxruntime (tens
            # of MB, slow to import) that only this branch needs. Keeping it
            # out of module import makes `import openkb` cheap and lets a
            # packaged build drop those deps when Office-format ingest isn't
            # required. markitdown appends warning filters on import but leaves
            # the CLI's front "ignore" filter in place, so no re-suppress here.
            from markitdown import MarkItDown

            mid = MarkItDown()
            result = mid.convert(str(src), keep_data_uris=True)
            markdown = result.text_content
            markdown = extract_base64_images(markdown, doc_name, images_dir)

        dest_md = sources_dir / f"{doc_name}.md"
        atomic_write_text(dest_md, markdown)

        # Emit the canonical DocIR (single .docir.json) alongside the .md.
        # Short docs -> shallow tree (root -> sections/paragraphs); extracted
        # images register as figure_anchor visual nodes (render pointer only,
        # no pre-analysis). See spec/docir-format.md section 9.
        _emit_short_doc_docir(
            markdown=markdown,
            doc_name=doc_name,
            src=src,
            images_dir=images_dir,
            sources_dir=sources_dir,
            file_hash=file_hash,
        )

        return ConvertResult(
            raw_path=raw_dest,
            source_path=dest_md,
            file_hash=file_hash,
            doc_name=doc_name,
        )

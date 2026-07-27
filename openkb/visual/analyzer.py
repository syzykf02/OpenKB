"""On-demand vision analyzer - the Vision Tool runner (spec section 3.4).

Text-first, vision-second (Harvey): the analyzer is invoked ONLY when a query
needs visual content a text search located. It:

1. Renders the candidate page to a high-DPI image (:func:`render_page` - the
   gate; pages with no visual content return a text-first message, no LLM call).
2. Calls a vision LLM with the image + a structured-reasoning prompt (read
   axes/labels, interpolate between ticks, distinguish exact reads from
   estimates, ALWAYS give a best answer + confidence; degrade gracefully when
   the target isn't on the page).
3. Caches the result in the DocIR visual node's ``vision.last_analysis`` so the
   next similar query reuses it (the crystallization handoff).

The LLM call is injectable (``llm_call``) so tests run without a vision model.
Default uses litellm's multimodal completion.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from openkb.agent.docir_tools import render_page
from openkb.docir import DocIRDocument

# The structured-reasoning prompt (spec section 3.4 step 4).
_VISION_PROMPT = """You are analyzing a page image from a legal/case document.
Answer the user's question by reading the image carefully.

Rules:
- Read axes, labels, and small print precisely.
- When a value falls between tick marks, interpolate and label it an ESTIMATE.
- Distinguish exact readings from estimates explicitly.
- ALWAYS give your best answer; if the target is not on this page, say so
  plainly rather than guessing.
- End with a confidence score (0.0-1.0) for your answer.

Respond as JSON: {"answer": "...", "confidence": 0.0, "note": "..."}
"""


@dataclass
class VisionResult:
    """Outcome of one on-demand vision analysis."""

    doc_name: str
    page: Optional[int]
    question: str
    answer: str = ""
    confidence: float = 0.0
    note: str = ""
    cached: bool = False  # True if served from vision.last_analysis
    error: Optional[str] = None
    render_ref: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "doc_name": self.doc_name,
            "page": self.page,
            "question": self.question,
            "answer": self.answer,
            "confidence": self.confidence,
            "note": self.note,
            "cached": self.cached,
            "error": self.error,
            "render_ref": self.render_ref,
        }


# Confidence below which the spec says to flag "待人工看图确认".
LOW_CONFIDENCE_THRESHOLD = 0.6


class VisionAnalyzer:
    """On-demand vision analysis over DocIR visual nodes.

    Construct with ``kb_root`` and an optional ``llm_call`` (injectable for
    tests). :meth:`analyze_page` is the entry point - it renders the page,
    calls the vision LLM, and caches the result.
    """

    def __init__(
        self,
        kb_root: Path | str,
        *,
        model: str = "gpt-4o",
        llm_call: Optional[Callable[[str, str, str], Dict[str, Any]]] = None,
    ) -> None:
        self.kb_root = Path(kb_root).resolve()
        self.model = model
        # llm_call(image_url, question, model) -> {"answer", "confidence", "note"}
        self._llm_call = llm_call or _default_vision_llm_call

    def analyze_page(self, doc_name: str, page: str | int, question: str) -> VisionResult:
        """Analyze a page for a question (the Vision Tool gate).

        Text-first: if the page has no registered visual content, returns a
        result with a text-first note and NO LLM call. Otherwise renders the
        page, calls the vision LLM, and caches the answer.
        """
        page_str = str(page)
        result = VisionResult(
            doc_name=doc_name, page=int(page_str) if page_str.isdigit() else None, question=question
        )
        rendered = render_page(doc_name, page_str, str(self.kb_root))
        if rendered.get("type") != "image":
            # Text-first gate: no visual content or no raw PDF to render.
            result.note = rendered.get("text", "no visual content; text-first")
            result.error = "no_visual_content"
            return result

        result.render_ref = f"rendered:{doc_name}:p{page_str}"
        image_url = rendered["image_url"]
        try:
            llm_result = self._llm_call(image_url, question, self.model)
            result.answer = str(llm_result.get("answer", ""))
            result.confidence = float(llm_result.get("confidence", 0.0))
            result.note = str(llm_result.get("note", ""))
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            result.error = f"vision_llm_failed: {exc}"
            result.note = "视觉分析失败,建议人工看图确认"
            return result

        # Low-confidence graceful degradation (spec: flag for human review).
        if result.confidence < LOW_CONFIDENCE_THRESHOLD:
            result.note = (result.note + " " if result.note else "") + "置信度低,待人工看图确认"
        return result

    def analyze_node(self, node_id: str, question: str) -> VisionResult:
        """Analyze a specific DocIR visual node, caching to ``vision.last_analysis``.

        Resolves the node across all DocIR docs, renders its page, calls the
        LLM, and writes the result back to the node's ``vision.last_analysis``
        (and ``analyzed=True``) so the next query reuses it.
        """
        doc, node = self._find_visual_node(node_id)
        if doc is None or node is None:
            return VisionResult(
                doc_name="", page=None, question=question, error="visual node not found"
            )
        page = node.loc.page if node.loc else None
        result = self.analyze_page(doc.doc_name, str(page) if page else "", question)
        if result.error:
            return result
        # Cache to vision.last_analysis.
        node.vision.analyzed = True
        node.vision.last_analysis = {
            "question": question,
            "answer": result.answer,
            "confidence": result.confidence,
            "note": result.note,
        }
        self._save_doc(doc)
        return result

    def get_cached_analysis(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Return the cached ``vision.last_analysis`` for a node, or None."""
        doc, node = self._find_visual_node(node_id)
        if doc is None or node is None or node.vision is None:
            return None
        return node.vision.last_analysis

    # -- internals ---------------------------------------------------------

    def _find_visual_node(self, node_id: str):
        """Locate a visual node across all DocIR docs. Returns (doc, node)."""
        from openkb.agent.docir_tools import _load_all_docir

        for doc in _load_all_docir(self.kb_root / "wiki"):
            node = doc.get_node(node_id)
            if node is not None and node.is_visual():
                return doc, node
        return None, None

    def _save_doc(self, doc: DocIRDocument) -> None:
        """Persist a DocIR doc back to its .docir.json."""
        path = self.kb_root / "wiki" / "sources" / f"{doc.doc_name}.docir.json"
        if path.exists():
            doc.save(path)


def _default_vision_llm_call(image_url: str, question: str, model: str) -> Dict[str, Any]:
    """Default vision LLM call via litellm multimodal completion.

    Returns a parsed {answer, confidence, note} dict. Falls back to a note-only
    result if the model's JSON is malformed.
    """
    import litellm

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"{_VISION_PROMPT}\n\nQuestion: {question}"},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    ]
    resp = litellm.completion(model=model, messages=messages, temperature=0.0)
    text = resp["choices"][0]["message"]["content"]
    return _parse_vision_response(text)


def _parse_vision_response(text: str) -> Dict[str, Any]:
    """Parse the vision LLM's JSON response, tolerating markdown fences."""
    import re

    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            return {
                "answer": str(data.get("answer", "")),
                "confidence": float(data.get("confidence", 0.0)),
                "note": str(data.get("note", "")),
            }
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    # Fallback: treat the whole text as the answer, low confidence.
    return {"answer": text.strip(), "confidence": 0.3, "note": "unstructured response"}


__all__ = ["VisionResult", "VisionAnalyzer", "LOW_CONFIDENCE_THRESHOLD"]

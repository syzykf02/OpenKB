"""Legal query agent - clone-and-extend of the base query agent.

Builds on :func:`openkb.agent.query.build_query_agent` (the domain-agnostic
wiki reader) by cloning it with the legal retrieval tools layered on:

- :func:`read_node` / :func:`search_docir` / :func:`render_page` - DocIR access
  (existence-gate basis + BM25 + vision gate).
- :func:`query_graph` / :func:`find_impact` - typed-relation graph traversal
  (the structural leg text retrieval cannot answer).
- :func:`verify_citation` - the three-gate citation verifier (verification as a
  first-class agent action, not a post-output step).

The legal instructions addendum tells the agent WHEN to use each tool
(spec section 3.2: "工具描述即路由") - statute numbers -> search_docir (BM25),
impact questions -> find_impact (graph), every claim -> verify_citation.
"""

from __future__ import annotations

from pathlib import Path

from agents import Agent, function_tool
from agents.items import ToolOutputImage, ToolOutputText

from openkb.agent.docir_tools import read_node, render_page, search_docir
from openkb.agent.legal_tools import find_impact, query_graph
from openkb.agent.query import build_query_agent
from openkb.citation import format_verification, verify_citation
from openkb.config import LlmCredentialBundle

_LEGAL_INSTRUCTIONS_ADDENDUM = """

## Legal Knowledge Tools (legal overlay)

You have additional legal retrieval tools beyond the base wiki reader. Use them
per the query type (tool description is routing):

- **search_docir(query)**: BM25 search over DocIR node text. USE FIRST for
  statute numbers (第N条), case numbers, or precise legal terms - these must
  hit exactly. Returns candidate node ids.
- **read_node(node_id)**: Read a DocIR node by id or typed URI (docir:// /
  law:// / case://). Use to fetch the exact text of a candidate node found via
  search_docir, or to resolve a citation's source.
- **query_graph(entity, relation, depth)**: Traverse typed legal relations
  (cites / applies / revises / similar_case / ...). Use when the question is
  about relationships - "what statutes does this case cite", "similar cases".
- **find_impact(entity)**: Reverse traversal - what cases/concepts/rules
  reference this entity. USE for impact questions ("this statute changes, what
  is affected") - structural impact that text search cannot capture.
- **render_page(doc_name, page)**: Vision Tool gate. Use ONLY when a question
  requires reading a figure/signature/photo/scanned exhibit on a specific page.
  Text-first: if text answers the question, do NOT call this.
- **verify_citation(claim, source_uri)**: Verify a claim's citation through the
  three gates (existence / recency / consistency). CALL for every legal claim
  you make - verification is a first-class action, not a post-output step. If
  the source is superseded/repealed, downgrade your answer and note the history.

Legal-answer discipline: every assertion must cite a source URI; prefer current
(current) sources over superseded/repealed ones; express confidence per the
source's recency status. If no source verifies a claim, say so plainly.
"""


def build_legal_query_agent(
    kb_dir: Path | str,
    model: str,
    language: str = "en",
    bundle: "LlmCredentialBundle | None" = None,
) -> Agent:
    """Build the legal Q&A agent (base wiki reader + legal retrieval tools).

    Clones :func:`build_query_agent` with the DocIR + graph + citation tools and
    a legal-tool-routing instructions addendum.
    """
    kb_root = Path(kb_dir).resolve()
    wiki_root = str(kb_root / "wiki")
    base = build_query_agent(wiki_root, model, language=language, bundle=bundle)

    @function_tool
    def docir_search(query: str) -> str:
        """BM25 search over DocIR node text. Use FIRST for statute numbers
        (第N条), case numbers, or precise legal terms. Returns ranked node ids.
        """
        return search_docir(query, wiki_root)

    @function_tool
    def docir_read_node(node_id: str) -> str:
        """Read a DocIR node by id or typed URI (docir:// / law:// / case://).
        Fetches the exact text + location + provenance of a node found via
        docir_search, or resolves a citation's source.
        """
        return read_node(node_id, wiki_root)

    @function_tool
    def legal_query_graph(entity: str, relation: str, depth: int = 2) -> str:
        """Traverse typed legal relations from an entity (cites / applies /
        revises / similar_case / ...). Use for relationship questions.
        """
        return query_graph(entity, relation, str(kb_root), depth=depth)

    @function_tool
    def legal_find_impact(entity: str) -> str:
        """Find what references an entity (impact analysis). Use for "this
        statute/case changes, what is affected" - structural impact text search
        cannot capture.
        """
        return find_impact(entity, str(kb_root))

    @function_tool
    def legal_render_page(doc_name: str, page: str) -> ToolOutputImage | ToolOutputText:
        """Render a document page for visual analysis (Vision Tool gate). Use
        ONLY when a question requires reading a figure/signature/photo on a
        specific page. Text-first: skip if text answers the question.
        """
        result = render_page(doc_name, page, str(kb_root))
        if result.get("type") == "image":
            return ToolOutputImage(image_url=result["image_url"])
        return ToolOutputText(text=result.get("text", ""))

    @function_tool
    def legal_verify_citation(claim: str, source_uri: str) -> str:
        """Verify a claim's citation through three gates (existence / recency /
        consistency). Call for every legal claim. Reports superseded/repealed
        sources with the history chain.
        """
        result = verify_citation(claim, source_uri, kb_root)
        return format_verification(result)

    extra_tools = [
        docir_search,
        docir_read_node,
        legal_query_graph,
        legal_find_impact,
        legal_render_page,
        legal_verify_citation,
    ]
    new_instructions = (base.instructions or "") + _LEGAL_INSTRUCTIONS_ADDENDUM
    return base.clone(tools=[*base.tools, *extra_tools], instructions=new_instructions)


__all__ = ["build_legal_query_agent"]

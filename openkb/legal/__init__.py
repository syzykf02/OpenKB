"""法宝法律智能知识库 - 法律知识库扩展模块

This module provides legal knowledge base capabilities for OpenKB, including:
- Legal entity types and schema
- DocIR (Document Intermediate Representation) for legal documents
- Knowledge lifecycle management (confidence, superseding, decay)
- Legal knowledge graph
- Vision tool integration for evidence analysis
- Sync engine for folder import and continuous sync
"""

from openkb.legal.docir import (
    KIND_ARTICLE,
    KIND_DOCUMENT,
    KIND_EVIDENCE,
    KIND_FINDING,
    KIND_HOLDING,
    KIND_JUDGMENT_MAIN,
    KIND_PARAGRAPH,
    KIND_SECTION,
    LEGAL_KINDS,
    STATUS_CURRENT,
    STATUS_REPEALED,
    STATUS_SUPERSEDED,
    DocIRAnchors,
    DocIRBuilder,
    DocIRDocument,
    DocIRLoc,
    DocIRNode,
    DocIRProvenance,
    DocIRVision,
    LegalDocIRBuilder,
    case_uri,
    create_docir_from_markdown,
    get_effective_status,
    law_uri,
    legal_anchor,
    legal_extension,
    set_effective_status,
)
from openkb.legal.graph import (
    GraphEdge,
    GraphNode,
    LegalKnowledgeGraph,
    TraversalResult,
)
from openkb.legal.lifecycle import (
    ConfidenceMetadata,
    KnowledgePageLifecycle,
    LifecycleManager,
    SupersedenceMetadata,
)
from openkb.legal.schema import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    LEGAL_DOC_TYPES,
    LEGAL_ENTITY_TYPES,
    LEGAL_PAGE_CONTENT_DIRS,
    AuthorityLevel,
    DecayRate,
    DocumentStatus,
    RelationType,
    get_decay_rate,
    get_half_life,
    get_legal_agents_md_extension,
)

__all__ = [
    # Schema
    "LEGAL_ENTITY_TYPES",
    "LEGAL_DOC_TYPES",
    "LEGAL_PAGE_CONTENT_DIRS",
    "CONFIDENCE_HIGH",
    "CONFIDENCE_MEDIUM",
    "CONFIDENCE_LOW",
    "DocumentStatus",
    "RelationType",
    "DecayRate",
    "AuthorityLevel",
    "get_decay_rate",
    "get_half_life",
    "get_legal_agents_md_extension",
    # DocIR (legal overlay + canonical re-exports)
    "DocIRNode",
    "DocIRDocument",
    "DocIRBuilder",
    "DocIRAnchors",
    "DocIRLoc",
    "DocIRProvenance",
    "DocIRVision",
    "LegalDocIRBuilder",
    "KIND_DOCUMENT",
    "KIND_SECTION",
    "KIND_PARAGRAPH",
    "KIND_ARTICLE",
    "KIND_JUDGMENT_MAIN",
    "KIND_EVIDENCE",
    "KIND_HOLDING",
    "KIND_FINDING",
    "LEGAL_KINDS",
    "STATUS_CURRENT",
    "STATUS_SUPERSEDED",
    "STATUS_REPEALED",
    "law_uri",
    "case_uri",
    "legal_anchor",
    "legal_extension",
    "get_effective_status",
    "set_effective_status",
    "create_docir_from_markdown",
    # Lifecycle
    "ConfidenceMetadata",
    "SupersedenceMetadata",
    "KnowledgePageLifecycle",
    "LifecycleManager",
    # Graph
    "GraphNode",
    "GraphEdge",
    "TraversalResult",
    "LegalKnowledgeGraph",
]

__version__ = "0.1.0"

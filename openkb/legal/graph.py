"""法宝法律智能知识库 - 法律知识图谱

Legal knowledge graph module for OpenKB, including:
- Graph node and edge types
- Graph index storage
- Graph traversal and query
- Legal-specific relationships
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from openkb.legal.schema import AuthorityLevel, RelationType


# ----------------------------------------------------------------------------
# 图谱节点 - Graph Node
# ----------------------------------------------------------------------------
@dataclass
class GraphNode:
    """知识图谱节点

    代表法律领域的实体或概念
    """

    node_id: str
    node_type: str  # entity type or 'concept'/'doctrine'
    label: str
    description: Optional[str] = None

    # 源引用
    source_page: Optional[str] = None
    source_doc: Optional[str] = None

    # 法律特定元数据
    authority_level: Optional[AuthorityLevel] = None
    effective_date: Optional[datetime] = None
    expiry_date: Optional[datetime] = None
    status: str = "active"

    # 索引
    aliases: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "label": self.label,
            "description": self.description,
            "source_page": self.source_page,
            "source_doc": self.source_doc,
            "authority_level": self.authority_level.value if self.authority_level else None,
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "status": self.status,
            "aliases": self.aliases,
            "tags": self.tags,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GraphNode":
        """从字典创建"""
        data = data.copy()
        if "authority_level" in data and data["authority_level"]:
            data["authority_level"] = (
                AuthorityLevel[data["authority_level"].upper()]
                if isinstance(data["authority_level"], str)
                else AuthorityLevel(data["authority_level"])
            )
        if "effective_date" in data and data["effective_date"]:
            data["effective_date"] = datetime.fromisoformat(data["effective_date"])
        if "expiry_date" in data and data["expiry_date"]:
            data["expiry_date"] = datetime.fromisoformat(data["expiry_date"])
        if "created_at" in data and data["created_at"]:
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if "updated_at" in data and data["updated_at"]:
            data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        return cls(**data)


# ----------------------------------------------------------------------------
# 图谱关系 - Graph Edge
# ----------------------------------------------------------------------------
@dataclass
class GraphEdge:
    """知识图谱关系边

    代表法律实体之间的关系
    """

    edge_id: str
    source_id: str
    target_id: str
    relation_type: RelationType
    weight: float = 1.0
    confidence: float = 0.9

    # 元数据
    description: Optional[str] = None
    source_page: Optional[str] = None  # 来源页面
    authority_level: Optional[AuthorityLevel] = None

    # 时间
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    # 其他元数据
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "edge_id": self.edge_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation_type": self.relation_type.value,
            "weight": self.weight,
            "confidence": self.confidence,
            "description": self.description,
            "source_page": self.source_page,
            "authority_level": self.authority_level.value if self.authority_level else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GraphEdge":
        """从字典创建"""
        data = data.copy()
        if "relation_type" in data:
            data["relation_type"] = (
                RelationType(data["relation_type"])
                if isinstance(data["relation_type"], str)
                else data["relation_type"]
            )
        if "authority_level" in data and data["authority_level"]:
            data["authority_level"] = (
                AuthorityLevel[data["authority_level"].upper()]
                if isinstance(data["authority_level"], str)
                else AuthorityLevel(data["authority_level"])
            )
        if "created_at" in data and data["created_at"]:
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if "updated_at" in data and data["updated_at"]:
            data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        return cls(**data)


# ----------------------------------------------------------------------------
# 遍历结果 - Traversal Result
# ----------------------------------------------------------------------------
@dataclass
class TraversalResult:
    """图谱遍历结果"""

    node: GraphNode
    depth: int
    path: List[str]  # 节点 ID 路径
    edges: List[GraphEdge]  # 路径上的边
    total_weight: float = 1.0


# ----------------------------------------------------------------------------
# 法律知识图谱 - Legal Knowledge Graph
# ----------------------------------------------------------------------------
class LegalKnowledgeGraph:
    """法律知识图谱

    提供:
    - 节点和边的管理
    - 图谱查询和遍历
    - 冲突检测
    - 影响分析
    """

    def __init__(self, kb_dir: Path) -> None:
        self.kb_dir = kb_dir
        self.graph_dir = kb_dir / ".openkb" / "graph"
        self.graph_dir.mkdir(parents=True, exist_ok=True)

        # 索引文件
        self._nodes_path = self.graph_dir / "nodes.json"
        self._edges_path = self.graph_dir / "edges.json"
        self._index_path = self.graph_dir / "index.json"

        # 内存索引
        self._nodes: Dict[str, GraphNode] = {}
        self._edges: Dict[str, GraphEdge] = {}
        self._outgoing_edges: Dict[str, List[str]] = {}  # node_id -> [edge_id]
        self._incoming_edges: Dict[str, List[str]] = {}  # node_id -> [edge_id]
        self._node_alias_index: Dict[str, str] = {}  # alias -> node_id
        self._type_index: Dict[str, List[str]] = {}  # type -> [node_id]

        # 加载
        self._load()

    def _load(self) -> None:
        """从磁盘加载图谱"""
        if self._nodes_path.exists():
            with self._nodes_path.open("r", encoding="utf-8") as f:
                nodes_data = json.load(f)
            for node_id, node_dict in nodes_data.items():
                self._nodes[node_id] = GraphNode.from_dict(node_dict)

        if self._edges_path.exists():
            with self._edges_path.open("r", encoding="utf-8") as f:
                edges_data = json.load(f)
            for edge_id, edge_dict in edges_data.items():
                edge = GraphEdge.from_dict(edge_dict)
                self._edges[edge_id] = edge
                self._add_edge_to_indices(edge)

        # 构建索引
        for node in self._nodes.values():
            self._add_node_to_indices(node)

    def _save(self) -> None:
        """保存图谱到磁盘"""
        # 保存节点
        nodes_data = {k: v.to_dict() for k, v in self._nodes.items()}
        with self._nodes_path.open("w", encoding="utf-8") as f:
            json.dump(nodes_data, f, ensure_ascii=False, indent=2)

        # 保存边
        edges_data = {k: v.to_dict() for k, v in self._edges.items()}
        with self._edges_path.open("w", encoding="utf-8") as f:
            json.dump(edges_data, f, ensure_ascii=False, indent=2)

        # 保存索引元数据
        index_data = {
            "node_count": len(self._nodes),
            "edge_count": len(self._edges),
            "last_updated": datetime.now().isoformat(),
        }
        with self._index_path.open("w", encoding="utf-8") as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)

    def _add_node_to_indices(self, node: GraphNode) -> None:
        """添加节点到索引"""
        # 类型索引
        if node.node_type not in self._type_index:
            self._type_index[node.node_type] = []
        if node.node_id not in self._type_index[node.node_type]:
            self._type_index[node.node_type].append(node.node_id)

        # 别名索引
        for alias in node.aliases:
            self._node_alias_index[alias.lower()] = node.node_id
        # 也用 label 作为别名
        self._node_alias_index[node.label.lower()] = node.node_id

    def _add_edge_to_indices(self, edge: GraphEdge) -> None:
        """添加边到索引"""
        # 出边索引
        if edge.source_id not in self._outgoing_edges:
            self._outgoing_edges[edge.source_id] = []
        if edge.edge_id not in self._outgoing_edges[edge.source_id]:
            self._outgoing_edges[edge.source_id].append(edge.edge_id)

        # 入边索引
        if edge.target_id not in self._incoming_edges:
            self._incoming_edges[edge.target_id] = []
        if edge.edge_id not in self._incoming_edges[edge.target_id]:
            self._incoming_edges[edge.target_id].append(edge.edge_id)

    def _remove_edge_from_indices(self, edge: GraphEdge) -> None:
        """从索引移除边"""
        if edge.source_id in self._outgoing_edges:
            if edge.edge_id in self._outgoing_edges[edge.source_id]:
                self._outgoing_edges[edge.source_id].remove(edge.edge_id)
        if edge.target_id in self._incoming_edges:
            if edge.edge_id in self._incoming_edges[edge.target_id]:
                self._incoming_edges[edge.target_id].remove(edge.edge_id)

    def _generate_node_id(self, label: str, node_type: str) -> str:
        """生成节点 ID"""
        key = f"{node_type}:{label}"
        return f"node_{hashlib.md5(key.encode()).hexdigest()[:12]}"

    def _generate_edge_id(self, source_id: str, target_id: str, relation_type: RelationType) -> str:
        """生成边 ID"""
        key = f"{source_id}:{target_id}:{relation_type.value}"
        return f"edge_{hashlib.md5(key.encode()).hexdigest()[:12]}"

    # ------------------------------------------------------------------------
    # 节点操作 - Node Operations
    # ------------------------------------------------------------------------

    def add_node(
        self,
        label: str,
        node_type: str,
        description: Optional[str] = None,
        source_page: Optional[str] = None,
        source_doc: Optional[str] = None,
        authority_level: Optional[AuthorityLevel] = None,
        aliases: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        node_id: Optional[str] = None,
    ) -> GraphNode:
        """添加节点

        如果已存在类似节点（同类型同标签），则返回现有节点
        """
        # 检查是否已存在
        existing_id = self._node_alias_index.get(label.lower())
        if existing_id and existing_id in self._nodes:
            existing = self._nodes[existing_id]
            if existing.node_type == node_type:
                return existing

        # 创建新节点
        if node_id is None:
            node_id = self._generate_node_id(label, node_type)

        node = GraphNode(
            node_id=node_id,
            node_type=node_type,
            label=label,
            description=description,
            source_page=source_page,
            source_doc=source_doc,
            authority_level=authority_level,
            aliases=aliases or [],
            tags=tags or [],
            metadata=metadata or {},
        )

        self._nodes[node_id] = node
        self._add_node_to_indices(node)
        self._save()
        return node

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        """获取节点"""
        return self._nodes.get(node_id)

    def find_node(self, label: str, node_type: Optional[str] = None) -> Optional[GraphNode]:
        """通过标签查找节点"""
        node_id = self._node_alias_index.get(label.lower())
        if node_id:
            node = self._nodes.get(node_id)
            if node and (node_type is None or node.node_type == node_type):
                return node

        # 如果找不到，尝试按类型过滤查找
        if node_type and node_type in self._type_index:
            for candidate_id in self._type_index[node_type]:
                candidate = self._nodes.get(candidate_id)
                if candidate and candidate.label == label:
                    return candidate

        return None

    def find_or_create_node(
        self,
        label: str,
        node_type: str,
        **kwargs: Any,
    ) -> GraphNode:
        """查找或创建节点"""
        existing = self.find_node(label, node_type)
        if existing:
            return existing
        return self.add_node(label, node_type, **kwargs)

    def get_nodes_by_type(self, node_type: str) -> List[GraphNode]:
        """获取指定类型的所有节点"""
        node_ids = self._type_index.get(node_type, [])
        return [self._nodes[id] for id in node_ids if id in self._nodes]

    def update_node(self, node_id: str, **kwargs: Any) -> Optional[GraphNode]:
        """更新节点"""
        node = self._nodes.get(node_id)
        if not node:
            return None

        # 更新字段
        for key, value in kwargs.items():
            if hasattr(node, key):
                setattr(node, key, value)

        node.updated_at = datetime.now()
        self._save()
        return node

    # ------------------------------------------------------------------------
    # 边操作 - Edge Operations
    # ------------------------------------------------------------------------

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        relation_type: RelationType,
        weight: float = 1.0,
        confidence: float = 0.9,
        description: Optional[str] = None,
        source_page: Optional[str] = None,
        authority_level: Optional[AuthorityLevel] = None,
        metadata: Optional[Dict[str, Any]] = None,
        edge_id: Optional[str] = None,
    ) -> GraphEdge:
        """添加边

        如果已存在相同关系，则更新现有边
        """
        # 检查是否已存在
        existing = self.get_edge(source_id, target_id, relation_type)
        if existing:
            existing.weight = max(existing.weight, weight)
            existing.confidence = max(existing.confidence, confidence)
            if description:
                existing.description = description
            existing.updated_at = datetime.now()
            self._save()
            return existing

        # 创建新边
        if edge_id is None:
            edge_id = self._generate_edge_id(source_id, target_id, relation_type)

        edge = GraphEdge(
            edge_id=edge_id,
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
            weight=weight,
            confidence=confidence,
            description=description,
            source_page=source_page,
            authority_level=authority_level,
            metadata=metadata or {},
        )

        self._edges[edge_id] = edge
        self._add_edge_to_indices(edge)
        self._save()
        return edge

    def get_edge(
        self, source_id: str, target_id: str, relation_type: RelationType
    ) -> Optional[GraphEdge]:
        """获取特定边"""
        # 检查源节点的出边
        outgoing = self._outgoing_edges.get(source_id, [])
        for edge_id in outgoing:
            edge = self._edges.get(edge_id)
            if edge and edge.target_id == target_id and edge.relation_type == relation_type:
                return edge
        return None

    def get_outgoing_edges(
        self, node_id: str, relation_types: Optional[List[RelationType]] = None
    ) -> List[GraphEdge]:
        """获取节点的出边"""
        edge_ids = self._outgoing_edges.get(node_id, [])
        edges = [self._edges[id] for id in edge_ids if id in self._edges]

        if relation_types:
            edges = [e for e in edges if e.relation_type in relation_types]

        return edges

    def get_incoming_edges(
        self, node_id: str, relation_types: Optional[List[RelationType]] = None
    ) -> List[GraphEdge]:
        """获取节点的入边"""
        edge_ids = self._incoming_edges.get(node_id, [])
        edges = [self._edges[id] for id in edge_ids if id in self._edges]

        if relation_types:
            edges = [e for e in edges if e.relation_type in relation_types]

        return edges

    # ------------------------------------------------------------------------
    # 图谱查询 - Graph Queries
    # ------------------------------------------------------------------------

    def traverse(
        self,
        start_node_id: str,
        relation_types: Optional[List[RelationType]] = None,
        max_depth: int = 3,
        min_confidence: float = 0.5,
    ) -> List[TraversalResult]:
        """遍历图谱

        从起始节点开始，按照指定关系类型进行广度优先遍历

        Args:
            start_node_id: 起始节点 ID
            relation_types: 要遍历的关系类型（None 表示所有类型）
            max_depth: 最大遍历深度
            min_confidence: 最低置信度

        Returns:
            遍历结果列表
        """
        if start_node_id not in self._nodes:
            return []

        results: List[TraversalResult] = []
        visited: Set[str] = set()
        queue: List[Tuple[str, int, List[str], List[GraphEdge], float]] = [
            (start_node_id, 0, [start_node_id], [], 1.0)
        ]

        while queue:
            node_id, depth, path, edges, total_weight = queue.pop(0)

            if node_id in visited:
                continue
            visited.add(node_id)

            node = self._nodes.get(node_id)
            if not node:
                continue

            results.append(
                TraversalResult(
                    node=node,
                    depth=depth,
                    path=path.copy(),
                    edges=edges.copy(),
                    total_weight=total_weight,
                )
            )

            if depth >= max_depth:
                continue

            outgoing = self.get_outgoing_edges(node_id, relation_types)
            for edge in outgoing:
                if edge.confidence < min_confidence:
                    continue
                if edge.target_id in visited:
                    continue

                queue.append(
                    (
                        edge.target_id,
                        depth + 1,
                        path + [edge.target_id],
                        edges + [edge],
                        total_weight * edge.weight,
                    )
                )

        return results

    def find_related(
        self,
        node_id: str,
        relation_type: RelationType,
        limit: int = 20,
    ) -> List[Tuple[GraphNode, GraphEdge]]:
        """查找相关节点

        Args:
            node_id: 起始节点
            relation_type: 关系类型
            limit: 最大结果数

        Returns:
            (节点, 边) 元组列表
        """
        edges = self.get_outgoing_edges(node_id, [relation_type])
        edges.sort(key=lambda e: e.weight * e.confidence, reverse=True)

        results = []
        for edge in edges[:limit]:
            target_node = self._nodes.get(edge.target_id)
            if target_node:
                results.append((target_node, edge))

        return results

    def find_affecting_nodes(
        self,
        changed_node_id: str,
    ) -> List[TraversalResult]:
        """查找受影响的节点

        当某个法条变更时，查找所有受影响的案件、引用等

        Args:
            changed_node_id: 变更的节点 ID

        Returns:
            受影响的节点列表
        """
        # 反向遍历：查找所有引用、适用、等关系指向变更节点的节点
        # 即查找入边为 CITES, APPLIES, INTERPRETS 等的节点
        affect_relations = [
            RelationType.CITES,
            RelationType.APPLIES,
            RelationType.INTERPRETS,
            RelationType.FOLLOWS,
            RelationType.DISTINGUISHES,
        ]

        results: List[TraversalResult] = []
        visited: Set[str] = set()
        queue: List[Tuple[str, int, List[str], List[GraphEdge]]] = [
            (changed_node_id, 0, [changed_node_id], [])
        ]

        while queue:
            node_id, depth, path, edges = queue.pop(0)

            if node_id in visited:
                continue
            visited.add(node_id)

            node = self._nodes.get(node_id)
            if not node:
                continue

            if node_id != changed_node_id:
                results.append(
                    TraversalResult(
                        node=node,
                        depth=depth,
                        path=path.copy(),
                        edges=edges.copy(),
                    )
                )

            if depth >= 3:
                continue

            # 查找入边：其他节点影响此节点
            incoming = self.get_incoming_edges(node_id, affect_relations)
            for edge in incoming:
                if edge.source_id in visited:
                    continue
                queue.append(
                    (
                        edge.source_id,
                        depth + 1,
                        path + [edge.source_id],
                        edges + [edge],
                    )
                )

        return results

    def detect_contradictions(
        self,
        authority_check: bool = True,
    ) -> List[Tuple[GraphNode, GraphNode, List[GraphEdge]]]:
        """检测矛盾

        查找相互矛盾的节点对

        Args:
            authority_check: 是否按权威级别过滤（低权威不能与高权威矛盾）

        Returns:
            矛盾节点对列表，每个条目为 (node1, node2, [contradiction_edges])
        """
        contradictions = []

        # 查找所有 CONTRADICTS 关系
        for edge in self._edges.values():
            if edge.relation_type != RelationType.CONTRADICTS:
                continue

            node1 = self._nodes.get(edge.source_id)
            node2 = self._nodes.get(edge.target_id)
            if not node1 or not node2:
                continue

            if authority_check:
                level1 = node1.authority_level or AuthorityLevel.LEGAL_SCHOLARSHIP
                level2 = node2.authority_level or AuthorityLevel.LEGAL_SCHOLARSHIP
                if level1 > level2:
                    continue  # 高权威与低权威的矛盾不算有效矛盾

            contradictions.append((node1, node2, [edge]))

        return contradictions

    # ------------------------------------------------------------------------
    # 统计信息 - Statistics
    # ------------------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """获取图谱统计信息"""
        type_counts = {t: len(ids) for t, ids in self._type_index.items()}
        relation_counts: Dict[str, int] = {}
        for edge in self._edges.values():
            rt = edge.relation_type.value
            relation_counts[rt] = relation_counts.get(rt, 0) + 1

        return {
            "node_count": len(self._nodes),
            "edge_count": len(self._edges),
            "node_types": type_counts,
            "relation_types": relation_counts,
        }

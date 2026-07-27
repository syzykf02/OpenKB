"""法宝法律智能知识库 - 视觉节点注册

Visual node registry for OpenKB, providing:
- Visual node metadata storage
- On-demand analysis triggering
- Analysis result caching
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


# ----------------------------------------------------------------------------
# 视觉节点信息 - Visual Node Info
# ----------------------------------------------------------------------------
@dataclass
class VisualNodeInfo:
    """视觉节点信息

    存储视觉内容的元数据，但不预分析内容
    """
    node_id: str
    doc_name: str
    page_number: int
    visual_type: str = "image"  # image, table, signature, handwritten, chart

    # 位置信息
    region: Optional[Dict[str, int]] = None  # x, y, width, height (pixels)
    region_coords: Optional[Dict[str, float]] = None  # x0, y0, x1, y1 (PDF coords)

    # 上下文信息
    surrounding_text: Optional[str] = None
    caption: Optional[str] = None
    label: Optional[str] = None

    # 文件引用
    image_path: Optional[str] = None  # relative to KB root
    image_hash: Optional[str] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None

    # 分析缓存（按需填充）
    analysis_cache: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # tool_name -> result
    last_analyzed: Optional[datetime] = None

    # 元数据
    created_at: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def get_analysis(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """获取缓存的分析结果"""
        return self.analysis_cache.get(tool_name)

    def set_analysis(self, tool_name: str, result: Dict[str, Any]) -> None:
        """缓存分析结果"""
        self.analysis_cache[tool_name] = result
        self.last_analyzed = datetime.now()

    def needs_analysis(self, tool_name: str, max_age_hours: int = 24) -> bool:
        """是否需要分析

        Args:
            tool_name: 工具名称
            max_age_hours: 缓存最大保留时间

        Returns:
            True 如果需要新分析
        """
        if tool_name not in self.analysis_cache:
            return True
        if self.last_analyzed is None:
            return True

        age = datetime.now() - self.last_analyzed
        return age.total_seconds() > max_age_hours * 3600

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "node_id": self.node_id,
            "doc_name": self.doc_name,
            "page_number": self.page_number,
            "visual_type": self.visual_type,
            "region": self.region,
            "region_coords": self.region_coords,
            "surrounding_text": self.surrounding_text,
            "caption": self.caption,
            "label": self.label,
            "image_path": self.image_path,
            "image_hash": self.image_hash,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "analysis_cache": self.analysis_cache,
            "last_analyzed": self.last_analyzed.isoformat() if self.last_analyzed else None,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VisualNodeInfo":
        """从字典创建"""
        data = data.copy()
        if "last_analyzed" in data and data["last_analyzed"]:
            data["last_analyzed"] = datetime.fromisoformat(data["last_analyzed"])
        if "created_at" in data and data["created_at"]:
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        return cls(**data)


# ----------------------------------------------------------------------------
# 视觉注册表 - Visual Registry
# ----------------------------------------------------------------------------
class VisualRegistry:
    """视觉节点注册表

    管理知识库中所有视觉节点的元数据
    """

    def __init__(self, kb_dir: Path) -> None:
        self.kb_dir = kb_dir
        self.registry_dir = kb_dir / ".openkb" / "visual"
        self.registry_dir.mkdir(parents=True, exist_ok=True)

        self._index_path = self.registry_dir / "index.json"
        self._nodes: Dict[str, VisualNodeInfo] = {}
        self._doc_index: Dict[str, List[str]] = {}  # doc_name -> [node_id]
        self._page_index: Dict[str, List[str]] = {}  # "doc_name:page" -> [node_id]

        self._load()

    def _get_node_path(self, node_id: str) -> Path:
        """获取节点数据文件路径"""
        safe_id = hashlib.md5(node_id.encode()).hexdigest()
        return self.registry_dir / f"node_{safe_id}.json"

    def _load(self) -> None:
        """从磁盘加载注册表"""
        if not self._index_path.exists():
            return

        with self._index_path.open("r", encoding="utf-8") as f:
            index_data = json.load(f)

        for node_id in index_data.get("nodes", []):
            node_path = self._get_node_path(node_id)
            if node_path.exists():
                with node_path.open("r", encoding="utf-8") as f:
                    node_data = json.load(f)
                node = VisualNodeInfo.from_dict(node_data)
                self._nodes[node_id] = node
                self._add_node_to_indices(node)

    def _save(self) -> None:
        """保存注册表到磁盘"""
        # 保存索引
        index_data = {
            "nodes": list(self._nodes.keys()),
            "doc_index": self._doc_index,
            "page_index": self._page_index,
            "last_updated": datetime.now().isoformat(),
        }
        with self._index_path.open("w", encoding="utf-8") as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)

    def _save_node(self, node: VisualNodeInfo) -> None:
        """保存单个节点"""
        node_path = self._get_node_path(node.node_id)
        with node_path.open("w", encoding="utf-8") as f:
            json.dump(node.to_dict(), f, ensure_ascii=False, indent=2)

    def _add_node_to_indices(self, node: VisualNodeInfo) -> None:
        """添加节点到索引"""
        # 文档索引
        if node.doc_name not in self._doc_index:
            self._doc_index[node.doc_name] = []
        if node.node_id not in self._doc_index[node.doc_name]:
            self._doc_index[node.doc_name].append(node.node_id)

        # 页码索引
        page_key = f"{node.doc_name}:{node.page_number}"
        if page_key not in self._page_index:
            self._page_index[page_key] = []
        if node.node_id not in self._page_index[page_key]:
            self._page_index[page_key].append(node.node_id)

    def _remove_node_from_indices(self, node: VisualNodeInfo) -> None:
        """从索引移除节点"""
        if node.doc_name in self._doc_index:
            if node.node_id in self._doc_index[node.doc_name]:
                self._doc_index[node.doc_name].remove(node.node_id)
            if not self._doc_index[node.doc_name]:
                del self._doc_index[node.doc_name]

        page_key = f"{node.doc_name}:{node.page_number}"
        if page_key in self._page_index:
            if node.node_id in self._page_index[page_key]:
                self._page_index[page_key].remove(node.node_id)
            if not self._page_index[page_key]:
                del self._page_index[page_key]

    def _generate_node_id(self, doc_name: str, page_number: int, suffix: int = 0) -> str:
        """生成节点 ID"""
        key = f"{doc_name}:{page_number}:{suffix}"
        return f"visual_{hashlib.md5(key.encode()).hexdigest()[:12]}"

    def register_visual_node(
        self,
        doc_name: str,
        page_number: int,
        visual_type: str = "image",
        region: Optional[Dict[str, int]] = None,
        region_coords: Optional[Dict[str, float]] = None,
        surrounding_text: Optional[str] = None,
        caption: Optional[str] = None,
        label: Optional[str] = None,
        image_path: Optional[str] = None,
        image_hash: Optional[str] = None,
        image_width: Optional[int] = None,
        image_height: Optional[int] = None,
    ) -> VisualNodeInfo:
        """注册视觉节点

        只注册元数据，不预分析内容

        Args:
            doc_name: 文档名称
            page_number: 页码
            visual_type: 视觉内容类型
            region: 像素区域坐标
            region_coords: PDF 坐标
            surrounding_text: 周边文本
            caption: 图片说明
            label: 标签（如 "Figure 1"）
            image_path: 图片文件路径
            image_hash: 图片哈希
            image_width: 图片宽度
            image_height: 图片高度

        Returns:
            注册的视觉节点
        """
        # 生成唯一 ID
        suffix = 0
        node_id = self._generate_node_id(doc_name, page_number, suffix)
        while node_id in self._nodes:
            suffix += 1
            node_id = self._generate_node_id(doc_name, page_number, suffix)

        node = VisualNodeInfo(
            node_id=node_id,
            doc_name=doc_name,
            page_number=page_number,
            visual_type=visual_type,
            region=region,
            region_coords=region_coords,
            surrounding_text=surrounding_text,
            caption=caption,
            label=label,
            image_path=image_path,
            image_hash=image_hash,
            image_width=image_width,
            image_height=image_height,
        )

        self._nodes[node_id] = node
        self._add_node_to_indices(node)
        self._save_node(node)
        self._save()
        return node

    def get_node(self, node_id: str) -> Optional[VisualNodeInfo]:
        """获取节点"""
        return self._nodes.get(node_id)

    def get_nodes_for_doc(self, doc_name: str) -> List[VisualNodeInfo]:
        """获取文档的所有视觉节点"""
        node_ids = self._doc_index.get(doc_name, [])
        return [self._nodes[id] for id in node_ids if id in self._nodes]

    def get_nodes_for_page(self, doc_name: str, page_number: int) -> List[VisualNodeInfo]:
        """获取页面的所有视觉节点"""
        page_key = f"{doc_name}:{page_number}"
        node_ids = self._page_index.get(page_key, [])
        return [self._nodes[id] for id in node_ids if id in self._nodes]

    def update_node_analysis(
        self,
        node_id: str,
        tool_name: str,
        analysis_result: Dict[str, Any],
    ) -> Optional[VisualNodeInfo]:
        """更新节点的分析结果缓存"""
        node = self._nodes.get(node_id)
        if not node:
            return None

        node.set_analysis(tool_name, analysis_result)
        self._save_node(node)
        self._save()
        return node

    def find_nodes_by_type(self, doc_name: str, visual_type: str) -> List[VisualNodeInfo]:
        """查找文档中指定类型的视觉节点"""
        nodes = self.get_nodes_for_doc(doc_name)
        return [n for n in nodes if n.visual_type == visual_type]

    def find_signature_nodes(self, doc_name: str) -> List[VisualNodeInfo]:
        """查找签名节点"""
        return self.find_nodes_by_type(doc_name, "signature")

    def find_table_nodes(self, doc_name: str) -> List[VisualNodeInfo]:
        """查找表格节点"""
        return self.find_nodes_by_type(doc_name, "table")

    def find_chart_nodes(self, doc_name: str) -> List[VisualNodeInfo]:
        """查找图表节点"""
        return self.find_nodes_by_type(doc_name, "chart")

    def stats(self) -> Dict[str, Any]:
        """获取注册表统计信息"""
        type_counts: Dict[str, int] = {}
        for node in self._nodes.values():
            vt = node.visual_type
            type_counts[vt] = type_counts.get(vt, 0) + 1

        return {
            "total_nodes": len(self._nodes),
            "docs_with_visuals": len(self._doc_index),
            "type_counts": type_counts,
        }

"""法宝法律智能知识库 - 知识生命周期管理

Knowledge lifecycle management for legal documents, including:
- Confidence metadata tracking
- Supersede relationships and history
- Knowledge decay engine
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from openkb.legal.schema import (
    CONFIDENCE_MEDIUM,
    DecayRate,
    DocumentStatus,
)


# ----------------------------------------------------------------------------
# 置信度元数据 - Confidence Metadata
# ----------------------------------------------------------------------------
@dataclass
class ConfidenceMetadata:
    """置信度元数据

    每个知识页面都携带置信度信息，包括:
    - 置信度分数 (0.0-1.0)
    - 支持来源数量
    - 最后确认时间
    - 矛盾声明列表
    - 衰减速率
    """

    confidence: float = CONFIDENCE_MEDIUM
    sources_count: int = 0
    last_confirmed: Optional[datetime] = None
    contradicted_by: List[str] = field(default_factory=list)
    decay_rate: DecayRate = DecayRate.SLOW
    original_confidence: Optional[float] = None  # 初始置信度
    created_at: datetime = field(default_factory=datetime.now)
    confirmed_history: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.original_confidence is None:
            self.original_confidence = self.confidence
        if self.last_confirmed is None:
            self.last_confirmed = datetime.now()

    def confirm(self, confirmation_confidence: Optional[float] = None) -> None:
        """确认知识，更新最后确认时间并可能提高置信度

        Args:
            confirmation_confidence: 可选的确认置信度（用于加权更新）
        """
        now = datetime.now()
        self.confirmed_history.append(
            {
                "at": now.isoformat(),
                "type": "confirm",
                "old_confidence": self.confidence,
                "sources_count": self.sources_count,
            }
        )
        self.last_confirmed = now

        if confirmation_confidence is not None:
            # 加权平均更新置信度
            weight_old = self.sources_count
            weight_new = 1
            self.confidence = (
                self.confidence * weight_old + confirmation_confidence * weight_new
            ) / (weight_old + weight_new)

    def add_source(self, confidence_boost: float = 0.05) -> None:
        """添加来源，增加置信度

        Args:
            confidence_boost: 每个来源的置信度提升
        """
        self.sources_count += 1
        # 随着来源增加，置信度逐步提升，但有上限
        max_boost = 0.2  # 最多提升 0.2
        boost = min(confidence_boost, max_boost / (self.sources_count))
        self.confidence = min(1.0, self.confidence + boost)

        self.confirmed_history.append(
            {
                "at": datetime.now().isoformat(),
                "type": "add_source",
                "old_confidence": self.confidence - boost,
                "new_confidence": self.confidence,
                "sources_count": self.sources_count,
            }
        )
        self.last_confirmed = datetime.now()

    def add_contradiction(self, contradiction_id: str) -> None:
        """添加矛盾声明

        Args:
            contradiction_id: 矛盾声明的 ID 或引用
        """
        if contradiction_id not in self.contradicted_by:
            self.contradicted_by.append(contradiction_id)
            # 有矛盾时降低置信度
            old_confidence = self.confidence
            self.confidence = max(0.1, self.confidence - 0.15)

            self.confirmed_history.append(
                {
                    "at": datetime.now().isoformat(),
                    "type": "add_contradiction",
                    "old_confidence": old_confidence,
                    "new_confidence": self.confidence,
                    "contradiction": contradiction_id,
                }
            )

    def resolve_contradiction(self, contradiction_id: str) -> None:
        """解决矛盾

        Args:
            contradiction_id: 要解决的矛盾声明 ID
        """
        if contradiction_id in self.contradicted_by:
            self.contradicted_by.remove(contradiction_id)
            # 解决矛盾时恢复部分置信度
            old_confidence = self.confidence
            self.confidence = min(1.0, self.confidence + 0.1)

            self.confirmed_history.append(
                {
                    "at": datetime.now().isoformat(),
                    "type": "resolve_contradiction",
                    "old_confidence": old_confidence,
                    "new_confidence": self.confidence,
                    "resolved": contradiction_id,
                }
            )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典用于序列化"""
        return {
            "confidence": self.confidence,
            "sources_count": self.sources_count,
            "last_confirmed": self.last_confirmed.isoformat() if self.last_confirmed else None,
            "contradicted_by": self.contradicted_by,
            "decay_rate": self.decay_rate.value,
            "original_confidence": self.original_confidence,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "confirmed_history": self.confirmed_history,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConfidenceMetadata":
        """从字典反序列化"""
        data = data.copy()
        if "decay_rate" in data and isinstance(data["decay_rate"], str):
            data["decay_rate"] = DecayRate(data["decay_rate"])
        if "last_confirmed" in data and data["last_confirmed"]:
            data["last_confirmed"] = datetime.fromisoformat(data["last_confirmed"])
        if "created_at" in data and data["created_at"]:
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        return cls(**data)


# ----------------------------------------------------------------------------
# 取代元数据 - Supersedence Metadata
# ----------------------------------------------------------------------------
@dataclass
class SupersedenceMetadata:
    """取代元数据

    记录知识被取代的信息:
    - 当前状态
    - 被谁取代
    - 何时被取代
    - 取代原因
    - 触发方式
    """

    status: DocumentStatus = DocumentStatus.ACTIVE
    superseded_by: Optional[str] = None  # 取代者的页面引用
    superseded_at: Optional[datetime] = None
    supersede_reason: Optional[str] = None
    triggered_by: str = "manual"  # manual, sync, contradiction, statute_change, import
    supersedes_list: List[str] = field(default_factory=list)  # 此页面取代的列表
    supersede_history: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            self.status = DocumentStatus(self.status)

    def mark_superseded(
        self,
        superseded_by: str,
        reason: str,
        triggered_by: str = "manual",
    ) -> None:
        """标记为被取代

        Args:
            superseded_by: 取代者的页面引用
            reason: 取代原因
            triggered_by: 触发方式
        """
        old_status = self.status
        self.supersede_history.append(
            {
                "at": datetime.now().isoformat(),
                "type": "superseded",
                "old_status": old_status.value if old_status else None,
                "new_status": DocumentStatus.SUPERSEDED.value,
                "superseded_by": superseded_by,
                "reason": reason,
                "triggered_by": triggered_by,
            }
        )

        self.status = DocumentStatus.SUPERSEDED
        self.superseded_by = superseded_by
        self.superseded_at = datetime.now()
        self.supersede_reason = reason
        self.triggered_by = triggered_by

    def mark_repealed(self, reason: str, triggered_by: str = "manual") -> None:
        """标记为已废止

        Args:
            reason: 废止原因
            triggered_by: 触发方式
        """
        old_status = self.status
        self.supersede_history.append(
            {
                "at": datetime.now().isoformat(),
                "type": "repealed",
                "old_status": old_status.value if old_status else None,
                "new_status": DocumentStatus.REPEALED.value,
                "reason": reason,
                "triggered_by": triggered_by,
            }
        )

        self.status = DocumentStatus.REPEALED
        self.superseded_at = datetime.now()
        self.supersede_reason = reason
        self.triggered_by = triggered_by

    def mark_amended(self, new_version_ref: Optional[str] = None, reason: str = "") -> None:
        """标记为已修订（用于文档自身更新）

        Args:
            new_version_ref: 新版本的引用（可选）
            reason: 修订原因
        """
        old_status = self.status
        self.supersede_history.append(
            {
                "at": datetime.now().isoformat(),
                "type": "amended",
                "old_status": old_status.value if old_status else None,
                "new_status": DocumentStatus.AMENDED.value,
                "new_version": new_version_ref,
                "reason": reason,
            }
        )

        self.status = DocumentStatus.AMENDED
        if new_version_ref:
            self.superseded_by = new_version_ref
        self.superseded_at = datetime.now()
        if reason:
            self.supersede_reason = reason

    def add_supersedes(self, target_ref: str) -> None:
        """记录此页面取代了另一个页面

        Args:
            target_ref: 被此页面取代的页面引用
        """
        if target_ref not in self.supersedes_list:
            self.supersedes_list.append(target_ref)
            self.supersede_history.append(
                {
                    "at": datetime.now().isoformat(),
                    "type": "supersedes_added",
                    "target": target_ref,
                }
            )

    def is_active(self) -> bool:
        """是否为有效状态"""
        return self.status == DocumentStatus.ACTIVE

    def is_superseded(self) -> bool:
        """是否被取代"""
        return self.status == DocumentStatus.SUPERSEDED

    def is_repealed(self) -> bool:
        """是否被废止"""
        return self.status == DocumentStatus.REPEALED

    def needs_supersede_processing(self) -> bool:
        """是否需要取代处理"""
        return DocumentStatus.requires_supersede(self.status.value)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典用于序列化"""
        return {
            "status": self.status.value if self.status else None,
            "superseded_by": self.superseded_by,
            "superseded_at": self.superseded_at.isoformat() if self.superseded_at else None,
            "supersede_reason": self.supersede_reason,
            "triggered_by": self.triggered_by,
            "supersedes_list": self.supersedes_list,
            "supersede_history": self.supersede_history,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SupersedenceMetadata":
        """从字典反序列化"""
        data = data.copy()
        if "status" in data and data["status"]:
            data["status"] = DocumentStatus(data["status"])
        if "superseded_at" in data and data["superseded_at"]:
            data["superseded_at"] = datetime.fromisoformat(data["superseded_at"])
        return cls(**data)


# ----------------------------------------------------------------------------
# 知识页面生命周期 - Knowledge Page Lifecycle
# ----------------------------------------------------------------------------
@dataclass
class KnowledgePageLifecycle:
    """知识页面完整生命周期管理

    整合:
    - 置信度元数据
    - 取代元数据
    """

    page_path: str
    confidence: ConfidenceMetadata = field(default_factory=ConfidenceMetadata)
    supersede: SupersedenceMetadata = field(default_factory=SupersedenceMetadata)
    version: int = 1
    first_created: datetime = field(default_factory=datetime.now)
    last_modified: datetime = field(default_factory=datetime.now)
    custom_metadata: Dict[str, Any] = field(default_factory=dict)

    def bump_version(self) -> None:
        """增加版本号"""
        self.version += 1
        self.last_modified = datetime.now()

    def is_accessible(self) -> bool:
        """是否可访问（有效或保留历史的已取代知识）"""
        return self.supersede.status in (
            DocumentStatus.ACTIVE,
            DocumentStatus.AMENDED,
            DocumentStatus.SUPERSEDED,  # 历史知识仍可查询
        )

    def should_show_warning(self) -> bool:
        """是否应该显示警告（已失效的知识）"""
        return self.supersede.status in (
            DocumentStatus.SUPERSEDED,
            DocumentStatus.REPEALED,
            DocumentStatus.EXPIRED,
        )

    def get_warning_message(self) -> Optional[str]:
        """获取警告消息"""
        if not self.should_show_warning():
            return None

        messages = {
            DocumentStatus.SUPERSEDED: f"此知识已被取代。请参阅: {self.supersede.superseded_by}",
            DocumentStatus.REPEALED: f"此知识已废止。原因: {self.supersede.supersede_reason}",
            DocumentStatus.EXPIRED: "此知识已过期。",
        }
        return messages.get(self.supersede.status)

    def to_frontmatter_dict(self) -> Dict[str, Any]:
        """转换为前题字典"""
        result = {
            "confidence": self.confidence.confidence,
            "sources_count": self.confidence.sources_count,
            "last_confirmed": self.confidence.last_confirmed.isoformat()
            if self.confidence.last_confirmed
            else None,
            "status": self.supersede.status.value if self.supersede.status else None,
            "decay_rate": self.confidence.decay_rate.value,
            "version": self.version,
        }

        if self.supersede.superseded_by:
            result["superseded_by"] = self.supersede.superseded_by
        if self.supersede.superseded_at:
            result["superseded_at"] = self.supersede.superseded_at.isoformat()
        if self.supersede.supersede_reason:
            result["supersede_reason"] = self.supersede.supersede_reason
        if self.confidence.contradicted_by:
            result["contradicted_by"] = self.confidence.contradicted_by
        if self.supersede.supersedes_list:
            result["supersedes_list"] = self.supersede.supersedes_list

        if self.custom_metadata:
            result.update(self.custom_metadata)

        return result

    @classmethod
    def from_frontmatter_dict(
        cls, data: Dict[str, Any], page_path: str
    ) -> "KnowledgePageLifecycle":
        """从前题字典创建"""
        confidence = ConfidenceMetadata(
            confidence=data.get("confidence", CONFIDENCE_MEDIUM),
            sources_count=data.get("sources_count", 0),
            last_confirmed=datetime.fromisoformat(data["last_confirmed"])
            if data.get("last_confirmed")
            else None,
            contradicted_by=data.get("contradicted_by", []),
            decay_rate=DecayRate(data["decay_rate"]) if data.get("decay_rate") else DecayRate.SLOW,
        )

        supersede = SupersedenceMetadata(
            status=DocumentStatus(data["status"]) if data.get("status") else DocumentStatus.ACTIVE,
            superseded_by=data.get("superseded_by"),
            superseded_at=datetime.fromisoformat(data["superseded_at"])
            if data.get("superseded_at")
            else None,
            supersede_reason=data.get("supersede_reason"),
            supersedes_list=data.get("supersedes_list", []),
        )

        custom_metadata = {
            k: v
            for k, v in data.items()
            if k
            not in [
                "confidence",
                "sources_count",
                "last_confirmed",
                "status",
                "decay_rate",
                "superseded_by",
                "superseded_at",
                "supersede_reason",
                "contradicted_by",
                "supersedes_list",
                "version",
            ]
        }

        return cls(
            page_path=page_path,
            confidence=confidence,
            supersede=supersede,
            version=data.get("version", 1),
            custom_metadata=custom_metadata,
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "page_path": self.page_path,
            "confidence": self.confidence.to_dict(),
            "supersede": self.supersede.to_dict(),
            "version": self.version,
            "first_created": self.first_created.isoformat(),
            "last_modified": self.last_modified.isoformat(),
            "custom_metadata": self.custom_metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KnowledgePageLifecycle":
        """从字典创建"""
        return cls(
            page_path=data["page_path"],
            confidence=ConfidenceMetadata.from_dict(data["confidence"]),
            supersede=SupersedenceMetadata.from_dict(data["supersede"]),
            version=data.get("version", 1),
            first_created=datetime.fromisoformat(data["first_created"]),
            last_modified=datetime.fromisoformat(data["last_modified"]),
            custom_metadata=data.get("custom_metadata", {}),
        )


# ----------------------------------------------------------------------------
# 生命周期管理器 - Lifecycle Manager
# ----------------------------------------------------------------------------
class LifecycleManager:
    """知识生命周期管理器

    管理知识库中所有页面的生命周期数据
    """

    def __init__(self, kb_dir: Path) -> None:
        self.kb_dir = kb_dir
        self.lifecycle_dir = kb_dir / ".openkb" / "lifecycle"
        self.lifecycle_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, KnowledgePageLifecycle] = {}

    def _get_lifecycle_path(self, page_path: str) -> Path:
        """获取页面生命周期数据的存储路径"""
        # 规范化路径作为文件名
        safe_name = hashlib.md5(page_path.encode()).hexdigest()
        return self.lifecycle_dir / f"{safe_name}.json"

    def get_lifecycle(self, page_path: str) -> KnowledgePageLifecycle:
        """获取页面的生命周期数据

        如果不存在则创建新的
        """
        if page_path in self._cache:
            return self._cache[page_path]

        lifecycle_path = self._get_lifecycle_path(page_path)

        if lifecycle_path.exists():
            with lifecycle_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            lifecycle = KnowledgePageLifecycle.from_dict(data)
            self._cache[page_path] = lifecycle
            return lifecycle

        # 创建新的
        lifecycle = KnowledgePageLifecycle(page_path=page_path)
        self._cache[page_path] = lifecycle
        return lifecycle

    def save_lifecycle(self, lifecycle: KnowledgePageLifecycle) -> None:
        """保存页面生命周期数据"""
        lifecycle_path = self._get_lifecycle_path(lifecycle.page_path)
        with lifecycle_path.open("w", encoding="utf-8") as f:
            json.dump(lifecycle.to_dict(), f, ensure_ascii=False, indent=2)
        self._cache[lifecycle.page_path] = lifecycle

    def update_confidence(
        self,
        page_path: str,
        new_confidence: Optional[float] = None,
        add_source: bool = False,
        add_confirmation: bool = False,
    ) -> KnowledgePageLifecycle:
        """更新页面置信度

        Args:
            page_path: 页面路径
            new_confidence: 可选的新置信度值
            add_source: 是否添加来源计数
            add_confirmation: 是否执行确认操作

        Returns:
            更新后的生命周期对象
        """
        lifecycle = self.get_lifecycle(page_path)

        if new_confidence is not None:
            lifecycle.confidence.confidence = new_confidence
        if add_source:
            lifecycle.confidence.add_source()
        if add_confirmation:
            lifecycle.confidence.confirm()

        lifecycle.bump_version()
        self.save_lifecycle(lifecycle)
        return lifecycle

    def mark_superseded(
        self,
        page_path: str,
        superseded_by: str,
        reason: str,
        triggered_by: str = "manual",
    ) -> KnowledgePageLifecycle:
        """标记页面被取代"""
        lifecycle = self.get_lifecycle(page_path)
        lifecycle.supersede.mark_superseded(superseded_by, reason, triggered_by)
        lifecycle.bump_version()
        self.save_lifecycle(lifecycle)

        # 更新取代者的 supersedes_list
        if superseded_by:
            superseder_lifecycle = self.get_lifecycle(superseded_by)
            superseder_lifecycle.supersede.add_supersedes(page_path)
            self.save_lifecycle(superseder_lifecycle)

        return lifecycle

    def add_contradiction(
        self,
        page_path: str,
        contradiction_id: str,
    ) -> KnowledgePageLifecycle:
        """添加矛盾声明"""
        lifecycle = self.get_lifecycle(page_path)
        lifecycle.confidence.add_contradiction(contradiction_id)
        lifecycle.bump_version()
        self.save_lifecycle(lifecycle)
        return lifecycle

    def resolve_contradiction(
        self,
        page_path: str,
        contradiction_id: str,
    ) -> KnowledgePageLifecycle:
        """解决矛盾声明"""
        lifecycle = self.get_lifecycle(page_path)
        lifecycle.confidence.resolve_contradiction(contradiction_id)
        lifecycle.bump_version()
        self.save_lifecycle(lifecycle)
        return lifecycle

"""法宝法律智能知识库 - 同步引擎

Sync engine for OpenKB, providing:
- Sync source management
- File manifest and diff detection
- Change tracking
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ----------------------------------------------------------------------------
# 同步源类型 - Sync Source Type
# ----------------------------------------------------------------------------
class SyncSourceType(Enum):
    """同步源类型"""

    LOCAL_DIR = "local_dir"
    WEBDAV = "webdav"
    GIT = "git"
    SFTP = "sftp"


# ----------------------------------------------------------------------------
# 同步源 - Sync Source
# ----------------------------------------------------------------------------
@dataclass
class SyncSource:
    """同步源配置"""

    source_id: str
    source_type: SyncSourceType
    path: str
    name: Optional[str] = None
    enabled: bool = True

    # 过滤配置
    include_patterns: List[str] = field(default_factory=list)
    exclude_patterns: List[str] = field(default_factory=list)

    # 触发配置
    auto_sync: bool = False
    sync_interval_minutes: int = 60

    # 法律特定配置
    auto_tag_new: Optional[str] = None  # 自动添加标签
    default_authority_level: Optional[str] = None  # 默认权威级别

    # 元数据
    config: Dict[str, Any] = field(default_factory=dict)
    last_sync: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        if isinstance(self.source_type, str):
            self.source_type = SyncSourceType(self.source_type)
        if self.name is None:
            self.name = self.source_id

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "source_id": self.source_id,
            "source_type": self.source_type.value,
            "path": self.path,
            "name": self.name,
            "enabled": self.enabled,
            "include_patterns": self.include_patterns,
            "exclude_patterns": self.exclude_patterns,
            "auto_sync": self.auto_sync,
            "sync_interval_minutes": self.sync_interval_minutes,
            "auto_tag_new": self.auto_tag_new,
            "default_authority_level": self.default_authority_level,
            "config": self.config,
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SyncSource":
        """从字典创建"""
        data = data.copy()
        if "source_type" in data:
            data["source_type"] = SyncSourceType(data["source_type"])
        if "last_sync" in data and data["last_sync"]:
            data["last_sync"] = datetime.fromisoformat(data["last_sync"])
        if "created_at" in data and data["created_at"]:
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        return cls(**data)


# ----------------------------------------------------------------------------
# 文件清单 - File Manifest
# ----------------------------------------------------------------------------
@dataclass
class FileManifestEntry:
    """文件清单条目"""

    path: str
    hash: str
    size: int
    mtime: float  # modification timestamp
    is_dir: bool = False
    first_seen: datetime = field(default_factory=datetime.now)
    last_seen: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "path": self.path,
            "hash": self.hash,
            "size": self.size,
            "mtime": self.mtime,
            "is_dir": self.is_dir,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FileManifestEntry":
        """从字典创建"""
        data = data.copy()
        if "first_seen" in data:
            data["first_seen"] = datetime.fromisoformat(data["first_seen"])
        if "last_seen" in data:
            data["last_seen"] = datetime.fromisoformat(data["last_seen"])
        return cls(**data)


@dataclass
class DiffResult:
    """差异比较结果"""

    new_files: List[str] = field(default_factory=list)
    modified_files: List[str] = field(default_factory=list)
    deleted_files: List[str] = field(default_factory=list)
    unchanged_files: List[str] = field(default_factory=list)


@dataclass
class SyncApplyResult:
    """Outcome of applying a sync diff - the ingest trigger (spec section 3.3).

    ``ingested`` maps each new/modified source file to its ingest outcome
    (``"converted"`` / ``"long_doc"`` / ``"skipped"`` / ``"failed: ..."``).
    ``deleted`` lists source-removed files (actual KB removal is a separate
    explicit step - deletion is destructive and stays human-gated).
    """

    source_id: str
    ingested: List[Tuple[str, str]] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def total_changed(self) -> int:
        return len(self.ingested) + len(self.deleted)


class SyncManifest:
    """同步清单

    记录同步源的文件状态，用于差异检测
    """

    def __init__(self, kb_dir: Path, source_id: str) -> None:
        self.kb_dir = kb_dir
        self.source_id = source_id
        self.manifest_dir = kb_dir / ".openkb" / "sync" / "manifests"
        self.manifest_dir.mkdir(parents=True, exist_ok=True)

        self._manifest_path = self.manifest_dir / f"{source_id}.json"
        self._entries: Dict[str, FileManifestEntry] = {}
        self._load()

    def _load(self) -> None:
        """从磁盘加载清单"""
        if not self._manifest_path.exists():
            return

        with self._manifest_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        for path, entry_dict in data.get("entries", {}).items():
            self._entries[path] = FileManifestEntry.from_dict(entry_dict)

    def _save(self) -> None:
        """保存清单到磁盘"""
        data = {
            "source_id": self.source_id,
            "last_updated": datetime.now().isoformat(),
            "entries": {k: v.to_dict() for k, v in self._entries.items()},
        }
        with self._manifest_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def compute_file_hash(file_path: Path) -> str:
        """计算文件哈希"""
        hasher = hashlib.sha256()
        try:
            with file_path.open("rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception:
            return ""

    def scan_directory(
        self,
        dir_path: Path,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ) -> Dict[str, FileManifestEntry]:
        """扫描目录，构建当前文件清单

        Args:
            dir_path: 目录路径
            include_patterns: 包含模式
            exclude_patterns: 排除模式

        Returns:
            文件清单字典
        """
        if not dir_path.exists() or not dir_path.is_dir():
            return {}

        current_entries: Dict[str, FileManifestEntry] = {}

        dir_path = dir_path.resolve()
        for root, dirs, files in os.walk(dir_path):
            # 检查排除模式
            rel_root = Path(root).relative_to(dir_path) if dir_path != Path(root) else Path(".")

            for file_name in files:
                file_path = Path(root) / file_name
                rel_path = str(rel_root / file_name) if rel_root != Path(".") else file_name

                # 简单的模式检查（未来可扩展为 glob）
                if exclude_patterns:
                    excluded = False
                    for pat in exclude_patterns:
                        if pat in rel_path:
                            excluded = True
                            break
                    if excluded:
                        continue

                try:
                    stat = file_path.stat()
                    file_hash = self.compute_file_hash(file_path)
                    entry = FileManifestEntry(
                        path=rel_path,
                        hash=file_hash,
                        size=stat.st_size,
                        mtime=stat.st_mtime,
                        is_dir=False,
                    )
                    current_entries[rel_path] = entry
                except Exception:
                    continue

        return current_entries

    def diff(self, current_entries: Dict[str, FileManifestEntry]) -> DiffResult:
        """与当前状态比较，计算差异

        Args:
            current_entries: 当前文件清单

        Returns:
            差异结果
        """
        result = DiffResult()

        existing_paths = set(self._entries.keys())
        current_paths = set(current_entries.keys())

        # 新增文件
        result.new_files = list(current_paths - existing_paths)

        # 删除文件
        result.deleted_files = list(existing_paths - current_paths)

        # 检查修改
        for path in existing_paths & current_paths:
            old_entry = self._entries[path]
            new_entry = current_entries[path]

            if old_entry.hash != new_entry.hash:
                result.modified_files.append(path)
            else:
                result.unchanged_files.append(path)

        return result

    def update(
        self, current_entries: Dict[str, FileManifestEntry], diff: Optional[DiffResult] = None
    ) -> None:
        """更新清单

        Args:
            current_entries: 当前文件清单
            diff: 可选的差异结果
        """
        now = datetime.now()

        if diff is None:
            diff = self.diff(current_entries)

        # 更新修改和新增的文件
        for path in diff.new_files + diff.modified_files:
            entry = current_entries[path]
            if path in self._entries:
                # 保留 first_seen
                entry.first_seen = self._entries[path].first_seen
            entry.last_seen = now
            self._entries[path] = entry

        # 标记删除的文件（保留历史记录）
        for path in diff.deleted_files:
            if path in self._entries:
                self._entries[path].last_seen = now

        self._save()

    def get_entry(self, path: str) -> Optional[FileManifestEntry]:
        """获取文件条目"""
        return self._entries.get(path)

    def all_entries(self) -> Dict[str, FileManifestEntry]:
        """获取所有条目"""
        return dict(self._entries)


# ----------------------------------------------------------------------------
# 同步引擎 - Sync Engine
# ----------------------------------------------------------------------------
class SyncEngine:
    """同步引擎

    管理多个同步源，执行同步操作
    """

    def __init__(self, kb_dir: Path) -> None:
        self.kb_dir = kb_dir
        self.sync_dir = kb_dir / ".openkb" / "sync"
        self.sync_dir.mkdir(parents=True, exist_ok=True)

        self._sources_path = self.sync_dir / "sources.json"
        self._sources: Dict[str, SyncSource] = {}
        self._manifests: Dict[str, SyncManifest] = {}
        self._load()

    def _load(self) -> None:
        """从磁盘加载源配置"""
        if not self._sources_path.exists():
            return

        with self._sources_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        for source_id, source_dict in data.get("sources", {}).items():
            self._sources[source_id] = SyncSource.from_dict(source_dict)

    def _save(self) -> None:
        """保存源配置到磁盘"""
        data = {
            "last_updated": datetime.now().isoformat(),
            "sources": {k: v.to_dict() for k, v in self._sources.items()},
        }
        with self._sources_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_manifest(self, source_id: str) -> SyncManifest:
        """获取同步源的清单

        如果不存在则创建新的
        """
        if source_id not in self._manifests:
            self._manifests[source_id] = SyncManifest(self.kb_dir, source_id)
        return self._manifests[source_id]

    def register_source(
        self,
        source_id: str,
        source_type: SyncSourceType,
        path: str,
        name: Optional[str] = None,
        **kwargs: Any,
    ) -> SyncSource:
        """注册同步源

        Args:
            source_id: 源 ID
            source_type: 源类型
            path: 路径
            name: 可选的显示名称
            **kwargs: 其他配置

        Returns:
            同步源对象
        """
        source = SyncSource(
            source_id=source_id,
            source_type=source_type,
            path=path,
            name=name,
            **kwargs,
        )
        self._sources[source_id] = source
        self._save()
        return source

    def get_source(self, source_id: str) -> Optional[SyncSource]:
        """获取同步源"""
        return self._sources.get(source_id)

    def list_sources(self) -> List[SyncSource]:
        """列出所有同步源"""
        return list(self._sources.values())

    def scan_source(
        self,
        source_id: str,
    ) -> Tuple[SyncSource, Dict[str, FileManifestEntry], DiffResult]:
        """扫描同步源

        Args:
            source_id: 源 ID

        Returns:
            (源, 当前清单, 差异)
        """
        source = self._sources.get(source_id)
        if not source:
            raise ValueError(f"Unknown source: {source_id}")

        manifest = self.get_manifest(source_id)

        if source.source_type == SyncSourceType.LOCAL_DIR:
            dir_path = Path(source.path)
            current_entries = manifest.scan_directory(
                dir_path,
                include_patterns=source.include_patterns,
                exclude_patterns=source.exclude_patterns,
            )
        else:
            current_entries = {}

        diff = manifest.diff(current_entries)
        return source, current_entries, diff

    def update_source_manifest(
        self,
        source_id: str,
        current_entries: Dict[str, FileManifestEntry],
        diff: Optional[DiffResult] = None,
    ) -> None:
        """更新源清单

        Args:
            source_id: 源 ID
            current_entries: 当前清单
            diff: 可选的差异结果
        """
        manifest = self.get_manifest(source_id)
        manifest.update(current_entries, diff)

        # 更新源的最后同步时间
        source = self._sources.get(source_id)
        if source:
            source.last_sync = datetime.now()
            self._save()

    def apply_diff(
        self,
        source_id: str,
        *,
        ingest_callback: Optional[Any] = None,
    ) -> SyncApplyResult:
        """Apply a sync diff - the ingest trigger (spec section 3.3).

        Scans the source, then for each new/modified file ingests it into the KB.
        By default ingestion is convert-only (``converter.convert_document``:
        hash-check + copy to raw/ + .md + .docir.json + hash register, NO LLM
        compile) so this is testable without an LLM. Pass ``ingest_callback`` to
        run the full add pipeline (convert + compile + mutation) - e.g. the CLI
        wires this to its ``openkb add`` path.

        Deleted files are recorded but NOT auto-removed from the KB - deletion is
        destructive and stays human-gated (the spec's "可逆批量操作").

        LOCAL_DIR only for now; WebDAV/Git/SFTP return an empty result with a
        not-implemented note (their scan_source already yields no entries).
        """
        result = SyncApplyResult(source_id=source_id)
        source = self._sources.get(source_id)
        if source is None:
            result.errors.append(f"Unknown source: {source_id}")
            return result
        if source.source_type != SyncSourceType.LOCAL_DIR:
            result.errors.append(f"sync not implemented for {source.source_type.value} sources yet")
            return result

        _source, entries, diff = self.scan_source(source_id)
        base = Path(source.path)

        for rel_path in diff.new_files + diff.modified_files:
            src = base / rel_path
            if not src.exists() or not src.is_file():
                result.errors.append(f"missing: {rel_path}")
                continue
            outcome = self._ingest_one(src, ingest_callback)
            result.ingested.append((rel_path, outcome))

        result.deleted = list(diff.deleted_files)
        # Update the manifest so the next scan sees the ingested state.
        self.update_source_manifest(source_id, entries, diff)
        return result

    def _ingest_one(self, src: Path, ingest_callback: Optional[Any]) -> str:
        """Ingest a single file. Returns an outcome string."""
        try:
            if ingest_callback is not None:
                return ingest_callback(src)
            # Default: convert-only (no LLM compile).
            from openkb.converter import convert_document

            res = convert_document(src, self.kb_dir)
            if res.skipped:
                return "skipped"
            return "long_doc" if res.is_long_doc else "converted"
        except Exception as exc:  # noqa: BLE001 - surface per-file failure, keep going
            return f"failed: {exc}"

    def remove_source(self, source_id: str) -> bool:
        """移除同步源"""
        if source_id not in self._sources:
            return False
        del self._sources[source_id]
        if source_id in self._manifests:
            del self._manifests[source_id]
        self._save()
        return True

    def stats(self) -> Dict[str, Any]:
        """获取同步引擎统计信息"""
        source_stats = []
        for source in self._sources.values():
            manifest = self.get_manifest(source.source_id)
            source_stats.append(
                {
                    "source_id": source.source_id,
                    "name": source.name,
                    "type": source.source_type.value,
                    "enabled": source.enabled,
                    "file_count": len(manifest.all_entries()),
                    "last_sync": source.last_sync.isoformat() if source.last_sync else None,
                }
            )

        return {
            "source_count": len(self._sources),
            "sources": source_stats,
        }

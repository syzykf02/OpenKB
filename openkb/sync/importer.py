"""法宝法律智能知识库 - 批量导入器

Batch importer for OpenKB, providing:
- Directory scanning
- File filtering
- Import planning
- Dry-run support
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# 支持的文件类型
SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".txt",
    ".md",
    ".html",
    ".htm",
    ".rtf",
    ".csv",
    ".json",
    ".yaml",
    ".yml",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".tif",
    ".tiff",
    ".bmp",
}


@dataclass
class FileImportInfo:
    """文件导入信息"""

    path: Path
    file_size: int
    file_type: str
    mime_type: Optional[str] = None
    estimated_pages: int = 1
    skip_reason: Optional[str] = None
    should_import: bool = True

    # 处理后
    doc_name: Optional[str] = None
    import_status: Optional[str] = None  # pending, imported, skipped, failed
    error_message: Optional[str] = None


@dataclass
class ImportPlan:
    """导入计划"""

    source_path: Path
    files: List[FileImportInfo] = field(default_factory=list)
    dry_run: bool = False

    # 统计
    total_files: int = 0
    to_import: int = 0
    to_skip: int = 0
    estimated_total_pages: int = 0
    estimated_total_size: int = 0

    # 过滤
    include_patterns: List[str] = field(default_factory=list)
    exclude_patterns: List[str] = field(default_factory=list)


class BatchImporter:
    """批量导入器"""

    def __init__(self, kb_dir: Path) -> None:
        self.kb_dir = kb_dir

    def scan_directory(
        self,
        dir_path: Path,
        recursive: bool = True,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ) -> ImportPlan:
        """扫描目录，生成导入计划

        Args:
            dir_path: 目录路径
            recursive: 是否递归
            include_patterns: 包含模式
            exclude_patterns: 排除模式

        Returns:
            导入计划
        """
        plan = ImportPlan(
            source_path=dir_path,
            include_patterns=include_patterns or [],
            exclude_patterns=exclude_patterns or [],
        )

        if not dir_path.exists() or not dir_path.is_dir():
            return plan

        # 扫描文件
        if recursive:
            files = list(dir_path.rglob("*"))
        else:
            files = list(dir_path.glob("*"))

        for file_path in files:
            if file_path.is_dir():
                continue

            info = self._analyze_file(file_path, dir_path)
            plan.files.append(info)

            if info.should_import:
                plan.to_import += 1
                plan.estimated_total_pages += info.estimated_pages
                plan.estimated_total_size += info.file_size
            else:
                plan.to_skip += 1

        plan.total_files = len(plan.files)
        return plan

    def _analyze_file(
        self,
        file_path: Path,
        root_path: Path,
    ) -> FileImportInfo:
        """分析单个文件

        Args:
            file_path: 文件路径
            root_path: 根路径（用于计算相对路径）

        Returns:
            文件信息
        """
        info = FileImportInfo(
            path=file_path,
            file_size=file_path.stat().st_size if file_path.exists() else 0,
            file_type=file_path.suffix.lower(),
        )

        # 检查扩展名
        if info.file_type not in SUPPORTED_EXTENSIONS:
            info.should_import = False
            info.skip_reason = f"Unsupported file type: {info.file_type}"
            return info

        # 检查文件大小（跳过小于 10 字节的文件）
        if info.file_size < 10:
            info.should_import = False
            info.skip_reason = "File too small"
            return info

        # 检查隐藏文件
        if file_path.name.startswith("."):
            info.should_import = False
            info.skip_reason = "Hidden file"
            return info

        # 获取 MIME 类型
        info.mime_type, _ = mimetypes.guess_type(str(file_path))

        # 估计页数
        if info.file_type == ".pdf":
            # 使用简单估计（实际可以用 PyMuPDF 精确计算）
            info.estimated_pages = max(1, info.file_size // 100000)  # ~100KB 每页
        elif info.file_type in {".doc", ".docx"}:
            info.estimated_pages = max(1, info.file_size // 20000)  # ~20KB 每页
        elif info.file_type in {".ppt", ".pptx"}:
            info.estimated_pages = max(1, info.file_size // 500000)  # ~500KB 每页
        else:
            info.estimated_pages = 1

        info.import_status = "pending"
        return info

    def filter_plan(
        self,
        plan: ImportPlan,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        min_size: Optional[int] = None,
        max_size: Optional[int] = None,
    ) -> ImportPlan:
        """过滤导入计划

        Args:
            plan: 原计划
            include_patterns: 包含模式
            exclude_patterns: 排除模式
            min_size: 最小文件大小
            max_size: 最大文件大小

        Returns:
            过滤后的计划
        """
        filtered = ImportPlan(
            source_path=plan.source_path,
            dry_run=plan.dry_run,
            include_patterns=include_patterns or plan.include_patterns,
            exclude_patterns=exclude_patterns or plan.exclude_patterns,
        )

        for info in plan.files:
            if not info.should_import:
                filtered.files.append(info)
                continue

            # 检查大小
            if min_size is not None and info.file_size < min_size:
                info.should_import = False
                info.skip_reason = f"File too small ({info.file_size} < {min_size})"
            elif max_size is not None and info.file_size > max_size:
                info.should_import = False
                info.skip_reason = f"File too large ({info.file_size} > {max_size})"

            # 检查路径模式
            str_path = str(info.path)
            if exclude_patterns:
                for pat in exclude_patterns:
                    if pat in str_path:
                        info.should_import = False
                        info.skip_reason = f"Excluded by pattern: {pat}"
                        break

            if include_patterns and info.should_import:
                matched = False
                for pat in include_patterns:
                    if pat in str_path:
                        matched = True
                        break
                if not matched:
                    info.should_import = False
                    info.skip_reason = "Not matched by any include pattern"

            filtered.files.append(info)

            if info.should_import:
                filtered.to_import += 1
                filtered.estimated_total_pages += info.estimated_pages
                filtered.estimated_total_size += info.file_size
            else:
                filtered.to_skip += 1

        filtered.total_files = len(filtered.files)
        return filtered

    def estimate_import_time(
        self,
        plan: ImportPlan,
        pages_per_minute: int = 20,
    ) -> Dict[str, Any]:
        """估计导入时间

        Args:
            plan: 导入计划
            pages_per_minute: 每分钟处理页数

        Returns:
            估计信息
        """
        estimated_minutes = plan.estimated_total_pages / pages_per_minute
        estimated_seconds = estimated_minutes * 60

        # 格式化
        if estimated_seconds < 60:
            time_str = f"{int(estimated_seconds)} seconds"
        elif estimated_seconds < 3600:
            minutes = int(estimated_seconds // 60)
            seconds = int(estimated_seconds % 60)
            time_str = f"{minutes} minutes {seconds} seconds"
        else:
            hours = int(estimated_seconds // 3600)
            minutes = int((estimated_seconds % 3600) // 60)
            time_str = f"{hours} hours {minutes} minutes"

        return {
            "total_pages": plan.estimated_total_pages,
            "total_size": plan.estimated_total_size,
            "estimated_minutes": estimated_minutes,
            "estimated_seconds": estimated_seconds,
            "human_readable": time_str,
            "pages_per_minute": pages_per_minute,
        }

    def get_file_type_summary(
        self,
        plan: ImportPlan,
    ) -> Dict[str, int]:
        """获取文件类型统计

        Args:
            plan: 导入计划

        Returns:
            文件类型统计
        """
        type_counts: Dict[str, int] = {}
        for info in plan.files:
            if info.should_import:
                ext = info.file_type
                type_counts[ext] = type_counts.get(ext, 0) + 1
        return type_counts

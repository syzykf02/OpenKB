"""法宝法律智能知识库 - 视觉工具模块

Vision tool integration for OpenKB, including:
- Visual node registry
- On-demand image analysis
- Page rendering
- Vision policy configuration
"""

from openkb.visual.registry import (
    VisualNodeInfo,
    VisualRegistry,
)

__all__ = [
    "VisualRegistry",
    "VisualNodeInfo",
]

__version__ = "0.1.0"

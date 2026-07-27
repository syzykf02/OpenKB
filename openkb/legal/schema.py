"""法宝法律智能知识库 - 法律 Schema 定义

This module defines the legal schema for OpenKB, including:
- Legal entity types
- Legal document types
- Confidence levels
- Page content directories
- Document status enum
"""

from enum import Enum
from typing import List, Tuple

# ----------------------------------------------------------------------------
# 置信度级别 - Confidence Levels
# ----------------------------------------------------------------------------
CONFIDENCE_HIGH: float = 0.9
CONFIDENCE_MEDIUM: float = 0.7
CONFIDENCE_LOW: float = 0.4

# ----------------------------------------------------------------------------
# 法律实体类型 - Legal Entity Types
# ----------------------------------------------------------------------------
LEGAL_ENTITY_TYPES: List[str] = [
    "statute",          # 法律/法规
    "regulation",       # 规章/条例
    "case",           # 案件/判例
    "court",          # 法院
    "judge",          # 法官
    "plaintiff",      # 原告
    "defendant",      # 被告
    "attorney",        # 律师/代理人
    "citation",        # 引用/引证
    "precedent",       # 先例/判例
    "motion",         # 动议/申请
    "evidence",       # 证据
    "contract",       # 合同/契约
    "treaty",         # 条约/协定
    "doctrine",       # 法律原则/学说
    "doctrine_principle",  # 法律原则
    "legal_principle",     # 法律原理
    "legal_rule",           # 法律规则
    "legal_concept",       # 法律概念
    "legal_term",         # 法律术语
]

# 实体类型中文描述
LEGAL_ENTITY_TYPE_DESCRIPTIONS = {
    "statute": "法律法规",
    "regulation": "规章条例",
    "case": "案件判例",
    "court": "法院",
    "judge": "法官",
    "plaintiff": "原告",
    "defendant": "被告",
    "attorney": "律师代理人",
    "citation": "引用引证",
    "precedent": "先例判例",
    "motion": "动议申请",
    "evidence": "证据",
    "contract": "合同契约",
    "treaty": "条约协定",
    "doctrine": "法律原则学说",
}

# ----------------------------------------------------------------------------
# 法律文档类型 - Legal Document Types
# ----------------------------------------------------------------------------
LEGAL_DOC_TYPES: List[str] = [
    "statute",          # 法律/法规文本
    "regulation",       # 规章/条例
    "judicial_opinion",   # 司法意见书/判决书
    "contract",          # 合同/协议
    "pleading",         # 诉状/答辩状
    "motion",           # 动议/申请书
    "brief",            # 法律意见书/案情摘要
    "treaty",           # 条约/协定
    "legal_textbook",   # 法律教科书
    "legal_treatise",   # 法学专著
    "court_rule",      # 法院规则
    "administrative_ruling",  # 行政裁决
    "arbitration_award",       # 仲裁裁决
    "legal_memo",           # 法律备忘录
    "evidence_exhibit",       # 证据展示
    "case_docket",            # 案件档案
    "deposition",            # 证词/证词笔录
    "interrogatory",         # 质询书
    "request_for_production",  # 证据开示请求
    "request_for_admission",   # 自认请求
]

# 文档类型中文描述
LEGAL_DOC_TYPE_DESCRIPTIONS = {
    "statute": "法律法规文本",
    "regulation": "规章条例",
    "judicial_opinion": "司法意见书判决书",
    "contract": "合同协议",
    "pleading": "诉状答辩状",
    "motion": "动议申请书",
    "brief": "法律意见书案情摘要",
    "treaty": "条约协定",
    "legal_textbook": "法律教科书",
    "legal_treatise": "法学专著",
    "court_rule": "法院规则",
    "administrative_ruling": "行政裁决",
    "arbitration_award": "仲裁裁决",
    "legal_memo": "法律备忘录",
    "evidence_exhibit": "证据展示",
    "case_docket": "案件档案",
    "deposition": "证词证词笔录",
    "interrogatory": "质询书",
    "request_for_production": "证据开示请求",
    "request_for_admission": "自认请求",
}

# ----------------------------------------------------------------------------
# 法律知识页面类型 - Legal Page Content Directories
# ----------------------------------------------------------------------------
# 扩展 OpenKB 的 PAGE_CONTENT_DIRS
LEGAL_PAGE_CONTENT_DIRS: Tuple[str, ...] = (
    "doctrines",      # 法律原则页面
    "legal_entities", # 法律实体页面
)

# ----------------------------------------------------------------------------
# 文档状态枚举 - Document Status Enum
# ----------------------------------------------------------------------------
class DocumentStatus(Enum):
    """文档/知识点的状态枚举"""
    ACTIVE = "active"           # 现行有效
    SUPERSEDED = "superseded"   # 已被取代
    REPEALED = "repealed"       # 已废止
    AMENDED = "amended"         # 已修订
    EXPIRED = "expired"         # 已过期
    PENDING = "pending"         # 待生效
    DRAFT = "draft"           # 草案/草稿
    ARCHIVED = "archived"     # 已归档

    @classmethod
    def is_active_status(cls, status: str) -> bool:
        """检查是否为有效状态"""
        return status in (cls.ACTIVE.value, cls.ACTIVE)

    @classmethod
    def requires_supersede(cls, status: str) -> bool:
        """检查是否需要取代处理"""
        return status in (cls.SUPERSEDED.value, cls.SUPERSEDED,
                         cls.REPEALED.value, cls.REPEALED,
                         cls.EXPIRED.value, cls.EXPIRED)

    def __str__(self) -> str:
        return self.value

# ----------------------------------------------------------------------------
# 关系类型 - Relation Types (for graph)
# ----------------------------------------------------------------------------
class RelationType(Enum):
    """法律知识图谱关系类型"""
    REVISES = "revises"           # 修订关系
    REPEALS = "repeals"           # 废止关系
    CITES = "cites"              # 引用关系
    APPLIES = "applies"          # 适用于（法条适用于案件）
    CONTRADICTS = "contradicts"   # 矛盾关系
    SIMILAR_CASE = "similar_case" # 类案关系
    SUPERSEDES = "supersedes"      # 取代关系
    PROVES = "proves"              # 证据证明关系
    IMPLEMENTS = "implements"   # 实施条例实施法律
    INTERPRETS = "interprets"    # 解释关系（司法解释解释法律）
    OVERRULES = "overrules"     # 推翻关系（上级法院推翻下级法院）
    FOLLOWS = "follows"          # 遵循关系（遵循先例）
    DISTINGUISHES = "distinguishes"  # 区别关系（区分先例）
    AFFIRMS = "affirms"         # 维持关系（上诉维持原判）
    REVERSES = "reverses"        # 撤销关系（上诉撤销原判）
    REMANDS = "remands"           # 发回关系（发回重审）

    def __str__(self) -> str:
        return self.value

# ----------------------------------------------------------------------------
# 视觉节点类型 - Visual Node Types
# ----------------------------------------------------------------------------
VISUAL_NODE_TYPES: List[str] = [
    "image",           # 普通图像
    "chart",          # 图表
    "table",            # 表格
    "signature",        # 签名
    "stamp",            # 印章
    "handwritten_note",   # 手写批注
    "handwritten",    # 手写内容
    "diagram",          # 示意图
    "photo",            # 照片
    "exhibit",          # 证物图片
]

# ----------------------------------------------------------------------------
# 衰减速率 - Decay Rates
# ----------------------------------------------------------------------------
class DecayRate(Enum):
    """知识衰减速率"""
    SLOW = "slow"         # 极慢衰减 - 架构性知识，如案由体系
    MEDIUM = "medium"     # 中等衰减 - 时效性知识，如地方审判口径
    FAST = "fast"         # 快速衰减 - 临时性知识


# 衰减速率映射
_DECAY_RATE_MAP = {
    DecayRate.SLOW: 0.01,    # 1% 每月
    DecayRate.MEDIUM: 0.05,  # 5% 每月
    DecayRate.FAST: 0.20,    # 20% 每月
}

_HALF_LIFE_MAP = {
    DecayRate.SLOW: 69,      # 约 69 个月减半
    DecayRate.MEDIUM: 14,    # 14 个月减半
    DecayRate.FAST: 3,       # 3 个月减半
}


def get_decay_rate(decay_rate: DecayRate) -> float:
    """获取衰减率"""
    return _DECAY_RATE_MAP.get(decay_rate, 0.05)


def get_half_life(decay_rate: DecayRate) -> int:
    """获取半衰期（月）"""
    return _HALF_LIFE_MAP.get(decay_rate, 14)

# ----------------------------------------------------------------------------
# 法律权威位阶 - Legal Authority Hierarchy
# ----------------------------------------------------------------------------
class AuthorityLevel(Enum):
    """法律权威位阶 - 用于解决知识冲突"""
    CONSTITUTION = 100      # 宪法/根本法
    STATUTE = 90             # 法律
    REGULATION = 80          # 行政法规
    LOCAL_REGULATION = 70   # 地方性法规
    JUDICIAL_INTERPRETATION = 85  # 司法解释
    GUIDING_CASE = 75          # 指导性案例
    PRECEDENT = 60              # 一般先例
    LEGAL_SCHOLARSHIP = 40      # 法学学说
    LOCAL_GUIDANCE = 50        # 地方指导意见

    @property
    def level(self) -> int:
        return self.value

    def __gt__(self, other: 'AuthorityLevel') -> bool:
        if not isinstance(other, AuthorityLevel):
            return NotImplemented
        return self.value > other.value

    def __lt__(self, other: 'AuthorityLevel') -> bool:
        if not isinstance(other, AuthorityLevel):
            return NotImplemented
        return self.value < other.value

# ----------------------------------------------------------------------------
# AGENTS.md 法律 Schema 扩展 - Legal Schema Extension for AGENTS.md
# ----------------------------------------------------------------------------
LEGAL_SCHEMA_ADDITION = """
## 法律知识库扩展 - Legal Knowledge Base Extension

### 法律实体类型 - Legal Entity Types

法律实体是特定的法律相关事物。可用类型:

- **statute** - 法律法规
- **regulation** - 规章条例
- **case** - 案件判例
- **court** - 法院
- **judge** - 法官
- **plaintiff** - 原告
- **defendant** - 被告
- **attorney** - 律师代理人
- **citation** - 引用引证
- **precedent** - 先例判例
- **evidence** - 证据
- **contract** - 合同契约
- **treaty** - 条约协定
- **doctrine** - 法律原则学说

### 文档状态 - Document Status

每个文档和知识点都有状态:

- **active** - 现行有效
- **superseded** - 已被取代
- **repealed** - 已废止
- **amended** - 已修订
- **expired** - 已过期

### 前题元数据扩展 - Frontmatter Metadata Extension

法律知识页面包含以下额外的前题字段:

```yaml
---
type: Concept|Entity|Summary
description: 单句描述
confidence: 0.95
sources_count: 3
last_confirmed: 2024-01-15
status: active|superseded|repealed|amended|expired
superseded_by: [[doctrines/new-principle.md
superseded_at: 2024-01-20
supersede_reason: 新法出台了新规出台新法新规新规新规新规新规新规新规新规
decay_rate: slow|medium|fast
authority_level: constitution|statute|regulation|guiding_case|precedent
---
```

### 引用格式 - Citation Format

使用标准的引用格式:

- 法律: [[statutes/民法典]]
- 案例: [[cases/2023-民终-123号]]
- 司法解释: [[regulations/民法典解释一]]
"""

def get_legal_agents_md_extension() -> str:
    """获取法律 Schema 扩展的 Markdown 内容"""
    return LEGAL_SCHEMA_ADDITION

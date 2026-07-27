# 法宝法律智能知识库 - 示例用法

## 快速开始

### 1. 初始化知识库

```python
from pathlib import Path
from openkb.legal import (
    LifecycleManager,
    LegalKnowledgeGraph,
    AuthorityLevel,
    RelationType,
)
from openkb.visual import VisualRegistry
from openkb.sync import SyncEngine, SyncSourceType

kb_dir = Path("/path/to/legal_kb")
```

### 2. 知识图谱 - 添加法条和案例

```python
# 初始化图谱
graph = LegalKnowledgeGraph(kb_dir)

# 添加民法典法条
civil_code = graph.add_node(
    label="民法典",
    node_type="statute",
    description="中华人民共和国民法典",
    authority_level=AuthorityLevel.STATUTE,
)

article_577 = graph.add_node(
    label="民法典第577条",
    node_type="statute",
    description="违约责任",
    authority_level=AuthorityLevel.STATUTE,
)

# 建立法条间的关系
graph.add_edge(
    source_id=article_577.node_id,
    target_id=civil_code.node_id,
    relation_type=RelationType.PART_OF,
)

# 添加案例
case_1 = graph.add_node(
    label="张某诉李某合同纠纷案",
    node_type="case",
    description="合同违约案例",
)

# 建立案例与法条的引用关系
graph.add_edge(
    source_id=case_1.node_id,
    target_id=article_577.node_id,
    relation_type=RelationType.CITES,
)
```

### 3. 知识生命周期 - 置信度管理

```python
# 初始化生命周期管理器
lifecycle = LifecycleManager(kb_dir)

# 获取或创建页面生命周期
page_life = lifecycle.get_lifecycle("concepts/违约责任")

# 更新置信度
page_life.confidence.confidence = 0.92
page_life.confidence.sources_count = 5
page_life.confidence.decay_rate = DecayRate.SLOW

# 标记为被新法取代
page_life.supersede.mark_superseded(
    superseded_by="concepts/违约责任_2024",
    reason="2024年新司法解释出台",
    triggered_by="statute_change",
)

# 保存
lifecycle.save_lifecycle(page_life)
```

### 4. 视觉工具 - 注册证据图片（按需分析）

```python
# 初始化视觉注册表
visual = VisualRegistry(kb_dir)

# 注册签名图片（不预分析）
signature_node = visual.register_visual_node(
    doc_name="contract_001",
    page_number=3,
    visual_type="signature",
    region={"x": 100, "y": 200, "width": 300, "height": 100},
    surrounding_text="甲方签字处：",
    image_path="wiki/sources/images/contract_001/p3_img1.png",
)

# 注册银行流水图表
chart_node = visual.register_visual_node(
    doc_name="evidence_001",
    page_number=1,
    visual_type="chart",
    label="银行流水",
    caption="2023年5月银行账户流水",
    image_path="wiki/sources/images/evidence_001/p1_img1.png",
)
```

### 5. 同步引擎 - 导入目录

```python
# 初始化同步引擎
sync = SyncEngine(kb_dir)

# 注册本地目录同步源
source = sync.register_source(
    source_id="my_cases_dir",
    source_type=SyncSourceType.LOCAL_DIR,
    path="/path/to/my/cases",
    name="我的案例文档",
    auto_sync=False,
)

# 扫描源
source, entries, diff = sync.scan_source("my_cases_dir")

print(f"新增: {len(diff.new_files)}")
print(f"修改: {len(diff.modified_files)}")
print(f"删除: {len(diff.deleted_files)}")

# 更新清单
sync.update_source_manifest("my_cases_dir", entries, diff)
```

## 法律智能查询流程

### 示例："这个案例适用哪些法条？"

```python
# 1. 找到案例节点
case_node = graph.find_node("张某诉李某合同纠纷案", "case")

# 2. 查找案例引用的法条
cited_nodes = graph.find_related(case_node.node_id, RelationType.CITES)
print(f"该案引用了 {len(cited_nodes)} 个法条:")
for node, edge in cited_nodes:
    print(f"  - {node.label}")

# 3. 查找是否有相关的司法解释
interpretations = []
for node, edge in cited_nodes:
    interp_nodes = graph.find_related(node.node_id, RelationType.INTERPRETS)
    interpretations.extend(interp_nodes)

# 4. 检查是否有新法条取代了旧法条
for node, edge in cited_nodes:
    page_life = lifecycle.get_lifecycle(f"statutes/{node.label}")
    if page_life.supersede.is_superseded():
        print(f"注意: {node.label} 已被取代")
        print(f"  取代者: {page_life.supersede.superseded_by}")
        print(f"  原因: {page_life.supersede.supersede_reason}")
```

### 示例："这个法条变更影响哪些案件？"

```python
# 查找受法条变更影响的所有案件
article_node = graph.find_node("民法典第577条", "statute")

if article_node:
    affected = graph.find_affecting_nodes(article_node.node_id)
    print(f"该法条变更影响 {len(affected)} 个节点:")
    for result in affected:
        print(f"  - {result.node.label} (深度: {result.depth})")
```

## DocIR 文档结构化示例

```python
from openkb.legal import DocIRBuilder, DocIRNodeType

# 构建 DocIR 文档
builder = DocIRBuilder("contract_001", "contract")

# 添加内容
builder.add_node(
    DocIRNodeType.SECTION,
    "第一条 合同主体",
    page_number=1,
    heading="第一条 合同主体",
)

# 添加视觉节点引用
builder.add_visual_node(
    page_number=3,
    visual_type="signature",
    region={"x": 100, "y": 200, "width": 300, "height": 100},
    label="甲方签字",
)

# 构建文档
docir = builder.build()

# 保存
docir.save(kb_dir / ".openkb" / "docir" / "contract_001.json")
```

## 图谱冲突检测

```python
# 检测图谱中的矛盾
contradictions = graph.detect_contradictions()

if contradictions:
    print(f"发现 {len(contradictions)} 个潜在矛盾:")
    for node1, node2, edges in contradictions:
        print(f"  - {node1.label} <-> {node2.label}")

        # 根据权威级别确定如何解决
        level1 = node1.authority_level or AuthorityLevel.LEGAL_SCHOLARSHIP
        level2 = node2.authority_level or AuthorityLevel.LEGAL_SCHOLARSHIP

        if level1 > level2:
            print(f"    应以 {node1.label} 为准（权威级别更高）")
        elif level2 > level1:
            print(f"    应以 {node2.label} 为准（权威级别更高）")
        else:
            print(f"    需要人工判断")
```

# 法宝法律智能知识库 - 使用指南

## 目录

1. [快速开始](#1-快速开始)
2. [CLI 使用](#2-cli-使用)
3. [API 使用](#3-api-使用)
4. [法律图谱示例](#4-法律图谱示例)
5. [文档处理示例](#5-文档处理示例)

---

## 1. 快速开始

### 1.1 安装和初始化

```bash
# 安装 OpenKB（已有）
pip install -e ".[dev,web]"

# 创建一个法律知识库目录
mkdir legal-kb && cd legal-kb

# 初始化知识库（启用法律功能）
openkb init
```

在生成的配置文件中启用法律功能（将来会有开关，现在默认可以用API）。

### 1.2 添加第一批法律文档

```bash
# 添加法律文档（支持 PDF、Word、文本等）
openkb add "2024-最高人民法院工作报告.pdf"
openkb add "民法典-合同编.docx"
openkb add "案例-合同纠纷-2024-001号.pdf"
```

---

## 2. CLI 使用

法律知识库模块提供 Python API，可以在 Python 脚本中使用。

### 2.1 初始化法律知识图谱

```python
#!/usr/bin/env python3
from pathlib import Path
from openkb.legal import (
    LegalKnowledgeGraph,
    AuthorityLevel,
    RelationType,
    ConfidenceMetadata,
    KnowledgePageLifecycle,
    LifecycleManager,
)

# 初始化图谱
kb_dir = Path("./legal-kb")
graph = LegalKnowledgeGraph(kb_dir)

# 添加主要法律节点
civil_code = graph.add_node(
    label="民法典",
    node_type="statute",
    description="中华人民共和国民法典",
    authority_level=AuthorityLevel.STATUTE,
)

contract_577 = graph.add_node(
    label="民法典第577条",
    node_type="statute",
    description="违约责任条款：当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。",
    authority_level=AuthorityLevel.STATUTE,
)

# 添加关系：属于整体的一部分
graph.add_edge(
    source_id=contract_577.node_id,
    target_id=civil_code.node_id,
    relation_type=RelationType.PART_OF,  # PART_OF 我们添加到 schema
    weight=1.0,
    confidence=1.0,
)

print("✅ 图谱初始化完成！")
```

### 2.2 添加案例和建立引用

```python
#!/usr/bin/env python3
from pathlib import Path
from openkb.legal import (
    LegalKnowledgeGraph,
    AuthorityLevel,
    RelationType,
)

kb_dir = Path("./legal-kb")
graph = LegalKnowledgeGraph(kb_dir)

# 先找到民法典第577条
contract_577 = graph.find_node("民法典第577条", "statute")

# 添加案例节点
case_node = graph.add_node(
    label="张某诉李某房屋买卖合同纠纷案",
    node_type="case",
    description="2024年典型房屋买卖合同纠纷案例，涉及违约责任认定和损失赔偿计算。",
)

# 建立案例引用法条的关系
if contract_577:
    graph.add_edge(
        source_id=case_node.node_id,
        target_id=contract_577.node_id,
        relation_type=RelationType.CITES,
        weight=1.0,
        confidence=0.95,
    )
    print("✅ 案例引用关系建立！")

# 分析法条变更的影响
if contract_577:
    affected = graph.find_affecting_nodes(contract_577.node_id)
    print(f"\n📍 分析民法典第577条变更的影响:")
    for result in affected:
        print(f"  - {result.node.label} (深度: {result.depth})")
```

### 2.3 知识生命周期管理

```python
#!/usr/bin/env python3
from pathlib import Path
from openkb.legal import (
    LifecycleManager,
    DecayRate,
    get_decay_rate,
    get_half_life,
)

kb_dir = Path("./legal-kb")
lifecycle_manager = LifecycleManager(kb_dir)

# 为概念页面创建或获取生命周期
page_life = lifecycle_manager.get_lifecycle("concepts/违约责任")

# 设置初始置信度
page_life.confidence.confidence = 0.85
page_life.confidence.sources_count = 3
page_life.confidence.decay_rate = DecayRate.SLOW

# 确认知识（提高置信度）
page_life.confidence.confirm(confirmation_confidence=0.92)
print(f"✅ 置信度更新为: {page_life.confidence.confidence}")

# 添加新来源
page_life.confidence.add_source()
print(f"✅ 添加来源后: 置信度 {page_life.confidence.confidence}, 来源数 {page_life.confidence.sources_count}")

# 标记被取代（当新法出台时）
page_life.supersede.mark_superseded(
    superseded_by="concepts/违约责任-2024解释",
    reason="最高人民法院2024年新司法解释出台",
    triggered_by="statute_change",
)

print(f"✅ 状态更新: {page_life.supersede.status}")
print(f"✅ 取代者: {page_life.supersede.superseded_by}")

# 保存
lifecycle_manager.save_lifecycle(page_life)
```

### 2.4 DocIR 文档结构构建

```python
#!/usr/bin/env python3
from pathlib import Path
from openkb.legal import (
    DocIRBuilder,
    DocIRNodeType,
    DocIRDocument,
)

# 构建 DocIR 文档
builder = DocIRBuilder("判决书-2024-001号", "judicial-opinion")

builder.set_title("张某诉李某房屋买卖合同纠纷案")
builder.set_description("2024年典型房屋买卖合同纠纷案例")

# 添加结构节点
builder.add_node(
    DocIRNodeType.HEADER,
    "首部",
    page_number=1,
    heading="首部",
)

builder.add_node(
    DocIRNodeType.PARTY,
    "原告：张某，被告：李某",
    page_number=1,
    parent_id="...",
)

builder.add_node(
    DocIRNodeType.FINDING,
    "事实认定：法院查明2024年3月原被告签订合同...",
    page_number=2,
    heading="事实认定",
)

# 注册视觉节点（不预分析，按需调用）
signature_node_id = builder.add_visual_node(
    page_number=3,
    visual_type="signature",
    region={"x": 100, "y": 200, "width": 300, "height": 100},
    surrounding_text="原告签字：_________",
    caption="原告签字区域",
)

builder.add_visual_node(
    page_number=4,
    visual_type="table",
    surrounding_text="证据1：银行转账记录",
    caption="银行转账记录",
)

# 构建完成
doc = builder.build()

# 保存
docir_dir = kb_dir / ".openkb" / "docir"
docir_dir.mkdir(parents=True, exist_ok=True)
doc.save(docir_dir / "判决书-2024-001号.json")
print("✅ DocIR 文档保存完成！")
```

---

## 3. API 使用

### 3.1 启动法律知识库服务器

```bash
# 启动服务器（带有法律模块）
openkb-web
```

服务器将在 `http://127.0.0.1:7566` 启动。

### 3.2 法律图谱 API

```python
import requests

API_BASE = "http://127.0.0.1:7566/api/v1"

# 获取图谱统计
stats = requests.get(f"{API_BASE}/legal/graph/stats").json()
print(f"📊 图谱统计:")
print(f"  节点数: {stats['nodeCount']}")
print(f"  关系数: {stats['edgeCount']}")

# 获取案例引用的法条
case_node = requests.get(f"{API_BASE}/legal/graph/nodes",
                          params={"type": "case", "label": "张某诉李某"}).json()

related = requests.get(f"{API_BASE}/legal/graph/nodes/{case_node['id']}/related",
                       params={"relationType": "cites"}).json()
print(f"\n📚 此案引用 {len(related)} 个法条:")
for r in related:
    print(f"  - {r['node']['label']} (置信度: {r['edge']['confidence']})")

# 影响分析
impact = requests.get(f"{API_BASE}/legal/graph/nodes/{contract_577['id']}/impact").json()
print(f"\n📍 法条变更影响 {len(impact)} 个节点:")
for i in impact:
    print(f"  - {i['node']['label']}")
```

### 3.3 生命周期 API

```python
# 获取页面生命周期
life = requests.get(f"{API_BASE}/legal/lifecycle/concepts/违约责任").json()

print(f"📜 知识生命周期:")
print(f"  置信度: {life['confidence']['confidence']}")
print(f"  来源数: {life['confidence']['sourcesCount']}")
print(f"  状态: {life['supersede']['status']}")

# 确认知识
requests.patch(f"{API_BASE}/legal/lifecycle/concepts/违约责任/confirm",
               json={"newConfidence": 0.95})
print("✅ 知识已确认！")

# 标记被取代
requests.post(f"{API_BASE}/legal/lifecycle/concepts/违约责任/supersede",
              json={"supersededBy": "concepts/违约责任-2024",
                    "reason": "2024新司法解释"})
```

### 3.4 视觉工具 API

```python
# 注册视觉节点
register_payload = {
    "docName": "合同001",
    "pageNumber": 3,
    "visualType": "signature",
    "region": {"x": 100, "y": 200, "width": 300, "height": 100},
    "surroundingText": "原告签字：_________",
}
resp = requests.post(f"{API_BASE}/legal/visual/register", json=register_payload)
node_id = resp.json()["nodeId"]

# 按需分析（只有查询时才调用视觉模型）
analyze_payload = {"toolName": "signature-verification"}
analysis = requests.post(f"{API_BASE}/legal/visual/{node_id}/analyze",
                         json=analyze_payload).json()

print(f"🖼️ 视觉分析结果:")
print(f"  结果: {analysis['result']}")
print(f"  置信度: {analysis['confidence']}")
```

---

## 4. 法律图谱示例

### 4.1 完整法律知识图谱构建

```python
#!/usr/bin/env python3
"""示例：构建完整的法律知识图谱"""

from pathlib import Path
from openkb.legal import (
    LegalKnowledgeGraph,
    DocIRBuilder,
    DocIRNodeType,
    AuthorityLevel,
    RelationType,
    LifecycleManager,
    DecayRate,
)

def build_legal_kb():
    kb_dir = Path("./legal-kb-demo")
    kb_dir.mkdir(exist_ok=True)

    # 1. 初始化图谱
    graph = LegalKnowledgeGraph(kb_dir)

    # 2. 添加基本法律
    print("📄 正在添加法律节点...")

    constitution = graph.add_node(
        label="宪法",
        node_type="statute",
        description="中华人民共和国宪法",
        authority_level=AuthorityLevel.CONSTITUTION,
    )

    civil_code = graph.add_node(
        label="民法典",
        node_type="statute",
        description="中华人民共和国民法典（2021年施行）",
        authority_level=AuthorityLevel.STATUTE,
    )

    contract_part = graph.add_node(
        label="民法典合同编",
        node_type="statute",
        description="民法典第三编 - 合同",
        authority_level=AuthorityLevel.STATUTE,
    )

    article_577 = graph.add_node(
        label="民法典第577条",
        node_type="statute",
        description="违约责任条款",
        authority_level=AuthorityLevel.STATUTE,
    )

    interpretation_2024 = graph.add_node(
        label="最高人民法院关于合同编的司法解释（2024）",
        node_type="regulation",
        description="2024年最新司法解释",
        authority_level=AuthorityLevel.JUDICIAL_INTERPRETATION,
    )

    # 建立层级关系
    graph.add_edge(civil_code.node_id, constitution.node_id, RelationType.BASED_ON, 1.0, 1.0)
    graph.add_edge(contract_part.node_id, civil_code.node_id, RelationType.PART_OF, 1.0, 1.0)
    graph.add_edge(article_577.node_id, contract_part.node_id, RelationType.PART_OF, 1.0, 1.0)
    graph.add_edge(interpretation_2024.node_id, article_577.node_id, RelationType.INTERPRETS, 1.0, 0.95)

    # 3. 添加指导性案例
    print("📂 正在添加指导性案例...")

    guiding_case_1 = graph.add_node(
        label="指导案例1号：房屋买卖合同纠纷",
        node_type="precedent",
        description="房屋买卖合同纠纷中违约责任的认定标准",
        authority_level=AuthorityLevel.GUIDING_CASE,
    )

    guiding_case_2 = graph.add_node(
        label="指导案例2号：民间借贷纠纷",
        node_type="precedent",
        description="民间借贷利率司法保护上限的认定",
        authority_level=AuthorityLevel.GUIDING_CASE,
    )

    graph.add_edge(guiding_case_1.node_id, article_577.node_id, RelationType.CITES, 1.0, 0.98)
    graph.add_edge(guiding_case_1.node_id, interpretation_2024.node_id, RelationType.CITES, 1.0, 0.90)

    # 4. 添加具体案例
    print("📋 正在添加具体案例...")

    cases = [
        ("张某诉李某房屋买卖合同纠纷案", "2024-001号", "2024年", [article_577]),
        ("王某诉赵某民间借贷纠纷案", "2024-002号", "2024年", [guiding_case_2]),
        ("刘某诉陈某租赁合同纠纷案", "2024-003号", "2024年", [article_577, interpretation_2024]),
    ]

    for case_name, case_no, year, cites_list in cases:
        case_node = graph.add_node(
            label=case_name,
            node_type="case",
            description=f"{year}年度典型案例",
        )
        for cited in cites_list:
            graph.add_edge(
                case_node.node_id,
                cited.node_id,
                RelationType.CITES,
                1.0,
                0.9,
            )

    # 5. 为知识设置生命周期
    print("⏰ 正在设置知识生命周期...")
    lifecycle_manager = LifecycleManager(kb_dir)

    # 为核心法条设置高置信度、慢衰减
    life_577 = lifecycle_manager.get_lifecycle("statutes/民法典第577条")
    life_577.confidence.confidence = 0.95
    life_577.confidence.sources_count = 10
    life_577.confidence.decay_rate = DecayRate.SLOW
    lifecycle_manager.save_lifecycle(life_577)

    # 为司法解释设置中等置信度
    life_interp = lifecycle_manager.get_lifecycle("regulations/2024合同编解释")
    life_interp.confidence.confidence = 0.90
    life_interp.confidence.sources_count = 5
    life_interp.confidence.decay_rate = DecayRate.MEDIUM
    lifecycle_manager.save_lifecycle(life_interp)

    # 6. 测试影响分析
    print("🔍 正在分析影响...")
    affected = graph.find_affecting_nodes(article_577.node_id)
    print(f"  民法典第577条变更影响 {len(affected)} 个节点：")
    for result in affected:
        print(f"    - {result.node.label} (深度: {result.depth})")

    # 7. 输出统计
    stats = graph.stats()
    print(f"\n✅ 图谱构建完成！统计:")
    print(f"  节点数: {stats['node_count']}")
    print(f"  关系数: {stats['edge_count']}")
    for node_type, count in stats['node_types'].items():
        print(f"  - {node_type}: {count}")

    print(f"\n🎉 完成！知识库已准备好！")

if __name__ == "__main__":
    build_legal_kb()
```

运行这个脚本：
```bash
python build_legal_kb_demo.py
```

---

## 5. 文档处理示例

### 5.1 批量导入案例文件夹

```python
#!/usr/bin/env python3
"""示例：批量导入案例文件夹"""

from pathlib import Path
from openkb.sync import (
    SyncEngine,
    SyncSourceType,
    BatchImporter,
)

kb_dir = Path("./legal-kb")

# 1. 初始化导入器
importer = BatchImporter(kb_dir)

# 2. 扫描案例文件夹
cases_dir = Path("/path/to/my/cases")
plan = importer.scan_directory(cases_dir, recursive=True)

print(f"📁 扫描发现 {plan.total_files} 个文件")
print(f"  计划导入: {plan.to_import}")
print(f"  计划跳过: {plan.to_skip}")
print(f"  预计总页数: {plan.estimated_total_pages}")

# 3. 显示文件类型统计
type_counts = importer.get_file_type_summary(plan)
print(f"\n📊 文件类型分布:")
for ftype, count in type_counts.items():
    print(f"  - {ftype}: {count}")

# 4. 估计导入时间
time_estimate = importer.estimate_import_time(plan)
print(f"\n⏰ 导入时间估计: {time_estimate['human_readable']}")

# 5. 过滤并导入（这里只是示例，真实需集成 OpenKB 的 add）
filtered_plan = importer.filter_plan(
    plan,
    exclude_patterns=["backup", "temp"],
    min_size=100,
)

print(f"\n🎯 过滤后:")
print(f"  将导入: {filtered_plan.to_import}")
print(f"  将跳过: {filtered_plan.to_skip}")
```

### 5.2 同步源管理

```python
#!/usr/bin/env python3
"""示例：同步源管理"""

from pathlib import Path
from openkb.sync import (
    SyncEngine,
    SyncSourceType,
)

kb_dir = Path("./legal-kb")
sync_engine = SyncEngine(kb_dir)

# 1. 注册本地文件夹同步源
source = sync_engine.register_source(
    source_id="my-cases-folder",
    source_type=SyncSourceType.LOCAL_DIR,
    path="/path/to/my/cases",
    name="我的案例文件夹",
    auto_sync=False,
    auto_tag_new="待分类",
    default_authority_level="case",
)

print(f"✅ 同步源注册成功: {source.name}")

# 2. 扫描源（查看差异）
source, entries, diff = sync_engine.scan_source("my-cases-folder")

print(f"📋 差异分析:")
print(f"  新增: {len(diff.new_files)}")
print(f"  修改: {len(diff.modified_files)}")
print(f"  删除: {len(diff.deleted_files)}")

if diff.new_files:
    print("\n📄 新增文件:")
    for f in diff.new_files:
        print(f"  - {f}")

# 3. 更新清单（标记已同步）
sync_engine.update_source_manifest("my-cases-folder", entries, diff)
print("\n✅ 清单已更新！")

# 4. 列出所有同步源
all_sources = sync_engine.list_sources()
print(f"\n🔗 同步源列表 ({len(all_sources)} 个):")
for s in all_sources:
    status = "✅ 已启用" if s.enabled else "❌ 已禁用"
    print(f"  - {s.name} [{s.source_type.value}] {status}")
```

---

## 6. 查询示例

### 6.1 从法律知识图谱回答问题

```python
#!/usr/bin/env python3
"""示例：利用法律知识图谱回答问题"""

from pathlib import Path
from openkb.legal import LegalKnowledgeGraph, RelationType

kb_dir = Path("./legal-kb")
graph = LegalKnowledgeGraph(kb_dir)

def answer_question(question):
    """模拟回答法律问题"""

    if "违约责任" in question:
        # 找到相关法条
        article = graph.find_node("民法典第577条", "statute")
        if article:
            # 找到相关案例
            related_cases = []
            out_edges = graph.get_outgoing_edges(article.node_id)
            for edge in out_edges:
                if edge.relation_type in [RelationType.CITED_BY]:
                    node = graph.get_node(edge.source_id)
                    if node:
                        related_cases.append(node)

            # 找到司法解释
            related_interps = []
            in_edges = graph.get_incoming_edges(article.node_id)
            for edge in in_edges:
                if edge.relation_type == RelationType.INTERPRETS:
                    node = graph.get_node(edge.source_id)
                    if node:
                        related_interps.append(node)

            # 组成答案
            answer = f"""
# 关于违约责任的回答

## 法条依据

《民法典》第577条规定：当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。

## 相关司法解释

"""
            for interp in related_interps:
                answer += f"- {interp.label}\n"

            answer += f"""

## 相关案例

"""
            for case in related_cases[:3]:
                answer += f"- {case.label}\n"

            answer += f"""

## 置信度

此项知识置信度较高，建议结合具体案情进行分析。
"""
            return answer

    return "抱歉，暂未找到相关知识。"

# 测试问答
print("💬 问题：合同违约如何处理？")
print(answer_question("合同违约如何处理？"))
```

---

## 7. 完整工作流示例

```bash
#!/bin/bash
# 完整的法律知识工程师工作流程示例

echo "===== 法宝法律智能知识库 - 完整工作流程 =====
"

# 1. 初始化知识库
echo "[1/7] 初始化知识库..."
mkdir -p legal-workflow
cd legal-workflow
openkb init --language zh

# 2. 批量导入案例
echo "[2/7] 批量导入案例..."
python <<'EOF'
from pathlib import Path
from openkb.sync import BatchImporter

kb_dir = Path(".")
importer = BatchImporter(kb_dir)
cases_dir = Path("./cases")  # 你的案例文件夹

if cases_dir.exists():
    plan = importer.scan_directory(cases_dir)
    print(f"发现 {plan.total_files} 个文件")
    # 这里使用 openkb add 实际导入...
EOF

# 3. 启动服务器
echo "[3/7] 启动服务器..."
openkb-web &
PID=$!

# 4. 等待服务启动
echo "[4/7] 等待服务启动..."
sleep 5

# 5. 构建法律图谱
echo "[5/7] 构建法律知识图谱..."
python <<'EOF'
from openkb.legal import LegalKnowledgeGraph, AuthorityLevel, RelationType

graph = LegalKnowledgeGraph(".")
# 添加法律节点和关系...
EOF

# 6. 查询知识库
echo "[6/7] 查询知识库..."
openkb query "合同违约的处理方式有哪些？"

# 7. 导出成果
echo "[7/7] 导出成果..."
echo "知识库已准备完成，可以在 UI 中查看"

# 清理
kill $PID 2>/dev/null
```

---

## 下一步

现在你已经了解了基本用法，可以：

1. 阅读 **UI_INTEGRATION_PLAN.md** 了解界面设计
2. 运行 **test_legal_kb.py** 测试核心功能
3. 查看 **IMPLEMENTATION_SUMMARY.md** 了解整体设计

---

## 📚 相关文档

- [技术方案](./法宝法律智能知识库技术方案.md) - 原始技术方案
- [实施总结](./IMPLEMENTATION_SUMMARY.md) - 实施总结
- [界面集成方案](./UI_INTEGRATION_PLAN.md) - 界面详细设计

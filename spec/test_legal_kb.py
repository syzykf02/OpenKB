#!/usr/bin/env python
"""法宝法律智能知识库 - 基本功能测试"""

import sys
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

# 测试导入
print("=" * 60)
print("1. 测试模块导入")
print("=" * 60)

from openkb.legal import (
    LEGAL_ENTITY_TYPES,
    LEGAL_DOC_TYPES,
    DocumentStatus,
    RelationType,
    DecayRate,
    AuthorityLevel,
    get_decay_rate,
    get_half_life,
    ConfidenceMetadata,
    SupersedenceMetadata,
    DocIRDocument,
    LegalDocIRBuilder,
    KIND_SECTION,
    KIND_ARTICLE,
    KIND_PARAGRAPH,
    KIND_EVIDENCE,
    law_uri,
    get_effective_status,
    STATUS_SUPERSEDED,
    GraphNode,
    GraphEdge,
    LegalKnowledgeGraph,
)

print("✓ 所有模块导入成功")
print(f"  - 法律实体类型: {len(LEGAL_ENTITY_TYPES)} 种")
print(f"  - 法律文档类型: {len(LEGAL_DOC_TYPES)} 种")
print(f"  - 关系类型: {len([r for r in RelationType])} 种")
print()

# 测试临时目录
print("=" * 60)
print("2. 创建测试知识库目录")
print("=" * 60)

kb_dir = Path(tempfile.mkdtemp(prefix="openkb_legal_test_"))
print(f"✓ 测试目录: {kb_dir}")
print()

try:
    # 测试 Schema
    print("=" * 60)
    print("3. 测试 Schema 功能")
    print("=" * 60)

    # 测试 DocumentStatus
    print(f"✓ DocumentStatus.ACTIVE = {DocumentStatus.ACTIVE}")
    print(f"✓ DocumentStatus.SUPERSEDED = {DocumentStatus.SUPERSEDED}")
    print(f"✓ is_active_status(ACTIVE) = {DocumentStatus.is_active_status(DocumentStatus.ACTIVE)}")
    print(f"✓ is_active_status(SUPERSEDED) = {DocumentStatus.is_active_status(DocumentStatus.SUPERSEDED)}")

    # 测试 DecayRate
    print(f"✓ DecayRate.SLOW 速率: {get_decay_rate(DecayRate.SLOW)}")
    print(f"✓ DecayRate.SLOW 半衰期: {get_half_life(DecayRate.SLOW)} 个月")
    print(f"✓ DecayRate.FAST 速率: {get_decay_rate(DecayRate.FAST)}")
    print(f"✓ DecayRate.FAST 半衰期: {get_half_life(DecayRate.FAST)} 个月")

    # 测试 AuthorityLevel
    print(f"✓ AuthorityLevel.CONSTITUTION = {AuthorityLevel.CONSTITUTION}")
    print(f"✓ AuthorityLevel.STATUTE = {AuthorityLevel.STATUTE}")
    print(f"✓ AuthorityLevel.STATUTE.level = {AuthorityLevel.STATUTE.level}")
    print(f"✓ STATUTE > REGULATION? {AuthorityLevel.STATUTE > AuthorityLevel.REGULATION}")
    print()

    # 测试 ConfidenceMetadata
    print("=" * 60)
    print("4. 测试置信度元数据")
    print("=" * 60)

    conf = ConfidenceMetadata(confidence=0.85, sources_count=3, decay_rate=DecayRate.SLOW)
    print(f"✓ 初始置信度: {conf.confidence}")
    print(f"✓ 来源数: {conf.sources_count}")

    # 确认知识
    conf.confirm(confirmation_confidence=0.95)
    print(f"✓ 确认后置信度: {conf.confidence}")

    # 添加来源
    conf.add_source()
    print(f"✓ 添加来源后置信度: {conf.confidence}")
    print(f"✓ 来源数: {conf.sources_count}")

    # 添加矛盾
    conf.add_contradiction("contradiction_001")
    print(f"✓ 添加矛盾后置信度: {conf.confidence}")
    print(f"✓ 矛盾列表: {conf.contradicted_by}")

    # 解决矛盾
    conf.resolve_contradiction("contradiction_001")
    print(f"✓ 解决矛盾后置信度: {conf.confidence}")
    print()

    # 测试 SupersedenceMetadata
    print("=" * 60)
    print("5. 测试取代元数据")
    print("=" * 60)

    supersede = SupersedenceMetadata(status=DocumentStatus.ACTIVE)
    print(f"✓ 初始状态: {supersede.status}")
    print(f"✓ is_active(): {supersede.is_active()}")

    # 标记为被取代
    supersede.mark_superseded(
        superseded_by="concepts/new_provision",
        reason="2024年新司法解释出台",
        triggered_by="statute_change"
    )
    print(f"✓ 取代后状态: {supersede.status}")
    print(f"✓ 取代者: {supersede.superseded_by}")
    print(f"✓ 取代时间: {supersede.superseded_at}")
    print(f"✓ 取代原因: {supersede.supersede_reason}")
    print(f"✓ is_superseded(): {supersede.is_superseded()}")
    print()

    # 测试 DocIR
    print("=" * 60)
    print("6. 测试 DocIR 文档中间表示")
    print("=" * 60)

    builder = LegalDocIRBuilder("test_contract", input_type="md")
    builder.set_title("测试合同")
    builder.set_effective_status(STATUS_SUPERSEDED)

    # 添加根 + 章节 + 条文
    root_id = builder.add_node(kind=KIND_SECTION, title="测试合同", text="")
    sec_id = builder.add_node(
        kind=KIND_SECTION, title="第一条 合同主体", text="",
        page=1, parent_id=root_id,
    )
    art_id = builder.add_article(
        title="甲方信息", text="甲方：张三，身份证号：XXX",
        statute="测试合同", article_path="第一条", page=1, parent_id=sec_id,
    )

    # 添加视觉节点（注册，不预分析）
    visual_id = builder.add_visual_node(
        page=2, visual_type="signature", text_anchor="甲方签字处：",
        render_ref="render://test_contract/p2_300dpi.png",
    )

    # 构建文档
    docir = builder.build()
    print(f"✓ DocIR 文档ID: {docir.doc_id}")
    node_table = docir.node_table()
    print(f"✓ DocIR 节点数: {len(node_table)}")
    print(f"✓ DocIR 视觉节点数: {len(docir.vision_nodes)}")
    print(f"✓ 根节点存在: {docir.root is not None}")

    # 验证法律 anchor + effective_status
    art_node = docir.get_node(art_id)
    legal_anchor_uri = law_uri("测试合同", "第一条")
    assert art_node.anchors.legal == legal_anchor_uri, art_node.anchors.legal
    assert docir.resolve_uri(legal_anchor_uri) is not None
    assert get_effective_status(docir) == STATUS_SUPERSEDED
    print(f"✓ 法律 anchor: {legal_anchor_uri}")
    print(f"✓ effective_status: {get_effective_status(docir)}")

    # 测试保存和加载
    docir_dir = kb_dir / ".openkb" / "docir"
    docir_dir.mkdir(parents=True, exist_ok=True)
    docir_path = docir_dir / "test_contract.json"
    docir.save(docir_path)
    print(f"✓ DocIR 已保存到: {docir_path}")

    loaded_docir = DocIRDocument.load(docir_path)
    print(f"✓ DocIR 重新加载成功，节点数: {len(loaded_docir.node_table())}")
    assert get_effective_status(loaded_docir) == STATUS_SUPERSEDED
    print()

    # 测试知识图谱
    print("=" * 60)
    print("7. 测试知识图谱")
    print("=" * 60)

    graph = LegalKnowledgeGraph(kb_dir)

    # 添加法条
    civil_code = graph.add_node(
        label="民法典",
        node_type="statute",
        description="中华人民共和国民法典",
        authority_level=AuthorityLevel.STATUTE
    )
    print(f"✓ 添加节点: {civil_code.label} (ID: {civil_code.node_id})")

    article_577 = graph.add_node(
        label="民法典第577条",
        node_type="statute",
        description="违约责任",
        authority_level=AuthorityLevel.STATUTE
    )
    print(f"✓ 添加节点: {article_577.label} (ID: {article_577.node_id})")

    # 添加案例
    case1 = graph.add_node(
        label="张某诉李某合同纠纷案",
        node_type="case",
        description="合同违约案例"
    )
    print(f"✓ 添加节点: {case1.label} (ID: {case1.node_id})")

    # 添加关系：案例引用法条
    edge1 = graph.add_edge(
        source_id=case1.node_id,
        target_id=article_577.node_id,
        relation_type=RelationType.CITES,
        weight=1.0,
        confidence=0.95
    )
    print(f"✓ 添加边: {edge1.relation_type}")

    # 测试查询
    cited = graph.find_related(case1.node_id, RelationType.CITES)
    print(f"✓ 案例引用的法条: {len(cited)} 个")
    for node, edge in cited:
        print(f"  - {node.label} (置信度: {edge.confidence})")

    # 测试受影响节点查询
    affected = graph.find_affecting_nodes(article_577.node_id)
    print(f"✓ 法条变更影响的节点: {len(affected)} 个")
    for result in affected:
        print(f"  - {result.node.label} (深度: {result.depth})")

    # 测试图谱统计
    stats = graph.stats()
    print(f"✓ 图谱统计: {stats['node_count']} 个节点, {stats['edge_count']} 条边")
    print()

    # 测试视觉注册表
    print("=" * 60)
    print("8. 测试视觉注册表")
    print("=" * 60)

    from openkb.visual import VisualRegistry
    visual = VisualRegistry(kb_dir)

    # 注册视觉节点
    sig_node = visual.register_visual_node(
        doc_name="test_doc",
        page_number=3,
        visual_type="signature",
        region={"x": 100, "y": 200, "width": 300, "height": 100},
        surrounding_text="甲方签字处："
    )
    print(f"✓ 注册视觉节点: {sig_node.node_id}")

    # 查询文档的视觉节点
    doc_nodes = visual.get_nodes_for_doc("test_doc")
    print(f"✓ 文档的视觉节点数: {len(doc_nodes)}")

    # 测试统计
    visual_stats = visual.stats()
    print(f"✓ 视觉注册表统计: {visual_stats['total_nodes']} 个节点")
    print()

    # 测试同步引擎
    print("=" * 60)
    print("9. 测试同步引擎（无实际文件）")
    print("=" * 60)

    from openkb.sync import SyncEngine, SyncSourceType
    sync = SyncEngine(kb_dir)

    # 测试创建源（不实际扫描）
    source = sync.register_source(
        source_id="test_source",
        source_type=SyncSourceType.LOCAL_DIR,
        path=str(kb_dir),
        name="测试目录"
    )
    print(f"✓ 注册同步源: {source.source_id}")

    # 测试列出源
    sources = sync.list_sources()
    print(f"✓ 同步源列表: {len(sources)} 个")

    # 测试统计
    sync_stats = sync.stats()
    print(f"✓ 同步引擎统计: {sync_stats['source_count']} 个源")
    print()

    # 测试序列化
    print("=" * 60)
    print("10. 测试序列化/反序列化")
    print("=" * 60)

    # ConfidenceMetadata
    conf_dict = conf.to_dict()
    conf2 = ConfidenceMetadata.from_dict(conf_dict)
    print(f"✓ ConfidenceMetadata 序列化/反序列化成功")

    # SupersedenceMetadata
    sup_dict = supersede.to_dict()
    sup2 = SupersedenceMetadata.from_dict(sup_dict)
    print(f"✓ SupersedenceMetadata 序列化/反序列化成功")
    print()

    print("=" * 60)
    print("✓ 所有测试通过！")
    print("=" * 60)

finally:
    # 清理
    shutil.rmtree(kb_dir, ignore_errors=True)
    print(f"\n✓ 清理测试目录: {kb_dir}")

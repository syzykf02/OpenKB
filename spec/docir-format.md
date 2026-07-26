# DocIR 格式规范

> 文档中间表示层(DocIR)— 把异构文档统一为可计算、可寻址、可溯源的结构化表示。
> 这是知识库的底座:所有 parser 的统一出口,引用校验与图谱锚定的物理基础。
>
> 本规范独立于领域;法律等垂直能力通过 `extensions` 与 `anchors` 扩展,不污染核心。

## 1. 设计目标

DocIR 必须同时满足:

1. **兼容长短文档** — 短文档(几页)与长文档(数百页)用同一套结构,仅深度不同。
2. **多格式解析** — txt / md / html / pdf / word / pptx / jpeg / png 等常见格式经各自 parser 后产出同一形状的 DocIR。
3. **追根溯源 + 结果验证** — 每个知识片段都能定位回原文(页 / 字符 / 区域),引用可校验存在性与一致性。
4. **通用优先,领域叠加** — 核心 schema 只含通用字段;法律等垂直领域通过 `extensions` 与 `anchors` 扩展,不污染核心。

## 2. 核心思想:递归节点树

一个 DocIR 文档 = 一棵节点树。**短文档是浅树**(根 → 若干段落),**长文档是深树**(根 → 编 / 章 / 节 → 条文 / 段落)。形状相同,深度不同。这取代了长短文档分别落成 `.md` 与 `.json` 的二元分裂。

每个节点携带四类信息:

- **身份** — 全局唯一 `id`(引用校验的查询键)
- **内容** — `title` / `text` / `children`
- **定位** — `loc`(页 / 字符偏移 / bbox)— 追根溯源的物理根
- **溯源** — `provenance`(抽取方式 / 置信度 / 是否已校验)

## 3. 文档顶层结构

```json
{
  "docir_version": "1.0",
  "doc_id": "minfade-7f3a9c2e",
  "doc_name": "minfade",
  "title": "中华人民共和国民法典",
  "source": {
    "origin_type": "file | url | connector | sync",
    "origin_uri": "raw/民法典.pdf",
    "content_hash": "sha256:abc123...",
    "ingested_at": "2026-07-26T10:30:00Z",
    "sync_source": null
  },
  "format": {
    "input_type": "pdf | txt | md | html | docx | pptx | jpeg | png | ...",
    "input_meta": { "page_count": 532, "lang": "zh" },
    "converter": "pageindex | pymupdf | markitdown | md-parser | ocr | figure-detect",
    "converter_version": "0.4.2"
  },
  "root": { "...": "node" },
  "page_map": {
    "47": { "node_ids": ["sec-3.2", "art-577"], "render_ref": "render://minfade/p47_300dpi.png" }
  },
  "vision_nodes": ["docir://minfade/vol3/p47-chart"],
  "extensions": {
    "legal": { "effective_status": "current" }
  }
}
```

字段说明:

- `page_map` — 页号到节点 ID 的反向索引,派生自各节点的 `loc.page`。给 Vision Tool 的"候选页定位"做 O(1) 查询(给页号 → 该页所有节点)。可由树重建,存储是为快速访问。
- `vision_nodes` — 所有视觉节点的 ID 列表,便于查询时快速判断文档是否含视觉内容、是否需要触发 Vision Tool。
- `extensions.legal` — 法律层扩展位。**当前只定义 `effective_status`(`current` / `superseded` / `repealed`)一个字段**,供引用校验的时效性闸使用;`superseded_by` / `repealed_by` / `revision_history` 等生命周期字段在 P1 阶段补齐。未启用法律层时为空对象。核心层不读这个字段。

## 4. 节点结构(Node)

```json
{
  "id": "docir://minfade/合同编/第577条",
  "kind": "article",
  "title": "第577条",
  "text": "当事人一方不履行合同义务或者履行义务不符合约定的,应当承担继续履行、采取补救措施或者赔偿损失等违约责任。",
  "children": [],
  "loc": {
    "page": 47,
    "char_start": 12340,
    "char_end": 12580,
    "bbox": [72.0, 410.5, 523.0, 460.2]
  },
  "provenance": {
    "extractor": "pageindex-tree | pymupdf-text | markitdown | md-parser | ocr | pdf-figure-detect | llm-summary",
    "confidence": 0.97,
    "verified": false
  },
  "anchors": {
    "default": "docir://minfade/合同编/第577条",
    "legal": "law://民法典/合同编/第577条"
  }
}
```

### 字段语义

| 字段 | 作用 | 备注 |
|---|---|---|
| `id` | 全局唯一稳定标识。引用校验"存在性闸"的查询键。 | 格式 `docir://<doc_name>/<层级路径>`。短文档无层级时退化为 `docir://<doc_name>/#p3`(段落序)。 |
| `kind` | 节点类型。 | 通用枚举:`document \| section \| paragraph \| list_item \| table \| figure_anchor \| page_marker`。法律层加 `article` 等别名,核心只当字符串透传。 |
| `title` | 节点标题(条文号、章节名等)。 | 可空。 |
| `text` | 节点正文。 | 视觉节点为空字符串。 |
| `children` | 子节点列表。 | 递归;空数组为叶节点。 |
| `loc` | 原始定位:页 / 字符偏移 / bbox。 | 追根溯源的物理根。短文档 `page` 可缺省(单页或无页概念)。 |
| `provenance.extractor` | 节点怎么来的。 | 确定性抽取(pymupdf-text / markitdown)置信 1.0;LLM 抽取(pageindex-tree)为模型自报。 |
| `provenance.confidence` | 抽取置信度 0–1。 | 确定性抽取 = 1.0。 |
| `provenance.verified` | 引用校验是否已复核。 | 默认 false;校验流水线通过后置 true。 |
| `anchors` | 类型化 URI。 | 通用层只有 `default`;法律层加 `legal: law://...` / `case://...`。校验按 scheme 路由。 |

## 5. 视觉节点(不预分析)

纯图像内容无 `text`,只登记位置与渲染指针,不生成描述:

```json
{
  "id": "docir://minfade/vol3/p47-chart",
  "kind": "figure_anchor",
  "title": "银行流水图(第3卷第47页)",
  "text": "",
  "children": [],
  "loc": { "page": 47, "bbox": [40, 120, 560, 380] },
  "provenance": { "extractor": "pdf-figure-detect", "confidence": 1.0, "verified": false },
  "anchors": { "default": "docir://minfade/vol3/p47-chart" },
  "vision": {
    "type": "chart | signature | photo | handwriting | table-complex",
    "text_anchor": "甲方于2023年5月累计转账87万元(见下图)",
    "render_ref": "render://minfade/p47_300dpi.png",
    "analyzed": false,
    "last_analysis": null
  }
}
```

`vision` 字段说明:

- `type` — 视觉内容类型,引导查询时是否触发视觉推理。
- `text_anchor` — 周边文本锚点。**这是 text-first 检索的关键**:用户问"第47页的营收图"→ 文本检索命中锚点文本 → 定位到该视觉节点 → 按需渲染分析。脱离上下文的图像描述昂贵且有损,约 90% 的视觉内容永不被查询,故不在摄取时分析。
- `render_ref` — 高保真渲染指针。Vision Tool 触发时按此渲染候选页为高 DPI 图像。
- `analyzed` / `last_analysis` — 是否已触发过 Vision Tool。结论写回 `last_analysis`,随 Evidence Pack 结晶化回流,下次同案查询直接复用。

## 6. 格式 → Parser 矩阵

所有 parser 产出同一 schema 的 DocIR,只是树的深度与节点 kind 不同:

| 输入 | Parser | DocIR 形态 |
|---|---|---|
| `.txt` | passthrough | 浅树:root → paragraphs(按空行切) |
| `.md` | md-heading-parser | 按 `#` 层级建树 |
| `.html` | dom-to-tree | DOM 标题结构 → 树 |
| `.pdf`(短) | pymupdf-text | 浅树:root → 段落,带 `loc.page` |
| `.pdf`(长) | pageindex | 深树:篇 / 章 / 节 + 条文,`extractor=pageindex-tree` |
| `.docx` | markitdown → md-parser | 树(标题层级) |
| `.pptx` | markitdown → md-parser | 树(slide 为 section) |
| `.jpeg` / `.png` | ocr + figure-detect | root → OCR 文本段落 + 一个 `figure_anchor`;无文字时仅 `figure_anchor` |

当前 `openkb/converter.py` 的 5 个分支(markitdown / pymupdf / md 直读 / PDF 长文档)收敛为"多个 emitter,1 个出口"。

## 7. 引用校验:三道闸

引用校验全部建在 DocIR 节点 ID 与 `loc` 上:

1. **存在性闸** — Evidence Pack 的 `source: docir://...` 必须能在 DocIR 节点表查到。查不到 = 幻觉,直接拒。
2. **时效性闸** — 查到节点后,读 `extensions.legal.effective_status`;若 `superseded` / `repealed`,降级输出并附历史链(P1 补齐后生效)。
3. **一致性闸** — 用 `loc` 取回原始文本片段,与 claim 做语义一致性复核(独立 prompt)。视觉证据额外查 `vision.last_analysis.confidence`,低于阈值标记"待人工看图"并附 `render_ref` 一键核验。

存在性闸与一致性闸**不依赖法律层**,任何领域通用。时效性闸是法律层注入的(通用领域可替换为"版本新鲜度")。

## 8. 通用层 vs 法律扩展

**通用核心(P0 必做)**:节点树 / id 寻址 / loc 定位 / provenance 溯源 / 视觉节点登记 / parser 矩阵 / 存在性 + 一致性校验。

**法律扩展(叠加,不污染核心)**:

- `kind` 增加 `article` / `judgment_main` / `evidence` 等 — 核心只当字符串透传。
- `anchors.legal` 注入 `law://` / `case://` URI scheme。
- `extensions.legal` 挂时效状态(当前仅 `effective_status`,P1 扩展取代 / 废止 / 修订历史链)。
- 实体与关系提取(法规 / 案例 / 案由)在 DocIR 之上做,产出写入 `graph/`,不进 DocIR 本体。

**硬约束**:抽掉 `extensions.legal` 与 `anchors.legal`,DocIR 仍是一个完整的通用知识库文档表示。

## 9. 落地与迁移

落盘格式:**单一 JSON**。`wiki/sources/<name>.docir.json` 是规范表示与唯一来源,不旁挂 .md 投影。

现有产物对应关系:

- `wiki/sources/<name>.md`(短文档)→ 浅 DocIR(root + paragraphs)。
- `wiki/sources/<name>.json`(逐页)→ DocIR 的 `page_map` + 叶节点 `loc.page`。
- `wiki/summaries/<name>.md`(PageIndex 树)→ DocIR `root` 子树,每个 PageIndex 节点成为 `kind: section` 节点。

落地步骤:

1. 新增 `openkb/docir.py` — DocIR 的 dataclass 与序列化器。
2. `converter.py` 出口从"直接写 .md / .json"改为"先建 DocIR,再写 `<name>.docir.json`"。
3. `indexer.py` 现有 `tree` 字典已接近 DocIR `root`,补 `id` / `loc` / `provenance` 即可。
4. 引用校验流水线改为查 DocIR 节点表(替换 / 包裹当前对 .md / .json 的直接读取)。

## 10. 版本与演进

- `docir_version` 字段锁定 schema 版本;不兼容变更升主版本号。
- 字段新增走 additive(老 reader 忽略未知字段)。
- `extensions.legal` 字段集当前最小化,按 P1 生命周期需求增量补齐 — 不预先定义未使用字段。

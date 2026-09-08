# Scholar Studio v2 架构思路总结：LaTeXML 替代解析器 → XML 语料 → 存储/检索/推荐 → MCP

状态：讨论整理稿（基于本次会话的决策与实验结果）

## 0. 已定决策

| 项 | 决定 |
|---|---|
| 解析器 | 用 LaTeXML 替代自研正则解析器；`output/parsed/*.json` 退役 |
| 权威数据 | LaTeXML XML（`data/papers/<ULID>/latexml/paper.xml`），只由 LaTeXML 生成，不手工修改 |
| 传输格式 | XML 只做语料库；MCP/API 对外一律返回 JSON（按需从数据库组装，不是 XML 的镜像） |
| 重构方式 | 在 academic-based-qoder 内破坏式重构 |
| MCP 工具 | 16 个 model-facing 工具名保持不变，只换内部实现（不动 Proxy Hub 策略和 DSH system prompt） |
| Lean4 | 保留；公式需要 Content MathML，不只是原始 TeX |
| Embedding | 智谱 API |

## 1. 总体链路

```
TeX source (arXiv 源码包)
  → [ingest] LaTeXML → paper.xml + paper.log + quality.json      （权威语料层）
  → [store]  从 XML 抽取 → 关系库 / 向量库 / 引用图                （派生投影层，可删库重建）
  → [serve]  检索 / 推荐算法 → JSON
  → [api]    16 个 MCP 工具（名字不变）→ DSH 学者模式
```

核心原则：XML 之下的一切都是投影。想换切分策略、换 embedding 模型、加新字段（定理、算法块、图表），只重建投影，不重跑 LaTeXML。

## 2. 第一阶段：ingest（TeX → XML）

### 2.1 为什么是 LaTeXML

- 真正把 TeX 当程序执行（宏展开、`\input`、BibTeX、数学解析），章节层级、公式、引用键、参考文献是确定的，不是正则猜测。
- 输出天然带稳定 `xml:id`：`S3`（第 3 章）、`S3.SS2`（3.2 节）、`S3.p1`（第 3 章第 1 段）、`S3.E1`（第 3 章第 1 个公式）、`bib.bib13`（第 13 条参考文献）。这是全系统溯源锚点。
- 每个 `Math` 同时有：`tex`（原始 LaTeX）、`text`（语义线性化）、Presentation MathML、Content MathML。Lean4 那条线直接取 Content MathML。
- 所有问题都写进日志（`Error:undefined:`、`Warning:missing_file`、`Fatal:`），可以机器统计归因；旧解析器的错误是无声的。

### 2.2 为什么 XML 而不是 HTML / JSON

- HTML5 是由 XML 经 XSLT 再加工出的渲染层，语义退化成 class 名，只会更少不会更多；需要人看预览时再从 XML 按需生成。
- JSON 不自然地表达“段落中混排文本 + 公式 + 引用”的树；xml2json 一类工具只是同构转换，没有信息增益。

### 2.3 运行方式

- 引擎：`latexml/ar5ivist:2512.17` Docker 镜像（LaTeXML + ar5iv bindings + TeXLive）。
- 每篇一个容器：`--network=none --memory=4g --cpus=1`，非 root，源码只读挂载。
- 参数：`--format=xml --mathtex --pmml --cmml --nocomments --includestyles --timeout=600`，外加自定义 binding 目录 `--path=/custom-bindings`。
- 超时自动降级 `--noparse` 重跑一次并在 quality.json 记 `math_mode`。
- 单篇失败不阻塞整批。

### 2.4 产物目录

```
data/papers/<ULID>/
  source.tar.gz | source.zip
  paper.pdf
  latexml/paper.xml       # 权威 XML
  latexml/paper.log       # LaTeXML 完整日志
  latexml/quality.json    # 质量报告与分级
  meta.json               # arXiv 元数据（作者兜底等）
```

### 2.5 质量门禁（已根据 577 篇分布修正）

- `failed`：无合法 XML / 无标题 / 无章节 / 段落 < 10 / 只有 `\includepdf` 的包装源码 / 源码包读取失败。
- `degraded`：有 bibliography 但 XML 为 0（`bib_lost`）；bibitem < 源码 90%（`bib_coverage`）；源码有公式但 XML 数学 < 80%（`math_coverage`）；`Fatal`；`Error` > 50；标题被识别成 Supplementary；摘要丢失。
- `ok`：以上都不触发。

关键修正：
- 作者上标 `$^{1}$` 不算公式；
- Error 阈值从 10 改为 50（11–50 个格式类错误的论文结构完整，改为日志诊断而非降级）；
- 源码包内重复的 `.tex/.bbl` 副本按 SHA-256 去重，否则会虚高 bib/math 源计数。

### 2.6 当前全量结果（577 篇）

```
ok 405 / degraded 126 / failed 46
```

degraded 主因（可重叠）：math_coverage 67、bib_lost 33、errors>50 29、bib_coverage 17、fatal 14、title_supplement 3、abstract_lost 2。

修复策略：按原因分组、小批验证后定向重跑，不做无差别全量重跑（第二轮 233 篇无差别重跑收益极低，已停止）。

已验证有效的修复：
- 模板/宏包 binding：`usenix`、`usenixbadges`、`IET-Conf-Paper`、`floatrow`、`duckuments`、`mlsys`、`iclr`、`colm`、`mdframed`、`datetime`、`arydshln`、`xltabular`、`tabu`、`xr` 等；
- `--includestyles` 让 LaTeXML 处理源码目录中的原始 `.sty`；
- 典型效果：Alpa 从 `bib=0` → 62 并转 `ok`；GTBench 0 → 62；Linformer、Mixture-of-Experts 等转 `ok`。

不修的：源码包损坏（`extract:ReadError`）、`\includepdf` 包装器——保持 failed，用 PDF 兜底，不伪装成功。

### 2.7 命令

`scholar v2 ingest SOURCES --out RELEASE` 将每篇 TeX 转为 LaTeXML XML。
`SOURCES` 是按论文分目录的源码或压缩包，失败论文写入 `failed.csv`。
可重复使用 `--arxiv ID` 或 `--arxiv-list FILE` 下载 arXiv 源码。
默认使用 `latexml/ar5ivist:2512.17`，可用 `--image`、`--timeout` 调整。
产物包含 `manifest.csv`、`paper.xml`、`paper.log` 和 `quality.json`。
加入 `--import --release-id ID` 可在生成后导入 v2 数据库。

## 3. 第二阶段：store（XML → 数据库 / 向量库 / 知识图谱）

统一抽取器读取 `paper.xml`（Python 标准库 ElementTree/XPath 即可），产出三类投影。全部带 `paper_id + xml_id` 回指原文。

### 3.1 关系库（Postgres）

| 表 | 来源节点 | 关键字段 |
|---|---|---|
| papers | `document/title`, `creator`, `abstract`, meta.json | id, title, authors, abstract, year, arxiv_id, grade |
| sections | `section`/`subsection`/`appendix` | paper_id, xml_id, level, number, title, parent |
| paragraphs | `para` | paper_id, xml_id, section_id, order, text |
| formulas | `Math`/`equation` | paper_id, xml_id, tex, text, pmml, cmml, para_id |
| references | `bibitem` | paper_id, key, xml_id, raw, title, authors, year, doi/arxiv（消解后） |
| citations | `cite@bibrefs` | paper_id, para_id, ref_key |
| figures/tables/theorems | 对应节点 | caption/statement |
| quality | quality.json | grade, reasons, log 统计 |

### 3.2 向量库（pgvector，智谱 embedding）

- 主 chunk = `para`（天然带“第几节第几段”路径，段落级检索结果能精确回指）。过长段落再按句子切，过短相邻段合并，但保留 xml_id 列表。
- 附加索引：section 标题 + 首段（章节级召回）、abstract（论文级召回）、公式 `Math@text`/`tex`（公式检索）。
- chunk 元数据：paper_id、section 路径（如 `3 > 3.2 Attention`）、xml_id、grade。检索时可按章节类型（Related Work / Method / Experiments）过滤。

### 3.3 知识图谱

- 引用边：`cite` → `bibitem` → 引用实体消解（arXiv id / DOI / 标题模糊匹配到库内论文）→ `paper --cites--> paper`。
- 概念图：从 `title/abstract/section title/theorem` 抽取术语，`paper --mentions--> concept`，`concept --cooccurs--> concept`；保留现有内存图谱接口。
- 节点属性带来源 xml_id，图谱结论可回溯到原文段落。

### 3.4 重建原则

任一投影可以 `drop + rebuild`，输入只有 `paper.xml + quality.json + meta.json`。`ingest_version` 变化才重跑 LaTeXML。

## 4. 第三阶段：检索与推荐

### 4.1 检索

- 混合检索：BM25/全文（Postgres tsvector）+ 向量相似度，RRF 融合；`grade` 做置信度加权（degraded 结果标注）。
- 结构化过滤：按论文、章节类型、年份、公式 only。
- 段落回指：结果直接给 `paper_id / section 路径 / xml_id / 原文`，DSH 可以“只取某篇 Introduction 前两段”。
- 公式检索：`Math@text` 语义文本向量 + `tex` 字符串匹配，返回 tex + Content MathML（Lean4 输入）。

### 4.2 推荐

- 引用网络：共引/耦合、PageRank、桥接节点（复用 citation-network / paper-recommendation skill 语义）。
- 内容相似：abstract / section 向量近邻。
- 兴趣驱动：用户 interests 与阅读记录 → 向量 + 图谱混合打分；解释性字段说明“因为引用了 X / 与 Y 相似”。
- 冷启动：仅内容相似 + 高被引。

## 5. 第四阶段：MCP / API（16 个工具名不变）

| 工具 | 新实现数据源 |
|---|---|
| scholar_search / scholar_vec_search | 混合检索（关系库全文 + pgvector） |
| scholar_info | papers + sections 目录树 + quality |
| scholar_section / scholar_passages | paragraphs 按 xml_id/章节路径读取 |
| scholar_cite_network / scholar_lineage / scholar_graph_query / scholar_graph_stats | 引用图 + 概念图 |
| scholar_list_papers / scholar_arxiv_search | papers 表 / arXiv API |
| read_parsed_paper | 从数据库组装 JSON（替代旧 parsed json） |
| scholar_auto_notes / scholar_interests / read_skill / scholar_read_output_file | 逻辑不变，读数据源切换 |

返回一律 JSON，包含 `paper_id、xml_id、section_path、grade` 溯源字段；XML 不出网。

## 6. 实施顺序与当前进度

1. ingest：解析器已接入（`scholar/v2/latexml_ingest.py`），577 篇初跑完成，405 ok 已交付；degraded 定向修复进行中。
2. store：设计抽取器与三张核心表（papers/sections/paragraphs）→ formulas/references/citations → 向量与图。
3. serve：混合检索 → 推荐。
4. api：16 个工具逐个切换数据源，保留旧签名，加溯源字段。
5. PR：待仓库授予写权限后提交解析器与设计文档（本地提交已保留）。

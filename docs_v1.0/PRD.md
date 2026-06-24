# TyAgent V2.0 · 需求规格说明书 (PRD)

**文档版本：** V2.0 Draft  
**项目代号：** Hermes  
**项目定位：** 专业级 Agentic RAG 引擎 · 开发者优先 · API 极简接入  
**核心架构：** FastAPI · LangGraph · LiteLLM · PostgreSQL · Milvus · Neo4j · Celery · Redis · BM25 · Reranker  
**基于版本：** TyAgent V1.5（会话管理 · 知识库管理 · 文件入库全链路）  
**文档状态：** Draft · 待评审

---

## 目录

1. [产品概述](#1-产品概述)
2. [系统架构概览](#2-系统架构概览)
3. [功能需求详细拆分](#3-功能需求详细拆分)
   - 3.1 智能文档处理模块
   - 3.2 混合检索引擎模块
   - 3.3 答案溯源与幻觉抑制模块
   - 3.4 统一查询接口模块
   - 3.5 RAG 效果评估模块
   - 3.6 可观测性 Trace 模块
4. [API 接口总览](#4-api-接口总览)
5. [数据库字典设计](#5-数据库字典设计)
6. [核心业务规则与约束](#6-核心业务规则与约束)
7. [错误码与响应规范](#7-错误码与响应规范)
8. [需求优先级总览](#8-需求优先级总览)

---

## 1. 产品概述

### 1.1 V2.0 定位

TyAgent V1.5 完成了会话管理、知识库管理、文件上传入库的全链路基础设施建设，系统从"能跑通"升级为"可管理"。

V2.0 的目标是完成第二次跨越：**从"可管理"升级为"效果可信赖"**。

产品代号 **Hermes**，核心主张只有一句话：

> **让开发者三行代码接入 RAG，但底层检索质量远超市面同类产品。**

这不是一个给运营人员用的平台，也不是拖拽配置的工作流工具。Hermes 是一个**开发者优先的专业 RAG 引擎**，对外呈现为极简的 API，对内运行着业界最完整的混合检索 + 知识图谱增强 + 答案溯源链路。

### 1.2 核心问题与解法

RAG 效果差的根源几乎总是出在同一条链路的三个节点上：

```
文档切得不好  →  检索召回不准  →  模型拿着错误上下文生成幻觉
     ↑                ↑                      ↑
  模块一            模块二                  模块三
  智能切片      混合检索 + 重排序        溯源 + 幻觉抑制
```

V2.0 对这三个节点分别进行专项攻坚，同时以知识图谱作为整条链路的语义补全层，构成其他纯向量 RAG 产品无法复制的技术护城河。

### 1.3 V2.0 不做的事（边界）

- 不引入多租户 / RBAC 权限体系（推迟至 V2.5 或 V3.0）。
- 不开发任何前端界面，所有能力以 REST API 形式交付。
- 不做垂直行业的专项优化，保持通用性。
- 不引入私有化部署的 Reranker 训练能力，使用预训练模型或 API 服务。
- 不做多 Agent 协作编排，V2.0 维持单 Agent 架构。

### 1.4 与 V1.5 的差异对照

| 维度 | V1.5 现状 | V2.0 目标 |
|------|-----------|-----------|
| 文档切片 | RecursiveCharacterTextSplitter 按字符数机械切割 | 结构感知切片 + 双层索引（段落摘要 + 细粒度 Chunk） |
| 检索策略 | 纯向量检索（COSINE 相似度） | 向量 + BM25 混合检索 + RRF 融合 + Reranker 精排 |
| 知识图谱 | 独立 Graph Tool，Agent 自行决策是否调用 | 融入检索主链路：Query NER → 图谱锚定 → 向量过滤 |
| 答案溯源 | 无 | Citation 注入 + source_citations 结构化返回 |
| 幻觉控制 | System Prompt 约束 | 答案自检节点 + 置信度评分 + 低置信度预警 |
| 对外接口 | 多个独立接口，开发者需自行组装 | 统一 /v2/query 封装全链路，分层子接口支持深度定制 |
| 效果评估 | 无 | 内置 RAGAS 指标评估接口（召回率 / 忠实度 / 相关性） |
| 可观测性 | 无 Trace | 完整 agent_traces 表 + Trace 查询接口 |

---

## 2. 系统架构概览

### 2.1 V2.0 检索全链路

```
用户 Query
    │
    ▼
┌─────────────────────────────────────┐
│  Query 预处理层                      │
│  ├─ Query 改写（HyDE / 多角度扩展）  │
│  └─ NER 实体识别（从 Query 提取实体）│
└─────────────────┬───────────────────┘
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
┌──────────────┐   ┌────────────────────┐
│  Neo4j       │   │  并行检索层         │
│  多跳图谱查询 │   │  ├─ Milvus 向量检索 │
│  → 实体标签  │   │  └─ BM25 全文检索   │
└──────┬───────┘   └─────────┬──────────┘
       │                     │
       └──────────┬──────────┘
                  ▼
        ┌─────────────────┐
        │  RRF 融合重排序  │
        └────────┬────────┘
                 ▼
        ┌─────────────────┐
        │  Reranker 精排   │
        │  + 相关性过滤    │
        └────────┬────────┘
                 ▼
        ┌─────────────────────────────┐
        │  Context 组装               │
        │  ├─ Chunk 编号注入（[1][2]）│
        │  └─ 元数据 / 来源信息携带   │
        └────────┬────────────────────┘
                 ▼
        ┌─────────────────┐
        │  LLM 生成        │
        └────────┬────────┘
                 ▼
        ┌──────────────────────────────┐
        │  答案后处理层                 │
        │  ├─ Citation 解析            │
        │  ├─ 答案自检（Faithfulness） │
        │  └─ 置信度评分               │
        └────────┬─────────────────────┘
                 ▼
        结构化响应（answer + citations + confidence）
```

### 2.2 新增组件

| 组件 | 技术选型 | 职责说明 |
|------|----------|----------|
| BM25 检索层 | Milvus 2.5 稀疏向量 / Elasticsearch | 精确词匹配检索，与向量检索并行执行 |
| Reranker 服务 | bge-reranker-v2-m3 / LiteLLM Rerank API | 对融合后候选 Chunk 做精排，过滤低相关性结果 |
| Query 改写器 | LLM Prompt（HyDE / 多角度扩展） | 提升召回多样性，生成假设性答案辅助检索 |
| NER 识别器（Query 侧） | LLM Prompt / spaCy | 从用户 Query 中提取实体，用于图谱锚定 |
| 答案自检器 | LLM as Judge | 验证答案事实是否有 Chunk 文本支撑 |
| RAGAS 评估器 | RAGAS 框架 + LLM as Judge | 量化评估召回率、忠实度、答案相关性 |
| Trace 记录器 | PostgreSQL agent_traces 表 | 持久化每次 ReAct 步骤的完整链路数据 |

---

## 3. 功能需求详细拆分

### 3.1 智能文档处理模块（Intelligent Document Processing）

V1.5 的机械切片是 RAG 效果差的根源之一。本模块对文档处理管道进行全面重构，核心思路是**理解文档结构，按语义单元切割，建立双层索引**。

---

#### IDP-01 · 结构感知文档解析

**描述：** 文档解析时不再只提取纯文本，而是同时识别并保留文档的结构层级信息，为后续结构感知切片提供基础。

**各格式结构识别规则：**

| 格式 | 识别的结构元素 | 处理方式 |
|------|----------------|----------|
| PDF | 标题层级（字体大小/粗细推断）、正文段落、表格、图注 | PyMuPDF 提取文本块，按坐标和字体属性推断层级 |
| DOCX | Heading 1/2/3、正文段落、表格、列表 | python-docx 直接读取 paragraph.style 判断层级 |
| Markdown | # / ## / ### 标题、正文、代码块、表格 | 正则解析 ATX 标题，代码块单独标记 |
| TXT | 空行分段 | 连续空行视为段落边界，无层级结构 |

**输出数据结构（每个语义块）：**

```json
{
  "block_id": "uuid",
  "block_type": "paragraph | heading | table | code | list",
  "heading_path": ["第一章 合同主体", "1.1 甲方信息"],
  "content": "甲方：北京某科技有限公司...",
  "page_number": 3,
  "position_index": 12
}
```

**验收标准：** 同一文档解析后，`heading_path` 能正确反映文档层级结构；表格类型块的 `content` 为 Markdown 格式；代码块类型不参与 NER 实体抽取。

---

#### IDP-02 · 结构感知切片策略

**描述：** 基于 IDP-01 的结构化解析结果，按以下优先级进行切片，而不是简单按字符数截断：

**切片优先级规则（从高到低）：**

1. **代码块**：整块作为单个 Chunk，不拆分（最大 4096 Token，超出时按函数边界拆分）。
2. **表格**：整张表格作为单个 Chunk，同时生成一个"表格自然语言描述"作为额外 Chunk（见 IDP-03）。
3. **标题段落组**：一个标题 + 其下属正文段落合并为一个 Chunk，超出 `chunk_size` 时在段落边界切分，切分后每个子 Chunk 保留标题信息在 `metadata.heading_path` 中。
4. **普通段落**：同 V1.5 的 `RecursiveCharacterTextSplitter`，作为兜底策略。

**Chunk 元数据扩展（新增字段）：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `heading_path` | ARRAY(VARCHAR) | 该 Chunk 所属的标题层级路径 |
| `block_type` | VARCHAR | paragraph / table / code / list |
| `page_number` | Integer | 所在页码（PDF 场景） |
| `position_index` | Integer | 在文档中的顺序位置，用于相邻 Chunk 合并 |
| `parent_chunk_id` | VARCHAR | 双层索引中，指向对应的段落摘要 Chunk |
| `is_summary` | Boolean | 是否为段落摘要级 Chunk（双层索引使用） |

**验收标准：** 一份含标题 / 正文 / 表格 / 代码的 PDF 切片后，表格不被截断为两个 Chunk；标题信息出现在 `metadata.heading_path` 而非 `content` 中；代码块保持完整。

---

#### IDP-03 · 表格自然语言描述生成

**描述：** 表格内容对向量检索极不友好（列名 + 数据分离导致语义残缺）。对每张识别到的表格，调用 LLM 生成一段自然语言描述，作为额外 Chunk 同步入库，参与向量检索。

**Prompt 设计：**

```
将以下 Markdown 表格转化为一段自然语言描述，要求：
1. 描述表格的主题和结构
2. 提炼表格中的关键数据和规律
3. 不超过 200 字
4. 不要使用"该表格"等冗余开头

表格内容：
{table_markdown}
```

**生成的描述 Chunk 关联关系：**
- `parent_chunk_id` 指向原始表格 Chunk
- `block_type = "table_description"`
- 参与向量检索，但不参与 BM25 检索（避免描述词汇干扰精确匹配）

**验收标准：** 含 5 张表格的文档入库后，产生 5 个额外的 `table_description` Chunk；检索"第三季度销售额"时，能通过描述 Chunk 正确召回对应表格。

---

#### IDP-04 · 双层索引架构（Hierarchical Index）

**描述：** 对同一文档同时建立两个粒度的向量索引，以解决"粗粒度检索语义完整 vs 细粒度检索精度高"的矛盾：

**两层索引定义：**

```
粗粒度层（段落摘要）
  - 每 3~5 个相邻 Chunk 合并后，调用 LLM 生成摘要
  - 摘要向量化后写入 Milvus，is_summary=True
  - 用于初筛阶段，Top-K 取较大值（如 20）
  - 维度与细粒度层相同，存入同一 Collection

细粒度层（原始 Chunk）
  - IDP-02 产出的原始切片
  - is_summary=False
  - 用于精读阶段，通过 parent_chunk_id 从粗粒度结果定位
```

**检索时的使用策略（由混合检索模块 HRE-01 调用）：**

```
Step 1: 用粗粒度摘要向量做初筛，召回 Top-20 摘要 Chunk
Step 2: 通过 parent_chunk_id 找到每个摘要对应的原始细粒度 Chunk
Step 3: 将细粒度 Chunk 送入 Reranker 精排
Step 4: 取精排后 Top-K 送入 LLM 上下文
```

**验收标准：** 文档入库后，`is_summary=True` 的摘要 Chunk 数量约为原始 Chunk 总数的 20%~30%；检索结果中返回的是细粒度 Chunk，而非摘要 Chunk。

---

#### IDP-05 · 文档级元数据自动提取

**描述：** 文档入库时，调用 LLM 对全文（或前 3000 Token）进行元数据提取，结果存入 `kb_files` 表的 `doc_metadata` 字段，并同步写入该文档所有 Chunk 的 `metadata` 字段，用于后续检索的前置过滤。

**提取字段：**

| 字段 | 类型 | 示例 | 用途 |
|------|------|------|------|
| `doc_type` | string | `合同` / `报告` / `手册` / `法规` | 检索时按文档类型过滤 |
| `doc_date` | string | `2024-03` | 时间范围过滤 |
| `language` | string | `zh` / `en` | 多语言场景路由 |
| `key_topics` | array | `["违约金", "交付期限"]` | 辅助 BM25 关键词扩展 |
| `summary_brief` | string | 不超过 100 字的文档摘要 | 在文件列表接口中展示 |

**验收标准：** 上传一份合同文件，`doc_type` 自动识别为"合同"；`key_topics` 包含合同中出现频率最高的 3~5 个核心词；元数据存入 `kb_files.doc_metadata` 字段。

---

#### IDP-06 · 入库任务管道重构

**描述：** V2.0 的 Celery 入库任务在 V1.5 基础上扩展，新增 IDP 系列步骤，完整流程如下：

```
Step 1:  status=processing, progress=0
Step 2:  结构感知文档解析（IDP-01）              [progress=15]
Step 3:  结构感知切片（IDP-02）                   [progress=25]
Step 4:  表格描述生成（IDP-03，仅含表格时）        [progress=30]
Step 5:  段落摘要生成（IDP-04 粗粒度层）           [progress=40]
Step 6:  文档元数据提取（IDP-05）                  [progress=45]
Step 7:  批量向量嵌入（细粒度 + 粗粒度）           [progress=65]
Step 8:  写入 Milvus（双层 Chunk）                [progress=80]
Step 9:  NER 实体抽取 → 写入 Neo4j               [progress=92]
Step 10: 写入 BM25 索引                           [progress=97]
Step 11: status=completed, progress=100
```

**验收标准：** 入库任务完成后，`chunk_count` 包含细粒度和粗粒度 Chunk 的总数；`entity_count` 与 Neo4j 中新增节点数一致；BM25 索引可检索该文档内容。

---

### 3.2 混合检索引擎模块（Hybrid Retrieval Engine）

本模块是 V2.0 召回率提升的核心，将 V1.5 的纯向量检索升级为向量 + BM25 混合检索，并在图谱层引入 Query 侧 NER，形成完整的三路融合检索架构。

---

#### HRE-01 · Query 改写与扩展

**描述：** 用户的原始 Query 往往过于简短或语义模糊，直接用于检索效果有限。V2.0 在检索前增加 Query 改写步骤，提升召回多样性。

**两种改写策略（可通过 API 参数选择）：**

**策略 A：HyDE（Hypothetical Document Embeddings）**
- 用 LLM 根据用户 Query 生成一段假设性的理想答案（不是真实答案，而是"如果文档里有答案，它大概长什么样"）
- 用假设答案的向量替代 Query 向量进行检索
- 适合：问答场景，Query 是问句时效果最好

**策略 B：多角度子查询**
- 用 LLM 将原始 Query 改写为 2~3 个不同角度的子查询
- 每个子查询独立检索后合并结果
- 适合：分析型 Query，原始问题较为复杂时

**API 参数：**

```json
"options": {
  "query_rewrite": "hyde" | "multi_query" | "none"
}
```

**验收标准：** 开启 HyDE 时，对"合同什么时候到期"的检索结果，能正确召回含"合同期限""有效期至"等表述的 Chunk；关闭时直接用原始 Query 向量检索。

---

#### HRE-02 · Query 侧 NER 实体识别

**描述：** 在检索开始前，从用户 Query 中识别命名实体，用于图谱锚定查询。这是 Graph RAG 联合检索的入口。

**执行流程：**

```
用户 Query: "张三和北京科技有限公司之间的合同条款是什么？"
    ↓
NER 识别: [
  {"name": "张三",       "type": "Person"},
  {"name": "北京科技有限公司", "type": "Organization"}
]
    ↓
Neo4j 查询: MATCH (e:Entity {name: "张三", kb_id: $kb_id})-[r]-(related)
            RETURN related.name, type(r)
    ↓
图谱返回实体标签: ["张三", "北京科技有限公司", "采购合同_2024", "违约条款"]
    ↓
注入为 Milvus 标量过滤条件:
  entity_tags array_contains_any ["张三", "北京科技有限公司"]
```

**验收标准：** Query 中含有知识库中存在的实体时，Milvus 检索自动附加 `entity_tags` 过滤条件；Query 中无实体或实体在图谱中不存在时，跳过图谱查询，直接进行纯向量检索。

---

#### HRE-03 · BM25 全文检索集成

**描述：** 在 Milvus 向量检索之外，并行执行 BM25 全文检索，解决向量检索在精确匹配场景下的弱势。

**技术实现选项（按优先级）：**

1. **Milvus 2.5 稀疏向量**（推荐）：利用 Milvus 内置的稀疏向量支持，将 BM25 分数表示为稀疏向量，与稠密向量在同一 Collection 内并行检索，无需引入额外组件。
2. **独立 Elasticsearch**：若 Milvus 版本不满足，独立部署 Elasticsearch 作为 BM25 检索后端，通过 `document_id` 与 Milvus 结果关联。

**BM25 索引内容：** `content` 字段全文，不含 `table_description` 类型的 Chunk（避免描述词汇干扰精确匹配），支持中文分词（jieba / IK 分词器）。

**验收标准：** 检索"bge-reranker-v2"这种专有名词时，BM25 路径能正确召回包含该词的 Chunk，而向量路径可能因语义空间稀疏而遗漏；两路结果均在合理时延内返回（各 < 500ms）。

---

#### HRE-04 · RRF 融合重排序

**描述：** 将向量检索、BM25 检索、图谱增强检索的结果通过 RRF（Reciprocal Rank Fusion）算法融合，输出统一的排序列表。

**RRF 公式：**

```
RRF_score(d) = Σ 1 / (k + rank_i(d))

其中：
  d    = 候选 Chunk
  k    = 平滑常数，默认 60
  rank_i(d) = Chunk d 在第 i 路检索结果中的排名（不在结果中则不计入）
```

**融合策略：**
- 向量检索结果：Top-20
- BM25 检索结果：Top-20
- 图谱增强后向量检索结果：Top-10（权重更高，通过减小 k 值实现，默认 k=30）
- 融合后取 Top-30 送入 Reranker

**验收标准：** 同一 Query 同时在向量和 BM25 中排名靠前的 Chunk，融合后排名高于仅在一路中出现的 Chunk；图谱增强的 Chunk 在融合结果中排名优先。

---

#### HRE-05 · Reranker 精排

**描述：** RRF 融合后的 Top-30 候选 Chunk，经过 Reranker 模型精排，计算 Query 与每个 Chunk 的细粒度交叉相关性分数，过滤低相关性 Chunk，输出最终 Top-K 送入 LLM。

**Reranker 集成方式（可配置）：**

| 方式 | 适用场景 | 配置项 |
|------|----------|--------|
| LiteLLM Rerank API（在线） | 无 GPU 环境，快速接入 | `RERANKER_TYPE=api` |
| 本地 bge-reranker-v2-m3 | 有 GPU，低延迟要求 | `RERANKER_TYPE=local` |
| 跳过 Reranker | 开发调试，快速验证 | `RERANKER_TYPE=none` |

**相关性过滤阈值：** 默认过滤 Reranker 分数低于 0.3 的 Chunk（可通过 `similarity_threshold` 参数调整）。过滤后若剩余 Chunk 数少于 3，则保留分数最高的 3 个，不做截断。

**验收标准：** 开启 Reranker 后，与 Query 明显不相关的 Chunk（如检索"违约金"时召回的"交货地址"段落）被过滤掉；Reranker 调用时延 < 300ms（API 方式）或 < 100ms（本地方式）。

---

#### HRE-06 · 检索参数统一配置

**描述：** V2.0 的检索行为通过统一的配置结构控制，支持在知识库级别设置默认值，也支持在单次查询时覆盖。

**知识库级默认检索配置（存入 `knowledge_bases.retrieval_config` JSONB 字段）：**

```json
{
  "top_k": 5,
  "rerank_top_n": 30,
  "similarity_threshold": 0.3,
  "enable_graph_rag": true,
  "enable_bm25": true,
  "query_rewrite": "none",
  "use_hierarchical_index": true
}
```

**单次查询覆盖（通过 `/v2/query` 接口的 `options` 字段传入）：** 任何字段均可覆盖知识库默认值，仅对本次查询生效。

**验收标准：** 知识库 A 设置 `enable_graph_rag=false` 后，对 A 的查询不触发图谱路径；单次查询传入 `"enable_graph_rag": true` 时，覆盖知识库配置，本次查询启用图谱。

---

### 3.3 答案溯源与幻觉抑制模块（Citation & Hallucination Control）

本模块解决"答案从哪来"和"答案可不可信"两个核心问题，是 Hermes 面向企业用户建立信任感的关键能力。

---

#### CHC-01 · Citation 注入机制

**描述：** 在将检索到的 Chunk 送入 LLM 前，对每个 Chunk 注入编号标记，要求 LLM 在生成答案时引用对应编号，实现答案与原文的精确映射。

**Context 组装格式：**

```
[1] 来源：采购合同_2024.pdf（第3页）
内容：第三条 违约责任：若甲方未按期付款，须按合同总额的20%支付违约金...

[2] 来源：采购合同_2024.pdf（第5页）
内容：第七条 合同期限：本合同自签署之日起至2025年12月31日止...

[3] 来源：补充协议_2024.pdf（第1页）
内容：经双方协商，合同期限延长至2026年6月30日...
```

**System Prompt 引用规则注入：**

```
你是一个专业的文档问答助手。请严格基于以下上下文回答问题。
规则：
1. 只使用上下文中的信息，不要引入上下文之外的知识
2. 在答案中用 [数字] 标注信息来源，例如：合同期限至2026年[3]
3. 如果上下文中找不到问题的答案，明确说明"根据已有文档，无法找到相关信息"
4. 不要推断或猜测未在上下文中明确提及的事实
```

**验收标准：** LLM 生成的答案中包含 `[1]`、`[3]` 等引用标记；引用标记能正确对应到对应编号的 Chunk；若 LLM 未生成任何引用，在响应中标记 `citations_available: false`。

---

#### CHC-02 · Citation 解析与 source_citations 结构化输出

**描述：** 解析 LLM 生成文本中的引用标记，将其映射为结构化的 `source_citations` 数组，随答案一起返回。

**响应结构（完整示例）：**

```json
{
  "code": 0,
  "data": {
    "answer": "根据合同第三条[1]，违约金为合同总额的20%。合同期限已延长至2026年6月30日[3]。",
    "source_citations": [
      {
        "index": 1,
        "chunk_id": "chunk_abc123",
        "document_id": "file_xyz456",
        "document_name": "采购合同_2024.pdf",
        "page_number": 3,
        "heading_path": ["第三条 违约责任"],
        "snippet": "若甲方未按期付款，须按合同总额的20%支付违约金...",
        "rerank_score": 0.94
      },
      {
        "index": 3,
        "chunk_id": "chunk_def789",
        "document_id": "file_uvw012",
        "document_name": "补充协议_2024.pdf",
        "page_number": 1,
        "heading_path": [],
        "snippet": "经双方协商，合同期限延长至2026年6月30日...",
        "rerank_score": 0.87
      }
    ],
    "retrieved_chunks_count": 5,
    "confidence": 0.91,
    "low_confidence_warning": null,
    "trace_id": "trace_uuid"
  }
}
```

**验收标准：** `source_citations` 数组中仅包含答案中实际引用的 Chunk，未被引用的检索结果不出现在此数组中；`snippet` 为对应 Chunk `content` 的前 150 字截断；`page_number` 在 TXT / MD 等无页码格式时为 `null`。

---

#### CHC-03 · 置信度评分

**描述：** 基于检索结果的质量计算本次回答的整体置信度分数，反映"这次检索到的内容有多支撑这个答案"。

**置信度计算公式：**

```
confidence = weighted_avg(rerank_scores of cited chunks)
           × coverage_factor
           × (1 - hallucination_penalty)

其中：
  rerank_scores of cited chunks  = 被引用的 Chunk 的 Reranker 分数均值
  coverage_factor                = cited_chunks_count / top_k（引用覆盖率）
  hallucination_penalty          = 答案自检失败的事实比例（见 CHC-04）
```

**低置信度预警：** 当 `confidence < 0.5` 时，`low_confidence_warning` 字段填充预警信息：

```json
"low_confidence_warning": "本次回答的文档依据不充分（置信度 0.38），建议人工核查或补充相关文档后重新查询。"
```

**验收标准：** 检索结果高度相关（Reranker 分数均值 > 0.8）且全部被引用时，`confidence > 0.8`；检索结果低相关或存在未支撑事实时，`confidence < 0.5` 并触发预警。

---

#### CHC-04 · 答案自检节点（Faithfulness Check）

**描述：** LLM 生成答案后，在返回给调用方之前，触发一个轻量的"答案自检"步骤：提取答案中的关键事实声明，逐一验证是否能在检索到的 Chunk 中找到文本支撑。

**自检 Prompt：**

```
给定以下上下文和生成的答案，判断答案中的每个关键事实是否有上下文支撑。

上下文：{context}
答案：{answer}

请列出答案中的关键事实声明，对每条标注：
- supported: 在上下文中有明确文本依据
- unverified: 在上下文中找不到明确支撑（可能是推断或幻觉）

只返回 JSON，格式：
[{"claim": "...", "status": "supported" | "unverified", "source_text": "...（仅supported时）"}]
```

**处理策略：**
- 全部 `supported`：正常返回，`hallucination_penalty=0`。
- 存在 `unverified`：在答案对应位置追加 `†` 标记，响应中增加 `unverified_claims` 数组，`hallucination_penalty` 按 `unverified` 比例计算。
- 自检调用失败（LLM 超时等）：跳过自检，响应中标记 `faithfulness_check: "skipped"`，不影响主流程。

**验收标准：** 对"合同金额是多少"的查询，若检索结果中无金额信息，LLM 猜测的金额被标记为 `unverified`；自检步骤不超过 2 秒；自检失败时主流程不中断。

---

### 3.4 统一查询接口模块（Unified Query API）

本模块是 Hermes "API 极简"主张的核心实现。对外提供一个统一的 `/v2/query` 接口封装完整链路，同时提供分层子接口供需要深度控制的开发者使用。

---

#### UQA-01 · 统一查询接口 /v2/query

**接口：** `POST /api/v2/query`

**描述：** 封装完整的"检索 + 生成 + 溯源 + 自检"链路，开发者无需了解底层 Milvus / Neo4j / BM25 的任何细节。支持流式（SSE）和非流式两种响应模式。

**请求体：**

```json
{
  "kb_ids": ["kb_001", "kb_002"],
  "query": "合同违约金条款是什么？",
  "session_id": "uuid（可选，传入时启用历史上下文）",
  "options": {
    "top_k": 5,
    "stream": false,
    "enable_graph_rag": true,
    "enable_bm25": true,
    "rerank": true,
    "query_rewrite": "none",
    "similarity_threshold": 0.3,
    "enable_citation": true,
    "enable_faithfulness_check": true
  }
}
```

**options 字段说明：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `top_k` | int | 5 | 最终送入 LLM 的 Chunk 数量 |
| `stream` | bool | false | 是否启用 SSE 流式响应 |
| `enable_graph_rag` | bool | true | 是否启用图谱增强检索 |
| `enable_bm25` | bool | true | 是否启用 BM25 全文检索 |
| `rerank` | bool | true | 是否启用 Reranker 精排 |
| `query_rewrite` | string | "none" | Query 改写策略：none / hyde / multi_query |
| `similarity_threshold` | float | 0.3 | Reranker 过滤阈值（0~1） |
| `enable_citation` | bool | true | 是否注入引用标记并返回 source_citations |
| `enable_faithfulness_check` | bool | false | 是否执行答案自检（会增加约 1~2s 延迟） |

**流式响应事件序列（stream=true 时）：**

```
event: trace_step
data: {"step": 1, "type": "query_rewrite", "result": "..."}

event: trace_step
data: {"step": 2, "type": "graph_rag", "entities_found": 3, "latency_ms": 210}

event: trace_step
data: {"step": 3, "type": "hybrid_retrieve", "vector_hits": 20, "bm25_hits": 15, "latency_ms": 380}

event: trace_step
data: {"step": 4, "type": "rerank", "before": 30, "after": 5, "latency_ms": 145}

event: text_delta
data: {"delta": "根据合同第三条"}

event: text_delta
data: {"delta": "[1]，违约金为..."}

event: done
data: {"source_citations": [...], "confidence": 0.91, "trace_id": "..."}
```

**验收标准：** 非流式模式下，`top_k=5` 时响应时间（含 Reranker，不含自检）< 3 秒；流式模式下首个 `text_delta` 事件在 1.5 秒内推送；所有 options 参数均能生效并覆盖知识库默认配置。

---

#### UQA-02 · 纯检索子接口 /v2/retrieve

**接口：** `POST /api/v2/retrieve`

**描述：** 只执行检索，不调用 LLM 生成答案。返回经过混合检索 + RRF + Reranker 处理后的 Chunk 列表，供开发者在自己的业务层使用。

**请求体：**

```json
{
  "kb_ids": ["kb_001"],
  "query": "违约金条款",
  "top_k": 10,
  "enable_graph_rag": true,
  "enable_bm25": true,
  "rerank": true
}
```

**响应体：**

```json
{
  "code": 0,
  "data": {
    "chunks": [
      {
        "chunk_id": "...",
        "content": "...",
        "document_name": "采购合同_2024.pdf",
        "page_number": 3,
        "heading_path": ["第三条 违约责任"],
        "vector_score": 0.89,
        "bm25_score": 0.72,
        "rrf_score": 0.031,
        "rerank_score": 0.94,
        "metadata": {...}
      }
    ],
    "total_retrieved": 35,
    "after_rerank": 10
  }
}
```

**验收标准：** 每个 Chunk 包含所有分数字段；不调用任何 LLM（纯检索链路，延迟 < 1s）；支持与 UQA-01 相同的所有检索参数。

---

#### UQA-03 · 纯生成子接口 /v2/generate

**接口：** `POST /api/v2/generate`

**描述：** 接受开发者自定义的上下文，跳过检索步骤，直接调用 LLM 生成答案并执行溯源和自检。适用于开发者已有自己的检索逻辑，只需要 Hermes 的生成 + 溯源能力的场景。

**请求体：**

```json
{
  "query": "合同违约金是多少？",
  "context_chunks": [
    {
      "chunk_id": "custom_001",
      "content": "违约金为合同总额的20%...",
      "source_label": "采购合同_2024.pdf P3"
    }
  ],
  "options": {
    "stream": false,
    "enable_citation": true,
    "enable_faithfulness_check": false
  }
}
```

**验收标准：** 自定义 `context_chunks` 被正确注入 Citation 编号；生成的答案中引用标记对应开发者传入的 `source_label`；不触发任何 Milvus / Neo4j 查询。

---

#### UQA-04 · Reranker 子接口 /v2/rerank

**接口：** `POST /api/v2/rerank`

**描述：** 接受 Query 和候选文本列表，返回精排后的结果。允许开发者将 Hermes 的 Reranker 能力独立使用，用于自己系统中的任意文本排序场景。

**请求体：**

```json
{
  "query": "违约金条款",
  "candidates": [
    {"id": "doc_1", "text": "第三条 违约责任：..."},
    {"id": "doc_2", "text": "交货地址：北京市..."},
    {"id": "doc_3", "text": "违约金按合同总额20%计算..."}
  ],
  "top_n": 2
}
```

**验收标准：** 返回的候选列表按 `rerank_score` 降序排列；`top_n` 参数生效；与 `doc_2` 明显不相关的候选排名靠后。

---

### 3.5 RAG 效果评估模块（RAG Evaluation）

本模块让"RAG 效果更好"从主观感受变为可量化的数据，是 Hermes 建立开发者信任的重要工具。

---

#### EVA-01 · 评估任务接口

**接口：** `POST /api/v2/knowledge-bases/{kb_id}/evaluate`

**描述：** 传入标准问答对（评估集），系统自动运行一轮完整的检索 + 生成，输出 RAGAS 框架的四项核心指标。评估任务异步执行，返回 `eval_task_id`。

**请求体：**

```json
{
  "eval_set": [
    {
      "question": "合同违约金是多少？",
      "ground_truth": "违约金为合同总额的20%"
    },
    {
      "question": "合同期限是什么时候？",
      "ground_truth": "合同期限至2026年6月30日"
    }
  ],
  "retrieval_options": {
    "top_k": 5,
    "enable_graph_rag": true,
    "rerank": true
  }
}
```

**验收标准：** 任务提交后立即返回 `eval_task_id`；评估完成后可通过 EVA-02 查询结果；评估集为空时返回 400。

---

#### EVA-02 · 评估结果查询

**接口：** `GET /api/v2/knowledge-bases/{kb_id}/evaluations/{eval_task_id}`

**描述：** 查询评估任务结果，包含四项 RAGAS 指标及每道题的详细得分。

**指标说明：**

| 指标 | 含义 | 计算方式 |
|------|------|----------|
| `faithfulness` | 忠实度：答案事实是否有文档支撑 | LLM as Judge（答案自检结果统计） |
| `answer_relevancy` | 答案相关性：答案是否真正回答了问题 | LLM as Judge（问题 vs 答案相关性） |
| `context_precision` | 上下文精确率：检索到的 Chunk 中有用的占比 | LLM as Judge（每个 Chunk 的贡献度） |
| `context_recall` | 上下文召回率：标准答案所需信息是否被召回 | ground_truth vs retrieved chunks 覆盖率 |

**响应体：**

```json
{
  "code": 0,
  "data": {
    "eval_task_id": "...",
    "status": "completed",
    "summary": {
      "faithfulness": 0.87,
      "answer_relevancy": 0.92,
      "context_precision": 0.78,
      "context_recall": 0.83,
      "overall_score": 0.85
    },
    "details": [
      {
        "question": "合同违约金是多少？",
        "answer": "违约金为合同总额的20%[1]",
        "faithfulness": 1.0,
        "answer_relevancy": 0.95,
        "context_precision": 0.80,
        "context_recall": 1.0
      }
    ]
  }
}
```

**验收标准：** 评估完成后四项指标均有值（0~1）；`details` 数组长度与评估集大小一致；`overall_score` 为四项指标的算术均值。

---

#### EVA-03 · 评估历史查询

**接口：** `GET /api/v2/knowledge-bases/{kb_id}/evaluations`

**描述：** 查询该知识库的所有历史评估记录，支持按 `created_at` 排序，方便开发者对比不同参数配置下的效果变化趋势。

**验收标准：** 返回的评估记录按 `created_at` 倒序排列；每条记录包含 `summary` 四项指标和 `retrieval_options`（记录评估时使用的检索参数，便于对比）。

---

### 3.6 可观测性 Trace 模块（Observability）

本模块落地 V1.5 规划中 P0 优先级的 Trace 体系，为开发者提供完整的 Agent 推理链路可视化能力。

---

#### OBS-01 · Trace 数据采集

**描述：** 每次 `/v2/query` 调用自动生成一个 Trace，记录从 Query 预处理到答案生成的全链路耗时和中间结果。

**`agent_traces` 表结构：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID | Trace 唯一标识 |
| `session_id` | UUID | 关联会话（可为 null） |
| `query` | Text | 原始用户 Query |
| `kb_ids` | ARRAY(VARCHAR) | 本次查询的知识库范围 |
| `step_index` | Integer | 当前步骤序号（1-based） |
| `step_type` | VARCHAR | query_rewrite / ner / graph_rag / vector_retrieve / bm25_retrieve / rrf / rerank / generate / faithfulness_check |
| `step_input` | JSONB | 本步骤的输入数据 |
| `step_output` | JSONB | 本步骤的输出数据（截断至 10KB） |
| `latency_ms` | Integer | 本步骤耗时（毫秒） |
| `token_input` | Integer | 本步骤 LLM 输入 Token 数（非 LLM 步骤为 0） |
| `token_output` | Integer | 本步骤 LLM 输出 Token 数 |
| `created_at` | Timestamp | 步骤发生时间 |

**全链路 Trace 示例（一次查询产生 8 条记录）：**

```
step 1: query_rewrite    latency=850ms  tokens_in=120  tokens_out=95
step 2: ner              latency=620ms  tokens_in=80   tokens_out=45
step 3: graph_rag        latency=210ms  tokens_in=0    tokens_out=0
step 4: vector_retrieve  latency=180ms  tokens_in=0    tokens_out=0
step 5: bm25_retrieve    latency=95ms   tokens_in=0    tokens_out=0
step 6: rrf              latency=12ms   tokens_in=0    tokens_out=0
step 7: rerank           latency=145ms  tokens_in=0    tokens_out=0
step 8: generate         latency=2100ms tokens_in=3200 tokens_out=280
```

**验收标准：** 每次 `/v2/query` 调用后，`agent_traces` 表产生对应的步骤记录；`step_output` 字段超过 10KB 时自动截断并标记 `"truncated": true`；Trace 采集失败时不影响主查询流程。

---

#### OBS-02 · Trace 查询接口

**接口：** `GET /api/v2/traces/{trace_id}`

**描述：** 查询单次查询的完整推理链路，返回所有步骤的详细信息，用于调试和效果分析。

**响应体：**

```json
{
  "code": 0,
  "data": {
    "trace_id": "trace_uuid",
    "query": "合同违约金是多少？",
    "kb_ids": ["kb_001"],
    "total_latency_ms": 4212,
    "total_tokens_in": 3400,
    "total_tokens_out": 420,
    "steps": [
      {
        "step_index": 4,
        "step_type": "vector_retrieve",
        "latency_ms": 180,
        "step_output": {
          "hits": 20,
          "top_score": 0.91
        }
      }
    ]
  }
}
```

**验收标准：** `total_latency_ms` 与各 step `latency_ms` 之和基本一致（允许 ±50ms 误差）；步骤按 `step_index` 正序返回。

---

#### OBS-03 · 聚合统计接口

**接口：** `GET /api/v2/analytics`

**描述：** 返回系统级聚合统计数据，支持按时间范围和知识库过滤，用于监控系统健康状况和优化方向。

**查询参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `start_date` | date | 统计开始日期 |
| `end_date` | date | 统计结束日期 |
| `kb_id` | string | 按知识库过滤（可选） |

**响应指标：**

```json
{
  "total_queries": 1520,
  "avg_latency_ms": 2840,
  "avg_confidence": 0.78,
  "low_confidence_rate": 0.12,
  "tool_usage": {
    "graph_rag_triggered": 0.65,
    "bm25_contributed": 0.43,
    "faithfulness_check_triggered": 0.28
  },
  "token_consumption": {
    "total_input": 4850000,
    "total_output": 620000
  },
  "avg_react_steps": 3.2,
  "error_rate": 0.02
}
```

**验收标准：** `low_confidence_rate` 为 `confidence < 0.5` 的查询占比；`tool_usage` 各字段为该工具被触发的查询占比；接口响应时间 < 500ms（基于预计算或缓存）。

---

## 4. API 接口总览

所有 V2.0 新增接口挂载于 `/api/v2` 前缀，与 V1.5 的 `/api/v1` 并行存在，响应格式统一为 `application/json`。

### 4.1 核心查询接口

| Method | Path | 描述 | 备注 |
|--------|------|------|------|
| `POST` | `/api/v2/query` | 统一查询接口（检索 + 生成 + 溯源） | 支持 stream=true |
| `POST` | `/api/v2/retrieve` | 纯检索子接口 | 不调用 LLM |
| `POST` | `/api/v2/generate` | 纯生成子接口 | 接受自定义 context |
| `POST` | `/api/v2/rerank` | Reranker 子接口 | 独立使用精排能力 |

### 4.2 知识库配置接口（V1.5 扩展）

| Method | Path | 描述 | 备注 |
|--------|------|------|------|
| `PATCH` | `/api/v2/knowledge-bases/{kb_id}/retrieval-config` | 更新知识库检索配置 | 设置 top_k / enable_graph_rag 等默认值 |
| `GET` | `/api/v2/knowledge-bases/{kb_id}/retrieval-config` | 查询当前检索配置 | 返回完整配置 JSON |

### 4.3 评估接口

| Method | Path | 描述 | 备注 |
|--------|------|------|------|
| `POST` | `/api/v2/knowledge-bases/{kb_id}/evaluate` | 提交评估任务 | 异步，返回 eval_task_id |
| `GET` | `/api/v2/knowledge-bases/{kb_id}/evaluations/{eval_task_id}` | 查询评估结果 | 含四项 RAGAS 指标 |
| `GET` | `/api/v2/knowledge-bases/{kb_id}/evaluations` | 查询评估历史 | 按 created_at 倒序 |

### 4.4 可观测性接口

| Method | Path | 描述 | 备注 |
|--------|------|------|------|
| `GET` | `/api/v2/traces/{trace_id}` | 查询单次 Trace 详情 | 完整推理链路 |
| `GET` | `/api/v2/sessions/{session_id}/traces` | 查询会话所有 Trace | 分页返回 |
| `GET` | `/api/v2/analytics` | 聚合统计数据 | 支持时间范围过滤 |

---

## 5. 数据库字典设计（V2.0 新增）

### 5.1 [PostgreSQL] knowledge_bases 表扩展（新增字段）

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `retrieval_config` | JSONB | Nullable, Default '{}'  | 知识库级检索配置，字段见 HRE-06 |
| `doc_metadata_schema` | JSONB | Nullable | 预留：自定义元数据字段 Schema |

### 5.2 [PostgreSQL] kb_files 表扩展（新增字段）

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `doc_metadata` | JSONB | Nullable | IDP-05 提取的文档元数据（doc_type / doc_date / key_topics 等） |
| `summary_brief` | Text | Nullable | 不超过 100 字的文档摘要，用于文件列表展示 |

### 5.3 [PostgreSQL] 新表：agent_traces

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `id` | UUID | Primary Key | Trace 步骤唯一标识 |
| `trace_id` | UUID | Not Null, Index | 同一次查询的所有步骤共享此 ID |
| `session_id` | UUID | Nullable, FK → chat_sessions.id | 关联会话 |
| `query` | Text | Not Null | 原始用户 Query |
| `kb_ids` | ARRAY(VARCHAR) | Not Null | 本次查询的知识库范围 |
| `step_index` | Integer | Not Null | 步骤序号（1-based） |
| `step_type` | VARCHAR(64) | Not Null | 步骤类型枚举 |
| `step_input` | JSONB | Nullable | 步骤输入（截断至 10KB） |
| `step_output` | JSONB | Nullable | 步骤输出（截断至 10KB） |
| `latency_ms` | Integer | Not Null | 步骤耗时（毫秒） |
| `token_input` | Integer | Not Null, Default 0 | LLM 输入 Token 数 |
| `token_output` | Integer | Not Null, Default 0 | LLM 输出 Token 数 |
| `created_at` | Timestamp | Not Null, Default NOW() | 步骤发生时间 |

> 建议对 `agent_traces` 按 `created_at` 设置数据保留策略，超过 90 天的记录定期清理或归档，避免表体积无限增长。

### 5.4 [PostgreSQL] 新表：eval_tasks（评估任务表）

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `id` | UUID | Primary Key | 评估任务唯一标识 |
| `kb_id` | UUID | FK → knowledge_bases.id | 关联知识库 |
| `status` | VARCHAR(20) | Not Null, Default 'pending' | pending / running / completed / failed |
| `retrieval_options` | JSONB | Not Null | 评估时使用的检索参数 |
| `eval_set` | JSONB | Not Null | 评估集（question + ground_truth 列表） |
| `results` | JSONB | Nullable | 评估完成后的完整结果 |
| `summary` | JSONB | Nullable | 四项指标汇总 |
| `celery_task_id` | VARCHAR(255) | Nullable | Celery 任务 ID |
| `created_at` | Timestamp | Not Null, Default NOW() | 提交时间 |
| `completed_at` | Timestamp | Nullable | 完成时间 |

### 5.5 [Milvus] knowledge_chunks Collection Schema 扩展（V2.0 新增字段）

| 字段名 | 数据类型 | 约束 / 参数 | 说明 |
|--------|----------|-------------|------|
| `heading_path` | ARRAY(VARCHAR) | Max Capacity: 10 | 所属标题层级路径（IDP-02） |
| `block_type` | VARCHAR | Max Length: 32 | paragraph / table / code / list / table_description |
| `page_number` | INT32 | Nullable | 所在页码（PDF 场景） |
| `position_index` | INT32 | Not Null | 文档内顺序位置 |
| `parent_chunk_id` | VARCHAR | Max Length: 64, Nullable | 细粒度 Chunk 指向对应摘要 Chunk |
| `is_summary` | BOOL | Not Null, Default False | 是否为双层索引中的摘要层 Chunk |

---

## 6. 核心业务规则与约束

### 6.1 检索链路执行规则

- **Graph RAG 触发条件：** Query 侧 NER 识别到实体，且该实体在对应知识库的 Neo4j 中存在节点时，才执行图谱查询。无实体或实体不存在时跳过，不报错。
- **BM25 禁用条件：** `block_type=table_description` 的 Chunk 不写入 BM25 索引，避免自然语言描述词汇干扰精确匹配结果。
- **双层索引使用规则：** 初筛阶段使用 `is_summary=True` 的粗粒度 Chunk；精排和最终返回使用 `is_summary=False` 的细粒度 Chunk，摘要 Chunk 不出现在 `source_citations` 中。
- **Reranker 降级策略：** 若 Reranker 服务不可达，自动降级为仅使用 RRF 融合分数排序，不中断查询，响应中标记 `"reranker_status": "degraded"`。

### 6.2 Citation 与溯源规则

- `source_citations` 只包含答案中实际被引用（出现 `[N]` 标记）的 Chunk，未引用的检索结果不返回。
- `snippet` 字段为对应 Chunk `content` 的前 150 字截断，超出部分以 `...` 结尾。
- 无页码的文档格式（TXT / MD）中，`page_number` 字段为 `null`，用 `position_index` 替代定位。
- LLM 未生成任何引用标记时（如模型版本不遵循指令），`source_citations` 返回空数组，`citations_available: false`。

### 6.3 Trace 数据保留策略

- `agent_traces` 表按月分区（PostgreSQL 分区表），超过 90 天的分区定期 `DETACH` 后归档或删除。
- `step_input` 和 `step_output` 字段的 JSONB 内容超过 10KB 时自动截断，并在字段末尾追加 `"_truncated": true` 标记。
- Trace 写入为异步操作（写入 Redis 队列，后台批量落库），不阻塞查询主链路。

### 6.4 评估任务约束

- 评估集（`eval_set`）最少 1 条，最多 100 条，超出返回 400。
- 每次评估任务会实际调用 LLM（检索 + 生成 + Judge），成本按评估集大小线性增长，建议开发者控制评估集规模。
- 同一知识库同时只允许 1 个评估任务运行，已有 `running` 状态的任务时提交新任务返回 409。

---

## 7. 错误码与响应规范

### 7.1 V2.0 新增错误码

| HTTP Status | 业务 Code | 说明 |
|-------------|-----------|------|
| 400 | 40010 | 评估集为空或超过 100 条 |
| 400 | 40011 | `query_rewrite` 参数值不在枚举范围内 |
| 409 | 40910 | 该知识库已有评估任务运行中 |
| 422 | 42201 | `context_chunks` 为空（/v2/generate 接口） |
| 503 | 50301 | Reranker 服务不可达（已自动降级，仅警告） |
| 503 | 50302 | BM25 检索服务不可达（已自动降级，仅警告） |

> `503` 系列错误在 V2.0 中为**警告级**，不阻断请求，响应中包含降级说明，`code` 仍返回 `0`，但 `data.warnings` 字段中会标明降级状态。

---

## 8. 需求优先级总览

| 优先级 | 模块 | 需求 ID | 核心理由 |
|--------|------|---------|----------|
| 🔴 P0 | 智能文档处理 | IDP-01, IDP-02 | 切片质量是所有后续效果的地基，必须先做 |
| 🔴 P0 | 混合检索引擎 | HRE-03, HRE-04 | BM25 + RRF 是召回率提升的核心，不做无法体现 V2.0 差异 |
| 🔴 P0 | 可观测性 | OBS-01, OBS-02 | 没有 Trace 数据，无法评估任何改进是否有效 |
| 🟠 P1 | 混合检索引擎 | HRE-05 (Reranker) | 精排是召回质量的最后一道门，显著降低噪声 |
| 🟠 P1 | 答案溯源 | CHC-01, CHC-02 | Citation 是企业客户最直接的信任来源 |
| 🟠 P1 | 统一查询接口 | UQA-01 | API 极简的核心交付，P1 组件就绪后即可集成 |
| 🟡 P2 | 智能文档处理 | IDP-03, IDP-04, IDP-05 | 表格处理和双层索引显著提升边缘场景效果 |
| 🟡 P2 | 混合检索引擎 | HRE-01, HRE-02, HRE-06 | Query 改写和图谱增强是护城河，但依赖 P0/P1 先跑通 |
| 🟡 P2 | 答案溯源 | CHC-03, CHC-04 | 置信度和自检增强专业感，但实现成本较高 |
| 🟢 P3 | 统一查询接口 | UQA-02, UQA-03, UQA-04 | 分层子接口提升开发者灵活度，非核心路径 |
| 🟢 P3 | RAG 效果评估 | EVA-01, EVA-02, EVA-03 | 评估体系需要一定真实数据积累后才有统计意义 |
| 🔵 P4 | 可观测性 | OBS-03 | 聚合统计依赖 Trace 数据量积累，早期意义有限 |

**强依赖链：**

```
IDP-01/02（切片） → HRE-03/04（混合检索） → HRE-05（Reranker）
                                                    ↓
OBS-01（Trace）  →  任何效果评估与 Prompt 调优      CHC-01/02（溯源）
                                                    ↓
                                              UQA-01（统一接口）
```

> P0 全部完成之前，不建议开始 P1 的开发；P1 全部完成之前，不建议对外演示或交付。

---

*TyAgent V2.0 (Hermes) PRD · End of Document*
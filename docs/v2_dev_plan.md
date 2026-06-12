# TyAgent V2.0 (Hermes) 开发拆分计划

> 起始日期：**2026-06-12** · 配套 PRD：[TyAgent V2.0 · 需求规格说明书](TyAgent%20V2.0%20%C2%B7%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md)
> **当前状态：** 待开干（V1.5 全链路 smoke 已通过 2026-06-11，作为 V2.0 的底座）
>
> 本文件是 V2.0 的"施工蓝图"：按 PRD §8 优先级链拆成最小可交付单元，标注依赖、技术要点、验收方式与人/机分工，避免反复回溯需求。
>
> 每完成一个阶段（T0~T12），同步更新 [progress.md](progress.md) 表格状态与历史变更。

---

## 0. 总体策略

### 0.1 拆分逻辑

PRD §8 优先级链：

```
IDP-01/02 → HRE-03/04 → HRE-05 → CHC-01/02 → UQA-01
                  ↑
            OBS-01/02（贯穿）
```

P0 全部完成前，**不**开 P1；P1 全部完成前，**不**开演示。这条规则会严格 follow。

V2.0 拆为 13 个阶段（T0~T12），P0 必须、P1 推荐、P2 加分、P3 可选：

| 阶段 | 子需求 | 优先级 | 工程量 | 依赖 |
|---|---|---|---|---|
| **T0** | 基础设施扩展（升级 Milvus / 加 BM25 / 加 trace 表 / 加 eval 表） | P0 前置 | 中 | 无 |
| **T1** | IDP-01/02/06（结构感知解析 + 切片 + 入库管道重构） | P0 | 大 | T0 |
| **T2** | HRE-03/04（BM25 + RRF 融合） | P0 | 中 | T1 |
| **T3** | OBS-01/02（Trace 采集 + 查询接口） | P0 | 中 | T0 |
| **T4** | HRE-05（Reranker 精排，在线 API） | P1 | 中 | T2 |
| **T5** | CHC-01/02（Citation 注入 + 解析） | P1 | 中 | T4 |
| **T6** | UQA-01（统一查询接口 /v2/query） | P1 | 中 | T5 + T3 |
| **T7** | IDP-03/04/05（表格描述 + 双层索引 + 文档元数据） | P2 | 大 | T1 |
| **T8** | HRE-01/02/06（Query 改写 + Query NER + 配置项） | P2 | 中 | T6 |
| **T9** | CHC-03/04（置信度 + 答案自检） | P2 | 中 | T5 |
| **T10** | UQA-02/03/04（分层子接口 retrieve/generate/rerank） | P3 | 中 | T6 |
| **T11** | EVA-01/02/03（RAGAS 评估） | P3 | 中 | T6 |
| **T12** | OBS-03（聚合统计） | P4 | 小 | T3 + 数据 |

### 0.2 工程原则

1. **`/api/v1/...` 接口完全不动**：V1.5 是已验收的生产代码。V2.0 在 `/api/v2/...` 路径下并存。
2. **Milvus Collection 全部重建**（用户已确认）：V2.0 新 Schema 含 6 个新字段。V2 上线前清空所有现有 KB Collection，文档需要重新上传。
3. **入库管道全量重写**：[app/tasks/ingest_task.py](../app/tasks/ingest_task.py) 在 V2.0 是全新版本（11 步），V1.5 版本保留作历史参考但不再用。
4. **每个阶段独立验收**：mock 单测覆盖业务逻辑 + 真服务集成测试。P0/P1 阶段必须有端到端 smoke。
5. **代码注释 / 文档 / commit message 一律简体中文**。

### 0.3 已确认的关键决策（2026-06-12）

| 决策点 | 选择 | 影响 |
|---|---|---|
| BM25 方案 | **Milvus 2.5+ 稀疏向量**（推荐） | 升级 Milvus 镜像到 2.5+；同 Collection 内同时存稠密+稀疏 |
| Reranker 方案 | **在线 API**（推荐起步） | 复用 LiteLLM 网关；优先 SiliconFlow `BAAI/bge-reranker-v2-m3` |
| V1.5 KB 数据处理 | **清空重来**（用户已确认） | V2 上线时 docker compose down + 删 milvus volume + drop_all PG |
| RAGAS 评估 | **官方 ragas 库**（推荐） | `pip install ragas`；适配 LiteLLM 走代理调用 |

### 0.4 用户操作约定

Claude **不主动执行**：
- `uv pip install ...`（只更新 `requirements.txt`）
- `docker run / docker compose up`（只写 `docker-compose/docker-compose.yml`）
- `pytest` 长跑 / `uvicorn` / `celery worker` / 端到端 smoke（只给命令，用户跑完贴回结果）

Claude **可自主执行**：
- 短跑 `pytest` 单测（mock 不依赖外部服务的部分）
- import 自检 / SDK API 探测

---

## T0 · 基础设施扩展

**目标**：把 V2.0 所有新组件的依赖、配置、Schema 全部准备好。

### T0.1 依赖与配置

- 更新 `requirements.txt` 追加：
  - `ragas>=0.2.0`（EVA 模块）
  - `jieba>=0.42.1`（BM25 中文分词；Milvus 2.5 稀疏向量 BM25 内置 BM25 函数支持，但中文场景可能需要预分词）
- 升级 `docker-compose/docker-compose.yml` Milvus 镜像到 `milvusdb/milvus:v2.5.x`（**用户操作：删旧 volume `d:/dockerVolumes/milvus` + 重启容器**）
- `app/core/config.py` 新增字段：
  - `reranker_type`（默认 `none`，可选 `api` / `local`）
  - `reranker_model`（默认 `BAAI/bge-reranker-v2-m3`）
  - `reranker_api_key` / `reranker_api_base`
  - `bm25_enable`（默认 `True`）
  - `trace_enable`（默认 `True`）
  - `trace_retention_days`（默认 90）
- `.env.example` 同步追加

### T0.2 PG 模型扩展

- 新增 `app/models/agent_trace.py`：`AgentTrace` 表（OBS-01 字段）
- 新增 `app/models/eval_task.py`：`EvalTask` 表（EVA 字段）
- 扩展 `app/models/knowledge_base.py`：加 `retrieval_config: JSONB`、`doc_metadata_schema: JSONB`
- 扩展 `app/models/kb_file.py`：加 `doc_metadata: JSONB`、`summary_brief: Text`
- lifespan `create_all` 自动建新表（V1.5 一致策略）

### T0.3 Milvus Schema 扩展（V2 版本）

- 新增 `app/rag/schema_v2.py::build_v2_kb_collection_schema(dim)`：在 V1.5 Schema 基础上加 6 字段：
  - `heading_path: ARRAY(VARCHAR, max_capacity=10)`
  - `block_type: VARCHAR(32)`
  - `page_number: INT32`（nullable）
  - `position_index: INT32`
  - `parent_chunk_id: VARCHAR(64)`（nullable）
  - `is_summary: BOOL`
  - **关键新增**：`sparse_vector: SPARSE_FLOAT_VECTOR`（BM25 稀疏向量）
  - 索引：稠密向量 HNSW+COSINE，稀疏向量 SPARSE_INVERTED_INDEX+BM25
- `app/rag/milvus_client.py` 新增 `create_v2_kb_collection(kb_id, dim)`

### T0.4 验收

- `pytest` mock 集全绿（V1.5 不破坏）
- 用户手动：清 milvus volume + 升级镜像 + 重启容器 + uvicorn 启动看到"V2 KB Collection 自动创建"日志
- `psql \d+ agent_traces eval_tasks knowledge_bases kb_files` 验所有新字段就位

---

## T1 · 智能文档处理（IDP P0：01/02/06）

**目标**：把入库管道从"机械切片"升级为"结构感知切片 + 元数据丰富"。

### T1.1 IDP-01 结构感知文档解析

- 重构 `app/ingest/parser.py`，输出从 `str` 改为 `list[StructuredBlock]`：

```python
@dataclass
class StructuredBlock:
    block_id: str  # uuid
    block_type: Literal["paragraph", "heading", "table", "code", "list"]
    heading_path: list[str]  # 该 block 所属的标题层级路径
    content: str  # 表格类型为 markdown
    page_number: int | None
    position_index: int
```

- PDF：PyMuPDF 提取 text blocks 按字体大小/粗细推断 heading 层级（启发式：大字号粗体 = 标题）
- DOCX：python-docx 直接读 `paragraph.style.name`，匹配 "Heading 1/2/3"
- MD：markdown-it-py 的 token 类型直接对应（`heading_open`/`paragraph_open`/`table_open`/`fence`）
- TXT：单 block_type=paragraph，heading_path=[]

### T1.2 IDP-02 结构感知切片

- 新建 `app/ingest/structured_splitter.py::split_structured_blocks`
- 输入：`list[StructuredBlock]` + chunk_size/overlap
- 输出：`list[StructuredChunk]`，每个 chunk 携带 heading_path / block_type / page_number / position_index
- 切片优先级（PRD §IDP-02）：代码块整块 > 表格整块 > 标题段落组合 > 普通段落兜底

### T1.3 IDP-06 入库管道重构

- 重构 `app/tasks/ingest_task.py`，从 7 步扩展到 11 步：
  ```
  Step 1: status=processing, progress=0
  Step 2: 结构感知解析（IDP-01）        [progress=15]
  Step 3: 结构感知切片（IDP-02）        [progress=25]
  Step 4: 表格描述生成（IDP-03，T7 才接通；T1 阶段先 skip）  [progress=30]
  Step 5: 段落摘要生成（IDP-04，T7 才接通；T1 阶段先 skip）  [progress=40]
  Step 6: 文档元数据提取（IDP-05，T7 才接通；T1 阶段先 skip） [progress=45]
  Step 7: 批量向量嵌入                  [progress=65]
  Step 8: 写入 Milvus（V2 Schema，含 heading_path 等新字段） [progress=80]
  Step 9: NER 实体抽取 → 写入 Neo4j   [progress=92]
  Step 10: 写入 BM25 索引（T2 才接通；T1 阶段先 skip）       [progress=97]
  Step 11: status=completed, progress=100
  ```
- T1 阶段 Step 4/5/6/10 都是 noop（写空数据），由后续阶段填实
- 保留 V1.5 的"每任务 task_resources 现建现断"模式

### T1.4 测试

- mock 单测：StructuredBlock 输出验证（PDF/DOCX/MD/TXT 各 1 sample）
- 切片测试：验 heading_path 透传 / 表格不被截断 / 代码块完整
- 集成测试：真上传 PDF → 进 V2 KB → 验 Milvus 中 chunks 含 heading_path 字段

### T1.5 验收

- 一份带标题 / 表格 / 代码的 PDF 入库后，Milvus chunks 的 `heading_path` 字段非空，`block_type` 多样
- 表格不会被切成两半（同一 chunk）
- V1.5 ingest_task 老代码作为 `app/tasks/ingest_task_v1.py` 归档（不再用，但保留参考）

---

## T2 · 混合检索引擎（HRE-03/04）

**目标**：在 V2 KB 上实现"向量 + BM25 + 图谱"三路并行 + RRF 融合。

### T2.1 HRE-03 BM25 集成

- Milvus 2.5+ Collection 已含 `sparse_vector` 字段（T0.3 已建好）
- 新增 `app/rag/bm25.py`：
  - `compute_sparse_vector(text) -> dict[int, float]`：用 jieba 分词 + tf 计算 BM25 稀疏向量
  - 或者直接用 Milvus 2.5 的内置 BM25 Function（更省事，**首选**）
- 入库 Step 10：把每个 chunk 的 content 计算 sparse_vector 写入 Milvus 同一行

### T2.2 HRE-04 RRF 融合

- 新增 `app/rag/hybrid_retriever.py::hybrid_search`
- 用 Milvus 2.5+ 的 `hybrid_search` API 一次性查向量+稀疏，返回 RRFRanker 融合后的结果
- 或者两次独立 search，应用层做 RRF 公式融合（k=60）

### T2.3 测试

- mock 单测：RRF 公式正确性（已知 rank 算预期 score）
- 集成测试：上传中英混合文档 → 查"bge-reranker-v2"（专有名词）→ 验 BM25 路径召回成功

### T2.4 验收

- 同一 query 同时在向量和 BM25 都靠前的 chunk → 融合后排第一
- 仅在 BM25 路径出现的精确匹配 chunk → 进 top-10

---

## T3 · 可观测性 Trace（OBS-01/02）

**目标**：每次 v2 接口调用自动写 trace，开发者能查到完整推理链路。

### T3.1 OBS-01 Trace 采集

- 新增 `app/observability/tracer.py`：
  - `Tracer` 类：context manager，进入时生成 trace_id
  - `tracer.step(step_type, step_input)` 装饰器：自动计时 + 写入 PG
  - 写入用异步任务（`agent_traces` 表大且写频，需异步）—— V2 阶段简化为同步写，T12 阶段再优化
- 集成点：T6 阶段 `/v2/query` 全链路串联时埋点

### T3.2 OBS-02 查询接口

- 新增 `app/api/v2/endpoints/traces.py`：
  - `GET /api/v2/traces/{trace_id}` 返完整步骤
  - `GET /api/v2/sessions/{session_id}/traces` 分页返该会话所有 trace

### T3.3 测试

- mock 单测：Tracer 上下文管理 / step 装饰器自动计时
- 集成测试：调一次 `/v2/query` → 查 trace_id → 验 8 个 step 都在

### T3.4 验收

- 一次 `/v2/query` 后 `agent_traces` 表新增 8 条记录
- Trace 查询接口返完整步骤；total_latency_ms ≈ sum(step latency)

---

## T4 · Reranker 精排（HRE-05）

**目标**：在 RRF 融合后加 Reranker 精排，过滤低相关性 chunk。

### T4.1 Reranker 客户端

- 新增 `app/rag/reranker.py`：
  - 抽象基类 `BaseReranker`
  - `LiteLLMReranker`：通过 LiteLLM 走 SiliconFlow 等 API
  - `NoopReranker`：开发期跳过（RERANKER_TYPE=none）
  - 失败降级：抛错时返原 chunks 顺序，记日志（HRE-05 降级策略）

### T4.2 集成到 hybrid_retriever

- `hybrid_search` 后追加 `rerank_chunks(query, chunks, top_k)`
- 过滤分数 < `similarity_threshold`（默认 0.3）
- 过滤后剩余 < 3 时保留 top-3 不截断（PRD 兜底规则）

### T4.3 验收

- 检索"违约金"召回的"交货地址"段落（向量相似但语义无关）被 Reranker 过滤
- API 时延 < 300ms

---

## T5 · Citation 注入 + 解析（CHC-01/02）

**目标**：让 LLM 答案带 `[1][2]` 引用，解析为 `source_citations` 结构。

### T5.1 CHC-01 Context 组装

- 新增 `app/rag/citation.py::build_context_with_citation(chunks)`
- 输出格式（PRD §CHC-01）：
  ```
  [1] 来源：xxx.pdf（第3页）
  内容：...
  
  [2] 来源：...
  ```
- System Prompt 注入引用规则

### T5.2 CHC-02 解析

- `parse_citations(answer_text, chunks) -> list[CitationItem]`
- 用正则 `\[(\d+)\]` 抽出引用编号 + 去重 + 映射回 chunks
- 返 `source_citations` 结构（PRD 含 chunk_id / document_name / page_number / heading_path / snippet / rerank_score）

### T5.3 验收

- LLM 答案中 `[1]` 标记能正确对应 chunks[0]
- 未引用的检索结果不出现在 source_citations 中

---

## T6 · 统一查询接口 /v2/query（UQA-01）

**目标**：把 T1~T5 全部能力封装成一个 endpoint，开发者三行代码接入。

### T6.1 endpoint 骨架

- 新增 `app/api/v2/endpoints/query.py`
- 串联：Query NER（T8 才接通，T6 阶段先 skip）→ 图谱锚定（T8 接通）→ hybrid_search → rerank → build_context → LLM 生成 → parse_citations → 返响应
- 支持 stream（SSE） 和 非流式两种模式

### T6.2 V2 schemas

- 新增 `app/schemas/v2/query.py`：`QueryRequest` / `QueryOptions` / `QueryResponse` / `CitationItem`

### T6.3 V2 router 挂载

- 新建 `app/api/v2/router.py`：`/api/v2` 前缀
- 挂到 `app/main.py::create_app()`

### T6.4 验收

- 非流式：`top_k=5` 响应时间 < 3 秒
- 流式：首个 text_delta 1.5 秒内推送
- options 全字段生效

---

## T7~T12（P2/P3+）

按 PRD §8 优先级链推进。每个阶段在前置完成后再启动，避免过早返工。

详细拆分会在 P0/P1 完成后再细化（届时根据真实数据决定细节）。

---

## 1. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| Milvus 2.5+ 升级后 V1 数据丢失 | 用户已知风险 | 操作前确认 V1 数据可清；用户操作前提示 |
| BM25 中文分词效果不佳 | 召回率不达预期 | T2 阶段同时验证 jieba 和 Milvus 内置 BM25 Function，择优 |
| Reranker API 限流 | 高并发场景排队 | API 调用加 Semaphore 限制并发；失败降级到不 rerank |
| RAGAS 评估调真 LLM 烧 token | EVA 验证成本高 | 评估集硬上限 100 条；建议开发期用 mock LLM 跑通流程 |
| Trace 表无限增长 | PG 体积膨胀 | T0 加配置 `trace_retention_days=90`；T12 阶段加定期清理 cron |
| 入库管道 11 步任一步抖动 | 全文件 failed | 沿用 V1.5 的 NER 软失败模式：非主链路步骤（IDP-03/04/05）失败不阻断 |

---

## 2. 进度追踪

每完成一个阶段，在本文件末尾追加 `### ✅ T0 完成 · YYYY-MM-DD` 段落，并同步刷新 [progress.md](progress.md) 总览表。

---

*TyAgent V2.0 Dev Plan · End of Document*

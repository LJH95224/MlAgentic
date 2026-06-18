# TyAgent 开发进度

> **维护约定**：每次完成一个 PRD 子模块（或对已完成模块做实质性改动）后，必须同步更新本文档。
> 文档定位：让任何接手者在 2 分钟内掌握当前实现到哪一步、下一步该做什么。
>
> **配套文档**：
> - [architecture.md](architecture.md) — 技术架构、数据流转、关键设计决策
> - [TyAgent V1.0 需求规格说明书](TyAgent%20V1.0%20%28%E5%9F%BA%E7%A1%80%E5%BA%95%E5%BA%A7%29%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md) — V1.0 PRD（基础底座，已完成）
> - [TyAgent V1.5 · 需求规格说明书](TyAgent%20V1.5%20%C2%B7%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md) — V1.5 PRD（数据管理层，已完成）
> - [v1.5_dev_plan.md](v1.5_dev_plan.md) — V1.5 开发拆分计划（已完成存档）
> - **[TyAgent V2.0 · 需求规格说明书](TyAgent%20V2.0%20%C2%B7%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md)** — V2.0 (Hermes) PRD（当前迭代，专业级 RAG 引擎）
> - **[v2_dev_plan.md](v2_dev_plan.md)** — V2.0 开发拆分计划（T0~T12，按 PRD §8 优先级链推进）
> - [v1_5_api_reference.md](v1_5_api_reference.md) — V1.5 接口参考
> - [v1_5_frontend_guide.md](v1_5_frontend_guide.md) — V1.5 前端模块拆解
> - [embedding.md](embedding.md) — Embedding 模型选型对比

---

## V1.0 基础底座（已完成 ✅）

| 模块 | PRD 章节 | 状态 | 完成日期 |
|---|---|---|---|
| 接入与通信 | 3.1 | ✅ 完成 | 2026-06-09 |
| LLM 路由 | 3.2 | ✅ 完成 | 2026-06-09 |
| Agent 编排（LangGraph ReAct） | 3.3 | ✅ 完成 | 2026-06-10 |
| 本地执行工具（subprocess） | 3.4 | ✅ 完成 | 2026-06-10 |
| Agentic RAG（**Milvus**） | 3.5 | ✅ 完成 + 联调验收 | 2026-06-10 |
| 知识图谱（**Neo4j**） | 3.6 | ✅ 完成 + 联调验收 | 2026-06-10 |

---

## V1.5 数据管理层（已完成 ✅）

> **迭代完成日期：2026-06-12**。端到端 smoke 全链路验收通过。详细拆分见 [v1.5_dev_plan.md](v1.5_dev_plan.md)。

| 阶段 | 模块 | PRD 子需求 | 状态 | 完成日期 |
|---|---|---|---|---|
| S0 | 基础设施（Celery + Redis + DB 迁移） | TASK-01 / 数据模型 | ✅ 完成 + 联调验收 | 2026-06-11 |
| S1 | 会话管理 CRUD（不含异步任务） | SES-01 ~ SES-06 / SES-09 | ✅ 完成 + 集成测试验收 | 2026-06-11 |
| S2 | 知识库 CRUD + Milvus 多 Collection | KB-01 ~ KB-05 | ✅ 完成 + 集成测试验收 | 2026-06-11 |
| S3 | 文件上传 + 异步入库（核心） | FILE-01 ~ FILE-05 / TASK-02 / TASK-03 | ✅ 完成 + 端到端 smoke 验收 | 2026-06-11 |
| S4 | 会话标题/摘要异步生成 | SES-07 / SES-08 / TASK-04 / TASK-05 | ✅ 完成 + 全链路 smoke 间接验收 | 2026-06-11 |
| S5 | KB 关联对话 + 端到端联调 | KB-06 | ✅ 完成 + 全链路 smoke 验收（1:44） | 2026-06-12 |

---

## V2.0 Hermes — 专业级 RAG 引擎（进行中 🔧）

> **迭代起点：2026-06-12**。V1.5 全链路 smoke 已通过作为底座。详细拆分见 [v2_dev_plan.md](v2_dev_plan.md)。
>
> **核心目标**：把 RAG 从"能跑通"升级为"效果可信赖"——智能切片 + BM25/RRF 混合检索 + Reranker 精排 + Citation 溯源 + RAGAS 评估 + Trace 可观测。

| 阶段 | 模块 | PRD 子需求 | 优先级 | 状态 | 完成日期 |
|---|---|---|---|---|---|
| T0 | 基础设施扩展（Milvus 升级 / BM25 / trace 表 / eval 表） | P0 前置 | P0 | ✅ 完成 + 集成验收 | 2026-06-12 |
| T1 | IDP-01/02/06（结构感知解析 + 切片 + 入库管道重构） | P0 | P0 | ✅ 完成 + 集成验收 | 2026-06-12 |
| T2 | HRE-03/04（BM25 + RRF 融合） | P0 | P0 | ✅ 完成 + 集成验收 | 2026-06-12 |
| T3 | OBS-01/02（Trace 采集 + 查询接口） | P0 | P0 | ✅ 完成 + 集成验收 | 2026-06-12 |
| T4 | HRE-05（Reranker 精排） | P1 | P1 | ✅ 完成 + 集成验收 | 2026-06-15 |
| T5 | CHC-01/02（Citation 注入 + 解析） | P1 | P1 | ✅ 完成 + 集成验收 | 2026-06-15 |
| T6 | UQA-01（统一查询接口 /v2/query） | P1 | P1 | ✅ 完成 + 集成验收 | 2026-06-15 |
| T7 | IDP-03/04/05（表格描述 + 双层索引 + 文档元数据） | P2 | P2 | ✅ 完成 + 集成验收 | 2026-06-15 |
| T8 | HRE-01/02/06（Query 改写 + NER + 配置项） | P2 | P2 | ✅ 完成 + 集成验收 | 2026-06-15 |
| T9 | CHC-03/04（置信度 + 答案自检） | P2 | P2 | ✅ 完成 + 集成验收 | 2026-06-15 |
| T10 | UQA-02/03/04（分层子接口） | P3 | P3 | ✅ 完成 + 集成验收 | 2026-06-16 |
| T11 | EVA-01/02/03（RAGAS 评估） | P3 | P3 | ✅ 完成 + 集成验收 | 2026-06-16 |
| T12 | OBS-03（聚合统计） | P4 | P4 | ✅ 完成 + 集成验收 | 2026-06-16 |

### 已确认的关键决策

| 决策点 | 选择 | 影响 |
|---|---|---|
| BM25 方案 | Milvus 2.5+ 稀疏向量 | 升级 Milvus 镜像；同 Collection 稠密+稀疏 |
| Reranker 方案 | 在线 API（LiteLLM 网关） | 优先 SiliconFlow `BAAI/bge-reranker-v2-m3` |
| V1.5 KB 数据 | 清空重来（用户已确认） | V2 上线删 milvus volume + drop_all PG |
| RAGAS 评估 | 官方 ragas 库 | `pip install ragas`；适配 LiteLLM 代理 |

### T0 · 基础设施扩展 ✅（2026-06-12）

#### 交付内容

| 子任务 | 实现位置 | 备注 |
|---|---|---|
| **T0.1 配置项扩展** | [app/core/config.py](../app/core/config.py)（新增 8 字段：reranker_type/model/api_key/api_base/similarity_threshold + bm25_enable + rrf_k + trace_enable/retention_days） | V2.0 区段 |
| **T0.1 .env.example 同步** | [.env.example](../.env.example)（追加 V2.0 配置块） | 含注释说明 |
| **T0.1 依赖追加** | [requirements.txt](../requirements.txt)（追加 jieba>=0.42.1 + ragas>=0.2.0） | 用户手动 `uv pip install` |
| **T0.2 AgentTrace 新表** | [app/models/agent_trace.py](../app/models/agent_trace.py)（13 字段：trace_id / session_id / kb_id / step_type / parent_step / step_latency_ms / total_latency_ms / step_input(JSONB) / step_output(JSONB) / model_name / token_count / error_message / created_at） | OBS-01 Trace 记录 |
| **T0.2 EvalTask 新表** | [app/models/eval_task.py](../app/models/eval_task.py)（12 字段：kb_id / name / status / progress / eval_dataset(JSONB) / eval_result(JSONB) / eval_config(JSONB) / question_count / error_message / created_at / completed_at） | EVA-01/02/03 评估任务 |
| **T0.2 KB 扩展字段** | [app/models/knowledge_base.py](../app/models/knowledge_base.py)（+retrieval_config JSONB / +doc_metadata_schema JSONB） | V2.0 混合检索配置 + 文档元数据模板 |
| **T0.2 KbFile 扩展字段** | [app/models/kb_file.py](../app/models/kb_file.py)（+doc_metadata JSONB / +summary_brief Text） | V2.0 文档元数据 + 摘要 |
| **T0.2 模型注册** | [app/models/__init__.py](../app/models/__init__.py)（新增 AgentTrace / EvalTask） | lifespan create_all 自动建表 |
| **T0.3 V2 Milvus Schema** | [app/rag/schema.py](../app/rag/schema.py)（`build_v2_kb_collection_schema` + `build_v2_index_params`） | 7 新字段 + SPARSE_FLOAT_VECTOR + SPARSE_INVERTED_INDEX BM25 |
| **T0.3 V2 KB Collection 创建** | [app/rag/milvus_client.py](../app/rag/milvus_client.py)（`create_v2_kb_collection`） | 幂等创建 + load |
| **T0.3 RAG 模块导出** | [app/rag/__init__.py](../app/rag/__init__.py)（新增 `create_v2_kb_collection` 导出） | — |
| **T0.4 单测** | [tests/test_v2_t0.py](../tests/test_v2_t0.py)（52 用例） | 配置项 + PG 模型 + V2 Schema + 索引 + V1.5 零回归 |

#### 关键设计决策

1. **Milvus 镜像不升级**：当前已用 `v2.6.18`（> 2.5），原生支持稀疏向量 + BM25，无需升级
2. **V1.5 `/api/v1/...` 完全不动**：`create_kb_collection` 继续用 V1.5 Schema，V2 用独立的 `create_v2_kb_collection`
3. **稀疏向量用 SPARSE_FLOAT_VECTOR**：Milvus 2.5+ 原生类型，索引走 SPARSE_INVERTED_INDEX + BM25 metric
4. **drop_ratio_build=0.2**：建索引时丢弃低频词后 20%，减小体积；后续可根据实际数据调优
5. **V2 Schema 总共 15 字段**：V1.5 的 8 个 + V2 新增 7 个（heading_path / block_type / page_number / position_index / parent_chunk_id / is_summary / sparse_vector）

#### 验证状态

- ✅ T0 单测 **52/52 通过**
- ✅ V1.5 全量回归 **472 passed + 6 skipped**（420 → 472，零回归）
- ✅ 集成验证（2026-06-17 v2_smoke）：uvicorn 启动看到 "数据库表初始化完成"，新表（agent_traces / eval_tasks / query_analytics）已建
- ✅ Bugfix（2026-06-17）：启动期新增 V2 兼容补列迁移，旧 V1.5 PG 表缺 `retrieval_config` / `doc_metadata_schema` / `doc_metadata` / `summary_brief` 时自动 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`，避免 `/api/v1/knowledge-bases` ORM 列表查询因 UndefinedColumn 返回 500

### T1 · 智能文档处理 ✅（2026-06-12）

#### 交付内容

| 子任务 | 实现位置 | 备注 |
|---|---|---|
| **T1.1 IDP-01 结构感知解析** | [app/ingest/parser.py](../app/ingest/parser.py)（`StructuredBlock` 数据类 + `parse_document_structured()` + 4 个结构感知解析器） | PDF 按字号/粗体推断标题；DOCX 读 style；MD token 对应；TXT 按段 |
| **T1.2 IDP-02 结构感知切片** | [app/ingest/structured_splitter.py](../app/ingest/structured_splitter.py)（`StructuredChunk` + `split_structured_blocks()`） | 代码块/表格整块保留 → 标题段落组合 → 超长段落 RecursiveCharacterTextSplitter 兜底 |
| **T1.3 IDP-06 入库管道重构** | [app/tasks/ingest_task.py](../app/tasks/ingest_task.py)（7步→11步；Step 4/5/6/10 noop） | V1.5 版归档为 [ingest_task_v1.py](../app/tasks/ingest_task_v1.py) |
| **T1.3 Milvus V2 写入** | ingest_task `_step_milvus_write_v2`（15 字段，含 heading_path / block_type / sparse_vector） | sparse_vector 暂写空（T2 填实） |
| **T1.4 单测** | [tests/test_v2_t1.py](../tests/test_v2_t1.py)（50 用例） | 解析/切片/管道/V1.5 兼容 |
| **V1.5 测试兼容** | [tests/test_ingest_task.py](../tests/test_ingest_task.py)（已适配 V2 API） | `_make_chunk_id` → `_make_chunk_id_int`；`parse_document` → `parse_document_structured` |

#### 关键设计决策

1. **V1.5 `parse_document()` 完全不动**：保留原始 V1.5 纯文本解析器实现，V2 新增独立的 `parse_document_structured()` 入口
2. **代码块/表格不可切断**：IDP-02 核心策略——代码块和表格无论多长都整块保留为一个 chunk
3. **heading_path 取最完整路径**：标题+段落组合 chunk 时，heading_path 取段落块的（含标题自身），而非标题块的（不含自身）
4. **MD 表格需 `.enable("table")`**：markdown-it-py 默认不解析表格，需显式启用
5. **V1.5 ingest_task 归档**：`ingest_task_v1.py` 保留供参考但不再使用

#### 验证状态

- ✅ T1 单测 **50/50 通过**
- ✅ V1.5 全量回归 **522 passed + 6 skipped**（472 → 522，零回归）
- ✅ 集成验证（2026-06-17 v2_smoke）：真实文档 docx 上传，chunk_count=160（fine 154 + table_description 5 + coarse 1），block_types=[table_description, table, paragraph] 三类齐全

### T2 · 混合检索引擎 ✅（2026-06-12）

#### 交付内容

| 子任务 | 实现位置 | 备注 |
|---|---|---|
| **T2.1 V2 Schema BM25 Function** | [app/rag/schema.py](../app/rag/schema.py)（content 字段加 `enable_analyzer=True` + BM25 Function `content→sparse_vector`） | Milvus 插入时自动生成稀疏向量，无需手动计算 |
| **T2.1 索引参数 BM25 k1/b** | schema.py `build_v2_index_params`（bm25_k1=1.2 / bm25_b=0.75 / drop_ratio_build=0.2） | 经典 BM25 标准参数 |
| **T2.1 入库管道适配** | [app/tasks/ingest_task.py](../app/tasks/ingest_task.py)（移除手动 `sparse_vector: {}`；Step 10 改为确认步骤） | Milvus BM25 Function 在 Step 8 插入时自动生成稀疏向量 |
| **T2.2 混合检索引擎** | [app/rag/hybrid_retriever.py](../app/rag/hybrid_retriever.py)（`HybridSearchResult` + `hybrid_search()` + `format_hybrid_results()`） | dense + BM25 双路 + RRFRanker 融合 |
| **T2.2 降级策略** | hybrid_retriever.py：BM25 失败→纯向量检索；bm25_enable=False→纯向量检索 | 保障可用性 |
| **T2.3 单测** | [tests/test_v2_t2.py](../tests/test_v2_t2.py)（17 用例） | Schema BM25 + 混合检索 + 降级 + 格式化 + V2 写入验证 |

#### 关键设计决策

1. **Milvus 内置 BM25 Function**：不用 jieba 手动计算稀疏向量。在 Schema 中声明 `Function(content→sparse_vector, BM25)`，插入时 Milvus 自动分词+计算；查询时直接传原始文本
2. **content 字段 `enable_analyzer=True`**：BM25 Function 的前提条件，让 Milvus 在插入时对文本做分词
3. **写入时不包含 sparse_vector**：插入数据中不应有 `sparse_vector` 字段，由 BM25 Function 自动生成
4. **RRF k=60**：学术标准值，可通过 `RRF_K` 配置调整
5. **hybrid_search API**：使用 `AnnSearchRequest` + `RRFRanker` 一次性查询双路，比应用层融合更高效
6. **V2 Schema 不再复用 V1.5 base_fields**：因为 content 字段需要 `enable_analyzer=True`，与 V1.5 的 content 字段定义不同

#### 验证状态

- ✅ T2 单测 **17/17 通过**
- ✅ V1.5 全量回归 **539 passed + 6 skipped**（522 → 539，零回归）
- ✅ 集成验证（A.1 实验 + 2026-06-17 v2_smoke）：BM25+RRF 链路在 4 组 A.1 实验和 v2_smoke 步骤 [5a-5d] 全部跑通；analytics tool_usage.bm25_contributed=1.000 印证每次查询 BM25 都参与

### T3 · 可观测性 Trace ✅（2026-06-12）

#### 交付内容

| 子任务 | 实现位置 | 备注 |
|---|---|---|
| **T3.1 Tracer 采集器** | [app/observability/tracer.py](../app/observability/tracer.py)（`Tracer` 上下文管理器 + `step()` 步骤装饰器 + `_flush_to_db()` 批量写入） | OBS-01 |
| **T3.1 TraceStep 数据类** | tracer.py `TraceStep`（step_type / parent_step / step_latency_ms / step_input / step_output / model_name / token_count / error_message） | 记录每步骤元数据 |
| **T3.2 Trace 查询端点** | [app/api/v2/endpoints/traces.py](../app/api/v2/endpoints/traces.py)（`GET /api/v2/traces/{trace_id}` + `GET /api/v2/traces/sessions/{session_id}/traces`） | OBS-02 |
| **T3.2 V2 Schemas** | [app/schemas/v2/trace.py](../app/schemas/v2/trace.py)（TraceDetail / TraceStepItem / TraceListItem / TraceListResponse） | — |
| **T3.2 V2 Router** | [app/api/v2/router.py](../app/api/v2/router.py)（`/api/v2` 前缀 + 挂载到 main.py） | 与 V1 `/api/v1` 独立并存 |
| **T3.3 单测** | [tests/test_v2_t3.py](../tests/test_v2_t3.py)（18 用例） | Tracer 生命周期 / step 计时 / 禁用短路 / Schema / 端点注册 / router 挂载 |

#### 关键设计决策

1. **trace_enable=False 短路**：禁用时 Tracer.step() 不记录任何数据，零开销
2. **同步写入 PG**：V2 阶段简化为同步写入（短连接）；T12 阶段优化为异步
3. **trace 写入失败不影响业务**：`_flush_to_db()` 包裹 try/except，失败仅 warning
4. **V2 API 在 `/api/v2/` 独立前缀**：与 V1.5 `/api/v1/` 完全隔离，互不影响
5. **session trace 分页查询**：先查根步骤再 count 每条步骤数，避免大 join

#### 验证状态

- ✅ T3 单测 **18/18 通过**
- ✅ V1.5 全量回归 **557 passed + 6 skipped**（539 → 557，零回归）
- ✅ 集成验证（2026-06-17 v2_smoke 步骤 [6]）：trace 链含 query_rewrite → query_ner → graph_anchor → retrieve → build_context → generate → citation_parse 全 7 步

### T4 · Reranker 精排 ✅（2026-06-15）

#### 交付内容

| 子任务 | 实现位置 | 备注 |
|---|---|---|
| **T4.1 BaseReranker 抽象** | [app/rag/reranker.py](../app/rag/reranker.py)（`BaseReranker` ABC + `RerankResult` 数据类） | 统一精排接口 |
| **T4.1 NoopReranker** | reranker.py（`NoopReranker.rerank` → 原顺序 + score=1.0） | reranker_type=none 时启用，零开销 |
| **T4.1 LiteLLMReranker** | reranker.py（`LiteLLMReranker._do_rerank` + Semaphore(5) 并发限制 + 降级 `_fallback`） | 走 `litellm.arerank` API，支持 SiliconFlow/Cohere/Jina 格式 |
| **T4.1 兜底规则** | reranker.py（过滤后 < 3 时补到 3 条，score=0 标记） | PRD 兜底，避免高阈值场景空召回 |
| **T4.2 集成到 hybrid_retriever** | [app/rag/hybrid_retriever.py](../app/rag/hybrid_retriever.py)（取候选 `2*top_k` → reranker.rerank → score 覆盖） | 失败降级返回原序 |
| **T4.3 工厂函数** | reranker.py（`get_reranker()` 按 `reranker_type` 切换 Noop/LiteLLM） | 无 LiteLLM 配置时默认 Noop |

#### 关键设计决策

1. **`import litellm` 提到模块顶层**：原本写在方法内 import，导致单测 `patch("app.rag.reranker.litellm")` 找不到模块属性 → 移到顶层后 patch 生效
2. **NoopReranker 给 score=1.0 而非透传**：表达"不做精排，给满分表示信任原排序"语义；统一 RerankResult 数据格式，下游无需分支处理
3. **降级返回 score=0 标记**：API 失败 / 兜底补充时分数标 0，便于上层日志区分"真实精排结果"和"降级 fallback"
4. **Semaphore(5) 限并发**：避免 Reranker API 限流，特别是 SiliconFlow 免费档限流较紧
5. **过滤分数低于 similarity_threshold**：默认 0.3，防止"语义无关但向量相似"的 chunk 进入 LLM 上下文

#### 验证状态

- ✅ T4 单测 **10/10 通过**（覆盖 NoopReranker/LiteLLMReranker/工厂函数/降级/兜底）
- ✅ 端到端贯通验证：`hybrid_search → NoopReranker → 返回 1.0 分数`
- ✅ 集成验证（A.1 实验 B0/B1/B2 + 2026-06-17 v2_smoke 步骤 [8b]）：Qwen3-Reranker-8B 三组阈值实验全部跑通；smoke /v2/rerank 端点首条 rerank_score=0.9955；当前生产配置 RERANKER_TYPE=none（详见 [eval_a1_reranker_tuning.md](eval_a1_reranker_tuning.md)）

### T5 · Citation 注入 + 解析 ✅（2026-06-15）

#### 交付内容

| 子任务 | 实现位置 | 备注 |
|---|---|---|
| **T5.1 CHC-01 context 组装** | [app/rag/citation.py](../app/rag/citation.py)（`build_context_with_citation`） | 输出 `[1] 来源：xxx.pdf（第3页）\n内容：...` 格式 |
| **T5.1 system prompt 注入** | citation.py（`build_citation_system_prompt`） | 引导 LLM 用 `[N]` 标注来源 |
| **T5.2 CHC-02 解析** | citation.py（`parse_citations`） | 正则 `\[(\d+)\]` 抽取 + 去重 + 映射回 chunks |
| **T5.2 CitationItem 输出** | parse_citations 返回（chunk_id / document_name / page_number / heading_path / snippet / rerank_score） | snippet 取前 200 字符摘要 |

#### 关键设计决策

1. **Unicode 中文引号 `"` `"` 替代 ASCII `"`**：原本 system prompt 内 `"台风是热带气旋[1]"` 用 ASCII 双引号导致 Python 字符串提前闭合 → SyntaxError；统一用 U+201C / U+201D
2. **去重保编号顺序**：`parse_citations` 用 `seen` 集合 + 顺序列表，保证 `[1] [2] [1]` 输入只产出 1 个 [1] 引用项，但保留首次出现顺序
3. **越界编号忽略**：解析到 `[5]` 但 chunks 只有 3 条时，静默丢弃越界编号（LLM 偶尔会编造编号）
4. **未引用 chunk 不输出**：仅 LLM 实际引用的 chunks 出现在 source_citations 中，避免噪音
5. **docstring 转义 `\\d` 而非 `\d`**：避免 Python `DeprecationWarning: invalid escape sequence`

#### 验证状态

- ✅ T5 单测 **4/4 通过**（context 组装 / 引用解析 / 无引用 / 去重）
- ✅ 端到端贯通验证：LLM 答案 `[1]...[2]...[1]` → source_citations 准确映射回 chunk_id

### T6 · 统一查询接口 /v2/query ✅（2026-06-15）

#### 交付内容

| 子任务 | 实现位置 | 备注 |
|---|---|---|
| **T6.1 endpoint 骨架** | [app/api/v2/endpoints/query.py](../app/api/v2/endpoints/query.py)（`POST /api/v2/query`） | 串联 hybrid_search → build_context → LLM → parse_citations |
| **T6.1 Trace 埋点** | query.py（`Tracer` 包裹 + 4 个 step：retrieve/build_context/generate/citation_parse） | 每步骤自动写 agent_traces 表 |
| **T6.1 LLM 调用** | query.py（`_generate_answer` 调 `litellm.acompletion`） | temperature=0.3, max_tokens=2000 |
| **T6.1 兜底文案** | query.py（检索为空 / LLM 失败时返回友好文案 + trace_id） | 不抛异常，保证 API 稳定 |
| **T6.2 V2 schemas** | [app/schemas/v2/query.py](../app/schemas/v2/query.py)（`QueryRequest` / `QueryOptions` / `QueryResponse` / `CitationItem`） | top_k 嵌套在 options 内 |
| **T6.3 V2 router 挂载** | [app/api/v2/router.py](../app/api/v2/router.py) 追加 `query.router` | `/api/v2` 前缀，与 traces 端点并存 |

#### 关键设计决策

1. **top_k 放在 QueryOptions 嵌套字段而非顶层**：`QueryRequest.options.top_k` 而非 `QueryRequest.top_k`，将检索控制参数收口到 `options`，便于未来扩展 stream / reranker_enable / bm25_enable 等
2. **删除无用导入 `from app.services.chat_service import ChatService`**：T6 用的是 stream_chat 函数式 API，不存在 ChatService 类；遗漏的导入导致 `/v2/query` 路由注册失败
3. **Trace 包裹整个推理流程**：每步用 `tracer.step()` 上下文管理器自动计时，失败步骤的 step_output 也会被记录，便于排查
4. **检索空时不调 LLM**：直接返回兜底文案，节省 token；trace_id 仍透传便于追溯
5. **总耗时取整 ms**：`int((time.perf_counter() - start) * 1000)`，避免浮点小数干扰前端展示

#### 验证状态

- ✅ T6 单测 **6/6 通过**（4 个 schema 校验 + 2 个端点注册校验）
- ✅ 端到端贯通测试 **3/3 通过**（完整链路 + 检索空兜底 + LLM 失败兜底，新增于 [tests/test_v2_p1.py::TestV2QueryE2E](../tests/test_v2_p1.py)）
- ✅ 集成测试：POST `/api/v2/query` 三种模式（none/HyDE/multi_query + Graph RAG ON/OFF）均通过，4~8s 返回，citation 正确

### P1 阶段验收

- ✅ P1 单测 **23/23 通过**（T4 10 + T5 4 + T6 6 + 端到端贯通 3，单文件 [tests/test_v2_p1.py](../tests/test_v2_p1.py)）
- ✅ V1.5 全量回归 **580 passed + 6 skipped**（557 → 580，零回归）
- ✅ 修复 T4 集成时引入的 T2 老测试失败（`test_hybrid_search_bm25_enabled` 期望原始 score 0.95，实际 NoopReranker 覆盖为 1.0；调整断言+注释说明）

### T8 · Query 改写 + Query NER + 三层配置合并 ✅（2026-06-15）

#### 交付内容

| 子任务 | 实现位置 | 备注 |
|---|---|---|
| **T8.1 配置层扩展** | [app/core/config.py](../app/core/config.py)（V2.0 区段新增 6 字段：query_rewriter_model / query_rewrite_default / graph_rag_enable / multi_query_count / query_ner_timeout_s / graph_anchor_timeout_s） | 默认 graph_rag_enable=True / query_rewrite_default=none |
| **T8.1 错误码 40011** | [app/api/error_codes.py](../app/api/error_codes.py) `QUERY_REWRITE_INVALID = 40011` + [app/api/exceptions.py](../app/api/exceptions.py) HTTP 400 注册 | 与 PRD §1127 严格对齐 |
| **T8.1 Schema 扩展** | [app/schemas/v2/query.py](../app/schemas/v2/query.py)（QueryOptions 加 query_rewrite/enable_graph_rag/similarity_threshold；top_k 默认改 None；QueryResponse 加 rewritten_query/sub_queries/ner_entities/graph_anchored_tags 4 个 debug 字段） | VALID_QUERY_REWRITE 模块级常量供下游复用 |
| **T8.1 KB Schema/Service** | [app/schemas/knowledge_base.py](../app/schemas/knowledge_base.py) Update/Detail 加 retrieval_config + [app/services/kb_service.py](../app/services/kb_service.py) update_kb 接受 retrieval_config_was_set + [app/api/v1/endpoints/knowledge_bases.py](../app/api/v1/endpoints/knowledge_bases.py) endpoint 透传 | HRE-06 KB 级配置闭环 |
| **T8.2 三层合并函数** | [app/rag/retrieval_config.py](../app/rag/retrieval_config.py)（`ResolvedRetrievalOptions` + `resolve_options`） | API > KB > settings；KB JSONB query_rewrite 非法值同样抛 40011 |
| **T8.3 Query 改写器** | [app/rag/query_rewriter.py](../app/rag/query_rewriter.py)（`RewriteResult` + `rewrite_query` + 两套 prompt） | none 零 LLM 调用 / hyde 100~200 字假设答案 / multi_query JSON 解析 N 子查询 |
| **T8.4 Query NER + 图谱锚定** | [app/rag/query_ner.py](../app/rag/query_ner.py)（`extract_query_entities` + `anchor_to_graph` + `_truncate_utf8`） | 薄封装 [app/kg/ner.py](../app/kg/ner.py)；锚定走 max_hops=1 + Semaphore(5) + UTF-8 字节安全截断 |
| **T8.5 query.py 主链路** | [app/api/v2/endpoints/query.py](../app/api/v2/endpoints/query.py)（重写：load KB → resolve_options → query_rewrite → query_ner → graph_anchor → retrieve → build_context → generate → citation_parse；7 步 trace） | 新增 `_multi_query_search` RRF 二次融合 + `_do_retrieve` 策略分支 |
| **T8.6 单测** | [tests/test_v2_t8.py](../tests/test_v2_t8.py)（41 用例） | resolve_options 7 + rewrite 11 + extract_ner 4 + anchor 6 + RRF 3 + Schema 5 + E2E 5 |
| **P1 兼容修复** | [tests/test_v2_p1.py](../tests/test_v2_p1.py)（top_k 默认 None；3 个 E2E 用例补 mock NER/锚定/改写） | 23 → 23（零回归） |

#### 关键设计决策

1. **`query_rewrite` 校验放在 endpoint/resolve_options 入口，不放 Pydantic field_validator**：Pydantic 会把任何 validator 内异常重新打包成 `ValidationError`，最终走 `validation_exception_handler` 翻译成 `PARAM_INVALID(40001)`，无法落到 PRD 要求的 `QUERY_REWRITE_INVALID(40011)`。改在 `resolve_options` 用 `BusinessError(40011)` 显式拦截
2. **multi_query 用 RRF 二次融合而非 max-score**：沿用 T2 RRF 思想，N 路结果按 `chunk_id` 去重 + rank-based 重算分数 `score = Σ 1/(k + rank_i)`，k=60。同 chunk 多路命中分数累加，语义上"多角度都命中"的 chunk 排名更高
3. **Graph RAG 默认启用**：`graph_rag_enable=True`，Query 无实体或实体不在图谱时自动短路（`anchor_to_graph` 返 `[]` → 不传 entity_tags 给 hybrid_search），无副作用
4. **查询 NER 薄封装而非独立 prompt**：直接复用 [app/kg/ner.py](../app/kg/ner.py) `run_ner` + 加 `wait_for(query_ner_timeout_s)` 硬超时；后续切换轻量模型（如 `QUERY_REWRITER_MODEL`）只需切配置
5. **多 KB 时取第一个 KB 的 retrieval_config**：本期限制；后续可演进为 union/优先级/fan-out 策略
6. **HybridSearchResult 字段对齐**：`_multi_query_search` 重建 `HybridSearchResult` 时字段必须与 `__slots__` 严格匹配（chunk_id / content / document_id / score / entity_tags / heading_path / block_type / page_number / metadata / source_collection），不能引用 PRD 设计文档里 V2 schema 的 position_index/parent_chunk_id/is_summary（那些字段在 Milvus 但不在内存数据类）
7. **anchor_to_graph 把起点实体本身也加入 tags**：即使该实体在图谱里没有邻居，它本身也是有效的过滤标签（PRD §HRE-02 的"实体标签"包含起点）；query 失败时起点仍计入，确保最坏情况退化为"按 NER 实体过滤"而非完全无 tags

#### 验证状态

- ✅ T8 单测 **41/41 通过**（resolve_options / rewrite_query / extract_query_entities / anchor_to_graph / _multi_query_search / Schemas / 5 个端到端集成）
- ✅ V2 全套单测 **201/201 通过**（T0~T8 完整链路）
- ✅ 全量 mock 回归 **621 passed + 6 skipped**（580 → 621，T8 净增 41，零回归）
- ✅ 集成验证（2026-06-17 v2_smoke 步骤 [5a-5c] + [6]）：三种路径全部跑通；HyDE 改写 rewritten_query 长度 150 字；multi_query 生成 3 条子查询；trace step 顺序符合预期

### T7 · 表格描述 + 双层索引 + 文档元数据 ✅（2026-06-15）

#### 交付内容

| 子任务 | 实现位置 | 备注 |
|---|---|---|
| **T7.1 配置层扩展** | [app/core/config.py](../app/core/config.py)（V2.0 区段新增 5 字段：idp_llm_model / idp_dual_index_enable / idp_llm_timeout_s / idp_concurrency / idp_doc_meta_input_chars） | 默认 idp_dual_index_enable=True；与 KG_NER_MODEL 同款解耦 |
| **T7.1 Schema 暴露** | [app/schemas/kb_file.py](../app/schemas/kb_file.py)（FileListItem 加 summary_brief；FileDetail 加 summary_brief + doc_metadata） | PRD §282 文件列表展示摘要 |
| **T7.2 IDP-03 表格描述** | [app/ingest/table_description.py](../app/ingest/table_description.py)（`TableDescription` + `generate_table_descriptions` + 共享 `_resolve_idp_kwargs`） | Semaphore(idp_concurrency) + wait_for(idp_llm_timeout_s) + UTF-8 字节安全截断到 600 |
| **T7.3 IDP-04 双层索引** | [app/ingest/dual_layer.py](../app/ingest/dual_layer.py)（`group_by_parent_heading` + `generate_coarse_chunks`） | 按 `heading_path[:-1]` 聚合；空 heading 单独成组；摘要 ≤ 300 字 |
| **T7.4 IDP-05 文档元数据** | [app/ingest/doc_metadata.py](../app/ingest/doc_metadata.py)（`DocMetadata` + `extract_doc_metadata` + `_parse_metadata`） | 提取 doc_type/doc_date/language/key_topics/summary_brief；JSON 输出 + 围栏剥离 + 非法值置 None |
| **T7.5 主链路** | [app/tasks/ingest_task.py](../app/tasks/ingest_task.py)（`_step_table_description` / `_step_dual_layer_index` / `_step_doc_metadata` 替换三个 noop；`_main` 串联三类 chunk + 回填 parent_chunk_id；返回值新增 fine_chunk_count / table_description_count / coarse_chunk_count） | NER 仅对 fine_chunks 跑；td/coarse 补空 entities 列表对齐 zip 长度 |
| **T7.6 单测** | [tests/test_v2_t7.py](../tests/test_v2_t7.py)（33 用例） | TableDescription 6 + Group/CoarseChunk 7 + DocMetadata 9 + StepFunctions 7 + E2E 1 + Schema 3 |
| **T1 兼容修复** | [tests/test_v2_t1.py](../tests/test_v2_t1.py)（3 个 noop 测试改为存在性断言） | 老测试断言 noop 函数存在，T7 起替换为新名 |
| **ingest_task 兼容修复** | [tests/test_ingest_task.py](../tests/test_ingest_task.py)（patched_pipeline fixture 加 mock T7 三步） | 防真调 LLM；保持 happy path 行为不变 |

#### 关键设计决策

1. **三类 chunk 的 chunk_index 全局唯一**：fine 用 splitter 给的 0..N-1；table_description 从 `len(fine)` 起递增；coarse 从 `len(fine) + len(td)` 起递增。`_make_chunk_id_int(document_id, index)` SHA256 → INT64 保证幂等 upsert 不冲突
2. **parent_chunk_id 存 INT64 字符串而非 uuid**：Milvus 检索返回的是 INT64 chunk_id，做 `expr="parent_chunk_id == \"<int_str>\""` 子查询时直接命中；VARCHAR(64) 装 64-bit 整数字符串绰绰有余
3. **frozen StructuredChunk 用 `dataclasses.replace` 回填**：T1 把 StructuredChunk 设为 frozen 防止意外突变；T7 双层索引需要回填 fine 的 parent_chunk_id 时只能用 replace 重建副本
4. **NER 仅对 fine_chunks 跑**：粗粒度摘要 / 表格描述都是 LLM 合成的二次文本，不应抽出新实体；同时给 td/coarse 补空 `[]` 让 zip(chunks, chunk_entities) 对齐 `_step_milvus_write_v2` / `_step_neo4j_write` 的索引语义
5. **table_description chunk 的 is_summary=False**：它不是双层索引的粗粒度层，而是表格的"语义检索代理"；按 PRD §229-231 该字段保持 False
6. **粗粒度 chunk 的 block_type 统一为 paragraph**：摘要本质就是段落文本，统一类型简化下游 BM25 排除规则（PRD §1095 排除 table_description；is_summary 单独看字段）
7. **三步软失败原则**：IDP-03 单张表失败 → 不出 description；IDP-04 单组失败 → 不生成对应粗 chunk + 子 chunk 的 parent_chunk_id 保持 None；IDP-05 失败 → doc_metadata/summary_brief 留空。任一步骤失败都不阻断主链路（沿用 V1.5 NER 软失败模式）
8. **`_resolve_idp_kwargs` 三模块共享**：在 [table_description.py](../app/ingest/table_description.py) 定义，dual_layer / doc_metadata 通过 `from .table_description import _resolve_idp_kwargs` 复用，避免厂商前缀推断逻辑写三遍

#### 验证状态

- ✅ T7 单测 **33/33 通过**（含 1 个端到端联跑：fine + td + coarse 三类都进 Milvus）
- ✅ V2 全套单测 **234/234 通过**（T0~T8 完整链路）
- ✅ 全量 mock 回归 **654 passed + 6 skipped**（621 → 654，T7 净增 33，零回归）
- ✅ 集成验证（2026-06-17 v2_smoke 步骤 [3] + [3a] + [4]）：真 docx 入库 chunk_count=160（fine 154 + table_description 5 + coarse 1），block_types=[table_description, table, paragraph]；doc_metadata 自动识别 doc_type=报告 / language=zh / key_topics=5；summary_brief 自动生成；T7 单测 33/33 已覆盖三类 chunk 索引唯一与 parent_chunk_id 关联

### T9 · 置信度评分 + 答案自检 ✅（2026-06-15，**P2 阶段全部收尾**）

#### 交付内容

| 子任务 | 实现位置 | 备注 |
|---|---|---|
| **T9.1 配置层** | [app/core/config.py](../app/core/config.py)（V2.0 区段新增 3 字段：faithfulness_model / faithfulness_check_default / faithfulness_check_timeout_s） | 默认 False；与 KG_NER_MODEL / IDP_LLM_MODEL 同款解耦 |
| **T9.1 Schema 扩展** | [app/schemas/v2/query.py](../app/schemas/v2/query.py)（QueryOptions 加 enable_faithfulness_check；QueryResponse 加 confidence / low_confidence_warning / faithfulness_check / unverified_claims 4 字段） | confidence 用 ge/le 校验 [0, 1] |
| **T9.1 三层合并** | [app/rag/retrieval_config.py](../app/rag/retrieval_config.py)（ResolvedRetrievalOptions + resolve_options 增量 enable_faithfulness_check） | API > KB > settings 同款 |
| **T9.2 CHC-03 置信度** | [app/rag/confidence.py](../app/rag/confidence.py)（`ConfidenceScore` + `compute_confidence`） | 纯函数；PRD §540 公式；< 0.5 触发 PRD §556 警告文案 |
| **T9.3 CHC-04 自检** | [app/rag/faithfulness.py](../app/rag/faithfulness.py)（`FaithfulnessResult` + `check_faithfulness` + `append_unverified_warning` + `_parse_claims`） | LLM as Judge；JSON 数组/对象兼容；wait_for + 围栏剥离 + 软失败 skipped |
| **T9.4 主链路** | [app/api/v2/endpoints/query.py](../app/api/v2/endpoints/query.py)（Step 7 后插入 faithfulness_check + compute_confidence；检索空兜底分支也透 4 个新字段） | 自检 disabled 时不调 LLM；有 unverified 时 append_unverified_warning 改 answer |
| **T9.5 单测** | [tests/test_v2_t9.py](../tests/test_v2_t9.py)（37 用例） | compute_confidence 9 + parse_claims 7 + check_faithfulness 7 + append_warning 2 + resolve 4 + Schemas 3 + E2E 5 |

#### 关键设计决策

1. **`faithfulness_check` 字段三态**：`"ok"`（跑通）/ `"skipped"`（异常或超时软失败）/ `"disabled"`（开关关闭）—— 三态分开便于运维区分"用户没启用" vs "启用了但失败了"，PRD §586 风格
2. **unverified 在 answer 上用追加文本清单**：在原 answer 末尾加 `⚠ 以下事实未在检索内容中找到明确支撑：- claim1 - claim2`；不再调 LLM 二次插 † 标记，简单可靠零成本
3. **confidence 计算用算术平均**：PRD §540 "weighted_avg(rerank_scores)" 措辞模糊，选最简单的"被引用 chunk 等权平均"；breakdown 字段透出 weighted_score / coverage / penalty 三因子便于 trace 排查
4. **检索空兜底也透 4 个新字段**：`confidence=0.0`，`low_confidence_warning` 触发，`faithfulness_check` 按开关标 disabled/skipped；防止前端因字段缺失走异常分支
5. **`response_format` 不强制 json_object**：PRD 期望返回 JSON **数组**而非对象；多数模型不支持 array 类型的 response_format。改用 prompt 强制 + 围栏剥离 + 包装对象兜底（兼容 `{"claims": [...]}` 格式）
6. **`DISABLED_RESULT` 模块级常量**：避免每次 disabled 路径都 new 一个 FaithfulnessResult；与 query_rewriter 的 `_NOOP_RESULT` 同款轻量优化
7. **极小 confidence 归零**：`raw < 1e-9` 时返 0.0 而非 0.0000001，避免浮点噪音让前端展示古怪小数
8. **CHC-04 不复用 query_rewriter 的 `_resolve_kwargs`**：独立写一份避免循环依赖；同时自检需要 `temperature=0.1`（追求确定性）和 `num_retries=0`（失败软降级，不重试增加延迟），与改写器参数差异较大
9. **自检失败时 penalty=0.0 不惩罚 confidence**：PRD §586 "不影响主流程"原则；故意不让 skipped 退化为 1.0 惩罚

#### 验证状态

- ✅ T9 单测 **37/37 通过**（含 5 个端到端：disabled/全 supported/含 unverified/skipped/检索空）
- ✅ V2 全套单测 **271/271 通过**（T0~T9 完整链路）
- ✅ 全量 mock 回归 **691 passed + 6 skipped**（654 → 691，T9 净增 37，零回归）
- ✅ 集成验证（2026-06-17 v2_smoke 步骤 [5d]）：故意问伪事实 "这份文档发布于 2030 年 12 月，作者是谁？"，请求成功返回；偶发自检 LLM 软失败时优雅降级为 faithfulness_check=skipped（不阻断主链路，符合 PRD §586 设计）

#### V2.0 P2 阶段全部完成 🎉

| 阶段 | 状态 | 完成日期 |
|---|---|---|
| T7 IDP-03/04/05 | ✅ | 2026-06-15 |
| T8 HRE-01/02/06 | ✅ | 2026-06-15 |
| T9 CHC-03/04 | ✅ | 2026-06-15 |

剩余 T10/T11/T12 都属 P3+ 增强项。

### T10 · 分层子接口 ✅（2026-06-16）

#### 交付内容

| 子任务 | 实现位置 | 备注 |
|---|---|---|
| **T10.1 错误码 42201** | [app/api/error_codes.py](../app/api/error_codes.py) `CONTEXT_CHUNKS_EMPTY = 42201` + [app/api/exceptions.py](../app/api/exceptions.py) HTTP 422 映射 | PRD §1129：/v2/generate 接口 context_chunks 为空 |
| **T10.2 UQA-02 Retrieve Schema** | [app/schemas/v2/retrieve.py](../app/schemas/v2/retrieve.py)（RetrieveRequest / RetrieveChunkItem / RetrieveResponse） | 4 个分数字段（vector_score / bm25_score / rrf_score / rerank_score） |
| **T10.3 UQA-03 Generate Schema** | [app/schemas/v2/generate.py](../app/schemas/v2/generate.py)（ContextChunk / GenerateOptions / GenerateRequest / GenerateResponse） | context_chunks 至少 1 条；复用 CitationItem |
| **T10.4 UQA-04 Rerank Schema** | [app/schemas/v2/rerank.py](../app/schemas/v2/rerank.py)（RerankCandidate / RerankRequest / RerankResultItem / RerankResponse） | candidates 至少 1 条；按 rerank_score 降序 |
| **T10.5 UQA-02 纯检索端点** | [app/api/v2/endpoints/retrieve.py](../app/api/v2/endpoints/retrieve.py) `POST /api/v2/retrieve` | 只调 hybrid_search 不调 LLM；支持 Graph RAG / BM25 / Rerank 开关 |
| **T10.6 UQA-04 独立精排端点** | [app/api/v2/endpoints/rerank.py](../app/api/v2/endpoints/rerank.py) `POST /api/v2/rerank` | query + candidates → rerank_score 降序；降级返回原顺序 |
| **T10.7 UQA-03 纯生成端点** | [app/api/v2/endpoints/generate.py](../app/api/v2/endpoints/generate.py) `POST /api/v2/generate` | 自定义 context → LLM + Citation + 自检 + 置信度；不触发 Milvus / Neo4j |
| **T10.8 V2 Router 扩展** | [app/api/v2/router.py](../app/api/v2/router.py)（追加 retrieve / generate / rerank 三个路由） | 与 V2 现有 traces / query / evaluations 并存 |
| **T10.9 单测** | [tests/test_v2_t10.py](../tests/test_v2_t10.py)（34 用例） | 错误码 3 + Retrieve Schema 4 + Generate Schema 4 + Rerank Schema 4 + Retrieve 端点 5 + Rerank 端点 4 + Generate 端点 6 + E2E 4 |

#### 关键设计决策

1. **三个子接口完全独立端点 + Schema**：每个子接口有独立的请求/响应 Schema，不复用 /v2/query 的 QueryRequest/QueryResponse，因为语义差异大（Retrieve 不需要 LLM 参数，Generate 需要 context_chunks 而非 kb_ids，Rerank 需要 candidates）
2. **Retrieve 端点不调 LLM**：只走 hybrid_search 链路（含 Graph RAG / BM25 / Rerank），延迟目标 < 1s
3. **Generate 端点不触发检索**：context 由开发者传入，不 import hybrid_search / NER 等检索模块；自有 `_generate_answer` 函数支持 `enable_citation_prompt` 开关
4. **Generate 的 ContextChunk.source_label 映射到 document_name**：citation 模块用 `document_name` 作为来源标签；Generate 端点将 `source_label or chunk_id` 映射过去，保证 Citation 解析正确
5. **Rerank 端点的 index → id 映射**：reranker 返回的是候选列表中的 index（0-based），需要映射回 RerankCandidate.id（字符串标识）
6. **Rerank 降级策略**：reranker 调用失败时返回原顺序候选（分数标 0.0），与 hybrid_retriever 的降级策略一致
7. **Retrieve 的 mock 路径必须用端点模块级名称**：`patch("app.api.v2.endpoints.retrieve.hybrid_search")` 而非 `patch.object(hybrid_retriever, "hybrid_search")`，因为端点用 `from ... import` 绑定了模块级名称

#### 验证状态

- ✅ T10 单测 **34/34 通过**（错误码 + Schema + 端点 + E2E）
- ✅ V2 全套单测 **343/343 通过**（零回归；T0~T11 + P1 + T10）
- ✅ 集成验证（2026-06-17 v2_smoke 步骤 [8a/8b/8c]）：三个分层端点全部跑通；/retrieve 返 5 条 chunks 含多分数字段（latency 360ms）；/rerank 3 候选按 rerank_score 降序（latency 1808ms）；/generate 自定义 context 返 110 字 answer

### T12 · 聚合统计 ✅（2026-06-16）

#### 交付内容

| 子任务 | 实现位置 | 备注 |
|---|---|---|
| **T12.1 QueryAnalytics 模型** | [app/models/query_analytics.py](../app/models/query_analytics.py)（14 字段：trace_id / session_id / kb_id / total_latency_ms / confidence / low_confidence / graph_rag_triggered / bm25_contributed / faithfulness_check_triggered / total_tokens / react_steps / has_error / created_at） | 快照表，每次查询写一行 |
| **T12.1 模型注册** | [app/models/__init__.py](../app/models/__init__.py)（新增 QueryAnalytics） | lifespan create_all 自动建表 |
| **T12.2 Analytics Schema** | [app/schemas/v2/analytics.py](../app/schemas/v2/analytics.py)（ToolUsageStats / TokenConsumptionStats / AnalyticsResponse） | 7 个核心指标 + 时间范围 |
| **T12.3 快照写入辅助** | [app/observability/analytics_writer.py](../app/observability/analytics_writer.py)（`build_analytics_snapshot` 纯函数 + `write_analytics_snapshot` 异步写入） | 从 Tracer.steps 提取工具使用 bool / Token 数 / 步骤数 / 错误 |
| **T12.4 /v2/query 集成** | [app/api/v2/endpoints/query.py](../app/api/v2/endpoints/query.py)（正常出口 + 检索空兜底出口各调用一次 write_analytics_snapshot） | 在 Tracer 上下文内写入，失败仅 warning 不阻断 |
| **T12.5 GET /analytics 端点** | [app/api/v2/endpoints/analytics.py](../app/api/v2/endpoints/analytics.py) `GET /api/v2/analytics` | 单次 SQL 聚合查询；支持 start_date / end_date / kb_id 过滤 |
| **T12.6 V2 Router 扩展** | [app/api/v2/router.py](../app/api/v2/router.py)（追加 analytics 路由） | 与 V2 现有路由并存 |
| **T12.7 单测** | [tests/test_v2_t12.py](../tests/test_v2_t12.py)（14 用例） | ORM 模型 3 + Schema 3 + analytics_writer 4 + query 集成 2 + analytics 端点 3 |

#### 关键设计决策

1. **快照表而非实时聚合 agent_traces**：agent_traces 的 step_input/step_output 是 JSONB，从中聚合 confidence / tool_usage 性能差且复杂。新增 query_analytics 快照表，每次查询写一行扁平指标，SQL 聚合简单高效
2. **工具使用率用 bool + AVG**：`AVG(graph_rag_triggered)` = 触发率，无需存 JSONB。PRD 要求的 `tool_usage` 各字段为查询占比 [0, 1]，bool 列 + AVG 天然满足
3. **low_confidence 冗余 bool 列**：避免聚合时每行做 `confidence < 0.5` 浮点比较；`AVG(CASE WHEN low_confidence THEN 1.0 ELSE 0.0 END)` 直接算占比
4. **Token 简化为 total_tokens**：Tracer 只记录 token_count 总数，不区分 input/output。PRD 的 token_consumption.total_input / total_output 简化为 total_tokens，减少字段复杂度
5. **写入在 Tracer 上下文内**：需要访问 `tracer.trace_id` 和 `tracer.steps`，所以两个出口点（正常 + 检索空）的快照写入都在 `async with Tracer(...) as tracer:` 块内
6. **单次 SQL 聚合**：analytics 端点用一个 SELECT 语句完成所有 10 个聚合指标（COUNT / AVG / SUM / CASE WHEN），响应 < 500ms
7. **默认 7 天时间范围**：start_date 默认 end_date - 7 天，覆盖最近一周数据

#### 验证状态

- ✅ T12 单测 **14/14 通过**（ORM + Schema + Writer + Query 集成 + Analytics 端点）
- ✅ V2 全套单测 **357/357 通过**（零回归；T0~T12 + P1）
- ✅ 集成验证（2026-06-17 v2_smoke 步骤 [9]）：total_queries=4，avg_latency_ms=13797，tool_usage.bm25=1.000 / faithfulness=0.250 / graph_rag=0.000，error_rate=0.000；途中暴露并修复 analytics_writer 未 commit 的快照丢失 bug（详见下方 Bugfix 段）

### Bugfix · V2 query 超时卡死修复 ✅（2026-06-16）

> **根因**：V2 query 主链路多个 LLM / 外部 API 调用**缺少超时保护**，任一 API 响应慢或挂掉均会导致请求无限挂起（实测 300s+ 仍超时）。

#### 修复清单

| # | 位置 | 问题 | 修复 |
|---|------|------|------|
| 1 | [query.py `_generate_answer`](../app/api/v2/endpoints/query.py) | `litellm.acompletion()` 无 timeout / num_retries | 加 `timeout=settings.litellm_timeout` + `num_retries=settings.litellm_num_retries` + `asyncio.wait_for` 硬超时兜底 |
| 2 | [embedding.py `aembed_texts`](../app/rag/embedding.py) | `litellm.aembedding()` 无 timeout | 加 `timeout=settings.litellm_timeout` |
| 3 | [reranker.py `LiteLLMReranker`](../app/rag/reranker.py) | `litellm.arerank()` 无 timeout | 加 `timeout=self.timeout`（复用 litellm_timeout） |
| 4 | [hybrid_retriever.py `hybrid_search`](../app/rag/hybrid_retriever.py) | 同步 Milvus gRPC 调用阻塞事件循环 | `_search_single_collection` / `_fallback_dense_search` 改 `asyncio.to_thread()` |
| 5 | [hybrid_retriever.py `hybrid_search`](../app/rag/hybrid_retriever.py) | Reranker 无条件调用，忽略 `reranker_enable` 配置 | 新增 `reranker_enable` 参数；`False` 时跳过精排直接返回 |
| 6 | [query.py `v2_query`](../app/api/v2/endpoints/query.py) | 无整体请求超时保护 | 加 `asyncio.wait_for(timeout=settings.query_total_timeout_s)` 硬超时兜底 |
| 7 | [config.py](../app/core/config.py) | 缺 query 整体超时配置 | 新增 `query_total_timeout_s` 字段（默认 120s） |

#### 关键设计决策

1. **三层超时防护**：LiteLLM 内部 timeout → `asyncio.wait_for` 步骤级硬超时 → `v2_query` 整体硬超时。任一层触发均有友好兜底文案返回，不会让请求无限挂起
2. **`query_total_timeout_s=120s`**：覆盖 embedding(30s) + Milvus(5s) + LLM(60s) + 余量(25s)；可在 .env 中按实际 API 响应速度调整
3. **`asyncio.to_thread` 包装同步 Milvus 调用**：MilvusClient 是同步 gRPC，直接在 async 函数中调用会阻塞事件循环（其他并发请求也会卡住）；`to_thread` 把 gRPC 调用丢到线程池，不阻塞主循环
4. **`reranker_enable` 参数穿透**：从 `resolve_options` → `_do_retrieve` → `hybrid_search` 全链路传递；`False` 时跳过精排步骤直接返回原排序结果
5. **超时兜底文案保持一致风格**：与 T6 原有的"检索为空兜底"和"LLM 失败兜底"同款友好文案 + `confidence=0.0` + `faithfulness_check="skipped"`

#### 验证状态

- ✅ V2 全套单测 **253/253 通过**（T0~T9 + P1，零回归）
- ✅ 集成验证：POST `/api/v2/query` enable_graph_rag=False → 2s 返回；enable_graph_rag=True → 5s 返回；v2_smoke.py 全链路通过

### Hardening · 审查报告 A/B 第一批修复 🔧（2026-06-18）

> 输入来源：[docs/0617/code_quality_review_2026-06-17.md](0617/code_quality_review_2026-06-17.md) 与 [docs/0617/codex-review.md](0617/codex-review.md)。执行清单沉淀到 [docs/0617/xiugai.md](0617/xiugai.md)。

#### 交付内容

| 审查项 | 实现位置 | 修复内容 |
|---|---|---|
| A P0-2 | [app/tasks/ingest_task.py](../app/tasks/ingest_task.py) | Neo4j 写入软失败时保留 completed 主状态，但写入 `kb_files.doc_metadata._ingest_warnings.neo4j_failed=True`、错误类型、错误摘要与时间戳，区分“无实体”和“图谱写入失败” |
| A P0-3 | [app/tasks/ingest_task.py](../app/tasks/ingest_task.py) | `_mark_failed_safe()` 在标记 failed 前尽力按 file_id 清理任务级 Milvus chunks 与 Neo4j Document，降低 Milvus 写入后 PG 失败造成永久孤岛的概率 |
| A P0-4 / P1-5 | [app/core/async_utils.py](../app/core/async_utils.py)、[app/api/v2/endpoints/query.py](../app/api/v2/endpoints/query.py)、[app/rag/query_ner.py](../app/rag/query_ner.py)、[app/ingest/table_description.py](../app/ingest/table_description.py)、[app/ingest/dual_layer.py](../app/ingest/dual_layer.py)、[app/tasks/ingest_task.py](../app/tasks/ingest_task.py) | 新增 `wait_for_named()` / `gather_with_timeout()`；5 个生产 `asyncio.gather` 调用点增加整组硬超时，并在 IDP / NER / graph anchor / multi_query 场景保持软降级语义 |
| A P1-6 | [app/tasks/ingest_task.py](../app/tasks/ingest_task.py) | Step 8 Milvus 同步 gRPC 写入改为 `asyncio.to_thread()`，避免大文件入库阻塞事件循环 |
| A P1-7 | [app/tasks/session_task.py](../app/tasks/session_task.py)、[app/kg/ner.py](../app/kg/ner.py)、[app/llm/client.py](../app/llm/client.py) | 标题/摘要任务、KG NER、统一 LLM client 的 `acompletion`/`astream` 增加外层 `asyncio.wait_for`；流式输出对首包和每个 chunk 都做硬超时保护 |
| A P1-10 | [app/rag/milvus_client.py](../app/rag/milvus_client.py)、[app/tasks/ingest_task.py](../app/tasks/ingest_task.py) | `create_v2_kb_collection(..., client=...)` 支持显式任务级 MilvusClient，移除入库自愈时临时替换模块级 `_client` 的共享状态风险 |
| B H-01 / H-06 / H-07 | [app/rag/reranker.py](../app/rag/reranker.py)、[app/api/v2/endpoints/traces.py](../app/api/v2/endpoints/traces.py)、[app/rag/retriever.py](../app/rag/retriever.py) | 沿用 batch1 已完成修复：NoopReranker 保留原始 score；trace 不存在走 BusinessError；Milvus filter 字符串转义 |
| 本地联调发现 | [app/api/v2/endpoints/analytics.py](../app/api/v2/endpoints/analytics.py)、[app/schemas/v2/generate.py](../app/schemas/v2/generate.py) | `/api/v2/analytics` 改为 `ApiResponse[AnalyticsResponse]` 统一包装；`/api/v2/generate` 空 `context_chunks` 改由 endpoint 返回 `42201 CONTEXT_CHUNKS_EMPTY`，避免被 Pydantic 先拦截成通用 `40001` |

#### 关键设计决策

1. **软失败仍保持业务可用，但必须可观测**：Neo4j 写入失败不阻断 Milvus 主链路，但通过 `_ingest_warnings` 标记降级，前端/运维可提示“图谱未完成”。
2. **失败补偿采用尽力清理**：跨 PG / Milvus / Neo4j 无分布式事务，失败路径只做幂等 best-effort cleanup；清理失败写 warning，不覆盖原始失败状态。
3. **整组超时不改变既有软降级契约**：IDP 表格描述、双层索引、NER、图谱锚定超时后返回空产物；multi_query 整组超时返回空检索结果，由上层沿用检索为空兜底。
4. **任务级 MilvusClient 不再污染全局单例**：FastAPI lifespan 全局 client 与 Celery task_resources client 分离，V2 Collection 自愈通过显式参数传递。

#### 验证状态

- ✅ 语法级验证：`python -m compileall app/core/async_utils.py app/tasks/ingest_task.py app/ingest/dual_layer.py app/ingest/table_description.py app/rag/query_ner.py app/api/v2/endpoints/query.py app/tasks/session_task.py app/kg/ner.py app/llm/client.py app/rag/milvus_client.py tests/test_async_utils.py tests/test_ingest_task.py tests/test_rag_retriever.py tests/test_v2_p1.py tests/test_v2_t3.py tests/test_v2_t0.py` 通过；追加 `python -m compileall app/api/v2/endpoints/analytics.py app/schemas/v2/generate.py tests/test_v2_t10.py tests/test_v2_t12.py` 通过。
- ✅ 本地服务轻量复测：`GET /health`、`OPTIONS /api/v2/query` CORS、`POST /api/v2/query` 非法 query_rewrite、`GET /api/v2/traces/not-exist-trace-id`、`GET /api/v1/knowledge-bases` 均通过；追加复测确认 `GET /api/v2/analytics` 返回 `code=0/message=success/data` 包装，`POST /api/v2/generate` 空/缺省 `context_chunks` 均返回 HTTP 422 + 业务码 `42201`。
- ✅ 用户手动回归：`pytest tests/test_async_utils.py tests/test_ingest_task.py tests/test_rag_retriever.py tests/test_v2_p1.py tests/test_v2_t3.py tests/test_v2_t0.py tests/test_v2_t10.py tests/test_v2_t12.py` → **191 passed, 1 skipped, 4 warnings in 11.94s**。其中 3 条 `AsyncMockMixin._execute_mock_call was never awaited` 来自 `tests/test_v2_p1.py` mock_db 形态，已调整为 `MagicMock.add + AsyncMock.commit/rollback` 以消除测试警告；1 条 asyncpg `_cancel` unawaited 属跳过真 PG 集成测试环境清理警告，功能用例均通过。

### T11 · RAGAS 评估 ✅（2026-06-16）

#### 交付内容

| 子任务 | 实现位置 | 备注 |
|---|---|---|
| **T11.1 配置项扩展** | [app/core/config.py](../app/core/config.py)（V2.0 区段新增 4 字段：eval_llm_model / eval_max_questions / eval_concurrency / eval_question_timeout_s） | 默认上限 100 题 + 单题超时 60s |
| **T11.1 Eval Schema** | [app/schemas/v2/eval.py](../app/schemas/v2/eval.py)（EvalQAItem / EvalCreateRequest / EvalRetrievalOptions / EvalCreateResponse / EvalSummary / EvalDetailItem / EvalDetailResponse / EvalListItem / EvalListResponse） | 4 项指标 + overall_score；[0,1] 范围校验 |
| **T11.1 错误码 40012/40013** | [app/api/error_codes.py](../app/api/error_codes.py)（EVAL_DATASET_EMPTY = 40012 / EVAL_DATASET_TOO_LARGE = 40013） + [app/api/exceptions.py](../app/api/exceptions.py) HTTP 400 注册 | 紧跟 query_rewrite 的 40011 |
| **T11.2 generate_answer public 化** | [app/api/v2/endpoints/query.py](../app/api/v2/endpoints/query.py)（`_generate_answer → generate_answer`） | 仅函数改名，主链路逻辑零改动；让 eval_runner 能 import |
| **T11.2 单题 RAG 执行器** | [app/rag/eval_runner.py](../app/rag/eval_runner.py)（`run_single_query_for_eval`） | 复用 hybrid_search + build_context + generate_answer；不写 Trace；不调 faithfulness_check（ragas 自己跑）；强制 single 路径不开 multi_query |
| **T11.3 RAGAS 评估管道** | [app/rag/ragas_evaluator.py](../app/rag/ragas_evaluator.py)（`evaluate_with_ragas` + `_to_float_or_none` + `_compute_overall` + `_build_evaluator_llm` + `_build_evaluator_embeddings`） | ragas 0.2+ API：SingleTurnSample（user_input/retrieved_contexts/response/reference）+ Faithfulness/AnswerRelevancy/ContextPrecision/ContextRecall；LiteLLM 经 LangChain ChatOpenAI(base_url=) 适配 |
| **T11.4 Celery 评估任务** | [app/tasks/eval_task.py](../app/tasks/eval_task.py)（`run_evaluation_task` 同步壳 + `_run_evaluation_main` async + `_resolve_eval_llm_kwargs` + `_mark_failed_safe`） | 范式严格对齐 session_task：asyncio.run + task_resources；progress 5→90→95→100；单题超时软降级 |
| **T11.4 任务注册** | [app/tasks/__init__.py](../app/tasks/__init__.py) + [app/tasks/celery_app.py](../app/tasks/celery_app.py) `_TASK_MODULES` 追加 `app.tasks.eval_task` | worker 启动时自动 import |
| **T11.5 EVA-01/02/03 端点** | [app/api/v2/endpoints/evaluations.py](../app/api/v2/endpoints/evaluations.py)（`create_evaluation` POST /evaluate + `get_evaluation` GET /{id} + `list_evaluations` GET 列表 + `_extract_summary` / `_extract_details` / `_row_to_detail` 辅助） | PRD §777-863 完整对齐；3 个端点均挂在 /api/v2/knowledge-bases/{kb_id}/... 下 |
| **T11.5 V2 router 挂载** | [app/api/v2/router.py](../app/api/v2/router.py)（追加 `evaluations.create_router` + `evaluations.router`） | 两个 router 区分 POST `/evaluate` 与 GET `/evaluations[/{id}]` 路径 |
| **T11.6 单测** | [tests/test_v2_t11.py](../tests/test_v2_t11.py)（34 用例） | Schemas 4 + ragas helpers 3 + evaluate_with_ragas 4 + eval_runner 4 + resolve_kwargs 4 + Celery 主流程 4 + API endpoints 8 + router 注册 2 + 错误码 1 |

#### 关键设计决策

1. **不绕 HTTP 调 /v2/query**：评估 worker 与 uvicorn 不在同一进程，httpx 调用要求 worker 能解析到 host:port，部署复杂。直接 import `hybrid_search` / `generate_answer` 等内部函数，零网络依赖
2. **`_generate_answer → generate_answer` 只改名不改语义**：让 eval_runner 能 public import；query.py 主链路其余逻辑零变动，T6/T9 已过的测试零回归
3. **eval_runner 不写 Trace**：评估场景每题写 ~7 条 step 会污染 agent_traces 表（100 题 = 700 行），且 ragas 自身有 trace；评估期主动跳过 `Tracer` 上下文管理器
4. **eval_runner 不调 faithfulness_check**：T9 的 `check_faithfulness` 是单题实时自检；ragas 的 Faithfulness 指标用更标准的算法（claim 拆解 + verification），重复跑浪费 LLM 调用
5. **multi_query 评估期禁用**：multi_query 每题烧 2~4 次 LLM 改写 token，性价比差；评估期强制 single 路径用 rewritten_text 或原 query
6. **ragas LLM/Embedding 经 LangChain 适配**：`LangchainLLMWrapper(ChatOpenAI(base_url=...))`；LiteLLM 完全兼容 OpenAI 协议，无需 ragas 自定义 wrapper。embedding 同款 `LangchainEmbeddingsWrapper(OpenAIEmbeddings)`
7. **ragas 模块懒加载**：`from ragas import evaluate` 推迟到 `evaluate_with_ragas` 函数内；环境无 ragas（或 ragas 内部 import 链路坏）→ 整批返 summary 全 None + error 字段，不阻断 EvalTask 落库
8. **NaN / Inf → None**：ragas 单题失败返 NaN；用 `_to_float_or_none` 清洗后写 JSONB，避免 PG 序列化 NaN 报错（PG JSONB 不接受 NaN）
9. **eval_dataset 完整存 JSONB**：question + ground_truth 原样保留；评估期生成的 answer + contexts 也写入 `eval_result.samples`，便于复跑指标 / 调试
10. **超 100 题硬拒绝不偷偷截断**：返 40013 让前端明确知道；保留 `EVAL_MAX_QUESTIONS` 配置可调（最大 500）
11. **Celery 调度失败映射 50300**：复用现有 `CELERY_UNAVAILABLE`（V1.5 文件入库管道同款），HTTP 503 + 友好文案
12. **EvalTask 落库再 .delay**：先 commit 拿 id 再投递任务；即使 Celery 不可达，行已在表里，可手工触发 / 重启 worker 后捞起
13. **summary 写回字段越界自动归 None**：`_extract_summary` 拿 JSONB 里的脏数据时用 `0.0 ≤ f ≤ 1.0` 区间检查 + 非数置 None；前端永远拿到合法 [0,1] 或 None，不会崩

#### 验证状态

- ✅ T11 单测 **38/38 通过**（原 34 + A.1 调优 4 = 38）
- ✅ V2 全套单测 **309/309 通过**（T0~T9 + Bugfix + T11 = 305 → 309，A.1 净增 4，零回归）
- ✅ 全量 mock 回归 **709 passed + 40 skipped**（V1.5 + V2 全部，零回归；test_kb_service.py 的 10 个 ERROR 在 HEAD 自带，与 T11 无关）
- ✅ 已安装 ragas（A.1 实验前置完成）
- ✅ 集成验证（A.1 实验 4 组实测）：4 组实验各跑完整 RAGAS 评估 → status pending→processing→completed，summary 4 项指标 [0,1]，详见 [eval_a1_reranker_tuning.md](eval_a1_reranker_tuning.md)

### A.1 · Reranker 调优工具链 🔧（2026-06-16）

> T11 评估实验暴露核心问题：Reranker 开启后 overall -0.231（主因 context_precision 0.591→0.218），
> 怀疑 `RERANKER_SIMILARITY_THRESHOLD=0.3` 过滤了优质 chunk。A.1 目标是通过参数对比实验找到最优阈值。

#### 交付内容

| 子任务 | 实现位置 | 备注 |
|---|---|---|
| **similarity_threshold 运行时覆盖** | [app/rag/reranker.py](../app/rag/reranker.py)（SiliconFlowReranker / LiteLLMReranker 构造函数加 `similarity_threshold` 可选参数；`get_reranker()` 同款） | 不改 .env 就能跑不同阈值的对比实验 |
| **hybrid_search 透传** | [app/rag/hybrid_retriever.py](../app/rag/hybrid_retriever.py)（`hybrid_search` 加 `similarity_threshold` 参数 → `get_reranker(similarity_threshold=...)`） | 新参数默认 None，不传时走 reranker 实例默认值 |
| **query.py 主链路透传** | [app/api/v2/endpoints/query.py](../app/api/v2/endpoints/query.py)（`_do_retrieve` + `_multi_query_search` 透传 `resolved.similarity_threshold`） | 三层合并后 threshold → hybrid_search → reranker 闭环 |
| **eval_runner 透传** | [app/rag/eval_runner.py](../app/rag/eval_runner.py)（`resolved.similarity_threshold` 传给 `hybrid_search`） | 评估任务的 retrieval_options.similarity_threshold 全链路生效 |
| **评估对比脚本** | [scripts/eval_compare.py](../scripts/eval_compare.py)（预定义 4 组实验：baseline / thresh_0.3 / thresh_0.1 / thresh_0.0；串行跑评估 + 轮询 + 对比报告） | A.1 调优核心工具；后续 A.2/A.3 可复用 |
| **单测** | [tests/test_v2_t11.py](../tests/test_v2_t11.py)（4 用例：eval_runner 透传 / reranker 覆盖 / reranker 回落 / get_reranker 透传） | T11 单测 34 → 38 |

#### 预定义实验配置

| 实验名 | 说明 | retrieval_options |
|---|---|---|
| `baseline` | 无 Reranker（参照组） | reranker_enable=false |
| `rerank_thresh_0.3` | 当前默认阈值 | reranker_enable=true, similarity_threshold=0.3 |
| `rerank_thresh_0.1` | 降低阈值 | reranker_enable=true, similarity_threshold=0.1 |
| `rerank_thresh_0.0` | 不过滤，纯精排 | reranker_enable=true, similarity_threshold=0.0 |

#### 关键设计决策

1. **`similarity_threshold=None` 表示"不覆盖"**：传 None 时 reranker 用 settings 全局值；传 0.0 时显式设"不过滤"。区分两种语义（"不指定"vs"设为 0"）
2. **不新增 Settings 字段**：threshold 覆盖是运行时行为（评估实验），不需要持久化到 .env。`resolve_options` 三层合并已支持 API 层 `options.similarity_threshold` 透传
3. **`get_reranker()` 每次创建新实例**：工厂函数每次调都 new 实例，使得不同请求可以用不同 threshold。当前无性能瓶颈（reranker 实例轻量，核心开销在 HTTP 调用）
4. **eval_compare.py 走 HTTP API 而非直接 import**：避免对 worker 进程的网络依赖（worker 在独立进程）；同时复用了完整评估链路（Celery + DB + Milvus），结果更可信

#### 验证状态

- ✅ A.1 单测 **4/4 通过**（similarity_threshold 透传全链路）
- ✅ V2 全套单测 **309/309 通过**（零回归）
- ✅ 集成验证：4 组实验全部跑完，结果见 [eval_a1_reranker_tuning.md](eval_a1_reranker_tuning.md)

#### A.1 实验结果摘要

| 实验 | faithfulness | answer_relevancy | context_precision | context_recall | overall_score |
|---|---|---|---|---|---|
| **A1 baseline** (无 reranker) | 0.533 | **0.423** | 0.559 | **0.367** | **0.471** |
| B0 reranker + thresh=0.3 | 0.307 | 0.190 | 0.167 | 0.067 | 0.183 |
| B1 reranker + thresh=0.1 | **0.673** | 0.230 | 0.533 | 0.217 | 0.413 |
| B2 reranker + thresh=0.0 | 0.285 | 0.234 | **0.647** | 0.317 | 0.370 |

**结论**：当前 Reranker 模型（Qwen3-Reranker-8B）弊大于利。即使 threshold=0.0（纯精排不过滤），overall 仍比 baseline 低 -0.100。**生产环境推荐 `RERANKER_TYPE=none`**。下一步：切换 bge-reranker-v2-m3 重测，或增加文档量后重测。

---

## V1.5 · S0 基础设施 ✅

### 交付内容

| 模块 | 实现位置 | 备注 |
|---|---|---|
| **配置项扩展**（Redis / Celery / 上传 / 上下文窗口 / 标题摘要 LLM） | [app/core/config.py](../app/core/config.py)（新增 7 字段 + 2 derived property） | 含 broker/backend 缺省复用 redis_url 的兜底逻辑 |
| **依赖清单** | [requirements.txt](../requirements.txt) | 新增 celery / redis / pymupdf / python-docx / unstructured / markdown-it-py / langchain-text-splitters / tiktoken / python-multipart |
| **PG 模型**：`ChatSession` 扩 5 字段 | [app/models/session.py](../app/models/session.py) | title / summary / summarized_at / updated_at / message_count |
| **PG 模型**：`KnowledgeBase` 新表 | [app/models/knowledge_base.py](../app/models/knowledge_base.py) | 10 字段 + 3 check 约束 + name 唯一 |
| **PG 模型**：`KbFile` 新表 | [app/models/kb_file.py](../app/models/kb_file.py) | 14 字段 + kb_id 外键级联 |
| **Celery app** | [app/tasks/celery_app.py](../app/tasks/celery_app.py) | acks_late=True / prefetch=1 / json-only / 中国时区 |
| **ping_task** | [app/tasks/ping.py](../app/tasks/ping.py) | smoke 用，单测覆盖 |
| **Redis 服务** | [docker-compose/docker-compose.yml](../docker-compose/docker-compose.yml) | redis:7-alpine + AOF + 持久化挂 d:/dockerVolumes/redis/data |
| **`.env.example`** | [.env.example](../.env.example) | V1.5 块含 Redis / Celery / 上传 / 上下文 / 标题摘要 LLM / NER 备选模型注释 |
| **Celery 开发指南** | [docs/celery_dev_guide.md](celery_dev_guide.md) | Windows pool=solo / Linux prefork / smoke 命令 / 排错 |

### 关键设计决策

1. **不写迁移脚本**：用户确认 DB 可清空，靠 `Base.metadata.create_all` + 一次性清库命令；后续真有需要再上 Alembic
2. **Celery broker/backend 缺省复用 REDIS_URL**：`Settings.effective_celery_broker_url` 是 derived property，避免业务层散落 `or` 兜底
3. **task_acks_late + prefetch_multiplier=1**：PRD TASK-01 的可靠性硬要求；worker 异常时任务重入队、防 OOM 阻塞队列
4. **task_serializer=json**：禁 pickle（RCE 风险），跨语言友好
5. **task_time_limit=30min / soft=25min**：兜底防文件解析挂死；单任务可装饰器覆盖
6. **`_TASK_MODULES` 显式列表**：worker 启动时按列表 import，新任务必须在这里注册
7. **`KbFile.knowledge_base` 关系用 `lazy="raise"`**：防 N+1，强制业务层显式 selectinload

### 单测

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| [tests/test_v1_5_models.py](../tests/test_v1_5_models.py) | 20 | ChatSession 扩展字段 / KB 表字段+约束+默认值 / KbFile 字段+外键级联+枚举完备性 |
| [tests/test_v1_5_settings.py](../tests/test_v1_5_settings.py) | 11 | Redis URL / Celery broker 缺省+覆盖 / 上传配置 / 上下文窗口 / 标题摘要 LLM |
| [tests/test_celery_app.py](../tests/test_celery_app.py) | 13 | Celery 配置项 / 任务注册 / broker 覆盖 / ping_task 三种调用方式（eager 模式） |

### 验证状态

- ✅ V1.5 模型 + 配置单测 **31/31 通过**（不依赖 Celery）
- ✅ Celery 单测 **11/11 通过**（用 `task_always_eager` 模式，不连真 Redis）
- ✅ V1.0 全量测试 **127 passed + 6 skipped**（零回归；skipped 为 DB 集成测试，等用户配 TEST_DATABASE_URL）
- ✅ **端到端联调 smoke 通过**（2026-06-11）：
  - uvicorn 启动日志显示 "数据库表初始化完成" + Milvus / Neo4j 连接 OK
  - `celery worker --pool=solo` 启动成功，`[tasks]` 段含 `app.tasks.ping.ping_task`
  - `ping_task.delay('hello-S0').get(timeout=5)` 返回 `pong: hello-S0 @ lvjinhu`，链路全通

### 联调阶段关键经验（已写入项目记忆 + 文档）

1. **Windows + Docker Desktop 必须用 `127.0.0.1` 不用 `localhost`**
   - 现象：`Test-NetConnection localhost -Port 6379` 显示 `RemoteAddress: ::1` + `TcpTestSucceeded: True`，但 `redis-cli PING` 永远等不到响应，worker 卡在 `[tasks]` 段下不动
   - 根因：Windows 解析 `localhost` 优先 IPv6 `::1`，vpnkit 对 IPv6→容器 的端口转发常丢应用层包
   - 修复：`Settings.redis_url` 默认值已固化为 `redis://127.0.0.1:6379/0`，`.env.example` 与 `docs/celery_dev_guide.md` 加警示
2. **`from-import` 遮蔽子模块的坑**：`app/tasks/__init__.py` 写 `from app.tasks.celery_app import celery_app` 会让 `app.tasks.celery_app` 这个名字被 Celery 实例对象遮蔽，单测里要 `importlib.reload` 必须从 `sys.modules["app.tasks.celery_app"]` 拿真模块对象
3. **broker 连接重试限制**：`broker_connection_max_retries=3` + `broker_connection_timeout=4`，避免 Redis 不通时 `.delay()` 无限卡死

### S0 终验收命令汇总（用户已执行）

```bash
# 装依赖
uv pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
# 起 Redis
cd docker-compose && docker compose up -d redis
# Celery 单测
pytest tests/test_celery_app.py -v          # 11 passed
# 清库 + 起 uvicorn 建表
psql -U postgres -c "DROP DATABASE IF EXISTS tyagent; CREATE DATABASE tyagent;"
uvicorn app.main:app --reload                # 看 "数据库表初始化完成"
# 起 worker
celery -A app.tasks.celery_app worker --pool=solo -l info
# smoke
python -c "from app.tasks import ping_task; print(ping_task.delay('hello-S0').get(timeout=5))"
# → pong: hello-S0 @ lvjinhu
```

---

> **PRD 路线变更（2026-06-10）**：新版 PRD 把存储架构由"PostgreSQL + pgvector"
> 调整为"PostgreSQL（会话/消息）+ Milvus（向量切片）+ Neo4j（知识图谱）"三库协同。
> 已完成的 3.1–3.4 模块**不受影响**；3.5 整段重写为 Milvus 版；新增 3.6 知识图谱模块。
> PostgreSQL 中**不再保留** `knowledge_chunks` 表，原 `app/models/knowledge.py` 已删除。

---

## 3.1 接入与通信模块 ✅（V1.0）

### 交付内容

| 需求 ID | 实现位置 | 状态 |
|---|---|---|
| **API-01** 会话创建 `POST /api/v1/sessions` | [app/api/v1/endpoints/sessions.py](../app/api/v1/endpoints/sessions.py) | ✅ |
| **API-02** 流式对话 `POST /api/v1/chat/stream`（SSE） | [app/api/v1/endpoints/chat.py](../app/api/v1/endpoints/chat.py) | ✅ |
| **API-03** SSE 双通道（`event: message` / `event: control`） | 同上 | ✅ |

### 工程基础设施

- 配置层：[app/core/config.py](../app/core/config.py)（pydantic-settings + `.env`，含 LRU 缓存）
- 数据库层：[app/db/session.py](../app/db/session.py)（SQLAlchemy 2.0 async + asyncpg + PostgreSQL 17.10）
  - 关键修复：`connect_args={"ssl": False}` 解决 Windows 上 asyncpg SSL 探测的 `[WinError 121]` 信号灯超时问题
- ORM 模型：[app/models/](../app/models/) — V1.0 PostgreSQL 只保留 `chat_sessions` / `chat_messages` 两张表。
  按新版 PRD，知识切片由 Milvus 管理（详见 3.5），原 pgvector 占位 `KnowledgeChunk` 模型已删除。
- Service 层：[app/services/](../app/services/)（API ↔ Agent 胶水层）
- FastAPI lifespan：启动建表 / 关闭释放连接池

### 测试

- `tests/test_sessions_api.py` — 3 用例
- `tests/test_chat_stream.py` — 3 用例（含 SSE 解析器对 `\r\n` 帧分隔的适配）

### 关键架构契约

- **`agent.runner.run_stream()` 是 Agent ↔ Service 之间的唯一接口** —— 后续模块（3.3 LangGraph）替换内核时，API/Service 层无需任何改动。

---

## 3.2 LLM 路由模块 ✅

### 交付内容

| 需求 ID | 实现位置 | 状态 |
|---|---|---|
| **LLM-01** LiteLLM 集成，仅改 `.env` 切换厂商 | [app/llm/client.py](../app/llm/client.py) | ✅ |
| **LLM-02** Function Calling，模型按 JSON 输出 `tool_calls` | 同上 + [scripts/llm_smoke.py](../scripts/llm_smoke.py) | ✅ |

### 文件清单

- [app/core/config.py](../app/core/config.py) — Settings 新增 6 个 LLM 字段（`litellm_model` / `_api_key` / `_api_base` / `_timeout` / `_num_retries`）
- [app/llm/messages.py](../app/llm/messages.py) — OpenAI 兼容消息构造器（user/system/assistant/tool_result）+ `define_tool`
- [app/llm/client.py](../app/llm/client.py) — `acompletion` / `astream`，统一返回 dict
- [tests/test_llm_client.py](../tests/test_llm_client.py) — 6 个 mock 单测
- [scripts/llm_smoke.py](../scripts/llm_smoke.py) — 真 LLM 联调脚本

### 关键设计

1. **`_to_dict()` 兜底**：LiteLLM 返回的是 OpenAI Pydantic 对象（非裸 dict），在 client 层统一 `model_dump()` 为 dict，下游零感知
2. **模型名自动补前缀**：`.env` 写 `deepseek-v4-pro` 或 `deepseek/deepseek-v4-pro` 都能跑（按 api_base 推断厂商）

### 验证结果

- 真 DeepSeek 联调：3 项测试（纯文本 / Function Calling / 流式）全部通过

---

## 3.3 Agent 编排引擎（LangGraph ReAct）✅

### 交付内容

| 需求 ID | 实现位置 | 状态 |
|---|---|---|
| **AGT-01** `AgentState` 定义，图编译无报错 | [app/agent/state.py](../app/agent/state.py) + `tests/test_agent_graph.py::test_graph_compiles_without_error` | ✅ |
| **AGT-02** `Thought → Action → Observation` 循环 | [app/agent/graph.py](../app/agent/graph.py) + 联调脚本用例 2、3 | ✅ |
| **AGT-03** `max_iterations = 5` 死循环熔断 | [app/agent/nodes.py](../app/agent/nodes.py)::`make_call_model_node` | ✅ |
| **AGT-04** Tool 异常→堆栈作为 `ToolMessage` 回传 | [app/agent/nodes.py](../app/agent/nodes.py)::`tool_node` | ✅ |
| **TOL-02** `mock_weather_parser`（保留作为一种 dummy 测试工具） | [app/tools/weather_parser.py](../app/tools/weather_parser.py) | ✅ |

### 文件清单

- [app/agent/state.py](../app/agent/state.py) — `AgentState(TypedDict)` 含 `messages` + `remaining_iterations`
- [app/agent/nodes.py](../app/agent/nodes.py) — `call_model_node` / `tool_node` / `should_continue`
- [app/agent/graph.py](../app/agent/graph.py) — LangGraph 图构建 + `ChatOpenAI` 初始化（指向 DeepSeek）
- [app/agent/runner.py](../app/agent/runner.py) — **重写**：调用图 + 翻译流事件为 `AgentEvent`
- [app/tools/__init__.py](../app/tools/__init__.py) — 工具注册中心（`get_tools()` / `get_tool_map()`）
- [app/tools/weather_parser.py](../app/tools/weather_parser.py) — mock 气象数据工具（保留，TOL-02 的一种实现）
- [app/services/chat_service.py](../app/services/chat_service.py) — 增加 `_load_history` 加载 DB 历史并传给 runner
- [tests/test_tools.py](../tests/test_tools.py) — 3 用例
- [tests/test_agent_runner.py](../tests/test_agent_runner.py) — 7 用例（mock graph 验证翻译逻辑）
- [tests/test_agent_graph.py](../tests/test_agent_graph.py) — 9 用例（熔断 / 错误反思 / 路由 / 编译）
- [scripts/agent_smoke.py](../scripts/agent_smoke.py) — 真 LLM 端到端联调脚本

### LangGraph 图结构

```
START → call_model → should_continue（条件边）
                        ├─ "tools" → tool_node → call_model（循环）
                        └─ END
```

### 关键设计

1. **LLM 接入选 `langchain_openai.ChatOpenAI`** 而非复用 3.2 的 LiteLLM：原生支持 `stream_mode="messages"` 的 token 级流式 + `tool_call_chunks` 自动增量累积，避免手动解析 JSON 增量片段
2. **流事件双路径兼容**：
   - 路径 a：`AIMessageChunk` 的 `tool_call_chunks`（流式增量，name 可能后到）→ 按 `index` 追踪
   - 路径 b：完整 `AIMessage` 的 `tool_calls`（一次性返回，多轮对话场景常见）→ 按 `id` 去重
3. **错误反思（AGT-04）**：tool_node 用 `try/except` 包裹工具执行，异常时返回含完整 traceback 的 `ToolMessage(status="error")`，模型能看到错误并自我修正

### 验证结果

- 单测：**18/18 通过**（test_tools + test_agent_runner + test_agent_graph）
- 联调：3 个用例全部正常，含多轮对话场景（模型从历史里推断 `station_id`）

---

## 3.4 本地执行工具模块 ✅

### 交付内容

| 需求 ID | 实现位置 | 状态 |
|---|---|---|
| **TOL-01** subprocess 引擎，30s 超时强制 Kill | [app/tools/script_runner.py](../app/tools/script_runner.py) | ✅ |
| TOL-02 dummy 测试工具 `mock_weather_parser`（新 PRD 命名为 `mock_data_parser`，当前实现作为一种类型保留） | 3.3 已完成 | ✅ |

### 文件清单

- [app/tools/script_runner.py](../app/tools/script_runner.py) — 异步 subprocess 引擎
  - `run_script(cmd, timeout, cwd, env, stdin_text)` → `ScriptResult(returncode, stdout, stderr, elapsed_seconds, timed_out)`
- [tests/test_script_runner.py](../tests/test_script_runner.py) — 9 个单测

### 关键设计

1. **全异步**：用 `asyncio.create_subprocess_exec` + `asyncio.wait_for`，不阻塞 FastAPI 事件循环
2. **超时强 kill 跨平台**：
   - Linux/Mac：`os.setsid` 建进程组 + `os.killpg(SIGKILL)` 杀整组
   - Windows：`CREATE_NEW_PROCESS_GROUP` + `proc.kill()`
3. **防 shell 注入**：`cmd` 强制要求 list/tuple，拒绝字符串（避免 `shell=True` 路径）
4. **不直接注册为 LLM 工具**：通用脚本执行权限过大，引擎层只提供底层能力；后续具体业务（气象脚本调度、RAG 预处理等）按需在自己的 `@tool` 中调用并做白名单/参数校验

### 验证结果

- 单测：**9/9 通过**（1.55s），含 PRD TOL-01 关键验收点 `test_run_script_timeout_force_kill`（sleep 30s 被 1s 超时在 < 5s 内强制 kill）

---

## 3.5 Agentic RAG 模块（Milvus）✅

### 交付内容

| 需求 ID | 实现位置 | 状态 |
|---|---|---|
| **RAG-01** Milvus 客户端初始化 + Collection 自动建/复用 + load | [app/rag/milvus_client.py](../app/rag/milvus_client.py)::`init_milvus` + [app/main.py](../app/main.py) lifespan | ✅ |
| **RAG-02** `search_knowledge_base(query, top_k, **kwargs)` 注册为 Agent 技能 | [app/rag/retriever.py](../app/rag/retriever.py) + [app/tools/__init__.py](../app/tools/__init__.py) | ✅ |
| **RAG-03** 混合标量过滤（doc_type / document_id） | [app/rag/retriever.py](../app/rag/retriever.py)::`_build_filter_expr` | ✅ |
| **RAG-04** `allowed_roles` 权限字段 + 自动注入 `ARRAY_CONTAINS` | [app/rag/schema.py](../app/rag/schema.py) + retriever 内 `get_current_role` | ✅ |
| **RAG-05** `document_id` + `entity_tags` 图谱锚点字段 | [app/rag/schema.py](../app/rag/schema.py) | ✅ |

### 文件清单

- [app/rag/schema.py](../app/rag/schema.py) — Milvus Collection Schema（7 字段，4096 维）+ HNSW/INVERTED 索引参数
- [app/rag/milvus_client.py](../app/rag/milvus_client.py) — `init_milvus` / `get_milvus_client` / `close_milvus` 单例与生命周期
- [app/rag/embedding.py](../app/rag/embedding.py) — `aembed_texts` 基于 LiteLLM 调远程 Qwen3-Embedding-8B，含维度严格校验
- [app/rag/retriever.py](../app/rag/retriever.py) — `search_knowledge_base` async `@tool`，含过滤拼装与结果格式化
- [app/rag/__init__.py](../app/rag/__init__.py) — 对外统一入口
- [app/core/config.py](../app/core/config.py) — 新增 8 个 RAG 相关字段
- [app/main.py](../app/main.py) — lifespan 接入 Milvus init/close
- [app/tools/__init__.py](../app/tools/__init__.py) — 注册 `search_knowledge_base`
- [tests/test_rag_schema.py](../tests/test_rag_schema.py) — 9 用例（字段定义、维度、capacity）
- [tests/test_rag_retriever.py](../tests/test_rag_retriever.py) — 13 用例（过滤拼装 / 格式化 / 端到端 mock / @tool 集成）
- [scripts/rag_ingest.py](../scripts/rag_ingest.py) — 简易入库脚本（段落切片 + hash chunk_id 幂等 upsert）
- [scripts/rag_smoke.py](../scripts/rag_smoke.py) — 真 Milvus + 真 Embedding + 真 LLM 端到端联调
- [data/seed/](../data/seed/) — 3 篇示例气象文本（台风路径 / 降雨监测 / 数值预报）
- [.env.example](../.env.example) — 新增 `MILVUS_*` / `EMBEDDING_*` / `RAG_DEFAULT_ROLE` 配置项
- [requirements.txt](../requirements.txt) — 启用 `pymilvus>=2.6.0`

### Milvus Collection Schema（实际落地）

| 字段 | DataType | 参数 |
|---|---|---|
| `chunk_id` | INT64 | PK, auto_id=False |
| `vector` | FLOAT_VECTOR | dim=4096 |
| `document_id` | VARCHAR | max_length=64 + INVERTED 索引 |
| `content` | VARCHAR | max_length=65535 |
| `allowed_roles` | ARRAY<VARCHAR> | capacity=20, length=32 |
| `entity_tags` | ARRAY<VARCHAR> | capacity=50, length=64 |
| `metadata` | JSON | dynamic_field=False |

- 向量索引：HNSW（M=16, efConstruction=200）+ COSINE
- 文档索引：document_id 上的 INVERTED 加速标量过滤

### 关键设计

1. **Embedding 独立配置**：chat 与 embedding 经常不同源（chat 走 DeepSeek，embedding 走 SiliconFlow），独立 `EMBEDDING_*` 配置项避免硬复用 chat 厂商前缀逻辑导致误判
2. **维度严格校验**：`aembed_texts` 返回向量长度必须等于 `settings.embedding_dimension`，否则直接抛 `ValueError`，防止错误维度写入 Milvus 后才暴露
3. **权限基线硬注入**：`current_role` 不暴露给 LLM —— retriever 内部通过 `get_current_role()` 解析（V1.0 写死 "ALL"，3.6 改成从请求 contextvar 读取，工具签名无需改动）
4. **async tool**：retriever 定义为 `async def`，LangGraph tool_node 通过 `tool.ainvoke` 直接 await，避免 `asyncio.run` 在已有事件循环中冲突
5. **chunk_id 幂等**：`hash(document_id + chunk_index)` 取低 63 位作为 INT64 主键，重跑入库脚本走 upsert，不产生垃圾数据
6. **fail-fast**：Milvus 启动期连不上直接抛 `RuntimeError`，让应用挂掉而不是带病运行

### 验证结果

- 单测：**40 用例全部通过**（test_rag_schema 9 + test_rag_retriever 18 + test_kg_writer 6 + test_kg_query 16 + test_kg_ner 13；总 62 含其他模块）
- 联调：**PRD §3.5 五条全部通过**
  - RAG-01：日志显示 Collection 自动检测 + 创建/复用 + load 全链路 OK
  - RAG-02：Agent 自主调用 `search_knowledge_base`，返回 score 0.760 的精准命中
  - RAG-03：`doc_type='report'` 标量过滤生效（日志 `filter=... and metadata["type"] == "report"`）
  - RAG-04：自动注入 `ARRAY_CONTAINS(allowed_roles, "ALL")` 权限基线
  - RAG-05：召回结果含完整 `entity_tags=[西北太平洋,菲律宾,...]` 与 `document_id` 透传

### 环境重建参考命令（按 CLAUDE.md 用户操作约定）

```bash
# 1. 安装新依赖
uv pip install pymilvus>=2.6.0 -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2. 启动本地 Milvus（任选其一）
docker run -d --name milvus-standalone -p 19530:19530 -p 9091:9091 milvusdb/milvus:v2.6.0
# 或 docker-compose 起完整 standalone

# 3. 编辑 .env，填入真实的 EMBEDDING_API_KEY（SiliconFlow / DashScope 等）

# 4. 单测
pytest tests/test_rag_schema.py tests/test_rag_retriever.py -v

# 5. 入库
python scripts/rag_ingest.py

# 6. 联调（3 个用例）
python scripts/rag_smoke.py
```

---

## 3.6 知识图谱模块（Neo4j）✅

### 交付内容

| 需求 ID | 实现位置 | 状态 |
|---|---|---|
| **KG-01** Neo4j 客户端初始化 + 健康检查 + 自动建唯一性约束 | [app/kg/neo4j_client.py](../app/kg/neo4j_client.py)::`init_neo4j` + [app/main.py](../app/main.py) lifespan | ✅ |
| **KG-02** 节点 / 关系 Upsert（MERGE 幂等）+ 批量版本 | [app/kg/writer.py](../app/kg/writer.py) | ✅ |
| **KG-03** `query_knowledge_graph` 注册为 Agent 技能 | [app/kg/tool.py](../app/kg/tool.py) + [app/kg/query.py](../app/kg/query.py) + [app/tools/__init__.py](../app/tools/__init__.py) | ✅ |
| **KG-04** Graph RAG 联合查询（两步调用都有 tool_start） | 保持两个独立 Tool + LangGraph runner 既有 tool_start 机制；retriever 新增 `entity_tags` 入参 | ✅ |
| **KG-05** 实体抽取管道（LLM Prompt NER）+ 同步写两库 | [app/kg/ner.py](../app/kg/ner.py) + 改造 [scripts/rag_ingest.py](../scripts/rag_ingest.py) | ✅ |

### 文件清单

- [app/kg/neo4j_client.py](../app/kg/neo4j_client.py) — `AsyncDriver` 单例 + `verify_connectivity` + 幂等建约束
- [app/kg/writer.py](../app/kg/writer.py) — `upsert_document` / `upsert_entity` / `link_entity_to_chunk` + 两个批量版本
- [app/kg/ner.py](../app/kg/ner.py) — LLM Prompt 通用 NER（5 类：PERSON / LOCATION / ORG / TIME / OTHER），软失败
- [app/kg/query.py](../app/kg/query.py) — 多跳查询 Cypher 构建 + 结果格式化（max_hops 夹值 [1,5]）
- [app/kg/tool.py](../app/kg/tool.py) — `query_knowledge_graph` async `@tool`
- [app/kg/__init__.py](../app/kg/__init__.py) — 对外统一入口（lifespan / writer / NER / tool）
- [app/core/config.py](../app/core/config.py) — 新增 5 个字段（neo4j_uri/user/password/database、kg_ner_model）
- [app/main.py](../app/main.py) — lifespan 接入 `await init_neo4j()` / `await close_neo4j()`
- [app/tools/__init__.py](../app/tools/__init__.py) — 注册 `query_knowledge_graph`
- [app/rag/retriever.py](../app/rag/retriever.py) — `search_knowledge_base` 新增 `entity_tags` 入参（ARRAY_CONTAINS_ANY 过滤）
- [scripts/rag_ingest.py](../scripts/rag_ingest.py) — 整合 NER + Neo4j：chunk 切完 → 并发 NER → 同步写 Milvus.entity_tags + Neo4j(Entity + MENTIONED_IN)
- [scripts/kg_smoke.py](../scripts/kg_smoke.py) — 3 用例：直接 query / 带过滤 query / Agent 端到端 Graph RAG
- [tests/test_kg_writer.py](../tests/test_kg_writer.py) — 6 用例（Cypher 结构与参数化）
- [tests/test_kg_query.py](../tests/test_kg_query.py) — 14 用例（夹值 / Cypher 构造 / 格式化 / @tool 集成）
- [tests/test_kg_ner.py](../tests/test_kg_ner.py) — 12 用例（解析 / 去重 / 大小写归一 / 软失败）
- [tests/test_rag_retriever.py](../tests/test_rag_retriever.py) — 同步扩展 `entity_tags` 过滤的测试

### Neo4j 数据模型（实际落地，PRD §4.4）

| 类型 | 名称 | 关键属性 / 唯一性 |
|---|---|---|
| Node Label | `Document` | `document_id`（**唯一约束**） / `title` / `created_at` |
| Node Label | `Entity` | (`name`, `type`) **复合唯一约束** + `document_ids[]`（出现过它的文档列表） |
| Relationship | `MENTIONED_IN` | `chunk_id`（指向具体 Milvus chunk，用于追溯出处） |
| Relationship | `RELATED_TO` | V1.0 未抽取关系，留作后续接入关系抽取时填充 |

### 关键设计

1. **`(name, type)` 复合唯一键**：同名实体可能是不同类型（"苹果"既可能是 ORG 也可能是 OTHER），仅按 name 唯一会丢失语义。复合键既保持 MERGE 幂等，又允许多义词共存
2. **NER 软失败原则**：NER 是入库的辅助步骤，主链路 Milvus 写入是核心。LLM 限流 / JSON 解析失败 → 返回 `[]`，记日志不抛错，不阻断整批入库
3. **NER 模型独立配置**：可选 `KG_NER_MODEL`，缺省复用 `LITELLM_MODEL`。**关键经验**：NER 应使用非 reasoning 的轻量快速模型（如 `deepseek-v4-flash`），避免推理模型对"什么算实体"过度思考导致大量返回 `entities=[]`。实测 v4-flash 在 3 篇气象文本中抽出 35 个高质量实体（地名/机构/时间）
4. **`max_hops` 夹值防爆炸**：`[r*1..N]` 变长路径中 N 不能参数化，必须用 f-string 拼接 —— 严格夹值到 [1, 5] 防注入与防图谱爆炸
5. **KG-04 不写新 Tool**：保留 `query_knowledge_graph` 与 `search_knowledge_base` 两个独立 Tool + system prompt 引导模型分两步调用，PRD "两步调用都有 tool_start" 由 LangGraph runner 既有机制自动满足
6. **AsyncDriver 与 FastAPI 原生匹配**：所有写入走 `session.execute_write(tx_fn)` 带自动重试，所有 Cypher 走 `$param` 参数化（防注入）
7. **批量化写入**：每份文档处理完一次性 UNWIND 写实体与关系，避免 N 次往返
8. **fail-fast**：Neo4j 启动期连不上直接抛 `RuntimeError`，让应用挂掉而不是带病运行

### 验证结果

- 单测：**35 用例全部通过**（test_kg_writer 6 + test_kg_query 16 + test_kg_ner 13）
- 联调：**PRD §3.6 五条全部通过**
  - KG-01：启动日志显示连接 OK + 约束自动创建/复用（第二次启动看到 "already exists, has no effect"）
  - KG-02：3 篇文档入库共写入 35 个唯一实体 + 19 条 MENTIONED_IN 关系；重跑 ingest 节点数不变（MERGE 幂等）
  - KG-03：模型主动调用 `query_knowledge_graph(entity_name="西北太平洋")` 返回 20 条路径
  - KG-04：联合查询完整链路验证通过 —— `query_knowledge_graph` 与 `search_knowledge_base` 都被实际调用，且 RAG 第二次调用带 `entity_tags=[西北太平洋,南海,ECMWF,GFS,...]` 精筛
  - KG-05：NER 抽取 35 个高质量实体（地名/机构/时间），同步写 Milvus.entity_tags + Neo4j Entity/Document 节点

### 联调阶段关键经验

1. **Embedding 模型必须带 LiteLLM 厂商前缀**：`EMBEDDING_MODEL=openai/Qwen/Qwen3-Embedding-8B`（缺前缀会被 LiteLLM 拒绝路由）
2. **LiteLLM 的 `openai/` 路由禁止 dimensions 参数**：embedding.py 已删除该参数，靠返回维度严格校验保证一致性
3. **NER 应使用非 reasoning 模型**（如 `deepseek-v4-flash`），推理模型对实体抽取"过度思考"导致大量空返回
4. **Agent system prompt 是 Graph RAG 联合查询的关键**：runner.py 注入 `_SYSTEM_PROMPT` 明确两个工具的分工与联合调用模式，避免模型陷入工具循环触发熔断

### 环境重建参考命令

```bash
# 1. 安装新依赖
uv pip install neo4j>=5.20.0 -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2. 启动 Neo4j（若未启动）
cd docker-compose && docker compose up -d neo4j
# 访问 http://localhost:7474 验证（账号 neo4j / tyagent_neo4j）

# 3. 编辑 .env，把 NEO4J_* 填好（默认值与 docker-compose 对齐）

# 4. 单测
pytest tests/test_kg_*.py tests/test_rag_retriever.py -v

# 5. 重新入库（这次同时写 Milvus + Neo4j，会调 NER 烧 LLM token）
python scripts/rag_ingest.py

# 6. KG 联调
python scripts/kg_smoke.py
```

---

## 历史变更

- **2026-06-18**：根据 2026-06-17 A/B 审查报告完成第一批 hardening 修复
  - 新增 [docs/0617/xiugai.md](0617/xiugai.md) 作为 A/B 审查报告统一 TODO 清单，标注当前批次、下批排期与验收建议
  - 异步防挂死：[app/core/async_utils.py](../app/core/async_utils.py) 新增 `wait_for_named()` / `gather_with_timeout()`；[app/api/v2/endpoints/query.py](../app/api/v2/endpoints/query.py)、[app/rag/query_ner.py](../app/rag/query_ner.py)、[app/ingest/table_description.py](../app/ingest/table_description.py)、[app/ingest/dual_layer.py](../app/ingest/dual_layer.py)、[app/tasks/ingest_task.py](../app/tasks/ingest_task.py) 接入整组超时并保留软降级语义
  - 入库一致性：[app/tasks/ingest_task.py](../app/tasks/ingest_task.py) 标记 Neo4j 软失败到 `doc_metadata._ingest_warnings`；失败兜底路径尽力清理 Milvus / Neo4j 残留；Step 8 Milvus upsert 改 `asyncio.to_thread()` 避免阻塞事件循环
  - LLM 防挂死：[app/tasks/session_task.py](../app/tasks/session_task.py)、[app/kg/ner.py](../app/kg/ner.py)、[app/llm/client.py](../app/llm/client.py) 为遗漏的 LiteLLM 调用增加外层硬超时；流式响应对首包与每个 chunk 都做超时保护
  - Milvus client 安全性：[app/rag/milvus_client.py](../app/rag/milvus_client.py) `create_v2_kb_collection(..., client=...)` 支持显式 client，入库自愈不再临时替换模块级 `_client`
  - 测试补充：[tests/test_async_utils.py](../tests/test_async_utils.py)、[tests/test_ingest_task.py](../tests/test_ingest_task.py) 增补公共异步工具、Neo4j 降级标记和失败补偿路径覆盖；语法级验证 `python -m compileall ...` 已通过，pytest 按项目约定待用户手动执行
- **2026-06-17**：修复旧 V1.5 数据库升级 V2 后知识库列表 500
  - **现象**：前端请求 `GET /api/v1/knowledge-bases?page=1&page_size=100` 返回 500 Internal Server Error
  - **根因**：V2.0 在 ORM 模型中给 [knowledge_bases](../app/models/knowledge_base.py) 新增 `retrieval_config` / `doc_metadata_schema`，给 [kb_files](../app/models/kb_file.py) 新增 `doc_metadata` / `summary_brief`；但当前项目未引入 Alembic，FastAPI 启动期 `Base.metadata.create_all()` 只会建新表，不会给旧 V1.5 已存在表补列。列表接口 [app/api/v1/endpoints/knowledge_bases.py](../app/api/v1/endpoints/knowledge_bases.py) → [app/services/kb_service.py](../app/services/kb_service.py) 执行 `select(KnowledgeBase)` 时会 SELECT ORM 全字段，PG 旧表缺列触发 `UndefinedColumn`，最终统一异常 handler 返回 500
  - **修复**：[app/main.py](../app/main.py) 新增 `_ensure_v2_compat_columns()`，在启动 `create_all` 后、Milvus/Neo4j 初始化前执行幂等兼容迁移；通过 `information_schema.columns` 检测旧表列，缺失时执行 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...`，旧数据保留且新列默认 NULL
  - **测试**：[tests/test_v2_t0.py](../tests/test_v2_t0.py) 新增 `_build_v2_compat_alter_sql` 单测 + 真 PG 旧表兼容集成测试，覆盖“旧表补列后 ORM 查询可读出旧 KB 数据”
  - **验证命令**：按项目约定由用户手动执行 `pytest tests/test_v2_t0.py::TestKBV2Extensions::test_legacy_v1_5_tables_generate_v2_compat_alter_sql tests/test_v2_t0.py::TestKBV2Extensions::test_existing_v2_columns_do_not_generate_alter_sql -q`；有 `TEST_DATABASE_URL` 时可追加 `pytest tests/test_v2_t0.py::test_v2_compat_migration_allows_orm_query_on_legacy_v1_5_tables -q`
- **2026-06-17**：V2.0 质量修复 batch1（防回归增强）
  - API 基础设施：[app/main.py](../app/main.py) 接入 `CORSMiddleware`；[app/core/config.py](../app/core/config.py) 新增 `CORS_ALLOW_ORIGINS` / `CORS_ALLOW_CREDENTIALS`；[.env.example](../.env.example) 同步前端本地开发白名单示例
  - RAG 精排降级语义：[app/rag/reranker.py](../app/rag/reranker.py) `NoopReranker` 改为保留原始检索 `score`，避免把 BM25/RRF 或 dense 分数覆盖成 1.0；[app/rag/hybrid_retriever.py](../app/rag/hybrid_retriever.py) 传入候选原始分数
  - Trace 错误一致性：[app/api/v2/endpoints/traces.py](../app/api/v2/endpoints/traces.py) trace 不存在时统一抛 `BusinessError(NOT_FOUND)`，交由统一响应 handler 输出 `{code,message,data}`
  - Milvus filter 安全性：[app/rag/retriever.py](../app/rag/retriever.py) 新增字符串字面量转义，覆盖权限角色、doc_type、document_id、entity_tags；hybrid 检索复用 `_build_filter_expr` 同步受益
  - 单测同步：[tests/test_v2_t0.py](../tests/test_v2_t0.py)、[tests/test_v2_p1.py](../tests/test_v2_p1.py)、[tests/test_v2_t2.py](../tests/test_v2_t2.py)、[tests/test_v2_t3.py](../tests/test_v2_t3.py)、[tests/test_rag_retriever.py](../tests/test_rag_retriever.py) 增补/调整覆盖；验证命令需由用户按项目约定手动执行
- **2026-06-17**：V2.0 全链路集成 smoke 端到端验收通过 ✅✅✅
  - [scripts/v2_smoke.py](../scripts/v2_smoke.py) 扩展覆盖 T10（/retrieve、/rerank、/generate 三个分层端点）+ T12（/api/v2/analytics 聚合统计），单脚本贯通 T0~T12 全链路
  - 12 项 ⬜ 集成验证全部勾掉：T0 启动建表 / T1 入库 / T2 BM25+RRF / T3 trace / T4 reranker / T7 doc_metadata / T8 三种改写路径 / T9 自检 / T10 分层端点 / T11 RAGAS / T12 analytics + ragas 安装
  - smoke 关键产出：[3] chunk_count=160（fine 154 + table_description 5 + coarse 1）/ [5a-5d] 4 次 query latency 11~22s / [6] trace 7 步全齐 / [8a] /retrieve 360ms 5 chunks / [8b] /rerank 1808ms 降序 / [8c] /generate answer_len=110 / [9] total_queries=4 tool_usage.bm25=1.000 faithfulness=0.250
  - **关键 Bugfix（OBS-03 快照丢失）**：smoke [9] 暴露 `total_queries=0` 异常，Phase 1 调查发现 [app/observability/analytics_writer.py](../app/observability/analytics_writer.py) `write_analytics_snapshot` 仅 `flush` 不 `commit`，注释说"由调用方统一 commit"但 [app/api/v2/endpoints/query.py](../app/api/v2/endpoints/query.py) 调用方从未 commit → 请求结束 AsyncSession 关闭时隐式 rollback → 数据永远不落库。修法：writer 内部独立 commit + rollback 兜底，符合项目惯例（chat/kb/session/file/evaluations 14 处全显式 commit）；commit 失败仍走 try/except warning 不阻断主链路。修复后 smoke `total_queries=4` 完全符合预期
  - T4 reranker 真实调用 + T11 RAGAS 评估通过 A.1 实验 4 组实测充分验证（详见 [eval_a1_reranker_tuning.md](eval_a1_reranker_tuning.md)）
  - 至此 V2.0 Hermes 迭代功能侧 + 集成验收全部完成；剩下进入 A.2/A.3 模型选型与运维上线阶段
- **2026-06-15**：V2.0 Hermes T9 完成（**P2 阶段全部收尾**，单测 691 通过）
  - CHC-03 置信度评分：[app/rag/confidence.py](../app/rag/confidence.py) `compute_confidence` 纯函数，按 PRD §540 公式 `weighted_avg(rerank) × coverage × (1 − penalty)`；< 0.5 自动填 PRD §556 警告文案
  - CHC-04 答案自检：[app/rag/faithfulness.py](../app/rag/faithfulness.py) LLM as Judge；状态三态 ok/skipped/disabled；JSON 数组/对象包装兼容；wait_for 硬超时 + 围栏剥离 + 软失败
  - unverified 处理：在 answer 末尾追加 `⚠ 以下事实未在检索内容中找到明确支撑：- claim1` 警告清单（不再二次调 LLM 插 † 标记）
  - 主链路：[app/api/v2/endpoints/query.py](../app/api/v2/endpoints/query.py) Step 7 后插入 faithfulness_check（disabled 不调 LLM）+ compute_confidence；检索空兜底分支也透 4 个新字段
  - 三层合并：[app/rag/retrieval_config.py](../app/rag/retrieval_config.py) `enable_faithfulness_check` 加入 ResolvedRetrievalOptions，API > KB > settings.faithfulness_check_default（默认 False）
  - Schema：QueryOptions 加 `enable_faithfulness_check`；QueryResponse 加 `confidence` / `low_confidence_warning` / `faithfulness_check` / `unverified_claims` 4 个字段
  - 全量回归 **691 passed + 6 skipped**（654 → 691，零回归）
  - 🎉 **V2.0 P2 阶段（T7+T8+T9）全部完成**；剩余 T10/T11/T12 属 P3+ 增强项
- **2026-06-15**：V2.0 Hermes T7 完成（P2 进度 2/3，单测 654 通过）
  - IDP-03：[app/ingest/table_description.py](../app/ingest/table_description.py) 表格自然语言描述生成；表格 chunk 不动，新增 `block_type="table_description"` 关联 chunk；parent_chunk_id 指向原表格 INT64 chunk_id 字符串
  - IDP-04：[app/ingest/dual_layer.py](../app/ingest/dual_layer.py) 双层索引；按 `heading_path[:-1]` 父级聚合；粗 chunk `is_summary=True`；fine_chunks 的 parent_chunk_id 用 `dataclasses.replace` 回填指向粗 chunk
  - IDP-05：[app/ingest/doc_metadata.py](../app/ingest/doc_metadata.py) 文档元数据提取（doc_type / doc_date / language / key_topics / summary_brief）；写入 `kb_files.doc_metadata` JSONB + `summary_brief` Text
  - 主链路：[app/tasks/ingest_task.py](../app/tasks/ingest_task.py) 三个 noop 替换为真实步骤；`_main` 串联三类 chunk + NER 仅对 fine_chunks 跑（td/coarse 补空 entities 对齐）；`chunk_count` = fine + td + coarse 三类总和
  - Schema 暴露：[app/schemas/kb_file.py](../app/schemas/kb_file.py) FileListItem 加 `summary_brief`；FileDetail 加 `summary_brief` + `doc_metadata`
  - 三类 chunk_index 全局唯一：fine 用 splitter 给的 0..N；td 从 `len(fine)` 起；coarse 从 `len(fine)+len(td)` 起 → `_make_chunk_id_int` 幂等 upsert 不冲突
  - 软失败原则：IDP-03/04/05 任一步骤失败 → 该步产物缺失但不阻断主链路（沿用 V1.5 NER 软失败模式）
  - 兼容修复：[tests/test_v2_t1.py](../tests/test_v2_t1.py) 3 个 noop 测试改为存在性断言；[tests/test_ingest_task.py](../tests/test_ingest_task.py) `patched_pipeline` fixture 加 mock T7 三步
  - 全量回归 **654 passed + 6 skipped**（621 → 654，零回归）
- **2026-06-15**：V2.0 Hermes T8 完成（P2 进度 1/3，单测 621 通过）
  - HRE-01：[app/rag/query_rewriter.py](../app/rag/query_rewriter.py) 三策略（none / hyde / multi_query）+ 软降级（异常/超时返 noop）
  - HRE-02：[app/rag/query_ner.py](../app/rag/query_ner.py) 薄封装 [app/kg/ner.py](../app/kg/ner.py) `run_ner` + Neo4j 单跳锚定（Semaphore=5 限流 + UTF-8 字节安全截断 + 上限 50 标签）
  - HRE-06：[app/rag/retrieval_config.py](../app/rag/retrieval_config.py) `resolve_options` 三层合并（API > KB > settings）；KB CRUD 通过 `KnowledgeBaseUpdateRequest.retrieval_config` 暴露
  - 主链路重写：[app/api/v2/endpoints/query.py](../app/api/v2/endpoints/query.py) 7 步 trace（query_rewrite → query_ner → graph_anchor → retrieve → build_context → generate → citation_parse）；multi_query 用 RRF 二次融合
  - 错误码：[app/api/error_codes.py](../app/api/error_codes.py) 新增 `QUERY_REWRITE_INVALID = 40011`，与 PRD §1127 对齐；非法值的拦截放在 `resolve_options` 入口处而非 Pydantic validator（避免被 ValidationError 重打包成 40001）
  - P1 兼容：[tests/test_v2_p1.py](../tests/test_v2_p1.py) `top_k` 默认改 None；3 个 E2E 用例补 mock `extract_query_entities`/`anchor_to_graph`/`rewrite_query`
  - 全量回归 **621 passed + 6 skipped**（580 → 621，零回归）
- **2026-06-15**：V2.0 Hermes T4+T5+T6 全部完成（P1 进度 3/3，单测 580 通过）
  - T4：HRE-05 Reranker 精排（BaseReranker / NoopReranker / LiteLLMReranker + Semaphore 限流 + 兜底规则 + 降级返原序）
  - T5：CHC-01/02 Citation（build_context_with_citation `[1] 来源:...` 格式 + parse_citations 正则解析去重）
  - T6：UQA-01 统一查询接口 `/api/v2/query`（hybrid_search → Tracer → build_context → litellm.acompletion → parse_citations 全链路；4 步埋点）
  - 修复阶段（共 4 类问题）：
    1. **`import litellm` 提到顶层**：[app/rag/reranker.py](../app/rag/reranker.py) 原写在方法内 import，导致单测 patch 失败 → 移到模块顶层
    2. **citation.py 中文引号 SyntaxError**：system prompt 内 ASCII `"` 被 Python 当作字符串闭合符 → 改用 Unicode 中文引号 U+201C/U+201D
    3. **citation.py docstring `\d` DeprecationWarning**：模块/函数 docstring 中 `\d` 是无效转义 → 改为 `\\d`
    4. **query.py 误导入不存在的 `ChatService`**：T6 实际用 stream_chat 函数式 API → 删除该导入
  - T2 老测试 `test_hybrid_search_bm25_enabled` 同步修复：T4 集成后 NoopReranker 把 score 覆盖为 1.0（"信任原排序、满分"语义），断言从 0.95 改为 1.0 + 注释说明
  - 端到端贯通测试新增 3 用例：完整链路（hybrid_search → rerank → context → LLM → citation 串通）+ 检索空兜底 + LLM 失败兜底
  - 全量回归 **580 passed + 6 skipped**（557 → 580，零回归）
- **2026-06-12**：V2.0 Hermes T0+T1+T2+T3 全部完成（P0 进度 4/4，单测 557 通过）
  - T0：基础设施扩展（8 配置项 + 2 新 PG 表 + Milvus V2 Schema 15 字段 + BM25 索引）
  - T1：智能文档处理（StructuredBlock + StructuredChunk + 11 步入库管道）
  - T2：混合检索引擎（Milvus BM25 Function + hybrid_search + RRFRanker + 降级策略）
  - T3：可观测性 Trace（Tracer 上下文管理器 + /api/v2/traces 查询接口）
  - 全量回归 557 passed + 6 skipped，零回归
- **2026-06-12**：V2.0 Hermes 迭代启动，T0 基础设施扩展进行中
- **2026-06-12**：V1.5 全链路 smoke 端到端验收通过 ✅✅✅
  - 6 个阶段 6/6 完成；mock 420 用例全过，集成测试 37/37 全过，**端到端真实跑通**
  - smoke 数据：2 个 KB / 2 份气象文档（docx + md）/ 真实入库 22 chunks + 60+ 实体 / 3 轮真 LLM 对话 / 全链路 1:44
  - 4 条 PRD 用户需求 100% 验证：KB CRUD / 上传指定 KB / 文件增删查 / 删除时三库联动清理
  - 联调阶段修补：[app/main.py](../app/main.py) lifespan 加 PG 启动重试（10×2s），规避 `the database system is starting up` 启动期窗口
  - 项目记忆 7 条、Celery worker 同步 / PG 多次 / NER 软失败 / Milvus 字节截断等关键工程坑全部固化
- **2026-06-11**：V1.5 S5 KB-06 关联对话完成（mock 全过，端到端 smoke 待用户跑）
  - 新增 [app/agent/context.py](../app/agent/context.py)：request-scoped contextvar `current_kb_ids` 三态语义（None / [] / [...]），与 `get_current_role()` 同款"业务上下文不暴露给 LLM"模式
  - 扩展 [app/schemas/chat.py](../app/schemas/chat.py) `ChatRequest` 加 `kb_ids: list[UUID] | None`；`ToolStartEvent.args` 类型放宽到 `dict`（容纳 `_kb_ids` 等嵌套值）
  - 改造 [app/rag/retriever.py](../app/rag/retriever.py) `_do_search`：根据 contextvar 决定查默认 Collection / 跳过 / 跨 KB Collection 查询 + score 合并重排；per-collection 失败仅 warning 不阻断其它 KB
  - 改造 [app/kg/query.py](../app/kg/query.py) `build_cypher` + `execute_graph_query` 接收 `kb_ids`，追加 `WHERE start.kb_id IN $kb_ids` 过滤
  - 改造 [app/kg/tool.py](../app/kg/tool.py) `query_knowledge_graph` 从 contextvar 读 kb_ids，kb_ids=[] 时直接早返不碰 Neo4j
  - 改造 [app/services/chat_service.py](../app/services/chat_service.py) `stream_chat` 加 `kb_ids` 参数，try/finally 注入与重置 contextvar；ToolStartEvent.args 注入 `_kb_ids` 信息（KB-06 验收点）
  - 改造 [app/api/v1/endpoints/chat.py](../app/api/v1/endpoints/chat.py) 把 `body.kb_ids` 透传给 service
  - 解开 [app/services/kb_service.py](../app/services/kb_service.py) `count_entities_for_kb` 的 S2 stub，接通 Neo4j 真实 `MATCH (e:Entity {kb_id}) RETURN count(e)`
  - 新增 [tests/test_kb06_chat_scope.py](../tests/test_kb06_chat_scope.py) **19 用例**（contextvar 三态/ChatRequest 校验/retriever 跨 collection/per-collection 容错/合并重排/KG cypher 拼接/KG tool 三态）
  - 修旧 [tests/test_rag_retriever.py](../tests/test_rag_retriever.py) `test_do_search_exception_caught_per_collection` 以符合 KB-06 容错策略
  - 新增 [scripts/v1_5_smoke.py](../scripts/v1_5_smoke.py)：V1.5 全链路 smoke（2 KB / 2 上传 / 3 轮对话验 kb_ids 三态 / 验 tool_start 携带 _kb_ids）
  - mock 全量回归 **420 passed**（401 → 420，零回归）
- **2026-06-11**：V1.5 S4 标题/摘要异步生成完成（待端到端 smoke）
  - 新增 [app/tasks/session_task.py](../app/tasks/session_task.py)：两个 Celery 任务（title / summary），独立 LLM 模型配置（SESSION_TITLE_MODEL / SESSION_SUMMARY_MODEL 缺省回退 LITELLM_MODEL），LLM 输出清洗（去引号/markdown/标点/截 20-200 字），摘要超长 SUMMARY_INPUT_CHAR_LIMIT=28k 字符直接 failed（dev_plan S4 决策）
  - 改造 [app/services/chat_service.py](../app/services/chat_service.py) `stream_chat` 流末尾：`_maybe_trigger_title_task` 判 `title is None AND message_count == 2` 时异步触发标题任务；任务里再判一次防并发竞态；写 title/summary 时**不 touch updated_at**（避免异步任务把会话顶到列表第一位）
  - 新增 endpoint [POST /api/v1/sessions/{id}/summarize](../app/api/v1/endpoints/sessions.py)：202 + task_id 立即返；Celery 不可达 → 50300
  - 注册到 [_TASK_MODULES](../app/tasks/celery_app.py) 让 worker 自动发现
  - 新增 [tests/test_session_task.py](../tests/test_session_task.py) **24 用例**（清洗/skip 分支/超长/happy path/异常包装）
  - 新增 [tests/test_s4_session_async.py](../tests/test_s4_session_async.py) **9 用例**（endpoint 202/404/503 + chat_service 触发判断）
  - mock 全量回归 **401 passed**（368 → 401，零回归）
- **2026-06-11**：V1.5 S3 阶段端到端 smoke 验收通过 ✅
  - 真实气象论文 PDF 端到端跑通：56 chunks / 613 entities / 457 唯一实体 → Milvus + Neo4j 双库写入；删除后三库 + 磁盘全清；总耗时 ~60s
  - PRD 用户 4 条需求 100% 验证：KB CRUD / 上传指定 KB / 文件增删查 / **删除时三库联动清理**
  - 联调阶段补丁（已写代码注释 + 项目记忆）：
    1. **NER 超时硬兜底**：litellm 默认 60s timeout 在大文档场景会让 `asyncio.gather` 被慢调用拖死整批；加 `wait_for(25s)` + NER 并发提到 8；超时按软失败原则返 `[]`
    2. **实体名 UTF-8 字节截断**：Milvus VARCHAR `max_length` 是按字节算（不是字符），中文 22 字 = 66 字节超 `entity_tags(max_length=64)`；用 `_truncate_utf8()` 工具按 UTF-8 字节安全截断（已记 [[milvus-varchar-max-length-is-bytes]]）
    3. **Windows Neo4j 用 127.0.0.1**：localhost 走 IPv6 vpnkit 转发不稳，60s 超时；与 Redis 同款坑，统一固化（已记 [[windows-redis-use-127-not-localhost]]）
    4. **SKIP_NER 开关**：大文档场景 LLM NER 慢且贵，加配置开关支持快速验证主管道；Phase 2 评估是否换 HanLP/spaCy 本地 NER
    5. **smoke 脚本超时调到 15min + 卡点告警**：单一 progress 阶段超 3min 无推进打 WARNING，方便定位
  - 验证数据：本测 7 步全部 ✓，含磁盘清理 / Milvus collection drop / Neo4j 子图 DETACH DELETE 三联清理
- **2026-06-11**：V1.5 S3.2 七步入库管道 + 三联清理链路完成（待用户跑 smoke 端到端验收）
  - 新增 [app/tasks/_resources.py](../app/tasks/_resources.py)：每任务 PG/Milvus/Neo4j 现建现断（NullPool + 局部 client，规避 prefork fork 副作用）
  - 重写 [app/tasks/ingest_task.py](../app/tasks/ingest_task.py)：async def _main 内按 PRD §3.4 七步推进（parse → split → embed → milvus_write → ner → neo4j_write → done），progress 锚点 20/35/60/80/90/95/100；NER 软失败 + Neo4j 软失败；TASK-03 重试策略（指数退避 30s/60s/120s，3 次用尽 → failed）+ 异常分类（ValueError/ParseError 不可重试；MilvusException/TimeoutError 可重试）
  - 接通 [app/services/kb_file_service.py](../app/services/kb_file_service.py) FILE-04 真清理：Milvus 按 `document_id == file_id` 删；Neo4j 按 (document_id, kb_id) 复合匹配 DETACH DELETE Document（Entity 节点保持复用不删）
  - 扩展 [app/services/kb_service.py](../app/services/kb_service.py) KB-05 三联清理：revoke 所有 processing 任务 → Milvus drop → Neo4j 子图 DETACH DELETE → PG → 磁盘目录清空
  - 新增 [tests/test_ingest_task.py](../tests/test_ingest_task.py) **16 用例**（异常分类 / chunk_id 稳定性 / 七步管道 happy path / Neo4j 软失败 / Celery eager 路径）
  - 新增 [tests/test_s3_cleanup.py](../tests/test_s3_cleanup.py) **16 用例**（FILE-04 Milvus/Neo4j 清理各分支 / KB-05 端到端顺序 / Milvus 失败短路）
  - 新增 [scripts/v1_5_s3_smoke.py](../scripts/v1_5_s3_smoke.py)：用户手动跑的端到端 smoke（建 KB → 上传 PDF → 轮询 progress → 验 Milvus+Neo4j → 删文件 → 删 KB → 验三库清理干净）
  - mock 全量回归 **368 passed**，零回归（336 → 368）
- **2026-06-11**：V1.5 S3.1 文件上传 endpoint（FILE-01~05）完成
  - 新增 [app/schemas/kb_file.py](../app/schemas/kb_file.py)（FileListItem / FileDetail / FileListResponse）
  - 新增 [app/services/kb_file_service.py](../app/services/kb_file_service.py)：upload / list / get / delete / reindex；含磁盘边读边量 + 防 Content-Length 欺骗 + KB 冗余计数维护 + Celery 任务触发
  - 新增 [app/api/v1/endpoints/kb_files.py](../app/api/v1/endpoints/kb_files.py)：5 个 endpoint（POST 上传/GET 列表/GET 详情/DELETE/POST reindex），挂在 `/api/v1/knowledge-bases/{kb_id}/files`
  - 新增 [app/tasks/ingest_task.py](../app/tasks/ingest_task.py)：S3.1 stub（仅记日志），S3.2 接入真实七步入库管道
  - 挂载到 [app/api/v1/router.py](../app/api/v1/router.py)，`parse_and_ingest_task` 已注册到 Celery `_TASK_MODULES`
  - 新增 [tests/test_kb_file_endpoints.py](../tests/test_kb_file_endpoints.py) **19 用例**（mock service，CI 友好）
  - 新增 [tests/test_kb_file_service.py](../tests/test_kb_file_service.py) **20 用例**（mock DB + mock Celery + 临时磁盘文件，覆盖 upload/delete/reindex/磁盘工具的内部协调）
  - mock 全量回归 336 passed，零回归（297 → 336，+39 新增 19+20）
  - **设计决策**：允许同名文件（磁盘按 file_id 隔离）+ S3.7 扩展名为主 MIME 二次校验 + S3.5 Celery+async 约定（S3.2 实现时 follow）
- **2026-06-11**：V1.5 S3.0 文档解析 + 切片模块完成
  - 新增 [app/ingest/__init__.py](../app/ingest/__init__.py) 包入口
  - 新增 [app/ingest/parser.py](../app/ingest/parser.py)：扩展名为主分发（PDF/docx/md/txt）+ MIME 二次校验 + `ParseError` 统一异常
  - 新增 [app/ingest/splitter.py](../app/ingest/splitter.py)：`RecursiveCharacterTextSplitter` 包装 + tiktoken token 长度估算 + 中英文混合分隔符
  - 新增 [tests/test_ingest_parser_and_splitter.py](../tests/test_ingest_parser_and_splitter.py) **28 用例**（fixture 现场生成 PDF/docx，不污染 repo）
  - mock 全量回归 297 passed，零回归
- **2026-06-11**：V1.5 S2 阶段集成测试验收通过 ✅
  - 本地 PG 实测 37/37 通过（test_sessions_api 3 + SES-01~06 15 + chat_service 5 + KB 14），1:34
  - 联调阶段定位并修复 3 个工程问题：
    1. **远程 PG 不稳 → 搬到本地 docker-compose**：新增 `postgres:17-alpine` 服务（含 init 脚本自动建 `tyagent_test`），从远程 117.72.214.41 切到 127.0.0.1。测试速度 3-5x 提升，WinError 121 信号灯超时彻底消失
    2. **Windows asyncpg 连接池跨 event loop bug → engine 在测试模式用 NullPool**：[app/db/session.py](../app/db/session.py) 检测 `TEST_DATABASE_URL` 存在时自动切 NullPool，每次新建/即断不复用，避免 pytest-asyncio 用例间连接池跨 loop 复用导致的 "Event loop is closed" 等问题；生产路径完全不受影响
    3. **PG `ORDER BY created_at` tie 问题已在 S1 修过**（[chat_service.py](../app/services/chat_service.py) / [session_service.py](../app/services/session_service.py) 加 `id` tie-breaker），S2 阶段沿用，KB 列表 `ORDER BY created_at + id` 也已加 tie-breaker
  - 同步修 `test_sessions_api.py` 从重型 `client` fixture 切到轻量 `pg_client`（V1.0 老测试本就不需要 Milvus/Neo4j），CI 不必起 Neo4j 也能跑全量集成
  - 沉淀写入项目记忆 [[postgres-local-docker-compose-for-tests]]
- **2026-06-11**：V1.5 S2.1 KB CRUD endpoint 完成（mock 全过，集成测试待用户跑）
  - 新增 [app/schemas/knowledge_base.py](../app/schemas/knowledge_base.py)（CreateRequest / UpdateRequest / Detail / ListItem / ListResponse）
  - 新增 [app/services/kb_service.py](../app/services/kb_service.py)（KB-01~05 业务逻辑，含失败回滚 + KB-05 严格清理顺序）
  - 新增 [app/api/v1/endpoints/knowledge_bases.py](../app/api/v1/endpoints/knowledge_bases.py)（5 个 endpoint）
  - 挂载到 [app/api/v1/router.py](../app/api/v1/router.py)
  - 新增 [tests/test_kb_endpoints.py](../tests/test_kb_endpoints.py) **29 用例**（mock service，CI 友好）
  - 新增 [tests/test_kb_service.py](../tests/test_kb_service.py) **16 用例**（mock DB + mock Milvus，service 内部协调逻辑）
  - 新增 [tests/test_kb_v1_5_integration.py](../tests/test_kb_v1_5_integration.py) 13 用例（真 PG + 真 Milvus，待用户跑）
  - 扩展 [tests/conftest.py](../tests/conftest.py) 加 `kb_client` fixture（真 PG + 真 Milvus + 跳 Neo4j，含本测 Collection 清理）
  - mock 全量回归 269 passed，零回归（224 + 29 + 16 = 269）
- **2026-06-11**：V1.5 S2.0 RAG 基础设施完成（多 KB Collection 命名 + Schema 扩展 + 生命周期）
  - 新增 [app/rag/naming.py](../app/rag/naming.py)（KB Collection 命名规则：`kb_{uuid.hex}`，唯一真相源）
  - 扩展 [app/rag/schema.py](../app/rag/schema.py) 增 `build_kb_collection_schema`（V1.0 7 字段 + kb_id 共 8 字段）
  - 扩展 [app/rag/milvus_client.py](../app/rag/milvus_client.py) 增 `create_kb_collection` / `drop_kb_collection` / `kb_collection_exists`
  - 新增 [tests/test_rag_naming_and_kb_collection.py](../tests/test_rag_naming_and_kb_collection.py) 25 用例（mock pymilvus）
  - 全量回归 224 passed，零回归
  - S2.1 决策：KB-03 entity_count 走懒计算（S2 stub 0 / S5 接通 Neo4j）；KB-05 严格按 Milvus → PG → Neo4j 顺序清理
- **2026-06-11**：V1.5 S1 阶段集成测试验收通过 ✅
  - 远程 PG（tyagent_test，AsyncPG 驱动）实测：23/23 集成测试通过（5:13）
  - 联调阶段定位并修复 4 个工程问题：
    1. **Windows + asyncpg + ProactorEventLoop 反复启停的连接池跨 loop 问题**：每个集成 fixture 末尾 `await engine.dispose()` 强制释放连接池
    2. **集成测试反复跑 lifespan 太慢**：新增 `pg_client` fixture（monkeypatch 掉 init_milvus / init_neo4j），速度降到 ~7s/case
    3. **`ORDER BY created_at` 的 PG tie 问题**：批量 insert `server_default=func.now()` 时间戳完全相同，PG 在 tie 下不保证插入顺序 → 给 service 排序加 `id` 做 tie-breaker（[chat_service.py](../app/services/chat_service.py) / [session_service.py](../app/services/session_service.py)）；测试 fixture 给每条消息显式递增 created_at
    4. **测试期望写错**：cursor pagination 测试把"取最近 N 条"误写成"取最早 N 条"，已对齐 PRD SES-06 真实语义
  - 沉淀写入 [docs/architecture.md](architecture.md) S1 段落 + 项目记忆 [[windows-asyncpg-dispose-per-test]]
- **2026-06-11**：V1.5 S1.2/S1.3 SES-09 上下文窗口 + 消息计数维护
  - 改造 [app/services/chat_service.py](../app/services/chat_service.py)：
    - `_load_history` 按 `settings.context_window_messages` 截断（system 必含、不计数）
    - 新增 `_append_message` 封装：写消息 + 一条 UPDATE 同步维护 `message_count` 与 `updated_at`
    - `stream_chat` 改走 `_append_message`，user / assistant 消息都自动维护计数
  - 新增 [tests/test_chat_service_v1_5.py](../tests/test_chat_service_v1_5.py)（**7 用例**，mock db，CI 友好）
  - 新增 [tests/test_chat_service_v1_5_integration.py](../tests/test_chat_service_v1_5_integration.py)（5 用例，真 PG 集成，待用户启 PG 跑）
  - 全量回归 199 passed + 26 skipped，零回归
- **2026-06-11**：V1.5 S1.1 会话 CRUD（SES-01~06）完成（不含 SES-09 上下文窗口）
  - 新增 [app/schemas/session.py](../app/schemas/session.py) 扩展（SessionCreateRequest / SessionUpdateRequest / SessionDetail / SessionListItem / SessionListResponse）
  - 新增 [app/schemas/message.py](../app/schemas/message.py)（MessageItem / MessageListResponse）
  - 扩展 [app/services/session_service.py](../app/services/session_service.py) 加 5 个业务方法（list / detail / update_title / delete / list_messages + get_or_raise）
  - 扩展 [app/api/v1/endpoints/sessions.py](../app/api/v1/endpoints/sessions.py)：5 个新 endpoint（GET 列表 / GET 详情 / PATCH 标题 / DELETE / GET 消息历史）
  - 新增 [tests/test_sessions_v1_5_endpoints.py](../tests/test_sessions_v1_5_endpoints.py)（**25 用例**，mock service 层，不依赖真 DB）
  - 新增 [tests/test_sessions_v1_5_integration.py](../tests/test_sessions_v1_5_integration.py)（15 用例，真 PG 集成测试，待用户启 PG 跑）
  - 全量回归 192 passed + 21 skipped，零失败
- **2026-06-11**：V1.5 S1.0b 统一响应格式 V1.0+V1.5 全覆盖
  - 主 app 挂 register_exception_handlers；老 endpoint 改包 ApiResponse；V1.0 测试同步改 + 新增 5 个 E2E（不依赖 DB）
  - 全量回归 167 passed + 6 skipped，零回归
- **2026-06-11**：V1.5 S1.0 统一响应基础设施
  - 新增 ApiResponse 容器、9 条业务错误码、BusinessError + 4 个 handler，未挂主 app（24 个单测覆盖）
  - 全量回归 162 passed + 6 skipped
- **2026-06-11**：V1.5 S0 基础设施联调验收通过 ✅
  - ping_task smoke 全链路跑通：`pong: hello-S0 @ lvjinhu`
  - 联调阶段定位并修复 Windows + Docker Desktop 上 `localhost`→IPv6 vpnkit 丢包坑（默认值锁 127.0.0.1）
  - 联调阶段定位并修复 `from-import` 遮蔽子模块导致 `importlib.reload` 失败的测试坑
  - 全量测试 138 passed + 6 skipped，零回归
- **2026-06-11**：V1.5 S0 基础设施代码完成
  - 新增 [app/tasks/](../app/tasks/)（celery_app / ping）+ [docs/celery_dev_guide.md](celery_dev_guide.md)
  - 扩展 `ChatSession`、新增 `KnowledgeBase` / `KbFile` 两张表（PRD §5.1~5.3）
  - 配置层新增 7 字段 + 2 derived property（broker/backend 缺省复用 redis_url）
  - docker-compose 加 redis:7-alpine 服务（持久化挂 d:/dockerVolumes/redis/data）
- **2026-06-11**：V1.0 基础底座收尾，V1.5 数据管理层启动
  - 新增 [v1.5_dev_plan.md](v1.5_dev_plan.md) — 子需求 ID 拆分 + 阶段依赖
  - [CLAUDE.md](../CLAUDE.md) 工作前必读追加 V1.5 PRD 与拆分计划入口
  - 进度文档表格结构升级：分 V1.0（已完成）/ V1.5（进行中）两段
- **2026-06-09**：完成 3.1、3.2
- **2026-06-10**：完成 3.3、3.4
- **2026-06-10**：PRD 升级到混合存储版（PostgreSQL + Milvus + Neo4j）
  - 删除原 PG 版 `app/models/knowledge.py` 与 pgvector 路径
  - 3.5 整段改为 Milvus 路线；新增 3.6 Neo4j 模块
  - TOL-02 工具：新 PRD 命名为 `mock_data_parser`，当前实现 `mock_weather_parser` 作为其"一种"测试场景保留
- **2026-06-10**：完成 3.5 Agentic RAG 模块（Milvus）
  - 新增 `app/rag/` 4 个核心文件（schema / milvus_client / embedding / retriever）
  - lifespan 接入 Milvus init/close（fail-fast）
  - 工具注册中心挂接 `search_knowledge_base`（async @tool）
  - 22 个单测覆盖 Schema 定义、过滤拼装、结果格式化、端到端 mock 调用、@tool 集成
  - 提供 ingest + smoke 联调脚本与 3 篇气象示例文本
  - 待用户启动本地 Milvus + 配置 Embedding API key 后执行 smoke 完成 RAG-01~05 终验收
- **2026-06-10**：完成 3.6 知识图谱模块（Neo4j）
  - 新增 `app/kg/` 6 个核心文件（neo4j_client / writer / ner / query / tool / __init__）
  - lifespan 接入 Neo4j async init/close（验证连通性 + 幂等建唯一性约束）
  - 工具注册中心挂接 `query_knowledge_graph`（async @tool）
  - `app/rag/retriever.py` `search_knowledge_base` 新增 `entity_tags` 入参支持 Graph RAG 联合（KG-04）
  - `scripts/rag_ingest.py` 整合 NER + Neo4j 写入：chunk 切完 → 并发 NER → 同步写 Milvus.entity_tags + Neo4j(Entity + MENTIONED_IN)
  - 32 个 KG 单测 + retriever 扩展测试覆盖 Cypher 结构、NER 解析/去重/软失败、查询夹值、@tool 集成
  - 提供 `scripts/kg_smoke.py`（直接 query / 带过滤 query / Agent 端到端 Graph RAG）
  - `docker-compose/docker-compose.yml` 已加 Neo4j 5.26 服务（含 APOC 插件、健康检查、固定 d:/dockerVolumes 挂载）
  - 待用户启动 Neo4j + 跑 rag_ingest 重新入库 + 执行 kg_smoke 完成 KG-01~05 终验收
- **2026-06-10**：3.5 + 3.6 联调验收全部通过，PRD V1.0 基础底座正式收尾
  - **数据**：3 篇气象文本入库 → 13 chunk → 35 唯一实体 → 19 MENTIONED_IN 关系
  - **配置定型**：LITELLM_MODEL=deepseek-v4-flash（chat）+ KG_NER_MODEL=deepseek-v4-flash（NER 解耦）+ EMBEDDING_MODEL=openai/Qwen/Qwen3-Embedding-8B（SiliconFlow 4096 维）
  - **关键修复**：
    - 修 [app/rag/embedding.py](../app/rag/embedding.py) 去掉 `dimensions` 参数（LiteLLM openai/ 路由不允许）
    - 新增 [scripts/embedding_test.py](../scripts/embedding_test.py) 独立排查 Embedding 链路
    - [app/agent/runner.py](../app/agent/runner.py) 注入 `_SYSTEM_PROMPT` 引导 Agent 正确使用工具（防止陷入工具循环）
    - [scripts/kg_smoke.py](../scripts/kg_smoke.py) 用 tool_end 兜底 tool_start 流式合并问题，确保 KG-04 验收判定可靠
  - **PRD 10 条验收点全部通过**：RAG-01/02/03/04/05 + KG-01/02/03/04/05
  - **Agent 表现亮点**：KG-04 端到端测试中，模型自动 fallback（"台风"图谱未命中 → 切 RAG 拿原文 → 从原文中抓实体回查 KG → 用实体精筛 RAG）输出 1403 字结构化报告，远超预期

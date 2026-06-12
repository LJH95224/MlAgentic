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
| T0 | 基础设施扩展（Milvus 升级 / BM25 / trace 表 / eval 表） | P0 前置 | P0 | ✅ 完成 + 单测验收 | 2026-06-12 |
| T1 | IDP-01/02/06（结构感知解析 + 切片 + 入库管道重构） | P0 | P0 | ✅ 完成 + 单测验收 | 2026-06-12 |
| T2 | HRE-03/04（BM25 + RRF 融合） | P0 | P0 | ✅ 完成 + 单测验收 | 2026-06-12 |
| T3 | OBS-01/02（Trace 采集 + 查询接口） | P0 | P0 | ✅ 完成 + 单测验收 | 2026-06-12 |
| T4 | HRE-05（Reranker 精排） | P1 | P1 | ⬜ 待开始 | — |
| T5 | CHC-01/02（Citation 注入 + 解析） | P1 | P1 | ⬜ 待开始 | — |
| T6 | UQA-01（统一查询接口 /v2/query） | P1 | P1 | ⬜ 待开始 | — |
| T7 | IDP-03/04/05（表格描述 + 双层索引 + 文档元数据） | P2 | P2 | ⬜ 待开始 | — |
| T8 | HRE-01/02/06（Query 改写 + NER + 配置项） | P2 | P2 | ⬜ 待开始 | — |
| T9 | CHC-03/04（置信度 + 答案自检） | P2 | P2 | ⬜ 待开始 | — |
| T10 | UQA-02/03/04（分层子接口） | P3 | P3 | ⬜ 待开始 | — |
| T11 | EVA-01/02/03（RAGAS 评估） | P3 | P3 | ⬜ 待开始 | — |
| T12 | OBS-03（聚合统计） | P4 | P4 | ⬜ 待开始 | — |

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
- ⬜ 用户手动验证：清 milvus volume + 重启容器 + uvicorn 启动看到新表自动创建

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
- ⬜ 集成测试：上传带标题/表格/代码的 PDF → V2 KB → Milvus chunks 含 heading_path + block_type

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
- ⬜ 集成测试：上传中英混合文档 → 查"bge-reranker-v2"（专有名词）→ BM25 路径召回成功

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
- ⬜ 集成测试：调一次 /v2/query → 查 trace_id → 验步骤完整

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

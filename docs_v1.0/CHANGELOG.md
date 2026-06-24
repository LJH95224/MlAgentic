# TyAgent 变更日志（CHANGELOG）

> 本文档合并自 `v1.5_dev_plan.md` / `v2_dev_plan.md` / `0617/xiugai.md`，记录 V1.0 → V1.5 → V2.0 + 3 批 Hardening 的版本时间线与每项关键修复。
> 原始审查报告归档在 `../docs/0617/`（`code_quality_review_2026-06-17.md` / `codex-review.md`）。
> 当前模块状态总览见 [progress.md](progress.md)。

---

## V2.0 Hermes · Hardening Batch 3 — 2026-06-23 ✅

### A P2-19 · 静默吞异常审视

- 盘点：全仓 `except Exception` 共 **97 处** / 34 个文件；其中 60 处已带 `# noqa: BLE001`，37 处未标注。
- 语义分类（37 处）：14 重抛 / 10 降级 / 10 软失败 / 2 资源 close / 1 AGT-04 错误反思 / **1 真正裸吞**。
- 关键修复：[app/observability/analytics_writer.py](../app/observability/analytics_writer.py) `write_analytics_snapshot` 内层 rollback 失败补 `logger.warning("Analytics rollback 失败（session 可能已损坏）: %s", rb_err)`。
- 全仓标注：其余 35 处合规 broad except 统一加 `# noqa: BLE001`（零语义变更）；全仓 97 处现 100% 有标注。
- 测试：[tests/test_v2_t12.py](../tests/test_v2_t12.py) 新增 `test_rollback_failure_logs_warning`。

### A P2-15 · 4 hot 模块补 41 case 单测

| 模块 | 文件 | case 数 | 覆盖 |
|---|---|---|---|
| RAG embedding | [tests/test_rag_embedding.py](../tests/test_rag_embedding.py) | 12 | `_build_kwargs` 拼装 / `aembed_texts` 维度校验 / 乱序排序 / Pydantic 响应 / 异常透传 |
| LLM messages | [tests/test_llm_messages.py](../tests/test_llm_messages.py) | 12 | SimpleMessages 3 + Assistant 4 + ToolResult 3 + DefineTool 2，含 assistant↔tool_result 闭环引用（ReAct 工具协议核心契约） |
| KG writer | [tests/test_kg_writer_behavior.py](../tests/test_kg_writer_behavior.py) | 11 | mock AsyncDriver → session → execute_write → tx.run 全链路；含同名不同类型复合键独立性 |
| async_utils | [tests/test_async_utils_edges.py](../tests/test_async_utils_edges.py) | 7 | 空列表短路 / 超时日志埋点 / 异常透传 / Iterable generator 兼容 |

### B L-06 · trace 列表 N+1 修复

- 问题：[app/api/v2/endpoints/traces.py](../app/api/v2/endpoints/traces.py) `list_session_traces` 取本页 N 条 root_step 后每条单独 `SELECT count(*)`，page_size=20 时一次列表 = 22 条 SQL。
- 修复：本页 N 个 trace_id 一次 `WHERE trace_id IN [...]` + `GROUP BY trace_id` 拿全 step_count；空页跳过 group-by 避免 `IN ()` 语法错。整端点稳定 3 条 SQL。
- 测试：[tests/test_v2_t3.py](../tests/test_v2_t3.py) 新增 2 case 断言 SQL 次数与 step_count_map 映射。

### 驳回/搁置项（性价比倒挂）

- L-01 Tracer 禁用 yield 空 step（disabled 短路，期望行为）
- L-02 citation 同号引用顺序（现实现已正确）
- L-03 splitter `_TOKEN_LEN_FN` 线程安全（GIL + 单事件循环下无正确性问题）
- L-04 裸 except 分级（已统一标 BLE001）
- L-05 task_id 二次 commit（结构必要）
- L-07 ragas stub 注入（依赖链兼容代价，非项目代码问题）
- A P2-16/17/18（长函数拆分 / 命名统一 / 魔法数字配置化）—— 改动面广风险倒挂

---

## V2.0 Hermes · Hardening Batch 2 — 2026-06-22 ✅

> 全量 `pytest tests/` 850 passed / 41 skipped / 0 failed。

### 🔴 影响运维 / 数据一致性

#### B M-01 · 引入 Alembic 迁移体系

- 新增 [alembic.ini](../alembic.ini)（`sqlalchemy.url` 留空，env.py 注入）
- 新增 [alembic/env.py](../alembic/env.py)（async 版本，`async_engine_from_config` + `connection.run_sync`；`compare_type=True` + `compare_server_default=True`；NullPool；asyncpg 关 SSL 兼容 Windows）
- 新增 `alembic/script.py.mako`（中文 docstring 模板）
- [requirements.txt](../requirements.txt) 数据库段加 `alembic>=1.13.0`
- [README.md](../README.md) 新增 §2.5「数据库迁移（Alembic）」段
- **附带修复**：[tests/test_kb06_chat_scope.py](../tests/test_kb06_chat_scope.py) 6 个 monkeypatch 路径从 `app.rag.retriever.aembed_texts` 改为 `app.rag.hybrid_retriever.aembed_texts`

#### A P1-9 · KB / KbFile 删除 Milvus / Neo4j 清理补偿

- **数据模型**：`kb_files` / `knowledge_bases` 各加 `deleting` / `pending_cleanup` 状态值 + `cleanup_retry_count` 字段；KB 表补 `updated_at`（index + onupdate）。
- **主路径**（"失败降级为补偿"）：[app/services/kb_service.py](../app/services/kb_service.py) / [app/services/kb_file_service.py](../app/services/kb_file_service.py) 改为四阶段：revoke → 标 deleting(commit) → 同步清外存(返 bool) → 全成功真删 / 任一失败改 pending_cleanup。DELETE 端点对用户始终返 200。
- **listing 过滤**：`list_kbs` / `list_files` 默认隐藏 `deleting` / `pending_cleanup`；详情接口仍可查到供运维诊断。
- **补偿 reaper**：新增 [app/tasks/cleanup_reaper_task.py](../app/tasks/cleanup_reaper_task.py)，复用 reaper 骨架扫 `pending_cleanup` 行重试；超 `CLEANUP_REAPER_MAX_RETRY` 仅告警。
- 测试：新增 [tests/test_kb_compensation.py](../tests/test_kb_compensation.py) 23 case。

#### B M-06 · V2 filter 增加 `kb_id` 兜底过滤

- [app/rag/filters.py](../app/rag/filters.py) `_build_filter_expr` 加 `kb_ids: list[str] | None`，注入 `kb_id IN [...]` 子句；[app/rag/hybrid_retriever.py](../app/rag/hybrid_retriever.py) 调用时把 contextvar `current_kb_ids` 序列化传入。安全双保险：contextvar + 显式参数。

### 🟡 可观测性 / 性能

#### B M-04 · Trace 写入异步化

- [app/observability/tracer.py](../app/observability/tracer.py) `__aexit__` 改为 `asyncio.create_task(self._flush_to_db())` + `_trace_flush_done` 回调（捕获 task.exception() 仅 warning）。边角降级 `RuntimeError`（无 running loop）时退回同步 await。
- 测试：[tests/test_v2_t3.py](../tests/test_v2_t3.py) 新增 `test_tracer_exit_schedules_flush_as_task`。

### 🟢 清理 + 测试覆盖

- **A P2-12**：删除 `app/tasks/ingest_task_v1.py` 死代码。
- **B M-07**：[tests/test_rag_retriever.py](../tests/test_rag_retriever.py) 补 `test_do_search_top_k_boundary_values_preserved`（4 个边界：1/50/-3/51）。

### Batch 1 第二轮补完（B 报告 M 级 4 项）

- **B M-11**：`bm25_contributed` 改为基于 retrieve step_output 真实判定（`bm25_enabled=True` 且 `hit_count>0`）。
- **A P1-8**：`hybrid_search` 全 collection 失败时冒泡 `RuntimeError`，对齐 AGT-04 错误反思链路。单 collection 失败直接抛；多 collection 部分失败保留幸存者；全部失败抛出。
- **A P2-13**：抽 [app/rag/filters.py](../app/rag/filters.py) 模块，`hybrid_retriever` 不再 `from app.rag.retriever import _build_filter_expr`；retriever 通过 re-export 保持对外契约。
- **A P1-11**：卡死 `processing` 文件回收周期任务。新增 [app/tasks/reaper_task.py](../app/tasks/reaper_task.py)，默认每 10 分钟扫一次，阈值 35min（= Celery hard timeout 30min + 5min 缓冲）。[app/models/kb_file.py](../app/models/kb_file.py) 加 `updated_at` 字段。测试 14 case。

---

## V2.0 Hermes · Hardening Batch 1 — 2026-06-18 ✅

### A 报告 P0 / B 报告 H 高优先级

- **A P0-1**：补充 CORS 中间件与配置项（[app/main.py](../app/main.py) / [app/core/config.py](../app/core/config.py)）。
- **A P0-2**：Neo4j 写入软失败不再静默标 completed —— `kb_files.doc_metadata._ingest_warnings.neo4j_failed=True` 标记降级完成。
- **A P0-3**：Milvus 写入成功后 PG 更新失败时，`_mark_failed_safe()` 在标记 failed 前尽力清理任务级 Milvus / Neo4j 残留。
- **A P0-4**：新增 [app/core/async_utils.py](../app/core/async_utils.py) `wait_for_named()` / `gather_with_timeout()` 公共异步超时工具。
- **B H-01**：[app/rag/reranker.py](../app/rag/reranker.py) `NoopReranker` 保留原始检索分数（`float(chunk.get("score", 0.0))`），避免置信度被拉满。
- **B H-06**：Trace 查询不存在改用 `BusinessError(error_codes.NOT_FOUND, ...)` 替代 HTTPException。
- **B H-07 / A P2-14**：[app/rag/retriever.py](../app/rag/retriever.py) 新增 `_milvus_str()` 做 filter 表达式字符串转义。

### A 报告 P1 中优先级

- **A P1-5**：6 处 `asyncio.gather` 外层接入 `gather_with_timeout()`（5 个当前生产调用点；`ingest_task_v1.py` 历史归档已删）。
- **A P1-6**：入库管道同步 Milvus 写入用 `asyncio.to_thread` 包裹，避免阻塞事件循环。
- **A P1-7**：[app/tasks/session_task.py](../app/tasks/session_task.py) / [app/kg/ner.py](../app/kg/ner.py) / [app/llm/client.py](../app/llm/client.py) 遗漏的 LiteLLM 调用加外层 `asyncio.wait_for`。
- **A P1-10**：`create_v2_kb_collection()` 支持显式 client，避免运行时替换模块级 `_client`。

### 本地服务联调发现

- **V2 REST 统一响应契约**：`GET /api/v2/analytics` 改为 `ApiResponse[AnalyticsResponse]` 包装。
- **V2 generate 错误码契约**：`context_chunks=[]` 改为允许进入 endpoint 返回 `42201 CONTEXT_CHUNKS_EMPTY`。

### B 报告 H/M 级修复

- **B H-02 / A-01**：Agent 工具 `search_knowledge_base` 委托 `hybrid_search()`，Agent ReAct 主动检索与 `/api/v2/query` 同享 BM25/RRF/Reranker 与 V2 结构字段。
- **B M-09 / M-10**：（详见原始 xiugai.md）

---

## V2.0 Hermes · 功能开发 T0~T12 — 2026-06-12 ~ 2026-06-16 ✅

> 详细拆分原文：`../docs/v2_dev_plan.md`。
> **核心目标**：把 RAG 从"能跑通"升级为"效果可信赖"。

### 已确认的关键决策

| 决策点 | 选择 | 影响 |
|---|---|---|
| BM25 方案 | Milvus 2.5+ 稀疏向量（`SPARSE_FLOAT_VECTOR` + `SPARSE_INVERTED_INDEX` + BM25 Function） | 同 Collection 稠密+稀疏，content `enable_analyzer=True` |
| Reranker | 在线 API（LiteLLM 网关），首选 SiliconFlow `BAAI/bge-reranker-v2-m3` | 不本地化 |
| V1.5 KB 数据 | 清空重来（用户已确认） | 上线时删 milvus volume + PG drop_all |
| RAGAS 评估 | 官方 ragas 库 + LiteLLM 代理 | 已稳定可跑 |

### T0 · 基础设施扩展 — 2026-06-12

- 配置项扩展（[app/core/config.py](../app/core/config.py) 新增 8 字段：reranker_type/model/api_key/api_base/similarity_threshold + bm25_enable + rrf_k + trace_enable/retention_days）
- 新表：`agent_traces`（13 字段）+ `eval_tasks`（12 字段）+ `query_analytics`
- KB 扩展：`retrieval_config` JSONB + `doc_metadata_schema` JSONB；KbFile 扩展：`doc_metadata` JSONB + `summary_brief` Text
- V2 Milvus Schema：[app/rag/schema.py](../app/rag/schema.py) `build_v2_kb_collection_schema` + `build_v2_index_params`（15 字段，含 sparse_vector）；[app/rag/milvus_client.py](../app/rag/milvus_client.py) `create_v2_kb_collection`
- 单测：52 通过；V1.5 全量回归 472 passed + 6 skipped（零回归）
- **关键决策**：Milvus 镜像 `v2.6.18` 已 > 2.5，不需要再升

### T1 · 智能文档处理（IDP-01/02/06）— 2026-06-12

- 结构感知解析：[app/ingest/parser.py](../app/ingest/parser.py) `StructuredBlock` + `parse_document_structured()` + 4 个结构感知解析器（PDF 按字号/粗体推断标题；DOCX 读 style；MD token；TXT 按段）
- 结构感知切片：[app/ingest/structured_splitter.py](../app/ingest/structured_splitter.py)（代码块/表格整块保留 → 标题+段落组合 → 超长 RecursiveCharacterTextSplitter 兜底）
- 入库管道重构：[app/tasks/ingest_task.py](../app/tasks/ingest_task.py)（7 步 → 11 步）
- Milvus V2 写入：15 字段（含 heading_path / block_type / sparse_vector）
- 单测：50 通过；V1.5 全量回归 522 passed + 6 skipped（零回归）

### T2 · 混合检索引擎（HRE-03/04）— 2026-06-12

- V2 Schema BM25 Function：content 字段 `enable_analyzer=True` + `Function(content→sparse_vector, BM25)`，Milvus 插入时自动生成稀疏向量
- 索引参数：bm25_k1=1.2 / bm25_b=0.75 / drop_ratio_build=0.2
- 混合检索：[app/rag/hybrid_retriever.py](../app/rag/hybrid_retriever.py) `hybrid_search()` + `RRFRanker`（k=60）；dense + BM25 双路融合
- 降级策略：BM25 失败→纯向量；`bm25_enable=False`→纯向量
- 单测：17 通过

### T3 · 可观测性 Trace（OBS-01/02）— 2026-06-12

- Trace 采集：[app/observability/tracer.py](../app/observability/tracer.py)（异步上下文管理器，自动记录 step 树）
- 查询接口：[app/api/v2/endpoints/traces.py](../app/api/v2/endpoints/traces.py)（列表 / 详情）

### T4 · Reranker 精排（HRE-05）— 2026-06-15

- [app/rag/reranker.py](../app/rag/reranker.py)：LiteLLM 网关 + SiliconFlow `BAAI/bge-reranker-v2-m3`；`NoopReranker` 兜底
- 相似度阈值过滤（`reranker_similarity_threshold`）

### T5 · Citation 注入 + 解析（CHC-01/02）— 2026-06-15

- [app/agent/citation.py](../app/agent/citation.py)：检索结果注入引用编号 `[1][2]`；模型回答中解析引用并对应回 chunk

### T6 · 统一查询接口（UQA-01）— 2026-06-15

- [app/api/v2/endpoints/query.py](../app/api/v2/endpoints/query.py) `POST /v2/query`：一站式 query → retrieve → rerank → generate → cite

### T7 · 表格描述 + 双层索引 + 文档元数据（IDP-03/04/05）— 2026-06-15

- 表格描述：[app/ingest/table_describer.py](../app/ingest/table_describer.py)（LLM 生成表格语义描述参与检索）
- 双层索引：fine（细粒度切片）+ coarse（文档级摘要）；[app/ingest/coarse_indexer.py](../app/ingest/coarse_indexer.py)
- 文档元数据：上传时按 KB 的 `doc_metadata_schema` 填充

### T8 · Query 改写 + NER + 三层配置（HRE-01/02/06）— 2026-06-15

- Query 改写：[app/rag/query_rewriter.py](../app/rag/query_rewriter.py)
- Query NER：[app/rag/query_ner.py](../app/rag/query_ner.py)（query 实体抽取，注入 `entity_tags` 过滤）
- 三层配置合并：系统默认 → KB 级 `retrieval_config` → 请求级覆盖

### T9 · 置信度评分 + 答案自检（CHC-03/04）— 2026-06-15

- [app/agent/confidence.py](../app/agent/confidence.py)：综合检索分数 + Reranker 分数 + Citation 命中率
- [app/agent/self_check.py](../app/agent/self_check.py)：LLM 自检答案与 context 一致性

### T10 · 分层子接口（UQA-02/03/04）— 2026-06-16

- `POST /v2/query/retrieve` / `POST /v2/query/rerank` / `POST /v2/query/generate`：分阶段独立调用

### T11 · RAGAS 评估（EVA-01/02/03）— 2026-06-16

- [app/eval/](../app/eval/)：faithfulness / answer_relevancy / context_precision / context_recall 四指标
- [app/tasks/eval_task.py](../app/tasks/eval_task.py)：Celery 异步评估任务
- LiteLLM 代理适配 ragas 官方库

### T12 · 聚合统计（OBS-03）— 2026-06-16

- [app/observability/analytics_writer.py](../app/observability/analytics_writer.py) + `query_analytics` 表：BM25 贡献率 / 平均延迟 / Token 总量 / Reranker 命中率

### Bugfix · V2 query 超时卡死 — 2026-06-16

详见 `../docs/progress.md` 历史变更段。

### A.1 · Reranker 调优工具链 — 2026-06-16

- 详见 `../docs/eval_a1_reranker_tuning.md`（已归档）
- **结论待定**：A.1 在 ~150 chunks 上的"Qwen3-Reranker-8B 弊大于利"是样本量噪音；社区共识 Qwen3 > bge；A.2 重评估等扩到 500+ chunks 再做。

---

## V1.5 数据管理层 — 2026-06-11 ~ 2026-06-12 ✅

> 详细拆分原文：`../docs/v1.5_dev_plan.md`。
> 迭代完成日期 **2026-06-12**，端到端 smoke 全链路验收通过。

| 阶段 | 模块 | 完成日期 |
|---|---|---|
| **S0** | 基础设施（Celery 5 + Redis 7 + DB 迁移） | 2026-06-11 |
| **S1** | 会话管理 CRUD（SES-01~06 / SES-09） | 2026-06-11 |
| **S2** | 知识库 CRUD + Milvus 多 Collection（KB-01~05） | 2026-06-11 |
| **S3** | 文件上传 + 异步入库（FILE-01~05 / TASK-02/03） | 2026-06-11 |
| **S4** | 会话标题/摘要异步生成（SES-07/08 / TASK-04/05） | 2026-06-11 |
| **S5** | KB 关联对话 + 端到端联调（KB-06） | 2026-06-12 |

### S0 联调阶段关键经验（已沉淀到项目记忆）

- **Windows + Docker Desktop 必须用 `127.0.0.1`**：`localhost` 走 IPv6 `::1` + vpnkit 应用层丢包，redis-cli PING 永远等不到响应。`Settings.redis_url` 默认值固化为 `redis://127.0.0.1:6379/0`。
- **`from-import` 遮蔽子模块**：`app/tasks/__init__.py` 写 `from app.tasks.celery_app import celery_app` 会让 `app.tasks.celery_app` 模块名被实例对象遮蔽；单测里 reload 必须从 `sys.modules["app.tasks.celery_app"]` 拿真模块对象。
- **broker 连接重试限制**：`broker_connection_max_retries=3` + `broker_connection_timeout=4`，避免 Redis 不通时 `.delay()` 无限卡死。

---

## V1.0 基础底座 — 2026-06-09 ~ 2026-06-10 ✅

> 详细拆分原文：`../docs/progress.md` 第 735 行后。
> PRD 路线变更（2026-06-10）：存储架构由 "PostgreSQL + pgvector" 调整为 "PostgreSQL + Milvus + Neo4j" 三库协同；3.5 整段重写为 Milvus 版；新增 3.6 知识图谱模块。

| 模块 | PRD 章节 | 完成日期 |
|---|---|---|
| **3.1** 接入与通信（FastAPI + SSE 双通道） | 3.1 | 2026-06-09 |
| **3.2** LLM 路由（LiteLLM 统一网关） | 3.2 | 2026-06-09 |
| **3.3** Agent 编排（LangGraph ReAct，max_iterations=5） | 3.3 | 2026-06-10 |
| **3.4** 本地执行工具（subprocess + 30s 超时） | 3.4 | 2026-06-10 |
| **3.5** Agentic RAG（Milvus + Embedding 4096 维） | 3.5 | 2026-06-10 |
| **3.6** 知识图谱（Neo4j） | 3.6 | 2026-06-10 |

### 关键架构契约

- **`agent.runner.run_stream()` 是 Agent ↔ Service 之间的唯一接口**——后续模块替换内核时 API/Service 层无需改动。
- **AGT-03 ReAct 熔断**：LangGraph 循环最大轮次 5，超过强制终止。
- **AGT-04 错误反思注入**：Tool 异常时捕获堆栈以 ToolMessage 形式回传，模型自我修正后重试。
- **`asyncpg.connect_args={"ssl": False}`**：解决 Windows 上 asyncpg SSL 探测的 `[WinError 121]` 信号灯超时。

---

## 当前待办

- **A.2 Qwen3-Reranker-8B 重评估**：阻塞依赖——扩文档库到 500+ chunks（用户业务侧动作）。

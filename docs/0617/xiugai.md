# 2026-06-17 审查报告修复 TODO

> 来源：
> - A：[code_quality_review_2026-06-17.md](code_quality_review_2026-06-17.md)
> - B：[codex-review.md](codex-review.md)
>
> 原则：V2.0 需求已完成，后续以审查报告为输入做 hardening / quality 修复；优先处理会阻塞前端、造成数据不一致、导致超时挂死或影响答案可信度的问题。每批修复完成后同步合并到 [../progress.md](../progress.md)。

---

## 一、已纳入当前批次（fix/v2-quality-batch1）

### P0 / H 高优先级

- [x] A P0-1：补充 CORS 中间件与配置项。
  - 文件：[../../app/main.py](../../app/main.py)、[../../app/core/config.py](../../app/core/config.py)
  - 状态：当前工作区已实现，待测试验证。

- [x] A P0-2：Neo4j 写入软失败时，不能静默标记为完全 completed。
  - 目标：在 `KbFile.metadata` 或等价字段记录 `neo4j_failed=True` / 错误摘要，使前端和运维能区分“无实体”和“图谱写入失败”。
  - 结果：写入 `kb_files.doc_metadata._ingest_warnings.neo4j_failed=True`，主链路仍 completed 但可观测为降级完成。

- [x] A P0-3：Milvus 写入成功后 PG 更新失败时增加失败回滚/补偿清理。
  - 目标：失败路径尽量清理 Milvus / Neo4j 残留，避免永久数据孤岛。
  - 结果：`_mark_failed_safe()` 在标记 failed 前尽力清理任务级 Milvus / Neo4j 残留。

- [x] A P0-4：DRY 重构第一步：公共异步超时工具。
  - 文件：[../../app/core/async_utils.py](../../app/core/async_utils.py)、[../../tests/test_async_utils.py](../../tests/test_async_utils.py)
  - 状态：已新增 `wait_for_named()` / `gather_with_timeout()`，并接入本批 gather / LiteLLM 调用点。

- [x] B H-01：NoopReranker 保留原始检索分数，避免置信度被强行拉满。
  - 文件：[../../app/rag/reranker.py](../../app/rag/reranker.py)
  - 状态：当前代码已改为 `float(chunk.get("score", 0.0))`，待测试验证。

- [x] B H-06：Trace 查询不存在时使用 BusinessError，而非 HTTPException。
  - 文件：[../../app/api/v2/endpoints/traces.py](../../app/api/v2/endpoints/traces.py)
  - 状态：当前代码已改为 `BusinessError(error_codes.NOT_FOUND, ...)`。

- [x] B H-07 / A P2-14：Milvus filter 表达式字符串转义。
  - 文件：[../../app/rag/retriever.py](../../app/rag/retriever.py)
  - 状态：当前代码已新增 `_milvus_str()`。

### P1 中优先级

- [x] A P1-5：6 处 `asyncio.gather` 外层加 `wait_for`。
  - 目标文件：
    - [../../app/api/v2/endpoints/query.py](../../app/api/v2/endpoints/query.py)
    - [../../app/rag/query_ner.py](../../app/rag/query_ner.py)
    - [../../app/ingest/dual_layer.py](../../app/ingest/dual_layer.py)
    - [../../app/ingest/table_description.py](../../app/ingest/table_description.py)
    - [../../app/tasks/ingest_task.py](../../app/tasks/ingest_task.py)
    - [../../app/tasks/ingest_task_v1.py](../../app/tasks/ingest_task_v1.py)（历史归档，低优先级，暂不改）
  - 结果：5 个当前生产调用点已接入 `gather_with_timeout()`，历史归档文件保留待 P2 清理。

- [x] A P1-6：入库管道同步 Milvus 写入用 `asyncio.to_thread` 包裹，避免阻塞事件循环。
  - 目标文件：[../../app/tasks/ingest_task.py](../../app/tasks/ingest_task.py)

- [x] A P1-7：遗漏的 LiteLLM 调用增加外层 `asyncio.wait_for`。
  - 目标文件：
    - [../../app/tasks/session_task.py](../../app/tasks/session_task.py)
    - [../../app/kg/ner.py](../../app/kg/ner.py)
    - [../../app/llm/client.py](../../app/llm/client.py)

- [x] A P1-10：`create_v2_kb_collection()` 支持显式 client，避免运行时替换模块级 `_client`。
  - 目标文件：[../../app/rag/milvus_client.py](../../app/rag/milvus_client.py)、[../../app/tasks/ingest_task.py](../../app/tasks/ingest_task.py)

### 本地服务联调发现并已修复

- [x] V2 REST 统一响应契约：`GET /api/v2/analytics` 原先返回裸 `AnalyticsResponse`，现改为 `ApiResponse[AnalyticsResponse]` 包装。
  - 目标文件：[../../app/api/v2/endpoints/analytics.py](../../app/api/v2/endpoints/analytics.py)、[../../tests/test_v2_t12.py](../../tests/test_v2_t12.py)
  - 验证：本地服务复测 `GET /api/v2/analytics` 返回 `code=0/message=success/data={...}`。

- [x] V2 generate 错误码契约：`context_chunks=[]` 原先被 Pydantic 拦截为 `40001`，现允许进入 endpoint 并返回 `42201 CONTEXT_CHUNKS_EMPTY`。
  - 目标文件：[../../app/schemas/v2/generate.py](../../app/schemas/v2/generate.py)、[../../tests/test_v2_t10.py](../../tests/test_v2_t10.py)
  - 验证：本地服务复测空列表与缺省 `context_chunks` 均返回 HTTP 422 + 业务码 `42201`。

### B 报告高/中优先级待排期

- [x] B H-02 / A-01：Agent 工具检索与 V2 检索能力不对等。
  - 结果：`search_knowledge_base` 已委托 `hybrid_search()`，Agent ReAct 主动检索与 `/api/v2/query` 同享 BM25/RRF/Reranker 与 V2 结构字段。

- [x] B H-03：multi_query 二次 RRF 分数归一化，避免 confidence 语义失真。
  - 结果：`_multi_query_search` 按有效检索路径数归一化 RRF 分数，空结果/异常路径不计入分母。

- [x] B H-04：`/v2/retrieve` 返回 `vector_score` / `bm25_score` / `rrf_score` 分项分数。
  - 结果：`HybridSearchResult` 新增 `vector_score` / `bm25_score` / `rrf_score` / `rerank_score`；`/api/v2/retrieve` 响应逐字段透出。当前 Milvus `hybrid_search + RRFRanker` 不暴露 BM25 单路原始分数，`bm25_score` 暂为 `None`，`rrf_score` 表示融合分数。

- [x] B H-05：`/v2/retrieve` 接入 Trace，返回非空 `trace_id`。
  - 结果：`/api/v2/retrieve` 已记录 `query_ner` / `graph_anchor` / `retrieve` 三步 Trace，成功与软失败响应均返回 `trace_id`。

- [x] B M-09 / M-10：统一 LLM 调用入口与厂商前缀推断逻辑。
  - 结果：`app.llm.client` 新增 `build_completion_kwargs()`，统一 chat completion 类调用的 model/api_key/api_base/timeout/num_retries 组装；`_resolve_model_name()` 改为 LiteLLM provider 白名单判断，修复 `Qwen/Qwen3-*` 这类模型命名空间被误判为 provider 前缀的问题；Query rewrite、KG NER、IDP、Faithfulness、Session/Eval、V2 query/generate 已接入。

---

## 二、Batch 1 已完成（fix/v2-quality-batch1 第二轮，2026-06-22）

本会话沿用 `fix/v2-quality-batch1` 分支，把 B 报告 M 级中影响最大的 4 项补完：

- [x] **B M-11**：`bm25_contributed` 改为基于 retrieve step_output 真实判定。
  - 文件：[../../app/observability/analytics_writer.py](../../app/observability/analytics_writer.py)、[../../app/api/v2/endpoints/query.py](../../app/api/v2/endpoints/query.py)、[../../app/api/v2/endpoints/retrieve.py](../../app/api/v2/endpoints/retrieve.py)
  - 口径：retrieve step `bm25_enabled=True` 且 `hit_count>0` 才算贡献。`bm25_enable=False` 或检索 0 命中均不算。
  - 测试：[../../tests/test_v2_t12.py](../../tests/test_v2_t12.py) 新增 3 个口径专项 case。

- [x] **A P1-8**：`hybrid_search` 全 collection 失败时冒泡 `RuntimeError`，对齐 AGT-04 错误反思链路。
  - 文件：[../../app/rag/hybrid_retriever.py](../../app/rag/hybrid_retriever.py)
  - 单 collection 失败直接抛；多 collection 部分失败保留幸存者；全部失败抛出（带原始异常链）。降级 hybrid→dense 保留（产品决策）。
  - 测试：[../../tests/test_v2_t2.py](../../tests/test_v2_t2.py) 新增 2 case。

- [x] **A P2-13**：抽 [../../app/rag/filters.py](../../app/rag/filters.py) 模块，`hybrid_retriever` 不再 `from app.rag.retriever import _build_filter_expr, get_current_role`。
  - retriever 通过 re-export 保持对外契约不变，所有 `monkeypatch` 路径仍有效。

- [x] **A P1-11**：卡死 `processing` 文件回收周期任务。
  - 文件：[../../app/models/kb_file.py](../../app/models/kb_file.py)（新增 `updated_at` 字段）、[../../app/tasks/reaper_task.py](../../app/tasks/reaper_task.py)（新建）、[../../app/tasks/celery_app.py](../../app/tasks/celery_app.py)（beat_schedule）、[../../app/core/config.py](../../app/core/config.py)（3 个新配置）
  - 默认每 10 分钟扫一次，阈值 35min（= Celery hard timeout 30min + 5min 缓冲）。复用 `_mark_failed_safe` 走标准失败补偿。
  - 测试：[../../tests/test_reaper_task.py](../../tests/test_reaper_task.py) 14 个 case 全过。
  - **部署注意**：已有 PG 需手动 `ALTER TABLE kb_files ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(); CREATE INDEX ix_kb_files_updated_at ON kb_files (updated_at);` —— 此后 B M-01 已引入 Alembic（详见下方），新部署走 `alembic upgrade head` 即可，本痛点已消除。

---

## 三、Batch 2 已完成（2026-06-22）

> 目标：把 B 报告 / A 报告剩余 P1/M 级项消化掉，让运维 & 数据一致性达到生产可上线水平。预计半天到一天。
>
> **6 项全部完成**：B M-01 Alembic / A P1-9 KB 删除补偿 / B M-06 kb_id 兜底 / B M-04 Trace 异步 / A P2-12 删死代码 / B M-07 top_k 边界。全量 `pytest tests/` 850 passed / 41 skipped / 0 failed。

按"对产品的影响 × 工作量"排序：

### 🔴 影响运维 / 数据一致性

- [x] **B M-01：引入 Alembic 迁移体系**（已完成 2026-06-22）
  - 痛点：本会话给 `kb_files` 加 `updated_at` 需手动 `ALTER TABLE`，下次还会遇到。项目早期一次性引入成本最低。
  - 步骤：`alembic init` → 配 `env.py` 接 `Base.metadata` + `DATABASE_URL` → `alembic revision --autogenerate -m "baseline"` 生成对应当前所有模型的迁移 → `alembic stamp head`（已部署的环境直接 stamp，不实跑） → 补一个 P1-11 的 `updated_at` 增量迁移作为示范。
  - 修改：新增 `alembic/`、修改 [../../app/main.py](../../app/main.py) `_create_all_with_retry` 注释（保留兼容，新部署仍可用）、补 `Makefile` 或 README 一条 `alembic upgrade head` 指令。
  - 预估：2~3 小时。
  - **实际交付**：
    - 新增 [../../alembic.ini](../../alembic.ini)（`sqlalchemy.url` 故意留空，env.py 注入）
    - 新增 [../../alembic/env.py](../../alembic/env.py)（async 版本，`async_engine_from_config` + `connection.run_sync`；从 `app.core.config.get_settings().database_url` 拿 URL；`compare_type=True` + `compare_server_default=True`；NullPool；asyncpg 关 SSL 兼容 Windows）
    - 新增 [../../alembic/script.py.mako](../../alembic/script.py.mako)（迁移脚本模板，中文 docstring）
    - 新增 [../../alembic/versions/.gitkeep](../../alembic/versions/.gitkeep)（空目录占位）
    - [../../requirements.txt](../../requirements.txt) 数据库段加 `alembic>=1.13.0`
    - [../../app/main.py](../../app/main.py) `_build_v2_compat_alter_sql` / `_create_all_with_retry` 注释更新（明确"开发态兜底，新部署走 alembic upgrade head"）
    - [../../README.md](../../README.md) 新增 §2.5「数据库迁移（Alembic）」段，含新部署 / 旧库 stamp / 新增迁移三种姿势
  - **偏离说明**：原计划"补一个 P1-11 `updated_at` 增量迁移作示范"取消——P1-11 的 `updated_at` 字段在本会话已合并进 [../../app/models/kb_file.py](../../app/models/kb_file.py)，autogenerate baseline 会一并捕获，没法再单独"补"一个增量。增量迁移的"示范"等下一次真正改 schema 时（如 B M-06 / A P1-9）自然演示。
  - **附带修复**：B M-01 验收回归时发现 [../../tests/test_kb06_chat_scope.py](../../tests/test_kb06_chat_scope.py) 6 个 case 全部 ERROR——monkeypatch 路径 `app.rag.retriever.aembed_texts` 已失效（retriever 在 P2-13 后委托给 hybrid_search，不再 import aembed_texts）。改为 `app.rag.hybrid_retriever.aembed_texts` 修复，6 case 恢复 GREEN。
  - **用户验收步骤**（CLAUDE.md 约定依赖安装与运行类命令归用户）：
    ```bash
    conda activate geo_agent
    uv pip install alembic -i https://pypi.tuna.tsinghua.edu.cn/simple
    # 1. 干净库上生成 baseline（或对现有库 stamp head）
    alembic revision --autogenerate -m "baseline_v2"
    # 2. 人工检查 alembic/versions/<timestamp>_baseline_v2.py
    #    应包含 7 张表（chat_sessions/chat_messages/knowledge_bases/kb_files/
    #    agent_traces/eval_tasks/query_analytics）+ V2.0 字段 + 索引 + CheckConstraint
    # 3. 双向校验
    alembic upgrade head
    alembic downgrade base
    alembic upgrade head
    # 4. 旧库姿势
    alembic stamp head && alembic current   # 输出 head revision id
    # 5. 回归
    pytest tests/ -v --tb=short
    ```

- [x] **A P1-9：删除 KB / KB File 时 Milvus / Neo4j 清理增加重试与补偿队列**（已完成 2026-06-22）
  - 现状未确认，需先看 [../../app/services/kb_file_service.py](../../app/services/kb_file_service.py) 与 KB 删除接口。类似 P1-11 的回收任务模式：删除时主路径同步清；失败 / 部分失败时写入"待补偿"标记，由周期任务捞起来重试。
  - 可复用 `reaper_task` 的 Celery beat 框架。
  - 预估：1~2 小时。
  - **实际交付**：
    - **数据模型**：[../../app/models/kb_file.py](../../app/models/kb_file.py) / [../../app/models/knowledge_base.py](../../app/models/knowledge_base.py) 各加 `deleting` / `pending_cleanup` 两个状态值 + `cleanup_retry_count` 字段；KB 表补 `updated_at`（带 index + onupdate，reaper 扫描排序用）。
    - **主路径改造**（"失败降级为补偿"语义）：[../../app/services/kb_service.py](../../app/services/kb_service.py) `delete_kb` 与 [../../app/services/kb_file_service.py](../../app/services/kb_file_service.py) `delete_file` 改为四阶段——revoke → 标 deleting(commit) → 同步清外存(Milvus/Neo4j 各返 bool) → 全成功真删 / 任一失败改 pending_cleanup。DELETE 端点对用户始终返 200，不再因外存抖动抛 500。
    - **`_cleanup_*` 函数改返 bool**：`_cleanup_milvus_chunks_for_file` / `_cleanup_neo4j_entities_for_file` / `_cleanup_kb_neo4j` / 新增 `_safe_drop_kb_collection`，外存真失败返 False 让调用方决策补偿。
    - **listing 过滤**：`list_kbs` / `list_files` 默认隐藏 `deleting` / `pending_cleanup`（用户视角已删）；详情接口仍可查到 pending_cleanup 行便于运维诊断。
    - **补偿 reaper**：新增 [../../app/tasks/cleanup_reaper_task.py](../../app/tasks/cleanup_reaper_task.py)，复用 reaper_task 骨架扫 `pending_cleanup` 行重试外存清理，成功真删 / 失败 `cleanup_retry_count += 1`，超 `CLEANUP_REAPER_MAX_RETRY` 仅告警。注册到 [../../app/tasks/celery_app.py](../../app/tasks/celery_app.py) beat_schedule（`reap-pending-cleanup`，默认 5min 一轮）。
    - **配置**：[../../app/core/config.py](../../app/core/config.py) 加 `cleanup_reaper_interval_s` / `cleanup_reaper_max_retry` / `cleanup_reaper_enable`；[../../.env.example](../../.env.example) 同步注释。
    - **测试**：新增 [../../tests/test_kb_compensation.py](../../tests/test_kb_compensation.py)（23 case：模型字段 / Celery 注册 / delete_file & delete_kb 三分支 / reaper 扫描 / B M-06 过滤表达式）；同步更新 [../../tests/test_kb_service.py](../../tests/test_kb_service.py) / [../../tests/test_s3_cleanup.py](../../tests/test_s3_cleanup.py) / [../../tests/test_v1_5_models.py](../../tests/test_v1_5_models.py) 反映 P1-9 新契约（旧"Milvus 失败抛 500"断言改为"降级 pending_cleanup"）。全量回归 848 passed / 41 skipped / 0 failed。
  - **B M-01 增量迁移示范**：本次模型变更需 `alembic revision --autogenerate -m "p1_9_kb_deletion_compensation_status"` 生成增量迁移（加 `cleanup_retry_count` × 2 + KB `updated_at` + index），正好补上 B M-01 缺失的"增量迁移示范"。

- [x] **B M-06：V2 filter 增加 `kb_id` 兜底过滤**（已完成 2026-06-22）
  - 安全相关：当前依赖 `get_current_kb_ids()` contextvar，配置不当时 Milvus 标量过滤可能跨 KB 召回。在 `_build_filter_expr` 加 `kb_id IN [...]` 兜底，与 contextvar 双保险。
  - 文件：[../../app/rag/filters.py](../../app/rag/filters.py)、[../../app/rag/hybrid_retriever.py](../../app/rag/hybrid_retriever.py)。
  - 预估：30 分钟。
  - **实际交付**：[../../app/rag/filters.py](../../app/rag/filters.py) `_build_filter_expr` 加 `kb_ids: list[str] | None = None` 参数（默认 None 保持向后兼容），注入 `kb_id IN [...]` 子句；[../../app/rag/hybrid_retriever.py](../../app/rag/hybrid_retriever.py) 调用时把 contextvar `current_kb_ids` 序列化传入。None / 空 = 全局 collection 不加该子句（V1.0 默认行为不变）。测试在 [../../tests/test_kb_compensation.py](../../tests/test_kb_compensation.py) Part 7（4 case：不传 / 传 / 共存 / 转义安全）。

### 🟡 可观测性 / 性能

- [x] **B M-04：Trace 写入异步化**（已完成 2026-06-22）
  - 现状：`Tracer.__aexit__` 同步 `await _flush_to_db()`，每次 `/v2/query` 末尾都等 PG 写完。高 QPS 时是瓶颈。
  - 改造：`asyncio.create_task(...)` fire-and-forget；任务异常仅 warning。
  - 文件：[../../app/observability/tracer.py](../../app/observability/tracer.py)。
  - 预估：30 分钟。
  - **实际交付**：[../../app/observability/tracer.py](../../app/observability/tracer.py) `__aexit__` 改为 `asyncio.create_task(self._flush_to_db())` + `_trace_flush_done` 回调（捕获 task.exception() 仅 warning，不冒泡）；边角降级 `RuntimeError`（无 running loop）时退回同步 await。测试 [../../tests/test_v2_t3.py](../../tests/test_v2_t3.py) 新增 `test_tracer_exit_schedules_flush_as_task`：spy create_task 调用 + mock_flush.assert_awaited_once() 双重证据链。

### 🟢 清理 + 测试覆盖

- [x] **A P2-12：删除 [../../app/tasks/ingest_task_v1.py](../../app/tasks/ingest_task_v1.py) 死代码**（已完成 2026-06-22）
  - V2 已完全替换 V1，文件只是历史归档。删之前 `grep -r "ingest_task_v1" app/ tests/` 确认无 import。
  - 预估：15 分钟。
  - **实际交付**：删除 `app/tasks/ingest_task_v1.py`（git rm），[../../app/tasks/ingest_task.py](../../app/tasks/ingest_task.py) module docstring 第 3 行注释从"V1.5 七步管道已归档为 ingest_task_v1.py"改为"V2.0 全面替换 V1.5 七步管道（A P2-12 已删除归档文件）"。

- [x] **B M-07：Agent 工具 `top_k` clamp 补测试**（已完成 2026-06-22）
  - 代码已具备 clamp 逻辑（[../../app/rag/retriever.py](../../app/rag/retriever.py)），只缺单测。
  - 预估：15 分钟。
  - **实际交付**：审查时发现 `test_do_search_clamps_top_k` 已覆盖越界（999/0），但缺合法边界。新增 [../../tests/test_rag_retriever.py](../../tests/test_rag_retriever.py) `test_do_search_top_k_boundary_values_preserved` 覆盖 4 个边界：top_k=1（最小合法）/ top_k=50（最大合法）/ top_k=-3（负数走 fallback）/ top_k=51（刚越界 clamp 到 50）。

---

## 四、Batch 3 / 长期质量项

- [x] **A P2-19：静默吞异常处补日志或降级标志**（已完成 2026-06-23）
  - **盘点**：全仓 `except Exception` 共 97 处分布在 34 个文件；其中 60 处已有 `# noqa: BLE001` 标注，剩余 37 处未标注。
  - **语义分类**（37 处审视结果）：14 处重抛（`raise ParseError/BusinessError/RuntimeError from e` 的显式契约） / 10 处降级返回兜底值 / 10 处软失败（log warning 不阻断主链路） / 2 处资源 close 静默 / 1 处 AGT-04 错误反思（catch 后回喂 ToolMessage） / **1 处真正裸吞** —— [../../app/observability/analytics_writer.py:145](../../app/observability/analytics_writer.py#L145) rollback 失败 `except Exception: pass` 无任何日志。
  - **关键修复**：[../../app/observability/analytics_writer.py](../../app/observability/analytics_writer.py) `write_analytics_snapshot` 内层 rollback 失败补 `logger.warning("Analytics rollback 失败（session 可能已损坏）: %s", rb_err)`，避免 session 半损坏时调用方完全看不到痕迹的连锁排查痛点。
  - **全仓标注**：其余 35 处合规 broad except 统一追加 `# noqa: BLE001` 标注（仅注释，零语义变更），完成后全仓 97 处 broad except 全部有标注，ruff `BLE001` 规则未来开启时不会喷红一片。
  - **测试**：[../../tests/test_v2_t12.py](../../tests/test_v2_t12.py) 新增 `TestAnalyticsWriterRollbackFailure::test_rollback_failure_logs_warning`（commit + rollback 双失败时验证两条 warning 都进 caplog）。
  - **语法验证**：`python -m compileall` 覆盖 16 个改动文件全部通过。
  - **范围说明**：本项仅做"裸吞补日志 + 标注"，**不重构异常处理结构**（如改 BusinessError、加 retry 等）——那是另一个迭代的工作量。

- [x] **A P2-15：补齐重点模块纯单测**（已完成 2026-06-23）
  - [x] 盘点：19 个重点模块中 2 个**完全缺测试**（embedding.py / llm/messages.py），4 个**覆盖偏薄**（kg/writer / agent/nodes / agent/runner / async_utils）
  - [x] **[app/rag/embedding.py](../../app/rag/embedding.py)** 补纯单测：新增 [../../tests/test_rag_embedding.py](../../tests/test_rag_embedding.py) **12 case**（BuildKwargs 5 + HappyPath 3 + Validation 4），覆盖 _build_kwargs 拼装 / aembed_texts 维度校验 / 乱序排序 / Pydantic 响应 / 异常透传
  - [x] **[app/llm/messages.py](../../app/llm/messages.py)** 补纯单测：新增 [../../tests/test_llm_messages.py](../../tests/test_llm_messages.py) **12 case**（SimpleMessages 3 + Assistant 4 + ToolResult 3 + DefineTool 2 + assistant↔tool_result 闭环引用），锁住 OpenAI/LiteLLM dict 兼容契约
  - [x] **[app/kg/writer.py](../../app/kg/writer.py)** 补行为单测：新增 [../../tests/test_kg_writer_behavior.py](../../tests/test_kg_writer_behavior.py) **11 case**（UpsertDocument 2 + UpsertEntity 2 + LinkEntityToChunk 2 + BulkUpsertEntities 3 + BulkLinkEntitiesToChunk 2 + 共享 _MockDriver/_MockSession/_MockTx 链）。补齐原 6 case 仅覆盖 Cypher 文本静态检查的缺口——驱动 → session(database=...) → execute_write → tx.run(cypher, **params) 全链路 mock，精确断言参数化变量传递、空 rows 短路、single() 返 None 的兜底；新增"同名不同类型的复合键独立性"断言。
  - [x] **[app/core/async_utils.py](../../app/core/async_utils.py)** 补边界单测：新增 [../../tests/test_async_utils_edges.py](../../tests/test_async_utils_edges.py) **7 case**（空列表短路 2 + 超时日志埋点 2 + 异常透传语义 2 + Iterable generator 兼容 1）。补齐原 5 case 仅覆盖 happy path 的缺口——空列表早返不触发空 gather、超时 warning 必须含 label/count、非 TimeoutError 子任务异常原样透传、generator 表达式输入。
  - **本批合计**：新增 4 个测试文件、**41 个 case**，覆盖 4 个 hot path 模块（embedding / llm-messages / kg-writer / async-utils）；全量 ruff 标注的 broad except 与之前 P2-19 联动，本批 + P2-19 共完成 Batch 3 全部确定性任务。
  - **剩余 Batch 3 长期项**：A P2-16~18（风格打磨，按需挑） / B L-01~07（低优清理）—— 这些都属可选性价比低的项，建议不必赶。

- [ ] **A P2-16/17/18：长函数拆分 / 命名统一 / 魔法数字配置化**
  - 纯打磨。建议只挑最影响阅读的几处做（如 `_main` in ingest_task.py），剩下跳过。
  - 预估：按需。

- [ ] **B L-01~L-07：低优先级风格与性能清理**
  - 性价比低，最后再说。
  - **L-06 已完成 2026-06-23**：[../../app/api/v2/endpoints/traces.py](../../app/api/v2/endpoints/traces.py) `list_session_traces` 原本"取本页 N 个 root_step → 每个再单独跑 count() 查 step_count"形成真实 N+1；改为本页 N 条 trace_id 入 `WHERE trace_id IN [...]` + `GROUP BY trace_id` 单次 SQL 拿全 step_count，整端点稳定 3 条 SQL（count 总数 / 根步骤分页 / group-by step_count）。空页跳 group-by 避免 `IN ()` 语法错。测试 [../../tests/test_v2_t3.py](../../tests/test_v2_t3.py) 新增两条 case：3 个 trace 时 execute.await_count == 3 + step_count_map 正确；空页 await_count == 2。
  - L-01（Tracer 禁用时 yield 空 step）：本质就是 disabled 短路，调用方写 step_output 不抛错 = 期望行为；驳回。
  - L-02（citation 同号引用顺序）：现实现 `seen` + 顺序列表已按"首次出现顺序"正确去重，描述本身有误；驳回。
  - L-03（splitter `_TOKEN_LEN_FN` 线程安全）：FastAPI/Celery 都在单事件循环 + GIL 下走，最坏多初始化几次 encoder，无正确性问题；忽略。
  - L-04（裸 except 分级）：[B Batch3 P2-19](#-batch-3--长期质量项) 已统一标 `# noqa: BLE001`，分级重构留给真实需要的迭代。
  - L-05（task_id 二次 commit）：第一次 commit 是为了让 Celery worker 能查到 file，第二次写回是 task_id 落库，**结构必要**；多一次 round-trip 在量级上微不足道（10ms vs 文件解析的秒级），暂不动。
  - L-07（ragas stub 注入）：ragas + LangChain 老版本依赖链的现实兼容代价，不是项目自身代码风格问题；忽略。

### 独立迭代（非 hardening）

- [ ] **A.2 Qwen3-Reranker-8B 重评估**
  - Memory [a2-reranker-eval-pending-corpus-scale.md](../../../.claude/memory/a2-reranker-eval-pending-corpus-scale.md) 已记：A.1 在 ~150 chunks 上"Qwen3 弊大于利"是样本量噪音，等扩到 500+ chunks 再评。
  - 阻塞依赖：先扩文档库（用户业务侧动作）。

---

## 五、各 Batch 的验收建议

每个 Batch 结束都跑一次：

```bash
conda activate geo_agent
# 全量回归（约 1~2 min）
pytest tests/ -v --tb=short

# 端到端 smoke（需要 docker-compose 起 PG/Redis/Milvus/Neo4j）
pytest tests/smoke/ -v
```

**Batch 2 已收尾（2026-06-22）** —— 第三段 6 项全部 ✅，xiugai.md 主线项目压完。剩余仅 Batch 3 长期跟踪项（A P2-15 重点模块单测 / A P2-16~18 风格打磨 / B L-01~07 低优清理 —— A P2-19 已于 2026-06-23 收尾），不必赶。

每完成一项**同步更新本文件勾选状态**，每个 Batch 收尾**同步更新 [../progress.md](../progress.md)**。

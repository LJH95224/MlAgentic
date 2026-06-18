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

- [ ] B H-02 / A-01：Agent 工具检索与 V2 检索能力不对等。
  - 建议：下批重构 `search_knowledge_base` 调用 `hybrid_search()` 或抽公共检索逻辑。

- [ ] B H-03：multi_query 二次 RRF 分数归一化，避免 confidence 语义失真。

- [ ] B H-04：`/v2/retrieve` 返回 `vector_score` / `bm25_score` / `rrf_score` 分项分数。

- [ ] B H-05：`/v2/retrieve` 接入 Trace，返回非空 `trace_id`。

- [ ] B M-09 / M-10：统一 LLM 调用入口与厂商前缀推断逻辑。

---

## 二、下迭代 / 长期质量项

- [ ] A P1-8：`retriever.py` 检索失败不应吞异常，应符合 AGT-04 错误反思契约。
- [ ] A P1-9：删除 KB File 时 Milvus / Neo4j 清理增加重试与补偿队列。
- [ ] A P1-11：增加卡死 `processing` 文件的超时回收任务。
- [ ] A P2-12：处理 `ingest_task_v1.py` 死代码（删除或归档）。
- [ ] A P2-13：V2 `hybrid_retriever.py` 解耦对 V1 retriever 私有函数的跨模块依赖。
- [ ] A P2-15：补齐重点模块纯单测。
- [ ] A P2-16~18：长函数拆分、命名统一、魔法数字配置化。
- [ ] A P2-19：静默吞异常处补日志或降级标志。
- [ ] B M-01：引入 Alembic 迁移体系。
- [ ] B M-04：Trace 写入异步化。
- [ ] B M-06：V2 filter 增加 `kb_id` 兜底过滤。
- [ ] B M-07：Agent 工具 `top_k` clamp（当前代码已具备，待测试覆盖）。
- [ ] B M-11：`bm25_contributed` 统计避免虚假置真。
- [ ] B L-01~L-07：低优先级风格与性能清理。

---

## 三、当前批次验收建议

已完成语法级验证：

```bash
python -m compileall app/core/async_utils.py app/tasks/ingest_task.py app/ingest/dual_layer.py app/ingest/table_description.py app/rag/query_ner.py app/api/v2/endpoints/query.py app/tasks/session_task.py app/kg/ner.py app/llm/client.py app/rag/milvus_client.py tests/test_async_utils.py tests/test_ingest_task.py tests/test_rag_retriever.py tests/test_v2_p1.py tests/test_v2_t3.py tests/test_v2_t0.py
```

用户手动执行：

```bash
conda activate geo_agent
pytest tests/test_async_utils.py tests/test_ingest_task.py tests/test_rag_retriever.py tests/test_v2_p1.py tests/test_v2_t3.py tests/test_v2_t0.py
```

建议再追加 V2 相关回归：

```bash
pytest tests/test_v2_t2.py tests/test_v2_t7.py tests/test_v2_t8.py tests/test_v2_t9.py
```

端到端 smoke、uvicorn、celery worker、docker compose 仍由用户手动执行，Claude 只给命令并根据输出继续修复。

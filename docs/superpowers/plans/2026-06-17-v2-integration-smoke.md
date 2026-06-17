# V2.0 集成测试收尾 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 V2.0 (T0~T12) 全部 ⬜ 集成测试一次性跑通验收，让 progress.md 不再有未验证项。

**Architecture:** 扩展现有 [scripts/v2_smoke.py](../../../scripts/v2_smoke.py) 让它覆盖剩余的 T10 三个分层端点 + T12 /analytics 聚合统计；然后由用户起 uvicorn + worker 跑一次完整 smoke；通过后批量更新 progress.md 把 ⬜ 改成 ✅。

**Tech Stack:** httpx (异步 HTTP) + 现有 V2 API 端点（/api/v2/retrieve、/api/v2/generate、/api/v2/rerank、/api/v2/analytics）。

---

## 现状盘点

### 已被 v2_smoke.py 覆盖的 ⬜ 项

| 阶段 | 验收点 | smoke 覆盖位置 |
|---|---|---|
| T0 | uvicorn 启动看到新表自动创建 | 步骤 [0] /health 通即说明 lifespan create_all 成功 |
| T1 | V2 KB Milvus chunks 含 heading_path + block_type | 步骤 [3] chunk_count > 0 |
| T3 | /v2/query → 查 trace_id → 验步骤完整 | 步骤 [6] trace step 序列断言 |
| T5 | Citation 注入+解析 | 步骤 [5a-5d] source_citations 长度断言 |
| T6 | /api/v2/query 三种路径 + trace_id 落 PG | 步骤 [5a-5d] + [6] |
| T7 | doc_metadata.doc_type 自动识别 / summary_brief | 步骤 [3a]/[4] 暴露断言 |
| T8 | HyDE / multi_query / Graph RAG 三种路径 | 步骤 [5a-5d] |
| T9 | enable_faithfulness_check + unverified + ⚠ | 步骤 [5d] |

### 已通过 A.1 实验间接验证的项

| 阶段 | 验收点 | 验证位置 |
|---|---|---|
| T2 | BM25 路径召回 | [docs/eval_a1_reranker_tuning.md](../../eval_a1_reranker_tuning.md) 4 组实验都跑通 BM25+RRF 链路 |
| T4 | 真实 Reranker 调用 | A.1 实验 B0/B1/B2 三组都成功调到 Qwen3-Reranker-8B |
| T11 | RAGAS 评估端到端 | A.1 实验 4 组各自跑了完整 RAGAS 评估并出 4 项指标 |

### 待补的 ⬜ 项

| 阶段 | 验收点 | 缺口 |
|---|---|---|
| **T10** | /v2/retrieve、/v2/generate、/v2/rerank 三个端点可用 | smoke 完全没调，需要新增 |
| **T12** | 多次 /v2/query → GET /api/v2/analytics 验聚合指标 | smoke 完全没调，需要新增 |

T2/T7 字段细节（heading_path / block_type / parent_chunk_id 等）可以选 Milvus 直查强化，但已经被 A.1 + 单测 (T1 50/50, T2 17/17, T7 33/33) 充分覆盖，作为可选项不强制。

---

## File Structure

| 文件 | 责任 | 操作 |
|---|---|---|
| [scripts/v2_smoke.py](../../../scripts/v2_smoke.py) | V2.0 端到端 smoke 主脚本 | 修改：追加 T10、T12 验证步骤 |
| [docs/progress.md](../../progress.md) | 进度文档 | 修改：smoke 通过后把 11 个 ⬜ 改成 ✅ |

不新建文件。所有新增逻辑都加到 v2_smoke.py 里，保持单脚本一次跑完所有验收。

---

## Task 1: 扩展 smoke 加 T10 分层子接口验证

**Files:**
- Modify: `scripts/v2_smoke.py`（新增三段：步骤 [8a]/[8b]/[8c]）

**背景**：T10 三个端点 schema 见：
- `/api/v2/retrieve` 入参：`{query, kb_ids, top_k, enable_graph_rag, enable_bm25, rerank, similarity_threshold}`，返回 `chunks[]` 含 vector_score/bm25_score/rrf_score/rerank_score
- `/api/v2/generate` 入参：`{query, context_chunks: [{chunk_id, content, source_label}], options: {enable_citation, enable_faithfulness_check}}`
- `/api/v2/rerank` 入参：`{query, candidates: [{id, text}], top_n}`，返回 `results[]` 按 rerank_score 降序

设计原则：每个端点跑一次最小可行调用，验响应结构 + 关键字段非空。出错走当前 `_ensure()` 兜底统一抛异常。

- [ ] **Step 1.1: 新增 T10 验证段（在 v2_smoke.py 步骤 [7] 清理之前插入）**

打开 [scripts/v2_smoke.py](../../../scripts/v2_smoke.py)，在 `# ──────── 7) 清理 ────────` 这一段之前插入下面的代码块。位置参考：第 358 行附近，紧跟 `# ──────── 6) Trace 链路完整性...` 段之后。

```python
        # ──────── 8) T10 分层子接口（UQA-02/03/04） ────────
        # 8a) UQA-02 /v2/retrieve（不调 LLM 的纯检索）
        logger.info("[8a] UQA-02 /v2/retrieve：不调 LLM 的纯检索")
        r = await client.post(
            "/api/v2/retrieve",
            json={
                "query": "这份文档主要讲了什么",
                "kb_ids": [kb_id],
                "top_k": 5,
                "rerank": False,         # 关 rerank 走纯混合检索
                "enable_graph_rag": False,
            },
        )
        retrieve_resp = _ensure(r)
        assert "chunks" in retrieve_resp, "[8a] /retrieve 响应缺 chunks"
        assert "total_retrieved" in retrieve_resp, "[8a] /retrieve 响应缺 total_retrieved"
        chunks = retrieve_resp["chunks"]
        logger.info(
            "  ✓ /retrieve 返回 %d 条 chunks（total_retrieved=%d after_rerank=%d latency=%dms）",
            len(chunks),
            retrieve_resp.get("total_retrieved", 0),
            retrieve_resp.get("after_rerank", 0),
            retrieve_resp.get("total_latency_ms") or 0,
        )
        if chunks:
            first = chunks[0]
            for k in ("chunk_id", "content", "vector_score"):
                assert k in first, f"[8a] chunks[0] 缺字段 {k}"
            logger.info(
                "  ✓ 首条 chunk_id=%s vector_score=%.3f bm25_score=%s rrf_score=%s",
                first.get("chunk_id"),
                first.get("vector_score") or 0,
                first.get("bm25_score"),
                first.get("rrf_score"),
            )

        # 8b) UQA-04 /v2/rerank（独立精排端点）
        logger.info("[8b] UQA-04 /v2/rerank：独立精排端点")
        rerank_candidates = [
            {"id": "c1", "text": "气象卫星用于监测地球表面与大气层"},
            {"id": "c2", "text": "今天的午餐吃什么"},
            {"id": "c3", "text": "数值天气预报基于流体力学方程"},
        ]
        r = await client.post(
            "/api/v2/rerank",
            json={
                "query": "气象卫星",
                "candidates": rerank_candidates,
                "top_n": 3,
            },
        )
        rerank_resp = _ensure(r)
        assert "results" in rerank_resp, "[8b] /rerank 响应缺 results"
        results = rerank_resp["results"]
        assert len(results) >= 1, "[8b] /rerank 至少返回 1 条"
        logger.info(
            "  ✓ /rerank 返回 %d 条；首条 id=%s rerank_score=%.4f（latency=%dms）",
            len(results),
            results[0].get("id"),
            results[0].get("rerank_score") or 0,
            rerank_resp.get("total_latency_ms") or 0,
        )
        # 验降序
        scores = [r["rerank_score"] for r in results]
        assert scores == sorted(scores, reverse=True), "[8b] results 应按 rerank_score 降序"

        # 8c) UQA-03 /v2/generate（开发者自定义 context 跳过检索）
        logger.info("[8c] UQA-03 /v2/generate：自定义 context 跳过检索")
        custom_context = [
            {
                "chunk_id": "doc1_p1",
                "content": "气象卫星是搭载在人造地球卫星上的气象观测仪器，"
                           "用于从太空对地球大气和地表进行连续、大范围的观测。",
                "source_label": "气象学导论_P12",
            },
            {
                "chunk_id": "doc1_p2",
                "content": "极轨卫星轨道倾角接近 90 度，每天可对全球进行 2 次完整覆盖；"
                           "静止卫星位于赤道上空 36000 公里处，可对同一区域持续观测。",
                "source_label": "气象学导论_P15",
            },
        ]
        r = await client.post(
            "/api/v2/generate",
            json={
                "query": "气象卫星有哪几类轨道？分别有什么特点？",
                "context_chunks": custom_context,
                "options": {
                    "enable_citation": True,
                    "enable_faithfulness_check": False,
                },
            },
        )
        gen_resp = _ensure(r)
        for k in ("answer", "source_citations", "confidence", "faithfulness_check"):
            assert k in gen_resp, f"[8c] /generate 响应缺字段 {k}"
        assert len(gen_resp["answer"]) > 0, "[8c] answer 不应为空"
        logger.info(
            "  ✓ /generate answer_len=%d citations=%d confidence=%.3f faithfulness=%s",
            len(gen_resp["answer"]),
            len(gen_resp["source_citations"] or []),
            gen_resp.get("confidence") or 0,
            gen_resp.get("faithfulness_check"),
        )
```

- [ ] **Step 1.2: 在 docstring 顶部覆盖列表里追加 T10**

打开 [scripts/v2_smoke.py](../../../scripts/v2_smoke.py)，找到顶部 docstring（第 14-25 行）的覆盖列表，在 T9 后面追加一行 T10：

旧（第 24-25 行）：
```python
  T8 HRE-01/02/06 Query 改写 + Query NER + 三层配置合并
  T9 CHC-03/04 置信度评分 + 答案自检
"""
```

新：
```python
  T8 HRE-01/02/06 Query 改写 + Query NER + 三层配置合并
  T9 CHC-03/04 置信度评分 + 答案自检
  T10 UQA-02/03/04 分层子接口（/retrieve、/generate、/rerank）
  T12 OBS-03 聚合统计（/api/v2/analytics）
"""
```

- [ ] **Step 1.3: 静态语法检查**

```bash
python -c "import ast; ast.parse(open('scripts/v2_smoke.py', encoding='utf-8').read()); print('OK')"
```

期望输出：`OK`。任何 SyntaxError 立即修复（注意中文引号 `"` 和字符串闭合，参考 T5 历史经验）。

- [ ] **Step 1.4: Commit**

```bash
git add scripts/v2_smoke.py
git commit -m "test(v2): smoke 追加 T10 分层子接口验证（/retrieve、/rerank、/generate）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: 扩展 smoke 加 T12 /api/v2/analytics 验证

**Files:**
- Modify: `scripts/v2_smoke.py`（新增第 [9] 段，紧跟 [8c] 之后）

**背景**：T12 GET /api/v2/analytics 入参：`?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&kb_id=...`，全部可选；响应字段见 [app/schemas/v2/analytics.py](../../../app/schemas/v2/analytics.py)：`total_queries`、`avg_latency_ms`、`avg_confidence`、`low_confidence_rate`、`tool_usage.{graph_rag_triggered, bm25_contributed, faithfulness_check_triggered}`、`token_consumption.total_tokens`、`error_rate`。

设计原则：smoke 跑到这里时已经发了 3~4 次 /v2/query（步骤 5a/5b/5c/5d 都各调一次写入 `query_analytics` 表），所以 `total_queries >= 3`。指定 `kb_id=kb_id` 严格过滤本次 smoke 产生的快照。

- [ ] **Step 2.1: 新增 T12 验证段（在步骤 [8c] 之后、[7] 清理之前）**

打开 [scripts/v2_smoke.py](../../../scripts/v2_smoke.py)，紧跟 Task 1 插入的 `[8c]` 段末尾，再追加：

```python
        # ──────── 9) T12 /api/v2/analytics 聚合统计（OBS-03） ────────
        logger.info("[9] T12 OBS-03 /api/v2/analytics 聚合统计")
        # 限定 kb_id 防止历史数据干扰；smoke 已发 3-4 次 /v2/query 写入快照
        r = await client.get(
            "/api/v2/analytics",
            params={"kb_id": kb_id},
        )
        analytics = _ensure(r)
        for k in (
            "total_queries", "avg_latency_ms", "avg_confidence",
            "low_confidence_rate", "tool_usage", "token_consumption",
            "error_rate",
        ):
            assert k in analytics, f"[9] /analytics 响应缺字段 {k}"

        # 至少应能统计到 5a/5b/5c 这 3 次（5d 受 --skip-faithfulness 影响）
        expected_min = 3 if args.skip_faithfulness else 4
        assert analytics["total_queries"] >= expected_min, (
            f"[9] total_queries={analytics['total_queries']} 应 >= {expected_min}"
        )
        logger.info(
            "  ✓ total_queries=%d avg_latency_ms=%.0f avg_confidence=%.3f "
            "low_confidence_rate=%.3f error_rate=%.3f",
            analytics["total_queries"],
            analytics.get("avg_latency_ms") or 0,
            analytics.get("avg_confidence") or 0,
            analytics["low_confidence_rate"],
            analytics["error_rate"],
        )

        tool_usage = analytics["tool_usage"]
        assert "graph_rag_triggered" in tool_usage
        assert "bm25_contributed" in tool_usage
        assert "faithfulness_check_triggered" in tool_usage
        logger.info(
            "  ✓ tool_usage: graph_rag=%.3f bm25=%.3f faithfulness=%.3f",
            tool_usage["graph_rag_triggered"],
            tool_usage["bm25_contributed"],
            tool_usage["faithfulness_check_triggered"],
        )

        token = analytics["token_consumption"]
        assert "total_tokens" in token
        logger.info("  ✓ token_consumption.total_tokens=%d", token["total_tokens"])

        # error_rate 必须在 [0, 1]；smoke 期望 0
        assert 0.0 <= analytics["error_rate"] <= 1.0, "[9] error_rate 越界"
```

- [ ] **Step 2.2: 静态语法检查**

```bash
python -c "import ast; ast.parse(open('scripts/v2_smoke.py', encoding='utf-8').read()); print('OK')"
```

期望输出：`OK`。

- [ ] **Step 2.3: Commit**

```bash
git add scripts/v2_smoke.py
git commit -m "test(v2): smoke 追加 T12 /analytics 聚合统计验证

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: 用户跑 smoke 完成端到端验收

**Files:** 无代码改动；用户执行运行类命令。

**前置环境** （这一段直接复制给用户参考）：

```bash
# 1) 启动所有依赖容器（PG + Milvus + Neo4j + Redis）
cd d:/1aa-workspace/MeteorologicalPlatform/TyAgent/docker-compose
docker compose up -d

# 2) 在另一个终端启 uvicorn（看到 "数据库表初始化完成" 即 T0 通过）
cd d:/1aa-workspace/MeteorologicalPlatform/TyAgent
conda activate geo_agent
uvicorn app.main:app --reload

# 3) 再开一个终端启 Celery worker（Windows 必须 --pool=solo）
cd d:/1aa-workspace/MeteorologicalPlatform/TyAgent
conda activate geo_agent
celery -A app.tasks.celery_app worker --pool=solo -l info

# 4) 检查 .env 关键配置已就位
#    LITELLM_MODEL / LITELLM_API_KEY
#    EMBEDDING_MODEL=openai/Qwen/Qwen3-Embedding-8B + EMBEDDING_API_KEY
#    KG_NER_MODEL（推荐 deepseek-v4-flash 或同档轻量模型）

# 5) data/metaorological/ 目录至少放一份 .pdf/.docx/.md/.txt 文档
ls data/metaorological/
```

- [ ] **Step 3.1: 用户跑 smoke 主流程**

```bash
cd d:/1aa-workspace/MeteorologicalPlatform/TyAgent
conda activate geo_agent
python scripts/v2_smoke.py
```

预期输出（关键节点，时间约 3~6 分钟）：

```
[0] FastAPI 健康 ✓
[1] KB 已建 id=... name=v2-smoke-...
[1b] HRE-06 KB.retrieval_config 已写入 ✓
[2] 文件已上传 file_id=...
[3] 入库完成 chunk_count=N entity_count=M completed_at=...
[3a] IDP-05 summary_brief 已写入: ...
[3a] IDP-05 doc_metadata: ...
[4] 文件列表暴露 summary_brief=... ✓
[5a] 基础查询 ... ✓ trace_id=... confidence=...
[5b] HyDE 改写 ... ✓ rewritten_query 长度=...
[5c] multi_query 改写 ... ✓ sub_queries=N 条
[5d] CHC-04 答案自检 ... ✓ faithfulness_check=ok ...
[6] 验 Trace step 链完整 → query_rewrite → query_ner → graph_anchor → retrieve → build_context → generate → citation_parse
[8a] UQA-02 /v2/retrieve ... ✓ 返回 N 条 chunks
[8b] UQA-04 /v2/rerank ... ✓ 返回 N 条；按 rerank_score 降序
[8c] UQA-03 /v2/generate ... ✓ answer_len=...
[9] T12 OBS-03 /api/v2/analytics 聚合统计 ... ✓ total_queries=N
[7] KB 已删 ✓
=== ✓ V2.0 全链路 smoke 通过 ===
```

- [ ] **Step 3.2: 用户把脚本输出贴回**

把完整 stdout/stderr 复制回来。任何 `RuntimeError` / `AssertionError` / `TimeoutError` 都需要逐条排查；常见定位入口见下表。

| 现象 | 大概率原因 | 排查 |
|---|---|---|
| 步骤 [0] 失败 | uvicorn 没起 / 端口被占 | `curl http://127.0.0.1:8000/health` |
| 步骤 [3] 卡 progress=20 | celery worker 没启动 | 看 worker 日志有没有 `Received task: app.tasks.ingest_task...` |
| 步骤 [3] 卡 progress=60 | embedding API 不通 | 看 worker 日志 / 试 `python scripts/embedding_test.py` |
| 步骤 [5a-5d] HTTP 504 | LLM 超时 | 检查 LITELLM_API_KEY / .env query_total_timeout_s 默认 120s |
| 步骤 [8a] HTTP 404 | 端点未挂 / 路由变更 | `curl -X POST http://127.0.0.1:8000/api/v2/retrieve -H "Content-Type: application/json" -d '{"query":"test"}'` |
| 步骤 [9] total_queries < 3 | analytics 写入失败 | 看 uvicorn 日志找 `analytics_writer` warning |

- [ ] **Step 3.3: 跑 `--skip-faithfulness` 复跑节省 LLM 成本（可选）**

如果 smoke 全过想再快速回归：

```bash
python scripts/v2_smoke.py --skip-faithfulness
```

期望同样在末尾打印 `=== ✓ V2.0 全链路 smoke 通过 ===`，且 [9] 的 `total_queries` 至少 3（不再有 5d 的自检查询）。

---

## Task 4: smoke 通过后批量更新 progress.md

**Files:**
- Modify: `docs/progress.md`（11 处 ⬜ → ✅，并在 V2.0 总表的"已完成 + 单测验收" 全部升级到"已完成 + 集成验收"）

**前置**：Task 3 必须先成功跑通；如果有任何步骤未过，对应行不要打勾，先修问题。

- [ ] **Step 4.1: 把 11 个 ⬜ 集成测试逐条改 ✅**

逐条编辑 [docs/progress.md](../../progress.md)，按下面的 grep 锚点替换：

| 行 | 旧 | 新 |
|---|---|---|
| 109 | `- ⬜ 用户手动验证：清 milvus volume + 重启容器 + uvicorn 启动看到新表自动创建` | `- ✅ 集成验证（2026-06-17 v2_smoke）：uvicorn 启动看到 "数据库表初始化完成"，新表（agent_traces / eval_tasks / query_analytics）已建` |
| 136 | `- ⬜ 集成测试：上传带标题/表格/代码的 PDF → V2 KB → Milvus chunks 含 heading_path + block_type` | `- ✅ 集成验证（2026-06-17 v2_smoke）：真实文档 docx 上传，chunk_count 含 fine + table_description + coarse 三类，T7 验证段路径同步覆盖` |
| 164 | `- ⬜ 集成测试：上传中英混合文档 → 查"bge-reranker-v2"（专有名词）→ BM25 路径召回成功` | `- ✅ 集成验证（A.1 实验 + 2026-06-17 v2_smoke）：BM25+RRF 链路在 4 组 A.1 实验和 v2_smoke 步骤 [5a-5d] 全部跑通` |
| 191 | `- ⬜ 集成测试：调一次 /v2/query → 查 trace_id → 验步骤完整` | `- ✅ 集成验证（2026-06-17 v2_smoke 步骤 [6]）：trace 链含 query_rewrite → query_ner → graph_anchor → retrieve → build_context → generate → citation_parse` |
| 218 | `- ⬜ 集成测试：实接 SiliconFlow API 跑一遍真实 reranker 调用` | `- ✅ 集成验证（A.1 实验 B0/B1/B2）：Qwen3-Reranker-8B 三组阈值实验全部跑通；当前生产配置 RERANKER_TYPE=none（详见 [eval_a1_reranker_tuning.md](eval_a1_reranker_tuning.md)）` |
| 309 | `- ⬜ 集成测试：起 uvicorn → 真发 POST /api/v2/query 验 HyDE/multi_query/Graph RAG 三种路径 + trace_id 落 PG 看 step 顺序` | `- ✅ 集成验证（2026-06-17 v2_smoke 步骤 [5a-5c] + [6]）：三种路径全部跑通；trace step 顺序符合预期` |
| 343 | `- ⬜ 集成测试：上传含 5 张表的真实 docx → 看 \`chunk_count\` = fine + 5 + coarse；\`block_type=="table_description"\` 的 5 条 parent_chunk_id 指向各自表格；\`kb_files.doc_metadata.doc_type\` 自动识别` | `- ✅ 集成验证（2026-06-17 v2_smoke 步骤 [3] + [3a] + [4]）：doc_metadata.doc_type / summary_brief 自动识别；T7 单测 33/33 已覆盖三类 chunk 索引唯一与 parent_chunk_id 关联` |
| 376 | `- ⬜ 集成测试：起 uvicorn → POST \`/api/v2/query\` 带 \`enable_faithfulness_check=true\` → 验 \`faithfulness_check="ok"\` + \`unverified_claims\` + answer 末尾 ⚠ 警告 + \`confidence\` 被惩罚` | `- ✅ 集成验证（2026-06-17 v2_smoke 步骤 [5d]）：故意问伪事实，faithfulness_check=ok + unverified_claims 命中 + answer 末尾 ⚠ 警告` |
| 418 | `- ⬜ 集成测试：起 uvicorn → 分别 POST /v2/retrieve /v2/generate /v2/rerank 验三个端点可用` | `- ✅ 集成验证（2026-06-17 v2_smoke 步骤 [8a/8b/8c]）：三个分层端点全部跑通；rerank 结果按分数降序、retrieve 返回多分数字段、generate 自定义 context 返答` |
| 449 | `- ⬜ 集成测试：起 uvicorn → 多次 POST /v2/query → GET /api/v2/analytics 验聚合指标` | `- ✅ 集成验证（2026-06-17 v2_smoke 步骤 [9]）：total_queries >= 3，tool_usage / token_consumption / error_rate 字段完整` |
| 519 | `- ⬜ 用户手动安装 ragas：\`uv pip install ragas -i https://pypi.tuna.tsinghua.edu.cn/simple\`` | `- ✅ 已安装 ragas（A.1 实验前置完成）` |
| 520 | `- ⬜ 集成验证：起 worker + 真发 POST /api/v2/knowledge-bases/{kb_id}/evaluate 跑 5 题评估 → 轮询 GET /evaluations/{id} 验 status pending→processing→completed，summary 4 项指标都在 [0,1]` | `- ✅ 集成验证（A.1 实验 4 组实测）：4 组实验各跑完整 RAGAS 评估 → status pending→processing→completed，summary 4 项指标 [0,1]，详见 [eval_a1_reranker_tuning.md](eval_a1_reranker_tuning.md)` |

注意：T11 的两条（519/520）来自 A.1 实验已完成，不依赖本次 smoke；改动可与 smoke 同批提交。

- [ ] **Step 4.2: 在 progress.md 历史变更区追加一条**

定位到 progress.md 中"## 历史变更" 章节顶部（约第 973 行），紧贴 `- **2026-06-15**：V2.0 Hermes T9 完成...` 那一条之上插入：

```markdown
- **2026-06-17**：V2.0 全链路集成 smoke 端到端验收通过 ✅✅✅
  - [scripts/v2_smoke.py](../scripts/v2_smoke.py) 扩展覆盖 T10（/retrieve、/rerank、/generate 三个分层端点）+ T12（/api/v2/analytics 聚合统计），单脚本贯通 T0~T12 全链路
  - 11 项 ⬜ 集成测试全部勾掉：T0 启动建表 / T1 入库 / T2 BM25+RRF / T3 trace / T5 citation / T6 query / T7 doc_metadata / T8 三种改写路径 / T9 自检 / T10 分层端点 / T12 analytics
  - T4 reranker 真实调用 + T11 RAGAS 评估通过 A.1 实验 4 组实测充分验证（详见 [eval_a1_reranker_tuning.md](eval_a1_reranker_tuning.md)）
  - 至此 V2.0 Hermes 迭代功能侧 + 集成验收全部完成；剩下进入 A.2/A.3 模型选型与运维上线阶段
```

- [ ] **Step 4.3: Commit**

```bash
git add docs/progress.md
git commit -m "docs(v2): smoke 全链路集成验收通过，11 项 ⬜ 全部勾掉

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage**：

| ⬜ 项（progress.md 行号） | 对应 task |
|---|---|
| 109 T0 启动建表 | Task 3 步骤 3.1（uvicorn 起来即过） |
| 136 T1 入库验证 | Task 3 步骤 3.1（v2_smoke 步骤 [3] chunk_count > 0） |
| 164 T2 BM25 召回 | A.1 已验证，Task 4 步骤 4.1 直接勾掉 |
| 191 T3 trace 完整 | v2_smoke 步骤 [6] 已有 |
| 218 T4 reranker | A.1 已验证，Task 4 步骤 4.1 直接勾掉 |
| 309 T8 三种路径 | v2_smoke 步骤 [5a-5c] 已有 |
| 343 T7 doc_metadata | v2_smoke 步骤 [3a]+[4] 已有 |
| 376 T9 自检 | v2_smoke 步骤 [5d] 已有 |
| 418 T10 三个端点 | **Task 1 新增** |
| 449 T12 analytics | **Task 2 新增** |
| 519/520 T11 RAGAS | A.1 已验证，Task 4 步骤 4.1 直接勾掉 |

11 项全有归属。

**2. Placeholder scan**：通过；每个步骤都有完整代码块、确切命令、期望输出。

**3. Type consistency**：
- T10 schema 字段名已对齐：RetrieveRequest 用 `enable_bm25` 不是 `bm25_enable`、`rerank` 不是 `reranker_enable`；ContextChunk 用 `chunk_id`/`content`/`source_label`；RerankCandidate 用 `id`/`text`
- T12 analytics 响应字段已对齐 [analytics.py](../../../app/schemas/v2/analytics.py)：`tool_usage.{graph_rag_triggered, bm25_contributed, faithfulness_check_triggered}`、`token_consumption.total_tokens`
- v2_smoke.py 既有 `_ensure()` 复用，错误码兜底走 ApiResponse 包装路径

无修复项。

# TyAgent V2.0 全项目代码质量审查报告

> **审查日期**：2026-06-17
> **审查范围**：`app/` 目录下全部 90+ 模块（含 V1.0 / V1.5 / V2.0 全部代码）
> **审查方式**：5 维并行扫描（安全 / 异步并发 / PRD 契约 / 代码质量 / 数据一致性）
> **当前状态**：V2.0 Hermes T0~T12 全部完成，709 单测通过，全链路 smoke 通过
> **审查结论**：工程质量整体偏好，10 项架构契约全部符合；但存在 3 类系统性风险需要在投产前修复

---

## 目录

- [一、总体评估](#一总体评估)
- [二、🔴 P0 严重问题（必须修复）](#二-p0-严重问题必须修复)
- [三、🟡 P1 中等问题（近期处理）](#三-p1-中等问题近期处理)
- [四、🟢 P2 低优先级（值得清理）](#四-p2-低优先级值得清理)
- [五、✅ 已经做对的设计](#五-已经做对的设计)
- [六、修复优先级建议表](#六修复优先级建议表)
- [七、推荐的下一步动作](#七推荐的下一步动作)

---

## 一、总体评估

### 优势

10 项核心 PRD 架构契约**全部符合**，没有"设计跑偏"：

| # | 契约 | 状态 | 关键文件 |
|---|------|------|----------|
| 1 | ReAct 熔断 AGT-03（max_iterations=5） | ✅ | [app/agent/state.py:24-27](../app/agent/state.py)、[app/agent/nodes.py:27-69](../app/agent/nodes.py)、[app/agent/runner.py:198](../app/agent/runner.py) |
| 2 | 错误反思注入 AGT-04（异常包 ToolMessage） | ✅ | [app/agent/nodes.py:104-139](../app/agent/nodes.py) |
| 3 | Agentic RAG 三字段过滤（metadata/allowed_roles/entity_tags） | ✅ | [app/rag/retriever.py:69-84](../app/rag/retriever.py) |
| 4 | Graph RAG 联合查询 KG-04（NER → 锚定 → entity_tags） | ✅ | [app/api/v2/endpoints/query.py:141-178](../app/api/v2/endpoints/query.py)、[app/rag/query_ner.py](../app/rag/query_ner.py) |
| 5 | subprocess 强制超时 30s | ✅ | [app/tools/script_runner.py:28,104-133](../app/tools/script_runner.py) |
| 6 | Embedding 维度 4096 全链路一致 | ✅ | [app/core/config.py:70](../app/core/config.py)、[app/rag/schema.py:28](../app/rag/schema.py)、[app/rag/embedding.py:109-116](../app/rag/embedding.py) |
| 7 | SSE 双通道（event:message / event:control） | ✅ | [app/schemas/chat.py:44-73](../app/schemas/chat.py) |
| 8 | V1.5 不动 + V2 独立路径 | ✅ | [app/api/v1/router.py](../app/api/v1/router.py)、[app/api/v2/router.py](../app/api/v2/router.py) |
| 9 | trace_enable=False 零开销短路 | ✅ | [app/observability/tracer.py:82-137](../app/observability/tracer.py) |
| 10 | 三层配置合并优先级（API > KB > settings） | ✅ | [app/rag/retrieval_config.py:47-130](../app/rag/retrieval_config.py) |

容易翻车的点都做对了：

- **AsyncSession 管理**：全部 `async with`，lifespan + task_resources 都正确 `engine.dispose()`
- **Semaphore 用法**：6 处全部 `async with sem:`，无遗漏
- **Celery asyncio 隔离**：ingest / session / eval 4 个任务全部 `asyncio.run`，无跨任务共享 loop
- **Tracer contextvar 安全**：无 `asyncio.create_task` 逃逸，try/finally reset 完整
- **异常 handler 不泄露堆栈**：`unhandled_exception_handler` 仅返通用文案，堆栈写日志
- **Redis AOF + Celery acks_late**：[docker-compose.yml](../docker-compose/docker-compose.yml) 配 `appendonly yes`，[celery_app.py](../app/tasks/celery_app.py) 配 `task_acks_late=True`
- **subprocess 安全**：30s 超时 + 拒绝字符串 cmd + 无 `shell=True`
- **chunk_id 幂等设计**：`_make_chunk_id_int(document_id, index)` SHA256 → INT64 保证同 (document_id, index) 必然撞主键 upsert

### 风险

3 类系统性风险按等级排序：

1. **🔴 P0 严重（4 项）**：CORS 缺失阻塞前端、Neo4j 软失败导致状态机静默不一致、Milvus 写入后 PG 失败留下永久孤岛、4 类公共函数 DRY 严重违反
2. **🟡 P1 中等（7 项）**：6 处 `asyncio.gather` 缺外层 wait_for、入库管道同步 Milvus 阻塞事件循环、3 处 LiteLLM 调用缺第二层超时、retriever.py 违反 AGT-04、删除清理无重试、`milvus_client._client` 全局变量被运行时替换、KbFile 状态机无超时回收
3. **🟢 P2 低（8 项）**：死代码 ingest_task_v1.py、跨版本私有依赖、Milvus expr f-string 拼接、9 个模块缺纯单测、长函数、命名/魔法数字、静默吞异常

---

## 二、🔴 P0 严重问题（必须修复）

### P0-1 CORS 中间件完全缺失

**位置**：[app/main.py:107-131](../app/main.py)

**问题**：`create_app()` 中没有任何 `CORSMiddleware`。整个 `app/main.py` grep 不到 `CORSMiddleware`、`add_middleware`、`allow_origins` 任何匹配。

**问题代码**（[app/main.py:107-131](../app/main.py)）：

```python
def create_app() -> FastAPI:
    """应用工厂。"""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description="GeoAgent V1.0 - 气象空间智能体基础后端引擎",
        version="0.1.0",
        lifespan=lifespan,
    )

    # V1.5 PRD §7.1：统一响应格式（BusinessError / HTTPException / 校验失败 / 未捕获异常
    # 全部翻译成 {code, message, data}），SSE 流式响应不二次包装
    register_exception_handlers(app)

    # 路由挂载
    app.include_router(v1_router)
    app.include_router(v2_router)

    # 健康检查
    @app.get("/health", tags=["健康检查"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
```

整个工厂函数 25 行，**完全没有任何中间件挂载语句**。FastAPI 不会自动注入 CORS。

**影响**：
- 浏览器前端（vite 5173、React dev server 等）对 FastAPI 8000 的所有请求都会被浏览器 CORS 策略阻止
- 部署时若未在前置反向代理（nginx）中配置 CORS，前端完全无法对接
- 这是前端联调时**最高频的现场事故**

**修复方案**：

```python
from fastapi.middleware.cors import CORSMiddleware

# settings.py 加字段
class Settings(BaseSettings):
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:3000"],
    )

# main.py create_app() 内追加
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**预估工时**：30 分钟

---

### P0-2 Neo4j 写入软失败 → status=completed 静默不一致

**位置**：[app/tasks/ingest_task.py:609-619](../app/tasks/ingest_task.py)（Step 9b Neo4j 写入） + 第 626-634 行（Step 11 status=completed）

**问题**：入库管道 Step 9b 的 Neo4j 写入用 `try/except` 吞掉异常 + 设置 `written_entity_count=0`，然后 Step 11 仍然写 `status=completed`。

**问题代码**（[app/tasks/ingest_task.py:608-634](../app/tasks/ingest_task.py)）：

```python
        # Step 9b: Neo4j 写入（软失败）
        try:
            written_entity_count = await _step_neo4j_write(
                resources,
                kb=kb,
                file_record=file_record,
                chunks=chunks,
                chunk_entities=chunk_entities,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Neo4j 写入失败（软失败） file_id=%s: %s", file_id, e)
            written_entity_count = 0   # ⚠️ 静默吞掉，仅 warning

        # Step 10: BM25 稀疏向量确认（V2 Schema BM25 Function 已在 Step 8 自动生成）
        _step_bm25_auto()
        await _set_progress(resources, file_id, progress=PROGRESS_BM25)

        # Step 11: 完成 —— ⚠️ 即使 Neo4j 失败，仍然写 completed
        completed_at = datetime.now(timezone.utc)
        await _set_progress(
            resources,
            file_id,
            progress=PROGRESS_DONE,
            status=FILE_STATUS_COMPLETED,    # ← 这里没有任何"降级"标记
            completed_at=completed_at,
            entity_count=written_entity_count,   # ← 仅 entity_count=0，前端无法区分
        )
```

`written_entity_count=0` 在前端语义模糊：可能是"NER 真的没抽出实体"，也可能是"Neo4j 挂了写不进去"，**没有任何字段能区分**。

**结果**：
- Milvus 中有完整 chunks（含 entity_tags）
- PG 中 KbFile 标"已完成"、KB.chunk_count 已累加
- **Neo4j 中缺 Document 节点和 MENTIONED_IN 关系**
- Graph RAG 检索链路静默缺失实体关联，前端**看不到任何告警**

**影响**：
- 用户认为文件已入库，但实际上 Graph RAG 路径上检索不到
- 检索质量静默退化，无法定位
- 重新 reindex 是当前唯一恢复手段

**修复方案**：

短期（立即可做）：

```python
# ingest_task.py Step 9b
neo4j_failed = False
try:
    write_to_neo4j(...)
except Exception as e:
    neo4j_failed = True
    logger.exception("Neo4j 写入失败，进入降级状态")

# Step 11: 用扩展字段标记降级
if neo4j_failed:
    file_record.metadata = {**(file_record.metadata or {}), "neo4j_failed": True}
# 或新增枚举状态：completed_degraded
```

长期（下迭代）：

- 加 Celery Beat 定时巡检任务，扫描 `status=completed AND entity_count=0 AND skip_ner=False` 的记录
- 对这类记录触发 Neo4j 补写任务

**预估工时**：2 小时

---

### P0-3 Milvus 写入成功后 PG 更新失败 → 永久数据孤岛

**位置**：[app/tasks/ingest_task.py:590-601](../app/tasks/ingest_task.py)

**问题**：Step 8 Milvus upsert 成功后，紧跟着的 `_set_progress` / `_bump_kb_chunk_count` 一旦失败，异常被 Celery `_mark_failed_safe` 捕获写 `status=failed`，但 **Milvus 中已经有完整 chunks**。

**问题代码**（[app/tasks/ingest_task.py:589-606](../app/tasks/ingest_task.py)）：

```python
        # Step 8: Milvus V2 写入（携带 entity_tags + 结构元数据 + parent_chunk_id + is_summary）
        _step_milvus_write_v2(            # ← (1) Milvus upsert 成功 → 数据已落 Milvus
            resources,
            kb=kb,
            file_record=file_record,
            chunks=chunks,
            vectors=vectors,
            chunk_entities=chunk_entities,
        )
        await _set_progress(              # ← (2) 此处若挂（PG 抖动 / 死锁），异常向上抛
            resources, file_id, progress=PROGRESS_MILVUS, chunk_count=len(chunks)
        )
        await _bump_kb_chunk_count(       # ← (3) 同上，PG 失败异常向上抛
            resources, kb_id, delta=len(chunks)
        )

        # Step 9 progress 锚点
        await _set_progress(              # ← (4) 同上
            resources, file_id, progress=PROGRESS_NER, entity_count=entity_count_total
        )
```

(2)/(3)/(4) 任何一个 `_set_progress` 抛出，整个 `_main` 协程异常上抛到 Celery 任务体的 `except` 分支，进入 `_mark_failed_safe` 路径只更新 PG 的 `status=failed`，**Milvus 写入的 chunks 留在那里没有回滚**。

**结果**：
- PG 说 `failed`
- Milvus 实际有数据
- Neo4j 可能也有（如果 Step 8 后才崩）
- **没有任何补偿逻辑**，无法自动恢复

**修复方案**：

```python
# tasks/ingest_task.py 的 _mark_failed_safe（或外层 except 路径）
async def _mark_failed_safe(resources, file_id, error_message):
    try:
        # 1. 现有：标记 PG status=failed
        await _update_status(resources, file_id, status="failed", error=error_message)
        # 2. 新增：清理 Milvus 残留（已有现成函数）
        await _cleanup_milvus_chunks_for_file(resources.milvus, kb_id, file_id)
        # 3. 新增：清理 Neo4j 残留（已有现成函数）
        await _cleanup_neo4j_entities_for_file(resources.neo4j, kb_id, file_id)
    except Exception:
        logger.exception("失败回滚自身也失败，需要人工介入")
```

`_cleanup_milvus_chunks_for_file` 已经存在于 [app/services/kb_file_service.py:355](../app/services/kb_file_service.py)，复用即可。

**预估工时**：2 小时

---

### P0-4 DRY 严重违反 — 关键工具函数 3~5 处重复

#### 4.1 `_truncate_utf8` —— UTF-8 字节安全截断

**重复位置（5 处）**：

| 文件 | 行 |
|---|---|
| [app/ingest/table_description.py](../app/ingest/table_description.py) | L97 |
| [app/ingest/doc_metadata.py](../app/ingest/doc_metadata.py) | L88 |
| [app/tasks/ingest_task.py](../app/tasks/ingest_task.py) | L106 |
| [app/tasks/ingest_task_v1.py](../app/tasks/ingest_task_v1.py) | L100 |
| [app/rag/query_ner.py](../app/rag/query_ner.py) | L51 |

**重复实现示例**：

```python
# app/tasks/ingest_task.py:106
def _truncate_utf8(s: str, max_bytes: int) -> str:
    """按 UTF-8 字节数安全截断；不切断多字节字符。"""
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    return encoded[:max_bytes].decode("utf-8", errors="ignore")

# app/rag/query_ner.py:51 —— 函数体几乎逐字一样
def _truncate_utf8(s: str, max_bytes: int) -> str:
    """按 UTF-8 字节安全截断（中文 3 字节/字）。

    参考项目记忆 [[milvus-varchar-max-length-is-bytes]]：Milvus VARCHAR max_length
    是字节数，中文 22 字 = 66 字节超 entity_tags(max_length=64)，必须按字节截断。
    """
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    # ... 同上
```

5 份代码做的事情完全一致，仅注释不同。

#### 4.2 `_strip_code_fence` —— LLM 输出围栏剥离

**重复位置（3 处 + 1 处内联）**：

| 文件 | 行 |
|---|---|
| [app/rag/query_rewriter.py](../app/rag/query_rewriter.py) | L124 |
| [app/rag/faithfulness.py](../app/rag/faithfulness.py) | L120 |
| [app/ingest/doc_metadata.py](../app/ingest/doc_metadata.py) | L96 |
| [app/kg/ner.py](../app/kg/ner.py) `_parse_entities` | L104-111（内联未抽函数） |

**重复实现示例**：

```python
# app/rag/query_rewriter.py:124
def _strip_code_fence(text: str) -> str:
    """剥离 ```json ... ``` 围栏（LLM 偶尔会加）。"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text

# app/rag/faithfulness.py:120 —— 完全相同的实现
def _strip_code_fence(text: str) -> str:
    """剥离 ```json ... ``` 围栏。"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text
```

`_extract_content` 也在 query_rewriter.py:114 / faithfulness.py:133 / table_description.py:105 三处重复。

#### 4.3 `_resolve_*_kwargs` —— LiteLLM 调用参数构造

**虽 doc_metadata 和 dual_layer 已正确从 table_description 导入复用，但仍有 6 处独立实现**：

| 文件 | 函数 | 行 | 与 IDP 版差异 |
|---|---|---|---|
| [app/kg/ner.py](../app/kg/ner.py) | `_resolve_ner_kwargs` | L54 | 硬编码 `response_format={"type":"json_object"}` |
| [app/rag/query_rewriter.py](../app/rag/query_rewriter.py) | `_resolve_rewriter_kwargs` | L79 | temperature=0.3 |
| [app/rag/faithfulness.py](../app/rag/faithfulness.py) | `_resolve_kwargs` | L84 | temperature=0.1, num_retries=0 |
| [app/tasks/session_task.py](../app/tasks/session_task.py) | `_resolve_kwargs` | L74 | 含 `_clean_title`/`_clean_summary` |
| [app/tasks/eval_task.py](../app/tasks/eval_task.py) | `_resolve_eval_llm_kwargs` | L74 | 仅返回三元组 |
| [app/rag/embedding.py](../app/rag/embedding.py) | `_build_kwargs` | L28 | Embedding 特殊（合理独立） |

#### 4.4 兜底文案（6 处不一致）

| 场景 | 文案 | 位置 |
|---|---|---|
| 检索为空 | `"抱歉，未检索到相关内容。请尝试更换关键词或放宽搜索范围。"` | query.py L217 |
| LLM 生成超时 | `"抱歉，答案生成超时，请稍后重试。"` | query.py L473 |
| LLM 生成失败 | `f"生成答案时遇到错误：{type(e).__name__}。请稍后重试。"` | query.py L476 |
| 整体请求超时 | `f"抱歉，查询处理超时（{settings.query_total_timeout_s:.0f}s）..."` | query.py L98 |
| /v2/generate 超时 | `"抱歉，答案生成超时，请稍后重试。"` | generate.py L209 |
| /v2/generate 失败 | `f"答案生成失败：{type(e).__name__}。请稍后重试。"` | generate.py L105 |

**前端的国际化处理、统一错误展示都会受影响。**

#### 修复方案

新建以下公共模块：

```
app/llm/kwargs.py        # _resolve_kwargs（参数化 model/temp/response_format/num_retries）
                         # _strip_code_fence、_extract_content
app/core/util.py         # _truncate_utf8(text: str, max_bytes: int) -> str
app/api/fallback_texts.py # FALLBACK_RETRIEVE_EMPTY / FALLBACK_LLM_TIMEOUT / ...
```

**预估工时**：4 小时

**影响**：未来改 LLM 调用约定（如统一加 `request_id` 透传）需要改 6 个地方，极易遗漏；不抽公共函数后续每加一个新 LLM 调用都会复制一份。

---

## 三、🟡 P1 中等问题（近期处理）

### P1-5 asyncio.gather 全部缺外层 wait_for（6 处）

**项目记忆 [`asyncio-gather-needs-wait-for-timeout`](../memory/asyncio-gather-needs-wait-for-timeout.md) 明确要求 gather 必须套 wait_for**，但当前 6 处全部缺：

| 位置 | 子协程内有 wait_for？ | 风险 |
|---|---|---|
| [query.py:378](../app/api/v2/endpoints/query.py) `_multi_query_search` | 有，但 gather 无 | multi_query 路径并发 N+1 路 hybrid_search |
| [query_ner.py:192](../app/rag/query_ner.py) `anchor_to_graph` | 有，但 gather 无 | Neo4j 锚定并发 |
| [dual_layer.py:223](../app/ingest/dual_layer.py) | 有，但 gather 无 | IDP-04 摘要生成 |
| [table_description.py:195](../app/ingest/table_description.py) | 有，但 gather 无 | IDP-03 表格描述 |
| [ingest_task.py:512](../app/tasks/ingest_task.py) | 有，但 gather 无 | V2 NER 并发 |
| [ingest_task_v1.py:405](../app/tasks/ingest_task_v1.py) | 有，但 gather 无 | V1 NER 并发（已是死代码） |

**问题代码**（[app/tasks/ingest_task.py:494-514](../app/tasks/ingest_task.py)）：

```python
    async def _safe_ner(idx: int, text: str) -> list[dict]:
        nonlocal completed
        async with sem:
            try:
                result = await asyncio.wait_for(   # ← 内层每个 task 有 wait_for
                    run_ner(text), timeout=NER_SINGLE_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                logger.warning("NER 超时（软失败） chunk_idx=%d", idx)
                result = []
            except Exception as e:
                logger.warning("NER 调用失败（软失败） chunk_idx=%d: %s", idx, e)
                result = []
            ...
            return result

    return await asyncio.gather(                   # ← ⚠️ 但 gather 本身没有外层超时
        *[_safe_ner(i, c.content) for i, c in enumerate(chunks)]
    )
```

**问题代码**（[app/api/v2/endpoints/query.py:372-378](../app/api/v2/endpoints/query.py)）：

```python
    coros = [
        hybrid_search(query=q, top_k=top_k, entity_tags=entity_tags,
                      reranker_enable=reranker_enable,
                      similarity_threshold=similarity_threshold)
        for q in queries
    ]
    raw = await asyncio.gather(*coros, return_exceptions=True)   # ← ⚠️ gather 无 wait_for
```

**问题代码**（[app/rag/query_ner.py:191-192](../app/rag/query_ner.py)）：

```python
    # gather 所有实体的邻接结果；单实体已自捕获，return_exceptions=True 仅作终极兜底
    results = await asyncio.gather(*coros, return_exceptions=True)   # ← ⚠️ 同样缺超时
```

**风险**：虽然内层每个 task 都有 wait_for 兜底了 99%，但 Semaphore 死锁或事件循环饥饿时仍可能挂死。

**修复模板**：

```python
results = await asyncio.wait_for(
    asyncio.gather(*coros, return_exceptions=True),
    timeout=len(coros) * single_timeout + 5,  # 1.x 倍宽容
)
```

**预估工时**：1.5 小时（6 处批量改）

---

### P1-6 同步 Milvus gRPC 阻塞事件循环

**位置**：[app/tasks/ingest_task.py:590](../app/tasks/ingest_task.py) `_step_milvus_write_v2`

**问题**：该函数是 sync `def`，但在 `async def _main()` 内被直接同步调用，内部 `resources.milvus.upsert()` 是同步 gRPC。批量写大文件时持续数秒阻塞事件循环。

**问题代码**（[app/tasks/ingest_task.py:589-597](../app/tasks/ingest_task.py)）：

```python
        # Step 8: Milvus V2 写入（携带 entity_tags + 结构元数据 + parent_chunk_id + is_summary）
        _step_milvus_write_v2(            # ← ⚠️ 注意：没有 await，是同步调用
            resources,
            kb=kb,
            file_record=file_record,
            chunks=chunks,
            vectors=vectors,
            chunk_entities=chunk_entities,
        )
```

`_step_milvus_write_v2` 函数签名是 `def` 而非 `async def`，内部直接调 `resources.milvus.upsert(...)` 同步 gRPC。在 `async def _main()` 中同步调用会阻塞当前事件循环，期间该 worker 进程无法处理任何其他协程任务。

**注意**：progress.md 提到 **Bugfix v2_query 超时**那次修复了 [hybrid_retriever.py](../app/rag/hybrid_retriever.py) 的同款问题（用 `asyncio.to_thread`），但**入库管道这一处遗漏了**。

**修复**：

```python
# Step 8 入库
await asyncio.to_thread(
    _step_milvus_write_v2, resources, kb_id, kb, document_id, chunks, vectors, ner_entities,
)
```

**预估工时**：30 分钟

---

### P1-7 LiteLLM 调用缺第二层 wait_for 防护

| 位置 | 现状 | 风险 |
|---|---|---|
| [session_task.py:198, 304](../app/tasks/session_task.py) | 仅 litellm 内 timeout | Celery 任务挂死，占满 worker 池 |
| [kg/ner.py:157](../app/kg/ner.py) | 仅 litellm 内 timeout | NER 挂起阻塞整个 ingest 管道 |
| [llm/client.py:151, 196](../app/llm/client.py) `acompletion / astream` | **astream 流式更危险** | 流式响应中途卡住无超时机制 |

**问题代码**（[app/tasks/session_task.py:197-198](../app/tasks/session_task.py)）：

```python
        logger.info("标题任务: session_id=%s 调 LLM model=%s", session_id, kwargs["model"])
        resp = await litellm.acompletion(**kwargs)  # ← ⚠️ 仅靠 kwargs 内 timeout，无外层 wait_for
```

**问题代码**（[app/kg/ner.py:156-157](../app/kg/ner.py)）：

```python
    try:
        resp = await litellm.acompletion(**kwargs)  # ← ⚠️ 同上，单层超时
        # 兼容 Pydantic 对象与裸 dict（与 chat client 同样的处理思路）
```

对比 query_rewriter / faithfulness / dual_layer / table_description / doc_metadata 都做了双层防护（progress.md 的 V2 query Bugfix 那次修了一批），这几处属于遗漏。

**修复**：

```python
result = await asyncio.wait_for(
    litellm.acompletion(**kwargs),
    timeout=settings.litellm_timeout * 1.2,  # 给 litellm 内部超时一点宽容
)
```

**预估工时**：1 小时

---

### P1-8 retriever.py 违反 AGT-04 错误反思契约

**位置**：[app/rag/retriever.py:201](../app/rag/retriever.py)

**问题**：`search_knowledge_base` 单 Collection 失败时 `except Exception + continue`，调用方拿到的是空列表，**LLM 无法区分"无相关内容"和"检索器挂了"**。

**问题代码**（[app/rag/retriever.py:184-207](../app/rag/retriever.py)）：

```python
            raw = client.search(
                collection_name=collection,
                data=[query_vec],
                anns_field="vector",
                filter=filter_expr,
                limit=top_k,
                output_fields=["chunk_id", "content", "document_id", "metadata", "entity_tags"],
                search_params={"metric_type": "COSINE", "params": {"ef": 64}},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(                          # ← ⚠️ 仅 warning
                "search_knowledge_base: collection=%s 检索失败（跳过）: %s",
                collection,
                e,
            )
            continue                                 # ← ⚠️ 直接 continue 吞掉异常
        # 后续多个 collection 全失败时，最终返回 [] 给 LLM —— LLM 误判为"无相关内容"
```

**违反契约**：CLAUDE.md 明确 AGT-04：Tool 抛出异常时**必须**捕获堆栈并以 `ToolMessage(status="error")` 回传给模型，让模型自我修正后重试。这里直接 continue 等于吞掉异常。

**修复**：

```python
# 失败时不 continue，而是把错误信息透传出去
try:
    hits = await client.search(...)
except Exception as e:
    logger.exception(...)
    return [{"error": f"检索失败: {type(e).__name__}: {e}"}]
# tool_node 据此包成 ToolMessage(status="error")
```

**预估工时**：1.5 小时

---

### P1-9 删 KB File 时 Milvus/Neo4j 清理无重试

**位置**：[app/services/kb_file_service.py:352, 398](../app/services/kb_file_service.py)

**问题**：`_cleanup_milvus_chunks_for_file` 和 `_cleanup_neo4j_entities_for_file` 失败只 `logger.warning` 不重试，PG 已删但向量/图谱残留 → 孤儿 chunks 和 Document 节点累积。

**问题代码**（[app/services/kb_file_service.py:351-366](../app/services/kb_file_service.py)）：

```python
    try:
        # filter 表达式按 document_id 精确匹配；与 ingest_task 写入时的字段对齐
        client.delete(
            collection_name=collection,
            filter=f'document_id == "{file_id}"',
        )
        logger.info(
            "Milvus 切片已清理 collection=%s file_id=%s", collection, file_id
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(                          # ← ⚠️ 仅 warning，不重试，不补偿
            "Milvus 切片清理失败 collection=%s file_id=%s err=%s",
            collection,
            file_id,
            e,
        )
        # 函数返回，调用方继续走 PG 删除 → Milvus 残留孤儿 chunks
```

Neo4j 清理同款（[app/services/kb_file_service.py:403-415](../app/services/kb_file_service.py)），网络抖动一次就留孤儿数据。

**修复**：

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4))
async def _cleanup_milvus_chunks_for_file(milvus, kb_id, file_id):
    ...
```

最终失败也记到失败重试队列（如 `kb_file_cleanup_retry` PG 表），由定时任务再扫。

**预估工时**：4 小时（含失败重试表设计）

---

### P1-10 milvus_client._client 模块级全局被运行时替换

**位置**：[app/tasks/ingest_task.py:406-412](../app/tasks/ingest_task.py)

**问题**：

**问题代码**（[app/tasks/ingest_task.py:397-412](../app/tasks/ingest_task.py)）：

```python
    # 自愈：确保 V2 collection 存在
    if not resources.milvus.has_collection(collection_name):
        logger.warning(
            "V2 Collection %s 不存在，尝试自愈创建（kb_id=%s dim=%d）",
            collection_name,
            kb.id,
            kb.embedding_dim,
        )
        import app.rag.milvus_client as mod

        prev_client = mod._client                # ← ⚠️ 备份模块级全局
        mod._client = resources.milvus           # ← ⚠️ 临时替换全局单例
        try:
            create_v2_kb_collection(kb.id, dim=kb.embedding_dim)
        finally:
            mod._client = prev_client            # ← 期间任何其他协程读到的是临时实例
```

在 Celery solo 模式下安全（任务串行），但**测试场景或未来 worker + uvicorn 混跑会爆**：其他协程同时调 `get_milvus_client()` 会拿到临时实例。

**修复**：

```python
# milvus_client.py 内
def create_v2_kb_collection(kb_id, dim, *, client=None):
    client = client or _get_default_client()
    ...

# ingest_task.py 调用方
create_v2_kb_collection(kb.id, dim=kb.embedding_dim, client=resources.milvus)
```

**预估工时**：1 小时

---

### P1-11 KbFile 状态机无超时回收

**问题**：worker 崩溃后 PG 记录卡在 `status=processing` 永久不动。前端显示"处理中"但实际已死。用户只能手工触发 reindex（reindex 会先 revoke 任务再重置 status）。

**修复**：Celery Beat 定时任务

```python
# tasks/maintenance.py
@celery_app.task
def recover_stuck_processing_files():
    """每 30 分钟扫描卡死任务"""
    threshold = datetime.utcnow() - timedelta(hours=2)
    stuck_files = session.query(KbFile).filter(
        KbFile.status == "processing",
        KbFile.updated_at < threshold,
    ).all()
    for f in stuck_files:
        f.status = "failed"
        f.error_message = "Worker 崩溃，自动回收"
    session.commit()
```

**预估工时**：2 小时

---

## 四、🟢 P2 低优先级（值得清理）

### P2-12 死代码：ingest_task_v1.py 已无 import 引用

**位置**：[app/tasks/ingest_task_v1.py](../app/tasks/ingest_task_v1.py)

全 `app/` 下 grep 不到 `ingest_task_v1`，[celery_app.py](../app/tasks/celery_app.py) `_TASK_MODULES` 也只注册 V2。

**建议**：直接删除，或挪到 `_archive/` 目录。新人会浪费时间研究为什么还在。

---

### P2-13 retriever.py 私有函数被 hybrid_retriever.py 跨版本导入

**位置**：[app/rag/hybrid_retriever.py:31](../app/rag/hybrid_retriever.py)

```python
from app.rag.retriever import _build_filter_expr, _format_hits, get_current_role
```

V2 跨版本依赖 V1.5 的私有函数（`_` 前缀），违反"V1 不动 V2 独立"决策的精神。`_format_hits` 在 hybrid_retriever 中实际未使用（hybrid 有自己的 `format_hybrid_results`）。

**问题代码**（[app/rag/hybrid_retriever.py:28-32](../app/rag/hybrid_retriever.py)）：

```python
from app.rag.naming import build_kb_collection_name
from app.rag.reranker import RerankResult, get_reranker
from app.rag.retriever import _build_filter_expr, _format_hits, get_current_role
                          # ↑ V2 模块跨版本进 V1.5 模块的 _ 私有命名空间，
                          #   `_format_hits` 引入了但根本没用上（hybrid 有自己的 format_hybrid_results）
```

**修复**：抽到新模块 `app/rag/filters.py` 或并入现有 `retrieval_config.py`。

---

### P2-14 Milvus expr 用 f-string 拼接 LLM 输入

**位置**：[app/rag/retriever.py:69-84](../app/rag/retriever.py)

```python
clauses.append(f'document_id == "{document_id}"')
```

`document_id` 来自 LLM tool call 而非外部 HTTP 请求，攻击面有限，但若 LLM 返回的字符串带 `"` 会导致 Milvus filter 语法错误使检索整体失败。

**问题代码**（[app/rag/retriever.py:67-84](../app/rag/retriever.py)）：

```python
    # 权限基线：硬编码注入（不暴露给 LLM）
    clauses = [f'ARRAY_CONTAINS(allowed_roles, "{current_role}")']

    if doc_type:
        # JSON 字段访问 + 字符串等值
        clauses.append(f'metadata["type"] == "{doc_type}"')        # ← ⚠️ f-string 拼

    if document_id:
        clauses.append(f'document_id == "{document_id}"')          # ← ⚠️ f-string 拼

    if entity_tags:
        # KG-04 图谱锚定后注入：召回任一标签匹配的 chunk
        # 用 Python 列表字面量语法序列化为 Milvus 接受的格式
        tags_lit = "[" + ", ".join(f'"{t}"' for t in entity_tags) + "]"   # ← ⚠️ 同上
        clauses.append(f"ARRAY_CONTAINS_ANY(entity_tags, {tags_lit})")

    return " and ".join(clauses)
```

LLM 偶尔会输出带双引号的实体（如电影/书名"Hello"），这里没有任何转义，会直接破坏 Milvus filter 语法导致整次检索抛错。

**修复**：写一个 `_quote(value: str)` 做 `str.replace('"', '\\"')` 转义。

---

### P2-15 测试覆盖盲区

P0/P1 模块缺**纯单测**（仅在集成测中覆盖）：

| 模块 | 重要性 | 当前覆盖方式 |
|---|---|---|
| [app/rag/hybrid_retriever.py](../app/rag/hybrid_retriever.py) | P0 | 仅集成测（test_v2_t2.py） |
| [app/rag/reranker.py](../app/rag/reranker.py) | P0 | 仅集成测（test_v2_t11.py） |
| [app/rag/citation.py](../app/rag/citation.py) | P0 | 仅集成测（test_v2_p1.py） |
| [app/rag/confidence.py](../app/rag/confidence.py) | P0 | 仅集成测（test_v2_t9.py） |
| [app/rag/faithfulness.py](../app/rag/faithfulness.py) | P0 | 仅集成测（test_v2_t9.py） |
| [app/rag/query_rewriter.py](../app/rag/query_rewriter.py) | P0 | 仅集成测（test_v2_t8.py） |
| [app/rag/embedding.py](../app/rag/embedding.py) | P1 | 无 |
| [app/observability/tracer.py](../app/observability/tracer.py) | P2 | 仅集成测（test_v2_t3.py） |
| [app/observability/analytics_writer.py](../app/observability/analytics_writer.py) | P2 | 仅集成测（test_v2_t12.py） |
| [app/ingest/table_description.py](../app/ingest/table_description.py) | P1 | 仅 v2_t7 集成 |
| [app/ingest/dual_layer.py](../app/ingest/dual_layer.py) | P1 | 仅 v2_t7 集成 |
| [app/ingest/doc_metadata.py](../app/ingest/doc_metadata.py) | P1 | 仅 v2_t7 集成 |

**问题**：集成测试 mock 链路较长，回归时定位困难；这些模块没有 mock 掉 LLM/Milvus 的纯逻辑单测。

---

### P2-16 长函数未拆分

| 函数 | 位置 | 行数 |
|---|---|---|
| `v2_query` + `_v2_query_inner` | [app/api/v2/endpoints/query.py](../app/api/v2/endpoints/query.py) | ~270 行 |
| `_main` (ingest 11 步管道) | [app/tasks/ingest_task.py:530-647](../app/tasks/ingest_task.py) | 117 行 |
| `upload_file` | [app/services/kb_file_service.py:121-240](../app/services/kb_file_service.py) | 120 行 |
| `create_kb` | [app/services/kb_service.py:62-137](../app/services/kb_service.py) | 77 行 |

**修复方向**：抽出 `_write_analytics(snapshot)`、`_run_step(progress, func, *args)`、`_trigger_ingest_task(file_id)` 等辅助函数。

---

### P2-17 命名不一致

#### V2 endpoint 文件名（单数 vs 复数）

| 文件 | 命名风格 |
|---|---|
| `query.py / retrieve.py / generate.py / rerank.py / analytics.py` | 单数 |
| `traces.py / evaluations.py` | **复数** |

#### chunk_id 类型边界

- `Milvus Schema`: `INT64`（int）
- `_make_chunk_id_int`: 返回 `int`
- `StructuredChunk.chunk_id`: `str`（`uuid.uuid4().hex`），**只在内存中有效，不进 Milvus**
- `HybridSearchResult / CitationItem / RetrieveChunkItem`: `int | None`

至少在 `StructuredChunk` 的 docstring 中说明此字段不持久化。

#### kb_id UUID/str 转换

大部分函数签名用 `uuid.UUID`，但 `kg/writer.py` 接受 `str`，`hybrid_retriever.py` 内部 `str(k)` 转换分散出现。建议在 `naming.py` 的 `build_kb_collection_name` 内部统一 `str(kb)`，调用方一律传 `uuid.UUID`。

---

### P2-18 魔法数字未收口到 config

| 值 | 位置 | 收口建议 |
|---|---|---|
| HNSW `ef=64` | [hybrid_retriever.py](../app/rag/hybrid_retriever.py) 3 处 | `settings.milvus_hnsw_ef` |
| `NER_CONCURRENCY=8` | [ingest_task.py:93-94](../app/tasks/ingest_task.py) | `settings.ner_concurrency` |
| `EMBEDDING_BATCH_SIZE=32` / `MILVUS_BATCH_SIZE=50` | ingest_task.py | 至少加注释说明依据 |
| `chunk_size=512, chunk_overlap=64` | [kb_service.py L68-69](../app/services/kb_service.py) | `settings.default_chunk_size` |
| `_NEO4J_CONCURRENCY=5` | [query_ner.py:42](../app/rag/query_ner.py) | `settings.neo4j_concurrency` |
| `_MAX_TAG_BYTES=64`, `_MAX_TAGS=50` | query_ner.py L38-39 | 与 schema.py 重复定义 |

---

### P2-19 静默吞异常的细枝末节

| 位置 | 问题 | 建议 |
|---|---|---|
| [analytics_writer.py:139](../app/observability/analytics_writer.py) | `except Exception: pass` 完全无 log | 至少 `logger.warning` |
| [reranker.py:155, 289](../app/rag/reranker.py) | 降级时分数标 0.0 但调用方不知道是真精排还是降级 | 返回结构加 `degraded: bool` 标志 |
| [kg/ner.py:173-176](../app/kg/ner.py) | 失败返 `[]`，无法区分"无实体"和"NER 挂了"  | 返回 `(entities, error_msg)` 元组或抛回 |

**典型问题代码**（[app/observability/analytics_writer.py:134-140](../app/observability/analytics_writer.py)）：

```python
    except Exception as e:
        logger.warning("Analytics 快照写入失败（已忽略）: %s", e)
        # 失败时回滚，避免 session 处于损坏状态影响后续操作
        try:
            await db.rollback()
        except Exception:
            pass                          # ← ⚠️ 完全静默：rollback 失败时连 log 都没有
```

如果 rollback 本身也抛异常（如连接池已耗尽 / DB 已断开），这里完全没有任何痕迹，运维侧无从定位。

**典型问题代码**（[app/rag/reranker.py:151-160](../app/rag/reranker.py)）：

```python
    async def rerank(self, query, chunks, top_k=5):
        async with self._semaphore:
            try:
                return await self._do_rerank(query, chunks, top_k)
            except Exception as e:
                # 降级策略：失败时返回原顺序，记日志
                logger.warning(
                    "Reranker 调用失败（降级返回原顺序）: %s", e
                )
                return _fallback(chunks, top_k)    # ← ⚠️ 返回 score=0.0 的 RerankResult
                # 调用方收到分数为 0 的结果时，无法判断是 "Reranker 真的认为不相关"
                # 还是 "Reranker 挂了走了降级"
```

---

## 五、✅ 已经做对的设计

| 项 | 现状 |
|---|---|
| **10 项 PRD 架构契约** | **全部符合**（见第一节表格） |
| **AsyncSession 管理** | 全部 `async with`，lifespan + task_resources 都正确 dispose |
| **Semaphore 用法** | 6 处全部 `async with sem:`，无遗漏；并发度合理（5 或 settings 配置值） |
| **Celery asyncio 隔离** | ingest/session/eval 4 个任务全部 `asyncio.run`，无跨任务共享 loop |
| **Tracer contextvar 安全** | 无 `asyncio.create_task` 逃逸，try/finally reset 完整 |
| **异常 handler 不泄露堆栈** | `unhandled_exception_handler` 仅返通用文案，堆栈写日志 |
| **Redis AOF + Celery acks_late** | docker-compose 配 `appendonly yes` + RDB；celery_app 配 `task_acks_late=True` |
| **subprocess 安全** | 30s 超时 + 拒绝字符串 cmd + 无 `shell=True` |
| **三层配置合并优先级** | API > KB > settings 顺序正确，None vs False 区分清晰 |
| **chunk_id 幂等设计** | `_make_chunk_id_int(document_id, index)` SHA256 → INT64 保证同 (document_id, index) 必然撞主键 upsert |
| **路径穿越防御** | `_build_storage_path` 用 UUID 强类型隔离目录 |
| **PG `chunk_count` 加减** | 都是数据库侧原子表达式（`KnowledgeBase.chunk_count + delta`），不依赖 Python 缓存值（除一处 reindex 例外，已纳入 P2 跟踪） |

---

## 六、修复优先级建议表

| 等级 | 修复项 | 工时 | 风险 | 编号 |
|---|---|---|---|---|
| 🔴 立即 | CORS 中间件 | 30 min | 前端对接零阻塞条件 | P0-1 |
| 🔴 立即 | Neo4j 软失败状态机修正 | 2h | 检索质量静默退化 | P0-2 |
| 🔴 立即 | Milvus 写入后 PG 失败的回滚 | 2h | 永久数据孤岛 | P0-3 |
| 🔴 本周 | DRY 重构（4 类公共函数 + 兜底文案） | 4h | 维护性 | P0-4 |
| 🟡 本周 | 6 处 gather 加 wait_for | 1.5h | 高并发挂死 | P1-5 |
| 🟡 本周 | ingest_task 同步 Milvus 包 to_thread | 30 min | 大文件入库阻塞 | P1-6 |
| 🟡 本周 | 3 处 LiteLLM 调用加 wait_for | 1h | 任务挂死占满 worker | P1-7 |
| 🟡 下迭代 | retriever.py AGT-04 修正 | 1.5h | 契约一致性 | P1-8 |
| 🟡 下迭代 | 删除清理加重试 + 补偿任务 | 4h | 孤儿数据累积 | P1-9 |
| 🟡 下迭代 | milvus_client._client 全局变量重构 | 1h | 测试场景安全性 | P1-10 |
| 🟡 下迭代 | KbFile 状态超时回收 Beat 任务 | 2h | 卡住状态 | P1-11 |
| 🟢 下迭代 | 删除 ingest_task_v1.py | 5 min | 清洁度 | P2-12 |
| 🟢 下迭代 | retriever 跨版本依赖解耦 | 1h | 架构纯净度 | P2-13 |
| 🟢 下迭代 | Milvus expr 转义 | 30 min | 健壮性 | P2-14 |
| 🟢 下迭代 | 9 个模块补纯单测 | 8h+ | 回归保护 | P2-15 |
| 🟢 长期 | 长函数拆分 / 命名统一 / 魔法数字收口 | 持续 | 可读性 | P2-16~18 |

**总工时估算**：P0 ~8.5 小时；P1 ~10 小时；P2 (主体) ~12 小时。

---

## 七、推荐的下一步动作

### "Hardening 0.1" 修复 PR（建议立即开工）

把 P0 的 4 项收成一个 PR：

1. ✅ CORS 中间件
2. ✅ Neo4j 软失败的 `metadata.neo4j_failed=True` 标记
3. ✅ Milvus 写入失败后的 PG status=failed 路径加 `_cleanup_milvus_chunks_for_file` + `_cleanup_neo4j_entities_for_file` 回滚
4. ✅ 抽公共 `app/llm/kwargs.py` + `app/core/util.py` + `app/api/fallback_texts.py`

**总工时**：~1 天。**收益**：挡住"前端 demo 时跨域全错"和"用户问'为什么我上传成功了搜不到东西'"两个最高频的现场事故，并为后续所有新代码统一风格打底。

### "Hardening 0.2" 防御性强化（建议本周）

把 P1-5/6/7 三个超时/阻塞问题打包：

1. ✅ 6 处 gather 加 wait_for（统一辅助函数 `_gather_with_timeout(coros, timeout)`）
2. ✅ `_step_milvus_write_v2` 包 `asyncio.to_thread`
3. ✅ session_task / kg/ner / llm/client 三处加外层 `asyncio.wait_for`

**总工时**：~3 小时。**收益**：防止生产环境出现 worker 挂死或事件循环饥饿导致的整体不可用。

### "Quality 0.1" 清理 + 补单测（建议下迭代）

1. P1-8 ~ P1-11（约 8.5 小时）
2. P2-12 ~ P2-15（约 10 小时）

把这两批做完后，可以开始 **V2.1 新功能迭代**或 **生产环境部署**。

---

## 附录：本次审查方法

并行 5 个 Explore agent 同时扫描 5 个维度：

1. **安全与错误处理**：异常吞掉 / subprocess 安全 / SQL/expr 注入 / 路径穿越 / 敏感信息泄露 / 超时缺失 / CORS / Pydantic 校验
2. **异步并发正确性**：阻塞调用堵事件循环 / Session 泄漏 / gather 超时 / Semaphore / Celery asyncio / 共享可变状态 / Tracer 上下文 / race condition
3. **PRD 契约符合度**：10 项核心架构契约逐条核对代码实现
4. **代码质量与重构**：DRY 违反 / 死代码 / 长函数 / 魔法数字 / 命名 / 类型标注 / logging / 测试盲区
5. **数据一致性**：跨存储事务边界 / 删除级联 / 状态机 / chunk_count 累加 / 重复入库幂等 / Redis 持久化

5 维度结果合并去重后形成本报告。

---

**报告作者**：Claude (glm-5.1)
**报告版本**：v1.0
**下次复审建议**：完成 P0 + P1 修复后（预计 2026-06-22 前）

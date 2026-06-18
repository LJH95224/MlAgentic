---

# TyAgent V2.0 Hermes 代码质量审查报告

> **审查日期**：2026-06-17  
> **审查范围**：`app/` 全部 109 个 Python 源文件 + `tests/` 42 个测试文件 + `scripts/` 9 个脚本  
> **代码规模**：约 612,600 字符  
> **审查基线**：PRD V2.0 需求规格说明书 + v2_dev_plan.md + progress.md  
> **审查方法**：全量人工代码审读，五轴评估（正确性/可读性/架构/安全/性能）

---

## 一、整体评价

项目从 V1.0 → V1.5 → V2.0 三次大迭代，代码质量**中等偏上**：模块边界清晰、docstring 规范、错误策略一致、配置集中管理。但 V2.0 叠加了智能切片/混合检索/Reranker/Citation/自检/评估/Trace 七大子系统，**代码间耦合度显著上升**，出现了若干设计漂移和一致性缺陷。

**核心风险**：Agent 聊天路径与 V2 API 查询路径的检索能力**严重不对等**。

---

## 二、🔴 高优先级问题（影响正确性/一致性）

### H-01：NoopReranker 覆盖原始分数为 1.0，污染置信度计算

**位置**：`app/rag/reranker.py` NoopReranker + `app/rag/hybrid_retriever.py` `_apply_reranker`

**问题代码**：

```python
# app/rag/reranker.py — NoopReranker.rerank()
class NoopReranker(BaseReranker):
    async def rerank(self, query, chunks, top_k=5):
        results = []
        for i, chunk in enumerate(chunks[:top_k]):
            results.append(
                RerankResult(
                    index=i,
                    relevance_score=1.0,  # ← 所有结果都给 1.0！
                    ...
                )
            )
        return results
```

```python
# app/rag/hybrid_retriever.py — _apply_reranker 部分
for rr in reranked:
    original = merged[rr.index] if rr.index < len(merged) else None
    if original is None:
        continue
    original.score = rr.relevance_score  # ← 把原 score 覆盖为 1.0！
    final_results.append(original)
```

**影响链**：
1. 所有检索结果 score 被强制拉到 1.0 → `confidence.py` 用 rerank_score 加权 → **置信度永远接近 1.0**
2. `low_confidence_warning` 永远不触发
3. `multi_query` 模式下 RRF 二次融合分数被 1.0 完全覆盖 → **融合排序失效**

**修复建议**：NoopReranker 应保留原始分数而非赋 1.0；或 `_apply_reranker` 对 NoopReranker 跳过分数覆写

---

### H-02：Agent 工具检索与 V2 接口检索能力严重不对等

**位置**：
- Agent 路径：`app/rag/retriever.py` `_do_search()` → 纯向量检索
- V2 API 路径：`app/rag/hybrid_retriever.py` `hybrid_search()` → 向量 + BM25 + RRF + Reranker

**问题代码**：

```python
# app/rag/retriever.py — Agent 走的纯向量检索
async def _do_search(query, top_k, doc_type, document_id, entity_tags):
    # ...
    raw = client.search(
        collection_name=collection,
        data=[query_vec],
        filter=filter_expr,
        limit=top_k,
        output_fields=["chunk_id", "content", "document_id", "metadata", "entity_tags"],
        search_params={"metric_type": "COSINE", "params": {"ef": 64}},
    )
    # ← 只有向量检索！没有 BM25、没有 RRF、没有 Reranker
```

```python
# app/rag/hybrid_retriever.py — V2 API 走的混合检索
async def hybrid_search(query, *, top_k=5, ...):
    # ← 有 BM25 稀疏检索 + RRF 融合 + Reranker 精排 + 降级策略
    results = await asyncio.to_thread(
        _search_single_collection,
        client=client, ... bm25_enable=settings.bm25_enable, rrf_k=settings.rrf_k,
    )
```

**影响**：用户通过 `/api/v1/chat` 聊天触发 Agent `search_knowledge_base`，走纯向量；同一问题通过 `/api/v2/query` 走混合检索，效果差异巨大。违反 PRD §RAG-02 的"Agentic RAG"原则。

**修复建议**：`retriever.py` 的 `_do_search` 改为调用 `hybrid_search()`；或抽取公共检索逻辑

---

### H-03：multi_query 路径下 RRF 分数被 confidence 错误夹值

**位置**：`app/api/v2/endpoints/query.py` `_multi_query_search()`

**问题代码**：

```python
# app/api/v2/endpoints/query.py — RRF 二次融合
async def _multi_query_search(*, queries, top_k, ...):
    # RRF 累加：score(c) = Σ 1/(k + rank_i(c))
    rrf_scores: dict[int, float] = {}
    for rank, item in enumerate(results, start=1):
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (rrf_k + rank)
    # ← RRF 分数范围约 0.01~0.3，远小于 1.0

    # 用 RRF 分数覆盖 score 字段
    merged.append(HybridSearchResult(
        ...
        score=rrf_scores[cid],  # ← RRF 分数替代原始 COSINE 分数
    ))
```

```python
# app/rag/confidence.py — 用 rerank_score 做加权
def compute_confidence(*, cited_chunks, top_k, hallucination_penalty=0.0):
    weighted_score = sum(_safe_score(c) for c in cited_chunks) / len(cited_chunks)
    raw = weighted_score * coverage * (1.0 - penalty)
    if raw > 1.0:
        confidence = 1.0   # ← min(raw, 1.0) 强夹
    # RRF 分数本身 < 1.0，但如果有多个 cited_chunks 分数被 RRF 覆盖后
    # 语义完全不同——confidence 失真
```

**影响**：RRF 分数语义与 COSINE 分数完全不同，直接用于 confidence 加权计算导致结果失真

**修复建议**：`_multi_query_search` 在构造 `HybridSearchResult` 时将 RRF 分数归一化到 [0, 1]

---

### H-04：retrieve.py 的 vector_score / bm25_score / rrf_score 字段从未填充

**位置**：`app/schemas/v2/retrieve.py` + `app/api/v2/endpoints/retrieve.py`

**问题代码**：

```python
# app/schemas/v2/retrieve.py — 定义了三个分项分数
class RetrieveChunkItem(BaseModel):
    vector_score: float | None = Field(default=None, description="稠密向量检索分数")
    bm25_score: float | None = Field(default=None, description="BM25 稀疏检索分数")
    rrf_score: float | None = Field(default=None, description="RRF 融合分数")
    rerank_score: float | None = Field(default=None, description="Reranker 精排分数")
```

```python
# app/api/v2/endpoints/retrieve.py — 只填了 rerank_score
for r in results:
    chunks.append(
        RetrieveChunkItem(
            chunk_id=r.chunk_id,
            content=r.content,
            ...
            rerank_score=r.score,       # ← 只填了这一个
            # vector_score=None,          ← 从未填充
            # bm25_score=None,            ← 从未填充
            # rrf_score=None,             ← 从未填充
        )
    )
# total_retrieved == after_rerank == len(results)，无法反映重排前后差异
```

**影响**：混合检索可观测性缺失，前端无法拆解"稠密向量 vs BM25 vs RRF"各路贡献

**修复建议**：在 `HybridSearchResult` 中携带分项分数，端点透传

---

### H-05：retrieve.py 导入了 Tracer 但 trace_id 始终为空字符串

**位置**：`app/api/v2/endpoints/retrieve.py`

**问题代码**：

```python
# app/api/v2/endpoints/retrieve.py 顶部
from app.observability.tracer import Tracer   # ← 导入了但未使用！

# 返回值中
return RetrieveResponse(
    chunks=chunks,
    total_retrieved=total_retrieved,
    after_rerank=after_rerank,
    trace_id="",               # ← 始终空字符串
    total_latency_ms=total_latency_ms,
)
```

**影响**：纯检索接口无 Trace，无法追踪检索链路

**修复建议**：加 `async with Tracer(...)` 包裹检索流程

---

### H-06：traces.py 用 HTTPException 而非 BusinessError

**位置**：`app/api/v2/endpoints/traces.py`

**问题代码**：

```python
# app/api/v2/endpoints/traces.py
if not steps:
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail=f"trace_id={trace_id} 不存在")
    # ← 违反项目统一错误规范！应该用 BusinessError
```

**对比其他端点的做法**：

```python
# app/api/v2/endpoints/evaluations.py — 正确做法
if row is None:
    raise BusinessError(
        error_codes.NOT_FOUND,
        f"评估任务 {eval_task_id} 在知识库 {kb_id} 下不存在",
    )
```

**影响**：返回格式不统一——HTTPException 返回 `{"detail": "..."}`，BusinessError 返回 `{"code": 40400, "message": "...", "data": null}`

**修复建议**：改为 `raise BusinessError(error_codes.NOT_FOUND, ...)`

---

### H-07：`_build_filter_expr` 未做注入防护

**位置**：`app/rag/retriever.py`

**问题代码**：

```python
# app/rag/retriever.py — 直接拼接字符串
def _build_filter_expr(doc_type, document_id, entity_tags, current_role):
    clauses = [f'ARRAY_CONTAINS(allowed_roles, "{current_role}")']

    if doc_type:
        clauses.append(f'metadata["type"] == "{doc_type}"')
        # ← 如果 doc_type='test" OR 1==1']，表达式被破坏！

    if document_id:
        clauses.append(f'document_id == "{document_id}"')
        # ← 同上，LLM 可能传入含双引号的值

    if entity_tags:
        tags_lit = "[" + ", ".join(f'"{t}"' for t in entity_tags) + "]"
        clauses.append(f"ARRAY_CONTAINS_ANY(entity_tags, {tags_lit})")

    return " and ".join(clauses)
```

**影响**：LLM 传入含 `"` 的 doc_type / document_id 可破坏 Milvus filter 表达式，导致查询异常或绕过过滤

**修复建议**：对字符串值做转义（替换 `"` 为 `\"`）或用参数化方式构建

---

### H-08：ingest_task Step 8（Milvus 写入）在 Step 9（NER）之前执行，entity_tags 不完整

**位置**：`app/tasks/ingest_task.py` `_main()`

**问题代码**：

```python
# app/tasks/ingest_task.py — _main() 执行顺序
# Step 7: 批量向量嵌入
vectors = await _step_embed(chunks)

# Step 9: NER —— 仅对 fine_chunks 跑
chunk_entities_fine = await _step_ner(fine_chunks)  # ← 先跑 NER
# ... 但 NER 结果要到后面才用

# Step 8: Milvus V2 写入（含 entity_tags）
_step_milvus_write_v2(resources, kb=kb, ..., chunk_entities=chunk_entities)
# ← 这里写入了 entity_tags，但 NER 结果在 Step 9 才出来？
# 实际代码中 Step 9 在 Step 8 之前执行了（注释编号与实际执行顺序不一致）

# Step 9 progress 锚点
await _set_progress(resources, file_id, progress=PROGRESS_NER, ...)
```

**影响**：注释编号与实际执行顺序不一致（代码中 NER 实际在 Milvus 写入之前执行，但注释说 Step 8 是 Milvus、Step 9 是 NER），容易导致维护者误解

**修复建议**：修正注释编号使与实际执行顺序一致

---

## 三、🟡 中等优先级问题（影响可维护性/健壮性）

### M-01：PG `create_all` 未引入 Alembic 迁移

```python
# app/main.py — 启动时直接 create_all
async def lifespan(app: FastAPI):
    from app.db.session import engine
    from app.models.base import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # ← 新增字段不会自动加到已有表上，需手动重建
```

**影响**：V2.0 新增了 `AgentTrace`、`QueryAnalytics`、`EvalTask` 等表，新增字段需手动重建表

**修复建议**：引入 Alembic 管理迁移

---

### M-02：Embedding 维度强校验严格但缺少自愈

```python
# app/rag/embedding.py — 维度不匹配直接抛 ValueError
async def aembed_texts(texts: list[str]) -> list[list[float]]:
    # ...
    if len(vec) != settings.embedding_dimension:
        raise ValueError(
            f"Embedding 维度不匹配：期望 {settings.embedding_dimension}，实际 {len(vec)}"
        )
    # ← 阻断整个入库流程，无法自动修正
```

**修复建议**：在 KB 创建时探测一次维度，不匹配时自动更新 KB 的 embedding_dim

---

### M-03：ChatOpenAI 复用 LITELLM 配置但去前缀

```python
# app/agent/graph.py — 剥除厂商前缀
def _strip_model_prefix(model: str) -> str:
    if "/" in model:
        _, _, name = model.partition("/")
        return name  # ← deepseek/deepseek-chat → deepseek-chat
    return model
```

```python
# app/llm/client.py — 自动加前缀
def _resolve_model_name(model, api_base):
    if "/" in model:
        return model
    if "deepseek.com" in api_base:
        resolved = f"deepseek/{model}"  # ← deepseek-chat → deepseek/deepseek-chat
    return resolved
```

**影响**：两套相反的前缀规则，维护时容易改漏

---

### M-04：Tracer 同步写 PG

```python
# app/observability/tracer.py — _flush_to_db
async def _flush_to_db(self):
    async with AsyncSessionLocal() as session:
        await session.execute(insert(AgentTrace), rows)
        await session.commit()
    # ← query 主链路同步等待写入完成
    # 注释说"T12 阶段优化为异步"，未做
```

**影响**：慢查询会拖延响应

---

### M-05：`@lru_cache` 单例 graph + Settings

```python
# app/agent/graph.py
@lru_cache(maxsize=1)
def get_compiled_graph():
    llm = _build_llm()
    # ← .env 改动后必须显式 reset_graph_cache()

# app/core/config.py
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
    # ← 测试中改了环境变量不会自动生效
```

**影响**：配置热更新困难

---

### M-06：V2 collection 未真正使用 `kb_id` 隔离过滤

```python
# app/rag/hybrid_retriever.py — KB 隔离靠 collection 名称
if current_kb_ids is None:
    target_collections = [settings.milvus_collection]
else:
    target_collections = [build_kb_collection_name(kb) for kb in current_kb_ids]
    # ← 完全靠命名规则隔离，collection 内 kb_id 字段是冗余的
    # 如果命名规则变化或 collection 跨 KB 复用，会泄漏
```

**修复建议**：在 filter 表达式中加 `kb_id == "{kb_id}"` 兜底

---

### M-07：`@tool search_knowledge_base` 未做 top_k clamp

```python
# app/rag/retriever.py — Agent 工具
@tool
async def search_knowledge_base(query, top_k=5, ...):
    # ← docstring 说 top_k 范围 1~50，但未做 clamp
    return await _do_search(query, top_k, doc_type, document_id, entity_tags)

# app/rag/hybrid_retriever.py — V2 API 做了 clamp
if top_k < 1:
    top_k = 5
if top_k > 50:
    top_k = 50
```

**修复建议**：`_do_search` 入口加 clamp

---

### M-08：kb_service.delete_kb 三库不一致

```python
# app/services/kb_service.py — 删除 KB
async def delete_kb(db, kb_id):
    # 1. PG 删除
    await db.execute(delete(KnowledgeBase).where(...))
    # 2. Milvus drop collection
    client.drop_collection(collection_name)
    # 3. Neo4j 删除 — 如果这里失败？
    # ← 只能记日志，三库状态不一致
```

**修复建议**：增加补偿机制或定期一致性检查

---

### M-09：厂商前缀推断逻辑 5 处重复

```python
# 以下 5 处都有相同的"根据 api_base 推断厂商前缀"逻辑：
# 1. app/llm/client.py        _resolve_model_name()
# 2. app/kg/ner.py            _resolve_kwargs()
# 3. app/rag/faithfulness.py  _resolve_kwargs()
# 4. app/rag/query_rewriter.py _resolve_rewriter_kwargs()
# 5. app/ingest/table_description.py _resolve_idp_kwargs()

# 每处都是：
if "/" not in model and settings.litellm_api_base:
    if "deepseek.com" in settings.litellm_api_base:
        model = f"deepseek/{model}"
    elif "dashscope.aliyuncs.com" in settings.litellm_api_base:
        model = f"dashscope/{model}"
    elif "open.bigmodel.cn" in settings.litellm_api_base:
        model = f"zhipu/{model}"
```

**修复建议**：统一到 `app/llm/client.py` 的 `_resolve_model_name()`

---

### M-10：V2 query/generate 绕过 llm/client.py 直接调 litellm

```python
# app/api/v2/endpoints/query.py — generate_answer()
import litellm
response = await asyncio.wait_for(
    litellm.acompletion(
        model=settings.litellm_model,  # ← 未走 _resolve_model_name()！
        messages=messages,
        api_key=settings.litellm_api_key,
        api_base=settings.litellm_api_base,
        temperature=0.3,
        max_tokens=2000,
        timeout=settings.litellm_timeout,
    ),
    timeout=hard_timeout,
)
```

**影响**：绕过了 `client.py` 的模型名自动修正、统一日志、token 统计

**修复建议**：改走 `app/llm/client.py` 的 `acompletion()`

---

### M-11：analytics_writer 的 bm25_contributed 虚假统计

```python
# app/observability/analytics_writer.py
for s in steps:
    if s.step_type == "retrieve":
        bm25_contributed = True   # ← 有 retrieve 步骤就认为 BM25 贡献
        # 但 settings.bm25_enable=False 时根本没开 BM25！
```

**修复建议**：检查 `settings.bm25_enable` 配置

---

### M-12：ingest_task 注释编号与实际执行顺序不一致

见 H-08 详细分析。Step 8/9 注释编号颠倒。

---

## 四、🟢 低优先级问题（代码风格/规范）

### L-01：Tracer 禁用时仍然 yield 空 TraceStep

```python
# app/observability/tracer.py
@contextmanager
def step(self, step_type, *, step_input=None, model_name=None):
    if not self._enabled:
        yield TraceStep(step_type=step_type)  # ← 返回空对象
        return
    # 调用方对 step 对象操作（s.step_output = ...）成为无效写入
```

**影响**：静默无效写入，不抛错但行为不可预期

---

### L-02：citation.parse_citations 不考虑同号多次引用顺序

```python
# app/rag/citation.py
refs = re.findall(r"\[(\d+)\]", answer_text)
seen: set[int] = set()
cited_indices: list[int] = []
for ref in refs:
    idx = int(ref)
    if idx not in seen and 1 <= idx <= len(chunks):
        seen.add(idx)
        cited_indices.append(idx)
    # ← 用 set 去重但丢失了首次出现的字符串匹配位置
```

---

### L-03：splitter.py 的 `_TOKEN_LEN_FN` 模块级缓存非线程安全

```python
# app/ingest/splitter.py
@lru_cache(maxsize=1)
def _TOKEN_LEN_FN():
    enc = tiktoken.get_encoding("cl100k_base")
    return lambda text: len(enc.encode(text))
    # ← tiktoken 加载并发时可能初始化两次
```

---

### L-04：大量 `# noqa: BLE001` 裸 except

全项目约 20+ 处裸 `except Exception` 配 `# noqa: BLE001`，缺少分级处理（可重试 vs 永久失败）

---

### L-05：kb_file_service.upload_file 中 task_id 写回单独再 commit

```python
# app/services/kb_file_service.py
async def upload_file(...):
    async with db_session() as session:
        db.add(kb_file)
        await session.commit()
    # ...
    async with db_session() as session:
        kb_file.task_id = task_id
        await session.commit()  # ← 多一次 round-trip
```

---

### L-06：traces.py 列表查询 N+1

```python
# app/api/v2/endpoints/traces.py — list_session_traces
for root in root_steps:
    step_count_result = await db.execute(
        select(func.count()).where(AgentTrace.trace_id == root.trace_id)
    )
    # ← 每个 trace 单独跑一次 count() 查询，N+1 问题
```

**修复建议**：用子查询一次性拿到所有 trace 的 step_count

---

### L-07：ragas_evaluator 模块级 stub 注入过于 hack

```python
# app/rag/ragas_evaluator.py
if "langchain_community.chat_models.vertexai" not in sys.modules:
    _stub = _types.ModuleType("langchain_community.chat_models.vertexai")
    class _ChatVertexAIStub: pass
    _stub.ChatVertexAI = _ChatVertexAIStub
    sys.modules["langchain_community.chat_models.vertexai"] = _stub
    # ← 往 sys.modules 注入 stub，非常规手段
```

---

## 五、⚠️ 架构漂移与设计不一致

### A-01：两条检索链路并存，能力严重分化

| 能力 | Agent `search_knowledge_base` | V2 `hybrid_search` |
|------|-----|-----|
| 稠密向量检索 | ✅ | ✅ |
| BM25 稀疏检索 | ❌ | ✅ |
| RRF 融合 | ❌ | ✅ |
| Reranker 精排 | ❌ | ✅ |
| NER 图谱锚定 | ❌（有参数但未接入） | ✅ |
| 降级策略 | 无 | hybrid→dense |
| 输出格式 | 字符串（给 LLM 看） | 结构化对象 |

**核心问题**：用户通过聊天入口完全无法享受 V2 新能力

---

### A-02：LLM 调用入口分散

全项目有 5+ 处独立调用 `litellm.acompletion`，未走统一 `client.py`：

1. `app/api/v2/endpoints/query.py` — generate_answer()
2. `app/api/v2/endpoints/generate.py` — _generate_answer()
3. `app/kg/ner.py` — _resolve_kwargs()
4. `app/rag/faithfulness.py` — _resolve_kwargs()
5. `app/rag/query_rewriter.py` — _resolve_rewriter_kwargs()
6. `app/ingest/table_description.py` — _resolve_idp_kwargs()

每处独立处理模型名解析、超时、重试，策略可能不一致

---

### A-03：配置的三层合并缺少显式校验

`app/rag/retrieval_config.py` 实现了 `resolve_options()` 三层合并（API > KB > Settings），但：
- `query_rewrite` 非法值在合并后才抛 BusinessError
- `reranker_enable=True` + `reranker_type=none` 语义矛盾但无校验
- KB 的 `retrieval_config` JSONB 可以存任意字段——没有 schema 校验

---

## 六、🧪 测试覆盖盲区（基于代码逻辑推断）

以下场景在现有测试中**未找到覆盖**：

1. **NoopReranker + confidence 联动**：测了 NoopReranker 返回 1.0，但未验证对 confidence 的影响
2. **multi_query + RRF 二次融合后 confidence**：RRF 分数范围异常未被测试捕获
3. **`_build_filter_expr` 注入场景**：doc_type 含特殊字符时的 Milvus filter 行为
4. **retrieve.py 分项分数为 None**：定义了 vector_score/bm25_score/rrf_score 但无测试验证
5. **traces.py 的 HTTPException vs BusinessError**：trace 不存在时返回格式不一致
6. **ingest_task Step 8/9 顺序**：NER/Milvus 前置依赖关系无测试
7. **kb_id 过滤兜底**：Collection 内 kb_id 字段从未在 filter 中使用
8. **Tracer 禁用时 step_output 赋值**：开发者可能不知道赋值无效
9. **generate.py 的 LITELLM_MODEL 不带前缀**：直接传给 litellm.acompletion 会失败
10. **delete_kb 三库不一致**：Milvus 删成功但 PG/Neo4j 失败后的数据状态

---

## 七、📋 修复优先级排序与落地建议

### 第一批（P0——影响正确性，建议 1 周内修复）

| 编号 | 问题 | 修复工作量 |
|------|------|------|
| H-01 | NoopReranker 覆盖分数 → confidence 失真 | S（改 NoopReranker 保留原分数） |
| H-06 | traces.py 用 HTTPException | XS（改 BusinessError） |
| H-07 | filter 表达式注入 | S（加转义） |
| H-04 | retrieve.py 分项分数未填充 | M（需 hybrid_search 携带分项） |

### 第二批（P1——影响一致性，建议 2 周内修复）

| 编号 | 问题 | 修复工作量 |
|------|------|------|
| H-02 | Agent vs V2 检索不对等 | L（retriever 改调 hybrid_search） |
| H-03 | multi_query confidence 失真 | M（RRF 归一化） |
| H-05 | retrieve.py 无 Trace | S（加 Tracer 包裹） |
| M-09 | 厂商前缀 5 处重复 | M（统一到 client.py） |
| M-10 | V2 query/generate 绕过 client.py | M（改走统一封装） |
| A-01 | 两条检索链路架构漂移 | L（同 H-02，重构 retriever） |

### 第三批（P2——健壮性增强，建议 1 月内修复）

| 编号 | 问题 | 修复工作量 |
|------|------|------|
| M-01 | Alembic 迁移 | L（新增基础设施） |
| M-04 | Tracer 异步写入 | M（fire-and-forget） |
| M-06 | kb_id 过滤兜底 | S（加 filter 子句） |
| M-07 | top_k clamp | XS（加一行） |
| M-11 | bm25_contributed 虚假统计 | S（读配置判断） |
| H-08 | ingest Step 8/9 顺序 | M（交换步骤） |

### 第四批（P3——代码质量，持续优化）

| 编号 | 问题 |
|------|------|
| M-03 | 两套前缀规则统一 |
| M-05 | lru_cache 配置不生效 |
| M-08 | delete_kb 补偿机制 |
| M-12 | Ingest 注释编号修正 |
| L-01 ~ L-07 | 各项低优先级问题 |

---

## 八、总结

### 亮点
1. **错误策略一致**：全项目统一的 BusinessError + ApiResponse + error_codes 枚举
2. **软失败设计**：NER/Query改写/Faithfulness/Table Description 全部"异常/超时→降级→不阻断"
3. **配置集中**：所有开关在 Settings 中显式声明，三层合并机制设计合理
4. **Trace 可观测**：全链路步骤追踪，analytics 快照表避免 JSONB 聚合
5. **测试覆盖厚**：42 个测试文件，V2 T0~T12 每阶段有独立测试

### 核心风险
1. **Agent 聊天路径检索效果弱**——V2.0 最大的设计漂移，用户通过聊天入口完全无法享受 V2 新能力
2. **NoopReranker 分数覆盖导致 confidence 系统形同虚设**——开发期置信度永远接近 1.0
3. **LLM 调用入口分散**——5+ 处独立调 litellm，模型名解析/超时/重试策略不一致

### 建议落地路径
1. 先修 H-01（NoopReranker 分数）和 H-06（HTTPException），投入小收益大
2. 再修 H-02（Agent 检索改走 hybrid_search），这是架构层面的根本改进
3. 统一 LLM 调用入口，消除厂商前缀推断的 5 处重复
4. 最后引入 Alembic + delete_kb 补偿 + Tracer 异步写入等基础设施改进
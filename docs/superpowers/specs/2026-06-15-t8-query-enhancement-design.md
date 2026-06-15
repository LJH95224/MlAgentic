# T8 · Query 改写 + Query NER + 检索参数统一配置 —— 设计文档

**日期**：2026-06-15
**对应 PRD**：[TyAgent V2.0 · 需求规格说明书](../../TyAgent%20V2.0%20%C2%B7%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md) §HRE-01 / §HRE-02 / §HRE-06
**对应阶段**：[v2_dev_plan.md](../../v2_dev_plan.md) T8（P2）
**前置依赖**：T0~T6 已完成（V2.0 P0 + P1 全部上线，580 单测通过）

---

## 1. 背景与目标

### 1.1 问题陈述

V2.0 P1 完成后，`/api/v2/query` 已能跑通"混合检索 → 精排 → 引用溯源"全链路，但检索质量受限于以下三类问题：

1. **用户 Query 过于简短或语义模糊**：例如用户问"合同什么时候到期"，文档里写的是"合同期限至 2026 年 12 月 31 日"，向量检索可能因短 Query 语义不充分而错过最相关的 chunk。
2. **图谱知识未被利用**：V1.0 已为知识库建好 Neo4j 实体图谱，但 `/v2/query` 只走纯混合检索，没有把图谱中的实体关系注入为检索过滤条件。例如用户问"张三和北京科技公司的合同"，应该自动锁定到 "张三 / 北京科技公司 / 合同_2024 / 违约条款" 等关联实体所在的 chunks。
3. **检索参数无法统一管理**：top_k / similarity_threshold / enable_bm25 等参数当前要么散在 settings，要么硬编码，KB 管理员无法为不同 KB 设置不同检索策略，且单次查询无法临时覆盖。

### 1.2 目标

- **HRE-01**：在检索前对原始 Query 做改写，提升召回多样性与精度。支持 HyDE（假设性答案）和 multi_query（多角度子查询）两种策略。
- **HRE-02**：在检索前对 Query 做 NER → 查 Neo4j 一跳邻居 → 注入 `entity_tags` 过滤，让混合检索的标量过滤"看到"图谱知识。
- **HRE-06**：把检索参数收口为三层（请求 options > KB.retrieval_config > 全局 settings），KB 设默认、单次查询可覆盖。

### 1.3 非目标

- 不重构 hybrid_search 内核（T2 已稳定）。
- 不改 Neo4j 节点/关系结构（V1.0 已定型）。
- 不实现 T7（IDP-03/04/05 表格描述 + 双层索引），那是 T8 之后的下一阶段。
- 不做 stream（流式 SSE）—— PRD 标 P3，T10 阶段做。

---

## 2. 架构总览

### 2.1 模块拆分

新增 4 个模块 + 修改 3 个现有文件：

```
app/rag/
├── query_rewriter.py    [新] HRE-01 Query 改写（hyde / multi_query / none）
├── query_ner.py         [新] HRE-02 Query NER + 图谱锚定
├── options_resolver.py  [新] HRE-06 三层配置合并
├── hybrid_retriever.py  [不动]
└── ...

app/api/v2/endpoints/query.py   [改] v2_query 串接 T8 三步
app/schemas/v2/query.py         [改] QueryOptions 扩字段
app/core/config.py              [改] 加 query_rewriter 全局开关 + 模型
.env.example                    [改] 同步配置块
tests/test_v2_t8.py             [新] T8 单测套件
```

### 2.2 数据流（v2_query 链路扩展）

```
请求 v2_query(body, db)
   │
   │ Step 0（前置，非 trace step）
   │   resolved = await resolve_options(body.options, body.kb_ids, db, settings)
   │   ResolvedOptions(top_k, query_rewrite, enable_graph_rag, ...)
   │
   ├─→ Tracer.step("query_rewrite")
   │     rewritten = await rewrite_query(body.query, resolved.query_rewrite)
   │     # hyde: rewritten = "假设性答案文本"
   │     # multi_query: rewritten = ["子查询1", "子查询2", "子查询3"]
   │     # none: rewritten = body.query
   │
   ├─→ Tracer.step("query_ner") [仅当 resolved.enable_graph_rag=True]
   │     entities = await extract_query_entities(body.query)  # 复用 run_ner
   │     # → [{"name":"张三","type":"PERSON"}, ...]
   │
   ├─→ Tracer.step("graph_anchor") [仅当 entities 非空]
   │     entity_tags = await anchor_to_graph(entities, body.kb_ids)
   │     # → ["张三", "采购合同_2024", "违约条款"]（实体名 + 一跳邻居名）
   │     # Neo4j 失败软降级为 [e.name for e in entities]
   │
   ├─→ Tracer.step("retrieve")
   │     # multi_query: N 个子查询各调一次 hybrid_search → 应用层 RRF 合并
   │     # hyde / none: 单次 hybrid_search
   │     results = await hybrid_search(
   │         query=rewritten,
   │         top_k=resolved.top_k,
   │         entity_tags=entity_tags,
   │     )
   │
   └─→ build_context / generate / citation_parse [T6 不动]
```

### 2.3 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| HRE-01 实现 | HyDE + multi_query 两种全做 | PRD 验收要两套，工程量适中 |
| HyDE 实现位置 | 把假设答案文本传给 hybrid_search | hybrid_search 内部自动做 embedding + BM25，零侵入 |
| multi_query 合并 | 应用层 RRF（k=60） | 复用 T2 RRF 思路；hybrid_search 不变 |
| HRE-02 NER 模型 | 复用 [run_ner](../../../app/kg/ner.py) | 与入库期同模型同 prompt，实体语义对齐 |
| HRE-02 软降级 | Neo4j 失败 → 仅用原始实体名作 tag | NER 软失败传统延续，不阻断主链路 |
| HRE-06 优先级 | 请求 > KB > 全局（PRD 原话） | 与 PRD §HRE-06 对齐 |
| 多 KB 时 KB 配置取哪个 | 取 kb_ids[0] 的 retrieval_config | PRD 未明确，先简化兜底，注释标待 V2.5 决策 |
| trace 埋点粒度 | 三个新步骤各一个 step | 排查时能定位"5 秒查询"是哪步慢 |
| 模块实现风格 | 纯函数 + 数据类，不引入 class | 与 T6 v2_query 的"endpoint 编排管线"模式一致 |

---

## 3. 模块详细设计

### 3.1 [app/rag/query_rewriter.py](../../../app/rag/query_rewriter.py)（HRE-01）

#### 3.1.1 数据结构

```python
@dataclass
class RewriteResult:
    """Query 改写结果。

    字段语义：
    - strategy: 实际生效的策略（none / hyde / multi_query）
    - rewritten: 单个改写后的字符串（hyde / none 用此字段）
    - sub_queries: 多个子查询列表（multi_query 用此字段）
    - 失败降级时 strategy 会变成 "none" + 用原始 query 填 rewritten
    """
    strategy: Literal["none", "hyde", "multi_query"]
    rewritten: str = ""              # hyde / none 用
    sub_queries: list[str] = field(default_factory=list)  # multi_query 用
    fallback: bool = False           # 是否走了 LLM 失败降级
```

#### 3.1.2 主函数

```python
async def rewrite_query(
    query: str,
    strategy: Literal["none", "hyde", "multi_query"],
) -> RewriteResult:
    """Query 改写主入口（HRE-01）。

    异常策略：LLM 失败时返回 strategy=none + rewritten=原始 query + fallback=True。
    """
    if strategy == "none" or not query.strip():
        return RewriteResult(strategy="none", rewritten=query)

    try:
        if strategy == "hyde":
            answer = await _generate_hyde_answer(query)
            return RewriteResult(strategy="hyde", rewritten=answer)
        elif strategy == "multi_query":
            subs = await _generate_multi_queries(query)
            return RewriteResult(strategy="multi_query", sub_queries=subs)
    except Exception as e:
        logger.warning("Query 改写失败（降级原 query）: %s: %s", type(e).__name__, e)
        return RewriteResult(strategy="none", rewritten=query, fallback=True)
```

#### 3.1.3 HyDE prompt

```python
_HYDE_SYSTEM = """你是一个文档检索辅助助手。请为用户问题生成一段假设性的"理想答案"——
即"如果文档中存在答案，它大概会用什么样的句子表述"。

要求：
1. 假设答案应该是陈述句，含与问题相关的关键术语和实体
2. 长度 80-150 字
3. 不要回答"我不知道"或拒答；即使你不确定，也要生成一段似是而非的答案
4. 不要标注 [1][2] 等引用，不要写"根据文档..."的元话语
5. 直接输出答案文本，不要任何前缀或解释"""
```

#### 3.1.4 multi_query prompt

```python
_MULTI_QUERY_SYSTEM = """你是一个查询改写助手。请将用户的原始问题改写为 2-3 个不同角度的子查询，
便于检索系统从多角度召回相关文档。

要求：
1. 每个子查询应该是独立、自包含的疑问句或陈述句
2. 子查询之间应有语义差异（同义词替换 / 上位概念 / 下位概念 / 时间维度等）
3. 不要丢失原问题的核心实体和动词
4. 仅返回 JSON 对象，格式严格如下：

{"queries": ["子查询1", "子查询2", "子查询3"]}"""
```

#### 3.1.5 解析与容错

- HyDE：直接取 `response.choices[0].message.content`，去前后空白即可。
- multi_query：用 JSON 解析（与 NER 同套 markdown 围栏剥离逻辑），过滤空字符串、去重、限制最多 5 个子查询。
- LLM 配置：默认复用 `LITELLM_MODEL`；可通过 `QUERY_REWRITER_MODEL` 单独配置（推荐用 `deepseek-v4-flash` 等轻量快速模型）。

---

### 3.2 [app/rag/query_ner.py](../../../app/rag/query_ner.py)（HRE-02）

#### 3.2.1 主函数 1：从 query 抽实体

```python
async def extract_query_entities(query: str) -> list[dict]:
    """从用户 Query 中抽取命名实体。

    复用 app.kg.ner.run_ner（与入库期同模型同 prompt，保证实体语义一致）。

    Returns:
        [{"name": "...", "type": "PERSON|LOCATION|ORG|TIME|OTHER"}, ...]
        失败或无实体时返回 []（run_ner 已是软失败）
    """
    from app.kg.ner import run_ner
    return await run_ner(query)
```

#### 3.2.2 主函数 2：图谱锚定

```python
async def anchor_to_graph(
    entities: list[dict],
    kb_ids: list[uuid.UUID] | None,
) -> list[str]:
    """对每个实体查 Neo4j 一跳邻居，合并为 entity_tags 列表。

    流程：
    1. 对每个实体调用 execute_graph_query(name, max_hops=1, kb_ids=...)
    2. 收集 nodes_in_path 中所有 name 字段（实体名 + 邻居名 + Document 名）
    3. 去重 + 截断到 max_tags（默认 50，避免 Milvus filter 表达式过长）

    异常策略：
    - 单个实体查询失败：log warning，跳过该实体
    - 全部查询失败：返回 [e["name"] for e in entities]（仅原始实体名）
    - entities=[]：直接返 []，不查 Neo4j

    Args:
        entities: extract_query_entities 的输出
        kb_ids: 限定到这些 KB 的子图（V1.5 KB-06 已支持）

    Returns:
        entity_tags 列表，给 hybrid_search 用作标量过滤
    """
```

#### 3.2.3 一跳邻居 Cypher 复用策略

直接复用 [app/kg/query.py::execute_graph_query](../../../app/kg/query.py)，固定 `max_hops=1`，不传 `entity_type` 和 `relation_types`（拿全部一跳邻居）。每个实体调一次，Python 层并发用 `asyncio.gather` + `wait_for(8s)` 硬超时（沿用项目记忆 [[asyncio-gather-needs-wait-for-timeout]]）。

#### 3.2.4 实体名归一与过滤

- Neo4j 返回的 `nodes_in_path` 含起点/中间/终点节点，全部 name 字段拍平
- Document 节点用 `document_id` 而非 `name`（已在 query.py format_paths 里处理）
- 过滤掉空字符串和过长字符串（> 64 字节，对齐 Milvus `entity_tags` 字段限制，沿用 [[milvus-varchar-max-length-is-bytes]]）
- 去重保留首次出现顺序

---

### 3.3 [app/rag/options_resolver.py](../../../app/rag/options_resolver.py)（HRE-06）

#### 3.3.1 数据结构

```python
@dataclass
class ResolvedOptions:
    """三层合并后的最终检索参数。"""
    top_k: int                    # 默认 5
    reranker_enable: bool         # 默认 settings.reranker_type != "none"
    bm25_enable: bool             # 默认 settings.bm25_enable
    query_rewrite: Literal["none", "hyde", "multi_query"]  # 默认 "none"
    enable_graph_rag: bool        # 默认 True
    similarity_threshold: float   # 默认 settings.reranker_similarity_threshold
    stream: bool                  # 默认 False
```

#### 3.3.2 合并函数

```python
async def resolve_options(
    req_options: QueryOptions | None,
    kb_ids: list[uuid.UUID] | None,
    db: AsyncSession,
    settings: Settings,
) -> ResolvedOptions:
    """三层配置合并：请求 > KB > 全局（PRD HRE-06）。

    多 KB 时取 kb_ids[0] 的 retrieval_config 作为 KB 层默认（PRD 未明确，
    暂简化处理，注释标待 V2.5 决策）。

    异常策略：
    - PG 查询失败：log warning + 跳过 KB 层，仅全局+请求合并
    - kb_ids=None 或 []：跳过 KB 层
    """
    # Step 1: 全局默认
    base = {
        "top_k": 5,
        "reranker_enable": settings.reranker_type != "none",
        "bm25_enable": settings.bm25_enable,
        "query_rewrite": "none",
        "enable_graph_rag": True,
        "similarity_threshold": settings.reranker_similarity_threshold,
        "stream": False,
    }

    # Step 2: KB 默认
    if kb_ids:
        try:
            kb = await db.get(KnowledgeBase, kb_ids[0])
            if kb and kb.retrieval_config:
                # 仅覆盖 base 中的字段；忽略 KB 配置里 base 没有的 key
                for k, v in kb.retrieval_config.items():
                    if k in base and v is not None:
                        base[k] = v
        except Exception as e:
            logger.warning("加载 KB retrieval_config 失败（用全局默认）: %s", e)

    # Step 3: 请求 options
    if req_options:
        for k, v in req_options.model_dump(exclude_none=True).items():
            if k in base:
                base[k] = v

    return ResolvedOptions(**base)
```

---

### 3.4 [app/schemas/v2/query.py](../../../app/schemas/v2/query.py)（QueryOptions 扩字段）

#### 3.4.1 字段调整（不兼容改动）

```python
class QueryOptions(BaseModel):
    """V2.0 查询选项（HRE-06）。

    所有字段默认 None，表示"跟随 KB.retrieval_config / 全局 settings"。
    实际生效值由 resolve_options() 三层合并得出。
    """
    # 已有（默认值改为 None）
    top_k: int | None = Field(default=None, ge=1, le=50)
    reranker_enable: bool | None = None
    bm25_enable: bool | None = None
    stream: bool | None = None  # 由 False 改为 None（统一语义；T8 不实现 stream 路径，T10 接入）

    # T8 新增
    query_rewrite: Literal["none", "hyde", "multi_query"] | None = None
    enable_graph_rag: bool | None = None
    similarity_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
```

#### 3.4.2 与 P1 单测的不兼容点

P1 端到端测试 [tests/test_v2_p1.py::TestV2QuerySchemas::test_query_request](../../../tests/test_v2_p1.py) 断言 `req.options.top_k == 5`（默认值）。改动后 `top_k` 默认为 `None`，需要：

- 把测试断言改为 `req.options.top_k is None`（表达"未指定，跟随上层"语义）
- 新增一个 `TestOptionsResolver::test_default_top_k` 验证 `resolve_options(None, None, db, settings).top_k == 5`

---

### 3.5 [app/core/config.py](../../../app/core/config.py)（全局配置扩展）

新增 3 个字段：

```python
# T8 V2.0 Query 增强
query_rewriter_model: str | None = Field(default=None, alias="QUERY_REWRITER_MODEL")
# Query NER 模型直接复用 KG_NER_MODEL（已存在，无需新增）
```

`.env.example` 同步追加：

```env
# ─── T8 Query 增强（HRE-01）───
# Query 改写器模型（HyDE / multi_query），缺省复用 LITELLM_MODEL
# 推荐用轻量快速模型（如 deepseek-v4-flash），避免 reasoning 模型的过度思考
QUERY_REWRITER_MODEL=deepseek-v4-flash
```

---

### 3.6 [app/api/v2/endpoints/query.py](../../../app/api/v2/endpoints/query.py)（v2_query 编排扩展）

#### 3.6.1 新链路骨架

```python
@router.post("/query", response_model=QueryResponse)
async def v2_query(body: QueryRequest, db: AsyncSession = Depends(get_db)) -> QueryResponse:
    start_time = time.perf_counter()
    settings = get_settings()

    # Step 0：合并配置（前置，不进 trace）
    resolved = await resolve_options(body.options, body.kb_ids, db, settings)

    async with Tracer(session_id=body.session_id, kb_id=body.kb_ids[0] if body.kb_ids else None) as tracer:
        # T8 Step 1：Query 改写
        with tracer.step("query_rewrite", step_input={"strategy": resolved.query_rewrite}):
            rewrite = await rewrite_query(body.query, resolved.query_rewrite)

        # T8 Step 2：Query NER（仅当启用图谱）
        entities: list[dict] = []
        if resolved.enable_graph_rag:
            with tracer.step("query_ner", step_input={"query_len": len(body.query)}):
                entities = await extract_query_entities(body.query)

        # T8 Step 3：图谱锚定（仅当抽到实体）
        entity_tags: list[str] = []
        if entities and resolved.enable_graph_rag:
            with tracer.step("graph_anchor", step_input={"entity_count": len(entities)}):
                entity_tags = await anchor_to_graph(entities, body.kb_ids)

        # 改造后的 retrieve（multi_query 走多次 + 应用层 RRF）
        with tracer.step("retrieve", step_input={"strategy": rewrite.strategy, "tag_count": len(entity_tags)}):
            results = await _retrieve_with_rewrite(rewrite, entity_tags, resolved)

        # ... 后续 build_context / generate / citation_parse 不动
```

#### 3.6.2 _retrieve_with_rewrite 内部实现

```python
async def _retrieve_with_rewrite(
    rewrite: RewriteResult,
    entity_tags: list[str],
    resolved: ResolvedOptions,
) -> list[HybridSearchResult]:
    """根据改写策略调度 hybrid_search。"""
    if rewrite.strategy == "multi_query" and rewrite.sub_queries:
        # 多路并发检索 + 应用层 RRF
        sub_results = await asyncio.gather(*[
            hybrid_search(
                query=sub,
                top_k=resolved.top_k * 2,  # 每路多取留余量
                entity_tags=entity_tags or None,
            )
            for sub in rewrite.sub_queries
        ], return_exceptions=True)
        # 过滤异常结果
        sub_results = [r for r in sub_results if isinstance(r, list)]
        return _rrf_merge(sub_results, top_k=resolved.top_k)
    else:
        # hyde 用假设答案，none 用原 query
        return await hybrid_search(
            query=rewrite.rewritten,
            top_k=resolved.top_k,
            entity_tags=entity_tags or None,
        )


def _rrf_merge(
    results_list: list[list[HybridSearchResult]],
    top_k: int,
    k: int = 60,
) -> list[HybridSearchResult]:
    """应用层 RRF 合并多路检索结果。

    模块归属：本函数与 _retrieve_with_rewrite 同放 [app/api/v2/endpoints/query.py](../../../app/api/v2/endpoints/query.py)
    内部（非 hybrid_retriever 内核），属于 query 编排逻辑。

    去重规则：
    - 优先用 chunk_id 作为去重 key；chunk_id 为 None 时用 content 字符串哈希兜底
    - 同一 key 跨多路出现 → RRF 分数累加（公式：Σ 1 / (k + rank_i(d))）
    - 累加后按总分降序取 top_k
    """
```

---

## 4. 错误处理矩阵

| 失败点 | 触发条件 | 行为 | 业务影响 |
|--------|----------|------|----------|
| `resolve_options` PG 查询失败 | KB 不存在 / DB 抖动 | log warning + 用全局默认 | 退化为无 KB 级配置 |
| `rewrite_query` LLM 失败 | LLM 限流 / 超时 / 解析失败 | log warning + RewriteResult(strategy=none, fallback=True) | 退化为原 query 检索 |
| `extract_query_entities` 失败 | LLM 抖动（已是软失败） | 返回 [] | 跳过 graph_anchor |
| `anchor_to_graph` 单实体 Neo4j 失败 | 单查询超时 / Cypher 错 | log warning + 跳过该实体 | 该实体不参与 entity_tags |
| `anchor_to_graph` 全部失败 | Neo4j 不可达 | 返回 `[e.name for e in entities]` | 仅原始实体名作 tag |
| `_retrieve_with_rewrite` multi_query 部分失败 | 单子查询 hybrid_search 抛错 | `return_exceptions=True` 过滤异常路 | 用剩余成功路的结果做 RRF |
| `hybrid_search` 失败 | T2 已有降级（仅向量） | T8 不动 | — |

**软失败原则**：所有 T8 模块均不阻断主链路。最坏情况下退化为 P1 现有的"纯混合检索 + 无图谱锚定"。

---

## 5. 测试策略

### 5.1 测试文件

[tests/test_v2_t8.py](../../../tests/test_v2_t8.py)（新增）—— 覆盖 4 大模块 + 1 个端到端 + P1 兼容性修复。

### 5.2 测试用例规划

| 测试类 | 用例数 | 覆盖点 |
|--------|--------|--------|
| `TestQueryRewriter` | 6 | none 原样返回 / hyde 调 LLM / multi_query 解析 / LLM 失败降级 / 子查询过滤空与去重 / 自定义模型生效 |
| `TestQueryNER` | 5 | extract_query_entities mock run_ner / anchor_to_graph 多实体合并去重 / Neo4j 失败软降级 / 空 entities 短路 / kb_ids 透传 |
| `TestOptionsResolver` | 6 | 仅全局默认 / KB 覆盖全局 / 请求覆盖 KB / 三层都设时优先级 / kb_ids=None 短路 / PG 失败降级 |
| `TestRRFMerge` | 3 | 公式正确性 / 跨路 chunk_id 累加 / 单路退化为 top_k 截断 |
| `TestV2QueryE2EWithT8` | 4 | hyde + graph_rag 双开 / multi_query 多路 RRF / enable_graph_rag=False 跳过 NER+anchor / NER 抽空时不调 anchor |
| `TestT6Compat`（修复） | 2 | P1 测试 `req.options.top_k is None` 默认 / 不传 options 时 resolve 后默认值正确 |

**预计 26 个新单测 + 修复 P1 中 2 个断言。**

### 5.3 集成测试（用户手动跑）

按 progress.md 已有约定，T8 上线后用户手动验证：

1. 真接 LLM 跑 hyde 改写：`POST /v2/query` with `options.query_rewrite=hyde`，查 trace 验 step 顺序
2. 真接 Neo4j 跑 graph_anchor：用 V1.5 已建库的实体名做 query，验 `entity_tags` 注入到 hybrid_search filter
3. KB.retrieval_config 设 `enable_graph_rag=false` → 单次查询验不走 NER+anchor

---

## 6. 实施分解（给 writing-plans 用）

T8 拆为 5 个增量提交（每个独立可测）：

1. **T8.1 HRE-06 OptionsResolver**：扩 QueryOptions 字段 + 实现 resolve_options + 修复 P1 兼容
   - ⚠️ 此阶段完成后 P1 [tests/test_v2_p1.py::TestV2QuerySchemas](../../../tests/test_v2_p1.py) 中的 `req.options.top_k == 5` 断言会失败，**必须在同一提交内修复**，避免跨提交破坏回归
2. **T8.2 HRE-01 QueryRewriter**：实现 rewrite_query（hyde + multi_query + none）+ 单测
3. **T8.3 HRE-02 QueryNER + Anchor**：实现 extract_query_entities + anchor_to_graph + 单测
4. **T8.4 v2_query 编排**：把三步串到 endpoint + 实现 _retrieve_with_rewrite + _rrf_merge + 端到端单测
   - 此阶段完成后 P1 [tests/test_v2_p1.py::TestV2QueryE2E](../../../tests/test_v2_p1.py) 应仍 100% 通过（默认 query_rewrite=none + enable_graph_rag=True 但抽不到实体 → 等价于 P1 链路）
5. **T8.5 配置 / 文档**：config.py + .env.example + progress.md + v2_dev_plan.md 状态更新

---

## 7. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| HyDE LLM 调用增加延迟（+1-3s） | /v2/query 总耗时上升 | options.query_rewrite 默认 "none"；用户/KB 主动开启才生效 |
| multi_query 触发 N 次 hybrid_search 拖累延迟 | 多路并发但仍受最慢一路约束 | sub_queries 上限 3；用 asyncio.gather 并发；后续可加 Semaphore 限流 |
| Neo4j 一跳邻居过多导致 entity_tags 爆炸 | Milvus filter 表达式过长 | 限 max_tags=50；超出按节点出现频率截断 |
| Query NER 把无意义词识别为实体 | 误注入 entity_tags 缩小召回 | run_ner 已限定 5 类实体；ALLOWED_ENTITY_TYPES 白名单过滤；anchor 失败时软降级 |
| KB.retrieval_config schema 漂移 | 未来加字段时旧 KB 配置缺字段 | resolve_options 用 base.update + 仅覆盖已知 key，旧 KB 自动取全局默认 |
| 多 KB 仅取 kb_ids[0] 配置 | 跨 KB 查询时其它 KB 配置被忽略 | 注释标待 V2.5 决策；当前少数场景可接受 |

---

## 8. 验收标准

### 8.1 单测（自动）

- T8 单测套件 26/26 通过
- P1 兼容性修复 2/2 通过
- 全量回归：608+ passed（580 → 608+，零回归）

### 8.2 集成（用户手动）

- ✅ HRE-01：开启 hyde 后，"合同什么时候到期"能召回含"合同期限""有效期至"的 chunk（PRD 验收原话）
- ✅ HRE-02：含 KB 内已有实体的 query → trace 中能看到 query_ner + graph_anchor 两步 + retrieve step 的 entity_tags 字段非空
- ✅ HRE-06：KB 设 enable_graph_rag=false 后查询不走 NER+anchor；options 传 enable_graph_rag=true 覆盖 KB 配置

---

*T8 Design · End of Document*

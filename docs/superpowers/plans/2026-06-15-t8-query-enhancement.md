# T8 · Query 改写 + Query NER + 三层配置合并 实施计划

> **阶段**：V2.0 Hermes T8（P2）
> **PRD 子需求**：HRE-01 / HRE-02 / HRE-06
> **前置**：T6（统一查询接口 `/api/v2/query`）✅
> **预计代码量**：~600 行实现 + ~400 行测试
> **目标落地路径**：`docs/superpowers/plans/2026-06-15-t8-query-enhancement.md`

---

## 1. Context（为什么做）

T6 阶段的 `/api/v2/query` 端到端跑通了 `检索 → 生成 → 引用解析` 主链路，但有两块功能位被显式 skip：

1. **Query 改写**：用户输入往往简短模糊（"合同什么时候到期？"），直接拿原文做向量检索召回率低；PRD 要求支持 HyDE（生成假设答案做检索）和 multi_query（拆 2~3 个子查询并行检索后融合）。
2. **Query NER + 图谱锚定**：V1.5 已落地 Neo4j Entity 图谱（按 kb_id 隔离），但 T6 的检索只走 Milvus 单路，**完全没用到图谱**。PRD 要求检索前先从 Query 抽实体 → Neo4j 查邻接实体 → 注入 Milvus `entity_tags` 做精筛，这是 V2.0 相对 V1.5 的核心护城河之一。

同时 PRD §HRE-06 要求所有检索参数有"**API options > KB.retrieval_config > 全局 Settings**"三层优先级，目前代码完全没有合并函数，KB 表的 `retrieval_config` JSONB 字段（T0 已建）也未暴露给用户。

T8 要做的就是把这三件事一次性补齐，让 `/api/v2/query` 真正成为"专业级 RAG 引擎"的查询入口。

---

## 2. 已就绪的依赖（直接复用）

| 模块 | 文件 | 复用方式 |
|---|---|---|
| 入库 NER | [app/kg/ner.py](../../app/kg/ner.py) `run_ner(text)` | T8 在 `app/rag/query_ner.py` 薄封装为 `extract_query_entities`，prompt 不动；后续可独立切换轻量模型 |
| Neo4j 多跳查询 | [app/kg/query.py](../../app/kg/query.py) `execute_graph_query(...)` | 固定 `max_hops=1` 调用，已支持 `kb_ids` 过滤 |
| Hybrid retriever 的 `entity_tags` 入参 | [app/rag/hybrid_retriever.py](../../app/rag/hybrid_retriever.py) `hybrid_search(..., entity_tags=...)` | T2 阶段已实现，直接传 |
| Milvus `ARRAY_CONTAINS_ANY` 过滤 | [app/rag/retriever.py](../../app/rag/retriever.py) `_build_filter_expr` | 现有 entity_tags 分支已就位 |
| KB.retrieval_config 字段 | [app/models/knowledge_base.py](../../app/models/knowledge_base.py) | T0 已建 JSONB 字段（nullable，default None） |
| Tracer 上下文 | [app/observability/tracer.py](../../app/observability/tracer.py) | step_type 是 free string，T8 直接 `tracer.step("query_rewrite")` 即可 |
| 错误码体系 | [app/api/error_codes.py](../../app/api/error_codes.py) + `BusinessError` | T8 新增 40011 一条 |
| asyncio 硬超时模式 | [app/tasks/ingest_task.py](../../app/tasks/ingest_task.py) `wait_for + Semaphore` | T8 multi_query 并行检索复用同款 |

---

## 3. 关键设计决策（已与用户对齐）

| # | 决策点 | 选择 | 影响 |
|---|---|---|---|
| 1 | KB CRUD 暴露 `retrieval_config` | **暴露** | KbUpdateRequest / kb_service.update_kb / endpoint 都加该字段；HRE-06 闭环 |
| 2 | multi_query N 路结果合并 | **RRF 二次融合**（k=60） | 沿用 T2 RRF 思想，`chunk_id` 去重 + rank-based 重算分数；语义最对齐 PRD |
| 3 | `query_rewrite` 枚举非法的错误码 | **独立 40011** | error_codes.py 新增 `QUERY_REWRITE_INVALID = 40011` + 自定义 validator 抛 BusinessError，与 PRD §1127 严格对齐 |
| 4 | Graph RAG 锚定默认开关 | **默认 True** | settings + KB + options 三层都默认启用；Query 无实体或实体不在图谱时自动短路，无副作用 |
| 5 | Query NER 是否独立模块 | **新增 `app/rag/query_ner.py` 薄封装** | 调用方语义清晰；未来切轻量模型零侵入 |
| 6 | HyDE / multi_query 的 LLM 模型 | **默认复用 `settings.litellm_model`，可通过新增 `QUERY_REWRITER_MODEL` 单独配** | 与 KG_NER_MODEL 同款解耦风格 |
| 7 | multi_query 子查询数量 | **固定 3 路**（N=3），含原 query 共 4 路检索后融合 | 平衡延迟和召回多样性；后续可调 |
| 8 | 图谱锚定的 entity_tags 上限 | **截断 50 个**（按 PRD `entity_tags ARRAY capacity=50`） + UTF-8 字节安全 | 避免 Milvus filter expr 超长 |

---

## 4. 实施步骤（按依赖顺序）

### T8.1 · 配置层 + 错误码 + Schema 扩展（基础）

**改 [app/core/config.py](../../app/core/config.py)** —— V2.0 区段新增：
```python
# --- Query 增强（HRE-01/02）---
query_rewriter_model: str | None = Field(default=None, alias="QUERY_REWRITER_MODEL")
query_rewrite_default: str = Field(default="none", alias="QUERY_REWRITE_DEFAULT")  # none / hyde / multi_query
graph_rag_enable: bool = Field(default=True, alias="GRAPH_RAG_ENABLE")
multi_query_count: int = Field(default=3, alias="MULTI_QUERY_COUNT", ge=2, le=5)
query_ner_timeout_s: float = Field(default=8.0, alias="QUERY_NER_TIMEOUT_S")
graph_anchor_timeout_s: float = Field(default=5.0, alias="GRAPH_ANCHOR_TIMEOUT_S")
```

**改 [app/api/error_codes.py](../../app/api/error_codes.py)**：新增 `QUERY_REWRITE_INVALID = 40011`，HTTP 400，message `"query_rewrite 参数值不在枚举范围内"`。同步在 `HTTP_STATUS_BY_CODE` / `DEFAULT_MESSAGES` 注册。

**改 [app/schemas/v2/query.py](../../app/schemas/v2/query.py)** —— `QueryOptions` 改造：
- 新增 `query_rewrite: Literal["none","hyde","multi_query"] | None = None`
- 新增 `enable_graph_rag: bool | None = None`
- 新增 `similarity_threshold: float | None = Field(None, ge=0.0, le=1.0)`
- `top_k` 改默认 `None`（让 resolve_options 三层合并决定，不再硬编码 5）
- 加 `@field_validator` 自定义校验：query_rewrite 不在枚举时抛 `BusinessError(40011)`，覆盖 Pydantic 默认 422

**改 `QueryResponse`** 新增可选字段：
- `rewritten_query: str | None = None` —— HyDE 改写后的假设答案，调试用
- `sub_queries: list[str] | None = None` —— multi_query 拆出的子查询列表
- `ner_entities: list[dict] | None = None` —— Query NER 结果 `[{"name":..,"type":..}]`
- `graph_anchored_tags: list[str] | None = None` —— 图谱锚定后注入的 entity_tags

**改 [app/schemas/knowledge_base.py](../../app/schemas/knowledge_base.py)**：
- `KnowledgeBaseUpdateRequest` 加 `retrieval_config: dict | None = None`
- `KnowledgeBaseDetail` 加 `retrieval_config: dict | None = None`

**改 [app/services/kb_service.py](../../app/services/kb_service.py) `update_kb`**：接收并写入 `retrieval_config`，None 表示不变更（与 name/description 同款语义）。

### T8.2 · 三层配置合并（HRE-06 核心）

**新增 [app/rag/retrieval_config.py](../../app/rag/retrieval_config.py)**：

```python
@dataclass
class ResolvedRetrievalOptions:
    top_k: int
    similarity_threshold: float
    bm25_enable: bool
    reranker_enable: bool
    query_rewrite: str           # "none" / "hyde" / "multi_query"
    enable_graph_rag: bool
    rrf_k: int
    rerank_top_n: int

async def resolve_options(
    *,
    options: QueryOptions,
    kb: KnowledgeBase | None,   # 多 KB 时取第一个；后续可演进为 union 策略
    settings: Settings,
) -> ResolvedRetrievalOptions:
    """三层合并：API options > kb.retrieval_config > 全局 settings"""
```

合并规则：
- API options 任一字段 `not None` → 优先用
- 否则查 `kb.retrieval_config[<key>]`
- 否则用 `settings.<key>`（或硬编码默认值，如 `top_k=5`）

**调用位置**：`v2_query` 入口处替换原有的 `body.options.top_k` 等直读逻辑。

### T8.3 · Query 改写器（HRE-01）

**新增 [app/rag/query_rewriter.py](../../app/rag/query_rewriter.py)**：

```python
@dataclass
class RewriteResult:
    rewritten_text: str | None       # hyde 时为假设答案；其他为 None
    sub_queries: list[str]           # multi_query 时为子查询列表；其他为 []

async def rewrite_query(
    query: str,
    strategy: str,              # "none" / "hyde" / "multi_query"
    *,
    n_sub: int = 3,
) -> RewriteResult:
    ...
```

- **`none`**：直接返回 `RewriteResult(None, [])`，零 LLM 调用
- **`hyde`**：调 LLM 生成 100~200 字"假设性答案"，填入 `rewritten_text`
- **`multi_query`**：调 LLM 一次性生成 N 个不同角度的子查询（`response_format={"type":"json_object"}`），填入 `sub_queries`，N=`settings.multi_query_count`

**LLM 调用包 `asyncio.wait_for(timeout=settings.query_ner_timeout_s)`**；失败/超时时**软降级为 `none`**（返 `RewriteResult(None, [])`），不阻断主链路；记 warning。

**模型选择**：优先 `settings.query_rewriter_model`，缺省回退 `settings.litellm_model`，与 `KG_NER_MODEL` 同款解耦风格。

### T8.4 · Query NER + 图谱锚定（HRE-02）

**新增 [app/rag/query_ner.py](../../app/rag/query_ner.py)**：

```python
async def extract_query_entities(query: str) -> list[dict]:
    """薄封装 app.kg.ner.run_ner，加 wait_for(query_ner_timeout_s) 硬超时。
    异常/超时返 []（软失败）。"""

async def anchor_to_graph(
    entities: list[dict],
    kb_ids: list[str] | None,
) -> list[str]:
    """对每个实体调 execute_graph_query(max_hops=1, kb_ids=kb_ids)。
    收集邻接实体 name → 去重 + UTF-8 字节截断到 64 → 上限 50 → 返 entity_tags。"""
```

**关键策略**：
- `extract_query_entities` 软失败：异常/超时返 `[]`
- `anchor_to_graph` 多个实体并发查 Neo4j（`asyncio.gather` + `Semaphore(5)` + `wait_for(graph_anchor_timeout_s)`）
- 任一实体查询失败 → 该实体 skip，不影响其他
- 实体名按 UTF-8 字节截断到 64 bytes（参考项目记忆 [[milvus-varchar-max-length-is-bytes]]）
- 整体返 `[]` 时 → 不传 entity_tags 给 hybrid_search（自动短路）

### T8.5 · query.py 主链路改造

**改 [app/api/v2/endpoints/query.py](../../app/api/v2/endpoints/query.py)**，替换 line 51-69 部分：

```
async with Tracer(...) as tracer:
    # Step 0: 加载 KB + 三层合并配置
    kb_obj = await _load_first_kb(db, body.kb_ids)
    resolved = await resolve_options(options=body.options, kb=kb_obj, settings=settings)

    # Step 1: Query 改写（HRE-01）
    with tracer.step("query_rewrite", step_input={...}):
        rewrite_result = await rewrite_query(body.query, resolved.query_rewrite)

    # Step 2: Query NER（HRE-02）
    with tracer.step("query_ner", step_input={...}):
        ner_entities = await extract_query_entities(body.query) if resolved.enable_graph_rag else []

    # Step 3: 图谱锚定
    with tracer.step("graph_anchor", step_input={...}):
        entity_tags = await anchor_to_graph(ner_entities, body.kb_ids) if ner_entities else []

    # Step 4: 混合检索（按改写策略分支）
    with tracer.step("retrieve", step_input={...}):
        if resolved.query_rewrite == "multi_query":
            results = await _multi_query_search(
                queries=rewrite_result.sub_queries + [body.query],
                entity_tags=entity_tags or None,
                resolved=resolved,
            )
        else:
            search_text = rewrite_result.rewritten_text or body.query  # hyde 时用假设答案
            results = await hybrid_search(
                query=search_text,
                top_k=resolved.top_k,
                entity_tags=entity_tags or None,
            )

    # Step 5+: 不变（build_context / generate / citation_parse）
```

**`_multi_query_search` 实现要点**：
- `asyncio.gather(*[wait_for(hybrid_search(q, ...), timeout=...) for q in queries])`，并发 N 路
- 任一路失败 → warning 跳过，其他继续（return_exceptions=True 或 try/except per-call）
- 结果按 `chunk_id` dedup，重新计算 RRF score：`score(c) = Σ 1/(k + rank_i(c))`，k=`settings.rrf_k`
- 取 top_k 返回

**KB 加载**：`_load_first_kb` 简单 `session.get(KnowledgeBase, kb_ids[0])`；多 KB 时本期取第一个，后续按需演进。

**响应字段补齐**：把 rewrite_result / ner_entities / entity_tags 透出到 QueryResponse 对应字段。

### T8.6 · 单测

**新增 [tests/test_v2_t8.py](../../tests/test_v2_t8.py)** —— 沿用 P1 mock 模式（patch hybrid_search / litellm.acompletion / Tracer / Neo4j driver），无需真 DB / Milvus / Neo4j。

覆盖矩阵：

| 模块 | 用例 |
|---|---|
| `resolve_options` | 三层合并优先级（API > KB > settings）/ kb=None / 字段缺失兜底 |
| `rewrite_query` | none 直通 / hyde happy / multi_query 解析 JSON / LLM 失败软降级 / 超时降级 |
| `extract_query_entities` | happy / 空 query / 超时返 [] / LLM 异常返 [] |
| `anchor_to_graph` | 多实体并发 / 部分实体失败容错 / 字节截断 / 上限 50 / kb_ids=[] 短路 |
| `_multi_query_search` RRF 融合 | 同 chunk 在多路命中分数累加 / 单路独占按 rank 计分 / 一路失败不影响其他 |
| Schema 校验 | query_rewrite Literal 非法值抛 40011（不是 422）/ KB Update 接受 retrieval_config |
| 端到端 v2_query | hyde 路径 / multi_query 路径 / Graph RAG 命中实体 / Graph RAG 无实体短路 / KB 配置覆盖全局 settings |

**修兼容**：[tests/test_v2_p1.py](../../tests/test_v2_p1.py) `TestV2QuerySchemas::test_query_request` 当前断言 `top_k == 5`，T8 改默认为 None 后需调整为 `top_k is None`，并在 resolve_options 落到 5。

### T8.7 · 进度文档同步

完成后更新 [docs/progress.md](../../docs/progress.md)：
- T8 行 → ✅，完成日期 2026-06-15
- 追加 T8 详细子节（交付内容 / 关键设计决策 / 验证状态）
- 历史变更顶部加一条

同步 [docs/v2_dev_plan.md](../../docs/v2_dev_plan.md) 末尾追加 `### ✅ T8 完成 · 2026-06-15`。

---

## 5. 关键文件清单

**新增**：
- `app/rag/retrieval_config.py` —— 三层合并
- `app/rag/query_rewriter.py` —— HyDE / multi_query
- `app/rag/query_ner.py` —— Query NER + 图谱锚定
- `tests/test_v2_t8.py`

**修改**：
- `app/core/config.py` —— V2.0 区段加 6 个字段
- `app/api/error_codes.py` —— 加 40011
- `app/schemas/v2/query.py` —— QueryOptions / QueryResponse 扩字段 + validator
- `app/schemas/knowledge_base.py` —— Update/Detail 加 retrieval_config
- `app/services/kb_service.py` —— update_kb 支持 retrieval_config
- `app/api/v2/endpoints/query.py` —— 主链路插 query_rewrite / query_ner / graph_anchor 三步
- `tests/test_v2_p1.py` —— top_k 默认值兼容

---

## 6. 验证方式

### 6.1 单测
```bash
pytest tests/test_v2_t8.py -v                   # T8 全部用例
pytest tests/test_v2_p1.py -v                   # P1 兼容性回归
pytest tests/ -v --ignore=tests/test_v1_5_integration*.py   # 全量 mock 回归（目标 580 → ~620+，零回归）
```

### 6.2 端到端联调（用户手动）
启动依赖：`docker compose up -d`（PG/Milvus/Neo4j/Redis）+ `uvicorn app.main:app --reload`

**HRE-01 验收（HyDE）**：
```bash
curl -X POST http://127.0.0.1:8000/api/v2/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "合同什么时候到期",
    "kb_ids": ["<kb_uuid>"],
    "options": {"query_rewrite": "hyde"}
  }'
```
检查响应中 `rewritten_query` 含"合同期限/有效期至"等假设性表述；source_citations 命中含此类表述的 chunk。

**HRE-02 验收（图谱锚定）**：
- 上传含"张三 / 北京科技公司"的文档让 NER 入图谱
- Query "张三和北京科技公司的合同条款？" → 看响应 `ner_entities` 含两个实体，`graph_anchored_tags` 含图谱邻接实体；查 trace 看 `graph_anchor` step 有命中
- Query "今天天气怎么样？"（无图谱实体）→ `ner_entities` 可能空，`graph_anchored_tags=[]`，trace 看 graph_anchor step 短路返 0 标签

**HRE-06 验收（三层配置）**：
- `PATCH /api/v1/knowledge-bases/{kb_id}` 设置 `retrieval_config={"enable_graph_rag": false}`
- 对该 KB 查询 → trace 看 query_ner / graph_anchor step 跳过
- 同次查询 body 加 `options.enable_graph_rag=true` → 覆盖 KB 配置，本次启用图谱

### 6.3 Trace 完整性验收
查询完成后取 `trace_id`：
```bash
curl http://127.0.0.1:8000/api/v2/traces/<trace_id>
```
验证 steps 顺序：`query_rewrite → query_ner → graph_anchor → retrieve → build_context → generate → citation_parse`，每步 `step_latency_ms` 合理，失败步骤的 `error_message` 已捕获。

---

## 7. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| HyDE 多调一次 LLM，~1s 延迟 + token 成本 | 用户体验下降 | 默认 `query_rewrite_default="none"`，按需开启；trace 暴露 LLM token；后续考虑缓存 |
| multi_query 3 路并行检索可能放大 LLM 误差 | 召回噪音上升 | RRF k=60 抑制单路异常；后续接 T11 RAGAS 评估对比开关效果 |
| Query NER 短文本（10~30 字）质量不稳定 | 实体漏抽 | 沿用入库 prompt（5 类通用实体），软失败返 [] 自动短路；后续按真实数据决定是否调 prompt |
| 图谱锚定单实体查询慢 | 整批延迟劣化 | `Semaphore(5)` + `wait_for(5s)` 硬超时 + 软失败 |
| entity_tags 列表过长导致 Milvus filter expr 超长 | 检索失败 | `anchor_to_graph` 强制截断 50 个 + UTF-8 字节安全 |
| 多 KB 时 retrieval_config 取第一个 KB 不严谨 | 跨 KB 配置语义模糊 | 本期就这样实现，文档注明限制；后续按需扩展为 union 或 KB 维度 fan-out |

---

*T8 实施计划 · End of Document*

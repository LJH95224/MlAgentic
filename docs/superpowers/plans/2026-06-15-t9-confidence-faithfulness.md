# T9 · 置信度评分 + 答案自检 实施计划

> **阶段**：V2.0 Hermes T9（P2 收尾）
> **PRD 子需求**：CHC-03 / CHC-04
> **前置**：T5（Citation 注入解析）✅、T6（统一查询接口）✅、T8（三层配置合并）✅
> **预计代码量**：~350 行实现 + ~250 行测试
> **目标落地路径**：`docs/superpowers/plans/2026-06-15-t9-confidence-faithfulness.md`

---

## 1. Context（为什么做）

T6 之后 `/api/v2/query` 已经能**输出带引用的答案**，但 PRD §CHC 系列还有两步幻觉控制没接通：

1. **CHC-03 置信度评分**：基于"被引用 chunk 的 rerank_score 加权 + 引用覆盖率 + 自检惩罚"算出 0~1 的 `confidence` 分数，让前端能看到"这次回答有多可靠"。低于 0.5 时填 `low_confidence_warning` 文本预警。
2. **CHC-04 答案自检（Faithfulness Check）**：LLM 生成答案后，再调一次轻量 LLM 把答案中的关键事实声明逐一比对 context，标 `supported` / `unverified`，把 unverified 比例反馈给 confidence 当 `hallucination_penalty`。

T9 是 P2 阶段最后一项，做完之后整个 V2.0 P0/P1/P2 三波都收尾，剩下的 T10/T11/T12 都属于 P3+ 增强项。

---

## 2. 已就绪的依赖（直接复用）

| 模块 | 文件 / 函数 | 状态 |
|---|---|---|
| 引用解析 | [app/rag/citation.py](../../app/rag/citation.py) `parse_citations(answer, chunks_for_citation)` 已返回 `[{chunk_id, document_name, ..., rerank_score}]` 列表 | ✅ T5 已做 |
| 主链路 trace 框架 | [app/api/v2/endpoints/query.py](../../app/api/v2/endpoints/query.py) `tracer.step()` 已有 7 步埋点 | ✅ T8 已做 |
| 三层配置合并 | [app/rag/retrieval_config.py](../../app/rag/retrieval_config.py) `resolve_options` + `ResolvedRetrievalOptions` | ✅ T8 已做 |
| QueryOptions 扩展位 | [app/schemas/v2/query.py](../../app/schemas/v2/query.py) `QueryOptions` 已有 enable_graph_rag/bm25_enable 等同款 None 三态字段 | ✅ T8 已做 |
| LLM 调用模式 | [app/rag/query_rewriter.py](../../app/rag/query_rewriter.py) 软失败 + wait_for + JSON 输出 + 围栏剥离的完整模板 | ✅ 可参考复用 |
| Settings 字段习惯 | `idp_llm_model` / `query_rewriter_model` 同款 None → 复用 LITELLM_MODEL 模式 | ✅ 可参考复用 |
| Tracer step 类型 | step_type 是 free string，PRD §886 已声明 `faithfulness_check` 是合法值 | ✅ 直接用 |

---

## 3. 关键设计决策（已与用户对齐）

| # | 决策点 | 选择 | 影响 |
|---|---|---|---|
| 1 | CHC-04 默认开关 | **默认 False** | 与 PRD §637 一致；高频调用不多花 LLM token；按需开 |
| 2 | unverified 在 answer 上怎么表现 | **追加文本清单**（在原 answer 末尾加一段 `⚠ 以下事实未在检索内容中找到明确支撑：- claim1 - claim2`） | 简单可靠，零额外 LLM 调用；前端无需特殊解析 |
| 3 | 低置信度阈值 | **0.5 硬编码**（PRD §553 原文要求） | 遵循 PRD；如未来需要调，T12 阶段再做配置化 |
| 4 | confidence 公式 | 严格按 PRD §540：`weighted_avg(rerank_scores) × coverage_factor × (1 − hallucination_penalty)` | rerank_scores 取被引用 chunk；coverage = `len(cited)/top_k` |
| 5 | hallucination_penalty 默认值 | **未跑自检时 = 0.0**（公式退化为前两项） | 自检关闭/失败时不惩罚 confidence |
| 6 | 自检失败标记 | 响应加 `faithfulness_check: "ok" \| "skipped" \| "disabled"`（PRD §586 风格） | "disabled"=未启用；"ok"=跑通；"skipped"=超时/异常软失败 |
| 7 | 自检 LLM 模型 | **新增 `FAITHFULNESS_CHECK_MODEL`**（缺省回退 `LITELLM_MODEL`） | 与 KG_NER_MODEL / IDP_LLM_MODEL / QUERY_REWRITER_MODEL 同款解耦风格 |
| 8 | 检索为空时的 confidence | **0.0**（无引用 → 无信心） | 检索空兜底文案场景下 confidence=0、warning 触发 |
| 9 | source_citations 只含被引用的 chunk | 沿用 T5 行为 | confidence 公式分子分母都按 PRD 取被引用集合 |
| 10 | 自检超时 | 复用 PRD §588 "不超过 2 秒"作为参考；新增 `faithfulness_check_timeout_s`（默认 8.0，比 PRD 宽，因为远程模型有抖动） | wait_for 硬超时 + 软失败返 skipped |

---

## 4. 实施步骤

### T9.1 · 配置层 + Schema 扩展

**改 [app/core/config.py](../../app/core/config.py)** —— V2.0 区段新增 3 字段：
```python
# --- 答案自检（CHC-04，T9 阶段启用） ---
# 留空则复用 LITELLM_MODEL（与 IDP_LLM_MODEL / KG_NER_MODEL 同款解耦风格）
faithfulness_model: str | None = Field(default=None, alias="FAITHFULNESS_CHECK_MODEL")
# 答案自检全局默认开关（API options.enable_faithfulness_check / KB 配置都可覆盖）
faithfulness_check_default: bool = Field(default=False, alias="FAITHFULNESS_CHECK_DEFAULT")
# 自检 LLM 调用硬超时（秒）；超时软失败返 skipped
faithfulness_check_timeout_s: float = Field(default=8.0, alias="FAITHFULNESS_CHECK_TIMEOUT_S")
```

**改 [app/schemas/v2/query.py](../../app/schemas/v2/query.py)**：
- `QueryOptions` 加 `enable_faithfulness_check: bool | None = None`
- `QueryResponse` 加 4 个字段：
  - `confidence: float | None = None` —— CHC-03 评分（0~1）
  - `low_confidence_warning: str | None = None` —— PRD §553 文案
  - `faithfulness_check: str | None = None` —— "ok" / "skipped" / "disabled"
  - `unverified_claims: list[dict] | None = None` —— `[{claim, status, source_text}]` 列表

**改 [app/rag/retrieval_config.py](../../app/rag/retrieval_config.py)**：
- `ResolvedRetrievalOptions` 加 `enable_faithfulness_check: bool`
- `resolve_options` 新增字段三层合并（API > KB > settings.faithfulness_check_default）

### T9.2 · CHC-03 置信度评分

**新增 [app/rag/confidence.py](../../app/rag/confidence.py)**：

```python
@dataclass(frozen=True)
class ConfidenceScore:
    confidence: float                # 0~1
    low_confidence_warning: str | None
    breakdown: dict                  # {weighted_score, coverage, penalty}，trace 可见

def compute_confidence(
    *,
    cited_chunks: list[dict],         # parse_citations 输出
    top_k: int,                        # resolved.top_k，作为 coverage 分母
    hallucination_penalty: float = 0.0,
) -> ConfidenceScore:
    """confidence = weighted_avg(rerank) × coverage × (1 − penalty)
    
    - cited_chunks 为空 → confidence=0.0，warning 触发
    - rerank_score 取 c["rerank_score"]，None 时按 0 计
    - coverage = min(len(cited)/top_k, 1.0)
    - confidence < 0.5 时填 low_confidence_warning（PRD §556 文案）
    """
```

**关键策略**：
- 纯函数，无 IO；好测
- weighted_avg 这里的 PRD 措辞是"被引用 Chunk 的 Reranker 分数均值"，权重就用引用次数（一个 chunk 在答案里被引多次仍只算一次，因为 parse_citations 已去重）→ 简单平均即可
- 极小数值（< 1e-9）的 confidence 也按 0.0 处理，避免 float 噪音
- 警告文案严格按 PRD §556

### T9.3 · CHC-04 答案自检

**新增 [app/rag/faithfulness.py](../../app/rag/faithfulness.py)**：

```python
FAITHFULNESS_SYSTEM_PROMPT = """给定上下文和答案，判断答案中每个关键事实声明
是否有上下文支撑。仅返回 JSON 数组：

[{"claim": "...", "status": "supported" | "unverified", "source_text": "..."}]

约束：
- claim 是答案中的独立事实声明（数字、日期、定性论断等）；闲谈/总结性语言可忽略
- status 必须是 supported / unverified 二选一
- supported 时 source_text 给出上下文中的支撑句（≤ 50 字）；unverified 时填空字符串
- 直接输出 JSON，不要 markdown 围栏
- 答案中无可验证事实时返 []"""

@dataclass(frozen=True)
class FaithfulnessResult:
    status: Literal["ok", "skipped", "disabled"]
    claims: list[dict]                  # 全部 claims（含 supported / unverified）
    unverified: list[dict]              # 仅 unverified 子集
    hallucination_penalty: float        # = len(unverified) / max(len(claims), 1)

async def check_faithfulness(
    *,
    answer: str,
    context: str,
) -> FaithfulnessResult:
    """LLM as Judge 自检。任何异常/超时返 status="skipped" + penalty=0.0。"""
```

**关键策略**：
- 调用 `litellm.acompletion`，`response_format={"type":"json_object"}` —— 但 PRD 期望返回数组而非对象，所以 prompt 里强调直接输出数组；不支持 array 的 response_format 时去掉该参数
- 包 `wait_for(faithfulness_check_timeout_s)` 硬超时
- 软失败：异常 / JSON 解析失败 → status="skipped" + penalty=0.0（不惩罚 confidence）
- 复用 [app/rag/query_rewriter.py](../../app/rag/query_rewriter.py) 的 `_resolve_kwargs` 厂商前缀推断习惯（独立写一份避免循环依赖）

**辅助函数 `append_unverified_warning(answer, unverified)`**：
```python
def append_unverified_warning(answer: str, unverified: list[dict]) -> str:
    """如有 unverified，在 answer 末尾追加警告清单。无 unverified 时原样返回。
    
    格式：
        <answer 原文>
        
        ⚠ 以下事实未在检索内容中找到明确支撑：
        - claim 1
        - claim 2
    """
```

### T9.4 · query.py 主链路串入

**改 [app/api/v2/endpoints/query.py](../../app/api/v2/endpoints/query.py)**：

在 Step 7 解析引用之后插入 CHC-04 + CHC-03：

```python
# 已有 Step 7: 解析引用
with tracer.step("citation_parse", ...):
    source_citations = parse_citations(answer, chunks_for_citation)

# Step 8 (T9): 答案自检（CHC-04）
faith_result = FaithfulnessResult(
    status="disabled", claims=[], unverified=[], hallucination_penalty=0.0,
)
if resolved.enable_faithfulness_check:
    with tracer.step("faithfulness_check",
                     step_input={"answer_len": len(answer), "context_len": len(context)}) as f_step:
        faith_result = await check_faithfulness(answer=answer, context=context)
        f_step.step_output = {
            "status": faith_result.status,
            "claim_count": len(faith_result.claims),
            "unverified_count": len(faith_result.unverified),
            "penalty": faith_result.hallucination_penalty,
        }
    # 如有 unverified，把警告清单追加到 answer
    if faith_result.unverified:
        answer = append_unverified_warning(answer, faith_result.unverified)

# Step 9 (T9): 置信度评分（CHC-03）
score = compute_confidence(
    cited_chunks=source_citations,
    top_k=resolved.top_k,
    hallucination_penalty=faith_result.hallucination_penalty,
)
```

`QueryResponse` 构造时透出新字段（含**检索为空兜底分支**也要透）：
```python
return QueryResponse(
    answer=answer,
    source_citations=...,
    trace_id=tracer.trace_id,
    total_latency_ms=...,
    rewritten_query=...,
    sub_queries=...,
    ner_entities=...,
    graph_anchored_tags=...,
    confidence=score.confidence,
    low_confidence_warning=score.low_confidence_warning,
    faithfulness_check=faith_result.status,
    unverified_claims=faith_result.unverified or None,
)
```

**检索为空兜底**：直接构造 `ConfidenceScore(0.0, warning, ...)` + `FaithfulnessResult(status="disabled" if not enabled else "skipped")`，透回响应。

### T9.5 · 单测

**新增 [tests/test_v2_t9.py](../../tests/test_v2_t9.py)** —— 沿用 T8 的 mock 模式（patch litellm.acompletion / hybrid_search / Tracer），无需真服务。

覆盖矩阵：

| 模块 | 用例 |
|---|---|
| `compute_confidence` | 高分高覆盖（>0.8）/ 全 unverified（penalty=1）/ 空 chunks (=0) / coverage 上限 1.0 / rerank_score=None 安全 |
| 低置信度警告 | confidence < 0.5 触发 / >= 0.5 不触发 / 文案与 PRD §556 一致 |
| `check_faithfulness` | happy path 解析数组 / 全 supported penalty=0 / 半 unverified penalty=0.5 / JSON 解析失败 skipped / LLM 超时 skipped / 围栏剥离 / 空 claims 数组 |
| `append_unverified_warning` | 有 unverified 追加 / 无 unverified 不动 / 多 claim 列表格式 |
| `resolve_options` 增量 | enable_faithfulness_check 三层合并 / KB 关闭 settings 开 |
| Schema | QueryOptions 接受 enable_faithfulness_check / QueryResponse 含 4 个新字段 |
| 端到端 v2_query | 自检 disabled 走默认（penalty=0）/ 自检 enabled + 全 supported / 自检 enabled + unverified 触发 answer 追加 / 自检失败 status=skipped 但响应仍正常 / 检索空兜底 confidence=0 + warning |

**修兼容**：[tests/test_v2_p1.py](../../tests/test_v2_p1.py) E2E 测试需补 mock `check_faithfulness`（虽然默认 disabled 不会走 LLM 调用，但代码路径会读 `resolved.enable_faithfulness_check`，需确保不抛错；新字段 `confidence` 等出现在响应里的断言可以保持原状—因为默认 disabled 时 confidence 仍会算出 > 0 的值，但老测试不断言 confidence，应不影响）。

P1 测试需要审查的点：旧 E2E 用例的 `mock_db.get` 已经在 T8 阶段补上了，不需要再改；只需要确认新 fields 不会让旧断言报错。

### T9.6 · 进度文档同步

完成后更新 [docs/progress.md](../../docs/progress.md)：
- T9 行 → ✅，完成日期 2026-06-15
- 追加 T9 详细子节
- 历史变更顶部加一条

同步 [docs/v2_dev_plan.md](../../docs/v2_dev_plan.md) 末尾追加 `### ✅ T9 完成 · 2026-06-15`，并在文档中标注 **P2 阶段全部完成**。

---

## 5. 关键文件清单

**新增**：
- `app/rag/confidence.py` —— CHC-03（纯函数）
- `app/rag/faithfulness.py` —— CHC-04（LLM 调用）
- `tests/test_v2_t9.py`

**修改**：
- `app/core/config.py` —— V2.0 区段加 3 字段
- `app/schemas/v2/query.py` —— QueryOptions 加 enable_faithfulness_check；QueryResponse 加 4 字段
- `app/rag/retrieval_config.py` —— ResolvedRetrievalOptions + resolve_options 增量合并 enable_faithfulness_check
- `app/api/v2/endpoints/query.py` —— Step 7 后插入 faithfulness_check + compute_confidence；响应字段透出
- 可能：`tests/test_v2_p1.py` 兼容修复（如有断言失败）

---

## 6. 验证方式

### 6.1 单测
```bash
pytest tests/test_v2_t9.py -v                            # T9 全部
pytest tests/test_v2_p1.py tests/test_v2_t8.py -v        # P1 + T8 兼容回归
pytest tests/ --ignore=tests/test_v1_5_integration*.py   # 全量 mock 回归（目标 654 → ~690+，零回归）
```

### 6.2 端到端联调（用户手动）
```bash
docker compose up -d
uvicorn app.main:app --reload
```

**CHC-03 验证**：
```bash
curl -X POST http://127.0.0.1:8000/api/v2/query \
  -H "Content-Type: application/json" \
  -d '{"query":"...","kb_ids":["<kb>"]}'
```
- 检索到高相关 chunks（rerank_score 均值 > 0.8 且全部被引用）→ `confidence > 0.8`
- 检索结果质量低 → `confidence < 0.5` + `low_confidence_warning` 文案
- 检索为空 → `confidence = 0.0` + warning

**CHC-04 验证**：
```bash
curl -X POST http://127.0.0.1:8000/api/v2/query \
  -H "Content-Type: application/json" \
  -d '{"query":"合同金额是多少？","kb_ids":["<kb>"],"options":{"enable_faithfulness_check":true}}'
```
- 检索内容中无金额，LLM 编造金额 → `unverified_claims` 数组含该金额声明
- `answer` 末尾追加 ⚠ 警告清单
- `faithfulness_check: "ok"`
- 关闭开关时 `faithfulness_check: "disabled"`，`unverified_claims: null`

### 6.3 Trace 完整性
查询完成后查 trace：
```bash
curl http://127.0.0.1:8000/api/v2/traces/<trace_id>
```
开启自检时应看到 step 顺序：`...citation_parse → faithfulness_check`，每步 step_latency_ms 合理；step_output 含 status/claim_count/penalty。

---

## 7. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| CHC-04 多调一次 LLM，~1~2s 延迟 + token 成本 | 用户体验下降 | 默认 False；开启后包 `wait_for(8s)` 硬超时；超时软失败标 skipped 不影响主链路 |
| LLM JSON 数组输出不稳（部分模型不支持 array response_format） | 解析失败率高 | prompt 强调直接 JSON 数组 + 围栏剥离 + 异常软失败；不依赖 response_format |
| confidence 公式 `weighted_avg` 措辞模糊 | 不同实现不一致 | 选最简单的"算术平均"；所有 cited chunk 等权；breakdown 字段透出便于调试 |
| coverage 大于 1（cited > top_k 不应发生但极端边界） | confidence 超 1 | `min(coverage, 1.0)` 兜底 |
| 默认 disabled 时 query.py 还要构造 FaithfulnessResult 占位 | 代码冗余 | 用模块级常量 `_DISABLED_RESULT` |
| 检索空 / LLM 失败兜底分支也要有这些字段 | 漏掉导致 schema 校验失败 | 在 v2_query 顶部初始化默认 score/faith_result，所有 return 路径统一透出 |

---

*T9 实施计划 · End of Document*

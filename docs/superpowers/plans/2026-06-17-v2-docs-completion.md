# V2.0 文档收尾 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 V2.0 Hermes 迭代的工程配套文档全部补齐：dev_plan 状态、architecture V2 章节、api_reference、frontend_guide、celery 补段。让前端联调与后续接手都有完整文档可循。

**Architecture:** 严格沿用 V1.5 现有文档的章节结构（[v1_5_api_reference.md](../../v1_5_api_reference.md) 777 行 / [v1_5_frontend_guide.md](../../v1_5_frontend_guide.md) 566 行）作为模板，写对应的 V2 版本；architecture.md 追加第三部分（V2.0 增量）；不重复 V1.5 已写过的通用约定（响应格式、错误码总表等），只在引用处明确链接。

**Tech Stack:** 纯 Markdown 文档；引用源用 `file_path:line_number` 格式；接口示例用 `curl` + JSON。

---

## 范围说明

**本 plan 不做** 的事：
- 不补 architecture.md 第二部分（V1.5 §18~§22 "⏳ 待填写"）—— 那是 V1.5 历史欠账，跟 V2.0 解耦
- 不写 V2 新增字段的 SQL 迁移脚本（V2 现状是 `Base.metadata.create_all`）
- 不动 V1.5 已有文档（v1_5_api_reference.md / v1_5_frontend_guide.md / v1.5_dev_plan.md 都不改）

**本 plan 产出**：
| 文件 | 操作 | 预估行数 |
|---|---|---|
| `docs/v2_dev_plan.md` | Modify | +30 行（状态行 + 完成日期表 + 收尾段） |
| `docs/celery_dev_guide.md` | Modify | +40 行（eval_task 段） |
| `docs/architecture.md` | Modify（追加第三部分） | +500 行（V2.0 §23~§30 共 8 节） |
| `docs/v2_api_reference.md` | **Create** | ~900 行（11 端点 + 通用约定） |
| `docs/v2_frontend_guide.md` | **Create** | ~600 行（V2 新增 5 个前端模块） |

---

## File Structure

| 文档 | 唯一职责 |
|---|---|
| `v2_dev_plan.md` | V2.0 13 阶段拆分计划（已写）+ 收尾状态（待补） |
| `architecture.md` 第三部分 | V2.0 架构原理 / 数据流 / 关键设计决策（"为什么这么做"） |
| `v2_api_reference.md` | V2 API 全部端点的对外契约（路径 / 请求体 / 响应体 / 错误码 / curl 示例） |
| `v2_frontend_guide.md` | 前端如何对接 V2 新功能（路由 / UI 模块 / SSE / 状态管理） |
| `celery_dev_guide.md` | Celery 开发约定 + 任务列表（含新增 eval_task） |

---

## Task 1: 更新 v2_dev_plan.md 顶部状态与表格

**Files:**
- Modify: `docs/v2_dev_plan.md`（第 1~13 行的总览段 + 第 19~32 行的阶段表）

**背景**：当前 v2_dev_plan.md 顶部是 "**当前状态：** 待开干（V1.5 全链路 smoke 已通过 2026-06-11，作为 V2.0 的底座）"，过期；§0.1 阶段表也没有"完成日期"列。

- [ ] **Step 1.1: 改第 1~13 行的总览段**

打开 [docs/v2_dev_plan.md](../../v2_dev_plan.md)，把开头：

```markdown
# TyAgent V2.0 (Hermes) 开发拆分计划

> 起始日期：**2026-06-12** · 配套 PRD：[TyAgent V2.0 · 需求规格说明书](TyAgent%20V2.0%20%C2%B7%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md)
> **当前状态：** 待开干（V1.5 全链路 smoke 已通过 2026-06-11，作为 V2.0 的底座）
```

替换为：

```markdown
# TyAgent V2.0 (Hermes) 开发拆分计划

> 起始日期：**2026-06-12** · 收尾日期：**2026-06-17** · 配套 PRD：[TyAgent V2.0 · 需求规格说明书](TyAgent%20V2.0%20%C2%B7%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md)
> **当前状态：** ✅ 全部完成（T0~T12 单测 + 集成验收 全部通过；详见 [progress.md](progress.md)）
> **配套文档**：[architecture.md V2.0 章节](architecture.md#第三部分--v20-hermes-增量) · [v2_api_reference.md](v2_api_reference.md) · [v2_frontend_guide.md](v2_frontend_guide.md)
```

- [ ] **Step 1.2: 在 §0.1 阶段表追加"完成日期"列**

定位 §0.1 那个表（约 19~32 行），把表头从：

```markdown
| 阶段 | 子需求 | 优先级 | 工程量 | 依赖 |
|---|---|---|---|---|
```

改为：

```markdown
| 阶段 | 子需求 | 优先级 | 工程量 | 依赖 | 完成日期 |
|---|---|---|---|---|---|
```

并对每行末尾追加完成日期（参照 [progress.md](progress.md) §V2.0 总表的日期）：

| 阶段 | 完成日期 |
|---|---|
| T0 | 2026-06-12 |
| T1 | 2026-06-12 |
| T2 | 2026-06-12 |
| T3 | 2026-06-12 |
| T4 | 2026-06-15 |
| T5 | 2026-06-15 |
| T6 | 2026-06-15 |
| T7 | 2026-06-15 |
| T8 | 2026-06-15 |
| T9 | 2026-06-15 |
| T10 | 2026-06-16 |
| T11 | 2026-06-16 |
| T12 | 2026-06-16 |

每行格式示例：原来 `| **T0** | 基础设施扩展... | P0 前置 | 中 | 无 |` 改为 `| **T0** | 基础设施扩展... | P0 前置 | 中 | 无 | 2026-06-12 |`

- [ ] **Step 1.3: 在文档末尾追加收尾段**

定位文件末尾（用 `wc -l docs/v2_dev_plan.md` 看总行数；约 200~400 行处的最后一节后），追加：

```markdown
---

## 收尾纪要（2026-06-17）

**功能交付**：T0~T12 全部 ✅，PRD §3 六大模块（IDP / HRE / CHC / UQA / EVA / OBS）所有子需求 100% 实现。

**单测**：357 passed（V2 全套），V1.5 + V2 全量回归 709 passed + 40 skipped，零回归。

**集成验收**：[scripts/v2_smoke.py](../scripts/v2_smoke.py) 端到端通过；[scripts/eval_compare.py](../scripts/eval_compare.py) A.1 实验跑完 4 组 RAGAS 评估。详见 [progress.md 历史变更 2026-06-17](progress.md#历史变更)。

**关键 Bug 修复**：smoke 暴露 OBS-03 快照丢失（writer 不 commit），已修。

**A.2 前置**：当前 RERANKER_TYPE=none；扩文档量到 500+ chunks 后再评估 reranker 切换（详见 [docs/eval_a1_reranker_tuning.md](eval_a1_reranker_tuning.md)）。
```

- [ ] **Step 1.4: 静态校验 + Commit**

```bash
cd d:/1aa-workspace/MeteorologicalPlatform/TyAgent
# Markdown 语法粗检：确认 grep 顶部新加的 ✅ 全部完成
grep -c "✅ 全部完成" docs/v2_dev_plan.md  # 至少 1
grep -c "完成日期" docs/v2_dev_plan.md     # 至少 1（表头）

git add docs/v2_dev_plan.md
git commit -m "docs(v2): v2_dev_plan.md 收尾状态更新（T0~T12 全部 ✅）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: 补 celery_dev_guide.md 加 eval_task 段

**Files:**
- Modify: `docs/celery_dev_guide.md`

**背景**：当前 celery_dev_guide.md 只列了 V1.5 的任务（ingest_task / session_task 标题摘要），V2.0 新增的 [app/tasks/eval_task.py](../../../app/tasks/eval_task.py)（RAGAS 评估）没提。

- [ ] **Step 2.1: 找当前任务清单段**

```bash
grep -n "ingest_task\|session_task\|任务清单\|任务列表" docs/celery_dev_guide.md
```

把这个 grep 结果给我看，我会指出在哪里追加 eval_task 段。

- [ ] **Step 2.2: 在任务清单段追加 eval_task**

模板（在已有任务列表的最后追加）：

```markdown
### 4. RAGAS 评估任务（V2.0 新增）

**任务**：`app.tasks.eval_task.run_evaluation_task`

**触发**：`POST /api/v2/knowledge-bases/{kb_id}/evaluate`（详见 [v2_api_reference.md](v2_api_reference.md)）

**输入**：`eval_task_id: str`（PG 表 `eval_tasks` 的 PK，UUID 字符串）

**输出**：写回 `eval_tasks.eval_result` JSONB 字段，包含 4 项 RAGAS 指标（faithfulness / answer_relevancy / context_precision / context_recall）+ overall_score + per-question samples

**进度锚点**：5（启动）→ 90（评估完成）→ 95（写回 PG）→ 100（已完成）

**关键约束**：
- 单题超时 `EVAL_QUESTION_TIMEOUT_S` 默认 60s（评估期 LLM 调用通常较慢）
- 单批最多 `EVAL_MAX_QUESTIONS` 默认 100 题（防止评估爆 token）
- ragas 模块按需 lazy import；环境无 ragas 时整批返 summary 全 None + error，不阻断
- NaN/Inf → None 清洗（PG JSONB 不接受 NaN）

**关键设计**：
- 不绕 HTTP 调 `/v2/query`：worker 与 uvicorn 不同进程，httpx 调用要求解析 host:port 部署复杂；改 `from app.api.v2.endpoints.query import generate_answer` 直接 import
- 评估期不写 Trace（每题 ~7 step 会污染 agent_traces）；不调 faithfulness_check（ragas 自身会跑 Faithfulness 指标）
- 多 query 评估期禁用（multi_query 烧 token 性价比差）
- LLM/Embedding 经 LangChain ChatOpenAI(base_url=) 适配（LiteLLM 完全兼容 OpenAI 协议）

**联调命令**：

```bash
# 触发评估（5 题）
curl -X POST http://127.0.0.1:8000/api/v2/knowledge-bases/<kb_id>/evaluate \
  -H 'Content-Type: application/json' \
  -d '{
    "eval_dataset": [
      {"question": "气象卫星有哪几类轨道？", "ground_truth": "极轨与静止两类。"},
      ...
    ]
  }'
# → 立即返回 {task_id, status: "pending"}

# 轮询
curl http://127.0.0.1:8000/api/v2/knowledge-bases/<kb_id>/evaluations/<task_id>
# → status pending → processing → completed
```

**worker 启动后看日志**：
```
[INFO/MainProcess] Received task: app.tasks.eval_task.run_evaluation_task[<id>]
[INFO/MainProcess] 评估进度: 1/5 ...
[INFO/MainProcess] 评估进度: 5/5 ...
[INFO/MainProcess] Task ... succeeded in 120s: {'eval_task_id': ..., 'status': 'completed'}
```
```

- [ ] **Step 2.3: Commit**

```bash
git add docs/celery_dev_guide.md
git commit -m "docs(v2): celery_dev_guide 追加 eval_task 章节（T11 RAGAS 评估）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: architecture.md 追加 V2.0 第三部分

**Files:**
- Modify: `docs/architecture.md`（在文件末尾追加第三部分）

**背景**：现 architecture.md 第二部分（V1.5）只有 §17 已写，§18~§22 是"⏳ 待填写"占位。**本 task 不补 V1.5 占位**，只在文件末尾追加第三部分（V2.0 增量）。

**章节规划**（追加到现有文件末尾）：

```
# 第三部分 · V2.0 Hermes 增量

## 23. V2.0 概览
### 23.1 迭代目标
### 23.2 与 V1.5 的差异速览
### 23.3 总体架构图（V2 检索全链路）
### 23.4 阶段交付概览（T0~T12）

## 24. T0+T1 智能文档处理（IDP）
### 24.1 V2 KB Collection Schema（15 字段）
### 24.2 结构感知解析（StructuredBlock）
### 24.3 结构感知切片（StructuredChunk）
### 24.4 11 步入库管道
### 24.5 三类 chunk 的 chunk_index 全局唯一策略
### 24.6 表格描述 / 双层索引 / 文档元数据（IDP-03/04/05）

## 25. T2+T4+T8 混合检索引擎（HRE）
### 25.1 BM25 稀疏向量（Milvus 内置 Function）
### 25.2 RRF 融合（dense + BM25）
### 25.3 Reranker 精排（在线 API + Noop 降级）
### 25.4 Query 改写（none / HyDE / multi_query）
### 25.5 Query NER + Graph 锚定
### 25.6 三层配置合并（API > KB > settings）

## 26. T5+T9 答案溯源与置信度（CHC）
### 26.1 Citation 注入与解析
### 26.2 置信度评分（CHC-03）
### 26.3 答案自检 LLM as Judge（CHC-04）
### 26.4 三态状态机：ok / skipped / disabled

## 27. T3+T12 可观测性 Trace
### 27.1 Tracer 上下文管理器
### 27.2 agent_traces 表 + 嵌套 step 查询
### 27.3 query_analytics 快照表
### 27.4 单 SQL 聚合统计
### 27.5 关键 bugfix：writer 内部 commit

## 28. T6+T10 统一查询接口（UQA）
### 28.1 /v2/query 主链路（7 步 trace）
### 28.2 检索空兜底 + LLM 失败兜底 + 整体超时
### 28.3 三个分层子接口：/retrieve、/generate、/rerank

## 29. T11 RAGAS 评估管道（EVA）
### 29.1 worker 进程内 import 而非 HTTP 调
### 29.2 LiteLLM 经 LangChain ChatOpenAI(base_url=) 适配 ragas
### 29.3 软失败设计（ragas 不可用 → summary 全 None）

## 30. V2.0 关键技术决策汇总
### 30.1 与 V1.5 不同的工程决策
### 30.2 已知限制 / 后续可改进
```

每节内容长度参考：每节 30~80 行，关键代码引用用 `file_path:line_number` 链接到 [app/...](../app/...)，关键设计决策对齐 [progress.md](../progress.md) 各 T 段已经写过的"关键设计决策"小节（**不要重新创作，从 progress.md 各 T 段提炼即可**）。

- [ ] **Step 3.1: 在文件末尾追加第三部分总章**

打开 [docs/architecture.md](../../architecture.md)，先 `wc -l` 看末尾行号，定位到最后一行，追加：

```markdown


---

# 第三部分 · V2.0 Hermes 增量

> **范围**：本部分仅记录 V2.0 在 V1.5 基础上的**增量**架构内容；V1.5 数据管理层架构（KB / 文件 / 异步任务）见第二部分。
> **写作约定**：每节 1~3 段说明"做了什么"+"为什么这么做"，关键代码用 `[文件名](../app/...)` 链接到源文件，关键设计决策从 [progress.md](progress.md) 各 T 段提炼，不重复细节实现描述。
> **配套文档**：[v2_api_reference.md](v2_api_reference.md)（接口契约）/ [v2_frontend_guide.md](v2_frontend_guide.md)（前端联调）/ [v2_dev_plan.md](v2_dev_plan.md)（阶段拆分）。
```

- [ ] **Step 3.2: 写 §23 V2.0 概览**

按上述规划追加 §23 完整内容。**数据来源**：

- 23.1 迭代目标 → 从 [docs/TyAgent V2.0 · 需求规格说明书.md](../../TyAgent%20V2.0%20%C2%B7%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md) §1.1 提炼
- 23.2 差异速览 → PRD §1.4 那张对照表
- 23.3 架构图 → PRD §2.1 + 在文末画一张 ASCII 框图（参考第一部分 §3 的画法）
- 23.4 阶段概览 → 直接引用 [progress.md V2.0 总表](../progress.md#v20-hermes--专业级-rag-引擎进行中-)

- [ ] **Step 3.3: 写 §24 IDP 章节**

数据来源：
- §24.1 V2 KB Collection Schema → [app/rag/schema.py:build_v2_kb_collection_schema](../../../app/rag/schema.py)，15 字段表已经在 progress.md T0 段有写
- §24.2 StructuredBlock → [app/ingest/parser.py](../../../app/ingest/parser.py) 的数据类定义
- §24.3 StructuredChunk → [app/ingest/structured_splitter.py](../../../app/ingest/structured_splitter.py)
- §24.4 11 步入库管道 → [app/tasks/ingest_task.py](../../../app/tasks/ingest_task.py) 的 `_main` 函数
- §24.5 chunk_index 唯一策略 → progress.md T7 关键设计决策第 1 条
- §24.6 IDP-03/04/05 → progress.md T7 段全部内容

- [ ] **Step 3.4: 写 §25 HRE 章节**

数据来源：
- §25.1 BM25 → progress.md T2 关键设计决策 1+2+3
- §25.2 RRF → progress.md T2 关键设计决策 4+5
- §25.3 Reranker → progress.md T4 + A.1 实验段（含 RERANKER_TYPE=none 决策）
- §25.4 Query 改写 → progress.md T8 关键设计决策 2（multi_query 用 RRF 二次融合）
- §25.5 Query NER + Graph 锚定 → progress.md T8 关键设计决策 4+7
- §25.6 三层配置合并 → progress.md T8 + [app/rag/retrieval_config.py](../../../app/rag/retrieval_config.py)

- [ ] **Step 3.5: 写 §26 CHC 章节**

数据来源：
- §26.1 Citation → progress.md T5 全部
- §26.2 置信度 → progress.md T9 关键设计决策 3+7
- §26.3 自检 → progress.md T9 关键设计决策 1+2+5+8
- §26.4 三态机 → progress.md T9 关键设计决策 1（"ok / skipped / disabled" 状态机解释）

- [ ] **Step 3.6: 写 §27 OBS 章节**

数据来源：
- §27.1 Tracer → [app/observability/tracer.py](../../../app/observability/tracer.py)
- §27.2 agent_traces 表 + step 查询 → progress.md T3
- §27.3 query_analytics → progress.md T12 + [app/models/query_analytics.py](../../../app/models/query_analytics.py)
- §27.4 单 SQL 聚合 → [app/api/v2/endpoints/analytics.py](../../../app/api/v2/endpoints/analytics.py) line 50~74
- §27.5 bugfix → progress.md 历史变更 2026-06-17 的 OBS-03 快照丢失 bugfix 段

- [ ] **Step 3.7: 写 §28 UQA 章节**

数据来源：
- §28.1 主链路 → progress.md T8 关键设计决策 5+6 + [app/api/v2/endpoints/query.py](../../../app/api/v2/endpoints/query.py)
- §28.2 兜底 → progress.md Bugfix · V2 query 超时卡死修复段
- §28.3 三个子接口 → progress.md T10 关键设计决策 1+2+3

- [ ] **Step 3.8: 写 §29 EVA 章节**

数据来源：progress.md T11 关键设计决策 1+2+6+8 + A.1 实验段（4 组对比结论）

- [ ] **Step 3.9: 写 §30 决策汇总**

数据来源：把 §24~§29 各节的"关键设计决策"做汇总表（决策点 + 选择 + 理由）。已知限制部分参考第一部分 §13 的格式。

- [ ] **Step 3.10: Commit**

```bash
git add docs/architecture.md
git commit -m "docs(v2): architecture.md 追加第三部分 V2.0 Hermes 增量（§23-§30）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: 创建 v2_api_reference.md

**Files:**
- Create: `docs/v2_api_reference.md`

**背景**：V1.5 的 [v1_5_api_reference.md](../../v1_5_api_reference.md) 是模板，沿用其结构（§0 通用约定 / §1~§N 各模块 / §附录），但**不重复 §0.2 响应格式 / §0.3 错误码总表**这些通用内容，链接到 V1.5 文档即可。

V2 新增的 11 个端点 + 1 个 V1 字段扩展，按"V2 路由层级"分组：

| 章节 | 路径前缀 | 端点 |
|---|---|---|
| §1 V2 统一查询 | `/api/v2/query` | 1 个 |
| §2 V2 分层子接口 | `/api/v2/{retrieve,generate,rerank}` | 3 个 |
| §3 V2 可观测性 | `/api/v2/traces/*` + `/api/v2/analytics` | 3 个 |
| §4 V2 RAGAS 评估 | `/api/v2/knowledge-bases/{kb_id}/{evaluate,evaluations,...}` | 3 个 |
| §5 V1 接口扩展 | `PATCH /api/v1/knowledge-bases/{kb_id}` 加 retrieval_config | 1 个 |

每个端点必须包含：路径 / 描述 / 请求体 schema（含字段类型与默认值）/ 响应体 schema / 错误码 / curl 示例。

- [ ] **Step 4.1: 写 §0 通用约定 + 接口总览**

```markdown
# TyAgent V2.0 · 接口文档

> **基线版本**：V2.0 Hermes（2026-06-17 全链路 smoke 验收通过）
> **配套文档**：[PRD](TyAgent%20V2.0%20%C2%B7%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md) · [架构 V2.0 章节](architecture.md#第三部分--v20-hermes-增量) · [开发计划](v2_dev_plan.md) · [进度](progress.md) · [前端联调](v2_frontend_guide.md)
> **V1.5 接口**：[v1_5_api_reference.md](v1_5_api_reference.md)（V2.0 不重写 V1.5 接口，只新增 V2 接口与 V1 接口字段扩展）
> **在线交互**：服务启动后访问 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)（Swagger UI）

---

## 0. 通用约定

### 0.1 BaseURL & 路径前缀

| 类别 | 路径前缀 | 备注 |
|---|---|---|
| V1.5 已有接口 | `/api/v1/...` | 完全不动 |
| V2.0 新增接口 | `/api/v2/...` | 与 V1.5 并存 |

### 0.2 响应格式

V2 接口沿用 V1.5 [v1_5_api_reference.md §0.2](v1_5_api_reference.md#02-统一响应格式prd-71) 的 `{code, message, data}` 包裹，不重复说明。

### 0.3 错误码

V2 新增的错误码（叠加在 V1.5 错误码表之后）：

| HTTP | 业务 code | 含义 | 触发场景 |
|---|---|---|---|
| 400 | 40011 | QUERY_REWRITE_INVALID | options.query_rewrite 不是 none/hyde/multi_query 之一 |
| 400 | 40012 | EVAL_DATASET_EMPTY | 评估时 eval_dataset 为空数组 |
| 400 | 40013 | EVAL_DATASET_TOO_LARGE | 评估题数 > EVAL_MAX_QUESTIONS（默认 100） |
| 422 | 42201 | CONTEXT_CHUNKS_EMPTY | /v2/generate 的 context_chunks 为空 |

V1.5 的 50300 CELERY_UNAVAILABLE 在 V2 评估接口同样适用。

### 0.4 接口总览

| # | 方法 | 路径 | 章节 |
|---|---|---|---|
| 1 | POST | /api/v2/query | §1.1 |
| 2 | POST | /api/v2/retrieve | §2.1 |
| 3 | POST | /api/v2/generate | §2.2 |
| 4 | POST | /api/v2/rerank | §2.3 |
| 5 | GET | /api/v2/traces/{trace_id} | §3.1 |
| 6 | GET | /api/v2/traces/sessions/{session_id}/traces | §3.2 |
| 7 | GET | /api/v2/analytics | §3.3 |
| 8 | POST | /api/v2/knowledge-bases/{kb_id}/evaluate | §4.1 |
| 9 | GET | /api/v2/knowledge-bases/{kb_id}/evaluations/{id} | §4.2 |
| 10 | GET | /api/v2/knowledge-bases/{kb_id}/evaluations | §4.3 |
| 11 | PATCH | /api/v1/knowledge-bases/{kb_id}（V1 字段扩展） | §5.1 |
```

- [ ] **Step 4.2: 写 §1.1 POST /api/v2/query**

数据来源：
- 请求体：[app/schemas/v2/query.py:QueryRequest + QueryOptions](../../../app/schemas/v2/query.py)
- 响应体：[app/schemas/v2/query.py:QueryResponse + CitationItem](../../../app/schemas/v2/query.py)
- 端点逻辑：[app/api/v2/endpoints/query.py:v2_query](../../../app/api/v2/endpoints/query.py)

格式参考 V1.5 [§2.1 流式对话](../../v1_5_api_reference.md) 的写法（路径 / 请求 / 响应 / 错误码 / 示例 curl 五段式）。包含的字段：

请求体所有字段（query / kb_ids / session_id / options{query_rewrite / enable_graph_rag / bm25_enable / reranker_enable / similarity_threshold / top_k / rrf_k / enable_faithfulness_check / metadata}）的类型 + 默认值 + 三层合并语义。

响应体所有字段（answer / source_citations[] / trace_id / total_latency_ms / rewritten_query / sub_queries / ner_entities / graph_anchored_tags / confidence / low_confidence_warning / faithfulness_check / unverified_claims）的类型与含义。

curl 示例至少 3 个：基础查询 / HyDE / multi_query + 自检。

- [ ] **Step 4.3: 写 §2.1 POST /api/v2/retrieve**

数据来源：
- Schema: [app/schemas/v2/retrieve.py:RetrieveRequest + RetrieveResponse + RetrieveChunkItem](../../../app/schemas/v2/retrieve.py)
- 端点：[app/api/v2/endpoints/retrieve.py](../../../app/api/v2/endpoints/retrieve.py)

请求体字段：query / kb_ids / top_k / enable_graph_rag / enable_bm25 / rerank / similarity_threshold。

响应体字段：chunks[] (含 vector_score / bm25_score / rrf_score / rerank_score) / total_retrieved / after_rerank / trace_id / total_latency_ms。

curl 示例 1 个：纯检索关 rerank。

- [ ] **Step 4.4: 写 §2.2 POST /api/v2/generate**

数据来源：
- Schema: [app/schemas/v2/generate.py:GenerateRequest + ContextChunk + GenerateOptions + GenerateResponse](../../../app/schemas/v2/generate.py)
- 端点：[app/api/v2/endpoints/generate.py](../../../app/api/v2/endpoints/generate.py)

请求体字段：query / context_chunks[]{chunk_id / content / source_label} / options{stream / enable_citation / enable_faithfulness_check}。**注意 context_chunks 至少 1 条，否则 42201**。

响应体字段：answer / source_citations[] / confidence / low_confidence_warning / faithfulness_check / unverified_claims / trace_id / total_latency_ms。

curl 示例 1 个：自定义 context + 启用 citation。

- [ ] **Step 4.5: 写 §2.3 POST /api/v2/rerank**

数据来源：
- Schema: [app/schemas/v2/rerank.py:RerankRequest + RerankCandidate + RerankResultItem + RerankResponse](../../../app/schemas/v2/rerank.py)
- 端点：[app/api/v2/endpoints/rerank.py](../../../app/api/v2/endpoints/rerank.py)

请求体字段：query / candidates[]{id / text} / top_n。

响应体字段：results[]{id / text / rerank_score} / total_latency_ms。**注意 results 按 rerank_score 降序排列**。

curl 示例 1 个：3 候选精排。

- [ ] **Step 4.6: 写 §3.1 GET /api/v2/traces/{trace_id}**

数据来源：
- Schema: [app/schemas/v2/trace.py:TraceDetail + TraceStepItem](../../../app/schemas/v2/trace.py)
- 端点：[app/api/v2/endpoints/traces.py:get_trace](../../../app/api/v2/endpoints/traces.py)

响应体字段：trace_id / session_id / kb_id / created_at / total_latency_ms / steps[]{step_type / parent_step / step_latency_ms / step_input / step_output / model_name / token_count / error_message}。

curl 示例 1 个 + 响应 JSON 示例（含 7 步序列）。

- [ ] **Step 4.7: 写 §3.2 GET /api/v2/traces/sessions/{session_id}/traces**

数据来源：
- Schema: [app/schemas/v2/trace.py:TraceListItem + TraceListResponse](../../../app/schemas/v2/trace.py)
- 端点：[app/api/v2/endpoints/traces.py:list_session_traces](../../../app/api/v2/endpoints/traces.py)

Query 参数：page / size。响应体字段：items[] / total / page / size。

- [ ] **Step 4.8: 写 §3.3 GET /api/v2/analytics**

数据来源：
- Schema: [app/schemas/v2/analytics.py:AnalyticsResponse + ToolUsageStats + TokenConsumptionStats](../../../app/schemas/v2/analytics.py)
- 端点：[app/api/v2/endpoints/analytics.py](../../../app/api/v2/endpoints/analytics.py)

Query 参数：start_date（可选，默认 7 天前）/ end_date（可选，默认今天）/ kb_id（可选）。

响应体字段：total_queries / avg_latency_ms / avg_confidence / low_confidence_rate / tool_usage{graph_rag_triggered / bm25_contributed / faithfulness_check_triggered} / token_consumption{total_tokens} / avg_react_steps / error_rate / start_date / end_date。

curl 示例 1 个 + smoke 实测响应（total_queries=4 ...）。

- [ ] **Step 4.9: 写 §4.1 POST /api/v2/knowledge-bases/{kb_id}/evaluate**

数据来源：
- Schema: [app/schemas/v2/eval.py:EvalCreateRequest + EvalQAItem + EvalRetrievalOptions + EvalCreateResponse](../../../app/schemas/v2/eval.py)
- 端点：[app/api/v2/endpoints/evaluations.py:create_evaluation](../../../app/api/v2/endpoints/evaluations.py)

请求体字段：name（可选）/ eval_dataset[]{question / ground_truth} / retrieval_options（可选，结构同 QueryOptions 子集）。

响应体字段：task_id / status（pending）/ created_at。

错误码：40012 数据集空 / 40013 超过 100 题 / 50300 Celery 不可达。

curl 示例 1 个（5 题数据集）。

- [ ] **Step 4.10: 写 §4.2 GET /api/v2/knowledge-bases/{kb_id}/evaluations/{id}**

数据来源：
- Schema: [app/schemas/v2/eval.py:EvalSummary + EvalDetailItem + EvalDetailResponse](../../../app/schemas/v2/eval.py)
- 端点：[app/api/v2/endpoints/evaluations.py:get_evaluation](../../../app/api/v2/endpoints/evaluations.py)

响应体字段：task_id / name / status（pending|processing|completed|failed）/ progress / question_count / summary{faithfulness / answer_relevancy / context_precision / context_recall / overall_score} / details[]（每题样本，可选）/ created_at / completed_at / error_message。

- [ ] **Step 4.11: 写 §4.3 GET /api/v2/knowledge-bases/{kb_id}/evaluations**

数据来源：
- Schema: [app/schemas/v2/eval.py:EvalListItem + EvalListResponse](../../../app/schemas/v2/eval.py)
- 端点：[app/api/v2/endpoints/evaluations.py:list_evaluations](../../../app/api/v2/endpoints/evaluations.py)

Query 参数：page / size。响应体字段：items[] / total / page / size。

- [ ] **Step 4.12: 写 §5.1 PATCH /api/v1/knowledge-bases/{kb_id}（retrieval_config 字段扩展）**

数据来源：
- Schema: [app/schemas/knowledge_base.py:KnowledgeBaseUpdateRequest](../../../app/schemas/knowledge_base.py)（看 retrieval_config 字段）
- 端点：[app/api/v1/endpoints/knowledge_bases.py:update_kb](../../../app/api/v1/endpoints/knowledge_bases.py)

说明：V1.5 接口 PATCH 现支持 retrieval_config 字段（V2.0 T8 HRE-06 加），用于 KB 级保存检索配置。retrieval_config 是 dict，支持的字段同 QueryOptions（top_k / query_rewrite / enable_graph_rag / bm25_enable / reranker_enable / similarity_threshold / rrf_k / enable_faithfulness_check）。

curl 示例：写入 retrieval_config={top_k: 5, enable_graph_rag: true, bm25_enable: true}。

- [ ] **Step 4.13: 写附录**

```markdown
## 附录

### A.1 三层配置合并

V2 检索相关参数支持三层合并优先级：**API 请求 options > KB.retrieval_config > settings (.env)**。详见 [architecture.md §25.6](architecture.md#256-三层配置合并api--kb--settings) 与 [retrieval_config.py](../app/rag/retrieval_config.py)。

### A.2 trace_id 与 session_id 的关系

每次 /v2/query 都会生成一个 trace_id（短哈希），关联到 session_id（如果请求带）。前端可在对话气泡上挂 trace_id，点击查看 trace 详情（参考 [v2_frontend_guide.md](v2_frontend_guide.md) 模块 6）。

### A.3 SSE 流式输出

V2 当前 /v2/query 仅支持非流式（同步返回完整 JSON），SSE 流式输出在 PRD §3.4 描述但 T6 阶段未实现，留待 V2.1 / 后续迭代。前端流式体验暂时复用 V1.5 [/api/v1/chat/stream](v1_5_api_reference.md#21-流式对话-post-apiv1chatstream)。

### A.4 在线交互文档

启动后 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)（Swagger UI）/ [/redoc](http://127.0.0.1:8000/redoc) 自动包含 V1 + V2 全部接口。
```

- [ ] **Step 4.14: Commit**

```bash
git add docs/v2_api_reference.md
git commit -m "docs(v2): 创建 v2_api_reference.md（11 端点 + 错误码 + curl 示例）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: 创建 v2_frontend_guide.md

**Files:**
- Create: `docs/v2_frontend_guide.md`

**背景**：V1.5 [v1_5_frontend_guide.md](../../v1_5_frontend_guide.md) 是模板，沿用其格式（§0 总览 / §1 技术栈 / §2 路由 / §3 模块详解 / §4 支撑 / §5~§8 附属）。V2.0 是后端能力增强，前端**不需要**重新搭建——本文档主要描述**新增的 V2 模块如何对接**。

V2 需要前端做的新模块（5 个）：

1. **答案溯源高亮（Citation 渲染）** — 把 `[1] [2]` 标记渲染为可点击锚点 → 弹层显示来源
2. **Trace 可视化** — trace 时序图展示 query_rewrite → ... → citation_parse 7 步耗时
3. **评估任务管理** — 创建/列表/查看 RAGAS 评估任务，含 4 项指标可视化
4. **Analytics 仪表盘** — 调 GET /v2/analytics 渲染查询量/延迟/置信度等仪表
5. **检索参数高级面板** — 暴露 query_rewrite / enable_graph_rag / bm25_enable / similarity_threshold 等开关给开发模式

V1.5 已有的会话/KB/文件/对话流模块**完全不变**。

- [ ] **Step 5.1: 写 §0~§2 总览/技术栈/路由设计**

```markdown
# TyAgent V2.0 · 前端联调指南

> **基线版本**：V2.0 Hermes（2026-06-17 全链路 smoke 验收通过）
> **配套文档**：[v2_api_reference.md](v2_api_reference.md) · [architecture.md V2 章节](architecture.md#第三部分--v20-hermes-增量) · [progress.md](progress.md)
> **V1.5 前端指南**：[v1_5_frontend_guide.md](v1_5_frontend_guide.md)（本文不重写 V1.5 模块，只描述 V2 新增模块的对接）

---

## 0. 总览

V2.0 是后端能力增强，前端不需要重新搭建。**V1.5 已有的会话 / KB / 文件 / 对话流模块完全不动**，本文档只描述 V2 新增的 5 个前端模块如何对接。

### 0.1 V2 前端工作清单

| 模块 | 优先级 | 工程量 | V2 后端依赖 |
|---|---|---|---|
| 模块 6：答案溯源高亮（Citation） | P0（前端联调必做） | 小 | T5 /v2/query 响应的 source_citations |
| 模块 7：Trace 可视化 | P1（开发期可观测） | 中 | T3 GET /v2/traces/{id} |
| 模块 8：评估任务管理 | P2（运营/算法用） | 中 | T11 评估接口 |
| 模块 9：Analytics 仪表盘 | P2（运营观测） | 中 | T12 GET /v2/analytics |
| 模块 10：检索参数高级面板 | P3（开发模式） | 小 | T8 QueryOptions 各字段 |

### 0.2 与 V1.5 的差异

- **检索路径切换**：原 V1.5 走 /api/v1/chat/stream（SSE 流），V2 走 POST /api/v2/query（同步 JSON）。流式体验在 V2 暂未实现，前端两者并存，按场景选择。
- **响应字段扩展**：V2 query 响应多了 trace_id / confidence / source_citations 等字段，前端按需渲染。
- **错误码扩展**：新增 40011-40013 / 42201 共 4 个错误码，详见 [v2_api_reference.md §0.3](v2_api_reference.md#03-错误码)。
```

- [ ] **Step 5.2: §1 推荐技术栈（沿用 V1.5）**

```markdown
## 1. 推荐技术栈

V1.5 已选定：React 18 + TypeScript + Vite + Tailwind + Zustand + axios + react-query + react-router。**V2 完全沿用**，新增依赖可选：

| 库 | 用途 | 备注 |
|---|---|---|
| recharts / echarts | Trace 时序图 + Analytics 仪表盘 | 二选一；echarts 中文社区资源更多 |
| react-syntax-highlighter | Citation 弹层代码块渲染（如有） | 可选 |
| date-fns | 评估任务时间格式化 | V1.5 已有 |
```

- [ ] **Step 5.3: §2 路由设计（V2 新增）**

```markdown
## 2. 路由设计（V2 新增）

V1.5 路由完全不动，V2 追加：

```
/dev/trace/:traceId        → Trace 详情可视化页（模块 7）
/eval                      → 评估任务列表页（模块 8）
/eval/:taskId              → 评估任务详情页（模块 8）
/analytics                 → 系统 Analytics 仪表盘（模块 9）
```

模块 6（Citation 高亮）和模块 10（高级面板）是组件级，不占独立路由，挂在原 V1.5 对话页内。
```

- [ ] **Step 5.4: §3 模块 6 答案溯源高亮**

详细描述：
- 数据来源：V2 query 响应 `answer`（字符串含 `[N]` 标记）+ `source_citations[]`（`{chunk_id, document_name, page_number, heading_path, snippet}`）
- 渲染思路：正则 `\[(\d+)\]` 匹配 → 替换为 `<a class="citation-anchor" data-idx="N">[N]</a>` → 点击触发弹层
- 弹层组件：显示 document_name / page_number / heading_path 面包屑 / snippet 200 字
- 边界处理：LLM 编造的 `[5]` 但 source_citations 只有 3 条 → 该锚点降级为普通文本
- 给一段 React TSX 示例代码（约 50~80 行）

- [ ] **Step 5.5: §3 模块 7 Trace 可视化**

详细描述：
- 数据来源：GET /v2/traces/{trace_id} 返回的 steps[]
- 渲染思路：每个 step 一个时间条，宽度 = step_latency_ms / total_latency_ms × container_width；颜色按 step_type 区分；step_input/step_output 用展开抽屉显示 JSON
- 推荐用 echarts gantt（时序图）或纯 div + flex 布局
- 给一段最小可工作的 React 示例代码框架

- [ ] **Step 5.6: §3 模块 8 评估任务管理**

详细描述：
- 列表页：调 GET /v2/knowledge-bases/{kb_id}/evaluations 列出全部任务，每行显示 task_id 短码 / status badge / progress bar / overall_score
- 详情页：调 GET /v2/knowledge-bases/{kb_id}/evaluations/{id}，渲染 4 项指标雷达图 + 每题样本展开
- 创建任务：表单上传 JSON（eval_dataset 数组）+ POST /v2/knowledge-bases/{kb_id}/evaluate
- 轮询策略：status 是 pending/processing 时每 5s 轮询，completed/failed 停止
- 给一段任务列表 React 示例代码框架

- [ ] **Step 5.7: §3 模块 9 Analytics 仪表盘**

详细描述：
- 数据来源：GET /v2/analytics?start_date=...&end_date=...&kb_id=...
- 渲染：4 个数字指标（total_queries / avg_latency_ms / avg_confidence / error_rate）+ 1 个工具使用率柱图（graph_rag / bm25 / faithfulness）+ 1 个 token 消耗指标
- 时间过滤器：日期 range 选择，默认最近 7 天
- KB 过滤器：可选下拉，选了之后看单 KB 的指标
- 给一段最小 React 示例代码框架

- [ ] **Step 5.8: §3 模块 10 检索参数高级面板**

详细描述：
- 触发位置：对话页输入框旁的"⚙️ 高级"按钮，点击展开抽屉
- 字段：query_rewrite (radio: none/hyde/multi_query) / enable_graph_rag (toggle) / bm25_enable (toggle) / reranker_enable (toggle) / similarity_threshold (slider 0~1) / top_k (number 1~50) / enable_faithfulness_check (toggle)
- 默认值：从用户选择的 KB 的 retrieval_config 拉，请求时合并到 options
- 持久化：localStorage 存最近一次的 options（开发期方便）
- 给一段简单的 React Form 示例

- [ ] **Step 5.9: §4 支撑模块**

V1.5 已有的（统一响应错误处理 + SSE 流式接收）完全沿用。V2 新增：

```markdown
### 支撑 C：Trace ID 生命周期管理

每次 /v2/query 响应带回 trace_id，前端建议挂在对话气泡的 data 属性上：

```tsx
<div className="bot-bubble" data-trace-id={resp.trace_id}>
  {renderAnswerWithCitations(resp.answer, resp.source_citations)}
  <button onClick={() => navigate(`/dev/trace/${resp.trace_id}`)}>
    查看 trace
  </button>
</div>
```

trace_id 是字符串，长度 16；保留 30 天（详见 [architecture.md §27.3](architecture.md#273-query_analytics-快照表)）。
```

- [ ] **Step 5.10: §5 推荐开发顺序**

```markdown
## 5. 推荐开发顺序（建议 1 人 5~7 天）

| 顺序 | 模块 | 工作量 |
|---|---|---|
| 1 | 模块 6 Citation 高亮 | 1 天（联调必做，对话页可视上看到 V2 价值） |
| 2 | 支撑 C trace_id 透传 | 0.5 天 |
| 3 | 模块 10 高级面板 | 0.5 天（开发自测用） |
| 4 | 模块 7 Trace 可视化 | 1.5 天 |
| 5 | 模块 9 Analytics 仪表盘 | 1.5 天 |
| 6 | 模块 8 评估任务管理 | 2 天 |

P0 + P1 跑通即满足前端对接 V2 的最小可用面，P2 模块按运营需要排期。
```

- [ ] **Step 5.11: §6~§8 关键 UX 决策 + OpenAPI SDK + 参考资源**

参考 V1.5 同节内容，加 V2 特有项：
- §6 关键 UX 决策：低 confidence 提示展示 / faithfulness_check skipped 不报错只标灰 / unverified_claims 警告样式
- §7 OpenAPI SDK：V2 端点同样 expose 在 [/openapi.json](http://127.0.0.1:8000/openapi.json)，用 `openapi-typescript` 一并生成 V1+V2 类型
- §8 参考资源：链接到 [v2_api_reference.md](v2_api_reference.md) / [architecture.md V2 章节](architecture.md)

- [ ] **Step 5.12: Commit**

```bash
git add docs/v2_frontend_guide.md
git commit -m "docs(v2): 创建 v2_frontend_guide.md（5 个新前端模块对接指南）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage**：

| 用户诉求 | 对应 task |
|---|---|
| v2_dev_plan 状态更新 | Task 1 |
| celery 补 eval_task 段 | Task 2 |
| architecture V2 章节 | Task 3 |
| v2_api_reference 创建 | Task 4 |
| v2_frontend_guide 创建（前端联调必备） | Task 5 |

5 个文档全有归属。

**2. Placeholder scan**：每个 task 都有具体的章节大纲 + 数据来源（指明从哪个源文件 / progress 段提炼），不是 "fill in details"；写文档不像写代码贴完整代码块，但每节都规定了 2~5 项必含字段或示例数。

**3. Type consistency**：
- 错误码 40011/40012/40013/42201 与 [app/api/error_codes.py](../../../app/api/error_codes.py) 一致
- V2 路由前缀 `/api/v2/...` 与 [app/api/v2/router.py](../../../app/api/v2/router.py) 一致
- Schema 字段对齐 [app/schemas/v2/](../../../app/schemas/v2/) 各文件
- frontend_guide 5 个模块对应 api_reference 端点编号一致

**4. 工作量估算（合计 ~6 小时单 session 工作量）**：
- Task 1: 30 分钟
- Task 2: 30 分钟
- Task 3: 90 分钟（写 8 节，每节 30~80 行）
- Task 4: 120 分钟（写 11 端点 + 通用约定 + 附录，~900 行）
- Task 5: 90 分钟（写 ~600 行）

单 session 干完风险大；建议用 subagent 并行执行 Task 3/4/5（独立性最强、工作量最大），Task 1/2 主 context 自做。

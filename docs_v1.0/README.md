# docs_v1.0 · 文档导航

> 本目录是 **TyAgent 1.0 版本对外文档集**（截至 2026-06-24）。
> 旧版完整文档（包含 V1.0/V1.5/V2.0 各阶段 PRD、开发拆分计划、审查报告原文）保留在 [../docs/](../docs/) 不动，需要回查时去那里。

---

## 项目状态一句话

**TyAgent / GeoAgent** —— 面向气象空间智能的 Agent 后端引擎。截至 2026-06-24，**V1.0（基础底座）+ V1.5（数据管理层）+ V2.0 Hermes（专业级 RAG 引擎）全部完成**，3 批 Hardening 收尾压完，主线项目已达生产可上线水平。剩余仅 1 项独立迭代（A.2 Reranker 重评估，阻塞依赖：扩文档库到 500+ chunks）。

---

## 文档清单

| 文档 | 用途 | 何时读 |
|---|---|---|
| [README.md](README.md) | 本文档（导航） | 第一次进项目 |
| [progress.md](progress.md) | 当前进度快照（模块状态、关键文件入口、架构契约） | **每次接手任务前必读** |
| [PRD.md](PRD.md) | 当前 PRD（V2.0 Hermes 需求规格说明书） | 需要确认需求来源 / 验收口径时 |
| [architecture.md](architecture.md) | 技术架构、数据流转、关键设计决策（含附录 A Embedding 模型对比） | 设计新模块 / 排查跨层问题时 |
| [api_reference.md](api_reference.md) | HTTP / SSE 接口参考（V2） | 前后端联调 / 接口契约确认 |
| [frontend_guide.md](frontend_guide.md) | 前端模块拆解 | 前端对接 |
| [celery_dev_guide.md](celery_dev_guide.md) | Celery 异步任务开发指南 | 写新的 Celery task 时 |
| [CHANGELOG.md](CHANGELOG.md) | V1.0→V1.5→V2.0 + 3 批 Hardening 全量时间线 | 回溯"什么时候为什么改的" |

---

## 必读阅读顺序

新接手者按此顺序读，约 30 分钟掌握项目全貌：

1. [README.md](README.md)（本文件，3 分钟）
2. [progress.md](progress.md)（10 分钟）—— 知道实现到哪了
3. [architecture.md](architecture.md) §1–§3（15 分钟）—— 知道架构怎么组的
4. [../CLAUDE.md](../CLAUDE.md)（5 分钟）—— 知道协作约定
5. [../environment_guide_zh.md](../environment_guide_zh.md)（按需）—— 环境管理规范

需要修改/新增某个具体模块时再去读对应 PRD 章节 + architecture.md 对应小节。

---

## 与旧 `docs/` 的差异

| 旧 `docs/` 中的文档 | 处理方式 |
|---|---|
| `architecture.md` | ✅ 直接复用 + 吸收 `embedding.md` 为附录 A |
| `celery_dev_guide.md` | ✅ 直接复用 |
| `embedding.md` | 已并入 `architecture.md` 附录 A |
| `TyAgent V2.0 · 需求规格说明书.md` | ✅ 重命名为 `PRD.md` |
| `v2_api_reference.md` | ✅ 重命名为 `api_reference.md` |
| `v2_frontend_guide.md` | ✅ 重命名为 `frontend_guide.md` |
| `progress.md`（1353 行） | ✅ 精简为 ~150 行快照（详细模块文件清单 / Schema 字段表 / 联调命令归 CHANGELOG 与原 docs） |
| `TyAgent V1.0 / V1.5 · 需求规格说明书.md` | ❌ 不复制（V2 PRD 已是超集；需要时回查旧 docs） |
| `v1_5_api_reference.md` / `v1_5_frontend_guide.md` | ❌ 不复制（已被 V2 版本完全覆盖） |
| `v1.5_dev_plan.md` / `v2_dev_plan.md` | ❌ 不复制（核心结论已合并进 CHANGELOG） |
| `eval_a1_reranker_tuning.md` | ❌ 不复制（一次性实验，结论在 memory + CHANGELOG） |
| `0617/code_quality_review_2026-06-17.md` | ❌ 不复制（原始审查报告，修复结论已在 CHANGELOG） |
| `0617/codex-review.md` | ❌ 不复制（同上） |
| `0617/xiugai.md` | ❌ 不复制（修复台账已合进 CHANGELOG） |

**净效果**：14 份 → 8 份；约 11000 行 → 约 5500 行；丢弃的内容都在旧 `../docs/` 里完整保留。

---

## 关键架构契约（速查）

> 详见 [architecture.md](architecture.md)；以下是必须背诵的硬约束。

1. **ReAct 熔断**：LangGraph 循环 `max_iterations = 5`，超过强制终止并返回兜底回复（AGT-03）。
2. **错误反思注入**：Tool 抛异常时捕获堆栈以 `ToolMessage` 形式回传，让模型自我修正后重试，**不要静默吞**（AGT-04）。
3. **Agentic RAG**：检索是大模型主动发起的 Tool，**不是**入站时硬塞 context。
4. **Embedding 维度固定 4096**：Milvus `knowledge_chunks.vector` 与 Qwen3-Embedding-8B 绑定，换模型需重建 Collection。
5. **`agent.runner.run_stream()`** 是 Agent ↔ Service 之间的唯一接口。
6. **`subprocess.run` 必须设超时**（建议 30s）；不上 Docker 动态沙盒。

---

## 维护约定

- 每次完成一个 PRD 子模块（或对已完成模块做实质性改动）后，更新 [progress.md](progress.md) 第一节表格 + 关键文件链接。
- 详细的批次内容、每项修复的验证证据，写入 [CHANGELOG.md](CHANGELOG.md)，不要塞进 progress.md。
- 旧 `../docs/` 是只读归档，**不再更新**——所有新文档都在本目录维护。

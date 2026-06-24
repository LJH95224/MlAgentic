# TyAgent 开发进度（快照）

> **版本基线**：V1.0 → V1.5 → V2.0 Hermes 三段全部交付完成，2026-06-23 起进入 Hardening 收尾阶段。
> **完整时间线**与每批 hardening 细节见 [CHANGELOG.md](CHANGELOG.md)；旧版逐模块详尽记录归档在 `../docs/progress.md`。
>
> **配套文档**：
> - [PRD.md](PRD.md) — 当前 PRD（V2.0 Hermes）
> - [architecture.md](architecture.md) — 技术架构、数据流、关键设计决策
> - [api_reference.md](api_reference.md) — HTTP / SSE 接口参考
> - [frontend_guide.md](frontend_guide.md) — 前端模块拆解
> - [celery_dev_guide.md](celery_dev_guide.md) — Celery 开发指南
> - [CHANGELOG.md](CHANGELOG.md) — 版本时间线 + Hardening 修复台账
> - [README.md](README.md) — 文档导航

---

## 一、模块状态总览

### V1.0 基础底座 ✅（2026-06-09 ~ 2026-06-10）

| 模块 | PRD 章节 | 关键文件 |
|---|---|---|
| 接入与通信（FastAPI + SSE 双通道） | 3.1 | [app/api/v1/endpoints/chat.py](../app/api/v1/endpoints/chat.py) / [sessions.py](../app/api/v1/endpoints/sessions.py) |
| LLM 路由（LiteLLM 统一网关） | 3.2 | [app/llm/client.py](../app/llm/client.py) / [messages.py](../app/llm/messages.py) |
| Agent 编排（LangGraph ReAct） | 3.3 | [app/agent/](../app/agent/)（runner / nodes / graph） |
| 本地执行工具（subprocess + 30s 超时） | 3.4 | [app/tools/local_exec.py](../app/tools/local_exec.py) |
| Agentic RAG（Milvus） | 3.5 | [app/rag/](../app/rag/)（milvus_client / schema / retriever / embedding） |
| 知识图谱（Neo4j） | 3.6 | [app/kg/](../app/kg/)（writer / retriever / extractor） |

### V1.5 数据管理层 ✅（2026-06-11 ~ 2026-06-12，端到端 smoke 通过）

| 阶段 | 模块 | 关键文件 |
|---|---|---|
| S0 | Celery + Redis 基础设施 + DB 迁移 | [app/tasks/celery_app.py](../app/tasks/celery_app.py) / [docker-compose/](../docker-compose/) |
| S1 | 会话管理 CRUD（SES-01~09） | [app/api/v1/endpoints/sessions.py](../app/api/v1/endpoints/sessions.py) |
| S2 | 知识库 CRUD + Milvus 多 Collection（KB-01~05） | [app/api/v1/endpoints/knowledge_bases.py](../app/api/v1/endpoints/knowledge_bases.py) |
| S3 | 文件上传 + 异步入库（FILE-01~05 / TASK-02/03） | [app/api/v1/endpoints/files.py](../app/api/v1/endpoints/files.py) / [app/tasks/ingest_task.py](../app/tasks/ingest_task.py) |
| S4 | 会话标题/摘要异步生成（SES-07/08 / TASK-04/05） | [app/tasks/session_tasks.py](../app/tasks/session_tasks.py) |
| S5 | KB 关联对话 + 端到端联调（KB-06） | [app/services/chat_service.py](../app/services/chat_service.py) |

### V2.0 Hermes — 专业级 RAG 引擎 ✅（2026-06-12 ~ 2026-06-23）

> 核心目标：把 RAG 从"能跑通"升级为"效果可信赖"——智能切片 + BM25/RRF 混合检索 + Reranker 精排 + Citation 溯源 + RAGAS 评估 + Trace 可观测。

| 阶段 | 优先级 | 模块 | 关键文件 |
|---|---|---|---|
| T0 | P0 | 基础设施扩展（Milvus V2 schema / BM25 / trace 表 / eval 表） | [app/rag/schema.py](../app/rag/schema.py) / [app/models/agent_trace.py](../app/models/agent_trace.py) / [app/models/eval_task.py](../app/models/eval_task.py) |
| T1 | P0 | IDP-01/02/06：结构感知解析 + 切片 + 入库管道重构 | [app/ingest/parser.py](../app/ingest/parser.py) / [structured_splitter.py](../app/ingest/structured_splitter.py) / [app/tasks/ingest_task.py](../app/tasks/ingest_task.py) |
| T2 | P0 | HRE-03/04：BM25 + RRF 融合 | [app/rag/hybrid_retriever.py](../app/rag/hybrid_retriever.py) |
| T3 | P0 | OBS-01/02：Trace 采集 + 查询接口 | [app/observability/](../app/observability/) / [app/api/v2/endpoints/traces.py](../app/api/v2/endpoints/traces.py) |
| T4 | P1 | HRE-05：Reranker 精排 | [app/rag/reranker.py](../app/rag/reranker.py) |
| T5 | P1 | CHC-01/02：Citation 注入 + 解析 | [app/agent/citation.py](../app/agent/citation.py) |
| T6 | P1 | UQA-01：统一查询接口 `POST /v2/query` | [app/api/v2/endpoints/query.py](../app/api/v2/endpoints/query.py) |
| T7 | P2 | IDP-03/04/05：表格描述 + 双层索引 + 文档元数据 | [app/ingest/table_describer.py](../app/ingest/table_describer.py) / [coarse_indexer.py](../app/ingest/coarse_indexer.py) |
| T8 | P2 | HRE-01/02/06：Query 改写 + NER + 三层配置 | [app/rag/query_rewriter.py](../app/rag/query_rewriter.py) / [query_ner.py](../app/rag/query_ner.py) |
| T9 | P2 | CHC-03/04：置信度评分 + 答案自检 | [app/agent/confidence.py](../app/agent/confidence.py) / [self_check.py](../app/agent/self_check.py) |
| T10 | P3 | UQA-02/03/04：分层子接口 | [app/api/v2/endpoints/query.py](../app/api/v2/endpoints/query.py) |
| T11 | P3 | EVA-01/02/03：RAGAS 评估 | [app/eval/](../app/eval/) / [app/tasks/eval_task.py](../app/tasks/eval_task.py) |
| T12 | P4 | OBS-03：聚合统计 | [app/observability/analytics_writer.py](../app/observability/analytics_writer.py) |

### Hardening 收尾 ✅（2026-06-18 ~ 2026-06-23）

| 批次 | 范围 | 完成日期 |
|---|---|---|
| Batch 1 | A 报告 P0-1~4 / P1-5~10 + B 报告 H-01~07 + M-09/10 + 本地联调 2 项 | 2026-06-18 |
| Batch 2 | Alembic / KB 删除补偿 / kb_id 兜底 / Trace 异步 / 死代码 / top_k 边界 等 10 项 | 2026-06-22 |
| Batch 3 | 静默吞异常审视 + 4 hot 模块 41 case 补测 + traces 列表 N+1 修复 | 2026-06-23 |

> 完整修复台账与每项验证证据见 [CHANGELOG.md](CHANGELOG.md)；原始审查报告归档在 `../docs/0617/`。

### 当前待办

- **A.2 Qwen3-Reranker-8B 重评估**：阻塞依赖——扩文档库到 500+ chunks。A.1 在 ~150 chunks 上得出的"Qwen3-Reranker-8B 弊大于利"是噪音，社区共识 Qwen3 > bge，需等语料规模上来再重评。

---

## 二、关键架构契约

> 详见 [architecture.md](architecture.md)；以下只列必须背诵的硬约束。

1. **ReAct 熔断**：LangGraph 循环 `max_iterations = 5`，超过强制终止并返回兜底回复（AGT-03）。
2. **错误反思注入**：Tool 抛异常时捕获堆栈以 `ToolMessage` 形式回传，让模型自我修正后重试，**不要静默吞**（AGT-04）。
3. **Agentic RAG**：检索是大模型主动发起的 Tool `search_knowledge_base(query, top_k, **kwargs)`，**不是**入站时硬塞 context。
4. **Graph + Vector 联合（KG-04）**：先调 Neo4j 锚定实体上下文，再把实体标签注入 Milvus `entity_tags` 标量过滤做精准向量检索；两次调用都要走 SSE `tool_start` 控制流。
5. **Embedding 维度固定 4096**：Milvus `knowledge_chunks.vector` 与 Qwen3-Embedding-8B 绑定，换模型需重建 Collection 与索引。
6. **`agent.runner.run_stream()`** 是 Agent ↔ Service 之间的唯一接口（V1.0 起的稳定契约）。
7. **`subprocess.run` 必须设超时**（建议 30s）；脚本执行用子进程模式，不上 Docker 动态沙盒。

---

## 三、关键技术决策（保留）

| 决策点 | 选择 | 影响 |
|---|---|---|
| 存储分工 | PostgreSQL（会话/消息/元数据）+ Milvus（向量切片，按 KB 隔离 Collection）+ Neo4j（图谱，按 kb_id 隔离子图） | V1.0 起固定 |
| BM25 方案 | Milvus 2.5+ 稀疏向量（`SPARSE_FLOAT_VECTOR` + `SPARSE_INVERTED_INDEX` + BM25 Function） | 同 Collection 稠密+稀疏，content `enable_analyzer=True` |
| 融合策略 | RRF（`RRFRanker`），k=60（学术标准值） | 可经 `RRF_K` 配置 |
| Reranker 方案 | 在线 API 经 LiteLLM 网关，首选 SiliconFlow `BAAI/bge-reranker-v2-m3` | 暂不本地化部署 |
| RAGAS 评估 | 官方 ragas 库 + LiteLLM 代理 | 已稳定可跑 |
| V1.5→V2 迁移 | 旧 KB 清空重来（用户已确认） | 上线时删 milvus volume + PG drop_all |
| Milvus 镜像 | `v2.6.18`（> 2.5），不需要再升 | — |

---

## 四、环境与运行约定

- **环境管理**：Conda（`gdal`/`geopandas`/`rasterio`/`pyproj`）+ uv（其他纯 Python），环境名 `geo_agent`，Python 3.11。详见 [../environment_guide_zh.md](../environment_guide_zh.md)。
- **uv pip 安装必须走清华镜像**：`uv pip install <pkg> -i https://pypi.tuna.tsinghua.edu.cn/simple`。
- **Windows + Docker Desktop 必须用 `127.0.0.1`**（不要用 `localhost`），否则 IPv6 vpnkit 丢包导致 Redis 卡死。
- **Claude 不自动执行**：依赖安装（`uv pip install`）+ 长进程（pytest / uvicorn / celery / 联调脚本）由用户手动执行。

### 端到端 smoke 命令（参考）

```bash
# 1. 起依赖
cd docker-compose && docker compose up -d redis postgres milvus neo4j

# 2. 清库 + 建表
psql -U postgres -c "DROP DATABASE IF EXISTS tyagent; CREATE DATABASE tyagent;"
uvicorn app.main:app --reload     # 看到 "数据库表初始化完成"

# 3. 起 worker
celery -A app.tasks.celery_app worker --pool=solo -l info

# 4. smoke
python -c "from app.tasks import ping_task; print(ping_task.delay('hello').get(timeout=5))"
```

---

## 五、维护约定

每次完成一个 PRD 子模块（或对已完成模块做实质性改动）后：

1. 把对应模块状态在本文档第一节标 ✅ + 完成日期。
2. 如新增/修改了关键文件入口，更新对应链接。
3. 如有新的架构契约或关键设计决策，写入第二/三节。
4. 详细的批次内容、每个修复项的验证证据，写入 [CHANGELOG.md](CHANGELOG.md)，不要塞进本快照文档。

本文档定位：**让任何接手者在 2 分钟内掌握当前实现到哪一步、下一步该做什么**。详尽的历史细节去 CHANGELOG 与归档目录查。

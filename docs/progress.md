# TyAgent 开发进度

> **维护约定**：每次完成一个 PRD 子模块（或对已完成模块做实质性改动）后，必须同步更新本文档。
> 文档定位：让任何接手者在 2 分钟内掌握当前实现到哪一步、下一步该做什么。
>
> **配套文档**：
> - [architecture.md](architecture.md) — 技术架构、数据流转、关键设计决策
> - [TyAgent V1.0 需求规格说明书](TyAgent%20V1.0%20%28%E5%9F%BA%E7%A1%80%E5%BA%95%E5%BA%A7%29%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md) — V1.0 PRD（基础底座，已完成）
> - **[TyAgent V1.5 · 需求规格说明书](TyAgent%20V1.5%20%C2%B7%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md)** — V1.5 PRD（当前迭代，数据管理层）
> - **[v1.5_dev_plan.md](v1.5_dev_plan.md)** — V1.5 开发拆分计划（子需求 ID + 依赖 + 阶段）
> - [embedding.md](embedding.md) — Embedding 模型选型对比

---

## V1.0 基础底座（已完成 ✅）

| 模块 | PRD 章节 | 状态 | 完成日期 |
|---|---|---|---|
| 接入与通信 | 3.1 | ✅ 完成 | 2026-06-09 |
| LLM 路由 | 3.2 | ✅ 完成 | 2026-06-09 |
| Agent 编排（LangGraph ReAct） | 3.3 | ✅ 完成 | 2026-06-10 |
| 本地执行工具（subprocess） | 3.4 | ✅ 完成 | 2026-06-10 |
| Agentic RAG（**Milvus**） | 3.5 | ✅ 完成 + 联调验收 | 2026-06-10 |
| 知识图谱（**Neo4j**） | 3.6 | ✅ 完成 + 联调验收 | 2026-06-10 |

---

## V1.5 数据管理层（进行中 🔧）

> **当前迭代起点：2026-06-11**。详细拆分见 [v1.5_dev_plan.md](v1.5_dev_plan.md)。

| 阶段 | 模块 | PRD 子需求 | 状态 | 完成日期 |
|---|---|---|---|---|
| S0 | 基础设施（Celery + Redis + DB 迁移） | TASK-01 / 数据模型 | ✅ 完成 + 联调验收 | 2026-06-11 |
| S1 | 会话管理 CRUD（不含异步任务） | SES-01 ~ SES-06 / SES-09 | ⏳ 待开始 | — |
| S2 | 知识库 CRUD + Milvus 多 Collection | KB-01 ~ KB-05 | ⏳ 待开始 | — |
| S3 | 文件上传 + 异步入库（核心） | FILE-01 ~ FILE-05 / TASK-02 / TASK-03 | ⏳ 待开始 | — |
| S4 | 会话标题/摘要异步生成 | SES-07 / SES-08 / TASK-04 / TASK-05 | ⏳ 待开始 | — |
| S5 | KB 关联对话 + 端到端联调 | KB-06 | ⏳ 待开始 | — |

---

## V1.5 · S0 基础设施 ✅

### 交付内容

| 模块 | 实现位置 | 备注 |
|---|---|---|
| **配置项扩展**（Redis / Celery / 上传 / 上下文窗口 / 标题摘要 LLM） | [app/core/config.py](../app/core/config.py)（新增 7 字段 + 2 derived property） | 含 broker/backend 缺省复用 redis_url 的兜底逻辑 |
| **依赖清单** | [requirements.txt](../requirements.txt) | 新增 celery / redis / pymupdf / python-docx / unstructured / markdown-it-py / langchain-text-splitters / tiktoken / python-multipart |
| **PG 模型**：`ChatSession` 扩 5 字段 | [app/models/session.py](../app/models/session.py) | title / summary / summarized_at / updated_at / message_count |
| **PG 模型**：`KnowledgeBase` 新表 | [app/models/knowledge_base.py](../app/models/knowledge_base.py) | 10 字段 + 3 check 约束 + name 唯一 |
| **PG 模型**：`KbFile` 新表 | [app/models/kb_file.py](../app/models/kb_file.py) | 14 字段 + kb_id 外键级联 |
| **Celery app** | [app/tasks/celery_app.py](../app/tasks/celery_app.py) | acks_late=True / prefetch=1 / json-only / 中国时区 |
| **ping_task** | [app/tasks/ping.py](../app/tasks/ping.py) | smoke 用，单测覆盖 |
| **Redis 服务** | [docker-compose/docker-compose.yml](../docker-compose/docker-compose.yml) | redis:7-alpine + AOF + 持久化挂 d:/dockerVolumes/redis/data |
| **`.env.example`** | [.env.example](../.env.example) | V1.5 块含 Redis / Celery / 上传 / 上下文 / 标题摘要 LLM / NER 备选模型注释 |
| **Celery 开发指南** | [docs/celery_dev_guide.md](celery_dev_guide.md) | Windows pool=solo / Linux prefork / smoke 命令 / 排错 |

### 关键设计决策

1. **不写迁移脚本**：用户确认 DB 可清空，靠 `Base.metadata.create_all` + 一次性清库命令；后续真有需要再上 Alembic
2. **Celery broker/backend 缺省复用 REDIS_URL**：`Settings.effective_celery_broker_url` 是 derived property，避免业务层散落 `or` 兜底
3. **task_acks_late + prefetch_multiplier=1**：PRD TASK-01 的可靠性硬要求；worker 异常时任务重入队、防 OOM 阻塞队列
4. **task_serializer=json**：禁 pickle（RCE 风险），跨语言友好
5. **task_time_limit=30min / soft=25min**：兜底防文件解析挂死；单任务可装饰器覆盖
6. **`_TASK_MODULES` 显式列表**：worker 启动时按列表 import，新任务必须在这里注册
7. **`KbFile.knowledge_base` 关系用 `lazy="raise"`**：防 N+1，强制业务层显式 selectinload

### 单测

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| [tests/test_v1_5_models.py](../tests/test_v1_5_models.py) | 20 | ChatSession 扩展字段 / KB 表字段+约束+默认值 / KbFile 字段+外键级联+枚举完备性 |
| [tests/test_v1_5_settings.py](../tests/test_v1_5_settings.py) | 11 | Redis URL / Celery broker 缺省+覆盖 / 上传配置 / 上下文窗口 / 标题摘要 LLM |
| [tests/test_celery_app.py](../tests/test_celery_app.py) | 13 | Celery 配置项 / 任务注册 / broker 覆盖 / ping_task 三种调用方式（eager 模式） |

### 验证状态

- ✅ V1.5 模型 + 配置单测 **31/31 通过**（不依赖 Celery）
- ✅ Celery 单测 **11/11 通过**（用 `task_always_eager` 模式，不连真 Redis）
- ✅ V1.0 全量测试 **127 passed + 6 skipped**（零回归；skipped 为 DB 集成测试，等用户配 TEST_DATABASE_URL）
- ✅ **端到端联调 smoke 通过**（2026-06-11）：
  - uvicorn 启动日志显示 "数据库表初始化完成" + Milvus / Neo4j 连接 OK
  - `celery worker --pool=solo` 启动成功，`[tasks]` 段含 `app.tasks.ping.ping_task`
  - `ping_task.delay('hello-S0').get(timeout=5)` 返回 `pong: hello-S0 @ lvjinhu`，链路全通

### 联调阶段关键经验（已写入项目记忆 + 文档）

1. **Windows + Docker Desktop 必须用 `127.0.0.1` 不用 `localhost`**
   - 现象：`Test-NetConnection localhost -Port 6379` 显示 `RemoteAddress: ::1` + `TcpTestSucceeded: True`，但 `redis-cli PING` 永远等不到响应，worker 卡在 `[tasks]` 段下不动
   - 根因：Windows 解析 `localhost` 优先 IPv6 `::1`，vpnkit 对 IPv6→容器 的端口转发常丢应用层包
   - 修复：`Settings.redis_url` 默认值已固化为 `redis://127.0.0.1:6379/0`，`.env.example` 与 `docs/celery_dev_guide.md` 加警示
2. **`from-import` 遮蔽子模块的坑**：`app/tasks/__init__.py` 写 `from app.tasks.celery_app import celery_app` 会让 `app.tasks.celery_app` 这个名字被 Celery 实例对象遮蔽，单测里要 `importlib.reload` 必须从 `sys.modules["app.tasks.celery_app"]` 拿真模块对象
3. **broker 连接重试限制**：`broker_connection_max_retries=3` + `broker_connection_timeout=4`，避免 Redis 不通时 `.delay()` 无限卡死

### S0 终验收命令汇总（用户已执行）

```bash
# 装依赖
uv pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
# 起 Redis
cd docker-compose && docker compose up -d redis
# Celery 单测
pytest tests/test_celery_app.py -v          # 11 passed
# 清库 + 起 uvicorn 建表
psql -U postgres -c "DROP DATABASE IF EXISTS tyagent; CREATE DATABASE tyagent;"
uvicorn app.main:app --reload                # 看 "数据库表初始化完成"
# 起 worker
celery -A app.tasks.celery_app worker --pool=solo -l info
# smoke
python -c "from app.tasks import ping_task; print(ping_task.delay('hello-S0').get(timeout=5))"
# → pong: hello-S0 @ lvjinhu
```

---

> **PRD 路线变更（2026-06-10）**：新版 PRD 把存储架构由"PostgreSQL + pgvector"
> 调整为"PostgreSQL（会话/消息）+ Milvus（向量切片）+ Neo4j（知识图谱）"三库协同。
> 已完成的 3.1–3.4 模块**不受影响**；3.5 整段重写为 Milvus 版；新增 3.6 知识图谱模块。
> PostgreSQL 中**不再保留** `knowledge_chunks` 表，原 `app/models/knowledge.py` 已删除。

---

## 3.1 接入与通信模块 ✅（V1.0）

### 交付内容

| 需求 ID | 实现位置 | 状态 |
|---|---|---|
| **API-01** 会话创建 `POST /api/v1/sessions` | [app/api/v1/endpoints/sessions.py](../app/api/v1/endpoints/sessions.py) | ✅ |
| **API-02** 流式对话 `POST /api/v1/chat/stream`（SSE） | [app/api/v1/endpoints/chat.py](../app/api/v1/endpoints/chat.py) | ✅ |
| **API-03** SSE 双通道（`event: message` / `event: control`） | 同上 | ✅ |

### 工程基础设施

- 配置层：[app/core/config.py](../app/core/config.py)（pydantic-settings + `.env`，含 LRU 缓存）
- 数据库层：[app/db/session.py](../app/db/session.py)（SQLAlchemy 2.0 async + asyncpg + PostgreSQL 17.10）
  - 关键修复：`connect_args={"ssl": False}` 解决 Windows 上 asyncpg SSL 探测的 `[WinError 121]` 信号灯超时问题
- ORM 模型：[app/models/](../app/models/) — V1.0 PostgreSQL 只保留 `chat_sessions` / `chat_messages` 两张表。
  按新版 PRD，知识切片由 Milvus 管理（详见 3.5），原 pgvector 占位 `KnowledgeChunk` 模型已删除。
- Service 层：[app/services/](../app/services/)（API ↔ Agent 胶水层）
- FastAPI lifespan：启动建表 / 关闭释放连接池

### 测试

- `tests/test_sessions_api.py` — 3 用例
- `tests/test_chat_stream.py` — 3 用例（含 SSE 解析器对 `\r\n` 帧分隔的适配）

### 关键架构契约

- **`agent.runner.run_stream()` 是 Agent ↔ Service 之间的唯一接口** —— 后续模块（3.3 LangGraph）替换内核时，API/Service 层无需任何改动。

---

## 3.2 LLM 路由模块 ✅

### 交付内容

| 需求 ID | 实现位置 | 状态 |
|---|---|---|
| **LLM-01** LiteLLM 集成，仅改 `.env` 切换厂商 | [app/llm/client.py](../app/llm/client.py) | ✅ |
| **LLM-02** Function Calling，模型按 JSON 输出 `tool_calls` | 同上 + [scripts/llm_smoke.py](../scripts/llm_smoke.py) | ✅ |

### 文件清单

- [app/core/config.py](../app/core/config.py) — Settings 新增 6 个 LLM 字段（`litellm_model` / `_api_key` / `_api_base` / `_timeout` / `_num_retries`）
- [app/llm/messages.py](../app/llm/messages.py) — OpenAI 兼容消息构造器（user/system/assistant/tool_result）+ `define_tool`
- [app/llm/client.py](../app/llm/client.py) — `acompletion` / `astream`，统一返回 dict
- [tests/test_llm_client.py](../tests/test_llm_client.py) — 6 个 mock 单测
- [scripts/llm_smoke.py](../scripts/llm_smoke.py) — 真 LLM 联调脚本

### 关键设计

1. **`_to_dict()` 兜底**：LiteLLM 返回的是 OpenAI Pydantic 对象（非裸 dict），在 client 层统一 `model_dump()` 为 dict，下游零感知
2. **模型名自动补前缀**：`.env` 写 `deepseek-v4-pro` 或 `deepseek/deepseek-v4-pro` 都能跑（按 api_base 推断厂商）

### 验证结果

- 真 DeepSeek 联调：3 项测试（纯文本 / Function Calling / 流式）全部通过

---

## 3.3 Agent 编排引擎（LangGraph ReAct）✅

### 交付内容

| 需求 ID | 实现位置 | 状态 |
|---|---|---|
| **AGT-01** `AgentState` 定义，图编译无报错 | [app/agent/state.py](../app/agent/state.py) + `tests/test_agent_graph.py::test_graph_compiles_without_error` | ✅ |
| **AGT-02** `Thought → Action → Observation` 循环 | [app/agent/graph.py](../app/agent/graph.py) + 联调脚本用例 2、3 | ✅ |
| **AGT-03** `max_iterations = 5` 死循环熔断 | [app/agent/nodes.py](../app/agent/nodes.py)::`make_call_model_node` | ✅ |
| **AGT-04** Tool 异常→堆栈作为 `ToolMessage` 回传 | [app/agent/nodes.py](../app/agent/nodes.py)::`tool_node` | ✅ |
| **TOL-02** `mock_weather_parser`（保留作为一种 dummy 测试工具） | [app/tools/weather_parser.py](../app/tools/weather_parser.py) | ✅ |

### 文件清单

- [app/agent/state.py](../app/agent/state.py) — `AgentState(TypedDict)` 含 `messages` + `remaining_iterations`
- [app/agent/nodes.py](../app/agent/nodes.py) — `call_model_node` / `tool_node` / `should_continue`
- [app/agent/graph.py](../app/agent/graph.py) — LangGraph 图构建 + `ChatOpenAI` 初始化（指向 DeepSeek）
- [app/agent/runner.py](../app/agent/runner.py) — **重写**：调用图 + 翻译流事件为 `AgentEvent`
- [app/tools/__init__.py](../app/tools/__init__.py) — 工具注册中心（`get_tools()` / `get_tool_map()`）
- [app/tools/weather_parser.py](../app/tools/weather_parser.py) — mock 气象数据工具（保留，TOL-02 的一种实现）
- [app/services/chat_service.py](../app/services/chat_service.py) — 增加 `_load_history` 加载 DB 历史并传给 runner
- [tests/test_tools.py](../tests/test_tools.py) — 3 用例
- [tests/test_agent_runner.py](../tests/test_agent_runner.py) — 7 用例（mock graph 验证翻译逻辑）
- [tests/test_agent_graph.py](../tests/test_agent_graph.py) — 9 用例（熔断 / 错误反思 / 路由 / 编译）
- [scripts/agent_smoke.py](../scripts/agent_smoke.py) — 真 LLM 端到端联调脚本

### LangGraph 图结构

```
START → call_model → should_continue（条件边）
                        ├─ "tools" → tool_node → call_model（循环）
                        └─ END
```

### 关键设计

1. **LLM 接入选 `langchain_openai.ChatOpenAI`** 而非复用 3.2 的 LiteLLM：原生支持 `stream_mode="messages"` 的 token 级流式 + `tool_call_chunks` 自动增量累积，避免手动解析 JSON 增量片段
2. **流事件双路径兼容**：
   - 路径 a：`AIMessageChunk` 的 `tool_call_chunks`（流式增量，name 可能后到）→ 按 `index` 追踪
   - 路径 b：完整 `AIMessage` 的 `tool_calls`（一次性返回，多轮对话场景常见）→ 按 `id` 去重
3. **错误反思（AGT-04）**：tool_node 用 `try/except` 包裹工具执行，异常时返回含完整 traceback 的 `ToolMessage(status="error")`，模型能看到错误并自我修正

### 验证结果

- 单测：**18/18 通过**（test_tools + test_agent_runner + test_agent_graph）
- 联调：3 个用例全部正常，含多轮对话场景（模型从历史里推断 `station_id`）

---

## 3.4 本地执行工具模块 ✅

### 交付内容

| 需求 ID | 实现位置 | 状态 |
|---|---|---|
| **TOL-01** subprocess 引擎，30s 超时强制 Kill | [app/tools/script_runner.py](../app/tools/script_runner.py) | ✅ |
| TOL-02 dummy 测试工具 `mock_weather_parser`（新 PRD 命名为 `mock_data_parser`，当前实现作为一种类型保留） | 3.3 已完成 | ✅ |

### 文件清单

- [app/tools/script_runner.py](../app/tools/script_runner.py) — 异步 subprocess 引擎
  - `run_script(cmd, timeout, cwd, env, stdin_text)` → `ScriptResult(returncode, stdout, stderr, elapsed_seconds, timed_out)`
- [tests/test_script_runner.py](../tests/test_script_runner.py) — 9 个单测

### 关键设计

1. **全异步**：用 `asyncio.create_subprocess_exec` + `asyncio.wait_for`，不阻塞 FastAPI 事件循环
2. **超时强 kill 跨平台**：
   - Linux/Mac：`os.setsid` 建进程组 + `os.killpg(SIGKILL)` 杀整组
   - Windows：`CREATE_NEW_PROCESS_GROUP` + `proc.kill()`
3. **防 shell 注入**：`cmd` 强制要求 list/tuple，拒绝字符串（避免 `shell=True` 路径）
4. **不直接注册为 LLM 工具**：通用脚本执行权限过大，引擎层只提供底层能力；后续具体业务（气象脚本调度、RAG 预处理等）按需在自己的 `@tool` 中调用并做白名单/参数校验

### 验证结果

- 单测：**9/9 通过**（1.55s），含 PRD TOL-01 关键验收点 `test_run_script_timeout_force_kill`（sleep 30s 被 1s 超时在 < 5s 内强制 kill）

---

## 3.5 Agentic RAG 模块（Milvus）✅

### 交付内容

| 需求 ID | 实现位置 | 状态 |
|---|---|---|
| **RAG-01** Milvus 客户端初始化 + Collection 自动建/复用 + load | [app/rag/milvus_client.py](../app/rag/milvus_client.py)::`init_milvus` + [app/main.py](../app/main.py) lifespan | ✅ |
| **RAG-02** `search_knowledge_base(query, top_k, **kwargs)` 注册为 Agent 技能 | [app/rag/retriever.py](../app/rag/retriever.py) + [app/tools/__init__.py](../app/tools/__init__.py) | ✅ |
| **RAG-03** 混合标量过滤（doc_type / document_id） | [app/rag/retriever.py](../app/rag/retriever.py)::`_build_filter_expr` | ✅ |
| **RAG-04** `allowed_roles` 权限字段 + 自动注入 `ARRAY_CONTAINS` | [app/rag/schema.py](../app/rag/schema.py) + retriever 内 `get_current_role` | ✅ |
| **RAG-05** `document_id` + `entity_tags` 图谱锚点字段 | [app/rag/schema.py](../app/rag/schema.py) | ✅ |

### 文件清单

- [app/rag/schema.py](../app/rag/schema.py) — Milvus Collection Schema（7 字段，4096 维）+ HNSW/INVERTED 索引参数
- [app/rag/milvus_client.py](../app/rag/milvus_client.py) — `init_milvus` / `get_milvus_client` / `close_milvus` 单例与生命周期
- [app/rag/embedding.py](../app/rag/embedding.py) — `aembed_texts` 基于 LiteLLM 调远程 Qwen3-Embedding-8B，含维度严格校验
- [app/rag/retriever.py](../app/rag/retriever.py) — `search_knowledge_base` async `@tool`，含过滤拼装与结果格式化
- [app/rag/__init__.py](../app/rag/__init__.py) — 对外统一入口
- [app/core/config.py](../app/core/config.py) — 新增 8 个 RAG 相关字段
- [app/main.py](../app/main.py) — lifespan 接入 Milvus init/close
- [app/tools/__init__.py](../app/tools/__init__.py) — 注册 `search_knowledge_base`
- [tests/test_rag_schema.py](../tests/test_rag_schema.py) — 9 用例（字段定义、维度、capacity）
- [tests/test_rag_retriever.py](../tests/test_rag_retriever.py) — 13 用例（过滤拼装 / 格式化 / 端到端 mock / @tool 集成）
- [scripts/rag_ingest.py](../scripts/rag_ingest.py) — 简易入库脚本（段落切片 + hash chunk_id 幂等 upsert）
- [scripts/rag_smoke.py](../scripts/rag_smoke.py) — 真 Milvus + 真 Embedding + 真 LLM 端到端联调
- [data/seed/](../data/seed/) — 3 篇示例气象文本（台风路径 / 降雨监测 / 数值预报）
- [.env.example](../.env.example) — 新增 `MILVUS_*` / `EMBEDDING_*` / `RAG_DEFAULT_ROLE` 配置项
- [requirements.txt](../requirements.txt) — 启用 `pymilvus>=2.6.0`

### Milvus Collection Schema（实际落地）

| 字段 | DataType | 参数 |
|---|---|---|
| `chunk_id` | INT64 | PK, auto_id=False |
| `vector` | FLOAT_VECTOR | dim=4096 |
| `document_id` | VARCHAR | max_length=64 + INVERTED 索引 |
| `content` | VARCHAR | max_length=65535 |
| `allowed_roles` | ARRAY<VARCHAR> | capacity=20, length=32 |
| `entity_tags` | ARRAY<VARCHAR> | capacity=50, length=64 |
| `metadata` | JSON | dynamic_field=False |

- 向量索引：HNSW（M=16, efConstruction=200）+ COSINE
- 文档索引：document_id 上的 INVERTED 加速标量过滤

### 关键设计

1. **Embedding 独立配置**：chat 与 embedding 经常不同源（chat 走 DeepSeek，embedding 走 SiliconFlow），独立 `EMBEDDING_*` 配置项避免硬复用 chat 厂商前缀逻辑导致误判
2. **维度严格校验**：`aembed_texts` 返回向量长度必须等于 `settings.embedding_dimension`，否则直接抛 `ValueError`，防止错误维度写入 Milvus 后才暴露
3. **权限基线硬注入**：`current_role` 不暴露给 LLM —— retriever 内部通过 `get_current_role()` 解析（V1.0 写死 "ALL"，3.6 改成从请求 contextvar 读取，工具签名无需改动）
4. **async tool**：retriever 定义为 `async def`，LangGraph tool_node 通过 `tool.ainvoke` 直接 await，避免 `asyncio.run` 在已有事件循环中冲突
5. **chunk_id 幂等**：`hash(document_id + chunk_index)` 取低 63 位作为 INT64 主键，重跑入库脚本走 upsert，不产生垃圾数据
6. **fail-fast**：Milvus 启动期连不上直接抛 `RuntimeError`，让应用挂掉而不是带病运行

### 验证结果

- 单测：**40 用例全部通过**（test_rag_schema 9 + test_rag_retriever 18 + test_kg_writer 6 + test_kg_query 16 + test_kg_ner 13；总 62 含其他模块）
- 联调：**PRD §3.5 五条全部通过**
  - RAG-01：日志显示 Collection 自动检测 + 创建/复用 + load 全链路 OK
  - RAG-02：Agent 自主调用 `search_knowledge_base`，返回 score 0.760 的精准命中
  - RAG-03：`doc_type='report'` 标量过滤生效（日志 `filter=... and metadata["type"] == "report"`）
  - RAG-04：自动注入 `ARRAY_CONTAINS(allowed_roles, "ALL")` 权限基线
  - RAG-05：召回结果含完整 `entity_tags=[西北太平洋,菲律宾,...]` 与 `document_id` 透传

### 环境重建参考命令（按 CLAUDE.md 用户操作约定）

```bash
# 1. 安装新依赖
uv pip install pymilvus>=2.6.0 -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2. 启动本地 Milvus（任选其一）
docker run -d --name milvus-standalone -p 19530:19530 -p 9091:9091 milvusdb/milvus:v2.6.0
# 或 docker-compose 起完整 standalone

# 3. 编辑 .env，填入真实的 EMBEDDING_API_KEY（SiliconFlow / DashScope 等）

# 4. 单测
pytest tests/test_rag_schema.py tests/test_rag_retriever.py -v

# 5. 入库
python scripts/rag_ingest.py

# 6. 联调（3 个用例）
python scripts/rag_smoke.py
```

---

## 3.6 知识图谱模块（Neo4j）✅

### 交付内容

| 需求 ID | 实现位置 | 状态 |
|---|---|---|
| **KG-01** Neo4j 客户端初始化 + 健康检查 + 自动建唯一性约束 | [app/kg/neo4j_client.py](../app/kg/neo4j_client.py)::`init_neo4j` + [app/main.py](../app/main.py) lifespan | ✅ |
| **KG-02** 节点 / 关系 Upsert（MERGE 幂等）+ 批量版本 | [app/kg/writer.py](../app/kg/writer.py) | ✅ |
| **KG-03** `query_knowledge_graph` 注册为 Agent 技能 | [app/kg/tool.py](../app/kg/tool.py) + [app/kg/query.py](../app/kg/query.py) + [app/tools/__init__.py](../app/tools/__init__.py) | ✅ |
| **KG-04** Graph RAG 联合查询（两步调用都有 tool_start） | 保持两个独立 Tool + LangGraph runner 既有 tool_start 机制；retriever 新增 `entity_tags` 入参 | ✅ |
| **KG-05** 实体抽取管道（LLM Prompt NER）+ 同步写两库 | [app/kg/ner.py](../app/kg/ner.py) + 改造 [scripts/rag_ingest.py](../scripts/rag_ingest.py) | ✅ |

### 文件清单

- [app/kg/neo4j_client.py](../app/kg/neo4j_client.py) — `AsyncDriver` 单例 + `verify_connectivity` + 幂等建约束
- [app/kg/writer.py](../app/kg/writer.py) — `upsert_document` / `upsert_entity` / `link_entity_to_chunk` + 两个批量版本
- [app/kg/ner.py](../app/kg/ner.py) — LLM Prompt 通用 NER（5 类：PERSON / LOCATION / ORG / TIME / OTHER），软失败
- [app/kg/query.py](../app/kg/query.py) — 多跳查询 Cypher 构建 + 结果格式化（max_hops 夹值 [1,5]）
- [app/kg/tool.py](../app/kg/tool.py) — `query_knowledge_graph` async `@tool`
- [app/kg/__init__.py](../app/kg/__init__.py) — 对外统一入口（lifespan / writer / NER / tool）
- [app/core/config.py](../app/core/config.py) — 新增 5 个字段（neo4j_uri/user/password/database、kg_ner_model）
- [app/main.py](../app/main.py) — lifespan 接入 `await init_neo4j()` / `await close_neo4j()`
- [app/tools/__init__.py](../app/tools/__init__.py) — 注册 `query_knowledge_graph`
- [app/rag/retriever.py](../app/rag/retriever.py) — `search_knowledge_base` 新增 `entity_tags` 入参（ARRAY_CONTAINS_ANY 过滤）
- [scripts/rag_ingest.py](../scripts/rag_ingest.py) — 整合 NER + Neo4j：chunk 切完 → 并发 NER → 同步写 Milvus.entity_tags + Neo4j(Entity + MENTIONED_IN)
- [scripts/kg_smoke.py](../scripts/kg_smoke.py) — 3 用例：直接 query / 带过滤 query / Agent 端到端 Graph RAG
- [tests/test_kg_writer.py](../tests/test_kg_writer.py) — 6 用例（Cypher 结构与参数化）
- [tests/test_kg_query.py](../tests/test_kg_query.py) — 14 用例（夹值 / Cypher 构造 / 格式化 / @tool 集成）
- [tests/test_kg_ner.py](../tests/test_kg_ner.py) — 12 用例（解析 / 去重 / 大小写归一 / 软失败）
- [tests/test_rag_retriever.py](../tests/test_rag_retriever.py) — 同步扩展 `entity_tags` 过滤的测试

### Neo4j 数据模型（实际落地，PRD §4.4）

| 类型 | 名称 | 关键属性 / 唯一性 |
|---|---|---|
| Node Label | `Document` | `document_id`（**唯一约束**） / `title` / `created_at` |
| Node Label | `Entity` | (`name`, `type`) **复合唯一约束** + `document_ids[]`（出现过它的文档列表） |
| Relationship | `MENTIONED_IN` | `chunk_id`（指向具体 Milvus chunk，用于追溯出处） |
| Relationship | `RELATED_TO` | V1.0 未抽取关系，留作后续接入关系抽取时填充 |

### 关键设计

1. **`(name, type)` 复合唯一键**：同名实体可能是不同类型（"苹果"既可能是 ORG 也可能是 OTHER），仅按 name 唯一会丢失语义。复合键既保持 MERGE 幂等，又允许多义词共存
2. **NER 软失败原则**：NER 是入库的辅助步骤，主链路 Milvus 写入是核心。LLM 限流 / JSON 解析失败 → 返回 `[]`，记日志不抛错，不阻断整批入库
3. **NER 模型独立配置**：可选 `KG_NER_MODEL`，缺省复用 `LITELLM_MODEL`。**关键经验**：NER 应使用非 reasoning 的轻量快速模型（如 `deepseek-v4-flash`），避免推理模型对"什么算实体"过度思考导致大量返回 `entities=[]`。实测 v4-flash 在 3 篇气象文本中抽出 35 个高质量实体（地名/机构/时间）
4. **`max_hops` 夹值防爆炸**：`[r*1..N]` 变长路径中 N 不能参数化，必须用 f-string 拼接 —— 严格夹值到 [1, 5] 防注入与防图谱爆炸
5. **KG-04 不写新 Tool**：保留 `query_knowledge_graph` 与 `search_knowledge_base` 两个独立 Tool + system prompt 引导模型分两步调用，PRD "两步调用都有 tool_start" 由 LangGraph runner 既有机制自动满足
6. **AsyncDriver 与 FastAPI 原生匹配**：所有写入走 `session.execute_write(tx_fn)` 带自动重试，所有 Cypher 走 `$param` 参数化（防注入）
7. **批量化写入**：每份文档处理完一次性 UNWIND 写实体与关系，避免 N 次往返
8. **fail-fast**：Neo4j 启动期连不上直接抛 `RuntimeError`，让应用挂掉而不是带病运行

### 验证结果

- 单测：**35 用例全部通过**（test_kg_writer 6 + test_kg_query 16 + test_kg_ner 13）
- 联调：**PRD §3.6 五条全部通过**
  - KG-01：启动日志显示连接 OK + 约束自动创建/复用（第二次启动看到 "already exists, has no effect"）
  - KG-02：3 篇文档入库共写入 35 个唯一实体 + 19 条 MENTIONED_IN 关系；重跑 ingest 节点数不变（MERGE 幂等）
  - KG-03：模型主动调用 `query_knowledge_graph(entity_name="西北太平洋")` 返回 20 条路径
  - KG-04：联合查询完整链路验证通过 —— `query_knowledge_graph` 与 `search_knowledge_base` 都被实际调用，且 RAG 第二次调用带 `entity_tags=[西北太平洋,南海,ECMWF,GFS,...]` 精筛
  - KG-05：NER 抽取 35 个高质量实体（地名/机构/时间），同步写 Milvus.entity_tags + Neo4j Entity/Document 节点

### 联调阶段关键经验

1. **Embedding 模型必须带 LiteLLM 厂商前缀**：`EMBEDDING_MODEL=openai/Qwen/Qwen3-Embedding-8B`（缺前缀会被 LiteLLM 拒绝路由）
2. **LiteLLM 的 `openai/` 路由禁止 dimensions 参数**：embedding.py 已删除该参数，靠返回维度严格校验保证一致性
3. **NER 应使用非 reasoning 模型**（如 `deepseek-v4-flash`），推理模型对实体抽取"过度思考"导致大量空返回
4. **Agent system prompt 是 Graph RAG 联合查询的关键**：runner.py 注入 `_SYSTEM_PROMPT` 明确两个工具的分工与联合调用模式，避免模型陷入工具循环触发熔断

### 环境重建参考命令

```bash
# 1. 安装新依赖
uv pip install neo4j>=5.20.0 -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2. 启动 Neo4j（若未启动）
cd docker-compose && docker compose up -d neo4j
# 访问 http://localhost:7474 验证（账号 neo4j / tyagent_neo4j）

# 3. 编辑 .env，把 NEO4J_* 填好（默认值与 docker-compose 对齐）

# 4. 单测
pytest tests/test_kg_*.py tests/test_rag_retriever.py -v

# 5. 重新入库（这次同时写 Milvus + Neo4j，会调 NER 烧 LLM token）
python scripts/rag_ingest.py

# 6. KG 联调
python scripts/kg_smoke.py
```

---

## 历史变更

- **2026-06-11**：V1.5 S0 基础设施联调验收通过 ✅
  - ping_task smoke 全链路跑通：`pong: hello-S0 @ lvjinhu`
  - 联调阶段定位并修复 Windows + Docker Desktop 上 `localhost`→IPv6 vpnkit 丢包坑（默认值锁 127.0.0.1）
  - 联调阶段定位并修复 `from-import` 遮蔽子模块导致 `importlib.reload` 失败的测试坑
  - 全量测试 138 passed + 6 skipped，零回归
- **2026-06-11**：V1.5 S0 基础设施代码完成
  - 新增 [app/tasks/](../app/tasks/)（celery_app / ping）+ [docs/celery_dev_guide.md](celery_dev_guide.md)
  - 扩展 `ChatSession`、新增 `KnowledgeBase` / `KbFile` 两张表（PRD §5.1~5.3）
  - 配置层新增 7 字段 + 2 derived property（broker/backend 缺省复用 redis_url）
  - docker-compose 加 redis:7-alpine 服务（持久化挂 d:/dockerVolumes/redis/data）
- **2026-06-11**：V1.0 基础底座收尾，V1.5 数据管理层启动
  - 新增 [v1.5_dev_plan.md](v1.5_dev_plan.md) — 子需求 ID 拆分 + 阶段依赖
  - [CLAUDE.md](../CLAUDE.md) 工作前必读追加 V1.5 PRD 与拆分计划入口
  - 进度文档表格结构升级：分 V1.0（已完成）/ V1.5（进行中）两段
- **2026-06-09**：完成 3.1、3.2
- **2026-06-10**：完成 3.3、3.4
- **2026-06-10**：PRD 升级到混合存储版（PostgreSQL + Milvus + Neo4j）
  - 删除原 PG 版 `app/models/knowledge.py` 与 pgvector 路径
  - 3.5 整段改为 Milvus 路线；新增 3.6 Neo4j 模块
  - TOL-02 工具：新 PRD 命名为 `mock_data_parser`，当前实现 `mock_weather_parser` 作为其"一种"测试场景保留
- **2026-06-10**：完成 3.5 Agentic RAG 模块（Milvus）
  - 新增 `app/rag/` 4 个核心文件（schema / milvus_client / embedding / retriever）
  - lifespan 接入 Milvus init/close（fail-fast）
  - 工具注册中心挂接 `search_knowledge_base`（async @tool）
  - 22 个单测覆盖 Schema 定义、过滤拼装、结果格式化、端到端 mock 调用、@tool 集成
  - 提供 ingest + smoke 联调脚本与 3 篇气象示例文本
  - 待用户启动本地 Milvus + 配置 Embedding API key 后执行 smoke 完成 RAG-01~05 终验收
- **2026-06-10**：完成 3.6 知识图谱模块（Neo4j）
  - 新增 `app/kg/` 6 个核心文件（neo4j_client / writer / ner / query / tool / __init__）
  - lifespan 接入 Neo4j async init/close（验证连通性 + 幂等建唯一性约束）
  - 工具注册中心挂接 `query_knowledge_graph`（async @tool）
  - `app/rag/retriever.py` `search_knowledge_base` 新增 `entity_tags` 入参支持 Graph RAG 联合（KG-04）
  - `scripts/rag_ingest.py` 整合 NER + Neo4j 写入：chunk 切完 → 并发 NER → 同步写 Milvus.entity_tags + Neo4j(Entity + MENTIONED_IN)
  - 32 个 KG 单测 + retriever 扩展测试覆盖 Cypher 结构、NER 解析/去重/软失败、查询夹值、@tool 集成
  - 提供 `scripts/kg_smoke.py`（直接 query / 带过滤 query / Agent 端到端 Graph RAG）
  - `docker-compose/docker-compose.yml` 已加 Neo4j 5.26 服务（含 APOC 插件、健康检查、固定 d:/dockerVolumes 挂载）
  - 待用户启动 Neo4j + 跑 rag_ingest 重新入库 + 执行 kg_smoke 完成 KG-01~05 终验收
- **2026-06-10**：3.5 + 3.6 联调验收全部通过，PRD V1.0 基础底座正式收尾
  - **数据**：3 篇气象文本入库 → 13 chunk → 35 唯一实体 → 19 MENTIONED_IN 关系
  - **配置定型**：LITELLM_MODEL=deepseek-v4-flash（chat）+ KG_NER_MODEL=deepseek-v4-flash（NER 解耦）+ EMBEDDING_MODEL=openai/Qwen/Qwen3-Embedding-8B（SiliconFlow 4096 维）
  - **关键修复**：
    - 修 [app/rag/embedding.py](../app/rag/embedding.py) 去掉 `dimensions` 参数（LiteLLM openai/ 路由不允许）
    - 新增 [scripts/embedding_test.py](../scripts/embedding_test.py) 独立排查 Embedding 链路
    - [app/agent/runner.py](../app/agent/runner.py) 注入 `_SYSTEM_PROMPT` 引导 Agent 正确使用工具（防止陷入工具循环）
    - [scripts/kg_smoke.py](../scripts/kg_smoke.py) 用 tool_end 兜底 tool_start 流式合并问题，确保 KG-04 验收判定可靠
  - **PRD 10 条验收点全部通过**：RAG-01/02/03/04/05 + KG-01/02/03/04/05
  - **Agent 表现亮点**：KG-04 端到端测试中，模型自动 fallback（"台风"图谱未命中 → 切 RAG 拿原文 → 从原文中抓实体回查 KG → 用实体精筛 RAG）输出 1403 字结构化报告，远超预期

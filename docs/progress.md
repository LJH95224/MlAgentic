# TyAgent V1.0 开发进度

> **维护约定**：每次完成一个 PRD 子模块（或对已完成模块做实质性改动）后，必须同步更新本文档。
> 文档定位：让任何接手者在 2 分钟内掌握当前实现到哪一步、下一步该做什么。

---

## 总览

| 模块 | PRD 章节 | 状态 | 完成日期 |
|---|---|---|---|
| 接入与通信 | 3.1 | ✅ 完成 | 2026-06-09 |
| LLM 路由 | 3.2 | ✅ 完成 | 2026-06-09 |
| Agent 编排（LangGraph ReAct） | 3.3 | ✅ 完成 | 2026-06-10 |
| 本地执行工具（subprocess） | 3.4 | ⏳ 待做 | — |
| Agentic RAG（pgvector） | 3.5 | ⏳ 待做 | — |

---

## 3.1 接入与通信模块 ✅

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
- ORM 模型：[app/models/](../app/models/) — `chat_sessions` / `chat_messages` 已建表；`knowledge_chunks` 模型已声明但 `embedding` 列暂为 Text 占位（待 3.5 启用 pgvector）
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
| **TOL-02** `mock_weather_parser` 注册 | [app/tools/weather_parser.py](../app/tools/weather_parser.py) | ✅ |

### 文件清单

- [app/agent/state.py](../app/agent/state.py) — `AgentState(TypedDict)` 含 `messages` + `remaining_iterations`
- [app/agent/nodes.py](../app/agent/nodes.py) — `call_model_node` / `tool_node` / `should_continue`
- [app/agent/graph.py](../app/agent/graph.py) — LangGraph 图构建 + `ChatOpenAI` 初始化（指向 DeepSeek）
- [app/agent/runner.py](../app/agent/runner.py) — **重写**：调用图 + 翻译流事件为 `AgentEvent`
- [app/tools/__init__.py](../app/tools/__init__.py) — 工具注册中心（`get_tools()` / `get_tool_map()`）
- [app/tools/weather_parser.py](../app/tools/weather_parser.py) — mock 气象数据工具
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

## 待办

### 3.4 本地执行工具模块（下一步）

| 需求 ID | 要求 | 实现路径 |
|---|---|---|
| TOL-01 | 基于 `subprocess.run` 的通用脚本执行引擎，30s 超时强制 Kill | 新增 `app/tools/script_runner.py` |
| TOL-02 | mock_weather_parser | ✅ 3.3 已完成 |

### 3.5 Agentic RAG 模块

| 需求 ID | 要求 | 实现路径 |
|---|---|---|
| RAG-01 | PostgreSQL pgvector 扩展启用 + 切换 `knowledge_chunks.embedding` 为 `Vector(N)` | 引入 alembic 迁移；激活 [app/models/knowledge.py](../app/models/knowledge.py)::`get_vector_type()` |
| RAG-02 | `search_knowledge_base(query, top_k)` 工具注册 | 新增 `app/rag/retriever.py` + 在工具注册中心挂接 |
| RAG-03 | metadata JSONB 硬过滤支持 | 在检索 SQL 中支持 `WHERE metadata->>'type' = ?` |

---

## 历史变更

- **2026-06-09**：完成 3.1、3.2
- **2026-06-10**：完成 3.3

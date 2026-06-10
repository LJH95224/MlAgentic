# TyAgent V1.0 基础底座 · 需求规格说明书 (PRD)

**文档版本：** V1.0 Draft
**项目定位：** 具备自主推理能力与主动知识检索能力的智能体基础后端引擎
**核心架构：** FastAPI · LangGraph · LiteLLM · PostgreSQL · Milvus · Neo4j

---

## 1. 产品概述

### 1.1 背景与目标

在引入复杂的业务脚本调度和外部系统联动之前，系统需要一个极其稳定、具备深度推理能力的大脑。V1.0 阶段的核心目标是**搭建纯净的底层控制流**，即打通基于 LangGraph 的 ReAct（推理与执行）循环，并实现模型自主调用工具查询 **Milvus 分布式向量库**（Agentic RAG）与 **Neo4j 知识图谱**的闭环，为海量文档的高维特征检索与结构化知识推理提供支撑。

### 1.2 边界与范围（V1.0 不做的事）

- 暂不开发外部 MCP（Model Context Protocol）接口。
- 暂不涉及复杂的前端 WebGIS 指令联动渲染。

---

## 2. 核心业务流程（User Story）

1. **发起请求：** 用户在网页端输入问题。
2. **意图规划（Thought）：** 大模型接收问题，判断自身知识不足以回答，决定调用检索工具。
3. **主动检索（Action）：** 大模型生成查询关键词，触发后端的 RAG Tool 或 Graph Tool。
4. **知识观测（Observation）：** 后端查询向量数据库或知识图谱，执行权限与元数据过滤，将召回内容返回给大模型。
5. **反思与重试（Self-Correction）：** 大模型阅读后，若发现缺失关键数据，自动修改关键词再次查询；或若执行脚本异常，根据错误堆栈修改参数重试。
6. **流式输出（Final Answer）：** 获取充分信息后，整合生成最终结论，并通过 SSE 实时推流至前端。

---

## 3. 详细功能需求拆分

### 3.1 接入与通信模块（Gateway & Communication）

| 需求 ID | 功能名称 | 详细描述 | 验收标准 |
| --- | --- | --- | --- |
| API-01 | 会话创建接口 | `POST /api/v1/sessions`：初始化新的对话上下文，返回唯一的 `session_id`。 | 成功在数据库生成 Session 记录并返回 UUID。 |
| API-02 | 流式对话接口 | `POST /api/v1/chat/stream`：接收用户输入及 `session_id`，采用 SSE 协议返回数据流。 | 前端能平滑接收打字机效果的文本，不出现阻塞。 |
| API-03 | 状态推送机制 | 在 SSE 流中区分"文本流"与"控制流"。当大模型开始调用工具时，推送 `{"type": "tool_start", "tool": "search"}`。 | 前端可根据标识展示"正在检索知识库..."等加载状态。 |

### 3.2 大模型路由模块（Model Routing）

| 需求 ID | 功能名称 | 详细描述 | 验收标准 |
| --- | --- | --- | --- |
| LLM-01 | LiteLLM 集成 | 引入 LiteLLM 代理，通过统一的 OpenAI 规范接口调用 DeepSeek、Qwen 或 GLM 等模型。 | 仅需修改 `.env` 配置文件即可无缝切换不同厂商大模型。 |
| LLM-02 | Function Calling 适配 | 确保选用的基础大模型能够精准识别并按 JSON 格式输出 `tool_calls`。 | 模型能连续三次正确输出符合参数预设的 Tool Call JSON。 |

### 3.3 Agent 编排引擎模块（LangGraph ReAct）

| 需求 ID | 功能名称 | 详细描述 | 验收标准 |
| --- | --- | --- | --- |
| AGT-01 | 状态机定义 | 定义 `AgentState`，至少包含 `messages`（对话列表）字段。 | LangGraph 图编译无报错。 |
| AGT-02 | ReAct 循环控制 | 实现 `Thought -> Action -> Observation` 的经典循环路径。 | 模型能在生成最终答案前，多次循环进入 Action 节点。 |
| AGT-03 | 死循环熔断 | 设置最大流转次数（如 `max_iterations = 5`），防止模型陷入无意义的循环调用消耗 Token。 | 循环达到 5 次时强制中止并输出兜底回复。 |
| AGT-04 | 错误反思注入 | 当 Tool 抛出异常时，捕获异常堆栈并作为 `ToolMessage` 返回给模型，提示其纠正。 | 模型接收报错后能输出"参数错误，我需要修正..."并重试。 |

### 3.4 本地执行工具模块（Local Tools）

| 需求 ID | 功能名称 | 详细描述 | 验收标准 |
| --- | --- | --- | --- |
| TOL-01 | 脚本子进程调度 | 基于 `subprocess.run` 封装通用脚本执行引擎，设置严格的执行超时（如 30s）。 | 超时脚本被强制 Kill 并返回超时提示。 |
| TOL-02 | Dummy 测试工具 | 注册 `mock_data_parser`，模拟读取结构化数据（仅返回固定 JSON 测试用）。 | 模型能正确理解其用途并成功传入必填参数调用。 |

### 3.5 主动知识检索模块（Agentic RAG）

针对高维稠密嵌入与系统未来的权限演进进行专项设计。Milvus 服务本身通过 `.env` 配置文件接入，V1.0 仅聚焦代码层面的客户端集成与功能实现。

| 需求 ID | 功能名称 | 详细描述 | 验收标准 |
| --- | --- | --- | --- |
| RAG-01 | Milvus 客户端初始化 | 从 `.env` 读取 Milvus 连接配置（`MILVUS_HOST`、`MILVUS_PORT`、`MILVUS_TOKEN` 等），应用启动时通过 PyMilvus 建立连接；若 Collection 不存在则自动执行初始化（Schema 定义 + HNSW 索引创建），若已存在则直接复用。 | 应用冷启动后 PyMilvus 连接成功，Collection `knowledge_chunks` 可用；首次启动自动完成 Schema 和索引初始化，重启时跳过重建。 |
| RAG-02 | 知识检索 Tool 封装 | 封装 `search_knowledge_base(query, top_k, **kwargs)` 并注册为 Agent 技能，支持大模型依据当前上下文动态生成多角度的标量查询子句。 | 模型在处理 Session 中不包含的专业背景时，能主动输出符合参数规范的 Tool 调用。 |
| RAG-03 | 混合标量过滤 | 利用 Milvus 的标量过滤能力，支持向量空间搜索时动态叠加基于元数据（如文档类型、所属域）的布尔表达式。 | 秒级响应包含 `metadata["type"] == "report"` 等复杂表达式的检索请求。 |
| RAG-04 | 数据级权限预留 | 在 Milvus Schema 中强制包含 `allowed_roles`（Array 类型）字段，暂存全局通用标识 `["ALL"]`，为后续行级 RAG 权限隔离打下基础。 | 执行检索前，Tool 能自动从上下文解析当前角色，并构造包含 `JSON_CONTAINS(allowed_roles, current_role)` 的过滤表达式。 |
| RAG-05 | 知识图谱锚点预留 | 数据切片时在 Schema 中预留 `document_id` 和 `entity_tags`（字符数组，存放从文本中提取的实体标签）。 | 召回的 Chunk 数据结构中天然包含实体关联指针，可无缝透传给后续 Graph RAG 流程。 |

### 3.6 知识图谱模块（Knowledge Graph）

基于 Neo4j 构建结构化知识网络，与 Milvus 向量检索形成互补：RAG 解决语义相似度召回，图谱解决实体关系推理与多跳查询，两者通过 `entity_tags` 和 `document_id` 字段串联。Neo4j 服务通过 `.env` 配置接入，V1.0 聚焦代码层面的集成与核心查询能力实现。

| 需求 ID | 功能名称 | 详细描述 | 验收标准 |
| --- | --- | --- | --- |
| KG-01 | Neo4j 客户端初始化 | 从 `.env` 读取 Neo4j 连接配置（`NEO4J_URI`、`NEO4J_USER`、`NEO4J_PASSWORD`），应用启动时通过官方 Python Driver 建立连接并执行健康检查；若基础约束（Constraint）不存在则自动创建，已存在则复用。 | 应用冷启动后驱动连接成功，基础 Node Label 的唯一性约束就绪，健康检查接口返回正常。 |
| KG-02 | 实体与关系写入 | 封装通用的节点（Node）与关系（Relationship）写入接口，支持 Upsert 语义（`MERGE` 语句），避免重复节点。写入时自动关联 `document_id`，建立图谱节点与向量切片之间的双向索引。 | 同一实体重复写入不产生重复节点；节点上的 `document_id` 属性与 Milvus 中对应 Chunk 的 `document_id` 一致可查。 |
| KG-03 | 图谱查询 Tool 封装 | 封装 `query_knowledge_graph(entity, relation_types, max_hops)` 并注册为 Agent 技能，支持以实体为起点进行多跳关系遍历，返回结构化的路径与节点属性列表。 | 模型在需要推理实体间关联时，能主动调用该 Tool，并正确解析返回的路径结果用于生成答案。 |
| KG-04 | RAG 联合增强查询 | 在 Agent 层实现 Graph RAG 联合调用策略：先通过图谱查询锚定实体上下文，再将图谱返回的实体标签注入为 Milvus 的标量过滤条件，执行精准向量检索。 | 联合查询的召回结果相比单纯向量检索，在实体相关性上有明显提升；两步调用在 SSE 流中均有对应的 `tool_start` 状态推送。 |
| KG-05 | 实体抽取管道预留 | 在文档入库流程中预留 NER（命名实体识别）调用接口，暂以大模型 Prompt 方式实现实体抽取，后续可替换为专用 NER 模型。抽取结果同步写入 Neo4j 节点及 Milvus `entity_tags` 字段。 | 给定一段文本，管道能返回结构化的实体列表（含实体名、类型），并完成图谱节点与向量切片的同步写入。 |

---

## 4. 数据库字典设计（混合存储架构）

V1.0 阶段采用三库协同：PostgreSQL 管理会话与上下文状态；Milvus 管理高维向量切片；Neo4j 管理实体关系网络。三者均通过 `.env` 统一配置接入。

### 4.1 [PostgreSQL] 表名：`chat_sessions`（会话表）

| 字段名 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | UUID | Primary Key | 会话唯一标识 |
| `created_at` | Timestamp | Not Null, Default NOW() | 创建时间 |
| `metadata` | JSONB | Nullable | 预留：存储当前 Session 的偏好或上下文范围 |

### 4.2 [PostgreSQL] 表名：`chat_messages`（消息上下文表）

| 字段名 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | UUID | Primary Key | 消息唯一标识 |
| `session_id` | UUID | Foreign Key → `chat_sessions.id` | 关联会话 |
| `role` | String | Not Null | system / user / assistant / tool |
| `content` | Text | Nullable | 消息内容 |
| `tool_calls` | JSONB | Nullable | 记录模型调用的工具名称、参数以及内部反思状态 |

### 4.3 [Milvus] Collection 名：`knowledge_chunks`（RAG 知识切片库）

- **Index 类别：** `HNSW`
- **Metric 类别：** `COSINE`

| 字段（Field）名 | 数据类型（DataType） | 约束 / 参数 | 说明 |
| --- | --- | --- | --- |
| `chunk_id` | INT64 | Primary Key, AutoID=False | 切片全局唯一标识 |
| `vector` | FLOAT_VECTOR | Dim = 4096 | 嵌入模型生成的高维特征向量 |
| `document_id` | VARCHAR | Max Length: 64 | 原文档唯一标识，与 Neo4j 节点属性对齐（图谱锚点） |
| `content` | VARCHAR | Max Length: 65535 | 文本切片原文 |
| `allowed_roles` | ARRAY (VARCHAR) | Max Capacity: 20 | 权限标识预留：允许访问的角色列表（如 `["ALL"]`） |
| `entity_tags` | ARRAY (VARCHAR) | Max Capacity: 50 | 图谱实体预留：从文本中提取的实体标签，与 Neo4j 节点名称对齐 |
| `metadata` | JSON | Dynamic Allowed | 动态元数据，包含文档类型、来源、入库时间等 |

### 4.4 [Neo4j] 核心节点与关系模型

| 类型 | 名称 | 关键属性 | 说明 |
| --- | --- | --- | --- |
| Node Label | `Entity` | `name`, `type`, `document_id` | 通用实体节点，`document_id` 与 Milvus Chunk 对齐 |
| Node Label | `Document` | `document_id`, `title`, `created_at` | 文档级节点，作为实体的来源锚点 |
| Relationship | `RELATED_TO` | `relation_type`, `weight` | 实体间的通用语义关系，`relation_type` 存储具体关系类型 |
| Relationship | `MENTIONED_IN` | `chunk_id` | 实体节点指向 Document 节点，`chunk_id` 记录出处切片 |

---

## 5. 开发里程碑与排期建议

### Week 1：基础设施与流式通信

- 完成 FastAPI 项目初始化与目录结构搭建。
- 配置 PostgreSQL 数据库及基础会话、消息上下文表（`chat_sessions` / `chat_messages`）。
- 引入 LiteLLM，跑通基础大模型调用的 `/chat/stream` 接口（纯文本对话 + SSE 控制流设计）。

### Week 2：Milvus 集成与 Agentic RAG 闭环

- 编写 Milvus 客户端初始化模块：从 `.env` 读取连接配置，应用启动时自动检测并按需创建 `knowledge_chunks` Collection（4096 维 HNSW 索引）。
- 完善 LangGraph 状态图，编写 Agent Node 和 Tool Node，打通 ReAct 错误反思链路。
- 封装内含标量过滤、权限匹配和图谱标识注入的 `search_knowledge_base` 工具，完成全链路联调。

### Week 3：Neo4j 集成与 Graph RAG 闭环

- 编写 Neo4j 客户端初始化模块：从 `.env` 读取连接配置，应用启动时自动完成约束创建与健康检查。
- 实现实体与关系的通用写入接口（Upsert 语义），完成与 Milvus `document_id` / `entity_tags` 的双向索引对齐。
- 封装 `query_knowledge_graph` Tool 并注册为 Agent 技能，实现 Graph RAG 联合增强查询策略（图谱锚定 → 向量精筛）。
- 打通实体抽取管道（Prompt 方式），完成文档入库时图谱节点与向量切片的同步写入，完成全链路联调。好的，更新后的完整 Markdown 如下：

---

# GeoAgent V1.0 基础底座 · 需求规格说明书 (PRD)

**文档版本：** V1.0 Draft
**项目定位：** 具备自主推理能力与主动知识检索能力的智能体基础后端引擎
**核心架构：** FastAPI · LangGraph · LiteLLM · PostgreSQL · Milvus · Neo4j

---

## 1. 产品概述

### 1.1 背景与目标

在引入复杂的业务脚本调度和外部系统联动之前，系统需要一个极其稳定、具备深度推理能力的大脑。V1.0 阶段的核心目标是**搭建纯净的底层控制流**，即打通基于 LangGraph 的 ReAct（推理与执行）循环，并实现模型自主调用工具查询 **Milvus 分布式向量库**（Agentic RAG）与 **Neo4j 知识图谱**的闭环，为海量文档的高维特征检索与结构化知识推理提供支撑。

### 1.2 边界与范围（V1.0 不做的事）

- 暂不开发外部 MCP（Model Context Protocol）接口。
- 暂不涉及复杂的前端 WebGIS 指令联动渲染。

---

## 2. 核心业务流程（User Story）

1. **发起请求：** 用户在网页端输入问题。
2. **意图规划（Thought）：** 大模型接收问题，判断自身知识不足以回答，决定调用检索工具。
3. **主动检索（Action）：** 大模型生成查询关键词，触发后端的 RAG Tool 或 Graph Tool。
4. **知识观测（Observation）：** 后端查询向量数据库或知识图谱，执行权限与元数据过滤，将召回内容返回给大模型。
5. **反思与重试（Self-Correction）：** 大模型阅读后，若发现缺失关键数据，自动修改关键词再次查询；或若执行脚本异常，根据错误堆栈修改参数重试。
6. **流式输出（Final Answer）：** 获取充分信息后，整合生成最终结论，并通过 SSE 实时推流至前端。

---

## 3. 详细功能需求拆分

### 3.1 接入与通信模块（Gateway & Communication）

| 需求 ID | 功能名称 | 详细描述 | 验收标准 |
| --- | --- | --- | --- |
| API-01 | 会话创建接口 | `POST /api/v1/sessions`：初始化新的对话上下文，返回唯一的 `session_id`。 | 成功在数据库生成 Session 记录并返回 UUID。 |
| API-02 | 流式对话接口 | `POST /api/v1/chat/stream`：接收用户输入及 `session_id`，采用 SSE 协议返回数据流。 | 前端能平滑接收打字机效果的文本，不出现阻塞。 |
| API-03 | 状态推送机制 | 在 SSE 流中区分"文本流"与"控制流"。当大模型开始调用工具时，推送 `{"type": "tool_start", "tool": "search"}`。 | 前端可根据标识展示"正在检索知识库..."等加载状态。 |

### 3.2 大模型路由模块（Model Routing）

| 需求 ID | 功能名称 | 详细描述 | 验收标准 |
| --- | --- | --- | --- |
| LLM-01 | LiteLLM 集成 | 引入 LiteLLM 代理，通过统一的 OpenAI 规范接口调用 DeepSeek、Qwen 或 GLM 等模型。 | 仅需修改 `.env` 配置文件即可无缝切换不同厂商大模型。 |
| LLM-02 | Function Calling 适配 | 确保选用的基础大模型能够精准识别并按 JSON 格式输出 `tool_calls`。 | 模型能连续三次正确输出符合参数预设的 Tool Call JSON。 |

### 3.3 Agent 编排引擎模块（LangGraph ReAct）

| 需求 ID | 功能名称 | 详细描述 | 验收标准 |
| --- | --- | --- | --- |
| AGT-01 | 状态机定义 | 定义 `AgentState`，至少包含 `messages`（对话列表）字段。 | LangGraph 图编译无报错。 |
| AGT-02 | ReAct 循环控制 | 实现 `Thought -> Action -> Observation` 的经典循环路径。 | 模型能在生成最终答案前，多次循环进入 Action 节点。 |
| AGT-03 | 死循环熔断 | 设置最大流转次数（如 `max_iterations = 5`），防止模型陷入无意义的循环调用消耗 Token。 | 循环达到 5 次时强制中止并输出兜底回复。 |
| AGT-04 | 错误反思注入 | 当 Tool 抛出异常时，捕获异常堆栈并作为 `ToolMessage` 返回给模型，提示其纠正。 | 模型接收报错后能输出"参数错误，我需要修正..."并重试。 |

### 3.4 本地执行工具模块（Local Tools）

| 需求 ID | 功能名称 | 详细描述 | 验收标准 |
| --- | --- | --- | --- |
| TOL-01 | 脚本子进程调度 | 基于 `subprocess.run` 封装通用脚本执行引擎，设置严格的执行超时（如 30s）。 | 超时脚本被强制 Kill 并返回超时提示。 |
| TOL-02 | Dummy 测试工具 | 注册 `mock_data_parser`，模拟读取结构化数据（仅返回固定 JSON 测试用）。 | 模型能正确理解其用途并成功传入必填参数调用。 |

### 3.5 主动知识检索模块（Agentic RAG）

针对高维稠密嵌入与系统未来的权限演进进行专项设计。Milvus 服务本身通过 `.env` 配置文件接入，V1.0 仅聚焦代码层面的客户端集成与功能实现。

| 需求 ID | 功能名称 | 详细描述 | 验收标准 |
| --- | --- | --- | --- |
| RAG-01 | Milvus 客户端初始化 | 从 `.env` 读取 Milvus 连接配置（`MILVUS_HOST`、`MILVUS_PORT`、`MILVUS_TOKEN` 等），应用启动时通过 PyMilvus 建立连接；若 Collection 不存在则自动执行初始化（Schema 定义 + HNSW 索引创建），若已存在则直接复用。 | 应用冷启动后 PyMilvus 连接成功，Collection `knowledge_chunks` 可用；首次启动自动完成 Schema 和索引初始化，重启时跳过重建。 |
| RAG-02 | 知识检索 Tool 封装 | 封装 `search_knowledge_base(query, top_k, **kwargs)` 并注册为 Agent 技能，支持大模型依据当前上下文动态生成多角度的标量查询子句。 | 模型在处理 Session 中不包含的专业背景时，能主动输出符合参数规范的 Tool 调用。 |
| RAG-03 | 混合标量过滤 | 利用 Milvus 的标量过滤能力，支持向量空间搜索时动态叠加基于元数据（如文档类型、所属域）的布尔表达式。 | 秒级响应包含 `metadata["type"] == "report"` 等复杂表达式的检索请求。 |
| RAG-04 | 数据级权限预留 | 在 Milvus Schema 中强制包含 `allowed_roles`（Array 类型）字段，暂存全局通用标识 `["ALL"]`，为后续行级 RAG 权限隔离打下基础。 | 执行检索前，Tool 能自动从上下文解析当前角色，并构造包含 `JSON_CONTAINS(allowed_roles, current_role)` 的过滤表达式。 |
| RAG-05 | 知识图谱锚点预留 | 数据切片时在 Schema 中预留 `document_id` 和 `entity_tags`（字符数组，存放从文本中提取的实体标签）。 | 召回的 Chunk 数据结构中天然包含实体关联指针，可无缝透传给后续 Graph RAG 流程。 |

### 3.6 知识图谱模块（Knowledge Graph）

基于 Neo4j 构建结构化知识网络，与 Milvus 向量检索形成互补：RAG 解决语义相似度召回，图谱解决实体关系推理与多跳查询，两者通过 `entity_tags` 和 `document_id` 字段串联。Neo4j 服务通过 `.env` 配置接入，V1.0 聚焦代码层面的集成与核心查询能力实现。

| 需求 ID | 功能名称 | 详细描述 | 验收标准 |
| --- | --- | --- | --- |
| KG-01 | Neo4j 客户端初始化 | 从 `.env` 读取 Neo4j 连接配置（`NEO4J_URI`、`NEO4J_USER`、`NEO4J_PASSWORD`），应用启动时通过官方 Python Driver 建立连接并执行健康检查；若基础约束（Constraint）不存在则自动创建，已存在则复用。 | 应用冷启动后驱动连接成功，基础 Node Label 的唯一性约束就绪，健康检查接口返回正常。 |
| KG-02 | 实体与关系写入 | 封装通用的节点（Node）与关系（Relationship）写入接口，支持 Upsert 语义（`MERGE` 语句），避免重复节点。写入时自动关联 `document_id`，建立图谱节点与向量切片之间的双向索引。 | 同一实体重复写入不产生重复节点；节点上的 `document_id` 属性与 Milvus 中对应 Chunk 的 `document_id` 一致可查。 |
| KG-03 | 图谱查询 Tool 封装 | 封装 `query_knowledge_graph(entity, relation_types, max_hops)` 并注册为 Agent 技能，支持以实体为起点进行多跳关系遍历，返回结构化的路径与节点属性列表。 | 模型在需要推理实体间关联时，能主动调用该 Tool，并正确解析返回的路径结果用于生成答案。 |
| KG-04 | RAG 联合增强查询 | 在 Agent 层实现 Graph RAG 联合调用策略：先通过图谱查询锚定实体上下文，再将图谱返回的实体标签注入为 Milvus 的标量过滤条件，执行精准向量检索。 | 联合查询的召回结果相比单纯向量检索，在实体相关性上有明显提升；两步调用在 SSE 流中均有对应的 `tool_start` 状态推送。 |
| KG-05 | 实体抽取管道预留 | 在文档入库流程中预留 NER（命名实体识别）调用接口，暂以大模型 Prompt 方式实现实体抽取，后续可替换为专用 NER 模型。抽取结果同步写入 Neo4j 节点及 Milvus `entity_tags` 字段。 | 给定一段文本，管道能返回结构化的实体列表（含实体名、类型），并完成图谱节点与向量切片的同步写入。 |

---

## 4. 数据库字典设计（混合存储架构）

V1.0 阶段采用三库协同：PostgreSQL 管理会话与上下文状态；Milvus 管理高维向量切片；Neo4j 管理实体关系网络。三者均通过 `.env` 统一配置接入。

### 4.1 [PostgreSQL] 表名：`chat_sessions`（会话表）

| 字段名 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | UUID | Primary Key | 会话唯一标识 |
| `created_at` | Timestamp | Not Null, Default NOW() | 创建时间 |
| `metadata` | JSONB | Nullable | 预留：存储当前 Session 的偏好或上下文范围 |

### 4.2 [PostgreSQL] 表名：`chat_messages`（消息上下文表）

| 字段名 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | UUID | Primary Key | 消息唯一标识 |
| `session_id` | UUID | Foreign Key → `chat_sessions.id` | 关联会话 |
| `role` | String | Not Null | system / user / assistant / tool |
| `content` | Text | Nullable | 消息内容 |
| `tool_calls` | JSONB | Nullable | 记录模型调用的工具名称、参数以及内部反思状态 |

### 4.3 [Milvus] Collection 名：`knowledge_chunks`（RAG 知识切片库）

- **Index 类别：** `HNSW`
- **Metric 类别：** `COSINE`

| 字段（Field）名 | 数据类型（DataType） | 约束 / 参数 | 说明 |
| --- | --- | --- | --- |
| `chunk_id` | INT64 | Primary Key, AutoID=False | 切片全局唯一标识 |
| `vector` | FLOAT_VECTOR | Dim = 4096 | 嵌入模型生成的高维特征向量 |
| `document_id` | VARCHAR | Max Length: 64 | 原文档唯一标识，与 Neo4j 节点属性对齐（图谱锚点） |
| `content` | VARCHAR | Max Length: 65535 | 文本切片原文 |
| `allowed_roles` | ARRAY (VARCHAR) | Max Capacity: 20 | 权限标识预留：允许访问的角色列表（如 `["ALL"]`） |
| `entity_tags` | ARRAY (VARCHAR) | Max Capacity: 50 | 图谱实体预留：从文本中提取的实体标签，与 Neo4j 节点名称对齐 |
| `metadata` | JSON | Dynamic Allowed | 动态元数据，包含文档类型、来源、入库时间等 |

### 4.4 [Neo4j] 核心节点与关系模型

| 类型 | 名称 | 关键属性 | 说明 |
| --- | --- | --- | --- |
| Node Label | `Entity` | `name`, `type`, `document_id` | 通用实体节点，`document_id` 与 Milvus Chunk 对齐 |
| Node Label | `Document` | `document_id`, `title`, `created_at` | 文档级节点，作为实体的来源锚点 |
| Relationship | `RELATED_TO` | `relation_type`, `weight` | 实体间的通用语义关系，`relation_type` 存储具体关系类型 |
| Relationship | `MENTIONED_IN` | `chunk_id` | 实体节点指向 Document 节点，`chunk_id` 记录出处切片 |

---

## 5. 开发里程碑与排期建议

### Week 1：基础设施与流式通信

- 完成 FastAPI 项目初始化与目录结构搭建。
- 配置 PostgreSQL 数据库及基础会话、消息上下文表（`chat_sessions` / `chat_messages`）。
- 引入 LiteLLM，跑通基础大模型调用的 `/chat/stream` 接口（纯文本对话 + SSE 控制流设计）。

### Week 2：Milvus 集成与 Agentic RAG 闭环

- 编写 Milvus 客户端初始化模块：从 `.env` 读取连接配置，应用启动时自动检测并按需创建 `knowledge_chunks` Collection（4096 维 HNSW 索引）。
- 完善 LangGraph 状态图，编写 Agent Node 和 Tool Node，打通 ReAct 错误反思链路。
- 封装内含标量过滤、权限匹配和图谱标识注入的 `search_knowledge_base` 工具，完成全链路联调。

### Week 3：Neo4j 集成与 Graph RAG 闭环

- 编写 Neo4j 客户端初始化模块：从 `.env` 读取连接配置，应用启动时自动完成约束创建与健康检查。
- 实现实体与关系的通用写入接口（Upsert 语义），完成与 Milvus `document_id` / `entity_tags` 的双向索引对齐。
- 封装 `query_knowledge_graph` Tool 并注册为 Agent 技能，实现 Graph RAG 联合增强查询策略（图谱锚定 → 向量精筛）。
- 打通实体抽取管道（Prompt 方式），完成文档入库时图谱节点与向量切片的同步写入，完成全链路联调。
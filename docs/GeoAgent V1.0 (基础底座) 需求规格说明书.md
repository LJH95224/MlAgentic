# GeoAgent V1.0 (基础底座) 需求规格说明书 (PRD)

**文档版本:** V1.0 Draft
**项目定位:** 具备自主推理能力与主动知识检索能力的气象空间智能体基础后端引擎。
**核心架构:** FastAPI + LangGraph + LiteLLM + PostgreSQL (pgvector)

## 1. 产品概述

### 1.1 背景与目标

在引入复杂的气象脚本调度和地理空间交互之前，系统需要一个极其稳定、具备深度推理能力的大脑。V1.0 阶段的核心目标是**搭建纯净的底层控制流**，即打通基于 LangGraph 的 ReAct（推理与执行）循环，并实现模型自主调用工具查询 PostgreSQL 向量库（Agentic RAG）的闭环。

### 1.2 边界与范围 (V1.0 不做的事)

* 暂不引入 Docker 动态沙盒隔离，代码统一在后端容器内按子进程运行。
* 暂不开发外部 MCP (Model Context Protocol) 接口。
* 暂不涉及复杂的前端 WebGIS（如 Cesium/OpenLayers）指令联动渲染。

---

## 2. 核心业务流程 (User Story)

1. **发起请求:** 用户在网页端输入气象相关问题（如：“分析一下这篇报告中提到的华南地区雷达回波特征”）。
2. **意图规划 (Thought):** 大模型接收问题，判断自身知识不足以回答，决定调用检索工具。
3. **主动检索 (Action):** 大模型生成查询关键词，触发后端的 RAG Tool。
4. **知识观测 (Observation):** 后端查询 pgvector，将召回的切片内容返回给大模型。
5. **反思与重试 (Self-Correction):** 大模型阅读后，若发现缺失关键数据，自动修改关键词再次查询；或若执行本地脚本异常，大模型根据错误堆栈修改参数重试。
6. **流式输出 (Final Answer):** 获取充分信息后，整合生成最终气象分析结论，并通过 SSE 实时推流至前端。

---

## 3. 详细功能需求拆分

### 3.1 接入与通信模块 (Gateway & Communication)

提供稳定、高性能的异步接口供前端调用。

| 需求 ID | 功能名称 | 详细描述 | 验收标准 |
| --- | --- | --- | --- |
| API-01 | 会话创建接口 | `POST /api/v1/sessions`：初始化一个新的对话上下文，返回唯一的 `session_id`。 | 成功在数据库生成 Session 记录并返回 UUID。 |
| API-02 | 流式对话接口 | `POST /api/v1/chat/stream`：接收用户输入及 `session_id`，采用 Server-Sent Events (SSE) 协议返回数据流。 | 前端能平滑接收打字机效果的文本，不出现阻塞。 |
| API-03 | 状态推送机制 | 在 SSE 流中区分“文本流”与“控制流”。当大模型开始调用工具时，推送 `{"type": "tool_start", "tool": "search"}`。 | 前端可根据标识展示“正在检索知识库...”等加载状态。 |

### 3.2 大模型路由模块 (Model Routing)

屏蔽底层 API 差异，提供统一的调用标准。

| 需求 ID | 功能名称 | 详细描述 | 验收标准 |
| --- | --- | --- | --- |
| LLM-01 | LiteLLM 集成 | 引入 LiteLLM 代理，通过统一的 OpenAI 规范接口调用 DeepSeek、Qwen 或 GLM 等开源模型。 | 仅需修改 `.env` 配置文件即可无缝切换不同厂商大模型。 |
| LLM-02 | Function Calling 适配 | 确保选用的基础大模型能够精准识别并按 JSON 格式输出 `tool_calls`。 | 模型能连续三次正确输出符合参数预设的 Tool Call JSON。 |

### 3.3 Agent 编排引擎模块 (LangGraph ReAct)

系统的核心大脑，负责状态流转。

| 需求 ID | 功能名称 | 详细描述 | 验收标准 |
| --- | --- | --- | --- |
| AGT-01 | 状态机定义 | 定义 `AgentState`，至少包含 `messages` (对话列表) 字段。 | LangGraph 图编译无报错。 |
| AGT-02 | ReAct 循环控制 | 实现 `Thought -> Action -> Observation` 的经典循环路径。 | 模型能在生成最终答案前，多次循环进入 Action 节点。 |
| AGT-03 | 死循环熔断 | 设置最大流转次数（如 `max_iterations = 5`），防止模型陷入无意义的循环调用消耗 Token。 | 循环达到 5 次时强制中止并输出兜底回复。 |
| AGT-04 | 错误反思注入 | 当 Tool 抛出异常时，捕获异常堆栈并作为 `ToolMessage` 返回给模型，提示其纠正。 | 模型接收报错后能输出“参数错误，我需要修正...”并重试。 |

### 3.4 本地执行工具模块 (Local Tools)

V1.0 阶段的轻量级内部技能库。

| 需求 ID | 功能名称 | 详细描述 | 验收标准 |
| --- | --- | --- | --- |
| TOL-01 | 脚本子进程调度 | 基于 `subprocess.run` 封装通用的脚本执行引擎，设置严格的执行超时（如 30s）。 | 超时脚本被强制 Kill 并返回超时提示。 |
| TOL-02 | Dummy 测试工具 | 注册一个 `mock_weather_parser`，模拟读取气象数据（仅作返回固定的气象 JSON 测试用）。 | 模型能正确理解其用途并成功传入必填参数调用。 |

### 3.5 主动知识检索模块 (Agentic RAG)

取代传统 RAG 的核心高阶模块。

| 需求 ID | 功能名称 | 详细描述 | 验收标准 |
| --- | --- | --- | --- |
| RAG-01 | 向量存储底座 | 部署 PostgreSQL 并初始化 `pgvector` 扩展。 | 成功插入并计算余弦相似度。 |
| RAG-02 | 知识检索 Tool | 封装 `search_knowledge_base(query, top_k)` 方法并注册为 Agent Tool，供大模型主动调用。 | 模型在面临专业问题时，能主动生成查询语句调用此函数。 |
| RAG-03 | 多路召回 (预留) | 核心表结构预留 `metadata` (JSONB 类型) 字段，支持按文档类型进行硬过滤。 | 能够执行带 `WHERE metadata->>'type' = 'report'` 的混合查询。 |

---

## 4. 数据库字典设计 (PostgreSQL)

V1.0 阶段需优先建立以下核心数据表（使用 SQLAlchemy ORM）：

### 4.1 表名：`chat_sessions` (会话表)

| 字段名 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | UUID | Primary Key | 会话唯一标识 |
| `created_at` | Timestamp | Not Null | 创建时间 |
| `metadata` | JSONB | Nullable | 预留：存储当前 Session 的偏好或地理范围 |

### 4.2 表名：`chat_messages` (消息上下文表)

| 字段名 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | UUID | Primary Key | 消息唯一标识 |
| `session_id` | UUID | Foreign Key | 关联会话 |
| `role` | String | Not Null | system, user, assistant, tool |
| `content` | Text | Nullable | 消息内容 |
| `tool_calls` | JSONB | Nullable | 记录模型调用的工具名称与参数 |

### 4.3 表名：`knowledge_chunks` (RAG 知识切片表)

| 字段名 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | UUID | Primary Key | 切片唯一标识 |
| `document_id` | String | Not Null | 原文档标识 |
| `content` | Text | Not Null | 切片文本内容 |
| `embedding` | Vector(1536) | Not Null | pgvector 向量表示（维度依选用的 Embedding 模型而定） |
| `metadata` | JSONB | Nullable | 元数据（如作者、时间、类型） |

---

## 5. 开发里程碑与排期建议

针对 V1.0 底座的开发，建议采用两周的 Sprint 周期：

**Week 1: 基础设施与流式通信**

* 完成 FastAPI 项目初始化与目录结构搭建。
* 配置 PostgreSQL + pgvector 数据库。
* 引入 LiteLLM，跑通基础大模型调用的 `/chat/stream` 接口（纯文本对话）。

**Week 2: LangGraph 与 Agentic RAG 闭环**

* 定义 LangGraph 状态图，编写 `Agent Node` 和 `Tool Node`。
* 实现 `subprocess` 脚本调用工具，测试 ReAct 错误反思链路。
* 实现 pgvector 的查询接口，封装为 `search_knowledge_base` 工具，完成主流程联调。
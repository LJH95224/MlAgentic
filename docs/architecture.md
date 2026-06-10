# TyAgent V1.0 技术文档

> **文档定位**：描述项目当前已实现部分的**技术架构、数据流转、关键技术细节**，供后续接手者快速建立全局认知。
> **维护约定**：每次完成 PRD 模块或对已完成部分做实质性改动后，必须同步更新本文档。本文档与 [progress.md](progress.md) 互补 ——
> - progress.md：模块完成度 + 文件清单 + 验收记录（"做到哪了"）
> - 本文档：架构原理 + 技术决策 + 关键实现（"为什么这么做、怎么做的"）

---

## 1. 系统定位

TyAgent 是一个**面向气象空间智能的 Agent 后端引擎基础底座**。V1.0 的核心目标：

> 搭建纯净的底层控制流，打通基于 LangGraph 的 ReAct 推理循环，让大模型能够**主动**调用向量检索（RAG）与知识图谱（KG）形成闭环。

**V1.0 明确不做**：Docker 动态沙盒、外部 MCP 接口、前端 WebGIS 联动渲染、复杂业务脚本调度。

---

## 2. 技术栈总览

| 层 | 选型 | 用途 |
|---|---|---|
| Web 框架 | **FastAPI** + sse-starlette | 异步、SSE 流式输出 |
| Agent 编排 | **LangGraph** | ReAct 状态机：Thought → Action → Observation |
| 模型网关 | **LiteLLM** | 统一 OpenAI 规范，多厂商切换（DeepSeek/Qwen/GLM/SiliconFlow） |
| 关系数据库 | **PostgreSQL 17** + SQLAlchemy 2.0 async | 会话与消息上下文持久化 |
| 向量库 | **Milvus 2.6 standalone** | RAG 知识切片存储（HNSW + COSINE，4096 维） |
| 知识图谱 | **Neo4j 5.26 Community** + APOC | 实体关系存储与多跳查询 |
| Embedding | **Qwen3-Embedding-8B**（SiliconFlow） | 文本 → 4096 维向量 |
| 对话/NER 模型 | **DeepSeek v4-flash** | 主对话 + NER 实体抽取（解耦配置） |
| 通信 | **Server-Sent Events (SSE)** | 文本流 + 控制流双通道 |

> 三个模型职责清晰解耦：`LITELLM_MODEL`（chat）/ `KG_NER_MODEL`（NER）/ `EMBEDDING_MODEL`（向量化），均可独立切换厂商。

---

## 3. 整体架构

### 3.1 层次结构

```
┌──────────────────────────────────────────────────────────────────┐
│                          客户端 / 前端                            │
└────────────────────────────┬─────────────────────────────────────┘
                             │ HTTP + SSE
┌────────────────────────────▼─────────────────────────────────────┐
│                  FastAPI 接入层（app/api/）                       │
│  POST /api/v1/sessions          创建会话                          │
│  POST /api/v1/chat/stream       SSE 流式对话                      │
└────────────────────────────┬─────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────┐
│           Service 胶水层（app/services/chat_service）             │
│  - 加载历史消息（PG）                                              │
│  - 调用 Agent runner                                              │
│  - 把 AgentEvent 翻译为 SSEEvent                                  │
│  - 把模型最终回复落库                                              │
└────────────────────────────┬─────────────────────────────────────┘
                             │ agent.runner.run_stream()
                             │ ↑↑↑ 唯一接口（Agent ↔ Service 契约）
┌────────────────────────────▼─────────────────────────────────────┐
│             Agent 编排层（app/agent/，LangGraph）                 │
│  ┌────────────┐    ┌───────────────────┐    ┌────────────┐      │
│  │ call_model │───▶│  should_continue  │───▶│ tool_node  │      │
│  │  (LLM 推理) │◀───│   (条件路由)      │◀───│  (执行工具) │      │
│  └────────────┘    └───────────────────┘    └────────────┘      │
└──┬────────────────────────────────────────────────────┬─────────┘
   │                                                    │
   │ LiteLLM / ChatOpenAI                               │ 工具调用
   ▼                                                    ▼
┌──────────────────┐                  ┌─────────────────────────────┐
│  LLM 厂商 API     │                  │  工具层（app/tools/）        │
│  DeepSeek/Qwen   │                  │  - mock_weather_parser      │
│                  │                  │  - search_knowledge_base ───┼──▶ Milvus
│                  │                  │  - query_knowledge_graph ───┼──▶ Neo4j
└──────────────────┘                  └─────────────────────────────┘
   ▲
   │ Embedding 调用
┌──┴─────────────────┐
│ SiliconFlow API    │
│ Qwen3-Embedding-8B │
└────────────────────┘
```

### 3.2 模块清单

| 模块 | 路径 | 职责 |
|---|---|---|
| **接入** | `app/api/v1/` | FastAPI 路由 + SSE 协议封装 |
| **配置** | `app/core/config.py` | pydantic-settings 统一加载 `.env` |
| **会话存储** | `app/db/` + `app/models/` | SQLAlchemy async + asyncpg |
| **业务编排** | `app/services/` | API ↔ Agent 之间的胶水 |
| **Agent** | `app/agent/` | LangGraph 图、节点、流事件翻译 |
| **LLM 网关** | `app/llm/` | LiteLLM 封装（被 3.3 旁路了，直接用 ChatOpenAI） |
| **工具** | `app/tools/` | LangChain @tool 注册中心 |
| **RAG** | `app/rag/` | Milvus 客户端 + Schema + Embedding + 检索工具 |
| **KG** | `app/kg/` | Neo4j 客户端 + Upsert + NER + 查询工具 |

---

## 4. LangGraph Agent 流程图

### 4.1 状态机结构

LangGraph 编译出的图（[app/agent/graph.py](../app/agent/graph.py)）：

```
                 ┌──────────┐
                 │  START   │
                 └────┬─────┘
                      ▼
              ┌──────────────┐
              │  call_model  │  ◀───── （循环回流）
              │  (LLM 推理)   │              │
              └──────┬───────┘              │
                     ▼                      │
            ┌──────────────────┐            │
            │  should_continue │            │
            │  (条件路由)       │            │
            └──┬───────────┬───┘            │
               │           │                │
       tool_calls?       否（END）          │
               │           │                │
               ▼           ▼                │
        ┌──────────┐    ┌────┐              │
        │  tools   │    │END │              │
        │ (执行工具) │    └────┘              │
        └─────┬────┘                        │
              │                             │
              └────────────────────────────►┘
```

### 4.2 状态结构（AgentState）

[app/agent/state.py](../app/agent/state.py)：

```python
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]  # 消息历史（自动追加）
    remaining_iterations: int                                  # 熔断倒计数（默认 5）
```

### 4.3 节点行为

| 节点 | 职责 | 关键设计 |
|---|---|---|
| **call_model** | 调用 LLM 推理，把响应追加进 state | 进入时检查 `remaining_iterations`，≤0 直接返回兜底回复（**AGT-03 熔断**），否则递减 |
| **tool_node** | 按 LLM 输出的 `tool_calls` 名称查表执行 | `try/except` 包裹，异常时把完整 traceback 封装成 `ToolMessage(status="error")` 回传给模型（**AGT-04 错误反思**） |
| **should_continue** | 条件路由 | AIMessage 含 `tool_calls` → 走 `tools` 节点；否则 → END |

### 4.4 流事件翻译（runner）

[app/agent/runner.py](../app/agent/runner.py) 的 `run_stream()` 把 LangGraph 的 `stream_mode=["messages", "custom"]` 双通道事件翻译为统一的 `AgentEvent`：

| LangGraph 事件 | AgentEvent |
|---|---|
| `AIMessageChunk.content`（token 流） | `AgentTextChunk(content=...)` |
| `tool_call_chunks` 首次出现 name | `AgentToolStart(tool=...)` |
| `tool_node` 通过 `get_stream_writer()` 发出 `{"kind":"tool_end"}` | `AgentToolEnd(tool=..., output=...)` |
| 图运行结束 | `AgentDone(final_content=...)` |

**为什么不直接复用 3.2 的 LiteLLM 客户端**：
- LangGraph 的 `stream_mode="messages"` 需要 `langchain_openai.ChatOpenAI` 这种 LangChain Runnable 才能拿到 token 级流式 + `tool_call_chunks` 自动增量累积
- LiteLLM 的 `acompletion(stream=True)` 返回的是 OpenAI 原生流，需要自己解析 JSON 增量片段，不值得

---

## 5. 数据流转：一次完整对话的端到端时序

以**用户问 "西北太平洋台风的常见路径有哪些？"** 为例：

```
1. [前端] POST /api/v1/chat/stream
          {"session_id": "...", "content": "西北太平洋台风的常见路径有哪些？"}
                          │
                          ▼
2. [接入层 chat.py] 校验会话存在 → 调 chat_service.stream_chat(...)
                          │
                          ▼
3. [Service 层 chat_service]
   - 从 PostgreSQL 加载历史消息（如果有）
   - 调用 agent.runner.run_stream(session_id, user_input, history)
                          │
                          ▼
4. [Runner] 注入 _SYSTEM_PROMPT（工具使用准则） + history + user_input
   → graph.astream(initial_state)
                          │
                          ▼
5. [LangGraph 循环开始]

   call_model
   ├─ LLM 输出："好的，我先查图谱..."
   └─ tool_calls=[{name: query_knowledge_graph, args: {entity_name: "台风"}}]
                          │
                          ▼  Runner 发出 AgentToolStart
                          │
   tool_node
   ├─ 执行 query_knowledge_graph.ainvoke({"entity_name": "台风"})
   ├─ Neo4j Cypher 多跳查询 → 未命中
   └─ 返回 ToolMessage("（图谱中未找到该实体的关联路径...）")
                          │
                          ▼  Runner 发出 AgentToolEnd
                          │
   call_model（第 2 轮）
   ├─ LLM 看到图谱失败 → 决定走 RAG 兜底
   └─ tool_calls=[{name: search_knowledge_base, args: {query: "台风"}}]
                          │
                          ▼
   tool_node
   ├─ 1) 文本 → Embedding（SiliconFlow Qwen3-Embedding-8B）→ 4096 维向量
   ├─ 2) 拼 filter: 'ARRAY_CONTAINS(allowed_roles, "ALL")'
   ├─ 3) Milvus search(data=[vec], filter=expr, limit=5)
   └─ 返回 5 条 chunk，包含 entity_tags=["西北太平洋","菲律宾",...]
                          │
                          ▼
   call_model（第 3 轮）
   ├─ LLM 从 chunk 中提取出实体名
   └─ tool_calls=[{name: query_knowledge_graph, args: {entity_name: "西北太平洋"}}]
                          │
                          ▼
   tool_node
   └─ Neo4j 命中 20 条路径
                          │
                          ▼
   call_model（第 4 轮）
   ├─ LLM 用拿到的实体名做精筛
   └─ tool_calls=[{name: search_knowledge_base,
                    args: {query: "台风路径", entity_tags: ["西北太平洋","南海","ECMWF",...]}}]
                          │
                          ▼
   tool_node
   ├─ filter: 'ARRAY_CONTAINS(allowed_roles, "ALL") and
   │           ARRAY_CONTAINS_ANY(entity_tags, ["西北太平洋", "南海", ...])'
   └─ Milvus 返回 entity_tags 精筛后的精准 chunk
                          │
                          ▼
   call_model（第 5 轮）
   ├─ 综合图谱关系 + RAG 原文，生成最终回答
   └─ 无 tool_calls → should_continue → END
                          │
                          ▼  Runner 持续发出 AgentTextChunk
                          ▼  最后发 AgentDone

6. [Service 层] 把最终回复（AgentDone.final_content）落库到 chat_messages 表

7. [接入层] EventSourceResponse 把 AgentEvent 序列翻译为 SSE 帧推给前端
```

### 关键观察

- **模型完全自主决策**：调几次工具、什么时候调、调哪个工具 —— 全是 LLM 看 system prompt + 工具描述自己判断
- **Graph RAG 自动 fallback**：用户问"台风"，图谱中无此节点，模型**自动**走 RAG 拿原文 → 从原文抓相关实体 → 再用实体回查图谱
- **每次工具调用都有 SSE `tool_start` / `tool_end`**：前端可实时显示"正在检索..."等状态
- **熔断保护**：`remaining_iterations=5`，防止模型陷入死循环烧 token

---

## 6. 知识库（RAG）实现细节

### 6.1 数据流：从 .txt 到 Milvus

```
data/seed/*.txt
    │
    ▼  ① 文本切片 split_document()
chunks: list[str]
    │
    ├──▶ ② NER（并发，KG-05）→ entities: list[{name, type}]
    │                                      │
    │                                      ▼
    │                              ③ Neo4j 写入 Entity 节点 + MENTIONED_IN 关系
    │
    ▼  ④ Embedding 批量请求
vectors: list[list[float]]（4096 维）
    │
    ▼  ⑤ 组装 Milvus row：{chunk_id, vector, document_id, content,
    │                       allowed_roles=["ALL"], entity_tags=[实体名],
    │                       metadata={type, source, ingested_at, chunk_index}}
    ▼
Milvus.upsert() —— 重跑幂等（chunk_id 由 hash(document_id + chunk_index) 生成）
```

### 6.2 切片策略（[scripts/rag_ingest.py](../scripts/rag_ingest.py)）

**V1.0 实现**：朴素两阶段切片，不做语义切片。

```python
_MAX_CHUNK_LEN = 800

def split_document(text):
    1. _split_paragraphs(text)         # 按 "\n\n" 切段落
    2. for para in paragraphs:          # 段落 > 800 字符再硬切
           if len(para) <= 800: keep
           else: 按 800 字符滑窗切
```

**为什么不做语义切片**：V1.0 聚焦联调链路打通。生产场景应替换为：
- LangChain `RecursiveCharacterTextSplitter`（保留段落/标点边界）
- 或基于句子边界的语义切片（如 spaCy / 句子级 BERT 切片）
- 或按 Token 数控制（避免超过 Embedding 模型上下文窗口）

**示例数据**（`data/seed/*.txt` 3 篇气象文档）实际切片产出：
- typhoon_paths.txt → 4 chunks
- nwp_overview.txt → 5 chunks
- rainfall_monitoring.txt → 4 chunks
- **合计 13 chunks**

### 6.3 chunk_id 设计：幂等性的关键

```python
def make_chunk_id(document_id: str, chunk_index: int) -> int:
    key = f"{document_id}::{chunk_index}".encode("utf-8")
    h = hashlib.sha256(key).digest()
    raw = int.from_bytes(h[:8], byteorder="big", signed=False)
    return raw & 0x7FFFFFFFFFFFFFFF  # 取低 63 位为正 INT64
```

**幂等价值**：相同文档同一切片位置永远生成相同 chunk_id，重跑 `rag_ingest.py` 走 `upsert` 路径而非 `insert`，**不会重复积累垃圾**。

### 6.4 Milvus Collection Schema（[app/rag/schema.py](../app/rag/schema.py)）

PRD §4.3 严格落地：

| 字段 | DataType | 参数 | 作用 |
|---|---|---|---|
| `chunk_id` | INT64 | Primary Key, auto_id=False | 切片唯一标识 |
| `vector` | FLOAT_VECTOR | **dim=4096** | Qwen3-Embedding-8B 输出维度 |
| `document_id` | VARCHAR | max_length=64 | 文档锚点（与 Neo4j Document.document_id 对齐） |
| `content` | VARCHAR | max_length=65535 | 切片原文 |
| `allowed_roles` | ARRAY<VARCHAR> | capacity=20 | **权限基线**（V1.0 全部 `["ALL"]`） |
| `entity_tags` | ARRAY<VARCHAR> | capacity=50 | **图谱锚点**（NER 抽出的实体名） |
| `metadata` | JSON | dynamic=False | type / source / ingested_at / chunk_index |

**索引**：
- `vector` 上 **HNSW**（M=16, efConstruction=200）+ **COSINE** 距离度量
- `document_id` 上 **INVERTED** 索引加速按文档过滤

### 6.5 检索 Tool（[app/rag/retriever.py](../app/rag/retriever.py)）

模型主动调用入口：

```python
@tool
async def search_knowledge_base(
    query: str,                          # 自然语言查询
    top_k: int = 5,                      # 返回前 N 条
    doc_type: str | None = None,         # 标量过滤：metadata["type"]
    document_id: str | None = None,      # 限定到特定文档
    entity_tags: list[str] | None = None # 实体精筛（Graph RAG 联合）
) -> str
```

**内部流程**：

```
1. query → aembed_texts([query]) → 4096 维向量（SiliconFlow Qwen3-Embedding-8B）
2. 拼 filter 表达式：
   - 强制注入：ARRAY_CONTAINS(allowed_roles, "ALL")  ← 权限基线
   - 可选叠加：metadata["type"] == "report"           ← doc_type 过滤
   - 可选叠加：document_id == "xxx"                   ← document_id 过滤
   - 可选叠加：ARRAY_CONTAINS_ANY(entity_tags, [...]) ← KG 联合
3. Milvus search:
   - search_params={"metric_type": "COSINE", "params": {"ef": 64}}
   - output_fields=["chunk_id","content","document_id","metadata","entity_tags"]
4. 格式化为 LLM 友好的字符串：
   [1] (score=0.872, doc=typhoon_paths, tags=[西北太平洋,菲律宾]) 文本片段...
   [2] (score=0.851, doc=...) ...
```

**权限模型（RAG-04）的 V1.0 实现**：

```python
def get_current_role() -> str:
    return get_settings().rag_default_role  # 默认 "ALL"
```

`current_role` **不暴露给 LLM**，由工具内部强制注入。未来接入用户体系时，只需把这个函数改成从请求 contextvar 读取，工具签名和调用方代码完全不动。

---

## 7. 知识图谱（KG）实现细节

### 7.1 数据模型（PRD §4.4 落地，[app/kg/](../app/kg/)）

**节点**：

| Label | 关键属性 | 唯一性约束 |
|---|---|---|
| `Document` | `document_id` / `title` / `created_at` | `document_id` 单字段唯一 |
| `Entity` | `name` / `type` / `document_ids[]` | **`(name, type)` 复合唯一** |

**关系**：

| 类型 | 起点 → 终点 | 属性 |
|---|---|---|
| `MENTIONED_IN` | Entity → Document | `chunk_id`（指向具体 Milvus chunk） |
| `RELATED_TO` | Entity → Entity | `relation_type` / `weight`（V1.0 未抽取关系，留空） |

**复合唯一键的关键决策**：同名实体可能是不同类型（"苹果" = ORG / OTHER），仅按 name 唯一会丢失语义。`(name, type)` 联合唯一既保持 MERGE 幂等，又允许多义词共存。

### 7.2 NER 实体抽取（KG-05，[app/kg/ner.py](../app/kg/ner.py)）

**实现路线**：LLM Prompt 驱动，5 类通用实体。

```python
NER_SYSTEM_PROMPT = """
你是一个命名实体识别助手...
仅返回 JSON 对象：
{"entities": [{"name": "实体名", "type": "PERSON|LOCATION|ORG|TIME|OTHER"}]}

约束：
- 仅抽取明确出现在文本中的实体，不要推断
- 实体名保持原文写法
- 同一实体只输出一次
- type 必须是 PERSON / LOCATION / ORG / TIME / OTHER 五类之一
"""
```

**模型选择**：`KG_NER_MODEL=deepseek-v4-flash`（**非 reasoning** 的轻量快速模型）。

**为什么不用 reasoning 模型做 NER**：DeepSeek-v4-pro 等推理模型会对"什么算实体"过度思考，倾向返回 `entities=[]`。实测 v4-flash 在 3 篇气象文本中抽出 35 个高质量实体（地名/机构/时间），v4-pro 几乎全空。

**软失败原则**：NER 是入库的辅助步骤，主链路 Milvus 写入是核心。JSON 解析失败 / LLM 限流 → 返回 `[]`，记日志不抛错。

### 7.3 实体抽取的双库同步（KG-05 → KG-02）

[scripts/rag_ingest.py](../scripts/rag_ingest.py)::`ingest_file()` 的实际流程：

```python
1. chunks = split_document(text)
2. chunk_entities = await batch_ner(chunks)       # 并发 5 个 chunk 一批
3. vectors = await aembed_texts(chunks)
4. Milvus rows = [
       {..., "entity_tags": [实体名列表], ...} for chunk, entities in zip(...)
   ]
   milvus_client.upsert(rows)                     # 写 Milvus（含 entity_tags）
5. await upsert_document(driver, doc_id, ...)     # 写 Neo4j Document 节点
   entity_rows = [{"name":..., "type":..., "document_id":...} for entities ...]
   await bulk_upsert_entities(driver, entity_rows)         # 批量 UNWIND 写 Entity
   await bulk_link_entities_to_chunk(driver, link_rows)    # 批量建 MENTIONED_IN
```

**关键设计**：
- 同一份文档处理完一次性 UNWIND 批量写 Neo4j（不是逐条 round-trip）
- 同一实体在不同 chunk 多次出现：Milvus 多个 entity_tags 都含它，Neo4j 只建一个 Entity 节点（MERGE 幂等），但建 N 条 MENTIONED_IN 关系（关系按 chunk_id 唯一）

### 7.4 多跳查询 Cypher（[app/kg/query.py](../app/kg/query.py)）

```cypher
MATCH path = (start:Entity {name: $name})-[r*1..N]-(neighbor)
WHERE ($entity_type IS NULL OR start.type = $entity_type)
  AND ($rel_types IS NULL OR ALL(rel IN r WHERE type(rel) IN $rel_types))
RETURN start.name AS start, start.type AS start_type,
       [n IN nodes(path) | {name: n.name, type: coalesce(n.type, labels(n)[0])}] AS nodes_in_path,
       [rel IN relationships(path) | type(rel)] AS rels_in_path,
       length(path) AS hops
LIMIT 20
```

**关键约束**：
- `max_hops` **必须夹值到 [1, 5]** —— Cypher 变长路径 `[r*1..N]` 中 N 不能参数化，必须 f-string 拼接，必须先 clamp 防注入 + 防图谱爆炸
- `LIMIT 20` 硬上限防大量数据回流
- 所有其他参数（`$name` / `$entity_type` / `$rel_types`）走 `$param` 参数化

### 7.5 图谱查询 Tool（[app/kg/tool.py](../app/kg/tool.py)）

```python
@tool
async def query_knowledge_graph(
    entity_name: str,                          # 起点实体名
    entity_type: str | None = None,            # 可选限定类型
    relation_types: list[str] | None = None,   # 可选限定关系类型
    max_hops: int = 2,                         # 1~5，超出自动夹值
) -> str
```

返回示例：

```
查询: "西北太平洋"
相关路径（共 20 条）:
[1] 西北太平洋 → MENTIONED_IN → typhoon_paths
[2] 西北太平洋 → MENTIONED_IN → typhoon_paths → MENTIONED_IN → 菲律宾
...
```

---

## 8. Graph RAG 联合查询机制（KG-04）

### 8.1 设计理念

**两个独立 Tool + 模型自主多步调用**，而不是封装一个组合 Tool。理由：
- PRD §3.6 KG-04 验收点明确要求"**两步调用在 SSE 流中均有 tool_start**"，封装就只剩一个 tool_start
- 模型自主决定是否联合（不是所有问题都需要 Graph RAG）

### 8.2 关键启动：system prompt 注入

[app/agent/runner.py](../app/agent/runner.py)::`_SYSTEM_PROMPT` 明确告诉模型：

```
**Graph RAG 联合场景**（用户问题既涉及实体关系又需要原文支撑）：
1. 先调 query_knowledge_graph 拿到相关实体列表
2. 再调 search_knowledge_base，把上一步得到的实体名传入 entity_tags 精筛

**重要约束**：
- 同一工具最多重复调用 2 次
- 拿到足够信息后，立即综合输出最终答案
```

**没有这个 prompt** 的版本曾经出现：模型陷入连调 4 次 `query_knowledge_graph` 触发熔断的失败案例（详见 progress.md 联调记录）。

### 8.3 实际验证案例（kg_smoke 用例 3）

输入：`"查一下知识图谱里'台风'这个实体，找到相关实体后再到知识库里检索对应原文。"`

模型自动执行（4 步）：

```
Step 1: query_knowledge_graph(entity_name="台风")
        → 未命中（图谱无该节点，NER 没把"台风"抽成实体）
Step 2: query_knowledge_graph(entity_name="热带气旋")
        → 未命中（继续尝试别名）
Step 3: search_knowledge_base(query="台风")  ← 自动 fallback 到 RAG
        → 命中 5 条 chunk，含 entity_tags=[西北太平洋,菲律宾,ECMWF,...]
Step 4: search_knowledge_base(query="台风路径...",
                              entity_tags=["西北太平洋","南海","ECMWF","GFS",...])
        → 精筛后命中 5 条精准 chunk
最终: 综合输出 980-1400 字结构化报告
```

---

## 9. 配置驱动设计（.env）

[app/core/config.py](../app/core/config.py) 集中管理所有配置，**禁止散落 `os.getenv`**。

| 域 | 字段 | 用途 |
|---|---|---|
| 应用 | APP_NAME / APP_ENV / APP_DEBUG / APP_HOST / APP_PORT | FastAPI 基础 |
| PostgreSQL | DATABASE_URL | 会话/消息存储 |
| 主对话 LLM | LITELLM_MODEL / LITELLM_API_KEY / LITELLM_API_BASE / LITELLM_TIMEOUT / LITELLM_NUM_RETRIES | LiteLLM 网关 |
| Agent 控制 | AGENT_MAX_ITERATIONS | ReAct 熔断（默认 5） |
| Milvus | MILVUS_URI / MILVUS_TOKEN / MILVUS_COLLECTION | 向量库 |
| Embedding | EMBEDDING_MODEL / EMBEDDING_API_KEY / EMBEDDING_API_BASE / EMBEDDING_DIMENSION | 向量化（独立于 chat 配置） |
| RAG 权限 | RAG_DEFAULT_ROLE | 检索权限基线（默认 "ALL"） |
| Neo4j | NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD / NEO4J_DATABASE | 知识图谱 |
| KG NER | KG_NER_MODEL | NER 独立模型（缺省复用 LITELLM_*） |

**当前生产配置**（V1.0 联调通过）：

| 用途 | 模型 | 厂商 / 备注 |
|---|---|---|
| 主对话 chat | `deepseek-v4-flash` | DeepSeek 官方 API |
| NER 实体抽取 | `deepseek-v4-flash` | DeepSeek（与 chat 同源但**配置层解耦**） |
| Embedding | `openai/Qwen/Qwen3-Embedding-8B` | SiliconFlow（走 LiteLLM `openai/` 协议路由） |

### 9.1 LiteLLM 厂商前缀约定（容易踩坑点）

LiteLLM 通过模型名前缀决定走哪家厂商路由：

| 前缀 | 路由到 | 用途场景 |
|---|---|---|
| `deepseek/xxx` | DeepSeek 原生协议 | 直连 DeepSeek API |
| `openai/xxx` | OpenAI 兼容协议 + 用 `api_base` 决定实际端点 | **SiliconFlow / 火山 / 阿里 OpenAI 兼容端点** |
| `dashscope/xxx` | DashScope 原生协议 | 阿里通义千问 |

**Embedding 必须用 `openai/Qwen/Qwen3-Embedding-8B`** 这种带 `openai/` 前缀的写法，否则 LiteLLM 不知道该走 SiliconFlow。这是联调阶段反复踩过的坑。

### 9.2 LiteLLM `openai/` 路由的 dimensions 限制

LiteLLM 在 `openai/` 路由中会强制拦截 `dimensions` 参数（"Setting dimensions is not supported for OpenAI text-embedding-3 and later models"），即使底层是 SiliconFlow 也会被拒绝。

**当前解决**：[app/rag/embedding.py](../app/rag/embedding.py) 不传 `dimensions`，靠返回向量长度严格校验维度一致性（与 `EMBEDDING_DIMENSION=4096` 比对）。

---

## 10. 部署与运行

### 10.1 依赖管理（Conda + uv 混合）

详见 [environment_guide_zh.md](../environment_guide_zh.md)。要点：

- **环境**：conda 环境 `geo_agent`，Python 3.11
- **GDAL/PROJ/GEOS** 底层库走 conda-forge
- **纯 Python** 包统一走 uv + 清华镜像：
  ```bash
  uv pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
  ```

### 10.2 容器化中间件

[docker-compose/docker-compose.yml](../docker-compose/docker-compose.yml) 启动 4 个服务：

| 服务 | 镜像 | 暴露端口 |
|---|---|---|
| milvus-etcd | quay.io/coreos/etcd:v3.5.25 | 内部 |
| milvus-minio | minio/minio | 9000 / 9001 |
| milvus-standalone | milvusdb/milvus:v2.6.18 | **19530** (gRPC) / 9091 (健康检查) |
| tyagent-neo4j | neo4j:5.26-community | **7474** (HTTP UI) / **7687** (Bolt) |

所有持久化卷统一挂到 `d:/dockerVolumes/volumes/...`。Neo4j 默认账号 `neo4j` / `tyagent_neo4j`，与 `.env` 默认值对齐。

### 10.3 启动顺序

```bash
# 1. 启动容器（Milvus + Neo4j）
cd docker-compose && docker compose up -d

# 2. 入库（首次或重新入库）
python scripts/rag_ingest.py

# 3. 启动应用
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

`app/main.py` 的 lifespan 启动顺序：
```
PG create_all → init_milvus()（同步）→ await init_neo4j()（异步）
```

关闭顺序反向：`close_neo4j → close_milvus → engine.dispose`。任一中间件**启动期连不上就直接抛 RuntimeError 让应用挂掉**（fail-fast，不带病运行）。

---

## 11. 测试体系

| 类型 | 工具 | 数量 | 用途 |
|---|---|---|---|
| 单元测试（纯 mock） | pytest + pytest-asyncio | 62 用例 | 不依赖真服务，CI 友好 |
| 联调脚本（真服务） | 手工执行 | 4 个脚本 | 端到端验证 |

### 11.1 单测分布

| 文件 | 用例数 | 覆盖范围 |
|---|---|---|
| test_sessions_api.py | 3 | 会话创建接口 |
| test_chat_stream.py | 3 | SSE 流（含 `\r\n` 帧分隔） |
| test_llm_client.py | 6 | LiteLLM 封装 + Function Calling mock |
| test_agent_runner.py | 7 | run_stream 流事件翻译 |
| test_agent_graph.py | 9 | 熔断 / 错误反思 / 路由 / 编译 |
| test_tools.py | 3 | 工具注册中心 + dummy 工具 |
| test_script_runner.py | 9 | subprocess 30s 超时强 kill |
| test_rag_schema.py | 9 | Milvus Schema 字段/索引 |
| test_rag_retriever.py | 18 | 过滤拼装 + Mock 检索 + entity_tags |
| test_kg_writer.py | 6 | Cypher 结构 + 参数化 |
| test_kg_query.py | 16 | max_hops 夹值 + Cypher 构造 + @tool 集成 |
| test_kg_ner.py | 13 | JSON 解析 / 去重 / 软失败 |
| **合计** | **62** | |

### 11.2 联调脚本

| 脚本 | 用途 | 何时跑 |
|---|---|---|
| [scripts/llm_smoke.py](../scripts/llm_smoke.py) | LiteLLM + DeepSeek 真调用 | 切换 LLM 厂商后 |
| [scripts/agent_smoke.py](../scripts/agent_smoke.py) | Agent + LangGraph 端到端 | 改 Agent 编排后 |
| [scripts/rag_smoke.py](../scripts/rag_smoke.py) | Milvus + Embedding 端到端 | 改 RAG 或换 Embedding 模型后 |
| [scripts/kg_smoke.py](../scripts/kg_smoke.py) | Neo4j + Graph RAG 联合 | 改 KG 或调整 system prompt 后 |
| [scripts/embedding_test.py](../scripts/embedding_test.py) | 独立测 Embedding 链路 | 排查 Embedding 配置问题专用 |
| [scripts/rag_ingest.py](../scripts/rag_ingest.py) | 同时写 Milvus + Neo4j 的入库脚本 | 加入新数据时 |

---

## 12. 关键技术决策汇总

| 决策点 | 选择 | 替代方案 | 理由 |
|---|---|---|---|
| Agent 编排 | LangGraph | 自写 ReAct 循环 / LangChain Agent | 显式状态机 + 内置流式 + 工具调用 |
| LLM 接入（Agent 层） | `langchain_openai.ChatOpenAI` | 复用 3.2 的 LiteLLM | LangGraph `stream_mode="messages"` 需要 LangChain Runnable |
| 切片策略 | 段落优先 + 800 字符硬切 | 语义切片 / Token 切片 | V1.0 联调够用，生产应换 |
| chunk_id 生成 | `hash(doc_id + index)` 低 63 位 | UUID / 雪花 | 幂等性 + 不需要全局协调 |
| 向量距离度量 | COSINE | L2 / IP | 文本嵌入主流，归一化无关 |
| 权限基线 | 工具内强制注入 `current_role` | 让 LLM 传 role | 防止模型乱传 / 漏传 |
| Embedding 调用 | LiteLLM `openai/` 路由 | 直连 SiliconFlow OpenAIEmbeddings | 统一抽象，未来切换厂商成本低 |
| NER | LLM Prompt 驱动 | spaCy / hanlp / 自训 BERT | V1.0 快速验证链路 |
| NER 模型 | 非 reasoning 模型（flash） | reasoning 模型（pro） | 后者过度思考返回大量空 |
| 实体复合唯一键 | `(name, type)` | 仅 `name` | 同名异义词消歧 |
| 多跳查询 | Cypher `[r*1..N]` + 手动 clamp | APOC 过程 | 避免 APOC 依赖 |
| KG-04 联合查询 | 两个独立 Tool + system prompt 引导 | 封装组合 Tool | 满足 PRD"两步 tool_start"硬要求 |
| 中间件初始化 | lifespan fail-fast | 懒加载 | 启动期暴露问题 > 运行时挂 |

---

## 13. 已知限制 / 后续可改进点

### 13.1 RAG 方向
- **切片**：当前是字符级硬切。生产应换 `RecursiveCharacterTextSplitter` 或语义切片，避免在段落中部硬截造成上下文断裂
- **Reranker**：召回后无二次精排。可接 bge-reranker-v2-m3 / cohere-rerank-3 提升 top_k 精度
- **混合检索**：当前纯向量检索。可加 BM25 稀疏检索做混合（Milvus 2.6 支持原生混合检索）

### 13.2 KG 方向
- **关系抽取（RE）**：V1.0 只抽实体，关系全靠 MENTIONED_IN（实体-文档）。可加 LLM Prompt 或 SpERT 抽 RELATED_TO（实体-实体）
- **实体消歧**：同义词没有归并（"台风" / "热带气旋" / "typhoon" 是三个不同节点）。可加同义词词典或基于 Embedding 的实体链接
- **图谱可视化**：Neo4j Browser 自带，无需开发

### 13.3 Agent 方向
- **持久化 Checkpointing**：当前 Agent 状态每次请求重新构建。可接 `langgraph.checkpoint.SqliteSaver` 支持断线续跑
- **流式 Reasoning 透传**：DeepSeek-v4-pro 的 reasoning_content 当前没暴露给前端，可加单独的 SSE event 类型推送"思考过程"

### 13.4 工程方向
- **认证 / 用户体系**：当前权限模型硬编码 `"ALL"`。需要把 `get_current_role()` 改成从 JWT / Session 解析
- **alembic 迁移**：当前 PG 用 `create_all` 建表，生产应换 alembic 管理 schema 版本
- **可观测性**：建议接 OpenTelemetry，追踪 LLM 调用耗时 / Token 用量 / 工具调用链路

---

## 14. 参考文档

- [PRD（需求规格说明书）](TyAgent%20V1.0%20%28%E5%9F%BA%E7%A1%80%E5%BA%95%E5%BA%A7%29%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md)
- [进度文档 progress.md](progress.md)
- [Embedding 模型对比 embedding.md](embedding.md)
- [环境管理规范 environment_guide_zh.md](../environment_guide_zh.md)
- [CLAUDE 协作约定 CLAUDE.md](../CLAUDE.md)

---

## 15. 文档维护约定

**每次发生以下情况，必须更新本文档**：

1. **新增 PRD 模块完成** → 在第 3 章模块清单 + 对应技术细节章节补充
2. **关键技术决策变更**（如换 Embedding 模型、换图数据库）→ 更新第 12 章决策表 + 相关章节
3. **数据流变化**（如新增中间步骤、改变工具调用模式）→ 更新第 5 章时序图
4. **配置项增减** → 更新第 9 章配置表 + `.env.example`
5. **联调中发现的"坑"** → 补到第 12 章决策表的"理由"或第 13 章"已知限制"

> 这份文档不是写完就完事的，而是与代码同步演进的"活文档"。

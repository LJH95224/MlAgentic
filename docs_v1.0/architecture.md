# TyAgent 技术文档

> **文档定位**：描述项目当前已实现部分的**技术架构、数据流转、关键技术细节**，供后续接手者快速建立全局认知。
> **维护约定**：每次完成 PRD 模块或对已完成部分做实质性改动后，必须同步更新本文档。本文档与 [progress.md](progress.md) 互补 ——
> - progress.md：模块完成度 + 文件清单 + 验收记录（"做到哪了"）
> - 本文档：架构原理 + 技术决策 + 关键实现（"为什么这么做、怎么做的"）
>
> **版本演进**：
> - 第 1~12 章 = **V1.0 基础底座**（已完成，本节内容随 V1.0 联调通过后定稿）
> - 第 13 章 = **后续可改进点**（V1.0 阶段总结的技术债清单）
> - 第 16 章及之后 = **V1.5 数据管理层增量**（按 S0~S5 阶段完成后追加）

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

- [PRD（需求规格说明书）](PRD.md)
- [进度文档 progress.md](progress.md)
- [API 参考 api_reference.md](api_reference.md)
- [前端模块拆解 frontend_guide.md](frontend_guide.md)
- [变更日志 CHANGELOG.md](CHANGELOG.md)
- Embedding 模型对比见本文附录 A
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

---

# 第二部分 · V1.5 数据管理层增量

> **追加规则**：每完成一个 V1.5 阶段（S0~S5），把对应章节填实；未完成阶段保留占位 + "⏳ 待填写"。
> 同时刷新 [progress.md](progress.md) 的阶段表与本文档的章节状态。

## 16. V1.5 概览

**目标**：在 V1.0 ReAct 底座之上构建面向用户和运营的**数据管理层**，把系统从"能跑通"升级为"可用、可管理"。

**新增三条主线**：
- 会话生命周期管理（标题、摘要、CRUD、消息历史游标翻页）
- 多知识库空间（每库独立 Milvus Collection、Neo4j `kb_id` 隔离子图）
- 文件上传 + 异步入库管道（Celery 异步、轮询进度）

**新增技术栈**：

| 层 | 选型 | 用途 |
|---|---|---|
| 异步任务队列 | **Celery 5 + Redis 7** | 文件解析入库、会话标题/摘要异步生成 |
| 文件存储 | 本地磁盘 `{UPLOAD_DIR}/{kb_id}/{file_id}/` | V1.5 简易方案，后期可换 OSS |
| 文档解析 | **PyMuPDF / python-docx / Unstructured / markdown-it-py** | 多格式 → 纯文本 |
| 文本切片 | **LangChain RecursiveCharacterTextSplitter + tiktoken** | 按 KB 配置的 chunk_size / chunk_overlap 切 |

**统一响应格式（D4 全覆盖）**：V1.0 + V1.5 所有 REST 接口包成 `{code, message, data}`；SSE 报文沿用 V1.0 协议（`event/type/...`）不再二次包装。

详细拆分见 [v1.5_dev_plan.md](v1.5_dev_plan.md)。

## 17. S0 基础设施 ✅（2026-06-11）

### 17.1 异步任务架构

```
┌───────────────┐    enqueue (msgpack-free, JSON only)    ┌──────────────┐    consume    ┌──────────────┐
│ FastAPI app   │ ──────────────────────────────────────▶ │   Redis 7    │ ─────────────▶│ Celery Worker │
│ (HTTP 接入)    │                                          │ broker+result │               │ pool=solo (W) │
└───────────────┘                                          └──────────────┘               │ prefork  (L)  │
                                                                  ▲                       └──────────────┘
                                                                  │  poll result                 │
                                                                  └──────────────────────────────┘
```

- **生产者**：FastAPI 进程调 `task.delay()` 把任务写进 Redis
- **broker / backend**：同一个 Redis 实例（broker = 任务队列；backend = 结果存储）
- **消费者**：独立 Python 进程（`celery worker`），Windows 开发用 `--pool=solo`，Linux 生产用 `--pool=prefork`
- 三者**完全解耦**：worker 挂了不影响 FastAPI 收请求，只是任务堆积在 Redis；FastAPI 挂了 worker 也能继续消化在途任务

### 17.2 Celery 配置定型（[app/tasks/celery_app.py](../app/tasks/celery_app.py)）

| 配置项 | 取值 | 作用 |
|---|---|---|
| `task_acks_late` | `True` | Worker 异常时任务可重新入队，保证至少一次执行（PRD TASK-01 硬要求）|
| `worker_prefetch_multiplier` | `1` | 防 OOM 场景下多任务并发阻塞队列（PRD TASK-01 硬要求）|
| `task_serializer / result_serializer / accept_content` | `json` only | 禁 pickle，避免 RCE 风险 |
| `timezone` | `Asia/Shanghai` + `enable_utc=False` | 日志对时直观 |
| `result_expires` | 86400 秒 | 任务结果保留 24h，足够前端 2s 轮询 |
| `task_time_limit / soft_time_limit` | 30min / 25min | 兜底防文件解析挂死 |
| `broker_connection_max_retries` | 3 | Redis 不通时 `.delay()` 最多重试 3 次，避免无限卡死 |
| `broker_connection_timeout` | 4s | 单次连接超时 |

### 17.3 任务注册机制

`app/tasks/celery_app.py::_TASK_MODULES` 是显式列表：

```python
_TASK_MODULES = [
    "app.tasks.ping",
    # S3 阶段追加：app.tasks.ingest_task
    # S4 阶段追加：app.tasks.session_task
]
```

worker 启动时按列表 import 各模块，触发 `@celery_app.task` 装饰器完成任务注册。新增任务**必须在这里追加**，否则 worker 不会发现它。

### 17.4 PostgreSQL 新表结构

#### `chat_sessions` 扩展（PRD §5.1）

V1.0 字段：`id` / `created_at` / `metadata`
V1.5 新增：

| 字段 | 类型 | 默认 | 用途 |
|---|---|---|---|
| `title` | VARCHAR(100) | NULL | 异步任务自动生成（SES-07） |
| `summary` | TEXT | NULL | `/summarize` 接口触发（SES-08） |
| `summarized_at` | TIMESTAMPTZ | NULL | 最近一次摘要生成时间 |
| `updated_at` | TIMESTAMPTZ | NOW() + onupdate | 写消息时自动刷新 |
| `message_count` | INTEGER | 0 | 冗余计数，提升 SES-02 列表性能 |

#### `knowledge_bases` 新表（PRD §5.2）

10 字段 + 3 个 check 约束：

- `id` UUID PK
- `name` VARCHAR(128) **UNIQUE NOT NULL** + INDEX
- `description` TEXT NULL
- `embedding_dim` INT DEFAULT 4096（**创建后不可改**，业务层 PATCH 接口拦截）
- `chunk_size` INT DEFAULT 512（check: 128~2048）
- `chunk_overlap` INT DEFAULT 64（check: ≥0；业务层加额外 `< chunk_size/2` 校验）
- `status` VARCHAR(20) DEFAULT 'active'（active/building/error）
- `file_count` INT DEFAULT 0（冗余）
- `chunk_count` INT DEFAULT 0（冗余）
- `created_at` TIMESTAMPTZ DEFAULT NOW()

#### `kb_files` 新表（PRD §5.3）

14 字段，`kb_id` 外键级联：

- `id` UUID PK（同时作为 `document_id` 写 Milvus / Neo4j）
- `kb_id` UUID FK → `knowledge_bases.id` **ON DELETE CASCADE**
- `filename` VARCHAR(512)、`file_path` VARCHAR(1024)、`mime_type` VARCHAR(128)
- `file_size` BIGINT
- `status` VARCHAR(20) DEFAULT 'pending' + INDEX（pending/processing/completed/failed）
- `progress` INT DEFAULT 0（0-100，对应 FILE-03 各阶段）
- `chunk_count` / `entity_count` INT DEFAULT 0
- `error_message` TEXT NULL（failed 时填）
- `celery_task_id` VARCHAR(255) NULL（删除时 revoke 用）
- `created_at` / `completed_at` TIMESTAMPTZ
- `knowledge_base` relationship `lazy="raise"`（强制显式 selectinload，防 N+1）

### 17.5 关键设计决策

1. **不写迁移脚本（用户确认数据库可清空）**：靠 `Base.metadata.create_all` + 首次启动前手动 `DROP DATABASE; CREATE DATABASE`。后续真需要复杂迁移再上 Alembic
2. **Celery broker/backend 缺省复用 REDIS_URL**：`Settings.effective_celery_broker_url` 是 derived property，避免业务层散落 `or` 兜底
3. **Windows `127.0.0.1` 锁死**：`Settings.redis_url` 默认 `redis://127.0.0.1:6379/0`，避免 Docker Desktop vpnkit IPv6 转发丢包坑（联调阶段踩到，文档已记）
4. **`from-import` 遮蔽子模块**：`app/tasks/__init__.py` 用 `from app.tasks.celery_app import celery_app` 后，子模块对象被同名 Celery 实例覆盖；测试里 `importlib.reload` 必须从 `sys.modules["app.tasks.celery_app"]` 取真模块对象
5. **broker 连接限重试 3 次**：`broker_connection_max_retries=3` + `broker_connection_timeout=4`，避免 Redis 不通时 `.delay()` 无限卡死，秒级报错给上层
6. **`KbFile.knowledge_base` 关系用 `lazy="raise"`**：强制业务层显式 selectinload，防 N+1 在列表查询中暴露

### 17.6 docker-compose 新增

```yaml
redis:
  image: redis:7-alpine
  command: ["redis-server", "--appendonly", "yes", "--save", "60", "1"]
  ports: ["6379:6379"]
  volumes: ["d:/dockerVolumes/redis/data:/data"]   # AOF 持久化
```

与现有 milvus / neo4j 并列，统一 `d:/dockerVolumes/` 卷目录约定。

## 18. S1 会话管理 · ⏳ 待填写

完成后填入：
- 统一响应 `ApiResponse` 设计 + 异常处理器映射表
- SES-09 上下文窗口裁剪策略实测效果
- 游标翻页 SQL 最终形态

## 19. S2 知识库管理 · ⏳ 待填写

完成后填入：
- Collection 命名规则 `kb_{hex}` 落地代码位置
- KB-05 删除回滚策略实测结果
- Milvus Schema 扩展字段 `kb_id` 的取值实例
- Neo4j `kb_id` 属性的索引/查询性能

## 20. S3 文件上传与异步入库 · ⏳ 待填写

完成后填入：
- 文件入库管道完整时序图（progress 0→100）
- 解析器分发表与每种格式的踩坑记录
- Celery 任务幂等性与重试策略实测
- NER 软失败实测案例

## 21. S4 会话标题与摘要 · ⏳ 待填写

完成后填入：
- 触发时机判定逻辑
- Prompt 最终形态
- 长摘要 map-reduce 策略（若需要）

## 22. S5 KB 关联对话 · ⏳ 待填写

完成后填入：
- `kb_ids` 跨 Collection 合并重排序的代码位置与性能基线
- 端到端 smoke 脚本走通的完整链路
- V1.5 整体验收结论

---

# 第三部分 · V2.0 Hermes 增量

> **范围**：本部分仅记录 V2.0 在 V1.5 基础上的**增量**架构内容；V1.5 数据管理层架构（KB / 文件 / 异步任务）见第二部分。
> **写作约定**：每节 1~3 段说明"做了什么"+"为什么这么做"，关键代码用 `[文件名](../app/xxx/yyy.py)` 链接到源文件，关键设计决策从 [progress.md](progress.md) 各 T 段提炼，不重复细节实现描述。
> **配套文档**：[v2_api_reference.md](v2_api_reference.md)（接口契约）/ [v2_frontend_guide.md](v2_frontend_guide.md)（前端联调）/ [v2_dev_plan.md](v2_dev_plan.md)（阶段拆分）。

## 23. V2.0 概览

### 23.1 迭代目标

V2.0 代号 Hermes，核心目标：把 RAG 从"能跑通"升级为"效果可信赖"。在 V1.5 数据管理层（会话/知识库/文件入库）之上，对 RAG 链路三个关键节点分别攻坚：

- **文档切得不好** → 智能文档处理（IDP）：结构感知解析 + 结构感知切片 + 双层索引
- **检索召回不准** → 混合检索引擎（HRE）：BM25 + RRF + Reranker + Query 改写 + Graph RAG 锚定
- **模型拿着错误上下文生成幻觉** → 答案溯源与置信度（CHC）：Citation 注入/解析 + 答案自检 + 置信度评分

同时配套可观测性 Trace（OBS）和 RAGAS 评估（EVA），让效果改进从主观感受变为可量化数据。

### 23.2 与 V1.5 的差异速览

| 维度 | V1.5 现状 | V2.0 目标 |
|------|-----------|-----------|
| 文档切片 | RecursiveCharacterTextSplitter 按字符数机械切割 | 结构感知切片 + 双层索引（段落摘要 + 细粒度 Chunk） |
| 检索策略 | 纯向量检索（COSINE 相似度） | 向量 + BM25 混合检索 + RRF 融合 + Reranker 精排 |
| 知识图谱 | 独立 Graph Tool，Agent 自行决策是否调用 | 融入检索主链路：Query NER → 图谱锚定 → 向量过滤 |
| 答案溯源 | 无 | Citation 注入 + source_citations 结构化返回 |
| 幻觉控制 | System Prompt 约束 | 答案自检节点 + 置信度评分 + 低置信度预警 |
| 对外接口 | 多个独立接口，开发者需自行组装 | 统一 /v2/query 封装全链路，分层子接口支持深度定制 |
| 效果评估 | 无 | 内置 RAGAS 指标评估接口 |
| 可观测性 | 无 Trace | 完整 agent_traces 表 + Trace 查询接口 + 聚合统计 |

### 23.3 总体架构图（V2 检索全链路）

```
用户 Query
    │
    ▼
┌─────────────────────────────────────┐
│  Query 预处理层                      │
│  ├─ Query 改写（none / HyDE / multi）│
│  └─ NER 实体识别（Query → 实体列表） │
└─────────────────┬───────────────────┘
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
┌──────────────┐   ┌────────────────────┐
│  Neo4j       │   │  并行检索层         │
│  单跳图谱查询 │   │  ├─ Milvus 稠密向量 │
│  → 实体标签  │   │  └─ BM25 稀疏向量   │
└──────┬───────┘   └─────────┬──────────┘
       │                     │
       └──────────┬──────────┘
                  ▼
        ┌─────────────────┐
        │  RRF 融合重排序  │
        │  k=60 双路融合   │
        └────────┬────────┘
                 ▼
        ┌─────────────────┐
        │  Reranker 精排   │
        │  + 相关性过滤    │
        └────────┬────────┘
                 ▼
        ┌─────────────────────────────┐
        │  Context 组装               │
        │  ├─ Chunk 编号注入 [1][2]   │
        │  └─ 来源/元数据携带         │
        └────────┬────────────────────┘
                 ▼
        ┌─────────────────┐
        │  LLM 生成        │
        └────────┬────────┘
                 ▼
        ┌──────────────────────────────┐
        │  答案后处理层                 │
        │  ├─ Citation 解析            │
        │  ├─ 答案自检（Faithfulness） │
        │  └─ 置信度评分               │
        └────────┬─────────────────────┘
                 ▼
        结构化响应（answer + citations + confidence + trace_id）
```

### 23.4 阶段交付概览（T0~T12）

V2.0 按 13 个阶段交付（T0~T12），按 PRD §8 优先级链推进。详见 [progress.md V2.0 总表](progress.md#v20-hermes--专业级-rag-引擎进行中-)。

| 阶段 | 模块 | 优先级 | 完成日期 |
|------|------|--------|----------|
| T0 | 基础设施扩展（BM25 Schema / Trace 表 / Eval 表） | P0 | 2026-06-12 |
| T1 | IDP-01/02/06 结构感知解析 + 切片 + 入库管道 | P0 | 2026-06-12 |
| T2 | HRE-03/04 BM25 + RRF 融合 | P0 | 2026-06-12 |
| T3 | OBS-01/02 Trace 采集 + 查询接口 | P0 | 2026-06-12 |
| T4 | HRE-05 Reranker 精排 | P1 | 2026-06-15 |
| T5 | CHC-01/02 Citation 注入 + 解析 | P1 | 2026-06-15 |
| T6 | UQA-01 统一查询接口 /v2/query | P1 | 2026-06-15 |
| T7 | IDP-03/04/05 表格描述 + 双层索引 + 文档元数据 | P2 | 2026-06-15 |
| T8 | HRE-01/02/06 Query 改写 + NER + 配置项 | P2 | 2026-06-15 |
| T9 | CHC-03/04 置信度 + 答案自检 | P2 | 2026-06-15 |
| T10 | UQA-02/03/04 分层子接口 | P3 | 2026-06-16 |
| T11 | EVA-01/02/03 RAGAS 评估 | P3 | 2026-06-16 |
| T12 | OBS-03 聚合统计 | P4 | 2026-06-16 |

## 24. T0+T1+T7 智能文档处理（IDP）

### 24.1 V2 KB Collection Schema（15 字段）

[app/rag/schema.py](../app/rag/schema.py) 的 `build_v2_kb_collection_schema()` 在 V1.5 8 字段基础上新增 7 个字段，共 15 字段：

| 字段 | DataType | 说明 |
|------|----------|------|
| V1.5 继承字段 | (8 个) | chunk_id / vector / document_id / content / allowed_roles / entity_tags / metadata / kb_id |
| **heading_path** | ARRAY<VARCHAR>(cap=10) | 标题层级路径（IDP-02） |
| **block_type** | VARCHAR(32) | paragraph / table / code / list / table_description |
| **page_number** | INT32 | PDF 场景页码 |
| **position_index** | INT32 | 文档内顺序位置 |
| **parent_chunk_id** | VARCHAR(64) | 细粒度 Chunk 指向摘要 Chunk |
| **is_summary** | BOOL | 是否为双层索引摘要层 |
| **sparse_vector** | SPARSE_FLOAT_VECTOR | BM25 稀疏向量（T2 启用） |

稀疏向量索引走 `SPARSE_INVERTED_INDEX + BM25` 度量。V2 Schema 独立于 V1.5（`create_v2_kb_collection`），互不影响。

### 24.2 结构感知解析（StructuredBlock）

[app/ingest/parser.py](../app/ingest/parser.py) 的 `StructuredBlock` 数据类（block_id / block_type / heading_path / content / page_number / position_index），搭配 4 个结构感知解析器：

- **PDF**：PyMuPDF 按坐标和字体大小/粗体推断标题层级
- **DOCX**：python-docx 直接读 `paragraph.style.name` 判断 heading 级别
- **Markdown**：markdown-it-py 解析 ATX 标题 + 代码块（需 `.enable("table")` 显式启表格）
- **TXT**：连续空行分段，无层级结构

V1.5 的 `parse_document()` 完全不动，V2 新增独立的 `parse_document_structured()` 入口。

### 24.3 结构感知切片（StructuredChunk）

[app/ingest/structured_splitter.py](../app/ingest/structured_splitter.py) 的 `StructuredChunk` + `split_structured_blocks()` 按优先级切片：

1. **代码块/表格**：整块保留为一个 chunk，不可切断
2. **标题段落组**：标题 + 下属正文合并，超出 `chunk_size` 时在段落边界切
3. **普通段落**：`RecursiveCharacterTextSplitter` 兜底

`heading_path` 在标题+段落组合 chunk 时取段落块的（含标题自身），确保路径完整。

### 24.4 11 步入库管道

[app/tasks/ingest_task.py](../app/tasks/ingest_task.py) 的 `_main` 函数串联 11 步（V1.5 7 步→ V2 11 步）：

```
Step  1: status=processing, progress=0
Step  2: 结构感知解析（IDP-01）                    [progress=15]
Step  3: 结构感知切片（IDP-02）                    [progress=25]
Step  4: 表格描述生成（IDP-03）                    [progress=30]
Step  5: 粗粒度摘要生成（IDP-04 双层索引）          [progress=40]
Step  6: 文档元数据提取（IDP-05）                  [progress=45]
Step  7: 批量向量嵌入                              [progress=65]
Step  8: 写入 Milvus V2（15 字段）                 [progress=80]
Step  9: NER 实体抽取 → 写入 Neo4j                [progress=92]
Step 10: BM25 索引确认（Milvus 自动）              [progress=97]
Step 11: status=completed, progress=100
```

V1.5 版 `ingest_task_v1.py` 已归档保留供参考。

### 24.5 三类 chunk 的 chunk_index 全局唯一策略

入库产生三类 chunk：fine（细粒度）、table_description（表格描述）、coarse（粗粒度摘要）。`chunk_index` 分配策略：

- fine：用 splitter 给的 0..N-1
- table_description：从 `len(fine)` 起递增
- coarse：从 `len(fine) + len(td)` 起递增

`_make_chunk_id_int(document_id, index)` 用 SHA256 → INT64 保证幂等 upsert 不冲突。`parent_chunk_id` 存 INT64 整数字符串（VARCHAR 64），Milvus 检索时可直接做 `expr` 子查询。

### 24.6 表格描述 / 双层索引 / 文档元数据（IDP-03/04/05）

- **IDP-03**（[app/ingest/table_description.py](../app/ingest/table_description.py)）：LLM 对每张表格生成自然语言描述作为额外 chunk（`block_type="table_description"`），参与向量检索但不参与 BM25（避免描述词汇干扰精确匹配）。`Semaphore(idp_concurrency)` 限并发 + `wait_for(idp_llm_timeout_s)` 硬超时。
- **IDP-04**（[app/ingest/dual_layer.py](../app/ingest/dual_layer.py)）：按 `heading_path[:-1]` 父级标题聚合 fine chunks，LLM 生成摘要作为粗粒度 chunk（`is_summary=True`）。fine chunks 的 `parent_chunk_id` 用 `dataclasses.replace` 回填指向粗 chunk。
- **IDP-05**（[app/ingest/doc_metadata.py](../app/ingest/doc_metadata.py)）：LLM 提取 doc_type / doc_date / language / key_topics / summary_brief 写入 `kb_files.doc_metadata` JSONB + `summary_brief` Text。

三步均遵循软失败原则：单步失败不阻断主链路，沿用 V1.5 NER 软失败模式。

## 25. T2+T4+T8 混合检索引擎（HRE）

### 25.1 BM25 稀疏向量（Milvus 内置 Function）

Milvus 2.5+ 原生支持稀疏向量 + BM25，无需 jieba 手动分词。在 Schema 中声明 `Function(content→sparse_vector, BM25)`，插入时 Milvus 自动分词+计算稀疏向量。查询时直接传原始文本。

关键配置：`content` 字段需 `enable_analyzer=True`（BM25 Function 前提），索引参数 `bm25_k1=1.2`、`bm25_b=0.75`、`drop_ratio_build=0.2`（建索引时丢弃低频词后 20%，减小体积）。

### 25.2 RRF 融合（dense + BM25）

[app/rag/hybrid_retriever.py](../app/rag/hybrid_retriever.py) 的 `hybrid_search()` 使用 Milvus `AnnSearchRequest` + `RRFRanker` 一次性查询双路：

- dense 检索（HNSW + COSINE）：语义相似度
- BM25 检索（SPARSE_INVERTED_INDEX + BM25）：精确词频匹配

RRF 参数 k=60（学术标准值，可通过 `RRF_K` 配置调整）。降级策略：BM25 失败 → 纯向量检索；`bm25_enable=False` → 纯向量检索。

### 25.3 Reranker 精排（在线 API + Noop 降级）

[app/rag/reranker.py](../app/rag/reranker.py) 的抽象 `BaseReranker` + 两种实现：

- **LiteLLMReranker**：走 `litellm.arerank` API，支持 SiliconFlow/Cohere/Jina 格式。`Semaphore(5)` 限并发，超时复用 `litellm_timeout`。失败时降级返回原顺序（score=0 标记）。
- **NoopReranker**：原顺序 + score=1.0（表示"不做精排，信任原排序"）。当前生产配置 `RERANKER_TYPE=none`，详见 [eval_a1_reranker_tuning.md](eval_a1_reranker_tuning.md)（A.1 实验表明 Qwen3-Reranker-8B 当前弊大于利）。

过滤规则：`hybrid_search` 取候选 `2*top_k` → reranker 精排 → 过滤低于 `similarity_threshold`（默认 0.3）的 chunk；过滤后不足 3 条时补到 3 条。

### 25.4 Query 改写（none / HyDE / multi_query）

[app/rag/query_rewriter.py](../app/rag/query_rewriter.py) 的 `rewrite_query` 三策略：

- **none**：零 LLM 调用，直接用原 query
- **HyDE**：LLM 生成 100~200 字"假设性答案"，用其向量替代 Query 向量做检索
- **multi_query**：LLM 一次生成 N 个子查询，每路独立检索后 RRF 二次融合（按 chunk_id 去重 + rank-based 重算分数 `score = Σ 1/(k + rank_i)`，同 chunk 多路命中分数累加）

软失败原则（与 NER 同款）：异常/超时返 noop，不阻断主链路。

### 25.5 Query NER + Graph 锚定

[app/rag/query_ner.py](../app/rag/query_ner.py) 薄封装 [app/kg/ner.py](../app/kg/ner.py) 的 `run_ner`，追加：

- Neo4j 单跳锚定（`max_hops=1`），Semaphore(5) 限流
- 锚定结果把**起点实体本身也加入 tags**（即使无邻居也有过滤价值）
- UTF-8 字节安全截断（中文 3 字节/字），上限 50 标签
- 硬超时 `query_ner_timeout_s` / `graph_anchor_timeout_s`

Graph RAG 默认启用（`graph_rag_enable=True`），Query 无实体或实体不在图谱时自动短路。

### 25.6 三层配置合并（API > KB > settings）

[app/rag/retrieval_config.py](../app/rag/retrieval_config.py) 的 `resolve_options` 函数实现三层合并：

```
API options（QueryOptions） > kb.retrieval_config（JSONB） > 全局 settings
```

任一上层字段为 None 时回落下一层。`ResolvedRetrievalOptions` 冻结数据类包含：top_k / similarity_threshold / bm25_enable / reranker_enable / query_rewrite / enable_graph_rag / enable_faithfulness_check / rrf_k / rerank_top_n。

`query_rewrite` 校验在 `resolve_options` 入口用 `BusinessError(40011)` 显式拦截，而非 Pydantic validator（避免被 `ValidationError` 重打包成 40001）。

## 26. T5+T9 答案溯源与置信度（CHC）

### 26.1 Citation 注入与解析

[app/rag/citation.py](../app/rag/citation.py) 两个核心函数：

- `build_context_with_citation(chunks)`：生成 `[1] 来源：xxx.pdf（第3页）\n内容：...` 格式的 context 文本
- `build_citation_system_prompt()`：注入引用规则，引导 LLM 用 `[N]` 标注来源
- `parse_citations(answer_text, chunks)`：正则 `\[(\d+)\]` 抽取引用编号 → 去重保序 → 映射回 chunks → 输出 `CitationItem`（chunk_id / document_name / page_number / heading_path / snippet / rerank_score）

关键细节：Unicode 中文引号 `"` `"` 替代 ASCII 防止 SyntaxError；越界编号静默忽略（LLM 偶会编造编号）；未引用 chunk 不出现在 source_citations 中。

### 26.2 置信度评分（CHC-03）

[app/rag/confidence.py](../app/rag/confidence.py) 的 `compute_confidence` 纯函数，按 PRD §540 公式：

```
confidence = weighted_avg(rerank_scores of cited chunks) × coverage_factor × (1 - hallucination_penalty)
```

- `weighted_avg`：被引用 chunk 等权算术平均
- `coverage_factor`：`len(cited) / top_k`（上限 1.0）
- `hallucination_penalty`：自检失败比例（默认 0.0，自检关闭/失败时不惩罚）

`confidence < 0.5` 时自动填 `low_confidence_warning` 预警文案。`ConfidenceScore.breakdown` 透出三因子原值便于 trace 排查。

### 26.3 答案自检 LLM as Judge（CHC-04）

[app/rag/faithfulness.py](../app/rag/faithfulness.py) 的 `check_faithfulness`：

- LLM as Judge：提取答案中关键事实声明 → 逐一比对该声明是否在检索 chunk 中有文本支撑
- 输出 JSON 数组 `[{"claim": "...", "status": "supported" | "unverified", "source_text": "..."}]`
- JSON 数组/对象包装兼容（`{"claims": [...]}` 也接受）
- `wait_for(faithfulness_check_timeout_s)` 硬超时
- `response_format` 不强制 `json_object`（多数模型不支持 array 类型的 response_format）
- unverified 处理：在 answer 末尾追加 `⚠ 以下事实未在检索内容中找到明确支撑：- claim1` 警告清单

### 26.4 三态状态机：ok / skipped / disabled

`faithfulness_check` 字段三态：

- **"ok"**：自检正常跑通，返回 claims 列表
- **"skipped"**：异常/超时/JSON 解析失败时软降级，不惩罚 confidence（`penalty=0.0`），不阻断主链路
- **"disabled"**：`enable_faithfulness_check=False` 时不调 LLM，`_DISABLED_RESULT` 模块级常量直接返回

## 27. T3+T12 可观测性 Trace

### 27.1 Tracer 上下文管理器

[app/observability/tracer.py](../app/observability/tracer.py) 的 `Tracer` 类：

```python
async with Tracer(session_id=sid, kb_id=kb_id) as t:
    with t.step("query_rewrite", step_input={"query": "..."}):
        result = await rewrite_query(query)
    with t.step("retrieve", step_input=...):
        results = await hybrid_search(...)
```

- `trace_id` 在入口生成（`uuid4().hex[:16]`），贯穿全链路所有 step
- 每个 `step()` 上下文管理器自动计时（`time.perf_counter()`），记录 step_latency_ms
- `trace_enable=False` 时所有操作短路，零开销
- 退出 `Tracer` 上下文时批量写入 PG `agent_traces` 表

### 27.2 agent_traces 表 + 嵌套 step 查询

`agent_traces` 表（[app/models/agent_trace.py](../app/models/agent_trace.py)）13 字段：trace_id / session_id / kb_id / step_type / parent_step / step_latency_ms / total_latency_ms / step_input(JSONB) / step_output(JSONB) / model_name / token_count / error_message / created_at。

Trace 查询端点（[app/api/v2/endpoints/traces.py](../app/api/v2/endpoints/traces.py)）：`GET /api/v2/traces/{trace_id}` + `GET /api/v2/traces/sessions/{session_id}/traces`。先查根步骤再 count 每条步骤数，避免大 join。

### 27.3 query_analytics 快照表

[app/models/query_analytics.py](../app/models/query_analytics.py) 14 字段：trace_id / session_id / kb_id / total_latency_ms / confidence / low_confidence / graph_rag_triggered / bm25_contributed / faithfulness_check_triggered / total_tokens / react_steps / has_error / created_at。

设计决策：快照表而非实时聚合 agent_traces。agent_traces 的 step_input/step_output 是 JSONB，从中聚合指标性能差；快照表每次查询写一行扁平指标，SQL 聚合简单高效。工具使用率用 bool + AVG（`AVG(bm25_contributed)` = 触发率）。

### 27.4 单 SQL 聚合统计

[app/api/v2/endpoints/analytics.py](../app/api/v2/endpoints/analytics.py) 的 `GET /api/v2/analytics` 端点：

- 单条 SELECT 完成所有 10 个聚合指标（COUNT / AVG / SUM / CASE WHEN）
- 支持 `start_date` / `end_date` / `kb_id` 过滤
- 默认 7 天时间范围
- 响应 < 500ms

[app/observability/analytics_writer.py](../app/observability/analytics_writer.py) 的 `write_analytics_snapshot` 在每次 `/v2/query` 结束时调用，从 `Tracer.steps` 提取工具使用 bool / Token 数 / 步骤数 / 错误，写入一行快照。

### 27.5 关键 bugfix：writer 内部 commit

v2_smoke 集成时暴露 OBS-03 快照丢失 bug：`write_analytics_snapshot` 仅 `flush` 不 `commit`，注释说"由调用方统一 commit"但调用方从未 commit → 请求结束 `AsyncSession` 关闭时隐式 rollback → 数据永远不落库。

修复：writer 内部独立 `commit()` + `rollback()` 兜底，符合项目惯例（chat/kb/session/file/evaluations 14 处全显式 commit）。commit 失败仍走 try/except warning 不阻断主链路。

## 28. T6+T10 统一查询接口（UQA）

### 28.1 /v2/query 主链路（7 步 trace）

[app/api/v2/endpoints/query.py](../app/api/v2/endpoints/query.py) 的 `v2_query` 端点串联完整链路，`Tracer` 包裹 7 步自动计时埋点：

```
1. query_rewrite   → rewrite_query(query, resolved.query_rewrite)
2. query_ner       → extract_query_entities(query)
3. graph_anchor    → anchor_to_graph(entities, kb_ids)
4. retrieve        → hybrid_search(text, top_k, entity_tags, ...)
5. build_context   → build_context_with_citation(results)
6. generate        → litellm.acompletion(context + system prompt)
7. citation_parse  → parse_citations(answer, chunks)
   （T9 追加）faithfulness_check + compute_confidence
```

`top_k` 放在 `QueryOptions` 嵌套字段而非顶层，便于未来扩展 stream / reranker_enable / bm25_enable 等。总耗时取整 ms。

### 28.2 检索空兜底 + LLM 失败兜底 + 整体超时

三类兜底保证 API 稳定性：

1. **检索为空**：不调 LLM，直接返回友好文案 + `confidence=0.0` + `trace_id` 透传
2. **LLM 失败**：返回 `（回答生成失败）` + `faithfulness_check="skipped"`
3. **整体超时**：`asyncio.wait_for(timeout=settings.query_total_timeout_s)` 硬超时兜底（默认 120s）

三层超时防护：LiteLLM 内部 timeout → `asyncio.wait_for` 步骤级硬超时 → `v2_query` 整体硬超时。任一层触发均有友好文案返回，不无限挂起。

### 28.3 三个分层子接口：/retrieve、/generate、/rerank

[progress.md T10](progress.md#t10--分层子接口-2026-06-16) 详细记录了三个子接口的独立 Schema 与端点实现：

- **`POST /api/v2/retrieve`**（[app/api/v2/endpoints/retrieve.py](../app/api/v2/endpoints/retrieve.py)）：纯检索，不调 LLM。返回 chunks 含 vector_score / bm25_score / rrf_score / rerank_score 四个分数字段。延迟目标 < 1s。
- **`POST /api/v2/generate`**（[app/api/v2/endpoints/generate.py](../app/api/v2/endpoints/generate.py)）：开发者传入自定义 context，跳过检索，只走 LLM + Citation + 自检 + 置信度。不触发 Milvus/Neo4j。`context_chunks` 至少 1 条，否则返 42201。
- **`POST /api/v2/rerank`**（[app/api/v2/endpoints/rerank.py](../app/api/v2/endpoints/rerank.py)）：query + candidates → rerank_score 降序。降级返回原顺序（score=0.0）。独立复用 Hermes 的 Reranker 能力。

三个子接口完全独立端点 + Schema，不复用 /v2/query 的 QueryRequest/QueryResponse，因为语义差异大。

## 29. T11 RAGAS 评估管道（EVA）

### 29.1 worker 进程内 import 而非 HTTP 调

[app/rag/eval_runner.py](../app/rag/eval_runner.py) 的 `run_single_query_for_eval` 直接 import `hybrid_search` / `generate_answer` 等内部函数，零网络依赖。评估 worker（Celery 子进程）与 uvicorn 不在同一进程，httpx 调用要求 worker 能解析到 host:port，部署复杂。

关键约束：不写 Trace（评估场景每题写 ~7 条 step 会污染 agent_traces 表）；不调 `faithfulness_check`（ragas 自身会算 faithfulness 指标）；multi_query 评估期禁用（每题烧 2~4 次 LLM 改写 token，性价比差）。

### 29.2 LiteLLM 经 LangChain ChatOpenAI(base_url=) 适配 ragas

[app/rag/ragas_evaluator.py](../app/rag/ragas_evaluator.py) 的 `evaluate_with_ragas`：

- LLM 适配：`LangchainLLMWrapper(ChatOpenAI(model=..., base_url=..., api_key=...))`，LiteLLM 完全兼容 OpenAI 协议
- Embedding 适配：`LangchainEmbeddingsWrapper(OpenAIEmbeddings(model=..., base_url=...))`
- 剥掉厂商前缀（`deepseek/deepseek-v4-flash` → `deepseek-v4-flash`），ChatOpenAI 不需要 LiteLLM 的 `/` 前缀路由

4 项核心指标（faithfulness / answer_relevancy / context_precision / context_recall），`overall_score` 为算术均值。

### 29.3 软失败设计（ragas 不可用 → summary 全 None）

- **ragas 模块懒加载**：`from ragas import evaluate` 推迟到 `evaluate_with_ragas` 函数内；环境无 ragas → 整批返 summary 全 None + error 字段，不阻断 EvalTask 落库
- **NaN / Inf → None**：单题失败返 NaN，用 `_to_float_or_none` 清洗后写 JSONB，避免 PG 序列化 NaN 报错
- **eval_dataset 完整存 JSONB**：question + ground_truth 原样保留；评估期生成的 answer + contexts 也写入 `eval_result.samples`，便于复跑指标
- **超 100 题硬拒绝**：返 40013，保留 `EVAL_MAX_QUESTIONS` 配置可调（最大 500）

A.1 实验验证了 4 组对比（baseline / thresh_0.3 / thresh_0.1 / thresh_0.0），详见 [eval_a1_reranker_tuning.md](eval_a1_reranker_tuning.md)。

## 30. V2.0 关键技术决策汇总

### 30.1 与 V1.5 不同的工程决策

| 决策点 | 选择 | 替代方案 | 理由 |
|--------|------|----------|------|
| BM25 方案 | Milvus 内置 Function | jieba 手动分词 + 稀疏向量 | 零编码，插入自动分词，查询传原文本 |
| RRF 融合 | Milvus `AnnSearchRequest` + `RRFRanker` | 应用层排序融合 | 双路一次性查询，Milvus 内部融合更高效 |
| Reranker | NoopReranker 生产态（score=1.0） | Qwen3-Reranker-8B | A.1 实验表明当前 Reranker 弊大于利（overall -0.231） |
| Query 改写校验 | `resolve_options` 入口拦截 | Pydantic field_validator | 避免被 ValidationError 重打包成 40001 |
| 三类 chunk 索引 | 全局连续递增 | 各自从 0 开始 | 幂等 upsert 不冲突，`_make_chunk_id_int` 统一管理 |
| Trace 写入 | 同步短连接（T3），T12 仍同步 | 异步写入 Redis 队列 | V2 阶段简化，写入失败仅 warning 不阻塞主链路 |
| 快照表 | query_analytics 扁平表 | 实时聚合 agent_traces JSONB | SQL 聚合简单高效，每次查询写一行 |
| RAGAS 集成 | worker 进程内 import | HTTP 调用 /v2/query | 零网络依赖，部署简单 |
| 整体超时 | `asyncio.wait_for` 硬超时 | 仅靠 LiteLLM timeout | 三层防护：LLM 层 → 步骤层 → 请求层 |
| 答案自检三态 | ok / skipped / disabled | 仅 bool | 区分"用户没启用" vs "启用了但失败" |
| Milvus 同步调用 | `asyncio.to_thread` 包 gRPC | 直接同步调用 | 防止阻塞事件循环影响其他并发请求 |
| NER 仅对 fine chunks | 表格描述/粗摘要跳过 | 全部跑 NER | 二次合成文本不应抽新实体；补空 entities 对齐 |

### 30.2 已知限制 / 后续可改进

- **Reranker 选型待定**：A.1 实验 Qwen3-Reranker-8B 结果不理想（overall 比 baseline 低 0.100~0.288），当前 `RERANKER_TYPE=none`。后续应测试 bge-reranker-v2-m3 或在文档量增大后重测
- **多 KB 检索取第一个 KB 的 retrieval_config**：本期限制；后续可演进为 union/优先级/fan-out 策略
- **评估期禁用 multi_query**：多路改写烧 token 且无性价比，但长远看应支持可选启用
- **query_analytics 简化 Token 统计**：仅记录 total_tokens，不区分 input/output；PRD 要求 total_input / total_output 分开统计
- **Milvus 同步 gRPC 阻塞事件循环**：虽已用 `asyncio.to_thread` 包装，但理想方案应换异步 gRPC 客户端
- **agent_traces 无数据保留策略**：当前无自动清理机制，大量查询后表体积会持续增长
- **Graph RAG 锚定仅单跳**：`max_hops=1` 防止图谱爆炸；复杂多跳场景需单独调优
- **Embedding 模型固定 4096 维**：换模型需重建 Collection，成本较高

---

## 附录 A · Embedding 模型选型对比

> 当前生产使用 **Qwen/Qwen3-Embedding-8B**（4096 维），Milvus `knowledge_chunks.vector` 与之绑定。换模型需重建 Collection 与索引。

| 模型 | 参数量 | 输出维度 | 上下文 | 多语言 | MTEB 得分 | 备注 |
|---|---|---|---|---|---|---|
| **Qwen3-Embedding-8B** ⭐ | 8B | 32–4096 | 32K | 100+ | 70.58（2025-06，多语言榜第一） | 当前生产模型；纯文本，指令感知 |
| Qwen3-VL-Embedding-8B | 8B | 32–4096 | 32K | 30+ | — | 多模态版（文本 + 图像），暂未启用 |
| Qwen3-Embedding-4B | 4B | 32–2560 | 32K | 100+ | 69.45 | 效率/效果平衡，备选 |
| Qwen3-Embedding-0.6B | 0.6B | 32–1024 | 32K | 100+ | 64.33 | 轻量级备选 |

**统一特征**：Apache 2.0 许可、Dense Transformer 架构、支持自定义输出维度、支持指令感知（task instruction）。

**换模型 checklist**：
1. 更新 `core/config.py` 的 `EMBEDDING_MODEL` 与 `EMBEDDING_DIM`。
2. 重建 Milvus Collection `knowledge_chunks`（HNSW + COSINE 索引）。
3. 全量重跑文件入库管道（Celery `ingest_document` 任务）。
4. 重跑 RAGAS 评估对比指标，确认无回归。

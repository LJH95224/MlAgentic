# TyAgent V1.5 · 需求规格说明书 (PRD)

**文档版本：** V1.5 Draft  
**项目定位：** 会话管理 · 知识库管理 · 文件上传入库 全链路完善  
**核心架构：** FastAPI · LangGraph · LiteLLM · PostgreSQL · Milvus · Neo4j · Celery · Redis  
**基于版本：** TyAgent V1.0（ReAct Agent 基础底座）  
**文档状态：** Draft · 待评审

---

## 目录

1. [产品概述](#1-产品概述)
2. [系统架构概览](#2-系统架构概览)
3. [功能需求详细拆分](#3-功能需求详细拆分)
   - 3.1 会话管理模块
   - 3.2 知识库管理模块
   - 3.3 文件上传与管理模块
   - 3.4 异步任务队列模块
4. [API 接口总览](#4-api-接口总览)
5. [数据库字典设计](#5-数据库字典设计)
6. [核心业务规则与约束](#6-核心业务规则与约束)
7. [错误码与响应规范](#7-错误码与响应规范)
8. [后续规划：智能体能力升级方案](#8-后续规划智能体能力升级方案)

---

## 1. 产品概述

### 1.1 V1.5 定位与背景

TyAgent V1.0 已完成基于 LangGraph 的 ReAct 推理循环、Milvus 向量检索（Agentic RAG）与 Neo4j 知识图谱的全链路联调，具备了智能体基础底座能力。V1.5 的目标是在此底座之上，全面打通面向最终用户和运营人员的**数据管理层**，使系统从"能跑通"升级为"可用、可管理"。

V1.5 聚焦以下三条主线：

- **会话生命周期管理：** 对话历史的持久化、标题自动生成、全文摘要，以及会话级别的增删改查。
- **知识库多空间管理：** 支持创建多个独立知识库（KnowledgeBase），不同知识库的文档、向量切片、图谱实体完全隔离，互不干扰。
- **文件上传与异步入库：** 提供文件上传接口，支持 PDF / Word / Markdown / TXT 等格式，上传后通过异步任务队列完成解析、切片、嵌入、向量写入、实体抽取、图谱写入的全流程自动化。

### 1.2 V1.5 不做的事（边界）

- 不引入用户认证与权限系统（JWT / RBAC），`allowed_roles` 字段预留不激活。
- 不开发前端界面，所有功能以 RESTful API 形式交付。
- 不做嵌入模型的微调与更换，向量维度维持 4096 维不变。
- 不实现实时协同编辑或多人共享会话。

### 1.3 与 V1.0 的差异对照

| 维度 | V1.0 现状 | V1.5 目标 |
|------|-----------|-----------|
| 会话管理 | 仅创建 session_id，无历史持久化 | 完整 CRUD，支持历史消息、标题、摘要 |
| 知识库 | 单一全局 Collection，无隔离 | 多 KnowledgeBase，独立 Collection，完整 CRUD |
| 文件入库 | 无文件上传接口，需手动写入 | REST 上传接口 + 异步解析入库 + 进度查询 |
| 任务队列 | 无 | Celery + Redis，支持任务状态轮询 |
| 实体抽取 | 管道预留，未激活 | 文件入库时自动触发 NER Prompt 并写入 Neo4j |

---

## 2. 系统架构概览

### 2.1 新增组件与依赖

| 组件 | 技术选型 | 职责说明 |
|------|----------|----------|
| 任务队列 Broker | Redis 7 | Celery 任务的消息中间件，存储任务状态 |
| 异步任务执行器 | Celery 5 | 文件解析、切片、向量写入、实体抽取全链路异步执行 |
| 文件存储 | 本地磁盘（可换 OSS） | 临时存储上传的原始文件，入库完成后可清理 |
| 文档解析层 | Unstructured / python-docx / PyMuPDF | 将 PDF、Word、Markdown 等异构格式统一解析为纯文本 |
| 文本切片器 | LangChain TextSplitter | 按 chunk_size / chunk_overlap 参数切割文本 |
| 嵌入服务 | 已有 LiteLLM 代理（Embedding API） | 对 Chunk 文本生成 4096 维向量 |

### 2.2 文件上传入库数据流

文件上传后的完整数据流全程异步执行，不阻塞 HTTP 响应：

```
POST /kb/{kb_id}/files
  → 保存原始文件
  → 创建 FileRecord（status=pending）
  → 返回 file_id
          ↓ 触发 Celery Task
parse_and_ingest_task(file_id, kb_id)
  ├─ 文档解析（Unstructured）        → 纯文本          [progress: 20]
  ├─ 文本切片（TextSplitter）        → Chunk[]          [progress: 35]
  ├─ 向量嵌入（LiteLLM Embedding）   → float[4096][]    [progress: 60]
  ├─ 写入 Milvus（kb_id 对应 Collection）               [progress: 80]
  ├─ NER 实体抽取（LLM Prompt）      → Entity[]         [progress: 95]
  ├─ 写入 Neo4j（MERGE 节点 + 关系）                    [progress: 95]
  └─ 更新 FileRecord（status=completed）                 [progress: 100]
```

---

## 3. 功能需求详细拆分

### 3.1 会话管理模块（Session Management）

在 V1.0 仅创建 session_id 的基础上，全面扩展会话的生命周期管理能力，包含历史持久化、元数据自动生成与完整 CRUD。

---

#### SES-01 · 会话创建

**接口：** `POST /api/v1/sessions`

**描述：** 初始化新会话，支持传入可选 `title`（若不传则暂为空，首条消息后异步自动生成）。成功后返回包含 `session_id`、`created_at`、`title`、`message_count` 的完整 Session 对象。

**请求体：**

```json
{
  "title": "可选，不传则自动生成"
}
```

**响应体：**

```json
{
  "code": 0,
  "data": {
    "id": "uuid",
    "title": null,
    "summary": null,
    "message_count": 0,
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2025-01-01T00:00:00Z"
  }
}
```

**验收标准：** 成功创建记录，返回完整 Session 对象；`title` 传入时直接存储，不传时字段为 `null`。

---

#### SES-02 · 会话列表查询

**接口：** `GET /api/v1/sessions`

**描述：** 分页返回所有会话列表，按 `updated_at` 倒序排列。每条记录包含 `id`、`title`、`summary_snippet`（摘要前 80 字）、`message_count`、`updated_at`。支持 `page` 和 `page_size` 参数（默认 `page=1, page_size=20`）。

**响应体：**

```json
{
  "code": 0,
  "data": {
    "items": [...],
    "page": 1,
    "page_size": 20,
    "total": 100
  }
}
```

**验收标准：** 返回正确分页结构；`message_count` 与数据库中实际消息数一致；`summary_snippet` 为 `summary` 字段前 80 字截断。

---

#### SES-03 · 会话详情查询

**接口：** `GET /api/v1/sessions/{session_id}`

**描述：** 返回会话完整信息，包含 `title`、`summary`、`summarized_at`、`metadata` 及统计数字。

**验收标准：** `session_id` 不存在时返回 404；存在时返回完整会话元数据，所有字段不缺失。

---

#### SES-04 · 会话标题修改

**接口：** `PATCH /api/v1/sessions/{session_id}`

**描述：** 支持手动更新会话 `title` 字段，字段长度不超过 100 字。仅该字段可手动修改，其余元数据由系统维护。

**请求体：**

```json
{
  "title": "新的会话标题"
}
```

**验收标准：** 标题更新成功并在后续列表查询中体现；传入空字符串或超过 100 字时返回 400 错误。

---

#### SES-05 · 会话删除

**接口：** `DELETE /api/v1/sessions/{session_id}`

**描述：** 物理删除会话记录，并级联删除该会话下所有 `chat_messages` 记录。操作不可撤销，建议调用方做二次确认。

**验收标准：** 会话及其消息全部物理删除；再次查询返回 404；关联的 Milvus / Neo4j 数据**不受影响**（消息与知识库数据完全解耦）。

---

#### SES-06 · 历史消息查询

**接口：** `GET /api/v1/sessions/{session_id}/messages`

**描述：** 分页返回该会话的完整消息列表，支持游标翻页（`limit` + `before` 参数），消息按 `created_at` 正序返回。每条消息包含 `id`、`role`（system / user / assistant / tool）、`content`、`tool_calls`、`created_at`。

**查询参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `limit` | int | 20 | 每页条数，最大 100 |
| `before` | string | — | 游标：返回该消息 ID 之前的消息 |

**验收标准：** 消息按 `created_at` 正序返回；`role` 类型完整覆盖四种；游标翻页结果无重复、无遗漏。

---

#### SES-07 · 自动标题生成（异步）

**触发时机：** 用户发送第一条消息并获得 AI 回复后，由 `/chat/stream` 接口自动触发 Celery 任务。

**描述：** 将首条 `user` 消息和 `assistant` 回复拼接，调用大模型生成不超过 20 字的中文标题，更新 `chat_sessions.title` 字段。若会话 `title` 已有值（手动设置），则不触发此任务。

**Prompt 设计要点：**

```
你是一个对话标题生成器。根据以下对话内容，生成一个简洁的中文标题，
要求：不超过 20 字，概括核心议题，不要加引号或标点符号结尾。
```

**验收标准：** 首轮对话完成后，`title` 字段在 10 秒内自动填充；标题不超过 20 字；若已有手动标题则不覆盖。

---

#### SES-08 · 会话摘要生成（主动触发）

**接口：** `POST /api/v1/sessions/{session_id}/summarize`

**描述：** 主动触发摘要生成任务。接口立即返回 `202 Accepted`，后台异步将全量 `messages` 拼接后调用大模型生成 200 字以内的中文摘要，更新 `chat_sessions.summary` 和 `summarized_at` 字段。接口幂等，多次调用覆盖更新。

**响应体：**

```json
{
  "code": 0,
  "message": "摘要生成任务已提交",
  "data": { "task_id": "celery-task-uuid" }
}
```

**验收标准：** 接口立即返回，不阻塞；摘要生成完成后 `summary` 字段非空，不超过 200 字；任务失败时 `summary` 保持原值，不写入空值。

---

#### SES-09 · 历史消息上下文注入

**描述：** `/chat/stream` 接口加载会话时，从 `chat_messages` 读取最近 N 条历史消息，拼入 LangGraph 状态机的 `messages` 字段参与推理。N 由配置项 `CONTEXT_WINDOW_MESSAGES` 控制，默认 20 条。

**上下文裁剪策略：**

- 超出窗口的历史消息不参与当次推理，但持久化保存，可通过 SES-06 接口完整访问。
- `tool` 类型消息计入窗口计数，长 ReAct 链路需注意 Token 消耗。
- `system` 消息始终包含，不计入 N 的限制。

**验收标准：** 同一 `session_id` 第二轮对话时，模型能正确引用前轮内容回答；历史消息超出 N 时，仅保留最近 N 条参与推理。

---

### 3.2 知识库管理模块（KnowledgeBase Management）

构建多知识库空间管理能力，每个知识库拥有独立的 Milvus Collection 和 Neo4j `kb_id` 子图标签，彼此完全隔离。

---

#### KB-01 · 知识库创建

**接口：** `POST /api/v1/knowledge-bases`

**描述：** 创建新知识库并完成底层资源初始化。接口**同步**完成以下操作：

1. 在 PostgreSQL `knowledge_bases` 表写入元数据记录。
2. 在 Milvus 中创建命名为 `kb_{kb_id}` 的 Collection（Schema 与 V1.0 `knowledge_chunks` 一致，新增 `kb_id` 字段），建立 HNSW 索引（`ef_construction=256, M=16`）并 load。

**请求体：**

```json
{
  "name": "法律法规库",
  "description": "存储所有合规与法律相关文档",
  "embedding_dim": 4096,
  "chunk_size": 512,
  "chunk_overlap": 64
}
```

**字段约束：**

| 字段 | 类型 | 必填 | 默认值 | 约束 |
|------|------|------|--------|------|
| `name` | string | ✅ | — | 全局唯一，最长 128 字符 |
| `description` | string | ❌ | null | 最长 500 字符 |
| `embedding_dim` | int | ❌ | 4096 | 创建后不可修改 |
| `chunk_size` | int | ❌ | 512 | 范围 128~2048 |
| `chunk_overlap` | int | ❌ | 64 | 不超过 chunk_size 的 50% |

**验收标准：** PostgreSQL 记录写入成功；Milvus 中 `kb_{kb_id}` Collection 创建完成并处于 loaded 状态；`name` 重复时返回 409。

---

#### KB-02 · 知识库列表查询

**接口：** `GET /api/v1/knowledge-bases`

**描述：** 分页返回所有知识库，含 `id`、`name`、`description`、`file_count`、`chunk_count`、`created_at`、`status`（`active` / `building` / `error`）。支持 `page` 和 `page_size` 参数。

**验收标准：** 返回正确的文件数与切片数（从冗余统计字段读取，不实时查询 Milvus）；`status` 与实际 Collection 状态一致。

---

#### KB-03 · 知识库详情查询

**接口：** `GET /api/v1/knowledge-bases/{kb_id}`

**描述：** 返回知识库完整配置信息及统计数据，额外包含 `entity_count`（从 Neo4j 查询 `kb_id` 匹配的节点数）。

**验收标准：** `kb_id` 不存在时返回 404；统计数据与数据库及 Milvus 实际数量基本一致（允许短暂的冗余字段滞后）。

---

#### KB-04 · 知识库元数据更新

**接口：** `PATCH /api/v1/knowledge-bases/{kb_id}`

**描述：** 支持更新 `name`、`description` 字段。`embedding_dim`、`chunk_size`、`chunk_overlap` 为只读字段，一旦创建不可通过此接口修改（维度变更需删除重建）。

**验收标准：** 更新后查询能反映新值；尝试传入 `embedding_dim` 字段时返回 400，提示该字段不可修改，须删除重建。

---

#### KB-05 · 知识库删除

**接口：** `DELETE /api/v1/knowledge-bases/{kb_id}`

**描述：** 完全清理该知识库的所有资源，按以下顺序执行：

1. Milvus：`drop` 对应 Collection（`kb_{kb_id}`）。
2. PostgreSQL：删除所有关联 `kb_files` 记录和 `knowledge_bases` 记录。
3. Neo4j：执行 `MATCH (n {kb_id: $kb_id}) DETACH DELETE n` 删除所有关联节点与关系。

> ⚠️ 该操作不可撤销，建议调用方在业务层做二次确认。若 Milvus drop 失败，整个操作应回滚，返回 500。

**验收标准：** 全部资源清理完成；再次查询返回 404；Milvus 中对应 Collection 不存在。

---

#### KB-06 · 知识库关联对话

**描述：** `/chat/stream` 接口扩展 `kb_ids` 参数，支持在对话中指定使用哪些知识库。

**请求体扩展：**

```json
{
  "session_id": "uuid",
  "message": "用户输入",
  "kb_ids": ["kb_id_a", "kb_id_b"]
}
```

**检索策略：**
- 传入 `kb_ids` 时，`search_knowledge_base` Tool 仅在指定的 Collection 内执行向量搜索，结果合并后按相似度重排序。
- 不传 `kb_ids` 时，默认查询系统内所有 active 状态的知识库，结果合并重排序（性能较差，生产环境建议显式传入）。
- `query_knowledge_graph` Tool 对应在 Neo4j 查询时追加 `WHERE n.kb_id IN $kb_ids` 过滤条件。

**验收标准：** 传入 `kb_ids=[A]` 时，检索结果不包含知识库 B / C 的内容；SSE 流中的 `tool_start` 事件包含本次检索的 `kb_ids` 信息。

---

### 3.3 文件上传与管理模块（File Management）

提供完整的文件生命周期管理，从上传、异步入库到查询、删除，配合 Celery 任务队列实现非阻塞处理。

---

#### FILE-01 · 文件上传

**接口：** `POST /api/v1/knowledge-bases/{kb_id}/files`

**描述：** `multipart/form-data` 格式，接收 `file` 字段。接口同步完成：① 文件格式与大小校验；② 保存至磁盘（路径：`uploads/{kb_id}/{file_id}/{filename}`）；③ 创建 `kb_files` 记录（`status=pending`）；④ 触发 Celery 任务。立即返回 `file_id`，不等待入库完成。

**支持格式与限制：**

| 格式 | MIME 类型 | 解析库 |
|------|-----------|--------|
| `.pdf` | application/pdf | PyMuPDF (fitz) |
| `.docx` | application/vnd.openxmlformats-officedocument... | python-docx |
| `.doc` | application/msword | LibreOffice 转 docx |
| `.md` | text/markdown | 纯文本处理 |
| `.txt` | text/plain | 内置 open()，UTF-8 |

文件大小上限由配置项 `MAX_FILE_SIZE_MB` 控制，默认 **50 MB**。

**验收标准：** 文件保存成功，`kb_files` 记录写入，Celery 任务入队；文件格式不支持时返回 415；文件超限时返回 413；知识库不存在时返回 404。

---

#### FILE-02 · 文件列表查询

**接口：** `GET /api/v1/knowledge-bases/{kb_id}/files`

**描述：** 分页返回该知识库所有文件，按 `created_at` 倒序。每条记录包含 `id`、`filename`、`file_size`、`status`、`chunk_count`、`created_at`、`completed_at`。

**`status` 枚举：**

| 状态 | 含义 |
|------|------|
| `pending` | 已上传，等待 Celery 任务调度 |
| `processing` | 任务执行中 |
| `completed` | 入库完成 |
| `failed` | 入库失败，`error_message` 非空 |

**验收标准：** 返回正确的分页结构；`status` 与 Celery 任务实际执行阶段同步更新。

---

#### FILE-03 · 文件详情与进度查询

**接口：** `GET /api/v1/knowledge-bases/{kb_id}/files/{file_id}`

**描述：** 返回文件详情，含 `status`、`progress`（0~100 整数）、`error_message`（`failed` 时填充）、`chunk_count`、`entity_count`、`celery_task_id`。前端可按 **2 秒间隔**轮询此接口观测入库进度。

**各阶段 progress 对应值：**

| 阶段 | progress |
|------|----------|
| 已上传，待处理 | 0 |
| 文档解析完成 | 20 |
| 文本切片完成 | 35 |
| 向量嵌入完成 | 60 |
| Milvus 写入完成 | 80 |
| Neo4j 写入完成 | 95 |
| 全部完成 | 100 |

**验收标准：** 多次轮询时 `progress` 单调递增；`completed` 时 `progress=100`，`chunk_count` 和 `entity_count` 均有值；`failed` 时 `error_message` 包含可读的错误摘要。

---

#### FILE-04 · 文件删除

**接口：** `DELETE /api/v1/knowledge-bases/{kb_id}/files/{file_id}`

**描述：** 级联清理该文件的全部资源，按顺序执行：

1. Milvus：通过 `document_id == file_id` 过滤表达式删除对应向量切片。
2. Neo4j：执行 `MATCH (n {kb_id: $kb_id, document_id: $file_id}) DETACH DELETE n`。
3. PostgreSQL：删除 `kb_files` 记录。
4. 磁盘：删除原始文件。

若文件当前 `status=processing`，应先终止 Celery 任务（`celery_app.control.revoke(task_id, terminate=True)`），再执行清理。

**验收标准：** 所有资源清理完成；删除后向量库中该文件的切片不再被检索到；磁盘文件已清除。

---

#### FILE-05 · 文件重新入库

**接口：** `POST /api/v1/knowledge-bases/{kb_id}/files/{file_id}/reindex`

**描述：** 先执行与 FILE-04 相同的资源清理（Milvus + Neo4j + ChunkRecord，但**保留磁盘文件**），再重新触发 `parse_and_ingest_task`，将 `status` 重置为 `pending`，`progress` 归零。适用于：入库失败需重试、知识库配置更新后需重建等场景。

**验收标准：** 重建任务成功入队，`status` 重置为 `pending`；重建完成后检索结果与首次入库一致；若磁盘文件不存在则返回 404 并提示重新上传。

---

### 3.4 异步任务队列模块（Async Task Queue）

基于 Celery + Redis 实现文件入库的全链路异步化，并向上层提供任务状态的可观测能力。

---

#### TASK-01 · Celery 初始化

**描述：** 引入 Celery，Broker 和 Backend 均指向 Redis（配置项 `REDIS_URL`）。定义 `celery_app` 单例，与 FastAPI 应用共享配置与依赖注入上下文。

**配置要点：**

```python
celery_app = Celery(
    "TyAgent",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    task_serializer="json",
    result_serializer="json",
    task_acks_late=True,       # Worker 异常时任务可重新入队
    worker_prefetch_multiplier=1,  # 防止 OOM 场景下多任务并发
)
```

**验收标准：** `celery worker` 启动无报错；FastAPI 启动时 Redis 连接健康检查通过；Worker 意外退出后重启时，`acks_late=True` 保证 `processing` 状态的任务重新执行。

---

#### TASK-02 · 入库任务定义

**任务名：** `parse_and_ingest_task(file_id: str, kb_id: str)`

**描述：** 完整入库流程的执行主体，严格按以下顺序执行各步骤，每步完成后更新 `progress`：

```
Step 1: 更新 status=processing, progress=0
Step 2: 加载原始文件 → 格式路由 → 解析为纯文本        [progress=20]
Step 3: RecursiveCharacterTextSplitter 切片           [progress=35]
Step 4: 批量调用 LiteLLM Embedding API 生成向量       [progress=60]
Step 5: 批量写入 Milvus（batch_size=50）              [progress=80]
Step 6: 按 Chunk 调用 LLM 执行 NER 实体抽取           [progress=90]
Step 7: MERGE 实体节点和关系到 Neo4j                  [progress=95]
Step 8: 更新 FileRecord（status=completed, 各统计数）  [progress=100]
```

**异常处理：**

- 任意步骤抛出非可重试异常时：捕获堆栈，写入 `FileRecord.error_message`，`status` 置为 `failed`，不重试。
- NER 步骤失败：记录 warning 日志，`entity_count` 保持当前值，**不中断任务**，继续执行后续步骤。

**验收标准：** 全流程成功时 `status=completed`，`progress=100`；任意主步骤失败时 `status=failed`，`error_message` 含可读错误摘要。

---

#### TASK-03 · 入库任务重试策略

**描述：** 针对临时性网络抖动（Milvus 连接超时、Redis 瞬断等）配置自动重试：

```python
@celery_app.task(
    autoretry_for=(MilvusException, RedisConnectionError),
    max_retries=3,
    countdown=30,  # 30秒后重试
    retry_backoff=True,  # 指数退避
)
def parse_and_ingest_task(file_id, kb_id): ...
```

3 次重试均失败后，`status` 最终置为 `failed`。

**验收标准：** 模拟 Milvus 临时连接中断时，任务自动重试不超过 3 次；3 次均失败后 `status=failed`，不无限循环。

---

#### TASK-04 · 会话标题异步生成

**任务名：** `generate_session_title_task(session_id: str)`

**触发条件：** `/chat/stream` 接口检测到当前 session 的 `title` 为 `null` 且本轮为首次 AI 回复时，触发此任务。

**执行逻辑：** 从 `chat_messages` 取首条 `user` 消息和首条 `assistant` 消息，拼接后调用 LLM 生成不超过 20 字的中文标题，更新 `chat_sessions.title`。

**验收标准：** 首轮对话完成后 10 秒内 `title` 自动填充；标题不超过 20 字；若 `title` 已有值则跳过，不覆盖。

---

#### TASK-05 · 会话摘要异步生成

**任务名：** `generate_session_summary_task(session_id: str)`

**触发条件：** 由 SES-08 接口主动触发，不自动调用。

**执行逻辑：** 从 `chat_messages` 取全量消息，拼接后调用 LLM 生成不超过 200 字的中文摘要，更新 `chat_sessions.summary` 和 `summarized_at`。

**验收标准：** 摘要生成完成后 `summary` 非空，不超过 200 字；任务失败时 `summary` 保持原值；接口幂等，多次调用覆盖更新。

---

## 4. API 接口总览

所有接口挂载于 `/api/v1` 前缀，响应格式统一为 `application/json`。

### 4.1 会话相关接口

| Method | Path | 描述 | 备注 |
|--------|------|------|------|
| `POST` | `/api/v1/sessions` | 创建新会话 | 返回完整 Session 对象 |
| `GET` | `/api/v1/sessions` | 分页查询会话列表 | 支持 `page` / `page_size` |
| `GET` | `/api/v1/sessions/{session_id}` | 查询会话详情 | 含 title / summary |
| `PATCH` | `/api/v1/sessions/{session_id}` | 更新会话标题 | 仅 title 字段 |
| `DELETE` | `/api/v1/sessions/{session_id}` | 删除会话及消息 | 级联删除 messages |
| `GET` | `/api/v1/sessions/{session_id}/messages` | 分页查询历史消息 | 游标翻页，正序返回 |
| `POST` | `/api/v1/sessions/{session_id}/summarize` | 触发会话摘要生成 | 异步，返回 202 |
| `POST` | `/api/v1/chat/stream` | 流式对话（SSE） | V1.0 已有，新增 `kb_ids` 参数 |

### 4.2 知识库相关接口

| Method | Path | 描述 | 备注 |
|--------|------|------|------|
| `POST` | `/api/v1/knowledge-bases` | 创建知识库 | 自动初始化 Milvus Collection |
| `GET` | `/api/v1/knowledge-bases` | 分页查询知识库列表 | 含统计数据 |
| `GET` | `/api/v1/knowledge-bases/{kb_id}` | 查询知识库详情 | 含 chunk_count / entity_count |
| `PATCH` | `/api/v1/knowledge-bases/{kb_id}` | 更新知识库元数据 | 不可改 embedding_dim |
| `DELETE` | `/api/v1/knowledge-bases/{kb_id}` | 删除知识库及全部资源 | 级联清理三库，不可撤销 |

### 4.3 文件相关接口

| Method | Path | 描述 | 备注 |
|--------|------|------|------|
| `POST` | `/api/v1/knowledge-bases/{kb_id}/files` | 上传文件并触发入库 | multipart/form-data |
| `GET` | `/api/v1/knowledge-bases/{kb_id}/files` | 查询文件列表 | 含 status / chunk_count |
| `GET` | `/api/v1/knowledge-bases/{kb_id}/files/{file_id}` | 查询文件详情与进度 | progress 0~100，建议 2s 轮询 |
| `DELETE` | `/api/v1/knowledge-bases/{kb_id}/files/{file_id}` | 删除文件及相关资源 | 级联清理三库 |
| `POST` | `/api/v1/knowledge-bases/{kb_id}/files/{file_id}/reindex` | 重新入库 | 先清理再重建 |

---

## 5. 数据库字典设计（V1.5 新增）

### 5.1 [PostgreSQL] chat_sessions 表扩展（新增字段）

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `title` | VARCHAR(100) | Nullable | 会话标题，异步自动生成，最长 100 字 |
| `summary` | Text | Nullable | 会话摘要，由 `/summarize` 接口触发生成 |
| `summarized_at` | Timestamp | Nullable | 最近一次摘要生成时间 |
| `updated_at` | Timestamp | Not Null, Default NOW() | 最近一次消息写入时自动更新 |
| `message_count` | Integer | Not Null, Default 0 | 冗余计数字段，提升列表查询性能 |

### 5.2 [PostgreSQL] 新表：knowledge_bases

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `id` | UUID | Primary Key | 知识库唯一标识 |
| `name` | VARCHAR(128) | Not Null, Unique | 知识库名称，全局唯一 |
| `description` | Text | Nullable | 知识库描述 |
| `embedding_dim` | Integer | Not Null, Default 4096 | 向量维度，创建后不可修改 |
| `chunk_size` | Integer | Not Null, Default 512 | 文本切片大小（Token 数） |
| `chunk_overlap` | Integer | Not Null, Default 64 | 切片重叠大小（Token 数） |
| `status` | VARCHAR(20) | Not Null, Default 'active' | `active` / `building` / `error` |
| `file_count` | Integer | Not Null, Default 0 | 冗余统计：关联文件数 |
| `chunk_count` | Integer | Not Null, Default 0 | 冗余统计：Milvus 向量切片数 |
| `created_at` | Timestamp | Not Null, Default NOW() | 创建时间 |

### 5.3 [PostgreSQL] 新表：kb_files

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `id` | UUID | Primary Key | 文件唯一标识，同时作为 `document_id` 写入 Milvus / Neo4j |
| `kb_id` | UUID | FK → knowledge_bases.id | 所属知识库 |
| `filename` | VARCHAR(512) | Not Null | 原始文件名 |
| `file_path` | VARCHAR(1024) | Not Null | 服务器存储路径 |
| `file_size` | BigInt | Not Null | 文件大小（字节） |
| `mime_type` | VARCHAR(128) | Not Null | MIME 类型 |
| `status` | VARCHAR(20) | Not Null, Default 'pending' | `pending` / `processing` / `completed` / `failed` |
| `progress` | Integer | Not Null, Default 0 | 入库进度 0~100 |
| `chunk_count` | Integer | Not Null, Default 0 | 成功写入 Milvus 的切片数 |
| `entity_count` | Integer | Not Null, Default 0 | 成功写入 Neo4j 的实体数 |
| `error_message` | Text | Nullable | 失败时的错误摘要 |
| `celery_task_id` | VARCHAR(255) | Nullable | Celery 任务 ID |
| `created_at` | Timestamp | Not Null, Default NOW() | 上传时间 |
| `completed_at` | Timestamp | Nullable | 入库完成时间 |

### 5.4 [Milvus] Collection 命名规则调整

V1.0 固定使用 `knowledge_chunks`，V1.5 改为动态命名，每个知识库对应独立 Collection：

| 规则项 | 说明 |
|--------|------|
| 命名规则 | `kb_{kb_id}`（去除连字符），例：`kb_a1b2c3d4e5f6...` |
| Schema | 与 V1.0 `knowledge_chunks` 一致，新增 `kb_id VARCHAR(64)` 冗余字段 |
| Index | HNSW，Metric=COSINE，ef_construction=256，M=16 |
| 初始化时机 | KB-01 创建知识库时同步完成 |
| 销毁时机 | KB-05 删除知识库时执行 `Collection.drop()` |

### 5.5 [Neo4j] 属性扩展

V1.5 在所有 `Entity` 和 `Document` 节点上新增 `kb_id` 属性，支持按知识库隔离图谱查询：

| 节点类型 | 新增属性 | 查询过滤方式 |
|----------|----------|--------------|
| `Entity` | `kb_id` | `WHERE n.kb_id IN $kb_ids` |
| `Document` | `kb_id` | `MATCH (n {kb_id: $kb_id, document_id: $doc_id}) DETACH DELETE n` |

---

## 6. 核心业务规则与约束

### 6.1 文件格式解析规则

| 文件类型 | 解析库 | 特殊处理 |
|----------|--------|----------|
| `.pdf` | PyMuPDF (fitz) | 逐页提取；扫描页文本为空时记录 warning，跳过该页，不中断任务 |
| `.docx` | python-docx | 提取正文段落与表格，忽略页眉页脚与图片 |
| `.doc` | LibreOffice 转换 | 先调用 soffice 转为 `.docx`，再按 `.docx` 处理 |
| `.md` | 纯文本 | 去除 Markdown 语法符号后作为纯文本处理 |
| `.txt` | 内置 open() | UTF-8 编码读取；非 UTF-8 时返回 415 错误 |

### 6.2 切片与嵌入规则

- 使用 `RecursiveCharacterTextSplitter`，分隔符优先级：段落 → 句子 → 词 → 字符。
- `chunk_size` 和 `chunk_overlap` 以 Token 计，通过 tiktoken 估算（中文场景约 1 Token ≈ 1.5~2 字）。
- 嵌入调用采用批量模式，默认 `batch_size=32`，避免单次请求超出 API 限制。
- 向量维度须与知识库 `embedding_dim` 一致，不一致时任务 `failed`，`error_message` 提示维度不匹配。

### 6.3 NER 实体抽取规则

- 每个 Chunk 独立调用 LLM，要求仅返回 JSON 数组：`[{"name": "实体名", "type": "实体类型"}]`。
- 结果去重后通过 `MERGE` 写入 Neo4j（相同 `name + kb_id` 的节点不重复创建）。
- NER 步骤失败不终止任务，记录 warning 后继续，`entity_count` 保持当前成功数。
- 实体类型预设：`Person`（人物）/ `Organization`（组织）/ `Location`（地点）/ `Concept`（概念）/ `Product`（产品）/ `Other`（其他）。

### 6.4 知识库隔离规则

- Milvus 层：每个知识库独立 Collection，向量搜索天然隔离。
- Neo4j 层：通过 `kb_id` 属性隔离，Cypher 查询必须强制过滤。
- 多知识库合并检索时，各 Collection 分别搜索后在应用层合并，按相似度分数重排序，默认取 Top-K。

---

## 7. 错误码与响应规范

### 7.1 统一响应格式

```json
// 成功
{ "code": 0, "message": "success", "data": { ... } }

// 错误
{ "code": 40400, "message": "Session not found", "data": null }
```

### 7.2 业务错误码表

| HTTP Status | 业务 Code | 说明 |
|-------------|-----------|------|
| 404 | 40400 | Session / KnowledgeBase / File 不存在 |
| 409 | 40900 | 知识库名称 `name` 已存在（唯一冲突） |
| 400 | 40001 | 请求参数校验失败（缺少必填字段 / 类型错误） |
| 400 | 40002 | 尝试修改 `embedding_dim`，操作不被允许 |
| 413 | 41300 | 文件大小超出 `MAX_FILE_SIZE_MB` 限制 |
| 415 | 41500 | 文件格式不支持或编码无法识别 |
| 422 | 42200 | 向量维度与知识库 `embedding_dim` 不匹配 |
| 500 | 50000 | 服务器内部错误（含 Milvus / Neo4j 连接异常） |
| 503 | 50300 | Celery Worker 不可达或 Redis 连接失败 |

---

## 8. 后续规划：智能体能力升级方案

### 8.1 方案评估

你提供的智能体优化方案整体框架清晰，四层分离的架构描述准确，以下是逐条评估：

**总体架构（四层分离）— 评价：✅ 完全认可**

四层的划分（网关层、编排层、工具层、基础支撑层）与 TyAgent 当前的实际代码结构高度契合，是合理的抽象粒度。这个框架可以直接作为后续开发的概念地图，不需要调整。

**高确定性与自修复能力 — 评价：✅ 已在 V1.0 部分实现，需强化**

V1.0 的 AGT-03（死循环熔断）和 AGT-04（错误反思注入）已覆盖基础容错。方案中提到的"将报错信息反馈给模型触发自我修正"是正确方向，但当前缺少的是：错误分类（可重试 vs 不可重试）和重试上限的精细化控制。这是 V2 值得重点投入的地方。

**深度业务专精（工具扩容）— 评价：✅ 方向正确，但执行顺序需调整**

方案建议"梳理核心业务流程，将其拆解并注册为标准化 Agent 工具"，思路对。但在工具扩容之前，**工具的注册规范和 Schema 标准**需要先定义清楚，否则多个工具堆砌后系统提示词会急剧膨胀，导致模型的工具选择准确率下降。建议先设计工具注册框架，再批量接入。

**安全沙箱 — 评价：⚠️ 重要，但当前阶段优先级偏高**

方案将容器沙箱放在第三阶段，这个时机判断是对的。但需要补充一点：沙箱不仅仅是为了执行动态代码，还需要处理**工具调用的副作用隔离**（如防止 Agent 误删数据）。建议在沙箱设计时同步考虑"只读工具"和"写操作工具"的权限分层。

**可观测性（Trace 追踪）— 评价：✅ 优先级应该提前**

方案将可观测性放在最后阶段，这是最常见的工程误区。建议**提前到 V2 早期**就引入基础 Trace 体系，原因很简单：没有 Trace 数据，无法知道哪条 ReAct 链路在什么场景下失败，也无法评估 Prompt 优化是否有效。不需要一开始就接入 LangSmith，自建一张 `agent_traces` 表记录 `session_id + trace_id + thought + action + observation + latency` 即可起步。

**低延迟流式响应 — 评价：✅ V1.0 已有 SSE，V2 需要精细化**

当前 V1.0 的 SSE 只推送文本和 `tool_start`，V2 应该扩展推送粒度：每个 ReAct 步骤的耗时、Token 消耗、检索到的 Chunk 来源等，让前端能呈现更丰富的"思考过程"可视化。

**实施顺序 — 评价：✅ 逻辑清晰，但第 4 步和第 5 步建议对调**

方案建议"先知识库，再可观测性"，但如前所述，没有可观测性很难评估知识库的 RAG 效果。建议将"可观测体系"提前到第 4 步，"知识库增强"与"长期记忆"合并为第 5 步。

---

### 8.2 V2 智能体能力升级方案

基于以上评估，结合 TyAgent 当前架构，对方案进行优化后纳入后续规划：

---

#### 8.2.1 Agent 架构重构

**目标：** 将当前单文件式的 ReAct 循环升级为更健壮的分层控制流。

**核心改造点：**

- **工具注册规范化：** 定义统一的 `BaseTool` 抽象类，要求每个 Tool 声明 `name`、`description`、`args_schema`（Pydantic）、`is_readonly`（是否为只读操作）。LangGraph 在组装系统提示词时，根据 `is_readonly` 对工具分组描述，降低模型的工具选择混淆率。

- **意图置信度评估节点：** 在 Thought 节点和 Action 节点之间插入一个轻量的"置信度评估"步骤：若模型的 `tool_calls` 参数存在明显的幻觉迹象（如引用了不存在的字段名），在实际执行前拦截并要求模型重新生成，避免将错误的参数发送给外部系统。

- **错误分类与差异化重试：** 将 Tool 异常分为三类：
  - `RetryableError`（网络超时、临时不可用）：自动重试，上限 3 次。
  - `ParameterError`（参数格式错误）：注入错误信息，触发模型自我修正。
  - `FatalError`（权限拒绝、资源不存在）：直接终止，输出兜底回复，不重试。

---

#### 8.2.2 可观测性体系（Trace）

**目标：** 在不引入外部 LLMOps 平台的前提下，建立基础的自托管 Trace 能力。

**新增数据表：** `agent_traces`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID | Trace 唯一标识 |
| `session_id` | UUID | 关联会话 |
| `message_id` | UUID | 触发本次 Trace 的用户消息 |
| `step_index` | Integer | 当前是第几步 ReAct 循环（1-based） |
| `step_type` | VARCHAR | `thought` / `action` / `observation` / `final` |
| `content` | JSONB | 本步骤的完整内容（含 tool_name、args、result） |
| `latency_ms` | Integer | 本步骤耗时（毫秒） |
| `token_input` | Integer | 本步骤输入 Token 数 |
| `token_output` | Integer | 本步骤输出 Token 数 |
| `created_at` | Timestamp | 步骤发生时间 |

**新增接口：**

- `GET /api/v1/sessions/{session_id}/traces`：查询该会话的所有 Trace 记录，用于调试和效果评估。
- `GET /api/v1/sessions/{session_id}/messages/{message_id}/trace`：查询单条消息的完整推理链路。

**SSE 扩展：** 在 `tool_start` 事件基础上，新增推送 `trace_step` 事件：

```json
{
  "type": "trace_step",
  "step": 2,
  "tool": "search_knowledge_base",
  "args": { "query": "..." },
  "latency_ms": 320,
  "chunks_retrieved": 5
}
```

---

#### 8.2.3 工具集扩容

**目标：** 在工具注册规范就绪后，分批接入业务工具。

**第一批（与现有 RAG 体系深度集成）：**

- `search_knowledge_base_v2`：在现有基础上支持**多路查询合并**，单次 Tool 调用可传入多个不同角度的子查询，结果合并后 RRF（Reciprocal Rank Fusion）重排序，提升召回多样性。
- `summarize_chunks`：接收多个 Chunk 内容，调用 LLM 生成摘要后返回给 Agent，解决 Chunk 过长导致上下文溢出的问题。

**第二批（业务深度对接，需业务侧配合）：**

- 结构化数据查询工具（对接 PostgreSQL / 业务 API）
- 地理空间分析工具（TyAgent 领域核心）
- 报告生成工具（结构化输出 PDF / Markdown）

---

#### 8.2.4 安全沙箱

**目标：** 为 Agent 动态生成和执行代码的场景提供隔离保障。

**设计要点：**

- 沙箱基于 **Docker 容器**实现，每次代码执行任务启动一个临时容器，执行完毕后销毁。
- 容器网络策略：默认断网，仅允许访问内部数据服务（Milvus、PostgreSQL 只读副本）。
- 资源限制：CPU 0.5 核，内存 512 MB，执行超时 60 秒（超时强制 kill）。
- 代码执行结果通过标准输出捕获，禁止写入宿主机文件系统。
- 当前 V1.0 的 `TOL-01` 脚本子进程调度在工具量少时可临时使用，沙箱为其长期替代方案。

---

#### 8.2.5 长期记忆与个性化

**目标：** 让 Agent 能跨 Session 积累领域知识，减少重复推理。

**实现策略：**

- **实体记忆：** 每次对话中被多次提及的实体（从 Neo4j 中识别），可通过专用接口标记为"重要实体"，在后续 Session 中作为系统提示的一部分注入。
- **用户偏好记忆：** 记录用户历史对话中的显式偏好（如"我更喜欢结论在前的回答格式"），存储在 `chat_sessions.metadata` 扩展字段中，跨会话传递。
- **知识积累：** 用户对 AI 回答的反馈（👍/👎）配合检索上下文持久化，为后续 RAG 效果评估和 Prompt 调优提供数据基础。

---

### 8.3 需求优先级评估

综合技术依赖关系、业务价值和实施风险，对 V2 各方向的优先级评估如下：

| 优先级 | 需求方向 | 推荐时机 | 核心理由 |
|--------|----------|----------|----------|
| 🔴 P0 | 可观测性 Trace 体系 | V2 第一步 | 没有数据就无法评估其他任何改进是否有效；成本低，价值高 |
| 🔴 P0 | 工具注册规范化 + 错误分类 | V2 第一步 | 工具扩容的前置条件；先有规范再批量接入，避免债务积累 |
| 🟠 P1 | search_knowledge_base_v2（多路查询 + RRF） | V2 第二步 | RAG 召回质量是智能体表现的直接瓶颈，提升 ROI 最高 |
| 🟠 P1 | SSE 推送粒度精细化 | V2 第二步 | 用户感知改善，调试能力提升，依赖 Trace 体系已建立 |
| 🟡 P2 | 意图置信度评估节点 | V2 第三步 | 有效，但需要足够的 Trace 数据才能调出合适的拦截阈值 |
| 🟡 P2 | 第二批业务工具（地理空间、报告生成） | V2 第三步 | 需业务侧配合，并行推进不阻塞主链路 |
| 🟢 P3 | 安全沙箱（Docker 隔离） | V2 第四步 | 当前脚本执行场景有限，待工具扩容后需求更迫切 |
| 🟢 P3 | 长期记忆与个性化 | V2 第四步 | 效果依赖大量真实对话数据积累，过早做投入产出比偏低 |
| 🔵 P4 | 用户反馈闭环（👍/👎） | V3 | 需要有一定用户规模才有统计意义；可作为 V3 启动信号 |

> **依赖关系：** `Trace 体系 → Prompt 调优 → 置信度评估` 是一条强依赖链，必须严格按序推进。`工具注册规范化 → 工具扩容` 同理。

---

*TyAgent V1.5 PRD · End of Document*
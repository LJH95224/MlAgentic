# TyAgent V1.5 · 接口文档

> **基线版本**：V1.5（2026-06-12 全链路 smoke 验收通过）
> **配套文档**：[PRD](TyAgent%20V1.5%20%C2%B7%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md) · [架构](architecture.md) · [开发计划](v1.5_dev_plan.md) · [进度](progress.md)
> **在线交互**：服务启动后访问 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)（Swagger UI）或 [/redoc](http://127.0.0.1:8000/redoc)

---

## 0. 通用约定

### 0.1 BaseURL

| 环境 | URL |
|---|---|
| 开发 | `http://127.0.0.1:8000` |
| 测试/生产 | 由部署方决定（保持 `/api/v1` 前缀） |

### 0.2 统一响应格式（PRD §7.1）

**所有 REST 接口** 一律返回 `{code, message, data}` 包裹结构。**仅** `/chat/stream` 的 200 响应是 SSE 流（非 JSON），但其 4xx 错误同样走统一格式。

```json
// 成功
{
  "code": 0,
  "message": "success",
  "data": { ... }
}

// 失败
{
  "code": 40400,
  "message": "会话 xxx 不存在",
  "data": null
}
```

### 0.3 业务错误码表（PRD §7.2）

| HTTP | 业务 code | 含义 | 触发场景 |
|---|---|---|---|
| 200 | 0 | 成功 | 正常 |
| 400 | 40001 | 请求参数校验失败 | Pydantic 校验失败（缺字段/类型错/超长/取值越界） |
| 400 | 40002 | 字段不可修改 | 尝试 PATCH 只读字段（如 KB 的 `embedding_dim`） |
| 404 | 40400 | 资源不存在 | Session / KB / File 不存在 |
| 409 | 40900 | 名称冲突 | KB `name` 已存在 |
| 413 | 41300 | 文件大小超限 | 单文件超 `MAX_FILE_SIZE_MB`（默认 50MB） |
| 415 | 41500 | 文件格式不支持 | 上传 .pdf/.docx/.md/.txt 之外的格式 |
| 422 | 42200 | 向量维度不匹配 | Embedding 实际维度与 KB.embedding_dim 不一致（异步任务报） |
| 500 | 50000 | 服务器内部错误 | Milvus / Neo4j 连接异常等 |
| 503 | 50300 | Celery 不可达 | Worker 挂了或 Redis 不通 |

### 0.4 数据类型与约定

- 所有 ID：UUID 字符串（标准 8-4-4-4-12 格式）
- 所有时间戳：ISO 8601 带时区，UTC（如 `"2026-06-11T12:00:00+00:00"`）
- 分页参数：`page` 从 1 起；`page_size` 默认 20、上限 100
- Content-Type：JSON 接口 `application/json`；上传接口 `multipart/form-data`
- 字符编码：UTF-8

### 0.5 接口总览

| 模块 | Method | Path | 说明 |
|---|---|---|---|
| **会话管理** | POST | `/api/v1/sessions` | 创建会话（SES-01）|
| | GET | `/api/v1/sessions` | 列表（SES-02）|
| | GET | `/api/v1/sessions/{session_id}` | 详情（SES-03）|
| | PATCH | `/api/v1/sessions/{session_id}` | 改标题（SES-04）|
| | DELETE | `/api/v1/sessions/{session_id}` | 删除（SES-05）|
| | GET | `/api/v1/sessions/{session_id}/messages` | 历史消息（SES-06）|
| | POST | `/api/v1/sessions/{session_id}/summarize` | 触发摘要（SES-08）|
| **流式对话** | POST | `/api/v1/chat/stream` | SSE 流式对话（API-02 + KB-06）|
| **知识库** | POST | `/api/v1/knowledge-bases` | 创建 KB（KB-01）|
| | GET | `/api/v1/knowledge-bases` | KB 列表（KB-02）|
| | GET | `/api/v1/knowledge-bases/{kb_id}` | KB 详情（KB-03）|
| | PATCH | `/api/v1/knowledge-bases/{kb_id}` | 改 KB 元数据（KB-04）|
| | DELETE | `/api/v1/knowledge-bases/{kb_id}` | 删除 KB（KB-05，三库联动清理） |
| **文件管理** | POST | `/api/v1/knowledge-bases/{kb_id}/files` | 上传文件（FILE-01）|
| | GET | `/api/v1/knowledge-bases/{kb_id}/files` | 文件列表（FILE-02）|
| | GET | `/api/v1/knowledge-bases/{kb_id}/files/{file_id}` | 文件详情+进度（FILE-03）|
| | DELETE | `/api/v1/knowledge-bases/{kb_id}/files/{file_id}` | 删除文件（FILE-04，三库联动）|
| | POST | `/api/v1/knowledge-bases/{kb_id}/files/{file_id}/reindex` | 重新入库（FILE-05）|
| **基础设施** | GET | `/health` | 健康检查（裸 JSON，不包装）|

---

## 1. 会话管理（Session）

### 1.1 创建会话 `POST /api/v1/sessions`

| | |
|---|---|
| **PRD** | SES-01 |
| **HTTP 状态** | 201 Created |

**请求体**（可选）：

```json
{
  "title": "可选的会话标题（≤100 字）"
}
```

> 不传 body 或不传 title 时 → 会话 title 字段为 null；首次 AI 回复完成后会**异步**自动生成（SES-07）。

**响应** `data` 字段：

```json
{
  "id": "uuid",
  "title": null,
  "summary": null,
  "summarized_at": null,
  "message_count": 0,
  "metadata": null,
  "created_at": "2026-06-11T12:00:00+00:00",
  "updated_at": "2026-06-11T12:00:00+00:00"
}
```

**错误**：
- `40001` title 为空白字符串 / 超过 100 字

---

### 1.2 会话列表 `GET /api/v1/sessions`

| | |
|---|---|
| **PRD** | SES-02 |

**Query 参数**：

| 参数 | 类型 | 默认 | 范围 | 说明 |
|---|---|---|---|---|
| `page` | int | 1 | ≥1 | 页码 |
| `page_size` | int | 20 | 1~100 | 每页条数 |

**响应** `data` 字段：

```json
{
  "items": [
    {
      "id": "uuid",
      "title": "标题或 null",
      "summary_snippet": "摘要前 80 字截断或 null",
      "message_count": 12,
      "updated_at": "2026-06-11T12:00:00+00:00"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 100
}
```

**排序**：按 `updated_at` 倒序（最近活跃在前），同时间戳按 id 做 tie-breaker。

> ⚠️ **设计要点**：列表项的 `summary_snippet` 是 `summary` 的前 80 字截断；完整 summary 需走详情接口（SES-03）。

---

### 1.3 会话详情 `GET /api/v1/sessions/{session_id}`

| | |
|---|---|
| **PRD** | SES-03 |

**响应** `data` 同 1.1 创建响应（完整 Session 对象）。

**错误**：
- `40400` session_id 不存在
- `40001` session_id 非合法 UUID

---

### 1.4 修改标题 `PATCH /api/v1/sessions/{session_id}`

| | |
|---|---|
| **PRD** | SES-04 |

**请求体**（必填 title）：

```json
{
  "title": "新标题（1~100 字，非空白）"
}
```

> ⚠️ 仅允许 `title` 字段；传入其它字段（如 summary）→ 422 PARAM_INVALID。

**响应** `data` 同 1.1。

---

### 1.5 删除会话 `DELETE /api/v1/sessions/{session_id}`

| | |
|---|---|
| **PRD** | SES-05 |

物理删除 session + 级联删除该会话的全部 `chat_messages`。**Milvus / Neo4j 数据不受影响**（消息与知识库完全解耦）。

**响应** `data: null`，message="会话已删除"。

---

### 1.6 历史消息（游标翻页）`GET /api/v1/sessions/{session_id}/messages`

| | |
|---|---|
| **PRD** | SES-06 |

**Query 参数**：

| 参数 | 类型 | 默认 | 范围 | 说明 |
|---|---|---|---|---|
| `limit` | int | 20 | 1~100 | 每页条数 |
| `before` | UUID | null | — | 游标：返回该消息 ID **之前**（更早）的消息 |

**翻页语义（重要！与传统分页相反）**：

- 不传 `before` → 拉**最近** N 条（如 limit=20 → 最近 20 条）
- 传 `before` → 拉该消息**之前**的 N 条

典型"加载更多历史"前端流程：
1. 进入会话 → 不传 before 拉最新 20 条
2. 用户滚到顶部要看更早的 → 传 `before = items[0].id` 拉前 20 条
3. 重复直到 `has_more = false`

**响应** `data` 字段：

```json
{
  "items": [
    {
      "id": "msg-uuid",
      "role": "user|assistant|system|tool",
      "content": "消息内容（tool 角色可能为 null）",
      "tool_calls": [{"id":"...","name":"...","args":{}}] | null,
      "created_at": "2026-06-11T12:00:00+00:00"
    }
  ],
  "has_more": true,
  "next_before": "uuid（items 首条 id，前端下次翻页传它）或 null"
}
```

**错误**：
- `40400` session_id 不存在
- `40001` `before` 消息不存在或不属于该会话

---

### 1.7 触发摘要生成 `POST /api/v1/sessions/{session_id}/summarize`

| | |
|---|---|
| **PRD** | SES-08 / TASK-05 |
| **HTTP 状态** | 202 Accepted |

立即返回，后台异步用 LLM 生成 200 字以内中文摘要写到 `session.summary` 字段。**幂等**：多次调用覆盖更新；任务失败时 summary 保持原值（不写空）。

**响应** `data` 字段：

```json
{
  "task_id": "celery-task-uuid"
}
```

**错误**：
- `40400` session_id 不存在
- `50300` Celery worker 不可达 / Redis 不通

**前端使用建议**：
- 触发后无需轮询 task_id（Celery backend 状态对前端无意义）
- 用户主动刷新会话详情时（GET /sessions/{id}），若 `summary` 字段非 null 即可展示
- 若需"摘要生成中"的 loading 状态：触发后本地标记 loading，10 秒后/用户切走时清掉

---

## 2. 流式对话（Chat Stream）

### 2.1 流式对话 `POST /api/v1/chat/stream`

| | |
|---|---|
| **PRD** | API-02 / API-03 / KB-06 / SES-09 |
| **响应类型** | `text/event-stream`（SSE） |

**请求体**：

```json
{
  "session_id": "uuid",
  "content": "用户输入的消息（≥1 字符）",
  "kb_ids": ["kb-uuid-A", "kb-uuid-B"]   // V1.5 KB-06 可选
}
```

**`kb_ids` 三态语义（V1.5 KB-06）**：

| 取值 | 含义 |
|---|---|
| 字段不传 / `null` | V1.0 默认行为：使用全局 `knowledge_chunks` Collection |
| `[]` 空数组 | 显式不查任何 KB（纯 LLM 答，不调检索工具） |
| `["kb-A", "kb-B"]` | 跨这些 KB 检索 + 合并按 score 重排 top_k |

**响应**：`Content-Type: text/event-stream`（SSE 协议），按 `\n\n` 分帧的事件流。

#### SSE 事件类型

帧形如：
```
event: <event_name>
data: <JSON 字符串>

```

| event | data.type | 说明 | data 结构 |
|---|---|---|---|
| `message` | `text` | 打字机逐 token 文本 | `{"event":"message","type":"text","content":"片段文本"}` |
| `control` | `tool_start` | 工具开始调用 | `{"event":"control","type":"tool_start","tool":"search_knowledge_base","args":{...,"_kb_ids":["kb-A"]}}` |
| `control` | `tool_end` | 工具调用结束 | `{"event":"control","type":"tool_end","tool":"search_knowledge_base","output":"片段摘要"}` |
| `message` | `done` | 流结束 | `{"event":"message","type":"done"}` |

#### `tool_start.args._kb_ids` 字段（KB-06 验收点）

当请求携带 `kb_ids` 时，service 层会把 kb_ids 注入到 `tool_start` 事件的 `args._kb_ids` 中，前端可据此向用户展示"正在检索 KB-A、KB-B..."。

#### 前端 EventSource 示例

```javascript
const resp = await fetch('/api/v1/chat/stream', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({
    session_id: sessionId,
    content: userInput,
    kb_ids: selectedKbIds,  // 可选
  }),
});

const reader = resp.body.getReader();
const decoder = new TextDecoder();
let buf = '';
while (true) {
  const {value, done} = await reader.read();
  if (done) break;
  buf += decoder.decode(value, {stream: true});
  // 按 SSE \n\n 分帧
  let idx;
  while ((idx = buf.indexOf('\n\n')) >= 0) {
    const frame = buf.slice(0, idx);
    buf = buf.slice(idx + 2);
    // 解析 event/data
    const lines = frame.split('\n');
    let eventName = 'message', data = null;
    for (const line of lines) {
      if (line.startsWith('event:')) eventName = line.slice(6).trim();
      if (line.startsWith('data:')) data = JSON.parse(line.slice(5).trim());
    }
    handleSSE(eventName, data);
  }
}

function handleSSE(eventName, data) {
  if (data.type === 'text') {
    // 追加 token 到聊天气泡
    appendToken(data.content);
  } else if (data.type === 'tool_start') {
    // 显示"正在检索..."
    showToolBadge(data.tool, data.args?._kb_ids);
  } else if (data.type === 'tool_end') {
    hideToolBadge(data.tool);
  } else if (data.type === 'done') {
    // 流结束，启动会话标题刷新（首轮场景）
  }
}
```

**错误响应**（非 200 时退回 JSON 格式）：
- `40400` session_id 不存在
- `40001` content 为空 / session_id 非 UUID

**重要约束**：
- ReAct 熔断：单次对话最多 5 轮工具调用（PRD AGT-03）
- 上下文窗口：自动加载最近 `CONTEXT_WINDOW_MESSAGES`（默认 20）条历史消息 + 全部 system 消息（PRD SES-09）
- 首轮 AI 回复完成后自动异步生成会话标题（SES-07）—— 10s 内通过 GET /sessions/{id} 能看到 title 填充

---

## 3. 知识库管理（Knowledge Base）

### 3.1 创建知识库 `POST /api/v1/knowledge-bases`

| | |
|---|---|
| **PRD** | KB-01 |
| **HTTP 状态** | 201 Created |

同步完成：① PG 写元数据 ② Milvus 创建独立 Collection（命名 `kb_{uuid.hex}`，含 HNSW 索引 + load）。任一步失败整体回滚。

**请求体**：

```json
{
  "name": "气象库（必填，1~128 字符，全局唯一）",
  "description": "可选描述（≤500 字符）",
  "embedding_dim": 4096,
  "chunk_size": 512,
  "chunk_overlap": 64
}
```

| 字段 | 必填 | 默认 | 约束 |
|---|---|---|---|
| `name` | ✅ | — | 1~128 字符，去空白后非空，全局唯一 |
| `description` | ❌ | null | ≤500 字符 |
| `embedding_dim` | ❌ | 4096 | >0，**创建后不可修改** |
| `chunk_size` | ❌ | 512 | 128~2048 |
| `chunk_overlap` | ❌ | 64 | ≥0，不超 chunk_size 的 50% |

**响应** `data` 字段：

```json
{
  "id": "kb-uuid",
  "name": "气象库",
  "description": "...",
  "embedding_dim": 4096,
  "chunk_size": 512,
  "chunk_overlap": 64,
  "status": "active",
  "file_count": 0,
  "chunk_count": 0,
  "entity_count": 0,
  "created_at": "2026-06-11T12:00:00+00:00"
}
```

**错误**：
- `40001` 参数校验失败（name 空白 / 超长 / chunk_size 越界 / overlap 超 size/2）
- `40900` name 已存在（含并发场景的二次冲突）
- `50000` Milvus 创建 Collection 失败

---

### 3.2 知识库列表 `GET /api/v1/knowledge-bases`

| | |
|---|---|
| **PRD** | KB-02 |

**Query 参数**：`page` / `page_size`（同 1.2 会话列表）。

**响应** `data` 字段：

```json
{
  "items": [
    {
      "id": "kb-uuid",
      "name": "气象库",
      "description": "...",
      "file_count": 5,
      "chunk_count": 1200,
      "status": "active",
      "created_at": "2026-06-11T12:00:00+00:00"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 8
}
```

**排序**：按 `created_at` 倒序 + id tie-breaker。

> ⚠️ 列表项**不含** `entity_count`（避免每条都查 Neo4j）；如需，请走 KB-03 详情。

---

### 3.3 知识库详情 `GET /api/v1/knowledge-bases/{kb_id}`

| | |
|---|---|
| **PRD** | KB-03 |

**响应** `data` 字段同 3.1（含 `entity_count` 实时查 Neo4j）。

`entity_count`：该 KB 在 Neo4j 中的 `Entity` 节点总数。失败时返 0 + 日志告警（不阻塞详情接口）。

**错误**：
- `40400` kb_id 不存在

---

### 3.4 修改知识库 `PATCH /api/v1/knowledge-bases/{kb_id}`

| | |
|---|---|
| **PRD** | KB-04 |

**仅 `name` / `description` 可改**。`embedding_dim` / `chunk_size` / `chunk_overlap` 传入 → 422 拦截（要改这些字段必须 KB-05 删除重建）。

**请求体**（name 与 description 至少传一个；description 显式传 null 表示清空）：

```json
{
  "name": "新名称",            // 可选
  "description": "新描述"       // 可选，null 表示清空
}
```

**响应** `data` 同 3.1。

**错误**：
- `40001` 两个字段都不传 / name 空白 / 超长 / 传入只读字段
- `40400` kb_id 不存在
- `40900` 新 name 与其它 KB 冲突

---

### 3.5 删除知识库 `DELETE /api/v1/knowledge-bases/{kb_id}`

| | |
|---|---|
| **PRD** | KB-05 |

**完全清理该 KB 的所有资源**，严格按以下顺序（PRD §3.2 强制）：

1. **revoke** 所有该 KB 下 processing 状态文件的 Celery 任务
2. **Milvus** drop Collection（不可逆，最先做）
3. **Neo4j** `MATCH (n {kb_id: $kb_id}) DETACH DELETE n`（清整个 KB 子图）
4. **PostgreSQL** 删 `knowledge_bases` 记录（外键级联删 `kb_files`）
5. **磁盘** 清空 `{UPLOAD_DIR}/{kb_id}/` 整个目录树

> ⚠️ **不可撤销，前端务必做二次确认弹窗**。

**响应** `data: null`，message="知识库已删除"。

**错误**：
- `40400` kb_id 不存在
- `50000` Milvus drop 失败（整体回滚返 500） / PG 删除失败后已 Milvus 已丢（日志告警，仍返 500 提示人工介入）

---

## 4. 文件管理（KB Files）

### 4.1 上传文件 `POST /api/v1/knowledge-bases/{kb_id}/files`

| | |
|---|---|
| **PRD** | FILE-01 |
| **HTTP 状态** | 201 Created |
| **Content-Type** | `multipart/form-data` |

**请求**：

multipart 字段 `file`：上传文件本身。

**支持格式与大小限制**：

| 格式 | MIME（参考） | 解析库 |
|---|---|---|
| `.pdf` | application/pdf | PyMuPDF |
| `.docx` | application/vnd.openxmlformats-officedocument...wordprocessingml.document | python-docx |
| `.md` | text/markdown / text/plain / application/octet-stream | markdown-it-py |
| `.txt` | text/plain | 内置 open() UTF-8 |

- 单文件最大 50 MB（由 `MAX_FILE_SIZE_MB` 控制，边读边量防 Content-Length 欺骗）
- `.doc` 暂不支持（V1.5 推迟，415 提示"请另存为 docx"）
- 同名文件**允许**：磁盘按 `file_id` 隔离 `{UPLOAD_DIR}/{kb_id}/{file_id}/{filename}`，PG 里多条不同 id 的记录

**响应** `data` 字段（同 4.3 文件详情）：

```json
{
  "id": "file-uuid",
  "kb_id": "kb-uuid",
  "filename": "report.pdf",
  "file_size": 2459871,
  "mime_type": "application/pdf",
  "status": "pending",
  "progress": 0,
  "chunk_count": 0,
  "entity_count": 0,
  "error_message": null,
  "celery_task_id": "task-uuid",
  "created_at": "2026-06-11T12:00:00+00:00",
  "completed_at": null
}
```

立即返回 `file_id`，**不等入库完成**。前端按 4.3 接口 2 秒轮询观察进度。

**错误**：
- `40400` kb_id 不存在
- `40001` filename 缺失
- `41300` 文件超过 50MB
- `41500` 格式不在白名单
- `50000` 磁盘写入失败 / PG 写元数据失败

---

### 4.2 文件列表 `GET /api/v1/knowledge-bases/{kb_id}/files`

| | |
|---|---|
| **PRD** | FILE-02 |

**Query 参数**：`page` / `page_size`。

**响应** `data` 字段：

```json
{
  "items": [
    {
      "id": "file-uuid",
      "filename": "report.pdf",
      "file_size": 2459871,
      "mime_type": "application/pdf",
      "status": "completed",
      "progress": 100,
      "chunk_count": 56,
      "created_at": "2026-06-11T12:00:00+00:00",
      "completed_at": "2026-06-11T12:02:30+00:00"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 12
}
```

**排序**：按 `created_at` 倒序 + id tie-breaker。

> ⚠️ 列表项**不含** `error_message` / `celery_task_id` / `entity_count`（避免列表体积过大），查这些字段走详情接口。

---

### 4.3 文件详情 + 进度 `GET /api/v1/knowledge-bases/{kb_id}/files/{file_id}`

| | |
|---|---|
| **PRD** | FILE-03 |

**响应** `data` 字段同 4.1（含 `error_message` / `entity_count` / `celery_task_id` 等完整字段）。

**`progress` 字段值对应阶段**（PRD §3.4 TASK-02）：

| progress | 阶段 |
|---|---|
| 0 | 等待 worker 调度（pending） |
| 20 | 文档解析完成 |
| 35 | 文本切片完成 |
| 60 | 向量嵌入完成 |
| 80 | Milvus 写入完成 |
| 90 | NER 实体抽取完成 |
| 95 | Neo4j 写入完成 |
| 100 | 全部完成（completed） |

**`status` 取值**：
- `pending`：已上传，等待 worker
- `processing`：worker 执行中
- `completed`：入库完成（progress=100）
- `failed`：入库失败（error_message 非空）

**前端建议**：
- 2 秒轮询
- progress 单调递增；status=completed 或 failed 即停止轮询
- failed 时 `error_message` 展示给用户，并提供"重新入库"按钮（→ 调 4.5 reindex）

**错误**：
- `40400` kb_id 或 file_id 不存在 / file 不属于该 kb

---

### 4.4 删除文件 `DELETE /api/v1/knowledge-bases/{kb_id}/files/{file_id}`

| | |
|---|---|
| **PRD** | FILE-04 |

级联清理顺序：

1. 若 status=processing → `celery_app.control.revoke(task_id, terminate=True)`
2. **Milvus**：按 `document_id == file_id` 过滤删除该文件的所有切片
3. **Neo4j**：`MATCH (n:Document {document_id, kb_id}) DETACH DELETE n`（Entity 节点保持复用不删）
4. **PG**：删 `kb_files` 记录 + KB.file_count -= 1 + chunk_count -= 该文件 chunk 数
5. **磁盘**：删除原始文件 + 空目录

**响应** `data: null`。

**错误**：
- `40400` file 不存在 / 不属于该 KB
- `50000` PG 删除失败

---

### 4.5 重新入库 `POST /api/v1/knowledge-bases/{kb_id}/files/{file_id}/reindex`

| | |
|---|---|
| **PRD** | FILE-05 |

清除该文件的旧切片 + 重置状态 + 触发新入库任务（**保留磁盘文件**）。适用：入库失败需重试、KB 配置更新后需重建。

清理顺序同 FILE-04 的 1~4 步（不含步骤 5 磁盘），然后：

5. 重置 `kb_files` 行：`status=pending, progress=0, chunk_count=0, entity_count=0, error_message=null, completed_at=null, celery_task_id=<新>`
6. 触发新 `parse_and_ingest_task.delay(file_id, kb_id)`

**响应** `data` 字段同 4.3，message="重建任务已提交"。

**错误**：
- `40400` file_id 不存在 / **磁盘文件已丢失**（提示用户重新上传）

---

## 5. 健康检查

### 5.1 健康检查 `GET /health`

返回 `{"status": "ok"}`（**不**走统一响应格式，因为是基础设施级别探针）。

部署时建议加 LB / k8s readiness probe 用此端点。

---

## 6. 附录

### 6.1 在线交互文档

服务启动后访问：
- [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) - Swagger UI（可在浏览器直接试调用）
- [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc) - ReDoc 静态文档
- [http://127.0.0.1:8000/openapi.json](http://127.0.0.1:8000/openapi.json) - OpenAPI 3.x 规范（前端可基于此自动生成 TS SDK，如 `openapi-typescript`）

### 6.2 异步任务说明（PRD §3.4）

V1.5 引入 Celery + Redis 处理三类异步任务：

| 任务 | 触发 | 接口 / 时机 |
|---|---|---|
| 文件入库管道（7 步） | 4.1 上传成功后自动 | parse_and_ingest_task |
| 会话标题生成 | 首轮 AI 回复完成自动 | generate_session_title_task |
| 会话摘要生成 | 1.7 接口主动调用 | generate_session_summary_task |

前端无需感知 Celery；通过轮询业务接口（4.3 文件详情 / 1.3 会话详情）即可观察异步结果。

### 6.3 部署提示

完整启动一套服务需要 4 个进程 + 4 个容器：

```
[Docker 容器]
  postgres:17-alpine  ──→ PG 数据库
  redis:7-alpine      ──→ Celery broker + result backend
  milvus-standalone   ──→ 向量库
  neo4j:5.26          ──→ 知识图谱

[Python 进程]
  uvicorn app.main:app   ──→ FastAPI HTTP 服务（含 lifespan 初始化 Milvus/Neo4j）
  celery worker          ──→ 异步任务消费者
```

详见 [docs/celery_dev_guide.md](celery_dev_guide.md) 和 [docker-compose/docker-compose.yml](../docker-compose/docker-compose.yml)。

---

*TyAgent V1.5 API · End of Document*

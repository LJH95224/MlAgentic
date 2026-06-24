# TyAgent · 全项目接口文档

> **适用范围**：当前后端实际注册的全部接口（V1.0 / V1.5 / V2.0），不再只面向 V2.0 Hermes。
> **代码依据**：路由聚合见 [app/api/v1/router.py](../app/api/v1/router.py) 与 [app/api/v2/router.py](../app/api/v2/router.py)，应用挂载见 [app/main.py](../app/main.py)。
> **在线交互**：服务启动后访问 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) / [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)。
> **历史说明**：本文沿用 `v2_api_reference.md` 文件名，但内容已调整为“全项目接口对接文档”。

---

## 0. 接口状态总览

### 0.1 BaseURL

| 环境 | URL |
|---|---|
| 本地开发 | `http://127.0.0.1:8000` |
| 测试/生产 | 由部署方决定，接口路径保持 `/api/v1`、`/api/v2` 前缀 |

### 0.2 响应格式现实约定

当前项目存在两类成功响应形态，前端对接时需要区分：

| 接口范围 | 成功响应 | 失败响应 |
|---|---|---|
| `/api/v1/**` | 统一 `{code, message, data}` | 统一 `{code, message, data:null}` |
| `/api/v2/analytics` | 统一 `{code, message, data}` | 统一 `{code, message, data:null}` |
| `/api/v2/query`、`/api/v2/retrieve`、`/api/v2/generate`、`/api/v2/rerank`、`/api/v2/traces/**`、`/api/v2/knowledge-bases/**/evaluate*` | 直接返回业务对象（不包 `code/data`） | 统一异常处理器返回 `{code, message, data:null}` |
| `/health` | `{"status":"ok"}` | 标准 HTTP 错误 |
| `/api/v1/chat/stream` | SSE 事件流 | 建流前失败返回 `{code, message, data:null}` |

前端建议封装一个兼容解析函数：

```ts
function unwrap<T>(resp: T | { code: number; message: string; data: T }): T {
  if (resp && typeof resp === 'object' && 'code' in resp && 'data' in resp) {
    const boxed = resp as { code: number; message: string; data: T };
    if (boxed.code !== 0) throw new Error(boxed.message);
    return boxed.data;
  }
  return resp as T;
}
```

### 0.3 通用错误码

| HTTP | 业务 code | 含义 | 常见触发场景 |
|---|---:|---|---|
| 400 | 40001 | PARAM_INVALID | 参数校验失败、空字符串、分页越界等 |
| 400 | 40002 | IMMUTABLE_FIELD | 修改只读字段 |
| 400 | 40011 | QUERY_REWRITE_INVALID | `query_rewrite` 不是 `none` / `hyde` / `multi_query` |
| 400 | 40012 | EVAL_DATASET_EMPTY | 评估集为空 |
| 400 | 40013 | EVAL_DATASET_TOO_LARGE | 评估题数超过 `EVAL_MAX_QUESTIONS` |
| 404 | 40400 | NOT_FOUND | session / kb / file / trace / eval_task 不存在 |
| 409 | 40900 | NAME_CONFLICT | 知识库名称冲突 |
| 413 | 41300 | FILE_TOO_LARGE | 上传文件超过限制 |
| 415 | 41500 | UNSUPPORTED_MEDIA | 上传了不支持的文件类型 |
| 422 | 42200 | EMBEDDING_DIM_MISMATCH | 向量维度不匹配 |
| 422 | 42201 | CONTEXT_CHUNKS_EMPTY | `/api/v2/generate` 的 `context_chunks` 为空 |
| 500 | 50000 | INTERNAL_ERROR | 未捕获内部错误 |
| 503 | 50300 | CELERY_UNAVAILABLE | Redis / Celery worker 不可用 |

### 0.4 当前仍在使用的接口

| 模块 | Method | Path | 状态 | 前端用途 |
|---|---|---|---|---|
| 健康检查 | GET | `/health` | ✅ 使用中 | 服务可用性探测 |
| 会话 | POST | `/api/v1/sessions` | ✅ 使用中 | 创建会话 |
| 会话 | GET | `/api/v1/sessions` | ✅ 使用中 | 会话列表 |
| 会话 | GET | `/api/v1/sessions/{session_id}` | ✅ 使用中 | 会话详情 |
| 会话 | PATCH | `/api/v1/sessions/{session_id}` | ✅ 使用中 | 修改标题 |
| 会话 | DELETE | `/api/v1/sessions/{session_id}` | ✅ 使用中 | 删除会话 |
| 会话 | GET | `/api/v1/sessions/{session_id}/messages` | ✅ 使用中 | 历史消息 |
| 会话 | POST | `/api/v1/sessions/{session_id}/summarize` | ✅ 使用中 | 主动生成摘要 |
| 对话 | POST | `/api/v1/chat/stream` | ✅ 使用中 | 流式 Agent 对话 |
| 知识库 | POST | `/api/v1/knowledge-bases` | ✅ 使用中 | 创建 KB |
| 知识库 | GET | `/api/v1/knowledge-bases` | ✅ 使用中 | KB 列表 |
| 知识库 | GET | `/api/v1/knowledge-bases/{kb_id}` | ✅ 使用中 | KB 详情 |
| 知识库 | PATCH | `/api/v1/knowledge-bases/{kb_id}` | ✅ 使用中 | 修改 KB / 检索配置 |
| 知识库 | DELETE | `/api/v1/knowledge-bases/{kb_id}` | ✅ 使用中 | 删除 KB |
| 文件 | POST | `/api/v1/knowledge-bases/{kb_id}/files` | ✅ 使用中 | 上传文件并入库 |
| 文件 | GET | `/api/v1/knowledge-bases/{kb_id}/files` | ✅ 使用中 | 文件列表 |
| 文件 | GET | `/api/v1/knowledge-bases/{kb_id}/files/{file_id}` | ✅ 使用中 | 文件详情/入库进度 |
| 文件 | DELETE | `/api/v1/knowledge-bases/{kb_id}/files/{file_id}` | ✅ 使用中 | 删除文件 |
| 文件 | POST | `/api/v1/knowledge-bases/{kb_id}/files/{file_id}/reindex` | ✅ 使用中 | 重新入库 |
| RAG 查询 | POST | `/api/v2/query` | ✅ 使用中 | 非流式可信 RAG 问答 |
| RAG 子能力 | POST | `/api/v2/retrieve` | ✅ 使用中 | 纯检索调试/自定义链路 |
| RAG 子能力 | POST | `/api/v2/generate` | ✅ 使用中 | 自带上下文生成答案 |
| RAG 子能力 | POST | `/api/v2/rerank` | ✅ 使用中 | 独立文本精排 |
| Trace | GET | `/api/v2/traces/{trace_id}` | ✅ 使用中 | 单次请求链路详情 |
| Trace | GET | `/api/v2/traces/sessions/{session_id}/traces` | ✅ 使用中 | 会话下 trace 列表 |
| 评估 | POST | `/api/v2/knowledge-bases/{kb_id}/evaluate` | ✅ 使用中 | 创建 RAGAS 评估任务 |
| 评估 | GET | `/api/v2/knowledge-bases/{kb_id}/evaluations/{eval_task_id}` | ✅ 使用中 | 评估进度/结果 |
| 评估 | GET | `/api/v2/knowledge-bases/{kb_id}/evaluations` | ✅ 使用中 | 评估历史 |
| Analytics | GET | `/api/v2/analytics` | ✅ 使用中 | 查询质量仪表盘 |

### 0.5 接口取舍与替换关系

整理前端对接时，按下面规则处理“旧接口 / 新接口 / 不用接口”：

| 原接口/能力 | 当前处理 | 使用哪个接口 | 说明 |
|---|---|---|---|
| `POST /api/v1/chat/stream` | ✅ 继续使用 | `POST /api/v1/chat/stream` | 仍是当前唯一流式聊天接口，主聊天页、打字机效果、工具徽章都用它 |
| `POST /api/v2/query` | ✅ 新增使用 | `POST /api/v2/query` | 不是替代 V1 流式聊天，而是用于非流式可信 RAG：Citation、confidence、trace |
| “聊天时不传 `kb_ids`” | ⚠️ 不推荐 | 仍用 `/api/v1/chat/stream`，但显式传 `kb_ids` | 新前端必须明确传当前选中 KB；纯聊天传 `[]`，不要依赖 V1.0 全局检索兼容行为 |
| V1.5 KB CRUD | ✅ 继续使用 | `/api/v1/knowledge-bases/**` | 仍是知识库管理主入口；V2 只扩展了 `retrieval_config` 字段，没有新的 KB CRUD 替代接口 |
| 旧 KB 更新只改 `name/description` | ✅ 用更新后的同一接口 | `PATCH /api/v1/knowledge-bases/{kb_id}` | 新版同一接口额外支持 `retrieval_config`，用于保存 KB 级检索默认配置 |
| V1.5 文件接口 | ✅ 继续使用 | `/api/v1/knowledge-bases/{kb_id}/files/**` | 仍是文件管理主入口；新版响应额外有 `summary_brief`、`doc_metadata`、入库 warnings |
| V1.5 入库任务旧实现 | ❌ 不对接 | 无接口 | `app/tasks/ingest_task_v1.py` 只是历史归档，当前运行入口是新版 V2 入库链路 |
| `/api/v2/retrieve` | 🛠️ 开发/调试使用 | `POST /api/v2/retrieve` | 不放普通聊天页；用于检索调试、召回诊断、自定义 RAG 链路 |
| `/api/v2/generate` | 🛠️ 开发/调试使用 | `POST /api/v2/generate` | 前端普通用户不用；仅当开发者自己提供 context_chunks 时使用 |
| `/api/v2/rerank` | 🛠️ 开发/调试使用 | `POST /api/v2/rerank` | 不作为普通业务页核心接口；当前 reranker 可能配置为 noop |
| 旧文档里的 `/api/v2/sessions/{session_id}/traces` | ❌ 不用 | `GET /api/v2/traces/sessions/{session_id}/traces` | 实际代码注册路径是 `/api/v2/traces/sessions/{session_id}/traces` |
| V2 `stream=true` | ❌ 不用 | 无 | `/api/v2/query.options.stream`、`/api/v2/generate.options.stream` 当前都是预留字段，不要在 UI 暴露 |
| `v1_5_api_reference.md` / `v1_5_frontend_guide.md` | 🗄️ 历史参考 | 本文为准 | 历史分册不再作为前端当前对接入口 |

### 0.6 已废弃 / 不推荐继续对接的能力

| 类型 | 名称 | 状态 | 说明 |
|---|---|---|---|
| 代码归档 | `app/tasks/ingest_task_v1.py` | 🗄️ 历史归档 | V1.5 入库任务旧实现，仅供参考，不再作为运行入口 |
| 兼容行为 | `/api/v1/chat/stream` 不传 `kb_ids` 的 V1.0 全局检索模式 | ⚠️ 兼容保留，不推荐 | 新前端应显式传入选中的 `kb_ids`；若只想纯聊天，可传空数组 `[]` |
| 预留字段 | `/api/v2/query.options.stream` | ⚠️ 预留，不可用 | 当前 V2 query 只支持非流式；流式体验仍用 `/api/v1/chat/stream` |
| 预留字段 | `/api/v2/generate.options.stream` | ⚠️ 预留，不可用 | 当前 generate 只返回一次性 JSON |
| 旧 Trace 路径 | `/api/v2/sessions/{session_id}/traces` | ❌ 不存在/不用 | 实际路径是 `/api/v2/traces/sessions/{session_id}/traces` |
| 文档分册 | `v1_5_api_reference.md` / `v1_5_frontend_guide.md` | 🗄️ 历史参考 | 当前对接以本文和 [v2_frontend_guide.md](v2_frontend_guide.md) 为准 |

---

## 1. 健康检查

### GET /health

**功能**：检查 FastAPI 进程是否存活。

**成功响应**：

```json
{"status": "ok"}
```

---

## 2. V1 会话与流式对话

### 2.1 POST /api/v1/sessions

**功能**：创建会话。

**请求体**（可省略）：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | string | 否 | 初始标题；不传则后续首轮对话可异步生成 |

**成功响应**：`ApiResponse<SessionDetail>`，HTTP 201。

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "title": null,
    "summary": null,
    "message_count": 0,
    "created_at": "2026-06-18T10:00:00+00:00",
    "updated_at": "2026-06-18T10:00:00+00:00"
  }
}
```

### 2.2 GET /api/v1/sessions

**Query 参数**：`page` 默认 1，`page_size` 默认 20，范围 1~100。

**成功响应**：`ApiResponse<SessionListResponse>`。

### 2.3 GET /api/v1/sessions/{session_id}

**功能**：查询会话详情。不存在返回 `40400`。

### 2.4 PATCH /api/v1/sessions/{session_id}

**功能**：修改会话标题。

```json
{"title": "新的会话标题"}
```

### 2.5 DELETE /api/v1/sessions/{session_id}

**功能**：物理删除会话及其消息。不会删除知识库、Milvus 或 Neo4j 数据。

### 2.6 GET /api/v1/sessions/{session_id}/messages

**功能**：按 `created_at` 正序返回历史消息。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `limit` | int | 20 | 1~100 |
| `before` | UUID | 无 | 游标：返回该消息之前的更早消息 |

**响应 data**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `items` | MessageItem[] | 消息列表 |
| `has_more` | bool | 是否还有更早消息 |
| `next_before` | UUID/null | 下一页游标 |

### 2.7 POST /api/v1/sessions/{session_id}/summarize

**功能**：主动触发会话摘要生成，立即返回 Celery `task_id`，后台更新 `summary` / `summarized_at`。

**成功响应**：HTTP 202。

```json
{
  "code": 0,
  "message": "摘要生成任务已提交",
  "data": {"task_id": "celery-task-id"}
}
```

### 2.8 POST /api/v1/chat/stream

**功能**：SSE 流式 Agent 对话。适合正式聊天页面的打字机体验、工具调用徽章展示。

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `session_id` | UUID | 是 | 会话 ID |
| `content` | string | 是 | 用户输入 |
| `kb_ids` | UUID[]/null | 否 | 推荐显式传入当前选中的 KB 列表；空数组表示不检索 KB |

**SSE 事件**：

| event | data.type | 说明 |
|---|---|---|
| `message` | `text` | 文本增量，字段 `content` |
| `control` | `tool_start` | 工具开始，字段 `tool`、`args` |
| `control` | `tool_end` | 工具结束，字段 `tool`、`output` |
| `message` | `done` | 流结束 |

**curl 示例**：

```bash
curl -N -X POST http://127.0.0.1:8000/api/v1/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "content": "总结这个知识库里的台风资料",
    "kb_ids": ["11111111-1111-1111-1111-111111111111"]
  }'
```

---

## 3. V1 知识库管理

### 3.1 POST /api/v1/knowledge-bases

**功能**：创建知识库，同步创建 PostgreSQL 记录与 Milvus Collection。

**请求体**：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | 是 | - | 全局唯一，1~128 字符 |
| `description` | string/null | 否 | null | ≤500 字符 |
| `embedding_dim` | int | 否 | 4096 | 创建后不可修改 |
| `chunk_size` | int | 否 | 512 | 128~2048 |
| `chunk_overlap` | int | 否 | 64 | 不超过 `chunk_size` 的 50% |

**成功响应**：`ApiResponse<KnowledgeBaseDetail>`，HTTP 201。

### 3.2 GET /api/v1/knowledge-bases

**功能**：分页获取知识库列表。

| 参数 | 类型 | 默认 | 范围 |
|---|---|---|---|
| `page` | int | 1 | ≥1 |
| `page_size` | int | 20 | 1~100 |

### 3.3 GET /api/v1/knowledge-bases/{kb_id}

**功能**：获取知识库详情，包含 `file_count`、`chunk_count`、`entity_count`、`retrieval_config`。

### 3.4 PATCH /api/v1/knowledge-bases/{kb_id}

**功能**：修改知识库名称、描述，以及 V2 检索默认配置。`embedding_dim` / `chunk_size` / `chunk_overlap` 创建后只读，不允许修改。

**请求体**：三者至少传一个。

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 新名称 |
| `description` | string/null | 新描述；显式 null 表示清空 |
| `retrieval_config` | object/null | V2 检索配置；`{}` 清空覆盖字段 |

`retrieval_config` 支持字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `top_k` | int | 默认返回结果数 |
| `reranker_enable` | bool | 是否启用 Reranker |
| `bm25_enable` | bool | 是否启用 BM25 |
| `query_rewrite` | string | `none` / `hyde` / `multi_query` |
| `enable_graph_rag` | bool | 是否启用 Graph RAG |
| `enable_faithfulness_check` | bool | 是否启用答案自检 |
| `similarity_threshold` | float | Reranker 过滤阈值 |
| `rerank_top_n` | int | Reranker 候选输入数 |

配置合并优先级：`API options > KB.retrieval_config > 全局 settings`。

### 3.5 DELETE /api/v1/knowledge-bases/{kb_id}

**功能**：删除知识库及其相关资源。不可撤销，前端必须二次确认。

---

## 4. V1 文件管理与异步入库

### 4.1 POST /api/v1/knowledge-bases/{kb_id}/files

**功能**：上传文件并触发 Celery 异步入库。不会等待解析/切片/向量化完成。

**请求格式**：`multipart/form-data`，字段名 `file`。

**成功响应**：`ApiResponse<FileDetail>`，HTTP 201，初始 `status=pending`、`progress=0`。

```bash
curl -X POST http://127.0.0.1:8000/api/v1/knowledge-bases/{kb_id}/files \
  -F 'file=@./docs/sample.pdf'
```

### 4.2 GET /api/v1/knowledge-bases/{kb_id}/files

**功能**：分页获取文件列表。

**列表项关键字段**：`id`、`filename`、`file_size`、`mime_type`、`status`、`progress`、`chunk_count`、`summary_brief`、`created_at`、`completed_at`。

### 4.3 GET /api/v1/knowledge-bases/{kb_id}/files/{file_id}

**功能**：查询文件详情和入库进度。前端可每 2 秒轮询，直到 `status=completed|failed`。

**详情额外字段**：`entity_count`、`doc_metadata`、`error_message`、`celery_task_id`。

`doc_metadata` 常见字段：`doc_type`、`doc_date`、`language`、`key_topics`、`summary_brief`；失败补偿场景可能带 `_ingest_warnings`。

### 4.4 DELETE /api/v1/knowledge-bases/{kb_id}/files/{file_id}

**功能**：删除文件及其切片、图谱、磁盘文件、PG 记录。

### 4.5 POST /api/v1/knowledge-bases/{kb_id}/files/{file_id}/reindex

**功能**：重新入库。会清理旧切片、重置状态并触发新 Celery 任务。

---

## 5. V2 可信 RAG 查询接口

### 5.1 POST /api/v2/query

**功能**：统一 RAG 查询入口。执行三层配置合并 → Query 改写 → NER → 图谱锚定 → 混合检索 → LLM 生成 → Citation → 置信度/自检 → Trace/Analytics。

**成功响应形态**：直接返回 `QueryResponse`，不包 `code/data`。

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `query` | string | 是 | 1~2000 字符 |
| `session_id` | UUID/null | 否 | 用于 trace 绑定到会话 |
| `kb_ids` | UUID[]/null | 否 | 推荐传当前 KB；多 KB 时当前配置合并取第一个 KB |
| `options` | QueryOptions | 否 | 查询选项 |

**QueryOptions**：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `top_k` | int/null | 跟随下层 | 1~50 |
| `reranker_enable` | bool/null | 跟随下层 | 是否启用精排 |
| `bm25_enable` | bool/null | 跟随下层 | 是否启用 BM25 |
| `stream` | bool | false | 预留；当前不要传 true |
| `query_rewrite` | string/null | 跟随下层 | `none` / `hyde` / `multi_query` |
| `enable_graph_rag` | bool/null | 跟随下层 | 是否启用图谱锚定 |
| `similarity_threshold` | float/null | 跟随下层 | 0~1 |
| `enable_faithfulness_check` | bool/null | 跟随下层 | 是否启用答案自检 |

**响应字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `answer` | string | 答案文本，可能包含 `[1]` 引用标记 |
| `source_citations` | CitationItem[] | 引用来源 |
| `trace_id` | string/null | 可用于查询 trace |
| `total_latency_ms` | int/null | 总耗时 |
| `rewritten_query` | string/null | HyDE 结果 |
| `sub_queries` | string[]/null | multi_query 子问题 |
| `ner_entities` | object[]/null | Query NER 结果 |
| `graph_anchored_tags` | string[]/null | 图谱锚定标签 |
| `confidence` | float/null | 0~1 置信度 |
| `low_confidence_warning` | string/null | 低置信度提示 |
| `faithfulness_check` | string/null | `ok` / `skipped` / `disabled` |
| `unverified_claims` | object[]/null | 未证实声明 |

**CitationItem**：`chunk_id`、`document_name`、`page_number`、`heading_path`、`snippet`、`rerank_score`。

**示例**：

```bash
curl -X POST http://127.0.0.1:8000/api/v2/query \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "2024 年台风生成数量是多少？",
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "kb_ids": ["11111111-1111-1111-1111-111111111111"],
    "options": {
      "query_rewrite": "none",
      "top_k": 5,
      "enable_graph_rag": false,
      "enable_faithfulness_check": true
    }
  }'
```

### 5.2 POST /api/v2/retrieve

**功能**：只检索，不调用 LLM。用于调试召回、构建自定义 RAG 链路。

**成功响应形态**：直接返回 `RetrieveResponse`。

**请求体**：`query`、`kb_ids`、`top_k`、`enable_graph_rag`、`enable_bm25`、`rerank`、`similarity_threshold`。

**响应字段**：`chunks`、`total_retrieved`、`after_rerank`、`trace_id`、`total_latency_ms`。

### 5.3 POST /api/v2/generate

**功能**：开发者自带 `context_chunks`，后端只做生成、Citation、置信度、自检；不访问 Milvus / Neo4j。

**成功响应形态**：直接返回 `GenerateResponse`。

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `query` | string | 是 | 用户问题 |
| `context_chunks` | ContextChunk[] | 是 | 至少 1 条，否则 `42201` |
| `options.enable_citation` | bool | 否 | 默认 true |
| `options.enable_faithfulness_check` | bool | 否 | 默认 false |
| `options.stream` | bool | 否 | 预留；当前不支持 |

### 5.4 POST /api/v2/rerank

**功能**：独立精排。当前生产配置可能为 `RERANKER_TYPE=none`，此时返回 Noop 分数/原序，前端不要把它当作绝对质量指标。

**请求体**：

```json
{
  "query": "2024 年台风生成数量",
  "candidates": [
    {"id": "doc1", "text": "2024 年共有 25 个台风生成。"},
    {"id": "doc2", "text": "台风是一种热带气旋。"}
  ],
  "top_n": 2
}
```

**成功响应形态**：直接返回 `{results,total_latency_ms}`。

---

## 6. V2 Trace 可观测性

### 6.1 GET /api/v2/traces/{trace_id}

**功能**：查询单条 trace 的完整步骤链路。不存在返回 `40400`。

**成功响应形态**：直接返回 `TraceDetail`。

**TraceStep 字段**：`id`、`step_type`、`parent_step`、`step_latency_ms`、`step_input`、`step_output`、`model_name`、`token_count`、`error_message`、`created_at`。

### 6.2 GET /api/v2/traces/sessions/{session_id}/traces

**功能**：查询某个会话下的 trace 列表。

| 参数 | 类型 | 默认 | 范围 |
|---|---|---|---|
| `page` | int | 1 | ≥1 |
| `page_size` | int | 20 | 1~100 |

---

## 7. V2 RAGAS 评估

### 7.1 POST /api/v2/knowledge-bases/{kb_id}/evaluate

**功能**：创建 RAGAS 评估任务。立即返回 `eval_task_id`，实际评估由 Celery worker 异步执行。

**成功响应形态**：直接返回 `EvalCreateResponse`。

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `eval_set` | EvalQAItem[] | 是 | QA 对列表，最大 `EVAL_MAX_QUESTIONS` |
| `retrieval_options` | object | 否 | 评估时检索参数快照 |
| `name` | string | 否 | 任务名称，≤256 字符 |

**EvalQAItem**：`question`（1~2000 字符）、`ground_truth`（1~4000 字符）。

### 7.2 GET /api/v2/knowledge-bases/{kb_id}/evaluations/{eval_task_id}

**功能**：查询评估进度与结果。

**关键字段**：`status`（pending / processing / completed / failed）、`progress`、`summary`、`details`、`retrieval_options`、`error_message`。

### 7.3 GET /api/v2/knowledge-bases/{kb_id}/evaluations

**功能**：分页查询评估历史。按 `created_at desc, id desc` 排序。

---

## 8. V2 Analytics

### GET /api/v2/analytics

**功能**：查询 RAG 查询质量聚合指标。

**成功响应形态**：`ApiResponse<AnalyticsResponse>`。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `start_date` | date | 最近 7 天 | `YYYY-MM-DD` |
| `end_date` | date | 今天 | `YYYY-MM-DD` |
| `kb_id` | UUID | 无 | 按知识库过滤 |

**data 字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `total_queries` | int | 查询总数 |
| `avg_latency_ms` | float/null | 平均延迟 |
| `avg_confidence` | float/null | 平均置信度 |
| `low_confidence_rate` | float | 低置信度占比 |
| `tool_usage.graph_rag_triggered` | float | Graph RAG 触发率 |
| `tool_usage.bm25_contributed` | float | BM25 参与率 |
| `tool_usage.faithfulness_check_triggered` | float | 答案自检触发率 |
| `token_consumption.total_tokens` | int | Token 总量 |
| `avg_react_steps` | float/null | 平均步骤数 |
| `error_rate` | float | 错误率 |

---

## 9. 推荐前端对接策略

1. **聊天主流程**：优先用 `/api/v1/chat/stream` 做流式体验；如果需要 Citation、confidence、trace，则用 `/api/v2/query` 做非流式可信问答。
2. **知识库与文件管理**：只用 `/api/v1/knowledge-bases/**`，这是当前生产主入口。
3. **V2 能力页**：Trace、评估、Analytics 独立做开发者/运营页面，不影响主聊天链路。
4. **不要对接预留 stream 字段**：V2 的 `stream` 字段当前只是 schema 预留。
5. **统一错误处理**：无论成功是否包裹，失败基本都会返回 `{code,message,data:null}`；前端按 `code !== 0` 展示错误即可。

---

*TyAgent API Reference · End of Document*

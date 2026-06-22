# TyAgent · 全项目前端接口对接指南

> **适用范围**：当前 TyAgent 后端全量接口（V1.0 / V1.5 / V2.0），不再只描述 V2 新增模块。
> **配套文档**：[全项目接口文档](v2_api_reference.md) · [当前进度](progress.md) · [架构说明](architecture.md)
> **历史说明**：本文沿用 `v2_frontend_guide.md` 文件名，但内容已改为“全项目前端对接指南”。

---

## 0. 前端该怎么选接口

### 0.1 一句话结论

- **聊天打字机体验**：用 `POST /api/v1/chat/stream`。
- **带引用、置信度、Trace 的可信 RAG 问答**：用 `POST /api/v2/query`。
- **知识库、文件上传、入库进度**：用 `/api/v1/knowledge-bases/**`。
- **Trace、评估、Analytics**：用 `/api/v2/**` 对应运营/开发者页面。

### 0.2 当前接口形态差异

| 场景 | 接口 | 成功响应 | 是否流式 | 前端定位 |
|---|---|---|---|---|
| 流式聊天 | `/api/v1/chat/stream` | SSE | ✅ | 主聊天页推荐 |
| 非流式可信问答 | `/api/v2/query` | 直接业务对象 | ❌ | 引用/置信度/Trace 场景 |
| KB/文件/会话管理 | `/api/v1/**` | `{code,message,data}` | ❌ | 基础后台能力 |
| Trace/评估/Rerank/Retrieve/Generate | `/api/v2/**` 多数接口 | 直接业务对象 | ❌ | 开发者/运营/调试页 |
| Analytics | `/api/v2/analytics` | `{code,message,data}` | ❌ | 仪表盘 |

> 注意：V2 的 `options.stream` 字段是预留字段，当前不要依赖它做流式输出。

### 0.3 哪些接口不用了，哪些接口用更新的

| 功能 | 原先可能会用的接口/做法 | 现在怎么用 | 前端处理 |
|---|---|---|---|
| 主聊天发送消息 | `/api/v1/chat/stream` | 仍用 `/api/v1/chat/stream` | ✅ 继续使用；它仍是唯一流式聊天接口 |
| 聊天绑定知识库 | 不传 `kb_ids`，依赖旧全局检索 | `/api/v1/chat/stream` 请求体显式传 `kb_ids` | ⚠️ 改成显式传；纯聊天传 `[]` |
| 带引用/置信度问答 | 以前没有专门接口，可能想复用聊天流 | `/api/v2/query` | ✅ 新增一个“可信问答/RAG 调试”入口；不要拿它替代流式聊天 |
| 会话 CRUD | `/api/v1/sessions/**` | 仍用 `/api/v1/sessions/**` | ✅ 继续使用 |
| 知识库 CRUD | `/api/v1/knowledge-bases/**` | 仍用同一组 V1 接口 | ✅ 继续使用；不是废弃接口 |
| 保存检索参数 | 以前 KB 只改 name/description | `PATCH /api/v1/knowledge-bases/{kb_id}` 增加 `retrieval_config` | ✅ 用更新后的同一接口，不需要新建页面接口 |
| 文件上传/进度 | `/api/v1/knowledge-bases/{kb_id}/files/**` | 仍用同一组 V1 文件接口 | ✅ 继续使用；新版响应多了 `summary_brief` / `doc_metadata` |
| 文件入库旧任务 | `app/tasks/ingest_task_v1.py` | 不对接 | ❌ 后端归档代码，不是接口 |
| Trace 会话列表 | 旧设计写法 `/api/v2/sessions/{session_id}/traces` | 实际路径 `/api/v2/traces/sessions/{session_id}/traces` | ✅ 用实际注册路径 |
| V2 流式 | `options.stream=true` | 当前不用 | ❌ 不要在 UI 暴露；V2 query/generate 当前非流式 |
| 纯检索 | 没有或手拼 RAG | `/api/v2/retrieve` | 🛠️ 放开发者调试页，不放普通聊天主流程 |
| 自带上下文生成 | 没有 | `/api/v2/generate` | 🛠️ 开发者工具页使用 |
| 独立精排 | 没有 | `/api/v2/rerank` | 🛠️ 调试工具；当前后端可能 noop |
| 评估 | 没有 | `/api/v2/knowledge-bases/{kb_id}/evaluate*` | ✅ 放运营/评估页 |
| 查询质量统计 | 没有 | `/api/v2/analytics` | ✅ 放 Analytics 仪表盘 |

### 0.4 已废弃 / 不推荐前端对接

| 能力 | 处理建议 |
|---|---|
| 不传 `kb_ids` 走 V1.0 全局检索兼容模式 | 新前端应显式传当前选中的 `kb_ids`；纯聊天传 `[]` |
| `app/tasks/ingest_task_v1.py` | 后端历史归档，不是接口，不需要对接 |
| 旧 Trace 路径 `/api/v2/sessions/{session_id}/traces` | 不存在；使用 `/api/v2/traces/sessions/{session_id}/traces` |
| 旧的 `v1_5_*` 文档分册 | 可参考历史背景；实际对接以本文和 [v2_api_reference.md](v2_api_reference.md) 为准 |
| V2 `stream=true` | 暂不支持，不要在 UI 中暴露 |

---

## 1. 推荐前端架构

| 层 | 推荐选型 | 说明 |
|---|---|---|
| 框架 | React 18 + TypeScript | 与 OpenAPI 类型生成、SSE hook 生态匹配 |
| 路由 | React Router 6 | 页面数量适中 |
| 请求缓存 | TanStack Query | 会话/KB/文件/评估/Analytics 都适合 query/mutation 模式 |
| 全局状态 | Zustand | 存当前 session、当前 KB、开发者模式、最近 trace_id |
| UI | Ant Design 5 / shadcn-ui | 后台型页面组件齐全 |
| Markdown | react-markdown + remark-gfm | 渲染 AI 回复 |
| OpenAPI 类型 | openapi-typescript | 生成类型后再手写少量 SSE hook |

---

## 2. 全局 API Client

### 2.1 兼容两种成功响应

当前后端成功响应有“包裹”和“非包裹”两种，建议统一封装：

```ts
export interface ApiEnvelope<T> {
  code: number;
  message: string;
  data: T;
}

export function unwrap<T>(json: T | ApiEnvelope<T>): T {
  if (json && typeof json === 'object' && 'code' in json && 'message' in json && 'data' in json) {
    const boxed = json as ApiEnvelope<T>;
    if (boxed.code !== 0) throw new Error(boxed.message || '请求失败');
    return boxed.data;
  }
  return json as T;
}

export async function request<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const res = await fetch(input, init);
  const json = await res.json().catch(() => null);

  if (!res.ok) {
    const message = json?.message || `HTTP ${res.status}`;
    const err = new Error(message) as Error & { code?: number; status?: number };
    err.code = json?.code;
    err.status = res.status;
    throw err;
  }

  return unwrap<T>(json);
}
```

### 2.2 业务错误展示

| code | 前端建议 |
|---:|---|
| `40001` | 表单字段旁展示校验错误 |
| `40011` | 高级检索面板的 query_rewrite 值异常，重置为 `none` |
| `40012` / `40013` | 评估集上传页提示“为空/超过上限” |
| `40400` | 显示空状态或跳转列表页 |
| `40900` | KB 名称输入框提示重复 |
| `41300` / `41500` | 文件上传组件提示大小/格式不支持 |
| `42201` | `/v2/generate` 自定义上下文为空，提示至少传 1 条 |
| `50300` | 提示异步队列不可用，允许稍后重试 |

---

## 3. 路由与页面规划

```text
/                              → 重定向到 /chat
/chat                          → 会话列表 + 流式聊天主界面
/chat/:sessionId               → 指定会话聊天页
/kb                            → 知识库列表
/kb/:kbId                      → 知识库详情 + 文件列表 + 检索配置
/kb/:kbId/files/:fileId        → 文件详情 + 入库进度
/rag/query                     → V2 非流式可信问答调试页（可选）
/dev/trace/:traceId            → Trace 详情页
/eval                          → 选择 KB / 评估入口
/eval/:kbId                    → 指定 KB 的评估任务列表
/eval/:kbId/:evalTaskId        → 评估任务详情
/analytics                     → 查询质量仪表盘
```

推荐导航：

```text
Logo  /chat  /kb  /eval  /analytics    开发者模式 [ ]
```

---

## 4. 会话与流式聊天对接

### 4.1 创建会话

```ts
interface SessionDetail {
  id: string;
  title: string | null;
  summary: string | null;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export function createSession(title?: string) {
  return request<SessionDetail>('/api/v1/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: title ? JSON.stringify({ title }) : undefined,
  });
}
```

### 4.2 会话列表 / 历史消息

- `GET /api/v1/sessions?page=1&page_size=20` 渲染左侧会话列表。
- `GET /api/v1/sessions/{session_id}/messages?limit=50` 进入会话时加载历史。
- `PATCH /api/v1/sessions/{session_id}` 修改标题。
- `POST /api/v1/sessions/{session_id}/summarize` 主动生成摘要。
- `DELETE /api/v1/sessions/{session_id}` 删除会话，必须二次确认。

### 4.3 SSE 聊天 Hook

`POST /api/v1/chat/stream` 不是 EventSource GET，而是 POST SSE，建议用 `fetch + ReadableStream` 解析。

```ts
type ChatEvent =
  | { event: 'message'; type: 'text'; content: string }
  | { event: 'message'; type: 'done' }
  | { event: 'control'; type: 'tool_start'; tool: string; args?: Record<string, unknown> }
  | { event: 'control'; type: 'tool_end'; tool: string; output?: string };

export async function streamChat(params: {
  sessionId: string;
  content: string;
  kbIds?: string[] | null;
  onEvent: (event: ChatEvent) => void;
  signal?: AbortSignal;
}) {
  const res = await fetch('/api/v1/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: params.sessionId,
      content: params.content,
      kb_ids: params.kbIds ?? [],
    }),
    signal: params.signal,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => null);
    throw new Error(err?.message || `HTTP ${res.status}`);
  }

  const reader = res.body?.getReader();
  if (!reader) throw new Error('浏览器不支持 ReadableStream');

  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() ?? '';

    for (const frame of frames) {
      const dataLine = frame.split(/\r?\n/).find(line => line.startsWith('data:'));
      if (!dataLine) continue;
      const json = dataLine.slice(5).trim();
      if (!json) continue;
      params.onEvent(JSON.parse(json));
    }
  }
}
```

### 4.4 聊天页 UX

| 场景 | 推荐处理 |
|---|---|
| `text` 事件 | 追加到当前 assistant 气泡，形成打字机效果 |
| `tool_start` | 在气泡下方显示工具徽章“正在调用 xxx” |
| `tool_end` | 工具徽章变为完成，必要时展示 output 摘要 |
| `done` | 结束 loading，刷新会话列表和历史消息 |
| 用户停止生成 | AbortController 取消请求 |
| `kb_ids=[]` | 纯聊天，不检索 KB |

---

## 5. 知识库与文件管理对接

### 5.1 KB 列表页

接口：`GET /api/v1/knowledge-bases?page=1&page_size=20`

列表字段：`id`、`name`、`description`、`file_count`、`chunk_count`、`status`、`created_at`。

推荐 UI：

```text
┌────────────────────────────────────────────────────┐
│ [新建知识库]                                        │
├────────────┬────────┬────────┬────────┬────────────┤
│ 名称        │ 文件数  │ 切片数  │ 状态    │ 创建时间     │
└────────────┴────────┴────────┴────────┴────────────┘
```

### 5.2 创建 / 编辑 KB

创建接口：`POST /api/v1/knowledge-bases`。

编辑接口：`PATCH /api/v1/knowledge-bases/{kb_id}`。

前端表单建议：

| 字段 | 创建 | 编辑 | 说明 |
|---|---|---|---|
| `name` | 可填 | 可填 | 必须唯一 |
| `description` | 可填 | 可填/清空 | 最大 500 字符 |
| `embedding_dim` | 可填 | 禁止编辑 | 默认 4096 |
| `chunk_size` | 可填 | 禁止编辑 | 默认 512 |
| `chunk_overlap` | 可填 | 禁止编辑 | 默认 64 |
| `retrieval_config` | 不建议创建时填 | 可在高级面板编辑 | V2 检索默认配置 |

### 5.3 KB 详情页文件管理

核心接口：

| 动作 | 接口 | UI 行为 |
|---|---|---|
| 上传文件 | `POST /api/v1/knowledge-bases/{kb_id}/files` | 上传后立即插入 pending 行 |
| 文件列表 | `GET /api/v1/knowledge-bases/{kb_id}/files` | 表格展示 |
| 文件详情 | `GET /api/v1/knowledge-bases/{kb_id}/files/{file_id}` | 进度/错误/元数据 |
| 删除文件 | `DELETE /api/v1/knowledge-bases/{kb_id}/files/{file_id}` | 二次确认 |
| 重新入库 | `POST /api/v1/knowledge-bases/{kb_id}/files/{file_id}/reindex` | 状态回到 pending/processing |

### 5.4 上传与轮询示例

```ts
export async function uploadKbFile(kbId: string, file: File) {
  const form = new FormData();
  form.append('file', file);
  return request<FileDetail>(`/api/v1/knowledge-bases/${kbId}/files`, {
    method: 'POST',
    body: form,
  });
}

export function useKbFile(kbId: string, fileId: string, enabled: boolean) {
  return useQuery({
    queryKey: ['kb-file', kbId, fileId],
    queryFn: () => request<FileDetail>(`/api/v1/knowledge-bases/${kbId}/files/${fileId}`),
    enabled,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'pending' || status === 'processing' ? 2000 : false;
    },
  });
}
```

### 5.5 入库状态展示

| status | progress | UI |
|---|---:|---|
| `pending` | 0 | 等待队列 |
| `processing` | 1~99 | 进度条 + 当前百分比 |
| `completed` | 100 | 绿色完成，展示 `chunk_count` / `entity_count` / `summary_brief` |
| `failed` | 任意 | 红色失败，展示 `error_message`，提供“重新入库” |

`doc_metadata._ingest_warnings` 存在时，建议在详情页展示“部分降级”提示，例如 Neo4j 写入失败但向量入库成功。

---

## 6. V2 非流式可信问答对接

### 6.1 什么时候用 `/api/v2/query`

适用：

- 需要 `source_citations` 做答案溯源。
- 需要 `confidence` 做低可信提示。
- 需要 `trace_id` 跳转开发者 Trace 页面。
- 需要展示 HyDE / multi_query / NER / graph tags 的调试信息。

不适用：

- 需要打字机流式体验：继续用 `/api/v1/chat/stream`。

### 6.2 请求示例

```ts
interface QueryOptions {
  top_k?: number | null;
  reranker_enable?: boolean | null;
  bm25_enable?: boolean | null;
  stream?: false;
  query_rewrite?: 'none' | 'hyde' | 'multi_query' | null;
  enable_graph_rag?: boolean | null;
  similarity_threshold?: number | null;
  enable_faithfulness_check?: boolean | null;
}

export function queryRag(params: {
  query: string;
  sessionId?: string | null;
  kbIds?: string[] | null;
  options?: QueryOptions;
}) {
  return request<QueryResponse>('/api/v2/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query: params.query,
      session_id: params.sessionId ?? null,
      kb_ids: params.kbIds ?? null,
      options: params.options ?? {},
    }),
  });
}
```

### 6.3 Citation 渲染

```tsx
function renderAnswerWithCitations(answer: string, citations: CitationItem[]) {
  const parts: Array<{ type: 'text'; text: string } | { type: 'cite'; idx: number }> = [];
  const re = /\[(\d+)\]/g;
  let last = 0;
  let match: RegExpExecArray | null;

  while ((match = re.exec(answer)) !== null) {
    if (match.index > last) parts.push({ type: 'text', text: answer.slice(last, match.index) });
    parts.push({ type: 'cite', idx: Number(match[1]) });
    last = re.lastIndex;
  }
  if (last < answer.length) parts.push({ type: 'text', text: answer.slice(last) });

  return parts.map((part, i) => {
    if (part.type === 'text') return <ReactMarkdown key={i}>{part.text}</ReactMarkdown>;
    const cite = citations[part.idx - 1];
    if (!cite) return <span key={i}>[{part.idx}]</span>;
    return (
      <Popover
        key={i}
        trigger="click"
        content={
          <div style={{ maxWidth: 360 }}>
            <b>{cite.document_name}</b>
            {cite.page_number != null && <span> · 第 {cite.page_number} 页</span>}
            {cite.heading_path?.length > 0 && <div>{cite.heading_path.join(' > ')}</div>}
            <p>{cite.snippet}</p>
          </div>
        }
      >
        <a>[{part.idx}]</a>
      </Popover>
    );
  });
}
```

### 6.4 低置信度与自检结果

| 字段 | UI 建议 |
|---|---|
| `confidence < 0.5` 且有 `low_confidence_warning` | 答案上方黄色 Alert |
| `faithfulness_check=ok` | 不打扰用户 |
| `faithfulness_check=skipped` | 开发者模式下显示灰色提示 |
| `faithfulness_check=disabled` | 不显示 |
| `unverified_claims` 非空 | 答案下方折叠卡片展示未证实声明 |
| `sub_queries` 非空 | 折叠展示“AI 实际检索的问题” |

### 6.5 Trace 跳转

```tsx
{resp.trace_id && (isDevMode || (resp.confidence != null && resp.confidence < 0.5)) && (
  <Button type="link" onClick={() => navigate(`/dev/trace/${resp.trace_id}`)}>
    查看 trace
  </Button>
)}
```

---

## 7. V2 子能力接口对接

### 7.1 `/api/v2/retrieve` 纯检索页

适合做“检索调试面板”：输入 query、KB、top_k、开关 BM25/Graph/Rerank，展示命中的 chunks。

展示字段：`document_name`、`page_number`、`heading_path`、`content`、`rerank_score`、`metadata`。

### 7.2 `/api/v2/generate` 自带上下文生成

适合开发者工具页，不建议普通用户直接使用。

```ts
await request<GenerateResponse>('/api/v2/generate', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    query: '根据上下文回答问题',
    context_chunks: [
      { chunk_id: 'manual-1', content: '这里是上下文', source_label: '手动上下文 #1' },
    ],
    options: { enable_citation: true, enable_faithfulness_check: false },
  }),
});
```

### 7.3 `/api/v2/rerank` 独立精排

适合调试候选文本排序。注意当前后端可能配置为 NoopReranker，分数只表示当前配置下的结果，不应作为生产质量承诺。

---

## 8. Trace 可视化页面

接口：

- `GET /api/v2/traces/{trace_id}`
- `GET /api/v2/traces/sessions/{session_id}/traces?page=1&page_size=20`

### 8.1 Trace 详情布局

```text
Trace: abc123        总耗时 1842ms
┌────────┬────────────┬────────┬──────────┐
│ 步骤    │ 耗时        │ 模型    │ 错误      │
├────────┼────────────┼────────┼──────────┤
│ rewrite│ 150ms      │ -      │ -        │
│ retrieve│ 360ms     │ -      │ -        │
│ generate│ 1200ms    │ model  │ -        │
└────────┴────────────┴────────┴──────────┘
点击步骤 → Drawer 展示 step_input / step_output JSON
```

### 8.2 Step 颜色建议

| step_type | 颜色 |
|---|---|
| `query_rewrite` | 蓝色 |
| `query_ner` / `graph_anchor` | 紫色 |
| `retrieve` | 绿色 |
| `build_context` | 橙色 |
| `generate` | 红色 |
| `citation_parse` / `faithfulness_check` | 灰色 |

---

## 9. 评估任务管理页面

### 9.1 页面职责

- 选择 KB。
- 上传或粘贴 JSON 评估集。
- 创建评估任务。
- 轮询 pending/processing 任务。
- 展示 summary 与每题 details。

### 9.2 评估集格式

```json
[
  {
    "question": "2024 年台风生成数量是多少？",
    "ground_truth": "2024 年西北太平洋和南海共有 25 个台风生成。"
  }
]
```

提交时转换为：

```json
{
  "eval_set": [...],
  "retrieval_options": {
    "top_k": 10,
    "reranker_enable": false,
    "bm25_enable": true,
    "query_rewrite": "none"
  },
  "name": "台风知识库评估"
}
```

### 9.3 轮询策略

```ts
useQuery({
  queryKey: ['evaluations', kbId],
  queryFn: () => request<EvalListResponse>(`/api/v2/knowledge-bases/${kbId}/evaluations`),
  refetchInterval: (query) => {
    const items = query.state.data?.items ?? [];
    return items.some(i => i.status === 'pending' || i.status === 'processing') ? 5000 : false;
  },
});
```

### 9.4 指标展示

| 指标 | 中文名 | UI |
|---|---|---|
| `faithfulness` | 忠实度 | 雷达图/进度条 |
| `answer_relevancy` | 答案相关性 | 雷达图/进度条 |
| `context_precision` | 上下文精确度 | 雷达图/进度条 |
| `context_recall` | 上下文召回率 | 雷达图/进度条 |
| `overall_score` | 总分 | 大号数字 |

---

## 10. Analytics 仪表盘

接口：`GET /api/v2/analytics?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&kb_id=...`

### 10.1 推荐布局

```text
┌──────────────────────────────────────────────────────────┐
│ 时间范围 [RangePicker]  知识库 [Select]  [刷新]           │
├──────────┬──────────┬──────────┬──────────┬──────────────┤
│ 查询总数  │ 平均延迟  │ 平均置信  │ 低置信率  │ 错误率        │
├──────────────────────────────────────────────────────────┤
│ 工具使用率：Graph RAG / BM25 / Faithfulness              │
├──────────────────────────────────────────────────────────┤
│ Token 总量 / 平均步骤数                                  │
└──────────────────────────────────────────────────────────┘
```

### 10.2 查询示例

```ts
function fetchAnalytics(params: { start: string; end: string; kbId?: string }) {
  const qs = new URLSearchParams({ start_date: params.start, end_date: params.end });
  if (params.kbId) qs.set('kb_id', params.kbId);
  return request<AnalyticsResponse>(`/api/v2/analytics?${qs}`);
}
```

---

## 11. 高级检索配置面板

### 11.1 配置来源

- KB 默认值：`GET /api/v1/knowledge-bases/{kb_id}` 的 `retrieval_config`。
- 单次请求覆盖：`POST /api/v2/query` 的 `options`。
- 持久化修改：`PATCH /api/v1/knowledge-bases/{kb_id}` 的 `retrieval_config`。

### 11.2 表单字段

| 字段 | 控件 | 建议默认 |
|---|---|---|
| `query_rewrite` | Radio：none / hyde / multi_query | `none` |
| `enable_graph_rag` | Switch | 跟随 KB |
| `bm25_enable` | Switch | true |
| `reranker_enable` | Switch | 当前建议 false，除非后端启用有效 reranker |
| `similarity_threshold` | Slider 0~1 | 0.3 |
| `top_k` | InputNumber 1~50 | 5 |
| `enable_faithfulness_check` | Switch | false |

### 11.3 保存策略

| 用户动作 | 接口 |
|---|---|
| 仅本次查询生效 | 放入 `/api/v2/query.options` |
| 保存为 KB 默认 | `PATCH /api/v1/knowledge-bases/{kb_id}`，传 `retrieval_config` |
| 清空 KB 覆盖 | `PATCH ...`，传 `retrieval_config: {}` |

---

## 12. 推荐开发顺序

| 顺序 | 模块 | 依赖 | 说明 |
|---|---|---|---|
| 1 | API Client + 错误处理 | 无 | 先处理包裹/非包裹响应差异 |
| 2 | 会话列表 + 流式聊天 | 会话 API | 主体验闭环 |
| 3 | KB 列表/详情/创建编辑 | KB API | 为聊天选择 KB 做准备 |
| 4 | 文件上传 + 入库轮询 | 文件 API | 数据管理闭环 |
| 5 | V2 Query + Citation 渲染 | KB 数据 | 展示可信 RAG 能力 |
| 6 | Trace 详情页 | V2 Query | 调试和低置信追踪 |
| 7 | 评估任务页 | KB 数据 + Celery | 运营评估 |
| 8 | Analytics 仪表盘 | V2 Query 数据 | 查询质量统计 |
| 9 | Retrieve/Generate/Rerank 调试页 | 可选 | 开发者工具 |

---

## 13. OpenAPI 类型生成

后端启动后：

```bash
curl http://127.0.0.1:8000/openapi.json > openapi.json
npm i -D openapi-typescript
npx openapi-typescript openapi.json -o src/api/types.ts
```

注意：

- `/api/v1/chat/stream` 是 POST SSE，OpenAPI 生成的普通方法不好直接使用，建议手写 `streamChat` hook。
- V2 多数接口成功响应不是 `{code,data}` 包裹，生成类型后仍建议统一走 `request<T>() + unwrap()`。

---

## 14. 对接验收清单

- [ ] 能创建会话、发送 SSE 消息、看到 token 流和工具徽章。
- [ ] 能创建 KB、上传文件、轮询到 completed 或 failed。
- [ ] 文件 completed 后，KB 的 `file_count` / `chunk_count` 有更新。
- [ ] `/api/v2/query` 能展示答案、引用 Popover、confidence、trace 按钮。
- [ ] trace 页面能打开并展示 step_input / step_output。
- [ ] 评估任务能创建、轮询、展示 summary/details。
- [ ] Analytics 能按日期和 KB 过滤。
- [ ] 所有失败响应能显示 `message`，不会因响应包裹差异崩溃。

---

*TyAgent Frontend Integration Guide · End of Document*

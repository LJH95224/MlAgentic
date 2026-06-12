# TyAgent V1.5 · 前端功能模块拆解

> **配套**：[V1.5 API 接口文档](v1_5_api_reference.md) · [V1.5 PRD](TyAgent%20V1.5%20%C2%B7%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md)
> **后端基线**：V1.5 全链路 smoke 验收通过（2026-06-12）

---

## 0. 总览

后端 V1.5 共 18 个业务接口，建议拆分前端为 **5 个核心模块 + 2 个支撑模块**：

```
┌──────────────────── 5 个核心功能模块 ────────────────────┐
│ 1. 会话与对话           （核心，最常用）                  │
│ 2. 知识库管理           （后台运营页）                   │
│ 3. 文件管理与入库进度   （知识库下钻）                   │
│ 4. 会话标题/摘要         （异步内嵌，无独立页面）         │
│ 5. KB 关联对话           （会话页内嵌选择器）             │
└──────────────────────────────────────────────────────────┘
┌──────────────── 2 个支撑模块（横切关注点） ────────────────┐
│ A. 统一响应错误处理 + 全局 Toast                          │
│ B. SSE 流式接收 + 增量渲染                                │
└──────────────────────────────────────────────────────────┘
```

---

## 1. 推荐技术栈（参考）

| 层 | 选型 | 理由 |
|---|---|---|
| 框架 | React 18 + TypeScript | 生态最全；与 OpenAPI 自动 SDK 工具链最成熟 |
| 路由 | React Router 6 | 主流稳定；本应用 3-4 个主路由够用 |
| 状态 | Zustand（推荐）/ Redux Toolkit | 本应用没有特别复杂的全局状态，Zustand 够轻量 |
| 请求 | TanStack Query（React Query） | KB/文件/会话列表都需要缓存 + 失效；轮询场景天然适配 |
| UI 库 | Ant Design 5 / shadcn-ui | Ant Design 上手快，组件全；shadcn 自由度高 |
| 样式 | Tailwind CSS（如果用 shadcn） | — |
| SDK 生成 | `openapi-typescript-codegen` | 基于 [/openapi.json](http://127.0.0.1:8000/openapi.json) 自动生成类型安全的请求 SDK，**强烈推荐**，省 80% 联调时间 |
| Markdown 渲染 | react-markdown + remark-gfm | 消息内容、KB 文件名等可能含 markdown |
| 文件上传 | Ant Design `Upload` / react-dropzone | 内置拖拽 + 进度条 |
| 图标 | lucide-react / @ant-design/icons | — |

> 第一步生成 SDK：
> ```bash
> npx openapi-typescript-codegen \
>   --input http://127.0.0.1:8000/openapi.json \
>   --output ./src/api/generated \
>   --client axios
> ```
> 之后所有接口调用都有类型提示 + 自动序列化。

---

## 2. 路由设计

```
/                              → 重定向到 /chat 或最近一个会话
/chat                          → 会话列表 + 对话主界面（左右栏布局）
/chat/:sessionId               → 同上，URL 携带 sessionId
/kb                            → 知识库列表
/kb/:kbId                      → KB 详情 + 文件管理
/kb/:kbId/files/:fileId        → 单文件详情（含入库进度 / error_message）
/settings                      → （可选）系统设置、KB 默认参数等
```

推荐主布局：

```
┌────────────────────────────────────────────────────────┐
│  顶部导航（Logo · /chat · /kb · 用户）                  │
├────────────────────────────────────────────────────────┤
│           │                                            │
│  左侧栏    │           主内容区                          │
│ (会话/KB   │     （路由对应组件）                         │
│  列表)     │                                            │
│           │                                            │
└────────────────────────────────────────────────────────┘
```

---

## 3. 核心模块详解

### 模块 1：会话与对话（最核心）

#### 1.1 会话列表（左栏）

**对应接口**：
- `GET /api/v1/sessions?page=1&page_size=50`（首屏拉一页）
- `POST /api/v1/sessions`（新建按钮）
- `DELETE /api/v1/sessions/{id}`（每条悬停显示删除按钮）
- `PATCH /api/v1/sessions/{id}`（双击标题原地编辑）

**UI 要点**：
- 每条显示：`title` 或"未命名"+ `summary_snippet`（鼠标悬停 tooltip）+ 相对时间（如"2 分钟前"）
- 当前活跃会话高亮
- 顶部"+ 新对话"按钮 → 调 POST /sessions → 跳转 `/chat/{new_id}`
- 悬停显示 ⋯ 菜单：重命名 / 删除（删除需二次确认弹窗）
- 实时反映 SES-07 异步标题：每次本地新建会话后约 10s 重新拉取详情，看 title 是否被填充

**异步标题生成的 UX 处理**：
```
首次发完消息 → 列表显示"未命名" → 5-10 秒后悄悄刷新 → 标题出现
```
- 实现：发送对话流 done 事件后启动一个 `setTimeout(() => refetchSession(id), 5000)`
- 用 React Query 的 `invalidateQueries(['session', id])` 触发刷新

**关键状态**：
- `currentSessionId`（URL 参数）
- `sessions` 列表（用 React Query 缓存，5-30 秒 staleTime）
- 删除时 optimistic update + 失败回滚

---

#### 1.2 对话主界面（右栏）

**对应接口**：
- `GET /api/v1/sessions/{id}`（详情，进入会话时拉一次）
- `GET /api/v1/sessions/{id}/messages?limit=20`（首屏拉最近 20 条）
- `GET /api/v1/sessions/{id}/messages?limit=20&before=<id>`（向上滚动加载更多）
- `POST /api/v1/chat/stream`（发送消息，SSE 流）
- `POST /api/v1/sessions/{id}/summarize`（标题旁边的"生成摘要"按钮）

**UI 结构**：

```
┌─────────────────────────────────────────────┐
│ 标题"AI 帮我分析数据"  ⋯ 菜单（导出/摘要）   │  ← header
├─────────────────────────────────────────────┤
│ (滚动到顶部 → 加载更多历史)                  │
│                                             │
│ user: 你好                                  │
│ assistant: 你好！我可以帮你...                │
│   ↳ tool_start: search_knowledge_base       │  ← 浅色徽章
│   ↳ tool_end: search_knowledge_base ✓       │
│ ...                                         │  ← messages 区
│                                             │
├─────────────────────────────────────────────┤
│ [选 KB ▼] [输入框                        ] │  ← composer
│            发送                              │
└─────────────────────────────────────────────┘
```

**SSE 流式接收**（参考 [v1_5_api_reference.md §2.1](v1_5_api_reference.md) 完整示例）：

关键状态机：
```javascript
const [streaming, setStreaming] = useState({
  text: '',           // 当前累积的 assistant 回复文本
  toolCalls: [],      // 当前已触发的工具 [{tool, args, output}]
  done: false,
});

// 收到 tool_start → 在消息体下方插入工具徽章
// 收到 text     → 追加 token 到 text，每次 setState 触发渲染
// 收到 tool_end → 标记对应工具完成（绿勾）+ 可点击展开 output
// 收到 done     → 把 streaming 状态推到 messages 列表 + 清空 streaming
```

**消息渲染要点**：
- `role=user`：右对齐气泡，蓝色
- `role=assistant`：左对齐气泡，灰色 + markdown 渲染（react-markdown + remark-gfm）
- `tool_start` 事件：消息体内嵌入轻量徽章 "🔍 正在检索知识库..."
- `tool_end` 事件：徽章变绿勾 + 可展开看返回内容
- `role=tool` 历史消息：默认折叠，点击展开

**特殊：tool_start.args._kb_ids 展示**：
当用户选了 kb_ids 后，工具调用徽章可以展示"在 KB-A、KB-B 中检索..."：
```javascript
if (sseData.type === 'tool_start' && sseData.args?._kb_ids) {
  const kbNames = sseData.args._kb_ids.map(id => kbNameMap.get(id) || id.slice(0, 8));
  badgeText = `🔍 在 ${kbNames.join(', ')} 中检索...`;
}
```

**摘要按钮 UX**：
```
header 右上角"⋯ 菜单" → 点"生成摘要"
  → POST /summarize 立即返 202 + task_id
  → 弹 toast "摘要生成中..."
  → 10 秒后或用户切回该会话时 refetch /sessions/{id}
  → 看到 summary 字段非空 → 展示在 header 副标题 / 抽屉里
```

不需要前端轮询 task_id（Celery backend 对前端无意义）。

---

### 模块 2：知识库管理

**对应接口**：
- `GET /api/v1/knowledge-bases?page=1&page_size=20`
- `POST /api/v1/knowledge-bases`（新建表单）
- `GET /api/v1/knowledge-bases/{id}`（详情）
- `PATCH /api/v1/knowledge-bases/{id}`（改 name/description）
- `DELETE /api/v1/knowledge-bases/{id}`（**强二次确认**）

#### 2.1 KB 列表页 `/kb`

```
┌────────────────────────────────────────────────────────┐
│  + 新建知识库                              [搜索...]    │
├────────────────────────────────────────────────────────┤
│ 名称        | 描述     | 文件数 | 切片数 | 状态 | 操作  │
│ 气象库       | ...     |   5   | 1280  | ✓   | ⋯    │
│ 应急预案库   | ...     |   2   | 340   | ✓   | ⋯    │
└────────────────────────────────────────────────────────┘
```

**UI 要点**：
- 列表项卡片化展示，含 `name` / `description`（截断）/ `file_count` / `chunk_count` / `status` 徽章
- 点击卡片 → 跳转 `/kb/:kbId` 文件管理页
- ⋯ 菜单：编辑（弹出 PATCH 表单）/ 删除（**两步确认**：第一次"删除会清空所有文件、向量、图谱，不可恢复"，第二次"请输入 KB 名称确认"）
- `status` 字段：`active` 绿色 / `building` 黄色 / `error` 红色

#### 2.2 新建 KB 弹窗 / 抽屉

表单字段（与 PRD KB-01 一致）：
- 名称（必填，去空白后非空，全局唯一 — 失败时显示 `40900` 提示）
- 描述（选填）
- 高级（折叠）：
  - 向量维度（默认 4096，**带警示"创建后不可修改"**，灰色 disabled 风格更稳）
  - 切片大小（默认 512，滑块 128~2048）
  - 切片重叠（默认 64，滑块 0~chunk_size/2，超时自动夹值）

**前端校验** 与后端对齐：
- name 长度 1~128，去空白后非空
- description ≤ 500
- chunk_size ∈ [128, 2048]
- chunk_overlap ≤ chunk_size / 2

#### 2.3 KB 详情面板（在 /kb/:kbId 顶部）

```
┌────────────────────────────────────────────────────────┐
│ 📚 气象库                                  [编辑] [删除] │
│ 描述: 存放所有气象领域文档                              │
│ 配置: dim=4096 chunk=512 overlap=64 status=active      │
│ 统计: 5 文件 / 1280 切片 / 42 实体（实时）              │
│ 创建于 2026-06-11                                      │
└────────────────────────────────────────────────────────┘
```

`entity_count` 是接 Neo4j 的实时数据（与列表的 chunk_count 不同），KB-03 详情会展示 V2 阶段会单独高亮。

---

### 模块 3：文件管理与入库进度

**对应接口**（全部在 KB 下钻路径 `/api/v1/knowledge-bases/{kb_id}/files/...`）：
- `GET .../files`（列表）
- `POST .../files`（上传）
- `GET .../files/{file_id}`（详情 + 进度，2 秒轮询）
- `DELETE .../files/{file_id}`（删除）
- `POST .../files/{file_id}/reindex`（重新入库）

#### 3.1 文件列表页 `/kb/:kbId`

```
┌────────────────────────────────────────────────────────┐
│ 📚 KB 详情面板（同 2.3）                                │
├────────────────────────────────────────────────────────┤
│  [拖入文件区域 / 选择文件]                              │
│  支持 .pdf .docx .md .txt，单文件 ≤ 50MB                │
├────────────────────────────────────────────────────────┤
│ 文件名           | 大小  | 状态       | 进度 | 切片 | 操作│
│ 气象报告.pdf     | 2.4M | completed | 100% | 56  | ⋯  │
│ 应急预案.docx    | 1.2M | processing| 60%  | -   | ⋯  │
│ 测试.txt         | 5K   | failed    | 35%  | -   | ⋯  │
└────────────────────────────────────────────────────────┘
```

**上传 UX**：
- 拖拽 + 点击两种入口（Ant Design `Upload.Dragger` 或 react-dropzone）
- 上传成功立即在列表顶部插入"pending"状态行
- 多文件并发上传无限制（后端各自走独立 Celery 任务）
- 失败场景前端提示：
  - `41300` → "文件超过 50MB"
  - `41500` → "格式不支持，请上传 PDF/DOCX/MD/TXT"
  - `50000` → "上传失败，请重试"

**进度轮询**：
- 仅对 `status = pending | processing` 的文件，每 2 秒调 `GET .../files/{id}` 一次
- React Query 的 `useQuery({refetchInterval: 2000, enabled: status !== 'completed' && status !== 'failed'})` 完美适配
- `progress` 字段值阶段含义见 API 文档 §4.3
- 进度条上方显示当前阶段文案：
  - 0 → "排队中"
  - 20 → "解析文档"
  - 35 → "切片"
  - 60 → "生成向量"
  - 80 → "写入向量库"
  - 90 → "抽取实体"
  - 95 → "写入知识图谱"
  - 100 → "完成 ✓"

**failed 状态处理**：
- `error_message` 字段展示给用户（红色 alert）
- 提供"重新入库"按钮 → 调 `POST .../files/{id}/reindex`
- 提供"删除"按钮

#### 3.2 文件详情面板（侧抽屉 / 单独页）

点击文件行 → 抽屉弹出，展示完整字段：

```
┌──────────────────────────────────┐
│ 📄 气象报告.pdf                   │
│ 大小: 2.4MB     MIME: application/pdf
│ 状态: completed   进度: 100%       │
│ 切片数: 56        实体数: 43       │
│ Celery 任务 ID: task-xxx           │
│ 上传时间: 2026-06-11 14:00:00      │
│ 完成时间: 2026-06-11 14:01:32      │
│                                  │
│ [重新入库] [删除]                  │
│ (failed 时上方显示 error_message)  │
└──────────────────────────────────┘
```

---

### 模块 4：会话标题/摘要异步生成

**特点**：**没有独立页面**，完全嵌入到模块 1 的会话流程中。

#### 4.1 标题自动生成（SES-07 / TASK-04）

**前端不主动触发**，由 `/chat/stream` 流末尾后端自动 enqueue。

**前端只需做的事**：
- 首次发完消息 → done 事件后启动 `setTimeout` 5 秒后 refetch session 详情
- 看到 `title` 字段从 null 变非空 → 平滑替换左侧栏的"未命名"

```javascript
// 在 SSE done 事件回调里
if (sseData.type === 'done' && messages.length === 2) {
  // 首轮对话 → 5 秒后刷标题
  setTimeout(() => queryClient.invalidateQueries(['session', sessionId]), 5000);
}
```

#### 4.2 摘要手动生成（SES-08 / TASK-05）

**入口**：会话 header "⋯ 菜单" → "生成摘要"

**流程**：
1. 点击 → `POST /sessions/{id}/summarize` → 立即返 202
2. 前端弹 toast "摘要生成中（约 10 秒）..."
3. 10 秒后或用户切走再切回该会话时 → refetch session 详情
4. `summary` 字段非空 → 在 header 副标题展示（鼠标悬停显示完整 200 字）

```javascript
const summarize = async (sessionId) => {
  const {data} = await api.post(`/sessions/${sessionId}/summarize`);
  toast.info(`摘要生成中... (任务 ${data.task_id.slice(0,8)})`);
  setTimeout(() => queryClient.invalidateQueries(['session', sessionId]), 10000);
};
```

---

### 模块 5：KB 关联对话（KB-06）

**特点**：嵌入到模块 1 的对话输入框上方，**不是独立页面**。

#### 5.1 KB 选择器

```
[选择知识库 ▼ ✓ 气象库 (5 文件)  ✗ 应急库 (2 文件)]    [输入框...]  [发送]
```

**UI 要点**：
- 多选下拉，显示所有 active 状态的 KB
- 每条 KB 展示：name + file_count（让用户知道"这个库有内容可查"）
- 显示已选数量徽章
- 提供"全选 / 清空"快捷按钮

**三态对应**（与 API §2.1 一致）：

| 用户操作 | 前端请求 body | 后端行为 |
|---|---|---|
| 一次都没动选择器 | `kb_ids` 字段**不传** | 走 V1.0 默认 collection |
| 点了清空按钮 | `kb_ids: []` | 不查任何 KB（纯 LLM 答） |
| 选了 A、B 两个 | `kb_ids: ["a-uuid", "b-uuid"]` | 跨 A 和 B 检索 + 合并重排 |

**状态持久化建议**：
- 每个会话各自记住一份 kb_ids（存到本地 storage 或 zustand store）
- 这样切到这个会话还能恢复上次的 kb_ids 选择

#### 5.2 检索工具调用可视化

回顾模块 1.2 的 SSE 处理 —— `tool_start.args._kb_ids` 字段已经把当前批次的 kb_ids 注入：

```javascript
if (sseData.type === 'tool_start') {
  if (sseData.tool === 'search_knowledge_base' && sseData.args?._kb_ids) {
    // 展示 "🔍 在 KB-气象库, KB-应急库 中检索..."
  } else if (sseData.tool === 'query_knowledge_graph') {
    // 展示 "🕸 查询图谱..."
  }
}
```

---

## 4. 支撑模块

### 支撑 A：统一响应错误处理 + 全局 Toast

后端所有 REST 接口返回 `{code, message, data}`。前端做一个 axios 响应拦截器：

```typescript
// src/api/client.ts
import axios from 'axios';
import {toast} from 'react-hot-toast';

const ERROR_CODE_TO_TOAST = {
  40400: '资源不存在',
  40900: '名称已存在',
  41300: '文件超出大小限制',
  41500: '文件格式不支持',
  50300: '后台任务系统暂时不可用，请稍后重试',
};

const client = axios.create({baseURL: '/api/v1'});

client.interceptors.response.use(
  (resp) => {
    // 成功响应：剥掉 ApiResponse 包装层，让业务代码拿 data 字段
    if (resp.data && typeof resp.data.code === 'number') {
      if (resp.data.code === 0) {
        return {...resp, data: resp.data.data};
      }
      // code !== 0 但 HTTP 是 200（理论不会，后端都是配对的）
      throw new ApiError(resp.data.code, resp.data.message);
    }
    return resp;
  },
  (err) => {
    const body = err.response?.data;
    if (body && typeof body.code === 'number') {
      const msg = ERROR_CODE_TO_TOAST[body.code] || body.message || '操作失败';
      toast.error(msg);
      throw new ApiError(body.code, body.message);
    }
    toast.error('网络错误，请检查连接');
    throw err;
  }
);

export class ApiError extends Error {
  constructor(public code: number, message: string) {
    super(message);
  }
}
```

**约定**：
- 业务组件不再写 `if (resp.code === 0) ...`，直接拿 `data`
- 错误自动 toast，业务组件可用 try/catch + `instanceof ApiError` 做特殊处理（如表单字段错）
- `40001`（PARAM_INVALID）建议不自动 toast，由表单组件 inline 显示

### 支撑 B：SSE 流式接收

详见模块 1.2 与 API 文档 §2.1 示例。提取成 `useChatStream` 自定义 hook：

```typescript
function useChatStream(sessionId: string) {
  const [streaming, setStreaming] = useState<StreamingState | null>(null);

  const send = useCallback(async (content: string, kbIds?: string[]) => {
    setStreaming({text: '', tools: [], done: false});
    const resp = await fetch('/api/v1/chat/stream', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        session_id: sessionId,
        content,
        ...(kbIds !== undefined && {kb_ids: kbIds}),
      }),
    });
    if (!resp.ok) {
      // 4xx 走 JSON 错误格式
      const err = await resp.json();
      throw new ApiError(err.code, err.message);
    }
    // 解析 SSE 流（见 API 文档 §2.1 完整示例）...
  }, [sessionId]);

  return {streaming, send};
}
```

---

## 5. 推荐开发顺序（建议 2 人 2-3 周）

| 周 | 阶段 | 产出 |
|---|---|---|
| **W1** | 1. 工程骨架 + 路由 + 布局 + axios 拦截器<br>2. OpenAPI SDK 生成<br>3. 模块 2（KB 列表 + 新建 + 删除）<br>4. 模块 3 简化版（上传 + 列表，不含轮询） | 后台运营页能跑通；可以建库上传文档 |
| **W2** | 1. 模块 3 完整版（进度轮询 + 详情 + 重新入库）<br>2. 模块 1.1（会话列表）<br>3. 模块 1.2（对话输入 + SSE 流式接收）<br>4. tool_start / tool_end 徽章 | 主对话功能可用 |
| **W3** | 1. 模块 5（KB 选择器三态）<br>2. 模块 4（标题/摘要 异步触发）<br>3. 历史消息游标翻页加载更多<br>4. Markdown 渲染 + 代码块高亮<br>5. 错误体验打磨 + 联调修 bug | V1.5 前端完整可演示 |

---

## 6. 关键 UX 决策清单

| 场景 | 推荐做法 | 理由 |
|---|---|---|
| 删除 KB / 会话 | 二次确认 + 输入名称确认 | KB-05 不可逆，PRD 明确要求二次确认 |
| 上传文件超限 | 前端先校验后再发请求（不依赖 413） | 大文件上传一半失败更糟糕 |
| 入库失败 | 红色 alert + "重新入库"按钮 | error_message 给用户看，常见原因如维度不匹配 |
| 摘要生成中 | toast 提示 + 10s 后自动刷 | 不要弹模态阻塞用户 |
| 首轮标题异步出现 | 不打扰用户，列表项标题平滑过渡 | 标题是辅助元数据，不需要刻意通知 |
| Celery 不可达（50300） | toast 提示 + 上传/摘要按钮 disabled | 让用户知道后台异步不可用 |
| 历史消息加载更多 | 滚到顶部自动 → loading 转圈 → 平滑插入 | 不需要"加载更多"按钮，自动触发更顺 |
| kb_ids 选择持久化 | 每个 session 独立记 | 用户切来切去保留各自的检索范围 |

---

## 7. OpenAPI SDK 生成（强烈推荐）

```bash
# 安装
npm i -D openapi-typescript-codegen

# 生成（保证后端服务起着）
npx openapi-typescript-codegen \
  --input http://127.0.0.1:8000/openapi.json \
  --output ./src/api/generated \
  --client axios \
  --useOptions
```

生成后会得到完整的 TypeScript 类型 + 请求函数，例如：

```typescript
import {SessionsService, KnowledgeBasesService, KbFilesService} from './api/generated';

// 自动类型推断
const session = await SessionsService.createSession({requestBody: {title: 'X'}});
//    ↑ 类型是 ApiResponse<SessionDetail>

const kbs = await KnowledgeBasesService.listKnowledgeBases({page: 1, pageSize: 20});
//    ↑ 类型是 ApiResponse<KnowledgeBaseListResponse>
```

**好处**：
- 后端改字段 → 前端类型立刻报错，**联调时间减半**
- 不必手写 baseURL + 路径拼接 + 请求/响应类型
- 错误处理仍走 axios 拦截器统一处理

> SSE 接口（chat/stream）SDK 不会生成（OpenAPI 暂未很好支持 SSE），需要自己写 `useChatStream` hook（见模块 1.2）。

---

## 8. 参考资源

- 在线 API 文档（开发时实时查阅）：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- 后端接口文档：[v1_5_api_reference.md](v1_5_api_reference.md)
- V1.5 PRD：[TyAgent V1.5 · 需求规格说明书](TyAgent%20V1.5%20%C2%B7%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md)（功能验收标准）
- V1.5 架构：[architecture.md](architecture.md)

---

*TyAgent V1.5 Frontend Guide · End of Document*

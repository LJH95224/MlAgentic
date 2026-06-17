# TyAgent V2.0 · 前端联调指南

> **基线版本**：V2.0 Hermes（2026-06-17 全链路 smoke 验收通过）
> **配套文档**：[v2_api_reference.md](v2_api_reference.md) · [architecture.md V2 章节](architecture.md#第三部分--v20-hermes-增量) · [progress.md](progress.md)
> **V1.5 前端指南**：[v1_5_frontend_guide.md](v1_5_frontend_guide.md)（本文不重写 V1.5 模块，只描述 V2 新增模块的对接）

---

## 0. 总览

V2.0 Hermes 迭代在后端增强了智能 RAG 能力（智能切片 / BM25+RRF 混合检索 / Reranker 精排 / Citation 溯源 / Trace 可观测 / RAGAS 评估 / 聚合统计），**V1.5 已有的会话 / KB / 文件 / 对话流等前端模块完全不动**。

本文档只描述 V2 新增的 5 个前端模块和 1 个支撑模块如何对接。

### 0.1 V2 前端工作清单（5 个新模块）

```
┌──────────────────── 5 个新增核心模块 ────────────────────┐
│ 6. 答案溯源高亮（Citation 渲染）  P0    对话页内嵌        │
│ 7. Trace 可视化                  P1    开发者页面          │
│ 8. 评估任务管理                  P2    运营后台页面        │
│ 9. Analytics 仪表盘              P2    运营后台页面        │
│ 10. 检索参数高级面板             P3    对话页内嵌          │
└──────────────────────────────────────────────────────────┘
┌──────────────── 1 个新增支撑模块 ────────────────────────┐
│ C. Trace ID 生命周期管理         P0    横切关注点          │
└──────────────────────────────────────────────────────────┘
```

### 0.2 与 V1.5 的差异

| 维度 | V1.5（已有） | V2.0 新增 |
|---|---|---|
| 对话接口 | `POST /api/v1/chat/stream`（SSE 流式） | `POST /api/v2/query`（非流式，单次返回） |
| 路由前缀 | `/api/v1/...` | `/api/v2/...` |
| 响应格式 | SSE 事件流 | REST JSON + trace_id |
| 对话流工具徽章 | tool_start / tool_end 徽章 | 保留，不变 |
| 答案展示 | 纯 Markdown | Markdown + `[N]` 溯源锚点 |
| 新增页面 | — | Trace 可视化 / 评估任务 / Analytics 仪表盘 |
| 新增配置 | KB 基础参数 | 检索参数面板（抽屉） |

---

## 1. 推荐技术栈（沿用 V1.5）

与 V1.5 完全一致，无新增依赖：

| 层 | 选型 | 理由 |
|---|---|---|
| 框架 | React 18 + TypeScript | 生态最全；与 OpenAPI 自动 SDK 工具链最成熟 |
| 路由 | React Router 6 | 主流稳定；本应用 4-5 个主路由够用 |
| 状态 | Zustand（推荐）/ Redux Toolkit | 本应用没有特别复杂的全局状态，Zustand 够轻量 |
| 请求 | TanStack Query（React Query） | KB/文件/会话列表都需要缓存 + 失效；轮询场景天然适配 |
| UI 库 | Ant Design 5 / shadcn-ui | Ant Design 上手快，组件全；shadcn 自由度高 |
| 样式 | Tailwind CSS（如果用 shadcn） | — |
| SDK 生成 | `openapi-typescript-codegen` | 基于 `/openapi.json` 自动生成类型安全的请求 SDK |
| Markdown 渲染 | react-markdown + remark-gfm | 消息内容、KB 文件名等可能含 markdown |
| 图标 | lucide-react / @ant-design/icons | — |

> V2 无需额外前端依赖。SSE 流式在 V2 中不启用（/v2/query 的 `options.stream` 预留，当前始终 false）。

---

## 2. 路由设计（V2 新增）

```
/                              → 重定向到 /chat 或最近一个会话        （V1.5 已有）
/chat                          → 会话列表 + 对话主界面               （V1.5 已有）
/chat/:sessionId               → 同上，URL 携带 sessionId           （V1.5 已有）
/kb                            → 知识库列表                         （V1.5 已有）
/kb/:kbId                      → KB 详情 + 文件管理                 （V1.5 已有）
/kb/:kbId/files/:fileId        → 单文件详情（含入库进度）            （V1.5 已有）

--- 以下为 V2 新增路由 ---
/dev/trace/:traceId            → Trace 详情页（模块 7）
/eval                          → 评估任务列表页（模块 8）
/eval/:kbId                    → 指定 KB 的评估任务列表
/eval/:kbId/:evalTaskId        → 评估任务详情页（含雷达图）
/analytics                     → Analytics 仪表盘（模块 9）
```

推荐将新增路由放在 V1.5 主布局的顶部导航栏新增入口：

```
┌────────────────────────────────────────────────────────┐
│ Logo  /chat  /kb  /eval  /analytics    开发者模式: [ ]  │
├────────────────────────────────────────────────────────┤
│           │                                            │
│  左侧栏    │           主内容区                          │
│           │     （路由对应组件）                         │
│           │                                            │
└────────────────────────────────────────────────────────┘
```

`/dev/trace/:traceId` 不放在主导航栏，由对话页的"查看 trace"按钮跳转进入（详见支撑 C）。

---

## 3. 核心模块详解

### 模块 6：答案溯源高亮（Citation 渲染）—— P0，必做

#### 6.1 数据来源

调用 `POST /api/v2/query` 后，响应体包含：

```typescript
interface QueryResponse {
  answer: string;               // 含 [N] 标记的答案文本，如 "台风是一种热带气旋[1][2]..."
  source_citations: CitationItem[];
  trace_id: string | null;
  total_latency_ms: number | null;
  confidence: number | null;    // [0, 1]
  low_confidence_warning: string | null;
  faithfulness_check: string | null; // "ok" | "skipped" | "disabled"
  unverified_claims: Array<{claim: string; status: string; source_text: string}> | null;
  // ... 更多字段见 QueryResponse Schema
}

interface CitationItem {
  chunk_id: number | null;
  document_name: string;        // 来源文档名，如 "气象报告.pdf"
  page_number: number | null;
  heading_path: string[];       // 面包屑导航，如 ["第三章", "热带气旋", "定义"]
  snippet: string;              // 被引用 chunk 的前 200 字符
  rerank_score: number | null;
}
```

#### 6.2 渲染思路

1. 用正则 `/\[(\d+)\]/g` 匹配 `answer` 中所有 `[N]` 标记
2. 将每个 `[N]` 替换为可交互的 `<a>` 锚点，`data-idx` 属性记录索引号
3. 用户点击锚点 → 弹出 Popover / Tooltip 展示该引用的详细信息
4. 若 LLM 编造了 `[5]` 但 `source_citations` 只有 3 条 → 该锚点降级为普通文本

#### 6.3 边界处理

| 场景 | 处理方式 |
|---|---|
| `[N]` 中的 N 超出 source_citations 长度 | 该 `[N]` 渲染为纯文本，不带交互 |
| source_citations 为空数组 | answer 整体渲染为纯 Markdown，不做解析 |
| 同一 chunk 被多次引用 | parse_citations 已去重，safe 渲染 |
| snippet 过长 | 弹层内截断 200 字符 + "展开"按钮 |

#### 6.4 React TSX 示例代码

```tsx
// components/CitationAnswer.tsx
import React, { useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { Popover } from 'antd';

interface CitationItem {
  chunk_id: number | null;
  document_name: string;
  page_number: number | null;
  heading_path: string[];
  snippet: string;
  rerank_score: number | null;
}

interface Props {
  answer: string;
  citations: CitationItem[];
}

function renderAnswerWithCitations(answer: string, citations: CitationItem[]): React.ReactNode {
  // 拆分 answer 为文本段和锚点段
  const parts: Array<{ type: 'text'; text: string } | { type: 'cite'; idx: number }> = [];
  const re = /\[(\d+)\]/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = re.exec(answer)) !== null) {
    if (match.index > lastIndex) {
      parts.push({ type: 'text', text: answer.slice(lastIndex, match.index) });
    }
    parts.push({ type: 'cite', idx: parseInt(match[1], 10) });
    lastIndex = re.lastIndex;
  }
  if (lastIndex < answer.length) {
    parts.push({ type: 'text', text: answer.slice(lastIndex) });
  }

  return (
    <div className="citation-answer">
      {parts.map((part, i) => {
        if (part.type === 'text') {
          return <ReactMarkdown key={i}>{part.text}</ReactMarkdown>;
        }
        const cite = citations[part.idx - 1]; // [1] → index 0
        if (!cite) {
          // 越界编号降级为纯文本
          return <span key={i} className="citation-degraded">[{part.idx}]</span>;
        }
        return (
          <Popover
            key={i}
            trigger="click"
            content={
              <div style={{ maxWidth: 360, fontSize: 13 }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>
                  {cite.document_name}
                  {cite.page_number != null && ` · 第 ${cite.page_number} 页`}
                </div>
                {cite.heading_path.length > 0 && (
                  <div style={{ color: '#888', fontSize: 12, marginBottom: 4 }}>
                    {cite.heading_path.join(' > ')}
                  </div>
                )}
                <div style={{ color: '#555', lineHeight: 1.6 }}>
                  {cite.snippet.length > 200
                    ? cite.snippet.slice(0, 200) + '...'
                    : cite.snippet}
                </div>
                {cite.rerank_score != null && (
                  <div style={{ color: '#aaa', fontSize: 11, marginTop: 4 }}>
                    相关性分: {cite.rerank_score.toFixed(4)}
                  </div>
                )}
              </div>
            }
          >
            <a className="citation-anchor" data-idx={part.idx}>
              [{part.idx}]
            </a>
          </Popover>
        );
      })}
    </div>
  );
}

export default function CitationAnswer({ answer, citations }: Props) {
  return <>{renderAnswerWithCitations(answer, citations)}</>;
}
```

#### 6.5 低 confidence 提示

当 `QueryResponse.confidence < 0.5` 且 `low_confidence_warning` 不为空时，在答案气泡上方展示黄色警告条：

```tsx
{
  confidence != null && confidence < 0.5 && low_confidence_warning && (
    <Alert type="warning" message={low_confidence_warning} showIcon style={{ marginBottom: 8 }} />
  )
}
```

#### 6.6 unverified_claims 展示

当 `unverified_claims` 不为空时，在答案下方折叠展示"未证实声明"卡片：

```tsx
{
  unverified_claims && unverified_claims.length > 0 && (
    <Card size="small" style={{ marginTop: 8, borderColor: '#faad14' }} title="未证实声明">
      <ul>
        {unverified_claims.map((uc, i) => (
          <li key={i}>{uc.claim}</li>
        ))}
      </ul>
    </Card>
  )
}
```

---

### 模块 7：Trace 可视化 —— P1

#### 7.1 数据来源

```typescript
// GET /api/v2/traces/{trace_id}
interface TraceDetail {
  trace_id: string;
  session_id: string | null;
  kb_id: string | null;
  total_latency_ms: number | null;
  steps: TraceStepItem[];
  created_at: string | null;
}

interface TraceStepItem {
  id: string;
  step_type: string;            // 如 "query_rewrite" | "query_ner" | "graph_anchor" | "retrieve" | "build_context" | "generate" | "citation_parse"
  parent_step: string | null;
  step_latency_ms: number | null;
  step_input: Record<string, unknown> | null;
  step_output: Record<string, unknown> | null;
  model_name: string | null;
  token_count: number | null;
  error_message: string | null;
  created_at: string;
}
```

#### 7.2 渲染思路

- 每个 step 一个水平时间条，宽度 = `step_latency_ms / total_latency_ms × container_width`
- 颜色按 `step_type` 区分：
  - `query_rewrite` → 蓝色
  - `query_ner` / `graph_anchor` → 紫色
  - `retrieve` → 绿色
  - `build_context` → 橙色
  - `generate` → 红色
  - `citation_parse` → 灰色
- 点击时间条 → 展开抽屉显示 `step_input` / `step_output` JSON
- 推荐纯 div + flex 布局，轻量无额外依赖

#### 7.3 最小 React 示例代码

```tsx
// pages/TraceDetailPage.tsx
import React, { useEffect, useState } from 'react';
import { useParams, Drawer, Tag, Spin } from 'antd';

interface TraceStepItem {
  id: string;
  step_type: string;
  step_latency_ms: number | null;
  step_input: Record<string, unknown> | null;
  step_output: Record<string, unknown> | null;
  model_name: string | null;
  token_count: number | null;
  error_message: string | null;
}

interface TraceDetail {
  trace_id: string;
  total_latency_ms: number | null;
  steps: TraceStepItem[];
}

const STEP_COLORS: Record<string, string> = {
  query_rewrite: '#1677ff',
  query_ner: '#722ed1',
  graph_anchor: '#722ed1',
  retrieve: '#52c41a',
  build_context: '#fa8c16',
  generate: '#f5222d',
  citation_parse: '#d9d9d9',
};

export default function TraceDetailPage() {
  const { traceId } = useParams<{ traceId: string }>();
  const [trace, setTrace] = useState<TraceDetail | null>(null);
  const [drawerStep, setDrawerStep] = useState<TraceStepItem | null>(null);

  useEffect(() => {
    fetch(`/api/v2/traces/${traceId}`)
      .then(r => r.json())
      .then(data => setTrace(data.data ?? data));
  }, [traceId]);

  if (!trace) return <Spin style={{ display: 'block', margin: '80px auto' }} />;

  const totalMs = trace.total_latency_ms ?? 1;

  return (
    <div style={{ padding: 24 }}>
      <h2>Trace: {trace.trace_id}</h2>
      <div style={{ display: 'flex', gap: 4, height: 40, marginTop: 16 }}>
        {trace.steps.map(step => {
          const widthPct = ((step.step_latency_ms ?? 0) / totalMs) * 100;
          return (
            <div
              key={step.id}
              onClick={() => setDrawerStep(step)}
              style={{
                width: `${Math.max(widthPct, 2)}%`,
                backgroundColor: STEP_COLORS[step.step_type] || '#bbb',
                borderRadius: 4,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: '#fff',
                fontSize: 11,
                overflow: 'hidden',
                whiteSpace: 'nowrap',
              }}
              title={`${step.step_type} (${step.step_latency_ms ?? '-'}ms)`}
            >
              {widthPct > 8 ? step.step_type : ''}
            </div>
          );
        })}
      </div>
      <div style={{ marginTop: 8, fontSize: 12, color: '#888' }}>
        总耗时: {totalMs}ms · 步骤数: {trace.steps.length}
      </div>

      <Drawer
        title={drawerStep?.step_type ?? ''}
        open={!!drawerStep}
        onClose={() => setDrawerStep(null)}
        width={480}
      >
        {drawerStep && (
          <div>
            <p>耗时: {drawerStep.step_latency_ms ?? '-'}ms</p>
            <p>模型: {drawerStep.model_name ?? '-'}</p>
            <p>Token: {drawerStep.token_count ?? '-'}</p>
            {drawerStep.error_message && (
              <Tag color="red">{drawerStep.error_message}</Tag>
            )}
            <h4>输入</h4>
            <pre style={{ fontSize: 12, maxHeight: 200, overflow: 'auto', background: '#f5f5f5', padding: 8 }}>
              {JSON.stringify(drawerStep.step_input, null, 2)}
            </pre>
            <h4>输出</h4>
            <pre style={{ fontSize: 12, maxHeight: 300, overflow: 'auto', background: '#f5f5f5', padding: 8 }}>
              {JSON.stringify(drawerStep.step_output, null, 2)}
            </pre>
          </div>
        )}
      </Drawer>
    </div>
  );
}
```

---

### 模块 8：评估任务管理 —— P2

#### 8.1 接口清单

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v2/knowledge-bases/{kb_id}/evaluate` | 创建评估任务 |
| `GET` | `/api/v2/knowledge-bases/{kb_id}/evaluations` | 评估历史列表 |
| `GET` | `/api/v2/knowledge-bases/{kb_id}/evaluations/{id}` | 评估任务详情 |

#### 8.2 创建评估任务

请求体示例（JSON 文件上传方式）：

```typescript
interface EvalCreateRequest {
  eval_set: Array<{ question: string; ground_truth: string }>;
  retrieval_options?: {
    top_k?: number;
    enable_graph_rag?: boolean;
    reranker_enable?: boolean;
    bm25_enable?: boolean;
    query_rewrite?: 'none' | 'hyde' | 'multi_query';
    similarity_threshold?: number;
  };
  name?: string;
}
```

推荐 UX：管理员上传 JSON 文件 → 前端读取并填充 `eval_set` → POST 创建 → 跳转到详情页。

#### 8.3 评估列表与轮询

```typescript
interface EvalListItem {
  eval_task_id: string;
  name: string | null;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  progress: number;     // 0~100
  question_count: number;
  summary: EvalSummary | null;
  created_at: string;
  completed_at: string | null;
}

interface EvalSummary {
  faithfulness: number | null;
  answer_relevancy: number | null;
  context_precision: number | null;
  context_recall: number | null;
  overall_score: number | null;
}
```

**轮询策略**：当 `status === 'pending' || status === 'processing'` 时，每 5 秒自动刷新该行。`completed` 或 `failed` 时停止轮询。推荐用 React Query 的 `refetchInterval` 实现。

#### 8.4 评估详情页

详情页包含：
- 4 项指标雷达图（faithfulness / answer_relevancy / context_precision / context_recall）
- overall_score 大字展示
- 每道题的可展开样本（question / ground_truth / answer / contexts / 单项指标）

#### 8.5 任务列表 React 示例代码

```tsx
// components/EvalTaskList.tsx
import React, { useState } from 'react';
import { Table, Tag, Progress, Button, Upload } from 'antd';
import { useQuery } from '@tanstack/react-query';

interface EvalListItem {
  eval_task_id: string;
  name: string | null;
  status: string;
  progress: number;
  question_count: number;
  summary: { overall_score: number | null } | null;
  created_at: string;
}

const STATUS_COLORS: Record<string, string> = {
  pending: 'default',
  processing: 'processing',
  completed: 'success',
  failed: 'error',
};

function fetchEvaluations(kbId: string): Promise<EvalListItem[]> {
  return fetch(`/api/v2/knowledge-bases/${kbId}/evaluations`)
    .then(r => r.json())
    .then(resp => resp.data?.items ?? resp.items ?? []);
}

export default function EvalTaskList({ kbId }: { kbId: string }) {
  const { data, refetch } = useQuery({
    queryKey: ['evaluations', kbId],
    queryFn: () => fetchEvaluations(kbId),
    // 自动轮询：有进行中的任务时每 5s 刷新
    refetchInterval: (query) => {
      const items = query.state.data ?? [];
      const hasActive = items.some(i => i.status === 'pending' || i.status === 'processing');
      return hasActive ? 5000 : false;
    },
  });

  const columns = [
    { title: '任务名称', dataIndex: 'name', render: (n: string | null) => n ?? '未命名' },
    {
      title: '状态', dataIndex: 'status',
      render: (s: string) => <Tag color={STATUS_COLORS[s] ?? 'default'}>{s}</Tag>,
    },
    {
      title: '进度', dataIndex: 'progress',
      render: (p: number, record: EvalListItem) =>
        record.status === 'completed' ? '100%' : <Progress percent={p} size="small" />,
    },
    { title: '题数', dataIndex: 'question_count' },
    {
      title: '总分', dataIndex: ['summary', 'overall_score'],
      render: (v: number | null) => v != null ? (v * 100).toFixed(1) + '%' : '-',
    },
  ];

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', gap: 8 }}>
        <Upload accept=".json" showUploadList={false}
          beforeUpload={(file) => { /* 读取 JSON 并跳转到创建页 */ return false; }}>
          <Button type="primary">上传评估集</Button>
        </Upload>
        <Button onClick={() => refetch()}>刷新</Button>
      </div>
      <Table rowKey="eval_task_id" dataSource={data ?? []} columns={columns} />
    </div>
  );
}
```

---

### 模块 9：Analytics 仪表盘 —— P2

#### 9.1 数据来源

```typescript
// GET /api/v2/analytics?start_date=...&end_date=...&kb_id=...
interface AnalyticsResponse {
  total_queries: number;
  avg_latency_ms: number | null;
  avg_confidence: number | null;
  low_confidence_rate: number;      // [0, 1]
  tool_usage: {
    graph_rag_triggered: number;     // [0, 1]
    bm25_contributed: number;        // [0, 1]
    faithfulness_check_triggered: number; // [0, 1]
  };
  token_consumption: {
    total_tokens: number;
  };
  avg_react_steps: number | null;
  error_rate: number;                // [0, 1]
  start_date: string | null;
  end_date: string | null;
}
```

#### 9.2 渲染布局

```
┌──────────────────────────────────────────────────────────┐
│  时间范围: [日期范围选择器]  知识库: [KB 下拉]   [刷新]    │
├──────────────────────────────────────────────────────────┤
│ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐              │
│ │ 查询总数 │ │ 平均延迟 │ │ 平均置信 │ │ 错误率  │              │
│ │  1,234  │ │ 1.2s   │ │  0.85  │ │  2.3%  │              │
│ └────────┘ └────────┘ └────────┘ └────────┘              │
├──────────────────────────────────────────────────────────┤
│  工具使用率                                                │
│  ┌────────────────────────────────────────┐              │
│  │  ████████████ Graph RAG      12%       │              │
│  │  ████████████████████████████ BM25    85%             │
│  │  ████████ Faithfulness      8%        │              │
│  └────────────────────────────────────────┘              │
├──────────────────────────────────────────────────────────┤
│  Token 消耗: 128,450                                     │
│  平均 ReAct 步骤: 3.2                                    │
└──────────────────────────────────────────────────────────┘
```

#### 9.3 最小 React 示例代码

```tsx
// pages/AnalyticsDashboard.tsx
import React, { useState } from 'react';
import { Card, Row, Col, DatePicker, Select, Statistic, Progress } from 'antd';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';

const { RangePicker } = DatePicker;

interface AnalyticsData {
  total_queries: number;
  avg_latency_ms: number | null;
  avg_confidence: number | null;
  low_confidence_rate: number;
  tool_usage: {
    graph_rag_triggered: number;
    bm25_contributed: number;
    faithfulness_check_triggered: number;
  };
  token_consumption: { total_tokens: number };
  avg_react_steps: number | null;
  error_rate: number;
}

function fetchAnalytics(params: { start: string; end: string; kbId?: string }): Promise<AnalyticsData> {
  const qs = new URLSearchParams({ start_date: params.start, end_date: params.end });
  if (params.kbId) qs.set('kb_id', params.kbId);
  return fetch(`/api/v2/analytics?${qs}`).then(r => r.json()).then(resp => resp.data ?? resp);
}

export default function AnalyticsDashboard() {
  const [dateRange, setDateRange] = useState<[string, string]>([
    dayjs().subtract(7, 'day').format('YYYY-MM-DD'),
    dayjs().format('YYYY-MM-DD'),
  ]);
  const [kbId, setKbId] = useState<string | undefined>();

  const { data } = useQuery({
    queryKey: ['analytics', ...dateRange, kbId],
    queryFn: () => fetchAnalytics({ start: dateRange[0], end: dateRange[1], kbId }),
  });

  if (!data) return null;

  const toolBars = [
    { label: 'Graph RAG', value: data.tool_usage.graph_rag_triggered, color: '#722ed1' },
    { label: 'BM25', value: data.tool_usage.bm25_contributed, color: '#52c41a' },
    { label: 'Faithfulness', value: data.tool_usage.faithfulness_check_triggered, color: '#fa8c16' },
  ];

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 16, display: 'flex', gap: 12, alignItems: 'center' }}>
        <RangePicker
          value={[dayjs(dateRange[0]), dayjs(dateRange[1])]}
          onChange={(dates) => {
            if (dates && dates[0] && dates[1]) {
              setDateRange([dates[0].format('YYYY-MM-DD'), dates[1].format('YYYY-MM-DD')]);
            }
          }}
        />
        <Select
          placeholder="选择知识库"
          allowClear
          style={{ width: 200 }}
          onChange={setKbId}
        />
      </div>

      <Row gutter={16}>
        <Col span={6}><Card><Statistic title="查询总数" value={data.total_queries} /></Card></Col>
        <Col span={6}>
          <Card>
            <Statistic title="平均延迟" value={data.avg_latency_ms != null ? (data.avg_latency_ms / 1000).toFixed(2) + 's' : '-'} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="平均置信度" value={data.avg_confidence != null ? (data.avg_confidence * 100).toFixed(1) + '%' : '-'} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="错误率" value={(data.error_rate * 100).toFixed(1) + '%'} />
          </Card>
        </Col>
      </Row>

      <Card title="工具使用率" style={{ marginTop: 16 }}>
        {toolBars.map(tb => (
          <div key={tb.label} style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <span>{tb.label}</span>
              <span>{(tb.value * 100).toFixed(0)}%</span>
            </div>
            <Progress percent={tb.value * 100} strokeColor={tb.color} showInfo={false} />
          </div>
        ))}
      </Card>

      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={12}>
          <Card title="Token 消耗">
            <Statistic value={data.token_consumption.total_tokens} suffix="tokens" />
          </Card>
        </Col>
        <Col span={12}>
          <Card title="平均 ReAct 步骤">
            <Statistic value={data.avg_react_steps ?? '-'} />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
```

---

### 模块 10：检索参数高级面板 —— P3

#### 10.1 触发位置

对话页输入框旁的"⚙️ 高级"按钮 → 点击弹出右侧抽屉。

```
┌─────────────────────────────────────────────┐
│ [选择 KB ▼] [输入框...]  [⚙️] [发送]        │
└─────────────────────────────────────────────┘
```

#### 10.2 字段清单

所有字段对应 `QueryOptions` Schema：

| 字段 | 控件 | 默认值 | 说明 |
|---|---|---|---|
| `query_rewrite` | Radio（none / hyde / multi_query） | 从 KB.retrieval_config 读取 | 改写策略 |
| `enable_graph_rag` | Toggle | 从 KB.retrieval_config 读取 | 图谱锚定 |
| `bm25_enable` | Toggle | 从 KB.retrieval_config 读取 | BM25 混合检索 |
| `reranker_enable` | Toggle | 从 KB.retrieval_config 读取 | Reranker 精排 |
| `similarity_threshold` | Slider 0~1，步长 0.05 | 从 KB.retrieval_config 读取 | 相似度阈值 |
| `top_k` | Number 1~50 | 从 KB.retrieval_config 读取 | 返回结果数 |
| `enable_faithfulness_check` | Toggle | settings 默认 False | 答案自检 |

#### 10.3 数据流

1. 用户打开抽屉时，从当前 KB 的 `retrieval_config` 获取默认值
2. 用户修改后 → 合并到 `QueryOptions` 发送给 `/api/v2/query`
3. 用户修改后 → 持久化到 `localStorage`（key = `v2_query_options`），下次打开自动回填

#### 10.4 最小 React 示例代码

```tsx
// components/AdvancedOptionsDrawer.tsx
import React, { useEffect } from 'react';
import { Drawer, Radio, Switch, Slider, InputNumber, Form } from 'antd';

interface QueryOptions {
  query_rewrite: string;
  enable_graph_rag: boolean;
  bm25_enable: boolean;
  reranker_enable: boolean;
  similarity_threshold: number;
  top_k: number;
  enable_faithfulness_check: boolean;
}

const STORAGE_KEY = 'v2_query_options';

export default function AdvancedOptionsDrawer({
  open,
  onClose,
  onChange,
  kbRetrievalConfig, // 从 KB 详情拉取的 retrieval_config
}: {
  open: boolean;
  onClose: () => void;
  onChange: (opts: Partial<QueryOptions>) => void;
  kbRetrievalConfig: Record<string, unknown> | null;
}) {
  const [form] = Form.useForm();

  useEffect(() => {
    if (!open) return;
    // 优先级：localStorage > KB 配置 > 硬编码默认值
    const saved = localStorage.getItem(STORAGE_KEY);
    const defaults: QueryOptions = {
      query_rewrite: (kbRetrievalConfig?.query_rewrite as string) ?? 'none',
      enable_graph_rag: (kbRetrievalConfig?.enable_graph_rag as boolean) ?? true,
      bm25_enable: (kbRetrievalConfig?.bm25_enable as boolean) ?? true,
      reranker_enable: (kbRetrievalConfig?.reranker_enable as boolean) ?? false,
      similarity_threshold: (kbRetrievalConfig?.similarity_threshold as number) ?? 0.3,
      top_k: (kbRetrievalConfig?.top_k as number) ?? 5,
      enable_faithfulness_check: false,
    };
    const merged = saved ? { ...defaults, ...JSON.parse(saved) } : defaults;
    form.setFieldsValue(merged);
  }, [open, kbRetrievalConfig, form]);

  const handleValuesChange = (_changed: Partial<QueryOptions>, allValues: QueryOptions) => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(allValues));
    onChange(allValues);
  };

  return (
    <Drawer title="高级检索参数" open={open} onClose={onClose} width={380}>
      <Form form={form} layout="vertical" onValuesChange={handleValuesChange}>
        <Form.Item label="Query 改写" name="query_rewrite">
          <Radio.Group>
            <Radio value="none">不改写</Radio>
            <Radio value="hyde">HyDE（假设答案）</Radio>
            <Radio value="multi_query">多角度查询</Radio>
          </Radio.Group>
        </Form.Item>
        <Form.Item label="图谱锚定" name="enable_graph_rag" valuePropName="checked">
          <Switch />
        </Form.Item>
        <Form.Item label="BM25 混合检索" name="bm25_enable" valuePropName="checked">
          <Switch />
        </Form.Item>
        <Form.Item label="Reranker 精排" name="reranker_enable" valuePropName="checked">
          <Switch />
        </Form.Item>
        <Form.Item label="相似度阈值" name="similarity_threshold">
          <Slider min={0} max={1} step={0.05} tooltip={{ formatter: (v) => v?.toFixed(2) }} />
        </Form.Item>
        <Form.Item label="返回条数" name="top_k">
          <InputNumber min={1} max={50} />
        </Form.Item>
        <Form.Item label="答案自检" name="enable_faithfulness_check" valuePropName="checked">
          <Switch />
        </Form.Item>
      </Form>
    </Drawer>
  );
}
```

---

## 4. 支撑模块

### 支撑 C：Trace ID 生命周期管理（V2 新增）

#### C.1 trace_id 的产生

每次 `POST /api/v2/query` 成功返回后，响应体中带回 `trace_id`（16 字符短哈希）：

```json
{
  "answer": "台风是一种热带气旋[1]...",
  "source_citations": [...],
  "trace_id": "a1b2c3d4e5f67890",
  ...
}
```

#### C.2 前端透传建议

将 `trace_id` 挂在对话气泡的 `data-trace-id` 属性上，并在气泡下方添加"查看 trace"按钮（仅在开发者模式或低 confidence 场景显示）：

```tsx
<div className="bot-bubble" data-trace-id={resp.trace_id}>
  <CitationAnswer answer={resp.answer} citations={resp.source_citations} />
  
  {/* 低 confidence 或开发者模式才显示 trace 按钮 */}
  {(isDevMode || (resp.confidence != null && resp.confidence < 0.5)) && (
    <Button
      type="link"
      size="small"
      onClick={() => navigate(`/dev/trace/${resp.trace_id}`)}
      style={{ marginTop: 8 }}
    >
      查看 trace
    </Button>
  )}
</div>
```

#### C.3 保留策略

- trace_id 长度 16 字符
- 后端保留 30 天（`TRACE_RETENTION_DAYS` 配置）
- 前端不需要缓存 trace_id，但建议将最近 50 条的 trace_id 存到 zustand store，方便对话中跳转

#### C.4 无 trace 场景兜底

以下场景 `trace_id` 可能为 `null`：
- V1.5 的 `/api/v1/chat/stream` 流式对话（无 trace）
- 网络异常或后端降级

此时"查看 trace"按钮不渲染。

---

## 5. 推荐开发顺序

建议 1 人 5~7 天完成 V2 全部前端工作：

| 顺序 | 模块 | 工作量 | 说明 |
|---|---|---|---|
| 1 | **模块 6 Citation 高亮** | 1 天 | 必做，对话页可见到 V2 核心价值 |
| 2 | **支撑 C trace_id 透传** | 0.5 天 | 对话页内嵌"查看 trace"按钮 |
| 3 | **模块 10 高级面板** | 0.5 天 | 对话页输入框旁抽屉，独立不影响主流程 |
| 4 | **模块 7 Trace 可视化** | 1.5 天 | 独立开发者页面，时间条 + JSON 抽屉 |
| 5 | **模块 9 Analytics 仪表盘** | 1.5 天 | 独立运营页面，数字卡片 + 工具使用率柱图 |
| 6 | **模块 8 评估任务管理** | 2 天 | 列表 + 详情 + 雷达图 + 轮询 + 上传 JSON |

### 依赖关系

```
模块 6（无前置依赖） ──→ 支撑 C（模块 6 完成后）
模块 10（无前置依赖）
模块 7（依赖支撑 C 完成，需要 trace_id 能拿到）
模块 9（无前置依赖，纯运营页面）
模块 8（无前置依赖，纯运营页面）
```

---

## 6. 关键 UX 决策清单（V2 部分）

| 场景 | 推荐做法 | 理由 |
|---|---|---|
| 低 confidence (< 0.5) | 答案气泡上方黄色警告条 + PRD §556 文案 | 让用户知道答案可信度低，谨慎采用 |
| faithfulness_check = ok | 不显示任何标记 | 正常状态，不打扰用户 |
| faithfulness_check = skipped | 灰色小图标 + tooltip "自检异常已跳过" | 运维可见，用户不感知 |
| faithfulness_check = disabled | 不显示任何标记 | 用户未启用，无需提示 |
| unverified_claims 非空 | 折叠在答案下方"未证实声明"卡片，黄色边框 | 不阻塞阅读，但可追溯 |
| multi_query 改写 | sub_queries 在答案下方折叠展示"AI 实际理解的问题" | 增加透明度，让用户理解"AI 怎么理解我的问题" |
| trace_id 默认展示 | 不展示给最终用户，仅在低 confidence 或开发者模式时出现"查看 trace"按钮 | 避免信息噪音 |
| 高级面板持久化 | localStorage 存最近一次 options，下次对话自动回填 | 减少重复设置 |
| 高级面板默认值 | 从用户当前 KB 的 retrieval_config 读取 | 与 KB 级配置对齐 |
| 评估任务轮询 | 仅对 pending/processing 状态的任务每 5s 轮询 | 减少无效请求 |
| 评估集上传 | 仅支持 JSON 文件，前端校验格式后再 POST | 避免无效请求浪费后端资源 |
| Analytics 默认时间 | 最近 7 天 | 覆盖足够数据量又不至于太宽泛 |
| 检索空结果 | answer 返回兜底文案 + confidence=0.0 + trace_id 仍透传 | 让用户知道"查不到但链路没问题" |

---

## 7. OpenAPI SDK 生成（V1+V2 一并）

后端 `/openapi.json` 自动包含 V1.5 + V2.0 全部接口，生成一次即可覆盖全量：

```bash
# 1. 确保后端启动
# 2. 下载 OpenAPI 规范
curl http://127.0.0.1:8000/openapi.json > openapi.json

# 3. 用 openapi-typescript 生成 TS 类型（推荐）
npm i -D openapi-typescript
npx openapi-typescript openapi.json -o src/api/types.ts

# 4. 用 openapi-fetch 创建类型安全的 fetch client
npm install openapi-fetch

# 或者沿用 V1.5 的 openapi-typescript-codegen 方案：
npm i -D openapi-typescript-codegen
npx openapi-typescript-codegen \
  --input openapi.json \
  --output ./src/api/generated \
  --client axios \
  --useOptions
```

生成的类型会包含 V2 的 `/api/v2/query`、`/api/v2/traces`、`/api/v2/analytics`、`/api/v2/knowledge-bases/{kb_id}/evaluations` 等全部新接口。

> SSE 接口（V1.5 `/api/v1/chat/stream`）OpenAPI 暂未很好支持，继续使用 V1.5 已有的 `useChatStream` hook。

---

## 8. 参考资源

- [v2_api_reference.md](v2_api_reference.md) —— V2.0 接口契约（待生成，参考 progress.md 中各阶段交付内容）
- [architecture.md V2 章节](architecture.md#第三部分--v20-hermes-增量) —— 后端架构设计
- [progress.md](progress.md) —— 当前实现进度与验收状态
- [v2_dev_plan.md](v2_dev_plan.md) —— V2.0 开发拆分计划（T0~T12）
- [TyAgent V2.0 · 需求规格说明书](TyAgent%20V2.0%20%C2%B7%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md) —— V2.0 PRD
- [v1_5_frontend_guide.md](v1_5_frontend_guide.md) —— V1.5 前端模块（本文不重写，模块 1~5 + 支撑 A/B 见该文档）
- 在线 API 文档（开发时实时查阅）：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

---

*TyAgent V2.0 Frontend Guide · End of Document*

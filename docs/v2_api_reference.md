# TyAgent V2.0 · 接口文档

> **基线版本**：V2.0 Hermes（2026-06-17 全链路 smoke 验收通过）
> **配套文档**：[PRD](TyAgent%20V2.0%20%C2%B7%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md) · [架构 V2.0 章节](architecture.md#第三部分--v20-hermes-增量) · [开发计划](v2_dev_plan.md) · [进度](progress.md) · [前端联调](v2_frontend_guide.md)
> **V1.5 接口**：[v1_5_api_reference.md](v1_5_api_reference.md)（V2.0 不重写 V1.5 接口，只新增 V2 接口与 V1 接口字段扩展）
> **在线交互**：服务启动后访问 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)（Swagger UI）

---

## 0. 通用约定

### 0.1 BaseURL & 路径前缀

| 环境 | URL |
|---|---|
| 开发 | `http://127.0.0.1:8000` |
| 测试/生产 | 由部署方决定（V1 接口保持 `/api/v1`，V2 接口保持 `/api/v2` 前缀） |

V2.0 新增端点全部挂载在 `/api/v2` 路径下。V1.5 已有接口路径不变，详见 [V1.5 接口文档](v1_5_api_reference.md)。

### 0.2 响应格式

沿用 V1.5 的统一响应格式，详见 [V1.5 §0.2](v1_5_api_reference.md#02-统一响应格式prd-§71)。

所有 REST 接口一律返回 `{code, message, data}` 包裹结构。仅 `/v1/chat/stream` 的 200 响应是 SSE 流。

```json
// 成功
{
  "code": 0,
  "message": "success",
  "data": { ... }
}

// 失败
{
  "code": 40011,
  "message": "query_rewrite 参数值不在枚举范围内",
  "data": null
}
```

### 0.3 错误码（V2 新增 4 个）

V1.5 的错误码总表见 [V1.5 §0.3](v1_5_api_reference.md#03-业务错误码表prd-§72)。以下仅列出 V2.0 **新增**的业务错误码：

| HTTP | 业务 code | 含义 | 触发场景 |
|---|---|---|---|
| 400 | 40011 | QUERY_REWRITE_INVALID | `options.query_rewrite` 不是 `none` / `hyde` / `multi_query` 之一（HRE-01 / PRD §1127） |
| 400 | 40012 | EVAL_DATASET_EMPTY | 评估时 `eval_set` 为空数组（EVA-01 / PRD §805） |
| 400 | 40013 | EVAL_DATASET_TOO_LARGE | 评估题数超出 `EVAL_MAX_QUESTIONS` 上限（默认 100）（EVA-01） |
| 422 | 42201 | CONTEXT_CHUNKS_EMPTY | `/v2/generate` 的 `context_chunks` 为空（UQA-03 / PRD §1129） |

V1.5 的 `50300 CELERY_UNAVAILABLE` 在 V2 评估接口（§4.1）同样适用。

### 0.4 接口总览（11 端点速查表）

| 模块 | Method | Path | 说明 |
|---|---|---|---|
| **V2 统一查询** | POST | `/api/v2/query` | 统一查询：三层配置合并 -> Query 改写 -> NER -> 图谱锚定 -> 混合检索 -> LLM 生成 -> 溯源/自检 |
| **V2 分层子接口** | POST | `/api/v2/retrieve` | 纯检索子接口（UQA-02） |
| | POST | `/api/v2/generate` | 纯生成子接口（UQA-03） |
| | POST | `/api/v2/rerank` | Reranker 精排子接口（UQA-04） |
| **V2 可观测性** | GET | `/api/v2/traces/{trace_id}` | 单条 trace 完整步骤链路（OBS-01） |
| | GET | `/api/v2/traces/sessions/{session_id}/traces` | 某会话的所有 trace 列表（OBS-02） |
| | GET | `/api/v2/analytics` | 聚合统计（OBS-03） |
| **V2 RAGAS 评估** | POST | `/api/v2/knowledge-bases/{kb_id}/evaluate` | 创建评估任务（EVA-01，异步执行） |
| | GET | `/api/v2/knowledge-bases/{kb_id}/evaluations/{eval_task_id}` | 查评估进度 + 结果（EVA-02） |
| | GET | `/api/v2/knowledge-bases/{kb_id}/evaluations` | 评估历史列表（EVA-03） |
| **V1 接口扩展** | PATCH | `/api/v1/knowledge-bases/{kb_id}` | V1 接口新增 `retrieval_config` 字段（HRE-06） |

---

## 1. V2 统一查询

### 1.1 POST /api/v2/query

**功能**：V2.0 统一查询入口。完整链路：三层配置合并（API > KB > settings）→ Query 改写（HyDE / multi_query）→ Query NER → 图谱锚定 → 混合检索（向量 + BM25 + RRF + Reranker）→ LLM 生成 → Citation 溯源 → 答案自检 → 置信度评分。当前仅支持非流式（同步返回完整 JSON）。

**请求体**：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `query` | string | 是 | - | 查询文本，1~2000 字符 |
| `session_id` | string (UUID) | 否 | null | 关联会话 ID（用于 Trace 绑定） |
| `kb_ids` | array[string (UUID)] | 否 | null | 限定知识库列表（多 KB 时本期取第一个做配置合并） |
| `options` | object | 否 | `{}` | 查询选项（见下方 QueryOptions） |

**QueryOptions 字段**（`options` 对象内）：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `top_k` | int | 否 | null（跟随下层） | 返回结果数量，1~50 |
| `reranker_enable` | bool | 否 | null（跟随下层） | 是否启用 Reranker 精排 |
| `bm25_enable` | bool | 否 | null（跟随下层） | 是否启用 BM25 稀疏检索 |
| `stream` | bool | 否 | false | 是否使用流式输出（SSE）—— 当前仅支持 false |
| `query_rewrite` | string | 否 | null（跟随下层） | Query 改写策略：`none` / `hyde` / `multi_query`（HRE-01） |
| `enable_graph_rag` | bool | 否 | null（跟随下层） | 是否启用 Graph RAG 锚定（HRE-02） |
| `similarity_threshold` | float | 否 | null（跟随下层） | Reranker 过滤阈值，0.0~1.0（HRE-05） |
| `enable_faithfulness_check` | bool | 否 | null（跟随下层） | 是否启用答案自检（CHC-04）；None 跟随配置 |

**响应体** `data` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `answer` | string | 生成的答案文本 |
| `source_citations` | array[CitationItem] | 引用列表（溯源信息） |
| `trace_id` | string | 请求追踪 ID（UUID 字符串，可用于 OBS-01/02 查询步骤链路） |
| `total_latency_ms` | int | 请求总延迟（毫秒） |
| `rewritten_query` | string | HyDE 改写后的假设性答案（仅 hyde 策略下有值） |
| `sub_queries` | array[string] | multi_query 拆出的子查询列表（仅 multi_query 策略下有值） |
| `ner_entities` | array[object] | Query NER 抽取的实体 `[{"name": ..., "type": ...}]` |
| `graph_anchored_tags` | array[string] | 图谱锚定后注入 Milvus entity_tags 的标签列表 |
| `confidence` | float | CHC-03 整体置信度（0~1），基于被引用 chunk 的 rerank 分加权 + 引用覆盖率 + 自检惩罚 |
| `low_confidence_warning` | string | confidence < 0.5 时的预警文案 |
| `faithfulness_check` | string | CHC-04 自检状态：`ok` / `skipped` / `disabled` |
| `unverified_claims` | array[object] | 未被支撑的事实声明列表 `[{claim, status, source_text}]` |

**CitationItem 结构**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `chunk_id` | int | 切片 ID（Milvus INT64 PK） |
| `document_name` | string | 来源文档名称 |
| `page_number` | int | 页码 |
| `heading_path` | array[string] | 标题路径（如 `["第一章", "第一节"]`） |
| `snippet` | string | 引用片段文本 |
| `rerank_score` | float | Reranker 精排分数 |

**错误码**：

- `40001` 请求参数校验失败（query 为空 / 超长等）
- `40011` `query_rewrite` 不是 `none`/`hyde`/`multi_query` 之一（三层合并后最终值不合法）
- `50000` 服务器内部错误

**示例**：

**示例 1：基础查询（默认选项）**

```bash
curl -X POST http://127.0.0.1:8000/api/v2/query \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "2024 年台风生成数量",
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "kb_ids": ["kb-uuid-aaaa-bbbb-cccc-ddddeeee0001"]
  }'
```

```json
// 响应
{
  "code": 0,
  "message": "success",
  "data": {
    "answer": "2024 年西北太平洋和南海共有 **25 个** 台风生成...",
    "source_citations": [
      {
        "chunk_id": 10001,
        "document_name": "2024年台风年鉴.pdf",
        "page_number": 3,
        "heading_path": ["第三章", "台风统计"],
        "snippet": "2024 年西北太平洋和南海共有 25 个台风生成，较常年偏多...",
        "rerank_score": 0.912
      }
    ],
    "trace_id": "trc-xxx-yyy-zzz",
    "total_latency_ms": 1842,
    "rewritten_query": null,
    "sub_queries": null,
    "ner_entities": [{"name": "2024年", "type": "TIME"}, {"name": "台风", "type": "PHENOMENON"}],
    "graph_anchored_tags": ["typhoon", "2024"],
    "confidence": 0.87,
    "low_confidence_warning": null,
    "faithfulness_check": "disabled",
    "unverified_claims": null
  }
}
```

**示例 2：HyDE 改写查询**

```bash
curl -X POST http://127.0.0.1:8000/api/v2/query \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "台风路径预报误差",
    "options": {
      "query_rewrite": "hyde",
      "top_k": 10
    }
  }'
```

```json
// 响应（data.rewritten_query 有值）
{
  "code": 0,
  "message": "success",
  "data": {
    "answer": "台风路径预报误差通常以 24h/48h/72h 平均距离误差衡量...",
    "rewritten_query": "台风路径预报误差通常以 24h/48h/72h 平均距离误差衡量，2024 年中央气象台 24h 平均路径误差约为 65 公里...",
    "sub_queries": null,
    "confidence": 0.92,
    ...
  }
}
```

**示例 3：multi_query 改写 + 答案自检**

```bash
curl -X POST http://127.0.0.1:8000/api/v2/query \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "高温预警信号等级",
    "options": {
      "query_rewrite": "multi_query",
      "enable_faithfulness_check": true
    }
  }'
```

```json
// 响应（data.sub_queries 有值，faithfulness_check 为 ok）
{
  "code": 0,
  "message": "success",
  "data": {
    "answer": "高温预警信号分为三个等级...",
    "sub_queries": [
      "高温预警信号有哪些等级",
      "高温预警信号分级标准",
      "高温预警信号颜色含义"
    ],
    "confidence": 0.95,
    "faithfulness_check": "ok",
    "unverified_claims": null,
    ...
  }
}
```

---

## 2. V2 分层子接口

### 2.1 POST /api/v2/retrieve

**功能**：纯检索子接口（UQA-02）。只执行检索（混合检索 + Graph RAG 锚定），不调用 LLM 生成答案。返回经过 RRF 融合 + Reranker 精排处理后的 Chunk 列表。

**请求体**：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `query` | string | 是 | - | 检索查询文本，1~2000 字符 |
| `kb_ids` | array[string (UUID)] | 否 | null | 限定知识库列表 |
| `top_k` | int | 否 | 5 | 返回结果数量，1~50 |
| `enable_graph_rag` | bool | 否 | null | 是否启用 Graph RAG 锚定 |
| `enable_bm25` | bool | 否 | null | 是否启用 BM25 稀疏检索 |
| `rerank` | bool | 否 | true | 是否启用 Reranker 精排 |
| `similarity_threshold` | float | 否 | null | Reranker 过滤阈值，0.0~1.0 |

**响应体** `data` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `chunks` | array[RetrieveChunkItem] | 检索结果列表（按 rerank_score 降序） |
| `total_retrieved` | int | Rerank 前检索总命中数 |
| `after_rerank` | int | Rerank 后保留数 |
| `trace_id` | string | 追踪 ID（当前为空字符串，预留） |
| `total_latency_ms` | int | 请求总延迟（毫秒） |

**RetrieveChunkItem 结构**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `chunk_id` | int | 切片 ID |
| `content` | string | 切片文本内容 |
| `document_name` | string | 来源文档名称 |
| `page_number` | int | 页码 |
| `heading_path` | array[string] | 标题路径 |
| `vector_score` | float | 稠密向量检索分数 |
| `bm25_score` | float | BM25 稀疏检索分数 |
| `rrf_score` | float | RRF 融合分数 |
| `rerank_score` | float | Reranker 精排分数（结果按此降序） |
| `metadata` | object | 原始元数据 |

**错误码**：

- `40001` 请求参数校验失败
- `50000` 检索过程内部失败（异常时返回空 chunks + total_retrieved=0）

**示例**：

```bash
curl -X POST http://127.0.0.1:8000/api/v2/retrieve \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "2024 年台风生成数量",
    "top_k": 3,
    "rerank": true
  }'
```

```json
// 响应
{
  "code": 0,
  "message": "success",
  "data": {
    "chunks": [
      {
        "chunk_id": 10001,
        "content": "2024 年西北太平洋和南海共有 25 个台风生成...",
        "document_name": "2024年台风年鉴.pdf",
        "page_number": 3,
        "heading_path": ["第三章", "台风统计"],
        "rerank_score": 0.912,
        "vector_score": 0.845,
        "bm25_score": 0.723,
        "rrf_score": 0.089
      }
    ],
    "total_retrieved": 15,
    "after_rerank": 3,
    "trace_id": "",
    "total_latency_ms": 356
  }
}
```

### 2.2 POST /api/v2/generate

**功能**：纯生成子接口（UQA-03）。接受开发者自定义的 `context_chunks`，跳过检索步骤，直接调 LLM 生成答案 + Citation 溯源 + 答案自检。**不触发任何 Milvus / Neo4j 查询**。

**请求体**：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `query` | string | 是 | - | 查询文本，1~2000 字符 |
| `context_chunks` | array[ContextChunk] | 是 | - | 自定义上下文块列表（至少 1 条，否则 42201） |
| `options` | object | 否 | `{}` | 生成选项 |

**ContextChunk 结构**（`context_chunks` 数组元素）：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `chunk_id` | string | 是 | - | 上下文块唯一标识（≥1 字符） |
| `content` | string | 是 | - | 上下文文本内容（≥1 字符） |
| `source_label` | string | 否 | "" | 来源标签（如 "采购合同_2024.pdf P3"），用于 Citation 映射 |

**GenerateOptions 字段**（`options` 对象内）：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `stream` | bool | 否 | false | 是否流式输出（预留，暂不支持） |
| `enable_citation` | bool | 否 | true | 是否启用 Citation 溯源 |
| `enable_faithfulness_check` | bool | 否 | false | 是否启用答案自检 |

**响应体** `data` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `answer` | string | 生成的答案文本 |
| `source_citations` | array[CitationItem] | 引用列表（与 §1.1 同结构） |
| `confidence` | float | 置信度（0~1） |
| `low_confidence_warning` | string | 低置信度预警文案 |
| `faithfulness_check` | string | 自检状态：`ok` / `skipped` / `disabled` |
| `unverified_claims` | array[object] | 未被支撑的事实声明列表 |
| `trace_id` | string | 追踪 ID（当前为空字符串，预留） |
| `total_latency_ms` | int | 请求总延迟（毫秒） |

**错误码**：

- `40001` 请求参数校验失败
- `42201` `context_chunks` 为空列表（至少需要 1 条上下文块）
- `50000` LLM 生成失败（软降级：返回兜底文案 + confidence=0）

**示例**：

```bash
curl -X POST http://127.0.0.1:8000/api/v2/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "2024 年台风生成数量是多少？",
    "context_chunks": [
      {
        "chunk_id": "my-chunk-001",
        "content": "2024 年西北太平洋和南海共有 25 个台风生成，较常年（27.1 个）偏少 2.1 个。",
        "source_label": "2024年台风统计.pdf P3"
      },
      {
        "chunk_id": "my-chunk-002",
        "content": "2024 年台风生成位置主要集中在西北太平洋中部海域，其中超强台风 6 个。",
        "source_label": "2024年台风统计.pdf P4"
      }
    ],
    "options": {
      "enable_citation": true,
      "enable_faithfulness_check": true
    }
  }'
```

```json
// 响应
{
  "code": 0,
  "message": "success",
  "data": {
    "answer": "2024 年西北太平洋和南海共有 **25 个** 台风生成[1]，较常年（27.1 个）偏少。其中超强台风 6 个[2]。",
    "source_citations": [
      {
        "chunk_id": null,
        "document_name": "2024年台风统计.pdf P3",
        "snippet": "2024 年西北太平洋和南海共有 25 个台风生成...",
        "rerank_score": null
      },
      {
        "chunk_id": null,
        "document_name": "2024年台风统计.pdf P4",
        "snippet": "2024 年台风生成位置主要集中在西北太平洋中部海域...",
        "rerank_score": null
      }
    ],
    "confidence": 0.93,
    "faithfulness_check": "ok",
    "trace_id": "",
    "total_latency_ms": 2105
  }
}
```

### 2.3 POST /api/v2/rerank

**功能**：独立 Reranker 精排子接口（UQA-04）。接受 Query + 候选文本列表，返回按 `rerank_score` **降序**排列的精排结果。允许开发者将 Hermes 的 Reranker 能力独立使用。

**请求体**：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `query` | string | 是 | - | 查询文本，1~2000 字符 |
| `candidates` | array[RerankCandidate] | 是 | - | 候选文本列表（至少 1 条） |
| `top_n` | int | 否 | 5 | 返回的最大数量，1~50 |

**RerankCandidate 结构**（`candidates` 数组元素）：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | 候选文本唯一标识（≥1 字符） |
| `text` | string | 是 | 候选文本内容（≥1 字符） |

**响应体** `data` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `results` | array[RerankResultItem] | 按 `rerank_score` **降序**排列的结果列表 |
| `total_latency_ms` | int | 请求总延迟（毫秒） |

**RerankResultItem 结构**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 候选文本标识（与请求中的 `id` 对应） |
| `text` | string | 候选文本内容 |
| `rerank_score` | float | 精排分数（降序排列） |

**错误码**：

- `40001` 请求参数校验失败
- `50000` Reranker 调用失败（软降级：返回原顺序，分数标 0.0）

**示例**：

```bash
curl -X POST http://127.0.0.1:8000/api/v2/rerank \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "2024 年台风生成数量",
    "candidates": [
      {"id": "doc1", "text": "2024 年西北太平洋和南海共有 25 个台风生成。"},
      {"id": "doc2", "text": "台风是一种强烈的热带气旋。"},
      {"id": "doc3", "text": "2024 年台风造成经济损失约 500 亿元。"}
    ],
    "top_n": 3
  }'
```

```json
// 响应（按 rerank_score 降序）
{
  "code": 0,
  "message": "success",
  "data": {
    "results": [
      {"id": "doc1", "text": "2024 年西北太平洋和南海共有 25 个台风生成。", "rerank_score": 0.952},
      {"id": "doc3", "text": "2024 年台风造成经济损失约 500 亿元。", "rerank_score": 0.734},
      {"id": "doc2", "text": "台风是一种强烈的热带气旋。", "rerank_score": 0.215}
    ],
    "total_latency_ms": 128
  }
}
```

---

## 3. V2 可观测性

### 3.1 GET /api/v2/traces/{trace_id}

**功能**：获取单条 trace 的完整步骤链路（OBS-01）。`trace_id` 来自 `/v2/query` 响应中的 `trace_id` 字段。

**路径参数**：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `trace_id` | string | 是 | 追踪 ID（UUID 字符串） |

**响应体** `data` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `trace_id` | string | 追踪 ID |
| `session_id` | string (UUID) | 关联会话 ID |
| `kb_id` | string (UUID) | 关联知识库 ID |
| `total_latency_ms` | int | 总延迟（毫秒，从根步骤取） |
| `steps` | array[TraceStepItem] | 步骤列表（按 created_at 升序） |
| `created_at` | string (ISO 8601) | 创建时间 |

**TraceStepItem 结构**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string (UUID) | 步骤 ID |
| `step_type` | string | 步骤类型（如 `query_rewrite`、`query_ner`、`graph_anchor`、`retrieve`、`build_context`、`generate`、`citation_parse`、`faithfulness_check`） |
| `parent_step` | string | 父步骤 ID（根步骤为 null） |
| `step_latency_ms` | int | 本步骤耗时（毫秒） |
| `step_input` | object | 步骤输入参数 |
| `step_output` | object | 步骤输出结果 |
| `model_name` | string | LLM 模型名称（LLM 步骤有值） |
| `token_count` | int | Token 消耗数（LLM 步骤有值） |
| `error_message` | string | 错误信息（步骤失败时有值） |
| `created_at` | string (ISO 8601) | 步骤创建时间 |

**错误码**：

- `404`（HTTP 404）trace_id 不存在（直接抛 HTTPException，非 BusinessError）

**示例**：

```bash
curl http://127.0.0.1:8000/api/v2/traces/trc-xxx-yyy-zzz
```

```json
// 响应
{
  "code": 0,
  "message": "success",
  "data": {
    "trace_id": "trc-xxx-yyy-zzz",
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "kb_id": "kb-uuid-aaaa-bbbb-cccc-ddddeeee0001",
    "total_latency_ms": 1842,
    "steps": [
      {
        "id": "step-1111",
        "step_type": "query_rewrite",
        "parent_step": null,
        "step_latency_ms": 150,
        "step_input": {"strategy": "none", "query_len": 12},
        "step_output": {"rewritten_len": 0, "sub_query_count": 0},
        "model_name": null,
        "token_count": null,
        "error_message": null,
        "created_at": "2026-06-17T10:00:00.123+00:00"
      },
      {
        "id": "step-2222",
        "step_type": "generate",
        "parent_step": null,
        "step_latency_ms": 1200,
        "step_input": {"model": "gpt-4o-mini"},
        "step_output": {"answer_len": 523},
        "model_name": "gpt-4o-mini",
        "token_count": 856,
        "error_message": null,
        "created_at": "2026-06-17T10:00:01.323+00:00"
      }
    ],
    "created_at": "2026-06-17T10:00:00.100+00:00"
  }
}
```

### 3.2 GET /api/v2/traces/sessions/{session_id}/traces

**功能**：获取某会话的所有 trace 列表（OBS-02），分页返回，不含步骤详情。

**路径参数**：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `session_id` | string (UUID) | 是 | 会话 ID |

**Query 参数**：

| 参数 | 类型 | 默认 | 范围 | 说明 |
|---|---|---|---|---|
| `page` | int | 1 | ≥1 | 页码 |
| `page_size` | int | 20 | 1~100 | 每页条数 |

**响应体** `data` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `items` | array[TraceListItem] | trace 列表（按 created_at 倒序） |
| `total` | int | 总数 |
| `page` | int | 当前页码 |
| `page_size` | int | 每页条数 |

**TraceListItem 结构**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `trace_id` | string | 追踪 ID |
| `session_id` | string (UUID) | 关联会话 ID |
| `kb_id` | string (UUID) | 关联知识库 ID |
| `total_latency_ms` | int | 总延迟（毫秒） |
| `step_count` | int | 步骤数 |
| `created_at` | string (ISO 8601) | 创建时间 |

**示例**：

```bash
curl "http://127.0.0.1:8000/api/v2/traces/sessions/550e8400-e29b-41d4-a716-446655440000/traces?page=1&page_size=10"
```

```json
// 响应
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "trace_id": "trc-xxx-yyy-zzz",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "kb_id": "kb-uuid-aaaa-bbbb-cccc-ddddeeee0001",
        "total_latency_ms": 1842,
        "step_count": 6,
        "created_at": "2026-06-17T10:00:00.100+00:00"
      }
    ],
    "total": 1,
    "page": 1,
    "page_size": 10
  }
}
```

### 3.3 GET /api/v2/analytics

**功能**：聚合统计（OBS-03）。从 `query_analytics` 快照表做 SQL 聚合，返回系统级统计数据。支持按时间范围和知识库过滤。单次 SQL 查询完成所有聚合。

**Query 参数**：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `start_date` | string (date) | 7 天前 | 统计开始日期（格式 `YYYY-MM-DD`） |
| `end_date` | string (date) | 今天 | 统计结束日期（格式 `YYYY-MM-DD`） |
| `kb_id` | string (UUID) | null | 按知识库过滤 |

**响应体** `data` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `total_queries` | int | 查询总数 |
| `avg_latency_ms` | float | 平均延迟（毫秒） |
| `avg_confidence` | float | 平均置信度 [0, 1] |
| `low_confidence_rate` | float | 低置信度查询占比（confidence < 0.5） |
| `tool_usage` | ToolUsageStats | 工具使用率统计 |
| `token_consumption` | TokenConsumptionStats | Token 消耗统计 |
| `avg_react_steps` | float | 平均 ReAct 步骤数 |
| `error_rate` | float | 错误率 |
| `start_date` | string (date) | 统计起始日期 |
| `end_date` | string (date) | 统计结束日期 |

**ToolUsageStats 结构**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `graph_rag_triggered` | float | Graph RAG 被触发的查询占比 [0, 1] |
| `bm25_contributed` | float | BM25 贡献的查询占比 [0, 1] |
| `faithfulness_check_triggered` | float | 答案自检被触发的查询占比 [0, 1] |

**TokenConsumptionStats 结构**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `total_tokens` | int | 总 token 消耗 |

**示例**：

```bash
curl "http://127.0.0.1:8000/api/v2/analytics?start_date=2026-06-10&end_date=2026-06-17"
```

```json
// 响应
{
  "code": 0,
  "message": "success",
  "data": {
    "total_queries": 128,
    "avg_latency_ms": 1520.3,
    "avg_confidence": 0.865,
    "low_confidence_rate": 0.0234,
    "tool_usage": {
      "graph_rag_triggered": 0.35,
      "bm25_contributed": 0.82,
      "faithfulness_check_triggered": 0.12
    },
    "token_consumption": {
      "total_tokens": 185000
    },
    "avg_react_steps": 2.15,
    "error_rate": 0.0078,
    "start_date": "2026-06-10",
    "end_date": "2026-06-17"
  }
}
```

---

## 4. V2 RAGAS 评估

### 4.1 POST /api/v2/knowledge-bases/{kb_id}/evaluate

**功能**：创建 RAGAS 评估任务（EVA-01）。异步执行——立即返回 `eval_task_id`，实际评估由 Celery worker 执行。评估集 QA 对在提交时做参数快照写入 `EvalTask.eval_config`。

**路径参数**：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `kb_id` | string (UUID) | 是 | 知识库 ID |

**请求体**：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `eval_set` | array[EvalQAItem] | 是 | - | 评估集（QA 对列表，至少 1 条，最多 `EVAL_MAX_QUESTIONS` 条，默认上限 100） |
| `retrieval_options` | EvalRetrievalOptions | 否 | `{}` | 评估时的检索参数（不传走 settings 默认） |
| `name` | string | 否 | 自动生成 | 评估任务名称（≤256 字符，便于识别） |

**EvalQAItem 结构**（`eval_set` 数组元素）：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `question` | string | 是 | 评估问题，1~2000 字符 |
| `ground_truth` | string | 是 | 标准答案，1~4000 字符 |

**EvalRetrievalOptions 结构**（`retrieval_options` 对象内）：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `top_k` | int | null（跟随 settings） | 返回结果数量，1~50 |
| `enable_graph_rag` | bool | null（跟随 settings） | 是否启用 Graph RAG 锚定 |
| `reranker_enable` | bool | null（跟随 settings） | 是否启用 Reranker |
| `bm25_enable` | bool | null（跟随 settings） | 是否启用 BM25 |
| `query_rewrite` | string | null（跟随 settings） | Query 改写策略（none / hyde / multi_query） |
| `similarity_threshold` | float | null（跟随 settings） | Reranker 过滤阈值，0.0~1.0 |

**响应体** `data` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `eval_task_id` | string (UUID) | 评估任务 ID（用于 EVA-02/03 查询） |
| `status` | string | 任务初始状态（`pending`） |

**错误码**：

- `40400` kb_id 不存在
- `40012` `eval_set` 为空数组
- `40013` `eval_set` 题数超出 `EVAL_MAX_QUESTIONS` 上限（默认 100）
- `50300` Celery Worker 不可达或 Redis 连接失败（评估任务调度失败）
- `50000` 服务器内部错误

**示例**：

```bash
curl -X POST http://127.0.0.1:8000/api/v2/knowledge-bases/kb-uuid-aaaa-bbbb-cccc-ddddeeee0001/evaluate \
  -H 'Content-Type: application/json' \
  -d '{
    "eval_set": [
      {
        "question": "2024 年台风生成数量是多少？",
        "ground_truth": "2024 年西北太平洋和南海共有 25 个台风生成。"
      },
      {
        "question": "高温预警信号分几个等级？",
        "ground_truth": "高温预警信号分为三级：黄色、橙色、红色。"
      }
    ],
    "retrieval_options": {
      "top_k": 10,
      "reranker_enable": true
    },
    "name": "台风知识库 V2 评估"
  }'
```

```json
// 响应
{
  "code": 0,
  "message": "success",
  "data": {
    "eval_task_id": "eval-uuuu-vvvv-wwww-xxxxyyyyzzzz",
    "status": "pending"
  }
}
```

### 4.2 GET /api/v2/knowledge-bases/{kb_id}/evaluations/{eval_task_id}

**功能**：查询评估进度 + 完成后的指标结果（EVA-02）。包括 RAGAS 4 项核心指标汇总（faithfulness / answer_relevancy / context_precision / context_recall）和每道题的详细评估结果。

**路径参数**：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `kb_id` | string (UUID) | 是 | 知识库 ID |
| `eval_task_id` | string (UUID) | 是 | 评估任务 ID |

**响应体** `data` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `eval_task_id` | string (UUID) | 评估任务 ID |
| `kb_id` | string (UUID) | 知识库 ID |
| `name` | string | 评估任务名称 |
| `status` | string | 任务状态：`pending` / `processing` / `completed` / `failed` |
| `progress` | int | 进度百分比（0~100） |
| `question_count` | int | 评估题数 |
| `summary` | EvalSummary | RAGAS 4 项核心指标汇总（completed 后非 null） |
| `details` | array[EvalDetailItem] | 每道题的详细评估结果（completed 后非 null） |
| `retrieval_options` | object | 评估时使用的检索参数快照（EvalTask.eval_config 中的 retrieval_options） |
| `error_message` | string | 失败原因（status=failed 时有值） |
| `created_at` | string (ISO 8601) | 创建时间 |
| `completed_at` | string (ISO 8601) | 完成时间 |

**EvalSummary 结构**（所有指标范围 [0, 1]；未完成或失败时可为 null）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `faithfulness` | float | 忠实度 |
| `answer_relevancy` | float | 答案相关性 |
| `context_precision` | float | 上下文精确度 |
| `context_recall` | float | 上下文召回率 |
| `overall_score` | float | 四项算术均值 |

**EvalDetailItem 结构**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `question` | string | 评估问题 |
| `ground_truth` | string | 标准答案 |
| `answer` | string | 系统生成的答案 |
| `contexts` | array[string] | 检索到的 chunk 文本列表 |
| `faithfulness` | float | 本题忠实度 |
| `answer_relevancy` | float | 本题答案相关性 |
| `context_precision` | float | 本题上下文精确度 |
| `context_recall` | float | 本题上下文召回率 |
| `error` | string | 单题失败时的简要错误信息 |

**错误码**：

- `40400` kb_id 不存在 / eval_task_id 在该 KB 下不存在

**示例**：

```bash
curl http://127.0.0.1:8000/api/v2/knowledge-bases/kb-uuid-aaaa-bbbb-cccc-ddddeeee0001/evaluations/eval-uuuu-vvvv-wwww-xxxxyyyyzzzz
```

```json
// 响应（completed 状态）
{
  "code": 0,
  "message": "success",
  "data": {
    "eval_task_id": "eval-uuuu-vvvv-wwww-xxxxyyyyzzzz",
    "kb_id": "kb-uuid-aaaa-bbbb-cccc-ddddeeee0001",
    "name": "台风知识库 V2 评估",
    "status": "completed",
    "progress": 100,
    "question_count": 2,
    "summary": {
      "faithfulness": 0.92,
      "answer_relevancy": 0.88,
      "context_precision": 0.85,
      "context_recall": 0.90,
      "overall_score": 0.8875
    },
    "details": [
      {
        "question": "2024 年台风生成数量是多少？",
        "ground_truth": "2024 年西北太平洋和南海共有 25 个台风生成。",
        "answer": "2024 年西北太平洋和南海共有 25 个台风生成。",
        "contexts": ["2024 年西北太平洋和南海共有 25 个台风生成..."],
        "faithfulness": 0.95,
        "answer_relevancy": 0.92,
        "context_precision": 0.88,
        "context_recall": 0.91,
        "error": null
      }
    ],
    "retrieval_options": {
      "top_k": 10,
      "reranker_enable": true
    },
    "error_message": null,
    "created_at": "2026-06-17T10:00:00+00:00",
    "completed_at": "2026-06-17T10:05:30+00:00"
  }
}
```

### 4.3 GET /api/v2/knowledge-bases/{kb_id}/evaluations

**功能**：评估历史列表（EVA-03），按 `created_at` 倒序分页返回，不含每题详情。

**路径参数**：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `kb_id` | string (UUID) | 是 | 知识库 ID |

**Query 参数**：

| 参数 | 类型 | 默认 | 范围 | 说明 |
|---|---|---|---|---|
| `page` | int | 1 | ≥1 | 页码 |
| `page_size` | int | 20 | 1~100 | 每页条数 |

**响应体** `data` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `items` | array[EvalListItem] | 评估历史列表（按 created_at 倒序 + id 倒序双键排序） |
| `total` | int | 总数 |
| `page` | int | 当前页码 |
| `page_size` | int | 每页条数 |

**EvalListItem 结构**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `eval_task_id` | string (UUID) | 评估任务 ID |
| `name` | string | 评估任务名称 |
| `status` | string | 任务状态 |
| `progress` | int | 进度（0~100） |
| `question_count` | int | 评估题数 |
| `summary` | EvalSummary | RAGAS 指标汇总（completed 后有值） |
| `retrieval_options` | object | 检索参数快照 |
| `created_at` | string (ISO 8601) | 创建时间 |
| `completed_at` | string (ISO 8601) | 完成时间 |

**示例**：

```bash
curl "http://127.0.0.1:8000/api/v2/knowledge-bases/kb-uuid-aaaa-bbbb-cccc-ddddeeee0001/evaluations?page=1&page_size=10"
```

```json
// 响应
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "eval_task_id": "eval-uuuu-vvvv-wwww-xxxxyyyyzzzz",
        "name": "台风知识库 V2 评估",
        "status": "completed",
        "progress": 100,
        "question_count": 2,
        "summary": {
          "faithfulness": 0.92,
          "answer_relevancy": 0.88,
          "context_precision": 0.85,
          "context_recall": 0.90,
          "overall_score": 0.8875
        },
        "retrieval_options": {
          "top_k": 10,
          "reranker_enable": true
        },
        "created_at": "2026-06-17T10:00:00+00:00",
        "completed_at": "2026-06-17T10:05:30+00:00"
      }
    ],
    "total": 1,
    "page": 1,
    "page_size": 10
  }
}
```

---

## 5. V1 接口扩展

### 5.1 PATCH /api/v1/knowledge-bases/{kb_id}

**功能**：V1.5 知识库修改接口（KB-04）在 V2.0 新增 `retrieval_config` 字段（HRE-06）。允许在知识库级别设置混合检索默认配置，作为三层配置合并的中间层。

**请求体**（新增字段以 `**` 标记）：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | 否 | null | 新的知识库名称（1~128 字符，全局唯一） |
| `description` | string | 否 | null | 新的知识库描述（≤500 字符，null 表示清空） |
| `retrieval_config` | object | 否 | ** | **V2.0 知识库级检索默认配置；null=不变更，`{}`=清空所有覆盖字段，dict=部分覆盖（service 层 merge）** |

`name` / `description` / `retrieval_config` 至少传一个，否则 422。

**`retrieval_config` 支持的字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `top_k` | int | 默认返回结果数量 |
| `reranker_enable` | bool | 是否启用 Reranker |
| `bm25_enable` | bool | 是否启用 BM25 |
| `query_rewrite` | string | Query 改写策略（none / hyde / multi_query） |
| `enable_graph_rag` | bool | 是否启用 Graph RAG 锚定 |
| `enable_faithfulness_check` | bool | 是否启用答案自检 |
| `similarity_threshold` | float | Reranker 过滤阈值 |
| `rerank_top_n` | int | Reranker 输入候选数 |

> 合并优先级：**API options > KB.retrieval_config > 全局 settings**。KB 层的字段为 None / 缺失时回落 settings，被 API 层传值覆盖时忽略。

**响应体** `data` 字段（V1.5 KB 详情 + 新增 `retrieval_config` 字段）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string (UUID) | KB ID |
| `name` | string | 名称 |
| `description` | string | 描述 |
| `embedding_dim` | int | 向量维度 |
| `chunk_size` | int | 切片大小 |
| `chunk_overlap` | int | 切片重叠 |
| `status` | string | 状态（active / building / error） |
| `file_count` | int | 关联文件数 |
| `chunk_count` | int | 向量切片数 |
| `entity_count` | int | Neo4j 实体数 |
| `retrieval_config` | object | **V2.0 知识库级检索默认配置** |
| `created_at` | string (ISO 8601) | 创建时间 |

**错误码**（同 V1.5 KB-04）：

- `40001` 三个字段都不传 / name 空白 / 超长 / 传入只读字段（embedding_dim 等）
- `40400` kb_id 不存在
- `40900` 新 name 与其它 KB 冲突

**示例**：

```bash
curl -X PATCH http://127.0.0.1:8000/api/v1/knowledge-bases/kb-uuid-aaaa-bbbb-cccc-ddddeeee0001 \
  -H 'Content-Type: application/json' \
  -d '{
    "retrieval_config": {
      "top_k": 10,
      "reranker_enable": true,
      "bm25_enable": true,
      "query_rewrite": "none",
      "similarity_threshold": 0.3
    }
  }'
```

```json
// 响应
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "kb-uuid-aaaa-bbbb-cccc-ddddeeee0001",
    "name": "气象库",
    "description": "台风知识库",
    "embedding_dim": 4096,
    "chunk_size": 512,
    "chunk_overlap": 64,
    "status": "active",
    "file_count": 5,
    "chunk_count": 1200,
    "entity_count": 0,
    "retrieval_config": {
      "top_k": 10,
      "reranker_enable": true,
      "bm25_enable": true,
      "query_rewrite": "none",
      "similarity_threshold": 0.3
    },
    "created_at": "2026-06-11T12:00:00+00:00"
  }
}
```

---

## 附录

### A.1 三层配置合并（HRE-06）

V2.0 的检索行为通过三层配置结构控制，优先级从高到低：

```
API options（QueryOptions） > KB.retrieval_config（JSONB） > 全局 settings
```

**合并规则**：

1. **API options**（`/v2/query` 请求体中的 `options` 字段）：最高优先级。任一字段为 `None` 表示"未指定，跟随下层"。
2. **KB.retrieval_config**（通过 §5.1 PATCH 接口设置）：中间层。字段缺失或为 `None` 时回落 settings。传 `{}` 可清空所有覆盖。
3. **全局 settings**（`app.core.config.Settings`）：最底层兜底。

**最终生效的 ResolvedRetrievalOptions**（下游模块只读此结构）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `top_k` | int | 返回结果数量（默认 5） |
| `similarity_threshold` | float | Reranker 过滤阈值（默认 0.3） |
| `bm25_enable` | bool | BM25 开关 |
| `reranker_enable` | bool | Reranker 开关 |
| `query_rewrite` | string | Query 改写策略（none / hyde / multi_query） |
| `enable_graph_rag` | bool | Graph RAG 锚定开关 |
| `enable_faithfulness_check` | bool | 答案自检开关（CHC-04） |
| `rrf_k` | int | RRF 融合常数（来自 settings） |
| `rerank_top_n` | int | Reranker 输入候选数（默认 30） |

### A.2 trace_id 与 session_id 的关系

- **session_id**：会话生命周期标识，一次对话（多轮消息）共享同一 session_id。
- **trace_id**：单次请求追踪标识。每次 `/v2/query` 调用产生一个唯一 trace_id，记录该请求的完整步骤链路（query_rewrite → NER → anchor → retrieve → generate → citation → faithfulness）。
- 关系：**1 个 session_id 对应 N 个 trace_id**（会话的每轮查询各有一条 trace）。
- 通过 `GET /api/v2/traces/sessions/{session_id}/traces` 获取某会话的所有 trace。

### A.3 SSE 流式输出（V2 暂未实现）

V2.0 当前 `/v2/query` 仅支持非流式（同步返回完整 JSON）。SSE 流式输出在 PRD §3.4 中有描述但 T6/T8 阶段尚未实现。

前端流式对话体验暂时复用 V1.5 的 [`POST /api/v1/chat/stream`](v1_5_api_reference.md#21-流式对话-post-apiv1chatstream) 接口。

### A.4 在线交互文档

服务启动后访问：
- [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) — Swagger UI（可在浏览器直接试调用）
- [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc) — ReDoc 静态文档
- [http://127.0.0.1:8000/openapi.json](http://127.0.0.1:8000/openapi.json) — OpenAPI 3.x 规范

---

*TyAgent V2.0 API · End of Document*

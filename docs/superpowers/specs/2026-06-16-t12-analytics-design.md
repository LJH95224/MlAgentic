# T12 · OBS-03 聚合统计接口设计

**目标：** 实现 `GET /api/v2/analytics`，返回系统级聚合统计（查询量、延迟、置信度、工具使用率、Token 消耗、错误率），支持时间范围和知识库过滤。

**方案：** 新增 `query_analytics` 快照表，每次 `/v2/query` 调用结束时同步写一行汇总；analytics 端点直接对该表做 SQL 聚合，响应时间 < 500ms。

## 数据模型

### 新增 `query_analytics` 表

```python
class QueryAnalytics(Base):
    __tablename__ = "query_analytics"

    id: UUID PK
    trace_id: String(64), index          # 关联的 trace_id
    session_id: UUID, nullable, index    # 关联会话
    kb_id: UUID, nullable, index        # 关联知识库

    # 延迟
    total_latency_ms: BigInteger         # 总耗时

    # 置信度
    confidence: Float, nullable          # CHC-03 置信度
    low_confidence: Boolean, default=False  # confidence < 0.5

    # 工具使用（bool 标记，聚合时 AVG 即为触发率）
    graph_rag_triggered: Boolean, default=False
    bm25_contributed: Boolean, default=False
    faithfulness_check_triggered: Boolean, default=False

    # Token 消耗
    total_input_tokens: Integer, default=0
    total_output_tokens: Integer, default=0

    # ReAct 步骤数
    react_steps: Integer, default=0

    # 错误
    has_error: Boolean, default=False    # 任一步骤有 error_message

    created_at: DateTime, server_default=now(), index
```

**设计要点：**
- 工具使用率用 bool + AVG 计算：`AVG(graph_rag_triggered)` = 触发率，无需存 JSONB
- `low_confidence` 冗余存储 bool：避免聚合时每行做浮点比较
- Token 数据从 Trace 的 `token_count` 字段汇总（generate 步骤的 token_count）
- `react_steps` = 该 trace 的步骤数（COUNT step_type）

## 写入时机

在 `app/api/v2/endpoints/query.py` 的 `_v2_query_inner` 末尾，Tracer 上下文退出之前，同步写一行 `QueryAnalytics`：

```python
# 在 Tracer.__aexit__ 之前插入
await _write_analytics_snapshot(
    db=db,
    trace_id=tracer.trace_id,
    session_id=body.session_id,
    kb_id=body.kb_ids[0] if body.kb_ids else None,
    total_latency_ms=total_latency_ms,
    confidence=score.confidence,
    faithfulness_check=resolved.enable_faithfulness_check,
    has_error=any(s.error_message for s in tracer.steps),
    tracer=tracer,
)
```

从 Tracer 的 steps 列表提取：
- `graph_rag_triggered`: 有 step_type="graph_anchor" 且 step_output.tag_count > 0
- `bm25_contributed`: 有 step_type="retrieve" 且 step_input 中 bm25 相关
- `faithfulness_check_triggered`: 有 step_type="faithfulness_check"
- `total_input_tokens / total_output_tokens`: 所有步骤 token_count 之和（简化处理，不区分 input/output）
- `react_steps`: len(tracer.steps)
- `has_error`: any(step.error_message for step in tracer.steps)

**检索为空兜底分支**也需要写快照（total_queries 应该包含空检索的查询）。

## 聚合端点

### `GET /api/v2/analytics`

**查询参数：**
| 参数 | 类型 | 必选 | 说明 |
|------|------|------|------|
| start_date | date | 否 | 统计开始日期（默认 7 天前） |
| end_date | date | 否 | 统计结束日期（默认今天） |
| kb_id | UUID | 否 | 按知识库过滤 |

**响应体：**
```json
{
  "total_queries": 1520,
  "avg_latency_ms": 2840,
  "avg_confidence": 0.78,
  "low_confidence_rate": 0.12,
  "tool_usage": {
    "graph_rag_triggered": 0.65,
    "bm25_contributed": 0.43,
    "faithfulness_check_triggered": 0.28
  },
  "token_consumption": {
    "total_input": 4850000,
    "total_output": 0
  },
  "avg_react_steps": 3.2,
  "error_rate": 0.02,
  "start_date": "2026-06-09",
  "end_date": "2026-06-16"
}
```

**SQL 聚合（单次查询）：**
```sql
SELECT
  COUNT(*) AS total_queries,
  AVG(total_latency_ms) AS avg_latency_ms,
  AVG(confidence) AS avg_confidence,
  AVG(CASE WHEN low_confidence THEN 1.0 ELSE 0.0 END) AS low_confidence_rate,
  AVG(CASE WHEN graph_rag_triggered THEN 1.0 ELSE 0.0 END) AS graph_rag_triggered_rate,
  AVG(CASE WHEN bm25_contributed THEN 1.0 ELSE 0.0 END) AS bm25_contributed_rate,
  AVG(CASE WHEN faithfulness_check_triggered THEN 1.0 ELSE 0.0 END) AS faithfulness_check_rate,
  SUM(total_input_tokens) AS total_input_tokens,
  SUM(total_output_tokens) AS total_output_tokens,
  AVG(react_steps) AS avg_react_steps,
  AVG(CASE WHEN has_error THEN 1.0 ELSE 0.0 END) AS error_rate
FROM query_analytics
WHERE created_at >= :start_date AND created_at < :end_date_plus_one
  AND (:kb_id IS NULL OR kb_id = :kb_id)
```

单次 SQL 查询完成所有聚合，性能 < 100ms（万级行量）。

## 文件结构

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `app/models/query_analytics.py` | QueryAnalytics ORM 模型 |
| 新建 | `app/schemas/v2/analytics.py` | Analytics 响应 Schema |
| 新建 | `app/api/v2/endpoints/analytics.py` | GET /api/v2/analytics 端点 |
| 新建 | `app/observability/analytics_writer.py` | 快照写入辅助函数 |
| 修改 | `app/models/__init__.py` | 注册 QueryAnalytics |
| 修改 | `app/api/v2/endpoints/query.py` | 末尾调用快照写入 |
| 修改 | `app/api/v2/router.py` | 挂载 analytics 路由 |
| 新建 | `tests/test_v2_t12.py` | T12 单测 |

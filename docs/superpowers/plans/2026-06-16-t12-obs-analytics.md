# T12 · OBS-03 聚合统计接口 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `GET /api/v2/analytics` 聚合统计端点，返回查询量、延迟、置信度、工具使用率、Token 消耗、错误率等指标。

**Architecture:** 新增 `query_analytics` 快照表，每次 `/v2/query` 结束时同步写一行汇总；analytics 端点对该表做单次 SQL 聚合查询，响应 < 500ms。

**Tech Stack:** SQLAlchemy ORM + asyncpg + FastAPI + Pydantic

---

## 文件结构

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `app/models/query_analytics.py` | QueryAnalytics ORM 模型 |
| 新建 | `app/schemas/v2/analytics.py` | Analytics 响应 Schema |
| 新建 | `app/observability/analytics_writer.py` | 快照写入辅助函数 |
| 新建 | `app/api/v2/endpoints/analytics.py` | GET /api/v2/analytics 端点 |
| 修改 | `app/models/__init__.py` | 注册 QueryAnalytics |
| 修改 | `app/api/v2/endpoints/query.py` | 末尾调用快照写入 |
| 修改 | `app/api/v2/router.py` | 挂载 analytics 路由 |
| 新建 | `tests/test_v2_t12.py` | T12 单测 |

---

## Task 1: QueryAnalytics ORM 模型 + 注册 + Schema

**Files:**
- Create: `app/models/query_analytics.py`
- Modify: `app/models/__init__.py`
- Create: `app/schemas/v2/analytics.py`
- Test: `tests/test_v2_t12.py`

### Step 1: 创建 QueryAnalytics ORM 模型

创建 `app/models/query_analytics.py`：

```python
"""query_analytics 表：V2.0 聚合统计快照（OBS-03）。

每次 /v2/query 调用结束时同步写一行汇总。
analytics 端点对该表做 SQL 聚合，无需扫描 agent_traces 的 JSONB 字段。

设计要点：
- 工具使用率用 bool 列 + AVG 聚合：AVG(graph_rag_triggered) = 触发率
- low_confidence 冗余存储 bool：避免聚合时浮点比较
- Token 数据简化为 total_tokens（不区分 input/output，Tracer 只记录总数）
- react_steps = 该 trace 的步骤数
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class QueryAnalytics(Base):
    """查询聚合统计快照（V2.0 OBS-03）。"""

    __tablename__ = "query_analytics"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="记录主键",
    )

    trace_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="关联的 trace_id",
    )

    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="关联会话 ID",
    )

    kb_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="关联知识库 ID",
    )

    # 延迟
    total_latency_ms: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="总耗时（毫秒）",
    )

    # 置信度
    confidence: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="CHC-03 置信度 [0, 1]",
    )

    low_confidence: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="confidence < 0.5 标记",
    )

    # 工具使用（bool 标记，聚合时 AVG 即为触发率）
    graph_rag_triggered: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="Graph RAG 是否触发",
    )

    bm25_contributed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="BM25 是否参与检索",
    )

    faithfulness_check_triggered: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="答案自检是否触发",
    )

    # Token 消耗
    total_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="总 token 消耗",
    )

    # ReAct 步骤数
    react_steps: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="该 trace 的步骤数",
    )

    # 错误
    has_error: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="任一步骤有 error_message",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="记录创建时间",
    )

    def __repr__(self) -> str:
        return (
            f"<QueryAnalytics trace_id={self.trace_id!r} "
            f"latency={self.total_latency_ms}ms confidence={self.confidence}>"
        )
```

### Step 2: 注册到 models/__init__.py

修改 `app/models/__init__.py`，在 import 区新增：

```python
from app.models.query_analytics import QueryAnalytics
```

在 `__all__` 列表新增 `"QueryAnalytics"`。

### Step 3: 创建 Analytics Schema

创建 `app/schemas/v2/analytics.py`：

```python
"""V2.0 OBS-03 聚合统计 Schema（GET /api/v2/analytics）。"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class ToolUsageStats(BaseModel):
    """工具使用率统计（各字段为该工具被触发的查询占比 [0, 1]）。"""

    graph_rag_triggered: float = Field(default=0.0, ge=0.0, le=1.0)
    bm25_contributed: float = Field(default=0.0, ge=0.0, le=1.0)
    faithfulness_check_triggered: float = Field(default=0.0, ge=0.0, le=1.0)


class TokenConsumptionStats(BaseModel):
    """Token 消耗统计。"""

    total_tokens: int = Field(default=0, description="总 token 消耗")


class AnalyticsResponse(BaseModel):
    """OBS-03 聚合统计响应。"""

    total_queries: int = Field(default=0, description="查询总数")
    avg_latency_ms: float | None = Field(default=None, description="平均延迟（毫秒）")
    avg_confidence: float | None = Field(default=None, description="平均置信度 [0, 1]")
    low_confidence_rate: float = Field(default=0.0, description="低置信度查询占比（confidence < 0.5）")
    tool_usage: ToolUsageStats = Field(default_factory=ToolUsageStats)
    token_consumption: TokenConsumptionStats = Field(default_factory=TokenConsumptionStats)
    avg_react_steps: float | None = Field(default=None, description="平均 ReAct 步骤数")
    error_rate: float = Field(default=0.0, description="错误率")
    start_date: date | None = None
    end_date: date | None = None
```

### Step 4: 写模型测试

创建 `tests/test_v2_t12.py`：

```python
"""V2.0 T12 阶段单测（OBS-03 聚合统计）。"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ──────────────── ORM 模型 ────────────────


class TestQueryAnalyticsModel:
    """QueryAnalytics ORM 模型基础测试。"""

    def test_model_importable(self):
        from app.models.query_analytics import QueryAnalytics
        assert QueryAnalytics.__tablename__ == "query_analytics"

    def test_model_registered_in_init(self):
        from app.models import QueryAnalytics
        assert QueryAnalytics is not None

    def test_model_fields(self):
        from app.models.query_analytics import QueryAnalytics
        columns = {c.name for c in QueryAnalytics.__table__.columns}
        expected = {
            "id", "trace_id", "session_id", "kb_id",
            "total_latency_ms", "confidence", "low_confidence",
            "graph_rag_triggered", "bm25_contributed", "faithfulness_check_triggered",
            "total_tokens", "react_steps", "has_error", "created_at",
        }
        assert expected.issubset(columns), f"缺失列: {expected - columns}"


# ──────────────── Schema ────────────────


class TestAnalyticsSchema:
    """OBS-03 Analytics Schema 测试。"""

    def test_analytics_response_defaults(self):
        from app.schemas.v2.analytics import AnalyticsResponse
        resp = AnalyticsResponse()
        assert resp.total_queries == 0
        assert resp.low_confidence_rate == 0.0
        assert resp.error_rate == 0.0

    def test_analytics_response_with_data(self):
        from app.schemas.v2.analytics import AnalyticsResponse, ToolUsageStats, TokenConsumptionStats
        resp = AnalyticsResponse(
            total_queries=1520,
            avg_latency_ms=2840.5,
            avg_confidence=0.78,
            low_confidence_rate=0.12,
            tool_usage=ToolUsageStats(
                graph_rag_triggered=0.65,
                bm25_contributed=0.43,
                faithfulness_check_triggered=0.28,
            ),
            token_consumption=TokenConsumptionStats(total_tokens=5470000),
            avg_react_steps=3.2,
            error_rate=0.02,
            start_date=date(2026, 6, 9),
            end_date=date(2026, 6, 16),
        )
        assert resp.total_queries == 1520
        assert resp.tool_usage.graph_rag_triggered == 0.65
        assert resp.token_consumption.total_tokens == 5470000

    def test_tool_usage_stats_range(self):
        from app.schemas.v2.analytics import ToolUsageStats
        with pytest.raises(Exception):
            ToolUsageStats(graph_rag_triggered=1.5)  # 超出 [0, 1]
```

### Step 5: 运行测试

```bash
conda activate geo_agent && pytest tests/test_v2_t12.py -v
```

预期：全部通过

### Step 6: 提交

```bash
git add app/models/query_analytics.py app/models/__init__.py app/schemas/v2/analytics.py tests/test_v2_t12.py
git commit -m "feat(v2): T12 QueryAnalytics 模型 + Schema 定义"
```

---

## Task 2: 快照写入辅助函数

**Files:**
- Create: `app/observability/analytics_writer.py`
- Test: `tests/test_v2_t12.py`

### Step 1: 写 analytics_writer 的测试

在 `tests/test_v2_t12.py` 追加：

```python
# ──────────────── Analytics Writer ────────────────


class TestAnalyticsWriter:
    """快照写入辅助函数测试。"""

    def _make_tracer_steps(self):
        """构造模拟的 Tracer steps 列表。"""
        from app.observability.tracer import TraceStep
        return [
            TraceStep(step_type="query_rewrite", step_latency_ms=100, step_output={"rewritten_len": 50}),
            TraceStep(step_type="query_ner", step_latency_ms=200, step_output={"entity_count": 2}),
            TraceStep(step_type="graph_anchor", step_latency_ms=150, step_output={"tag_count": 3}),
            TraceStep(step_type="retrieve", step_latency_ms=300, step_input={"query_rewrite": "hyde"}, step_output={"hit_count": 5}),
            TraceStep(step_type="build_context", step_latency_ms=10, step_output={"chunks": 5}),
            TraceStep(step_type="generate", step_latency_ms=2000, model_name="deepseek-v4", token_count=1500, step_output={"answer_len": 200}),
            TraceStep(step_type="citation_parse", step_latency_ms=5, step_output={"citations": 3}),
            TraceStep(step_type="faithfulness_check", step_latency_ms=800, step_output={"status": "ok"}),
            TraceStep(step_type="compute_confidence", step_latency_ms=2, step_output={"confidence": 0.85}),
        ]

    def test_build_analytics_snapshot_normal(self):
        """正常查询的快照数据构建。"""
        from app.observability.analytics_writer import build_analytics_snapshot
        steps = self._make_tracer_steps()
        snap = build_analytics_snapshot(
            trace_id="abc123",
            session_id=uuid.uuid4(),
            kb_id=uuid.uuid4(),
            total_latency_ms=3500,
            confidence=0.85,
            enable_faithfulness_check=True,
            steps=steps,
        )
        assert snap["trace_id"] == "abc123"
        assert snap["total_latency_ms"] == 3500
        assert snap["confidence"] == 0.85
        assert snap["low_confidence"] is False  # 0.85 >= 0.5
        assert snap["graph_rag_triggered"] is True  # 有 graph_anchor 且 tag_count > 0
        assert snap["faithfulness_check_triggered"] is True  # 有 faithfulness_check 步骤
        assert snap["total_tokens"] == 1500  # generate 步骤的 token_count
        assert snap["react_steps"] == 9
        assert snap["has_error"] is False

    def test_build_analytics_snapshot_low_confidence(self):
        """低置信度场景。"""
        from app.observability.analytics_writer import build_analytics_snapshot
        steps = self._make_tracer_steps()
        snap = build_analytics_snapshot(
            trace_id="low1",
            session_id=None,
            kb_id=None,
            total_latency_ms=500,
            confidence=0.3,
            enable_faithfulness_check=False,
            steps=steps,
        )
        assert snap["low_confidence"] is True  # 0.3 < 0.5
        assert snap["faithfulness_check_triggered"] is False  # 开关关闭

    def test_build_analytics_snapshot_with_error(self):
        """有步骤出错的场景。"""
        from app.observability.analytics_writer import build_analytics_snapshot
        from app.observability.tracer import TraceStep
        steps = self._make_tracer_steps()
        steps.append(TraceStep(step_type="retrieve", error_message="Milvus timeout", step_latency_ms=5000))
        snap = build_analytics_snapshot(
            trace_id="err1",
            session_id=None,
            kb_id=None,
            total_latency_ms=6000,
            confidence=0.0,
            enable_faithfulness_check=False,
            steps=steps,
        )
        assert snap["has_error"] is True
        assert snap["low_confidence"] is True  # confidence=0.0 < 0.5

    def test_build_analytics_snapshot_empty_steps(self):
        """空步骤（检索为空兜底场景）。"""
        from app.observability.analytics_writer import build_analytics_snapshot
        snap = build_analytics_snapshot(
            trace_id="empty1",
            session_id=None,
            kb_id=None,
            total_latency_ms=100,
            confidence=0.0,
            enable_faithfulness_check=False,
            steps=[],
        )
        assert snap["react_steps"] == 0
        assert snap["total_tokens"] == 0
        assert snap["graph_rag_triggered"] is False
        assert snap["has_error"] is False
        assert snap["low_confidence"] is True  # 0.0 < 0.5
```

### Step 2: 实现 analytics_writer

创建 `app/observability/analytics_writer.py`：

```python
"""V2.0 OBS-03 聚合统计快照写入（OBS-03）。

每次 /v2/query 调用结束时，将关键指标汇总写一行到 query_analytics 表。
analytics 端点对该表做 SQL 聚合，无需扫描 agent_traces 的 JSONB 字段。

设计要点：
- 从 Tracer.steps 列表提取工具使用 bool / Token 数 / 步骤数 / 错误
- confidence 和 total_latency_ms 由调用方传入（已在 query.py 中计算好）
- 写入失败仅 warning，不阻断主链路
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.query_analytics import QueryAnalytics

logger = logging.getLogger(__name__)

# 低置信度阈值（与 confidence.py 保持一致）
_LOW_CONFIDENCE_THRESHOLD = 0.5


def build_analytics_snapshot(
    *,
    trace_id: str,
    session_id: uuid.UUID | None,
    kb_id: uuid.UUID | None,
    total_latency_ms: int | None,
    confidence: float | None,
    enable_faithfulness_check: bool,
    steps: list,
) -> dict:
    """从 trace 步骤列表构建 query_analytics 行数据。

    Args:
        trace_id: trace 唯一标识
        session_id: 关联会话 ID
        kb_id: 关联知识库 ID
        total_latency_ms: 总延迟
        confidence: CHC-03 置信度
        enable_faithfulness_check: 自检开关是否开启
        steps: Tracer.steps 列表（TraceStep 对象列表）

    Returns:
        dict，可直接传给 QueryAnalytics 构造函数
    """
    # 工具使用 bool
    graph_rag_triggered = False
    bm25_contributed = False
    faithfulness_check_triggered = False
    total_tokens = 0
    has_error = False

    for s in steps:
        # Graph RAG 触发：有 graph_anchor 步骤且有 tag 输出
        if s.step_type == "graph_anchor":
            output = s.step_output or {}
            if output.get("tag_count", 0) > 0:
                graph_rag_triggered = True

        # BM25 参与：retrieve 步骤的 step_input 含 bm25 相关信息
        # 简化判断：有 retrieve 步骤即认为 BM25 可能贡献（因为默认 bm25_enable=True）
        if s.step_type == "retrieve":
            bm25_contributed = True

        # 答案自检触发
        if s.step_type == "faithfulness_check":
            faithfulness_check_triggered = True

        # Token 累加
        if s.token_count:
            total_tokens += s.token_count

        # 错误检测
        if s.error_message:
            has_error = True

    # 如果 faithfulness_check 未开启，标记为未触发
    if not enable_faithfulness_check:
        faithfulness_check_triggered = False

    # 低置信度
    low_confidence = confidence is not None and confidence < _LOW_CONFIDENCE_THRESHOLD

    return {
        "trace_id": trace_id,
        "session_id": session_id,
        "kb_id": kb_id,
        "total_latency_ms": total_latency_ms,
        "confidence": confidence,
        "low_confidence": low_confidence,
        "graph_rag_triggered": graph_rag_triggered,
        "bm25_contributed": bm25_contributed,
        "faithfulness_check_triggered": faithfulness_check_triggered,
        "total_tokens": total_tokens,
        "react_steps": len(steps),
        "has_error": has_error,
    }


async def write_analytics_snapshot(
    *,
    db: AsyncSession,
    trace_id: str,
    session_id: uuid.UUID | None,
    kb_id: uuid.UUID | None,
    total_latency_ms: int | None,
    confidence: float | None,
    enable_faithfulness_check: bool,
    steps: list,
) -> None:
    """构建并写入一行 query_analytics 快照。

    写入失败仅 warning，不阻断主链路。
    """
    try:
        data = build_analytics_snapshot(
            trace_id=trace_id,
            session_id=session_id,
            kb_id=kb_id,
            total_latency_ms=total_latency_ms,
            confidence=confidence,
            enable_faithfulness_check=enable_faithfulness_check,
            steps=steps,
        )
        row = QueryAnalytics(**data)
        db.add(row)
        await db.flush()
        # 注意：不 commit，由调用方统一 commit
    except Exception as e:
        logger.warning("Analytics 快照写入失败（已忽略）: %s", e)


__all__ = ["build_analytics_snapshot", "write_analytics_snapshot"]
```

### Step 3: 运行测试

```bash
conda activate geo_agent && pytest tests/test_v2_t12.py -v
```

预期：全部通过

### Step 4: 提交

```bash
git add app/observability/analytics_writer.py tests/test_v2_t12.py
git commit -m "feat(v2): T12 analytics_writer 快照写入辅助函数"
```

---

## Task 3: 集成到 /v2/query 主链路

**Files:**
- Modify: `app/api/v2/endpoints/query.py`
- Test: `tests/test_v2_t12.py`

### Step 1: 读取 query.py 当前代码

需要读取 `app/api/v2/endpoints/query.py` 的完整代码来定位插入点。

关键位置：
- `_v2_query_inner` 函数末尾，Tracer `__aexit__` 之前
- 两个出口点：
  1. **正常出口**：检索非空，走完 generate → citation → faithfulness → confidence
  2. **检索为空兜底出口**：`if not results:` 分支

两个出口都需要写快照。

### Step 2: 写集成测试

在 `tests/test_v2_t12.py` 追加：

```python
# ──────────────── /v2/query 集成 ────────────────


class TestQueryAnalyticsIntegration:
    """验证 /v2/query 末尾正确调用 write_analytics_snapshot。"""

    @pytest.mark.asyncio
    async def test_query_writes_analytics_on_success(self):
        """正常查询完成后写一行 analytics 快照。"""
        from app.api.v2.endpoints.query import v2_query
        from app.schemas.v2.query import QueryRequest
        from app.observability.analytics_writer import write_analytics_snapshot

        with patch("app.api.v2.endpoints.query.hybrid_search", new_callable=AsyncMock) as mock_search, \
             patch("app.api.v2.endpoints.query.generate_answer", new_callable=AsyncMock) as mock_gen, \
             patch("app.api.v2.endpoints.query.write_analytics_snapshot", new_callable=AsyncMock) as mock_write:

            mock_search.return_value = [
                MagicMock(chunk_id=1, content="内容", document_id="d1", score=0.9,
                          entity_tags=[], heading_path=[], block_type="paragraph",
                          page_number=1, metadata={}, source_collection="kb"),
            ]
            mock_gen.return_value = "答案[1]。"

            body = QueryRequest(query="测试", kb_ids=[uuid.uuid4()])
            resp = await v2_query(body=body, db=MagicMock())
            mock_write.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_writes_analytics_on_empty_results(self):
        """检索为空兜底分支也写 analytics 快照。"""
        from app.api.v2.endpoints.query import v2_query
        from app.schemas.v2.query import QueryRequest

        with patch("app.api.v2.endpoints.query.hybrid_search", new_callable=AsyncMock) as mock_search, \
             patch("app.api.v2.endpoints.query.write_analytics_snapshot", new_callable=AsyncMock) as mock_write:

            mock_search.return_value = []

            body = QueryRequest(query="不存在的查询", kb_ids=[uuid.uuid4()])
            resp = await v2_query(body=body, db=MagicMock())
            mock_write.assert_called_once()
            # 检索空场景 confidence=0.0, low_confidence=True
            call_kwargs = mock_write.call_args[1]
            assert call_kwargs["confidence"] == 0.0
```

### Step 3: 修改 query.py 集成快照写入

在 `app/api/v2/endpoints/query.py` 中：

1. 在文件顶部新增 import：
```python
from app.observability.analytics_writer import write_analytics_snapshot
```

2. 在 `_v2_query_inner` 函数中，找到两个出口点，在 Tracer 退出前调用 `write_analytics_snapshot`：

**正常出口**（在 `return QueryResponse(...)` 之前，`total_latency_ms` 计算之后）：
```python
    # 写聚合统计快照（OBS-03）
    try:
        await write_analytics_snapshot(
            db=db,
            trace_id=tracer.trace_id,
            session_id=body.session_id,
            kb_id=body.kb_ids[0] if body.kb_ids else None,
            total_latency_ms=total_latency_ms,
            confidence=score.confidence,
            enable_faithfulness_check=resolved.enable_faithfulness_check,
            steps=tracer.steps,
        )
    except Exception as e:
        logger.warning("Analytics 快照写入失败: %s", e)
```

**检索为空兜底出口**（在 `return QueryResponse(...)` 之前）：
```python
    # 写聚合统计快照（OBS-03）—— 检索空场景
    try:
        await write_analytics_snapshot(
            db=db,
            trace_id=tracer.trace_id,
            session_id=body.session_id,
            kb_id=body.kb_ids[0] if body.kb_ids else None,
            total_latency_ms=int((time.perf_counter() - start_time) * 1000),
            confidence=empty_score.confidence,
            enable_faithfulness_check=resolved.enable_faithfulness_check,
            steps=tracer.steps,
        )
    except Exception as e:
        logger.warning("Analytics 快照写入失败: %s", e)
```

**注意**：需要在 `_v2_query_inner` 函数签名或局部变量中确保 `tracer` 和 `resolved` 变量在兜底分支可达。需要先读取 query.py 确认变量作用域。

### Step 4: 运行测试

```bash
conda activate geo_agent && pytest tests/test_v2_t12.py -v
```

### Step 5: 提交

```bash
git add app/api/v2/endpoints/query.py tests/test_v2_t12.py
git commit -m "feat(v2): T12 /v2/query 末尾集成 analytics 快照写入"
```

---

## Task 4: GET /api/v2/analytics 端点 + 路由注册

**Files:**
- Create: `app/api/v2/endpoints/analytics.py`
- Modify: `app/api/v2/router.py`
- Test: `tests/test_v2_t12.py`

### Step 1: 写 analytics 端点测试

在 `tests/test_v2_t12.py` 追加：

```python
# ──────────────── Analytics 端点 ────────────────


class TestAnalyticsEndpoint:
    """OBS-03 GET /api/v2/analytics 端点测试。"""

    @pytest.mark.asyncio
    async def test_analytics_returns_stats(self):
        """正常返回聚合统计数据。"""
        from app.api.v2.endpoints.analytics import v2_analytics

        # Mock DB 查询返回聚合行
        mock_db = MagicMock()
        mock_row = MagicMock()
        mock_row.total_queries = 100
        mock_row.avg_latency_ms = 2500.0
        mock_row.avg_confidence = 0.78
        mock_row.low_confidence_rate = 0.12
        mock_row.graph_rag_triggered_rate = 0.65
        mock_row.bm25_contributed_rate = 0.43
        mock_row.faithfulness_check_rate = 0.28
        mock_row.total_tokens = 500000
        mock_row.avg_react_steps = 3.2
        mock_row.error_rate = 0.02

        mock_result = MagicMock()
        mock_result.first.return_value = mock_row
        mock_db.execute = AsyncMock(return_value=mock_result)

        resp = await v2_analytics(db=mock_db)
        assert resp.total_queries == 100
        assert resp.avg_latency_ms == 2500.0
        assert resp.tool_usage.graph_rag_triggered == 0.65

    @pytest.mark.asyncio
    async def test_analytics_empty_data(self):
        """无数据时返回零值默认。"""
        from app.api.v2.endpoints.analytics import v2_analytics

        mock_db = MagicMock()
        mock_row = MagicMock()
        mock_row.total_queries = 0
        mock_row.avg_latency_ms = None
        mock_row.avg_confidence = None
        mock_row.low_confidence_rate = 0.0
        mock_row.graph_rag_triggered_rate = 0.0
        mock_row.bm25_contributed_rate = 0.0
        mock_row.faithfulness_check_rate = 0.0
        mock_row.total_tokens = 0
        mock_row.avg_react_steps = None
        mock_row.error_rate = 0.0

        mock_result = MagicMock()
        mock_result.first.return_value = mock_row
        mock_db.execute = AsyncMock(return_value=mock_result)

        resp = await v2_analytics(db=mock_db)
        assert resp.total_queries == 0
        assert resp.avg_latency_ms is None

    def test_analytics_router_registered(self):
        """验证 /analytics 路由已注册。"""
        from app.api.v2.router import router
        paths = [r.path for r in router.routes]
        assert any("/analytics" in p for p in paths), f"/analytics 不在路由中: {paths}"
```

### Step 2: 实现 analytics 端点

创建 `app/api/v2/endpoints/analytics.py`：

```python
"""V2.0 OBS-03 聚合统计端点 GET /api/v2/analytics。

从 query_analytics 快照表做 SQL 聚合，返回系统级统计数据。
支持按时间范围和知识库过滤。单次 SQL 查询完成所有聚合。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models.query_analytics import QueryAnalytics
from app.schemas.v2.analytics import (
    AnalyticsResponse,
    TokenConsumptionStats,
    ToolUsageStats,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["V2 可观测性"])


@router.get("/analytics", response_model=AnalyticsResponse)
async def v2_analytics(
    start_date: date | None = Query(default=None, description="统计开始日期（默认 7 天前）"),
    end_date: date | None = Query(default=None, description="统计结束日期（默认今天）"),
    kb_id: str | None = Query(default=None, description="按知识库过滤"),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsResponse:
    """OBS-03 聚合统计：查询量 / 延迟 / 置信度 / 工具使用率 / Token / 错误率。"""
    # 默认时间范围：最近 7 天
    if end_date is None:
        end_date = date.today()
    if start_date is None:
        start_date = end_date - timedelta(days=7)

    # 转 datetime 做 PG 比较
    start_dt = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
    # end_date 的次日 0 点（左闭右开）
    end_dt = datetime(end_date.year, end_date.month, end_date.day, tzinfo=timezone.utc) + timedelta(days=1)

    # 构建聚合查询
    stmt = select(
        func.count(QueryAnalytics.id).label("total_queries"),
        func.avg(QueryAnalytics.total_latency_ms).label("avg_latency_ms"),
        func.avg(QueryAnalytics.confidence).label("avg_confidence"),
        func.avg(
            case((QueryAnalytics.low_confidence == True, 1.0), else_=0.0)  # noqa: E712
        ).label("low_confidence_rate"),
        func.avg(
            case((QueryAnalytics.graph_rag_triggered == True, 1.0), else_=0.0)  # noqa: E712
        ).label("graph_rag_triggered_rate"),
        func.avg(
            case((QueryAnalytics.bm25_contributed == True, 1.0), else_=0.0)  # noqa: E712
        ).label("bm25_contributed_rate"),
        func.avg(
            case((QueryAnalytics.faithfulness_check_triggered == True, 1.0), else_=0.0)  # noqa: E712
        ).label("faithfulness_check_rate"),
        func.sum(QueryAnalytics.total_tokens).label("total_tokens"),
        func.avg(QueryAnalytics.react_steps).label("avg_react_steps"),
        func.avg(
            case((QueryAnalytics.has_error == True, 1.0), else_=0.0)  # noqa: E712
        ).label("error_rate"),
    ).where(
        QueryAnalytics.created_at >= start_dt,
        QueryAnalytics.created_at < end_dt,
    )

    # kb_id 过滤
    if kb_id is not None:
        import uuid as _uuid
        try:
            kb_uuid = _uuid.UUID(kb_id)
        except ValueError:
            kb_uuid = None
        if kb_uuid is not None:
            stmt = stmt.where(QueryAnalytics.kb_id == kb_uuid)

    result = await db.execute(stmt)
    row = result.first()

    if row is None or row.total_queries == 0:
        return AnalyticsResponse(
            total_queries=0,
            start_date=start_date,
            end_date=end_date,
        )

    return AnalyticsResponse(
        total_queries=row.total_queries,
        avg_latency_ms=round(row.avg_latency_ms, 1) if row.avg_latency_ms else None,
        avg_confidence=round(row.avg_confidence, 4) if row.avg_confidence else None,
        low_confidence_rate=round(row.low_confidence_rate, 4) if row.low_confidence_rate else 0.0,
        tool_usage=ToolUsageStats(
            graph_rag_triggered=round(row.graph_rag_triggered_rate, 4) if row.graph_rag_triggered_rate else 0.0,
            bm25_contributed=round(row.bm25_contributed_rate, 4) if row.bm25_contributed_rate else 0.0,
            faithfulness_check_triggered=round(row.faithfulness_check_rate, 4) if row.faithfulness_check_rate else 0.0,
        ),
        token_consumption=TokenConsumptionStats(
            total_tokens=row.total_tokens or 0,
        ),
        avg_react_steps=round(row.avg_react_steps, 2) if row.avg_react_steps else None,
        error_rate=round(row.error_rate, 4) if row.error_rate else 0.0,
        start_date=start_date,
        end_date=end_date,
    )
```

### Step 3: 注册路由

修改 `app/api/v2/router.py`：
- 新增 `from app.api.v2.endpoints import analytics`
- 新增 `router.include_router(analytics.router)`

### Step 4: 运行测试

```bash
conda activate geo_agent && pytest tests/test_v2_t12.py -v
```

### Step 5: 提交

```bash
git add app/api/v2/endpoints/analytics.py app/api/v2/router.py tests/test_v2_t12.py
git commit -m "feat(v2): T12 OBS-03 聚合统计端点 GET /api/v2/analytics"
```

---

## Task 5: 全量回归 + 进度文档

**Files:**
- Modify: `docs/progress.md`
- Modify: `docs/v2_dev_plan.md`

### Step 1: 运行 T12 全部测试

```bash
conda activate geo_agent && pytest tests/test_v2_t12.py -v
```

### Step 2: 运行 V2 全套回归

```bash
conda activate geo_agent && pytest tests/test_v2_t0.py tests/test_v2_t1.py tests/test_v2_t2.py tests/test_v2_t3.py tests/test_v2_t7.py tests/test_v2_t8.py tests/test_v2_t9.py tests/test_v2_t11.py tests/test_v2_p1.py tests/test_v2_t10.py tests/test_v2_t12.py -q
```

预期：零回归

### Step 3: 更新 docs/progress.md

1. T12 状态从 `⬜ 待开始` 改为 `✅ 完成 + 单测验收`
2. 填入完成日期 2026-06-16
3. 新增 T12 交付内容段落

### Step 4: 更新 docs/v2_dev_plan.md

在进度追踪区追加 `### ✅ T12 完成 · 2026-06-16` 段落。

### Step 5: 提交

```bash
git add docs/progress.md docs/v2_dev_plan.md
git commit -m "docs(v2): T12 OBS-03 聚合统计完成 + 进度更新"
```

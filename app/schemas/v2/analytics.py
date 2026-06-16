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

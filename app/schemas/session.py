"""会话相关的 Pydantic Schema。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CreateSessionResponse(BaseModel):
    """POST /api/v1/sessions 响应体（API-01 验收标准）。"""

    id: uuid.UUID = Field(..., description="会话唯一标识")
    created_at: datetime = Field(..., description="创建时间（UTC）")

    model_config = {"from_attributes": True}


class SessionInfo(BaseModel):
    """会话详情（预留用于后续扩展的列表查询）。"""

    id: uuid.UUID
    created_at: datetime
    db_metadata: dict | None = Field(None, alias="metadata")

    model_config = {"from_attributes": True, "populate_by_name": True}
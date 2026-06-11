"""知识库相关的 Pydantic Schema（V1.5 PRD §3.2 KB-01~05）。

字段约束严格对齐 PRD：
- name 全局唯一，最长 128
- description 最长 500
- embedding_dim 创建后不可修改（PRD KB-04 明确）
- chunk_size 范围 128~2048
- chunk_overlap 不超过 chunk_size 的 50%
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.knowledge_base import KB_STATUS_CHOICES

# ──────────────── 请求 ────────────────


# embedding_dim 缺省值（与 PG 默认值、Milvus schema 默认 dim 保持一致）
DEFAULT_EMBEDDING_DIM = 4096
DEFAULT_CHUNK_SIZE = 512
DEFAULT_CHUNK_OVERLAP = 64


class KnowledgeBaseCreateRequest(BaseModel):
    """POST /api/v1/knowledge-bases 请求体（KB-01）。

    `name` 必填、全局唯一；其余字段缺省使用 PRD 推荐值。
    `embedding_dim` 创建后不可改 —— Update 接口（KB-04）会拦截。
    """

    name: str = Field(..., min_length=1, max_length=128, description="知识库名称，全局唯一")
    description: str | None = Field(
        None, max_length=500, description="知识库描述"
    )
    embedding_dim: int = Field(
        DEFAULT_EMBEDDING_DIM,
        gt=0,
        description="向量维度（创建后不可改）",
    )
    chunk_size: int = Field(
        DEFAULT_CHUNK_SIZE,
        ge=128,
        le=2048,
        description="文本切片大小（Token 数）",
    )
    chunk_overlap: int = Field(
        DEFAULT_CHUNK_OVERLAP,
        ge=0,
        description="切片重叠大小（Token 数），不超过 chunk_size 的 50%",
    )

    @field_validator("name")
    @classmethod
    def _no_blank_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name 不能为空白字符串")
        return v

    @model_validator(mode="after")
    def _overlap_within_half_of_size(self) -> "KnowledgeBaseCreateRequest":
        # PRD 明确：chunk_overlap 不超过 chunk_size 的 50%
        if self.chunk_overlap > self.chunk_size // 2:
            raise ValueError(
                f"chunk_overlap ({self.chunk_overlap}) 不能超过 chunk_size "
                f"({self.chunk_size}) 的 50%"
            )
        return self


class KnowledgeBaseUpdateRequest(BaseModel):
    """PATCH /api/v1/knowledge-bases/{kb_id} 请求体（KB-04）。

    PRD 明确：仅 `name` / `description` 可改；`embedding_dim` / `chunk_size` /
    `chunk_overlap` 创建后只读，传入直接 422（借 extra="forbid"）。

    name / description 都可选，但至少传一个；两个都不传 → 422。
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        None, min_length=1, max_length=128, description="新的知识库名称"
    )
    description: str | None = Field(
        None, max_length=500, description="新的知识库描述"
    )

    @field_validator("name")
    @classmethod
    def _no_blank_name(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("name 不能为空白字符串")
        return v

    @model_validator(mode="after")
    def _require_at_least_one(self) -> "KnowledgeBaseUpdateRequest":
        # 注意 description 允许显式传 null（用户主动清空描述），所以不能简单看 None
        if "name" not in self.model_fields_set and "description" not in self.model_fields_set:
            raise ValueError("至少需要传入 name 或 description 之一")
        return self


# ──────────────── 响应 ────────────────


class KnowledgeBaseDetail(BaseModel):
    """知识库详情（KB-01 / KB-03 共用）。

    `entity_count` 由 KB-03 详情接口实时计算（S2 阶段返回 0 stub，
    S5 阶段接通 Neo4j 真实查询）。
    """

    id: uuid.UUID
    name: str
    description: str | None
    embedding_dim: int
    chunk_size: int
    chunk_overlap: int
    status: str = Field(..., description="active / building / error")
    file_count: int = Field(..., description="冗余统计：关联文件数")
    chunk_count: int = Field(..., description="冗余统计：Milvus 向量切片数")
    entity_count: int = Field(0, description="Neo4j 实体数（S5 阶段接通）")
    created_at: datetime

    @field_validator("status")
    @classmethod
    def _status_in_choices(cls, v: str) -> str:
        if v not in KB_STATUS_CHOICES:
            raise ValueError(f"status 必须是 {KB_STATUS_CHOICES} 之一")
        return v

    @classmethod
    def from_orm_kb(cls, kb, *, entity_count: int = 0) -> "KnowledgeBaseDetail":
        """从 ORM 模型构造；entity_count 由调用方传入（KB-03 接通 Neo4j 时）。"""
        return cls(
            id=kb.id,
            name=kb.name,
            description=kb.description,
            embedding_dim=kb.embedding_dim,
            chunk_size=kb.chunk_size,
            chunk_overlap=kb.chunk_overlap,
            status=kb.status,
            file_count=kb.file_count,
            chunk_count=kb.chunk_count,
            entity_count=entity_count,
            created_at=kb.created_at,
        )


class KnowledgeBaseListItem(BaseModel):
    """知识库列表项（KB-02）。

    PRD 明确列表项含 id / name / description / file_count / chunk_count /
    created_at / status；不含 entity_count（避免每条都查 Neo4j）。
    """

    id: uuid.UUID
    name: str
    description: str | None
    file_count: int
    chunk_count: int
    status: str
    created_at: datetime

    @classmethod
    def from_orm_kb(cls, kb) -> "KnowledgeBaseListItem":
        return cls(
            id=kb.id,
            name=kb.name,
            description=kb.description,
            file_count=kb.file_count,
            chunk_count=kb.chunk_count,
            status=kb.status,
            created_at=kb.created_at,
        )


class KnowledgeBaseListResponse(BaseModel):
    """KB 列表分页响应（KB-02）。"""

    items: list[KnowledgeBaseListItem]
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)
    total: int = Field(..., ge=0)

"""V1.5 PostgreSQL 模型字段单测（S0 验收，纯结构校验，不连真库）。

覆盖：
- ChatSession 新增 5 个 V1.5 字段是否就位
- KnowledgeBase 字段、唯一约束、check 约束、默认值
- KbFile 字段、外键、默认值、status/progress 枚举
"""

from app.models import ChatSession, KbFile, KnowledgeBase
from app.models.kb_file import (
    FILE_STATUS_CHOICES,
    FILE_STATUS_PENDING,
)
from app.models.knowledge_base import (
    KB_STATUS_ACTIVE,
    KB_STATUS_CHOICES,
)


# ─────────── ChatSession 扩展字段（PRD §5.1） ───────────


def test_chat_session_has_v1_5_fields():
    """V1.5 新增 5 个字段必须就位。"""
    cols = ChatSession.__table__.columns
    for name in ("title", "summary", "summarized_at", "updated_at", "message_count"):
        assert name in cols, f"ChatSession 缺少 V1.5 字段：{name}"


def test_chat_session_title_max_length():
    """title 字段最长 100 字（PRD §5.1）。"""
    assert ChatSession.__table__.columns["title"].type.length == 100


def test_chat_session_message_count_default_zero():
    """message_count 默认值为 0（server_default）。"""
    col = ChatSession.__table__.columns["message_count"]
    assert col.server_default is not None
    # SQLAlchemy server_default 是 DefaultClause，取其 arg 看字面值
    assert str(col.server_default.arg) == "0"


def test_chat_session_updated_at_has_onupdate():
    """updated_at 必须配置 onupdate，写消息时自动刷新。"""
    col = ChatSession.__table__.columns["updated_at"]
    assert col.onupdate is not None, "updated_at 缺少 onupdate（消息写入时无法自动更新）"


# ─────────── KnowledgeBase（PRD §5.2） ───────────


def test_knowledge_base_table_name():
    assert KnowledgeBase.__tablename__ == "knowledge_bases"


def test_knowledge_base_name_unique_and_length():
    col = KnowledgeBase.__table__.columns["name"]
    assert col.unique is True
    assert col.type.length == 128
    assert col.nullable is False


def test_knowledge_base_immutable_fields_defaults():
    """embedding_dim / chunk_size / chunk_overlap 默认值符合 PRD KB-01。"""
    cols = KnowledgeBase.__table__.columns
    assert str(cols["embedding_dim"].server_default.arg) == "4096"
    assert str(cols["chunk_size"].server_default.arg) == "512"
    assert str(cols["chunk_overlap"].server_default.arg) == "64"


def test_knowledge_base_status_default_active():
    col = KnowledgeBase.__table__.columns["status"]
    assert str(col.server_default.arg) == KB_STATUS_ACTIVE
    assert col.type.length == 20


def test_knowledge_base_status_choices_completeness():
    """status 枚举必须包含 PRD KB-02 要求的 3 个状态。"""
    assert set(KB_STATUS_CHOICES) == {"active", "building", "error"}


def test_knowledge_base_check_constraints_present():
    """chunk_size 范围、chunk_overlap 非负、embedding_dim 正 三条 check 约束都在。"""
    constraints = {c.name for c in KnowledgeBase.__table__.constraints if c.name}
    assert "ck_kb_chunk_size_range" in constraints
    assert "ck_kb_chunk_overlap_nonneg" in constraints
    assert "ck_kb_embedding_dim_positive" in constraints


def test_knowledge_base_redundant_counters_default_zero():
    cols = KnowledgeBase.__table__.columns
    assert str(cols["file_count"].server_default.arg) == "0"
    assert str(cols["chunk_count"].server_default.arg) == "0"


# ─────────── KbFile（PRD §5.3） ───────────


def test_kb_file_table_name():
    assert KbFile.__tablename__ == "kb_files"


def test_kb_file_primary_key_is_uuid():
    pk = list(KbFile.__table__.primary_key)[0]
    assert pk.name == "id"
    # 主键 default 必须是可调用（不能写死，否则每行 PK 相同会冲突）
    assert pk.default is not None
    assert callable(pk.default.arg), "KbFile.id 的 default 必须是可调用工厂"
    # PK 字段类型必须是 UUID
    from sqlalchemy.dialects.postgresql import UUID

    assert isinstance(pk.type, UUID)


def test_kb_file_kb_id_foreign_key_cascade():
    """kb_id 外键必须级联删除，KB 删除时文件元数据一并清理。"""
    col = KbFile.__table__.columns["kb_id"]
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    fk = fks[0]
    assert fk.column.table.name == "knowledge_bases"
    assert fk.ondelete == "CASCADE"


def test_kb_file_status_default_pending():
    col = KbFile.__table__.columns["status"]
    assert str(col.server_default.arg) == FILE_STATUS_PENDING


def test_kb_file_status_choices_completeness():
    assert set(FILE_STATUS_CHOICES) == {
        "pending",
        "processing",
        "completed",
        "failed",
    }


def test_kb_file_progress_default_zero():
    col = KbFile.__table__.columns["progress"]
    assert str(col.server_default.arg) == "0"


def test_kb_file_counters_default_zero():
    cols = KbFile.__table__.columns
    assert str(cols["chunk_count"].server_default.arg) == "0"
    assert str(cols["entity_count"].server_default.arg) == "0"


def test_kb_file_nullable_fields():
    """error_message / celery_task_id / completed_at 都允许为空。"""
    cols = KbFile.__table__.columns
    assert cols["error_message"].nullable is True
    assert cols["celery_task_id"].nullable is True
    assert cols["completed_at"].nullable is True


def test_kb_file_filename_and_path_lengths():
    cols = KbFile.__table__.columns
    assert cols["filename"].type.length == 512
    assert cols["file_path"].type.length == 1024
    assert cols["mime_type"].type.length == 128

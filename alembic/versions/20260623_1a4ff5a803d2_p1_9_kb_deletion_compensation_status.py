"""p1_9_kb_deletion_compensation_status

Revision ID: 1a4ff5a803d2
Revises: b74589b68f7d
Create Date: 2026-06-23 13:44:53.181100+08:00

——— 2026-06-24 改造 ———
原本是 V2 baseline 之后的纯补列脚本。现在 baseline 改成「空库走 create_all、旧库走补列」
后，空库的 create_all 已经把这些列都建了，所以本脚本所有 DDL 都加 IF NOT EXISTS 兜底，
保证两种路径下都幂等。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1a4ff5a803d2'
down_revision: Union[str, Sequence[str], None] = 'b74589b68f7d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """升级到本版本：执行 schema 变更（幂等）。"""
    # kb_files.cleanup_retry_count
    op.execute(
        "ALTER TABLE kb_files ADD COLUMN IF NOT EXISTS cleanup_retry_count "
        "INTEGER DEFAULT 0 NOT NULL"
    )
    op.execute(
        "COMMENT ON COLUMN kb_files.cleanup_retry_count IS "
        "'待补偿清理重试次数；超过 CLEANUP_REAPER_MAX_RETRY 仅告警'"
    )

    # knowledge_bases.updated_at + cleanup_retry_count
    op.execute(
        "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS updated_at "
        "TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_bases.updated_at IS "
        "'行最后更新时间；pending_cleanup 扫描据此判定优先级'"
    )
    op.execute(
        "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS cleanup_retry_count "
        "INTEGER DEFAULT 0 NOT NULL"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_bases.cleanup_retry_count IS "
        "'待补偿清理重试次数；超过 CLEANUP_REAPER_MAX_RETRY 仅告警'"
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_bases_updated_at "
        "ON knowledge_bases (updated_at)"
    )


def downgrade() -> None:
    """回滚本版本：撤销 upgrade() 所做的变更。"""
    op.execute("DROP INDEX IF EXISTS ix_knowledge_bases_updated_at")
    op.execute("ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS cleanup_retry_count")
    op.execute("ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS updated_at")
    op.execute("ALTER TABLE kb_files DROP COLUMN IF EXISTS cleanup_retry_count")

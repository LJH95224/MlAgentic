"""baseline (V1.5 + V2.0 完整 schema)

Revision ID: b74589b68f7d
Revises:
Create Date: 2026-06-22 15:31:54.723140+08:00

——— 2026-06-24 改造 ———
这是项目的「真 baseline」：
- 空库（新机器初始化 / 新测试库）：用 Base.metadata.create_all 一次性把所有表 + V2.0 字段建出来。
- 旧 V1.5 库（kb_files 已存在但缺 updated_at 列）：只补 V2.0 新增列，保留数据。

判定依据：检查 kb_files 表是否存在。

为什么不写一个 V1.5 真 baseline + 一个 V2.0 升级补丁两条迁移？
- 项目升级期没有 alembic，V1.5 库是靠 create_all 建的，无法溯源精确 schema。
- ORM 模型与 V1.5 时代有列名 / 类型微调，直接 `create_all` 拿到的是当前模型 schema，
  这与 V2.0 baseline 完全一致——既然如此，「V1.5 baseline + V2.0 补丁」拆出来意义不大。
- 单文件双分支既能空库一键初始化，又能旧库无痛升级，是当前最简洁的方案。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = 'b74589b68f7d'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _kb_files_exists() -> bool:
    """检查 kb_files 表是否存在（区分空库 vs 旧 V1.5 库）。"""
    bind = op.get_bind()
    insp = inspect(bind)
    return insp.has_table("kb_files")


def upgrade() -> None:
    """升级到本版本：空库走 create_all、旧库走补列。"""
    if not _kb_files_exists():
        # —— 空库分支：用 ORM 模型 metadata 一次性建全部表 ——
        # 这等价于以前 app/main.py lifespan 的 create_all。
        # 必须 import 所有模型，触发它们注册到 Base.metadata
        from app.models import (  # noqa: F401
            AgentTrace,
            ChatMessage,
            ChatSession,
            EvalTask,
            KbFile,
            KnowledgeBase,
            QueryAnalytics,
        )
        from app.models.base import Base

        bind = op.get_bind()
        Base.metadata.create_all(bind=bind)
        # create_all 已经把 updated_at + 索引建上了，跳过后续 ALTER
        return

    # —— 旧 V1.5 库分支：只补 V2 新增的 updated_at 列 + 索引 ——
    # 用 IF NOT EXISTS 兜底，避免半升级状态下重复跑炸掉
    op.execute(
        "ALTER TABLE kb_files ADD COLUMN IF NOT EXISTS updated_at "
        "TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL"
    )
    op.execute("COMMENT ON COLUMN kb_files.updated_at IS '行最后更新时间；processing 文件的心跳锚点（卡死回收依据）'")
    op.execute("CREATE INDEX IF NOT EXISTS ix_kb_files_updated_at ON kb_files (updated_at)")


def downgrade() -> None:
    """回滚本版本：仅处理 V2 补列，空库情况下回滚等于 DROP 整库，下游应直接重建库。"""
    op.execute("DROP INDEX IF EXISTS ix_kb_files_updated_at")
    op.execute("ALTER TABLE kb_files DROP COLUMN IF EXISTS updated_at")

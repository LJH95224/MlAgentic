"""Milvus Collection 命名规则（V1.5 PRD §5.4）。

V1.5 每个知识库对应独立 Milvus Collection，命名规则：
    kb_{kb_id_no_hyphen}

例：
    kb_id = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
    → collection_name = "kb_a1b2c3d4e5f67890abcdef1234567890"

设计要点：
- 整个项目对 KB Collection 命名只有这一处真相，避免散落字符串拼接漂移
- Milvus Collection 名长度上限 255 字符、必须以字母开头、只能含字母/数字/下划线
  → 加 `kb_` 前缀确保以字母开头；UUID 32 个 hex 字符 + 3 = 35，远小于 255 ✓
- 反向解析（collection name → kb_id）不提供：调用方应该一直拿着 kb_id（UUID）
  作为锚点向下游传递，不要从 collection name 反推
"""

from __future__ import annotations

import re
import uuid

# KB Collection 命名前缀
KB_COLLECTION_PREFIX = "kb_"

# 32 个小写 hex 字符
_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")


def build_kb_collection_name(kb_id: uuid.UUID | str) -> str:
    """根据 kb_id 生成对应 Milvus Collection 名。

    Args:
        kb_id: 知识库 UUID（UUID 对象或合法 UUID 字符串）

    Returns:
        Collection 名，形如 "kb_a1b2c3d4e5f67890abcdef1234567890"

    Raises:
        ValueError: kb_id 不是合法 UUID
    """
    if isinstance(kb_id, str):
        # 标准化校验：必须能 parse 成 UUID
        try:
            kb_id = uuid.UUID(kb_id)
        except ValueError as e:
            raise ValueError(f"kb_id 不是合法 UUID：{kb_id!r}") from e

    # uuid.hex 已经是无连字符的 32 位小写 hex；不直接 str(kb_id).replace('-', '')
    # 是因为前者更明确，且不依赖 str 表示形式
    return f"{KB_COLLECTION_PREFIX}{kb_id.hex}"


def is_kb_collection_name(name: str) -> bool:
    """判断一个 Collection 名是否符合 KB 命名约定。

    用于运维场景的"识别哪些 collection 是 V1.5 KB 创建的"，比如清理孤儿
    Collection 时筛选；业务代码不需要用它。
    """
    if not name.startswith(KB_COLLECTION_PREFIX):
        return False
    suffix = name[len(KB_COLLECTION_PREFIX) :]
    return bool(_HEX32_RE.match(suffix))


__all__ = [
    "KB_COLLECTION_PREFIX",
    "build_kb_collection_name",
    "is_kb_collection_name",
]

"""Milvus 标量过滤与权限基线工具。

这些函数与具体检索后端（`retriever` 纯向量 / `hybrid_retriever` 混合检索）无关，
属于共用的过滤表达式拼装与权限基线注入逻辑。提到独立模块是为了：

1. 让 `hybrid_retriever` 不再 `from app.rag.retriever import _build_filter_expr, get_current_role`
   ——避免跨模块依赖私有名。
2. 让 `retriever` 重新导出本模块名字保持对外契约（tests/test_rag_retriever.py 仍按
   `from app.rag.retriever import _build_filter_expr, _format_hits, ...` 导入）。

权限基线（RAG-04）：所有 filter 表达式都强制包含
``ARRAY_CONTAINS(allowed_roles, current_role)``，不暴露给 LLM。
"""

from __future__ import annotations

from app.core.config import get_settings


# ──────────────────── 权限解析 ────────────────────


def get_current_role() -> str:
    """获取当前请求的角色（RAG-04）。

    当前没有用户体系，直接从 .env 读取 ``RAG_DEFAULT_ROLE``（默认 ``"ALL"``）。
    后续接入用户体系（JWT / contextvar）时只改本函数实现，工具签名不变。
    """
    return get_settings().rag_default_role


# ──────────────────── 字面量转义 ────────────────────


def _milvus_str(value: str) -> str:
    """把 Python 字符串转成 Milvus filter 可用的双引号字面量（带转义）。

    Milvus filter 没有参数化绑定，所有字面量必须在表达式拼装时手动转义，
    否则 doc_type / document_id / entity_tags 等用户/LLM 可控字段就会成为注入点
    （历史 H-07 已经处理过）。

    覆盖两类转义：
      - 反斜杠 ``\\`` → ``\\\\``，避免后续的双引号转义被反斜杠吃掉
      - 双引号 ``"`` → ``\\"``，闭合掉用户传入的引号，杜绝表达式截断
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


# ──────────────────── 过滤表达式拼装 ────────────────────


def _build_filter_expr(
    doc_type: str | None,
    document_id: str | None,
    entity_tags: list[str] | None,
    current_role: str,
    kb_ids: list[str] | None = None,
) -> str:
    """拼装 Milvus filter 表达式（跨检索后端共用）。

    基线过滤永远包含权限子句（RAG-04）。其他过滤按传参可选叠加。

    B M-06 新增 ``kb_ids`` 参数：在物理 Collection 隔离之上加一层 ``kb_id IN [...]``
    兜底过滤。chunk schema 中的 ``kb_id`` 冗余字段就是为此设计的——即使
    Collection 命名规则改变或 contextvar 被绕过，也保证不会跨 KB 召回。

    Milvus filter 语法注意：
      - 字符串值要带双引号
      - 数组包含用 ``ARRAY_CONTAINS(field, value)`` 单个 / ``ARRAY_CONTAINS_ANY(field, [list])`` 任一
      - JSON 字段访问用 ``metadata["key"]``
      - 多条件用小写 ``and`` 连接

    Args:
        doc_type: 文档类型过滤（``metadata["type"]``），None 时跳过。
        document_id: 限定到具体文档，None 时跳过。
        entity_tags: KG-04 图谱锚定标签数组，任一命中即可，None / 空列表跳过。
        current_role: 当前角色，由 :func:`get_current_role` 提供。
        kb_ids: B M-06 kb_id 兜底过滤列表。传 None 或空列表时不加该子句
                （全局 collection 场景不需要 kb_id 过滤）。

    Returns:
        Milvus filter 表达式字符串，多子句用 ``and`` 连接。
    """
    # 权限基线：硬编码注入（不暴露给 LLM）
    clauses = [f"ARRAY_CONTAINS(allowed_roles, {_milvus_str(current_role)})"]

    # B M-06：kb_id 兜底过滤（纵深防御，不替代 contextvar / Collection 命名隔离）
    if kb_ids:
        kb_lit = "[" + ", ".join(_milvus_str(k) for k in kb_ids) + "]"
        clauses.append(f"kb_id IN {kb_lit}")

    if doc_type:
        clauses.append(f'metadata["type"] == {_milvus_str(doc_type)}')

    if document_id:
        clauses.append(f"document_id == {_milvus_str(document_id)}")

    if entity_tags:
        tags_lit = "[" + ", ".join(_milvus_str(t) for t in entity_tags) + "]"
        clauses.append(f"ARRAY_CONTAINS_ANY(entity_tags, {tags_lit})")

    return " and ".join(clauses)


__all__ = [
    "get_current_role",
    "_milvus_str",
    "_build_filter_expr",
]

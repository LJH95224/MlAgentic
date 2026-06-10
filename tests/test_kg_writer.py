"""Neo4j Writer 单测（不连真 Neo4j）。

验证 Cypher 文本结构与参数化正确，确保：
- 全部用 MERGE 实现幂等
- 全部走参数化（防注入）
- name+type 复合键正确传递
"""

import pytest

from app.kg.writer import (
    BULK_LINK_CYPHER,
    BULK_UPSERT_ENTITIES_CYPHER,
    LINK_ENTITY_TO_CHUNK_CYPHER,
    UPSERT_DOCUMENT_CYPHER,
    UPSERT_ENTITY_CYPHER,
)


# ──────────────────── Cypher 静态文本检查 ────────────────────


def test_upsert_document_uses_merge_and_document_id():
    """upsert_document Cypher 必须用 MERGE 按 document_id 幂等。"""
    cypher = UPSERT_DOCUMENT_CYPHER
    assert "MERGE (d:Document {document_id: $document_id})" in cypher
    # 创建时间用 coalesce 保护，不覆盖已有值
    assert "coalesce(d.created_at, $created_at)" in cypher


def test_upsert_entity_uses_composite_key():
    """upsert_entity 必须按 (name, type) 复合键 MERGE。"""
    cypher = UPSERT_ENTITY_CYPHER
    assert "MERGE (e:Entity {name: $name, type: $type})" in cypher
    # document_ids 数组：新建用 ON CREATE，已存在用 ON MATCH 追加且去重
    assert "ON CREATE SET" in cypher
    assert "ON MATCH SET" in cypher
    assert "$document_id IN" in cypher  # 去重判定


def test_link_entity_creates_mentioned_in_with_chunk_id():
    """link_entity_to_chunk 必须建 MENTIONED_IN 关系，chunk_id 作为属性。"""
    cypher = LINK_ENTITY_TO_CHUNK_CYPHER
    # 先 MATCH 两端节点，再 MERGE 关系（带 chunk_id 去重）
    assert "MATCH (e:Entity {name: $name, type: $type})" in cypher
    assert "MATCH (d:Document {document_id: $document_id})" in cypher
    assert "MERGE (e)-[r:MENTIONED_IN {chunk_id: $chunk_id}]->(d)" in cypher


def test_bulk_upsert_uses_unwind():
    """批量版本必须用 UNWIND 一次性下发，避免逐条往返。"""
    cypher = BULK_UPSERT_ENTITIES_CYPHER
    assert "UNWIND $rows AS row" in cypher
    assert "MERGE (e:Entity {name: row.name, type: row.type})" in cypher


def test_bulk_link_uses_unwind():
    cypher = BULK_LINK_CYPHER
    assert "UNWIND $rows AS row" in cypher
    assert "MERGE (e)-[r:MENTIONED_IN {chunk_id: row.chunk_id}]->(d)" in cypher


def test_no_string_interpolation_in_cypher():
    """所有 Cypher 都不能包含字符串拼接占位 —— 防止 SQL 注入式漏洞。

    Cypher 注入主要风险是把用户输入拼到查询里；本模块全部走 $param 参数化，
    所有 Cypher 文本中不应该出现 Python f-string 残留（如 '{xxx}' 这种格式）。
    """
    for cypher in [
        UPSERT_DOCUMENT_CYPHER,
        UPSERT_ENTITY_CYPHER,
        LINK_ENTITY_TO_CHUNK_CYPHER,
        BULK_UPSERT_ENTITIES_CYPHER,
        BULK_LINK_CYPHER,
    ]:
        # 检查没有形如 "{var}" 的纯字符串插值残留
        # 允许 Cypher 自身的 map literal {key: value}
        # 简化判定：每个 { 后面必须紧跟字母（Cypher 标识符），不应该是数字或符号
        import re
        # 找形如 {abc} （Python 占位）但排除 {key: ...} （Cypher map）
        py_format = re.findall(r"\{[a-zA-Z_]\w*\}", cypher)
        assert not py_format, f"Cypher 中疑似 Python 占位残留: {py_format}\n{cypher}"

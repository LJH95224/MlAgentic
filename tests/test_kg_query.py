"""KG 查询与 Tool 单测（不连真 Neo4j）。

验证：
- max_hops 夹值
- 各种过滤组合下 Cypher 文本结构
- 结果格式化（空 / 单条 / 多条）
- @tool 集成与工具注册中心挂接
"""

import pytest

from app.kg.query import _clamp_hops, build_cypher, format_paths
from app.kg.tool import query_knowledge_graph


# ──────────────────── _clamp_hops ────────────────────


class TestClampHops:
    def test_in_range_unchanged(self):
        assert _clamp_hops(1) == 1
        assert _clamp_hops(3) == 3
        assert _clamp_hops(5) == 5

    def test_too_large_clamped_to_5(self):
        assert _clamp_hops(10) == 5
        assert _clamp_hops(999) == 5

    def test_too_small_clamped_to_1(self):
        assert _clamp_hops(0) == 1
        assert _clamp_hops(-3) == 1


# ──────────────────── build_cypher ────────────────────


class TestBuildCypher:
    def test_no_filters_has_no_where(self):
        """无任何过滤时，不出现 WHERE 子句。"""
        cypher = build_cypher(entity_type=None, relation_types=None, max_hops=2)
        assert "WHERE" not in cypher
        # max_hops 已被拼到变长路径里
        assert "[r*1..2]" in cypher
        # 必须按起点 name 参数化
        assert "(start:Entity {name: $name})" in cypher

    def test_with_entity_type_adds_where_clause(self):
        cypher = build_cypher(
            entity_type="LOCATION", relation_types=None, max_hops=2
        )
        assert "WHERE start.type = $entity_type" in cypher

    def test_with_relation_types_adds_all_filter(self):
        cypher = build_cypher(
            entity_type=None, relation_types=["MENTIONED_IN"], max_hops=2
        )
        assert "ALL(rel IN r WHERE type(rel) IN $rel_types)" in cypher

    def test_both_filters_combined_with_and(self):
        cypher = build_cypher(
            entity_type="LOCATION",
            relation_types=["RELATED_TO"],
            max_hops=3,
        )
        assert "WHERE start.type = $entity_type AND ALL(rel IN r WHERE" in cypher

    def test_max_hops_clamped_externally(self):
        """build_cypher 不负责 clamp，需调用方先 clamp 再传入。
        但变长路径模式必须出现且 = 传入值（验证拼接正确）。"""
        cypher = build_cypher(None, None, max_hops=5)
        assert "[r*1..5]" in cypher

    def test_returns_required_fields(self):
        """RETURN 必须暴露给上层格式化用到的字段。"""
        cypher = build_cypher(None, None, max_hops=1)
        assert "nodes_in_path" in cypher
        assert "rels_in_path" in cypher
        assert "hops" in cypher
        assert "LIMIT 20" in cypher


# ──────────────────── format_paths ────────────────────


class TestFormatPaths:
    def test_empty_returns_hint(self):
        out = format_paths("台风", None, [])
        assert "台风" in out
        assert "未找到" in out

    def test_with_entity_type_in_header(self):
        out = format_paths("台风", "LOCATION", [])
        assert "(LOCATION)" in out

    def test_single_path_two_nodes(self):
        records = [
            {
                "nodes_in_path": [
                    {"name": "台风", "type": "LOCATION"},
                    {"name": "typhoon_paths", "type": "Document"},
                ],
                "rels_in_path": ["MENTIONED_IN"],
                "hops": 1,
            }
        ]
        out = format_paths("台风", None, records)
        assert "[1]" in out
        assert "台风" in out
        assert "MENTIONED_IN" in out
        assert "typhoon_paths" in out

    def test_multi_hop_path(self):
        records = [
            {
                "nodes_in_path": [
                    {"name": "台风", "type": "LOCATION"},
                    {"name": "副热带高压", "type": "OTHER"},
                    {"name": "typhoon_paths", "type": "Document"},
                ],
                "rels_in_path": ["RELATED_TO", "MENTIONED_IN"],
                "hops": 2,
            }
        ]
        out = format_paths("台风", None, records)
        # 两个关系应都出现
        assert "RELATED_TO" in out
        assert "MENTIONED_IN" in out
        # 中间节点存在
        assert "副热带高压" in out

    def test_counts_records_in_header(self):
        records = [
            {"nodes_in_path": [{"name": "a"}], "rels_in_path": [], "hops": 0},
            {"nodes_in_path": [{"name": "b"}], "rels_in_path": [], "hops": 0},
            {"nodes_in_path": [{"name": "c"}], "rels_in_path": [], "hops": 0},
        ]
        out = format_paths("x", None, records)
        assert "共 3 条" in out


# ──────────────────── @tool 集成 ────────────────────


def test_tool_name_and_args_schema_for_llm():
    """@tool 装饰后必须暴露规范的 name 与参数 schema 给 LLM。"""
    assert query_knowledge_graph.name == "query_knowledge_graph"

    schema = query_knowledge_graph.args_schema.model_json_schema()
    props = schema["properties"]
    assert "entity_name" in props
    assert "entity_type" in props
    assert "relation_types" in props
    assert "max_hops" in props


def test_tool_registered_in_tool_map():
    """query_knowledge_graph 已挂到工具注册中心。"""
    from app.tools import get_tool_map

    tool_map = get_tool_map()
    assert "query_knowledge_graph" in tool_map
    assert tool_map["query_knowledge_graph"] is query_knowledge_graph

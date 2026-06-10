"""知识检索工具：search_knowledge_base（RAG-02 / 03 / 04 / 05）。

这是 Agent 在 ReAct 循环中**主动调用**的工具，把"是否检索 / 检索什么 / 检索多少"
的决策权完全交给 LLM。本模块负责：

- RAG-02：用 langchain @tool 装饰器暴露规范化签名给 LLM
- RAG-03：把 LLM 传入的 doc_type / document_id 翻译成 Milvus 标量过滤表达式
- RAG-04：检索前自动追加 ARRAY_CONTAINS(allowed_roles, current_role) 权限过滤
- RAG-05：召回结果中保留 document_id 与 entity_tags 透传给后续 Graph RAG

异步约定：
- 工具定义为 async 函数。LangGraph tool_node 通过 tool.ainvoke 调用，
  Embedding 步骤直接 await，避免 asyncio.run / nest_asyncio 兼容问题。
- Milvus search 是同步 gRPC 调用 —— 短耗时操作直接同步执行，不再包 to_thread。

异常策略：直接抛回上层。LangGraph 的 tool_node 会捕获并转为
ToolMessage(status="error")，触发 AGT-04 错误反思链路 —— 本层不要吞异常。
"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from app.core.config import get_settings
from app.rag.embedding import aembed_texts
from app.rag.milvus_client import get_milvus_client

logger = logging.getLogger(__name__)


# ──────────────────── 权限解析（V1.0 占位） ────────────────────


def get_current_role() -> str:
    """获取当前请求的角色。

    V1.0 阶段没有用户体系，直接从 .env 读取 RAG_DEFAULT_ROLE（默认 "ALL"）。
    3.6 / 后续阶段接入用户体系时，改为从请求上下文（contextvar）解析即可，
    工具签名无需改动。
    """
    return get_settings().rag_default_role


# ──────────────────── 过滤表达式拼装 ────────────────────


def _build_filter_expr(
    doc_type: str | None,
    document_id: str | None,
    entity_tags: list[str] | None,
    current_role: str,
) -> str:
    """拼装 Milvus filter 表达式。

    基线过滤永远包含权限子句（RAG-04）。其他过滤按传参可选叠加。

    Milvus filter 语法注意：
      - 字符串值要带双引号
      - 数组包含用 ARRAY_CONTAINS(field, value) 单个 / ARRAY_CONTAINS_ANY(field, [list]) 任一
      - JSON 字段访问用 metadata["key"]
      - 多条件用小写 and 连接
    """
    # 权限基线：硬编码注入（不暴露给 LLM）
    clauses = [f'ARRAY_CONTAINS(allowed_roles, "{current_role}")']

    if doc_type:
        # JSON 字段访问 + 字符串等值
        clauses.append(f'metadata["type"] == "{doc_type}"')

    if document_id:
        clauses.append(f'document_id == "{document_id}"')

    if entity_tags:
        # KG-04 图谱锚定后注入：召回任一标签匹配的 chunk
        # 用 Python 列表字面量语法序列化为 Milvus 接受的格式
        tags_lit = "[" + ", ".join(f'"{t}"' for t in entity_tags) + "]"
        clauses.append(f"ARRAY_CONTAINS_ANY(entity_tags, {tags_lit})")

    return " and ".join(clauses)


# ──────────────────── 结果格式化 ────────────────────


def _format_hits(hits: list[dict]) -> str:
    """把 Milvus 检索结果格式化为对 LLM 友好的字符串。

    形如：
        [1] (score=0.872, doc=typhoon_2024, tags=[台风,海洋]) 文本片段...
        [2] (score=0.851, doc=...) ...

    单条不带 entity_tags 时也不报错（3.5 阶段实体抽取还没上线，标签可能为空）。
    """
    if not hits:
        return "（检索无结果。可尝试换关键词或放宽 doc_type 过滤后重试。）"

    lines = []
    for i, h in enumerate(hits, start=1):
        # MilvusClient.search 返回结构：
        #   [{"id": ..., "distance": ..., "entity": {...output_fields...}}, ...]
        entity = h.get("entity", {})
        distance = h.get("distance", 0.0)
        doc_id = entity.get("document_id", "?")
        content = entity.get("content", "")
        tags = entity.get("entity_tags") or []

        tags_str = f", tags=[{','.join(tags)}]" if tags else ""
        lines.append(
            f"[{i}] (score={distance:.3f}, doc={doc_id}{tags_str})\n{content}"
        )

    return "\n\n".join(lines)


# ──────────────────── 核心检索逻辑 ────────────────────


async def _do_search(
    query: str,
    top_k: int,
    doc_type: str | None,
    document_id: str | None,
    entity_tags: list[str] | None,
) -> str:
    """实际检索：embed → search → format。被 @tool 包装的 async 函数复用。"""
    settings = get_settings()
    client = get_milvus_client()
    current_role = get_current_role()

    # 入参校验（防御性）：LLM 偶尔会传不合理的 top_k
    if top_k < 1:
        top_k = 5
    if top_k > 50:
        top_k = 50

    # 1) 文本 → 向量（异步调用 Embedding API）
    vectors = await aembed_texts([query])
    query_vec = vectors[0]

    # 2) 拼过滤表达式
    filter_expr = _build_filter_expr(doc_type, document_id, entity_tags, current_role)
    logger.info(
        "search_knowledge_base: query=%r top_k=%d filter=%s",
        query[:60],
        top_k,
        filter_expr,
    )

    # 3) Milvus 检索（同步 gRPC，单次调用耗时通常 < 100ms，不阻塞事件循环）
    raw = client.search(
        collection_name=settings.milvus_collection,
        data=[query_vec],
        filter=filter_expr,
        limit=top_k,
        output_fields=[
            "chunk_id",
            "content",
            "document_id",
            "metadata",
            "entity_tags",
        ],
        search_params={"metric_type": "COSINE", "params": {"ef": 64}},
    )

    # raw 结构：list[list[dict]]，外层每个 query 一个结果列表
    hits = raw[0] if raw else []
    logger.info("search_knowledge_base: 命中 %d 条", len(hits))

    return _format_hits(hits)


# ──────────────────── 暴露给 LLM 的 Tool ────────────────────


@tool
async def search_knowledge_base(
    query: str,
    top_k: int = 5,
    doc_type: str | None = None,
    document_id: str | None = None,
    entity_tags: list[str] | None = None,
) -> str:
    """检索知识库中与用户问题语义最相关的文本片段。

    当遇到以下情况时应主动调用：
    - 用户问题涉及你不掌握的专业资料、政策文件、历史报告
    - 需要引用具体来源的事实性数据
    - 对话上下文中没有现成答案

    与 query_knowledge_graph 的协同（Graph RAG）：
    - 若问题涉及实体关系，可先调 query_knowledge_graph 拿到相关实体名
    - 再调本工具时传入 entity_tags=[实体名列表]，精筛包含这些实体的切片

    Args:
        query: 检索的自然语言查询。应当尽量浓缩为关键概念，不要直接传整段用户原话。
        top_k: 返回的最相关片段数量，默认 5，可调范围 1~50。
        doc_type: 可选，按文档类型过滤，例如 "report"、"policy"、"paper"。
                  仅在用户明确限定文档类型时传入。
        document_id: 可选，限定到某个具体文档。仅在已知 document_id 时传入。
        entity_tags: 可选，限定召回的切片必须包含其中任一实体标签
                     （配合 query_knowledge_graph 实现 Graph RAG 联合查询）。

    Returns:
        格式化的检索结果字符串。每条形如 "[序号] (score=分数, doc=文档ID) 文本"。
        若无命中，会返回提示文本而不是空字符串。
    """
    return await _do_search(query, top_k, doc_type, document_id, entity_tags)


__all__ = [
    "search_knowledge_base",
    "get_current_role",
    "_build_filter_expr",  # 暴露给单测
    "_format_hits",        # 暴露给单测
    "_do_search",          # 暴露给单测
]

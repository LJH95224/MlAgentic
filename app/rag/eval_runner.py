"""单题 RAG 执行器（T11 评估专用）。

设计要点：
- 复用 V2 query 的内部函数：hybrid_search → build_context_with_citation → generate_answer
- 不写 Trace（评估场景每题写 8 条 step 会污染 agent_traces 表）
- 不调 faithfulness_check（ragas 自身会跑 faithfulness 指标，避免 LLM 双跑）
- 不绕 HTTP，避免对 worker host:port 的网络依赖
- 单题失败软降级：answer="（生成失败）", contexts=[]，让上层继续打分剩余题
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import reset_current_kb_ids, set_current_kb_ids
from app.api.v2.endpoints.query import generate_answer
from app.core.config import get_settings
from app.models.knowledge_base import KnowledgeBase
from app.rag.citation import build_context_with_citation
from app.rag.hybrid_retriever import hybrid_search
from app.rag.query_ner import anchor_to_graph, extract_query_entities
from app.rag.query_rewriter import rewrite_query
from app.rag.retrieval_config import resolve_options
from app.schemas.v2.query import QueryOptions

logger = logging.getLogger(__name__)


async def run_single_query_for_eval(
    *,
    query: str,
    kb_ids: list[uuid.UUID],
    options: dict[str, Any] | None,
    db: AsyncSession,
) -> dict[str, Any]:
    """跑一道评估题：拿 answer + contexts 给 ragas 打分。

    Args:
        query: 评估问题
        kb_ids: 限定知识库（一般是评估任务对应的单个 KB）
        options: 评估参数（top_k / enable_graph_rag / reranker_enable / query_rewrite 等）
        db: PG session（评估任务 _main 里从 task_resources.db() 拿）

    Returns:
        {
            "answer": str,
            "contexts": list[str],   # 每条 chunk 的 content（给 ragas 用）
            "source_citations": list[dict],
            "error": str | None,     # 单题失败时填错误摘要
        }
    """
    settings = get_settings()

    # 加载 KB 用于三层配置合并；多 KB 时取第一个（本期限制）
    kb_obj: KnowledgeBase | None = None
    if kb_ids:
        kb_obj = await db.get(KnowledgeBase, kb_ids[0])

    # 构造 QueryOptions：只把请求里有的字段透传，保持其余 None 让 resolve_options 走默认
    options = options or {}
    qo = QueryOptions(
        top_k=options.get("top_k"),
        reranker_enable=options.get("reranker_enable"),
        bm25_enable=options.get("bm25_enable"),
        query_rewrite=options.get("query_rewrite"),
        enable_graph_rag=options.get("enable_graph_rag"),
        similarity_threshold=options.get("similarity_threshold"),
        # 评估期不调 faithfulness（ragas 自己会算）
        enable_faithfulness_check=False,
    )

    try:
        resolved = resolve_options(options=qo, kb=kb_obj, settings=settings)
    except Exception as e:  # noqa: BLE001
        logger.warning("eval_runner: resolve_options 失败 query=%r err=%s", query[:60], e)
        return {
            "answer": "（生成失败：配置解析错误）",
            "contexts": [],
            "source_citations": [],
            "error": f"resolve_options: {type(e).__name__}: {e}",
        }

    # KB-06 contextvar：让 hybrid_search 内部按 kb_ids 限定 collection
    kb_ids_token = set_current_kb_ids(kb_ids)
    try:
        # ── Query 改写（HRE-01）──
        try:
            rewrite_result = await rewrite_query(query, resolved.query_rewrite)
        except Exception as e:  # noqa: BLE001
            logger.warning("eval_runner: rewrite 软失败 → noop err=%s", e)
            from app.rag.query_rewriter import RewriteResult

            rewrite_result = RewriteResult(strategy="none", rewritten_text=query, sub_queries=[])

        # ── Query NER + 图谱锚定（HRE-02）──
        entity_tags: list[str] = []
        if resolved.enable_graph_rag:
            try:
                ner_entities = await extract_query_entities(query)
                if ner_entities:
                    kb_ids_str = [str(k) for k in kb_ids] if kb_ids else None
                    entity_tags = await anchor_to_graph(ner_entities, kb_ids_str)
            except Exception as e:  # noqa: BLE001
                logger.warning("eval_runner: NER/anchor 软失败 → 忽略 err=%s", e)

        # ── 混合检索 ──
        # multi_query 路径不在评估期启用（多路 LLM 改写每题烧 2~4 次 token，性价比差）；
        # 强制 single 路径以 rewritten_text 或原 query 跑
        search_text = rewrite_result.rewritten_text or query
        try:
            results = await hybrid_search(
                query=search_text,
                top_k=resolved.top_k,
                entity_tags=entity_tags or None,
                reranker_enable=resolved.reranker_enable,
                similarity_threshold=resolved.similarity_threshold,
            )
        except Exception as e:  # noqa: BLE001
            logger.error("eval_runner: hybrid_search 失败 query=%r err=%s", query[:60], e)
            return {
                "answer": "（生成失败：检索失败）",
                "contexts": [],
                "source_citations": [],
                "error": f"hybrid_search: {type(e).__name__}: {e}",
            }

        # 检索为空：直接跳过 LLM，answer 标"无相关内容"，让 ragas 给低分
        if not results:
            return {
                "answer": "（未检索到相关内容）",
                "contexts": [],
                "source_citations": [],
                "error": None,
            }

        # ── 提取 contexts（给 ragas）+ 构造 citation chunks（给 generate_answer）──
        contexts: list[str] = [r.content or "" for r in results]
        chunks_for_citation = [
            {
                "document_name": (r.metadata or {}).get("filename", r.document_id),
                "page_number": r.page_number,
                "content": r.content,
                "chunk_id": r.chunk_id,
                "heading_path": r.heading_path,
                "rerank_score": r.score,
            }
            for r in results
        ]

        # ── 构 context + LLM 生成 ──
        context = build_context_with_citation(chunks_for_citation)
        try:
            answer = await generate_answer(
                query=query,
                context=context,
                session_id=None,
                db=db,
            )
        except Exception as e:  # noqa: BLE001
            logger.error("eval_runner: generate_answer 失败 query=%r err=%s", query[:60], e)
            return {
                "answer": "（生成失败：LLM 调用错误）",
                "contexts": contexts,
                "source_citations": chunks_for_citation,
                "error": f"generate_answer: {type(e).__name__}: {e}",
            }

        return {
            "answer": answer,
            "contexts": contexts,
            "source_citations": chunks_for_citation,
            "error": None,
        }

    finally:
        reset_current_kb_ids(kb_ids_token)


__all__ = ["run_single_query_for_eval"]

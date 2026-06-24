"""三层配置合并（HRE-06）。

PRD §HRE-06 要求：检索行为通过统一的配置结构控制，支持 KB 级默认值 + API 单次覆盖。
合并优先级：

    API options（QueryOptions）  >  kb.retrieval_config（JSONB）  >  全局 settings

任一上层字段为 None / 缺失时回落下一层。本模块只做合并，不做 LLM/IO；纯函数好测。

调用方：[app/api/v2/endpoints/query.py](../api/v2/endpoints/query.py) 的 v2_query 入口。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.api import error_codes
from app.api.exceptions import BusinessError
from app.core.config import Settings
from app.models.knowledge_base import KnowledgeBase
from app.schemas.v2.query import VALID_QUERY_REWRITE, QueryOptions

# ──────────────── 默认值（最底层兜底） ────────────────

# 当 API / KB / settings 三层都没给时使用；不放进 Settings 是因为这些值
# 与 PRD §HRE-06 表中的"默认值"绑定，属于业务常量而非环境配置
_FALLBACK_TOP_K = 5
_FALLBACK_RERANK_TOP_N = 30
_FALLBACK_SIMILARITY_THRESHOLD = 0.3


@dataclass(frozen=True)
class ResolvedRetrievalOptions:
    """三层合并后的最终生效配置，下游模块只读该结构，不再回查 settings / kb。"""

    top_k: int
    similarity_threshold: float
    bm25_enable: bool
    reranker_enable: bool
    query_rewrite: str  # "none" / "hyde" / "multi_query"
    enable_graph_rag: bool
    enable_faithfulness_check: bool  # 答案自检（CHC-04）
    rrf_k: int
    rerank_top_n: int


def _pick(
    api_value,
    kb_config: dict | None,
    kb_key: str,
    settings_default,
):
    """三层取值：api_value 非 None 就用；否则查 kb_config[kb_key]；否则 settings_default。

    注意区分 None 和 False/0/[]：API/KB 未传字段才回落，显式传 False/0 等假值要尊重。
    """
    if api_value is not None:
        return api_value
    if kb_config and kb_key in kb_config and kb_config[kb_key] is not None:
        return kb_config[kb_key]
    return settings_default


def resolve_options(
    *,
    options: QueryOptions,
    kb: KnowledgeBase | None,
    settings: Settings,
) -> ResolvedRetrievalOptions:
    """三层合并入口。

    Args:
        options: API 入参；任一字段为 None 表示「跟随下层」
        kb: 当前查询绑定的 KB（多 KB 时本期取第一个；后续按需演进）；None 表示无 KB
        settings: 全局 Settings 单例

    Raises:
        BusinessError(QUERY_REWRITE_INVALID): 任一层提供的 query_rewrite 不在
            ("none","hyde","multi_query") 枚举内（HRE-01 / PRD §1127 → 40011）。
    """
    kb_cfg: dict | None = kb.retrieval_config if kb is not None else None

    # bm25 / reranker 的 enable 三层默认：API > KB > settings
    bm25_enable = _pick(options.bm25_enable, kb_cfg, "bm25_enable", settings.bm25_enable)
    reranker_enable = _pick(
        options.reranker_enable,
        kb_cfg,
        "reranker_enable",
        # settings 没有独立的 reranker_enable 字段；用 reranker_type != "none" 推断
        settings.reranker_type != "none",
    )

    # query_rewrite 默认走 settings.query_rewrite_default（默认 "none"）
    query_rewrite = _pick(
        options.query_rewrite,
        kb_cfg,
        "query_rewrite",
        settings.query_rewrite_default,
    )
    # 再次校验枚举：API 层已在 schema 把住，KB 层是 JSONB 自由写入，必须再卡一次
    if query_rewrite not in VALID_QUERY_REWRITE:
        raise BusinessError(
            error_codes.QUERY_REWRITE_INVALID,
            f"query_rewrite 参数值不在枚举范围内：{VALID_QUERY_REWRITE}（实际：{query_rewrite!r}）",
        )

    enable_graph_rag = _pick(
        options.enable_graph_rag,
        kb_cfg,
        "enable_graph_rag",
        settings.graph_rag_enable,
    )

    # 答案自检（CHC-04）
    enable_faithfulness_check = _pick(
        options.enable_faithfulness_check,
        kb_cfg,
        "enable_faithfulness_check",
        settings.faithfulness_check_default,
    )

    top_k = _pick(options.top_k, kb_cfg, "top_k", _FALLBACK_TOP_K)
    rerank_top_n = _pick(None, kb_cfg, "rerank_top_n", _FALLBACK_RERANK_TOP_N)
    similarity_threshold = _pick(
        options.similarity_threshold,
        kb_cfg,
        "similarity_threshold",
        settings.reranker_similarity_threshold,
    )

    return ResolvedRetrievalOptions(
        top_k=int(top_k),
        similarity_threshold=float(similarity_threshold),
        bm25_enable=bool(bm25_enable),
        reranker_enable=bool(reranker_enable),
        query_rewrite=str(query_rewrite),
        enable_graph_rag=bool(enable_graph_rag),
        enable_faithfulness_check=bool(enable_faithfulness_check),
        rrf_k=int(settings.rrf_k),
        rerank_top_n=int(rerank_top_n),
    )


__all__ = ["ResolvedRetrievalOptions", "resolve_options"]

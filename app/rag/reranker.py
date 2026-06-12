"""V2.0 Reranker 精排模块（HRE-05）。

核心类：
- BaseReranker：抽象基类
- LiteLLMReranker：通过 LiteLLM 走在线 Reranker API（推荐起步）
- NoopReranker：开发期跳过（reranker_type=none）

设计要点：
- reranker_type=none 时走 NoopReranker，零开销
- LiteLLMReranker 失败时降级返回原顺序（HRE-05 降级策略）
- 过滤分数 < similarity_threshold 的低相关 chunk
- 过滤后剩余 < 3 时保留 top-3 不截断（PRD 兜底规则）
- 并发控制：API 调用加 Semaphore 限制

配置项（app/core/config.py T0.1 已加）：
- RERANKER_TYPE：none / api
- RERANKER_MODEL：默认 BAAI/bge-reranker-v2-m3
- RERANKER_API_KEY / RERANKER_API_BASE
- RERANKER_SIMILARITY_THRESHOLD：默认 0.3
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.core.config import get_settings
import litellm

logger = logging.getLogger(__name__)


# ──────────────── 数据类 ────────────────


@dataclass
class RerankResult:
    """Reranker 返回的单条结果。"""

    index: int  # 原始 chunks 中的索引
    relevance_score: float  # 精排分数
    content: str = ""  # 方便后续使用
    document_id: str = ""
    heading_path: list[str] | None = None
    block_type: str = ""
    page_number: int | None = None
    entity_tags: list[str] | None = None
    metadata: dict | None = None


# ──────────────── 抽象基类 ────────────────


class BaseReranker(ABC):
    """Reranker 抽象基类。"""

    @abstractmethod
    async def rerank(
        self,
        query: str,
        chunks: list[dict],
        top_k: int = 5,
    ) -> list[RerankResult]:
        """对检索结果做精排。

        Args:
            query: 用户查询文本
            chunks: 检索返回的 chunk 列表，每个 dict 含 content / document_id 等
            top_k: 精排后返回的最大数量

        Returns:
            RerankResult 列表，按 relevance_score 降序
        """
        ...


# ──────────────── NoopReranker ────────────────


class NoopReranker(BaseReranker):
    """空 Reranker（开发期跳过），直接返原顺序。"""

    async def rerank(
        self,
        query: str,
        chunks: list[dict],
        top_k: int = 5,
    ) -> list[RerankResult]:
        results = []
        for i, chunk in enumerate(chunks[:top_k]):
            results.append(
                RerankResult(
                    index=i,
                    relevance_score=1.0,  # 不做精排，给满分表示"信任原排序"
                    content=chunk.get("content", ""),
                    document_id=chunk.get("document_id", ""),
                    heading_path=chunk.get("heading_path"),
                    block_type=chunk.get("block_type", ""),
                    page_number=chunk.get("page_number"),
                    entity_tags=chunk.get("entity_tags"),
                    metadata=chunk.get("metadata"),
                )
            )
        return results


# ──────────────── LiteLLMReranker ────────────────


class LiteLLMReranker(BaseReranker):
    """通过 LiteLLM 走在线 Reranker API。

    优先 SiliconFlow BAAI/bge-reranker-v2-m3。
    失败时降级返回原顺序（HRE-05 降级策略）。
    """

    # 并发控制：避免 API 限流
    _semaphore = asyncio.Semaphore(5)

    def __init__(self):
        settings = get_settings()
        self.model = settings.reranker_model or "BAAI/bge-reranker-v2-m3"
        self.api_key = settings.reranker_api_key
        self.api_base = settings.reranker_api_base
        self.similarity_threshold = settings.reranker_similarity_threshold

    async def rerank(
        self,
        query: str,
        chunks: list[dict],
        top_k: int = 5,
    ) -> list[RerankResult]:
        async with self._semaphore:
            try:
                return await self._do_rerank(query, chunks, top_k)
            except Exception as e:
                # 降级策略：失败时返回原顺序，记日志
                logger.warning(
                    "Reranker 调用失败（降级返回原顺序）: %s", e
                )
                return self._fallback(chunks, top_k)

    async def _do_rerank(
        self,
        query: str,
        chunks: list[dict],
        top_k: int,
    ) -> list[RerankResult]:
        """调用 LiteLLM rerank API。"""
        documents = [chunk.get("content", "") for chunk in chunks]

        # LiteLLM rerank API（兼容 Cohere / Jina / SiliconFlow 等格式）
        response = await litellm.arerank(
            model=self.model,
            query=query,
            documents=documents,
            top_n=min(top_k, len(documents)),
            api_key=self.api_key,
            api_base=self.api_base,
        )

        # 解析响应
        results = []
        for item in response.results:
            idx = item.index
            score = item.relevance_score

            # 过滤低分 chunk
            if score < self.similarity_threshold:
                continue

            chunk = chunks[idx] if idx < len(chunks) else {}
            results.append(
                RerankResult(
                    index=idx,
                    relevance_score=score,
                    content=chunk.get("content", ""),
                    document_id=chunk.get("document_id", ""),
                    heading_path=chunk.get("heading_path"),
                    block_type=chunk.get("block_type", ""),
                    page_number=chunk.get("page_number"),
                    entity_tags=chunk.get("entity_tags"),
                    metadata=chunk.get("metadata"),
                )
            )

        # PRD 兜底规则：过滤后剩余 < 3 时保留 top-3 不截断
        if len(results) < 3 and len(chunks) > 0:
            logger.info(
                "Reranker 过滤后仅剩 %d 条（< 3），保留 top-3",
                len(results),
            )
            # 补充到 3 条
            existing_indices = {r.index for r in results}
            for i, chunk in enumerate(chunks):
                if i in existing_indices:
                    continue
                if len(results) >= 3:
                    break
                results.append(
                    RerankResult(
                        index=i,
                        relevance_score=0.0,  # 降级补充，分数标 0
                        content=chunk.get("content", ""),
                        document_id=chunk.get("document_id", ""),
                        heading_path=chunk.get("heading_path"),
                        block_type=chunk.get("block_type", ""),
                        page_number=chunk.get("page_number"),
                        entity_tags=chunk.get("entity_tags"),
                        metadata=chunk.get("metadata"),
                    )
                )

        # 按 relevance_score 降序
        results.sort(key=lambda r: r.relevance_score, reverse=True)
        return results[:top_k]

    @staticmethod
    def _fallback(chunks: list[dict], top_k: int) -> list[RerankResult]:
        """降级：返回原顺序结果。"""
        results = []
        for i, chunk in enumerate(chunks[:top_k]):
            results.append(
                RerankResult(
                    index=i,
                    relevance_score=0.0,  # 降级标记
                    content=chunk.get("content", ""),
                    document_id=chunk.get("document_id", ""),
                    heading_path=chunk.get("heading_path"),
                    block_type=chunk.get("block_type", ""),
                    page_number=chunk.get("page_number"),
                    entity_tags=chunk.get("entity_tags"),
                    metadata=chunk.get("metadata"),
                )
            )
        return results


# ──────────────── 工厂函数 ────────────────


def get_reranker() -> BaseReranker:
    """根据配置创建 Reranker 实例。"""
    settings = get_settings()
    reranker_type = (settings.reranker_type or "none").lower()

    if reranker_type == "api":
        return LiteLLMReranker()
    else:
        return NoopReranker()


__all__ = [
    "BaseReranker",
    "LiteLLMReranker",
    "NoopReranker",
    "RerankResult",
    "get_reranker",
]

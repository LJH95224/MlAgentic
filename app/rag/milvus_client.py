"""Milvus 客户端单例 + 启动期初始化（RAG-01）。

设计要点：
- 单例 MilvusClient，全进程共享，避免重复建连。
- 启动时调用 init_milvus()：连接 + 幂等检测/创建 Collection 与索引 + load_collection。
- 关闭时调用 close_milvus()：释放连接。
- 不在模块顶层 import pymilvus，便于无 Milvus 环境下的单测/类型检查（pyright/mypy）只跑纯逻辑模块。

并发说明：
- MilvusClient 本身是同步 API（gRPC），单例线程安全（pymilvus 官方文档保证）。
- 检索是只读 + 短耗时操作，直接同步调用即可，不阻塞事件循环。
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.rag.naming import build_kb_collection_name
from app.rag.schema import (
    build_index_params,
    build_kb_collection_schema,
    build_knowledge_chunks_schema,
    build_v2_index_params,
    build_v2_kb_collection_schema,
)

if TYPE_CHECKING:
    from pymilvus import MilvusClient

logger = logging.getLogger(__name__)


# 进程级单例。get_milvus_client() 首次调用前必须先调 init_milvus()。
_client: "MilvusClient | None" = None


def init_milvus() -> "MilvusClient":
    """连接 Milvus，幂等创建 Collection 与索引，加载到内存。

    重复调用安全：
      - 已建连：直接返回单例
      - Collection 已存在：跳过创建，直接 load

    Raises:
        RuntimeError: 连接失败时透传 PyMilvus 异常并附环境信息
    """
    global _client

    if _client is not None:
        return _client

    # 局部导入，避免无 pymilvus 环境时模块加载失败
    from pymilvus import MilvusClient

    settings = get_settings()

    logger.info(
        "Milvus 连接初始化: uri=%s collection=%s dim=%d",
        settings.milvus_uri,
        settings.milvus_collection,
        settings.embedding_dimension,
    )

    try:
        _client = MilvusClient(
            uri=settings.milvus_uri,
            token=settings.milvus_token or "",
        )
    except Exception as e:
        # 不吞异常 —— 启动期连不上 Milvus 应该让应用直接挂掉
        raise RuntimeError(
            f"Milvus 连接失败 uri={settings.milvus_uri}：{e}"
        ) from e

    collection_name = settings.milvus_collection

    if _client.has_collection(collection_name):
        logger.info("Collection '%s' 已存在，跳过创建", collection_name)
    else:
        logger.info("Collection '%s' 不存在，开始创建 Schema + HNSW 索引", collection_name)
        schema = build_knowledge_chunks_schema(dim=settings.embedding_dimension)
        index_params = build_index_params(_client)

        _client.create_collection(
            collection_name=collection_name,
            schema=schema,
            index_params=index_params,
        )
        logger.info("Collection '%s' 创建完成", collection_name)

    # load_collection 是幂等的（已加载时直接返回）
    _client.load_collection(collection_name)
    logger.info("Collection '%s' 已加载到内存，可检索", collection_name)

    return _client


def get_milvus_client() -> "MilvusClient":
    """获取已初始化的 MilvusClient 单例。

    Raises:
        RuntimeError: 调用前未执行 init_milvus()
    """
    if _client is None:
        raise RuntimeError(
            "Milvus 客户端尚未初始化。请先在应用启动时调用 init_milvus()。"
        )
    return _client


def close_milvus() -> None:
    """释放 Milvus 连接。应用关闭时调用，幂等。"""
    global _client
    if _client is None:
        return

    try:
        _client.close()
    except Exception as e:  # noqa: BLE001
        # 关闭阶段静默记录，不阻断 lifespan
        logger.warning("Milvus 关闭异常（已忽略）：%s", e)
    finally:
        _client = None
        logger.info("Milvus 连接已释放")


# ──────────────── V1.5 多 KB Collection 生命周期 ────────────────


def create_kb_collection(
    kb_id: uuid.UUID | str,
    dim: int | None = None,
) -> str:
    """为指定知识库创建独立 Milvus Collection（V1.5 KB-01）。

    同步完成：
      1. 创建 Collection（schema 见 build_kb_collection_schema）
      2. 建 HNSW 向量索引 + INVERTED document_id 索引
      3. load_collection 加载到内存

    幂等：Collection 已存在 → 直接 load 并返回 collection name；不报错。

    Args:
        kb_id: 知识库 UUID
        dim: 向量维度；缺省使用 settings.embedding_dimension

    Returns:
        Collection 名（形如 "kb_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"）

    Raises:
        RuntimeError: Milvus 未初始化 / 建 Collection 失败
    """
    client = get_milvus_client()
    settings = get_settings()
    effective_dim = dim if dim is not None else settings.embedding_dimension

    collection_name = build_kb_collection_name(kb_id)

    if client.has_collection(collection_name):
        logger.info("KB Collection '%s' 已存在，直接 load", collection_name)
    else:
        logger.info(
            "KB Collection '%s' 不存在，创建 Schema(dim=%d) + HNSW 索引",
            collection_name,
            effective_dim,
        )
        schema = build_kb_collection_schema(dim=effective_dim)
        index_params = build_index_params(client)

        try:
            client.create_collection(
                collection_name=collection_name,
                schema=schema,
                index_params=index_params,
            )
        except Exception as e:
            # 创建失败要让上层回滚 PG（kb_service 里 try/except 捕捉）
            raise RuntimeError(
                f"创建 KB Collection 失败 name={collection_name}: {e}"
            ) from e

        logger.info("KB Collection '%s' 创建完成", collection_name)

    # load_collection 幂等
    client.load_collection(collection_name)
    logger.info("KB Collection '%s' 已加载到内存", collection_name)

    return collection_name


def drop_kb_collection(kb_id: uuid.UUID | str) -> bool:
    """删除指定知识库的 Milvus Collection（V1.5 KB-05）。

    不可逆操作。建议调用方先在业务层做二次确认。
    幂等：Collection 不存在 → 直接返回 False；存在 → drop 后返回 True。

    Args:
        kb_id: 知识库 UUID

    Returns:
        True 表示真删了 / False 表示本来就不存在

    Raises:
        RuntimeError: Milvus 未初始化 / drop 失败
    """
    client = get_milvus_client()
    collection_name = build_kb_collection_name(kb_id)

    if not client.has_collection(collection_name):
        logger.warning("KB Collection '%s' 不存在，drop 跳过", collection_name)
        return False

    try:
        # release 是 drop 的前置（PyMilvus 2.6+ drop 内部会自动 release，
        # 这里显式 release 一遍更直观、出错时定位更容易）
        try:
            client.release_collection(collection_name)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "KB Collection '%s' release 失败（继续 drop）: %s",
                collection_name,
                e,
            )
        client.drop_collection(collection_name)
    except Exception as e:
        raise RuntimeError(
            f"删除 KB Collection 失败 name={collection_name}: {e}"
        ) from e

    logger.info("KB Collection '%s' 已删除", collection_name)
    return True


def kb_collection_exists(kb_id: uuid.UUID | str) -> bool:
    """检查指定 KB 的 Collection 是否存在（调试 / 健康检查用）。"""
    client = get_milvus_client()
    return client.has_collection(build_kb_collection_name(kb_id))


# ──────────────── V2.0 KB Collection 生命周期 ────────────────


def create_v2_kb_collection(
    kb_id: uuid.UUID | str,
    dim: int | None = None,
) -> str:
    """为指定知识库创建 V2.0 版 Milvus Collection（T0.3）。

    V2.0 与 V1.5 的差异：
    - Schema 新增 7 个字段（heading_path / block_type / page_number / position_index /
      parent_chunk_id / is_summary / sparse_vector）
    - 索引新增 SPARSE_INVERTED_INDEX + BM25（用于混合检索）

    同步完成：
      1. 创建 Collection（schema 见 build_v2_kb_collection_schema）
      2. 建 HNSW 稠密向量索引 + SPARSE_INVERTED_INDEX BM25 稀疏向量索引 + INVERTED document_id 索引
      3. load_collection 加载到内存

    幂等：Collection 已存在 → 直接 load 并返回 collection name；不报错。

    Args:
        kb_id: 知识库 UUID
        dim: 向量维度；缺省使用 settings.embedding_dimension

    Returns:
        Collection 名（形如 "kb_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"）

    Raises:
        RuntimeError: Milvus 未初始化 / 建 Collection 失败
    """
    client = get_milvus_client()
    settings = get_settings()
    effective_dim = dim if dim is not None else settings.embedding_dimension

    collection_name = build_kb_collection_name(kb_id)

    if client.has_collection(collection_name):
        logger.info("V2 KB Collection '%s' 已存在，直接 load", collection_name)
    else:
        logger.info(
            "V2 KB Collection '%s' 不存在，创建 V2 Schema(dim=%d) + 索引",
            collection_name,
            effective_dim,
        )
        schema = build_v2_kb_collection_schema(dim=effective_dim)
        index_params = build_v2_index_params(client)

        try:
            client.create_collection(
                collection_name=collection_name,
                schema=schema,
                index_params=index_params,
            )
        except Exception as e:
            raise RuntimeError(
                f"创建 V2 KB Collection 失败 name={collection_name}: {e}"
            ) from e

        logger.info("V2 KB Collection '%s' 创建完成", collection_name)

    # load_collection 幂等
    client.load_collection(collection_name)
    logger.info("V2 KB Collection '%s' 已加载到内存", collection_name)

    return collection_name


__all__ = [
    "init_milvus",
    "get_milvus_client",
    "close_milvus",
    # V1.5 KB Collection 生命周期
    "create_kb_collection",
    "drop_kb_collection",
    "kb_collection_exists",
    # V2.0 KB Collection 生命周期
    "create_v2_kb_collection",
]

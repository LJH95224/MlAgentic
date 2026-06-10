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
from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.rag.schema import build_index_params, build_knowledge_chunks_schema

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


__all__ = ["init_milvus", "get_milvus_client", "close_milvus"]

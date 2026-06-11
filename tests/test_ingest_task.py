"""V1.5 S3.2 ingest_task 单测（mock 所有外部依赖，验证管道逻辑）。

覆盖：
- _classify_retryable 异常分类
- _make_chunk_id 稳定性 + INT64 正数
- _main 七步管道每个阶段：parse / split / embed / milvus_write / ner / neo4j_write / progress
- task 入口：成功路径返结构、不可重试异常落 failed、可重试异常进 retry

不连真 PG / Milvus / Neo4j；用 monkeypatch + AsyncMock 替换所有 IO。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingest.parser import ParseError
from app.ingest.splitter import Chunk
from app.models.kb_file import (
    FILE_STATUS_COMPLETED,
    FILE_STATUS_FAILED,
    FILE_STATUS_PROCESSING,
    KbFile,
)
from app.models.knowledge_base import KnowledgeBase
from app.tasks import ingest_task


# ──────────────── 工具函数测试 ────────────────


def test_classify_retryable_value_error_not_retryable():
    assert ingest_task._classify_retryable(ValueError("x")) is False


def test_classify_retryable_parse_error_not_retryable():
    assert ingest_task._classify_retryable(ParseError("x")) is False


def test_classify_retryable_file_not_found_not_retryable():
    assert ingest_task._classify_retryable(FileNotFoundError("x")) is False


def test_classify_retryable_milvus_exception_retryable():
    class MilvusException(Exception):
        pass

    assert ingest_task._classify_retryable(MilvusException()) is True


def test_classify_retryable_timeout_retryable():
    assert ingest_task._classify_retryable(TimeoutError("x")) is True


def test_classify_retryable_unknown_default_not_retryable():
    """未知异常默认不重试（避免无限重试烧 worker）。"""

    class Weird(Exception):
        pass

    assert ingest_task._classify_retryable(Weird()) is False


def test_make_chunk_id_stable_for_same_input():
    a = ingest_task._make_chunk_id("doc-A", 5)
    b = ingest_task._make_chunk_id("doc-A", 5)
    assert a == b


def test_make_chunk_id_different_for_different_inputs():
    assert ingest_task._make_chunk_id("doc-A", 1) != ingest_task._make_chunk_id(
        "doc-A", 2
    )
    assert ingest_task._make_chunk_id("doc-A", 1) != ingest_task._make_chunk_id(
        "doc-B", 1
    )


def test_make_chunk_id_is_positive_int64():
    for i in range(50):
        cid = ingest_task._make_chunk_id(f"doc-{i}", i)
        assert isinstance(cid, int)
        assert cid > 0
        assert cid < 2**63


# ──────────────── _main 七步管道 ────────────────


def _make_file(file_id, kb_id) -> KbFile:
    f = KbFile(
        id=file_id,
        kb_id=kb_id,
        filename="sample.txt",
        file_path="/tmp/x/sample.txt",
        file_size=100,
        mime_type="text/plain",
        status="pending",
        progress=0,
        chunk_count=0,
        entity_count=0,
    )
    f.created_at = datetime.now(timezone.utc)
    return f


def _make_kb(kb_id) -> KnowledgeBase:
    return KnowledgeBase(
        id=kb_id,
        name="测试库",
        embedding_dim=4096,
        chunk_size=512,
        chunk_overlap=64,
        status="active",
        file_count=1,
        chunk_count=0,
    )


@pytest.fixture
def patched_resources(monkeypatch):
    """替换 task_resources 为 mock 上下文管理器，所有 IO 都可断言。"""
    from contextlib import asynccontextmanager

    mock_milvus = MagicMock()
    mock_milvus.has_collection = MagicMock(return_value=True)
    mock_milvus.upsert = MagicMock()
    mock_milvus.delete = MagicMock()

    mock_neo4j = MagicMock()
    # neo4j_driver.session(...) 是上下文管理器，返 session
    mock_session = MagicMock()
    mock_session.run = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_neo4j.session = MagicMock(return_value=mock_session)

    # db() 工厂返一个 session
    mock_db_session = MagicMock()
    mock_db_session.execute = AsyncMock()
    mock_db_session.commit = AsyncMock()
    # SQLAlchemy 查询结果
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock()
    mock_result.scalar_one = MagicMock()
    mock_db_session.execute.return_value = mock_result

    @asynccontextmanager
    async def _db_factory():
        yield mock_db_session

    mock_resources = MagicMock()
    mock_resources.milvus = mock_milvus
    mock_resources.neo4j = mock_neo4j
    mock_resources.db = _db_factory

    @asynccontextmanager
    async def _fake_task_resources():
        yield mock_resources

    monkeypatch.setattr(ingest_task, "task_resources", _fake_task_resources)
    return mock_resources, mock_db_session, mock_result


@pytest.fixture
def patched_pipeline(monkeypatch):
    """替换七步管道的 IO 函数：parse / embed / NER。

    切片器走真实 split_text（验真实分隔逻辑）。
    """
    # parse_document 返一段长中文文本
    parse_mock = MagicMock(return_value="第一段。\n\n第二段。\n\n" + "x" * 500)
    monkeypatch.setattr(ingest_task, "parse_document", parse_mock)

    # aembed_texts 按输入长度造对应数量的伪向量
    async def _fake_embed(texts):
        return [[0.1] * 4096 for _ in texts]

    monkeypatch.setattr(ingest_task, "aembed_texts", _fake_embed)

    # NER：第 0 个 chunk 返 1 个实体，其他空，方便验软失败 + entity_count
    async def _fake_safe_ner_step(chunks):
        result = []
        for i, _ in enumerate(chunks):
            if i == 0:
                result.append([{"name": "北京", "type": "LOCATION"}])
            else:
                result.append([])
        return result

    monkeypatch.setattr(ingest_task, "_step_ner", _fake_safe_ner_step)

    return parse_mock


async def test_main_happy_path(patched_resources, patched_pipeline):
    """七步管道全成功 → status=completed + progress=100 + chunk/entity 计数。"""
    mock_resources, mock_db, mock_result = patched_resources

    file_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    file_record = _make_file(file_id, kb_id)
    kb = _make_kb(kb_id)
    # _load_file_record 内部连查两次（kb_files + knowledge_bases）
    mock_result.scalar_one_or_none.side_effect = [file_record, kb]

    result = await ingest_task._main(str(file_id), str(kb_id))

    assert result["file_id"] == str(file_id)
    assert result["status"] == FILE_STATUS_COMPLETED
    assert result["chunk_count"] > 0
    # NER 命中 1 个实体（北京）
    assert result["entity_count"] == 1

    # Milvus.upsert 被调
    mock_resources.milvus.upsert.assert_called()
    # 拿出最后一次 upsert 的 rows，验字段结构
    upsert_calls = mock_resources.milvus.upsert.call_args_list
    last_rows = upsert_calls[-1].kwargs["data"]
    assert len(last_rows) > 0
    row = last_rows[0]
    assert "chunk_id" in row
    assert "vector" in row and len(row["vector"]) == 4096
    assert "document_id" in row and row["document_id"] == str(file_id)
    assert "kb_id" in row and row["kb_id"] == str(kb_id)
    assert "allowed_roles" in row
    assert row["allowed_roles"] == ["ALL"]
    assert "entity_tags" in row

    # Neo4j session.run 被调（Document upsert + entity bulk + link bulk）
    assert mock_resources.neo4j.session.called


async def test_main_load_file_record_not_found(patched_resources):
    """file_id 不存在 → raise ValueError → 不可重试。"""
    mock_resources, mock_db, mock_result = patched_resources

    mock_result.scalar_one_or_none.side_effect = [None]

    with pytest.raises(ValueError, match="不存在"):
        await ingest_task._main(str(uuid.uuid4()), str(uuid.uuid4()))


async def test_main_kb_id_mismatch_raises(patched_resources):
    """传入 kb_id 与文件实际 kb_id 不一致 → ValueError。"""
    mock_resources, mock_db, mock_result = patched_resources

    file_id = uuid.uuid4()
    real_kb_id = uuid.uuid4()
    other_kb_id = uuid.uuid4()
    file_record = _make_file(file_id, real_kb_id)
    kb = _make_kb(real_kb_id)
    mock_result.scalar_one_or_none.side_effect = [file_record, kb]

    with pytest.raises(ValueError, match="不一致"):
        await ingest_task._main(str(file_id), str(other_kb_id))


async def test_main_empty_text_raises_parse_error(
    patched_resources, monkeypatch
):
    """parse 返空文本 → ParseError → 不可重试。"""
    mock_resources, mock_db, mock_result = patched_resources

    file_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    file_record = _make_file(file_id, kb_id)
    kb = _make_kb(kb_id)
    mock_result.scalar_one_or_none.side_effect = [file_record, kb]

    # parse 返全空白
    monkeypatch.setattr(ingest_task, "parse_document", MagicMock(return_value="   "))

    with pytest.raises(ParseError, match="为空"):
        await ingest_task._main(str(file_id), str(kb_id))


async def test_main_neo4j_failure_is_soft(patched_resources, patched_pipeline):
    """Neo4j 写入失败 → 整体仍 completed，entity_count=0（软失败）。"""
    mock_resources, mock_db, mock_result = patched_resources

    # 让 neo4j session.run 抛错
    mock_resources.neo4j.session().run.side_effect = RuntimeError("neo4j down")

    file_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    file_record = _make_file(file_id, kb_id)
    kb = _make_kb(kb_id)
    mock_result.scalar_one_or_none.side_effect = [file_record, kb]

    result = await ingest_task._main(str(file_id), str(kb_id))
    # 主链路完成
    assert result["status"] == FILE_STATUS_COMPLETED
    # Neo4j 失败 → entity_count 回退 0
    assert result["entity_count"] == 0


# ──────────────── Celery task 入口 ────────────────


def test_task_eager_happy_path(monkeypatch, patched_resources, patched_pipeline):
    """eager 模式跑 happy path：任务返字典 + status=completed。"""
    from app.tasks import celery_app

    mock_resources, mock_db, mock_result = patched_resources

    file_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    file_record = _make_file(file_id, kb_id)
    kb = _make_kb(kb_id)
    mock_result.scalar_one_or_none.side_effect = [file_record, kb]

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False
    try:
        res = ingest_task.parse_and_ingest_task.delay(str(file_id), str(kb_id))
        out = res.get(timeout=10)
        assert out["status"] == FILE_STATUS_COMPLETED
        assert out["file_id"] == str(file_id)
    finally:
        celery_app.conf.task_always_eager = False


def test_task_eager_unretryable_failure_marks_failed(monkeypatch, patched_resources):
    """不可重试异常 → 任务结果是 failed 字典 + 走 _mark_failed_safe。"""
    from app.tasks import celery_app

    mock_resources, mock_db, mock_result = patched_resources

    # 让 _load_file_record 抛 ValueError（不可重试）
    mock_result.scalar_one_or_none.side_effect = [None]

    # mock 掉 _mark_failed_safe，避免再走一次 task_resources（已被 mock 了，能跑通，
    # 但 _mark_failed_safe 内部新建 task_resources 上下文管理器调用次数难追，
    # 直接 stub 更清晰）
    async def _noop_mark(file_id, *, error_message):
        return None

    monkeypatch.setattr(ingest_task, "_mark_failed_safe", _noop_mark)

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False
    try:
        res = ingest_task.parse_and_ingest_task.delay(
            str(uuid.uuid4()), str(uuid.uuid4())
        )
        out = res.get(timeout=10)
        assert out["status"] == FILE_STATUS_FAILED
        assert "ValueError" in out["error"]
    finally:
        celery_app.conf.task_always_eager = False

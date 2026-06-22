"""A P1-11 reaper_task 单测（卡死 processing 文件回收）。

覆盖：
- _scan_stale_processing_files：扫表条件 status=processing + updated_at < cutoff
- _reap_one：调标准 _mark_failed_safe（含 Milvus / Neo4j 残留清理）
- _main：开关 / 空扫描 / 多条回收 / 单条失败不阻塞其他
- Celery beat schedule 注册正确
- updated_at 字段存在 + 通过 _set_progress onupdate 自动刷
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.tasks import reaper_task


# ──────────────── kb_files.updated_at 模型字段 ────────────────


def test_kbfile_has_updated_at_column():
    """模型必须显式声明 updated_at 字段；reaper 扫表依赖它。"""
    from app.models.kb_file import KbFile

    columns = {c.name for c in KbFile.__table__.columns}
    assert "updated_at" in columns, "KbFile 必须有 updated_at 字段（P1-11 心跳锚点）"


def test_kbfile_updated_at_has_server_default_and_onupdate():
    """server_default + onupdate 才能保证每次 update 都自动刷新。"""
    from app.models.kb_file import KbFile

    col = KbFile.__table__.columns["updated_at"]
    assert col.server_default is not None, "updated_at 缺 server_default（insert 时给默认）"
    assert col.onupdate is not None, "updated_at 缺 onupdate（update 时自动刷新）"
    assert col.nullable is False, "updated_at 不应可空"
    assert col.index is True, "updated_at 应建索引（回收扫表 WHERE updated_at < cutoff）"


# ──────────────── Celery 注册 / 调度 ────────────────


def test_reaper_task_registered():
    from app.tasks import celery_app

    assert "app.tasks.reaper_task.reap_stale_processing_files" in celery_app.tasks


def test_reaper_module_in_include_list():
    """worker 启动时必须能 import 到 reaper_task 模块。"""
    from app.tasks import celery_app

    assert "app.tasks.reaper_task" in celery_app.conf.include


def test_reaper_beat_schedule_registered():
    """beat_schedule 必须配 reaper 周期任务，否则 beat 跑不起来。"""
    from app.tasks import celery_app

    schedule_cfg = celery_app.conf.beat_schedule
    assert "reap-stale-processing-files" in schedule_cfg
    entry = schedule_cfg["reap-stale-processing-files"]
    assert entry["task"] == "app.tasks.reaper_task.reap_stale_processing_files"
    assert entry["schedule"].run_every.total_seconds() > 0


# ──────────────── _scan_stale_processing_files ────────────────


@pytest.fixture
def patched_task_resources(monkeypatch):
    """Mock task_resources，注入可控的 db session。返回 (mock_resources, mock_db_session)。"""
    mock_db_session = MagicMock()
    mock_db_session.execute = AsyncMock()
    mock_db_session.commit = AsyncMock()

    @asynccontextmanager
    async def _db_factory():
        yield mock_db_session

    mock_resources = MagicMock()
    mock_resources.db = _db_factory

    @asynccontextmanager
    async def _fake_task_resources():
        yield mock_resources

    monkeypatch.setattr(reaper_task, "task_resources", _fake_task_resources)
    return mock_resources, mock_db_session


@pytest.mark.asyncio
async def test_scan_returns_stale_rows(patched_task_resources):
    """扫表返回 (file_id, kb_id, updated_at) 三元组列表。"""
    _, mock_db = patched_task_resources

    fid, kid = uuid.uuid4(), uuid.uuid4()
    old_ts = datetime.now(timezone.utc) - timedelta(hours=1)
    row = MagicMock(id=fid, kb_id=kid, updated_at=old_ts)
    mock_result = MagicMock()
    mock_result.all = MagicMock(return_value=[row])
    mock_db.execute.return_value = mock_result

    rows = await reaper_task._scan_stale_processing_files(threshold_s=60)
    assert rows == [(fid, kid, old_ts)]


@pytest.mark.asyncio
async def test_scan_empty_when_no_stale(patched_task_resources):
    """没卡死文件 → 返回空列表（None 路径不会爆）。"""
    _, mock_db = patched_task_resources

    mock_result = MagicMock()
    mock_result.all = MagicMock(return_value=[])
    mock_db.execute.return_value = mock_result

    rows = await reaper_task._scan_stale_processing_files(threshold_s=60)
    assert rows == []


# ──────────────── _reap_one ────────────────


@pytest.mark.asyncio
async def test_reap_one_calls_mark_failed_safe(monkeypatch):
    """复用 ingest_task._mark_failed_safe，避免逻辑分叉。"""
    from app.tasks import ingest_task

    fake_mark = AsyncMock()
    monkeypatch.setattr(ingest_task, "_mark_failed_safe", fake_mark)

    fid, kid = uuid.uuid4(), uuid.uuid4()
    ts = datetime.now(timezone.utc) - timedelta(hours=1)

    ok = await reaper_task._reap_one(fid, kid, ts)
    assert ok is True
    fake_mark.assert_awaited_once()
    call_kwargs = fake_mark.call_args.kwargs
    # 第一个位置参数是 file_id（str）
    assert fake_mark.call_args.args[0] == str(fid)
    assert call_kwargs["kb_id"] == str(kid)
    # error_message 必须能让运维一眼看出是回收触发的
    assert "卡死回收" in call_kwargs["error_message"]
    assert ts.isoformat() in call_kwargs["error_message"]


@pytest.mark.asyncio
async def test_reap_one_returns_false_on_failure(monkeypatch):
    """_mark_failed_safe 内部抛错 → _reap_one 不抛，返 False，让上层继续扫下一个。"""
    from app.tasks import ingest_task

    async def _boom(*a, **kw):
        raise RuntimeError("DB down")

    monkeypatch.setattr(ingest_task, "_mark_failed_safe", _boom)

    ok = await reaper_task._reap_one(uuid.uuid4(), uuid.uuid4(), datetime.now(timezone.utc))
    assert ok is False


# ──────────────── _main 编排逻辑 ────────────────


@pytest.mark.asyncio
async def test_main_skipped_when_disabled(monkeypatch, patched_task_resources):
    """INGEST_REAPER_ENABLE=False 时直接返回 skipped=True，不扫不调。"""
    from app.core import config

    fake_settings = MagicMock()
    fake_settings.ingest_reaper_enable = False
    fake_settings.ingest_stale_timeout_s = 60
    monkeypatch.setattr(config, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(reaper_task, "get_settings", lambda: fake_settings)

    result = await reaper_task._main()
    assert result["skipped"] is True
    assert result["scanned"] == 0


@pytest.mark.asyncio
async def test_main_empty_scan_returns_zero(monkeypatch, patched_task_resources):
    """开关开 + 没卡死文件 → scanned=0 / reaped=0 / skipped=False。"""
    _, mock_db = patched_task_resources

    fake_settings = MagicMock()
    fake_settings.ingest_reaper_enable = True
    fake_settings.ingest_stale_timeout_s = 60
    monkeypatch.setattr(reaper_task, "get_settings", lambda: fake_settings)

    mock_result = MagicMock()
    mock_result.all = MagicMock(return_value=[])
    mock_db.execute.return_value = mock_result

    result = await reaper_task._main()
    assert result["skipped"] is False
    assert result["scanned"] == 0
    assert result["reaped"] == 0


@pytest.mark.asyncio
async def test_main_reaps_all_stale_files(monkeypatch, patched_task_resources):
    """多条卡死文件全部回收成功 → scanned == reaped。"""
    from app.tasks import ingest_task

    _, mock_db = patched_task_resources

    fake_settings = MagicMock()
    fake_settings.ingest_reaper_enable = True
    fake_settings.ingest_stale_timeout_s = 60
    monkeypatch.setattr(reaper_task, "get_settings", lambda: fake_settings)

    fid1, fid2 = uuid.uuid4(), uuid.uuid4()
    kid1, kid2 = uuid.uuid4(), uuid.uuid4()
    ts = datetime.now(timezone.utc) - timedelta(hours=2)
    row1 = MagicMock(id=fid1, kb_id=kid1, updated_at=ts)
    row2 = MagicMock(id=fid2, kb_id=kid2, updated_at=ts)
    mock_result = MagicMock()
    mock_result.all = MagicMock(return_value=[row1, row2])
    mock_db.execute.return_value = mock_result

    fake_mark = AsyncMock()
    monkeypatch.setattr(ingest_task, "_mark_failed_safe", fake_mark)

    result = await reaper_task._main()
    assert result["scanned"] == 2
    assert result["reaped"] == 2
    assert result["threshold_s"] == 60
    assert fake_mark.await_count == 2


@pytest.mark.asyncio
async def test_main_single_failure_does_not_block_others(monkeypatch, patched_task_resources):
    """单条 _mark_failed_safe 失败时，其他条仍会被处理；reaped < scanned。"""
    from app.tasks import ingest_task

    _, mock_db = patched_task_resources

    fake_settings = MagicMock()
    fake_settings.ingest_reaper_enable = True
    fake_settings.ingest_stale_timeout_s = 60
    monkeypatch.setattr(reaper_task, "get_settings", lambda: fake_settings)

    fid1, fid2 = uuid.uuid4(), uuid.uuid4()
    row1 = MagicMock(id=fid1, kb_id=uuid.uuid4(), updated_at=datetime.now(timezone.utc) - timedelta(hours=2))
    row2 = MagicMock(id=fid2, kb_id=uuid.uuid4(), updated_at=datetime.now(timezone.utc) - timedelta(hours=2))
    mock_result = MagicMock()
    mock_result.all = MagicMock(return_value=[row1, row2])
    mock_db.execute.return_value = mock_result

    call_count = {"n": 0}

    async def _flaky_mark(file_id, *, kb_id, error_message):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("first one boom")
        # 第二条成功

    monkeypatch.setattr(ingest_task, "_mark_failed_safe", _flaky_mark)

    result = await reaper_task._main()
    assert result["scanned"] == 2
    assert result["reaped"] == 1  # 只成功 1 条
    assert call_count["n"] == 2  # 失败也调用过


# ──────────────── Celery 任务入口（eager） ────────────────


@pytest.fixture
def eager_celery():
    from app.tasks import celery_app

    prev_eager = celery_app.conf.task_always_eager
    prev_prop = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    try:
        yield celery_app
    finally:
        celery_app.conf.task_always_eager = prev_eager
        celery_app.conf.task_eager_propagates = prev_prop


def test_reap_task_swallows_internal_exception(eager_celery, monkeypatch):
    """_main 抛异常时任务不应炸（下一周期重试），返回 error 字段供告警。"""

    async def _boom():
        raise RuntimeError("unexpected")

    monkeypatch.setattr(reaper_task, "_main", _boom)

    res = reaper_task.reap_stale_processing_files.delay()
    assert res.successful()
    assert res.result["scanned"] == 0
    assert "RuntimeError" in res.result["error"]

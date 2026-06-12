"""V1.5 S4 会话标题/摘要异步任务单测（mock 所有外部 IO）。

覆盖：
- _clean_title / _clean_summary：LLM 输出清洗
- _resolve_kwargs：模型 override / 厂商前缀推断
- _generate_title_main：跳过分支（title 已存在 / 消息不够 / session 不存在）+ happy path
- _generate_summary_main：跳过 / 超长 failed / happy path
- Celery 任务壳：异常包装

不连真 LLM / PG；用 monkeypatch + AsyncMock。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.session import ChatSession
from app.models.message import ChatMessage
from app.tasks import session_task


# ──────────────── 清洗工具 ────────────────


def test_clean_title_strips_quotes():
    assert session_task._clean_title('"测试标题"') == "测试标题"
    assert session_task._clean_title("'测试标题'") == "测试标题"
    # 中文引号
    assert session_task._clean_title("“测试标题”") == "测试标题"
    assert session_task._clean_title("「测试标题」") == "测试标题"


def test_clean_title_strips_markdown_fence():
    assert session_task._clean_title("```\n测试标题\n```") == "测试标题"
    assert session_task._clean_title("```json\n标题\n```") == "标题"


def test_clean_title_strips_trailing_punctuation():
    assert session_task._clean_title("测试标题。") == "测试标题"
    assert session_task._clean_title("测试标题！") == "测试标题"
    assert session_task._clean_title("测试标题?") == "测试标题"


def test_clean_title_truncates_to_20_chars():
    long = "气" * 30
    cleaned = session_task._clean_title(long)
    assert len(cleaned) == 20
    assert cleaned == "气" * 20


def test_clean_title_collapses_whitespace():
    assert session_task._clean_title("  多 空白   标题  ") == "多 空白 标题"


def test_clean_title_empty_returns_empty():
    assert session_task._clean_title("") == ""
    assert session_task._clean_title("   ") == ""


def test_clean_summary_truncates_to_200_chars():
    long = "气象" * 200
    cleaned = session_task._clean_summary(long)
    assert len(cleaned) == 200


def test_clean_summary_strips_fence():
    assert session_task._clean_summary("```\n摘要内容\n```") == "摘要内容"


# ──────────────── _resolve_kwargs ────────────────


def test_resolve_kwargs_uses_override(monkeypatch):
    monkeypatch.setenv("LITELLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-fake")
    monkeypatch.setenv("LITELLM_API_BASE", "https://api.deepseek.com")
    from app.core.config import get_settings

    get_settings.cache_clear()

    kw = session_task._resolve_kwargs("deepseek-v4-pro")
    # override 模型应该被加前缀
    assert kw["model"] == "deepseek/deepseek-v4-pro"
    assert kw["api_key"] == "sk-fake"


def test_resolve_kwargs_falls_back_to_litellm_model(monkeypatch):
    monkeypatch.setenv("LITELLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("LITELLM_API_BASE", "https://api.deepseek.com")
    from app.core.config import get_settings

    get_settings.cache_clear()

    kw = session_task._resolve_kwargs(None)
    assert kw["model"] == "deepseek/deepseek-v4-flash"


def test_resolve_kwargs_raises_when_no_model_configured(monkeypatch):
    """完全清掉 LITELLM_MODEL（注意 .env 默认有值，必须用 setenv 空覆盖）。"""
    # pydantic-settings 优先读环境变量，再读 .env；setenv 设空字符串能覆盖 .env
    monkeypatch.setenv("LITELLM_MODEL", "")
    from app.core.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(ValueError, match="LITELLM_MODEL"):
        session_task._resolve_kwargs(None)


def test_resolve_kwargs_keeps_explicit_provider_prefix(monkeypatch):
    """显式带前缀的模型名不应被二次补全。"""
    monkeypatch.setenv("LITELLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("LITELLM_API_BASE", "https://api.deepseek.com")
    from app.core.config import get_settings

    get_settings.cache_clear()

    kw = session_task._resolve_kwargs("zhipu/glm-4-flash")
    assert kw["model"] == "zhipu/glm-4-flash"


# ──────────────── _generate_title_main mock ────────────────


def _make_session(*, title=None, message_count=2):
    s = ChatSession(id=uuid.uuid4(), title=title, message_count=message_count)
    s.created_at = datetime.now(timezone.utc)
    return s


def _make_msg(role: str, content: str, *, sid=None):
    m = ChatMessage(
        id=uuid.uuid4(),
        session_id=sid or uuid.uuid4(),
        role=role,
        content=content,
    )
    m.created_at = datetime.now(timezone.utc)
    return m


@pytest.fixture
def patched_resources(monkeypatch):
    """task_resources 替换为返回 mock；不连真 PG/Milvus/Neo4j。"""
    from contextlib import asynccontextmanager

    mock_db_session = MagicMock()
    mock_db_session.execute = AsyncMock()
    mock_db_session.commit = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock()
    mock_result.scalars = MagicMock()
    mock_db_session.execute.return_value = mock_result

    @asynccontextmanager
    async def _db_factory():
        yield mock_db_session

    mock_resources = MagicMock()
    mock_resources.db = _db_factory

    @asynccontextmanager
    async def _fake_task_resources():
        yield mock_resources

    monkeypatch.setattr(session_task, "task_resources", _fake_task_resources)
    return mock_resources, mock_db_session, mock_result


@pytest.fixture
def patched_litellm(monkeypatch):
    """litellm.acompletion 替换为返回固定 message 的 mock。"""

    async def _fake_acompletion(**kwargs):
        return {"choices": [{"message": {"content": "AI 生成的标题"}}]}

    monkeypatch.setattr(session_task.litellm, "acompletion", _fake_acompletion)


async def test_title_main_session_not_found(patched_resources):
    _, _, mock_result = patched_resources
    mock_result.scalar_one_or_none.side_effect = [None]

    result = await session_task._generate_title_main(str(uuid.uuid4()))
    assert result["status"] == "skipped"
    assert result["reason"] == "session_not_found"


async def test_title_main_already_has_title(patched_resources):
    _, _, mock_result = patched_resources
    sess = _make_session(title="手动设的标题")
    mock_result.scalar_one_or_none.side_effect = [sess]

    result = await session_task._generate_title_main(str(uuid.uuid4()))
    assert result["status"] == "skipped"
    assert result["reason"] == "title_already_set"


async def test_title_main_not_enough_messages(patched_resources):
    _, _, mock_result = patched_resources
    sess = _make_session(title=None)
    mock_result.scalar_one_or_none.side_effect = [sess]
    # 仅 1 条消息
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=[_make_msg("user", "hi")])
    mock_result.scalars.return_value = scalars_mock

    result = await session_task._generate_title_main(str(uuid.uuid4()))
    assert result["status"] == "skipped"
    assert result["reason"] == "not_enough_messages"


async def test_title_main_happy_path(patched_resources, patched_litellm, monkeypatch):
    monkeypatch.setenv("LITELLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("LITELLM_API_BASE", "https://api.deepseek.com")
    from app.core.config import get_settings

    get_settings.cache_clear()

    _, mock_db, mock_result = patched_resources
    sid = uuid.uuid4()
    sess = _make_session(title=None)
    mock_result.scalar_one_or_none.side_effect = [sess]
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(
        return_value=[_make_msg("user", "你好", sid=sid), _make_msg("assistant", "你好！", sid=sid)]
    )
    mock_result.scalars.return_value = scalars_mock

    result = await session_task._generate_title_main(str(sid))
    assert result["status"] == "completed"
    assert result["title"] == "AI 生成的标题"
    # 应执行写回 UPDATE
    assert mock_db.commit.await_count >= 1


# ──────────────── _generate_summary_main mock ────────────────


async def test_summary_main_session_not_found(patched_resources):
    _, _, mock_result = patched_resources
    mock_result.scalar_one_or_none.side_effect = [None]

    result = await session_task._generate_summary_main(str(uuid.uuid4()))
    assert result["status"] == "skipped"
    assert result["reason"] == "session_not_found"


async def test_summary_main_no_messages(patched_resources):
    _, _, mock_result = patched_resources
    mock_result.scalar_one_or_none.side_effect = [_make_session()]
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=[])
    mock_result.scalars.return_value = scalars_mock

    result = await session_task._generate_summary_main(str(uuid.uuid4()))
    assert result["status"] == "skipped"
    assert result["reason"] == "no_messages"


async def test_summary_main_too_long_returns_failed(patched_resources):
    """拼接后 > SUMMARY_INPUT_CHAR_LIMIT → status=failed（dev_plan S4 决策）。"""
    _, _, mock_result = patched_resources
    mock_result.scalar_one_or_none.side_effect = [_make_session()]
    # 制造一条超长消息
    long_msg = _make_msg("user", "气" * (session_task.SUMMARY_INPUT_CHAR_LIMIT + 100))
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=[long_msg])
    mock_result.scalars.return_value = scalars_mock

    result = await session_task._generate_summary_main(str(uuid.uuid4()))
    assert result["status"] == "failed"
    assert result["reason"] == "content_too_long"


async def test_summary_main_happy_path(patched_resources, patched_litellm, monkeypatch):
    monkeypatch.setenv("LITELLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("LITELLM_API_BASE", "https://api.deepseek.com")
    from app.core.config import get_settings

    get_settings.cache_clear()

    _, mock_db, mock_result = patched_resources
    mock_result.scalar_one_or_none.side_effect = [_make_session()]
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(
        return_value=[
            _make_msg("user", "首条问题"),
            _make_msg("assistant", "回答 A"),
            _make_msg("user", "追问 B"),
            _make_msg("assistant", "回答 B"),
        ]
    )
    mock_result.scalars.return_value = scalars_mock

    result = await session_task._generate_summary_main(str(uuid.uuid4()))
    assert result["status"] == "completed"
    assert result["summary_chars"] > 0
    # 写回 commit 应执行
    assert mock_db.commit.await_count >= 1


# ──────────────── Celery 任务壳异常包装 ────────────────


def test_title_task_wraps_exception(monkeypatch):
    """_generate_title_main 抛错 → task 返 failed 字典（不重抛给 Celery）。"""

    async def _raise(_sid):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(session_task, "_generate_title_main", _raise)

    out = session_task.generate_session_title_task(str(uuid.uuid4()))
    assert out["status"] == "failed"
    assert "RuntimeError" in out["error"]
    assert "kaboom" in out["error"]


def test_summary_task_wraps_exception(monkeypatch):
    async def _raise(_sid):
        raise ValueError("bad input")

    monkeypatch.setattr(session_task, "_generate_summary_main", _raise)

    out = session_task.generate_session_summary_task(str(uuid.uuid4()))
    assert out["status"] == "failed"
    assert "ValueError" in out["error"]


def test_title_task_happy_path(monkeypatch):
    """正常返字典原样透传。"""

    async def _ok(_sid):
        return {"status": "completed", "title": "OK 标题"}

    monkeypatch.setattr(session_task, "_generate_title_main", _ok)
    out = session_task.generate_session_title_task(str(uuid.uuid4()))
    assert out["status"] == "completed"
    assert out["title"] == "OK 标题"


def test_summary_task_happy_path(monkeypatch):
    async def _ok(_sid):
        return {"status": "completed", "summary_chars": 88}

    monkeypatch.setattr(session_task, "_generate_summary_main", _ok)
    out = session_task.generate_session_summary_task(str(uuid.uuid4()))
    assert out["status"] == "completed"
    assert out["summary_chars"] == 88

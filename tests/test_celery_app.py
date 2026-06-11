"""Celery 配置与 ping_task 链路单测（S0 验收）。

不连真 Redis：通过 task_always_eager 让任务同步在当前进程执行，验证：
- celery_app 配置项（acks_late / prefetch / serializer / 时区）
- broker / backend 缺省复用 REDIS_URL，显式配置可覆盖
- ping_task 注册成功且能跑通
"""

import importlib
import os

import pytest


# ─────────── Celery 配置 ───────────


def test_celery_app_singleton_exists():
    from app.tasks import celery_app

    assert celery_app is not None
    assert celery_app.main == "TyAgent"


def test_celery_critical_options_for_reliability():
    """task_acks_late + prefetch_multiplier=1 是 PRD TASK-01 的核心可靠性保证。"""
    from app.tasks import celery_app

    conf = celery_app.conf
    assert conf.task_acks_late is True, "task_acks_late 必须为 True（worker 异常时任务重入队）"
    assert conf.worker_prefetch_multiplier == 1, "prefetch_multiplier 必须为 1（防 OOM 阻塞队列）"


def test_celery_serializer_is_json_only():
    """禁用 pickle，避免 RCE 风险。"""
    from app.tasks import celery_app

    conf = celery_app.conf
    assert conf.task_serializer == "json"
    assert conf.result_serializer == "json"
    assert conf.accept_content == ["json"]


def test_celery_timezone_china():
    from app.tasks import celery_app

    assert celery_app.conf.timezone == "Asia/Shanghai"
    assert celery_app.conf.enable_utc is False


def test_celery_includes_ping_module():
    """ping 模块必须在 include 列表里，否则 worker 起来认不到任务。"""
    from app.tasks import celery_app

    assert "app.tasks.ping" in celery_app.conf.include


def test_celery_ping_task_registered():
    from app.tasks import celery_app

    assert "app.tasks.ping.ping_task" in celery_app.tasks


# ─────────── broker / backend 缺省与覆盖 ───────────


def _reload_celery_with_env(**env):
    """改环境变量 → 重置 settings 缓存 → 重新 import celery_app 模块。

    注意：`app/tasks/__init__.py` 写了 `from app.tasks.celery_app import celery_app`，
    这会让 `app.tasks.celery_app` 这个名字在 `app.tasks` 命名空间下被 Celery 实例遮蔽
    （Python 的 `from ... import` 优先级高于子模块导入）。因此不能用
    `import app.tasks.celery_app as mod` 来拿模块对象 —— 会拿到 Celery 实例，
    `importlib.reload()` 必然报 `TypeError: reload() argument must be a module`。

    正确做法：直接从 `sys.modules` 里取已加载的子模块。
    """
    import sys

    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    from app.core.config import get_settings

    get_settings.cache_clear()

    # 触发一次普通导入，确保子模块在 sys.modules 里
    import app.tasks.celery_app  # noqa: F401

    mod = sys.modules["app.tasks.celery_app"]
    importlib.reload(mod)
    return mod.celery_app


def test_celery_broker_defaults_to_redis_url():
    app = _reload_celery_with_env(
        REDIS_URL="redis://default-host:6379/0",
        CELERY_BROKER_URL=None,
        CELERY_RESULT_BACKEND=None,
    )
    assert app.conf.broker_url == "redis://default-host:6379/0"
    assert app.conf.result_backend == "redis://default-host:6379/0"


def test_celery_broker_and_backend_override():
    app = _reload_celery_with_env(
        REDIS_URL="redis://default-host:6379/0",
        CELERY_BROKER_URL="redis://broker-host:6379/0",
        CELERY_RESULT_BACKEND="redis://backend-host:6379/1",
    )
    assert app.conf.broker_url == "redis://broker-host:6379/0"
    assert app.conf.result_backend == "redis://backend-host:6379/1"


# ─────────── ping_task 链路 ───────────


@pytest.fixture
def eager_celery():
    """开 always_eager 让任务在 caller 进程同步执行，无需起真 worker / Redis。"""
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


def test_ping_task_default_message(eager_celery):
    from app.tasks import ping_task

    res = ping_task.delay()
    assert res.successful()
    assert res.result.startswith("pong: ping @ ")


def test_ping_task_custom_message(eager_celery):
    from app.tasks import ping_task

    res = ping_task.delay("hello")
    assert res.successful()
    assert res.result.startswith("pong: hello @ ")


def test_ping_task_returns_string(eager_celery):
    from app.tasks import ping_task

    res = ping_task.delay("S0-smoke")
    assert isinstance(res.result, str)
    assert "S0-smoke" in res.result

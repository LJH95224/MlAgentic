"""V1.5 新增 Settings 字段单测（S0 验收）。

只验默认值与从环境变量读取的行为，不连真服务。
"""

import importlib
import os


def _fresh_settings(**env_overrides):
    """清空 lru_cache + 设环境变量 + 重新拿一个 Settings 实例。"""
    for k, v in env_overrides.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    from app.core.config import get_settings

    get_settings.cache_clear()
    return get_settings()


def _cleanup(*keys):
    for k in keys:
        os.environ.pop(k, None)
    from app.core.config import get_settings

    get_settings.cache_clear()


# ─────────── Redis ───────────


def test_redis_url_default():
    s = _fresh_settings(REDIS_URL=None)
    # 默认走 127.0.0.1 强制 IPv4，规避 Windows + Docker Desktop 上 localhost→IPv6 的转发坑
    assert s.redis_url == "redis://127.0.0.1:6379/0"
    _cleanup("REDIS_URL")


def test_redis_url_override():
    s = _fresh_settings(REDIS_URL="redis://prod-host:6380/2")
    assert s.redis_url == "redis://prod-host:6380/2"
    _cleanup("REDIS_URL")


# ─────────── Celery 缺省与覆盖 ───────────


def test_celery_broker_defaults_to_redis_url():
    s = _fresh_settings(
        REDIS_URL="redis://r:6379/0",
        CELERY_BROKER_URL=None,
        CELERY_RESULT_BACKEND=None,
    )
    assert s.effective_celery_broker_url == "redis://r:6379/0"
    assert s.effective_celery_result_backend == "redis://r:6379/0"
    _cleanup("REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")


def test_celery_broker_and_backend_overrides():
    s = _fresh_settings(
        REDIS_URL="redis://r:6379/0",
        CELERY_BROKER_URL="redis://broker:6379/0",
        CELERY_RESULT_BACKEND="redis://backend:6379/1",
    )
    assert s.effective_celery_broker_url == "redis://broker:6379/0"
    assert s.effective_celery_result_backend == "redis://backend:6379/1"
    _cleanup("REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")


# ─────────── 文件上传 ───────────


def test_upload_dir_default():
    s = _fresh_settings(UPLOAD_DIR=None)
    assert s.upload_dir == "./uploads"
    _cleanup("UPLOAD_DIR")


def test_max_file_size_default_50mb():
    s = _fresh_settings(MAX_FILE_SIZE_MB=None)
    assert s.max_file_size_mb == 50
    _cleanup("MAX_FILE_SIZE_MB")


def test_max_file_size_override():
    s = _fresh_settings(MAX_FILE_SIZE_MB="200")
    assert s.max_file_size_mb == 200
    _cleanup("MAX_FILE_SIZE_MB")


# ─────────── 会话上下文窗口 ───────────


def test_context_window_default_20():
    s = _fresh_settings(CONTEXT_WINDOW_MESSAGES=None)
    assert s.context_window_messages == 20
    _cleanup("CONTEXT_WINDOW_MESSAGES")


def test_context_window_override():
    s = _fresh_settings(CONTEXT_WINDOW_MESSAGES="50")
    assert s.context_window_messages == 50
    _cleanup("CONTEXT_WINDOW_MESSAGES")


# ─────────── 标题/摘要 LLM 独立切换 ───────────


def test_session_title_and_summary_models_default_none():
    """缺省 None，业务层自行 fallback 到 LITELLM_MODEL。"""
    s = _fresh_settings(SESSION_TITLE_MODEL=None, SESSION_SUMMARY_MODEL=None)
    assert s.session_title_model is None
    assert s.session_summary_model is None
    _cleanup("SESSION_TITLE_MODEL", "SESSION_SUMMARY_MODEL")


def test_session_title_and_summary_models_override():
    s = _fresh_settings(
        SESSION_TITLE_MODEL="deepseek-v4-flash",
        SESSION_SUMMARY_MODEL="zhipu/glm-4-flash",
    )
    assert s.session_title_model == "deepseek-v4-flash"
    assert s.session_summary_model == "zhipu/glm-4-flash"
    _cleanup("SESSION_TITLE_MODEL", "SESSION_SUMMARY_MODEL")

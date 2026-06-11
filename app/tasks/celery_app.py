"""Celery 应用单例（V1.5 PRD §3.4 TASK-01）。

设计要点：
- broker / backend 均指向 Redis（缺省复用 REDIS_URL，可通过 CELERY_BROKER_URL /
  CELERY_RESULT_BACKEND 单独覆盖）
- task_acks_late=True：Worker 异常时任务可重新入队，保证至少一次执行
- worker_prefetch_multiplier=1：防止 OOM 场景下多任务并发
- task_serializer="json"：禁用 pickle，避免 RCE 风险
- include：显式列出包含任务的模块，让 worker 启动时 import 完成注册
- timezone="Asia/Shanghai" + enable_utc=False：方便日志对时

启动命令（Windows 开发态）：
    celery -A app.tasks.celery_app worker --pool=solo -l info

Linux 生产部署：
    celery -A app.tasks.celery_app worker --pool=prefork -c 4 -l info
"""

import logging

from celery import Celery

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()


# Celery 任务所在模块（worker 启动时会 import 并注册其中的 @celery_app.task）
# 新增任务模块时记得在这里追加，否则 worker 不会发现它
_TASK_MODULES: list[str] = [
    "app.tasks.ping",
    # S3 阶段（2026-06-11 起）：文件入库管道
    "app.tasks.ingest_task",
    # S4 阶段追加：
    # "app.tasks.session_task",
]


celery_app = Celery(
    "TyAgent",
    broker=_settings.effective_celery_broker_url,
    backend=_settings.effective_celery_result_backend,
    include=_TASK_MODULES,
)


celery_app.conf.update(
    # 序列化（json 安全 + 跨语言；禁 pickle）
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # 时区（用中国时区便于看日志；启用 UTC 也不影响行为）
    timezone="Asia/Shanghai",
    enable_utc=False,
    # 可靠性：worker 进程异常时任务重新入队（PRD TASK-01 关键要求）
    task_acks_late=True,
    # 防止单 worker 一次抓多个任务，避免 OOM 时阻塞队列（PRD TASK-01 关键要求）
    worker_prefetch_multiplier=1,
    # 结果保留时间（秒），24 小时；轮询接口 2s 查一次足够
    result_expires=24 * 60 * 60,
    # 任务执行超时（兜底，防文件解析挂死）；具体任务可在装饰器里覆盖
    task_time_limit=30 * 60,        # 硬超时 30 分钟
    task_soft_time_limit=25 * 60,   # 软超时 25 分钟
    # 心跳与连接重连
    broker_connection_retry_on_startup=True,
    # broker 不可达时最多重试 3 次（每次间隔指数退避），避免应用层 .delay() 无限卡死
    # 用户体验上：Redis 没起时立刻报错 → 比"卡 30 分钟没反应"友好得多
    broker_connection_max_retries=3,
    broker_connection_timeout=4,  # 单次连接超时（秒）
)


logger.info(
    "Celery 初始化完成 broker=%s backend=%s",
    _settings.effective_celery_broker_url,
    _settings.effective_celery_result_backend,
)

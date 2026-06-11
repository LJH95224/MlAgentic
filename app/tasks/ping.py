"""ping_task — Celery 链路 smoke 任务（S0 验收用）。

用法：
    from app.tasks import ping_task
    res = ping_task.delay("hello")
    print(res.get(timeout=5))     # → "pong: hello"

只用来验证 Worker 起得来、broker 通、result backend 写得进。
"""

import logging
import socket

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.ping.ping_task")
def ping_task(message: str = "ping") -> str:
    """返回 "pong: <message> @ <hostname>"，仅供链路自检。"""
    host = socket.gethostname()
    reply = f"pong: {message} @ {host}"
    logger.info("ping_task 收到 message=%r 回 reply=%r", message, reply)
    return reply

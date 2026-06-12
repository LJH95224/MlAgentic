"""Celery 异步任务模块（V1.5 PRD §3.4）。

引入此包即可获得 Celery app 单例与已注册的任务集合：

    from app.tasks import celery_app
    from app.tasks.ping import ping_task

后续阶段会陆续注册：
- S3：app.tasks.ingest_task.parse_and_ingest_task（文件解析入库）★ 2026-06-11 完整实现
- S4：app.tasks.session_task.generate_session_{title,summary}_task ★ 2026-06-11 已实现
"""

from app.tasks.celery_app import celery_app
from app.tasks.ingest_task import parse_and_ingest_task
from app.tasks.ping import ping_task
from app.tasks.session_task import (
    generate_session_summary_task,
    generate_session_title_task,
)

__all__ = [
    "celery_app",
    "ping_task",
    "parse_and_ingest_task",
    "generate_session_title_task",
    "generate_session_summary_task",
]

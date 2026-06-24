"""Celery 异步任务模块（PRD §3.4）。

引入此包即可获得 Celery app 单例与已注册的任务集合：

    from app.tasks import celery_app
    from app.tasks.ping import ping_task

已注册的任务包括：
- app.tasks.ingest_task.parse_and_ingest_task（文件解析入库）★ 2026-06-11 完整实现
- app.tasks.session_task.generate_session_{title,summary}_task（会话标题/摘要生成）★ 2026-06-11 已实现
- app.tasks.eval_task.run_evaluation_task（RAGAS 评估）★ 2026-06-16 已实现
- app.tasks.reaper_task.reap_stale_processing_files（卡死回收）★ 2026-06-22 已实现
"""

from app.tasks.celery_app import celery_app
from app.tasks.eval_task import run_evaluation_task
from app.tasks.ingest_task import parse_and_ingest_task
from app.tasks.ping import ping_task
from app.tasks.reaper_task import reap_stale_processing_files
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
    "run_evaluation_task",
    "reap_stale_processing_files",
]

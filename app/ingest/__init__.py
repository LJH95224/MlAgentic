"""文档解析与切片管道（V1.5 PRD §3.3 / §6.1 / §6.2）。

模块组织：
- parser.py：按 MIME / 扩展名分发到具体解析函数，输出纯文本
- splitter.py：把纯文本按 KB 配置的 chunk_size / chunk_overlap 切成 Chunk

被 S3 Celery 任务 [app/tasks/ingest_task.py] 调用。
"""

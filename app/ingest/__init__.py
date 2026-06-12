"""文档解析与切片管道（V1.5 PRD §3.3 / §6.1 / §6.2 + V2.0 IDP-01/02）。

模块组织：
- parser.py：按 MIME / 扩展名分发到具体解析函数
  - V1.5: parse_document() → 纯文本 str
  - V2.0: parse_document_structured() → list[StructuredBlock]
- splitter.py：V1.5 纯文本切片 → list[Chunk]
- structured_splitter.py：V2.0 结构感知切片 → list[StructuredChunk]

被 Celery 任务 [app/tasks/ingest_task.py] 调用。
"""

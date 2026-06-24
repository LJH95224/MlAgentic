"""文档解析与切片管道（IDP-01/02）。

模块组织：
- parser.py：按 MIME / 扩展名分发到具体解析函数
  - parse_document() → 纯文本 str
  - parse_document_structured() → list[StructuredBlock]
- splitter.py：纯文本切片 → list[Chunk]
- structured_splitter.py：结构感知切片 → list[StructuredChunk]

被 Celery 任务 [app/tasks/ingest_task.py] 调用。
"""
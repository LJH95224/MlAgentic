# T7 · 表格描述 + 双层索引 + 文档元数据 实施计划

> **阶段**：V2.0 Hermes T7（P2）
> **PRD 子需求**：IDP-03 / IDP-04 / IDP-05
> **前置**：T1（IDP-01/02/06 入库管道骨架）✅
> **预计代码量**：~500 行实现 + ~400 行测试
> **目标落地路径**：`docs/superpowers/plans/2026-06-15-t7-idp-enhance.md`

---

## 1. Context（为什么做）

T1 阶段已经把入库管道从 V1.5 七步重构成 V2.0 十一步骨架，结构感知的 parser/splitter 也都接通了——但 **Step 4/5/6 三个新增步骤现在仍是 noop**（`_step_table_description_noop` / `_step_summary_noop` / `_step_doc_metadata_noop`），数据落库时 `parent_chunk_id` / `is_summary` / `doc_metadata` / `summary_brief` 这些 V2 新字段全都是空值。

PRD 把这三步定位为 V2.0 RAG 效果的**质变点**：

1. **IDP-03 表格描述**：表格本身向量极不友好（列名+数值结构化语义），需为每张表格 LLM 生成自然语言描述作为额外 chunk 入库，让"第三季度销售额"这种 query 能命中表格内容。
2. **IDP-04 双层索引**：粗粒度摘要 chunk 解决"细粒度 chunk 语义碎片"的问题；初筛走粗粒度（is_summary=True）覆盖更广，精排回到细粒度（parent_chunk_id 串联）保证精度。
3. **IDP-05 文档元数据**：LLM 提取 doc_type / doc_date / language / key_topics / summary_brief，写入 `kb_files.doc_metadata` JSONB；前端文件列表能展示摘要，未来按 doc_type / doc_date 做检索前置过滤的基础也在这里铺好。

T7 完成后，整个入库管道才真正"满血"，T8 的 Query 改写和图谱锚定也才能拿到完整的多粒度索引来检索。

---

## 2. 已就绪的依赖（直接复用）

| 模块 | 文件 / 字段 | 状态 |
|---|---|---|
| 表格识别 | [app/ingest/parser.py](../../app/ingest/parser.py) `block_type="table"` 在 PDF/DOCX/MD 三个 parser 都已正确标识 | ✅ T1 已做 |
| Splitter 字段 | [app/ingest/structured_splitter.py](../../app/ingest/structured_splitter.py) `StructuredChunk.parent_chunk_id` / `is_summary` 字段已建（默认 None / False） | ✅ T1 已做 |
| Milvus V2 Schema | [app/rag/schema.py](../../app/rag/schema.py) `parent_chunk_id`(VARCHAR 64) / `is_summary`(BOOL) 已建 | ✅ T0 已做 |
| Milvus 写入分支 | [app/tasks/ingest_task.py](../../app/tasks/ingest_task.py) `_step_milvus_write_v2` 已写 chunk.parent_chunk_id / chunk.is_summary | ✅ T1 已做 |
| `kb_files` 表字段 | [app/models/kb_file.py](../../app/models/kb_file.py) `doc_metadata` JSONB / `summary_brief` Text | ✅ T0 已做 |
| `chunk_id` 生成 | [app/tasks/ingest_task.py](../../app/tasks/ingest_task.py) `_make_chunk_id_int(document_id, chunk_index)` SHA256 → INT64，幂等 upsert | ✅ T1 已做 |
| Embedding 批处理 | [app/rag/embedding.py](../../app/rag/embedding.py) `aembed_texts` | ✅ V1.0 已做 |
| 异步 LLM 软失败模式 | [app/kg/ner.py](../../app/kg/ner.py) `run_ner` 模式（自建 kwargs + JSON 输出 + 软失败） | ✅ 可参考复用 |
| 写入侧 NER 模式 | [app/tasks/ingest_task.py](../../app/tasks/ingest_task.py) `_step_ner` `Semaphore(8) + wait_for(25s)` | ✅ 可参考复用 |

---

## 3. 关键设计决策（已与用户对齐）

| # | 决策点 | 选择 | 影响 |
|---|---|---|---|
| 1 | IDP-04 粗粒度聚合策略 | **按父级 heading_path 聚合**（取最末一级以外的 path 作为 group key） | 语义最对齐；无标题的 chunk 自成一组；空 heading_path 不参与聚合 |
| 2 | summary_brief / doc_metadata 暴露 | **FileListItem 加 summary_brief；FileDetail 加 summary_brief + doc_metadata** | PRD §282 闭环；前端可展示摘要列表 |
| 3 | 子 chunk 的 parent_chunk_id 字段值 | **存父 chunk 的 INT64 chunk_id 转字符串**（如 "9876543210"） | Milvus filter `parent_chunk_id == "X"` 直接命中；VARCHAR 64 足够装 |
| 4 | 表格描述 chunk 与原表格 chunk 的关系 | **新增独立 chunk，block_type="table_description"，parent_chunk_id 指向原表格 chunk** | 检索能命中描述，溯源能回到原表格；与 PRD §229-231 一致 |
| 5 | IDP-03/04/05 LLM 模型 | **默认复用 `LITELLM_MODEL`，可通过新增 `IDP_LLM_MODEL` 单独配** | 与 KG_NER_MODEL / QUERY_REWRITER_MODEL 同款解耦风格 |
| 6 | LLM 调用模式 | **直接调 `litellm.acompletion`**，与 ner.py / query_rewriter.py / session_task.py 一致；不引入新的 helper | 项目当前没有统一 LLM helper，T7 不为此专门重构 |
| 7 | LLM 失败软降级 | **IDP-03/04/05 任一步骤失败/超时不阻断主链路**：表格描述失败 → 不生成额外 chunk；段落摘要失败 → 跳过双层索引；元数据失败 → doc_metadata=None | 与 V1.5 NER 软失败原则一致 |
| 8 | 粗粒度 chunk 的 chunk_index 编号 | **从 `len(fine_chunks)` 起递增** | `_make_chunk_id_int(document_id, fine_count + i)` 与细粒度不冲突，幂等 upsert 不破 |
| 9 | 表格描述 chunk 的 chunk_index 编号 | **从 `len(fine_chunks) + len(coarse_chunks)` 起递增** | 三类 chunk 的 chunk_id 互不冲突 |
| 10 | NER 是否对粗/描述 chunk 也跑 | **不跑**，NER 只在原始细粒度 chunk 上做 | PRD 隐含：粗粒度是摘要、描述是合成文本，不应抽出新实体 |
| 11 | `chunk_count` 语义 | **fine + coarse + table_description 三类总数** | PRD §306 IDP-06 验收标准明确 |
| 12 | 双层索引开关 | **新增 `IDP_DUAL_INDEX_ENABLE` 配置（默认 True）** | 异常调试时可一键关闭；但 T7 阶段默认开启走全量验证 |

---

## 4. 实施步骤（按依赖顺序）

### T7.1 · 配置层 + Schema 暴露

**改 [app/core/config.py](../../app/core/config.py)** —— V2.0 区段新增：
```python
# --- IDP 智能文档处理（IDP-03/04/05，T7 阶段启用） ---
# 留空则复用 LITELLM_MODEL（与 KG_NER_MODEL 同款解耦风格）
idp_llm_model: str | None = Field(default=None, alias="IDP_LLM_MODEL")
# 双层索引开关；False 时跳过粗粒度 chunk 生成
idp_dual_index_enable: bool = Field(default=True, alias="IDP_DUAL_INDEX_ENABLE")
# 单步 LLM 调用硬超时（秒）—— 表格描述/段落摘要/元数据提取统一用
idp_llm_timeout_s: float = Field(default=20.0, alias="IDP_LLM_TIMEOUT_S")
# IDP 步骤的并发限制（同 NER）
idp_concurrency: int = Field(default=5, alias="IDP_CONCURRENCY")
# 文档元数据提取时取前 N 个字符做输入（PRD 推荐前 3000 token）
idp_doc_meta_input_chars: int = Field(default=8000, alias="IDP_DOC_META_INPUT_CHARS")
```

**改 [app/schemas/kb_file.py](../../app/schemas/kb_file.py)**：
- `FileListItem` 加 `summary_brief: str | None = None`
- `FileDetail` 加 `summary_brief: str | None = None` + `doc_metadata: dict | None = None`
- 两者都靠 `from_attributes=True` 直接从 ORM 自动映射

### T7.2 · IDP-03 表格描述生成

**新增 [app/ingest/table_description.py](../../app/ingest/table_description.py)**：

```python
TABLE_DESC_SYSTEM_PROMPT = """将以下 Markdown 表格转化为一段自然语言描述：
1. 描述表格的主题和结构
2. 提炼表格中的关键数据和规律
3. 不超过 200 字
4. 不要使用"该表格"等冗余开头"""

@dataclass(frozen=True)
class TableDescription:
    parent_index: int     # 原表格 chunk 在 fine_chunks 列表中的下标
    description: str

async def generate_table_descriptions(
    fine_chunks: list[StructuredChunk],
) -> list[TableDescription]:
    """对所有 block_type="table" 的 chunk 并发生成描述。
    异常/超时单条软失败，记 warning 不阻断。"""
```

实现要点：
- 复用 `litellm.acompletion`（参考 [app/kg/ner.py](../../app/kg/ner.py) 模式）
- `asyncio.Semaphore(idp_concurrency)` + `wait_for(idp_llm_timeout_s)`
- 模型选择：`settings.idp_llm_model or settings.litellm_model`，自动补厂商前缀（沿用 ner.py 推断逻辑）
- 描述长度兜底：超过 600 字节按 UTF-8 安全截断（防 Milvus VARCHAR 截断意外）
- 软失败：异常返 None，跳过该表

**改 [app/tasks/ingest_task.py](../../app/tasks/ingest_task.py)**：
- 删 `_step_table_description_noop`，新增 `_step_table_description(fine_chunks) -> list[StructuredChunk]`：返回**新增**的 table_description chunks（不修改原表格 chunk）
- 新 chunk 的字段：
  - `index = len(fine_chunks) + i`（决定 chunk_id 不冲突）
  - `block_type = "table_description"`
  - `parent_chunk_id = str(_make_chunk_id_int(document_id, parent_index))`
  - `is_summary = False`（不是粗粒度，是检索代理）
  - `heading_path` / `page_number` / `position_index` 复用原表格 chunk
- 在 `_main` 中：拿到 `td_chunks` 后追加到 `chunks` 列表，让后续 embedding / Milvus 写入自然纳入

### T7.3 · IDP-04 双层索引

**新增 [app/ingest/dual_layer.py](../../app/ingest/dual_layer.py)**：

```python
SUMMARY_SYSTEM_PROMPT = """对以下文档片段生成一段简明摘要：
1. 提炼核心论点和关键事实
2. 不超过 300 字
3. 保持中文风格
4. 直接输出摘要，不要前缀"""

@dataclass(frozen=True)
class CoarseChunk:
    """粗粒度摘要 chunk 中间产物。"""
    parent_indices: list[int]   # 关联的细粒度 chunk index 列表
    heading_path: list[str]
    block_type: str = "paragraph"  # 摘要总是 paragraph
    page_number: int | None = None
    summary_text: str = ""

def group_by_parent_heading(
    fine_chunks: list[StructuredChunk],
) -> list[list[int]]:
    """按父级 heading_path（去掉最末一级）分组，返回 group->fine_chunk_index 列表。

    - 空 heading_path 的 chunk 单独一组
    - 仅 1 个 chunk 的组也保留（虽冗余，但下游 LLM 可生成简短摘要）
    - 表格 / 代码 chunk 也参与分组（它们也属于某一标题下）
    - table_description chunk 不参与分组（在 _main 中已分离）
    """

async def generate_coarse_chunks(
    fine_chunks: list[StructuredChunk],
) -> list[CoarseChunk]:
    """分组 + 并发 LLM 摘要 + 软失败。"""
```

**改 [app/tasks/ingest_task.py](../../app/tasks/ingest_task.py)**：
- 删 `_step_summary_noop`，新增 `_step_dual_layer_index(fine_chunks) -> list[StructuredChunk]`
- 流程：
  1. `idp_dual_index_enable=False` 时直接返 `[]`
  2. 调 `generate_coarse_chunks` 得到 `CoarseChunk` 列表
  3. 转换为 `StructuredChunk`：
     - `index = len(fine_chunks) + len(td_chunks) + i`
     - `block_type = "paragraph"`
     - `parent_chunk_id = None`（粗 chunk 自己是 parent，无父）
     - `is_summary = True`
     - `content = summary_text`
     - `heading_path` 取分组的 group key
- 同时**回填 fine_chunks 的 parent_chunk_id**：每个粗 chunk 生成后，把它的 INT64 chunk_id 字符串回填到对应 fine_chunks 的 `parent_chunk_id` 字段
  - **难点**：`StructuredChunk` 是 frozen dataclass，需 `dataclasses.replace` 重建
  - 在 `_main` 中：粗 chunk 生成后用 `replace` 重新生成 `chunks[i]` 的副本，再追加粗 chunk 列表
- 注意：因为粗 chunk 的 chunk_id 依赖 `_make_chunk_id_int(document_id, coarse_index)`，所以 `document_id` 必须在该步可用——把 `file_record` 一并传入

### T7.4 · IDP-05 文档元数据

**新增 [app/ingest/doc_metadata.py](../../app/ingest/doc_metadata.py)**：

```python
DOC_META_SYSTEM_PROMPT = """从以下文档内容中提取结构化元数据，仅返回 JSON：

{
  "doc_type": "合同|报告|手册|法规|其他",
  "doc_date": "YYYY-MM" 或 null,
  "language": "zh|en|mixed",
  "key_topics": ["关键词1", "关键词2", ...],   // 3-5 个
  "summary_brief": "不超过 100 字的文档摘要"
}

约束：
- 严格按以上 schema 输出，不要其他字段
- summary_brief 不超过 100 字
- key_topics 必须是数组，3~5 个
- 信息无法判断时填 null"""

@dataclass(frozen=True)
class DocMetadata:
    doc_type: str | None = None
    doc_date: str | None = None
    language: str | None = None
    key_topics: list[str] = field(default_factory=list)
    summary_brief: str | None = None

    def to_dict(self) -> dict:
        ...

async def extract_doc_metadata(
    blocks: list[StructuredBlock],
) -> DocMetadata | None:
    """从 blocks 拼接前 N 字符 → LLM JSON 输出 → 解析 → 软失败返 None。"""
```

**改 [app/tasks/ingest_task.py](../../app/tasks/ingest_task.py)**：
- 删 `_step_doc_metadata_noop`，新增 `_step_doc_metadata(resources, file_record, blocks)`：
  - 调 `extract_doc_metadata`
  - 写入 PG：`UPDATE kb_files SET doc_metadata=:meta, summary_brief=:brief WHERE id=:file_id`
  - 失败软降级：返 None 不抛错

### T7.5 · 主链路 _main 串入

**改 [app/tasks/ingest_task.py](../../app/tasks/ingest_task.py) `_main`**，按 PRD §3.4 顺序：

```python
# Step 3: 切片
fine_chunks = _step_split_structured(blocks, kb)

# Step 4: 表格描述（追加 chunks）
td_chunks = await _step_table_description(fine_chunks, document_id=str(file_record.id))

# Step 5: 双层索引（生成粗 + 回填 fine 的 parent_chunk_id）
fine_chunks, coarse_chunks = await _step_dual_layer_index(
    fine_chunks, td_chunk_count=len(td_chunks),
    document_id=str(file_record.id),
)

# 合并三类 chunk（顺序：fine → table_desc → coarse；index 已唯一不冲突）
chunks = fine_chunks + td_chunks + coarse_chunks

# Step 6: 文档元数据
await _step_doc_metadata(resources, file_record, blocks)
await _set_progress(..., progress=PROGRESS_DOC_META)

# Step 7: Embedding（自然包含三类）
vectors = await _step_embed(chunks)

# Step 9: NER（只对 fine_chunks 跑！）
chunk_entities_fine = await _step_ner(fine_chunks)
# 给 td/coarse 补空 entities，对齐 zip(chunks, chunk_entities) 的长度
chunk_entities = chunk_entities_fine + [[] for _ in td_chunks] + [[] for _ in coarse_chunks]

# Step 8: Milvus V2 写入（已支持所有字段）
_step_milvus_write_v2(...)

# chunk_count 用三类总数
await _set_progress(..., chunk_count=len(chunks))
```

返回值：
```python
return {
    "file_id": ...,
    "kb_id": ...,
    "chunk_count": len(chunks),
    "fine_chunk_count": len(fine_chunks),
    "table_description_count": len(td_chunks),
    "coarse_chunk_count": len(coarse_chunks),
    "entity_count": ...,
    "block_types": list({c.block_type for c in chunks}),
    "status": FILE_STATUS_COMPLETED,
}
```

### T7.6 · 单测

**新增 [tests/test_v2_t7.py](../../tests/test_v2_t7.py)** —— 沿用 P1 / T8 的 mock 模式（patch litellm.acompletion / aembed_texts / Milvus client / get_neo4j_driver），无需真服务。

覆盖矩阵：

| 模块 | 用例 |
|---|---|
| `generate_table_descriptions` | 含表格 chunks 命中 / 无表格短路 / LLM 异常软失败跳过该表 / 超时软失败 / 字节截断 |
| `group_by_parent_heading` | 同 h1/h2 聚合 / 空 heading 单独一组 / 仅 1 chunk 也保留 / 不同顺序保持稳定 |
| `generate_coarse_chunks` | happy path / 关闭开关返空 / 单组 LLM 失败跳过 / heading_path 透传 |
| `extract_doc_metadata` | JSON 解析 / 字段缺失填默认 / 围栏剥离 / 异常软失败返 None / 输入截断 |
| `_step_table_description` | 返回新 chunks，原 fine_chunks 不变 / chunk_index 从 fine_count 起 |
| `_step_dual_layer_index` | 回填 fine.parent_chunk_id / 粗 chunk index 续号 / 关闭开关返空 |
| `_step_doc_metadata` | 写 PG 成功 / 软失败不抛 |
| `_main` 端到端 | 全 mock 联跑：fine + td + coarse 都进 Milvus / chunk_count 三类总和 / NER 只跑 fine |
| Schema | FileListItem 含 summary_brief / FileDetail 含 doc_metadata |

**修兼容**：
- [tests/test_v2_t1.py](../../tests/test_v2_t1.py) 如有断言 `chunks` 里全是细粒度的（如 `c.is_summary == False`）需调整：T7 集成后可能产生 td/coarse
- [tests/test_ingest_task.py](../../tests/test_ingest_task.py) 端到端用例需补 mock `_step_table_description` / `_step_dual_layer_index` / `_step_doc_metadata` 三步以避免真调 LLM

### T7.7 · 进度文档同步

完成后更新 [docs/progress.md](../../docs/progress.md)：
- T7 行 → ✅，完成日期 2026-06-15
- 追加 T7 详细子节（交付内容 / 关键设计决策 / 验证状态）
- 历史变更顶部加一条

同步 [docs/v2_dev_plan.md](../../docs/v2_dev_plan.md) 末尾追加 `### ✅ T7 完成 · 2026-06-15`。

---

## 5. 关键文件清单

**新增**：
- `app/ingest/table_description.py` —— IDP-03
- `app/ingest/dual_layer.py` —— IDP-04
- `app/ingest/doc_metadata.py` —— IDP-05
- `tests/test_v2_t7.py`

**修改**：
- `app/core/config.py` —— V2.0 区段加 5 字段（idp_llm_model / idp_dual_index_enable / idp_llm_timeout_s / idp_concurrency / idp_doc_meta_input_chars）
- `app/schemas/kb_file.py` —— FileListItem 加 summary_brief；FileDetail 加 summary_brief + doc_metadata
- `app/tasks/ingest_task.py` —— 替换 3 个 noop 步骤为真实步骤；改 `_main` 串联三类 chunk + 回填 parent_chunk_id；调整 chunk_count 语义
- 可能修改：[tests/test_v2_t1.py](../../tests/test_v2_t1.py) / [tests/test_ingest_task.py](../../tests/test_ingest_task.py) 兼容修复

---

## 6. 验证方式

### 6.1 单测
```bash
pytest tests/test_v2_t7.py -v                                        # T7 全部
pytest tests/test_v2_t1.py tests/test_ingest_task.py -v              # 兼容性回归
pytest tests/ --ignore=tests/test_v1_5_integration*.py               # 全量 mock 回归（目标 621 → ~660+，零回归）
```

### 6.2 端到端联调（用户手动）
启动依赖：`docker compose up -d` + `uvicorn app.main:app --reload` + `celery -A app.tasks.celery_app worker --pool=solo -l info`

**IDP-03 验收**：
- 上传含 5 张表格的 docx/md → 等任务 completed
- `GET /api/v1/knowledge-bases/{kb_id}/files/{file_id}` → `chunk_count` 应该比纯切片多至少 5（5 张表格的 description chunk）
- 用 pymilvus client 查 `block_type=="table_description"` 的 chunks 应有 5 条；每条 `parent_chunk_id` 指向某个 `block_type=="table"` 的 chunk
- 调 `/api/v2/query` 查"销售额"等表格内关键词 → 看 source_citations 能命中表格

**IDP-04 验收**：
- 上传中等长度文档（10+ heading）
- 查 Milvus：`is_summary=True` 的 chunk 数约为 fine 的 20~30%；fine chunk 的 `parent_chunk_id` 全部指向某个 is_summary 的 chunk
- 检索默认走 fine，trace 中 retrieve step 不返回 is_summary=True 的 chunk

**IDP-05 验收**：
- 上传一份合同 docx
- `GET /api/v1/knowledge-bases/{kb_id}/files` → `summary_brief` 字段非空
- `GET .../files/{file_id}` → `doc_metadata.doc_type=="合同"`，`key_topics` 含 3~5 个核心词

### 6.3 chunk_count 语义验收
- 入库完成后 `kb_files.chunk_count` = `fine + table_description + coarse`
- `kb.chunk_count` 增量 = 同上

---

## 7. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| IDP-03/04/05 都要 LLM，单文档 LLM 调用次数暴增（fine chunks N 个表格 + M 个粗 chunk + 1 个 doc_meta） | 入库延迟 + token 成本 | `Semaphore(idp_concurrency=5)` 限并发；提供 `IDP_DUAL_INDEX_ENABLE=False` 开关 |
| LLM JSON 解析失败 | 元数据/摘要丢失 | 沿用 NER 模式：异常软失败、剥离 markdown 围栏、空回退 |
| 粗 chunk 过短（仅 1 个 fine 时摘要冗余） | 双层索引退化 | 接受冗余，PRD 允许；摘要 LLM 提示"简明摘要，可以与原文相似" |
| 父子关联错位 | parent_chunk_id 指向错误 chunk | `dataclasses.replace` 严格按 index 对应；单测覆盖回填顺序 |
| 与 V1.5 ingest_task 测试断言冲突 | 测试失败 | 在 T7.6 同提交修兼容；同 T8 在 P1 测试上的处理方式 |
| Milvus filter 表达式 `parent_chunk_id == "<int_str>"` 性能 | 检索慢 | parent_chunk_id 字段 INVERTED 索引（V2 Schema 应已有；如无，T8 / T9 再补） |

---

*T7 实施计划 · End of Document*

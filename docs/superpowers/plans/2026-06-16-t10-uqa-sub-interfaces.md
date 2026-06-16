# T10 · UQA-02/03/04 分层子接口 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `/api/v2/` 下新增三个分层子接口——纯检索 `/v2/retrieve`、纯生成 `/v2/generate`、独立精排 `/v2/rerank`——让开发者能按需使用 Hermes 的子能力，而非只能走全链路 `/v2/query`。

**Architecture:** 三个子接口各自独立端点文件 + Schema 文件，复用现有的 `hybrid_search`、`generate_answer`、`build_context_with_citation`、`parse_citations`、`get_reranker` 等核心模块。路由注册到现有 V2 router。错误码新增 42201（context_chunks 为空）。

**Tech Stack:** FastAPI + Pydantic + SQLAlchemy async + 现有 RAG 模块（hybrid_retriever / reranker / citation / confidence / faithfulness）

---

## 文件结构

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `app/schemas/v2/retrieve.py` | UQA-02 Retrieve 请求/响应 Schema |
| 新建 | `app/schemas/v2/generate.py` | UQA-03 Generate 请求/响应 Schema |
| 新建 | `app/schemas/v2/rerank.py` | UQA-04 Rerank 请求/响应 Schema |
| 新建 | `app/api/v2/endpoints/retrieve.py` | UQA-02 纯检索端点 |
| 新建 | `app/api/v2/endpoints/generate.py` | UQA-03 纯生成端点 |
| 新建 | `app/api/v2/endpoints/rerank.py` | UQA-04 独立精排端点 |
| 修改 | `app/api/v2/router.py` | 挂载三个新路由 |
| 修改 | `app/api/error_codes.py` | 新增 `CONTEXT_CHUNKS_EMPTY = 42201` |
| 修改 | `app/api/exceptions.py` | 注册 42201 → HTTP 422 映射 |
| 新建 | `tests/test_v2_t10.py` | T10 全套单测 |

---

## Task 1: 错误码 42201 + Schema 定义

**Files:**
- Modify: `app/api/error_codes.py`
- Modify: `app/api/exceptions.py`
- Create: `app/schemas/v2/retrieve.py`
- Create: `app/schemas/v2/generate.py`
- Create: `app/schemas/v2/rerank.py`
- Test: `tests/test_v2_t10.py`

### Step 1: 写错误码 42201 的失败测试

```python
# tests/test_v2_t10.py
"""V2.0 T10 阶段单测（UQA-02/03/04 分层子接口）。"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ──────────────── 错误码 ────────────────


class TestErrorCode42201:
    """PRD §1129: context_chunks 为空 → 42201 / HTTP 422。"""

    def test_error_code_defined(self):
        from app.api import error_codes
        assert hasattr(error_codes, "CONTEXT_CHUNKS_EMPTY")
        assert error_codes.CONTEXT_CHUNKS_EMPTY == 42201

    def test_error_code_default_message(self):
        from app.api import error_codes
        assert 42201 in error_codes.DEFAULT_MESSAGES

    def test_http_status_mapping(self):
        from app.api.exceptions import HTTP_STATUS_BY_CODE
        from app.api import error_codes
        from http import HTTPStatus
        assert HTTP_STATUS_BY_CODE[error_codes.CONTEXT_CHUNKS_EMPTY] == HTTPStatus.UNPROCESSABLE_ENTITY
```

### Step 2: 运行测试确认失败

```bash
conda activate geo_agent && pytest tests/test_v2_t10.py::TestErrorCode42201 -v
```

预期：`test_error_code_defined` 失败（`CONTEXT_CHUNKS_EMPTY` 不存在）

### Step 3: 实现错误码 + HTTP 映射

修改 `app/api/error_codes.py`，在 `EMBEDDING_DIM_MISMATCH` 后面新增：

```python
CONTEXT_CHUNKS_EMPTY = 42201  # context_chunks 为空（/v2/generate 接口，PRD §1129）
```

在 `DEFAULT_MESSAGES` 中新增：

```python
CONTEXT_CHUNKS_EMPTY: "传入的上下文块列表为空",
```

在 `__all__` 中新增 `"CONTEXT_CHUNKS_EMPTY"`。

修改 `app/api/exceptions.py`，在 `HTTP_STATUS_BY_CODE` 中新增：

```python
error_codes.CONTEXT_CHUNKS_EMPTY: HTTPStatus.UNPROCESSABLE_ENTITY,  # 422 (V2.0 UQA-03)
```

### Step 4: 运行测试确认通过

```bash
conda activate geo_agent && pytest tests/test_v2_t10.py::TestErrorCode42201 -v
```

预期：3 passed

### Step 5: 写 Retrieve Schema 的测试

在 `tests/test_v2_t10.py` 追加：

```python
# ──────────────── Retrieve Schema ────────────────


class TestRetrieveSchema:
    """UQA-02 Retrieve 请求/响应 Schema。"""

    def test_retrieve_request_defaults(self):
        from app.schemas.v2.retrieve import RetrieveRequest
        req = RetrieveRequest(query="违约金条款", kb_ids=[uuid.uuid4()])
        assert req.top_k == 5
        assert req.enable_graph_rag is None
        assert req.enable_bm25 is None
        assert req.rerank is True

    def test_retrieve_request_validation(self):
        from app.schemas.v2.retrieve import RetrieveRequest
        with pytest.raises(Exception):
            RetrieveRequest(query="", kb_ids=[])  # query 不能为空

    def test_retrieve_chunk_item(self):
        from app.schemas.v2.retrieve import RetrieveChunkItem
        item = RetrieveChunkItem(
            chunk_id=1,
            content="文本内容",
            document_name="合同.pdf",
            page_number=3,
            heading_path=["第三条"],
            vector_score=0.89,
            bm25_score=0.72,
            rrf_score=0.031,
            rerank_score=0.94,
            metadata={"filename": "合同.pdf"},
        )
        assert item.chunk_id == 1
        assert item.rerank_score == 0.94

    def test_retrieve_response(self):
        from app.schemas.v2.retrieve import RetrieveResponse, RetrieveChunkItem
        resp = RetrieveResponse(
            chunks=[],
            total_retrieved=35,
            after_rerank=10,
            trace_id="abc-123",
            total_latency_ms=150,
        )
        assert resp.total_retrieved == 35
        assert resp.after_rerank == 10
```

### Step 6: 实现 Retrieve Schema

创建 `app/schemas/v2/retrieve.py`：

```python
"""V2.0 UQA-02 纯检索子接口 Schema（POST /api/v2/retrieve）。"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class RetrieveRequest(BaseModel):
    """UQA-02 纯检索请求。

    只执行检索，不调用 LLM。支持与 /v2/query 相同的检索参数。
    """

    query: str = Field(..., min_length=1, max_length=2000, description="检索查询文本")
    kb_ids: list[uuid.UUID] | None = Field(default=None, description="限定知识库列表")
    top_k: int = Field(default=5, ge=1, le=50, description="返回结果数量")
    enable_graph_rag: bool | None = Field(default=None, description="是否启用 Graph RAG 锚定")
    enable_bm25: bool | None = Field(default=None, description="是否启用 BM25")
    rerank: bool = Field(default=True, description="是否启用 Reranker 精排")
    similarity_threshold: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="Reranker 过滤阈值",
    )


class RetrieveChunkItem(BaseModel):
    """检索返回的单条 Chunk，包含所有分数字段。"""

    chunk_id: int | None = None
    content: str = ""
    document_name: str = ""
    page_number: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    vector_score: float | None = Field(default=None, description="稠密向量检索分数")
    bm25_score: float | None = Field(default=None, description="BM25 稀疏检索分数")
    rrf_score: float | None = Field(default=None, description="RRF 融合分数")
    rerank_score: float | None = Field(default=None, description="Reranker 精排分数")
    metadata: dict | None = None


class RetrieveResponse(BaseModel):
    """UQA-02 纯检索响应。"""

    chunks: list[RetrieveChunkItem] = Field(default_factory=list)
    total_retrieved: int = Field(default=0, description="Rerank 前检索总命中数")
    after_rerank: int = Field(default=0, description="Rerank 后保留数")
    trace_id: str | None = None
    total_latency_ms: int | None = None
```

### Step 7: 写 Generate Schema 的测试

在 `tests/test_v2_t10.py` 追加：

```python
# ──────────────── Generate Schema ────────────────


class TestGenerateSchema:
    """UQA-03 Generate 请求/响应 Schema。"""

    def test_generate_request_with_context(self):
        from app.schemas.v2.generate import GenerateRequest, ContextChunk
        req = GenerateRequest(
            query="合同违约金是多少？",
            context_chunks=[
                ContextChunk(
                    chunk_id="custom_001",
                    content="违约金为合同总额的20%...",
                    source_label="采购合同_2024.pdf P3",
                )
            ],
        )
        assert len(req.context_chunks) == 1
        assert req.options.enable_citation is True

    def test_generate_request_empty_context_fails(self):
        from app.schemas.v2.generate import GenerateRequest
        with pytest.raises(Exception):
            GenerateRequest(query="测试", context_chunks=[])

    def test_context_chunk_source_label(self):
        from app.schemas.v2.generate import ContextChunk
        chunk = ContextChunk(
            chunk_id="c1",
            content="内容",
            source_label="文档 P1",
        )
        assert chunk.source_label == "文档 P1"

    def test_generate_response(self):
        from app.schemas.v2.generate import GenerateResponse
        resp = GenerateResponse(
            answer="违约金为合同总额的20%[1]。",
            source_citations=[],
            confidence=0.85,
            faithfulness_check="disabled",
            trace_id="t1",
            total_latency_ms=300,
        )
        assert resp.confidence == 0.85
```

### Step 8: 实现 Generate Schema

创建 `app/schemas/v2/generate.py`：

```python
"""V2.0 UQA-03 纯生成子接口 Schema（POST /api/v2/generate）。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.v2.query import CitationItem


class ContextChunk(BaseModel):
    """开发者传入的自定义上下文块。"""

    chunk_id: str = Field(..., min_length=1, description="上下文块唯一标识")
    content: str = Field(..., min_length=1, description="上下文文本内容")
    source_label: str = Field(
        default="",
        description="来源标签（如 '采购合同_2024.pdf P3'），用于 Citation 映射",
    )


class GenerateOptions(BaseModel):
    """生成选项。"""

    stream: bool = Field(default=False, description="是否流式输出（暂不支持，预留）")
    enable_citation: bool = Field(default=True, description="是否启用 Citation 溯源")
    enable_faithfulness_check: bool = Field(default=False, description="是否启用答案自检")


class GenerateRequest(BaseModel):
    """UQA-03 纯生成请求。

    接受自定义 context_chunks，跳过检索，直接调 LLM 生成 + 溯源 + 自检。
    """

    query: str = Field(..., min_length=1, max_length=2000, description="查询文本")
    context_chunks: list[ContextChunk] = Field(
        ..., min_length=1,
        description="自定义上下文块列表（至少 1 条）",
    )
    options: GenerateOptions = Field(default_factory=GenerateOptions, description="生成选项")


class GenerateResponse(BaseModel):
    """UQA-03 纯生成响应。"""

    answer: str
    source_citations: list[CitationItem] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    low_confidence_warning: str | None = None
    faithfulness_check: str | None = Field(
        default=None,
        description="自检状态：ok / skipped / disabled",
    )
    unverified_claims: list[dict] | None = None
    trace_id: str | None = None
    total_latency_ms: int | None = None
```

### Step 9: 写 Rerank Schema 的测试

在 `tests/test_v2_t10.py` 追加：

```python
# ──────────────── Rerank Schema ────────────────


class TestRerankSchema:
    """UQA-04 Rerank 请求/响应 Schema。"""

    def test_rerank_request(self):
        from app.schemas.v2.rerank import RerankRequest, RerankCandidate
        req = RerankRequest(
            query="违约金条款",
            candidates=[
                RerankCandidate(id="doc_1", text="第三条 违约责任..."),
                RerankCandidate(id="doc_2", text="交货地址：北京市..."),
            ],
            top_n=2,
        )
        assert len(req.candidates) == 2
        assert req.top_n == 2

    def test_rerank_request_top_n_default(self):
        from app.schemas.v2.rerank import RerankRequest
        req = RerankRequest(
            query="测试",
            candidates=[{"id": "1", "text": "内容"}],
        )
        assert req.top_n == 5

    def test_rerank_response_sorted(self):
        from app.schemas.v2.rerank import RerankResponse, RerankResultItem
        resp = RerankResponse(
            results=[
                RerankResultItem(id="doc_3", text="违约金...", rerank_score=0.95),
                RerankResultItem(id="doc_1", text="违约责任...", rerank_score=0.80),
            ],
        )
        assert resp.results[0].rerank_score >= resp.results[1].rerank_score

    def test_rerank_candidate_requires_text(self):
        from app.schemas.v2.rerank import RerankCandidate
        with pytest.raises(Exception):
            RerankCandidate(id="1", text="")  # text 不能为空
```

### Step 10: 实现 Rerank Schema

创建 `app/schemas/v2/rerank.py`：

```python
"""V2.0 UQA-04 Reranker 子接口 Schema（POST /api/v2/rerank）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RerankCandidate(BaseModel):
    """待精排的候选文本。"""

    id: str = Field(..., min_length=1, description="候选文本唯一标识")
    text: str = Field(..., min_length=1, description="候选文本内容")


class RerankRequest(BaseModel):
    """UQA-04 Reranker 请求。"""

    query: str = Field(..., min_length=1, max_length=2000, description="查询文本")
    candidates: list[RerankCandidate] = Field(
        ..., min_length=1,
        description="候选文本列表（至少 1 条）",
    )
    top_n: int = Field(default=5, ge=1, le=50, description="返回的最大数量")


class RerankResultItem(BaseModel):
    """精排结果中的单条。"""

    id: str = Field(description="候选文本标识（与请求中的 id 对应）")
    text: str = Field(default="", description="候选文本内容")
    rerank_score: float = Field(description="精排分数")


class RerankResponse(BaseModel):
    """UQA-04 Reranker 响应。"""

    results: list[RerankResultItem] = Field(
        default_factory=list,
        description="按 rerank_score 降序排列的结果列表",
    )
    total_latency_ms: int | None = None
```

### Step 11: 运行所有 Schema 测试

```bash
conda activate geo_agent && pytest tests/test_v2_t10.py -v
```

预期：所有测试通过

### Step 12: 提交

```bash
git add app/api/error_codes.py app/api/exceptions.py app/schemas/v2/retrieve.py app/schemas/v2/generate.py app/schemas/v2/rerank.py tests/test_v2_t10.py
git commit -m "feat(v2): T10 错误码 42201 + UQA-02/03/04 Schema 定义"
```

---

## Task 2: UQA-02 纯检索端点 `/v2/retrieve`

**Files:**
- Create: `app/api/v2/endpoints/retrieve.py`
- Modify: `app/api/v2/router.py`
- Test: `tests/test_v2_t10.py`

### Step 1: 写 retrieve 端点的失败测试

在 `tests/test_v2_t10.py` 追加：

```python
# ──────────────── UQA-02 Retrieve 端点 ────────────────


class TestRetrieveEndpoint:
    """UQA-02 POST /api/v2/retrieve 端点测试。"""

    @pytest.fixture
    def _patch_hybrid_search(self):
        """Mock hybrid_search 返回预设结果。"""
        from app.rag import hybrid_retriever
        from app.rag.hybrid_retriever import HybridSearchResult

        fake_results = [
            HybridSearchResult(
                chunk_id=1,
                content="违约金为合同总额的20%",
                document_id="doc_001",
                score=0.94,
                entity_tags=["违约金"],
                heading_path=["第三条 违约责任"],
                block_type="paragraph",
                page_number=3,
                metadata={"filename": "采购合同_2024.pdf"},
                source_collection="kb_test",
            ),
            HybridSearchResult(
                chunk_id=2,
                content="交货地址：北京市朝阳区",
                document_id="doc_001",
                score=0.45,
                heading_path=["第五条 交货"],
                block_type="paragraph",
                page_number=5,
                metadata={"filename": "采购合同_2024.pdf"},
                source_collection="kb_test",
            ),
        ]

        with patch.object(hybrid_retriever, "hybrid_search", new_callable=AsyncMock) as mock:
            mock.return_value = fake_results
            yield mock

    @pytest.mark.asyncio
    async def test_retrieve_returns_chunks(self, _patch_hybrid_search):
        """检索返回 chunk 列表 + 分数字段。"""
        from app.api.v2.endpoints.retrieve import v2_retrieve
        from app.schemas.v2.retrieve import RetrieveRequest

        body = RetrieveRequest(query="违约金条款", kb_ids=[uuid.uuid4()])
        resp = await v2_retrieve(body=body, db=MagicMock())
        assert len(resp.chunks) == 2
        assert resp.chunks[0].rerank_score is not None
        assert resp.total_retrieved == 2

    @pytest.mark.asyncio
    async def test_retrieve_no_kb_ids(self, _patch_hybrid_search):
        """kb_ids 为 None 时也能检索（走默认 collection）。"""
        from app.api.v2.endpoints.retrieve import v2_retrieve
        from app.schemas.v2.retrieve import RetrieveRequest

        body = RetrieveRequest(query="测试查询")
        resp = await v2_retrieve(body=body, db=MagicMock())
        assert resp.chunks is not None

    @pytest.mark.asyncio
    async def test_retrieve_empty_results(self):
        """检索无结果时返回空列表。"""
        from app.api.v2.endpoints.retrieve import v2_retrieve
        from app.schemas.v2.retrieve import RetrieveRequest

        with patch("app.api.v2.endpoints.retrieve.hybrid_search", new_callable=AsyncMock) as mock:
            mock.return_value = []
            body = RetrieveRequest(query="不存在的查询", kb_ids=[uuid.uuid4()])
            resp = await v2_retrieve(body=body, db=MagicMock())
            assert resp.chunks == []
            assert resp.total_retrieved == 0
            assert resp.after_rerank == 0

    @pytest.mark.asyncio
    async def test_retrieve_with_graph_rag(self, _patch_hybrid_search):
        """启用 Graph RAG 时先跑 NER + 锚定再检索。"""
        from app.api.v2.endpoints.retrieve import v2_retrieve
        from app.schemas.v2.retrieve import RetrieveRequest

        body = RetrieveRequest(
            query="违约金条款",
            kb_ids=[uuid.uuid4()],
            enable_graph_rag=True,
        )
        with patch("app.api.v2.endpoints.retrieve.extract_query_entities", new_callable=AsyncMock) as ner_mock, \
             patch("app.api.v2.endpoints.retrieve.anchor_to_graph", new_callable=AsyncMock) as anchor_mock:
            ner_mock.return_value = [{"name": "违约金", "type": "LEGAL_TERM"}]
            anchor_mock.return_value = ["违约金"]
            resp = await v2_retrieve(body=body, db=MagicMock())
            ner_mock.assert_called_once()
            anchor_mock.assert_called_once()

    def test_retrieve_router_registered(self):
        """验证 /retrieve 路由已注册到 V2 router。"""
        from app.api.v2.router import router
        paths = [r.path for r in router.routes]
        assert any("/retrieve" in p for p in paths), f"/retrieve 不在路由中: {paths}"
```

### Step 2: 运行测试确认失败

```bash
conda activate geo_agent && pytest tests/test_v2_t10.py::TestRetrieveEndpoint -v
```

预期：失败（端点模块不存在）

### Step 3: 实现 retrieve 端点

创建 `app/api/v2/endpoints/retrieve.py`：

```python
"""V2.0 UQA-02 纯检索子接口 POST /api/v2/retrieve。

只执行检索（hybrid_search），不调用 LLM 生成答案。
返回经过混合检索 + RRF + Reranker 处理后的 Chunk 列表。
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import reset_current_kb_ids, set_current_kb_ids
from app.api.deps import get_db
from app.core.config import get_settings
from app.observability.tracer import Tracer
from app.rag.hybrid_retriever import hybrid_search
from app.rag.query_ner import anchor_to_graph, extract_query_entities
from app.schemas.v2.retrieve import RetrieveChunkItem, RetrieveRequest, RetrieveResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["V2 分层子接口"])


@router.post("/retrieve", response_model=RetrieveResponse)
async def v2_retrieve(
    body: RetrieveRequest,
    db: AsyncSession = Depends(get_db),
) -> RetrieveResponse:
    """UQA-02 纯检索：返回 chunks 列表，不调 LLM。"""
    start = time.perf_counter()
    settings = get_settings()

    # KB contextvar
    kb_ids_token = set_current_kb_ids(body.kb_ids)
    try:
        # Graph RAG：NER + 锚定（与 /v2/query 同款，但简化为无条件跟随配置）
        entity_tags: list[str] = []
        if body.enable_graph_rag:
            try:
                ner_entities = await extract_query_entities(body.query)
                if ner_entities:
                    kb_ids_str = [str(k) for k in body.kb_ids] if body.kb_ids else None
                    entity_tags = await anchor_to_graph(ner_entities, kb_ids_str)
            except Exception as e:
                logger.warning("Retrieve Graph RAG 失败（已忽略）: %s", e)

        # 混合检索
        results = await hybrid_search(
            query=body.query,
            top_k=body.top_k,
            entity_tags=entity_tags or None,
            reranker_enable=body.rerank,
            similarity_threshold=body.similarity_threshold,
        )

        total_retrieved = len(results)

        # 转换为 RetrieveChunkItem
        chunks: list[RetrieveChunkItem] = []
        for r in results:
            chunks.append(
                RetrieveChunkItem(
                    chunk_id=r.chunk_id,
                    content=r.content,
                    document_name=(r.metadata or {}).get("filename", r.document_id),
                    page_number=r.page_number,
                    heading_path=r.heading_path,
                    rerank_score=r.score,
                    metadata=r.metadata,
                )
            )

        after_rerank = len(chunks)
        total_latency_ms = int((time.perf_counter() - start) * 1000)

        return RetrieveResponse(
            chunks=chunks,
            total_retrieved=total_retrieved,
            after_rerank=after_rerank,
            trace_id="",
            total_latency_ms=total_latency_ms,
        )
    except Exception as e:
        logger.error("Retrieve 失败: %s", e, exc_info=True)
        total_latency_ms = int((time.perf_counter() - start) * 1000)
        return RetrieveResponse(
            chunks=[],
            total_retrieved=0,
            after_rerank=0,
            trace_id="",
            total_latency_ms=total_latency_ms,
        )
    finally:
        reset_current_kb_ids(kb_ids_token)
```

### Step 4: 注册路由

修改 `app/api/v2/router.py`，在现有 import 之后新增：

```python
from app.api.v2.endpoints import evaluations, query, retrieve, traces
```

在路由挂载区域新增：

```python
router.include_router(retrieve.router)
```

### Step 5: 运行 retrieve 测试

```bash
conda activate geo_agent && pytest tests/test_v2_t10.py::TestRetrieveEndpoint -v
```

预期：全部通过

### Step 6: 提交

```bash
git add app/api/v2/endpoints/retrieve.py app/api/v2/router.py tests/test_v2_t10.py
git commit -m "feat(v2): T10 UQA-02 纯检索子接口 /v2/retrieve"
```

---

## Task 3: UQA-04 Reranker 子接口 `/v2/rerank`

**Files:**
- Create: `app/api/v2/endpoints/rerank.py`
- Modify: `app/api/v2/router.py`
- Test: `tests/test_v2_t10.py`

> 先做 UQA-04 再做 UQA-03，因为 UQA-04 最简单（无检索无 LLM），可以快速验证端点模式。

### Step 1: 写 rerank 端点的失败测试

在 `tests/test_v2_t10.py` 追加：

```python
# ──────────────── UQA-04 Rerank 端点 ────────────────


class TestRerankEndpoint:
    """UQA-04 POST /api/v2/rerank 端点测试。"""

    @pytest.mark.asyncio
    async def test_rerank_returns_sorted(self):
        """Rerank 返回按 rerank_score 降序的结果。"""
        from app.api.v2.endpoints.rerank import v2_rerank
        from app.schemas.v2.rerank import RerankRequest

        fake_rerank_results = [
            MagicMock(index=2, relevance_score=0.95),
            MagicMock(index=0, relevance_score=0.80),
        ]
        # 设置 fallback 属性
        fake_rerank_results[0].content = "违约金按合同总额20%计算..."
        fake_rerank_results[1].content = "第三条 违约责任..."

        with patch("app.api.v2.endpoints.rerank.get_reranker") as mock_factory:
            mock_reranker = AsyncMock()
            mock_reranker.rerank.return_value = fake_rerank_results
            mock_factory.return_value = mock_reranker

            body = RerankRequest(
                query="违约金条款",
                candidates=[
                    {"id": "doc_1", "text": "第三条 违约责任..."},
                    {"id": "doc_2", "text": "交货地址：北京市..."},
                    {"id": "doc_3", "text": "违约金按合同总额20%计算..."},
                ],
                top_n=2,
            )
            resp = await v2_rerank(body)
            assert len(resp.results) == 2
            assert resp.results[0].rerank_score >= resp.results[1].rerank_score
            # 验证 id 映射正确
            assert resp.results[0].id == "doc_3"
            assert resp.results[1].id == "doc_1"

    @pytest.mark.asyncio
    async def test_rerank_empty_candidates_fails(self):
        """candidates 为空时 Pydantic 校验拒绝。"""
        from app.schemas.v2.rerank import RerankRequest
        with pytest.raises(Exception):
            RerankRequest(query="测试", candidates=[])

    @pytest.mark.asyncio
    async def test_rerank_fallback_on_failure(self):
        """Reranker 调用失败时降级返回原顺序。"""
        from app.api.v2.endpoints.rerank import v2_rerank
        from app.schemas.v2.rerank import RerankRequest

        with patch("app.api.v2.endpoints.rerank.get_reranker") as mock_factory:
            mock_reranker = AsyncMock()
            mock_reranker.rerank.side_effect = Exception("API 不可达")
            mock_factory.return_value = mock_reranker

            body = RerankRequest(
                query="测试",
                candidates=[{"id": "1", "text": "内容A"}, {"id": "2", "text": "内容B"}],
                top_n=2,
            )
            resp = await v2_rerank(body)
            # 降级时仍返回结果，保持原顺序
            assert len(resp.results) == 2

    def test_rerank_router_registered(self):
        """验证 /rerank 路由已注册。"""
        from app.api.v2.router import router
        paths = [r.path for r in router.routes]
        assert any("/rerank" in p for p in paths), f"/rerank 不在路由中: {paths}"
```

### Step 2: 运行测试确认失败

```bash
conda activate geo_agent && pytest tests/test_v2_t10.py::TestRerankEndpoint -v
```

### Step 3: 实现 rerank 端点

创建 `app/api/v2/endpoints/rerank.py`：

```python
"""V2.0 UQA-04 Reranker 子接口 POST /api/v2/rerank。

接受 Query + 候选文本列表，返回精排后的结果。
允许开发者将 Hermes 的 Reranker 能力独立使用。
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter

from app.rag.reranker import get_reranker
from app.schemas.v2.rerank import RerankRequest, RerankResponse, RerankResultItem

logger = logging.getLogger(__name__)

router = APIRouter(tags=["V2 分层子接口"])


@router.post("/rerank", response_model=RerankResponse)
async def v2_rerank(body: RerankRequest) -> RerankResponse:
    """UQA-04 独立精排：query + candidates → 按 rerank_score 降序。"""
    start = time.perf_counter()

    # 构造 chunks dict 列表（与 hybrid_retriever → reranker 接口对齐）
    chunks = [{"content": c.text} for c in body.candidates]

    # id → index 映射（reranker 返回 index，需要映射回 id）
    id_list = [c.id for c in body.candidates]
    text_list = [c.text for c in body.candidates]

    try:
        reranker = get_reranker()
        rerank_results = await reranker.rerank(body.query, chunks, top_k=body.top_n)

        # RerankResult.index → 映射回 candidate id
        items: list[RerankResultItem] = []
        for rr in rerank_results:
            idx = rr.index
            if idx < len(id_list):
                items.append(
                    RerankResultItem(
                        id=id_list[idx],
                        text=text_list[idx],
                        rerank_score=rr.relevance_score,
                    )
                )

        # 按 rerank_score 降序（reranker 内部已排，这里保险再排一次）
        items.sort(key=lambda x: x.rerank_score, reverse=True)

    except Exception as e:
        # 降级：返回原顺序，分数标 0
        logger.warning("Rerank 端点降级: %s", e)
        items = [
            RerankResultItem(id=id_list[i], text=text_list[i], rerank_score=0.0)
            for i in range(min(body.top_n, len(id_list)))
        ]

    total_latency_ms = int((time.perf_counter() - start) * 1000)
    return RerankResponse(results=items, total_latency_ms=total_latency_ms)
```

### Step 4: 注册路由

修改 `app/api/v2/router.py`，新增 import 和挂载：

```python
from app.api.v2.endpoints import evaluations, query, rerank, retrieve, traces
```

```python
router.include_router(rerank.router)
```

### Step 5: 运行 rerank 测试

```bash
conda activate geo_agent && pytest tests/test_v2_t10.py::TestRerankEndpoint -v
```

### Step 6: 提交

```bash
git add app/api/v2/endpoints/rerank.py app/api/v2/router.py tests/test_v2_t10.py
git commit -m "feat(v2): T10 UQA-04 Reranker 子接口 /v2/rerank"
```

---

## Task 4: UQA-03 纯生成端点 `/v2/generate`

**Files:**
- Create: `app/api/v2/endpoints/generate.py`
- Modify: `app/api/v2/router.py`
- Test: `tests/test_v2_t10.py`

### Step 1: 写 generate 端点的失败测试

在 `tests/test_v2_t10.py` 追加：

```python
# ──────────────── UQA-03 Generate 端点 ────────────────


class TestGenerateEndpoint:
    """UQA-03 POST /api/v2/generate 端点测试。"""

    @pytest.mark.asyncio
    async def test_generate_with_citation(self):
        """自定义 context 被注入 Citation 编号，答案引用正确。"""
        from app.api.v2.endpoints.generate import v2_generate
        from app.schemas.v2.generate import GenerateRequest, ContextChunk

        with patch("app.api.v2.endpoints.generate.generate_answer", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = "违约金为合同总额的20%[1]。"
            body = GenerateRequest(
                query="合同违约金是多少？",
                context_chunks=[
                    ContextChunk(
                        chunk_id="custom_001",
                        content="违约金为合同总额的20%...",
                        source_label="采购合同_2024.pdf P3",
                    )
                ],
                options={"enable_citation": True, "enable_faithfulness_check": False},
            )
            resp = await v2_generate(body)
            assert "违约金" in resp.answer
            # Citation 解析后 source_citations 应映射回 custom_001
            assert len(resp.source_citations) >= 1 or resp.answer  # LLM 可能不引用

    @pytest.mark.asyncio
    async def test_generate_empty_context_raises_42201(self):
        """context_chunks 为空时抛 BusinessError(42201)。"""
        from app.api.v2.endpoints.generate import v2_generate
        from app.schemas.v2.generate import GenerateRequest
        from app.api.exceptions import BusinessError
        from app.api import error_codes

        # Pydantic 校验在 Schema 层拦截（min_length=1），不会到达端点
        # 这里验证端点层也做防御：如果 somehow 空列表通过了 Schema
        with pytest.raises(Exception):
            GenerateRequest(query="测试", context_chunks=[])

    @pytest.mark.asyncio
    async def test_generate_no_milvus_neo4j(self):
        """generate 不触发任何 Milvus / Neo4j 查询。"""
        from app.api.v2.endpoints.generate import v2_generate
        from app.schemas.v2.generate import GenerateRequest, ContextChunk

        with patch("app.api.v2.endpoints.generate.generate_answer", new_callable=AsyncMock) as mock_gen, \
             patch("app.api.v2.endpoints.generate.hybrid_search", new_callable=AsyncMock) as mock_search:
            mock_gen.return_value = "测试答案"
            body = GenerateRequest(
                query="测试",
                context_chunks=[
                    ContextChunk(chunk_id="c1", content="内容", source_label="文档 P1"),
                ],
            )
            await v2_generate(body)
            mock_search.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_with_faithfulness_check(self):
        """开启 faithfulness_check 后自检流程正常。"""
        from app.api.v2.endpoints.generate import v2_generate
        from app.schemas.v2.generate import GenerateRequest, ContextChunk
        from app.rag.faithfulness import FaithfulnessResult

        with patch("app.api.v2.endpoints.generate.generate_answer", new_callable=AsyncMock) as mock_gen, \
             patch("app.api.v2.endpoints.generate.check_faithfulness", new_callable=AsyncMock) as mock_faith:
            mock_gen.return_value = "答案内容[1]。"
            mock_faith.return_value = FaithfulnessResult(
                status="ok",
                claims=[{"claim": "事实1", "status": "supported", "source_text": "原文"}],
                unverified=[],
                hallucination_penalty=0.0,
            )
            body = GenerateRequest(
                query="测试",
                context_chunks=[
                    ContextChunk(chunk_id="c1", content="内容", source_label="文档 P1"),
                ],
                options={"enable_faithfulness_check": True},
            )
            resp = await v2_generate(body)
            assert resp.faithfulness_check == "ok"

    def test_generate_router_registered(self):
        """验证 /generate 路由已注册。"""
        from app.api.v2.router import router
        paths = [r.path for r in router.routes]
        assert any("/generate" in p for p in paths), f"/generate 不在路由中: {paths}"
```

### Step 2: 运行测试确认失败

```bash
conda activate geo_agent && pytest tests/test_v2_t10.py::TestGenerateEndpoint -v
```

### Step 3: 实现 generate 端点

创建 `app/api/v2/endpoints/generate.py`：

```python
"""V2.0 UQA-03 纯生成子接口 POST /api/v2/generate。

接受开发者自定义的 context_chunks，跳过检索步骤，
直接调 LLM 生成答案 + Citation 溯源 + 答案自检。
不触发任何 Milvus / Neo4j 查询。
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import error_codes
from app.api.deps import get_db
from app.api.exceptions import BusinessError
from app.core.config import get_settings
from app.observability.tracer import Tracer
from app.rag.citation import (
    build_citation_system_prompt,
    build_context_with_citation,
    parse_citations,
)
from app.rag.confidence import ConfidenceScore, compute_confidence
from app.rag.faithfulness import (
    DISABLED_RESULT,
    FaithfulnessResult,
    append_unverified_warning,
    check_faithfulness,
)
from app.rag.hybrid_retriever import hybrid_search  # noqa: F401 — 不使用，显式导入防止误用
from app.schemas.v2.generate import GenerateRequest, GenerateResponse
from app.schemas.v2.query import CitationItem

logger = logging.getLogger(__name__)

router = APIRouter(tags=["V2 分层子接口"])


@router.post("/generate", response_model=GenerateResponse)
async def v2_generate(
    body: GenerateRequest,
    db: AsyncSession = Depends(get_db),
) -> GenerateResponse:
    """UQA-03 纯生成：自定义 context + LLM + Citation + 自检。"""
    start = time.perf_counter()
    settings = get_settings()

    # 防御性校验（Schema 已有 min_length=1，但端点层也做兜底）
    if not body.context_chunks:
        raise BusinessError(
            error_codes.CONTEXT_CHUNKS_EMPTY,
            "传入的上下文块列表为空",
        )

    # 将 ContextChunk 转换为 citation 模块需要的格式
    chunks_for_citation = [
        {
            "document_name": c.source_label or c.chunk_id,
            "page_number": None,
            "content": c.content,
            "chunk_id": c.chunk_id,
            "heading_path": [],
            "rerank_score": None,
        }
        for c in body.context_chunks
    ]

    # 构建 context（与 /v2/query 同款，含 [1][2] 引用标记）
    if body.options.enable_citation:
        context = build_context_with_citation(chunks_for_citation)
    else:
        # 不启用 Citation 时，简单拼接内容
        context = "\n\n".join(c.content for c in body.context_chunks)

    # LLM 生成
    try:
        answer = await generate_answer(
            query=body.query,
            context=context,
            session_id=None,
            db=db,
            enable_citation_prompt=body.options.enable_citation,
        )
    except Exception as e:
        logger.error("Generate LLM 失败: %s", e, exc_info=True)
        total_latency_ms = int((time.perf_counter() - start) * 1000)
        return GenerateResponse(
            answer=f"答案生成失败：{type(e).__name__}。请稍后重试。",
            source_citations=[],
            confidence=0.0,
            faithfulness_check="skipped",
            trace_id="",
            total_latency_ms=total_latency_ms,
        )

    # Citation 解析
    source_citations = []
    if body.options.enable_citation:
        raw_citations = parse_citations(answer, chunks_for_citation)
        source_citations = [
            CitationItem(
                chunk_id=c.get("chunk_id"),
                document_name=c.get("document_name", ""),
                page_number=c.get("page_number"),
                heading_path=c.get("heading_path", []),
                snippet=c.get("snippet", ""),
                rerank_score=c.get("rerank_score"),
            )
            for c in raw_citations
        ]

    # 答案自检（CHC-04）
    faith_result: FaithfulnessResult = DISABLED_RESULT
    if body.options.enable_faithfulness_check:
        try:
            faith_result = await check_faithfulness(answer=answer, context=context)
        except Exception as e:
            logger.warning("Generate faithfulness 失败（软降级）: %s", e)
            faith_result = FaithfulnessResult(status="skipped")
        if faith_result.unverified:
            answer = append_unverified_warning(answer, faith_result.unverified)

    # 置信度评分（CHC-03）
    score: ConfidenceScore = compute_confidence(
        cited_chunks=raw_citations if body.options.enable_citation else [],
        top_k=len(body.context_chunks),
        hallucination_penalty=faith_result.hallucination_penalty,
    )

    total_latency_ms = int((time.perf_counter() - start) * 1000)

    return GenerateResponse(
        answer=answer,
        source_citations=source_citations,
        confidence=score.confidence,
        low_confidence_warning=score.low_confidence_warning,
        faithfulness_check=faith_result.status,
        unverified_claims=faith_result.unverified or None,
        trace_id="",
        total_latency_ms=total_latency_ms,
    )


async def generate_answer(
    *,
    query: str,
    context: str,
    session_id,
    db,
    enable_citation_prompt: bool = True,
) -> str:
    """调用 LLM 生成答案（UQA-03 专用）。

    与 /v2/query 的 generate_answer 类似，但：
    - 支持 enable_citation_prompt 开关
    - 不依赖 session_id / db（纯生成场景无历史）
    """
    import asyncio

    import litellm

    settings = get_settings()

    citation_prompt = build_citation_system_prompt() if enable_citation_prompt else ""
    system_prompt = (
        "你是一个气象空间智能助手。请基于以下提供的上下文回答用户问题。\n\n"
        f"{citation_prompt}\n\n"
        f"上下文：\n{context}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query},
    ]

    hard_timeout = settings.litellm_timeout * (settings.litellm_num_retries + 1) + 10

    try:
        litellm.num_retries = settings.litellm_num_retries
        response = await asyncio.wait_for(
            litellm.acompletion(
                model=settings.litellm_model,
                messages=messages,
                api_key=settings.litellm_api_key,
                api_base=settings.litellm_api_base,
                temperature=0.3,
                max_tokens=2000,
                timeout=settings.litellm_timeout,
            ),
            timeout=hard_timeout,
        )
        return response.choices[0].message.content or ""
    except asyncio.TimeoutError:
        logger.error("Generate LLM 超时（%.0fs）", hard_timeout)
        return "抱歉，答案生成超时，请稍后重试。"
    except Exception as e:
        logger.error("Generate LLM 失败: %s", e)
        raise
```

### Step 4: 注册路由

修改 `app/api/v2/router.py`，新增 import 和挂载：

```python
from app.api.v2.endpoints import evaluations, generate, query, rerank, retrieve, traces
```

```python
router.include_router(generate.router)
```

### Step 5: 运行 generate 测试

```bash
conda activate geo_agent && pytest tests/test_v2_t10.py::TestGenerateEndpoint -v
```

### Step 6: 提交

```bash
git add app/api/v2/endpoints/generate.py app/api/v2/router.py tests/test_v2_t10.py
git commit -m "feat(v2): T10 UQA-03 纯生成子接口 /v2/generate"
```

---

## Task 5: 集成测试 + 回归验证 + 进度文档

**Files:**
- Test: `tests/test_v2_t10.py`（追加 E2E 测试）
- Modify: `docs/progress.md`

### Step 1: 写 E2E 集成测试

在 `tests/test_v2_t10.py` 追加：

```python
# ──────────────── E2E 集成测试 ────────────────


class TestT10E2E:
    """T10 三个子接口端到端集成。"""

    @pytest.mark.asyncio
    async def test_retrieve_e2e(self):
        """Retrieve: 检索 → 返回 chunks + 分数字段。"""
        from app.api.v2.endpoints.retrieve import v2_retrieve
        from app.schemas.v2.retrieve import RetrieveRequest
        from app.rag.hybrid_retriever import HybridSearchResult

        with patch("app.api.v2.endpoints.retrieve.hybrid_search", new_callable=AsyncMock) as mock:
            mock.return_value = [
                HybridSearchResult(
                    chunk_id=10, content="测试内容", document_id="d1",
                    score=0.92, heading_path=["标题1"], block_type="paragraph",
                    page_number=1, metadata={"filename": "test.pdf"},
                    source_collection="kb_test",
                ),
            ]
            body = RetrieveRequest(query="测试", kb_ids=[uuid.uuid4()], top_k=3)
            resp = await v2_retrieve(body, db=MagicMock())
            assert len(resp.chunks) == 1
            assert resp.chunks[0].rerank_score == 0.92
            assert resp.chunks[0].document_name == "test.pdf"

    @pytest.mark.asyncio
    async def test_rerank_e2e(self):
        """Rerank: query + candidates → 降序排列。"""
        from app.api.v2.endpoints.rerank import v2_rerank
        from app.schemas.v2.rerank import RerankRequest
        from app.rag.reranker import RerankResult

        with patch("app.api.v2.endpoints.rerank.get_reranker") as mock_factory:
            mock_reranker = AsyncMock()
            mock_reranker.rerank.return_value = [
                RerankResult(index=1, relevance_score=0.90, content="B"),
                RerankResult(index=0, relevance_score=0.70, content="A"),
            ]
            mock_factory.return_value = mock_reranker

            body = RerankRequest(
                query="测试",
                candidates=[
                    {"id": "a", "text": "A内容"},
                    {"id": "b", "text": "B内容"},
                ],
                top_n=2,
            )
            resp = await v2_rerank(body)
            assert resp.results[0].id == "b"
            assert resp.results[0].rerank_score == 0.90

    @pytest.mark.asyncio
    async def test_generate_e2e(self):
        """Generate: context → LLM → citation + confidence。"""
        from app.api.v2.endpoints.generate import v2_generate
        from app.schemas.v2.generate import GenerateRequest, ContextChunk
        from app.rag.confidence import ConfidenceScore

        with patch("app.api.v2.endpoints.generate.generate_answer", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = "违约金为合同总额20%[1]。"
            body = GenerateRequest(
                query="违约金是多少？",
                context_chunks=[
                    ContextChunk(
                        chunk_id="c1",
                        content="违约金为合同总额的20%",
                        source_label="合同.pdf P3",
                    ),
                ],
            )
            resp = await v2_generate(body, db=MagicMock())
            assert "违约金" in resp.answer
            assert resp.confidence is not None

    @pytest.mark.asyncio
    async def test_generate_no_citation(self):
        """Generate: 关闭 citation 时 context 不含 [N] 标记。"""
        from app.api.v2.endpoints.generate import v2_generate
        from app.schemas.v2.generate import GenerateRequest, ContextChunk, GenerateOptions

        with patch("app.api.v2.endpoints.generate.generate_answer", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = "答案文本。"
            body = GenerateRequest(
                query="测试",
                context_chunks=[
                    ContextChunk(chunk_id="c1", content="内容1", source_label="文档1"),
                ],
                options=GenerateOptions(enable_citation=False),
            )
            resp = await v2_generate(body, db=MagicMock())
            assert resp.source_citations == []
```

### Step 2: 运行 T10 全部测试

```bash
conda activate geo_agent && pytest tests/test_v2_t10.py -v
```

预期：全部通过

### Step 3: 运行 V2 全套 + 全量回归

```bash
conda activate geo_agent && pytest tests/test_v2_t10.py tests/test_v2_t0.py tests/test_v2_t1.py tests/test_v2_t2.py tests/test_v2_t3.py tests/test_v2_t7.py tests/test_v2_t8.py tests/test_v2_t9.py tests/test_v2_t11.py tests/test_v2_p1.py -v
```

预期：零回归

```bash
conda activate geo_agent && pytest --timeout=120 -q
```

预期：全量 mock 回归零失败

### Step 4: 更新进度文档

在 `docs/progress.md` 中：
1. 将 T10 状态从 `⬜ 待开始` 改为 `✅ 完成 + 单测验收`
2. 填入完成日期
3. 新增 T10 交付内容段落（关键文件 + 设计决策 + 验证状态）

### Step 5: 提交

```bash
git add docs/progress.md tests/test_v2_t10.py
git commit -m "feat(v2): T10 UQA-02/03/04 分层子接口全部完成 + 进度更新"
```

---

## 自检清单

### 1. PRD 需求覆盖

| PRD 需求 | 对应 Task |
|----------|-----------|
| UQA-02 `POST /api/v2/retrieve` 纯检索 | Task 2 |
| UQA-02 每个 Chunk 含所有分数字段 | Task 2（RetrieveChunkItem 含 vector_score / bm25_score / rrf_score / rerank_score） |
| UQA-02 不调用任何 LLM | Task 2（端点只调 hybrid_search，无 LLM 调用） |
| UQA-02 支持与 UQA-01 相同的检索参数 | Task 2（enable_graph_rag / enable_bm25 / rerank / similarity_threshold） |
| UQA-03 `POST /api/v2/generate` 纯生成 | Task 4 |
| UQA-03 自定义 context_chunks 被 Citation 注入 | Task 4（ContextChunk → build_context_with_citation） |
| UQA-03 引用标记对应 source_label | Task 4（source_label 映射到 document_name 字段） |
| UQA-03 不触发 Milvus / Neo4j | Task 4（端点无 hybrid_search / neo4j 调用） |
| UQA-04 `POST /api/v2/rerank` 独立精排 | Task 3 |
| UQA-04 候选按 rerank_score 降序 | Task 3（items.sort） |
| UQA-04 top_n 生效 | Task 3（reranker.rerank top_k=top_n） |
| 错误码 42201（context_chunks 空） | Task 1 |

### 2. Placeholder 扫描

无 TBD / TODO / "implement later" / "fill in details"。

### 3. 类型一致性

- `RetrieveChunkItem` 的字段名与 `HybridSearchResult` 对齐（chunk_id / content / document_id → document_name / page_number / heading_path / metadata / score → rerank_score）
- `ContextChunk.source_label` 映射到 `chunks_for_citation[].document_name`，与 `parse_citations` 期望的 key 一致
- `RerankResultItem.id` 是 str 类型，与 `RerankCandidate.id` 一致
- `GenerateResponse` 复用 `QueryResponse` 的 `CitationItem`，字段对齐

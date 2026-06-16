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
            RetrieveRequest(query="", kb_ids=[])

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
            RerankCandidate(id="1", text="")

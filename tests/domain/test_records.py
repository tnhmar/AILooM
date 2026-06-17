"""Tests for domain request/response objects: Scope, WriteRequest, SearchRequest, RecallRequest."""

from __future__ import annotations

from datetime import datetime

from memory_layer.domain.records import (
    RecallItem,
    RecallRequest,
    RecallResult,
    RecallStatus,
    SearchMode,
    SearchRequest,
    Scope,
    WriteRequest,
    WriteResult,
)
from memory_layer.domain.types import (
    LifecycleState,
    MemorySector,
    PayloadType,
    PipelineStatus,
    PrincipalId,
    TenantId,
    new_memory_id,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scope() -> Scope:
    return Scope(
        tenant_id=TenantId("t1"),
        principal_id=PrincipalId("u1"),
    )


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


class TestScope:
    def test_defaults(self) -> None:
        s = _scope()
        assert s.workspace_id is None
        assert s.session_id is None
        assert s.run_id is None

    def test_frozen(self) -> None:
        s = _scope()
        try:
            s.tenant_id = TenantId("x")  # type: ignore[misc]
            assert False, "Should have raised"
        except Exception:
            pass


# ---------------------------------------------------------------------------
# WriteRequest
# ---------------------------------------------------------------------------


class TestWriteRequest:
    def test_defaults(self) -> None:
        req = WriteRequest(
            tenant_id=TenantId("t1"),
            scope=_scope(),
            raw_payload="hello",
            payload_type=PayloadType.CONVERSATION_TURN,
        )
        assert req.extract is True
        assert req.sector is None
        assert req.idempotency_key is None
        assert req.wait_for_enrichment is False
        assert req.metadata == {}


# ---------------------------------------------------------------------------
# WriteResult
# ---------------------------------------------------------------------------


class TestWriteResult:
    def test_fields(self) -> None:
        mid = new_memory_id()
        result = WriteResult(
            memory_id=mid,
            scope=_scope(),
            pipeline_status=PipelineStatus.PENDING,
            accepted_at=datetime.utcnow(),
        )
        assert result.memory_id == mid
        assert result.idempotent is False


# ---------------------------------------------------------------------------
# SearchRequest
# ---------------------------------------------------------------------------


class TestSearchRequest:
    def test_defaults(self) -> None:
        req = SearchRequest(
            tenant_id=TenantId("t1"),
            scope=_scope(),
            query="what did I say yesterday?",
        )
        assert req.mode == SearchMode.HYBRID
        assert req.sectors is None
        assert req.lifecycle_states == [LifecycleState.ACTIVE]
        assert req.temporal_filter is None
        assert req.k == 10

    def test_sector_filter(self) -> None:
        req = SearchRequest(
            tenant_id=TenantId("t1"),
            scope=_scope(),
            query="q",
            sectors=[MemorySector.EPISODIC],
        )
        assert req.sectors == [MemorySector.EPISODIC]


# ---------------------------------------------------------------------------
# RecallRequest
# ---------------------------------------------------------------------------


class TestRecallRequest:
    def test_defaults(self) -> None:
        req = RecallRequest(
            tenant_id=TenantId("t1"),
            scope=_scope(),
            query="context for next step",
        )
        assert req.max_tokens == 4000
        assert req.max_items == 10
        assert req.include_facts is True
        assert req.include_verbatim is True
        assert req.mode == SearchMode.HYBRID


# ---------------------------------------------------------------------------
# RecallItem
# ---------------------------------------------------------------------------


class TestRecallItem:
    def test_optional_fields(self) -> None:
        item = RecallItem(
            memory_id=new_memory_id(),
            content="some memory",
            sector=MemorySector.SEMANTIC,
            lifecycle_state=LifecycleState.ACTIVE,
            pipeline_status=PipelineStatus.ENRICHED,
        )
        assert item.effective_from is None
        assert item.trace_id is None
        assert item.explanation == ""
        assert item.signals == {}


# ---------------------------------------------------------------------------
# RecallResult
# ---------------------------------------------------------------------------


class TestRecallResult:
    def test_no_match(self) -> None:
        result = RecallResult(
            status=RecallStatus.NO_MATCH,
            no_match_reason="nothing relevant found",
        )
        assert result.items == []
        assert result.total_tokens_estimate == 0

    def test_with_items(self) -> None:
        item = RecallItem(
            memory_id=new_memory_id(),
            content="ctx",
            sector=MemorySector.EPISODIC,
            lifecycle_state=LifecycleState.ACTIVE,
            pipeline_status=PipelineStatus.ENRICHED,
        )
        result = RecallResult(status=RecallStatus.MATCH, items=[item])
        assert len(result.items) == 1
        assert result.status == RecallStatus.MATCH

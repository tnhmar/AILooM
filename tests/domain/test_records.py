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
        tenant_id=TenantId("t-1"),
        principal_id=PrincipalId("p-1"),
    )


def _write_request(**kwargs) -> WriteRequest:
    defaults: dict = dict(
        tenant_id=TenantId("t-1"),
        scope=_scope(),
        raw_payload="hello",
        payload_type=PayloadType.CONVERSATION_TURN,
    )
    defaults.update(kwargs)
    return WriteRequest(**defaults)


def _search_request(**kwargs) -> SearchRequest:
    defaults: dict = dict(
        tenant_id=TenantId("t-1"),
        scope=_scope(),
        query="what is the user's name?",
    )
    defaults.update(kwargs)
    return SearchRequest(**defaults)


def _recall_request(**kwargs) -> RecallRequest:
    defaults: dict = dict(
        tenant_id=TenantId("t-1"),
        scope=_scope(),
        query="what is the user's name?",
    )
    defaults.update(kwargs)
    return RecallRequest(**defaults)


def _recall_item() -> RecallItem:
    return RecallItem(
        memory_id=new_memory_id(),
        content="Alice lives in Montreal.",
        sector=MemorySector.SEMANTIC,
        lifecycle_state=LifecycleState.ACTIVE,
        pipeline_status=PipelineStatus.ENRICHED,
    )


# ---------------------------------------------------------------------------
# 1. Scope is hashable
# ---------------------------------------------------------------------------

def test_scope_is_hashable():
    scope = _scope()
    d = {scope: "value"}
    assert d[scope] == "value"


# ---------------------------------------------------------------------------
# 2. WriteRequest.extract defaults to True
# ---------------------------------------------------------------------------

def test_write_request_extract_defaults_true():
    assert _write_request().extract is True


# ---------------------------------------------------------------------------
# 3. WriteRequest.wait_for_enrichment defaults to False
# ---------------------------------------------------------------------------

def test_write_request_wait_for_enrichment_defaults_false():
    assert _write_request().wait_for_enrichment is False


# ---------------------------------------------------------------------------
# 4. WriteRequest.metadata is not shared across instances
# ---------------------------------------------------------------------------

def test_write_request_metadata_not_shared():
    a = _write_request()
    b = _write_request()
    a.metadata["x"] = 1
    assert "x" not in b.metadata


# ---------------------------------------------------------------------------
# 5. WriteResult.idempotent defaults to False
# ---------------------------------------------------------------------------

def test_write_result_idempotent_defaults_false():
    result = WriteResult(
        memory_id=new_memory_id(),
        scope=_scope(),
        pipeline_status=PipelineStatus.PENDING,
        accepted_at=datetime.utcnow(),
    )
    assert result.idempotent is False


# ---------------------------------------------------------------------------
# 6. SearchRequest.lifecycle_states defaults to [ACTIVE]
# ---------------------------------------------------------------------------

def test_search_request_lifecycle_states_default():
    req = _search_request()
    assert req.lifecycle_states == [LifecycleState.ACTIVE]


# ---------------------------------------------------------------------------
# 7. SearchRequest.lifecycle_states is not shared across instances
# ---------------------------------------------------------------------------

def test_search_request_lifecycle_states_not_shared():
    a = _search_request()
    b = _search_request()
    a.lifecycle_states.append(LifecycleState.CONSOLIDATED)
    assert LifecycleState.CONSOLIDATED not in b.lifecycle_states


# ---------------------------------------------------------------------------
# 8. RecallResult.recalled_at is auto-populated
# ---------------------------------------------------------------------------

def test_recall_result_recalled_at_auto_populated():
    result = RecallResult(status=RecallStatus.NO_MATCH)
    assert isinstance(result.recalled_at, datetime)


# ---------------------------------------------------------------------------
# 9. RecallItem.signals is not shared across instances
# ---------------------------------------------------------------------------

def test_recall_item_signals_not_shared():
    a = _recall_item()
    b = _recall_item()
    a.signals["score"] = 0.9
    assert "score" not in b.signals


# ---------------------------------------------------------------------------
# 10. RecallStatus str enum round-trip
# ---------------------------------------------------------------------------

def test_recall_status_str_roundtrip():
    assert RecallStatus("NO_MATCH") == RecallStatus.NO_MATCH


# ---------------------------------------------------------------------------
# 11. SearchMode.HYBRID has value "HYBRID"
# ---------------------------------------------------------------------------

def test_search_mode_hybrid_value():
    assert SearchMode.HYBRID == "HYBRID"
    assert SearchMode.HYBRID.value == "HYBRID"


# ---------------------------------------------------------------------------
# 12. Two RecallRequest objects with identical fields are equal
# ---------------------------------------------------------------------------

def test_recall_request_equality():
    scope = _scope()
    a = RecallRequest(tenant_id=TenantId("t-1"), scope=scope, query="q")
    b = RecallRequest(tenant_id=TenantId("t-1"), scope=scope, query="q")
    assert a == b

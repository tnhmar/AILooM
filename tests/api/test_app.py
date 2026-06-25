"""Acceptance tests for the memory-layer FastAPI app — M5-T1."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from memory_layer.api.app import (
    app,
    get_consolidate_use_case,
    get_decay_use_case,
    get_delete_use_case,
    get_explain_use_case,
    get_get_memory_use_case,
    get_notify_session_ended_use_case,
    get_recall_use_case,
    get_search_use_case,
    get_write_use_case,
)
from memory_layer.domain.exceptions import MemoryNotFoundError, TenantIsolationViolation
from memory_layer.domain.records import (
    AuditEntry,
    MemoryRecord,
    MemoryTrace,
    RecallResult,
    RecallStatus,
    Scope,
    WriteResult,
)
from memory_layer.domain.types import (
    AuditOperation,
    AuditOutcome,
    LifecycleState,
    MemorySector,
    PayloadType,
    PipelineStatus,
    PrincipalId,
    PrincipalType,
    TenantId,
    new_audit_id,
    new_memory_id,
    new_trace_id,
)
from memory_layer.ports.inbound import SearchResult, SearchResultItem

TENANT = "test-tenant"
HEADERS = {"X-Tenant-Id": TENANT}
_NOW = datetime(2024, 1, 1, tzinfo=UTC)

_SCOPE_BODY = {"principal_id": "user-1", "principal_type": "USER"}
_DOMAIN_SCOPE = Scope(
    tenant_id=TenantId(TENANT),
    principal_id=PrincipalId("user-1"),
    principal_type=PrincipalType.USER,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_write_result() -> WriteResult:
    return WriteResult(
        memory_id=new_memory_id(),
        scope=_DOMAIN_SCOPE,
        pipeline_status=PipelineStatus.PENDING,
        accepted_at=_NOW,
    )


def _make_memory_record() -> MemoryRecord:
    return MemoryRecord(
        id=new_memory_id(),
        tenant_id=TenantId(TENANT),
        scope=_DOMAIN_SCOPE,
        raw_payload="hello world",
        payload_type=PayloadType.CONVERSATION_TURN,
        sector=MemorySector.EPISODIC,
        lifecycle_state=LifecycleState.ACTIVE,
        pipeline_status=PipelineStatus.ENRICHED,
        recorded_at=_NOW,
    )


def _make_search_result() -> SearchResult:
    return SearchResult(
        items=[
            SearchResultItem(
                memory_id=new_memory_id(),
                content="some memory",
                sector=MemorySector.EPISODIC,
                score=0.9,
                pipeline_status=PipelineStatus.ENRICHED,
                lifecycle_state=LifecycleState.ACTIVE,
            )
        ],
        total=1,
        searched_at=_NOW,
    )


def _make_recall_result() -> RecallResult:
    return RecallResult(
        status=RecallStatus.MATCH,
        items=[],
        recalled_at=_NOW,
    )


def _make_memory_trace() -> MemoryTrace:
    mem_id = new_memory_id()
    trace_id = new_trace_id()
    audit = AuditEntry(
        id=new_audit_id(),
        tenant_id=TenantId(TENANT),
        scope=_DOMAIN_SCOPE,
        operation=AuditOperation.WRITE,
        memory_id=mem_id,
        outcome=AuditOutcome.SUCCESS,
    )
    return MemoryTrace(
        trace_id=trace_id,
        memory_id=mem_id,
        scope=_DOMAIN_SCOPE,
        write_event=audit,
        enrichment_status=PipelineStatus.ENRICHED,
        constructed_at=_NOW,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_overrides():
    """Ensure dependency overrides are cleaned up after each test."""
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


# 1 — /healthz returns 200 with status "ok" and the standard HealthReport fields.
# The endpoint serialises the full HealthReport dataclass (version, components,
# checked_at in addition to status), so we assert each field individually
# rather than doing an exact-equality check.
def test_healthz_returns_200() -> None:
    with TestClient(app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["version"], str)
    assert isinstance(body["components"], list)
    assert isinstance(body["checked_at"], str)


# 2  — missing X-Tenant-Id must be rejected; the TenantMiddleware raises
# TenantIsolationViolation which the error handler maps to 403. We also
# accept 400/422 in case request validation fires first on some code paths.
def test_write_requires_tenant_header() -> None:
    mock_uc = AsyncMock()
    mock_uc.execute.return_value = _make_write_result()
    app.dependency_overrides[get_write_use_case] = lambda: mock_uc

    body = {
        "scope": _SCOPE_BODY,
        "raw_payload": "hello",
        "payload_type": "CONVERSATION_TURN",
    }
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/v1/memories:write", json=body)  # no HEADERS
    assert resp.status_code in (400, 403, 422)


# 3
def test_write_returns_memory_id() -> None:
    result = _make_write_result()
    mock_uc = AsyncMock()
    mock_uc.execute.return_value = result
    app.dependency_overrides[get_write_use_case] = lambda: mock_uc

    body = {
        "scope": _SCOPE_BODY,
        "raw_payload": "hello",
        "payload_type": "CONVERSATION_TURN",
    }
    with TestClient(app) as client:
        resp = client.post("/v1/memories:write", json=body, headers=HEADERS)
    assert resp.status_code == 200
    assert "memory_id" in resp.json()


# 4
def test_search_returns_results() -> None:
    mock_uc = AsyncMock()
    mock_uc.execute.return_value = _make_search_result()
    app.dependency_overrides[get_search_use_case] = lambda: mock_uc

    body = {"scope": _SCOPE_BODY, "query": "what did I say?"}
    with TestClient(app) as client:
        resp = client.post("/v1/memories:search", json=body, headers=HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert data["total"] == 1


# 5
def test_recall_returns_results() -> None:
    mock_uc = AsyncMock()
    mock_uc.execute.return_value = _make_recall_result()
    app.dependency_overrides[get_recall_use_case] = lambda: mock_uc

    body = {"scope": _SCOPE_BODY, "query": "summarise my last session"}
    with TestClient(app) as client:
        resp = client.post("/v1/memories:recall", json=body, headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "MATCH"


# 6
def test_get_memory_returns_200() -> None:
    record = _make_memory_record()
    mock_uc = AsyncMock()
    mock_uc.execute.return_value = record
    app.dependency_overrides[get_get_memory_use_case] = lambda: mock_uc

    with TestClient(app) as client:
        resp = client.get(f"/v1/memories/{record.id}", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["memory_id"] == str(record.id)


# 7
def test_delete_memory_returns_204() -> None:
    mock_uc = AsyncMock()
    mock_uc.execute.return_value = None
    app.dependency_overrides[get_delete_use_case] = lambda: mock_uc

    with TestClient(app) as client:
        resp = client.delete("/v1/memories/mem-123", headers=HEADERS)
    assert resp.status_code == 204


# 8
def test_explain_trace_returns_data() -> None:
    trace = _make_memory_trace()
    mock_uc = AsyncMock()
    mock_uc.execute.return_value = trace
    app.dependency_overrides[get_explain_use_case] = lambda: mock_uc

    with TestClient(app) as client:
        resp = client.get(f"/v1/traces/{trace.trace_id}", headers=HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "trace_id" in data
    assert "steps" in data


# 9
def test_session_end_returns_202() -> None:
    mock_uc = AsyncMock()
    mock_uc.execute.return_value = None
    app.dependency_overrides[get_notify_session_ended_use_case] = lambda: mock_uc

    body = {"scope": _SCOPE_BODY}
    with TestClient(app) as client:
        resp = client.post("/v1/sessions/sess-1:end", json=body, headers=HEADERS)
    assert resp.status_code == 202


# 10
def test_admin_decay_returns_count() -> None:
    mock_uc = AsyncMock()
    mock_uc.execute.return_value = 7
    app.dependency_overrides[get_decay_use_case] = lambda: mock_uc

    with TestClient(app) as client:
        resp = client.post(f"/v1/admin/tenants/{TENANT}:decay")
    assert resp.status_code == 200
    assert resp.json()["transitions"] == 7


# 11
def test_memory_not_found_maps_to_404() -> None:
    mock_uc = AsyncMock()
    mock_uc.execute.side_effect = MemoryNotFoundError("mem-999")
    app.dependency_overrides[get_get_memory_use_case] = lambda: mock_uc

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/v1/memories/mem-999", headers=HEADERS)
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "MEMORY_NOT_FOUND"


# 12
def test_tenant_isolation_maps_to_403() -> None:
    mock_uc = AsyncMock()
    mock_uc.execute.side_effect = TenantIsolationViolation(
        actor="agent-1", requested_tenant_id="other-tenant"
    )
    app.dependency_overrides[get_get_memory_use_case] = lambda: mock_uc

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/v1/memories/mem-1", headers=HEADERS)
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "TENANT_ISOLATION_VIOLATION"


# 13
def test_unknown_exception_maps_to_500() -> None:
    mock_uc = AsyncMock()
    mock_uc.execute.side_effect = RuntimeError("boom")
    app.dependency_overrides[get_get_memory_use_case] = lambda: mock_uc

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/v1/memories/mem-1", headers=HEADERS)
    assert resp.status_code == 500
    assert resp.json()["error_code"] == "INTERNAL_SERVER_ERROR"


# 14
def test_extra_fields_forbidden_returns_422() -> None:
    body = {
        "scope": _SCOPE_BODY,
        "raw_payload": "hello",
        "payload_type": "CONVERSATION_TURN",
        "unknown_field": "should be rejected",
    }
    mock_uc = AsyncMock()
    mock_uc.execute.return_value = _make_write_result()
    app.dependency_overrides[get_write_use_case] = lambda: mock_uc

    with TestClient(app) as client:
        resp = client.post("/v1/memories:write", json=body, headers=HEADERS)
    # FastAPI raises RequestValidationError which our handler maps to 400.
    # ConfigDict(extra="forbid") on the Pydantic model triggers this path.
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "VALIDATION_ERROR"


# 15
def test_dependency_override_honored() -> None:
    """Verify that app.dependency_overrides replaces the stub provider."""
    result = _make_write_result()
    custom_uc = AsyncMock()
    custom_uc.execute.return_value = result

    app.dependency_overrides[get_write_use_case] = lambda: custom_uc

    body = {
        "scope": _SCOPE_BODY,
        "raw_payload": "override test",
        "payload_type": "CONVERSATION_TURN",
    }
    with TestClient(app) as client:
        resp = client.post("/v1/memories:write", json=body, headers=HEADERS)
    assert resp.status_code == 200
    custom_uc.execute.assert_awaited_once()

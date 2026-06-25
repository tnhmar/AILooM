"""Acceptance tests for tenant isolation middleware — M5-T5."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.testclient import TestClient as StarletteTestClient

from memory_layer.api.app import app, get_get_memory_use_case, get_write_use_case
from memory_layer.api.middleware import get_request_tenant_id
from memory_layer.api.tenant import assert_tenant_match, resolve_tenant_id
from memory_layer.domain.exceptions import TenantIsolationViolation
from memory_layer.domain.records import (
    MemoryRecord,
    Scope,
    WriteResult,
)
from memory_layer.domain.types import (
    LifecycleState,
    MemorySector,
    PayloadType,
    PipelineStatus,
    PrincipalId,
    PrincipalType,
    TenantId,
    new_memory_id,
)

TENANT = "tenant-mw-test"
HEADERS = {"X-Tenant-Id": TENANT}
_NOW = datetime(2024, 1, 1, tzinfo=UTC)
_SCOPE_BODY = {"principal_id": "user-1", "principal_type": "USER"}
_DOMAIN_SCOPE = Scope(
    tenant_id=TenantId(TENANT),
    principal_id=PrincipalId("user-1"),
    principal_type=PrincipalType.USER,
)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


# 1. /healthz works without X-Tenant-Id and returns the standard HealthReport.
# The endpoint serialises the full HealthReport dataclass (version, components,
# checked_at in addition to status), so we assert each field individually
# rather than doing an exact-equality check.
def test_healthz_works_without_tenant_header() -> None:
    with TestClient(app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["version"], str)
    assert isinstance(body["components"], list)
    assert isinstance(body["checked_at"], str)


# 2. Non-admin /v1/ route without tenant header returns 403
def test_v1_route_without_tenant_header_returns_403() -> None:
    mock_uc = AsyncMock()
    mock_uc.execute.return_value = _make_write_result()
    app.dependency_overrides[get_write_use_case] = lambda: mock_uc

    body = {
        "scope": _SCOPE_BODY,
        "raw_payload": "hello",
        "payload_type": "CONVERSATION_TURN",
    }
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/v1/memories:write", json=body)  # no X-Tenant-Id
    assert resp.status_code == 403


# 3. Non-admin /v1/ route with tenant header stores tenant on request state
def test_v1_route_with_tenant_header_sets_state() -> None:
    mock_uc = AsyncMock()
    mock_uc.execute.return_value = _make_write_result()
    app.dependency_overrides[get_write_use_case] = lambda: mock_uc

    body = {
        "scope": _SCOPE_BODY,
        "raw_payload": "hello",
        "payload_type": "CONVERSATION_TURN",
    }
    with TestClient(app) as client:
        resp = client.post("/v1/memories:write", json=body, headers=HEADERS)
    assert resp.status_code == 200
    # If state was not set the use case would not have been called successfully
    mock_uc.execute.assert_awaited_once()


# 4. Admin route does not require tenant header (tenant is in path)
def test_admin_route_does_not_require_tenant_header() -> None:
    from memory_layer.api.app import get_decay_use_case

    mock_uc = AsyncMock()
    mock_uc.execute.return_value = 3
    app.dependency_overrides[get_decay_use_case] = lambda: mock_uc

    with TestClient(app) as client:
        resp = client.post(f"/v1/admin/tenants/{TENANT}:decay")  # no header
    assert resp.status_code == 200
    assert resp.json()["transitions"] == 3


# 5. resolve_tenant_id raises on missing header
def test_resolve_tenant_id_raises_on_missing_header() -> None:
    scope = {"type": "http", "method": "GET", "path": "/", "query_string": b"",
             "headers": [], "app": None}
    request = Request(scope)  # type: ignore[arg-type]
    with pytest.raises(TenantIsolationViolation):
        resolve_tenant_id(request)


# 6. resolve_tenant_id raises on blank header
def test_resolve_tenant_id_raises_on_blank_header() -> None:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [(b"x-tenant-id", b"   ")],
        "app": None,
    }
    request = Request(scope)  # type: ignore[arg-type]
    with pytest.raises(TenantIsolationViolation):
        resolve_tenant_id(request)


# 7. assert_tenant_match passes when equal
def test_assert_tenant_match_passes_when_equal() -> None:
    assert_tenant_match("tenant-a", "tenant-a")  # must not raise


# 8. assert_tenant_match raises when different
def test_assert_tenant_match_raises_when_different() -> None:
    with pytest.raises(TenantIsolationViolation):
        assert_tenant_match("tenant-a", "tenant-b")


# 9. get_request_tenant_id returns the tenant placed in request state
def test_get_request_tenant_id_reads_state() -> None:
    scope = {"type": "http", "method": "GET", "path": "/", "query_string": b"",
             "headers": [], "app": None}
    request = Request(scope)  # type: ignore[arg-type]
    request.state.tenant_id = "my-tenant"
    assert get_request_tenant_id(request) == "my-tenant"


# 10. Existing endpoint dependency logic still works after middleware installation
def test_existing_endpoint_still_works_after_middleware() -> None:
    record = _make_memory_record()
    mock_uc = AsyncMock()
    mock_uc.execute.return_value = record
    app.dependency_overrides[get_get_memory_use_case] = lambda: mock_uc

    with TestClient(app) as client:
        resp = client.get(f"/v1/memories/{record.id}", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["memory_id"] == str(record.id)

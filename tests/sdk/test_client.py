"""Acceptance tests for MemoryLayerClient — M5-T2."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from memory_layer.sdk.client import MemoryLayerClient
from memory_layer.sdk.errors import MemoryLayerHTTPError, MemoryLayerTransportError
from memory_layer.sdk.models import (
    SDKMemoryTrace,
    SDKRecallRequest,
    SDKRecallResponse,
    SDKSearchRequest,
    SDKSearchResponse,
    SDKWriteRequest,
    SDKWriteResponse,
)

BASE_URL = "http://testserver"
TENANT = "tenant-sdk-test"
_NOW_STR = "2024-01-01T00:00:00+00:00"
_NOW = datetime(2024, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Transport helpers
# ---------------------------------------------------------------------------


def _json_response(
    body: Any,
    status_code: int = 200,
    content_type: str = "application/json",
) -> httpx.Response:
    """Build an httpx.Response with a JSON body."""
    content = json.dumps(body).encode()
    return httpx.Response(
        status_code=status_code,
        headers={"content-type": content_type},
        content=content,
        request=httpx.Request("GET", BASE_URL),
    )


class _MockTransport(httpx.AsyncBaseTransport):
    """Minimal async transport that returns a single pre-configured response."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.last_request: httpx.Request | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.last_request = request
        # Re-attach request to response so httpx internals are satisfied
        return httpx.Response(
            status_code=self._response.status_code,
            headers=self._response.headers,
            content=self._response.content,
            request=request,
        )


class _RaisingTransport(httpx.AsyncBaseTransport):
    """Transport that simulates a network-level failure."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)


def _client(transport: httpx.AsyncBaseTransport) -> MemoryLayerClient:
    """Build a MemoryLayerClient wired to *transport*."""
    return MemoryLayerClient(
        base_url=BASE_URL,
        tenant_id=TENANT,
        transport=transport,
    )


# ---------------------------------------------------------------------------
# Fixtures / shared data
# ---------------------------------------------------------------------------

_WRITE_RESPONSE_BODY = {
    "memory_id": "mem-abc",
    "pipeline_status": "PENDING",
    "accepted_at": _NOW_STR,
    "idempotent": False,
}

_SEARCH_RESPONSE_BODY = {
    "items": [
        {
            "memory_id": "mem-abc",
            "content": "hello world",
            "sector": "EPISODIC",
            "score": 0.95,
            "pipeline_status": "ENRICHED",
            "lifecycle_state": "ACTIVE",
            "signals": {},
            "effective_from": None,
        }
    ],
    "total": 1,
    "searched_at": _NOW_STR,
}

_RECALL_RESPONSE_BODY = {
    "status": "MATCH",
    "items": [],
    "total_tokens_estimate": 0,
    "recall_strategy": "hybrid",
    "recalled_at": _NOW_STR,
    "no_match_reason": None,
}

_TRACE_RESPONSE_BODY = {
    "trace_id": "trace-1",
    "tenant_id": TENANT,
    "query": "what did I say",
    "mode": "HYBRID",
    "steps": [
        {
            "memory_id": "mem-abc",
            "rank": 0,
            "score": 0.9,
            "signals": {},
            "explanation": "",
            "record_available": True,
        }
    ],
    "created_at": _NOW_STR,
}

_GET_MEMORY_BODY = {
    "memory_id": "mem-abc",
    "sector": "EPISODIC",
    "lifecycle_state": "ACTIVE",
    "pipeline_status": "ENRICHED",
    "recorded_at": _NOW_STR,
    "raw_payload": "hello world",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthz_returns_parsed_json() -> None:
    transport = _MockTransport(_json_response({"status": "ok"}))
    async with _client(transport) as c:
        result = await c.healthz()
    assert result == {"status": "ok"}


@pytest.mark.asyncio
async def test_write_sends_tenant_header() -> None:
    transport = _MockTransport(_json_response(_WRITE_RESPONSE_BODY))
    async with _client(transport) as c:
        await c.write(
            SDKWriteRequest(
                principal_id="user-1",
                raw_payload="test",
                payload_type="CONVERSATION_TURN",
            )
        )
    assert transport.last_request is not None
    assert transport.last_request.headers["x-tenant-id"] == TENANT


@pytest.mark.asyncio
async def test_write_returns_sdk_write_response() -> None:
    transport = _MockTransport(_json_response(_WRITE_RESPONSE_BODY))
    async with _client(transport) as c:
        result = await c.write(
            SDKWriteRequest(
                principal_id="user-1",
                raw_payload="test",
                payload_type="CONVERSATION_TURN",
            )
        )
    assert isinstance(result, SDKWriteResponse)
    assert result.memory_id == "mem-abc"
    assert result.pipeline_status == "PENDING"
    assert result.accepted_at == _NOW


@pytest.mark.asyncio
async def test_search_returns_typed_response() -> None:
    transport = _MockTransport(_json_response(_SEARCH_RESPONSE_BODY))
    async with _client(transport) as c:
        result = await c.search(
            SDKSearchRequest(principal_id="user-1", query="hello")
        )
    assert isinstance(result, SDKSearchResponse)
    assert result.total == 1
    assert result.items[0].content == "hello world"
    assert result.items[0].score == 0.95


@pytest.mark.asyncio
async def test_recall_returns_typed_response() -> None:
    transport = _MockTransport(_json_response(_RECALL_RESPONSE_BODY))
    async with _client(transport) as c:
        result = await c.recall(
            SDKRecallRequest(principal_id="user-1", query="summarise")
        )
    assert isinstance(result, SDKRecallResponse)
    assert result.status == "MATCH"


@pytest.mark.asyncio
async def test_get_memory_returns_dict() -> None:
    transport = _MockTransport(_json_response(_GET_MEMORY_BODY))
    async with _client(transport) as c:
        result = await c.get_memory("mem-abc")
    assert isinstance(result, dict)
    assert result["memory_id"] == "mem-abc"


@pytest.mark.asyncio
async def test_delete_memory_returns_none_on_204() -> None:
    transport = _MockTransport(
        httpx.Response(
            status_code=204,
            content=b"",
            request=httpx.Request("DELETE", BASE_URL),
        )
    )
    async with _client(transport) as c:
        result = await c.delete_memory("mem-abc")
    assert result is None


@pytest.mark.asyncio
async def test_explain_returns_sdk_memory_trace() -> None:
    transport = _MockTransport(_json_response(_TRACE_RESPONSE_BODY))
    async with _client(transport) as c:
        result = await c.explain("trace-1")
    assert isinstance(result, SDKMemoryTrace)
    assert result.trace_id == "trace-1"
    assert len(result.steps) == 1
    assert result.steps[0].rank == 0


@pytest.mark.asyncio
async def test_non_2xx_raises_http_error() -> None:
    error_body = {
        "error_code": "MEMORY_NOT_FOUND",
        "message": "Memory not found: mem-999",
        "details": {"memory_id": "mem-999"},
        "trace_id": None,
    }
    transport = _MockTransport(_json_response(error_body, status_code=404))
    async with _client(transport) as c:
        with pytest.raises(MemoryLayerHTTPError) as exc_info:
            await c.get_memory("mem-999")
    err = exc_info.value
    assert err.status_code == 404
    assert err.error_code == "MEMORY_NOT_FOUND"
    assert "mem-999" in err.message


@pytest.mark.asyncio
async def test_transport_failure_raises_transport_error() -> None:
    transport = _RaisingTransport()
    async with _client(transport) as c:
        with pytest.raises(MemoryLayerTransportError):
            await c.healthz()


@pytest.mark.asyncio
async def test_async_context_manager() -> None:
    transport = _MockTransport(_json_response({"status": "ok"}))
    async with MemoryLayerClient(
        base_url=BASE_URL, tenant_id=TENANT, transport=transport
    ) as c:
        result = await c.healthz()
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_aclose_closes_underlying_client(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _MockTransport(_json_response({"status": "ok"}))
    client = _client(transport)
    closed: list[bool] = []

    original_aclose = client._client.aclose

    async def _tracking_aclose() -> None:
        closed.append(True)
        await original_aclose()

    monkeypatch.setattr(client._client, "aclose", _tracking_aclose)
    await client.aclose()
    assert closed == [True]

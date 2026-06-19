"""Acceptance tests for the MCP server — M5-T4."""

from __future__ import annotations

import inspect
import os
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import memory_layer.mcp.server as server_mod
from memory_layer.sdk.models import (
    SDKMemoryTrace,
    SDKRecallResponse,
    SDKSearchResponse,
    SDKTraceStep,
    SDKWriteResponse,
)

_NOW = datetime(2024, 1, 1, tzinfo=UTC)
TENANT = "test-tenant"


# ---------------------------------------------------------------------------
# Fake SDK responses
# ---------------------------------------------------------------------------


def _fake_write_response() -> SDKWriteResponse:
    return SDKWriteResponse(
        memory_id="mem-1",
        pipeline_status="PENDING",
        accepted_at=_NOW,
    )


def _fake_search_response() -> SDKSearchResponse:
    return SDKSearchResponse(items=[], total=0, searched_at=_NOW)


def _fake_recall_response() -> SDKRecallResponse:
    return SDKRecallResponse(
        status="MATCH",
        items=[],
        total_tokens_estimate=0,
        recall_strategy="hybrid",
        recalled_at=_NOW,
    )


def _fake_trace() -> SDKMemoryTrace:
    return SDKMemoryTrace(
        trace_id="trace-1",
        tenant_id=TENANT,
        query="what did I say",
        mode="HYBRID",
        steps=[SDKTraceStep(memory_id="mem-1", rank=0, score=0.9)],
        created_at=_NOW,
    )


# ---------------------------------------------------------------------------
# Helper: build a fake client and monkeypatch build_client
# ---------------------------------------------------------------------------


def _make_fake_client() -> MagicMock:
    """Return an AsyncMock-based fake MemoryLayerClient."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.write = AsyncMock(return_value=_fake_write_response())
    client.search = AsyncMock(return_value=_fake_search_response())
    client.recall = AsyncMock(return_value=_fake_recall_response())
    client.explain = AsyncMock(return_value=_fake_trace())
    client.get_memory = AsyncMock(return_value={"memory_id": "mem-1", "raw_payload": "hello"})
    client.delete_memory = AsyncMock(return_value=None)
    client.end_session = AsyncMock(return_value=None)
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


# 1
def test_mcp_server_object_defined() -> None:
    """MCP module defines the `mcp` server object."""
    assert hasattr(server_mod, "mcp")
    assert server_mod.mcp is not None


# 2
@pytest.mark.asyncio
async def test_write_memory_returns_memory_id() -> None:
    fake = _make_fake_client()
    with patch.object(server_mod, "build_client", return_value=fake):
        result = await server_mod.write_memory(
            tenant_id=TENANT,
            principal_id="user-1",
            raw_payload="hello world",
            payload_type="CONVERSATION_TURN",
        )
    assert isinstance(result, dict)
    assert "memory_id" in result
    assert result["memory_id"] == "mem-1"


# 3
@pytest.mark.asyncio
async def test_search_memory_returns_result_payload() -> None:
    fake = _make_fake_client()
    with patch.object(server_mod, "build_client", return_value=fake):
        result = await server_mod.search_memory(
            tenant_id=TENANT,
            principal_id="user-1",
            query="what did I say",
        )
    assert isinstance(result, dict)
    assert "items" in result
    assert "total" in result
    assert "searched_at" in result


# 4
@pytest.mark.asyncio
async def test_recall_memory_returns_result_payload() -> None:
    fake = _make_fake_client()
    with patch.object(server_mod, "build_client", return_value=fake):
        result = await server_mod.recall_memory(
            tenant_id=TENANT,
            principal_id="user-1",
            query="summarise",
        )
    assert isinstance(result, dict)
    assert "status" in result
    assert "items" in result
    assert "recalled_at" in result


# 5
@pytest.mark.asyncio
async def test_explain_trace_returns_trace_payload() -> None:
    fake = _make_fake_client()
    with patch.object(server_mod, "build_client", return_value=fake):
        result = await server_mod.explain_trace(
            tenant_id=TENANT,
            trace_id="trace-1",
        )
    assert isinstance(result, dict)
    assert result["trace_id"] == "trace-1"
    assert "steps" in result
    assert "created_at" in result


# 6
@pytest.mark.asyncio
async def test_get_memory_delegates_and_returns_dict() -> None:
    fake = _make_fake_client()
    with patch.object(server_mod, "build_client", return_value=fake):
        result = await server_mod.get_memory(
            tenant_id=TENANT,
            memory_id="mem-1",
        )
    assert isinstance(result, dict)
    assert result["memory_id"] == "mem-1"
    fake.get_memory.assert_awaited_once_with("mem-1")


# 7
@pytest.mark.asyncio
async def test_delete_memory_returns_success_payload() -> None:
    fake = _make_fake_client()
    with patch.object(server_mod, "build_client", return_value=fake):
        result = await server_mod.delete_memory(
            tenant_id=TENANT,
            memory_id="mem-1",
        )
    assert isinstance(result, dict)
    assert result["deleted"] is True
    assert result["memory_id"] == "mem-1"


# 8
@pytest.mark.asyncio
async def test_end_session_returns_success_payload() -> None:
    fake = _make_fake_client()
    with patch.object(server_mod, "build_client", return_value=fake):
        result = await server_mod.end_session(
            tenant_id=TENANT,
            session_id="sess-1",
            principal_id="user-1",
        )
    assert isinstance(result, dict)
    assert result["accepted"] is True
    assert result["session_id"] == "sess-1"


# 9
def test_build_client_reads_base_url_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_LAYER_BASE_URL", "http://custom-host:9000")
    client = server_mod.build_client(TENANT)
    # httpx.AsyncClient stores base_url as a URL object
    assert "custom-host" in str(client._client.base_url)
    # Clean up the live httpx client to avoid ResourceWarning
    import asyncio
    asyncio.get_event_loop().run_until_complete(client.aclose())


# 10
def test_all_tools_require_tenant_id() -> None:
    """Every tool function must declare `tenant_id` as an explicit parameter."""
    tools = [
        server_mod.write_memory,
        server_mod.search_memory,
        server_mod.recall_memory,
        server_mod.explain_trace,
        server_mod.get_memory,
        server_mod.delete_memory,
        server_mod.end_session,
    ]
    for fn in tools:
        sig = inspect.signature(fn)
        assert "tenant_id" in sig.parameters, (
            f"{fn.__name__} is missing required 'tenant_id' parameter"
        )

"""Acceptance tests for the LangGraph adapter — M5-T3."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Coroutine
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from memory_layer.integrations.langgraph import (
    build_recall_memory_node,
    build_search_memory_node,
    build_write_memory_node,
)
from memory_layer.sdk.models import (
    SDKRecallResponse,
    SDKSearchResponse,
    SDKWriteResponse,
)

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Shared fake SDK responses
# ---------------------------------------------------------------------------


def _fake_write_response() -> SDKWriteResponse:
    return SDKWriteResponse(
        memory_id="mem-1",
        pipeline_status="PENDING",
        accepted_at=_NOW,
    )


def _fake_search_response() -> SDKSearchResponse:
    return SDKSearchResponse(
        items=[],
        total=0,
        searched_at=_NOW,
    )


def _fake_recall_response() -> SDKRecallResponse:
    return SDKRecallResponse(
        status="MATCH",
        items=[],
        total_tokens_estimate=0,
        recall_strategy="hybrid",
        recalled_at=_NOW,
    )


# ---------------------------------------------------------------------------
# Helper: build a mock MemoryLayerClient
# ---------------------------------------------------------------------------


def _mock_client(
    write_result: SDKWriteResponse | None = None,
    search_result: SDKSearchResponse | None = None,
    recall_result: SDKRecallResponse | None = None,
) -> MagicMock:
    client = MagicMock()
    client.write = AsyncMock(return_value=write_result or _fake_write_response())
    client.search = AsyncMock(return_value=search_result or _fake_search_response())
    client.recall = AsyncMock(return_value=recall_result or _fake_recall_response())
    return client


# ---------------------------------------------------------------------------
# Tests 1-3: builders return async callables
# ---------------------------------------------------------------------------


def test_build_write_memory_node_returns_async_callable() -> None:
    node = build_write_memory_node(_mock_client())
    assert callable(node)
    result = node({"principal_id": "u", "raw_payload": "x", "payload_type": "CONVERSATION_TURN"})
    assert isinstance(result, Coroutine)
    result.close()  # prevent RuntimeWarning: coroutine was never awaited


def test_build_search_memory_node_returns_async_callable() -> None:
    node = build_search_memory_node(_mock_client())
    assert callable(node)
    result = node({"principal_id": "u", "search_query": "hello"})
    assert isinstance(result, Coroutine)
    result.close()


def test_build_recall_memory_node_returns_async_callable() -> None:
    node = build_recall_memory_node(_mock_client())
    assert callable(node)
    result = node({"principal_id": "u", "recall_query": "hello"})
    assert isinstance(result, Coroutine)
    result.close()


# ---------------------------------------------------------------------------
# Tests 4-6: nodes return the correct state update keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_node_returns_memory_write_result() -> None:
    client = _mock_client(write_result=_fake_write_response())
    node = build_write_memory_node(client)
    state = {
        "principal_id": "user-1",
        "raw_payload": "hello world",
        "payload_type": "CONVERSATION_TURN",
    }
    update = await node(state)
    assert "memory_write_result" in update
    assert isinstance(update["memory_write_result"], SDKWriteResponse)
    assert update["memory_write_result"].memory_id == "mem-1"


@pytest.mark.asyncio
async def test_search_node_returns_memory_search_result() -> None:
    client = _mock_client(search_result=_fake_search_response())
    node = build_search_memory_node(client)
    state = {"principal_id": "user-1", "search_query": "what did I say"}
    update = await node(state)
    assert "memory_search_result" in update
    assert isinstance(update["memory_search_result"], SDKSearchResponse)


@pytest.mark.asyncio
async def test_recall_node_returns_memory_recall_result() -> None:
    client = _mock_client(recall_result=_fake_recall_response())
    node = build_recall_memory_node(client)
    state = {"principal_id": "user-1", "recall_query": "summarise"}
    update = await node(state)
    assert "memory_recall_result" in update
    assert isinstance(update["memory_recall_result"], SDKRecallResponse)


# ---------------------------------------------------------------------------
# Tests 7-9: missing required state fields raise ValueError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_node_raises_on_missing_required_field() -> None:
    node = build_write_memory_node(_mock_client())
    # Missing raw_payload and payload_type
    with pytest.raises(ValueError, match="raw_payload"):
        await node({"principal_id": "user-1"})


@pytest.mark.asyncio
async def test_search_node_raises_on_missing_required_field() -> None:
    node = build_search_memory_node(_mock_client())
    # Missing search_query
    with pytest.raises(ValueError, match="search_query"):
        await node({"principal_id": "user-1"})


@pytest.mark.asyncio
async def test_recall_node_raises_on_missing_required_field() -> None:
    node = build_recall_memory_node(_mock_client())
    # Missing recall_query
    with pytest.raises(ValueError, match="recall_query"):
        await node({"principal_id": "user-1"})


# ---------------------------------------------------------------------------
# Test 10: module import does not require LangGraph
# ---------------------------------------------------------------------------


def test_module_import_does_not_require_langgraph() -> None:
    """Importing langgraph.py must succeed even when the langgraph package
    is not installed.
    """
    # Temporarily shadow the langgraph package with None in sys.modules
    # so that any accidental top-level import would raise ImportError.
    langgraph_backup = sys.modules.get("langgraph")
    sys.modules["langgraph"] = None  # type: ignore[assignment]
    try:
        # Force reimport from scratch
        mod_name = "memory_layer.integrations.langgraph"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        imported = importlib.import_module(mod_name)
        assert hasattr(imported, "build_write_memory_node")
        assert hasattr(imported, "build_search_memory_node")
        assert hasattr(imported, "build_recall_memory_node")
    finally:
        # Restore original state
        if langgraph_backup is None:
            sys.modules.pop("langgraph", None)
        else:
            sys.modules["langgraph"] = langgraph_backup

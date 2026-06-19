"""LangGraph adapter nodes for memory-layer.

This module is **import-safe without LangGraph installed**. It never imports
``langgraph`` at module level. Each builder function returns a plain async
callable ``(state: dict) -> dict`` that is fully compatible with LangGraph's
node protocol.

Usage example
-------------
::

    from memory_layer.sdk.client import MemoryLayerClient
    from memory_layer.integrations.langgraph import (
        build_write_memory_node,
        build_search_memory_node,
        build_recall_memory_node,
    )

    client = MemoryLayerClient(base_url="http://...", tenant_id="t-1")

    write_node  = build_write_memory_node(client)
    search_node = build_search_memory_node(client)
    recall_node = build_recall_memory_node(client)

    # Wire into a LangGraph StateGraph:
    # graph.add_node("write_memory",  write_node)
    # graph.add_node("search_memory", search_node)
    # graph.add_node("recall_memory", recall_node)

Expected state shapes
---------------------
Write node reads the following keys from ``state``:

* ``principal_id``  (str, **required**)
* ``raw_payload``   (str, **required**)
* ``payload_type``  (str, **required**)
* ``sector``        (str | None, optional)
* ``metadata``      (dict, optional)

Returns ``{"memory_write_result": SDKWriteResponse}``.

Search node reads:

* ``principal_id``  (str, **required**)
* ``search_query``  (str, **required**)
* ``search_mode``   (str, optional, default ``"HYBRID"``)
* ``search_k``      (int, optional, default ``10``)

Returns ``{"memory_search_result": SDKSearchResponse}``.

Recall node reads:

* ``principal_id``  (str, **required**)
* ``recall_query``  (str, **required**)
* ``max_tokens``    (int | None, optional, default ``4000``)
* ``max_items``     (int, optional, default ``10``)

Returns ``{"memory_recall_result": SDKRecallResponse}``.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from memory_layer.sdk.client import MemoryLayerClient
from memory_layer.sdk.models import (
    SDKRecallRequest,
    SDKSearchRequest,
    SDKWriteRequest,
)

# Type alias for a LangGraph-compatible async node callable.
_NodeCallable = Callable[[dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]]


def _require(state: dict[str, Any], key: str) -> Any:
    """Return ``state[key]`` or raise ``ValueError`` if absent or None."""
    value = state.get(key)
    if value is None:
        raise ValueError(
            f"Memory node requires state key '{key}' but it is missing or None. "
            f"Present keys: {list(state.keys())}"
        )
    return value


def build_write_memory_node(client: MemoryLayerClient) -> _NodeCallable:
    """Return an async node that writes a memory record from graph state.

    Parameters
    ----------
    client:
        An initialised :class:`~memory_layer.sdk.client.MemoryLayerClient`.

    Returns
    -------
    async callable
        ``(state: dict) -> {"memory_write_result": SDKWriteResponse}``
    """

    async def _write_node(state: dict[str, Any]) -> dict[str, Any]:
        principal_id: str = _require(state, "principal_id")
        raw_payload: str = _require(state, "raw_payload")
        payload_type: str = _require(state, "payload_type")

        request = SDKWriteRequest(
            principal_id=principal_id,
            raw_payload=raw_payload,
            payload_type=payload_type,
            sector=state.get("sector"),
            metadata=state.get("metadata") or {},
        )
        result = await client.write(request)
        return {"memory_write_result": result}

    return _write_node


def build_search_memory_node(client: MemoryLayerClient) -> _NodeCallable:
    """Return an async node that searches the memory index from graph state.

    Parameters
    ----------
    client:
        An initialised :class:`~memory_layer.sdk.client.MemoryLayerClient`.

    Returns
    -------
    async callable
        ``(state: dict) -> {"memory_search_result": SDKSearchResponse}``
    """

    async def _search_node(state: dict[str, Any]) -> dict[str, Any]:
        principal_id: str = _require(state, "principal_id")
        search_query: str = _require(state, "search_query")

        request = SDKSearchRequest(
            principal_id=principal_id,
            query=search_query,
            mode=state.get("search_mode", "HYBRID"),
            k=state.get("search_k", 10),
        )
        result = await client.search(request)
        return {"memory_search_result": result}

    return _search_node


def build_recall_memory_node(client: MemoryLayerClient) -> _NodeCallable:
    """Return an async node that recalls memory items for agent context from graph state.

    Parameters
    ----------
    client:
        An initialised :class:`~memory_layer.sdk.client.MemoryLayerClient`.

    Returns
    -------
    async callable
        ``(state: dict) -> {"memory_recall_result": SDKRecallResponse}``
    """

    async def _recall_node(state: dict[str, Any]) -> dict[str, Any]:
        principal_id: str = _require(state, "principal_id")
        recall_query: str = _require(state, "recall_query")

        request = SDKRecallRequest(
            principal_id=principal_id,
            query=recall_query,
            max_tokens=state.get("max_tokens", 4000),
            max_items=state.get("max_items", 10),
        )
        result = await client.recall(request)
        return {"memory_recall_result": result}

    return _recall_node

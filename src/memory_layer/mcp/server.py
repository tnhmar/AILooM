"""MCP server exposing memory-layer operations as tools.

Tools are backed by :class:`~memory_layer.sdk.client.MemoryLayerClient`.
The base URL is read from the ``MEMORY_LAYER_BASE_URL`` environment variable
(default: ``http://localhost:8000``).

All tool inputs are simple JSON-serialisable primitives or dicts.
All tool outputs are JSON-serialisable dicts.

Starting the server::

    MEMORY_LAYER_BASE_URL=http://api:8000 python -m memory_layer.mcp.server
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from memory_layer.sdk.client import MemoryLayerClient
from memory_layer.sdk.models import (
    SDKRecallRequest,
    SDKSearchRequest,
    SDKWriteRequest,
)

mcp = FastMCP("memory-layer")

_DEFAULT_BASE_URL = "http://localhost:8000"


def build_client(tenant_id: str) -> MemoryLayerClient:
    """Construct a :class:`MemoryLayerClient` for *tenant_id*.

    Reads the server base URL from the ``MEMORY_LAYER_BASE_URL`` environment
    variable, falling back to ``http://localhost:8000``.

    This function is the intended seam for tests: monkeypatch it to inject a
    fake client without touching the tool functions themselves.
    """
    base_url = os.environ.get("MEMORY_LAYER_BASE_URL", _DEFAULT_BASE_URL)
    return MemoryLayerClient(base_url=base_url, tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def write_memory(
    tenant_id: str,
    principal_id: str,
    raw_payload: str,
    payload_type: str = "CONVERSATION_TURN",
    sector: str | None = None,
    idempotency_key: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a new memory record for *tenant_id*.

    Args:
        tenant_id: The tenant that owns this memory.
        principal_id: Identity of the user or agent writing the memory.
        raw_payload: Raw text content to store.
        payload_type: Payload category (e.g. ``CONVERSATION_TURN``, ``DOCUMENT``).
        sector: Optional memory sector (e.g. ``EPISODIC``, ``SEMANTIC``).
        idempotency_key: Optional key to prevent duplicate writes.
        metadata: Arbitrary key-value metadata attached to the record.

    Returns:
        ``{memory_id, pipeline_status, accepted_at, idempotent}``
    """
    import memory_layer.mcp.server as _self  # allow monkeypatching build_client

    async with _self.build_client(tenant_id) as client:
        result = await client.write(
            SDKWriteRequest(
                principal_id=principal_id,
                raw_payload=raw_payload,
                payload_type=payload_type,
                sector=sector,
                idempotency_key=idempotency_key,
                metadata=metadata or {},
            )
        )
    return {
        "memory_id": result.memory_id,
        "pipeline_status": result.pipeline_status,
        "accepted_at": result.accepted_at.isoformat(),
        "idempotent": result.idempotent,
    }


@mcp.tool()
async def search_memory(
    tenant_id: str,
    principal_id: str,
    query: str,
    mode: str = "HYBRID",
    k: int = 10,
    sectors: list[str] | None = None,
) -> dict[str, Any]:
    """Search the memory index for *tenant_id*.

    Args:
        tenant_id: The tenant whose memories to search.
        principal_id: Identity of the requesting user or agent.
        query: Free-text search query.
        mode: Retrieval mode: ``SEMANTIC``, ``KEYWORD``, ``HYBRID`` (default),
              ``HYBRID_TEMPORAL``, ``QUALITY``, or ``GRAPH``.
        k: Maximum number of results to return (default 10).
        sectors: Optional list of memory sectors to restrict the search.

    Returns:
        ``{items: [...], total, searched_at}``
    """
    import memory_layer.mcp.server as _self

    async with _self.build_client(tenant_id) as client:
        result = await client.search(
            SDKSearchRequest(
                principal_id=principal_id,
                query=query,
                mode=mode,
                k=k,
                sectors=sectors,
            )
        )
    return {
        "items": [
            {
                "memory_id": item.memory_id,
                "content": item.content,
                "sector": item.sector,
                "score": item.score,
                "pipeline_status": item.pipeline_status,
                "lifecycle_state": item.lifecycle_state,
            }
            for item in result.items
        ],
        "total": result.total,
        "searched_at": result.searched_at.isoformat(),
    }


@mcp.tool()
async def recall_memory(
    tenant_id: str,
    principal_id: str,
    query: str,
    max_tokens: int = 4000,
    max_items: int = 10,
    mode: str = "HYBRID",
    sectors: list[str] | None = None,
) -> dict[str, Any]:
    """Recall memory items for agent context injection for *tenant_id*.

    Args:
        tenant_id: The tenant whose memories to recall.
        principal_id: Identity of the requesting user or agent.
        query: Natural-language query driving the recall.
        max_tokens: Token budget for recalled content (default 4000).
        max_items: Maximum number of items to return (default 10).
        mode: Retrieval mode (default ``HYBRID``).
        sectors: Optional list of memory sectors to restrict the recall.

    Returns:
        ``{status, items: [...], total_tokens_estimate, recall_strategy, recalled_at}``
    """
    import memory_layer.mcp.server as _self

    async with _self.build_client(tenant_id) as client:
        result = await client.recall(
            SDKRecallRequest(
                principal_id=principal_id,
                query=query,
                max_tokens=max_tokens,
                max_items=max_items,
                mode=mode,
                sectors=sectors,
            )
        )
    return {
        "status": result.status,
        "items": [
            {
                "memory_id": item.memory_id,
                "content": item.content,
                "sector": item.sector,
                "explanation": item.explanation,
            }
            for item in result.items
        ],
        "total_tokens_estimate": result.total_tokens_estimate,
        "recall_strategy": result.recall_strategy,
        "recalled_at": result.recalled_at.isoformat(),
        "no_match_reason": result.no_match_reason,
    }


@mcp.tool()
async def explain_trace(
    tenant_id: str,
    trace_id: str,
) -> dict[str, Any]:
    """Fetch the recall explanation trace for *trace_id* under *tenant_id*.

    Args:
        tenant_id: The tenant that owns the trace.
        trace_id: ID of the recall trace to explain.

    Returns:
        ``{trace_id, tenant_id, query, mode, steps: [...], created_at}``
    """
    import memory_layer.mcp.server as _self

    async with _self.build_client(tenant_id) as client:
        trace = await client.explain(trace_id)
    return {
        "trace_id": trace.trace_id,
        "tenant_id": trace.tenant_id,
        "query": trace.query,
        "mode": trace.mode,
        "steps": [
            {
                "memory_id": step.memory_id,
                "rank": step.rank,
                "score": step.score,
                "explanation": step.explanation,
            }
            for step in trace.steps
        ],
        "created_at": trace.created_at.isoformat(),
    }


@mcp.tool()
async def get_memory(
    tenant_id: str,
    memory_id: str,
) -> dict[str, Any]:
    """Retrieve a single memory record by ID for *tenant_id*.

    Args:
        tenant_id: The tenant that owns the record.
        memory_id: ID of the memory record to fetch.

    Returns:
        Raw memory record dict as returned by the server.
    """
    import memory_layer.mcp.server as _self

    async with _self.build_client(tenant_id) as client:
        return await client.get_memory(memory_id)


@mcp.tool()
async def delete_memory(
    tenant_id: str,
    memory_id: str,
    actor: str = "mcp",
) -> dict[str, Any]:
    """Delete a memory record for *tenant_id*.

    Args:
        tenant_id: The tenant that owns the record.
        memory_id: ID of the memory record to delete.
        actor: Identifier of the actor requesting deletion (default ``mcp``).

    Returns:
        ``{deleted: true, memory_id}``
    """
    import memory_layer.mcp.server as _self

    async with _self.build_client(tenant_id) as client:
        await client.delete_memory(memory_id, actor=actor)
    return {"deleted": True, "memory_id": memory_id}


@mcp.tool()
async def end_session(
    tenant_id: str,
    session_id: str,
    principal_id: str,
    principal_type: str = "USER",
) -> dict[str, Any]:
    """Signal session end, optionally triggering consolidation for *tenant_id*.

    Args:
        tenant_id: The tenant that owns the session.
        session_id: ID of the session that has ended.
        principal_id: Identity of the user or agent that owned the session.
        principal_type: Principal type (default ``USER``).

    Returns:
        ``{accepted: true, session_id}``
    """
    import memory_layer.mcp.server as _self

    scope = {
        "principal_id": principal_id,
        "principal_type": principal_type,
    }
    async with _self.build_client(tenant_id) as client:
        await client.end_session(session_id, scope=scope)
    return {"accepted": True, "session_id": session_id}


if __name__ == "__main__":
    mcp.run()

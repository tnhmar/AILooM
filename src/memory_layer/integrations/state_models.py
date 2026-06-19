"""Lightweight graph state fragments for memory-layer LangGraph integration.

These dataclasses / TypedDicts hold the minimum fields required for each
memory operation and are the canonical contract between graph state and the
memory-layer SDK nodes.

Expected graph state shapes
---------------------------
Write node reads::

    MemoryWriteState:
        principal_id: str          # required
        raw_payload:  str          # required
        payload_type: str          # required  e.g. "CONVERSATION_TURN"
        sector:       str | None   # optional
        metadata:     dict | None  # optional

Search node reads::

    MemorySearchState:
        principal_id: str          # required
        search_query: str          # required
        search_mode:  str          # optional, default "HYBRID"
        search_k:     int          # optional, default 10

Recall node reads::

    MemoryRecallState:
        principal_id:  str          # required
        recall_query:  str          # required
        max_tokens:    int | None   # optional, default 4000
        max_items:     int          # optional, default 10
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryWriteState:
    """State fragment consumed by the write memory node."""

    principal_id: str
    raw_payload: str
    payload_type: str
    sector: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemorySearchState:
    """State fragment consumed by the search memory node."""

    principal_id: str
    search_query: str
    search_mode: str = "HYBRID"
    search_k: int = 10


@dataclass
class MemoryRecallState:
    """State fragment consumed by the recall memory node."""

    principal_id: str
    recall_query: str
    max_tokens: int | None = 4000
    max_items: int = 10

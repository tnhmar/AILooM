"""Client-facing SDK request/response dataclasses.

These are intentionally separate from server-side domain dataclasses.
They carry only the fields the SDK consumer needs; no Pydantic dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


@dataclass
class SDKWriteRequest:
    """Request to write a new memory via the HTTP API."""

    principal_id: str
    raw_payload: str
    payload_type: str
    principal_type: str = "USER"
    sector: str | None = None
    idempotency_key: str | None = None
    extract: bool = True
    wait_for_enrichment: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    workspace_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None


@dataclass
class SDKWriteResponse:
    """Response from a successful write operation."""

    memory_id: str
    pipeline_status: str
    accepted_at: datetime
    idempotent: bool = False


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@dataclass
class SDKSearchRequest:
    """Request to search the memory index."""

    principal_id: str
    query: str
    principal_type: str = "USER"
    mode: str = "HYBRID"
    sectors: list[str] | None = None
    lifecycle_states: list[str] = field(default_factory=lambda: ["ACTIVE"])
    k: int = 10
    workspace_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None


@dataclass
class SDKSearchResultItem:
    """A single ranked result returned by a search operation."""

    memory_id: str
    content: str
    sector: str
    score: float
    pipeline_status: str
    lifecycle_state: str
    signals: dict[str, Any] = field(default_factory=dict)
    effective_from: datetime | None = None


@dataclass
class SDKSearchResponse:
    """Aggregated search response."""

    items: list[SDKSearchResultItem]
    total: int
    searched_at: datetime


# ---------------------------------------------------------------------------
# Recall
# ---------------------------------------------------------------------------


@dataclass
class SDKRecallRequest:
    """Request to recall memory items for agent context injection."""

    principal_id: str
    query: str
    principal_type: str = "USER"
    max_tokens: int | None = 4000
    max_items: int = 10
    sectors: list[str] | None = None
    include_facts: bool = True
    include_verbatim: bool = True
    mode: str = "HYBRID"
    workspace_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None


@dataclass
class SDKRecallItem:
    """A single item returned by a recall operation."""

    memory_id: str
    content: str
    sector: str
    lifecycle_state: str
    pipeline_status: str
    effective_from: datetime | None = None
    signals: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    trace_id: str | None = None


@dataclass
class SDKRecallResponse:
    """Aggregated recall response."""

    status: str
    items: list[SDKRecallItem]
    total_tokens_estimate: int
    recall_strategy: str
    recalled_at: datetime
    no_match_reason: str | None = None


# ---------------------------------------------------------------------------
# Trace / explain
# ---------------------------------------------------------------------------


@dataclass
class SDKTraceStep:
    """A single step within a recall trace."""

    memory_id: str
    rank: int
    score: float
    signals: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    record_available: bool = True


@dataclass
class SDKMemoryTrace:
    """Recall explanation trace returned by the explain endpoint."""

    trace_id: str
    tenant_id: str
    query: str
    mode: str
    steps: list[SDKTraceStep]
    created_at: datetime

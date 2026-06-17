"""Domain request/response objects: Scope, WriteRequest, SearchRequest, RecallRequest, etc."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional

from memory_layer.domain.types import (
    FactId,
    LifecycleState,
    MemoryId,
    MemorySector,
    PayloadType,
    PipelineStatus,
    PrincipalId,
    PrincipalType,
    RunId,
    SessionId,
    TenantId,
    TraceId,
    WorkspaceId,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SearchMode(str, Enum):
    """Retrieval mode used for search and recall operations."""
    SEMANTIC = "SEMANTIC"
    KEYWORD = "KEYWORD"
    HYBRID = "HYBRID"
    HYBRID_TEMPORAL = "HYBRID_TEMPORAL"
    QUALITY = "QUALITY"
    GRAPH = "GRAPH"


class RecallStatus(str, Enum):
    """High-level outcome of a recall operation."""
    MATCH = "MATCH"
    PARTIAL_MATCH = "PARTIAL_MATCH"
    NO_MATCH = "NO_MATCH"


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Scope:
    """Immutable identity context attached to every memory operation."""

    tenant_id: TenantId
    principal_id: PrincipalId
    principal_type: PrincipalType = PrincipalType.USER
    workspace_id: Optional[WorkspaceId] = None
    session_id: Optional[SessionId] = None
    run_id: Optional[RunId] = None


# ---------------------------------------------------------------------------
# TemporalFilter
# ---------------------------------------------------------------------------

@dataclass
class TemporalFilter:
    """Optional time-range constraints applied to search and recall queries."""

    as_of: Optional[datetime] = None
    from_dt: Optional[datetime] = None
    until_dt: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

@dataclass
class WriteRequest:
    """Request to persist a new memory payload into the memory layer."""

    tenant_id: TenantId
    scope: Scope
    raw_payload: str
    payload_type: PayloadType
    sector: Optional[MemorySector] = None
    idempotency_key: Optional[str] = None
    extract: bool = True
    wait_for_enrichment: bool = False
    metadata: dict = field(default_factory=dict)


@dataclass
class WriteResult:
    """Result returned after a successful write operation."""

    memory_id: MemoryId
    scope: Scope
    pipeline_status: PipelineStatus
    accepted_at: datetime
    idempotent: bool = False


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@dataclass
class SearchRequest:
    """Request to search the memory index with configurable retrieval mode and filters."""

    tenant_id: TenantId
    scope: Scope
    query: str
    mode: SearchMode = SearchMode.HYBRID
    sectors: Optional[List[MemorySector]] = None
    lifecycle_states: List[LifecycleState] = field(
        default_factory=lambda: [LifecycleState.ACTIVE]
    )
    temporal_filter: Optional[TemporalFilter] = None
    k: int = 10


# ---------------------------------------------------------------------------
# Recall
# ---------------------------------------------------------------------------

@dataclass
class RecallRequest:
    """Request to recall memory items for injection into an agent context window."""

    tenant_id: TenantId
    scope: Scope
    query: str
    max_tokens: Optional[int] = 4000
    max_items: int = 10
    sectors: Optional[List[MemorySector]] = None
    include_facts: bool = True
    include_verbatim: bool = True
    mode: SearchMode = SearchMode.HYBRID


@dataclass
class RecallItem:
    """A single ranked memory item returned by a recall operation."""

    memory_id: MemoryId
    content: str
    sector: MemorySector
    lifecycle_state: LifecycleState
    pipeline_status: PipelineStatus
    effective_from: Optional[datetime] = None
    signals: dict = field(default_factory=dict)
    explanation: str = ""
    trace_id: Optional[TraceId] = None


@dataclass
class RecallResult:
    """Aggregated result of a recall operation including ranked items and metadata."""

    status: RecallStatus
    no_match_reason: Optional[str] = None
    items: List[RecallItem] = field(default_factory=list)
    total_tokens_estimate: int = 0
    recall_strategy: str = ""
    recalled_at: datetime = field(default_factory=datetime.utcnow)

"""Domain request/response objects and core domain entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from memory_layer.domain.types import (
    AuditId,
    AuditOperation,
    AuditOutcome,
    EntityId,
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


class SearchMode(StrEnum):
    """Retrieval mode used for search and recall operations."""

    SEMANTIC = "SEMANTIC"
    KEYWORD = "KEYWORD"
    HYBRID = "HYBRID"
    HYBRID_TEMPORAL = "HYBRID_TEMPORAL"
    QUALITY = "QUALITY"
    GRAPH = "GRAPH"


class RecallStatus(StrEnum):
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
    workspace_id: WorkspaceId | None = None
    session_id: SessionId | None = None
    run_id: RunId | None = None


# ---------------------------------------------------------------------------
# TemporalFilter
# ---------------------------------------------------------------------------


@dataclass
class TemporalFilter:
    """Optional time-range constraints applied to search and recall queries."""

    as_of: datetime | None = None
    from_dt: datetime | None = None
    until_dt: datetime | None = None


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
    sector: MemorySector | None = None
    idempotency_key: str | None = None
    extract: bool = True
    wait_for_enrichment: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


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
    sectors: list[MemorySector] | None = None
    lifecycle_states: list[LifecycleState] = field(
        default_factory=lambda: [LifecycleState.ACTIVE]
    )
    temporal_filter: TemporalFilter | None = None
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
    max_tokens: int | None = 4000
    max_items: int = 10
    sectors: list[MemorySector] | None = None
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
    effective_from: datetime | None = None
    signals: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    trace_id: TraceId | None = None


@dataclass
class RecallResult:
    """Aggregated result of a recall operation including ranked items and metadata."""

    status: RecallStatus
    no_match_reason: str | None = None
    items: list[RecallItem] = field(default_factory=list)
    total_tokens_estimate: int = 0
    recall_strategy: str = ""
    recalled_at: datetime = field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# MemoryRecord  (M1-T2)
# CRITICAL INVARIANT: lifecycle_state defaults to ACTIVE.
# Records are searchable immediately on durable write.
# pipeline_status alone reflects enrichment progress — never lifecycle_state.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryRecord:
    """Central domain entity representing a persisted memory payload."""

    id: MemoryId
    tenant_id: TenantId
    scope: Scope
    raw_payload: str
    payload_type: PayloadType
    sector: MemorySector
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE
    pipeline_status: PipelineStatus = PipelineStatus.PENDING
    recorded_at: datetime = field(default_factory=datetime.utcnow)
    idempotency_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Fact  (M1-T2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fact:
    """An extracted, versioned, temporal fact derived from a MemoryRecord."""

    id: FactId
    memory_record_id: MemoryId
    tenant_id: TenantId
    scope: Scope
    subject_entity_id: EntityId
    predicate: str
    predicate_group: str
    object_value: str
    effective_from: datetime
    effective_to: datetime | None = None
    recorded_at: datetime = field(default_factory=datetime.utcnow)
    supersedes: FactId | None = None
    confidence: float = 1.0
    sector: MemorySector = MemorySector.SEMANTIC
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE


# ---------------------------------------------------------------------------
# AuditEntry  (M1-T2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditEntry:
    """Immutable audit log entry recording an operation on the memory layer."""

    id: AuditId
    tenant_id: TenantId
    scope: Scope
    operation: AuditOperation
    memory_id: MemoryId | None = None
    actor: str = "system"
    timestamp: datetime = field(default_factory=datetime.utcnow)
    outcome: AuditOutcome = AuditOutcome.SUCCESS
    detail: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# MemoryTrace  (M1-T2) — write/audit provenance trace
# ---------------------------------------------------------------------------


@dataclass
class MemoryTrace:
    """Mutable trace aggregating the full lifecycle of a single MemoryRecord."""

    trace_id: TraceId
    memory_id: MemoryId
    scope: Scope
    write_event: AuditEntry
    enrichment_status: PipelineStatus
    facts_derived: list[FactId] = field(default_factory=list)
    entities_extracted: list[EntityId] = field(default_factory=list)
    mutations: list[AuditEntry] = field(default_factory=list)
    recall_event: AuditEntry | None = None
    recall_signals: dict[str, Any] | None = None
    recall_explanation: str | None = None
    query_plan: Any | None = None
    constructed_at: datetime = field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# TraceStep + RecallTrace  (M4-T4) — recall explanation trace
# ---------------------------------------------------------------------------


@dataclass
class TraceStep:
    """A single ranked record entry within a RecallTrace."""

    memory_id: MemoryId
    rank: int
    score: float
    signals: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    record_available: bool = True


@dataclass
class RecallTrace:
    """Recall explanation trace: captures query context and ranked TraceSteps."""

    trace_id: TraceId
    tenant_id: TenantId
    query: str
    mode: str
    steps: list[TraceStep] = field(default_factory=list)
    query_plan: Any | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)

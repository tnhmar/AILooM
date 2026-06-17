"""Domain event catalog — emitted by use cases, consumed by ObserverPort."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from memory_layer.domain.records import Scope
from memory_layer.domain.types import (
    EntityId,
    FactId,
    JobId,
    LifecycleState,
    MemoryId,
    MemorySector,
    PipelineStatus,
    ScheduleId,
    SessionId,
    TenantId,
    TraceId,
)


# ---------------------------------------------------------------------------
# EventType enum
# ---------------------------------------------------------------------------


class EventType(StrEnum):
    """Exhaustive catalog of all domain events emitted by memory-layer."""

    MEMORY_WRITTEN = "MEMORY_WRITTEN"
    MEMORY_ENRICHED = "MEMORY_ENRICHED"
    ENRICHMENT_FAILED = "ENRICHMENT_FAILED"
    FACTS_EXTRACTED = "FACTS_EXTRACTED"
    CONTRADICTION_DETECTED = "CONTRADICTION_DETECTED"
    CONTRADICTION_LOW_CONFIDENCE = "CONTRADICTION_LOW_CONFIDENCE"
    MEMORY_RECALLED = "MEMORY_RECALLED"
    RECALL_NO_MATCH = "RECALL_NO_MATCH"
    MEMORY_CONSOLIDATED = "MEMORY_CONSOLIDATED"
    MEMORY_DECAYED = "MEMORY_DECAYED"
    MEMORY_ARCHIVED = "MEMORY_ARCHIVED"
    MEMORY_DELETED = "MEMORY_DELETED"
    SESSION_ENDED = "SESSION_ENDED"
    CONSOLIDATION_JOB_STARTED = "CONSOLIDATION_JOB_STARTED"
    CONSOLIDATION_JOB_COMPLETED = "CONSOLIDATION_JOB_COMPLETED"
    TENANT_ISOLATION_VIOLATION = "TENANT_ISOLATION_VIOLATION"
    SCHEMA_MIGRATED = "SCHEMA_MIGRATED"


# ---------------------------------------------------------------------------
# Base event
# ---------------------------------------------------------------------------

_EMPTY_MEMORY_ID = MemoryId("")
_EMPTY_FACT_ID = FactId("")
_EMPTY_ENTITY_ID = EntityId("")
_EMPTY_SESSION_ID = SessionId("")
_EMPTY_JOB_ID = JobId("")
_EMPTY_SCHEDULE_ID = ScheduleId("")


@dataclass(frozen=True)
class MemoryEvent:
    """Immutable base for all domain events."""

    tenant_id: TenantId
    event_id: str = field(default_factory=lambda: str(uuid4()))
    # Subclasses override via object.__setattr__ in __post_init__ (frozen pattern).
    event_type: EventType = field(init=False, default=EventType.MEMORY_WRITTEN)
    occurred_at: datetime = field(default_factory=datetime.utcnow)
    correlation_id: str | None = None


# ---------------------------------------------------------------------------
# Concrete events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryWrittenEvent(MemoryEvent):
    """Emitted after a MemoryRecord is durably persisted."""

    memory_id: MemoryId = _EMPTY_MEMORY_ID
    scope: Scope | None = None
    sector: MemorySector = MemorySector.EPISODIC
    pipeline_status: PipelineStatus = PipelineStatus.PENDING

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", EventType.MEMORY_WRITTEN)


@dataclass(frozen=True)
class MemoryEnrichedEvent(MemoryEvent):
    """Emitted after enrichment pipeline completes successfully."""

    memory_id: MemoryId = _EMPTY_MEMORY_ID
    scope: Scope | None = None
    facts_extracted: int = 0
    entities_extracted: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", EventType.MEMORY_ENRICHED)


@dataclass(frozen=True)
class EnrichmentFailedEvent(MemoryEvent):
    """Emitted when the enrichment pipeline fails for a record."""

    memory_id: MemoryId = _EMPTY_MEMORY_ID
    scope: Scope | None = None
    error: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", EventType.ENRICHMENT_FAILED)


@dataclass(frozen=True)
class FactsExtractedEvent(MemoryEvent):
    """Emitted after facts are extracted from a MemoryRecord."""

    memory_id: MemoryId = _EMPTY_MEMORY_ID
    fact_ids: tuple[FactId, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", EventType.FACTS_EXTRACTED)


@dataclass(frozen=True)
class ContradictionDetectedEvent(MemoryEvent):
    """Emitted when a new fact directly contradicts an existing one."""

    new_fact_id: FactId = _EMPTY_FACT_ID
    superseded_fact_id: FactId = _EMPTY_FACT_ID
    entity_id: EntityId = _EMPTY_ENTITY_ID
    predicate_group: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", EventType.CONTRADICTION_DETECTED)


@dataclass(frozen=True)
class ContradictionLowConfidenceEvent(MemoryEvent):
    """Emitted when a potential contradiction is flagged below confidence threshold."""

    new_fact_id: FactId = _EMPTY_FACT_ID
    entity_id: EntityId = _EMPTY_ENTITY_ID
    predicate_group: str = ""
    confidence: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", EventType.CONTRADICTION_LOW_CONFIDENCE)


@dataclass(frozen=True)
class MemoryRecalledEvent(MemoryEvent):
    """Emitted after a successful recall operation returns results."""

    scope: Scope | None = None
    query_hash: str = ""
    items_returned: int = 0
    total_tokens_estimate: int = 0
    mode: str = ""
    trace_id: TraceId | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", EventType.MEMORY_RECALLED)


@dataclass(frozen=True)
class RecallNoMatchEvent(MemoryEvent):
    """Emitted when a recall query returns no matching records."""

    scope: Scope | None = None
    query_hash: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", EventType.RECALL_NO_MATCH)


@dataclass(frozen=True)
class MemoryConsolidatedEvent(MemoryEvent):
    """Emitted when a MemoryRecord transitions to CONSOLIDATED state."""

    memory_id: MemoryId = _EMPTY_MEMORY_ID
    scope: Scope | None = None
    previous_state: LifecycleState = LifecycleState.ACTIVE

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", EventType.MEMORY_CONSOLIDATED)


@dataclass(frozen=True)
class MemoryDecayedEvent(MemoryEvent):
    """Emitted when a MemoryRecord transitions to DECAYED state."""

    memory_id: MemoryId = _EMPTY_MEMORY_ID
    scope: Scope | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", EventType.MEMORY_DECAYED)


@dataclass(frozen=True)
class MemoryArchivedEvent(MemoryEvent):
    """Emitted when a MemoryRecord transitions to ARCHIVED state."""

    memory_id: MemoryId = _EMPTY_MEMORY_ID
    scope: Scope | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", EventType.MEMORY_ARCHIVED)


@dataclass(frozen=True)
class MemoryDeletedEvent(MemoryEvent):
    """Emitted when a MemoryRecord is permanently deleted."""

    memory_id: MemoryId = _EMPTY_MEMORY_ID
    scope: Scope | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", EventType.MEMORY_DELETED)


@dataclass(frozen=True)
class SessionEndedEvent(MemoryEvent):
    """Emitted when an agent session concludes."""

    session_id: SessionId = _EMPTY_SESSION_ID
    scope: Scope | None = None
    record_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", EventType.SESSION_ENDED)


@dataclass(frozen=True)
class ConsolidationJobStartedEvent(MemoryEvent):
    """Emitted when a scheduled or on-demand consolidation job begins."""

    job_id: JobId = _EMPTY_JOB_ID
    schedule_id: ScheduleId = _EMPTY_SCHEDULE_ID
    trigger: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", EventType.CONSOLIDATION_JOB_STARTED)


@dataclass(frozen=True)
class ConsolidationJobCompletedEvent(MemoryEvent):
    """Emitted when a consolidation job finishes."""

    job_id: JobId = _EMPTY_JOB_ID
    records_processed: int = 0
    duration_ms: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", EventType.CONSOLIDATION_JOB_COMPLETED)


@dataclass(frozen=True)
class TenantIsolationViolationEvent(MemoryEvent):
    """Emitted when a cross-tenant access attempt is detected and blocked."""

    actor: str = ""
    requested_tenant_id: str = ""
    operation: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", EventType.TENANT_ISOLATION_VIOLATION)


@dataclass(frozen=True)
class SchemaMigratedEvent(MemoryEvent):
    """Emitted after a successful schema migration completes."""

    from_version: int = 0
    to_version: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", EventType.SCHEMA_MIGRATED)

"""Inbound port protocols — use-case interfaces called by adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from memory_layer.domain.records import (
    RecallRequest,
    RecallResult,
    Scope,
    SearchRequest,
    WriteRequest,
    WriteResult,
)
from memory_layer.domain.records import MemoryRecord, MemoryTrace
from memory_layer.domain.types import (
    LifecycleState,
    MemoryId,
    MemorySector,
    PipelineStatus,
    SessionId,
    TenantId,
    TraceId,
)


# ---------------------------------------------------------------------------
# Support dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SearchResultItem:
    """A single ranked item returned by a search operation."""

    memory_id: MemoryId
    content: str
    sector: MemorySector
    score: float
    pipeline_status: PipelineStatus
    lifecycle_state: LifecycleState
    signals: dict[str, Any] = field(default_factory=dict)
    effective_from: datetime | None = None


@dataclass
class SearchResult:
    """Aggregated result of a search operation."""

    items: list[SearchResultItem] = field(default_factory=list)
    total: int = 0
    query_plan: Any | None = None
    searched_at: datetime = field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Inbound use-case protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class WriteMemoryUseCase(Protocol):
    """Persist a new memory payload and enqueue enrichment."""

    async def execute(self, request: WriteRequest) -> WriteResult: ...


@runtime_checkable
class SearchMemoryUseCase(Protocol):
    """Search the memory index and return ranked results."""

    async def execute(self, request: SearchRequest) -> SearchResult: ...


@runtime_checkable
class RecallMemoryUseCase(Protocol):
    """Recall memory items for injection into an agent context window."""

    async def execute(self, request: RecallRequest) -> RecallResult: ...


@runtime_checkable
class GetMemoryUseCase(Protocol):
    """Retrieve a single MemoryRecord by ID."""

    async def execute(self, memory_id: MemoryId, tenant_id: TenantId) -> MemoryRecord: ...


@runtime_checkable
class DeleteMemoryUseCase(Protocol):
    """Permanently delete a MemoryRecord and its derived data."""

    async def execute(
        self, memory_id: MemoryId, tenant_id: TenantId, actor: str
    ) -> None: ...


@runtime_checkable
class ExplainRecallUseCase(Protocol):
    """Return the full MemoryTrace for a previous recall operation."""

    async def execute(
        self, trace_id: TraceId, tenant_id: TenantId
    ) -> MemoryTrace: ...


@runtime_checkable
class ConsolidateUseCase(Protocol):
    """Run the consolidation pass for a tenant, returning count of records processed."""

    async def execute(
        self, tenant_id: TenantId, scope: Scope | None = None
    ) -> int: ...


@runtime_checkable
class DecayUseCase(Protocol):
    """Apply retention decay rules for a tenant, returning count of records affected."""

    async def execute(self, tenant_id: TenantId) -> int: ...


@runtime_checkable
class NotifySessionEndedUseCase(Protocol):
    """Handle session-end signal, optionally triggering consolidation."""

    async def execute(
        self, tenant_id: TenantId, session_id: SessionId, scope: Scope
    ) -> None: ...

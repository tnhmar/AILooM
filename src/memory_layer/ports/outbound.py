"""Outbound port protocols — storage and service interfaces implemented by adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from memory_layer.domain.events import MemoryEvent
from memory_layer.domain.policies import TenantPolicies
from memory_layer.domain.records import AuditEntry, Fact, MemoryRecord, Scope
from memory_layer.domain.types import (
    EntityId,
    FactId,
    LifecycleState,
    MemoryId,
    MemorySector,
    PipelineStatus,
    TenantId,
)


# ---------------------------------------------------------------------------
# Support dataclasses
# ---------------------------------------------------------------------------


@dataclass
class VectorDocument:
    """Document unit stored and retrieved by the vector index."""

    memory_id: MemoryId
    tenant_id: TenantId
    embedding: list[float]
    embedding_model_id: str
    embedding_dimensions: int
    content: str
    sector: MemorySector
    lifecycle_state: LifecycleState
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class VectorSearchResult:
    """Single result returned by a vector similarity search."""

    memory_id: MemoryId
    score: float
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FullTextDocument:
    """Document unit stored and retrieved by the full-text index."""

    memory_id: MemoryId
    tenant_id: TenantId
    content: str
    sector: MemorySector
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FullTextSearchResult:
    """Single result returned by a full-text keyword search."""

    memory_id: MemoryId
    score: float
    content: str


@dataclass
class ExtractionResult:
    """Output of the LLM-backed fact extraction pipeline for a single MemoryRecord."""

    memory_record_id: MemoryId
    facts: list[Fact] = field(default_factory=list)
    entities: list[EntityId] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# Outbound port protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class MemoryRecordRepositoryPort(Protocol):
    """Persistent store for MemoryRecord entities."""

    async def save(self, record: MemoryRecord) -> None: ...

    async def get_by_id(
        self, memory_id: MemoryId, tenant_id: TenantId
    ) -> MemoryRecord | None: ...

    async def update_lifecycle(
        self,
        memory_id: MemoryId,
        tenant_id: TenantId,
        state: LifecycleState,
        actor: str,
    ) -> None: ...

    async def update_pipeline_status(
        self, memory_id: MemoryId, tenant_id: TenantId, status: PipelineStatus
    ) -> None: ...

    async def list_by_scope(
        self,
        scope: Scope,
        lifecycle_states: list[LifecycleState],
        limit: int = 100,
    ) -> list[MemoryRecord]: ...

    async def get_by_idempotency_key(
        self, key: str, tenant_id: TenantId
    ) -> MemoryRecord | None: ...


@runtime_checkable
class FactRepositoryPort(Protocol):
    """Persistent store for extracted Fact entities."""

    async def save(self, fact: Fact) -> None: ...

    async def get_by_id(
        self, fact_id: FactId, tenant_id: TenantId
    ) -> Fact | None: ...

    async def close_fact(
        self,
        fact_id: FactId,
        tenant_id: TenantId,
        effective_to: datetime,
        new_fact_id: FactId,
    ) -> None: ...

    async def get_active_facts_by_entity_predicate(
        self,
        entity_id: EntityId,
        predicate_group: str,
        tenant_id: TenantId,
    ) -> list[Fact]: ...

    async def list_by_memory_record(
        self, memory_record_id: MemoryId, tenant_id: TenantId
    ) -> list[Fact]: ...


@runtime_checkable
class VectorIndexPort(Protocol):
    """Vector similarity search index."""

    async def upsert(self, doc: VectorDocument) -> None: ...

    async def search(
        self,
        query_embedding: list[float],
        tenant_id: TenantId,
        k: int,
        filters: dict[str, Any],
    ) -> list[VectorSearchResult]: ...

    async def delete(self, memory_id: MemoryId, tenant_id: TenantId) -> None: ...


@runtime_checkable
class FullTextIndexPort(Protocol):
    """Full-text keyword search index."""

    async def upsert(self, doc: FullTextDocument) -> None: ...

    async def search(
        self,
        query: str,
        tenant_id: TenantId,
        k: int,
        filters: dict[str, Any],
    ) -> list[FullTextSearchResult]: ...

    async def delete(self, memory_id: MemoryId, tenant_id: TenantId) -> None: ...


@runtime_checkable
class EmbeddingPort(Protocol):
    """Text embedding service."""

    model_id: str
    dimensions: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class ExtractionPort(Protocol):
    """LLM-backed fact extraction service."""

    async def extract(self, record: MemoryRecord) -> ExtractionResult: ...


@runtime_checkable
class GraphPort(Protocol):
    """Property graph store for entity and relationship data."""

    async def upsert_entity(
        self,
        entity_id: EntityId,
        tenant_id: TenantId,
        properties: dict[str, Any],
    ) -> None: ...

    async def upsert_relationship(
        self,
        subject: EntityId,
        predicate: str,
        obj: EntityId,
        tenant_id: TenantId,
        properties: dict[str, Any],
    ) -> None: ...

    async def query(
        self, cypher: str, params: dict[str, Any], tenant_id: TenantId
    ) -> list[dict[str, Any]]: ...


@runtime_checkable
class ObserverPort(Protocol):
    """Domain event sink — receives all emitted MemoryEvents."""

    async def emit(self, event: MemoryEvent) -> None: ...


@runtime_checkable
class AuditLogPort(Protocol):
    """Append-only audit log for compliance and debugging."""

    async def append(self, entry: AuditEntry) -> None: ...

    async def get_by_memory_id(
        self, memory_id: MemoryId, tenant_id: TenantId
    ) -> list[AuditEntry]: ...


@runtime_checkable
class TenantPolicyRepositoryPort(Protocol):
    """Persistent store for per-tenant policy configuration."""

    async def get(self, tenant_id: TenantId) -> TenantPolicies: ...

    async def save(self, tenant_id: TenantId, policies: TenantPolicies) -> None: ...

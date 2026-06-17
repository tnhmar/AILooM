# System Design Specification
## Framework-Agnostic AI Memory Layer

**Version:** v1.1
**Status:** Implementation-Ready
**Date:** 2026-06-17
**Supersedes:** v1.0
**Companion documents:** PRD v1.2, ADR Companion (ADR-001–006), ADR Expansion Pack (ADR-007–012)
**Owner:** Architecture

### Changelog v1.0 → v1.1

| # | Source | Change |
|---|---|---|
| 1 | Design review gap #1 | Added `RecallStatus` enum and `status` / `no_match_reason` to `RecallResult` |
| 2 | Design review gap #8 + ADR-008 | Clarified lifecycle transition: `ACTIVE` on durable write; `pipeline_status` carries enrichment state. Updated write flow steps 3–4. |
| 3 | Design review gap #2 | Added `MemoryTrace` as a composed first-class object |
| 4 | Design review gap #3 | Added sector-typed domain object conventions: Episode, Procedure, IdentityProfile, Relationship |
| 5 | Design review gap #4 + ADR-009 | Added `ConsolidationPolicy` and `RetentionPolicy` minimal schemas |
| 6 | Design review gap #5 | Added `schema_version` table and migration command interface |
| 7 | Design review gap #7 + ADR-012 | Added graph-disabled degradation contract |
| 8 | Design review gap (style) | Added `MemoryEvent` base type to ObserverPort |
| 9 | Design review gap (style) | Added `lifecycle_state` and `pipeline_status` to `RecallItem` |
| 10 | Design review gap #6 | Added migration/import design as a reference to forthcoming migration spec |
| 11 | ADR-007 | Added `embedding_model_id` and `embedding_dimensions` to `VectorDocument` metadata; documented model-scoped collection strategy |
| 12 | ADR-008 | Added `wait_for_enrichment` to `WriteRequest`; added `pipeline_status` to `SearchResultItem`; documented session consistency requirement on storage port contracts |
| 13 | ADR-009 | Added `ConsolidationScheduler` protocol; added `SESSION_ENDED` event to catalog; documented threshold trigger and scheduler placement in module map |
| 14 | ADR-010 | Expanded `SearchMode` enum; added `QueryPlanner` protocol and `QueryPlan` dataclass; updated recall flow to show parallel execution and RRF fusion; added weight configuration to tenant policy |
| 15 | ADR-011 | Expanded extraction flow to include contradiction detection steps; added `predicate_group` to `Fact`; added `ConflictResolutionPolicy`; added `CONTRADICTION_LOW_CONFIDENCE` event |
| 16 | ADR-012 | Added RLS note to DDL; specified vector collection naming convention; added `tenant_id` to graph schema; added cross-tenant isolation contract test to adapter rules |

---

## 1. Introduction

This document translates the PRD into a concrete design: modules, ports, domain schema, storage schema, API contracts, data flows, adapter contracts, and benchmark harness mapping. It is the authoritative reference for implementation teams. Architectural decisions are sourced from the ADR companion and ADR expansion pack; this document consumes them and applies them to structure.

---

## 2. Module Map

The system is organized into six top-level modules following ports-and-adapters (hexagonal) architecture. Each module exposes named ports and may only depend on other modules via those ports.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        INBOUND ADAPTERS                                 │
│  Python SDK │ TypeScript SDK │ REST API │ CLI │ MCP Server │ Framework  │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ inbound ports
┌───────────────────────────────▼─────────────────────────────────────────┐
│                         DOMAIN CORE                                     │
│  MemoryRecord · Episode · Fact · Procedure · IdentityProfile            │
│  Relationship · Scope · LifecycleState · TemporalTruth · MemoryTrace    │
│  Use cases: Write · Search · Recall · Explain · Lifecycle               │
└───────┬───────────────┬───────────────┬───────────────┬─────────────────┘
        │               │               │               │ outbound ports
        ▼               ▼               ▼               ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐ ┌───────────────────┐
│ MEMORY ENGINE │ │ POLICY LAYER  │ │ STORAGE       │ │ OBSERVABILITY     │
│ Ingestion     │ │ Reinforcement │ │ PROVIDERS     │ │ & AUDIT           │
│ Extraction    │ │ Consolidation │ │ Relational    │ │ Trace emitter     │
│ Contradiction │ │ Decay         │ │ Vector        │ │ Metric emitter    │
│ detection     │ │ Retention     │ │ Full-text     │ │ Audit log         │
│ Indexing      │ │ Deletion      │ │ Temporal      │ │ Lineage store     │
│ QueryPlanner  │ │ Archival      │ │ Graph (opt.)  │ │                   │
│ Retrieval     │ │               │ │               │ │                   │
│ Ranking (RRF) │ ├───────────────┤ │               │ │                   │
│ Recall pkg    │ │ CONSOLIDATION │ │               │ │                   │
│               │ │ SCHEDULER     │ │               │ │                   │
│               │ │ (in-proc or   │ │               │ │                   │
│               │ │  distributed) │ │               │ │                   │
└───────────────┘ └───────────────┘ └───────────────┘ └───────────────────┘
```

### Module responsibilities

**Domain Core:** All domain objects, invariants, use-case interfaces, and value types. Zero dependencies on external frameworks, storage libraries, or HTTP. Defines all inbound ports (use-case interfaces) and all outbound ports (repository and service interfaces).

**Memory Engine:** Ingestion pipeline, extraction, contradiction detection, indexing, query planning (QueryPlanner), retrieval, RRF fusion, ranking, and recall packaging. Calls outbound storage ports; does not own storage directly.

**Policy Layer:** Lifecycle policies (reinforcement, consolidation, decay, retention, deletion, archival) and the Consolidation Scheduler. Operates as a scheduled or event-driven component calling domain core use cases.

**Storage Providers:** Implements all outbound repository ports. Default local: SQLite + embedded vector index. Default service: PostgreSQL + pgvector. All providers are swappable via configuration.

**Observability and Audit:** Typed `MemoryEvent` emission (OpenTelemetry), metric collection, audit log writes, lineage store. Events are emitted via the outbound `ObserverPort`.

**Inbound Adapters:** Translation only — no business logic. Each adapter translates external requests into domain use-case calls.

---

## 3. Port Definitions

### 3.1 Inbound Ports (Use-Case Interfaces)

```python
class WriteMemoryUseCase:
    def execute(self, request: WriteRequest) -> WriteResult: ...

class SearchMemoryUseCase:
    def execute(self, request: SearchRequest) -> SearchResult: ...

class RecallMemoryUseCase:
    def execute(self, request: RecallRequest) -> RecallResult: ...

class LifecycleUseCase:
    def reinforce(self, memory_id: MemoryId, scope: Scope) -> LifecycleResult: ...
    def consolidate(self, policy: ConsolidationPolicy, scope: Scope) -> LifecycleResult: ...
    def decay(self, policy: RetentionPolicy, scope: Scope) -> LifecycleResult: ...
    def archive(self, memory_id: MemoryId, scope: Scope) -> LifecycleResult: ...
    def delete(self, memory_id: MemoryId, scope: Scope, reason: str) -> LifecycleResult: ...

class ExplainRecallUseCase:
    def execute(self, recall_result: RecallResult) -> ExplainResult: ...

class AuditQueryUseCase:
    def query(self, scope: Scope, filters: AuditFilter) -> AuditLog: ...
```

### 3.2 Outbound Ports (Repository and Service Interfaces)

```python
class MemoryRecordRepository:
    def save(self, record: MemoryRecord) -> MemoryId: ...
    def find_by_id(self, id: MemoryId) -> Optional[MemoryRecord]: ...
    def find_by_scope(self, scope: Scope, filters: RecordFilter) -> List[MemoryRecord]: ...
    def update_lifecycle_state(self, id: MemoryId, state: LifecycleState) -> None: ...
    def update_pipeline_status(self, id: MemoryId, status: PipelineStatus) -> None: ...

class FactRepository:
    def save(self, fact: Fact) -> FactId: ...
    def find_current(self, entity_id: EntityId, predicate_group: str, scope: Scope) -> List[Fact]: ...
    def find_at(self, entity_id: EntityId, predicate_group: str, at: datetime, scope: Scope) -> List[Fact]: ...
    def supersede(self, fact_id: FactId, superseded_by: FactId, effective_to: datetime) -> None: ...
    def find_active_by_entity(self, entity_id: EntityId, scope: Scope) -> List[Fact]: ...

class VectorIndexPort:
    """
    Collections are model-scoped: one collection per (tenant_id, embedding_model_id).
    Naming convention: memory_vectors_{tenant_id_short}_{model_id_slug}
    e.g., memory_vectors_a1b2c3_nomic_embed_v15
    """
    def upsert(self, id: MemoryId, embedding: Vector, metadata: VectorMetadata) -> None: ...
    def search(self, query_embedding: Vector, k: int, scope_filter: ScopeFilter,
               model_id: str) -> List[ScoredId]: ...
    def delete(self, id: MemoryId, model_id: str) -> None: ...

class FullTextIndexPort:
    def index(self, id: MemoryId, text: str, metadata: Dict) -> None: ...
    def search(self, query: str, k: int, scope_filter: ScopeFilter) -> List[ScoredId]: ...

class TemporalIndexPort:
    def index_fact(self, fact: Fact) -> None: ...
    def query_current(self, entity_id: EntityId, scope: Scope) -> List[Fact]: ...
    def query_at(self, entity_id: EntityId, at: datetime, scope: Scope) -> List[Fact]: ...

class GraphPort:
    """
    Optional capability. All methods raise CapabilityNotAvailableError if graph
    provider is not configured. Callers must check is_available() before calling.
    """
    def is_available(self) -> bool: ...
    def upsert_entity(self, entity: Entity) -> None: ...
    def upsert_relationship(self, rel: Relationship) -> None: ...
    def traverse(self, start: EntityId, depth: int, scope: Scope) -> SubGraph: ...

class EmbeddingPort:
    model_id: str
    dimensions: int

    def embed(self, texts: List[str]) -> List[Vector]: ...
    def embed_query(self, query: str) -> Vector: ...

class ExtractionPort:
    def extract(self, raw: str, context: ExtractionContext) -> ExtractionResult: ...

class ObserverPort:
    def emit(self, event: MemoryEvent) -> None: ...

class AuditLogPort:
    def record(self, entry: AuditEntry) -> None: ...
    def query(self, scope: Scope, filters: AuditFilter) -> List[AuditEntry]: ...
```

### 3.3 ConsolidationScheduler Protocol (Policy Layer)

```python
class ConsolidationScheduler(Protocol):
    """
    Separate from domain core. Calls LifecycleUseCase via inbound port.
    Local mode: lightweight in-process scheduler (e.g., APScheduler).
    Service mode: distributed scheduler with persisted state (Celery Beat, etc.).
    Scheduler state (job registry, per-tenant time-slice limits) must be persisted
    to survive restarts.
    """
    def register_policy(self, policy: ConsolidationPolicy, scope: Scope) -> ScheduleId: ...
    def trigger_session_boundary(self, scope: Scope) -> JobId: ...
    def trigger_threshold(self, scope: Scope, trigger: ThresholdTrigger) -> JobId: ...
    def get_job_status(self, job_id: JobId) -> JobStatus: ...
    def cancel(self, schedule_id: ScheduleId) -> None: ...
```

### 3.4 QueryPlanner Protocol (Memory Engine)

```python
class QueryPlanner(Protocol):
    def plan(self, request: SearchRequest) -> QueryPlan: ...
    def execute(self, plan: QueryPlan) -> List[ScoredCandidate]: ...

@dataclass
class QueryPlan:
    mode: SearchMode
    indexes: List[IndexTarget]               # which ports to query
    fusion_strategy: FusionStrategy          # RRF | WEIGHTED | STRUCTURAL
    weights: Dict[str, float]                # per-signal weights (tenant-configurable)
    rerank: bool                             # LLM rerank in QUALITY mode
    explanation_enabled: bool
    parallel: bool = True                    # all index queries run in parallel

@dataclass
class ScoredCandidate:
    memory_id: MemoryId
    signals: Dict[str, float]               # semantic, keyword, entity, temporal, recency
    composite_score: float
    source_indexes: List[str]
```

---

## 4. Domain Object Schema

### 4.1 MemoryRecord

```python
@dataclass(frozen=True)
class MemoryRecord:
    id: MemoryId
    tenant_id: TenantId
    scope: Scope
    raw_payload: str                          # immutable verbatim content
    payload_type: PayloadType                 # CONVERSATION_TURN | DOCUMENT | TOOL_OUTPUT | EVENT | STRUCTURED
    sector: MemorySector                      # EPISODIC | SEMANTIC | PROCEDURAL | IDENTITY | RELATIONAL | REFLECTIVE
    lifecycle_state: LifecycleState           # ACTIVE on durable write (see Section 7.1)
    pipeline_status: PipelineStatus           # PENDING | ENRICHED | PARTIAL_ENRICHMENT_FAILED | ENRICHMENT_SKIPPED
    recorded_at: datetime
    idempotency_key: Optional[str]
    metadata: Dict[str, Any]                  # sector-specific conventions — see Section 4.2
```

### 4.2 Sector-Typed Domain Object Conventions

All domain objects (Episode, Procedure, IdentityProfile, Relationship) are realised as sector-typed `MemoryRecord` with documented `metadata` conventions. No separate table is required; the `sector` field and `metadata` schema differentiate them at the application level.

```python
# EPISODIC sector — Episode
metadata = {
    "occurred_at": "ISO8601",               # when the event happened
    "participants": ["principal_id", ...],
    "event_type": str,                      # e.g., "conversation_turn", "tool_use", "decision"
    "outcome": Optional[str]
}

# PROCEDURAL sector — Procedure
metadata = {
    "procedure_name": str,
    "trigger_condition": str,
    "steps": List[str],
    "version": int,
    "superseded_by": Optional[MemoryId]
}

# IDENTITY sector — IdentityProfile
metadata = {
    "entity_type": str,                     # USER | AGENT | ORG
    "display_name": str,
    "attributes": Dict[str, Any],
    "profile_version": int
}

# RELATIONAL sector — Relationship
metadata = {
    "source_entity_id": str,
    "target_entity_id": str,
    "relationship_type": str,               # e.g., "works_at", "manages", "collaborates_with"
    "effective_from": "ISO8601",
    "effective_to": Optional["ISO8601"]
}
```

### 4.3 MemoryTrace

`MemoryTrace` is a composed object built from existing system data. It is not persisted as a separate entity; it is constructed on-demand by `ExplainRecallUseCase`.

```python
@dataclass
class MemoryTrace:
    trace_id: TraceId
    memory_id: MemoryId
    scope: Scope

    # Write lineage
    write_event: AuditEntry                  # WRITE operation entry
    enrichment_status: PipelineStatus
    facts_derived: List[FactId]
    entities_extracted: List[EntityId]

    # Mutation lineage (supersession, consolidation)
    mutations: List[AuditEntry]              # ordered chronologically

    # Recall trace
    recall_event: Optional[AuditEntry]       # RECALL operation if trace is from a recall
    recall_signals: Optional[Dict[str, float]]
    recall_explanation: Optional[str]
    query_plan: Optional[QueryPlan]          # plan used to retrieve this item

    constructed_at: datetime
```

### 4.4 Fact

```python
@dataclass(frozen=True)
class Fact:
    id: FactId
    memory_record_id: MemoryId
    tenant_id: TenantId
    scope: Scope
    subject_entity_id: EntityId
    predicate: str                           # raw extracted predicate
    predicate_group: str                     # normalized group (e.g., "preference", "location")
    object_value: str
    effective_from: datetime
    effective_to: Optional[datetime]         # None = currently valid
    recorded_at: datetime
    supersedes: Optional[FactId]
    confidence: float
    sector: MemorySector
```

### 4.5 Scope

```python
@dataclass(frozen=True)
class Scope:
    tenant_id: TenantId
    workspace_id: Optional[WorkspaceId]
    principal_type: PrincipalType            # USER | AGENT
    principal_id: PrincipalId
    session_id: Optional[SessionId]
    run_id: Optional[RunId]
```

### 4.6 WriteRequest / WriteResult

```python
@dataclass
class WriteRequest:
    tenant_id: TenantId
    scope: Scope
    raw_payload: str
    payload_type: PayloadType
    sector: Optional[MemorySector]           # inferred if None
    idempotency_key: Optional[str]
    extract: bool = True
    wait_for_enrichment: bool = False        # block until pipeline_status = ENRICHED
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class WriteResult:
    memory_id: MemoryId
    scope: Scope
    pipeline_status: PipelineStatus
    accepted_at: datetime
    idempotent: bool
```

### 4.7 SearchRequest / SearchResult

```python
class SearchMode(Enum):
    SEMANTIC = "SEMANTIC"
    KEYWORD = "KEYWORD"
    HYBRID = "HYBRID"
    HYBRID_TEMPORAL = "HYBRID_TEMPORAL"
    QUALITY = "QUALITY"             # HYBRID + optional LLM rerank
    GRAPH = "GRAPH"                 # requires graph capability

@dataclass
class SearchRequest:
    tenant_id: TenantId
    scope: Scope
    query: str
    mode: SearchMode = SearchMode.HYBRID
    sectors: Optional[List[MemorySector]] = None
    lifecycle_states: List[LifecycleState] = field(default_factory=lambda: [LifecycleState.ACTIVE])
    temporal_filter: Optional[TemporalFilter] = None
    k: int = 10

@dataclass
class SearchResultItem:
    memory_id: MemoryId
    score: float
    raw_payload: str
    sector: MemorySector
    lifecycle_state: LifecycleState
    pipeline_status: PipelineStatus          # so callers can reason about enrichment state
    recorded_at: datetime
    signals: Dict[str, float]               # semantic, keyword, entity, temporal, recency
    explanation: str
    query_plan_mode: SearchMode             # mode used by planner

@dataclass
class SearchResult:
    items: List[SearchResultItem]
    query: str
    mode: SearchMode
    total_matched: int
    query_plan: QueryPlan                   # full plan for observability
```

### 4.8 RecallRequest / RecallResult

```python
class RecallStatus(Enum):
    MATCH = "MATCH"
    PARTIAL_MATCH = "PARTIAL_MATCH"
    NO_MATCH = "NO_MATCH"

@dataclass
class RecallRequest:
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
class RecallResult:
    status: RecallStatus                     # MATCH | PARTIAL_MATCH | NO_MATCH
    no_match_reason: Optional[str]           # populated when status = NO_MATCH
    items: List[RecallItem]
    total_tokens_estimate: int
    recall_strategy: str
    recalled_at: datetime

@dataclass
class RecallItem:
    memory_id: MemoryId
    content: str
    sector: MemorySector
    lifecycle_state: LifecycleState          # for auditability
    pipeline_status: PipelineStatus          # so callers can reason about enrichment state
    effective_from: Optional[datetime]
    signals: Dict[str, float]               # semantic, keyword, entity, temporal, recency
    explanation: str
    trace_id: TraceId
```

### 4.9 ConsolidationPolicy

```python
@dataclass
class ConsolidationPolicy:
    id: PolicyId
    tenant_id: TenantId
    scope: Optional[Scope]                  # None = applies to all scopes for tenant
    sectors: List[MemorySector]
    trigger: ConsolidationTrigger           # SESSION_END | THRESHOLD | SCHEDULE | ON_DEMAND
    threshold_record_count: Optional[int]   # used when trigger = THRESHOLD (default: 500)
    grouping_strategy: GroupingStrategy     # BY_ENTITY | BY_TOPIC | BY_TIME_WINDOW
    time_window_hours: Optional[int]        # used when grouping = BY_TIME_WINDOW
    summarization_strategy: SummarizationStrategy  # EXTRACTIVE | ABSTRACTIVE | VERBATIM_MERGE
    min_records_to_consolidate: int = 3
    created_at: datetime
    active: bool = True
```

### 4.10 RetentionPolicy

```python
@dataclass
class RetentionPolicy:
    id: PolicyId
    tenant_id: TenantId
    scope: Optional[Scope]
    sectors: List[MemorySector]
    decay_after_days: Optional[int]         # records older than N days moved to DECAYED
    archive_after_days: Optional[int]       # DECAYED records older than N days archived
    delete_after_days: Optional[int]        # ARCHIVED records older than N days deleted-with-trace
    legal_hold: bool = False                # overrides all deletion if True
    created_at: datetime
    active: bool = True
```

### 4.11 ConflictResolutionPolicy

```python
class ConflictResolutionMode(Enum):
    AUTO_CLOSE = "AUTO_CLOSE"               # default: new fact wins, old fact effective_to set
    FLAG_ONLY = "FLAG_ONLY"                 # both remain ACTIVE, event emitted
    MANUAL = "MANUAL"                       # new fact goes to PROPOSED
    LLM_ARBITRATED = "LLM_ARBITRATED"      # post-v1 only

@dataclass
class ConflictResolutionPolicy:
    tenant_id: TenantId
    mode: ConflictResolutionMode = ConflictResolutionMode.AUTO_CLOSE
    low_confidence_threshold: float = 0.6   # facts below this go to PROPOSED regardless of mode
    predicate_group_map: Dict[str, List[str]] = field(default_factory=dict)
    # e.g., {"preference": ["prefers", "likes", "switched_to", "chose", "now_uses"]}
```

### 4.12 AuditEntry

```python
@dataclass(frozen=True)
class AuditEntry:
    id: AuditId
    tenant_id: TenantId
    scope: Scope
    operation: AuditOperation               # WRITE | SEARCH | RECALL | CONSOLIDATE | DECAY | ARCHIVE | DELETE | MIGRATION
    memory_id: Optional[MemoryId]
    actor: str
    timestamp: datetime
    outcome: AuditOutcome                   # SUCCESS | PARTIAL | FAILED
    detail: Dict[str, Any]
```

### 4.13 MemoryEvent Base Type

```python
@dataclass
class MemoryEvent:
    """Base type for all observer events. All concrete events inherit from this."""
    event_type: str                         # matches event catalog keys (Section 9)
    tenant_id: TenantId
    scope: Optional[Scope]
    memory_id: Optional[MemoryId]
    trace_id: TraceId
    timestamp: datetime
    latency_ms: Optional[float]
    detail: Dict[str, Any]
```

---

## 5. Storage Schema

### 5.1 Relational Schema (PostgreSQL / SQLite)

**Note on RLS (service mode):** In service mode, `FORCE ROW LEVEL SECURITY` and RLS policies are applied to all tenant-scoped tables. The application layer must set `app.tenant_id` via `SET LOCAL app.tenant_id = '...'` at the start of every transaction. See ADR-012 for full RLS policy definitions. Commented stubs below show the pattern.

```sql
-- Schema version tracking
CREATE TABLE schema_version (
    version         INTEGER PRIMARY KEY,
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    description     TEXT NOT NULL
);
INSERT INTO schema_version (version, description) VALUES (1, 'Initial schema v1.1');

-- Migration command interface:
-- CLI: memory-layer migrate [up|down|status] [--target-version N]
-- Migrations are additive-only within a major version.
-- Breaking changes require a major version bump and a migration spec document.
-- Scripts live in: migrations/V{version}__{description}.sql

-- Core record store
CREATE TABLE memory_records (
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    scope_hash      TEXT NOT NULL,
    workspace_id    UUID,
    principal_type  TEXT NOT NULL,
    principal_id    UUID NOT NULL,
    session_id      UUID,
    run_id          UUID,
    raw_payload     TEXT NOT NULL,
    payload_type    TEXT NOT NULL,
    sector          TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL DEFAULT 'ACTIVE',   -- set ACTIVE on durable write
    pipeline_status TEXT NOT NULL DEFAULT 'PENDING',
    idempotency_key TEXT,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata        JSONB,
    CONSTRAINT uq_idempotency UNIQUE (tenant_id, idempotency_key)
);

-- Service mode RLS stubs:
-- ALTER TABLE memory_records ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE memory_records FORCE ROW LEVEL SECURITY;
-- CREATE POLICY tenant_isolation ON memory_records
--   USING (tenant_id = current_setting('app.tenant_id')::uuid);

CREATE INDEX idx_memory_tenant_scope ON memory_records (tenant_id, principal_id, session_id);
CREATE INDEX idx_memory_sector       ON memory_records (sector, lifecycle_state);
CREATE INDEX idx_memory_recorded_at  ON memory_records (recorded_at DESC);
CREATE INDEX idx_memory_pipeline     ON memory_records (pipeline_status)
    WHERE pipeline_status != 'ENRICHED';

-- Fact store with temporal validity and predicate grouping
CREATE TABLE facts (
    id                UUID PRIMARY KEY,
    memory_record_id  UUID NOT NULL REFERENCES memory_records(id),
    tenant_id         UUID NOT NULL,
    principal_id      UUID NOT NULL,
    session_id        UUID,
    subject_entity_id UUID NOT NULL,
    predicate         TEXT NOT NULL,
    predicate_group   TEXT NOT NULL,          -- normalized group for contradiction detection
    object_value      TEXT NOT NULL,
    effective_from    TIMESTAMPTZ NOT NULL,
    effective_to      TIMESTAMPTZ,            -- NULL = currently valid
    recorded_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    supersedes        UUID REFERENCES facts(id),
    confidence        FLOAT NOT NULL DEFAULT 1.0,
    sector            TEXT NOT NULL
);

CREATE INDEX idx_facts_entity_current  ON facts (subject_entity_id, predicate_group, effective_to)
    WHERE effective_to IS NULL;
CREATE INDEX idx_facts_entity_temporal ON facts (subject_entity_id, predicate_group, effective_from, effective_to);
CREATE INDEX idx_facts_tenant          ON facts (tenant_id, principal_id);

-- Entity registry
CREATE TABLE entities (
    id          UUID PRIMARY KEY,
    tenant_id   UUID NOT NULL,
    name        TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata    JSONB
);

-- Tenant policy store (consolidation, retention, conflict resolution, search weights)
CREATE TABLE tenant_policies (
    id          UUID PRIMARY KEY,
    tenant_id   UUID NOT NULL,
    policy_type TEXT NOT NULL,
    policy_data JSONB NOT NULL,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_policies_tenant ON tenant_policies (tenant_id, policy_type, active);

-- Audit log
CREATE TABLE audit_log (
    id          UUID PRIMARY KEY,
    tenant_id   UUID NOT NULL,
    scope_hash  TEXT NOT NULL,
    operation   TEXT NOT NULL,
    memory_id   UUID,
    actor       TEXT NOT NULL,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    outcome     TEXT NOT NULL,
    detail      JSONB
);

CREATE INDEX idx_audit_tenant_ts ON audit_log (tenant_id, timestamp DESC);
```

### 5.2 Vector Collection Schema

```python
# Collection naming convention (ADR-012):
# One collection per (tenant_id, embedding_model_id).
# Collection name: memory_vectors_{tenant_id_short}_{model_id_slug}
# Example: memory_vectors_a1b2c3_nomic_embed_v15
# Collections are created on first write and dropped on tenant offboarding.

@dataclass
class VectorMetadata:
    """Metadata stored alongside every embedding vector."""
    tenant_id: str
    principal_id: str
    session_id: Optional[str]
    sector: str
    lifecycle_state: str
    pipeline_status: str
    recorded_at: str                        # ISO8601
    payload_type: str
    embedding_model_id: str                 # e.g., "nomic-embed-text-v1.5"
    embedding_dimensions: int               # e.g., 768
```

### 5.3 Full-Text Index

```sql
-- SQLite FTS5
CREATE VIRTUAL TABLE memory_fts USING fts5(
    memory_id UNINDEXED,
    content,
    tenant_id UNINDEXED,
    principal_id UNINDEXED,
    tokenize = "unicode61"
);

-- PostgreSQL tsvector
ALTER TABLE memory_records ADD COLUMN fts_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('english', raw_payload)) STORED;
CREATE INDEX idx_memory_fts ON memory_records USING GIN(fts_vector);
```

### 5.4 Optional Graph Schema (Neo4j / Kuzu Cypher)

```cypher
// All nodes and edges carry tenant_id as a mandatory property (ADR-012).
// All graph queries MUST include tenant_id as a filter predicate.

(:Entity {
  id: UUID,
  tenant_id: UUID,          // mandatory
  name: String,
  entity_type: String
})

[:RELATES_TO {
  id: UUID,
  tenant_id: UUID,          // mandatory
  predicate: String,
  predicate_group: String,
  effective_from: DateTime,
  effective_to: DateTime,
  confidence: Float,
  source_memory_id: UUID
}]

(:MemoryNode {
  id: UUID,
  tenant_id: UUID,          // mandatory
  sector: String,
  recorded_at: DateTime
})

(:Entity)-[:MENTIONED_IN]->(:MemoryNode)
(:MemoryNode)-[:SUPERSEDES]->(:MemoryNode)

// Mandatory query pattern — always include tenant filter:
// MATCH (e:Entity {tenant_id: $tid})-[r]->(n)
// WHERE r.tenant_id = $tid RETURN ...
```

---

## 6. API Contracts

### 6.1 Python SDK Surface

```python
from memory_layer import MemoryClient, WriteRequest, SearchRequest, RecallRequest

client = MemoryClient(backend="local", config=LocalConfig(data_dir="~/.memory"))

# Write (async enrichment)
result: WriteResult = client.write(WriteRequest(
    scope=Scope(tenant_id="t1", principal_type="USER", principal_id="u1"),
    raw_payload="User prefers dark mode in all applications.",
    payload_type=PayloadType.CONVERSATION_TURN,
    idempotency_key="session-42-turn-7"
))
# result.pipeline_status = PENDING

# Write and wait for enrichment (integration tests, benchmarks)
result: WriteResult = client.write(WriteRequest(..., wait_for_enrichment=True))
# result.pipeline_status = ENRICHED | PARTIAL_ENRICHMENT_FAILED

# Recall
result: RecallResult = client.recall(RecallRequest(...))
if result.status == RecallStatus.NO_MATCH:
    # result.no_match_reason explains why
    pass
```

### 6.2 REST API Endpoints

```
POST   /v1/memories                    Write a memory
GET    /v1/memories/{id}               Fetch by ID
POST   /v1/memories/search             Search memories
POST   /v1/memories/recall             Recall for context hydration
GET    /v1/memories/{id}/explain       Explain a memory (returns MemoryTrace)
GET    /v1/memories/{id}/trace         Full MemoryTrace for a memory
PATCH  /v1/memories/{id}/lifecycle     Update lifecycle state
DELETE /v1/memories/{id}               Delete with trace

POST   /v1/facts                       Upsert a structured fact
GET    /v1/facts/{entity_id}/current   Current facts for entity
GET    /v1/facts/{entity_id}/at        Point-in-time facts for entity

POST   /v1/lifecycle/consolidate       Trigger consolidation policy
POST   /v1/lifecycle/decay             Trigger decay policy
POST   /v1/lifecycle/archive           Trigger archival policy

GET    /v1/audit                       Query audit log
GET    /v1/graph/entity/{id}           Graph entity lookup (503 if graph disabled)
GET    /v1/graph/traverse/{id}         Graph traversal (503 if graph disabled)

POST   /v1/migrations/import           Import from external source
GET    /v1/migrations/status/{job_id}  Migration job status

GET    /v1/health                      Health check
GET    /v1/metrics                     Prometheus-compatible metrics
GET    /v1/capabilities                Returns enabled capabilities including graph
```

### 6.3 Graph-Disabled Degradation Contract

When the graph provider is not configured:

- `GraphPort.is_available()` returns `False`.
- `GET /v1/graph/**` returns `503 Service Unavailable` with body `{"error": "CAPABILITY_NOT_AVAILABLE", "capability": "graph"}`.
- `GET /v1/capabilities` returns `{"graph": false}`.
- Fact and entity operations backed by relational + vector stores continue to function normally. Graph capability is not required for fact writes, reads, or temporal queries.
- Domain use cases check `GraphPort.is_available()` before calling graph methods; graph indexing steps in the write pipeline are skipped silently when disabled.

### 6.4 MCP Server Tools

```
memory_write         Write raw memory
memory_search        Search memories
memory_recall        Recall context for prompt hydration
memory_explain       Explain a recalled item (returns MemoryTrace)
fact_upsert          Upsert a structured fact
fact_current         Get current facts for an entity
fact_at              Get point-in-time facts for an entity
lifecycle_reinforce  Reinforce a memory
lifecycle_decay      Trigger decay on a scope
audit_query          Query audit log
capabilities_get     Get enabled capabilities
```

---

## 7. Data Flow Diagrams

### 7.1 Write Path

```
Adapter (SDK / REST / MCP)
    │
    │  WriteRequest
    ▼
WriteMemoryUseCase
    │
    ├── 1. Validate scope and tenant isolation (fail closed on violation)
    ├── 2. Check idempotency key (return existing WriteResult if duplicate)
    ├── 3. Persist MemoryRecord → MemoryRecordRepository
    │       lifecycle_state = ACTIVE     ← set ACTIVE on durable write
    │       pipeline_status = PENDING
    ├── 4. Emit WRITE_ACCEPTED → ObserverPort
    ├── 5. Record audit entry → AuditLogPort
    ├── 6. Return WriteResult (pipeline_status: PENDING)
    │       [if wait_for_enrichment=True: block on enrichment completion]
    │
    └── [ASYNC] ExtractionPipeline
            │
            ├── 7.  Embed raw_payload → EmbeddingPort
            ├── 8.  Index embedding → VectorIndexPort (model-scoped collection)
            ├── 9.  Index full-text → FullTextIndexPort
            ├── 10. Extract facts and entities → ExtractionPort
            ├── 11. For each extracted Fact:
            │       a. Query FactRepository for active facts matching
            │          (entity_id, predicate_group) — contradiction detection
            │       b. If conflict found AND confidence >= threshold:
            │             Apply ConflictResolutionPolicy (AUTO_CLOSE by default)
            │             Set conflicting fact effective_to = new fact effective_from
            │             Emit CONTRADICTION_DETECTED
            │       c. If conflict found AND confidence < threshold:
            │             Set new fact lifecycle_state = PROPOSED
            │             Emit CONTRADICTION_LOW_CONFIDENCE
            │       d. Persist new Fact → FactRepository
            │       e. Index temporal fact → TemporalIndexPort
            ├── 12. [IF graph enabled] Upsert entities/relationships → GraphPort
            ├── 13. Update pipeline_status → ENRICHED
            │       [or PARTIAL_ENRICHMENT_FAILED if any step above failed]
            │       lifecycle_state remains ACTIVE in both outcomes
            └── 14. Emit WRITE_ENRICHED (or WRITE_ENRICHMENT_FAILED) → ObserverPort
```

**Critical invariant:** `lifecycle_state` is set to `ACTIVE` at step 3 and is never changed by enrichment. A write-acknowledged record is always searchable. `pipeline_status` is the sole field that reflects enrichment progress. Records with `pipeline_status = PARTIAL_ENRICHMENT_FAILED` are ACTIVE and searchable by raw content but may lack embeddings or extracted facts.

### 7.2 Recall Path (Parallel Hybrid Retrieval with RRF Fusion)

```
Adapter (SDK / REST / MCP)
    │
    │  RecallRequest
    ▼
RecallMemoryUseCase
    │
    ├── 1. Validate scope and tenant isolation
    ├── 2. Invoke QueryPlanner.plan(request) → QueryPlan
    ├── 3. QueryPlanner.execute(plan) — PARALLEL dispatch:
    │       ├── [Semantic]  Embed query → EmbeddingPort
    │       │               Search → VectorIndexPort → List[ScoredId]
    │       ├── [Keyword]   Search → FullTextIndexPort → List[ScoredId]
    │       ├── [Temporal]  Query → TemporalIndexPort → List[Fact]   (HYBRID_TEMPORAL)
    │       └── [Entity]    Extract query entities → ExtractionPort  (HYBRID / QUALITY)
    │               All index calls dispatched concurrently; results collected after all resolve
    ├── 4. Fuse results via RRF (default) or weighted fusion per QueryPlan.weights
    │       composite_score = w_sem*s + w_kw*k + w_ent*e + w_temp*t + w_rec*r
    ├── 5. Apply scope filter and lifecycle filter (ACTIVE by default)
    ├── 6. Rank and select top-k within token budget
    ├── 7. Hydrate payloads → MemoryRecordRepository
    ├── 8. Build explanation and per-signal scores per item
    ├── 9. Determine RecallStatus:
    │       MATCH         if items > 0 and confidence >= threshold
    │       PARTIAL_MATCH if items > 0 but some signals missing or low confidence
    │       NO_MATCH      if items = 0 → set no_match_reason
    ├── 10. Package RecallResult (status, no_match_reason, items)
    ├── 11. Emit RECALL_COMPLETED or RECALL_NO_MATCH → ObserverPort
    └── 12. Record audit entry → AuditLogPort
```

### 7.3 Consolidation Path

```
ConsolidationScheduler (session boundary | threshold | cron)
    │
    ▼
LifecycleUseCase.consolidate(policy, scope)
    │
    ├── 1. Load active memories matching policy criteria
    ├── 2. Group per policy.grouping_strategy
    ├── 3. Derive consolidated representation per policy.summarization_strategy
    ├── 4. Write consolidated MemoryRecord (lifecycle_state = ACTIVE)
    ├── 5. Mark source records as CONSOLIDATED (lineage preserved, not deleted)
    ├── 6. Record lineage links in metadata
    ├── 7. Update fact supersession chains → FactRepository
    ├── 8. Emit CONSOLIDATION_COMPLETE → ObserverPort
    └── 9. Record audit entry → AuditLogPort
```

### 7.4 Lifecycle / Decay Path

```
ConsolidationScheduler (cron — background medium/slow tier)
    │
    ▼
LifecycleUseCase.decay(policy, scope)
    │
    ├── 1. Load ACTIVE records matching policy age and sector criteria
    ├── 2. Apply RetentionPolicy.decay_after_days threshold
    ├── 3. Update lifecycle_state → DECAYED
    ├── 4. Adjust vector metadata (deprioritized in ranking)
    ├── 5. Emit DECAY_COMPLETE → ObserverPort
    └── 6. Record audit entry → AuditLogPort
```

---

## 8. Adapter Contracts

### 8.1 Framework Adapter Interface

```python
class MemoryLayerAdapter(Protocol):
    """
    All adapters must satisfy this contract.
    No business logic permitted — translation only.
    Adapters must propagate OpenTelemetry trace context into every core use-case call.
    Adapters must not catch domain exceptions silently; they must translate or re-raise.
    Adapters must call capabilities() and handle CAPABILITY_NOT_AVAILABLE gracefully.
    """
    def write(self, request: WriteRequest) -> WriteResult: ...
    def search(self, request: SearchRequest) -> SearchResult: ...
    def recall(self, request: RecallRequest) -> RecallResult: ...
    def explain(self, trace_id: TraceId) -> ExplainResult: ...
    def lifecycle(self, operation: LifecycleOperation, memory_id: MemoryId, scope: Scope) -> LifecycleResult: ...
    def capabilities(self) -> Dict[str, bool]: ...
```

### 8.2 LangGraph Adapter Contract

```python
from langgraph.graph import StateGraph
from memory_layer.adapters.langgraph import RecallNode, WriteNode

recall_node = RecallNode(client=memory_client)
write_node  = WriteNode(client=memory_client)

builder = StateGraph(AgentState)
builder.add_node("recall_memory", recall_node)
builder.add_node("write_memory",  write_node)

# State key contracts (adapter must not mutate other keys):
# recall_node reads:  state["query"], state["scope"]
# recall_node writes: state["recalled_context"], state["recall_status"]
# write_node reads:   state["output"], state["scope"]
# write_node writes:  state["memory_write_result"]
```

### 8.3 MCP Adapter Tool Schema

```
Tool: memory_recall
Input:
  query:          string  (required)
  tenant_id:      string  (required)
  principal_id:   string  (required)
  session_id:     string  (optional)
  max_tokens:     integer (optional, default 2000)
  sectors:        array[string] (optional)
  mode:           string  (optional, default "HYBRID")

Output:
  status:              string (MATCH | PARTIAL_MATCH | NO_MATCH)
  no_match_reason:     string (populated when status = NO_MATCH)
  items:               array[{memory_id, content, sector, lifecycle_state,
                               pipeline_status, explanation, trace_id}]
  total_tokens_estimate: integer
  recalled_at:         string (ISO8601)
```

### 8.4 Adapter Validation Rules

1. Adapters contain translation logic only; no memory business logic is permitted.
2. Adapter contract tests must verify that no core domain types are mutated inside the adapter.
3. Adapters must not catch domain exceptions silently; they must translate or re-raise.
4. Adapters must propagate OpenTelemetry span context into every core use-case call.
5. **Cross-tenant isolation test is a mandatory CI gate:** A contract test must verify that a request scoped to tenant A cannot return any results belonging to tenant B. This test must pass before any adapter is considered releasable.
6. Adapters must call `capabilities()` and gracefully handle `CAPABILITY_NOT_AVAILABLE` for graph-related requests.

---

## 9. Observability Event Catalog

All events inherit from `MemoryEvent` base type (Section 4.13).

| Event | Trigger | Key `detail` Fields |
|---|---|---|
| WRITE_ACCEPTED | Raw record persisted (lifecycle=ACTIVE) | pipeline_status, latency_ms |
| WRITE_ENRICHED | Extraction pipeline complete | facts_extracted, entities_extracted, model_id, latency_ms |
| WRITE_ENRICHMENT_FAILED | Extraction error | error_code, raw_error |
| SEARCH_COMPLETED | Search returned | query, mode, k, items_returned, query_plan, latency_ms |
| RECALL_COMPLETED | Recall returned | query, status, items_returned, total_tokens, strategy, latency_ms |
| RECALL_NO_MATCH | Recall returned zero items | query, scope, no_match_reason |
| CONSOLIDATION_COMPLETE | Consolidation run finished | records_consolidated, policy_id, latency_ms |
| DECAY_COMPLETE | Decay run finished | records_decayed, policy_id |
| DELETE_WITH_TRACE | Record deleted | actor, reason |
| CONTRADICTION_DETECTED | Conflicting fact auto-closed | fact_ids, entity_id, predicate_group, resolution_mode |
| CONTRADICTION_LOW_CONFIDENCE | Conflicting fact held in PROPOSED | fact_id, confidence, threshold, predicate_group |
| TENANT_ISOLATION_VIOLATION | Cross-tenant access attempt detected | actor, requested_tenant_id |
| SESSION_ENDED | Session boundary reached | session_id, record_count, triggers_consolidation |
| MIGRATION_STARTED | Import job initiated | source_format, job_id |
| MIGRATION_COMPLETED | Import job complete | job_id, records_imported, records_failed |
| MIGRATION_FAILED | Import job error | job_id, error_code, records_imported_before_failure |

---

## 10. Migration and Import Design

The import system translates external memory formats into `MemoryRecord + Fact` pairs. Full transformation logic per source format is specified in a separate **Migration Specification** document (forthcoming). Design contracts:

- Import runs as a background job; `POST /v1/migrations/import` returns `job_id` immediately.
- Each imported record receives a new `MemoryId`; the original source ID is preserved in `metadata.source_id`.
- Scope, timestamps, and lineage are preserved where the source format provides them.
- If a single record fails transformation, it is skipped and counted in `records_failed`; the job continues.
- `MIGRATION_STARTED`, `MIGRATION_COMPLETED`, and `MIGRATION_FAILED` events are emitted (Section 9).
- Import jobs respect `ConflictResolutionPolicy` for facts that conflict with existing tenant data.
- v1 supported source format: Mem0-style JSONL. Further formats tracked in the Migration Specification.

---

## 11. Benchmark Harness Mapping

### LongMemEval

- Ingest 500 conversation turns via `WriteRequest` (CONVERSATION_TURN, EPISODIC) with `wait_for_enrichment=True`.
- Record `embedding_model_id` and `embedding_dimensions` in every benchmark run result.
- Issue `RecallRequest` (mode=HYBRID) per benchmark question.
- Evaluate `RecallItem.content` against gold answers.
- Metrics: Recall@1, Recall@5, temporal_correctness_rate, NO_MATCH rate.

### LoCoMo

- Ingest LoCoMo dialogue pairs via `WriteRequest`.
- Issue `SearchRequest` (mode=HYBRID) and `RecallRequest` per question.
- Evaluate top-1 match against gold label.
- Metrics: Recall@1, temporal_correctness_rate, token_budget_adherence, PARTIAL_MATCH rate.

### Internal Suite

```
Temporal truth test:
  Write F1 (effective_from T1, effective_to None)
  Write F2 superseding F1 (effective_from T2)
  Recall at T1 → must return F1
  Recall at T3 (T3 > T2) → must return F2

No-match contract test:
  Recall on scope with no records
  result.status must equal NO_MATCH
  result.no_match_reason must be non-empty

Scope isolation test:
  Write records under scope A
  Recall under scope B → must return NO_MATCH

Idempotency test:
  Write same idempotency_key twice → record count must equal 1

Extraction failure state test:
  Simulate extraction failure after durable write
  record.lifecycle_state must equal ACTIVE
  record.pipeline_status must equal PARTIAL_ENRICHMENT_FAILED
  Record must appear in search results

Contradiction detection test:
  Write two conflicting facts with overlapping validity intervals
  CONTRADICTION_DETECTED event must be emitted
  Only one fact must be ACTIVE after resolution

Graph-disabled degradation test:
  Deploy with graph capability disabled
  GraphPort.is_available() must return False
  GET /v1/graph/** must return 503
  Write, search, recall, fact queries must all succeed normally

Cross-tenant isolation test (mandatory CI gate):
  Write records for tenant A
  Recall with tenant B credentials
  Result must be NO_MATCH; zero tenant A records must appear in response
```

---

## 12. Open Design Decisions for Future ADRs

ADR-007 through ADR-012 are decided and fully reflected in this specification. The following are newly surfaced open decisions for post-v1 consideration:

1. **LLM-arbitrated contradiction resolution** (ADR-011, post-v1): Cost and latency profile must be characterized in production before enabling as a default option.
2. **Learned/adaptive query planner** (ADR-010, post-v1): Requires production query distribution data before a workload-adaptive plan selector can be trained.
3. **Customer-managed encryption keys (CMEK):** Requires schema-per-tenant or database-per-tenant; candidate for a regulated-tier offering.
4. **Geometric memory shadowing:** Continuous phase rotation for fact deprioritization (2026 research direction). Track for post-v1 retention policy enhancement.
5. **Stream-processing scheduler:** Kafka/Flink-based event-driven consolidation at high write throughput. Revisit if the cron+threshold model proves insufficient at scale.

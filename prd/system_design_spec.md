# System Design Specification
## Framework-Agnostic AI Memory Layer

**Version:** v1.0  
**Status:** Draft  
**Companion documents:** PRD v1.2, ADR Companion  
**Owner:** Architecture

---

## 1. Introduction

This document translates the PRD into a concrete design: modules, ports, domain schema, storage schema, API contracts, data flows, adapter contracts, and benchmark harness mapping.

---

## 2. Module Map

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        INBOUND ADAPTERS                                 │
│  Python SDK │ TypeScript SDK │ REST API │ CLI │ MCP Server │ Framework  │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ inbound ports
┌───────────────────────────────▼─────────────────────────────────────────┐
│                         DOMAIN CORE                                     │
│  MemoryRecord · Episode · Fact · Procedure · IdentityProfile            │
│  Relationship · Scope · LifecycleState · TemporalTruth                  │
│  Use cases: Write · Search · Recall · Explain · Lifecycle               │
└───────┬───────────────┬───────────────┬───────────────┬─────────────────┘
        │               │               │               │ outbound ports
        ▼               ▼               ▼               ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐ ┌───────────────────┐
│ MEMORY ENGINE │ │ POLICY LAYER  │ │ STORAGE       │ │ OBSERVABILITY     │
│ Ingestion     │ │ Reinforcement │ │ PROVIDERS     │ │ & AUDIT           │
│ Extraction    │ │ Consolidation │ │ Relational    │ │ Trace emitter     │
│ Indexing      │ │ Decay         │ │ Vector        │ │ Metric emitter    │
│ Retrieval     │ │ Retention     │ │ Full-text     │ │ Audit log         │
│ Ranking       │ │ Deletion      │ │ Temporal      │ │ Lineage store     │
│ Recall pkg    │ │ Archival      │ │ Graph (opt.)  │ │                   │
└───────────────┘ └───────────────┘ └───────────────┘ └───────────────────┘
```

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

class FactRepository:
    def save(self, fact: Fact) -> FactId: ...
    def find_current(self, entity_id: EntityId, attribute: str, scope: Scope) -> Optional[Fact]: ...
    def find_at(self, entity_id: EntityId, attribute: str, at: datetime, scope: Scope) -> Optional[Fact]: ...
    def supersede(self, fact_id: FactId, superseded_by: FactId) -> None: ...

class VectorIndexPort:
    def upsert(self, id: MemoryId, embedding: Vector, metadata: Dict) -> None: ...
    def search(self, query_embedding: Vector, k: int, scope_filter: ScopeFilter) -> List[ScoredId]: ...
    def delete(self, id: MemoryId) -> None: ...

class FullTextIndexPort:
    def index(self, id: MemoryId, text: str, metadata: Dict) -> None: ...
    def search(self, query: str, k: int, scope_filter: ScopeFilter) -> List[ScoredId]: ...

class TemporalIndexPort:
    def index_fact(self, fact: Fact) -> None: ...
    def query_current(self, entity_id: EntityId, scope: Scope) -> List[Fact]: ...
    def query_at(self, entity_id: EntityId, at: datetime, scope: Scope) -> List[Fact]: ...

class GraphPort:
    def upsert_entity(self, entity: Entity) -> None: ...
    def upsert_relationship(self, rel: Relationship) -> None: ...
    def traverse(self, start: EntityId, depth: int, scope: Scope) -> SubGraph: ...

class EmbeddingPort:
    def embed(self, texts: List[str]) -> List[Vector]: ...

class ExtractionPort:
    def extract(self, raw: str, context: ExtractionContext) -> ExtractionResult: ...

class ObserverPort:
    def emit(self, event: MemoryEvent) -> None: ...

class AuditLogPort:
    def record(self, entry: AuditEntry) -> None: ...
    def query(self, scope: Scope, filters: AuditFilter) -> List[AuditEntry]: ...
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
    raw_payload: str
    payload_type: PayloadType
    sector: MemorySector
    lifecycle_state: LifecycleState
    recorded_at: datetime
    idempotency_key: Optional[str]
    pipeline_status: PipelineStatus
    metadata: Dict[str, Any]
```

### 4.2 Fact

```python
@dataclass(frozen=True)
class Fact:
    id: FactId
    memory_record_id: MemoryId
    tenant_id: TenantId
    scope: Scope
    subject_entity_id: EntityId
    predicate: str
    object_value: str
    effective_from: datetime
    effective_to: Optional[datetime]
    recorded_at: datetime
    supersedes: Optional[FactId]
    confidence: float
    sector: MemorySector
```

### 4.3 Scope

```python
@dataclass(frozen=True)
class Scope:
    tenant_id: TenantId
    workspace_id: Optional[WorkspaceId]
    principal_type: PrincipalType
    principal_id: PrincipalId
    session_id: Optional[SessionId]
    run_id: Optional[RunId]
```

### 4.4 WriteRequest / WriteResult

```python
@dataclass
class WriteRequest:
    tenant_id: TenantId
    scope: Scope
    raw_payload: str
    payload_type: PayloadType
    sector: Optional[MemorySector]
    idempotency_key: Optional[str]
    extract: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class WriteResult:
    memory_id: MemoryId
    scope: Scope
    pipeline_status: PipelineStatus
    accepted_at: datetime
    idempotent: bool
```

### 4.5 SearchRequest / SearchResult

```python
@dataclass
class SearchRequest:
    tenant_id: TenantId
    scope: Scope
    query: str
    mode: SearchMode
    sectors: Optional[List[MemorySector]]
    lifecycle_states: List[LifecycleState] = field(default_factory=lambda: [LifecycleState.ACTIVE])
    temporal_filter: Optional[TemporalFilter] = None
    k: int = 10

@dataclass
class SearchResult:
    items: List[SearchResultItem]
    query: str
    mode: SearchMode
    total_matched: int

@dataclass
class SearchResultItem:
    memory_id: MemoryId
    score: float
    raw_payload: str
    sector: MemorySector
    lifecycle_state: LifecycleState
    recorded_at: datetime
    signals: Dict[str, float]
    explanation: str
```

### 4.6 RecallRequest / RecallResult

```python
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

@dataclass
class RecallResult:
    items: List[RecallItem]
    total_tokens_estimate: int
    recall_strategy: str
    recalled_at: datetime

@dataclass
class RecallItem:
    memory_id: MemoryId
    content: str
    sector: MemorySector
    effective_from: Optional[datetime]
    signals: Dict[str, float]
    explanation: str
    trace_id: TraceId
```

---

## 5. Storage Schema

### 5.1 Relational Schema (PostgreSQL / SQLite)

```sql
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
    lifecycle_state TEXT NOT NULL DEFAULT 'ACTIVE',
    pipeline_status TEXT NOT NULL DEFAULT 'PENDING',
    idempotency_key TEXT,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata        JSONB,
    CONSTRAINT uq_idempotency UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX idx_memory_tenant_scope ON memory_records (tenant_id, principal_id, session_id);
CREATE INDEX idx_memory_sector ON memory_records (sector, lifecycle_state);
CREATE INDEX idx_memory_recorded_at ON memory_records (recorded_at DESC);

CREATE TABLE facts (
    id              UUID PRIMARY KEY,
    memory_record_id UUID NOT NULL REFERENCES memory_records(id),
    tenant_id       UUID NOT NULL,
    principal_id    UUID NOT NULL,
    session_id      UUID,
    subject_entity_id UUID NOT NULL,
    predicate       TEXT NOT NULL,
    object_value    TEXT NOT NULL,
    effective_from  TIMESTAMPTZ NOT NULL,
    effective_to    TIMESTAMPTZ,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    supersedes      UUID REFERENCES facts(id),
    confidence      FLOAT NOT NULL DEFAULT 1.0,
    sector          TEXT NOT NULL
);

CREATE INDEX idx_facts_entity_current ON facts (subject_entity_id, predicate, effective_to)
    WHERE effective_to IS NULL;
CREATE INDEX idx_facts_entity_temporal ON facts (subject_entity_id, effective_from, effective_to);

CREATE TABLE entities (
    id          UUID PRIMARY KEY,
    tenant_id   UUID NOT NULL,
    name        TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata    JSONB
);

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
```

### 5.2 Vector Collection Schema

```python
VectorDocument = {
    "id": str,
    "embedding": List[float],
    "metadata": {
        "tenant_id": str,
        "principal_id": str,
        "session_id": str,
        "sector": str,
        "lifecycle_state": str,
        "recorded_at": str,
        "payload_type": str
    }
}
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

-- PostgreSQL
ALTER TABLE memory_records ADD COLUMN fts_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('english', raw_payload)) STORED;
CREATE INDEX idx_memory_fts ON memory_records USING GIN(fts_vector);
```

### 5.4 Optional Graph Schema (Cypher)

```cypher
(:Entity { id: UUID, tenant_id: UUID, name: String, entity_type: String })
[:RELATES_TO { id: UUID, predicate: String, effective_from: DateTime, effective_to: DateTime, confidence: Float }]
(:MemoryNode { id: UUID, sector: String, recorded_at: DateTime })
(:Entity)-[:MENTIONED_IN]->(:MemoryNode)
(:MemoryNode)-[:SUPERSEDES]->(:MemoryNode)
```

---

## 6. API Contracts

### 6.1 REST Endpoints

```
POST   /v1/memories
GET    /v1/memories/{id}
POST   /v1/memories/search
POST   /v1/memories/recall
GET    /v1/memories/{id}/explain
PATCH  /v1/memories/{id}/lifecycle
DELETE /v1/memories/{id}

POST   /v1/facts
GET    /v1/facts/{entity_id}/current
GET    /v1/facts/{entity_id}/at

POST   /v1/lifecycle/consolidate
POST   /v1/lifecycle/decay
POST   /v1/lifecycle/archive

GET    /v1/audit
POST   /v1/migrations/import
GET    /v1/migrations/status/{job_id}
GET    /v1/health
GET    /v1/metrics
```

### 6.2 MCP Server Tools

```
memory_write
memory_search
memory_recall
memory_explain
fact_upsert
fact_current
fact_at
lifecycle_reinforce
lifecycle_decay
audit_query
```

---

## 7. Data Flow Diagrams

### 7.1 Write Path

```
Adapter → WriteMemoryUseCase
  1. Validate scope and tenant isolation
  2. Check idempotency key
  3. Assign MemoryRecord (PROPOSED)
  4. Persist raw record → MemoryRecordRepository
  5. Emit WRITE_ACCEPTED → ObserverPort
  6. Return WriteResult (pipeline_status: PENDING)
  [ASYNC] ExtractionPipeline
  7. Embed → VectorIndexPort
  8. Index full-text → FullTextIndexPort
  9. Extract facts/entities → ExtractionPort
  10. Persist facts → FactRepository
  11. Index temporal → TemporalIndexPort
  12. [graph enabled] upsert entities → GraphPort
  13. Update state to ACTIVE, pipeline_status ENRICHED
  14. Emit WRITE_ENRICHED → ObserverPort
```

### 7.2 Recall Path

```
Adapter → RecallMemoryUseCase
  1. Validate scope and tenant isolation
  2. Embed query → EmbeddingPort
  3. Search vector index → VectorIndexPort
  4. [HYBRID] Search full-text → FullTextIndexPort
  5. [temporal filter] Query → TemporalIndexPort
  6. Merge and composite-score candidates
  7. Apply scope and lifecycle filters
  8. Rank and select within token budget
  9. Hydrate payloads → MemoryRecordRepository
  10. Build explanation metadata
  11. Package RecallResult
  12. Emit RECALL → ObserverPort
  13. Record audit → AuditLogPort
```

### 7.3 Consolidation Path

```
PolicyScheduler → LifecycleUseCase.consolidate
  1. Load active memories matching policy
  2. Group by entity/topic/time window
  3. Derive consolidated representation
  4. Write consolidated MemoryRecord (ACTIVE)
  5. Mark sources as CONSOLIDATED
  6. Record lineage links
  7. Update fact supersession chains
  8. Emit CONSOLIDATION_COMPLETE → ObserverPort
  9. Record audit → AuditLogPort
```

---

## 8. Adapter Contracts

```python
class MemoryLayerAdapter(Protocol):
    def write(self, request: WriteRequest) -> WriteResult: ...
    def search(self, request: SearchRequest) -> SearchResult: ...
    def recall(self, request: RecallRequest) -> RecallResult: ...
    def explain(self, trace_id: TraceId) -> ExplainResult: ...
    def lifecycle(self, operation: LifecycleOperation, memory_id: MemoryId, scope: Scope) -> LifecycleResult: ...
```

### LangGraph Adapter Pattern

```python
write_node = WriteNode(client=memory_client)
recall_node = RecallNode(client=memory_client)

builder = StateGraph(AgentState)
builder.add_node("recall_memory", recall_node)
builder.add_node("write_memory", write_node)

# recall_node reads: state["query"], state["scope"]
# recall_node writes: state["recalled_context"]
# write_node reads: state["output"], state["scope"]
# write_node writes: state["memory_write_result"]
```

---

## 9. Observability Event Catalog

| Event | Trigger | Key Fields |
|---|---|---|
| WRITE_ACCEPTED | Raw record persisted | memory_id, scope, pipeline_status, latency_ms |
| WRITE_ENRICHED | Extraction complete | memory_id, facts_extracted, entities_extracted |
| WRITE_ENRICHMENT_FAILED | Extraction error | memory_id, error_code |
| SEARCH_COMPLETED | Search returned | query, mode, k, items_returned, latency_ms |
| RECALL_COMPLETED | Recall returned | query, items_returned, total_tokens, strategy |
| RECALL_NO_MATCH | Recall empty | query, scope, reason |
| CONSOLIDATION_COMPLETE | Consolidation run | scope, records_consolidated, policy_id |
| DECAY_COMPLETE | Decay run | scope, records_decayed, policy_id |
| DELETE_WITH_TRACE | Deletion | memory_id, actor, reason |
| CONTRADICTION_DETECTED | Conflicting facts | fact_ids, entity_id, predicate |
| TENANT_ISOLATION_VIOLATION | Cross-tenant attempt | actor, requested_tenant_id |
| MIGRATION_STARTED | Import job | source_format, job_id |
| MIGRATION_COMPLETED | Import complete | job_id, records_imported |
| MIGRATION_FAILED | Import error | job_id, records_imported_before_failure |

---

## 10. Benchmark Harness Mapping

### LongMemEval
- Ingest 500 conversation turns via WriteRequest (CONVERSATION_TURN, EPISODIC)
- Issue recall requests from benchmark question set
- Evaluate RecallResult items against gold answers
- Metrics: Recall@1, Recall@5, temporal_correctness_rate

### LoCoMo
- Ingest dialogue pairs via WriteRequest
- Issue SearchRequest (HYBRID) and RecallRequest per question
- Evaluate top-1 match against gold label
- Metrics: Recall@1, temporal_correctness_rate, token_budget_adherence

### Internal Suite
- Temporal truth: supersede F1 with F2; point-in-time recall must return correct fact.
- Scope isolation: writes under scope A must not appear in scope B recall.
- Idempotency: same key twice must yield one record.
- Contradiction detection: overlapping validity facts must emit CONTRADICTION_DETECTED.
- Failure recovery: extraction failure must not lose raw record.

---

## 11. Open Decisions for ADR Expansion

1. ADR-007: Embedding model selection and dimension
2. ADR-008: Consistency model (local vs service)
3. ADR-009: Consolidation scheduler strategy
4. ADR-010: Query planner design
5. ADR-011: Contradiction resolution policy
6. ADR-012: Multi-tenancy isolation level

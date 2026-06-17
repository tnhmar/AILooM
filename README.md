# AILooM

**A framework-agnostic AI memory layer for production agentic systems.**

[![CI](https://github.com/tnhmar/AILooM/actions/workflows/ci.yml/badge.svg)](https://github.com/tnhmar/AILooM/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![mypy: strict](https://img.shields.io/badge/mypy-strict-blue)](pyproject.toml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

---

`AILooM` gives agents, assistants, and AI applications **persistent, explainable, auditable memory** without coupling to any specific framework. Plug it into LangGraph, call it from a raw OpenAI loop, deploy it as an MCP server, or embed it locally — the domain contracts never change.

```python
from memory_layer import MemoryClient, WriteRequest, RecallRequest

client = MemoryClient(backend="local")

# Write — acknowledged after durable persist; enrichment is async
result = client.write(WriteRequest(
    scope=scope,
    raw_payload="User prefers dark mode in all applications.",
    payload_type=PayloadType.CONVERSATION_TURN,
    idempotency_key="session-42-turn-7",
))

# Recall — hybrid semantic + keyword + entity + temporal fusion
context = client.recall(RecallRequest(scope=scope, query="user display preferences"))
if context.status == RecallStatus.NO_MATCH:
    print(context.no_match_reason)
```

---

## Why AILooM?

Existing memory systems trade off portability, temporal accuracy, privacy, or operational simplicity. `AILooM` unifies the core concerns:

| Concern | What AILooM provides |
|---|---|
| **Portability** | Pure ports-and-adapters architecture — no framework dependency in domain core |
| **Temporal truth** | Explicit `effective_from` / `effective_to` on every fact; point-in-time queries |
| **Search quality** | Hybrid retrieval with RRF fusion across semantic, keyword, entity, and temporal signals |
| **Explainability** | Every recalled item carries per-signal scores, a `MemoryTrace`, and a full `QueryPlan` |
| **Governance** | Tenant isolation, audit log, retention and decay policies, redaction workflows |
| **Local-first** | SQLite + embedded vector index; no cloud dependency required |

---

## Architecture

`AILooM` follows **hexagonal (ports-and-adapters) architecture**. Domain core has zero external dependencies. Every integration — storage, embedding model, extraction service, framework adapter — is swappable via a typed `Protocol`.

```
┌─────────────────────────────────────────────────────────────────┐
│                      INBOUND ADAPTERS                           │
│  Python SDK · REST API · MCP Server · LangGraph · CLI           │
└──────────────────────────┬──────────────────────────────────────┘
                           │ inbound ports (Protocol)
┌──────────────────────────▼──────────────────────────────────────┐
│                       DOMAIN CORE                               │
│  MemoryRecord · Fact · Scope · MemoryTrace · AuditEntry         │
│  Use cases: Write · Search · Recall · Explain · Lifecycle       │
└───────┬──────────────┬──────────────┬──────────────┬────────────┘
        │              │              │              │ outbound ports
        ▼              ▼              ▼              ▼
 ┌─────────────┐ ┌───────────┐ ┌──────────┐ ┌──────────────────┐
 │   MEMORY    │ │  POLICY   │ │ STORAGE  │ │ OBSERVABILITY    │
 │   ENGINE    │ │  LAYER    │ │PROVIDERS │ │ & AUDIT          │
 │  Ingestion  │ │Consolidate│ │SQLite /  │ │ OpenTelemetry    │
 │  Extraction │ │  Decay    │ │Postgres  │ │ Langfuse traces  │
 │  QueryPlan  │ │  Retain   │ │pgvector  │ │ Audit log        │
 │  RRF fusion │ │Scheduler  │ │Neo4j/opt │ │                  │
 └─────────────┘ └───────────┘ └──────────┘ └──────────────────┘
```

**Module responsibilities:**

- **Domain Core** — all domain objects, invariants, use-case interfaces, and value types. Zero dependencies on external frameworks or storage.
- **Memory Engine** — ingestion pipeline, LLM-backed extraction, contradiction detection, hybrid indexing, `QueryPlanner`, RRF fusion, and recall packaging.
- **Policy Layer** — consolidation, decay, retention, deletion, and archival. Operates as a scheduled or event-driven component calling core use cases.
- **Storage Providers** — implements all outbound repository ports. Swappable via configuration.
- **Observability & Audit** — typed `MemoryEvent` emission, OpenTelemetry, Langfuse-compatible trace correlation, and append-only audit log.
- **Inbound Adapters** — translation only; no business logic.

---

## Core Concepts

### Memory Sectors

Every `MemoryRecord` belongs to one of six sectors:

| Sector | What it holds |
|---|---|
| `EPISODIC` | Events and interaction history |
| `SEMANTIC` | Durable facts and preferences |
| `PROCEDURAL` | Reusable instructions and learned workflows |
| `IDENTITY` | Stable profile attributes of a principal |
| `RELATIONAL` | Links among people, agents, entities, and resources |
| `REFLECTIVE` | Synthesized insights derived from lower-level memory |

### Lifecycle States

```
PROPOSED → ACTIVE → CONSOLIDATED
                  ↘ DECAYED → ARCHIVED → DELETED (with trace)
```

**Critical invariant:** `lifecycle_state` is set to `ACTIVE` on durable write and is never changed by enrichment. Records are always searchable immediately after a write is acknowledged. `pipeline_status` (`PENDING` → `ENRICHED` / `PARTIAL_ENRICHMENT_FAILED`) is the sole field reflecting enrichment progress.

### Temporal Truth

Facts carry explicit validity intervals:

```python
@dataclass(frozen=True)
class Fact:
    subject_entity_id: EntityId
    predicate: str
    predicate_group: str        # normalized group, e.g. "preference"
    object_value: str
    effective_from: datetime
    effective_to: Optional[datetime]   # None = currently valid
    supersedes: Optional[FactId]
    confidence: float
```

The system supports current-truth queries, historical point-in-time queries, contradiction detection, and supersession chains.

### Scope Hierarchy

Every memory belongs to an explicit scope: `tenant → workspace → principal (user or agent) → session → run`. Tenant isolation is mandatory and enforced at every layer — including as a CI gate.

---

## Write Contract

- A write is acknowledged **only after the raw record is durably persisted**.
- Extraction and enrichment complete **asynchronously** (or synchronously with `wait_for_enrichment=True`).
- Duplicate `idempotency_key` values never create duplicate records.
- If extraction fails after a durable write, the record remains `ACTIVE` with `pipeline_status = PARTIAL_ENRICHMENT_FAILED` — no data loss.

## Recall Contract

Recall is **context hydration**. It returns a compact, token-budgeted package optimized for prompt or agent-state injection. Every item includes per-signal scores and an explanation. When nothing matches, `RecallStatus.NO_MATCH` is returned with a `no_match_reason` — never a silent empty success.

---

## Getting Started

### Requirements

- Python 3.11+

### Install

```bash
pip install -e .
# or with dev tooling
pip install -e ".[dev]"
```

### Run tests

```bash
pytest
```

### Lint and type-check

```bash
ruff check src tests
mypy src
```

---

## Optional Extras

| Extra | Installs |
|---|---|
| `.[postgres]` | `asyncpg`, `psycopg[binary]` for PostgreSQL backend |
| `.[langgraph]` | `langgraph` adapter support |
| `.[mcp]` | MCP server adapter |
| `.[graph]` | `neo4j`, `kuzu` for optional graph capability |
| `.[dev]` | `pytest`, `ruff`, `mypy`, `pytest-asyncio`, `httpx` |

---

## REST API (service mode)

```
POST   /v1/memories                  Write a memory
POST   /v1/memories/search           Search memories
POST   /v1/memories/recall           Recall for context hydration
GET    /v1/memories/{id}/trace       Full MemoryTrace with lineage
PATCH  /v1/memories/{id}/lifecycle   Update lifecycle state
DELETE /v1/memories/{id}             Delete with trace

GET    /v1/facts/{entity_id}/current Current facts for entity
GET    /v1/facts/{entity_id}/at      Point-in-time facts (?at=ISO8601)

POST   /v1/lifecycle/consolidate     Trigger consolidation policy
POST   /v1/lifecycle/decay           Trigger decay policy

GET    /v1/audit                     Query audit log
GET    /v1/capabilities              Enabled capabilities (incl. graph)
GET    /v1/health                    Health check
GET    /v1/metrics                   Prometheus-compatible metrics
```

---

## MCP Tools

```
memory_write      memory_search     memory_recall
memory_explain    fact_upsert       fact_current
fact_at           lifecycle_decay   audit_query
capabilities_get
```

---

## Observability

All operations emit structured `MemoryEvent` objects via the `ObserverPort`:

- **OpenTelemetry** compatible — attach to any OTLP-compatible collector.
- **Langfuse** trace correlation — every event carries `trace_id` and `correlation_id`.
- **Prometheus** metrics endpoint — ingestion latency, recall latency, extraction backlog, error rate.

Key events include `MEMORY_WRITTEN`, `MEMORY_ENRICHED`, `CONTRADICTION_DETECTED`, `MEMORY_RECALLED`, `RECALL_NO_MATCH`, `CONSOLIDATION_JOB_COMPLETED`, and `TENANT_ISOLATION_VIOLATION`.

---

## Benchmarks

`AILooM` ships with a benchmark harness mapped to:

- **LongMemEval** — long-term recall quality (Recall@1, Recall@5, temporal correctness)
- **LoCoMo** — long-horizon conversational memory (Recall@1, token budget adherence)
- **Internal suite** — temporal truth, no-match contract, scope isolation, idempotency, extraction failure state, contradiction detection, and cross-tenant isolation (mandatory CI gate)

---

## Performance Targets (v1)

| Mode | Operation | p95 target |
|---|---|---|
| Local | Write acknowledge | < 75 ms |
| Local | Recall | < 200 ms |
| Local | Hybrid search | < 250 ms |
| Service | Write acknowledge | < 150 ms |
| Service | Recall | < 300 ms |
| Service | Hybrid search | < 350 ms |

Service mode targets 1,000+ tenants, 10M+ active records, 100+ concurrent recall requests per instance.

---

## Roadmap

**v1 (current focus)**
- Durable raw writes with hybrid enrichment pipeline
- Semantic + keyword + entity + temporal hybrid retrieval with RRF fusion
- Temporal fact management with contradiction detection
- Consolidation, decay, and retention policy engine
- SQLite + embedded vector index (local) / PostgreSQL + pgvector (service)
- Python SDK, REST API, MCP server, LangGraph adapter
- OpenTelemetry observability and append-only audit log
- Mem0-style JSONL import

**Post-v1**
- Optional graph provider packs (Neo4j / Kuzu)
- LLM-arbitrated contradiction resolution
- Additional framework adapters (LangChain, AutoGen, CrewAI)
- Reflective memory pipeline
- Federated / multi-region deployments

---

## Contributing

See [CONTRIBUTING.md](docs/CONTRIBUTING.md) _(forthcoming)_. All PRs must pass `ruff`, `mypy --strict`, and the full test suite including the cross-tenant isolation CI gate.

---

## License

[Apache 2.0](LICENSE)

---

## Related Documents

- [Product Requirements Document](prd/framework_agnostic_ai_memory_layer_prd_final.md)
- [System Design Specification](prd/system_design_spec.md)
- [Architecture Decision Records](prd/adr_companion.md)

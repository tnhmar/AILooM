# ADR Companion — Framework-Agnostic AI Memory Layer

**Version:** v1.1 — ADR-007–012 added 2026-06-17

---

## ADR-001 Architecture Style
**Decision:** Use ports-and-adapters (hexagonal) architecture.  
**Rationale:** Keep core memory logic independent of frameworks, transports, and storage products.  
**Consequence:** All framework integrations are adapters; core modules expose stable ports.

## ADR-002 Temporal Model
**Decision:** Use append-only temporal truth with explicit validity intervals.  
**Rationale:** Preserve current truth, historical truth, and point-in-time reasoning without destructive overwrite.  
**Consequence:** Facts are superseded, not updated in place.

## ADR-003 Graph Capability
**Decision:** Graph support is optional but first-class.  
**Rationale:** Some deployments need entity and relationship traversal, but lightweight local deployments should not require a graph backend.  
**Consequence:** Graph APIs are capability-gated; core domain stays graph-neutral.

## ADR-004 Write Model
**Decision:** Use hybrid writes: synchronous durable raw ingest plus asynchronous enrichment.  
**Rationale:** Preserve low-latency writes while allowing richer extraction, linking, and policy jobs.  
**Consequence:** Pipeline status becomes part of the write contract.

## ADR-005 Extraction Model
**Decision:** Ship a default pluggable extraction pipeline that can be disabled or replaced.  
**Rationale:** Strong developer experience requires a working default, but adopters need freedom to substitute models and prompts.  
**Consequence:** Raw-only usage remains valid; extraction is a capability, not a mandatory dependency.

## ADR-006 Canonical Local Backend
**Decision:** Use SQLite plus a local vector index as the canonical v1 local backend.  
**Rationale:** Minimize setup friction while keeping portability high.  
**Consequence:** Service deployments can swap in Postgres/pgvector and optional graph providers.

---

## ADR-007 Embedding Model Selection

**Status:** Decided  
**Date:** 2026-06-17

**Context:**  
The system requires text embeddings for semantic search and recall across all memory sectors. The choice of model affects retrieval quality, latency, portability, and operational complexity. The core must remain model-agnostic while providing sensible defaults for both local and service deployment modes.

**Decision:**  
Embedding model selection is fully pluggable via `EmbeddingPort`. Two standard default profiles are defined:

- **Local mode default:** `nomic-embed-text-v1.5` (768 dimensions) — runs entirely on-device, no external API calls, strong retrieval quality at low resource cost.
- **Service mode default:** `text-embedding-3-large` (3072 dimensions, or 1536 with truncation) — OpenAI-managed, predictable ops, strong multilingual and semantic coverage.

Every embedding vector is stored alongside `embedding_model_id` and `embedding_dimensions` in vector metadata. Vector collections are model-scoped: one collection per `(tenant_id, embedding_model_id)` pair. Naming convention: `memory_vectors_{tenant_id_short}_{model_id_slug}`.

Benchmark runs must record `embedding_model_id` and `embedding_dimensions` in run metadata to ensure reproducibility.

**Rationale:**  
Hardcoding a model creates lock-in and breaks local-first optionality. Storing model identity alongside every vector allows safe model migration and multi-model coexistence. Nomic-embed-text-v1.5 has demonstrated strong retrieval benchmarks at a footprint suitable for local operation. text-embedding-3-large provides the best managed-service tradeoff for production workloads.

**Consequence:**  
- `EmbeddingPort` must declare `model_id` and `dimensions` as typed properties.
- `VectorDocument` metadata includes `embedding_model_id` and `embedding_dimensions`.
- Model migration requires a re-embedding job; this is documented in the Migration Specification.
- New embedding models can be added by implementing `EmbeddingPort` — no core changes required.

---

## ADR-008 Consistency Model

**Status:** Decided  
**Date:** 2026-06-17

**Context:**  
The hybrid write model (ADR-004) separates durable raw ingest from asynchronous enrichment. This creates a spectrum of consistency options for readers. Blanket strong consistency is prohibitively expensive for vector indexes; blanket eventual consistency degrades agent UX by making just-written memories temporarily invisible.

**Decision:**  
A tiered consistency model is adopted:

| Tier | Guarantee | Scope |
|---|---|---|
| Durable write ack | Record is persisted and retrievable by ID immediately after `WriteResult` is returned | Always |
| Session read-your-writes | Within the same session, a recalled memory written earlier in the session is always visible | Session scope |
| Bounded eventual (cross-session) | Cross-session vector search reflects writes within a bounded window (target: < 5 s p95) | Service mode |
| Strong audit reads | Audit log and `GET /v1/memories/{id}` always reflect the latest durable state | Always |

`WriteRequest` adds an optional `wait_for_enrichment: bool = False` field. When `True`, the write call blocks until `pipeline_status = ENRICHED` (or `PARTIAL_ENRICHMENT_FAILED`). This is intended for integration tests, benchmarks, and latency-tolerant workflows — not for hot write paths.

**Rationale:**  
Tiered consistency matches agent interaction patterns. Agents performing multi-turn reasoning within a session need read-your-writes. Cross-session and cross-agent consistency can tolerate bounded staleness without user-visible degradation. Strong consistency everywhere would require synchronous vector index writes, eliminating the latency benefit of ADR-004.

**Consequence:**  
- `WriteRequest.wait_for_enrichment` is a new API surface; default is `False` to preserve non-blocking behavior.
- `SearchResultItem` exposes `pipeline_status` so callers can reason about enrichment state of search results.
- `RecallItem` exposes `pipeline_status` for the same reason.
- Service-mode storage adapters must implement session-scoped read guarantees or a polling mechanism against `pipeline_status`. This is a non-functional requirement on `VectorIndexPort` and `FullTextIndexPort` implementations.
- In local SQLite mode, all consistency guarantees are trivially satisfied by single-process execution.

---

## ADR-009 Consolidation Scheduler Strategy

**Status:** Decided  
**Date:** 2026-06-17

**Context:**  
Consolidation must happen at meaningful boundaries (session end, memory volume thresholds) rather than only at arbitrary clock intervals, to ensure timely compression of episodic memory and accurate fact salience. Purely event-driven approaches are fragile under partial failures; purely cron-based approaches are insufficiently responsive.

**Decision:**  
A hybrid scheduler strategy is adopted with three trigger tiers:

| Tier | Trigger | Latency target |
|---|---|---|
| Fast | Session boundary (`SESSION_ENDED` event) | < 30 s |
| Medium | Write-count threshold per scope (configurable, default 500 records) | < 5 min |
| Slow | Cron safety-net loops (session: every 15 min, medium: every 1 h, decay/archive: nightly) | Best-effort |

The `ConsolidationScheduler` is a first-class protocol separate from the domain core. It calls `LifecycleUseCase` via the inbound port.

- **Local mode:** Lightweight in-process scheduler (e.g., APScheduler). No external dependencies.
- **Service mode:** Distributed scheduler with persisted job state (e.g., Celery Beat, Temporal). Per-tenant time-slice limits enforced to prevent noisy-neighbor consolidation storms.

Scheduler state (job registry, per-tenant limits) must be persisted to survive restarts.

**Rationale:**  
Event-driven triggers at session boundaries ensure low-latency consolidation for conversational agents. Threshold triggers prevent unbounded memory accumulation in high-write scenarios. Cron safety-nets catch events missed due to failures or restarts. The three-tier design gives timely, reliable, and cost-efficient consolidation without relying on any single mechanism.

**Consequence:**  
- `ConsolidationScheduler` protocol is defined in the system design (Section 3.3).
- `SESSION_ENDED` event is added to the observability event catalog (Section 9).
- `ConsolidationPolicy.trigger` supports `SESSION_END | THRESHOLD | SCHEDULE | ON_DEMAND`.
- `ConsolidationPolicy.threshold_record_count` configures the medium-tier trigger (default: 500).
- Local mode must not require an external message broker or scheduler service.

---

## ADR-010 Query Planner Design

**Status:** Decided  
**Date:** 2026-06-17

**Context:**  
Retrieval quality in 2026 requires hybrid signals (semantic, keyword, entity, temporal, recency). A single retrieval mode cannot serve all workloads — latency-sensitive pipelines, high-precision recall, temporal fact queries, and graph-traversal queries have different cost and quality profiles. A mode-driven planner avoids hardcoding retrieval logic while keeping the common path simple.

**Decision:**  
A mode-driven `QueryPlanner` protocol is adopted with the following modes:

| Mode | Indexes | Fusion | LLM Rerank |
|---|---|---|---|
| `SEMANTIC` | Vector only | — | No |
| `KEYWORD` | Full-text only | — | No |
| `HYBRID` (default) | Vector + full-text + entity | RRF | No |
| `HYBRID_TEMPORAL` | Vector + full-text + temporal | RRF | No |
| `QUALITY` | Vector + full-text + entity + temporal | RRF | Optional |
| `GRAPH` | Graph traversal (requires graph capability) | Structural | No |

Default fusion strategy is **Reciprocal Rank Fusion (RRF)**. All index queries within a plan execute in parallel. Per-signal weights are tenant-configurable and stored in `tenant_policies` (policy_type: `SEARCH_WEIGHTS`). The `QueryPlan` dataclass carries the full plan for observability — it is included in `SearchResult.query_plan` and `MemoryTrace.query_plan`.

**Rationale:**  
RRF is model-free, parameter-light, and empirically strong for multi-source fusion. Parallel index execution minimizes retrieval latency. Mode-based routing makes the common path (`HYBRID`) simple while preserving full power for `QUALITY` and `GRAPH` modes. Exposing `QueryPlan` in results makes retrieval fully explainable and debuggable.

**Consequence:**  
- `SearchMode` enum has six values: `SEMANTIC`, `KEYWORD`, `HYBRID`, `HYBRID_TEMPORAL`, `QUALITY`, `GRAPH`.
- `QueryPlanner` protocol and `QueryPlan` dataclass are defined in the system design (Section 3.4).
- `SearchResult` includes `query_plan` for observability.
- `GRAPH` mode returns `CAPABILITY_NOT_AVAILABLE` if graph provider is not configured.
- Tenant search weight configuration is stored in `tenant_policies` with `policy_type = SEARCH_WEIGHTS`.
- Post-v1: a learned/adaptive query planner can replace the mode-driven planner without changing the `QueryPlanner` protocol.

---

## ADR-011 Contradiction Resolution Policy

**Status:** Decided  
**Date:** 2026-06-17

**Context:**  
Agents accumulate facts about entities over time. New information frequently contradicts older facts (e.g., a user changes their preferred framework, location, or role). The system must resolve these contradictions in a way that keeps current truth accessible, preserves historical truth (ADR-002), and surfaces ambiguity when confidence is low.

**Decision:**  
**Auto-close with history preserved** is the default resolution mode. When a new fact conflicts with an existing active fact on the same `(entity_id, predicate_group)`:

1. The existing fact's `effective_to` is set to the new fact's `effective_from`.
2. The new fact is persisted as active with `supersedes` pointing to the old fact.
3. A `CONTRADICTION_DETECTED` event is emitted.

When the new fact's `confidence` is below `ConflictResolutionPolicy.low_confidence_threshold` (default: 0.6), the new fact is placed in `PROPOSED` state instead of `ACTIVE`, and a `CONTRADICTION_LOW_CONFIDENCE` event is emitted. Manual review or a future LLM-arbitration pass resolves it.

Configurable resolution modes per tenant:

| Mode | Behavior |
|---|---|
| `AUTO_CLOSE` (default) | New fact wins; old fact closed with `effective_to` |
| `FLAG_ONLY` | Both facts remain ACTIVE; event emitted for review |
| `MANUAL` | New fact goes to `PROPOSED`; requires explicit promotion |
| `LLM_ARBITRATED` | Post-v1 only |

**Rationale:**  
Auto-close preserves temporal truth (ADR-002) without manual intervention in the common case. The confidence threshold prevents low-quality extractions from silently overwriting established facts. Configurable modes allow regulated or high-stakes tenants to require human review. The `predicate_group` abstraction prevents false contradictions between semantically distinct predicates.

**Consequence:**  
- `Fact` schema adds `predicate_group: str` — a normalized grouping of semantically equivalent predicates (e.g., `"preference"` covers `"prefers"`, `"likes"`, `"switched_to"`).
- `ConflictResolutionPolicy` is defined in the system design (Section 4.11) and stored per tenant in `tenant_policies`.
- Extraction pipeline step 11 includes contradiction detection and resolution logic (Section 7.1).
- `CONTRADICTION_DETECTED` and `CONTRADICTION_LOW_CONFIDENCE` events are in the event catalog (Section 9).
- The dual use of `PROPOSED` (initial ingestion AND low-confidence hold) is valid; `pipeline_status` distinguishes the two cases.
- LLM-arbitrated resolution is deferred to post-v1 pending cost and latency characterization.

---

## ADR-012 Multi-Tenancy Isolation Level

**Status:** Decided  
**Date:** 2026-06-17

**Context:**  
The system must serve multiple tenants from a single deployment (SaaS service mode) while providing strong data isolation guarantees. The isolation strategy must balance security, operational simplicity, and cost. Three standard strategies exist: shared schema with RLS, schema-per-tenant, and database-per-tenant.

**Decision:**  
A tiered isolation model is adopted:

| Deployment tier | Relational isolation | Vector isolation | Graph isolation |
|---|---|---|---|
| Default (all tiers) | Shared schema + PostgreSQL RLS (`FORCE ROW LEVEL SECURITY`) | Dedicated collection per tenant per model | Tenant-filtered queries (mandatory `tenant_id` on all nodes and edges) |
| Enterprise option | Schema-per-tenant (separate `memory_{tenant_id}` schema) | Dedicated collection per tenant per model | Dedicated graph database or namespace per tenant |

`FORCE ROW LEVEL SECURITY` is mandatory in service mode. The application layer sets `app.tenant_id` via `SET LOCAL` at transaction start. All maintenance jobs (consolidation, decay, migration) must operate within tenant-scoped transactions to prevent cross-tenant data access.

**Rationale:**  
Shared schema + RLS is the best default tradeoff: strong security guarantees from the database engine, single schema to manage, and efficient resource utilization. Dedicated vector collections per tenant prevent cross-tenant embedding leakage without requiring separate databases. Schema-per-tenant is reserved for enterprise tier where regulatory or contractual isolation requirements exceed what RLS provides.

**Consequence:**  
- All relational tables include `tenant_id UUID NOT NULL` as a mandatory column.
- RLS policy stubs are included in the DDL (Section 5.1) for service mode activation.
- Vector collection naming convention is `memory_vectors_{tenant_id_short}_{model_id_slug}` (Section 5.2).
- All graph nodes and edges carry `tenant_id` as a mandatory property; all graph queries must include a `tenant_id` filter predicate (Section 5.4).
- A cross-tenant isolation test is a **mandatory CI gate** for all adapters (Section 8.4, rule 5).
- `TENANT_ISOLATION_VIOLATION` event is emitted on any detected cross-tenant access attempt (Section 9).
- Schema-per-tenant enterprise mode is post-v1; tracked as an open decision in Section 12.

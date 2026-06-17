# ADR Companion — Framework-Agnostic AI Memory Layer

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

## Open Decisions (ADR Expansion Pack — see System Design Spec Section 11)
- ADR-007: Embedding model selection
- ADR-008: Consistency model
- ADR-009: Consolidation scheduler strategy
- ADR-010: Query planner design
- ADR-011: Contradiction resolution policy
- ADR-012: Multi-tenancy isolation level

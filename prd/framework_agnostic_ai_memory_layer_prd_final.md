# Product Requirements Document
## Framework-Agnostic AI Memory Layer

### 1. Document Control
- Version: v1.2 final draft.
- Status: reviewed and consolidated.
- Audience: product, architecture, platform, and implementation teams.
- Related artifacts: competitive analysis, PRD outline, ADR companion, benchmark specification, migration notes.
- Document owner: product architecture.
- Review cadence: update at each major architecture decision or release milestone.

### 2. Product Summary
The product is a framework-agnostic AI memory layer for agents, assistants, and AI applications. It provides persistent memory primitives, temporal truth, search, recall, consolidation, decay, auditability, and portable integrations across runtimes.

The product is intentionally not an agent orchestration framework. Frameworks such as LangGraph, LangChain, AutoGen, and CrewAI are integration targets exposed through adapters rather than architectural dependencies.

### 3. Problem Statement
Existing memory systems optimize different slices of the problem space. Mem0 emphasizes extraction-centric memory and token-efficient temporal recall, Zep emphasizes temporal knowledge graphs, MemPalace emphasizes verbatim local-first memory, OpenMemory emphasizes multi-sector cognitive memory, and MemGPT/Letta emphasizes controller-style hierarchical memory management.

This fragmentation forces teams to trade off portability, temporal accuracy, privacy, explainability, storage strategy, and operational simplicity. A framework-agnostic memory layer should unify the core concerns while leaving orchestration, planning, and runtime state management outside the product boundary.

### 4. Goals
- Provide a portable memory core usable from any agent runtime.
- Support persistent memory across sessions, runs, and deployments.
- Represent temporal truth explicitly with point-in-time semantics.
- Support both verbatim evidence and structured memory.
- Provide explainable recall and auditable lifecycle operations.
- Support local-first, self-hosted, and service-based deployments.
- Expose stable core APIs with pluggable adapters and pluggable storage backends.

### 5. Non-Goals
- The product will not implement planning, tool orchestration, or workflow execution.
- The product will not depend on any specific agent framework.
- The product will not require a cloud service to function.
- The product will not be a benchmark-only research project.
- The product will not prescribe a single fixed ontology for every adopter; sectors are extensible.

### 6. Product Principles
- Core logic must be framework-agnostic.
- All external integrations must be implemented through adapters.
- Temporal truth is first-class.
- Memory must be explainable, auditable, and governable.
- The system must support both local-first and production-scale deployment modes.
- Product requirements, architecture decisions, and implementation details must be distinguished explicitly.
- Defaults should optimize developer adoption without constraining production deployment.

### 7. Target Users
#### 7.1 Primary Persona
Application engineers building long-lived agents that need memory without coupling to a specific runtime.

#### 7.2 Secondary Persona
Platform engineers who need a multi-tenant memory service with observability, access control, governance, and lifecycle policies.

#### 7.3 Tertiary Persona
Framework authors and researchers who need portable memory abstractions, reproducible benchmarks, and schema stability.

### 8. Product Boundary and Responsibility Model
The PRD defines product responsibilities, not detailed implementation design. The product boundary is divided into six responsibility areas:

- Domain Core: canonical concepts, invariants, and use-case contracts.
- Memory Engine: ingestion, indexing, recall, search, temporal reasoning, and trace generation.
- Policy Layer: reinforcement, consolidation, decay, retention, deletion, and archival.
- Adapters: runtime, transport, and framework integrations.
- Storage Providers: vector, relational, event, and graph persistence backends.
- Observability and Audit: traces, metrics, lineage, and compliance evidence.

The following architectural decisions are confirmed for v1 and maintained in the companion ADR document rather than duplicated here: ports-and-adapters architecture, append-only temporal model with explicit validity intervals, optional-but-first-class graph capability, hybrid write acknowledgment with asynchronous enrichment, default pluggable extraction pipeline, and SQLite plus a local vector index as the canonical local backend.

### 9. Domain Model
#### 9.1 Core Objects
- MemoryRecord: immutable stored unit containing raw evidence, metadata, scope, and lifecycle state.
- Episode: event-oriented memory representing a concrete interaction or occurrence.
- Fact: structured claim about an entity, relationship, or property with temporal validity.
- Procedure: reusable instruction, workflow, or learned method.
- IdentityProfile: durable description of an actor such as a user, agent, or organization.
- Relationship: typed connection between identities, entities, or facts.
- MemoryTrace: explanation and lineage for how memory was written, changed, or recalled.
- SearchRequest: open retrieval request optimized for discovery.
- SearchResult: ranked evidence-oriented result set.
- RecallRequest: context-hydration request optimized for downstream reasoning.
- RecallResult: compact result set prepared for prompt or state hydration.
- ConsolidationPolicy: rules controlling merging, summarizing, superseding, and retention.
- RetentionPolicy: rules controlling expiry, archival, deletion, and legal hold behavior.
- Tenant: top-level isolation boundary for multi-tenant operation.
- User: human principal.
- Agent: software principal acting within a scope.
- Session: bounded interaction thread.
- Run: specific execution instance inside a session or workflow.

#### 9.2 Memory Sectors
- Episodic memory: events and interaction history.
- Semantic memory: durable facts and preferences.
- Procedural memory: reusable instructions and learned workflows.
- Identity memory: stable profile attributes.
- Relational memory: links among actors, entities, and resources.
- Reflective memory: optional synthesized insights and higher-order observations.

#### 9.3 Ownership and Scope
Every memory belongs to an explicit scope hierarchy: tenant -> workspace or organization -> principal (user or agent) -> session -> run. A memory may be private, shared within scope, or promoted to a higher shared scope based on policy.

#### 9.4 Lifecycle States
- Proposed: accepted for ingestion but not yet enriched.
- Active: available for search and recall.
- Superseded: replaced by a newer valid fact while retained for historical truth.
- Decayed: still retained but deprioritized by policy.
- Consolidated: merged or abstracted into a more durable representation while lineage remains available.
- Archived: removed from active recall but retained for compliance or long-term storage.
- Deleted-with-trace: payload removed while deletion evidence remains.

#### 9.5 Temporal Truth Model
Facts carry at minimum:
- effective_from
- effective_to
- recorded_at
- supersedes (optional)

The system must support current truth queries, historical truth queries, point-in-time queries, contradiction detection, and supersession chains.

#### 9.6 Immutability Rule
MemoryRecord payloads are immutable after durable write. Derived structures such as facts, relationships, scores, or summaries may be appended, superseded, or invalidated, but the original evidence remains intact unless an explicit redaction or deletion workflow is executed.

### 10. Functional Requirements
Requirements are grouped by delivery tier.

#### 10.1 MVP Requirements
1. The system must durably accept raw writes of conversation turns, tool outputs, documents, and structured events.
2. The system must support tenant, user, agent, session, and run scoping.
3. The system must support semantic search over active memory.
4. The system must support recall that packages memory for downstream prompt or state hydration.
5. The system must preserve temporal truth for structured facts using explicit validity intervals.
6. The system must expose audit traces for writes and recalls.
7. The system must run in a local mode without external cloud dependencies.
8. The system must expose Python and TypeScript SDKs plus one service interface.

#### 10.2 V1 Requirements
1. When a search request includes keyword terms, the system must combine keyword and semantic signals in ranking for searchable scopes.
2. When extraction is enabled, the system must use recognized entities and preferences as additional retrieval signals during search and recall.
3. When multiple candidate memories are eligible for retrieval, the system must rank them using at minimum semantic relevance, scope match, and temporal relevance.
4. When raw inputs are accepted without structured payloads, the default extraction pipeline must derive facts, entities, and preferences unless that pipeline is explicitly disabled.
5. When lifecycle policies are scheduled or invoked, the system must support reinforce, consolidate, decay, archive, and delete-with-trace operations without breaking lineage.
6. When recall returns results, the system must include explanation metadata showing why each item matched and which signals contributed.
7. The system must expose at least one framework adapter and one MCP adapter without changing core domain contracts.
8. The system must import Mem0-style exports and generic JSONL while preserving scope, timestamps, and lineage where available.
9. The system must version schemas and provide documented upgrade migrations for supported prior versions.
10. The system must emit observability events compatible with OpenTelemetry and Langfuse-style trace correlation.

#### 10.3 Post-V1 Requirements
1. Optional graph persistence providers.
2. Multi-region or federated deployments.
3. Reflective memory pipelines.
4. Advanced policy plugins and learned consolidation strategies.
5. Additional importers such as Zep-style exports and other ecosystem formats.

### 11. Behavioral Contracts
#### 11.1 Write Contract
- A write request is acknowledged only after the raw record is durably persisted.
- Extraction and enrichment may complete asynchronously.
- The write response must include a stable memory identifier, scope, and current pipeline status.
- Duplicate idempotency keys must not create duplicate raw records.

#### 11.2 Search Contract
Search is evidence discovery. It returns ranked memory records or derived facts for an explicit query, scope filter, and mode. Search may return broad evidence and is not required to optimize for token budget.

#### 11.3 Recall Contract
Recall is context hydration. It returns a compact, downstream-consumable package optimized for prompting, reasoning, or agent state injection. Recall must support token or size limits and should prefer compact, high-value evidence while preserving traceability.

#### 11.4 Consolidation Contract
Consolidation is a policy-driven process that may merge, summarize, supersede, or reclassify derived memory while preserving raw evidence lineage. Consolidation must never delete evidence silently.

#### 11.5 Explain Contract
Every recalled item must include an explanation payload with contributing signals, source identifiers, scope path, and temporal context.

### 12. Failure Modes and Edge Cases
1. If raw persistence succeeds but extraction fails, the write remains committed with status partial_enrichment_failed.
2. If two contradictory facts arrive with overlapping validity intervals, the system must flag the contradiction and record both until policy resolves the conflict.
3. If recall returns zero results, the response must explicitly identify no_match rather than returning an empty success with no explanation.
4. If a decay or consolidation job fails mid-batch, the system must support retry without corrupting lineage.
5. If migration encounters an unsupported schema version, the system must fail the migration with diagnostics and leave existing data untouched.
6. If a tenant isolation rule is violated by an adapter request, the request must fail closed.
7. If graph capability is disabled, entity and relationship APIs must degrade gracefully rather than failing with missing-backend errors.

### 13. Storage and Indexing Requirements
#### 13.1 Required Logical Stores
- Relational/event store for raw records, scopes, policies, and lifecycle state.
- Vector index for semantic retrieval.
- Full-text index for keyword retrieval.
- Temporal index for validity interval queries.
- Optional graph store for entity and relationship traversal.

#### 13.2 Default Backend Strategy
- Local mode: SQLite plus embedded vector index.
- Service mode: Postgres/pgvector plus optional graph provider.
- Provider interfaces must allow replacement without changing domain contracts.

#### 13.3 Indexing Requirements
- Embedding index update must occur for every active MemoryRecord eligible for semantic retrieval.
- Keyword index update must occur for every searchable raw record.
- Temporal index update must occur for every structured fact.
- Graph index update must occur only when graph capability is enabled.

### 14. Interface Requirements
#### 14.1 First-Party Interfaces
- Python SDK.
- TypeScript SDK.
- REST API for service mode.
- CLI for local operations, migrations, and diagnostics.
- MCP server.

#### 14.2 Framework Adapters
- LangGraph adapter.
- LangChain adapter.
- AutoGen adapter.
- CrewAI adapter.
- Direct embedded application adapter.

#### 14.3 Adapter Rules
- Adapters must contain translation logic only.
- Adapters must not duplicate memory business logic.
- Core contracts must remain stable even if adapter APIs evolve.
- Framework-specific types must not leak into core domain objects.

### 15. Observability and Operations
- All writes, searches, recalls, consolidations, decays, deletes, and migrations must emit structured events.
- The system must expose metrics for ingestion latency, recall latency, extraction backlog, error rate, and queue depth.
- The system must support OpenTelemetry emission.
- The system must provide Langfuse-compatible trace correlation fields for memory operations.
- The system must store audit trails for every mutation, supersession, consolidation, and deletion.
- A debug mode must explain why a memory was or was not returned.

### 16. Security and Governance
#### 16.1 V1 Governance Requirements
- Tenant isolation is mandatory.
- Audit trail is mandatory.
- Retention and deletion policies are mandatory.
- Redaction workflows are mandatory.
- Encryption at rest is mandatory for service mode.
- Local mode may use file-level encryption or OS-level protection, but the product must document this boundary explicitly.

#### 16.2 Deployment Modes
- Local-only mode.
- Self-hosted service mode.
- Managed-cloud-compatible mode.

### 17. Performance and Scale Targets
These are provisional v1 targets and must be reviewed after reference implementation benchmarking.

#### 17.1 Ownership and Review Date
- Metric owner: platform architecture.
- First review date: after benchmark baseline freeze for v1.

#### 17.2 Local Mode Targets
- Write acknowledge latency: p95 under 75 ms for raw-only writes under nominal load.
- Recall latency: p95 under 200 ms for top-k recall on active scope under nominal load.
- Search latency: p95 under 250 ms for hybrid search on active scope under nominal load.

#### 17.3 Service Mode Targets
- Write acknowledge latency: p95 under 150 ms excluding asynchronous enrichment.
- Recall latency: p95 under 300 ms for scoped recall.
- Search latency: p95 under 350 ms for scoped hybrid search.

#### 17.4 Scale Targets
- Support at least 1,000 tenants in service mode.
- Support at least 10 million active MemoryRecords per deployment.
- Support at least 100 concurrent recall requests per service instance under target latency.

### 18. Quality Metrics and Benchmarks
#### 18.1 Memory Quality Metrics
- Recall@k.
- Temporal correctness rate.
- Contradiction detection rate.
- Explainability completeness rate.
- Consolidation lineage preservation rate.

#### 18.2 System Quality Metrics
- p50 and p95 latency by operation.
- Ingestion success rate.
- Extraction completion rate.
- Migration success rate.
- Adapter compatibility coverage.
- Storage overhead ratio for verbatim versus structured memory.

#### 18.3 Benchmark Strategy
- LongMemEval for long-term recall quality.
- LoCoMo for long-horizon conversational memory.
- Internal benchmark suite for scoped recall, temporal truth, and lifecycle operations.

### 19. Rollout Plan
#### 19.1 V1 Must-Have
- Durable raw writes.
- Explicit scoping model.
- Semantic search.
- Recall packaging.
- Temporal validity for facts.
- Audit traces.
- Local mode.
- Python and TypeScript SDKs.
- REST API.
- Default extraction pipeline.
- OpenTelemetry-compatible observability.

#### 19.2 V1 Should-Have
- Keyword search.
- Entity-aware retrieval.
- MCP server.
- One framework adapter.
- Mem0-style import.
- Consolidation and decay jobs.

#### 19.3 Post-V1
- Optional graph provider packs.
- Additional framework adapters.
- Federated deployment support.
- Reflective memory plugins.
- Rich admin UI.

#### 19.4 Backward Compatibility Policy
Schema changes must be versioned. Breaking changes require documented migration paths for one major version.

#### 19.5 Deprecation Policy
Deprecated APIs remain supported for one minor release cycle unless security or data-integrity risks require faster removal.

### 20. Risks and Mitigations
- Risk: ontology becomes too rigid. Mitigation: keep sector taxonomy extensible and capability-based.
- Risk: default extraction adds cost and latency. Mitigation: hybrid write path and pluggable extraction.
- Risk: verbatim retention increases storage volume. Mitigation: lifecycle policies, archival tiers, and bounded recall packaging.
- Risk: optional graph capability creates fragmentation. Mitigation: graph APIs are capability-gated and core contracts stay graph-neutral.
- Risk: adapter layer diverges by framework. Mitigation: strict adapter rules and contract tests.

### 21. Acceptance Criteria
1. Given a raw write through the Python SDK with a valid scope, the system must durably persist the record and return a stable identifier within the write latency target.
2. Given a structured fact superseding an older fact in the same scope, a current-truth recall must return the newer fact while a point-in-time recall before supersession must return the older fact.
3. Given a recall request with a size limit, the system must return a compact package that includes explanation metadata for every returned item.
4. Given duplicate writes using the same idempotency key, the system must store exactly one raw record.
5. Given an extraction failure after durable write, the system must preserve the raw record and expose pipeline failure state without data loss.
6. Given tenant A credentials, a request for tenant B memory must fail closed.
7. Given local mode installation, the system must support write, search, recall, and audit without requiring external cloud services.
8. Given a contract-tested framework adapter, integration with the adapter must require no change to core domain modules.
9. Given the benchmark harness, the published benchmark scenarios must be reproducible from documented configuration.

### 22. Glossary
- Active: lifecycle state in which a memory participates normally in search and recall.
- Archived: lifecycle state in which a memory is retained but removed from normal active recall.
- Consolidated: lifecycle state in which derived memory has been merged, summarized, or superseded while lineage remains intact.
- Decayed: lifecycle state in which a memory remains available but is ranked lower by policy.
- Deleted-with-trace: deletion mode in which payload is removed while audit evidence remains.
- Effective_from: timestamp at which a fact becomes valid in modeled reality.
- Effective_to: timestamp at which a fact stops being valid in modeled reality.
- Recorded_at: timestamp at which the system observed or stored the fact.
- Episodic memory: event-oriented memory about what happened.
- Semantic memory: durable fact-oriented memory about what is true.
- Procedural memory: reusable instructions or learned methods.
- Identity memory: stable profile information about a principal.
- Relational memory: links among people, agents, entities, and resources.
- Reflective memory: optional synthesized insights derived from lower-level memory.
- Search: broad evidence retrieval optimized for discovery.
- Recall: compact retrieval optimized for context hydration.
- Superseded: lifecycle state for a fact replaced by a newer valid fact while retained for historical truth.
- Tenant isolation: rule preventing cross-tenant visibility or mutation.

### 23. Appendices
- ADR companion: see `adr_companion.md`.
- System design specification: see `system_design_spec.md`.
- Benchmark references.
- Migration references.
- Adapter reference matrix.

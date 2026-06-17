# memory-layer

A production-grade, framework-agnostic AI memory layer for agentic systems.
Built with hexagonal (ports-and-adapters) architecture, `memory-layer` provides
multi-sector memory (episodic, semantic, procedural, identity), temporal
knowledge graphs, hybrid retrieval, lifecycle management (decay, consolidation),
and first-class integrations for LangGraph, MCP, FastAPI, and PostgreSQL/pgvector.

## Getting Started

```bash
# Install in editable mode with dev dependencies
pip install -e .[dev]

# Verify the package is importable
python -c "import memory_layer; print(memory_layer.__version__)"

# Run the full test suite
pytest

# Run with coverage
pytest --cov=src/memory_layer --cov-report=term-missing

# Run only unit tests (skip integration and benchmarks)
pytest -m "not integration and not benchmark"

# Start the development server (SQLite + ChromaDB local adapters)
uvicorn memory_layer.api.app:create_app --factory --reload
```

## Architecture

```
src/memory_layer/
├── domain/          # Types, records, policies, events
├── ports/           # Inbound + outbound abstract interfaces
├── engine/          # Ingestion, extraction, retrieval, recall
├── policy/          # Decay, consolidation, scheduler
├── storage/         # Local adapters: SQLite + ChromaDB
├── adapters/        # REST, SDK, MCP, LangGraph, Postgres, Qdrant
├── api/             # FastAPI app, schemas, health, middleware
├── sdk/             # Async Python client
├── mcp/             # MCP tool server
├── integrations/    # LangGraph nodes
├── config/          # Settings + loader
└── observability/   # Logging, tracing, metrics, audit
```

## Milestones

| Milestone | Scope |
|---|---|
| M0 | Scaffold & directory structure |
| M1 | Domain Core |
| M2 | Local Storage Adapters |
| M3 | Memory Engine Write + Search |
| M4 | Lifecycle & Consolidation Engine |
| M5 | API + Integration Layer |
| M6 | Production Hardening & Observability |
| M7 | PostgreSQL + Cloud Adapters (v0.1.0 release gate) |

See [`tasks/`](tasks/) for full prompt-ready implementation specifications.

## License

MIT

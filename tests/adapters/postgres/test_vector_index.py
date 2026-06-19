"""Integration tests for PgvectorVectorIndex — M7-T3 (10 tests).

Requires a real PostgreSQL instance with the pgvector extension installed.
Set TEST_POSTGRES_DSN to run; skipped automatically when unset.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
import pytest_asyncio

TEST_DSN = os.environ.get("TEST_POSTGRES_DSN", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DSN,
        reason="TEST_POSTGRES_DSN not set — skipping pgvector integration tests",
    ),
]

_DIM = 4  # small dimension for fast test vectors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _vec(val: float, dim: int = _DIM) -> list[float]:
    """Return a unit-ish vector with all components set to *val*."""
    return [val] * dim


def _make_doc(
    memory_id: str = "vec-001",
    tenant_id: str = "tenant-A",
    sector: str = "EPISODIC",
    lifecycle_state: str = "ACTIVE",
    embedding: list[float] | None = None,
):
    from memory_layer.domain.types import LifecycleState, MemoryId, MemorySector, TenantId
    from memory_layer.ports.outbound import VectorDocument

    return VectorDocument(
        memory_id=MemoryId(memory_id),
        tenant_id=TenantId(tenant_id),
        embedding=embedding if embedding is not None else _vec(0.5),
        embedding_model_id="test-model",
        embedding_dimensions=_DIM,
        content=f"content of {memory_id}",
        sector=MemorySector(sector),
        lifecycle_state=LifecycleState(lifecycle_state),
        metadata={"test": True},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def pool():
    import asyncpg
    import pgvector.asyncpg  # type: ignore[import-untyped]

    p = await asyncpg.create_pool(dsn=TEST_DSN, min_size=1, max_size=3)
    # Bootstrap schema with small vector dimension for tests
    async with p.acquire() as conn:
        await pgvector.asyncpg.register_vector(conn)
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vector_index (
                memory_id            TEXT NOT NULL,
                tenant_id            TEXT NOT NULL,
                embedding            vector(4),
                embedding_model_id   TEXT NOT NULL,
                embedding_dimensions INT  NOT NULL,
                content              TEXT NOT NULL,
                sector               TEXT NOT NULL,
                lifecycle_state      TEXT NOT NULL,
                metadata             JSONB NOT NULL DEFAULT '{}',
                PRIMARY KEY (memory_id, tenant_id)
            )
            """
        )
    yield p
    async with p.acquire() as conn:
        await conn.execute("DELETE FROM vector_index WHERE tenant_id LIKE 'tenant-%'")
    await p.close()


@pytest_asyncio.fixture
async def idx(pool):
    from memory_layer.adapters.postgres.vector_index import PgvectorVectorIndex
    return PgvectorVectorIndex(pool, dimensions=_DIM)


@pytest_asyncio.fixture(autouse=True)
async def cleanup(pool):
    yield
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM vector_index WHERE tenant_id LIKE 'tenant-%'")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_then_search_returns_doc(idx):
    """1. upsert then search returns the inserted doc in results."""
    from memory_layer.domain.types import TenantId

    doc = _make_doc()
    await idx.upsert(doc)
    results = await idx.search(_vec(0.5), TenantId("tenant-A"), k=5, filters={})
    ids = [r.memory_id for r in results]
    assert doc.memory_id in ids


@pytest.mark.asyncio
async def test_search_results_ordered_by_descending_score(idx):
    """2. search returns results ordered by descending score."""
    from memory_layer.domain.types import TenantId

    await idx.upsert(_make_doc(memory_id="vec-close", embedding=_vec(0.9)))
    await idx.upsert(_make_doc(memory_id="vec-far", embedding=_vec(0.1)))
    results = await idx.search(_vec(0.9), TenantId("tenant-A"), k=10, filters={})
    assert len(results) >= 2
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_search_respects_k_limit(idx):
    """3. search respects k limit."""
    from memory_layer.domain.types import TenantId

    for i in range(5):
        await idx.upsert(_make_doc(memory_id=f"vec-k{i}", embedding=_vec(float(i) / 10)))
    results = await idx.search(_vec(0.5), TenantId("tenant-A"), k=3, filters={})
    assert len(results) <= 3


@pytest.mark.asyncio
async def test_cross_tenant_isolation(idx):
    """4. Cross-tenant isolation: vector from tenant A not returned for tenant B."""
    from memory_layer.domain.types import TenantId

    doc_a = _make_doc(memory_id="vec-iso", tenant_id="tenant-A")
    await idx.upsert(doc_a)
    results = await idx.search(_vec(0.5), TenantId("tenant-B"), k=10, filters={})
    ids = [r.memory_id for r in results]
    assert doc_a.memory_id not in ids


@pytest.mark.asyncio
async def test_delete_removes_from_search(idx):
    """5. delete removes doc from search results."""
    from memory_layer.domain.types import MemoryId, TenantId

    doc = _make_doc(memory_id="vec-del")
    await idx.upsert(doc)
    await idx.delete(MemoryId("vec-del"), TenantId("tenant-A"))
    results = await idx.search(_vec(0.5), TenantId("tenant-A"), k=10, filters={})
    ids = [r.memory_id for r in results]
    assert "vec-del" not in ids


@pytest.mark.asyncio
async def test_upsert_updates_embedding(idx):
    """6. upsert with same memory_id updates the embedding (upsert semantics)."""
    from memory_layer.domain.types import TenantId

    doc_v1 = _make_doc(memory_id="vec-upsert", embedding=_vec(0.1))
    await idx.upsert(doc_v1)
    doc_v2 = _make_doc(memory_id="vec-upsert", embedding=_vec(0.9))
    await idx.upsert(doc_v2)

    # After upsert the stored embedding should match v2 (high similarity to 0.9 vector)
    results = await idx.search(_vec(0.9), TenantId("tenant-A"), k=5, filters={})
    ids = [r.memory_id for r in results]
    assert "vec-upsert" in ids
    # The top result should be the updated high-similarity doc
    assert results[0].memory_id == "vec-upsert"


@pytest.mark.asyncio
async def test_search_sector_filter_excludes_other_sectors(idx):
    """7. search with sectors filter excludes other sectors."""
    from memory_layer.domain.types import MemorySector, TenantId

    doc_ep = _make_doc(memory_id="vec-sec-ep", sector="EPISODIC", embedding=_vec(0.5))
    doc_sem = _make_doc(memory_id="vec-sec-sem", sector="SEMANTIC", embedding=_vec(0.5))
    await idx.upsert(doc_ep)
    await idx.upsert(doc_sem)

    results = await idx.search(
        _vec(0.5),
        TenantId("tenant-A"),
        k=10,
        filters={"sectors": [MemorySector.EPISODIC]},
    )
    ids = [r.memory_id for r in results]
    assert "vec-sec-ep" in ids
    assert "vec-sec-sem" not in ids


@pytest.mark.asyncio
async def test_search_lifecycle_filter_excludes_non_matching(idx):
    """8. search with lifecycle_states filter excludes non-matching states."""
    from memory_layer.domain.types import LifecycleState, TenantId

    doc_active = _make_doc(memory_id="vec-lc-act", lifecycle_state="ACTIVE", embedding=_vec(0.5))
    doc_deleted = _make_doc(memory_id="vec-lc-del", lifecycle_state="DELETED", embedding=_vec(0.5))
    await idx.upsert(doc_active)
    await idx.upsert(doc_deleted)

    results = await idx.search(
        _vec(0.5),
        TenantId("tenant-A"),
        k=10,
        filters={"lifecycle_states": [LifecycleState.ACTIVE]},
    )
    ids = [r.memory_id for r in results]
    assert "vec-lc-act" in ids
    assert "vec-lc-del" not in ids


@pytest.mark.asyncio
async def test_search_empty_index_returns_empty_list(idx):
    """9. search on empty index returns []."""
    from memory_layer.domain.types import TenantId

    results = await idx.search(_vec(0.5), TenantId("tenant-empty"), k=10, filters={})
    assert results == []


@pytest.mark.asyncio
async def test_score_values_in_valid_range(idx):
    """10. Score values are between -1.0 and 1.0."""
    from memory_layer.domain.types import TenantId

    await idx.upsert(_make_doc(memory_id="vec-score", embedding=_vec(0.7)))
    results = await idx.search(_vec(0.5), TenantId("tenant-A"), k=5, filters={})
    assert results, "Expected at least one result"
    for r in results:
        assert -1.0 <= r.score <= 1.0, f"Score out of range: {r.score}"

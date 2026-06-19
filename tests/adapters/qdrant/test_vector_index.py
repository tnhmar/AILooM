"""Integration tests for QdrantVectorIndex — M7-T4 (10 tests).

Requires a running Qdrant instance (local or Qdrant Cloud).
Set TEST_QDRANT_URL to run; skipped automatically when unset.

Optional: set TEST_QDRANT_API_KEY for authenticated Qdrant Cloud endpoints.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

TEST_QDRANT_URL = os.environ.get("TEST_QDRANT_URL", "")
TEST_QDRANT_API_KEY = os.environ.get("TEST_QDRANT_API_KEY", None)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_QDRANT_URL,
        reason="TEST_QDRANT_URL not set — skipping Qdrant integration tests",
    ),
]

_DIM = 4  # small dimension for fast test vectors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _vec(val: float, dim: int = _DIM) -> list[float]:
    return [val] * dim


def _make_doc(
    memory_id: str = "qdoc-001",
    tenant_id: str = "qtenant-A",
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
async def idx():
    from memory_layer.adapters.qdrant.vector_index import QdrantVectorIndex

    instance = QdrantVectorIndex(
        url=TEST_QDRANT_URL,
        api_key=TEST_QDRANT_API_KEY,
        dimensions=_DIM,
    )
    yield instance
    await instance.aclose()


@pytest_asyncio.fixture(autouse=True)
async def cleanup(idx):
    """Delete test collections after each test to ensure isolation."""
    yield
    from qdrant_client.http.exceptions import UnexpectedResponse

    for suffix in ["qtenant-A", "qtenant-B", "qtenant-iso", "qtenant-close"]:
        cname = f"memory_layer_{suffix}"
        try:
            await idx._client.delete_collection(cname)
        except (UnexpectedResponse, Exception):
            pass
    # Reset the in-process collection cache so _ensure_collection re-runs
    idx._ensured.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_then_search_returns_doc(idx):
    """1. upsert then search returns the inserted doc."""
    from memory_layer.domain.types import TenantId

    doc = _make_doc()
    await idx.upsert(doc)
    results = await idx.search(_vec(0.5), TenantId("qtenant-A"), k=5, filters={})
    ids = [r.memory_id for r in results]
    assert doc.memory_id in ids


@pytest.mark.asyncio
async def test_search_ordered_by_descending_score(idx):
    """2. search returns results ordered by descending score."""
    from memory_layer.domain.types import TenantId

    await idx.upsert(_make_doc(memory_id="qdoc-close", embedding=_vec(0.9)))
    await idx.upsert(_make_doc(memory_id="qdoc-far", embedding=_vec(0.1)))
    results = await idx.search(_vec(0.9), TenantId("qtenant-A"), k=10, filters={})
    assert len(results) >= 2
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_search_respects_k_limit(idx):
    """3. search respects k limit."""
    from memory_layer.domain.types import TenantId

    for i in range(5):
        await idx.upsert(_make_doc(memory_id=f"qdoc-k{i}", embedding=_vec(float(i) / 10 + 0.1)))
    results = await idx.search(_vec(0.5), TenantId("qtenant-A"), k=3, filters={})
    assert len(results) <= 3


@pytest.mark.asyncio
async def test_cross_tenant_isolation_different_collections(idx):
    """4. Cross-tenant isolation: different collections per tenant."""
    from memory_layer.domain.types import TenantId

    doc_a = _make_doc(memory_id="qdoc-iso", tenant_id="qtenant-A")
    await idx.upsert(doc_a)
    results = await idx.search(_vec(0.5), TenantId("qtenant-B"), k=10, filters={})
    ids = [r.memory_id for r in results]
    assert doc_a.memory_id not in ids


@pytest.mark.asyncio
async def test_delete_removes_from_results(idx):
    """5. delete removes doc from results."""
    from memory_layer.domain.types import MemoryId, TenantId

    doc = _make_doc(memory_id="qdoc-del")
    await idx.upsert(doc)
    await idx.delete(MemoryId("qdoc-del"), TenantId("qtenant-A"))
    results = await idx.search(_vec(0.5), TenantId("qtenant-A"), k=10, filters={})
    ids = [r.memory_id for r in results]
    assert "qdoc-del" not in ids


@pytest.mark.asyncio
async def test_upsert_updates_existing_point(idx):
    """6. upsert with same memory_id updates the point."""
    from memory_layer.domain.types import TenantId

    doc_v1 = _make_doc(memory_id="qdoc-up", embedding=_vec(0.1))
    await idx.upsert(doc_v1)
    doc_v2 = _make_doc(memory_id="qdoc-up", embedding=_vec(0.9))
    await idx.upsert(doc_v2)
    results = await idx.search(_vec(0.9), TenantId("qtenant-A"), k=5, filters={})
    assert results, "Expected at least one result after upsert update"
    assert results[0].memory_id == "qdoc-up"


@pytest.mark.asyncio
async def test_sectors_filter_excludes_other_sectors(idx):
    """7. sectors filter excludes non-matching sectors."""
    from memory_layer.domain.types import MemorySector, TenantId

    await idx.upsert(_make_doc(memory_id="qdoc-ep", sector="EPISODIC", embedding=_vec(0.5)))
    await idx.upsert(_make_doc(memory_id="qdoc-sem", sector="SEMANTIC", embedding=_vec(0.5)))
    results = await idx.search(
        _vec(0.5),
        TenantId("qtenant-A"),
        k=10,
        filters={"sectors": [MemorySector.EPISODIC]},
    )
    ids = [r.memory_id for r in results]
    assert "qdoc-ep" in ids
    assert "qdoc-sem" not in ids


@pytest.mark.asyncio
async def test_lifecycle_states_filter_excludes_non_matching(idx):
    """8. lifecycle_states filter excludes non-matching states."""
    from memory_layer.domain.types import LifecycleState, TenantId

    await idx.upsert(_make_doc(memory_id="qdoc-act", lifecycle_state="ACTIVE", embedding=_vec(0.5)))
    await idx.upsert(_make_doc(memory_id="qdoc-del", lifecycle_state="DELETED", embedding=_vec(0.5)))
    results = await idx.search(
        _vec(0.5),
        TenantId("qtenant-A"),
        k=10,
        filters={"lifecycle_states": [LifecycleState.ACTIVE]},
    )
    ids = [r.memory_id for r in results]
    assert "qdoc-act" in ids
    assert "qdoc-del" not in ids


@pytest.mark.asyncio
async def test_ensure_collection_is_idempotent(idx):
    """9. _ensure_collection is idempotent (call twice, no error)."""
    from memory_layer.domain.types import TenantId

    t = TenantId("qtenant-A")
    await idx._ensure_collection(t, _DIM)
    await idx._ensure_collection(t, _DIM)  # must not raise


@pytest.mark.asyncio
async def test_aclose_does_not_raise():
    """10. aclose does not raise."""
    from memory_layer.adapters.qdrant.vector_index import QdrantVectorIndex

    instance = QdrantVectorIndex(url=TEST_QDRANT_URL, api_key=TEST_QDRANT_API_KEY, dimensions=_DIM)
    await instance.aclose()  # must not raise

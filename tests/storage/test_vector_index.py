"""Acceptance tests for ChromaVectorIndex — M2-T4.

Uses chromadb.EphemeralClient so no disk I/O occurs during the test run.
"""

from __future__ import annotations

import chromadb
import pytest
import pytest_asyncio

from memory_layer.domain.types import (
    LifecycleState,
    MemoryId,
    MemorySector,
    TenantId,
    new_memory_id,
    new_tenant_id,
)
from memory_layer.ports.outbound import VectorDocument
from memory_layer.storage.vector.local_vector import (
    ChromaVectorIndex,
    _collection_name,
)

pytestmark = pytest.mark.asyncio

_MODEL_ID = "text-embedding-3-small"
_DIMS = 4  # tiny embeddings for tests


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def index() -> ChromaVectorIndex:
    """In-memory ChromaDB index instance — isolated per test."""
    client = chromadb.EphemeralClient()
    return ChromaVectorIndex(chroma_settings=client)


def _make_doc(
    *,
    tenant_id: TenantId | None = None,
    memory_id: MemoryId | None = None,
    embedding: list[float] | None = None,
    sector: MemorySector = MemorySector.EPISODIC,
    metadata: dict | None = None,
) -> VectorDocument:
    return VectorDocument(
        memory_id=memory_id or new_memory_id(),
        tenant_id=tenant_id or new_tenant_id(),
        embedding=embedding or [0.1, 0.2, 0.3, 0.4],
        embedding_model_id=_MODEL_ID,
        embedding_dimensions=_DIMS,
        content="test content",
        sector=sector,
        lifecycle_state=LifecycleState.ACTIVE,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# 1. upsert then search returns the upserted document
# ---------------------------------------------------------------------------

async def test_upsert_then_search_returns_document(
    index: ChromaVectorIndex,
) -> None:
    doc = _make_doc()
    await index.upsert(doc)
    results = await index.search(
        query_embedding=doc.embedding,
        tenant_id=doc.tenant_id,
        k=5,
        filters={"embedding_model_id": _MODEL_ID, "embedding_dimensions": _DIMS},
    )
    assert any(r.memory_id == doc.memory_id for r in results)


# ---------------------------------------------------------------------------
# 2. search returns results with score between 0 and 1
# ---------------------------------------------------------------------------

async def test_search_scores_between_0_and_1(index: ChromaVectorIndex) -> None:
    doc = _make_doc()
    await index.upsert(doc)
    results = await index.search(
        query_embedding=[0.1, 0.2, 0.3, 0.4],
        tenant_id=doc.tenant_id,
        k=5,
        filters={"embedding_model_id": _MODEL_ID},
    )
    for r in results:
        assert 0.0 <= r.score <= 1.0, f"Score out of range: {r.score}"


# ---------------------------------------------------------------------------
# 3. delete removes document — subsequent search does not return it
# ---------------------------------------------------------------------------

async def test_delete_removes_document(index: ChromaVectorIndex) -> None:
    doc = _make_doc()
    await index.upsert(doc)
    await index.delete(doc.memory_id, doc.tenant_id)
    results = await index.search(
        query_embedding=doc.embedding,
        tenant_id=doc.tenant_id,
        k=10,
        filters={"embedding_model_id": _MODEL_ID},
    )
    assert all(r.memory_id != doc.memory_id for r in results)


# ---------------------------------------------------------------------------
# 4. Upserting same memory_id twice is idempotent
# ---------------------------------------------------------------------------

async def test_upsert_idempotent(index: ChromaVectorIndex) -> None:
    doc = _make_doc()
    await index.upsert(doc)
    await index.upsert(doc)  # second call must not raise or duplicate
    results = await index.search(
        query_embedding=doc.embedding,
        tenant_id=doc.tenant_id,
        k=10,
        filters={"embedding_model_id": _MODEL_ID},
    )
    matching = [r for r in results if r.memory_id == doc.memory_id]
    assert len(matching) == 1


# ---------------------------------------------------------------------------
# 5. search with k=1 returns at most 1 result
# ---------------------------------------------------------------------------

async def test_search_k1_returns_at_most_one(index: ChromaVectorIndex) -> None:
    tid = new_tenant_id()
    for vec in ([0.1, 0.2, 0.3, 0.4], [0.4, 0.3, 0.2, 0.1], [0.5, 0.5, 0.0, 0.0]):
        await index.upsert(_make_doc(tenant_id=tid, embedding=vec))
    results = await index.search(
        query_embedding=[0.1, 0.2, 0.3, 0.4],
        tenant_id=tid,
        k=1,
        filters={"embedding_model_id": _MODEL_ID},
    )
    assert len(results) <= 1


# ---------------------------------------------------------------------------
# 6. search filters by tenant_id — docs from another tenant not returned
# ---------------------------------------------------------------------------

async def test_search_tenant_isolation(index: ChromaVectorIndex) -> None:
    tid_a = new_tenant_id()
    tid_b = new_tenant_id()
    doc_a = _make_doc(tenant_id=tid_a, embedding=[0.1, 0.2, 0.3, 0.4])
    doc_b = _make_doc(tenant_id=tid_b, embedding=[0.1, 0.2, 0.3, 0.4])
    await index.upsert(doc_a)
    await index.upsert(doc_b)

    results = await index.search(
        query_embedding=[0.1, 0.2, 0.3, 0.4],
        tenant_id=tid_a,
        k=10,
        filters={"embedding_model_id": _MODEL_ID},
    )
    ids = {r.memory_id for r in results}
    assert doc_a.memory_id in ids
    assert doc_b.memory_id not in ids


# ---------------------------------------------------------------------------
# 7. _collection_name produces valid string for typical inputs
# ---------------------------------------------------------------------------

def test_collection_name_valid_typical() -> None:
    name = _collection_name("tenant-abc12345-xyz", "text-embedding-3-small")
    assert name.startswith("memory_vectors_")
    assert len(name) > 0
    # Must not contain chars that would break ChromaDB collection names
    assert all(c.isalnum() or c == "_" for c in name)


# ---------------------------------------------------------------------------
# 8. _collection_name replaces non-alphanumeric model_id chars with underscores
# ---------------------------------------------------------------------------

def test_collection_name_model_slug() -> None:
    name = _collection_name("tenantabc", "text-embedding-3-small")
    assert "text_embedding_3_small" in name


# ---------------------------------------------------------------------------
# 9. _collection_name uses only first 8 chars of tenant_id (lowercased, alphanumeric)
# ---------------------------------------------------------------------------

def test_collection_name_tenant_short() -> None:
    name = _collection_name("ABCDEFGHIJKLMNOP", "model")
    # lowercased + first 8 alphanumeric = "abcdefgh"
    assert "abcdefgh" in name
    assert "abcdefghi" not in name


# ---------------------------------------------------------------------------
# 10. upsert stores sector in metadata; appears in VectorSearchResult.metadata
# ---------------------------------------------------------------------------

async def test_sector_stored_in_metadata(index: ChromaVectorIndex) -> None:
    doc = _make_doc(sector=MemorySector.SEMANTIC)
    await index.upsert(doc)
    results = await index.search(
        query_embedding=doc.embedding,
        tenant_id=doc.tenant_id,
        k=5,
        filters={"embedding_model_id": _MODEL_ID},
    )
    hit = next((r for r in results if r.memory_id == doc.memory_id), None)
    assert hit is not None
    assert hit.metadata.get("sector") == str(MemorySector.SEMANTIC)

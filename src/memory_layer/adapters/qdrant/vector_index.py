"""Qdrant Cloud (or self-hosted) implementation of VectorIndexPort.

Uses collection-per-tenant isolation: every tenant gets its own Qdrant
collection named ``memory_layer_{tenant_id}``.  This is the recommended path
for teams that want a dedicated vector database rather than pgvector.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels
from qdrant_client.http.exceptions import UnexpectedResponse

from memory_layer.domain.types import LifecycleState, MemoryId, MemorySector, TenantId
from memory_layer.ports.outbound import VectorDocument, VectorSearchResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _point_id(memory_id: str) -> str:
    """Deterministic UUID5 from *memory_id* for stable Qdrant point IDs."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, memory_id))


def _collection_name(tenant_id: TenantId) -> str:
    return f"memory_layer_{tenant_id}"


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class QdrantVectorIndex:
    """Implements :class:`VectorIndexPort` using the Qdrant vector database.

    Parameters
    ----------
    url:
        Qdrant server URL, e.g. ``https://xyz.cloud.qdrant.io:6333``.
    api_key:
        Optional Qdrant API key for Qdrant Cloud authentication.
    dimensions:
        Embedding vector dimensionality.  Defaults to 1536.
    """

    def __init__(
        self,
        url: str,
        api_key: Optional[str] = None,
        dimensions: int = 1536,
    ) -> None:
        self._dimensions = dimensions
        self._client = AsyncQdrantClient(url=url, api_key=api_key)
        # Cache of collection names already ensured this process lifetime.
        self._ensured: set[str] = set()

    # ------------------------------------------------------------------
    # VectorIndexPort
    # ------------------------------------------------------------------

    async def upsert(self, doc: VectorDocument) -> None:
        """Upsert a single vector document into the tenant collection.

        The collection is created automatically on the first upsert for a
        given tenant.
        """
        await self._ensure_collection(doc.tenant_id, doc.embedding_dimensions)
        collection = _collection_name(doc.tenant_id)
        point = qmodels.PointStruct(
            id=_point_id(str(doc.memory_id)),
            vector=doc.embedding,
            payload={
                "tenant_id": str(doc.tenant_id),
                "sector": str(doc.sector),
                "lifecycle_state": str(doc.lifecycle_state),
                "content": doc.content,
                "metadata": doc.metadata,
                "memory_id": str(doc.memory_id),
            },
        )
        await self._client.upsert(
            collection_name=collection,
            points=[point],
        )

    async def search(
        self,
        query_embedding: list[float],
        tenant_id: TenantId,
        k: int,
        filters: dict[str, Any],
    ) -> list[VectorSearchResult]:
        """Approximate nearest-neighbour search within *tenant_id*'s collection.

        Parameters
        ----------
        filters:
            Optional keys:

            ``sectors`` — list of :class:`MemorySector`; maps to a
            ``FieldCondition`` with ``MatchAny``.

            ``lifecycle_states`` — list of :class:`LifecycleState`; maps
            to a ``FieldCondition`` with ``MatchAny``.  Defaults to
            ``[ACTIVE, CONSOLIDATED]``.
        """
        collection = _collection_name(tenant_id)

        # Build filter conditions
        conditions: list[qmodels.Condition] = []

        raw_states = filters.get("lifecycle_states", None)
        if raw_states:
            state_vals = [str(s) for s in raw_states]
        else:
            state_vals = [str(LifecycleState.ACTIVE), str(LifecycleState.CONSOLIDATED)]
        conditions.append(
            qmodels.FieldCondition(
                key="lifecycle_state",
                match=qmodels.MatchAny(any=state_vals),
            )
        )

        raw_sectors = filters.get("sectors", None)
        if raw_sectors:
            conditions.append(
                qmodels.FieldCondition(
                    key="sector",
                    match=qmodels.MatchAny(any=[str(s) for s in raw_sectors]),
                )
            )

        qdrant_filter = qmodels.Filter(must=conditions)

        try:
            hits = await self._client.search(
                collection_name=collection,
                query_vector=query_embedding,
                query_filter=qdrant_filter,
                limit=k,
                with_payload=True,
            )
        except UnexpectedResponse:
            # Collection does not exist yet — return empty list.
            return []

        results: list[VectorSearchResult] = []
        for hit in hits:
            payload = hit.payload or {}
            results.append(
                VectorSearchResult(
                    memory_id=MemoryId(payload.get("memory_id", "")),
                    score=float(hit.score),
                    content=payload.get("content", ""),
                    metadata=payload.get("metadata", {}),
                )
            )
        return results

    async def delete(self, memory_id: MemoryId, tenant_id: TenantId) -> None:
        """Remove a single point from *tenant_id*'s collection."""
        collection = _collection_name(tenant_id)
        point_id = _point_id(str(memory_id))
        try:
            await self._client.delete(
                collection_name=collection,
                points_selector=qmodels.PointIdsList(points=[point_id]),
            )
        except UnexpectedResponse:
            # Collection not found — nothing to delete.
            pass

    async def _ensure_collection(
        self, tenant_id: TenantId, dimensions: int
    ) -> None:
        """Create the tenant collection if it does not already exist.

        Idempotent: a second call for the same tenant is a fast in-process
        cache hit after the first successful creation check.
        """
        collection = _collection_name(tenant_id)
        if collection in self._ensured:
            return

        existing = {c.name for c in (await self._client.get_collections()).collections}
        if collection not in existing:
            await self._client.create_collection(
                collection_name=collection,
                vectors_config=qmodels.VectorParams(
                    size=dimensions,
                    distance=qmodels.Distance.COSINE,
                ),
                hnsw_config=qmodels.HnswConfigDiff(
                    m=16,
                    ef_construct=64,
                ),
            )
            logger.info("Created Qdrant collection %r for tenant %r", collection, tenant_id)

        self._ensured.add(collection)

    async def aclose(self) -> None:
        """Close the underlying Qdrant async client."""
        await self._client.close()

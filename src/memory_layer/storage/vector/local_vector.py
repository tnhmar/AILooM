"""ChromaDB-backed vector index adapter for memory-layer (local mode).

Design notes
------------
- Collections are scoped per ``(tenant_id, embedding_model_id)`` per ADR-007.
  Collection name format: ``memory_vectors_{tenant_id_short}_{model_id_slug}``.
- The ChromaDB sync client is wrapped in ``asyncio.to_thread`` so the public
  interface is fully async without running blocking I/O on the event-loop thread.
- Every ``search`` call injects ``{"tenant_id": tenant_id}`` into the ChromaDB
  ``where`` filter as a mandatory guard against cross-tenant leakage.
- ``delete`` is best-effort per tenant: it queries by ``tenant_id`` metadata
  before attempting deletion so it never raises if a document doesn't exist
  under the given tenant.
- ``chroma_settings`` is accepted as a constructor parameter so tests can inject
  an ``EphemeralClient`` without touching the filesystem.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from typing import Any, cast

import chromadb
from chromadb.api import ClientAPI

from memory_layer.domain.exceptions import StorageError
from memory_layer.domain.types import MemoryId, TenantId
from memory_layer.ports.outbound import VectorDocument, VectorSearchResult


# ---------------------------------------------------------------------------
# Collection naming  (ADR-007)
# ---------------------------------------------------------------------------


def _collection_name(tenant_id: str, model_id: str) -> str:
    """Build a scoped ChromaDB collection name.

    Parameters
    ----------
    tenant_id:
        Full tenant identifier.  Only the first 8 lowercased alphanumeric
        characters are used so the name stays within ChromaDB's 63-char limit.
    model_id:
        Embedding model identifier (e.g. ``"text-embedding-3-small"``).
        Non-alphanumeric characters are replaced with underscores.

    Returns
    -------
    str
        A deterministic, filesystem-safe collection name such as
        ``"memory_vectors_abc12345_text_embedding_3_small"``.
    """
    tenant_short = re.sub(r"[^a-z0-9]", "", tenant_id.lower())[:8]
    model_slug = re.sub(r"[^a-zA-Z0-9]", "_", model_id)
    return f"memory_vectors_{tenant_short}_{model_slug}"


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class ChromaVectorIndex:
    """Local ChromaDB adapter implementing :class:`~memory_layer.ports.outbound.VectorIndexPort`.

    Parameters
    ----------
    persist_directory:
        Directory used by the ChromaDB persistent client.  Ignored when
        *chroma_settings* provides an ephemeral or custom client.
    chroma_settings:
        Optional pre-built ``ClientAPI`` instance.  Pass a client
        created with ``chromadb.EphemeralClient()`` in tests to avoid
        disk I/O entirely.
    """

    def __init__(
        self,
        persist_directory: str = "./chroma_data",
        chroma_settings: ClientAPI | None = None,
    ) -> None:
        if chroma_settings is not None:
            self._client: ClientAPI = chroma_settings
        else:
            self._client = chromadb.PersistentClient(path=persist_directory)

        # Cache open collections to avoid repeated get_or_create overhead.
        self._collections: dict[str, chromadb.Collection] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_collection(
        self, tenant_id: str, model_id: str, dimensions: int
    ) -> chromadb.Collection:
        """Return (and cache) the ChromaDB collection for this tenant+model pair.

        The collection is created on first access with a cosine distance metric
        so that similarity scores are in the range [0, 1] after normalisation.
        """
        name = _collection_name(tenant_id, model_id)
        if name not in self._collections:
            self._collections[name] = self._client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collections[name]

    # ------------------------------------------------------------------
    # VectorIndexPort implementation
    # ------------------------------------------------------------------

    async def upsert(self, doc: VectorDocument) -> None:
        """Insert or replace a document in the tenant-scoped collection.

        The full ``metadata`` dict is flattened and stored alongside mandatory
        fields: ``tenant_id``, ``sector``, ``lifecycle_state``,
        ``embedding_model_id``, and ``embedding_dimensions``.

        Raises
        ------
        StorageError
            On unexpected ChromaDB errors.
        """
        flat_meta: dict[str, Any] = {
            **{k: v for k, v in doc.metadata.items() if isinstance(v, (str, int, float, bool))},
            "tenant_id": str(doc.tenant_id),
            "sector": str(doc.sector),
            "lifecycle_state": str(doc.lifecycle_state),
            "embedding_model_id": doc.embedding_model_id,
            "embedding_dimensions": doc.embedding_dimensions,
        }

        def _sync() -> None:
            collection = self._get_collection(
                str(doc.tenant_id), doc.embedding_model_id, doc.embedding_dimensions
            )
            collection.upsert(
                ids=[str(doc.memory_id)],
                embeddings=cast(list[Sequence[float]], [doc.embedding]),
                documents=[doc.content],
                metadatas=[flat_meta],
            )

        try:
            await asyncio.to_thread(_sync)
        except Exception as exc:
            raise StorageError(f"upsert failed for memory_id={doc.memory_id}: {exc}") from exc

    async def search(
        self,
        query_embedding: list[float],
        tenant_id: TenantId,
        k: int,
        filters: dict[str, Any],
    ) -> list[VectorSearchResult]:
        """Query the tenant's collection with a pre-computed embedding.

        The ``tenant_id`` is always injected into the ChromaDB ``where`` clause
        as a mandatory isolation guard, regardless of what *filters* contains.

        Parameters
        ----------
        query_embedding:
            Pre-computed query vector with the same dimensionality as the
            stored documents.
        tenant_id:
            Caller's tenant — used both for collection routing and as a
            mandatory metadata filter.
        k:
            Maximum number of results to return.
        filters:
            Additional ChromaDB ``where`` filters (merged with tenant guard).

        Returns
        -------
        list[VectorSearchResult]
            Results ordered by descending similarity.  Scores are cosine
            similarity values in [0, 1].

        Raises
        ------
        StorageError
            On unexpected ChromaDB errors.
        """
        # Merge caller filters with mandatory tenant isolation guard.
        where: dict[str, Any] = {**filters, "tenant_id": str(tenant_id)}

        # model_id is required to route to the right collection.  Callers
        # should pass it via filters["embedding_model_id"]; fall back to a
        # sentinel that will simply return no results from any collection.
        model_id: str = filters.get("embedding_model_id", "__unknown__")
        dimensions: int = filters.get("embedding_dimensions", len(query_embedding))

        # Remove routing keys from the ChromaDB where clause — they are not
        # stored as searchable metadata fields by default.
        chroma_where = {
            k_: v for k_, v in where.items() if k_ not in ("embedding_dimensions",)
        }

        def _sync() -> list[VectorSearchResult]:
            collection = self._get_collection(str(tenant_id), model_id, dimensions)

            # ChromaDB raises if n_results > number of documents in the
            # collection.  Clamp to avoid this error on small/empty collections.
            n_docs = collection.count()
            n_results = max(1, min(k, n_docs)) if n_docs > 0 else 0
            if n_results == 0:
                return []

            results = collection.query(
                query_embeddings=cast(list[Sequence[float]], [query_embedding]),
                n_results=n_results,
                where=chroma_where if len(chroma_where) > 1 else {"tenant_id": str(tenant_id)},
                include=["documents", "metadatas", "distances"],
            )

            out: list[VectorSearchResult] = []
            ids = (results.get("ids") or [[]])[0]
            docs = (results.get("documents") or [[]])[0]
            metas = (results.get("metadatas") or [[]])[0]
            dists = (results.get("distances") or [[]])[0]

            for mem_id, content, meta, dist in zip(ids, docs, metas, dists):
                # ChromaDB cosine distance ∈ [0, 2]; convert to similarity ∈ [0, 1].
                score = max(0.0, min(1.0, 1.0 - dist / 2.0))
                out.append(
                    VectorSearchResult(
                        memory_id=MemoryId(mem_id),
                        score=score,
                        content=content or "",
                        metadata=dict(meta) if meta else {},
                    )
                )
            return out

        try:
            return await asyncio.to_thread(_sync)
        except Exception as exc:
            raise StorageError(f"search failed for tenant_id={tenant_id}: {exc}") from exc

    async def delete(self, memory_id: MemoryId, tenant_id: TenantId) -> None:
        """Remove a document from every collection owned by this tenant.

        Iterates all cached collections whose name starts with the tenant's
        prefix and attempts deletion.  Silently skips if the document is not
        present.

        Raises
        ------
        StorageError
            On unexpected ChromaDB errors.
        """
        tenant_short = re.sub(r"[^a-z0-9]", "", str(tenant_id).lower())[:8]
        prefix = f"memory_vectors_{tenant_short}_"

        def _sync() -> None:
            # Discover all collections for this tenant (handles multi-model case).
            all_collections = self._client.list_collections()
            for col_meta in all_collections:
                name = col_meta.name if hasattr(col_meta, "name") else str(col_meta)
                if not name.startswith(prefix):
                    continue
                col = self._client.get_collection(name)
                # Only delete if the doc actually belongs to this tenant.
                existing = col.get(
                    ids=[str(memory_id)],
                    where={"tenant_id": str(tenant_id)},
                    include=[],
                )
                if existing["ids"]:
                    col.delete(ids=[str(memory_id)])

        try:
            await asyncio.to_thread(_sync)
        except Exception as exc:
            raise StorageError(
                f"delete failed for memory_id={memory_id}, tenant={tenant_id}: {exc}"
            ) from exc

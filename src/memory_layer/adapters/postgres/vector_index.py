"""pgvector implementation of VectorIndexPort.

Colocates embedding vectors with records in the same PostgreSQL database.
Uses an HNSW index for approximate nearest-neighbour search and supports
tenant-scoped hybrid metadata filtering.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg
import pgvector.asyncpg  # type: ignore[import-untyped]

from memory_layer.domain.types import (
    LifecycleState,
    MemoryId,
    MemorySector,
    TenantId,
)
from memory_layer.ports.outbound import VectorDocument, VectorSearchResult

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

VECTOR_INDEX_DDL: str = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS vector_index (
    memory_id            TEXT        NOT NULL,
    tenant_id            TEXT        NOT NULL,
    embedding            vector(1536),
    embedding_model_id   TEXT        NOT NULL,
    embedding_dimensions INT         NOT NULL,
    content              TEXT        NOT NULL,
    sector               TEXT        NOT NULL,
    lifecycle_state      TEXT        NOT NULL,
    metadata             JSONB       NOT NULL DEFAULT '{}',
    PRIMARY KEY (memory_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_vector_index_hnsw
    ON vector_index
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_vector_index_tenant_sector
    ON vector_index (tenant_id, sector, lifecycle_state);
"""

# Default lifecycle states used when not overridden via filters.
_DEFAULT_LIFECYCLE_STATES = [str(LifecycleState.ACTIVE), str(LifecycleState.CONSOLIDATED)]


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class PgvectorVectorIndex:
    """Implements :class:`VectorIndexPort` using the pgvector Postgres extension.

    Parameters
    ----------
    pool:
        An :class:`asyncpg.Pool` connected to a Postgres instance with the
        ``vector`` extension available.
    dimensions:
        Embedding dimensionality.  Must match the ``vector(N)`` column
        declaration in :data:`VECTOR_INDEX_DDL`.  Defaults to 1536.
    """

    def __init__(self, pool: asyncpg.Pool, dimensions: int = 1536) -> None:
        self._pool = pool
        self._dimensions = dimensions

    async def ensure_schema(self) -> None:
        """Run :data:`VECTOR_INDEX_DDL` against the pool.

        Safe to call at every startup — all statements use ``IF NOT EXISTS``.
        """
        async with self._pool.acquire() as conn:
            await pgvector.asyncpg.register_vector(conn)
            await conn.execute(VECTOR_INDEX_DDL)

    # ------------------------------------------------------------------
    # VectorIndexPort methods
    # ------------------------------------------------------------------

    async def upsert(self, doc: VectorDocument) -> None:
        """INSERT or UPDATE a vector document.

        On conflict (same ``memory_id`` + ``tenant_id``) the embedding,
        content, sector, lifecycle_state, and metadata are overwritten.
        """
        metadata_json = json.dumps(doc.metadata)
        async with self._pool.acquire() as conn:
            await pgvector.asyncpg.register_vector(conn)
            await conn.execute(
                """
                INSERT INTO vector_index (
                    memory_id, tenant_id, embedding,
                    embedding_model_id, embedding_dimensions,
                    content, sector, lifecycle_state, metadata
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
                ON CONFLICT (memory_id, tenant_id) DO UPDATE SET
                    embedding         = EXCLUDED.embedding,
                    content           = EXCLUDED.content,
                    sector            = EXCLUDED.sector,
                    lifecycle_state   = EXCLUDED.lifecycle_state,
                    metadata          = EXCLUDED.metadata,
                    embedding_model_id     = EXCLUDED.embedding_model_id,
                    embedding_dimensions   = EXCLUDED.embedding_dimensions
                """,
                str(doc.memory_id),
                str(doc.tenant_id),
                doc.embedding,
                doc.embedding_model_id,
                doc.embedding_dimensions,
                doc.content,
                str(doc.sector),
                str(doc.lifecycle_state),
                metadata_json,
            )

    async def search(
        self,
        query_embedding: list[float],
        tenant_id: TenantId,
        k: int,
        filters: dict[str, Any],
    ) -> list[VectorSearchResult]:
        """Approximate nearest-neighbour search with metadata filtering.

        Parameters
        ----------
        query_embedding:
            Dense query vector.
        tenant_id:
            Results are strictly scoped to this tenant.
        k:
            Maximum number of results to return.
        filters:
            Optional keys:

            ``lifecycle_states`` (:class:`list` of :class:`LifecycleState`)
                Defaults to ``[ACTIVE, CONSOLIDATED]``.
            ``sectors`` (:class:`list` of :class:`MemorySector`)
                When absent all sectors are included.

        Returns
        -------
        list[VectorSearchResult]
            Ordered by descending cosine similarity score.
        """
        raw_states = filters.get("lifecycle_states", None)
        if raw_states:
            lifecycle_states = [str(s) for s in raw_states]
        else:
            lifecycle_states = _DEFAULT_LIFECYCLE_STATES

        raw_sectors: list[Any] | None = filters.get("sectors", None)

        async with self._pool.acquire() as conn:
            await pgvector.asyncpg.register_vector(conn)
            # Tune ef_search for this query within the transaction.
            await conn.execute("SET LOCAL hnsw.ef_search = 100")

            if raw_sectors:
                sector_strs = [str(s) for s in raw_sectors]
                rows = await conn.fetch(
                    """
                    SELECT memory_id, content, metadata,
                           1 - (embedding <=> $1::vector) AS score
                    FROM vector_index
                    WHERE tenant_id = $2
                      AND lifecycle_state = ANY($3::text[])
                      AND sector = ANY($4::text[])
                    ORDER BY embedding <=> $1::vector
                    LIMIT $5
                    """,
                    query_embedding,
                    str(tenant_id),
                    lifecycle_states,
                    sector_strs,
                    k,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT memory_id, content, metadata,
                           1 - (embedding <=> $1::vector) AS score
                    FROM vector_index
                    WHERE tenant_id = $2
                      AND lifecycle_state = ANY($3::text[])
                    ORDER BY embedding <=> $1::vector
                    LIMIT $4
                    """,
                    query_embedding,
                    str(tenant_id),
                    lifecycle_states,
                    k,
                )

        results: list[VectorSearchResult] = []
        for row in rows:
            raw_meta = row["metadata"]
            if isinstance(raw_meta, str):
                metadata: dict[str, Any] = json.loads(raw_meta)
            elif isinstance(raw_meta, dict):
                metadata = raw_meta
            else:
                metadata = {}
            results.append(
                VectorSearchResult(
                    memory_id=MemoryId(row["memory_id"]),
                    score=float(row["score"]),
                    content=row["content"],
                    metadata=metadata,
                )
            )
        return results

    async def delete(self, memory_id: MemoryId, tenant_id: TenantId) -> None:
        """Remove a single vector document scoped to *tenant_id*."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                DELETE FROM vector_index
                WHERE memory_id = $1 AND tenant_id = $2
                """,
                str(memory_id),
                str(tenant_id),
            )

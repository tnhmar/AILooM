"""PostgreSQL implementation of FactRepositoryPort.

Uses asyncpg for connection pooling. Every query is scoped to tenant_id.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg

from memory_layer.domain.records import Fact, Scope
from memory_layer.domain.types import (
    EntityId,
    FactId,
    LifecycleState,
    MemoryId,
    MemorySector,
    PrincipalId,
    PrincipalType,
    TenantId,
)

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

FACTS_DDL: str = """
CREATE TABLE IF NOT EXISTS facts (
    fact_id           TEXT        NOT NULL,
    tenant_id         TEXT        NOT NULL,
    memory_record_id  TEXT        NOT NULL,
    subject_entity_id TEXT        NOT NULL,
    predicate         TEXT        NOT NULL,
    predicate_group   TEXT        NOT NULL,
    object_value      TEXT        NOT NULL,
    confidence        FLOAT       NOT NULL,
    sector            TEXT        NOT NULL,
    lifecycle_state   TEXT        NOT NULL,
    effective_from    TIMESTAMPTZ NOT NULL,
    effective_to      TIMESTAMPTZ,
    superseded_by     TEXT,
    PRIMARY KEY (fact_id, tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_facts_entity_predicate
    ON facts (tenant_id, subject_entity_id, predicate_group)
    WHERE effective_to IS NULL;
"""


# ---------------------------------------------------------------------------
# Row <-> domain helpers
# ---------------------------------------------------------------------------


def _row_to_fact(row: dict[str, Any]) -> Fact:
    scope = Scope(
        tenant_id=TenantId(row["tenant_id"]),
        principal_id=PrincipalId("system"),
        principal_type=PrincipalType.AGENT,
    )
    effective_to: datetime | None = row.get("effective_to")
    return Fact(
        id=FactId(row["fact_id"]),
        memory_record_id=MemoryId(row["memory_record_id"]),
        tenant_id=TenantId(row["tenant_id"]),
        scope=scope,
        subject_entity_id=EntityId(row["subject_entity_id"]),
        predicate=row["predicate"],
        predicate_group=row["predicate_group"],
        object_value=row["object_value"],
        effective_from=row["effective_from"],
        effective_to=effective_to,
        supersedes=FactId(row["superseded_by"]) if row.get("superseded_by") else None,
        confidence=float(row["confidence"]),
        sector=MemorySector(row["sector"]),
        lifecycle_state=LifecycleState(row["lifecycle_state"]),
    )


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class PostgresFactRepository:
    """asyncpg-backed implementation of :class:`FactRepositoryPort`.

    Every query is scoped to ``tenant_id`` — no exceptions.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save(self, fact: Fact) -> None:
        """INSERT a fact row."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO facts (
                    fact_id, tenant_id, memory_record_id,
                    subject_entity_id, predicate, predicate_group,
                    object_value, confidence, sector,
                    lifecycle_state, effective_from, effective_to, superseded_by
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                ON CONFLICT (fact_id, tenant_id) DO NOTHING
                """,
                str(fact.id),
                str(fact.tenant_id),
                str(fact.memory_record_id),
                str(fact.subject_entity_id),
                fact.predicate,
                fact.predicate_group,
                fact.object_value,
                fact.confidence,
                str(fact.sector),
                str(fact.lifecycle_state),
                fact.effective_from,
                fact.effective_to,
                str(fact.supersedes) if fact.supersedes else None,
            )

    async def get_by_id(
        self, fact_id: FactId, tenant_id: TenantId
    ) -> Fact | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM facts
                WHERE fact_id = $1 AND tenant_id = $2
                """,
                str(fact_id),
                str(tenant_id),
            )
        if row is None:
            return None
        return _row_to_fact(dict(row))

    async def close_fact(
        self,
        fact_id: FactId,
        tenant_id: TenantId,
        effective_to: datetime,
        new_fact_id: FactId,
    ) -> None:
        """Set effective_to and superseded_by on a fact, scoped to tenant."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE facts
                SET effective_to = $3, superseded_by = $4
                WHERE fact_id = $1 AND tenant_id = $2
                """,
                str(fact_id),
                str(tenant_id),
                effective_to,
                str(new_fact_id),
            )

    async def get_active_facts_by_entity_predicate(
        self,
        entity_id: EntityId,
        predicate_group: str,
        tenant_id: TenantId,
    ) -> list[Fact]:
        """Return open (effective_to IS NULL) facts matching entity + predicate_group."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM facts
                WHERE effective_to IS NULL
                  AND subject_entity_id = $1
                  AND predicate_group = $2
                  AND tenant_id = $3
                """,
                str(entity_id),
                predicate_group,
                str(tenant_id),
            )
        return [_row_to_fact(dict(r)) for r in rows]

    async def list_by_memory_record(
        self, memory_record_id: MemoryId, tenant_id: TenantId
    ) -> list[Fact]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM facts
                WHERE memory_record_id = $1 AND tenant_id = $2
                """,
                str(memory_record_id),
                str(tenant_id),
            )
        return [_row_to_fact(dict(r)) for r in rows]

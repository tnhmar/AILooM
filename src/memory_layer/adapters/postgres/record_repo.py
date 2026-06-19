"""PostgreSQL implementation of MemoryRecordRepositoryPort.

Uses asyncpg for connection pooling and sqlalchemy.sql for query construction
(Core only — no ORM, no declarative base).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg
from sqlalchemy.sql import text

from memory_layer.domain.records import MemoryRecord, Scope
from memory_layer.domain.types import (
    LifecycleState,
    MemoryId,
    MemorySector,
    PayloadType,
    PipelineStatus,
    PrincipalId,
    PrincipalType,
    SessionId,
    TenantId,
    WorkspaceId,
)

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

MEMORY_RECORDS_DDL: str = """
CREATE TABLE IF NOT EXISTS memory_records (
    memory_id        TEXT        NOT NULL,
    tenant_id        TEXT        NOT NULL,
    raw_payload      TEXT        NOT NULL,
    payload_type     TEXT        NOT NULL,
    sector           TEXT        NOT NULL,
    lifecycle_state  TEXT        NOT NULL,
    pipeline_status  TEXT        NOT NULL,
    scope_agent_id   TEXT,
    scope_user_id    TEXT,
    scope_session_id TEXT,
    idempotency_key  TEXT,
    recorded_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (memory_id, tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_memory_records_tenant_lifecycle
    ON memory_records (tenant_id, lifecycle_state);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_records_idempotency
    ON memory_records (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
"""


# ---------------------------------------------------------------------------
# Row <-> domain helpers
# ---------------------------------------------------------------------------


def _row_to_record(row: dict[str, Any]) -> MemoryRecord:
    scope = Scope(
        tenant_id=TenantId(row["tenant_id"]),
        principal_id=PrincipalId(row.get("scope_user_id") or "unknown"),
        principal_type=PrincipalType.USER,
        session_id=SessionId(row["scope_session_id"]) if row.get("scope_session_id") else None,
    )
    return MemoryRecord(
        id=MemoryId(row["memory_id"]),
        tenant_id=TenantId(row["tenant_id"]),
        scope=scope,
        raw_payload=row["raw_payload"],
        payload_type=PayloadType(row["payload_type"]),
        sector=MemorySector(row["sector"]),
        lifecycle_state=LifecycleState(row["lifecycle_state"]),
        pipeline_status=PipelineStatus(row["pipeline_status"]),
        recorded_at=row["recorded_at"] if isinstance(row["recorded_at"], datetime) else datetime.fromisoformat(str(row["recorded_at"])),
        idempotency_key=row.get("idempotency_key"),
    )


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class PostgresMemoryRecordRepository:
    """asyncpg-backed implementation of :class:`MemoryRecordRepositoryPort`.

    Every query is scoped to ``tenant_id`` — no exceptions.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save(self, record: MemoryRecord) -> None:
        """INSERT the record; silently skip on duplicate (memory_id, tenant_id)."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO memory_records (
                    memory_id, tenant_id, raw_payload, payload_type, sector,
                    lifecycle_state, pipeline_status,
                    scope_agent_id, scope_user_id, scope_session_id,
                    idempotency_key, recorded_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                ON CONFLICT (memory_id, tenant_id) DO NOTHING
                """,
                str(record.id),
                str(record.tenant_id),
                record.raw_payload,
                str(record.payload_type),
                str(record.sector),
                str(record.lifecycle_state),
                str(record.pipeline_status),
                None,  # scope_agent_id — not in current Scope dataclass
                str(record.scope.principal_id),
                str(record.scope.session_id) if record.scope.session_id else None,
                record.idempotency_key,
                record.recorded_at,
            )

    async def get_by_id(
        self, memory_id: MemoryId, tenant_id: TenantId
    ) -> MemoryRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM memory_records
                WHERE memory_id = $1 AND tenant_id = $2
                """,
                str(memory_id),
                str(tenant_id),
            )
        if row is None:
            return None
        return _row_to_record(dict(row))

    async def update_lifecycle(
        self,
        memory_id: MemoryId,
        tenant_id: TenantId,
        state: LifecycleState,
        actor: str,
    ) -> None:
        """UPDATE lifecycle_state; no-op if already in the target state (optimistic)."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE memory_records
                SET lifecycle_state = $1
                WHERE memory_id = $2
                  AND tenant_id = $3
                  AND lifecycle_state != $1
                """,
                str(state),
                str(memory_id),
                str(tenant_id),
            )

    async def update_pipeline_status(
        self,
        memory_id: MemoryId,
        tenant_id: TenantId,
        status: PipelineStatus,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE memory_records
                SET pipeline_status = $1
                WHERE memory_id = $2 AND tenant_id = $3
                """,
                str(status),
                str(memory_id),
                str(tenant_id),
            )

    async def list_by_scope(
        self,
        scope: Scope,
        lifecycle_states: list[LifecycleState],
        limit: int = 100,
    ) -> list[MemoryRecord]:
        states = [str(s) for s in lifecycle_states]
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM memory_records
                WHERE tenant_id = $1
                  AND lifecycle_state = ANY($2::text[])
                ORDER BY recorded_at DESC
                LIMIT $3
                """,
                str(scope.tenant_id),
                states,
                limit,
            )
        return [_row_to_record(dict(r)) for r in rows]

    async def get_by_idempotency_key(
        self, key: str, tenant_id: TenantId
    ) -> MemoryRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM memory_records
                WHERE idempotency_key = $1 AND tenant_id = $2
                """,
                key,
                str(tenant_id),
            )
        if row is None:
            return None
        return _row_to_record(dict(row))

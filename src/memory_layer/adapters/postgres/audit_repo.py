"""PostgreSQL implementation of AuditLogPort.

Audit rows are append-only, enforced by a Postgres trigger that blocks
UPDATE and DELETE at the database level.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from memory_layer.domain.records import AuditEntry, Scope
from memory_layer.domain.types import (
    AuditId,
    AuditOperation,
    AuditOutcome,
    MemoryId,
    PrincipalId,
    PrincipalType,
    TenantId,
)

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

AUDIT_LOG_DDL: str = """
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id    TEXT        NOT NULL,
    tenant_id   TEXT        NOT NULL,
    memory_id   TEXT        NOT NULL,
    operation   TEXT        NOT NULL,
    actor       TEXT        NOT NULL,
    outcome     TEXT        NOT NULL,
    detail      JSONB,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (audit_id, tenant_id)
);

CREATE OR REPLACE FUNCTION audit_log_immutable()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$;

DROP TRIGGER IF EXISTS trg_audit_log_immutable ON audit_log;
CREATE TRIGGER trg_audit_log_immutable
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
"""


# ---------------------------------------------------------------------------
# Row <-> domain helpers
# ---------------------------------------------------------------------------


def _row_to_entry(row: dict[str, Any]) -> AuditEntry:
    scope = Scope(
        tenant_id=TenantId(row["tenant_id"]),
        principal_id=PrincipalId(row.get("actor") or "system"),
        principal_type=PrincipalType.USER,
    )
    raw_detail = row.get("detail")
    if isinstance(raw_detail, str):
        detail: dict[str, Any] = json.loads(raw_detail)
    elif isinstance(raw_detail, dict):
        detail = raw_detail
    else:
        detail = {}

    return AuditEntry(
        id=AuditId(row["audit_id"]),
        tenant_id=TenantId(row["tenant_id"]),
        scope=scope,
        operation=AuditOperation(row["operation"]),
        memory_id=MemoryId(row["memory_id"]) if row.get("memory_id") else None,
        actor=row["actor"],
        timestamp=row["occurred_at"],
        outcome=AuditOutcome(row["outcome"]),
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class PostgresAuditLogRepository:
    """asyncpg-backed implementation of :class:`AuditLogPort`.

    Rows are append-only — the ``trg_audit_log_immutable`` trigger prevents
    any UPDATE or DELETE at the database level.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def append(self, entry: AuditEntry) -> None:
        """INSERT an audit entry; trigger enforces immutability."""
        detail_json = json.dumps(entry.detail) if entry.detail else None
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_log (
                    audit_id, tenant_id, memory_id,
                    operation, actor, outcome, detail, occurred_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
                """,
                str(entry.id),
                str(entry.tenant_id),
                str(entry.memory_id) if entry.memory_id else "",
                str(entry.operation),
                entry.actor,
                str(entry.outcome),
                detail_json,
                entry.timestamp,
            )

    async def get_by_memory_id(
        self, memory_id: MemoryId, tenant_id: TenantId
    ) -> list[AuditEntry]:
        """Return all entries for *memory_id* scoped to *tenant_id*, oldest first."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM audit_log
                WHERE memory_id = $1 AND tenant_id = $2
                ORDER BY occurred_at ASC
                """,
                str(memory_id),
                str(tenant_id),
            )
        return [_row_to_entry(dict(r)) for r in rows]

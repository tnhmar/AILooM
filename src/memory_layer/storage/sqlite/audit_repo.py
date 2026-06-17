"""SQLite adapter for AuditLogPort.

Design notes
------------
- ``append`` is INSERT-only — audit entries are immutable once written.
- ``detail`` dict is serialised as a JSON text column.
- ``timestamp`` is stored as ISO-8601 UTC string and restored as an
  aware ``datetime`` object.
- ``get_by_memory_id`` results are ordered by ``timestamp ASC`` so callers
  see the chronological audit trail.
- Tenant isolation is enforced in ``get_by_memory_id``: if *any* row for
  ``memory_id`` belongs to a different tenant a ``TenantIsolationViolation``
  is raised before returning results.
- The ``scope`` object is not persisted in its own columns — only the
  ``principal_id`` drawn from it is stored, matching the V1 DDL.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from memory_layer.domain.exceptions import StorageError, TenantIsolationViolation
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


def _dt_to_str(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def _str_to_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


class SqliteAuditLog:
    """SQLite-backed append-only audit log.

    Parameters
    ----------
    db_path:
        Path to the SQLite file, or ``":memory:"`` for an in-process database.
        The V1 schema must already be applied before instantiation.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # Internal mapping helpers
    # ------------------------------------------------------------------

    def _entry_to_row(self, entry: AuditEntry) -> dict[str, Any]:
        """Flatten an :class:`AuditEntry` to a dict for DB insertion."""
        return {
            "id": str(entry.id),
            "tenant_id": str(entry.tenant_id),
            "principal_id": str(entry.scope.principal_id),
            "operation": str(entry.operation),
            "memory_id": str(entry.memory_id) if entry.memory_id else None,
            "actor": entry.actor,
            "timestamp": _dt_to_str(entry.timestamp),
            "outcome": str(entry.outcome),
            "detail": json.dumps(entry.detail),
        }

    def _row_to_entry(self, row: dict[str, Any]) -> AuditEntry:
        """Reconstruct an :class:`AuditEntry` from a flat DB row dict."""
        scope = Scope(
            tenant_id=TenantId(row["tenant_id"]),
            principal_id=PrincipalId(row["principal_id"]),
            principal_type=PrincipalType.USER,
        )
        return AuditEntry(
            id=AuditId(row["id"]),
            tenant_id=TenantId(row["tenant_id"]),
            scope=scope,
            operation=AuditOperation(row["operation"]),
            memory_id=MemoryId(row["memory_id"]) if row["memory_id"] else None,
            actor=row["actor"],
            timestamp=_str_to_dt(row["timestamp"]),
            outcome=AuditOutcome(row["outcome"]),
            detail=json.loads(row["detail"]) if row["detail"] else {},
        )

    # ------------------------------------------------------------------
    # Port implementation
    # ------------------------------------------------------------------

    async def append(self, entry: AuditEntry) -> None:
        """Insert an audit entry.  Raises :exc:`StorageError` on DB errors.

        Audit entries are immutable: this method uses a plain ``INSERT``
        (not ``INSERT OR REPLACE``) so duplicate IDs surface as DB errors
        rather than silently overwriting history.
        """
        row = self._entry_to_row(entry)
        sql = """
            INSERT INTO audit_log (
                id, tenant_id, principal_id, operation,
                memory_id, actor, timestamp, outcome, detail
            ) VALUES (
                :id, :tenant_id, :principal_id, :operation,
                :memory_id, :actor, :timestamp, :outcome, :detail
            )
        """
        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("PRAGMA foreign_keys=ON")
                await db.execute(sql, row)
                await db.commit()
        except Exception as exc:
            raise StorageError(f"append failed for audit_id={entry.id}: {exc}") from exc

    async def get_by_memory_id(
        self,
        memory_id: MemoryId,
        tenant_id: TenantId,
    ) -> list[AuditEntry]:
        """Return all audit entries for *memory_id*, ordered by timestamp ASC.

        Returns an empty list when no entries exist.

        Raises
        ------
        TenantIsolationViolation
            If any stored row for *memory_id* belongs to a different tenant.
        StorageError
            On unexpected database errors.
        """
        sql = """
            SELECT * FROM audit_log
             WHERE memory_id = ?
             ORDER BY timestamp ASC
        """
        try:
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys=ON")
                async with db.execute(sql, (str(memory_id),)) as cursor:
                    rows = await cursor.fetchall()
        except Exception as exc:
            raise StorageError(
                f"get_by_memory_id failed for memory_id={memory_id}: {exc}"
            ) from exc

        if not rows:
            return []

        # Tenant isolation check: all rows must belong to the requesting tenant.
        for raw in rows:
            if raw["tenant_id"] != str(tenant_id):
                raise TenantIsolationViolation(
                    actor=str(tenant_id),
                    requested_tenant_id=raw["tenant_id"],
                )

        return [self._row_to_entry(dict(r)) for r in rows]

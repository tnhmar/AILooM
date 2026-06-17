"""SQLite adapter for MemoryRecordRepositoryPort.

Design notes
------------
- All queries are parameterised; no string interpolation of user data.
- ``metadata`` is stored as a JSON text column and round-tripped via
  ``json.dumps`` / ``json.loads``.
- All ``datetime`` fields are stored as ISO-8601 UTC strings
  (``datetime.isoformat()``) and parsed back with
  ``datetime.fromisoformat()``.
- Tenant isolation is enforced in every read: if the row's ``tenant_id``
  does not match the requested ``tenant_id`` a ``TenantIsolationViolation``
  is raised.
- ``save`` uses ``INSERT OR REPLACE`` so that re-saving an unchanged record
  is idempotent.
- ``update_lifecycle`` and ``update_pipeline_status`` both include
  ``WHERE tenant_id = ?`` so they can never mutate a foreign-tenant row.
  They also verify the row exists (and belongs to the tenant) before
  writing, raising ``TenantIsolationViolation`` on mismatch and
  ``StorageError`` if the record is missing entirely.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from memory_layer.domain.exceptions import StorageError, TenantIsolationViolation
from memory_layer.domain.records import MemoryRecord, Scope
from memory_layer.domain.types import (
    LifecycleState,
    MemoryId,
    MemorySector,
    PayloadType,
    PipelineStatus,
    PrincipalId,
    PrincipalType,
    RunId,
    SessionId,
    TenantId,
    WorkspaceId,
)


def _dt_to_str(dt: datetime) -> str:
    """Serialise a datetime to an ISO-8601 UTC string."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _str_to_dt(value: str) -> datetime:
    """Deserialise an ISO-8601 string to a timezone-aware UTC datetime."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class SqliteMemoryRecordRepository:
    """SQLite-backed repository for :class:`~memory_layer.domain.records.MemoryRecord`.

    Parameters
    ----------
    db_path:
        Path to the SQLite file, or ``":memory:"`` for an in-process database.
        The schema must already be applied (via :func:`ensure_schema`) before
        instantiating this class.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # Internal mapping helpers
    # ------------------------------------------------------------------

    def _record_to_row(self, record: MemoryRecord) -> dict[str, Any]:
        """Convert a :class:`MemoryRecord` to a flat dict for DB insertion."""
        scope = record.scope
        return {
            "id": str(record.id),
            "tenant_id": str(record.tenant_id),
            "principal_id": str(scope.principal_id),
            "principal_type": str(scope.principal_type),
            "workspace_id": str(scope.workspace_id) if scope.workspace_id else None,
            "session_id": str(scope.session_id) if scope.session_id else None,
            "run_id": str(scope.run_id) if scope.run_id else None,
            "raw_payload": record.raw_payload,
            "payload_type": str(record.payload_type),
            "sector": str(record.sector),
            "lifecycle_state": str(record.lifecycle_state),
            "pipeline_status": str(record.pipeline_status),
            "recorded_at": _dt_to_str(record.recorded_at),
            "idempotency_key": record.idempotency_key,
            "metadata": json.dumps(record.metadata),
        }

    def _row_to_record(self, row: dict[str, Any]) -> MemoryRecord:
        """Reconstruct a :class:`MemoryRecord` from a flat DB row dict."""
        scope = Scope(
            tenant_id=TenantId(row["tenant_id"]),
            principal_id=PrincipalId(row["principal_id"]),
            principal_type=PrincipalType(row["principal_type"]),
            workspace_id=WorkspaceId(row["workspace_id"]) if row["workspace_id"] else None,
            session_id=SessionId(row["session_id"]) if row["session_id"] else None,
            run_id=RunId(row["run_id"]) if row["run_id"] else None,
        )
        return MemoryRecord(
            id=MemoryId(row["id"]),
            tenant_id=TenantId(row["tenant_id"]),
            scope=scope,
            raw_payload=row["raw_payload"],
            payload_type=PayloadType(row["payload_type"]),
            sector=MemorySector(row["sector"]),
            lifecycle_state=LifecycleState(row["lifecycle_state"]),
            pipeline_status=PipelineStatus(row["pipeline_status"]),
            recorded_at=_str_to_dt(row["recorded_at"]),
            idempotency_key=row["idempotency_key"] or None,
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )

    # ------------------------------------------------------------------
    # Port implementation
    # ------------------------------------------------------------------

    async def save(self, record: MemoryRecord) -> None:
        """Persist a :class:`MemoryRecord`.  Idempotent: re-saving the same
        record (same ``id``) replaces the existing row.

        Raises
        ------
        StorageError
            On unexpected database errors.
        """
        row = self._record_to_row(record)
        sql = """
            INSERT OR REPLACE INTO memory_records (
                id, tenant_id, principal_id, principal_type,
                workspace_id, session_id, run_id,
                raw_payload, payload_type, sector,
                lifecycle_state, pipeline_status,
                recorded_at, idempotency_key, metadata
            ) VALUES (
                :id, :tenant_id, :principal_id, :principal_type,
                :workspace_id, :session_id, :run_id,
                :raw_payload, :payload_type, :sector,
                :lifecycle_state, :pipeline_status,
                :recorded_at, :idempotency_key, :metadata
            )
        """
        try:
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys=ON")
                await db.execute(sql, row)
                await db.commit()
        except Exception as exc:
            raise StorageError(f"save failed for memory_id={record.id}: {exc}") from exc

    async def get_by_id(
        self,
        memory_id: MemoryId,
        tenant_id: TenantId,
    ) -> MemoryRecord | None:
        """Fetch a record by ID.

        Returns ``None`` when the record does not exist.

        Raises
        ------
        TenantIsolationViolation
            If the stored row belongs to a different tenant.
        StorageError
            On unexpected database errors.
        """
        sql = "SELECT * FROM memory_records WHERE id = ?"
        try:
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys=ON")
                async with db.execute(sql, (str(memory_id),)) as cursor:
                    raw = await cursor.fetchone()
        except Exception as exc:
            raise StorageError(f"get_by_id failed for memory_id={memory_id}: {exc}") from exc

        if raw is None:
            return None

        row = dict(raw)
        if row["tenant_id"] != str(tenant_id):
            raise TenantIsolationViolation(
                actor=str(tenant_id),
                requested_tenant_id=row["tenant_id"],
            )
        return self._row_to_record(row)

    async def update_lifecycle(
        self,
        memory_id: MemoryId,
        tenant_id: TenantId,
        state: LifecycleState,
        actor: str,  # noqa: ARG002  (reserved for future audit integration)
    ) -> None:
        """Transition the ``lifecycle_state`` of a record.

        Raises
        ------
        TenantIsolationViolation
            If no row matches ``(memory_id, tenant_id)`` but the record
            exists under a different tenant.
        StorageError
            If the record does not exist at all, or on unexpected DB errors.
        """
        await self._assert_belongs_to_tenant(memory_id, tenant_id)
        sql = """
            UPDATE memory_records
               SET lifecycle_state = ?
             WHERE id = ? AND tenant_id = ?
        """
        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("PRAGMA foreign_keys=ON")
                await db.execute(sql, (str(state), str(memory_id), str(tenant_id)))
                await db.commit()
        except Exception as exc:
            raise StorageError(
                f"update_lifecycle failed for memory_id={memory_id}: {exc}"
            ) from exc

    async def update_pipeline_status(
        self,
        memory_id: MemoryId,
        tenant_id: TenantId,
        status: PipelineStatus,
    ) -> None:
        """Update the ``pipeline_status`` of a record.

        Raises
        ------
        TenantIsolationViolation
            If the record belongs to a different tenant.
        StorageError
            If the record does not exist at all, or on unexpected DB errors.
        """
        await self._assert_belongs_to_tenant(memory_id, tenant_id)
        sql = """
            UPDATE memory_records
               SET pipeline_status = ?
             WHERE id = ? AND tenant_id = ?
        """
        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("PRAGMA foreign_keys=ON")
                await db.execute(sql, (str(status), str(memory_id), str(tenant_id)))
                await db.commit()
        except Exception as exc:
            raise StorageError(
                f"update_pipeline_status failed for memory_id={memory_id}: {exc}"
            ) from exc

    async def list_by_scope(
        self,
        scope: Scope,
        lifecycle_states: list[LifecycleState],
        limit: int = 100,
    ) -> list[MemoryRecord]:
        """Return records matching *scope* and any of *lifecycle_states*.

        Results are ordered by ``recorded_at DESC``.

        Raises
        ------
        StorageError
            On unexpected database errors.
        """
        if not lifecycle_states:
            return []

        placeholders = ",".join("?" * len(lifecycle_states))
        sql = f"""
            SELECT * FROM memory_records
             WHERE tenant_id      = ?
               AND principal_id   = ?
               AND lifecycle_state IN ({placeholders})
             ORDER BY recorded_at DESC
             LIMIT ?
        """  # noqa: S608  (not an injection — placeholders used for states)

        params: list[Any] = [
            str(scope.tenant_id),
            str(scope.principal_id),
            *[str(s) for s in lifecycle_states],
            limit,
        ]

        try:
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys=ON")
                async with db.execute(sql, params) as cursor:
                    rows = await cursor.fetchall()
        except Exception as exc:
            raise StorageError(f"list_by_scope failed: {exc}") from exc

        return [self._row_to_record(dict(r)) for r in rows]

    async def get_by_idempotency_key(
        self,
        key: str,
        tenant_id: TenantId,
    ) -> MemoryRecord | None:
        """Look up a record by its idempotency key within a tenant.

        Returns ``None`` when no match exists.

        Raises
        ------
        StorageError
            On unexpected database errors.
        """
        sql = """
            SELECT * FROM memory_records
             WHERE tenant_id = ? AND idempotency_key = ?
             LIMIT 1
        """
        try:
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys=ON")
                async with db.execute(sql, (str(tenant_id), key)) as cursor:
                    raw = await cursor.fetchone()
        except Exception as exc:
            raise StorageError(
                f"get_by_idempotency_key failed for key={key!r}: {exc}"
            ) from exc

        if raw is None:
            return None
        return self._row_to_record(dict(raw))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _assert_belongs_to_tenant(
        self,
        memory_id: MemoryId,
        tenant_id: TenantId,
    ) -> None:
        """Raise ``StorageError`` if the record does not exist, or
        ``TenantIsolationViolation`` if it exists under a different tenant.
        """
        sql = "SELECT tenant_id FROM memory_records WHERE id = ?"
        try:
            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute(sql, (str(memory_id),)) as cursor:
                    raw = await cursor.fetchone()
        except Exception as exc:
            raise StorageError(
                f"tenant check failed for memory_id={memory_id}: {exc}"
            ) from exc

        if raw is None:
            raise StorageError(f"MemoryRecord not found: {memory_id}")
        if raw[0] != str(tenant_id):
            raise TenantIsolationViolation(
                actor=str(tenant_id),
                requested_tenant_id=raw[0],
            )

"""SQLite adapter for FactRepositoryPort.

Design notes
------------
- All queries are parameterised; no string interpolation of user data.
- ``close_fact`` is fully transactional: both the ``effective_to`` update on
  the old fact and the ``supersedes`` update on the new fact are committed
  atomically.  If either row is missing or the DB raises, the connection is
  rolled back and the exception is re-raised.
- ``get_active_facts_by_entity_predicate`` enforces the open-world temporal
  model: a fact is "current" only when ``lifecycle_state = 'ACTIVE'`` AND
  ``effective_to IS NULL``.
- Tenant isolation is checked on every read: a row whose ``tenant_id`` does
  not match the caller's ``tenant_id`` raises ``TenantIsolationViolation``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import aiosqlite

from memory_layer.domain.exceptions import (
    FactNotFoundError,
    StorageError,
    TenantIsolationViolation,
)
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


def _dt_to_str(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def _str_to_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


class SqliteFactRepository:
    """SQLite-backed repository for :class:`~memory_layer.domain.records.Fact`.

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

    def _fact_to_row(self, fact: Fact) -> dict[str, Any]:
        """Convert a :class:`Fact` to a flat dict suitable for DB insertion."""
        scope = fact.scope
        return {
            "id": str(fact.id),
            "memory_record_id": str(fact.memory_record_id),
            "tenant_id": str(fact.tenant_id),
            "principal_id": str(scope.principal_id),
            "subject_entity_id": str(fact.subject_entity_id),
            "predicate": fact.predicate,
            "predicate_group": fact.predicate_group,
            "object_value": fact.object_value,
            "effective_from": _dt_to_str(fact.effective_from),
            "effective_to": _dt_to_str(fact.effective_to) if fact.effective_to else None,
            "recorded_at": _dt_to_str(fact.recorded_at),
            "supersedes": str(fact.supersedes) if fact.supersedes else None,
            "confidence": fact.confidence,
            "sector": str(fact.sector),
            "lifecycle_state": str(fact.lifecycle_state),
        }

    def _row_to_fact(self, row: dict[str, Any]) -> Fact:
        """Reconstruct a :class:`Fact` from a flat DB row dict."""
        scope = Scope(
            tenant_id=TenantId(row["tenant_id"]),
            principal_id=PrincipalId(row["principal_id"]),
            principal_type=PrincipalType.USER,
        )
        return Fact(
            id=FactId(row["id"]),
            memory_record_id=MemoryId(row["memory_record_id"]),
            tenant_id=TenantId(row["tenant_id"]),
            scope=scope,
            subject_entity_id=EntityId(row["subject_entity_id"]),
            predicate=row["predicate"],
            predicate_group=row["predicate_group"],
            object_value=row["object_value"],
            effective_from=_str_to_dt(row["effective_from"]),
            effective_to=_str_to_dt(row["effective_to"]) if row["effective_to"] else None,
            recorded_at=_str_to_dt(row["recorded_at"]),
            supersedes=FactId(row["supersedes"]) if row["supersedes"] else None,
            confidence=float(row["confidence"]),
            sector=MemorySector(row["sector"]),
            lifecycle_state=LifecycleState(row["lifecycle_state"]),
        )

    # ------------------------------------------------------------------
    # Port implementation
    # ------------------------------------------------------------------

    async def save(self, fact: Fact) -> None:
        """Persist a :class:`Fact`.  Idempotent: re-saving replaces the row.

        Raises
        ------
        StorageError
            On unexpected database errors.
        """
        row = self._fact_to_row(fact)
        sql = """
            INSERT OR REPLACE INTO facts (
                id, memory_record_id, tenant_id, principal_id,
                subject_entity_id, predicate, predicate_group, object_value,
                effective_from, effective_to, recorded_at,
                supersedes, confidence, sector, lifecycle_state
            ) VALUES (
                :id, :memory_record_id, :tenant_id, :principal_id,
                :subject_entity_id, :predicate, :predicate_group, :object_value,
                :effective_from, :effective_to, :recorded_at,
                :supersedes, :confidence, :sector, :lifecycle_state
            )
        """
        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("PRAGMA foreign_keys=ON")
                await db.execute(sql, row)
                await db.commit()
        except Exception as exc:
            raise StorageError(f"save failed for fact_id={fact.id}: {exc}") from exc

    async def get_by_id(
        self,
        fact_id: FactId,
        tenant_id: TenantId,
    ) -> Fact | None:
        """Fetch a fact by its ID.

        Returns ``None`` when no row with *fact_id* exists.

        Raises
        ------
        TenantIsolationViolation
            If the stored row belongs to a different tenant.
        StorageError
            On unexpected database errors.
        """
        sql = "SELECT * FROM facts WHERE id = ?"
        try:
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys=ON")
                async with db.execute(sql, (str(fact_id),)) as cursor:
                    raw = await cursor.fetchone()
        except Exception as exc:
            raise StorageError(f"get_by_id failed for fact_id={fact_id}: {exc}") from exc

        if raw is None:
            return None

        row = dict(raw)
        if row["tenant_id"] != str(tenant_id):
            raise TenantIsolationViolation(
                actor=str(tenant_id),
                requested_tenant_id=row["tenant_id"],
            )
        return self._row_to_fact(row)

    async def close_fact(
        self,
        fact_id: FactId,
        tenant_id: TenantId,
        effective_to: datetime,
        new_fact_id: FactId,
    ) -> None:
        """Atomically close an old fact and link the replacement.

        Within a single transaction:

        1. Verify ``fact_id`` exists and belongs to ``tenant_id``.
        2. Set ``effective_to`` on the old fact row.
        3. Set ``supersedes = fact_id`` on the new fact row (``new_fact_id``).

        The entire transaction is rolled back if either update fails.

        Raises
        ------
        FactNotFoundError
            If ``fact_id`` does not exist in the database.
        TenantIsolationViolation
            If ``fact_id`` belongs to a different tenant.
        StorageError
            If ``new_fact_id`` does not exist, or on unexpected DB errors.
        """
        effective_to_str = _dt_to_str(effective_to)

        try:
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys=ON")

                # --- Step 1: verify old fact exists and belongs to tenant ---
                async with db.execute(
                    "SELECT tenant_id FROM facts WHERE id = ?", (str(fact_id),)
                ) as cursor:
                    row = await cursor.fetchone()

                if row is None:
                    raise FactNotFoundError(str(fact_id))
                if row["tenant_id"] != str(tenant_id):
                    raise TenantIsolationViolation(
                        actor=str(tenant_id),
                        requested_tenant_id=row["tenant_id"],
                    )

                # --- Step 2 + 3: both updates in one transaction -----------
                await db.execute("BEGIN")
                try:
                    # Close old fact
                    await db.execute(
                        "UPDATE facts SET effective_to = ? WHERE id = ? AND tenant_id = ?",
                        (effective_to_str, str(fact_id), str(tenant_id)),
                    )
                    # Link supersedes on new fact — raises if new_fact_id absent
                    result = await db.execute(
                        "UPDATE facts SET supersedes = ? WHERE id = ? AND tenant_id = ?",
                        (str(fact_id), str(new_fact_id), str(tenant_id)),
                    )
                    if result.rowcount == 0:
                        raise StorageError(
                            f"new_fact_id={new_fact_id!r} does not exist or belongs to "
                            "a different tenant; rolling back close_fact"
                        )
                    await db.commit()
                except (FactNotFoundError, TenantIsolationViolation, StorageError):
                    await db.rollback()
                    raise
                except Exception as exc:
                    await db.rollback()
                    raise StorageError(
                        f"close_fact failed mid-transaction: {exc}"
                    ) from exc

        except (FactNotFoundError, TenantIsolationViolation, StorageError):
            raise
        except Exception as exc:
            raise StorageError(f"close_fact failed for fact_id={fact_id}: {exc}") from exc

    async def get_active_facts_by_entity_predicate(
        self,
        entity_id: EntityId,
        predicate_group: str,
        tenant_id: TenantId,
    ) -> list[Fact]:
        """Return facts that are currently active and have no ``effective_to``.

        A fact is "current" only when ``lifecycle_state = 'ACTIVE'`` AND
        ``effective_to IS NULL``.

        Raises
        ------
        StorageError
            On unexpected database errors.
        """
        sql = """
            SELECT * FROM facts
             WHERE tenant_id          = ?
               AND subject_entity_id  = ?
               AND predicate_group    = ?
               AND lifecycle_state    = 'ACTIVE'
               AND effective_to       IS NULL
             ORDER BY recorded_at DESC
        """
        try:
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys=ON")
                async with db.execute(
                    sql, (str(tenant_id), str(entity_id), predicate_group)
                ) as cursor:
                    rows = await cursor.fetchall()
        except Exception as exc:
            raise StorageError(
                f"get_active_facts_by_entity_predicate failed: {exc}"
            ) from exc

        return [self._row_to_fact(dict(r)) for r in rows]

    async def list_by_memory_record(
        self,
        memory_record_id: MemoryId,
        tenant_id: TenantId,
    ) -> list[Fact]:
        """Return all facts derived from *memory_record_id*, ordered by
        ``recorded_at DESC``.

        Returns an empty list when no facts are found.

        Raises
        ------
        StorageError
            On unexpected database errors.
        """
        sql = """
            SELECT * FROM facts
             WHERE memory_record_id = ?
               AND tenant_id        = ?
             ORDER BY recorded_at DESC
        """
        try:
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys=ON")
                async with db.execute(
                    sql, (str(memory_record_id), str(tenant_id))
                ) as cursor:
                    rows = await cursor.fetchall()
        except Exception as exc:
            raise StorageError(
                f"list_by_memory_record failed for memory_record_id={memory_record_id}: {exc}"
            ) from exc

        return [self._row_to_fact(dict(r)) for r in rows]

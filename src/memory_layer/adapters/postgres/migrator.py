"""Lightweight schema migration runner for memory-layer.

Design goals:
- No Alembic *runtime* dependency — uses asyncpg directly.
- Maintains a ``schema_migrations`` table as the source of truth.
- Each migration version is applied exactly once inside its own transaction.
- Emits :class:`~memory_layer.domain.events.SchemaMigratedEvent` per applied version.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import ClassVar

import asyncpg

from memory_layer.adapters.postgres.audit_repo import AUDIT_LOG_DDL
from memory_layer.adapters.postgres.fact_repo import FACTS_DDL
from memory_layer.adapters.postgres.record_repo import MEMORY_RECORDS_DDL
from memory_layer.adapters.postgres.trace_repo import TRACES_DDL
from memory_layer.adapters.postgres.vector_index import VECTOR_INDEX_DDL
from memory_layer.domain.events import SchemaMigratedEvent
from memory_layer.domain.types import TenantId
from memory_layer.ports.outbound import ObserverPort

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class MigrationResult:
    """Summary returned by :meth:`SchemaMigrator.run`."""

    applied: list[str] = field(default_factory=list)   # versions applied this run
    skipped: list[str] = field(default_factory=list)   # versions already present
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# Migrator
# ---------------------------------------------------------------------------

_MIGRATIONS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT        PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

# Concatenate all DDL blocks in the correct dependency order.
_INITIAL_DDL = "\n".join([
    MEMORY_RECORDS_DDL,
    FACTS_DDL,
    AUDIT_LOG_DDL,
    TRACES_DDL,
    VECTOR_INDEX_DDL,
])


class SchemaMigrator:
    """Apply pending schema migrations and emit a :class:`SchemaMigratedEvent`
    for each one.

    Parameters
    ----------
    pool:
        Active asyncpg connection pool.
    observer:
        Domain event sink; receives a :class:`SchemaMigratedEvent` per applied
        migration version.
    tenant_id:
        Tenant context for emitted events (defaults to ``"system"``).
    """

    MIGRATIONS: ClassVar[list[tuple[str, str]]] = [
        ("0001_initial", _INITIAL_DDL),
        # Future migrations: ("0002_add_column", "ALTER TABLE ..."),
    ]

    def __init__(
        self,
        pool: asyncpg.Pool,
        observer: ObserverPort,
        tenant_id: str = "system",
    ) -> None:
        self._pool = pool
        self._observer = observer
        self._tenant_id = TenantId(tenant_id)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> MigrationResult:
        """Apply all pending migrations; return a :class:`MigrationResult`."""
        start = time.monotonic()
        result = MigrationResult()

        await self._ensure_migrations_table()

        for idx, (version, ddl) in enumerate(self.MIGRATIONS):
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    if await self._is_applied(conn, version):
                        result.skipped.append(version)
                        continue
                    await self._apply(conn, version, ddl)
                    result.applied.append(version)

            # Emit event outside the transaction (best-effort; not rolled back on failure).
            event = SchemaMigratedEvent(
                tenant_id=self._tenant_id,
                from_version=idx,
                to_version=idx + 1,
            )
            await self._observer.emit(event)

        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _ensure_migrations_table(self) -> None:
        """Create ``schema_migrations`` if it does not already exist."""
        async with self._pool.acquire() as conn:
            await conn.execute(_MIGRATIONS_TABLE_DDL)

    async def _is_applied(self, conn: asyncpg.Connection, version: str) -> bool:  # type: ignore[type-arg]
        """Return True if *version* is already recorded in ``schema_migrations``."""
        row = await conn.fetchrow(
            "SELECT version FROM schema_migrations WHERE version = $1",
            version,
        )
        return row is not None

    async def _apply(self, conn: asyncpg.Connection, version: str, ddl: str) -> None:  # type: ignore[type-arg]
        """Execute *ddl* and record *version* in ``schema_migrations``."""
        await conn.execute(ddl)
        await conn.execute(
            "INSERT INTO schema_migrations (version) VALUES ($1)",
            version,
        )

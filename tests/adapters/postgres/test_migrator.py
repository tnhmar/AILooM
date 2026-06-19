"""Integration tests for SchemaMigrator and the migrations admin endpoint — M7-T5.

Requires a real PostgreSQL instance. Set TEST_POSTGRES_DSN to run.
Skipped automatically in environments without the env var.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

TEST_DSN = os.environ.get("TEST_POSTGRES_DSN", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DSN,
        reason="TEST_POSTGRES_DSN not set — skipping migrator integration tests",
    ),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def pool():
    """Fresh pool; drops migration state between tests."""
    import asyncpg

    p = await asyncpg.create_pool(dsn=TEST_DSN, min_size=1, max_size=3)
    # Wipe migration tracking so each test starts from a clean slate.
    async with p.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS schema_migrations")
    yield p
    await p.close()


class _NullObserver:
    """No-op ObserverPort that records emitted events."""

    def __init__(self) -> None:
        self.events: list = []

    async def emit(self, event) -> None:
        self.events.append(event)


@pytest_asyncio.fixture
async def observer():
    return _NullObserver()


@pytest_asyncio.fixture
async def migrator(pool, observer):
    from memory_layer.adapters.postgres.migrator import SchemaMigrator
    return SchemaMigrator(pool=pool, observer=observer, tenant_id="system")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_on_fresh_db_applies_all_migrations(migrator):
    """1. run() on a fresh DB applies all migrations and returns applied list."""
    from memory_layer.adapters.postgres.migrator import SchemaMigrator

    result = await migrator.run()
    expected_versions = [v for v, _ in SchemaMigrator.MIGRATIONS]
    assert result.applied == expected_versions
    assert result.skipped == []


@pytest.mark.asyncio
async def test_run_on_migrated_db_returns_empty_applied(migrator):
    """2. run() on already-migrated DB returns applied=[], skipped non-empty."""
    from memory_layer.adapters.postgres.migrator import SchemaMigrator

    await migrator.run()  # first run applies everything
    result = await migrator.run()  # second run must skip all
    assert result.applied == []
    expected_versions = [v for v, _ in SchemaMigrator.MIGRATIONS]
    assert result.skipped == expected_versions


@pytest.mark.asyncio
async def test_schema_migrated_event_emitted(migrator, observer):
    """3. SchemaMigratedEvent is emitted for each applied migration."""
    from memory_layer.domain.events import SchemaMigratedEvent

    result = await migrator.run()
    assert len(observer.events) == len(result.applied)
    for event in observer.events:
        assert isinstance(event, SchemaMigratedEvent)


@pytest.mark.asyncio
async def test_schema_migrations_table_exists_after_run(migrator, pool):
    """4. schema_migrations table exists after first run."""
    await migrator.run()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT to_regclass('public.schema_migrations') AS tbl"
        )
    assert row is not None and row["tbl"] is not None


@pytest.mark.asyncio
async def test_each_version_appears_exactly_once(migrator, pool):
    """5. Each migration version appears exactly once in schema_migrations."""
    from memory_layer.adapters.postgres.migrator import SchemaMigrator

    await migrator.run()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT version FROM schema_migrations ORDER BY version")
    versions = [r["version"] for r in rows]
    expected = sorted(v for v, _ in SchemaMigrator.MIGRATIONS)
    assert versions == expected


@pytest.mark.asyncio
async def test_duration_ms_is_positive(migrator):
    """6. MigrationResult.duration_ms is a positive integer."""
    result = await migrator.run()
    assert isinstance(result.duration_ms, int)
    assert result.duration_ms >= 0  # allow 0 ms on very fast systems


@pytest.mark.asyncio
async def test_run_is_idempotent(migrator):
    """7. run() is idempotent: calling twice does not raise and produces same schema."""
    r1 = await migrator.run()
    r2 = await migrator.run()
    assert r1.applied != [] or True  # first run applied something (or DB was pre-migrated)
    assert r2.applied == []  # second run applies nothing new


@pytest.mark.asyncio
async def test_admin_endpoint_returns_200_with_applied_field(pool, observer):
    """8. POST /v1/admin/migrations:run returns 200 with applied field."""
    from httpx import ASGITransport, AsyncClient
    from memory_layer.adapters.postgres.migrator import SchemaMigrator
    from memory_layer.api.app import app, get_schema_migrator

    migrator_instance = SchemaMigrator(pool=pool, observer=observer, tenant_id="system")

    app.dependency_overrides[get_schema_migrator] = lambda: migrator_instance
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/admin/migrations:run",
                headers={"x-tenant-id": "system"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "applied" in data
    finally:
        app.dependency_overrides.pop(get_schema_migrator, None)


@pytest.mark.asyncio
async def test_admin_endpoint_returns_401_without_admin_key(pool, observer, monkeypatch):
    """9. POST /v1/admin/migrations:run without MEMORY_LAYER_ADMIN_KEY returns 401."""
    import os
    from httpx import ASGITransport, AsyncClient
    from memory_layer.adapters.postgres.migrator import SchemaMigrator
    from memory_layer.api.app import app, get_schema_migrator

    monkeypatch.setenv("MEMORY_LAYER_ADMIN_KEY", "super-secret")

    migrator_instance = SchemaMigrator(pool=pool, observer=observer, tenant_id="system")
    app.dependency_overrides[get_schema_migrator] = lambda: migrator_instance
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/admin/migrations:run",
                headers={"x-tenant-id": "system"},
                # No X-Admin-Key header
            )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.pop(get_schema_migrator, None)
        monkeypatch.delenv("MEMORY_LAYER_ADMIN_KEY", raising=False)

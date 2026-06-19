"""Integration tests for PostgresAuditLogRepository — M7-T2 (5 tests).

Requires a real PostgreSQL instance. Set TEST_POSTGRES_DSN to run.
Skipped automatically in environments without the env var.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
import pytest_asyncio

TEST_DSN = os.environ.get("TEST_POSTGRES_DSN", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DSN,
        reason="TEST_POSTGRES_DSN not set — skipping PostgreSQL integration tests",
    ),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def pool():
    import asyncpg
    from memory_layer.adapters.postgres.audit_repo import AUDIT_LOG_DDL

    p = await asyncpg.create_pool(dsn=TEST_DSN, min_size=1, max_size=3)
    async with p.acquire() as conn:
        await conn.execute(AUDIT_LOG_DDL)
    yield p
    # Remove the immutability trigger before teardown so we can clean up
    async with p.acquire() as conn:
        await conn.execute("DROP TRIGGER IF EXISTS trg_audit_log_immutable ON audit_log")
        await conn.execute("DELETE FROM audit_log WHERE tenant_id LIKE 'tenant-%'")
    await p.close()


@pytest_asyncio.fixture
async def repo(pool):
    from memory_layer.adapters.postgres.audit_repo import PostgresAuditLogRepository
    return PostgresAuditLogRepository(pool)


def _make_entry(
    audit_id: str = "audit-001",
    tenant_id: str = "tenant-A",
    memory_id: str = "mem-001",
    actor: str = "test-actor",
):
    from memory_layer.domain.records import AuditEntry, Scope
    from memory_layer.domain.types import (
        AuditId, AuditOperation, AuditOutcome,
        MemoryId, PrincipalId, PrincipalType, TenantId,
    )

    t_id = TenantId(tenant_id)
    scope = Scope(
        tenant_id=t_id,
        principal_id=PrincipalId(actor),
        principal_type=PrincipalType.USER,
    )
    return AuditEntry(
        id=AuditId(audit_id),
        tenant_id=t_id,
        scope=scope,
        operation=AuditOperation.WRITE,
        memory_id=MemoryId(memory_id),
        actor=actor,
        timestamp=datetime.now(timezone.utc),
        outcome=AuditOutcome.SUCCESS,
        detail={"source": "test"},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_append_and_get_by_memory_id(repo):
    """1. append then get_by_memory_id returns the entry."""
    from memory_layer.domain.types import MemoryId, TenantId

    entry = _make_entry(audit_id="audit-t1", memory_id="mem-t1")
    await repo.append(entry)
    results = await repo.get_by_memory_id(MemoryId("mem-t1"), TenantId("tenant-A"))
    assert any(e.id == entry.id for e in results)


@pytest.mark.asyncio
async def test_multiple_appends_ordered_asc(repo):
    """2. Multiple appends return entries ordered by occurred_at ASC."""
    import asyncio
    from memory_layer.domain.types import MemoryId, TenantId

    e1 = _make_entry(audit_id="audit-ord1", memory_id="mem-ord")
    await repo.append(e1)
    await asyncio.sleep(0.01)  # ensure distinct timestamps
    e2 = _make_entry(audit_id="audit-ord2", memory_id="mem-ord")
    await repo.append(e2)

    results = await repo.get_by_memory_id(MemoryId("mem-ord"), TenantId("tenant-A"))
    ids = [e.id for e in results]
    assert ids.index(e1.id) < ids.index(e2.id)


@pytest.mark.asyncio
async def test_cross_tenant_isolation(repo):
    """3. Entry from tenant A is not visible to tenant B."""
    from memory_layer.domain.types import MemoryId, TenantId

    entry_a = _make_entry(audit_id="audit-iso-a", tenant_id="tenant-A", memory_id="mem-iso")
    await repo.append(entry_a)

    results_b = await repo.get_by_memory_id(MemoryId("mem-iso"), TenantId("tenant-B"))
    assert not any(e.id == entry_a.id for e in results_b)


@pytest.mark.asyncio
async def test_update_blocked_by_trigger(pool):
    """4. UPDATE on audit_log raises Postgres exception (trigger test)."""
    import asyncpg
    from memory_layer.adapters.postgres.audit_repo import PostgresAuditLogRepository

    repo = PostgresAuditLogRepository(pool)
    entry = _make_entry(audit_id="audit-upd", memory_id="mem-upd")
    await repo.append(entry)

    with pytest.raises(asyncpg.exceptions.RaiseError):
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE audit_log SET actor = 'hacker' WHERE audit_id = $1 AND tenant_id = $2",
                "audit-upd",
                "tenant-A",
            )


@pytest.mark.asyncio
async def test_delete_blocked_by_trigger(pool):
    """5. DELETE on audit_log raises Postgres exception (trigger test)."""
    import asyncpg
    from memory_layer.adapters.postgres.audit_repo import PostgresAuditLogRepository

    repo = PostgresAuditLogRepository(pool)
    entry = _make_entry(audit_id="audit-del", memory_id="mem-del")
    await repo.append(entry)

    with pytest.raises(asyncpg.exceptions.RaiseError):
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM audit_log WHERE audit_id = $1 AND tenant_id = $2",
                "audit-del",
                "tenant-A",
            )

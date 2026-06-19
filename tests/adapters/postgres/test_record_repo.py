"""Integration tests for PostgresMemoryRecordRepository — M7-T1 (10 tests).

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
    from memory_layer.adapters.postgres.record_repo import MEMORY_RECORDS_DDL

    p = await asyncpg.create_pool(dsn=TEST_DSN, min_size=1, max_size=3)
    async with p.acquire() as conn:
        await conn.execute(MEMORY_RECORDS_DDL)
    yield p
    await p.close()


@pytest_asyncio.fixture
async def repo(pool):
    from memory_layer.adapters.postgres.record_repo import PostgresMemoryRecordRepository
    return PostgresMemoryRecordRepository(pool)


def _make_record(
    memory_id: str = "mem-001",
    tenant_id: str = "tenant-A",
    idempotency_key: str | None = None,
):
    from memory_layer.domain.records import MemoryRecord, Scope
    from memory_layer.domain.types import (
        LifecycleState, MemoryId, MemorySector, PayloadType,
        PipelineStatus, PrincipalId, PrincipalType, TenantId,
    )

    t_id = TenantId(tenant_id)
    scope = Scope(
        tenant_id=t_id,
        principal_id=PrincipalId("user-1"),
        principal_type=PrincipalType.USER,
    )
    return MemoryRecord(
        id=MemoryId(memory_id),
        tenant_id=t_id,
        scope=scope,
        raw_payload="Test payload",
        payload_type=PayloadType.CONVERSATION_TURN,
        sector=MemorySector.EPISODIC,
        lifecycle_state=LifecycleState.ACTIVE,
        pipeline_status=PipelineStatus.PENDING,
        recorded_at=datetime.now(timezone.utc),
        idempotency_key=idempotency_key,
    )


# Tear down between tests to keep state clean
@pytest_asyncio.fixture(autouse=True)
async def cleanup(pool):
    yield
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM memory_records WHERE tenant_id LIKE 'tenant-%'")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_and_get_by_id(repo):
    """1. save then get_by_id returns the saved record."""
    record = _make_record()
    await repo.save(record)
    fetched = await repo.get_by_id(record.id, record.tenant_id)
    assert fetched is not None
    assert fetched.id == record.id
    assert fetched.tenant_id == record.tenant_id
    assert fetched.raw_payload == record.raw_payload


@pytest.mark.asyncio
async def test_save_duplicate_is_noop(repo):
    """2. save with duplicate memory_id is a no-op (no exception)."""
    record = _make_record()
    await repo.save(record)
    await repo.save(record)  # must not raise


@pytest.mark.asyncio
async def test_update_lifecycle_transitions(repo):
    """3. update_lifecycle transitions state correctly."""
    from memory_layer.domain.types import LifecycleState

    record = _make_record()
    await repo.save(record)
    await repo.update_lifecycle(record.id, record.tenant_id, LifecycleState.ARCHIVED, "test")
    fetched = await repo.get_by_id(record.id, record.tenant_id)
    assert fetched is not None
    assert fetched.lifecycle_state == LifecycleState.ARCHIVED


@pytest.mark.asyncio
async def test_update_lifecycle_same_state_noop(repo):
    """4. update_lifecycle to same state is a no-op (no exception)."""
    from memory_layer.domain.types import LifecycleState

    record = _make_record()
    await repo.save(record)
    await repo.update_lifecycle(record.id, record.tenant_id, LifecycleState.ACTIVE, "test")
    fetched = await repo.get_by_id(record.id, record.tenant_id)
    assert fetched is not None
    assert fetched.lifecycle_state == LifecycleState.ACTIVE


@pytest.mark.asyncio
async def test_update_pipeline_status(repo):
    """5. update_pipeline_status changes status."""
    from memory_layer.domain.types import PipelineStatus

    record = _make_record()
    await repo.save(record)
    await repo.update_pipeline_status(record.id, record.tenant_id, PipelineStatus.ENRICHED)
    fetched = await repo.get_by_id(record.id, record.tenant_id)
    assert fetched is not None
    assert fetched.pipeline_status == PipelineStatus.ENRICHED


@pytest.mark.asyncio
async def test_list_by_scope_returns_matching(repo):
    """6. list_by_scope returns only matching tenant+scope records."""
    from memory_layer.domain.types import LifecycleState

    record = _make_record(memory_id="mem-006")
    await repo.save(record)
    results = await repo.list_by_scope(record.scope, [LifecycleState.ACTIVE])
    ids = [r.id for r in results]
    assert record.id in ids


@pytest.mark.asyncio
async def test_list_by_scope_tenant_isolation(repo):
    """7. list_by_scope does not return records from a different tenant."""
    from memory_layer.domain.records import Scope
    from memory_layer.domain.types import (
        LifecycleState, PrincipalId, PrincipalType, TenantId,
    )

    record_a = _make_record(memory_id="mem-007a", tenant_id="tenant-A")
    record_b = _make_record(memory_id="mem-007b", tenant_id="tenant-B")
    await repo.save(record_a)
    await repo.save(record_b)

    scope_a = Scope(
        tenant_id=TenantId("tenant-A"),
        principal_id=PrincipalId("user-1"),
        principal_type=PrincipalType.USER,
    )
    results = await repo.list_by_scope(scope_a, [LifecycleState.ACTIVE])
    ids = [r.id for r in results]
    assert record_a.id in ids
    assert record_b.id not in ids


@pytest.mark.asyncio
async def test_get_by_idempotency_key_found(repo):
    """8. get_by_idempotency_key returns matching record."""
    record = _make_record(memory_id="mem-008", idempotency_key="key-abc")
    await repo.save(record)
    fetched = await repo.get_by_idempotency_key("key-abc", record.tenant_id)
    assert fetched is not None
    assert fetched.id == record.id


@pytest.mark.asyncio
async def test_get_by_idempotency_key_not_found(repo):
    """9. get_by_idempotency_key returns None when key not found."""
    from memory_layer.domain.types import TenantId

    result = await repo.get_by_idempotency_key("nonexistent-key", TenantId("tenant-A"))
    assert result is None


@pytest.mark.asyncio
async def test_get_by_id_unknown_returns_none(repo):
    """10. get_by_id returns None for unknown memory_id."""
    from memory_layer.domain.types import MemoryId, TenantId

    result = await repo.get_by_id(MemoryId("does-not-exist"), TenantId("tenant-A"))
    assert result is None

"""Integration tests for PostgresFactRepository — M7-T1 (6 tests).

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
    from memory_layer.adapters.postgres.fact_repo import FACTS_DDL

    p = await asyncpg.create_pool(dsn=TEST_DSN, min_size=1, max_size=3)
    async with p.acquire() as conn:
        await conn.execute(FACTS_DDL)
    yield p
    await p.close()


@pytest_asyncio.fixture
async def repo(pool):
    from memory_layer.adapters.postgres.fact_repo import PostgresFactRepository
    return PostgresFactRepository(pool)


@pytest_asyncio.fixture(autouse=True)
async def cleanup(pool):
    yield
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM facts WHERE tenant_id LIKE 'tenant-%'")


def _make_fact(
    fact_id: str = "fact-001",
    tenant_id: str = "tenant-A",
    memory_record_id: str = "mem-001",
    subject_entity_id: str = "entity-alice",
    predicate_group: str = "identity",
    effective_to: datetime | None = None,
):
    from memory_layer.domain.records import Fact, Scope
    from memory_layer.domain.types import (
        EntityId, FactId, LifecycleState, MemoryId, MemorySector,
        PrincipalId, PrincipalType, TenantId,
    )

    t_id = TenantId(tenant_id)
    scope = Scope(
        tenant_id=t_id,
        principal_id=PrincipalId("system"),
        principal_type=PrincipalType.AGENT,
    )
    return Fact(
        id=FactId(fact_id),
        memory_record_id=MemoryId(memory_record_id),
        tenant_id=t_id,
        scope=scope,
        subject_entity_id=EntityId(subject_entity_id),
        predicate="name",
        predicate_group=predicate_group,
        object_value="Alice",
        effective_from=datetime.now(timezone.utc),
        effective_to=effective_to,
        confidence=0.95,
        sector=MemorySector.SEMANTIC,
        lifecycle_state=LifecycleState.ACTIVE,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_and_get_by_id(repo):
    """1. save then get_by_id returns the saved fact."""
    fact = _make_fact()
    await repo.save(fact)
    fetched = await repo.get_by_id(fact.id, fact.tenant_id)
    assert fetched is not None
    assert fetched.id == fact.id
    assert fetched.object_value == fact.object_value


@pytest.mark.asyncio
async def test_close_fact(repo):
    """2. close_fact sets effective_to and superseded_by."""
    from memory_layer.domain.types import FactId

    fact = _make_fact(fact_id="fact-close")
    await repo.save(fact)
    new_id = FactId("fact-new")
    close_ts = datetime.now(timezone.utc)
    await repo.close_fact(fact.id, fact.tenant_id, close_ts, new_id)
    fetched = await repo.get_by_id(fact.id, fact.tenant_id)
    assert fetched is not None
    assert fetched.effective_to is not None
    assert fetched.supersedes == new_id


@pytest.mark.asyncio
async def test_get_active_facts_returns_open(repo):
    """3. get_active_facts_by_entity_predicate returns only open facts."""
    from memory_layer.domain.types import EntityId, TenantId

    fact = _make_fact(fact_id="fact-open")
    await repo.save(fact)
    results = await repo.get_active_facts_by_entity_predicate(
        EntityId("entity-alice"), "identity", TenantId("tenant-A")
    )
    assert any(f.id == fact.id for f in results)


@pytest.mark.asyncio
async def test_closed_fact_not_in_active(repo):
    """4. Closed fact is NOT returned by get_active_facts_by_entity_predicate."""
    from memory_layer.domain.types import EntityId, FactId, TenantId

    fact = _make_fact(fact_id="fact-closed2")
    await repo.save(fact)
    await repo.close_fact(
        fact.id, fact.tenant_id, datetime.now(timezone.utc), FactId("fact-new2")
    )
    results = await repo.get_active_facts_by_entity_predicate(
        EntityId("entity-alice"), "identity", TenantId("tenant-A")
    )
    assert not any(f.id == fact.id for f in results)


@pytest.mark.asyncio
async def test_list_by_memory_record(repo):
    """5. list_by_memory_record returns all facts for a record."""
    from memory_layer.domain.types import MemoryId, TenantId

    fact1 = _make_fact(fact_id="fact-lr1", memory_record_id="mem-lr")
    fact2 = _make_fact(fact_id="fact-lr2", memory_record_id="mem-lr", subject_entity_id="entity-bob")
    await repo.save(fact1)
    await repo.save(fact2)
    results = await repo.list_by_memory_record(MemoryId("mem-lr"), TenantId("tenant-A"))
    ids = {f.id for f in results}
    assert fact1.id in ids
    assert fact2.id in ids


@pytest.mark.asyncio
async def test_cross_tenant_isolation(repo):
    """6. Fact from tenant A is not visible to tenant B."""
    from memory_layer.domain.types import EntityId, TenantId

    fact_a = _make_fact(fact_id="fact-iso-a", tenant_id="tenant-A")
    await repo.save(fact_a)
    results = await repo.get_active_facts_by_entity_predicate(
        EntityId("entity-alice"), "identity", TenantId("tenant-B")
    )
    assert not any(f.id == fact_a.id for f in results)

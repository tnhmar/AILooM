"""Acceptance tests for SqliteFactRepository — M2-T3."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from memory_layer.domain.exceptions import FactNotFoundError, TenantIsolationViolation
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
    new_fact_id,
    new_memory_id,
    new_tenant_id,
)
from memory_layer.storage.sqlite.migration_runner import ensure_schema
from memory_layer.storage.sqlite.fact_repo import SqliteFactRepository

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_ENTITY = EntityId("entity-abc")
_PRED_GROUP = "preference"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_path(tmp_path: pytest.TempPathFactory) -> str:
    path = str(tmp_path / "facts_test.db")
    conn = ensure_schema(path)
    conn.close()
    return path


@pytest_asyncio.fixture
async def repo(db_path: str) -> SqliteFactRepository:
    return SqliteFactRepository(db_path)


def _make_fact(
    *,
    tenant_id: TenantId | None = None,
    entity_id: EntityId = _ENTITY,
    predicate_group: str = _PRED_GROUP,
    effective_to: datetime | None = None,
    supersedes: FactId | None = None,
    confidence: float = 1.0,
    memory_record_id: MemoryId | None = None,
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE,
) -> Fact:
    tid = tenant_id or new_tenant_id()
    scope = Scope(
        tenant_id=tid,
        principal_id=PrincipalId("user-1"),
        principal_type=PrincipalType.USER,
    )
    return Fact(
        id=new_fact_id(),
        memory_record_id=memory_record_id or new_memory_id(),
        tenant_id=tid,
        scope=scope,
        subject_entity_id=entity_id,
        predicate="prefers",
        predicate_group=predicate_group,
        object_value="dark mode",
        effective_from=_NOW,
        effective_to=effective_to,
        recorded_at=_NOW,
        supersedes=supersedes,
        confidence=confidence,
        sector=MemorySector.SEMANTIC,
        lifecycle_state=lifecycle_state,
    )


# ---------------------------------------------------------------------------
# 1. save then get_by_id returns same Fact
# ---------------------------------------------------------------------------

async def test_save_and_get_by_id_roundtrip(repo: SqliteFactRepository) -> None:
    fact = _make_fact()
    await repo.save(fact)
    fetched = await repo.get_by_id(fact.id, fact.tenant_id)
    assert fetched is not None
    assert fetched.id == fact.id
    assert fetched.object_value == fact.object_value


# ---------------------------------------------------------------------------
# 2. get_by_id returns None for unknown ID
# ---------------------------------------------------------------------------

async def test_get_by_id_unknown_returns_none(repo: SqliteFactRepository) -> None:
    result = await repo.get_by_id(FactId("no-such-id"), new_tenant_id())
    assert result is None


# ---------------------------------------------------------------------------
# 3. effective_to defaults to None on new fact
# ---------------------------------------------------------------------------

async def test_effective_to_defaults_to_none(repo: SqliteFactRepository) -> None:
    fact = _make_fact()  # no effective_to
    await repo.save(fact)
    fetched = await repo.get_by_id(fact.id, fact.tenant_id)
    assert fetched is not None
    assert fetched.effective_to is None


# ---------------------------------------------------------------------------
# 4. close_fact sets effective_to on old fact
# ---------------------------------------------------------------------------

async def test_close_fact_sets_effective_to(repo: SqliteFactRepository) -> None:
    tid = new_tenant_id()
    old = _make_fact(tenant_id=tid)
    new = _make_fact(tenant_id=tid)
    await repo.save(old)
    await repo.save(new)

    close_dt = _NOW + timedelta(days=1)
    await repo.close_fact(old.id, tid, close_dt, new.id)

    fetched_old = await repo.get_by_id(old.id, tid)
    assert fetched_old is not None
    assert fetched_old.effective_to == close_dt


# ---------------------------------------------------------------------------
# 5. close_fact sets supersedes on new fact
# ---------------------------------------------------------------------------

async def test_close_fact_sets_supersedes(repo: SqliteFactRepository) -> None:
    tid = new_tenant_id()
    old = _make_fact(tenant_id=tid)
    new = _make_fact(tenant_id=tid)
    await repo.save(old)
    await repo.save(new)

    await repo.close_fact(old.id, tid, _NOW + timedelta(days=1), new.id)

    fetched_new = await repo.get_by_id(new.id, tid)
    assert fetched_new is not None
    assert fetched_new.supersedes == old.id


# ---------------------------------------------------------------------------
# 6. After close_fact, get_active_facts_by_entity_predicate returns only new fact
# ---------------------------------------------------------------------------

async def test_active_facts_after_close_fact(repo: SqliteFactRepository) -> None:
    tid = new_tenant_id()
    entity = EntityId("ent-6")
    old = _make_fact(tenant_id=tid, entity_id=entity)
    new = _make_fact(tenant_id=tid, entity_id=entity)
    await repo.save(old)
    await repo.save(new)

    await repo.close_fact(old.id, tid, _NOW + timedelta(days=1), new.id)

    active = await repo.get_active_facts_by_entity_predicate(entity, _PRED_GROUP, tid)
    ids = {f.id for f in active}
    assert old.id not in ids
    assert new.id in ids


# ---------------------------------------------------------------------------
# 7. close_fact is atomic — if new_fact_id does not exist, both rolled back
# ---------------------------------------------------------------------------

async def test_close_fact_atomic_rollback(repo: SqliteFactRepository) -> None:
    tid = new_tenant_id()
    old = _make_fact(tenant_id=tid)
    await repo.save(old)

    ghost_id = FactId("ghost-does-not-exist")
    from memory_layer.domain.exceptions import StorageError
    with pytest.raises(StorageError):
        await repo.close_fact(old.id, tid, _NOW + timedelta(days=1), ghost_id)

    # old fact must still have effective_to = None (rolled back)
    fetched = await repo.get_by_id(old.id, tid)
    assert fetched is not None
    assert fetched.effective_to is None


# ---------------------------------------------------------------------------
# 8. list_by_memory_record returns all facts for that record
# ---------------------------------------------------------------------------

async def test_list_by_memory_record_returns_all(repo: SqliteFactRepository) -> None:
    tid = new_tenant_id()
    mid = new_memory_id()
    f1 = _make_fact(tenant_id=tid, memory_record_id=mid)
    f2 = _make_fact(tenant_id=tid, memory_record_id=mid)
    other = _make_fact(tenant_id=tid)  # different memory_record_id
    await repo.save(f1)
    await repo.save(f2)
    await repo.save(other)

    results = await repo.list_by_memory_record(mid, tid)
    ids = {f.id for f in results}
    assert f1.id in ids
    assert f2.id in ids
    assert other.id not in ids


# ---------------------------------------------------------------------------
# 9. list_by_memory_record returns empty list for unknown memory_record_id
# ---------------------------------------------------------------------------

async def test_list_by_memory_record_unknown_returns_empty(
    repo: SqliteFactRepository,
) -> None:
    result = await repo.list_by_memory_record(new_memory_id(), new_tenant_id())
    assert result == []


# ---------------------------------------------------------------------------
# 10. get_by_id with wrong tenant_id raises TenantIsolationViolation
# ---------------------------------------------------------------------------

async def test_get_by_id_wrong_tenant_raises(repo: SqliteFactRepository) -> None:
    fact = _make_fact()
    await repo.save(fact)
    with pytest.raises(TenantIsolationViolation):
        await repo.get_by_id(fact.id, new_tenant_id())


# ---------------------------------------------------------------------------
# 11. close_fact with unknown fact_id raises FactNotFoundError
# ---------------------------------------------------------------------------

async def test_close_fact_unknown_fact_id_raises(repo: SqliteFactRepository) -> None:
    with pytest.raises(FactNotFoundError):
        await repo.close_fact(
            FactId("no-such-fact"),
            new_tenant_id(),
            _NOW + timedelta(days=1),
            FactId("irrelevant"),
        )


# ---------------------------------------------------------------------------
# 12. confidence round-trips as float
# ---------------------------------------------------------------------------

async def test_confidence_roundtrips_as_float(repo: SqliteFactRepository) -> None:
    fact = _make_fact(confidence=0.87)
    await repo.save(fact)
    fetched = await repo.get_by_id(fact.id, fact.tenant_id)
    assert fetched is not None
    assert isinstance(fetched.confidence, float)
    assert abs(fetched.confidence - 0.87) < 1e-9

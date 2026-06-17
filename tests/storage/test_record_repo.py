"""Acceptance tests for SqliteMemoryRecordRepository — M2-T2."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from memory_layer.domain.exceptions import TenantIsolationViolation
from memory_layer.domain.records import MemoryRecord, Scope
from memory_layer.domain.types import (
    LifecycleState,
    MemoryId,
    MemorySector,
    PayloadType,
    PipelineStatus,
    PrincipalId,
    PrincipalType,
    TenantId,
    new_memory_id,
    new_tenant_id,
)
from memory_layer.storage.sqlite.migration_runner import ensure_schema
from memory_layer.storage.sqlite.record_repo import SqliteMemoryRecordRepository

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_path(tmp_path: pytest.TempPathFactory) -> str:
    """Return a path to a freshly migrated SQLite DB."""
    path = str(tmp_path / "test.db")
    conn = ensure_schema(path)
    conn.close()
    return path


@pytest_asyncio.fixture
async def repo(db_path: str) -> SqliteMemoryRecordRepository:
    return SqliteMemoryRecordRepository(db_path)


def _make_record(
    *,
    tenant_id: TenantId | None = None,
    session_id: str | None = None,
    idempotency_key: str | None = None,
    metadata: dict | None = None,
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE,
    pipeline_status: PipelineStatus = PipelineStatus.PENDING,
    recorded_at: datetime | None = None,
) -> MemoryRecord:
    tid = tenant_id or new_tenant_id()
    from memory_layer.domain.types import SessionId as SID
    scope = Scope(
        tenant_id=tid,
        principal_id=PrincipalId("user-1"),
        principal_type=PrincipalType.USER,
        session_id=SID(session_id) if session_id else None,
    )
    return MemoryRecord(
        id=new_memory_id(),
        tenant_id=tid,
        scope=scope,
        raw_payload="test payload",
        payload_type=PayloadType.CONVERSATION_TURN,
        sector=MemorySector.EPISODIC,
        lifecycle_state=lifecycle_state,
        pipeline_status=pipeline_status,
        recorded_at=recorded_at or datetime.now(timezone.utc),
        idempotency_key=idempotency_key,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# 1. save then get_by_id returns same MemoryRecord
# ---------------------------------------------------------------------------

async def test_save_and_get_by_id_roundtrip(repo: SqliteMemoryRecordRepository) -> None:
    record = _make_record()
    await repo.save(record)
    fetched = await repo.get_by_id(record.id, record.tenant_id)
    assert fetched is not None
    assert fetched.id == record.id
    assert fetched.raw_payload == record.raw_payload
    assert fetched.tenant_id == record.tenant_id


# ---------------------------------------------------------------------------
# 2. get_by_id returns None for unknown ID
# ---------------------------------------------------------------------------

async def test_get_by_id_unknown_returns_none(repo: SqliteMemoryRecordRepository) -> None:
    result = await repo.get_by_id(MemoryId("does-not-exist"), new_tenant_id())
    assert result is None


# ---------------------------------------------------------------------------
# 3. save is idempotent
# ---------------------------------------------------------------------------

async def test_save_is_idempotent(repo: SqliteMemoryRecordRepository) -> None:
    record = _make_record()
    await repo.save(record)
    await repo.save(record)  # second call must not raise or duplicate
    fetched = await repo.get_by_id(record.id, record.tenant_id)
    assert fetched is not None
    assert fetched.id == record.id


# ---------------------------------------------------------------------------
# 4. update_lifecycle changes lifecycle_state to CONSOLIDATED
# ---------------------------------------------------------------------------

async def test_update_lifecycle(
    repo: SqliteMemoryRecordRepository,
) -> None:
    record = _make_record()
    await repo.save(record)
    await repo.update_lifecycle(
        record.id, record.tenant_id, LifecycleState.CONSOLIDATED, actor="test"
    )
    fetched = await repo.get_by_id(record.id, record.tenant_id)
    assert fetched is not None
    assert fetched.lifecycle_state == LifecycleState.CONSOLIDATED


# ---------------------------------------------------------------------------
# 5. update_pipeline_status changes pipeline_status to ENRICHED
# ---------------------------------------------------------------------------

async def test_update_pipeline_status(
    repo: SqliteMemoryRecordRepository,
) -> None:
    record = _make_record()
    await repo.save(record)
    await repo.update_pipeline_status(
        record.id, record.tenant_id, PipelineStatus.ENRICHED
    )
    fetched = await repo.get_by_id(record.id, record.tenant_id)
    assert fetched is not None
    assert fetched.pipeline_status == PipelineStatus.ENRICHED


# ---------------------------------------------------------------------------
# 6. list_by_scope returns only records matching lifecycle_states
# ---------------------------------------------------------------------------

async def test_list_by_scope_filters_lifecycle(
    repo: SqliteMemoryRecordRepository,
) -> None:
    tid = new_tenant_id()
    active = _make_record(tenant_id=tid, lifecycle_state=LifecycleState.ACTIVE)
    decayed = _make_record(tenant_id=tid, lifecycle_state=LifecycleState.DECAYED)
    await repo.save(active)
    await repo.save(decayed)

    results = await repo.list_by_scope(
        active.scope, [LifecycleState.ACTIVE]
    )
    ids = {r.id for r in results}
    assert active.id in ids
    assert decayed.id not in ids


# ---------------------------------------------------------------------------
# 7. list_by_scope respects limit
# ---------------------------------------------------------------------------

async def test_list_by_scope_respects_limit(
    repo: SqliteMemoryRecordRepository,
) -> None:
    tid = new_tenant_id()
    for _ in range(5):
        await repo.save(_make_record(tenant_id=tid))

    # all 5 share the same scope (principal_id="user-1", tenant=tid)
    sample = _make_record(tenant_id=tid)
    results = await repo.list_by_scope(sample.scope, [LifecycleState.ACTIVE], limit=3)
    assert len(results) <= 3


# ---------------------------------------------------------------------------
# 8. get_by_idempotency_key returns record when key matches
# ---------------------------------------------------------------------------

async def test_get_by_idempotency_key_found(
    repo: SqliteMemoryRecordRepository,
) -> None:
    record = _make_record(idempotency_key="idem-xyz")
    await repo.save(record)
    fetched = await repo.get_by_idempotency_key("idem-xyz", record.tenant_id)
    assert fetched is not None
    assert fetched.id == record.id


# ---------------------------------------------------------------------------
# 9. get_by_idempotency_key returns None when key not found
# ---------------------------------------------------------------------------

async def test_get_by_idempotency_key_not_found(
    repo: SqliteMemoryRecordRepository,
) -> None:
    result = await repo.get_by_idempotency_key("no-such-key", new_tenant_id())
    assert result is None


# ---------------------------------------------------------------------------
# 10. metadata dict round-trips through save/get
# ---------------------------------------------------------------------------

async def test_metadata_roundtrip(repo: SqliteMemoryRecordRepository) -> None:
    meta = {"source": "slack", "channel": "#general", "priority": 3}
    record = _make_record(metadata=meta)
    await repo.save(record)
    fetched = await repo.get_by_id(record.id, record.tenant_id)
    assert fetched is not None
    assert fetched.metadata == meta


# ---------------------------------------------------------------------------
# 11. get_by_id with wrong tenant_id raises TenantIsolationViolation
# ---------------------------------------------------------------------------

async def test_get_by_id_wrong_tenant_raises(
    repo: SqliteMemoryRecordRepository,
) -> None:
    record = _make_record()
    await repo.save(record)
    wrong_tenant = new_tenant_id()
    with pytest.raises(TenantIsolationViolation):
        await repo.get_by_id(record.id, wrong_tenant)


# ---------------------------------------------------------------------------
# 12. recorded_at round-trips as datetime (not string)
# ---------------------------------------------------------------------------

async def test_recorded_at_roundtrips_as_datetime(
    repo: SqliteMemoryRecordRepository,
) -> None:
    dt = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
    record = _make_record(recorded_at=dt)
    await repo.save(record)
    fetched = await repo.get_by_id(record.id, record.tenant_id)
    assert fetched is not None
    assert isinstance(fetched.recorded_at, datetime)
    assert fetched.recorded_at == dt

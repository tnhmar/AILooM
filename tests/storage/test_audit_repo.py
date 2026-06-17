"""Acceptance tests for SqliteAuditLog — M2-T5."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from memory_layer.domain.exceptions import TenantIsolationViolation
from memory_layer.domain.records import AuditEntry, Scope
from memory_layer.domain.types import (
    AuditId,
    AuditOperation,
    AuditOutcome,
    MemoryId,
    PrincipalId,
    PrincipalType,
    TenantId,
    new_memory_id,
    new_tenant_id,
)
from memory_layer.storage.sqlite.audit_repo import SqliteAuditLog
from memory_layer.storage.sqlite.migration_runner import ensure_schema

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_path(tmp_path: pytest.TempPathFactory) -> str:
    path = str(tmp_path / "audit_test.db")
    conn = ensure_schema(path)
    conn.close()
    return path


@pytest_asyncio.fixture
async def log(db_path: str) -> SqliteAuditLog:
    return SqliteAuditLog(db_path)


def _make_entry(
    *,
    tenant_id: TenantId | None = None,
    memory_id: MemoryId | None = None,
    operation: AuditOperation = AuditOperation.WRITE,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    detail: dict | None = None,
    timestamp: datetime = _NOW,
) -> AuditEntry:
    tid = tenant_id or new_tenant_id()
    scope = Scope(
        tenant_id=tid,
        principal_id=PrincipalId("user-1"),
        principal_type=PrincipalType.USER,
    )
    return AuditEntry(
        id=AuditId(str(new_memory_id())),
        tenant_id=tid,
        scope=scope,
        operation=operation,
        memory_id=memory_id or new_memory_id(),
        actor="test-actor",
        timestamp=timestamp,
        outcome=outcome,
        detail=detail or {},
    )


# ---------------------------------------------------------------------------
# 1. append then get_by_memory_id round-trip
# ---------------------------------------------------------------------------

async def test_append_get_roundtrip(log: SqliteAuditLog) -> None:
    entry = _make_entry()
    await log.append(entry)
    results = await log.get_by_memory_id(entry.memory_id, entry.tenant_id)
    assert len(results) == 1
    assert results[0].id == entry.id
    assert results[0].operation == entry.operation


# ---------------------------------------------------------------------------
# 2. two appends produce two entries for same memory_id
# ---------------------------------------------------------------------------

async def test_two_appends_two_entries(log: SqliteAuditLog) -> None:
    tid = new_tenant_id()
    mid = new_memory_id()
    e1 = _make_entry(tenant_id=tid, memory_id=mid, timestamp=_NOW)
    e2 = _make_entry(
        tenant_id=tid,
        memory_id=mid,
        operation=AuditOperation.RECALL,
        timestamp=datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC),
    )
    await log.append(e1)
    await log.append(e2)
    results = await log.get_by_memory_id(mid, tid)
    assert len(results) == 2


# ---------------------------------------------------------------------------
# 3. results are ordered by timestamp ASC
# ---------------------------------------------------------------------------

async def test_ordering_asc(log: SqliteAuditLog) -> None:
    tid = new_tenant_id()
    mid = new_memory_id()
    t1 = datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    t3 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    for ts in (t3, t1, t2):  # insert out-of-order
        await log.append(_make_entry(tenant_id=tid, memory_id=mid, timestamp=ts))
    results = await log.get_by_memory_id(mid, tid)
    timestamps = [r.timestamp for r in results]
    assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# 4. empty list for unknown memory_id
# ---------------------------------------------------------------------------

async def test_empty_for_unknown_memory_id(log: SqliteAuditLog) -> None:
    result = await log.get_by_memory_id(new_memory_id(), new_tenant_id())
    assert result == []


# ---------------------------------------------------------------------------
# 5. detail dict round-trips
# ---------------------------------------------------------------------------

async def test_detail_roundtrip(log: SqliteAuditLog) -> None:
    detail = {"source": "api", "tokens": 42, "flag": True}
    entry = _make_entry(detail=detail)
    await log.append(entry)
    results = await log.get_by_memory_id(entry.memory_id, entry.tenant_id)
    assert results[0].detail == detail


# ---------------------------------------------------------------------------
# 6. wrong tenant raises TenantIsolationViolation
# ---------------------------------------------------------------------------

async def test_wrong_tenant_raises(log: SqliteAuditLog) -> None:
    entry = _make_entry()
    await log.append(entry)
    with pytest.raises(TenantIsolationViolation):
        await log.get_by_memory_id(entry.memory_id, new_tenant_id())


# ---------------------------------------------------------------------------
# 7. timestamp is a datetime object after round-trip
# ---------------------------------------------------------------------------

async def test_timestamp_is_datetime(log: SqliteAuditLog) -> None:
    entry = _make_entry()
    await log.append(entry)
    results = await log.get_by_memory_id(entry.memory_id, entry.tenant_id)
    assert isinstance(results[0].timestamp, datetime)
    assert results[0].timestamp == entry.timestamp


# ---------------------------------------------------------------------------
# 8. outcome defaults to SUCCESS
# ---------------------------------------------------------------------------

async def test_outcome_defaults_to_success(log: SqliteAuditLog) -> None:
    entry = _make_entry()  # outcome defaults to AuditOutcome.SUCCESS
    await log.append(entry)
    results = await log.get_by_memory_id(entry.memory_id, entry.tenant_id)
    assert results[0].outcome == AuditOutcome.SUCCESS

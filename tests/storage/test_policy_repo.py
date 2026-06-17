"""Acceptance tests for SqliteTenantPolicyRepository — M2-T5."""

from __future__ import annotations

import pytest
import pytest_asyncio

from memory_layer.domain.policies import (
    ConsolidationTrigger,
    TenantPolicies,
)
from memory_layer.domain.types import new_tenant_id
from memory_layer.storage.sqlite.migration_runner import ensure_schema
from memory_layer.storage.sqlite.policy_repo import SqliteTenantPolicyRepository

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_path(tmp_path: pytest.TempPathFactory) -> str:
    path = str(tmp_path / "policy_test.db")
    conn = ensure_schema(path)
    conn.close()
    return path


@pytest_asyncio.fixture
async def repo(db_path: str) -> SqliteTenantPolicyRepository:
    return SqliteTenantPolicyRepository(db_path)


# ---------------------------------------------------------------------------
# 1. get unknown tenant returns default TenantPolicies
# ---------------------------------------------------------------------------

async def test_get_unknown_returns_default(repo: SqliteTenantPolicyRepository) -> None:
    result = await repo.get(new_tenant_id())
    assert isinstance(result, TenantPolicies)
    # Spot-check a default value
    assert result.consolidation.threshold_record_count == 500


# ---------------------------------------------------------------------------
# 2. save then get round-trips
# ---------------------------------------------------------------------------

async def test_save_get_roundtrip(repo: SqliteTenantPolicyRepository) -> None:
    tid = new_tenant_id()
    policies = TenantPolicies()
    policies.consolidation.threshold_record_count = 999
    await repo.save(tid, policies)
    fetched = await repo.get(tid)
    assert fetched.consolidation.threshold_record_count == 999


# ---------------------------------------------------------------------------
# 3. modify + save updates stored value
# ---------------------------------------------------------------------------

async def test_modify_then_save_updates(repo: SqliteTenantPolicyRepository) -> None:
    tid = new_tenant_id()
    p1 = TenantPolicies()
    await repo.save(tid, p1)

    p2 = await repo.get(tid)
    p2.retention.decay_after_days = 180
    await repo.save(tid, p2)

    fetched = await repo.get(tid)
    assert fetched.retention.decay_after_days == 180


# ---------------------------------------------------------------------------
# 4. threshold_record_count round-trips as int
# ---------------------------------------------------------------------------

async def test_threshold_record_count_roundtrip(repo: SqliteTenantPolicyRepository) -> None:
    tid = new_tenant_id()
    p = TenantPolicies()
    p.consolidation.threshold_record_count = 1234
    await repo.save(tid, p)
    fetched = await repo.get(tid)
    assert isinstance(fetched.consolidation.threshold_record_count, int)
    assert fetched.consolidation.threshold_record_count == 1234


# ---------------------------------------------------------------------------
# 5. low_confidence_threshold round-trips as float
# ---------------------------------------------------------------------------

async def test_low_confidence_threshold_roundtrip(repo: SqliteTenantPolicyRepository) -> None:
    tid = new_tenant_id()
    p = TenantPolicies()
    p.conflict_resolution.low_confidence_threshold = 0.42
    await repo.save(tid, p)
    fetched = await repo.get(tid)
    assert isinstance(fetched.conflict_resolution.low_confidence_threshold, float)
    assert abs(fetched.conflict_resolution.low_confidence_threshold - 0.42) < 1e-9


# ---------------------------------------------------------------------------
# 6. ConsolidationTrigger enum round-trips
# ---------------------------------------------------------------------------

async def test_consolidation_trigger_enum_roundtrip(repo: SqliteTenantPolicyRepository) -> None:
    tid = new_tenant_id()
    p = TenantPolicies()
    p.consolidation.trigger = ConsolidationTrigger.THRESHOLD
    await repo.save(tid, p)
    fetched = await repo.get(tid)
    assert fetched.consolidation.trigger == ConsolidationTrigger.THRESHOLD
    assert isinstance(fetched.consolidation.trigger, ConsolidationTrigger)


# ---------------------------------------------------------------------------
# 7. two gets return independent objects
# ---------------------------------------------------------------------------

async def test_two_gets_are_independent(repo: SqliteTenantPolicyRepository) -> None:
    tid = new_tenant_id()
    await repo.save(tid, TenantPolicies())
    a = await repo.get(tid)
    b = await repo.get(tid)
    a.consolidation.threshold_record_count = 1
    # Mutating `a` must not affect `b`
    assert b.consolidation.threshold_record_count != 1


# ---------------------------------------------------------------------------
# 8. save is idempotent
# ---------------------------------------------------------------------------

async def test_save_is_idempotent(repo: SqliteTenantPolicyRepository) -> None:
    tid = new_tenant_id()
    p = TenantPolicies()
    await repo.save(tid, p)
    await repo.save(tid, p)  # second save must not raise
    fetched = await repo.get(tid)
    assert isinstance(fetched, TenantPolicies)

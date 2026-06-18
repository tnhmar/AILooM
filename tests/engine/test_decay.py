"""Acceptance tests for DecayService — M4-T1."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from memory_layer.domain.events import (
    MemoryArchivedEvent,
    MemoryDecayedEvent,
    MemoryDeletedEvent,
)
from memory_layer.domain.policies import RetentionPolicy, TenantPolicies
from memory_layer.domain.records import MemoryRecord, Scope
from memory_layer.domain.types import (
    LifecycleState,
    MemorySector,
    PayloadType,
    PipelineStatus,
    PrincipalType,
    TenantId,
    new_memory_id,
)
from memory_layer.engine.decay import DecayService
from memory_layer.ports.inbound import DecayUseCase

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

TENANT = TenantId("tenant-decay")
_SCOPE = Scope(
    tenant_id=TENANT,
    principal_id="user-1",  # type: ignore[arg-type]
    principal_type=PrincipalType.USER,
)


def _record(
    state: LifecycleState = LifecycleState.ACTIVE,
    age_days: int = 100,
    sector: MemorySector = MemorySector.EPISODIC,
) -> MemoryRecord:
    return MemoryRecord(
        id=new_memory_id(),
        tenant_id=TENANT,
        scope=_SCOPE,
        raw_payload="test payload",
        payload_type=PayloadType.CONVERSATION_TURN,
        sector=sector,
        lifecycle_state=state,
        pipeline_status=PipelineStatus.ENRICHED,
        recorded_at=datetime.now(tz=UTC) - timedelta(days=age_days),
    )


def _policy(
    decay_after_days: int | None = 90,
    archive_after_days: int | None = None,
    delete_after_days: int | None = None,
    sector_decay_overrides: dict | None = None,
) -> RetentionPolicy:
    return RetentionPolicy(
        decay_after_days=decay_after_days,
        archive_after_days=archive_after_days,
        delete_after_days=delete_after_days,
        sector_decay_overrides=sector_decay_overrides or {},
    )


def _make_service(
    active_records: list[MemoryRecord] | None = None,
    decayed_records: list[MemoryRecord] | None = None,
    archived_records: list[MemoryRecord] | None = None,
    policy: RetentionPolicy | None = None,
    process_limit: int = 500,
) -> tuple[DecayService, AsyncMock, AsyncMock, AsyncMock, AsyncMock]:
    retention = policy or _policy()
    tenant_policies = TenantPolicies(retention=retention)

    record_repo = AsyncMock()

    async def _list_by_scope(
        scope: Scope,
        lifecycle_states: list[LifecycleState],
        limit: int = 100,
    ) -> list[MemoryRecord]:
        mapping = {
            LifecycleState.ACTIVE: active_records or [],
            LifecycleState.DECAYED: decayed_records or [],
            LifecycleState.ARCHIVED: archived_records or [],
        }
        for state in lifecycle_states:
            if state in mapping:
                return mapping[state][:limit]
        return []

    record_repo.list_by_scope.side_effect = _list_by_scope
    record_repo.update_lifecycle = AsyncMock()

    audit_log = AsyncMock()
    observer = AsyncMock()
    policy_repo = AsyncMock()
    policy_repo.get.return_value = tenant_policies

    svc = DecayService(
        record_repo=record_repo,
        audit_log=audit_log,
        observer=observer,
        policy_repo=policy_repo,
        process_limit=process_limit,
    )
    return svc, record_repo, audit_log, observer, policy_repo


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# 1. DecayService satisfies DecayUseCase protocol
def test_decay_service_satisfies_use_case() -> None:
    svc, *_ = _make_service()
    assert isinstance(svc, DecayUseCase)


# 2. Records older than decay_after_days are transitioned to DECAYED
@pytest.mark.asyncio
async def test_old_records_transitioned_to_decayed() -> None:
    old_record = _record(state=LifecycleState.ACTIVE, age_days=100)
    svc, record_repo, *_ = _make_service(
        active_records=[old_record],
        policy=_policy(decay_after_days=90),
    )
    await svc.execute(TENANT)
    record_repo.update_lifecycle.assert_awaited_once_with(
        memory_id=old_record.id,
        tenant_id=TENANT,
        state=LifecycleState.DECAYED,
        actor="decay-service",
    )


# 3. MemoryDecayedEvent is emitted for each decayed record
@pytest.mark.asyncio
async def test_decayed_event_emitted() -> None:
    old_record = _record(state=LifecycleState.ACTIVE, age_days=100)
    svc, _, _, observer, _ = _make_service(
        active_records=[old_record],
        policy=_policy(decay_after_days=90),
    )
    await svc.execute(TENANT)
    observer.emit.assert_awaited_once()
    event = observer.emit.call_args[0][0]
    assert isinstance(event, MemoryDecayedEvent)
    assert event.memory_id == old_record.id


# 4. audit_log.append is called for each DECAYED transition
@pytest.mark.asyncio
async def test_audit_log_called_for_decay() -> None:
    records = [_record(age_days=100) for _ in range(3)]
    svc, _, audit_log, *_ = _make_service(
        active_records=records,
        policy=_policy(decay_after_days=90),
    )
    await svc.execute(TENANT)
    assert audit_log.append.await_count == 3


# 5. Records NOT yet old enough are NOT transitioned
@pytest.mark.asyncio
async def test_young_records_not_transitioned() -> None:
    young = _record(state=LifecycleState.ACTIVE, age_days=10)
    svc, record_repo, *_ = _make_service(
        active_records=[young],
        policy=_policy(decay_after_days=90),
    )
    count = await svc.execute(TENANT)
    record_repo.update_lifecycle.assert_not_awaited()
    assert count == 0


# 6. DECAYED records older than archive_after_days → ARCHIVED
@pytest.mark.asyncio
async def test_decayed_records_archived() -> None:
    old_decayed = _record(state=LifecycleState.DECAYED, age_days=400)
    svc, record_repo, *_ = _make_service(
        decayed_records=[old_decayed],
        policy=_policy(decay_after_days=None, archive_after_days=365),
    )
    await svc.execute(TENANT)
    record_repo.update_lifecycle.assert_awaited_once_with(
        memory_id=old_decayed.id,
        tenant_id=TENANT,
        state=LifecycleState.ARCHIVED,
        actor="decay-service",
    )


# 7. MemoryArchivedEvent is emitted for each archived record
@pytest.mark.asyncio
async def test_archived_event_emitted() -> None:
    old_decayed = _record(state=LifecycleState.DECAYED, age_days=400)
    svc, _, _, observer, _ = _make_service(
        decayed_records=[old_decayed],
        policy=_policy(decay_after_days=None, archive_after_days=365),
    )
    await svc.execute(TENANT)
    observer.emit.assert_awaited_once()
    event = observer.emit.call_args[0][0]
    assert isinstance(event, MemoryArchivedEvent)


# 8. ARCHIVED records older than delete_after_days → DELETED
@pytest.mark.asyncio
async def test_archived_records_deleted() -> None:
    old_archived = _record(state=LifecycleState.ARCHIVED, age_days=800)
    svc, record_repo, *_ = _make_service(
        archived_records=[old_archived],
        policy=_policy(decay_after_days=None, archive_after_days=None, delete_after_days=730),
    )
    await svc.execute(TENANT)
    record_repo.update_lifecycle.assert_awaited_once_with(
        memory_id=old_archived.id,
        tenant_id=TENANT,
        state=LifecycleState.DELETED,
        actor="decay-service",
    )


# 9. MemoryDeletedEvent is emitted for each deleted record
@pytest.mark.asyncio
async def test_deleted_event_emitted() -> None:
    old_archived = _record(state=LifecycleState.ARCHIVED, age_days=800)
    svc, _, _, observer, _ = _make_service(
        archived_records=[old_archived],
        policy=_policy(decay_after_days=None, archive_after_days=None, delete_after_days=730),
    )
    await svc.execute(TENANT)
    observer.emit.assert_awaited_once()
    event = observer.emit.call_args[0][0]
    assert isinstance(event, MemoryDeletedEvent)


# 10. archive_after_days=None skips the archive step
@pytest.mark.asyncio
async def test_none_archive_threshold_skips_step() -> None:
    decayed = _record(state=LifecycleState.DECAYED, age_days=500)
    svc, record_repo, *_ = _make_service(
        decayed_records=[decayed],
        policy=_policy(decay_after_days=None, archive_after_days=None),
    )
    await svc.execute(TENANT)
    record_repo.update_lifecycle.assert_not_awaited()


# 11. delete_after_days=None skips the delete step
@pytest.mark.asyncio
async def test_none_delete_threshold_skips_step() -> None:
    archived = _record(state=LifecycleState.ARCHIVED, age_days=1000)
    svc, record_repo, *_ = _make_service(
        archived_records=[archived],
        policy=_policy(decay_after_days=None, archive_after_days=None, delete_after_days=None),
    )
    await svc.execute(TENANT)
    record_repo.update_lifecycle.assert_not_awaited()


# 12. process_limit=2 caps total transitions
@pytest.mark.asyncio
async def test_process_limit_caps_transitions() -> None:
    records = [_record(age_days=100) for _ in range(5)]
    svc, *_ = _make_service(
        active_records=records,
        policy=_policy(decay_after_days=90),
        process_limit=2,
    )
    count = await svc.execute(TENANT)
    assert count == 2


# 13. Sector override takes precedence over global decay_after_days
@pytest.mark.asyncio
async def test_sector_override_takes_precedence() -> None:
    # EPISODIC override = 30 days; global = 90 days.
    # Record is 50 days old — would NOT decay under global, but SHOULD under override.
    record_episodic = _record(
        state=LifecycleState.ACTIVE,
        age_days=50,
        sector=MemorySector.EPISODIC,
    )
    record_semantic = _record(
        state=LifecycleState.ACTIVE,
        age_days=50,
        sector=MemorySector.SEMANTIC,
    )
    pol = _policy(
        decay_after_days=90,
        sector_decay_overrides={MemorySector.EPISODIC.value: 30},
    )
    svc, record_repo, *_ = _make_service(
        active_records=[record_episodic, record_semantic],
        policy=pol,
    )
    count = await svc.execute(TENANT)
    assert count == 1
    record_repo.update_lifecycle.assert_awaited_once_with(
        memory_id=record_episodic.id,
        tenant_id=TENANT,
        state=LifecycleState.DECAYED,
        actor="decay-service",
    )


# 14. Record already in target state is silently skipped
@pytest.mark.asyncio
async def test_already_decayed_record_skipped() -> None:
    already_decayed = _record(state=LifecycleState.DECAYED, age_days=100)
    svc, record_repo, audit_log, observer, _ = _make_service(
        active_records=[already_decayed],
        policy=_policy(decay_after_days=90),
    )
    await svc._transition(already_decayed, LifecycleState.DECAYED, TENANT)
    record_repo.update_lifecycle.assert_not_awaited()
    audit_log.append.assert_not_awaited()
    observer.emit.assert_not_awaited()


# 15. Return value equals number of actual transitions
@pytest.mark.asyncio
async def test_return_value_equals_transitions() -> None:
    records = [_record(age_days=100) for _ in range(4)]
    svc, *_ = _make_service(
        active_records=records,
        policy=_policy(decay_after_days=90),
    )
    count = await svc.execute(TENANT)
    assert count == 4

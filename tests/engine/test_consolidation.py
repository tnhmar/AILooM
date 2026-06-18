"""Acceptance tests for ConsolidationService — M4-T2."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from memory_layer.domain.events import (
    ConsolidationJobCompletedEvent,
    ConsolidationJobStartedEvent,
    MemoryConsolidatedEvent,
)
from memory_layer.domain.policies import ConsolidationPolicy, TenantPolicies
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
from memory_layer.engine.consolidation import ConsolidationService
from memory_layer.ports.inbound import ConsolidateUseCase

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

TENANT = TenantId("tenant-consolidation")
_SCOPE = Scope(
    tenant_id=TENANT,
    principal_id="user-1",  # type: ignore[arg-type]
    principal_type=PrincipalType.USER,
)


def _record(
    sector: MemorySector = MemorySector.EPISODIC,
    payload: str = "some memory",
    state: LifecycleState = LifecycleState.ACTIVE,
) -> MemoryRecord:
    return MemoryRecord(
        id=new_memory_id(),
        tenant_id=TENANT,
        scope=_SCOPE,
        raw_payload=payload,
        payload_type=PayloadType.CONVERSATION_TURN,
        sector=sector,
        lifecycle_state=state,
        pipeline_status=PipelineStatus.ENRICHED,
        recorded_at=datetime.now(tz=UTC) - timedelta(days=5),
    )


def _policy(
    enabled: bool = True,
    threshold: int = 1,
    max_items: int = 100,
    sectors: list[MemorySector] | None = None,
) -> ConsolidationPolicy:
    return ConsolidationPolicy(
        enabled=enabled,
        threshold_record_count=threshold,
        max_items_per_run=max_items,
        sectors=sectors if sectors is not None else [MemorySector.EPISODIC],
    )


def _make_service(
    records: list[MemoryRecord] | None = None,
    policy: ConsolidationPolicy | None = None,
    llm_client=None,
) -> tuple[ConsolidationService, AsyncMock, AsyncMock, AsyncMock, AsyncMock]:
    retention_policy = policy or _policy()
    tenant_policies = TenantPolicies(consolidation=retention_policy)

    record_repo = AsyncMock()
    record_repo.list_by_scope.return_value = records or []
    record_repo.save = AsyncMock()
    record_repo.update_lifecycle = AsyncMock()

    audit_log = AsyncMock()
    observer = AsyncMock()
    policy_repo = AsyncMock()
    policy_repo.get.return_value = tenant_policies

    svc = ConsolidationService(
        record_repo=record_repo,
        audit_log=audit_log,
        observer=observer,
        policy_repo=policy_repo,
        llm_client=llm_client,
    )
    return svc, record_repo, audit_log, observer, policy_repo


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# 1. ConsolidationService satisfies ConsolidateUseCase protocol
def test_consolidation_service_satisfies_use_case() -> None:
    svc, *_ = _make_service()
    assert isinstance(svc, ConsolidateUseCase)


# 2. ConsolidationJobStartedEvent emitted at the start
@pytest.mark.asyncio
async def test_job_started_event_emitted() -> None:
    svc, _, _, observer, _ = _make_service(records=[_record()])
    await svc.execute(TENANT)
    emitted_types = [type(call.args[0]) for call in observer.emit.call_args_list]
    assert ConsolidationJobStartedEvent in emitted_types


# 3. ConsolidationJobCompletedEvent emitted at end with correct records_processed
@pytest.mark.asyncio
async def test_job_completed_event_records_processed() -> None:
    records = [_record(payload=f"p{i}") for i in range(3)]
    svc, _, _, observer, _ = _make_service(records=records)
    count = await svc.execute(TENANT)
    completed_events = [
        call.args[0]
        for call in observer.emit.call_args_list
        if isinstance(call.args[0], ConsolidationJobCompletedEvent)
    ]
    assert len(completed_events) == 1
    assert completed_events[0].records_processed == count


# 4. policy.enabled=False returns 0 and skips all processing
@pytest.mark.asyncio
async def test_disabled_policy_returns_zero() -> None:
    records = [_record() for _ in range(5)]
    svc, record_repo, audit_log, observer, _ = _make_service(
        records=records,
        policy=_policy(enabled=False),
    )
    result = await svc.execute(TENANT)
    assert result == 0
    record_repo.update_lifecycle.assert_not_awaited()
    audit_log.append.assert_not_awaited()


# 5. Sector not in policy.sectors is skipped
@pytest.mark.asyncio
async def test_out_of_scope_sector_skipped() -> None:
    semantic_record = _record(sector=MemorySector.SEMANTIC)
    # policy only covers EPISODIC
    svc, record_repo, *_ = _make_service(
        records=[semantic_record],
        policy=_policy(sectors=[MemorySector.EPISODIC]),
    )
    count = await svc.execute(TENANT)
    assert count == 0
    record_repo.update_lifecycle.assert_not_awaited()


# 6. Count below threshold_record_count causes sector to be skipped
@pytest.mark.asyncio
async def test_below_threshold_sector_skipped() -> None:
    record = _record()
    svc, record_repo, *_ = _make_service(
        records=[record],
        policy=_policy(threshold=5),  # need 5, only have 1
    )
    count = await svc.execute(TENANT)
    assert count == 0
    record_repo.update_lifecycle.assert_not_awaited()


# 7. Source records are transitioned to CONSOLIDATED lifecycle state
@pytest.mark.asyncio
async def test_source_records_transitioned_to_consolidated() -> None:
    records = [_record(payload=f"payload-{i}") for i in range(2)]
    svc, record_repo, *_ = _make_service(
        records=records,
        policy=_policy(threshold=1),
    )
    await svc.execute(TENANT)
    calls = record_repo.update_lifecycle.await_args_list
    transitioned_states = [c.kwargs["state"] for c in calls]
    assert all(s == LifecycleState.CONSOLIDATED for s in transitioned_states)
    assert len(calls) == 2


# 8. MemoryConsolidatedEvent emitted per source record
@pytest.mark.asyncio
async def test_consolidated_event_emitted_per_record() -> None:
    records = [_record(payload=f"p{i}") for i in range(3)]
    svc, _, _, observer, _ = _make_service(
        records=records,
        policy=_policy(threshold=1),
    )
    await svc.execute(TENANT)
    consolidated_events = [
        c.args[0]
        for c in observer.emit.call_args_list
        if isinstance(c.args[0], MemoryConsolidatedEvent)
    ]
    assert len(consolidated_events) == 3


# 9. audit_log.append called per source record
@pytest.mark.asyncio
async def test_audit_log_called_per_source_record() -> None:
    records = [_record(payload=f"p{i}") for i in range(4)]
    svc, _, audit_log, *_ = _make_service(
        records=records,
        policy=_policy(threshold=1),
    )
    await svc.execute(TENANT)
    assert audit_log.append.await_count == 4


# 10. A new consolidated MemoryRecord is saved via record_repo.save
@pytest.mark.asyncio
async def test_consolidated_record_saved() -> None:
    records = [_record(payload=f"p{i}") for i in range(2)]
    svc, record_repo, *_ = _make_service(
        records=records,
        policy=_policy(threshold=1),
    )
    await svc.execute(TENANT)
    record_repo.save.assert_awaited_once()


# 11. New consolidated record has lifecycle_state=CONSOLIDATED and pipeline_status=ENRICHED
@pytest.mark.asyncio
async def test_new_consolidated_record_fields() -> None:
    records = [_record(payload=f"p{i}") for i in range(2)]
    svc, record_repo, *_ = _make_service(
        records=records,
        policy=_policy(threshold=1),
    )
    await svc.execute(TENANT)
    saved_record: MemoryRecord = record_repo.save.call_args[0][0]
    assert saved_record.lifecycle_state == LifecycleState.CONSOLIDATED
    assert saved_record.pipeline_status == PipelineStatus.ENRICHED


# 12. Without llm_client, fallback is newline-joined payloads
@pytest.mark.asyncio
async def test_no_llm_fallback_is_newline_join() -> None:
    records = [_record(payload="alpha"), _record(payload="beta")]
    svc, record_repo, *_ = _make_service(
        records=records,
        policy=_policy(threshold=1),
        llm_client=None,
    )
    await svc.execute(TENANT)
    saved_record: MemoryRecord = record_repo.save.call_args[0][0]
    assert saved_record.raw_payload == "alpha\nbeta"


# 13. With llm_client, _summarise calls llm_client.complete
@pytest.mark.asyncio
async def test_llm_client_called_for_summarise() -> None:
    llm_client = AsyncMock()
    llm_client.complete.return_value = "LLM summary"
    records = [_record(payload="alpha"), _record(payload="beta")]
    svc, record_repo, *_ = _make_service(
        records=records,
        policy=_policy(threshold=1),
        llm_client=llm_client,
    )
    await svc.execute(TENANT)
    llm_client.complete.assert_awaited_once()
    saved_record: MemoryRecord = record_repo.save.call_args[0][0]
    assert saved_record.raw_payload == "LLM summary"


# 14. _summarise LLM exception falls back gracefully (no raise)
@pytest.mark.asyncio
async def test_llm_exception_falls_back_gracefully() -> None:
    llm_client = AsyncMock()
    llm_client.complete.side_effect = RuntimeError("LLM timeout")
    records = [_record(payload="x"), _record(payload="y")]
    svc, record_repo, *_ = _make_service(
        records=records,
        policy=_policy(threshold=1),
        llm_client=llm_client,
    )
    # Should not raise; should fall back to newline-joined payloads.
    await svc.execute(TENANT)
    saved_record: MemoryRecord = record_repo.save.call_args[0][0]
    assert saved_record.raw_payload == "x\ny"


# 15. Return value equals number of source records consolidated
@pytest.mark.asyncio
async def test_return_value_equals_source_records() -> None:
    records = [_record(payload=f"p{i}") for i in range(7)]
    svc, *_ = _make_service(
        records=records,
        policy=_policy(threshold=1),
    )
    count = await svc.execute(TENANT)
    assert count == 7

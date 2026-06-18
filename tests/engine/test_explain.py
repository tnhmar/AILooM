"""Acceptance tests for ExplainRecallService — M4-T4."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from memory_layer.domain.exceptions import MemoryNotFoundError
from memory_layer.domain.records import MemoryRecord, RecallTrace, Scope, TraceStep
from memory_layer.domain.types import (
    LifecycleState,
    MemorySector,
    PayloadType,
    PipelineStatus,
    PrincipalType,
    TenantId,
    TraceId,
    new_memory_id,
)
from memory_layer.engine.explain import ExplainRecallService
from memory_layer.ports.inbound import ExplainRecallUseCase

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TENANT = TenantId("tenant-explain")
TRACE_ID = TraceId("trace-001")
_SCOPE = Scope(
    tenant_id=TENANT,
    principal_id="user-1",  # type: ignore[arg-type]
    principal_type=PrincipalType.USER,
)


def _step(memory_id: str | None = None) -> TraceStep:
    return TraceStep(
        memory_id=new_memory_id() if memory_id is None else memory_id,  # type: ignore[arg-type]
        rank=1,
        score=0.9,
    )


def _trace(steps: list[TraceStep] | None = None) -> RecallTrace:
    return RecallTrace(
        trace_id=TRACE_ID,
        tenant_id=TENANT,
        query="what did I say yesterday?",
        mode="HYBRID",
        steps=steps or [],
    )


def _record(memory_id: str) -> MemoryRecord:
    from datetime import UTC, datetime
    return MemoryRecord(
        id=memory_id,  # type: ignore[arg-type]
        tenant_id=TENANT,
        scope=_SCOPE,
        raw_payload="payload",
        payload_type=PayloadType.CONVERSATION_TURN,
        sector=MemorySector.EPISODIC,
        lifecycle_state=LifecycleState.ACTIVE,
        pipeline_status=PipelineStatus.ENRICHED,
        recorded_at=datetime.now(tz=UTC),
    )


def _make_service(
    trace: RecallTrace | None = None,
    records: dict[str, MemoryRecord] | None = None,
) -> tuple[ExplainRecallService, AsyncMock, AsyncMock, AsyncMock]:
    trace_repo = AsyncMock()
    trace_repo.get_by_trace_id.return_value = trace

    record_repo = AsyncMock()
    _records = records or {}

    async def _get_by_id(memory_id, tenant_id):
        return _records.get(str(memory_id))

    record_repo.get_by_id.side_effect = _get_by_id

    audit_log = AsyncMock()

    svc = ExplainRecallService(
        trace_repo=trace_repo,
        record_repo=record_repo,
        audit_log=audit_log,
    )
    return svc, trace_repo, record_repo, audit_log


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# 1. ExplainRecallService satisfies ExplainRecallUseCase protocol
def test_satisfies_use_case() -> None:
    svc, *_ = _make_service(trace=_trace())
    assert isinstance(svc, ExplainRecallUseCase)


# 2. Returns RecallTrace when trace exists
@pytest.mark.asyncio
async def test_returns_recall_trace_when_found() -> None:
    t = _trace()
    svc, *_ = _make_service(trace=t)
    result = await svc.execute(TRACE_ID, TENANT)
    assert isinstance(result, RecallTrace)
    assert result.trace_id == TRACE_ID


# 3. Raises MemoryNotFoundError when trace_id not found
@pytest.mark.asyncio
async def test_raises_not_found_when_trace_missing() -> None:
    svc, *_ = _make_service(trace=None)
    with pytest.raises(MemoryNotFoundError):
        await svc.execute(TRACE_ID, TENANT)


# 4. record_repo.get_by_id called for each TraceStep
@pytest.mark.asyncio
async def test_get_by_id_called_per_step() -> None:
    steps = [_step() for _ in range(3)]
    t = _trace(steps=steps)
    svc, _, record_repo, _ = _make_service(trace=t)
    await svc.execute(TRACE_ID, TENANT)
    assert record_repo.get_by_id.await_count == 3


# 5. TraceStep.record_available=True when record exists
@pytest.mark.asyncio
async def test_step_available_true_when_record_exists() -> None:
    mid = str(new_memory_id())
    step = _step(memory_id=mid)
    t = _trace(steps=[step])
    svc, *_ = _make_service(trace=t, records={mid: _record(mid)})
    result = await svc.execute(TRACE_ID, TENANT)
    assert result.steps[0].record_available is True


# 6. TraceStep.record_available=False when record has been deleted
@pytest.mark.asyncio
async def test_step_available_false_when_record_missing() -> None:
    mid = str(new_memory_id())
    step = _step(memory_id=mid)
    t = _trace(steps=[step])
    svc, *_ = _make_service(trace=t, records={})  # record not present
    result = await svc.execute(TRACE_ID, TENANT)
    assert result.steps[0].record_available is False


# 7. Returned RecallTrace preserves original trace_id
@pytest.mark.asyncio
async def test_returned_trace_preserves_trace_id() -> None:
    t = _trace(steps=[_step()])
    svc, *_ = _make_service(trace=t)
    result = await svc.execute(TRACE_ID, TENANT)
    assert result.trace_id == TRACE_ID


# 8. MemoryTrace.steps count matches steps in stored trace
@pytest.mark.asyncio
async def test_steps_count_preserved() -> None:
    steps = [_step() for _ in range(5)]
    t = _trace(steps=steps)
    svc, *_ = _make_service(trace=t)
    result = await svc.execute(TRACE_ID, TENANT)
    assert len(result.steps) == 5

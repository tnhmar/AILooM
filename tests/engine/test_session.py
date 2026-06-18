"""Acceptance tests for SessionEndHandler — M4-T3."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from memory_layer.domain.events import SessionEndedEvent
from memory_layer.domain.policies import (
    ConsolidationPolicy,
    ConsolidationTrigger,
    TenantPolicies,
)
from memory_layer.domain.records import MemoryRecord, Scope
from memory_layer.domain.types import (
    LifecycleState,
    MemorySector,
    PayloadType,
    PipelineStatus,
    PrincipalType,
    SessionId,
    TenantId,
    new_memory_id,
)
from memory_layer.engine.session import SessionEndHandler
from memory_layer.ports.inbound import NotifySessionEndedUseCase

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

TENANT = TenantId("tenant-session")
SESSION = SessionId("session-abc")
_SCOPE = Scope(
    tenant_id=TENANT,
    principal_id="user-1",  # type: ignore[arg-type]
    principal_type=PrincipalType.USER,
    session_id=SESSION,
)


def _record() -> MemoryRecord:
    from datetime import UTC, datetime
    return MemoryRecord(
        id=new_memory_id(),
        tenant_id=TENANT,
        scope=_SCOPE,
        raw_payload="payload",
        payload_type=PayloadType.CONVERSATION_TURN,
        sector=MemorySector.EPISODIC,
        lifecycle_state=LifecycleState.ACTIVE,
        pipeline_status=PipelineStatus.ENRICHED,
        recorded_at=datetime.now(tz=UTC),
    )


def _policy(
    trigger: ConsolidationTrigger = ConsolidationTrigger.SESSION_END,
    enabled: bool = True,
) -> ConsolidationPolicy:
    return ConsolidationPolicy(trigger=trigger, enabled=enabled)


def _make_handler(
    active_records: list[MemoryRecord] | None = None,
    policy: ConsolidationPolicy | None = None,
) -> tuple[SessionEndHandler, AsyncMock, AsyncMock, AsyncMock, AsyncMock]:
    tenant_policies = TenantPolicies(consolidation=policy or _policy())

    record_repo = AsyncMock()
    record_repo.list_by_scope.return_value = active_records or []

    policy_repo = AsyncMock()
    policy_repo.get.return_value = tenant_policies

    observer = AsyncMock()
    consolidation_service = AsyncMock()
    consolidation_service.execute.return_value = 0

    handler = SessionEndHandler(
        record_repo=record_repo,
        policy_repo=policy_repo,
        observer=observer,
        consolidation_service=consolidation_service,
    )
    return handler, record_repo, policy_repo, observer, consolidation_service


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# 1. Satisfies NotifySessionEndedUseCase protocol
def test_handler_satisfies_use_case() -> None:
    handler, *_ = _make_handler()
    assert isinstance(handler, NotifySessionEndedUseCase)


# 2. SessionEndedEvent emitted with correct session_id
@pytest.mark.asyncio
async def test_session_ended_event_emitted_with_session_id() -> None:
    handler, _, _, observer, _ = _make_handler()
    await handler.execute(TENANT, SESSION, _SCOPE)
    observer.emit.assert_awaited_once()
    event = observer.emit.call_args[0][0]
    assert isinstance(event, SessionEndedEvent)
    assert event.session_id == SESSION


# 3. SessionEndedEvent.record_count equals ACTIVE records in scope
@pytest.mark.asyncio
async def test_event_record_count_matches_active_records() -> None:
    records = [_record() for _ in range(5)]
    handler, _, _, observer, _ = _make_handler(active_records=records)
    await handler.execute(TENANT, SESSION, _SCOPE)
    event = observer.emit.call_args[0][0]
    assert event.record_count == 5


# 4. consolidation_service.execute called when trigger=SESSION_END and enabled=True
@pytest.mark.asyncio
async def test_consolidation_triggered_on_session_end() -> None:
    handler, _, _, _, consolidation_service = _make_handler(
        policy=_policy(trigger=ConsolidationTrigger.SESSION_END, enabled=True)
    )
    await handler.execute(TENANT, SESSION, _SCOPE)
    consolidation_service.execute.assert_awaited_once()


# 5. consolidation_service.execute NOT called when trigger=THRESHOLD
@pytest.mark.asyncio
async def test_consolidation_not_triggered_for_threshold_trigger() -> None:
    handler, _, _, _, consolidation_service = _make_handler(
        policy=_policy(trigger=ConsolidationTrigger.THRESHOLD)
    )
    await handler.execute(TENANT, SESSION, _SCOPE)
    consolidation_service.execute.assert_not_awaited()


# 6. consolidation_service.execute NOT called when policy.enabled=False
@pytest.mark.asyncio
async def test_consolidation_not_triggered_when_disabled() -> None:
    handler, _, _, _, consolidation_service = _make_handler(
        policy=_policy(trigger=ConsolidationTrigger.SESSION_END, enabled=False)
    )
    await handler.execute(TENANT, SESSION, _SCOPE)
    consolidation_service.execute.assert_not_awaited()


# 7. consolidation_service.execute called with the session scope
@pytest.mark.asyncio
async def test_consolidation_called_with_session_scope() -> None:
    handler, _, _, _, consolidation_service = _make_handler(
        policy=_policy(trigger=ConsolidationTrigger.SESSION_END)
    )
    await handler.execute(TENANT, SESSION, _SCOPE)
    consolidation_service.execute.assert_awaited_once_with(TENANT, scope=_SCOPE)


# 8. Zero ACTIVE records → SessionEndedEvent.record_count=0
@pytest.mark.asyncio
async def test_zero_records_event_count_is_zero() -> None:
    handler, _, _, observer, _ = _make_handler(active_records=[])
    await handler.execute(TENANT, SESSION, _SCOPE)
    event = observer.emit.call_args[0][0]
    assert event.record_count == 0


# 9. record_repo.list_by_scope called with lifecycle_states=[ACTIVE]
@pytest.mark.asyncio
async def test_list_by_scope_called_with_active_state() -> None:
    handler, record_repo, *_ = _make_handler()
    await handler.execute(TENANT, SESSION, _SCOPE)
    record_repo.list_by_scope.assert_awaited_once()
    call_kwargs = record_repo.list_by_scope.call_args
    lifecycle_states = call_kwargs.kwargs.get(
        "lifecycle_states", call_kwargs.args[1] if len(call_kwargs.args) > 1 else None
    )
    assert LifecycleState.ACTIVE in lifecycle_states

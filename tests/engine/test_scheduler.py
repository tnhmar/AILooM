"""Acceptance tests for LifecycleScheduler — M4-T5."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from memory_layer.domain.policies import (
    ConsolidationPolicy,
    ConsolidationTrigger,
    TenantPolicies,
)
from memory_layer.domain.types import TenantId
from memory_layer.engine.scheduler import LifecycleScheduler, ScheduleConfig

TENANT_A = TenantId("tenant-sched-a")
TENANT_B = TenantId("tenant-sched-b")

FAST = ScheduleConfig(
    decay_interval_seconds=0,
    consolidation_interval_seconds=0,
    enabled=True,
)


def _make_scheduler(
    trigger: ConsolidationTrigger = ConsolidationTrigger.SCHEDULE,
) -> tuple[LifecycleScheduler, AsyncMock, AsyncMock, AsyncMock]:
    decay_service = AsyncMock()
    decay_service.execute.return_value = 0

    consolidation_service = AsyncMock()
    consolidation_service.execute.return_value = 0

    policy_repo = AsyncMock()
    policy_repo.get.return_value = TenantPolicies(
        consolidation=ConsolidationPolicy(trigger=trigger)
    )

    scheduler = LifecycleScheduler(
        decay_service=decay_service,
        consolidation_service=consolidation_service,
        policy_repo=policy_repo,
    )
    return scheduler, decay_service, consolidation_service, policy_repo


def test_is_running_false_before_start() -> None:
    scheduler, *_ = _make_scheduler()
    assert scheduler.is_running(TENANT_A) is False


@pytest.mark.asyncio
async def test_is_running_true_after_start() -> None:
    scheduler, *_ = _make_scheduler()
    await scheduler.start(TENANT_A, FAST)
    try:
        assert scheduler.is_running(TENANT_A) is True
    finally:
        await scheduler.stop_all()


@pytest.mark.asyncio
async def test_is_running_false_after_stop() -> None:
    scheduler, *_ = _make_scheduler()
    await scheduler.start(TENANT_A, FAST)
    await scheduler.stop(TENANT_A)
    assert scheduler.is_running(TENANT_A) is False


@pytest.mark.asyncio
async def test_decay_execute_called_after_interval() -> None:
    scheduler, decay_service, *_ = _make_scheduler()
    await scheduler.start(TENANT_A, FAST)
    await asyncio.sleep(0.05)
    await scheduler.stop_all()
    decay_service.execute.assert_awaited()


@pytest.mark.asyncio
async def test_consolidation_execute_called_when_schedule_trigger() -> None:
    scheduler, _, consolidation_service, _ = _make_scheduler(
        trigger=ConsolidationTrigger.SCHEDULE
    )
    await scheduler.start(TENANT_A, FAST)
    await asyncio.sleep(0.05)
    await scheduler.stop_all()
    consolidation_service.execute.assert_awaited()


@pytest.mark.asyncio
async def test_consolidation_not_scheduled_for_session_end_trigger() -> None:
    scheduler, _, consolidation_service, _ = _make_scheduler(
        trigger=ConsolidationTrigger.SESSION_END
    )
    await scheduler.start(TENANT_A, FAST)
    await asyncio.sleep(0.05)
    await scheduler.stop_all()
    consolidation_service.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_decay_loop_survives_exception() -> None:
    scheduler, decay_service, *_ = _make_scheduler()
    call_count = 0

    async def _flaky(tenant_id):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient error")
        return 0

    decay_service.execute.side_effect = _flaky
    await scheduler.start(TENANT_A, FAST)
    await asyncio.sleep(0.15)
    await scheduler.stop_all()
    assert call_count >= 2


@pytest.mark.asyncio
async def test_stop_all_stops_all_tenants() -> None:
    scheduler, *_ = _make_scheduler()
    await scheduler.start(TENANT_A, FAST)
    await scheduler.start(TENANT_B, FAST)
    await scheduler.stop_all()
    assert scheduler.is_running(TENANT_A) is False
    assert scheduler.is_running(TENANT_B) is False


@pytest.mark.asyncio
async def test_stop_nonexistent_tenant_is_noop() -> None:
    scheduler, *_ = _make_scheduler()
    await scheduler.stop(TenantId("ghost-tenant"))


@pytest.mark.asyncio
async def test_two_tenants_run_independently() -> None:
    scheduler, *_ = _make_scheduler()
    await scheduler.start(TENANT_A, FAST)
    await scheduler.start(TENANT_B, FAST)
    try:
        assert scheduler.is_running(TENANT_A) is True
        assert scheduler.is_running(TENANT_B) is True
        await scheduler.stop(TENANT_A)
        assert scheduler.is_running(TENANT_A) is False
        assert scheduler.is_running(TENANT_B) is True
    finally:
        await scheduler.stop_all()

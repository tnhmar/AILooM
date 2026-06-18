"""LifecycleScheduler — asyncio background scheduler for decay and consolidation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

from memory_layer.domain.policies import ConsolidationTrigger
from memory_layer.domain.types import TenantId
from memory_layer.engine.consolidation import ConsolidationService
from memory_layer.engine.decay import DecayService
from memory_layer.ports.outbound import TenantPolicyRepositoryPort

log = logging.getLogger(__name__)


@dataclass
class ScheduleConfig:
    """Per-tenant scheduler configuration."""

    decay_interval_seconds: int = 3600
    consolidation_interval_seconds: int = 1800
    enabled: bool = True


_TaskKey = tuple[TenantId, str]


class LifecycleScheduler:
    """Asyncio-based background scheduler for decay and consolidation sweeps.

    Design:
    - One asyncio.Task per (tenant_id, job_type) pair.
    - Each job loops: sleep(interval) → run service → repeat.
    - Exceptions inside a job are logged but do NOT cancel the loop.
    - start() creates tasks; stop() / stop_all() cancel and await them.
    - Single-event-loop use only — no threading primitives.
    """

    def __init__(
        self,
        decay_service: DecayService,
        consolidation_service: ConsolidationService,
        policy_repo: TenantPolicyRepositoryPort,
    ) -> None:
        self._decay_service = decay_service
        self._consolidation_service = consolidation_service
        self._policy_repo = policy_repo
        self._tasks: dict[_TaskKey, asyncio.Task[None]] = {}

    async def start(
        self, tenant_id: TenantId, config: ScheduleConfig | None = None
    ) -> None:
        """Start decay (and optionally consolidation) loops for *tenant_id*."""
        cfg = config or ScheduleConfig()
        if not cfg.enabled:
            log.debug("Scheduler disabled for tenant %s — not starting.", tenant_id)
            return

        self._ensure_task(
            tenant_id,
            "decay",
            self._decay_loop(tenant_id, cfg.decay_interval_seconds),
        )

        tenant_policies = await self._policy_repo.get(tenant_id)
        consolidation_policy = tenant_policies.consolidation
        if consolidation_policy.trigger == ConsolidationTrigger.SCHEDULE:
            self._ensure_task(
                tenant_id,
                "consolidation",
                self._consolidation_loop(tenant_id, cfg.consolidation_interval_seconds),
            )
        else:
            log.debug(
                "Tenant %s consolidation trigger=%s — skipping scheduled task.",
                tenant_id,
                consolidation_policy.trigger,
            )

    async def stop(self, tenant_id: TenantId) -> None:
        """Cancel all tasks for *tenant_id*. No-op if tenant is not running."""
        keys = [k for k in self._tasks if k[0] == tenant_id]
        if not keys:
            return
        await self._cancel_keys(keys)

    async def stop_all(self) -> None:
        """Cancel and await all running tasks across all tenants."""
        await self._cancel_keys(list(self._tasks.keys()))

    def is_running(self, tenant_id: TenantId) -> bool:
        """Return True if at least one task is active for *tenant_id*."""
        return any(
            k[0] == tenant_id and not t.done()
            for k, t in self._tasks.items()
        )

    async def _decay_loop(self, tenant_id: TenantId, interval: int) -> None:
        """Run decay sweep every *interval* seconds; survive individual failures."""
        while True:
            await asyncio.sleep(interval)
            try:
                count = await self._decay_service.execute(tenant_id)
                log.debug("Decay sweep tenant=%s transitions=%d.", tenant_id, count)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "Decay loop error for tenant %s: %s",
                    tenant_id,
                    exc,
                    exc_info=True,
                )

    async def _consolidation_loop(self, tenant_id: TenantId, interval: int) -> None:
        """Run consolidation sweep every *interval* seconds; survive individual failures."""
        while True:
            await asyncio.sleep(interval)
            try:
                count = await self._consolidation_service.execute(tenant_id)
                log.debug(
                    "Consolidation sweep tenant=%s records=%d.", tenant_id, count
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "Consolidation loop error for tenant %s: %s",
                    tenant_id,
                    exc,
                    exc_info=True,
                )

    def _ensure_task(
        self,
        tenant_id: TenantId,
        job_type: str,
        coro: Coroutine[Any, Any, None],
    ) -> None:
        """Create and register a task; cancel existing one if already running."""
        key: _TaskKey = (tenant_id, job_type)
        existing = self._tasks.get(key)
        if existing and not existing.done():
            existing.cancel()
        task: asyncio.Task[None] = asyncio.get_event_loop().create_task(coro)
        self._tasks[key] = task

    async def _cancel_keys(self, keys: list[_TaskKey]) -> None:
        """Cancel and await all tasks identified by *keys*."""
        tasks_to_cancel: list[asyncio.Task[None]] = []
        for key in keys:
            task = self._tasks.pop(key, None)
            if task and not task.done():
                task.cancel()
                tasks_to_cancel.append(task)
        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

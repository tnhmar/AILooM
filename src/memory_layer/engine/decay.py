"""Decay use case — DecayService."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from memory_layer.domain.records import AuditEntry
from memory_layer.domain.types import (
    AuditOperation,
    AuditOutcome,
    LifecycleState,
    TenantId,
    new_audit_id,
)
from memory_layer.ports.outbound import AuditLogPort, MemoryRecordRepositoryPort

log = logging.getLogger(__name__)

_DEFAULT_DECAY_DAYS = 30


class DecayService:
    """Concrete implementation of DecayUseCase."""

    def __init__(
        self,
        record_repo: MemoryRecordRepositoryPort,
        audit_log: AuditLogPort,
        decay_days: int = _DEFAULT_DECAY_DAYS,
    ) -> None:
        self._record_repo = record_repo
        self._audit_log = audit_log
        self._decay_days = decay_days

    async def execute(self, tenant_id: TenantId) -> int:
        """Run decay pass for *tenant_id* and return number of transitions."""
        from memory_layer.observability.metrics import memory_decays_total

        cutoff = datetime.utcnow() - timedelta(days=self._decay_days)

        records = await self._record_repo.list_active_older_than(
            tenant_id=tenant_id,
            cutoff=cutoff,
        )

        count = 0
        for record in records:
            await self._record_repo.update_lifecycle_state(
                record_id=record.id,
                tenant_id=tenant_id,
                new_state=LifecycleState.DECAYED,
            )
            audit = AuditEntry(
                id=new_audit_id(),
                tenant_id=tenant_id,
                scope=record.scope,
                operation=AuditOperation.DECAY,
                memory_id=record.id,
                outcome=AuditOutcome.SUCCESS,
            )
            await self._audit_log.append(audit)
            count += 1

        memory_decays_total.labels(tenant_id=str(tenant_id)).inc(count)

        log.info("Decay pass complete for tenant=%s transitions=%d", tenant_id, count)
        return count

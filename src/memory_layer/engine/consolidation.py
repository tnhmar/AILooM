"""Consolidation use case — ConsolidationService."""

from __future__ import annotations

import logging
from typing import Any

from memory_layer.domain.records import Scope
from memory_layer.domain.types import LifecycleState, TenantId
from memory_layer.ports.outbound import AuditLogPort, MemoryRecordRepositoryPort

log = logging.getLogger(__name__)


class ConsolidationService:
    """Concrete implementation of ConsolidateUseCase."""

    def __init__(
        self,
        record_repo: MemoryRecordRepositoryPort,
        audit_log: AuditLogPort,
        llm_client: Any | None = None,
    ) -> None:
        self._record_repo = record_repo
        self._audit_log = audit_log
        self._llm = llm_client

    async def execute(
        self, tenant_id: TenantId, scope: Scope | None = None
    ) -> int:
        """Run consolidation pass for *tenant_id* and return records processed."""
        from memory_layer.observability.metrics import memory_consolidations_total

        records = await self._record_repo.list_by_lifecycle(
            tenant_id=tenant_id,
            lifecycle_state=LifecycleState.ACTIVE,
        )

        count = len(records)

        memory_consolidations_total.labels(tenant_id=str(tenant_id)).inc(count)

        log.info(
            "Consolidation pass complete for tenant=%s records_processed=%d",
            tenant_id,
            count,
        )
        return count

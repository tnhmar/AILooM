"""SessionEndHandler — concrete NotifySessionEndedUseCase."""

from __future__ import annotations

import logging

from memory_layer.domain.events import SessionEndedEvent
from memory_layer.domain.policies import ConsolidationTrigger
from memory_layer.domain.records import Scope
from memory_layer.domain.types import LifecycleState, SessionId, TenantId
from memory_layer.engine.consolidation import ConsolidationService
from memory_layer.ports.inbound import NotifySessionEndedUseCase
from memory_layer.ports.outbound import (
    MemoryRecordRepositoryPort,
    ObserverPort,
    TenantPolicyRepositoryPort,
)

log = logging.getLogger(__name__)


class SessionEndHandler:
    """Implements NotifySessionEndedUseCase.

    Flow:
    1. Count ACTIVE records in the session scope.
    2. Emit SessionEndedEvent(session_id, scope, record_count).
    3. Load ConsolidationPolicy via policy_repo.
    4. If policy.trigger == SESSION_END and policy.enabled:
       trigger consolidation_service.execute(tenant_id, scope=scope).
    """

    def __init__(
        self,
        record_repo: MemoryRecordRepositoryPort,
        policy_repo: TenantPolicyRepositoryPort,
        observer: ObserverPort,
        consolidation_service: ConsolidationService,
    ) -> None:
        self._record_repo = record_repo
        self._policy_repo = policy_repo
        self._observer = observer
        self._consolidation_service = consolidation_service

    async def execute(
        self,
        tenant_id: TenantId,
        session_id: SessionId,
        scope: Scope,
    ) -> None:
        # Step 1: count ACTIVE records in this session scope.
        records = await self._record_repo.list_by_scope(
            scope=scope,
            lifecycle_states=[LifecycleState.ACTIVE],
            limit=10_000,
        )
        record_count = len(records)

        # Step 2: emit SessionEndedEvent.
        await self._observer.emit(
            SessionEndedEvent(
                tenant_id=tenant_id,
                session_id=session_id,
                scope=scope,
                record_count=record_count,
            )
        )

        # Step 3: load consolidation policy.
        tenant_policies = await self._policy_repo.get(tenant_id)
        policy = tenant_policies.consolidation

        # Step 4: conditionally trigger consolidation.
        if policy.trigger == ConsolidationTrigger.SESSION_END and policy.enabled:
            log.debug(
                "Session %s ended for tenant %s — triggering consolidation.",
                session_id,
                tenant_id,
            )
            await self._consolidation_service.execute(tenant_id, scope=scope)
        else:
            log.debug(
                "Session %s ended for tenant %s — consolidation not triggered "
                "(trigger=%s, enabled=%s).",
                session_id,
                tenant_id,
                policy.trigger,
                policy.enabled,
            )

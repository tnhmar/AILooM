"""Consolidation use case — ConsolidationService."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from memory_layer.domain.events import (
    ConsolidationJobCompletedEvent,
    ConsolidationJobStartedEvent,
    MemoryConsolidatedEvent,
)
from memory_layer.domain.records import MemoryRecord, Scope
from memory_layer.domain.types import (
    LifecycleState,
    MemorySector,
    PayloadType,
    PipelineStatus,
    TenantId,
    new_memory_id,
)
from memory_layer.ports.outbound import AuditLogPort, MemoryRecordRepositoryPort

log = logging.getLogger(__name__)


class ConsolidationService:
    """Concrete implementation of ConsolidateUseCase.

    Parameters
    ----------
    record_repo:
        Repository for reading and updating memory records.
    audit_log:
        Audit log port; ``append`` is called once per source record processed.
    observer:
        Event observer; receives ``ConsolidationJobStartedEvent``,
        ``MemoryConsolidatedEvent`` (one per source record), and
        ``ConsolidationJobCompletedEvent``.
    policy_repo:
        Policy repository; ``get(tenant_id)`` returns ``TenantPolicies``.
    llm_client:
        Optional LLM client with an ``async complete(prompt) -> str`` method
        used to summarise source records.  Falls back to newline-join when
        ``None`` or when the LLM raises.
    """

    def __init__(
        self,
        record_repo: MemoryRecordRepositoryPort,
        audit_log: AuditLogPort,
        observer: Any,
        policy_repo: Any,
        llm_client: Any | None = None,
    ) -> None:
        self._record_repo = record_repo
        self._audit_log = audit_log
        self._observer = observer
        self._policy_repo = policy_repo
        self._llm = llm_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self, tenant_id: TenantId, scope: Scope | None = None
    ) -> int:
        """Run a consolidation pass for *tenant_id*.

        Returns the number of source records that were consolidated.
        """
        from memory_layer.observability.metrics import memory_consolidations_total

        # Load policy ---------------------------------------------------
        tenant_policies = await self._policy_repo.get(tenant_id)
        policy = tenant_policies.consolidation

        if not policy.enabled:
            log.info("Consolidation disabled for tenant=%s", tenant_id)
            return 0

        # Fetch candidate records per sector ----------------------------
        total_processed = 0

        for sector in policy.sectors:
            processed = await self._run_sector(
                tenant_id=tenant_id,
                sector=sector,
                scope=scope,
                policy=policy,
            )
            total_processed += processed

        memory_consolidations_total.labels(tenant_id=str(tenant_id)).inc(
            total_processed
        )
        log.info(
            "Consolidation pass complete tenant=%s records_processed=%d",
            tenant_id,
            total_processed,
        )
        return total_processed

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _run_sector(
        self,
        tenant_id: TenantId,
        sector: MemorySector,
        scope: Scope | None,
        policy: Any,
    ) -> int:
        """Consolidate records for a single sector; return count processed."""
        records: list[MemoryRecord] = await self._record_repo.list_by_scope(
            scope=scope
            or Scope(
                tenant_id=tenant_id,
                principal_id=None,  # type: ignore[arg-type]
                principal_type=None,  # type: ignore[arg-type]
            ),
            lifecycle_states=[LifecycleState.ACTIVE],
        )

        # Filter to this sector
        sector_records = [r for r in records if r.sector == sector]

        # Threshold check
        if len(sector_records) < policy.threshold_record_count:
            return 0

        # Respect max_items_per_run
        batch = sector_records[: policy.max_items_per_run]

        await self._observer.emit(
            ConsolidationJobStartedEvent(
                tenant_id=tenant_id,
            )
        )

        # Summarise payload
        raw_payload = await self._summarise([r.raw_payload for r in batch])

        # Build consolidated record
        first = batch[0]
        consolidated = MemoryRecord(
            id=new_memory_id(),
            tenant_id=tenant_id,
            scope=first.scope,
            raw_payload=raw_payload,
            payload_type=PayloadType.CONVERSATION_TURN,
            sector=sector,
            lifecycle_state=LifecycleState.CONSOLIDATED,
            pipeline_status=PipelineStatus.ENRICHED,
            recorded_at=datetime.now(tz=UTC),
        )
        await self._record_repo.save(consolidated)

        # Transition source records and emit per-record events
        for record in batch:
            await self._record_repo.update_lifecycle(
                memory_id=record.id,
                tenant_id=tenant_id,
                state=LifecycleState.CONSOLIDATED,
                actor="consolidation-service",
            )
            await self._audit_log.append(
                tenant_id=tenant_id,
                event_type="MEMORY_CONSOLIDATED",
                memory_id=record.id,
                actor="consolidation-service",
            )
            await self._observer.emit(
                MemoryConsolidatedEvent(
                    tenant_id=tenant_id,
                    memory_id=record.id,
                )
            )

        await self._observer.emit(
            ConsolidationJobCompletedEvent(
                tenant_id=tenant_id,
                records_processed=len(batch),
            )
        )

        return len(batch)

    async def _summarise(self, payloads: list[str]) -> str:
        """Return a summary string for *payloads* using LLM or newline-join fallback."""
        if self._llm is None:
            return "\n".join(payloads)
        try:
            prompt = "\n".join(payloads)
            return await self._llm.complete(prompt)
        except Exception:  # noqa: BLE001
            log.warning("LLM summarise failed, falling back to newline-join")
            return "\n".join(payloads)

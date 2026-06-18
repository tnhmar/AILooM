"""ConsolidationService — concrete ConsolidateUseCase (sleep-cycle analogue)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from memory_layer.domain.events import (
    ConsolidationJobCompletedEvent,
    ConsolidationJobStartedEvent,
    MemoryConsolidatedEvent,
)
from memory_layer.domain.policies import ConsolidationPolicy
from memory_layer.domain.records import AuditEntry, MemoryRecord, Scope
from memory_layer.domain.types import (
    AuditOperation,
    AuditOutcome,
    LifecycleState,
    MemorySector,
    PayloadType,
    PipelineStatus,
    PrincipalId,
    PrincipalType,
    TenantId,
    new_audit_id,
    new_job_id,
    new_memory_id,
    new_schedule_id,
)
from memory_layer.engine.extraction import LLMClientPort
from memory_layer.ports.outbound import (
    AuditLogPort,
    MemoryRecordRepositoryPort,
    ObserverPort,
    TenantPolicyRepositoryPort,
)

log = logging.getLogger(__name__)

_ACTOR = "consolidation-service"

_CONSOLIDATION_SYSTEM_PROMPT = (
    "You are a memory consolidation assistant. "
    "Given a list of memory records, produce a concise, unified summary "
    "that preserves all key facts, removing redundancy. "
    "Return plain prose only."
)


class ConsolidationService:
    """Implements ConsolidateUseCase.

    Consolidation flow:
    1. Emit ConsolidationJobStartedEvent.
    2. Load ConsolidationPolicy. If disabled, emit Completed(0) and return 0.
    3. For each target sector in policy.sectors:
       a. Fetch ACTIVE records up to policy.max_items_per_run.
       b. Skip if count < policy.threshold_record_count.
       c. Group by (principal_id, workspace_id) within scope.
       d. For each group: summarise → save consolidated record →
          transition source records → audit + emit per source record.
    4. Emit ConsolidationJobCompletedEvent.
    5. Return total source records consolidated.
    """

    def __init__(
        self,
        record_repo: MemoryRecordRepositoryPort,
        audit_log: AuditLogPort,
        observer: ObserverPort,
        policy_repo: TenantPolicyRepositoryPort,
        llm_client: LLMClientPort | None = None,
    ) -> None:
        self._record_repo = record_repo
        self._audit_log = audit_log
        self._observer = observer
        self._policy_repo = policy_repo
        self._llm_client = llm_client

    async def execute(
        self, tenant_id: TenantId, scope: Scope | None = None
    ) -> int:
        """Run consolidation sweep for *tenant_id*; return source records processed."""
        started_at = datetime.now(tz=UTC)
        job_id = new_job_id()
        schedule_id = new_schedule_id()

        await self._observer.emit(
            ConsolidationJobStartedEvent(
                tenant_id=tenant_id,
                job_id=job_id,
                schedule_id=schedule_id,
                trigger="on-demand",
            )
        )

        tenant_policies = await self._policy_repo.get(tenant_id)
        policy: ConsolidationPolicy = tenant_policies.consolidation

        if not policy.enabled:
            log.debug("Consolidation disabled for tenant %s.", tenant_id)
            await self._observer.emit(
                ConsolidationJobCompletedEvent(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    records_processed=0,
                    duration_ms=0,
                )
            )
            return 0

        target_sectors: list[MemorySector] = (
            list(policy.sectors) if policy.sectors else list(MemorySector)
        )

        if scope is None:
            scope = Scope(
                tenant_id=tenant_id,
                principal_id=PrincipalId("system"),
                principal_type=PrincipalType.AGENT,
            )

        total_processed = 0

        for sector in target_sectors:
            total_processed += await self._process_sector(
                tenant_id=tenant_id,
                base_scope=scope,
                sector=sector,
                policy=policy,
            )

        duration_ms = int(
            (datetime.now(tz=UTC) - started_at).total_seconds() * 1000
        )
        await self._observer.emit(
            ConsolidationJobCompletedEvent(
                tenant_id=tenant_id,
                job_id=job_id,
                records_processed=total_processed,
                duration_ms=duration_ms,
            )
        )
        log.info(
            "Consolidation complete for tenant %s: %d records in %d ms.",
            tenant_id,
            total_processed,
            duration_ms,
        )
        return total_processed

    async def _process_sector(
        self,
        tenant_id: TenantId,
        base_scope: Scope,
        sector: MemorySector,
        policy: ConsolidationPolicy,
    ) -> int:
        records = await self._record_repo.list_by_scope(
            scope=base_scope,
            lifecycle_states=[LifecycleState.ACTIVE],
            limit=policy.max_items_per_run,
        )
        records = [r for r in records if r.sector == sector]

        if len(records) < policy.threshold_record_count:
            log.debug(
                "Sector %s: %d records < threshold %d — skipping.",
                sector,
                len(records),
                policy.threshold_record_count,
            )
            return 0

        groups: dict[tuple[str, str | None], list[MemoryRecord]] = {}
        for record in records:
            key = (record.scope.principal_id, record.scope.workspace_id)
            groups.setdefault(key, []).append(record)

        processed = 0
        for group_records in groups.values():
            processed += await self._consolidate_group(
                tenant_id=tenant_id,
                records=group_records,
                sector=sector,
            )
        return processed

    async def _consolidate_group(
        self,
        tenant_id: TenantId,
        records: list[MemoryRecord],
        sector: MemorySector,
    ) -> int:
        """Consolidate a group of same-scope records; return source record count."""
        if not records:
            return 0

        summary = await self._summarise(records)
        group_scope = records[0].scope

        consolidated = MemoryRecord(
            id=new_memory_id(),
            tenant_id=tenant_id,
            scope=group_scope,
            raw_payload=summary,
            payload_type=PayloadType.DOCUMENT,
            sector=sector,
            lifecycle_state=LifecycleState.CONSOLIDATED,
            pipeline_status=PipelineStatus.ENRICHED,
        )
        await self._record_repo.save(consolidated)

        for record in records:
            await self._record_repo.update_lifecycle(
                memory_id=record.id,
                tenant_id=tenant_id,
                state=LifecycleState.CONSOLIDATED,
                actor=_ACTOR,
            )
            audit_entry = AuditEntry(
                id=new_audit_id(),
                tenant_id=tenant_id,
                scope=record.scope,
                operation=AuditOperation.CONSOLIDATE,
                memory_id=record.id,
                actor=_ACTOR,
                outcome=AuditOutcome.SUCCESS,
                detail={"consolidated_into": consolidated.id},
            )
            await self._audit_log.append(audit_entry)
            await self._observer.emit(
                MemoryConsolidatedEvent(
                    tenant_id=tenant_id,
                    memory_id=record.id,
                    scope=record.scope,
                    previous_state=record.lifecycle_state,
                )
            )

        return len(records)

    async def _summarise(self, records: list[MemoryRecord]) -> str:
        """Produce a consolidated summary string from *records*.

        Uses LLM when available; falls back to newline-joined payloads on any
        exception or when llm_client is None.
        """
        fallback = "\n".join(r.raw_payload for r in records)

        if self._llm_client is None:
            return fallback

        numbered = "\n".join(
            f"{i + 1}. {r.raw_payload}" for i, r in enumerate(records)
        )
        user_prompt = f"Memory records to consolidate:\n{numbered}"

        try:
            return await self._llm_client.complete(
                system_prompt=_CONSOLIDATION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "LLM consolidation failed (%s) — using fallback concatenation.",
                exc,
            )
            return fallback

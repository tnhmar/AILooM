"""DecayService — concrete DecayUseCase that enforces RetentionPolicy."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from memory_layer.domain.events import (
    MemoryArchivedEvent,
    MemoryDecayedEvent,
    MemoryDeletedEvent,
)
from memory_layer.domain.policies import RetentionPolicy
from memory_layer.domain.records import AuditEntry, MemoryRecord, Scope
from memory_layer.domain.types import (
    AuditOperation,
    AuditOutcome,
    LifecycleState,
    PrincipalId,
    PrincipalType,
    TenantId,
    new_audit_id,
)
from memory_layer.ports.outbound import (
    AuditLogPort,
    MemoryRecordRepositoryPort,
    ObserverPort,
    TenantPolicyRepositoryPort,
)

log = logging.getLogger(__name__)

_ACTOR = "decay-service"


class DecayService:
    """Implements DecayUseCase.

    Sweep logic (per tenant):
    1. Load RetentionPolicy via TenantPolicyRepositoryPort.
    2. Fetch ACTIVE records older than effective decay threshold and transition
       them to DECAYED, emitting MemoryDecayedEvent for each.
    3. Fetch DECAYED records older than archive_after_days (if set) and
       transition to ARCHIVED, emitting MemoryArchivedEvent.
    4. Fetch ARCHIVED records older than delete_after_days (if set) and
       transition to DELETED, emitting MemoryDeletedEvent.
    5. Return total transitions performed.

    Sector overrides in policy.sector_decay_overrides take precedence over
    the global decay_after_days. process_limit caps total transitions per run.
    All transitions are idempotent: records already in the target state are
    silently skipped.
    """

    def __init__(
        self,
        record_repo: MemoryRecordRepositoryPort,
        audit_log: AuditLogPort,
        observer: ObserverPort,
        policy_repo: TenantPolicyRepositoryPort,
        process_limit: int = 500,
    ) -> None:
        self._record_repo = record_repo
        self._audit_log = audit_log
        self._observer = observer
        self._policy_repo = policy_repo
        self._process_limit = process_limit

    async def execute(self, tenant_id: TenantId) -> int:
        """Run decay sweep for *tenant_id*; return number of records transitioned."""
        tenant_policies = await self._policy_repo.get(tenant_id)
        policy: RetentionPolicy = tenant_policies.retention

        if not policy.enabled:
            log.debug("Retention policy disabled for tenant %s — skipping.", tenant_id)
            return 0

        total = 0
        now = datetime.now(tz=UTC)

        sweep_scope = Scope(
            tenant_id=tenant_id,
            principal_id=PrincipalId("system"),
            principal_type=PrincipalType.AGENT,
        )

        # ---- Step 1: ACTIVE → DECAYED --------------------------------
        if policy.decay_after_days is not None:
            candidates = await self._record_repo.list_by_scope(
                scope=sweep_scope,
                lifecycle_states=[LifecycleState.ACTIVE],
                limit=self._process_limit,
            )
            for record in candidates:
                if total >= self._process_limit:
                    break
                effective_days = self._effective_decay_days(record, policy)
                if effective_days is None:
                    continue
                age = now - _ensure_utc(record.recorded_at)
                if age >= timedelta(days=effective_days):
                    await self._transition(record, LifecycleState.DECAYED, tenant_id)
                    total += 1

        # ---- Step 2: DECAYED → ARCHIVED ------------------------------
        if policy.archive_after_days is not None and total < self._process_limit:
            decayed = await self._record_repo.list_by_scope(
                scope=sweep_scope,
                lifecycle_states=[LifecycleState.DECAYED],
                limit=self._process_limit - total,
            )
            for record in decayed:
                if total >= self._process_limit:
                    break
                age = now - _ensure_utc(record.recorded_at)
                if age >= timedelta(days=policy.archive_after_days):
                    await self._transition(record, LifecycleState.ARCHIVED, tenant_id)
                    total += 1

        # ---- Step 3: ARCHIVED → DELETED ------------------------------
        if policy.delete_after_days is not None and total < self._process_limit:
            archived = await self._record_repo.list_by_scope(
                scope=sweep_scope,
                lifecycle_states=[LifecycleState.ARCHIVED],
                limit=self._process_limit - total,
            )
            for record in archived:
                if total >= self._process_limit:
                    break
                age = now - _ensure_utc(record.recorded_at)
                if age >= timedelta(days=policy.delete_after_days):
                    await self._transition(record, LifecycleState.DELETED, tenant_id)
                    total += 1

        log.info(
            "DecayService sweep complete for tenant %s — %d transitions.",
            tenant_id,
            total,
        )
        return total

    async def _transition(
        self,
        record: MemoryRecord,
        target_state: LifecycleState,
        tenant_id: TenantId,
    ) -> None:
        """Transition *record* to *target_state*, emitting event + audit entry.

        Idempotent: silently returns if the record is already in *target_state*.
        """
        if record.lifecycle_state == target_state:
            log.debug(
                "Record %s already in state %s — skipping.",
                record.id,
                target_state,
            )
            return

        await self._record_repo.update_lifecycle(
            memory_id=record.id,
            tenant_id=tenant_id,
            state=target_state,
            actor=_ACTOR,
        )

        operation = _STATE_TO_AUDIT_OP[target_state]
        audit_entry = AuditEntry(
            id=new_audit_id(),
            tenant_id=tenant_id,
            scope=record.scope,
            operation=operation,
            memory_id=record.id,
            actor=_ACTOR,
            outcome=AuditOutcome.SUCCESS,
            detail={"previous_state": record.lifecycle_state, "new_state": target_state},
        )
        await self._audit_log.append(audit_entry)

        event = _build_event(record, tenant_id, target_state)
        await self._observer.emit(event)

    def _effective_decay_days(
        self, record: MemoryRecord, policy: RetentionPolicy
    ) -> int | None:
        """Return effective decay threshold in days for *record*.

        Sector override (keyed by MemorySector value or enum member) takes
        precedence over the global decay_after_days. Returns None if no
        threshold is configured.
        """
        sector_key: str = (
            record.sector.value if hasattr(record.sector, "value") else str(record.sector)
        )
        override: Any = policy.sector_decay_overrides.get(sector_key)
        if override is None:
            override = policy.sector_decay_overrides.get(record.sector)
        if override is not None:
            return int(override)
        return policy.decay_after_days


_STATE_TO_AUDIT_OP: dict[LifecycleState, AuditOperation] = {
    LifecycleState.DECAYED: AuditOperation.DECAY,
    LifecycleState.ARCHIVED: AuditOperation.ARCHIVE,
    LifecycleState.DELETED: AuditOperation.DELETE,
}


def _build_event(
    record: MemoryRecord,
    tenant_id: TenantId,
    target_state: LifecycleState,
) -> MemoryDecayedEvent | MemoryArchivedEvent | MemoryDeletedEvent:
    if target_state == LifecycleState.DECAYED:
        return MemoryDecayedEvent(
            tenant_id=tenant_id,
            memory_id=record.id,
            scope=record.scope,
        )
    if target_state == LifecycleState.ARCHIVED:
        return MemoryArchivedEvent(
            tenant_id=tenant_id,
            memory_id=record.id,
            scope=record.scope,
        )
    return MemoryDeletedEvent(
        tenant_id=tenant_id,
        memory_id=record.id,
        scope=record.scope,
    )


def _ensure_utc(dt: datetime) -> datetime:
    """Return *dt* as UTC-aware; assume UTC if naive."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)

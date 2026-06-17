"""Tests for core domain entities: MemoryRecord, Fact, AuditEntry, MemoryTrace."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime

from memory_layer.domain.records import (
    AuditEntry,
    Fact,
    MemoryRecord,
    MemoryTrace,
    Scope,
)
from memory_layer.domain.types import (
    AuditOperation,
    AuditOutcome,
    EntityId,
    LifecycleState,
    MemorySector,
    PayloadType,
    PipelineStatus,
    PrincipalId,
    TenantId,
    new_audit_id,
    new_fact_id,
    new_memory_id,
    new_trace_id,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scope() -> Scope:
    return Scope(tenant_id=TenantId("t1"), principal_id=PrincipalId("u1"))


def _memory_record(**kwargs: object) -> MemoryRecord:
    defaults: dict = {
        "id": new_memory_id(),
        "tenant_id": TenantId("t1"),
        "scope": _scope(),
        "raw_payload": "hello world",
        "payload_type": PayloadType.CONVERSATION_TURN,
        "sector": MemorySector.EPISODIC,
    }
    defaults.update(kwargs)
    return MemoryRecord(**defaults)  # type: ignore[arg-type]


def _fact(**kwargs: object) -> Fact:
    defaults: dict = {
        "id": new_fact_id(),
        "memory_record_id": new_memory_id(),
        "tenant_id": TenantId("t1"),
        "scope": _scope(),
        "subject_entity_id": EntityId("entity-1"),
        "predicate": "works_at",
        "predicate_group": "employment",
        "object_value": "Acme Corp",
        "effective_from": datetime(2024, 1, 1),
    }
    defaults.update(kwargs)
    return Fact(**defaults)  # type: ignore[arg-type]


def _audit_entry(**kwargs: object) -> AuditEntry:
    defaults: dict = {
        "id": new_audit_id(),
        "tenant_id": TenantId("t1"),
        "scope": _scope(),
        "operation": AuditOperation.WRITE,
    }
    defaults.update(kwargs)
    return AuditEntry(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# MemoryRecord
# ---------------------------------------------------------------------------


class TestMemoryRecord:
    def test_lifecycle_state_defaults_to_active(self) -> None:
        """CRITICAL INVARIANT: records are searchable immediately on durable write."""
        rec = _memory_record()
        assert rec.lifecycle_state == LifecycleState.ACTIVE

    def test_pipeline_status_defaults_to_pending(self) -> None:
        rec = _memory_record()
        assert rec.pipeline_status == PipelineStatus.PENDING

    def test_is_immutable(self) -> None:
        rec = _memory_record()
        try:
            rec.lifecycle_state = LifecycleState.ARCHIVED  # type: ignore[misc]
            assert False, "Expected FrozenInstanceError"
        except FrozenInstanceError:
            pass

    def test_recorded_at_auto_populated(self) -> None:
        rec = _memory_record()
        assert rec.recorded_at is not None
        assert isinstance(rec.recorded_at, datetime)

    def test_metadata_not_shared_between_instances(self) -> None:
        r1 = _memory_record()
        r2 = _memory_record()
        assert r1.metadata is not r2.metadata


# ---------------------------------------------------------------------------
# Fact
# ---------------------------------------------------------------------------


class TestFact:
    def test_effective_to_defaults_to_none(self) -> None:
        f = _fact()
        assert f.effective_to is None

    def test_confidence_defaults_to_one(self) -> None:
        f = _fact()
        assert f.confidence == 1.0

    def test_is_immutable(self) -> None:
        f = _fact()
        try:
            f.confidence = 0.5  # type: ignore[misc]
            assert False, "Expected FrozenInstanceError"
        except FrozenInstanceError:
            pass

    def test_low_confidence(self) -> None:
        f = _fact(confidence=0.3)
        assert f.confidence < 0.6


# ---------------------------------------------------------------------------
# AuditEntry
# ---------------------------------------------------------------------------


class TestAuditEntry:
    def test_outcome_defaults_to_success(self) -> None:
        entry = _audit_entry()
        assert entry.outcome == AuditOutcome.SUCCESS

    def test_actor_defaults_to_system(self) -> None:
        entry = _audit_entry()
        assert entry.actor == "system"


# ---------------------------------------------------------------------------
# MemoryTrace
# ---------------------------------------------------------------------------


class TestMemoryTrace:
    def _make_trace(self) -> MemoryTrace:
        return MemoryTrace(
            trace_id=new_trace_id(),
            memory_id=new_memory_id(),
            scope=_scope(),
            write_event=_audit_entry(),
            enrichment_status=PipelineStatus.PENDING,
        )

    def test_is_mutable(self) -> None:
        trace = self._make_trace()
        trace.enrichment_status = PipelineStatus.ENRICHED
        assert trace.enrichment_status == PipelineStatus.ENRICHED

    def test_facts_derived_not_shared_between_instances(self) -> None:
        t1 = self._make_trace()
        t2 = self._make_trace()
        assert t1.facts_derived is not t2.facts_derived

    def test_query_plan_defaults_to_none(self) -> None:
        trace = self._make_trace()
        assert trace.query_plan is None

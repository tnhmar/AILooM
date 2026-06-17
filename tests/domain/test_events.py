"""Tests for the domain event catalog."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime

from memory_layer.domain.events import (
    ConsolidationJobCompletedEvent,
    ConsolidationJobStartedEvent,
    ContradictionDetectedEvent,
    ContradictionLowConfidenceEvent,
    EnrichmentFailedEvent,
    EventType,
    FactsExtractedEvent,
    MemoryArchivedEvent,
    MemoryConsolidatedEvent,
    MemoryDecayedEvent,
    MemoryDeletedEvent,
    MemoryEnrichedEvent,
    MemoryEvent,
    MemoryRecalledEvent,
    MemoryWrittenEvent,
    RecallNoMatchEvent,
    SchemaMigratedEvent,
    SessionEndedEvent,
    TenantIsolationViolationEvent,
)
from memory_layer.domain.types import TenantId

TID = TenantId("t-test")

# All concrete event classes for subclass / immutability bulk checks
ALL_CONCRETE: list[type[MemoryEvent]] = [
    MemoryWrittenEvent,
    MemoryEnrichedEvent,
    EnrichmentFailedEvent,
    FactsExtractedEvent,
    ContradictionDetectedEvent,
    ContradictionLowConfidenceEvent,
    MemoryRecalledEvent,
    RecallNoMatchEvent,
    MemoryConsolidatedEvent,
    MemoryDecayedEvent,
    MemoryArchivedEvent,
    MemoryDeletedEvent,
    SessionEndedEvent,
    ConsolidationJobStartedEvent,
    ConsolidationJobCompletedEvent,
    TenantIsolationViolationEvent,
    SchemaMigratedEvent,
]


class TestEventTypes:
    def test_memory_written_event_type(self) -> None:
        ev = MemoryWrittenEvent(tenant_id=TID)
        assert ev.event_type == EventType.MEMORY_WRITTEN

    def test_session_ended_event_type(self) -> None:
        ev = SessionEndedEvent(tenant_id=TID)
        assert ev.event_type == EventType.SESSION_ENDED

    def test_contradiction_detected_event_type(self) -> None:
        ev = ContradictionDetectedEvent(tenant_id=TID)
        assert ev.event_type == EventType.CONTRADICTION_DETECTED

    def test_tenant_isolation_violation_event_type(self) -> None:
        ev = TenantIsolationViolationEvent(tenant_id=TID)
        assert ev.event_type == EventType.TENANT_ISOLATION_VIOLATION

    def test_event_type_has_17_members(self) -> None:
        assert len(EventType) == 17


class TestEventIdentity:
    def test_all_events_have_non_empty_event_id(self) -> None:
        for cls in ALL_CONCRETE:
            ev = cls(tenant_id=TID)
            assert ev.event_id, f"{cls.__name__}.event_id must be non-empty"

    def test_two_instances_have_different_event_ids(self) -> None:
        ev1 = MemoryWrittenEvent(tenant_id=TID)
        ev2 = MemoryWrittenEvent(tenant_id=TID)
        assert ev1.event_id != ev2.event_id

    def test_occurred_at_auto_populated(self) -> None:
        for cls in ALL_CONCRETE:
            ev = cls(tenant_id=TID)
            assert isinstance(ev.occurred_at, datetime)


class TestEventHierarchy:
    def test_all_concrete_events_are_subclasses_of_memory_event(self) -> None:
        for cls in ALL_CONCRETE:
            assert issubclass(cls, MemoryEvent), f"{cls.__name__} must extend MemoryEvent"


class TestImmutability:
    def test_all_events_are_frozen(self) -> None:
        for cls in ALL_CONCRETE:
            ev = cls(tenant_id=TID)
            try:
                ev.event_id = "mutated"  # type: ignore[misc]
                assert False, f"{cls.__name__} should be frozen"
            except FrozenInstanceError:
                pass


class TestSpecificFields:
    def test_memory_written_event_constructs_with_required_args(self) -> None:
        from memory_layer.domain.records import Scope
        from memory_layer.domain.types import MemoryId, MemorySector, PipelineStatus, PrincipalId

        scope = Scope(tenant_id=TID, principal_id=PrincipalId("u1"))
        ev = MemoryWrittenEvent(
            tenant_id=TID,
            memory_id=MemoryId("m1"),
            scope=scope,
            sector=MemorySector.EPISODIC,
            pipeline_status=PipelineStatus.PENDING,
        )
        assert ev.memory_id == "m1"

    def test_facts_extracted_event_fact_ids_defaults_to_empty_tuple(self) -> None:
        ev = FactsExtractedEvent(tenant_id=TID)
        assert ev.fact_ids == ()
        assert isinstance(ev.fact_ids, tuple)

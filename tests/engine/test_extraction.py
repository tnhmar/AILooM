"""Acceptance tests for LLMExtractionService — M3-T2."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from memory_layer.domain.events import (
    ContradictionDetectedEvent,
    ContradictionLowConfidenceEvent,
    FactsExtractedEvent,
)
from memory_layer.domain.policies import ConflictResolutionPolicy
from memory_layer.domain.records import Fact, MemoryRecord, MemorySector, Scope
from memory_layer.domain.types import (
    EntityId,
    LifecycleState,
    MemoryId,
    PayloadType,
    PipelineStatus,
    PrincipalType,
    TenantId,
    new_fact_id,
    new_memory_id,
)
from memory_layer.engine.extraction import LLMExtractionService
from memory_layer.ports.outbound import ExtractionResult


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

TENANT = TenantId("tenant-ext")
_SCOPE = Scope(
    tenant_id=TENANT,
    principal_id="user-1",  # type: ignore[arg-type]
    principal_type=PrincipalType.USER,
)


def _make_record(payload: str = "User prefers dark mode.") -> MemoryRecord:
    return MemoryRecord(
        id=new_memory_id(),
        tenant_id=TENANT,
        scope=_SCOPE,
        raw_payload=payload,
        payload_type=PayloadType.CONVERSATION_TURN,
        sector=MemorySector.SEMANTIC,
        lifecycle_state=LifecycleState.ACTIVE,
        pipeline_status=PipelineStatus.PENDING,
        recorded_at=datetime.utcnow(),
    )


def _make_raw_fact(
    entity: str = "user-1",
    predicate: str = "prefers",
    predicate_group: str = "preference",
    object_value: str = "dark mode",
    confidence: float = 0.9,
    sector: str = "SEMANTIC",
) -> dict:
    return {
        "subject_entity_id": entity,
        "predicate": predicate,
        "predicate_group": predicate_group,
        "object_value": object_value,
        "confidence": confidence,
        "sector": sector,
    }


def _make_service(
    llm_response: str = "[]",
    existing_facts: list[Fact] | None = None,
    policy: ConflictResolutionPolicy | None = None,
) -> tuple[LLMExtractionService, AsyncMock, AsyncMock, AsyncMock]:
    llm = AsyncMock()
    llm.complete.return_value = llm_response

    fact_repo = AsyncMock()
    fact_repo.get_active_facts_by_entity_predicate.return_value = existing_facts or []

    observer = AsyncMock()

    svc = LLMExtractionService(
        llm_client=llm,
        fact_repo=fact_repo,
        observer=observer,
        policy=policy,
    )
    return svc, llm, fact_repo, observer


def _existing_fact(entity: str = "user-1", predicate_group: str = "preference") -> Fact:
    return Fact(
        id=new_fact_id(),
        memory_record_id=new_memory_id(),
        tenant_id=TENANT,
        scope=_SCOPE,
        subject_entity_id=EntityId(entity),
        predicate="prefers",
        predicate_group=predicate_group,
        object_value="light mode",
        effective_from=datetime.utcnow(),
        confidence=0.95,
        lifecycle_state=LifecycleState.ACTIVE,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# 1. Valid JSON with 2 facts returns 2 facts
@pytest.mark.asyncio
async def test_two_facts_extracted() -> None:
    raw = json.dumps([_make_raw_fact(), _make_raw_fact(predicate="dislikes", predicate_group="dislike")])
    svc, *_ = _make_service(llm_response=raw)
    result = await svc.extract(_make_record())
    assert len(result.facts) == 2
    assert result.error is None


# 2. Empty JSON array returns empty facts, no error
@pytest.mark.asyncio
async def test_empty_json_returns_no_facts() -> None:
    svc, *_ = _make_service(llm_response="[]")
    result = await svc.extract(_make_record())
    assert result.facts == []
    assert result.error is None


# 3. Invalid JSON returns error, empty facts
@pytest.mark.asyncio
async def test_invalid_json_sets_error() -> None:
    svc, *_ = _make_service(llm_response="not json at all")
    result = await svc.extract(_make_record())
    assert result.facts == []
    assert result.error is not None
    assert len(result.error) > 0


# 4. fact_repo.save called once per fact
@pytest.mark.asyncio
async def test_fact_repo_save_called_per_fact() -> None:
    raw = json.dumps([_make_raw_fact(), _make_raw_fact(predicate="uses", predicate_group="tool")])
    svc, _, fact_repo, _ = _make_service(llm_response=raw)
    await svc.extract(_make_record())
    assert fact_repo.save.await_count == 2


# 5. FactsExtractedEvent emitted via observer
@pytest.mark.asyncio
async def test_facts_extracted_event_emitted() -> None:
    raw = json.dumps([_make_raw_fact()])
    svc, _, _, observer = _make_service(llm_response=raw)
    await svc.extract(_make_record())
    emitted_types = [type(c[0][0]) for c in observer.emit.call_args_list]
    assert FactsExtractedEvent in emitted_types


# 6. High-confidence + existing fact → ContradictionDetectedEvent
@pytest.mark.asyncio
async def test_high_confidence_contradiction_emits_event() -> None:
    raw = json.dumps([_make_raw_fact(confidence=0.9)])
    policy = ConflictResolutionPolicy(low_confidence_threshold=0.6)
    existing = [_existing_fact()]
    svc, _, _, observer = _make_service(llm_response=raw, existing_facts=existing, policy=policy)
    await svc.extract(_make_record())
    emitted_types = [type(c[0][0]) for c in observer.emit.call_args_list]
    assert ContradictionDetectedEvent in emitted_types


# 7. High-confidence auto-close calls fact_repo.close_fact
@pytest.mark.asyncio
async def test_high_confidence_calls_close_fact() -> None:
    raw = json.dumps([_make_raw_fact(confidence=0.9)])
    policy = ConflictResolutionPolicy(low_confidence_threshold=0.6)
    existing = [_existing_fact()]
    svc, _, fact_repo, _ = _make_service(llm_response=raw, existing_facts=existing, policy=policy)
    await svc.extract(_make_record())
    fact_repo.close_fact.assert_awaited_once()


# 8. Low-confidence + existing fact → ContradictionLowConfidenceEvent
@pytest.mark.asyncio
async def test_low_confidence_emits_low_confidence_event() -> None:
    raw = json.dumps([_make_raw_fact(confidence=0.4)])
    policy = ConflictResolutionPolicy(low_confidence_threshold=0.6)
    existing = [_existing_fact()]
    svc, _, _, observer = _make_service(llm_response=raw, existing_facts=existing, policy=policy)
    await svc.extract(_make_record())
    emitted_types = [type(c[0][0]) for c in observer.emit.call_args_list]
    assert ContradictionLowConfidenceEvent in emitted_types


# 9. Low-confidence fact saved with lifecycle_state=PROPOSED
@pytest.mark.asyncio
async def test_low_confidence_fact_saved_as_proposed() -> None:
    raw = json.dumps([_make_raw_fact(confidence=0.4)])
    policy = ConflictResolutionPolicy(low_confidence_threshold=0.6)
    existing = [_existing_fact()]
    svc, _, fact_repo, _ = _make_service(llm_response=raw, existing_facts=existing, policy=policy)
    await svc.extract(_make_record())
    saved_fact: Fact = fact_repo.save.call_args[0][0]
    assert saved_fact.lifecycle_state == LifecycleState.PROPOSED


# 10. No existing fact → new fact saved as ACTIVE
@pytest.mark.asyncio
async def test_no_conflict_saves_active() -> None:
    raw = json.dumps([_make_raw_fact(confidence=0.9)])
    svc, _, fact_repo, _ = _make_service(llm_response=raw, existing_facts=[])
    await svc.extract(_make_record())
    saved_fact: Fact = fact_repo.save.call_args[0][0]
    assert saved_fact.lifecycle_state == LifecycleState.ACTIVE

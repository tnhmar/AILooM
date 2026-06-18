"""LLM extraction pipeline — LLMExtractionService.

Extracts structured :class:`~memory_layer.domain.records.Fact` objects from a
:class:`~memory_layer.domain.records.MemoryRecord` via an LLM, then runs
contradiction detection and auto-close resolution (ADR-011).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional, Protocol, runtime_checkable

from memory_layer.domain.events import (
    ContradictionDetectedEvent,
    ContradictionLowConfidenceEvent,
    FactsExtractedEvent,
)
from memory_layer.domain.policies import ConflictResolutionPolicy
from memory_layer.domain.records import Fact, MemoryRecord, MemorySector
from memory_layer.domain.types import (
    EntityId,
    FactId,
    LifecycleState,
    MemoryId,
    new_fact_id,
)
from memory_layer.ports.outbound import (
    ExtractionResult,
    FactRepositoryPort,
    ObserverPort,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM client port (defined inline per task spec)
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMClientPort(Protocol):
    """Minimal LLM interface required by the extraction service."""

    async def complete(self, system_prompt: str, user_prompt: str) -> str: ...


# ---------------------------------------------------------------------------
# Extraction prompt
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """
You are a memory extraction assistant. Given a text, extract structured facts.
Return a JSON array with fields:
  subject_entity_id, predicate, predicate_group, object_value,
  confidence (float 0-1), sector (str).
Return [] if no facts. Return ONLY valid JSON.
"""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class LLMExtractionService:
    """Concrete :class:`~memory_layer.ports.outbound.ExtractionPort` that uses
    an LLM to extract structured facts from a :class:`~memory_layer.domain.records.MemoryRecord`.

    Extraction flow
    ---------------
    1. Build a user prompt from ``record.raw_payload``.
    2. Call :meth:`LLMClientPort.complete`.
    3. Parse the JSON response into a list of raw fact dicts.
    4. For each raw fact:

       a. Construct a candidate :class:`~memory_layer.domain.records.Fact`
          (``effective_from=record.recorded_at``).
       b. Call :meth:`_resolve_contradiction` to handle any conflicts.
       c. Save the (possibly mutated) fact via
          :class:`~memory_layer.ports.outbound.FactRepositoryPort`.

    5. Emit :class:`~memory_layer.domain.events.FactsExtractedEvent`.
    6. Return :class:`~memory_layer.ports.outbound.ExtractionResult`.

    On JSON parse error: return ``ExtractionResult(error=..., facts=[])``;  the
    caller (``WriteMemoryService._enrich``) will set
    ``PARTIAL_ENRICHMENT_FAILED`` accordingly.

    Contradiction resolution
    ------------------------
    - No active fact on ``(entity_id, predicate_group)`` → save as ``ACTIVE``.
    - Active fact found **and** ``confidence >= threshold`` → AUTO_CLOSE:
      close the existing fact (``effective_to=now``), save candidate as
      ``ACTIVE``, emit
      :class:`~memory_layer.domain.events.ContradictionDetectedEvent`.
    - Active fact found **and** ``confidence < threshold`` → save candidate
      as ``PROPOSED``, emit
      :class:`~memory_layer.domain.events.ContradictionLowConfidenceEvent`.
    """

    def __init__(
        self,
        llm_client: LLMClientPort,
        fact_repo: FactRepositoryPort,
        observer: ObserverPort,
        policy: Optional[ConflictResolutionPolicy] = None,
    ) -> None:
        self._llm = llm_client
        self._fact_repo = fact_repo
        self._observer = observer
        self._policy = policy or ConflictResolutionPolicy()

    # ------------------------------------------------------------------
    # Public extraction entry point
    # ------------------------------------------------------------------

    async def extract(self, record: MemoryRecord) -> ExtractionResult:
        """Extract facts from *record* and return an :class:`ExtractionResult`."""
        user_prompt = f"Extract facts from the following text:\n\n{record.raw_payload}"

        try:
            raw_response = await self._llm.complete(
                system_prompt=EXTRACTION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )
            raw_facts: list[dict[str, Any]] = json.loads(raw_response)
            if not isinstance(raw_facts, list):
                raise ValueError("LLM response is not a JSON array")
        except Exception as exc:
            log.warning(
                "Fact extraction parse error for memory_id=%s: %s",
                record.id,
                exc,
                exc_info=True,
            )
            return ExtractionResult(
                memory_record_id=record.id,
                facts=[],
                entities=[],
                error=str(exc),
            )

        saved_facts: list[Fact] = []
        entity_ids: list[EntityId] = []

        for raw in raw_facts:
            candidate = self._build_candidate(raw, record)
            resolved = await self._resolve_contradiction(candidate, self._policy)
            await self._fact_repo.save(resolved)
            saved_facts.append(resolved)
            if resolved.subject_entity_id not in entity_ids:
                entity_ids.append(resolved.subject_entity_id)

        # Emit facts-extracted event.
        await self._observer.emit(
            FactsExtractedEvent(
                tenant_id=record.tenant_id,
                memory_id=record.id,
                fact_ids=tuple(f.id for f in saved_facts),
            )
        )

        return ExtractionResult(
            memory_record_id=record.id,
            facts=saved_facts,
            entities=entity_ids,
            error=None,
        )

    # ------------------------------------------------------------------
    # Contradiction resolution
    # ------------------------------------------------------------------

    async def _resolve_contradiction(
        self,
        candidate: Fact,
        policy: ConflictResolutionPolicy,
    ) -> Fact:
        """Resolve potential contradictions and return the fact to persist.

        Returns a new :class:`Fact` with the correct ``lifecycle_state``
        (either ``ACTIVE`` or ``PROPOSED``).
        """
        active_facts = await self._fact_repo.get_active_facts_by_entity_predicate(
            entity_id=candidate.subject_entity_id,
            predicate_group=candidate.predicate_group,
            tenant_id=candidate.tenant_id,
        )

        if not active_facts:
            # No conflict — activate immediately.
            return _with_lifecycle(candidate, LifecycleState.ACTIVE)

        existing = active_facts[0]  # most recent active fact for this predicate
        threshold = policy.low_confidence_threshold

        if candidate.confidence >= threshold:
            # HIGH confidence — AUTO_CLOSE: supersede the existing fact.
            now = datetime.utcnow()
            await self._fact_repo.close_fact(
                fact_id=existing.id,
                tenant_id=existing.tenant_id,
                effective_to=now,
                new_fact_id=candidate.id,
            )
            await self._observer.emit(
                ContradictionDetectedEvent(
                    tenant_id=candidate.tenant_id,
                    new_fact_id=candidate.id,
                    superseded_fact_id=existing.id,
                    entity_id=candidate.subject_entity_id,
                    predicate_group=candidate.predicate_group,
                )
            )
            return _with_lifecycle(
                _with_supersedes(candidate, existing.id),
                LifecycleState.ACTIVE,
            )
        else:
            # LOW confidence — flag as PROPOSED, do not close existing.
            await self._observer.emit(
                ContradictionLowConfidenceEvent(
                    tenant_id=candidate.tenant_id,
                    new_fact_id=candidate.id,
                    entity_id=candidate.subject_entity_id,
                    predicate_group=candidate.predicate_group,
                    confidence=candidate.confidence,
                )
            )
            return _with_lifecycle(candidate, LifecycleState.PROPOSED)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_candidate(self, raw: dict[str, Any], record: MemoryRecord) -> Fact:
        """Construct a candidate :class:`Fact` from a raw LLM-extracted dict."""
        sector_raw: str = raw.get("sector", MemorySector.SEMANTIC)
        try:
            sector = MemorySector(sector_raw)
        except ValueError:
            sector = MemorySector.SEMANTIC

        return Fact(
            id=new_fact_id(),
            memory_record_id=record.id,
            tenant_id=record.tenant_id,
            scope=record.scope,
            subject_entity_id=EntityId(str(raw.get("subject_entity_id", ""))),
            predicate=str(raw.get("predicate", "")),
            predicate_group=str(raw.get("predicate_group", "")),
            object_value=str(raw.get("object_value", "")),
            effective_from=record.recorded_at,
            confidence=float(raw.get("confidence", 1.0)),
            sector=sector,
            lifecycle_state=LifecycleState.ACTIVE,  # may be overridden by _resolve
        )


# ---------------------------------------------------------------------------
# Frozen-dataclass mutation helpers (return new instances)
# ---------------------------------------------------------------------------


def _with_lifecycle(fact: Fact, state: LifecycleState) -> Fact:
    """Return a copy of *fact* with ``lifecycle_state`` set to *state*."""
    from dataclasses import replace
    return replace(fact, lifecycle_state=state)


def _with_supersedes(fact: Fact, superseded_id: FactId) -> Fact:
    """Return a copy of *fact* with ``supersedes`` set to *superseded_id*."""
    from dataclasses import replace
    return replace(fact, supersedes=superseded_id)

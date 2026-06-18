"""Search and Recall use cases — SearchMemoryService, RecallMemoryService.

``SearchMemoryService`` executes a scored memory search and emits
:class:`~memory_layer.domain.events.MemoryRecalledEvent`.

``RecallMemoryService`` wraps search, applies a token budget, and packages
results into a context-window-ready
:class:`~memory_layer.domain.records.RecallResult`.
"""

from __future__ import annotations

import hashlib
import logging

from memory_layer.domain.events import MemoryRecalledEvent, RecallNoMatchEvent
from memory_layer.domain.records import (
    RecallItem,
    RecallRequest,
    RecallResult,
    RecallStatus,
    SearchRequest,
)
from memory_layer.domain.types import new_trace_id
from memory_layer.engine.planner import DefaultQueryPlanner
from memory_layer.engine.retrieval import RetrievalService
from memory_layer.ports.inbound import SearchResult
from memory_layer.ports.outbound import ObserverPort, TenantPolicyRepositoryPort

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _query_hash(query: str) -> str:
    """Return an 8-char hex digest of *query* for use in events."""
    return hashlib.sha256(query.encode()).hexdigest()[:8]


# ---------------------------------------------------------------------------
# SearchMemoryService
# ---------------------------------------------------------------------------


class SearchMemoryService:
    """Concrete :class:`~memory_layer.ports.inbound.SearchMemoryUseCase`.

    Execution flow
    --------------
    1. Load :class:`~memory_layer.domain.policies.SearchWeightsPolicy` from
       :class:`~memory_layer.ports.outbound.TenantPolicyRepositoryPort`.
    2. :class:`~memory_layer.engine.planner.DefaultQueryPlanner` builds a
       :class:`~memory_layer.engine.planner.QueryPlan`.
    3. :class:`~memory_layer.engine.retrieval.RetrievalService` executes the
       plan and returns scored items.
    4. Emit :class:`~memory_layer.domain.events.MemoryRecalledEvent`.
    5. Return :class:`~memory_layer.ports.inbound.SearchResult`.
    """

    def __init__(
        self,
        retrieval: RetrievalService,
        planner: DefaultQueryPlanner,
        policy_repo: TenantPolicyRepositoryPort,
        observer: ObserverPort,
        graph_available: bool = False,
    ) -> None:
        self._retrieval = retrieval
        self._planner = planner
        self._policy_repo = policy_repo
        self._observer = observer
        self._graph_available = graph_available

    async def execute(self, request: SearchRequest) -> SearchResult:
        """Run the search pipeline and return a :class:`SearchResult`."""
        # 1. Load tenant policies → extract search weights.
        policies = await self._policy_repo.get(request.tenant_id)
        weights = policies.search_weights

        # 2. Build query plan.
        plan = self._planner.plan(request, weights, self._graph_available)

        # 3. Execute retrieval.
        result = await self._retrieval.retrieve(request, plan)

        # 4. Emit event.
        await self._observer.emit(
            MemoryRecalledEvent(
                tenant_id=request.tenant_id,
                scope=request.scope,
                query_hash=_query_hash(request.query),
                items_returned=len(result.items),
                total_tokens_estimate=0,
                mode=str(request.mode),
            )
        )

        return result


# ---------------------------------------------------------------------------
# RecallMemoryService
# ---------------------------------------------------------------------------


class RecallMemoryService:
    """Concrete :class:`~memory_layer.ports.inbound.RecallMemoryUseCase`.

    Execution flow
    --------------
    1. Convert :class:`~memory_layer.domain.records.RecallRequest` into a
       :class:`~memory_layer.domain.records.SearchRequest` with
       ``k = max_items * 2`` for headroom.
    2. Delegate to :class:`SearchMemoryService`.
    3. Empty results → :class:`~memory_layer.domain.records.RecallResult`
       with ``status=NO_MATCH``; emit
       :class:`~memory_layer.domain.events.RecallNoMatchEvent`.
    4. Package items respecting ``max_tokens`` and ``max_items``:

       - Token estimate: ``int(len(content.split()) * 1.3)``
       - ``explanation = f"Retrieved via {mode} search, score={score:.3f}"``
       - ``trace_id = new_trace_id()`` per item.

    5. ``status = MATCH`` when all ``max_items`` were included;
       ``PARTIAL_MATCH`` when the token budget cut the list short.
    6. Emit :class:`~memory_layer.domain.events.MemoryRecalledEvent`.
    7. Return :class:`~memory_layer.domain.records.RecallResult`.
    """

    def __init__(
        self,
        search_service: SearchMemoryService,
        observer: ObserverPort,
    ) -> None:
        self._search = search_service
        self._observer = observer

    async def execute(self, request: RecallRequest) -> RecallResult:
        """Run the recall pipeline and return a :class:`RecallResult`."""
        # 1. Convert to SearchRequest with extra headroom.
        search_req = SearchRequest(
            tenant_id=request.tenant_id,
            scope=request.scope,
            query=request.query,
            mode=request.mode,
            sectors=request.sectors,
            k=request.max_items * 2,
        )

        # 2. Search.
        search_result = await self._search.execute(search_req)

        # 3. No results.
        if not search_result.items:
            await self._observer.emit(
                RecallNoMatchEvent(
                    tenant_id=request.tenant_id,
                    scope=request.scope,
                    query_hash=_query_hash(request.query),
                    reason="No matching memories found.",
                )
            )
            return RecallResult(
                status=RecallStatus.NO_MATCH,
                no_match_reason="No matching memories found.",
                recall_strategy=str(request.mode),
            )

        # 4. Package with token budget.
        items: list[RecallItem] = []
        total_tokens = 0
        token_cut = False
        max_tokens = request.max_tokens  # may be None → unlimited

        for search_item in search_result.items[: request.max_items * 2]:
            if len(items) >= request.max_items:
                break

            token_est = self._estimate_tokens(search_item.content)
            if max_tokens is not None and total_tokens + token_est > max_tokens:
                token_cut = True
                break

            total_tokens += token_est
            items.append(
                RecallItem(
                    memory_id=search_item.memory_id,
                    content=search_item.content,
                    sector=search_item.sector,
                    lifecycle_state=search_item.lifecycle_state,
                    pipeline_status=search_item.pipeline_status,
                    signals=search_item.signals,
                    explanation=(
                        f"Retrieved via {request.mode} search, "
                        f"score={search_item.score:.3f}"
                    ),
                    trace_id=new_trace_id(),
                )
            )

        # 5. Determine status.
        if token_cut:
            status = RecallStatus.PARTIAL_MATCH
        elif len(items) == request.max_items:
            status = RecallStatus.MATCH
        elif items:
            status = RecallStatus.MATCH
        else:
            status = RecallStatus.NO_MATCH

        # 6. Emit.
        await self._observer.emit(
            MemoryRecalledEvent(
                tenant_id=request.tenant_id,
                scope=request.scope,
                query_hash=_query_hash(request.query),
                items_returned=len(items),
                total_tokens_estimate=total_tokens,
                mode=str(request.mode),
            )
        )

        # 7. Return.
        return RecallResult(
            status=status,
            items=items,
            total_tokens_estimate=total_tokens,
            recall_strategy=str(request.mode),
        )

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimate: word-count × 1.3."""
        return int(len(text.split()) * 1.3)

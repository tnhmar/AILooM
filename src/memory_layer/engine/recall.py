"""Search and Recall use cases — SearchMemoryService, RecallMemoryService (M3-T5)."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from uuid import uuid4

from memory_layer.domain.events import MemoryRecalledEvent, RecallNoMatchEvent
from memory_layer.domain.records import (
    RecallItem,
    RecallRequest,
    RecallResult,
    RecallStatus,
    SearchRequest,
)
from memory_layer.domain.types import TraceId
from memory_layer.engine.planner import DefaultQueryPlanner
from memory_layer.engine.retrieval import RetrievalService
from memory_layer.ports.inbound import SearchResult
from memory_layer.ports.outbound import ObserverPort, TenantPolicyRepositoryPort

log = logging.getLogger(__name__)


class SearchMemoryService:
    """Concrete implementation of SearchMemoryUseCase.

    Delegates low-level retrieval to :class:`~memory_layer.engine.retrieval.RetrievalService`,
    applies tenant policy weights via the planner, and emits a
    :class:`~memory_layer.domain.events.MemoryRecalledEvent` on every successful search.
    """

    def __init__(
        self,
        retrieval: RetrievalService,
        planner: DefaultQueryPlanner,
        policy_repo: TenantPolicyRepositoryPort,
        observer: ObserverPort,
    ) -> None:
        self._retrieval = retrieval
        self._planner = planner
        self._policy_repo = policy_repo
        self._observer = observer

    async def execute(self, request: SearchRequest) -> SearchResult:
        """Execute a search request and return ranked results."""
        policies = await self._policy_repo.get(request.tenant_id)
        plan = self._planner.plan(
            request,
            policies.search_weights,
            graph_available=False,
        )
        result = await self._retrieval.retrieve(request, plan)

        trace_id = TraceId(str(uuid4()))  # type: ignore[call-arg]
        await self._observer.emit(
            MemoryRecalledEvent(
                tenant_id=request.tenant_id,
                scope=request.scope,
                query_hash=hashlib.sha256(request.query.encode()).hexdigest()[:16],
                items_returned=len(result.items),
                total_tokens_estimate=sum(len(i.content) // 4 for i in result.items),
                mode=str(request.mode),
                trace_id=trace_id,
            )
        )
        return result


class RecallMemoryService:
    """Wraps :class:`SearchMemoryService` and enforces context-window budgets.

    Applies ``max_items`` and ``max_tokens`` limits, sets the appropriate
    :class:`~memory_layer.domain.records.RecallStatus`, and emits a
    :class:`~memory_layer.domain.events.RecallNoMatchEvent` when no results are found.
    """

    def __init__(
        self,
        search_service: SearchMemoryService,
        observer: ObserverPort,
    ) -> None:
        self._search = search_service
        self._observer = observer

    async def execute(self, request: RecallRequest) -> RecallResult:
        """Recall memory items within the supplied token/item budget."""
        search_req = SearchRequest(
            tenant_id=request.tenant_id,
            scope=request.scope,
            query=request.query,
            mode=request.mode,
            sectors=request.sectors,
            k=request.max_items,
        )
        search_result = await self._search.execute(search_req)

        if not search_result.items:
            await self._observer.emit(
                RecallNoMatchEvent(
                    tenant_id=request.tenant_id,
                    scope=request.scope,
                    query_hash=hashlib.sha256(request.query.encode()).hexdigest()[:16],
                    reason="No active memories found for this query.",
                )
            )
            return RecallResult(
                status=RecallStatus.NO_MATCH,
                items=[],
                recalled_at=datetime.utcnow(),
                no_match_reason="No active memories found for this query.",
            )

        trace_id = TraceId(str(uuid4()))  # type: ignore[call-arg]
        mode_str = str(request.mode)
        items: list[RecallItem] = []
        total_tokens = 0
        token_budget_exhausted = False

        for item in search_result.items[: request.max_items]:
            item_tokens = self._estimate_tokens(item.content)
            if (
                request.max_tokens is not None
                and total_tokens + item_tokens > request.max_tokens
            ):
                token_budget_exhausted = True
                break
            total_tokens += item_tokens
            items.append(
                RecallItem(
                    memory_id=item.memory_id,
                    content=item.content,
                    sector=item.sector,
                    lifecycle_state=item.lifecycle_state,
                    pipeline_status=item.pipeline_status,
                    signals=item.signals,
                    explanation=f"Retrieved via search mode {mode_str}",
                    trace_id=trace_id,
                )
            )

        if not items:
            await self._observer.emit(
                RecallNoMatchEvent(
                    tenant_id=request.tenant_id,
                    scope=request.scope,
                    query_hash=hashlib.sha256(request.query.encode()).hexdigest()[:16],
                    reason="Token budget too small for any result.",
                )
            )
            return RecallResult(
                status=RecallStatus.NO_MATCH,
                items=[],
                total_tokens_estimate=0,
                recall_strategy=mode_str,
                recalled_at=datetime.utcnow(),
                no_match_reason="Token budget too small for any result.",
            )

        status = RecallStatus.PARTIAL_MATCH if token_budget_exhausted else RecallStatus.MATCH

        return RecallResult(
            status=status,
            items=items,
            total_tokens_estimate=total_tokens,
            recall_strategy=mode_str,
            recalled_at=datetime.utcnow(),
        )

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimate: 1 token ≈ 4 characters."""
        return max(1, len(text) // 4)

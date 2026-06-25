"""Retrieval layer — RetrievalService and reciprocal_rank_fusion (M3-T4)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from memory_layer.domain.exceptions import CapabilityNotAvailableError
from memory_layer.domain.records import SearchMode, SearchRequest
from memory_layer.engine.planner import IndexTarget, QueryPlan
from memory_layer.ports.inbound import SearchResult, SearchResultItem
from memory_layer.ports.outbound import (
    EmbeddingPort,
    FullTextIndexPort,
    GraphPort,
    VectorIndexPort,
)

log = logging.getLogger(__name__)


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    weights: list[float] | None = None,
    k: int = 60,
) -> list[tuple[str, float]]:
    """Merge multiple ranked ID lists into a single scored ranking via RRF.

    Args:
        ranked_lists: Each inner list is an ordered sequence of memory IDs,
            highest-ranked first.
        weights: Optional per-list multiplier (default 1.0 for every list).
        k: RRF smoothing constant (default 60).

    Returns:
        List of ``(memory_id, rrf_score)`` tuples, highest score first.
    """
    if not ranked_lists:
        return []

    if weights is None:
        weights = [1.0] * len(ranked_lists)

    scores: dict[str, float] = {}
    for ranked, weight in zip(ranked_lists, weights):
        for rank, mid in enumerate(ranked):
            scores[mid] = scores.get(mid, 0.0) + weight / (k + rank + 1)

    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


class RetrievalService:
    """Orchestrates vector, full-text, and graph retrieval into a unified ranking.

    Instantiated once per request (or shared as a singleton) and called via
    :meth:`retrieve`.  The caller supplies a :class:`~memory_layer.engine.planner.QueryPlan`
    that dictates which indexes to hit and with what weights.
    """

    def __init__(
        self,
        embedding_port: EmbeddingPort,
        vector_index: VectorIndexPort,
        full_text_index: FullTextIndexPort,
        graph_port: GraphPort | None = None,
    ) -> None:
        self._embed = embedding_port
        self._vector = vector_index
        self._fts = full_text_index
        self._graph = graph_port

    async def retrieve(self, request: SearchRequest, plan: QueryPlan) -> SearchResult:
        """Execute the retrieval plan and return a merged, ranked SearchResult."""
        from memory_layer.domain.types import LifecycleState, MemorySector, PipelineStatus

        targets = plan.targets
        k_per = plan.k_per_index
        final_k = plan.final_k

        # guard: graph requires the port
        if IndexTarget.GRAPH in targets and self._graph is None:
            raise CapabilityNotAvailableError("graph")

        # parallel fetch
        tasks: dict[IndexTarget, Any] = {}

        if IndexTarget.VECTOR in targets:
            embeddings = await self._embed.embed([request.query])
            tasks[IndexTarget.VECTOR] = self._vector.search(
                query_embedding=embeddings[0],
                tenant_id=request.tenant_id,
                k=k_per,
                filters={},
            )

        if IndexTarget.FULL_TEXT in targets:
            tasks[IndexTarget.FULL_TEXT] = self._fts.search(
                query=request.query,
                tenant_id=request.tenant_id,
                k=k_per,
                filters={},
            )

        gathered: dict[IndexTarget, list[Any]] = {}
        if tasks:
            keys = list(tasks.keys())
            results = await asyncio.gather(*[tasks[k] for k in keys])
            gathered = dict(zip(keys, results))

        # build per-source ranked ID lists
        ranked_lists: list[list[str]] = []
        weights: list[float] = []
        content_map: dict[str, str] = {}
        score_map: dict[str, float] = {}

        if IndexTarget.VECTOR in gathered:
            vec_results = gathered[IndexTarget.VECTOR]
            ranked_lists.append([str(r.memory_id) for r in vec_results])
            weights.append(plan.weights.get("semantic_weight", 1.0))
            for r in vec_results:
                content_map[str(r.memory_id)] = r.content
                score_map[str(r.memory_id)] = r.score

        if IndexTarget.FULL_TEXT in gathered:
            fts_results = gathered[IndexTarget.FULL_TEXT]
            ranked_lists.append([str(r.memory_id) for r in fts_results])
            weights.append(plan.weights.get("keyword_weight", 1.0))
            for r in fts_results:
                content_map.setdefault(str(r.memory_id), r.content)
                score_map.setdefault(str(r.memory_id), r.score)

        # merge and trim
        if len(ranked_lists) > 1:
            merged = reciprocal_rank_fusion(ranked_lists, weights=weights)
        elif len(ranked_lists) == 1:
            merged = [(mid, score_map.get(mid, 1.0)) for mid in ranked_lists[0]]
        else:
            merged = []

        merged = merged[:final_k]

        items = [
            SearchResultItem(
                memory_id=mid,  # type: ignore[arg-type]
                content=content_map.get(mid, ""),
                sector=MemorySector.SEMANTIC,
                score=score,
                pipeline_status=PipelineStatus.ENRICHED,
                lifecycle_state=LifecycleState.ACTIVE,
            )
            for mid, score in merged
        ]

        return SearchResult(items=items, total=len(items), query_plan=plan)

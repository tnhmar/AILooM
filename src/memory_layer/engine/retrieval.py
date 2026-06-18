"""Parallel retrieval and RRF fusion — RetrievalService.

Executes a :class:`~memory_layer.engine.planner.QueryPlan` by running all
required index queries in parallel via ``asyncio.gather``, fusing results
with Reciprocal Rank Fusion, and optionally applying LLM reranking.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from memory_layer.domain.exceptions import CapabilityNotAvailableError
from memory_layer.domain.types import LifecycleState, MemoryId, MemorySector, PipelineStatus
from memory_layer.engine.planner import IndexTarget, QueryPlan
from memory_layer.ports.inbound import SearchResult, SearchResultItem
from memory_layer.ports.outbound import (
    EmbeddingPort,
    FullTextIndexPort,
    GraphPort,
    VectorIndexPort,
)
from memory_layer.domain.records import SearchRequest

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RRF — pure, module-level
# ---------------------------------------------------------------------------


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    k: int = 60,
    weights: list[float] | None = None,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion over multiple ranked lists of memory IDs.

    Parameters
    ----------
    ranked_lists:
        Each inner list is a ranking of ``memory_id`` strings, best-first.
    k:
        RRF smoothing constant (default 60 per the original paper).
    weights:
        Per-list multiplicative weights.  ``None`` → all weights = 1.0.

    Returns
    -------
    List of ``(memory_id, rrf_score)`` sorted by score descending.
    ``score(d) = Σ_i  weight_i / (k + rank_i(d))``  where rank is 1-based.
    """
    if not ranked_lists:
        return []

    effective_weights: list[float] = (
        weights if weights is not None else [1.0] * len(ranked_lists)
    )

    scores: dict[str, float] = {}
    for ranked, w in zip(ranked_lists, effective_weights):
        for rank_zero, memory_id in enumerate(ranked):
            rank_one = rank_zero + 1
            scores[memory_id] = scores.get(memory_id, 0.0) + w / (k + rank_one)

    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


# ---------------------------------------------------------------------------
# RetrievalService
# ---------------------------------------------------------------------------


class RetrievalService:
    """Execute a :class:`~memory_layer.engine.planner.QueryPlan` and return
    a ranked :class:`~memory_layer.ports.inbound.SearchResult`.

    Retrieval flow
    --------------
    1. Embed query text (only when ``VECTOR`` is in ``plan.targets``).
    2. Launch all required index coroutines in parallel with
       ``asyncio.gather``.
    3. RRF-fuse the ranked ID lists using ``plan.weights``.
    4. Trim fused list to ``plan.final_k``.
    5. Build :class:`~memory_layer.ports.inbound.SearchResultItem` per entry.
    6. If ``plan.use_llm_rerank``: log a warning (stub — no reranker wired).
    7. Return :class:`~memory_layer.ports.inbound.SearchResult`.
    """

    def __init__(
        self,
        embedding_port: EmbeddingPort,
        vector_index: VectorIndexPort,
        full_text_index: FullTextIndexPort,
        graph_port: GraphPort | None = None,
    ) -> None:
        self._embedding = embedding_port
        self._vector = vector_index
        self._full_text = full_text_index
        self._graph = graph_port

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def retrieve(
        self, request: SearchRequest, plan: QueryPlan
    ) -> SearchResult:
        """Execute *plan* against the available indexes and return fused results."""
        targets = plan.targets

        # Validate GRAPH capability up front.
        if IndexTarget.GRAPH in targets and self._graph is None:
            raise CapabilityNotAvailableError(
                "QueryPlan requests GRAPH index but no GraphPort is wired."
            )

        # 1. Embed if needed.
        embedding: list[float] = []
        if IndexTarget.VECTOR in targets:
            vectors = await self._embedding.embed([request.query_text])
            embedding = vectors[0]

        # 2. Build coroutines for each target.
        coros: list[Any] = []
        coro_targets: list[IndexTarget] = []

        for target in targets:
            if target == IndexTarget.VECTOR:
                coros.append(self._vector_search(embedding, plan, request))
                coro_targets.append(target)
            elif target == IndexTarget.FULL_TEXT:
                coros.append(self._full_text_search(plan, request))
                coro_targets.append(target)
            elif target == IndexTarget.GRAPH:
                coros.append(self._graph_search(plan, request))
                coro_targets.append(target)
            elif target == IndexTarget.TEMPORAL:
                # Temporal re-ranking is applied post-fusion; no dedicated index
                # query here — fall through to the vector/full-text results.
                pass

        # 3. Execute in parallel.
        raw_results: list[list[str]] = list(await asyncio.gather(*coros))

        # 4. RRF weights — map list order to plan.weights values.
        rrf_weights = _extract_rrf_weights(plan, coro_targets)

        fused = reciprocal_rank_fusion(raw_results, weights=rrf_weights)

        # 5. Trim to final_k.
        fused = fused[: plan.final_k]

        # 6. Build result items (scores come from RRF, content from index cache).
        items = _build_items(fused, raw_results, coro_targets, plan)

        # 7. LLM rerank stub.
        if plan.use_llm_rerank:
            log.warning(
                "LLM rerank requested by plan but no reranker is wired; skipping."
            )

        return SearchResult(
            items=items,
            total=len(items),
            query_plan=plan,
        )

    # ------------------------------------------------------------------
    # Index sub-queries
    # ------------------------------------------------------------------

    async def _vector_search(
        self,
        embedding: list[float],
        plan: QueryPlan,
        request: SearchRequest,
    ) -> list[str]:
        results = await self._vector.search(
            query_embedding=embedding,
            tenant_id=request.tenant_id,
            k=plan.k_per_index,
            filters={},
        )
        return [str(r.memory_id) for r in results]

    async def _full_text_search(
        self,
        plan: QueryPlan,
        request: SearchRequest,
    ) -> list[str]:
        results = await self._full_text.search(
            query=request.query_text,
            tenant_id=request.tenant_id,
            k=plan.k_per_index,
            filters={},
        )
        return [str(r.memory_id) for r in results]

    async def _graph_search(
        self,
        plan: QueryPlan,
        request: SearchRequest,
    ) -> list[str]:
        # GraphPort.query is generic Cypher; search-specific Cypher is
        # intentionally left to a future adapter. Here we issue a basic
        # full-text-style match as a placeholder.
        assert self._graph is not None
        rows = await self._graph.query(
            cypher="MATCH (m:Memory {tenant_id: $tid}) RETURN m.id AS id LIMIT $k",
            params={"tid": str(request.tenant_id), "k": plan.k_per_index},
            tenant_id=request.tenant_id,
        )
        return [str(row["id"]) for row in rows if "id" in row]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _extract_rrf_weights(
    plan: QueryPlan, coro_targets: list[IndexTarget]
) -> list[float]:
    """Map each coroutine target to its weight from ``plan.weights``."""
    target_key_map: dict[IndexTarget, str] = {
        IndexTarget.VECTOR: "semantic_weight",
        IndexTarget.FULL_TEXT: "keyword_weight",
        IndexTarget.GRAPH: "graph_weight",
        IndexTarget.TEMPORAL: "temporal_weight",
    }
    out: list[float] = []
    for t in coro_targets:
        key = target_key_map.get(t)
        out.append(float(plan.weights.get(key, 1.0)) if key else 1.0)
    return out


def _build_items(
    fused: list[tuple[str, float]],
    raw_results: list[list[str]],
    coro_targets: list[IndexTarget],
    plan: QueryPlan,
) -> list[SearchResultItem]:
    """Construct :class:`SearchResultItem` objects from RRF-fused IDs."""
    # Determine which target list to pull content from (prefer VECTOR then FULL_TEXT).
    content_idx: int | None = None
    for preferred in (IndexTarget.VECTOR, IndexTarget.FULL_TEXT, IndexTarget.GRAPH):
        if preferred in coro_targets:
            content_idx = coro_targets.index(preferred)
            break

    items: list[SearchResultItem] = []
    for memory_id_str, score in fused:
        mid = MemoryId(memory_id_str)  # type: ignore[call-arg]
        items.append(
            SearchResultItem(
                memory_id=mid,
                content="",  # content hydration is done by a separate read-path
                sector=MemorySector.SEMANTIC,
                score=score,
                pipeline_status=PipelineStatus.ENRICHED,
                lifecycle_state=LifecycleState.ACTIVE,
                signals={"rrf_score": score, "plan_mode": plan.mode.value},
            )
        )
    return items

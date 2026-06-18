"""Query planner — DefaultQueryPlanner.

Pure orchestration logic: maps a :class:`~memory_layer.domain.records.SearchRequest`
to a :class:`QueryPlan` based on :class:`~memory_layer.domain.records.SearchMode`
and :class:`~memory_layer.domain.policies.SearchWeightsPolicy` (ADR-010).
No I/O whatsoever.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from memory_layer.domain.exceptions import CapabilityNotAvailableError
from memory_layer.domain.policies import SearchWeightsPolicy
from memory_layer.domain.records import SearchMode, SearchRequest, TemporalFilter


# ---------------------------------------------------------------------------
# Index targets
# ---------------------------------------------------------------------------


class IndexTarget(StrEnum):
    VECTOR = "VECTOR"
    FULL_TEXT = "FULL_TEXT"
    GRAPH = "GRAPH"
    TEMPORAL = "TEMPORAL"


# ---------------------------------------------------------------------------
# Query plan
# ---------------------------------------------------------------------------


@dataclass
class QueryPlan:
    """Fully resolved plan passed downstream to index adapters."""

    mode: SearchMode
    targets: list[IndexTarget]
    weights: dict[str, float]
    use_llm_rerank: bool = False
    temporal_filter: TemporalFilter | None = None
    k_per_index: int = 20
    final_k: int = 10
    explanation: str = ""


# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------


@runtime_checkable
class QueryPlannerPort(Protocol):
    """Interface any query planner must satisfy."""

    def plan(
        self,
        request: SearchRequest,
        weights: SearchWeightsPolicy,
        graph_available: bool,
    ) -> QueryPlan: ...


# ---------------------------------------------------------------------------
# Default planner
# ---------------------------------------------------------------------------


class DefaultQueryPlanner:
    """Mode-driven planner implementing ADR-010 routing rules.

    Mode → Index targets mapping
    ----------------------------
    - ``SEMANTIC``         → ``[VECTOR]``
    - ``KEYWORD``          → ``[FULL_TEXT]``
    - ``HYBRID``           → ``[VECTOR, FULL_TEXT]``
    - ``HYBRID_TEMPORAL``  → ``[VECTOR, FULL_TEXT, TEMPORAL]``
    - ``QUALITY``          → ``[VECTOR, FULL_TEXT, TEMPORAL]`` + LLM rerank
    - ``GRAPH``            → ``[GRAPH]`` (raises if ``graph_available=False``)

    ``k_per_index`` is set to ``max(final_k * 3, 20)`` to ensure sufficient
    candidates for RRF fusion before truncation to ``final_k``.
    """

    def plan(
        self,
        request: SearchRequest,
        weights: SearchWeightsPolicy,
        graph_available: bool,
    ) -> QueryPlan:
        """Return a :class:`QueryPlan` for *request*.

        Raises
        ------
        CapabilityNotAvailableError
            When ``request.mode`` is ``GRAPH`` and ``graph_available`` is ``False``.
        """
        mode = request.mode
        final_k: int = getattr(request, "k", 10) or 10
        k_per_index = max(final_k * 3, 20)

        if mode == SearchMode.SEMANTIC:
            targets = [IndexTarget.VECTOR]
            w = {"semantic_weight": weights.semantic_weight}
            use_rerank = False
            explanation = (
                "SEMANTIC mode: dense vector search only; "
                f"semantic_weight={weights.semantic_weight}."
            )

        elif mode == SearchMode.KEYWORD:
            targets = [IndexTarget.FULL_TEXT]
            w = {"keyword_weight": weights.keyword_weight}
            use_rerank = False
            explanation = (
                "KEYWORD mode: full-text BM25 search only; "
                f"keyword_weight={weights.keyword_weight}."
            )

        elif mode == SearchMode.HYBRID:
            targets = [IndexTarget.VECTOR, IndexTarget.FULL_TEXT]
            w = {
                "semantic_weight": weights.semantic_weight,
                "keyword_weight": weights.keyword_weight,
            }
            use_rerank = False
            explanation = (
                "HYBRID mode: RRF fusion of vector + full-text; "
                f"semantic={weights.semantic_weight}, "
                f"keyword={weights.keyword_weight}."
            )

        elif mode == SearchMode.HYBRID_TEMPORAL:
            targets = [
                IndexTarget.VECTOR,
                IndexTarget.FULL_TEXT,
                IndexTarget.TEMPORAL,
            ]
            w = {
                "semantic_weight": weights.semantic_weight,
                "keyword_weight": weights.keyword_weight,
                "recency_weight": weights.recency_weight,
            }
            use_rerank = False
            explanation = (
                "HYBRID_TEMPORAL mode: RRF fusion of vector + full-text + temporal decay; "
                f"semantic={weights.semantic_weight}, "
                f"keyword={weights.keyword_weight}, "
                f"recency={weights.recency_weight}."
            )

        elif mode == SearchMode.QUALITY:
            targets = [
                IndexTarget.VECTOR,
                IndexTarget.FULL_TEXT,
                IndexTarget.TEMPORAL,
            ]
            w = {
                "semantic_weight": weights.semantic_weight,
                "keyword_weight": weights.keyword_weight,
                "recency_weight": weights.recency_weight,
            }
            use_rerank = True
            explanation = (
                "QUALITY mode: full RRF fusion + LLM rerank for highest precision; "
                f"semantic={weights.semantic_weight}, "
                f"keyword={weights.keyword_weight}, "
                f"recency={weights.recency_weight}."
            )

        elif mode == SearchMode.GRAPH:
            if not graph_available:
                raise CapabilityNotAvailableError(
                    "GRAPH search mode requested but no graph index is available."
                )
            targets = [IndexTarget.GRAPH]
            w = {"entity_weight": weights.entity_weight}
            use_rerank = False
            explanation = (
                "GRAPH mode: graph traversal search; "
                f"entity_weight={weights.entity_weight}."
            )

        else:
            # Defensive fallback for any future SearchMode values.
            targets = [IndexTarget.VECTOR]
            w = {"semantic_weight": weights.semantic_weight}
            use_rerank = False
            explanation = f"Unknown mode {mode!r}; defaulting to VECTOR."

        return QueryPlan(
            mode=mode,
            targets=targets,
            weights=w,
            use_llm_rerank=use_rerank,
            temporal_filter=request.temporal_filter,
            k_per_index=k_per_index,
            final_k=final_k,
            explanation=explanation,
        )

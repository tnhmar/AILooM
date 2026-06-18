"""Acceptance tests for DefaultQueryPlanner — M3-T3."""

from __future__ import annotations

import pytest

from memory_layer.domain.exceptions import CapabilityNotAvailableError
from memory_layer.domain.policies import SearchWeightsPolicy
from memory_layer.domain.records import (
    SearchMode,
    SearchRequest,
    Scope,
    TemporalFilter,
)
from memory_layer.domain.types import PrincipalType, TenantId
from memory_layer.engine.planner import (
    DefaultQueryPlanner,
    IndexTarget,
    QueryPlan,
    QueryPlannerPort,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

TENANT = TenantId("tenant-planner")
_SCOPE = Scope(
    tenant_id=TENANT,
    principal_id="user-1",  # type: ignore[arg-type]
    principal_type=PrincipalType.USER,
)
_WEIGHTS = SearchWeightsPolicy()
_PLANNER = DefaultQueryPlanner()


def _req(mode: SearchMode, top_k: int = 10, temporal_filter: TemporalFilter | None = None) -> SearchRequest:
    return SearchRequest(
        tenant_id=TENANT,
        scope=_SCOPE,
        query_text="test query",
        mode=mode,
        top_k=top_k,
        temporal_filter=temporal_filter,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# 1. SEMANTIC → [VECTOR], no rerank
def test_semantic_targets_vector() -> None:
    plan = _PLANNER.plan(_req(SearchMode.SEMANTIC), _WEIGHTS, graph_available=False)
    assert plan.targets == [IndexTarget.VECTOR]
    assert plan.use_llm_rerank is False


# 2. KEYWORD → [FULL_TEXT]
def test_keyword_targets_full_text() -> None:
    plan = _PLANNER.plan(_req(SearchMode.KEYWORD), _WEIGHTS, graph_available=False)
    assert plan.targets == [IndexTarget.FULL_TEXT]


# 3. HYBRID → VECTOR + FULL_TEXT
def test_hybrid_targets_vector_and_full_text() -> None:
    plan = _PLANNER.plan(_req(SearchMode.HYBRID), _WEIGHTS, graph_available=False)
    assert IndexTarget.VECTOR in plan.targets
    assert IndexTarget.FULL_TEXT in plan.targets


# 4. HYBRID_TEMPORAL → includes TEMPORAL
def test_hybrid_temporal_includes_temporal() -> None:
    plan = _PLANNER.plan(_req(SearchMode.HYBRID_TEMPORAL), _WEIGHTS, graph_available=False)
    assert IndexTarget.TEMPORAL in plan.targets


# 5. QUALITY → VECTOR + FULL_TEXT + TEMPORAL
def test_quality_includes_all_three() -> None:
    plan = _PLANNER.plan(_req(SearchMode.QUALITY), _WEIGHTS, graph_available=False)
    assert IndexTarget.VECTOR in plan.targets
    assert IndexTarget.FULL_TEXT in plan.targets
    assert IndexTarget.TEMPORAL in plan.targets


# 6. GRAPH + available → [GRAPH]
def test_graph_available_targets_graph() -> None:
    plan = _PLANNER.plan(_req(SearchMode.GRAPH), _WEIGHTS, graph_available=True)
    assert plan.targets == [IndexTarget.GRAPH]


# 7. GRAPH + unavailable → CapabilityNotAvailableError
def test_graph_unavailable_raises() -> None:
    with pytest.raises(CapabilityNotAvailableError):
        _PLANNER.plan(_req(SearchMode.GRAPH), _WEIGHTS, graph_available=False)


# 8. k_per_index >= max(final_k * 3, 20) for all modes
@pytest.mark.parametrize(
    "mode",
    [
        SearchMode.SEMANTIC,
        SearchMode.KEYWORD,
        SearchMode.HYBRID,
        SearchMode.HYBRID_TEMPORAL,
        SearchMode.QUALITY,
    ],
)
def test_k_per_index_formula(mode: SearchMode) -> None:
    top_k = 10
    plan = _PLANNER.plan(_req(mode, top_k=top_k), _WEIGHTS, graph_available=False)
    assert plan.k_per_index >= max(top_k * 3, 20)


# 9. DefaultQueryPlanner satisfies QueryPlannerPort
def test_planner_satisfies_port() -> None:
    assert isinstance(_PLANNER, QueryPlannerPort)


# 10. HYBRID plan weights contain semantic_weight key
def test_hybrid_weights_contain_semantic_weight() -> None:
    plan = _PLANNER.plan(_req(SearchMode.HYBRID), _WEIGHTS, graph_available=False)
    assert "semantic_weight" in plan.weights


# 11. explanation is non-empty for all modes
@pytest.mark.parametrize(
    "mode,graph_available",
    [
        (SearchMode.SEMANTIC, False),
        (SearchMode.KEYWORD, False),
        (SearchMode.HYBRID, False),
        (SearchMode.HYBRID_TEMPORAL, False),
        (SearchMode.QUALITY, False),
        (SearchMode.GRAPH, True),
    ],
)
def test_explanation_non_empty(mode: SearchMode, graph_available: bool) -> None:
    plan = _PLANNER.plan(_req(mode), _WEIGHTS, graph_available=graph_available)
    assert len(plan.explanation) > 0


# 12. temporal_filter from request is propagated to QueryPlan
def test_temporal_filter_propagated() -> None:
    from datetime import datetime, timezone
    tf = TemporalFilter(
        after=datetime(2025, 1, 1, tzinfo=timezone.utc),
        before=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    plan = _PLANNER.plan(_req(SearchMode.HYBRID_TEMPORAL, temporal_filter=tf), _WEIGHTS, graph_available=False)
    assert plan.temporal_filter is tf

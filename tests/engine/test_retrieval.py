"""Acceptance tests for RetrievalService + RRF — M3-T4."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from memory_layer.domain.exceptions import CapabilityNotAvailableError
from memory_layer.domain.records import SearchMode, SearchRequest, Scope
from memory_layer.domain.types import PrincipalType, TenantId
from memory_layer.domain.policies import SearchWeightsPolicy
from memory_layer.engine.planner import DefaultQueryPlanner, IndexTarget, QueryPlan
from memory_layer.engine.retrieval import RetrievalService, reciprocal_rank_fusion
from memory_layer.ports.outbound import FullTextSearchResult, VectorSearchResult

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

TENANT = TenantId("tenant-retrieval")
_SCOPE = Scope(
    tenant_id=TENANT,
    principal_id="user-1",  # type: ignore[arg-type]
    principal_type=PrincipalType.USER,
)
_WEIGHTS = SearchWeightsPolicy()
_PLANNER = DefaultQueryPlanner()


def _req(mode: SearchMode, top_k: int = 5) -> SearchRequest:
    return SearchRequest(
        tenant_id=TENANT,
        scope=_SCOPE,
        query_text="test query",
        mode=mode,
        top_k=top_k,
    )


def _make_service(
    vector_ids: list[str] | None = None,
    full_text_ids: list[str] | None = None,
    graph_port: object = None,
) -> tuple[RetrievalService, AsyncMock, AsyncMock, AsyncMock]:
    """Build a RetrievalService with mocked ports."""
    vector_ids = vector_ids or ["m1", "m2", "m3"]
    full_text_ids = full_text_ids or ["m2", "m3", "m4"]

    embedding_port = AsyncMock()
    embedding_port.embed.return_value = [[0.1] * 8]

    vector_index = AsyncMock()
    vector_index.search.return_value = [
        VectorSearchResult(memory_id=mid, score=1.0 - i * 0.1, content=f"content-{mid}")
        for i, mid in enumerate(vector_ids)
    ]

    full_text_index = AsyncMock()
    full_text_index.search.return_value = [
        FullTextSearchResult(memory_id=mid, score=1.0 - i * 0.1, content=f"content-{mid}")
        for i, mid in enumerate(full_text_ids)
    ]

    svc = RetrievalService(
        embedding_port=embedding_port,
        vector_index=vector_index,
        full_text_index=full_text_index,
        graph_port=graph_port,  # type: ignore[arg-type]
    )
    return svc, embedding_port, vector_index, full_text_index


# ---------------------------------------------------------------------------
# RRF unit tests
# ---------------------------------------------------------------------------

# 1. Single list preserves original order
def test_rrf_single_list_preserves_order() -> None:
    result = reciprocal_rank_fusion([["a", "b", "c"]])
    ids = [r[0] for r in result]
    assert ids == ["a", "b", "c"]


# 2. Two lists — item appearing in both ranks higher
def test_rrf_two_lists_merges_by_score() -> None:
    result = reciprocal_rank_fusion([["a", "b"], ["b", "c"]])
    id_map = {r[0]: r[1] for r in result}
    assert id_map["b"] > id_map["a"]
    assert id_map["b"] > id_map["c"]


# 3. Higher-weighted list gives higher score
def test_rrf_weighted_lists() -> None:
    result = reciprocal_rank_fusion([["x"], ["y"]], weights=[2.0, 1.0])
    id_map = {r[0]: r[1] for r in result}
    assert id_map["x"] > id_map["y"]


# 4. Empty input returns []
def test_rrf_empty_lists() -> None:
    assert reciprocal_rank_fusion([]) == []


# ---------------------------------------------------------------------------
# RetrievalService integration tests
# ---------------------------------------------------------------------------

# 5. HYBRID calls both vector and full_text search
@pytest.mark.asyncio
async def test_hybrid_calls_both_indexes() -> None:
    svc, _, vector_index, full_text_index = _make_service()
    plan = _PLANNER.plan(_req(SearchMode.HYBRID), _WEIGHTS, graph_available=False)
    await svc.retrieve(_req(SearchMode.HYBRID), plan)
    vector_index.search.assert_awaited_once()
    full_text_index.search.assert_awaited_once()


# 6. SEMANTIC calls only vector_index.search
@pytest.mark.asyncio
async def test_semantic_calls_only_vector() -> None:
    svc, _, vector_index, full_text_index = _make_service()
    plan = _PLANNER.plan(_req(SearchMode.SEMANTIC), _WEIGHTS, graph_available=False)
    await svc.retrieve(_req(SearchMode.SEMANTIC), plan)
    vector_index.search.assert_awaited_once()
    full_text_index.search.assert_not_awaited()


# 7. KEYWORD calls only full_text_index.search
@pytest.mark.asyncio
async def test_keyword_calls_only_full_text() -> None:
    svc, _, vector_index, full_text_index = _make_service()
    plan = _PLANNER.plan(_req(SearchMode.KEYWORD), _WEIGHTS, graph_available=False)
    await svc.retrieve(_req(SearchMode.KEYWORD), plan)
    full_text_index.search.assert_awaited_once()
    vector_index.search.assert_not_awaited()


# 8. Results trimmed to plan.final_k
@pytest.mark.asyncio
async def test_results_trimmed_to_final_k() -> None:
    many_ids = [f"m{i}" for i in range(20)]
    svc, *_ = _make_service(vector_ids=many_ids, full_text_ids=many_ids)
    plan = _PLANNER.plan(_req(SearchMode.SEMANTIC, top_k=3), _WEIGHTS, graph_available=False)
    result = await svc.retrieve(_req(SearchMode.SEMANTIC, top_k=3), plan)
    assert len(result.items) <= plan.final_k


# 9. SearchResult.query_plan is the QueryPlan used
@pytest.mark.asyncio
async def test_search_result_contains_query_plan() -> None:
    svc, *_ = _make_service()
    plan = _PLANNER.plan(_req(SearchMode.HYBRID), _WEIGHTS, graph_available=False)
    result = await svc.retrieve(_req(SearchMode.HYBRID), plan)
    assert result.query_plan is plan


# 10. GRAPH plan with graph_port=None raises CapabilityNotAvailableError
@pytest.mark.asyncio
async def test_graph_port_none_raises() -> None:
    svc, *_ = _make_service(graph_port=None)
    plan = QueryPlan(
        mode=SearchMode.GRAPH,
        targets=[IndexTarget.GRAPH],
        weights={"graph_weight": 1.0},
        final_k=5,
        k_per_index=20,
        explanation="graph test",
    )
    with pytest.raises(CapabilityNotAvailableError):
        await svc.retrieve(_req(SearchMode.GRAPH), plan)


# 11. Each SearchResultItem has score > 0
@pytest.mark.asyncio
async def test_all_items_have_positive_score() -> None:
    svc, *_ = _make_service()
    plan = _PLANNER.plan(_req(SearchMode.HYBRID), _WEIGHTS, graph_available=False)
    result = await svc.retrieve(_req(SearchMode.HYBRID), plan)
    for item in result.items:
        assert item.score > 0


# 12. embedding_port.embed NOT called for KEYWORD mode
@pytest.mark.asyncio
async def test_keyword_does_not_call_embed() -> None:
    svc, embedding_port, *_ = _make_service()
    plan = _PLANNER.plan(_req(SearchMode.KEYWORD), _WEIGHTS, graph_available=False)
    await svc.retrieve(_req(SearchMode.KEYWORD), plan)
    embedding_port.embed.assert_not_awaited()

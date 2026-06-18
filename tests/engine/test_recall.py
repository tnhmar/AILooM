"""Acceptance tests for SearchMemoryService and RecallMemoryService — M3-T5."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from memory_layer.domain.events import MemoryRecalledEvent, RecallNoMatchEvent
from memory_layer.domain.policies import SearchWeightsPolicy, TenantPolicies
from memory_layer.domain.records import (
    RecallRequest,
    RecallStatus,
    Scope,
    SearchMode,
    SearchRequest,
)
from memory_layer.domain.types import (
    LifecycleState,
    MemoryId,
    MemorySector,
    PipelineStatus,
    PrincipalType,
    TenantId,
)
from memory_layer.engine.planner import DefaultQueryPlanner, QueryPlan, IndexTarget
from memory_layer.engine.recall import RecallMemoryService, SearchMemoryService
from memory_layer.ports.inbound import (
    RecallMemoryUseCase,
    SearchMemoryUseCase,
    SearchResult,
    SearchResultItem,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TENANT = TenantId("tenant-recall")
_SCOPE = Scope(
    tenant_id=TENANT,
    principal_id="user-1",  # type: ignore[arg-type]
    principal_type=PrincipalType.USER,
)


def _search_req(mode: SearchMode = SearchMode.HYBRID, k: int = 10) -> SearchRequest:
    return SearchRequest(
        tenant_id=TENANT, scope=_SCOPE, query="test query", mode=mode, k=k
    )


def _recall_req(
    max_items: int = 3,
    max_tokens: int | None = 4000,
    mode: SearchMode = SearchMode.HYBRID,
) -> RecallRequest:
    return RecallRequest(
        tenant_id=TENANT,
        scope=_SCOPE,
        query="test query",
        max_items=max_items,
        max_tokens=max_tokens,
        mode=mode,
    )


def _make_item(
    mid: str = "m1", score: float = 0.9, content: str = "hello world"
) -> SearchResultItem:
    return SearchResultItem(
        memory_id=MemoryId(mid),  # type: ignore[call-arg]
        content=content,
        sector=MemorySector.SEMANTIC,
        score=score,
        pipeline_status=PipelineStatus.ENRICHED,
        lifecycle_state=LifecycleState.ACTIVE,
    )


def _make_search_service(
    items: list[SearchResultItem] | None = None,
) -> tuple[SearchMemoryService, AsyncMock, AsyncMock]:
    """Build a SearchMemoryService with fully mocked dependencies."""
    items = items if items is not None else [_make_item()]

    plan = QueryPlan(
        mode=SearchMode.HYBRID,
        targets=[IndexTarget.VECTOR, IndexTarget.FULL_TEXT],
        weights={"semantic_weight": 0.6, "keyword_weight": 0.4},
        final_k=10,
        k_per_index=30,
        explanation="test plan",
    )

    retrieval = AsyncMock()
    retrieval.retrieve.return_value = SearchResult(
        items=items, total=len(items), query_plan=plan
    )

    planner = MagicMock(spec=DefaultQueryPlanner)
    planner.plan.return_value = plan

    policies = TenantPolicies(search_weights=SearchWeightsPolicy())
    policy_repo = AsyncMock()
    policy_repo.get.return_value = policies

    observer = AsyncMock()

    svc = SearchMemoryService(
        retrieval=retrieval,
        planner=planner,
        policy_repo=policy_repo,
        observer=observer,
    )
    return svc, retrieval, observer


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# 1. SearchMemoryService satisfies SearchMemoryUseCase protocol
def test_search_service_satisfies_protocol() -> None:
    svc, *_ = _make_search_service()
    assert isinstance(svc, SearchMemoryUseCase)


# 2. RecallMemoryService satisfies RecallMemoryUseCase protocol
def test_recall_service_satisfies_protocol() -> None:
    svc, *_ = _make_search_service()
    observer = AsyncMock()
    recall_svc = RecallMemoryService(search_service=svc, observer=observer)
    assert isinstance(recall_svc, RecallMemoryUseCase)


# 3. SearchMemoryService.execute calls retrieval.retrieve exactly once
@pytest.mark.asyncio
async def test_search_calls_retrieve_once() -> None:
    svc, retrieval, _ = _make_search_service()
    await svc.execute(_search_req())
    retrieval.retrieve.assert_awaited_once()


# 4. SearchMemoryService.execute emits MemoryRecalledEvent
@pytest.mark.asyncio
async def test_search_emits_memory_recalled_event() -> None:
    svc, _, observer = _make_search_service()
    await svc.execute(_search_req())
    emitted_types = [type(c[0][0]) for c in observer.emit.call_args_list]
    assert MemoryRecalledEvent in emitted_types


# 5. No search results → status=NO_MATCH
@pytest.mark.asyncio
async def test_no_results_returns_no_match() -> None:
    search_svc, *_ = _make_search_service(items=[])
    observer = AsyncMock()
    recall_svc = RecallMemoryService(search_service=search_svc, observer=observer)
    result = await recall_svc.execute(_recall_req())
    assert result.status == RecallStatus.NO_MATCH


# 6. No-match emits RecallNoMatchEvent
@pytest.mark.asyncio
async def test_no_match_emits_recall_no_match_event() -> None:
    search_svc, *_ = _make_search_service(items=[])
    observer = AsyncMock()
    recall_svc = RecallMemoryService(search_service=search_svc, observer=observer)
    await recall_svc.execute(_recall_req())
    emitted_types = [type(c[0][0]) for c in observer.emit.call_args_list]
    assert RecallNoMatchEvent in emitted_types


# 7. Respects max_items limit
@pytest.mark.asyncio
async def test_respects_max_items() -> None:
    items = [_make_item(f"m{i}") for i in range(10)]
    search_svc, *_ = _make_search_service(items=items)
    observer = AsyncMock()
    recall_svc = RecallMemoryService(search_service=search_svc, observer=observer)
    result = await recall_svc.execute(_recall_req(max_items=3))
    assert len(result.items) <= 3


# 8. Respects max_tokens budget
@pytest.mark.asyncio
async def test_respects_max_tokens() -> None:
    long_content = " ".join(["word"] * 100)
    items = [_make_item(f"m{i}", content=long_content) for i in range(10)]
    search_svc, *_ = _make_search_service(items=items)
    observer = AsyncMock()
    recall_svc = RecallMemoryService(search_service=search_svc, observer=observer)
    result = await recall_svc.execute(_recall_req(max_items=10, max_tokens=200))
    assert result.total_tokens_estimate <= 200


# 9. RecallItem.explanation contains the word "search"
@pytest.mark.asyncio
async def test_recall_item_explanation_contains_search() -> None:
    search_svc, *_ = _make_search_service(items=[_make_item()])
    observer = AsyncMock()
    recall_svc = RecallMemoryService(search_service=search_svc, observer=observer)
    result = await recall_svc.execute(_recall_req())
    assert result.items
    assert "search" in result.items[0].explanation


# 10. RecallItem.trace_id is not None
@pytest.mark.asyncio
async def test_recall_item_has_trace_id() -> None:
    search_svc, *_ = _make_search_service(items=[_make_item()])
    observer = AsyncMock()
    recall_svc = RecallMemoryService(search_service=search_svc, observer=observer)
    result = await recall_svc.execute(_recall_req())
    assert result.items
    assert result.items[0].trace_id is not None


# 11. status=MATCH when all max_items filled
@pytest.mark.asyncio
async def test_status_match_when_all_items_filled() -> None:
    items = [_make_item(f"m{i}") for i in range(5)]
    search_svc, *_ = _make_search_service(items=items)
    observer = AsyncMock()
    recall_svc = RecallMemoryService(search_service=search_svc, observer=observer)
    result = await recall_svc.execute(_recall_req(max_items=3, max_tokens=None))
    assert result.status == RecallStatus.MATCH


# 12. status=PARTIAL_MATCH when token budget cuts short
@pytest.mark.asyncio
async def test_status_partial_match_on_token_cut() -> None:
    long_content = " ".join(["word"] * 200)
    items = [_make_item(f"m{i}", content=long_content) for i in range(5)]
    search_svc, *_ = _make_search_service(items=items)
    observer = AsyncMock()
    recall_svc = RecallMemoryService(search_service=search_svc, observer=observer)
    result = await recall_svc.execute(_recall_req(max_items=5, max_tokens=300))
    assert result.status == RecallStatus.PARTIAL_MATCH


# 13. _estimate_tokens returns positive int for non-empty string
def test_estimate_tokens_positive() -> None:
    svc, *_ = _make_search_service()
    observer = AsyncMock()
    recall_svc = RecallMemoryService(search_service=svc, observer=observer)
    assert recall_svc._estimate_tokens("hello world this is a test") > 0


# 14. recall_strategy reflects the search mode used
@pytest.mark.asyncio
async def test_recall_strategy_reflects_mode() -> None:
    search_svc, *_ = _make_search_service(items=[_make_item()])
    observer = AsyncMock()
    recall_svc = RecallMemoryService(search_service=search_svc, observer=observer)
    result = await recall_svc.execute(_recall_req(mode=SearchMode.SEMANTIC))
    assert "SEMANTIC" in result.recall_strategy

"""LongMemEval-style in-process recall quality benchmark — M6-T4.

Run with::

    pytest tests/benchmarks/ -m benchmark -v

Skip in normal CI with::

    pytest -m "not benchmark"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Benchmark fixtures
# ---------------------------------------------------------------------------

FIXTURES: list[dict[str, str]] = [
    {
        "memory": "User's name is Alice and she lives in Montreal.",
        "query": "What is the user's name?",
        "expected_in_result": "Alice",
    },
    {
        "memory": "Preferred LLM is Claude 3.5 Sonnet for coding tasks.",
        "query": "Which LLM does the user prefer for coding?",
        "expected_in_result": "Claude",
    },
    # Temporal scenario
    {
        "memory": "On 2024-01-15 the user completed the onboarding tutorial.",
        "query": "When did the user finish onboarding?",
        "expected_in_result": "2024-01-15",
    },
    # Contradiction scenario
    {
        "memory": "User changed their preferred language from Python to Rust in March 2024.",
        "query": "What is the user's preferred programming language?",
        "expected_in_result": "Rust",
    },
    # Procedural scenario
    {
        "memory": "To deploy: run `make build && docker push registry/app:latest`.",
        "query": "How does the user deploy their application?",
        "expected_in_result": "docker push",
    },
]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkResult:
    """Aggregate recall quality metrics for one benchmark run."""

    total: int
    passed: int
    recall_at_1: float
    recall_at_5: float


# ---------------------------------------------------------------------------
# In-process client stub
# ---------------------------------------------------------------------------


class _InProcessClient:
    """Minimal in-process stub that stands in for MemoryLayerClient.

    Stores written payloads in memory and returns any that contain the query
    as a substring (case-insensitive keyword match).
    """

    def __init__(self) -> None:
        self._store: list[str] = []

    async def write(self, memory: str) -> None:
        self._store.append(memory)

    async def recall(self, query: str, max_items: int = 5) -> list[str]:
        """Return stored memories that contain any word from the query."""
        query_words = {w.lower().strip("?") for w in query.split()}
        results = [
            m for m in self._store
            if any(w in m.lower() for w in query_words)
        ]
        return results[:max_items]


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


async def run_benchmark(client: _InProcessClient) -> BenchmarkResult:
    """Write all fixture memories then recall each query and score the results.

    For each fixture
    ----------------
    1. Write the ``memory`` string.
    2. Recall with the matching ``query`` (max 5 items).
    3. Check whether ``expected_in_result`` appears in any recalled item.

    Returns
    -------
    :class:`BenchmarkResult` with ``total``, ``passed``, ``recall_at_1``,
    and ``recall_at_5`` fields.
    """
    for fixture in FIXTURES:
        await client.write(fixture["memory"])

    passed_at_1 = 0
    passed_at_5 = 0

    for fixture in FIXTURES:
        query = fixture["query"]
        expected = fixture["expected_in_result"]

        items_5 = await client.recall(query, max_items=5)
        items_1 = items_5[:1]

        if any(expected in item for item in items_1):
            passed_at_1 += 1
        if any(expected in item for item in items_5):
            passed_at_5 += 1

    total = len(FIXTURES)
    return BenchmarkResult(
        total=total,
        passed=passed_at_5,
        recall_at_1=passed_at_1 / total,
        recall_at_5=passed_at_5 / total,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
@pytest.mark.asyncio
async def test_benchmark_result_has_required_fields() -> None:
    """Test 10: BenchmarkResult has fields total, passed, recall_at_1, recall_at_5."""
    result = BenchmarkResult(total=5, passed=4, recall_at_1=0.8, recall_at_5=0.9)
    assert hasattr(result, "total")
    assert hasattr(result, "passed")
    assert hasattr(result, "recall_at_1")
    assert hasattr(result, "recall_at_5")


@pytest.mark.benchmark
@pytest.mark.asyncio
async def test_benchmark_recall_quality() -> None:
    """Full LongMemEval-style benchmark: write 5 memories and recall each."""
    client = _InProcessClient()
    result = await run_benchmark(client)

    assert result.total == len(FIXTURES)
    assert 0.0 <= result.recall_at_1 <= 1.0
    assert 0.0 <= result.recall_at_5 <= 1.0
    assert result.passed <= result.total
    # Quality gate: recall@5 must be >= 0.6 on this synthetic set
    assert result.recall_at_5 >= 0.6, (
        f"Recall@5 regression: {result.recall_at_5:.2f} < 0.60"
    )

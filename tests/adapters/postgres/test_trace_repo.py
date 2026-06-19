"""Integration tests for PostgresTraceRepository — M7-T2 (5 tests).

Requires a real PostgreSQL instance. Set TEST_POSTGRES_DSN to run.
Skipped automatically in environments without the env var.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
import pytest_asyncio

TEST_DSN = os.environ.get("TEST_POSTGRES_DSN", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DSN,
        reason="TEST_POSTGRES_DSN not set — skipping PostgreSQL integration tests",
    ),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def pool():
    import asyncpg
    from memory_layer.adapters.postgres.trace_repo import TRACES_DDL

    p = await asyncpg.create_pool(dsn=TEST_DSN, min_size=1, max_size=3)
    async with p.acquire() as conn:
        await conn.execute(TRACES_DDL)
    yield p
    async with p.acquire() as conn:
        await conn.execute("DELETE FROM memory_traces WHERE tenant_id LIKE 'tenant-%'")
    await p.close()


@pytest_asyncio.fixture
async def repo(pool):
    from memory_layer.adapters.postgres.trace_repo import PostgresTraceRepository
    return PostgresTraceRepository(pool)


@pytest_asyncio.fixture(autouse=True)
async def cleanup(pool):
    yield
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM memory_traces WHERE tenant_id LIKE 'tenant-%'")


def _make_trace(
    trace_id: str = "trace-001",
    tenant_id: str = "tenant-A",
    num_steps: int = 2,
):
    from memory_layer.domain.records import RecallTrace, TraceStep
    from memory_layer.domain.types import MemoryId, TenantId, TraceId

    steps = [
        TraceStep(
            memory_id=MemoryId(f"mem-{i}"),
            rank=i,
            score=0.9 - i * 0.1,
            signals={"vector": 0.8},
            explanation=f"step {i}",
            record_available=True,
        )
        for i in range(num_steps)
    ]
    return RecallTrace(
        trace_id=TraceId(trace_id),
        tenant_id=TenantId(tenant_id),
        query="What is the user's name?",
        mode="HYBRID",
        steps=steps,
        query_plan={"strategy": "hybrid", "k": 10},
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_and_get_by_trace_id(repo):
    """1. save then get_by_trace_id returns the trace."""
    trace = _make_trace()
    await repo.save(trace)
    fetched = await repo.get_by_trace_id(trace.trace_id, trace.tenant_id)
    assert fetched is not None


@pytest.mark.asyncio
async def test_returned_trace_has_correct_ids(repo):
    """2. Returned trace has correct trace_id and tenant_id."""
    trace = _make_trace(trace_id="trace-ids")
    await repo.save(trace)
    fetched = await repo.get_by_trace_id(trace.trace_id, trace.tenant_id)
    assert fetched is not None
    assert fetched.trace_id == trace.trace_id
    assert fetched.tenant_id == trace.tenant_id
    assert fetched.query == trace.query
    assert fetched.mode == trace.mode


@pytest.mark.asyncio
async def test_steps_deserialized_as_trace_step_objects(repo):
    """3. steps are deserialized as a list of TraceStep objects."""
    from memory_layer.domain.records import TraceStep

    trace = _make_trace(trace_id="trace-steps", num_steps=3)
    await repo.save(trace)
    fetched = await repo.get_by_trace_id(trace.trace_id, trace.tenant_id)
    assert fetched is not None
    assert len(fetched.steps) == 3
    for step in fetched.steps:
        assert isinstance(step, TraceStep)
    # Verify rank ordering preserved
    assert fetched.steps[0].rank == 0
    assert fetched.steps[1].rank == 1


@pytest.mark.asyncio
async def test_get_by_trace_id_unknown_returns_none(repo):
    """4. get_by_trace_id returns None for unknown trace_id."""
    from memory_layer.domain.types import TenantId, TraceId

    result = await repo.get_by_trace_id(TraceId("nonexistent"), TenantId("tenant-A"))
    assert result is None


@pytest.mark.asyncio
async def test_cross_tenant_isolation(repo):
    """5. Trace from tenant A is not visible to tenant B."""
    from memory_layer.domain.types import TenantId

    trace_a = _make_trace(trace_id="trace-iso", tenant_id="tenant-A")
    await repo.save(trace_a)
    result = await repo.get_by_trace_id(trace_a.trace_id, TenantId("tenant-B"))
    assert result is None

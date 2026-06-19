"""PostgreSQL implementation of TraceRepositoryPort.

Traces use a JSONB column for steps and query_plan to allow schema-free
storage of variable-depth recall explanations.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import asyncpg

from memory_layer.domain.records import RecallTrace, TraceStep
from memory_layer.domain.types import MemoryId, TenantId, TraceId

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

TRACES_DDL: str = """
CREATE TABLE IF NOT EXISTS memory_traces (
    trace_id   TEXT        NOT NULL,
    tenant_id  TEXT        NOT NULL,
    query      TEXT        NOT NULL,
    mode       TEXT        NOT NULL,
    steps      JSONB       NOT NULL DEFAULT '[]',
    query_plan JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (trace_id, tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_traces_tenant
    ON memory_traces (tenant_id, created_at DESC);
"""


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _steps_to_json(steps: list[TraceStep]) -> str:
    """Serialize a list of :class:`TraceStep` to a JSON string."""
    return json.dumps([dataclasses.asdict(s) for s in steps])


def _steps_from_json(raw: str | list[Any]) -> list[TraceStep]:
    """Deserialize JSONB array back to a list of :class:`TraceStep`."""
    if isinstance(raw, str):
        data: list[dict[str, Any]] = json.loads(raw)
    else:
        data = raw  # asyncpg decodes JSONB to Python objects automatically
    return [
        TraceStep(
            memory_id=MemoryId(d["memory_id"]),
            rank=int(d["rank"]),
            score=float(d["score"]),
            signals=d.get("signals", {}),
            explanation=d.get("explanation", ""),
            record_available=bool(d.get("record_available", True)),
        )
        for d in data
    ]


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class PostgresTraceRepository:
    """asyncpg-backed implementation of :class:`TraceRepositoryPort`.

    ``steps`` and ``query_plan`` are stored as JSONB.
    Every query is scoped to ``tenant_id``.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save(self, trace: RecallTrace) -> None:
        """INSERT a trace; steps serialized via :func:`dataclasses.asdict`."""
        steps_json = _steps_to_json(trace.steps)
        query_plan_json = (
            json.dumps(trace.query_plan)
            if trace.query_plan is not None
            else None
        )
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO memory_traces (
                    trace_id, tenant_id, query, mode,
                    steps, query_plan, created_at
                ) VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7)
                ON CONFLICT (trace_id, tenant_id) DO NOTHING
                """,
                str(trace.trace_id),
                str(trace.tenant_id),
                trace.query,
                trace.mode,
                steps_json,
                query_plan_json,
                trace.created_at,
            )

    async def get_by_trace_id(
        self, trace_id: TraceId, tenant_id: TenantId
    ) -> RecallTrace | None:
        """SELECT and reconstruct a :class:`RecallTrace` with :class:`TraceStep` objects."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM memory_traces
                WHERE trace_id = $1 AND tenant_id = $2
                """,
                str(trace_id),
                str(tenant_id),
            )
        if row is None:
            return None

        d = dict(row)
        steps = _steps_from_json(d["steps"])

        # query_plan is stored as raw dict; do not reconstruct a QueryPlan object
        raw_qp = d.get("query_plan")
        query_plan: Any = None
        if raw_qp is not None:
            query_plan = json.loads(raw_qp) if isinstance(raw_qp, str) else raw_qp

        return RecallTrace(
            trace_id=TraceId(d["trace_id"]),
            tenant_id=TenantId(d["tenant_id"]),
            query=d["query"],
            mode=d["mode"],
            steps=steps,
            query_plan=query_plan,
            created_at=d["created_at"],
        )

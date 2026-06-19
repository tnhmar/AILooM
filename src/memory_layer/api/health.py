"""Deep health and readiness probes for memory-layer.

Call :func:`HealthChecker.check` to obtain a :class:`HealthReport` that
aggregates the status of every registered component. This drives both the
``/healthz`` (liveness) and ``/readyz`` (readiness) FastAPI endpoints.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable, List, Literal, Optional

from memory_layer.ports.outbound import (
    FullTextIndexPort,
    MemoryRecordRepositoryPort,
    VectorIndexPort,
)


# ---------------------------------------------------------------------------
# Data-classes
# ---------------------------------------------------------------------------


@dataclass
class ComponentHealth:
    """Health status for a single infrastructure component."""

    name: str
    status: Literal["ok", "degraded", "down"]
    latency_ms: Optional[float] = None
    detail: Optional[str] = None


@dataclass
class HealthReport:
    """Aggregated health report for the whole service."""

    status: Literal["ok", "degraded", "down"]
    version: str
    components: List[ComponentHealth] = field(default_factory=list)
    checked_at: datetime = field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# HealthChecker
# ---------------------------------------------------------------------------


class HealthChecker:
    """Runs probe functions for each registered component and aggregates results.

    Overall status rules
    --------------------
    * ``"ok"``       — every component is ``"ok"``.
    * ``"degraded"`` — at least one component is ``"degraded"`` and none are ``"down"``.
    * ``"down"``     — at least one component is ``"down"``.
    """

    def __init__(self, version: str = "0.1.0") -> None:
        self._version = version
        self._probes: list[tuple[str, Callable[[], Awaitable[ComponentHealth]]]] = []

    def register(
        self,
        name: str,
        probe: Callable[[], Awaitable[ComponentHealth]],
    ) -> None:
        """Register a named async probe function."""
        self._probes.append((name, probe))

    async def check(self) -> HealthReport:
        """Execute all registered probes concurrently and return a :class:`HealthReport`."""
        import asyncio

        if self._probes:
            results: list[ComponentHealth] = list(
                await asyncio.gather(*[probe() for _, probe in self._probes])
            )
        else:
            results = []

        if any(c.status == "down" for c in results):
            overall: Literal["ok", "degraded", "down"] = "down"
        elif any(c.status == "degraded" for c in results):
            overall = "degraded"
        else:
            overall = "ok"

        return HealthReport(
            status=overall,
            version=self._version,
            components=results,
            checked_at=datetime.utcnow(),
        )


# ---------------------------------------------------------------------------
# Built-in probes
# ---------------------------------------------------------------------------


async def probe_record_repo(
    repo: MemoryRecordRepositoryPort,
) -> ComponentHealth:
    """Probe the relational / document record store.

    Calls ``repo.get_by_id`` with a sentinel value; any non-exception response
    is treated as ``"ok"``.
    """
    from memory_layer.domain.types import MemoryId, TenantId

    start = time.perf_counter()
    try:
        await repo.get_by_id(
            MemoryId("__health_probe__"),
            TenantId("__health_probe__"),
        )
        latency_ms = (time.perf_counter() - start) * 1000
        return ComponentHealth(
            name="record_repo",
            status="ok",
            latency_ms=round(latency_ms, 2),
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return ComponentHealth(
            name="record_repo",
            status="down",
            latency_ms=round(latency_ms, 2),
            detail=str(exc),
        )


async def probe_vector_index(
    index: VectorIndexPort,
) -> ComponentHealth:
    """Probe the vector similarity index.

    Issues a zero-vector search with ``k=1``; any non-exception response is
    treated as ``"ok"``.
    """
    from memory_layer.domain.types import TenantId

    start = time.perf_counter()
    try:
        await index.search(
            query_embedding=[0.0],
            tenant_id=TenantId("__health_probe__"),
            k=1,
            filters={},
        )
        latency_ms = (time.perf_counter() - start) * 1000
        return ComponentHealth(
            name="vector_index",
            status="ok",
            latency_ms=round(latency_ms, 2),
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return ComponentHealth(
            name="vector_index",
            status="down",
            latency_ms=round(latency_ms, 2),
            detail=str(exc),
        )


async def probe_full_text_index(
    index: FullTextIndexPort,
) -> ComponentHealth:
    """Probe the full-text search index.

    Issues a probe query; any non-exception response is treated as ``"ok"``.
    """
    from memory_layer.domain.types import TenantId

    start = time.perf_counter()
    try:
        await index.search(
            query="__health_probe__",
            tenant_id=TenantId("__health_probe__"),
            k=1,
            filters={},
        )
        latency_ms = (time.perf_counter() - start) * 1000
        return ComponentHealth(
            name="full_text_index",
            status="ok",
            latency_ms=round(latency_ms, 2),
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return ComponentHealth(
            name="full_text_index",
            status="down",
            latency_ms=round(latency_ms, 2),
            detail=str(exc),
        )

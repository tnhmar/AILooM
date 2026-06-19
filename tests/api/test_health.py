"""Acceptance tests for health/readiness probes — M6-T4 (tests 1-9)."""

from __future__ import annotations

from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from memory_layer.api.health import (
    ComponentHealth,
    HealthChecker,
    HealthReport,
    probe_record_repo,
    probe_vector_index,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _ok_repo() -> MagicMock:
    """Mock MemoryRecordRepositoryPort whose get_by_id succeeds."""
    repo = MagicMock()
    repo.get_by_id = AsyncMock(return_value=None)
    return repo


def _failing_repo() -> MagicMock:
    """Mock MemoryRecordRepositoryPort whose get_by_id raises."""
    repo = MagicMock()
    repo.get_by_id = AsyncMock(side_effect=RuntimeError("DB connection refused"))
    return repo


def _ok_vector_index() -> MagicMock:
    """Mock VectorIndexPort whose search succeeds."""
    idx = MagicMock()
    idx.search = AsyncMock(return_value=[])
    return idx


# ---------------------------------------------------------------------------
# 1. probe_record_repo returns ok for working repo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_record_repo_ok() -> None:
    ch = await probe_record_repo(_ok_repo())
    assert isinstance(ch, ComponentHealth)
    assert ch.status == "ok"
    assert ch.name == "record_repo"
    assert ch.latency_ms is not None and ch.latency_ms >= 0.0


# ---------------------------------------------------------------------------
# 2. probe_record_repo returns down when repo raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_record_repo_down_on_error() -> None:
    ch = await probe_record_repo(_failing_repo())
    assert ch.status == "down"
    assert ch.detail is not None
    assert "DB connection refused" in ch.detail


# ---------------------------------------------------------------------------
# 3. probe_vector_index returns ok for working index
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_vector_index_ok() -> None:
    ch = await probe_vector_index(_ok_vector_index())
    assert isinstance(ch, ComponentHealth)
    assert ch.status == "ok"
    assert ch.name == "vector_index"


# ---------------------------------------------------------------------------
# 4. HealthChecker.check returns ok when all probes pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_checker_all_ok() -> None:
    checker = HealthChecker(version="test")

    async def _ok_probe() -> ComponentHealth:
        return ComponentHealth(name="db", status="ok", latency_ms=1.0)

    checker.register("db", _ok_probe)
    report = await checker.check()
    assert isinstance(report, HealthReport)
    assert report.status == "ok"
    assert len(report.components) == 1


# ---------------------------------------------------------------------------
# 5. HealthChecker.check returns down when any probe returns down
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_checker_down_on_any_down() -> None:
    checker = HealthChecker()

    async def _down() -> ComponentHealth:
        return ComponentHealth(name="db", status="down", detail="offline")

    async def _ok() -> ComponentHealth:
        return ComponentHealth(name="cache", status="ok")

    checker.register("db", _down)
    checker.register("cache", _ok)
    report = await checker.check()
    assert report.status == "down"


# ---------------------------------------------------------------------------
# 6. HealthChecker.check returns degraded when any probe is degraded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_checker_degraded() -> None:
    checker = HealthChecker()

    async def _degraded() -> ComponentHealth:
        return ComponentHealth(name="index", status="degraded", detail="slow")

    async def _ok() -> ComponentHealth:
        return ComponentHealth(name="db", status="ok")

    checker.register("index", _degraded)
    checker.register("db", _ok)
    report = await checker.check()
    assert report.status == "degraded"


# ---------------------------------------------------------------------------
# 7. /healthz returns 200 with JSON body containing 'status'
# ---------------------------------------------------------------------------


def test_healthz_returns_200_with_status_field() -> None:
    from memory_layer.api.app import app

    with TestClient(app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body


# ---------------------------------------------------------------------------
# 8. /readyz returns 200 when all healthy
# ---------------------------------------------------------------------------


def test_readyz_returns_200_when_healthy() -> None:
    from memory_layer.api.app import app, get_health_checker

    healthy_checker = HealthChecker(version="test")

    with TestClient(app) as client:
        app.dependency_overrides[get_health_checker] = lambda: healthy_checker
        resp = client.get("/readyz")
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# 9. /readyz returns 503 when any component is down
# ---------------------------------------------------------------------------


def test_readyz_returns_503_when_down() -> None:
    from memory_layer.api.app import app, get_health_checker

    down_checker = HealthChecker(version="test")

    async def _down_probe() -> ComponentHealth:
        return ComponentHealth(name="db", status="down", detail="offline")

    down_checker.register("db", _down_probe)

    with TestClient(app) as client:
        app.dependency_overrides[get_health_checker] = lambda: down_checker
        resp = client.get("/readyz")
        app.dependency_overrides.clear()

    assert resp.status_code == 503

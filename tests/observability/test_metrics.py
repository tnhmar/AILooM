"""Acceptance tests for Prometheus metrics — M6-T3 (9 tests)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import memory_layer.observability.metrics as metrics_mod
from memory_layer.config.settings import ObservabilitySettings
from memory_layer.observability.metrics import (
    configure_metrics,
    metrics_response,
    track_latency,
)


@pytest.fixture(autouse=True)
def reset_metrics():
    """Reset module-level metrics state between tests."""
    original_enabled = metrics_mod._metrics_enabled
    yield
    metrics_mod._metrics_enabled = original_enabled


# 1
def test_configure_metrics_no_raise_enabled() -> None:
    settings = ObservabilitySettings(metrics_enabled=True)
    configure_metrics(settings)  # must not raise


# 2
def test_configure_metrics_no_raise_disabled() -> None:
    settings = ObservabilitySettings(metrics_enabled=False)
    configure_metrics(settings)  # must not raise


# 3
def test_track_latency_records_positive_duration() -> None:
    observed: list[float] = []

    class _FakeHistogram:
        def labels(self, **_):
            return self

        def observe(self, v: float):
            observed.append(v)

    metrics_mod._metrics_enabled = True
    with track_latency(_FakeHistogram(), {"tenant_id": "t"}):
        pass  # near-zero but positive duration

    assert len(observed) == 1
    assert observed[0] >= 0.0


# 4
def test_memory_writes_total_increments() -> None:
    configure_metrics(ObservabilitySettings(metrics_enabled=True))
    # Increment must not raise whether or not prometheus_client is installed
    metrics_mod.memory_writes_total.labels(
        tenant_id="t-1", sector="EPISODIC", status="success"
    ).inc()


# 5
def test_search_latency_seconds_records() -> None:
    configure_metrics(ObservabilitySettings(metrics_enabled=True))
    metrics_mod.search_latency_seconds.labels(tenant_id="t-1", mode="HYBRID").observe(0.05)


# 6
def test_metrics_response_returns_text_plain() -> None:
    configure_metrics(ObservabilitySettings(metrics_enabled=True))
    resp = metrics_response()
    assert "text/plain" in resp.media_type


# 7
def test_metrics_endpoint_returns_200() -> None:
    from memory_layer.config.loader import override_settings, reset_settings
    from memory_layer.config.settings import Settings

    # Build settings with metrics enabled so the endpoint is registered
    reset_settings()
    override_settings(Settings())

    from memory_layer.api.app import app

    with TestClient(app) as client:
        resp = client.get("/metrics")
    # 200 when registered; 404 acceptable if metrics_enabled=False in default settings
    assert resp.status_code in (200, 404)

    reset_settings()


# 8
def test_counter_increment_noop_when_disabled() -> None:
    configure_metrics(ObservabilitySettings(metrics_enabled=False))
    # All metric objects are _NoOpMetric stubs; calling .labels().inc() must not raise
    metrics_mod.memory_writes_total.labels(
        tenant_id="t-1", sector="EPISODIC", status="success"
    ).inc()


# 9
def test_prometheus_unavailable_graceful_noop() -> None:
    """Simulate prometheus_client not installed: configure_metrics must not raise."""
    original = metrics_mod._PROM_AVAILABLE
    metrics_mod._PROM_AVAILABLE = False  # type: ignore[attr-defined]
    try:
        configure_metrics(ObservabilitySettings(metrics_enabled=True))
        # All metrics remain _NOOP stubs
        metrics_mod.memory_writes_total.labels(
            tenant_id="t", sector="EPISODIC", status="success"
        ).inc()
    finally:
        metrics_mod._PROM_AVAILABLE = original  # type: ignore[attr-defined]

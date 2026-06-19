"""Acceptance tests for distributed tracing — M6-T2 (6 tests)."""

from __future__ import annotations

import pytest

from memory_layer.config.settings import ObservabilitySettings
from memory_layer.observability import tracing as tracing_mod
from memory_layer.observability.tracing import (
    configure_tracing,
    engine_span,
    get_tracer,
)


@pytest.fixture(autouse=True)
def reset_provider():
    """Clear the module-level provider between tests."""
    tracing_mod._provider = None
    yield
    tracing_mod._provider = None


# 1
def test_configure_tracing_no_raise_disabled() -> None:
    settings = ObservabilitySettings(tracing_enabled=False)
    configure_tracing(settings)  # must not raise


# 2
def test_configure_tracing_no_raise_enabled() -> None:
    # tracing_enabled=True but no real OTLP collector — must not raise.
    settings = ObservabilitySettings(tracing_enabled=True, otlp_endpoint=None)
    configure_tracing(settings)  # must not raise


# 3
def test_get_tracer_returns_non_none_after_configure() -> None:
    settings = ObservabilitySettings(tracing_enabled=False)
    configure_tracing(settings)
    tracer = get_tracer("test.component")
    assert tracer is not None


# 4
@pytest.mark.asyncio
async def test_engine_span_works_as_context_manager() -> None:
    settings = ObservabilitySettings(tracing_enabled=False)
    configure_tracing(settings)
    # Must not raise and must yield
    with engine_span("write_memory", tenant_id="t-1", extra_attr="value"):
        pass


# 5
def test_engine_span_reraises_exceptions() -> None:
    settings = ObservabilitySettings(tracing_enabled=False)
    configure_tracing(settings)
    with pytest.raises(ValueError, match="boom"):
        with engine_span("test_op", tenant_id="t-1"):
            raise ValueError("boom")


# 6
def test_engine_span_works_when_otel_not_installed() -> None:
    """engine_span must be a no-op graceful context manager even without OTel."""
    # Force the no-op path by not calling configure_tracing (provider=None)
    # and temporarily pretending OTel is unavailable.
    original = tracing_mod._OTEL_AVAILABLE
    tracing_mod._OTEL_AVAILABLE = False  # type: ignore[attr-defined]
    try:
        with engine_span("op", tenant_id="t-1"):
            pass  # must not raise
    finally:
        tracing_mod._OTEL_AVAILABLE = original  # type: ignore[attr-defined]

"""Prometheus metrics registry for memory-layer.

All metrics are module-level singletons. Call :func:`configure_metrics` once
at startup. When ``prometheus_client`` is not installed, every helper is a
transparent no-op — no ``ImportError`` is ever raised at use-sites.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator

from memory_layer.config.settings import ObservabilitySettings

# ---------------------------------------------------------------------------
# Optional prometheus_client import
# ---------------------------------------------------------------------------

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )
    from starlette.responses import Response

    _PROM_AVAILABLE = True
except ImportError:
    _PROM_AVAILABLE = False
    Counter = None  # type: ignore[assignment]
    Gauge = None  # type: ignore[assignment]
    Histogram = None  # type: ignore[assignment]
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"
    generate_latest = None  # type: ignore[assignment]

    class Response:  # type: ignore[no-redef]
        def __init__(self, content: bytes, media_type: str) -> None:
            self.body = content
            self.media_type = media_type


# ---------------------------------------------------------------------------
# Metrics-enabled flag (set by configure_metrics)
# ---------------------------------------------------------------------------

_metrics_enabled: bool = False

# ---------------------------------------------------------------------------
# No-op stubs used when prometheus_client is absent or metrics disabled
# ---------------------------------------------------------------------------


class _NoOpMetric:
    """Silent stand-in for any Prometheus metric."""

    def labels(self, **_: Any) -> "_NoOpMetric":
        return self

    def inc(self, amount: float = 1) -> None:
        pass

    def observe(self, amount: float) -> None:
        pass

    def set(self, value: float) -> None:
        pass


_NOOP = _NoOpMetric()


def _make_counter(name: str, doc: str, labels: list[str]) -> Any:
    if _PROM_AVAILABLE:
        return Counter(name, doc, labels)
    return _NOOP


def _make_histogram(name: str, doc: str, labels: list[str]) -> Any:
    if _PROM_AVAILABLE:
        return Histogram(name, doc, labels)
    return _NOOP


def _make_gauge(name: str, doc: str, labels: list[str]) -> Any:
    if _PROM_AVAILABLE:
        return Gauge(name, doc, labels)
    return _NOOP


# ---------------------------------------------------------------------------
# Module-level metric singletons
# ---------------------------------------------------------------------------

memory_writes_total: Any = _NOOP
memory_searches_total: Any = _NOOP
memory_recalls_total: Any = _NOOP
memory_decays_total: Any = _NOOP
memory_consolidations_total: Any = _NOOP
extraction_facts_total: Any = _NOOP
contradictions_detected_total: Any = _NOOP

write_latency_seconds: Any = _NOOP
search_latency_seconds: Any = _NOOP
recall_latency_seconds: Any = _NOOP
extraction_latency_seconds: Any = _NOOP

memory_active_records: Any = _NOOP


def configure_metrics(settings: ObservabilitySettings) -> None:
    """Initialise Prometheus metrics; no-op if ``metrics_enabled=False``.

    Must be called once at application startup before any metric is incremented.
    Safe to call multiple times (subsequent calls are no-ops).
    """
    global _metrics_enabled
    global memory_writes_total, memory_searches_total, memory_recalls_total
    global memory_decays_total, memory_consolidations_total
    global extraction_facts_total, contradictions_detected_total
    global write_latency_seconds, search_latency_seconds
    global recall_latency_seconds, extraction_latency_seconds
    global memory_active_records

    _metrics_enabled = settings.metrics_enabled

    if not settings.metrics_enabled or not _PROM_AVAILABLE:
        return

    memory_writes_total = _make_counter(
        "memory_writes_total", "Total memory write operations.", ["tenant_id", "sector", "status"]
    )
    memory_searches_total = _make_counter(
        "memory_searches_total", "Total memory search operations.", ["tenant_id", "mode", "status"]
    )
    memory_recalls_total = _make_counter(
        "memory_recalls_total", "Total memory recall operations.", ["tenant_id", "mode", "status"]
    )
    memory_decays_total = _make_counter(
        "memory_decays_total", "Total memory decay operations.", ["tenant_id"]
    )
    memory_consolidations_total = _make_counter(
        "memory_consolidations_total", "Total memory consolidation operations.", ["tenant_id"]
    )
    extraction_facts_total = _make_counter(
        "extraction_facts_total", "Total facts extracted.", ["tenant_id", "sector"]
    )
    contradictions_detected_total = _make_counter(
        "contradictions_detected_total", "Total contradictions detected.", ["tenant_id", "resolution"]
    )
    write_latency_seconds = _make_histogram(
        "write_latency_seconds", "Write operation latency in seconds.", ["tenant_id"]
    )
    search_latency_seconds = _make_histogram(
        "search_latency_seconds", "Search operation latency in seconds.", ["tenant_id", "mode"]
    )
    recall_latency_seconds = _make_histogram(
        "recall_latency_seconds", "Recall operation latency in seconds.", ["tenant_id"]
    )
    extraction_latency_seconds = _make_histogram(
        "extraction_latency_seconds", "Extraction latency in seconds.", ["tenant_id"]
    )
    memory_active_records = _make_gauge(
        "memory_active_records", "Current number of active memory records.", ["tenant_id"]
    )


@contextmanager
def track_latency(histogram: Any, labels: dict[str, str]) -> Generator[None, None, None]:
    """Context manager that observes *histogram* with wall-clock duration on exit.

    Parameters
    ----------
    histogram:
        A Prometheus ``Histogram`` (or no-op stand-in).
    labels:
        Label key-value pairs forwarded to ``histogram.labels()``.
    """
    import time

    start = time.perf_counter()
    try:
        yield
    finally:
        duration = time.perf_counter() - start
        if _metrics_enabled:
            try:
                histogram.labels(**labels).observe(duration)
            except Exception:
                pass


def metrics_response() -> Any:
    """Return a Prometheus text-format HTTP response for the ``/metrics`` endpoint."""
    if not _PROM_AVAILABLE or not _metrics_enabled:
        return Response(content=b"", media_type="text/plain")
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )

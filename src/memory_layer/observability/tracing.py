"""Optional OpenTelemetry distributed tracing for memory-layer.

All OTel imports are wrapped in ``try/except ImportError`` so the module
remains fully functional when ``opentelemetry-sdk`` is not installed.

Call :func:`configure_tracing` once at startup, then obtain tracers via
:func:`get_tracer` and instrument use-case boundaries with :func:`engine_span`.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator, Literal

from memory_layer.config.settings import ObservabilitySettings

# ---------------------------------------------------------------------------
# Optional OTel imports
# ---------------------------------------------------------------------------

try:
    from opentelemetry import trace as _otel_trace  # type: ignore[import-untyped]
    from opentelemetry.sdk.resources import Resource  # type: ignore[import-untyped]
    from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-untyped]
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore[import-untyped]
    from opentelemetry.trace import NonRecordingSpan, StatusCode  # type: ignore[import-untyped]

    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False
    _otel_trace = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Module-level provider slot — populated only by configure_tracing
# ---------------------------------------------------------------------------

_provider: Any = None


def configure_tracing(settings: ObservabilitySettings) -> None:
    """Initialise the global tracer provider.

    Parameters
    ----------
    settings:
        Observability settings that control whether real tracing is active.

    Behaviour
    ---------
    * ``tracing_enabled=False`` — installs a ``NoOpTracerProvider``; all spans
      are no-ops with zero overhead.
    * ``tracing_enabled=True`` — installs a ``TracerProvider`` backed by
      an OTLP gRPC exporter when ``otlp_endpoint`` is set, otherwise uses an
      in-process no-export provider (safe for environments without a collector).
    """
    global _provider

    if not _OTEL_AVAILABLE:
        # OTel not installed — use internal no-op stub.
        _provider = _NoOpProvider()
        return

    if not settings.tracing_enabled:
        from opentelemetry.trace import NoOpTracerProvider  # type: ignore[import-untyped]

        _provider = NoOpTracerProvider()
        _otel_trace.set_tracer_provider(_provider)
        return

    # Real tracing.
    resource = Resource.create({"service.name": settings.service_name})
    provider = TracerProvider(resource=resource)

    if settings.otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,  # type: ignore[import-untyped]
            )

            exporter = OTLPSpanExporter(endpoint=settings.otlp_endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
        except ImportError:
            pass  # OTLP exporter not installed — proceed with no-export provider

    _otel_trace.set_tracer_provider(provider)
    _provider = provider


def get_tracer(name: str) -> Any:
    """Return a tracer from the configured provider.

    :func:`configure_tracing` must be called before :func:`get_tracer`.
    If it has not been called, a no-op tracer is returned.
    """
    if _provider is None:
        return _NoOpProvider().get_tracer(name)
    if _OTEL_AVAILABLE and _otel_trace is not None:
        return _otel_trace.get_tracer(name)
    return _provider.get_tracer(name)


@contextmanager
def engine_span(
    operation: str,
    tenant_id: str,
    **attrs: Any,
) -> Generator[Any, None, None]:
    """Context manager that opens a tracing span for a use-case boundary.

    Parameters
    ----------
    operation:
        Span name (e.g. ``"write_memory"``).
    tenant_id:
        Tenant identifier, stored as a span attribute.
    **attrs:
        Additional span attributes.

    Behaviour
    ---------
    * On normal completion: span is closed with OK status.
    * On exception: span status is set to ERROR and the exception is re-raised.
    * When OTel is unavailable: works as a transparent no-op context manager.
    """
    if not _OTEL_AVAILABLE or _provider is None or isinstance(_provider, _NoOpProvider):
        # Graceful no-op path.
        try:
            yield None
        except Exception:
            raise
        return

    tracer = _otel_trace.get_tracer("memory_layer.engine")
    with tracer.start_as_current_span(operation) as span:
        span.set_attribute("tenant_id", tenant_id)
        for key, value in attrs.items():
            span.set_attribute(key, str(value))
        try:
            yield span
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            raise


# ---------------------------------------------------------------------------
# Internal no-op stubs used when OTel is unavailable
# ---------------------------------------------------------------------------


class _NoOpSpan:
    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, status: Any, description: str = "") -> None:
        pass

    def __enter__(self) -> "_NoOpSpan":
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_val: object,
        exc_tb: object,
    ) -> Literal[False]:
        return False


class _NoOpTracer:
    def start_as_current_span(self, name: str) -> _NoOpSpan:
        return _NoOpSpan()


class _NoOpProvider:
    def get_tracer(self, name: str) -> _NoOpTracer:
        return _NoOpTracer()

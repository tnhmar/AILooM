"""Structured logging for memory-layer.

Call :func:`configure_logging` once at application startup.
Then use :class:`MemoryLayerLogger` for all log emission; it auto-injects
``component`` and forwards arbitrary ``**context`` fields to the log record.
"""

from __future__ import annotations

import logging
import logging.config
from typing import Any

from memory_layer.config.settings import ObservabilitySettings

_CONTEXT_FIELDS = ("tenant_id", "trace_id", "component")


class _ContextFilter(logging.Filter):
    """Inject missing context fields with empty-string defaults.

    ``pythonjsonlogger`` serialises extra kwargs passed to log calls only when
    they are present on the ``LogRecord``. This filter guarantees the three
    standard context fields always appear, even when not supplied by the caller.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        for field in _CONTEXT_FIELDS:
            if not hasattr(record, field):
                setattr(record, field, "")
        return True


def configure_logging(settings: ObservabilitySettings, log_level: str = "INFO") -> None:
    """Configure the root logger once at startup.

    Parameters
    ----------
    settings:
        Observability settings controlling JSON vs plain formatting.
    log_level:
        Root log level string (e.g. ``"INFO"``, ``"DEBUG"``).
        Typically sourced from ``ServerSettings.log_level``.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates on re-configuration.
    root.handlers.clear()

    handler = logging.StreamHandler()
    handler.addFilter(_ContextFilter())

    if settings.log_json:
        try:
            from pythonjsonlogger.json import JsonFormatter
        except ImportError:
            from pythonjsonlogger import jsonlogger  # type: ignore[import-untyped]
            JsonFormatter = jsonlogger.JsonFormatter  # type: ignore[assignment]

        fmt: logging.Formatter = JsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s "
                "%(tenant_id)s %(trace_id)s %(component)s",
            rename_fields={"asctime": "timestamp", "levelname": "level", "name": "name"},
        )
    else:
        fmt = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s %(message)s"
                " tenant=%(tenant_id)s trace=%(trace_id)s component=%(component)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )

    handler.setFormatter(fmt)
    root.addHandler(handler)


class MemoryLayerLogger:
    """Thin stdlib-Logger wrapper that auto-injects ``component`` context.

    Any extra keyword arguments passed to the log methods are forwarded as
    ``extra`` fields on the ``LogRecord``, making them available to formatters
    (including JSON formatters).

    Usage::

        logger = MemoryLayerLogger(__name__, component="write_pipeline")
        logger.info("Memory written", tenant_id="t-1", trace_id="tr-1")
    """

    def __init__(self, name: str, component: str = "") -> None:
        self._logger = logging.getLogger(name)
        self._component = component

    def _extra(self, context: dict[str, Any]) -> dict[str, Any]:
        merged = {"component": self._component}
        merged.update(context)
        return merged

    def info(self, msg: str, **context: Any) -> None:
        """Log *msg* at INFO level with optional context fields."""
        self._logger.info(msg, extra=self._extra(context))

    def warning(self, msg: str, **context: Any) -> None:
        """Log *msg* at WARNING level with optional context fields."""
        self._logger.warning(msg, extra=self._extra(context))

    def error(self, msg: str, **context: Any) -> None:
        """Log *msg* at ERROR level with optional context fields."""
        self._logger.error(msg, extra=self._extra(context))

    def debug(self, msg: str, **context: Any) -> None:
        """Log *msg* at DEBUG level with optional context fields."""
        self._logger.debug(msg, extra=self._extra(context))

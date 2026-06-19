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
    """Inject missing context fields with empty-string defaults."""

    def filter(self, record: logging.LogRecord) -> bool:
        for field in _CONTEXT_FIELDS:
            if not hasattr(record, field):
                setattr(record, field, "")
        return True


def configure_logging(settings: ObservabilitySettings, log_level: str = "INFO") -> None:
    """Configure the root logger once at startup."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    root.handlers.clear()

    handler = logging.StreamHandler()
    handler.addFilter(_ContextFilter())

    if settings.log_json:
        try:
            from pythonjsonlogger.json import JsonFormatter
        except ImportError:
            from pythonjsonlogger import jsonlogger
            JsonFormatter = jsonlogger.JsonFormatter

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
    """Thin stdlib-Logger wrapper that auto-injects ``component`` context."""

    def __init__(self, name: str, component: str = "") -> None:
        self._logger = logging.getLogger(name)
        self._component = component

    def _extra(self, context: dict[str, Any]) -> dict[str, Any]:
        merged = {"component": self._component}
        merged.update(context)
        return merged

    def info(self, msg: str, **context: Any) -> None:
        self._logger.info(msg, extra=self._extra(context))

    def warning(self, msg: str, **context: Any) -> None:
        self._logger.warning(msg, extra=self._extra(context))

    def error(self, msg: str, **context: Any) -> None:
        self._logger.error(msg, extra=self._extra(context))

    def debug(self, msg: str, **context: Any) -> None:
        self._logger.debug(msg, extra=self._extra(context))

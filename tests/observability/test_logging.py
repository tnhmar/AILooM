"""Acceptance tests for structured logging — M6-T2 (7 tests)."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from memory_layer.config.settings import ObservabilitySettings
from memory_layer.observability.logging import MemoryLayerLogger, configure_logging


@pytest.fixture(autouse=True)
def reset_root_logger():
    """Restore root logger handlers after each test."""
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    yield
    root.handlers = original_handlers
    root.level = original_level


# 1
def test_configure_logging_does_not_raise() -> None:
    settings = ObservabilitySettings(log_json=False)
    configure_logging(settings, log_level="INFO")  # must not raise


# 2
def test_memory_layer_logger_info_calls_underlying_logger() -> None:
    ml = MemoryLayerLogger(__name__, component="test")
    with patch.object(ml._logger, "info") as mock_info:
        ml.info("hello")
    mock_info.assert_called_once()


# 3
def test_memory_layer_logger_warning_calls_underlying_logger() -> None:
    ml = MemoryLayerLogger(__name__, component="test")
    with patch.object(ml._logger, "warning") as mock_warn:
        ml.warning("beware")
    mock_warn.assert_called_once()


# 4
def test_memory_layer_logger_error_calls_underlying_logger() -> None:
    ml = MemoryLayerLogger(__name__, component="test")
    with patch.object(ml._logger, "error") as mock_err:
        ml.error("boom")
    mock_err.assert_called_once()


# 5
def test_context_kwargs_passed_to_log_record() -> None:
    ml = MemoryLayerLogger(__name__, component="pipeline")
    captured: list[dict] = []

    original_info = ml._logger.info

    def capturing_info(msg: str, *args, **kwargs):
        captured.append(kwargs)
        return original_info(msg, *args, **kwargs)

    with patch.object(ml._logger, "info", side_effect=capturing_info):
        ml.info("test", tenant_id="t-1", trace_id="tr-1")

    assert len(captured) == 1
    extra = captured[0].get("extra", {})
    assert extra.get("tenant_id") == "t-1"
    assert extra.get("trace_id") == "tr-1"
    assert extra.get("component") == "pipeline"


# 6
def test_log_json_true_configures_json_formatter() -> None:
    settings = ObservabilitySettings(log_json=True)
    configure_logging(settings, log_level="INFO")
    root = logging.getLogger()
    assert len(root.handlers) >= 1
    formatter = root.handlers[-1].formatter
    # JsonFormatter class name contains "Json"
    assert "Json" in type(formatter).__name__ or "json" in type(formatter).__module__.lower()


# 7
def test_log_json_false_configures_plain_formatter() -> None:
    settings = ObservabilitySettings(log_json=False)
    configure_logging(settings, log_level="DEBUG")
    root = logging.getLogger()
    assert len(root.handlers) >= 1
    formatter = root.handlers[-1].formatter
    assert isinstance(formatter, logging.Formatter)
    # Confirm it is NOT a JSON formatter (no "Json" in name)
    assert "Json" not in type(formatter).__name__

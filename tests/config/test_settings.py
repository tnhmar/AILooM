"""Acceptance tests for the config system — M6-T1."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from memory_layer.config.loader import get_settings, override_settings, reset_settings
from memory_layer.config.settings import (
    ObservabilitySettings,
    Settings,
    StorageSettings,
)


@pytest.fixture(autouse=True)
def isolate_settings():
    """Reset the singleton before and after every test."""
    reset_settings()
    yield
    reset_settings()


# 1
def test_settings_constructs_with_defaults() -> None:
    s = Settings()
    assert s.server is not None
    assert s.storage is not None
    assert s.embedding is not None
    assert s.llm is not None
    assert s.scheduler is not None
    assert s.observability is not None


# 2
def test_server_port_default_is_8000() -> None:
    s = Settings()
    assert s.server.port == 8000


# 3
def test_storage_backend_default_is_sqlite() -> None:
    s = Settings()
    assert s.storage.backend == "sqlite"


# 4
def test_embedding_provider_default_is_openai() -> None:
    s = Settings()
    assert s.embedding.provider == "openai"


# 5
def test_env_var_overrides_server_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_LAYER_SERVER__PORT", "9090")
    s = Settings()
    assert s.server.port == 9090


# 6
def test_env_var_overrides_storage_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_LAYER_STORAGE__BACKEND", "postgres")
    s = Settings()
    assert s.storage.backend == "postgres"


# 7
def test_nested_env_var_overrides_llm_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_LAYER_LLM__MODEL_ID", "gpt-4o")
    s = Settings()
    assert s.llm.model_id == "gpt-4o"


# 8
def test_get_settings_returns_singleton() -> None:
    a = get_settings()
    b = get_settings()
    assert a is b


# 9
def test_override_settings_replaces_singleton() -> None:
    custom = Settings()
    override_settings(custom)
    assert get_settings() is custom


# 10
def test_reset_settings_clears_singleton() -> None:
    first = get_settings()
    reset_settings()
    second = get_settings()
    assert first is not second


# 11
def test_invalid_storage_backend_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        StorageSettings(backend="redis")  # type: ignore[arg-type]


# 12
def test_observability_defaults() -> None:
    o = ObservabilitySettings()
    assert o.metrics_enabled is True
    assert o.tracing_enabled is False

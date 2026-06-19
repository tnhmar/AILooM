"""Layered, validated configuration for memory-layer.

All settings are read from (in descending priority):
1. Environment variables prefixed with ``MEMORY_LAYER_`` using ``__`` as the
   nested delimiter (e.g. ``MEMORY_LAYER_SERVER__PORT=9090``).
2. A ``.env`` file in the working directory.
3. Safe defaults defined below.

Import the singleton via :func:`~memory_layer.config.loader.get_settings`.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerSettings(BaseModel):
    """HTTP server configuration."""

    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1
    log_level: str = "INFO"
    reload: bool = False


class StorageSettings(BaseModel):
    """Persistent storage and vector-store configuration."""

    backend: Literal["sqlite", "postgres"] = "sqlite"
    sqlite_path: str = "./data/memory_layer.db"
    postgres_dsn: Optional[str] = None
    vector_backend: Literal["chroma", "qdrant", "pgvector"] = "chroma"
    chroma_path: str = "./data/chroma"
    qdrant_url: Optional[str] = None
    qdrant_api_key: Optional[str] = None


class EmbeddingSettings(BaseModel):
    """Embedding model configuration."""

    provider: Literal["openai", "ollama", "sentence-transformers"] = "openai"
    model_id: str = "text-embedding-3-small"
    dimensions: int = 1536
    api_key: Optional[str] = None
    base_url: Optional[str] = None


class LLMSettings(BaseModel):
    """LLM configuration used for enrichment and recall."""

    provider: Literal["openai", "anthropic", "ollama"] = "openai"
    model_id: str = "gpt-4o-mini"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    max_tokens: int = 2048
    temperature: float = 0.0


class SchedulerSettings(BaseModel):
    """Background scheduler configuration for decay and consolidation jobs."""

    decay_interval_seconds: int = 3600
    consolidation_interval_seconds: int = 1800
    enabled: bool = True


class ObservabilitySettings(BaseModel):
    """Metrics, tracing, and structured logging configuration."""

    metrics_enabled: bool = True
    tracing_enabled: bool = False
    otlp_endpoint: Optional[str] = None
    service_name: str = "memory-layer"
    log_json: bool = False


class Settings(BaseSettings):  # type: ignore[misc]
    """Root settings object for memory-layer.

    Reads from env vars prefixed ``MEMORY_LAYER_`` with ``__`` as the nested
    delimiter, then from a ``.env`` file, then from defaults.
    """

    server: ServerSettings = ServerSettings()
    storage: StorageSettings = StorageSettings()
    embedding: EmbeddingSettings = EmbeddingSettings()
    llm: LLMSettings = LLMSettings()
    scheduler: SchedulerSettings = SchedulerSettings()
    observability: ObservabilitySettings = ObservabilitySettings()

    model_config = SettingsConfigDict(
        env_prefix="MEMORY_LAYER_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

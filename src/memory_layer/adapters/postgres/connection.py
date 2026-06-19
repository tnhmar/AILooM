"""asyncpg connection pool factory for the PostgreSQL storage adapter."""

from __future__ import annotations

import asyncpg

from memory_layer.config.settings import StorageSettings


async def create_pool(settings: StorageSettings) -> asyncpg.Pool[asyncpg.Record]:
    """Create and return an asyncpg connection pool."""
    if not settings.postgres_dsn:
        raise ValueError(
            "StorageSettings.postgres_dsn must be set to use the PostgreSQL adapter."
        )

    min_size: int = getattr(settings, "postgres_pool_min_size", 2)
    max_size: int = getattr(settings, "postgres_pool_max_size", 10)

    pool: asyncpg.Pool[asyncpg.Record] = await asyncpg.create_pool(
        dsn=settings.postgres_dsn,
        min_size=min_size,
        max_size=max_size,
    )
    return pool


async def close_pool(pool: asyncpg.Pool[asyncpg.Record]) -> None:
    """Gracefully close all connections in *pool*."""
    await pool.close()

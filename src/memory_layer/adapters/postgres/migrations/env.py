"""Alembic env.py — async migrations via asyncpg.

This file is invoked by Alembic at runtime.  The actual schema changes are
applied by :class:`~memory_layer.adapters.postgres.migrator.SchemaMigrator`;
this env.py exists so that ``alembic`` CLI commands (``history``, ``current``,
``stamp``) continue to work against the same DSN.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Alembic Config object — gives access to .ini values.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Load DSN from Settings
# ---------------------------------------------------------------------------

try:
    from memory_layer.config.loader import get_settings

    _dsn = get_settings().storage.postgres_dsn
    if _dsn:
        # asyncpg DSN → SQLAlchemy async DSN
        if _dsn.startswith("postgresql://"):
            _dsn = _dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif _dsn.startswith("postgres://"):
            _dsn = _dsn.replace("postgres://", "postgresql+asyncpg://", 1)
        config.set_main_option("sqlalchemy.url", _dsn)
except Exception:
    pass  # DSN not required when running offline or generating scripts.

target_metadata = None  # We use raw DDL; no SQLAlchemy ORM metadata needed.


# ---------------------------------------------------------------------------
# Offline migrations (generate SQL script without live DB)
# ---------------------------------------------------------------------------


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online migrations (applied against a live DB)
# ---------------------------------------------------------------------------


def do_run_migrations(connection):  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

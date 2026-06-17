"""SQLite migration runner for memory-layer.

Conventions
-----------
- Migration files live next to this module in ``migrations/``.
- Each file is named ``V{N}__{description}.sql`` where N is a positive integer.
- Files are applied in ascending version order.
- Each migration runs inside its own transaction; a failed migration is rolled
  back and the database is left at the last successfully applied version.
- The runner is idempotent: versions already recorded in ``schema_version`` are
  skipped.
- If the on-disk schema version is *ahead* of the highest known migration file,
  ``SchemaVersionError`` is raised to prevent accidental downgrade.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from memory_layer.domain.exceptions import SchemaVersionError

# Directory that holds V{N}__*.sql files, resolved relative to this module.
_MIGRATIONS_DIR: Path = Path(__file__).parent / "migrations"


def _parse_version(filename: str) -> int:
    """Extract the integer version from a migration filename.

    Parameters
    ----------
    filename:
        Bare filename (not a full path), e.g. ``"V1__initial_schema.sql"``.

    Returns
    -------
    int
        The version number embedded in the filename.

    Raises
    ------
    ValueError
        If the filename does not match the expected ``V{N}__*.sql`` pattern.
    """
    match = re.match(r"^V(\d+)__.*\.sql$", filename)
    if not match:
        raise ValueError(
            f"Migration filename does not match pattern V{{N}}__description.sql: {filename!r}"
        )
    return int(match.group(1))


def get_current_version(conn: sqlite3.Connection) -> int:
    """Return the highest migration version already applied to the database.

    Returns ``0`` if the ``schema_version`` table does not yet exist (fresh DB).
    """
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    )
    if cursor.fetchone() is None:
        return 0
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def run_migrations(conn: sqlite3.Connection) -> list[int]:
    """Discover and apply all pending migrations in version order.

    Each migration is wrapped in its own transaction so a failure at version N
    does not corrupt earlier applied versions.

    Parameters
    ----------
    conn:
        An open ``sqlite3.Connection``.  ``isolation_level`` is temporarily set
        to ``None`` (autocommit) so that each migration controls its own
        ``BEGIN`` / ``COMMIT`` / ``ROLLBACK``.

    Returns
    -------
    list[int]
        Versions that were applied during this invocation (empty if everything
        was already up-to-date).

    Raises
    ------
    SchemaVersionError
        If the database is ahead of the highest known migration file.
    """
    # Collect all .sql files and sort by version.
    migration_files: list[tuple[int, Path]] = []
    for path in _MIGRATIONS_DIR.glob("V*.sql"):
        try:
            version = _parse_version(path.name)
        except ValueError:
            continue
        migration_files.append((version, path))
    migration_files.sort(key=lambda t: t[0])

    current = get_current_version(conn)
    known_max = migration_files[-1][0] if migration_files else 0

    if current > known_max:
        raise SchemaVersionError(expected=known_max, found=current)

    applied: list[int] = []
    old_isolation = conn.isolation_level
    conn.isolation_level = None  # manual transaction control

    try:
        for version, path in migration_files:
            if version <= current:
                continue

            sql = path.read_text(encoding="utf-8")
            now = datetime.now(UTC).isoformat()

            conn.execute("BEGIN")
            try:
                conn.executescript(sql)
                # executescript implicitly commits; record version in its own statement.
                conn.execute(
                    "INSERT INTO schema_version"
                    " (version, description, applied_at) VALUES (?, ?, ?)",
                    (version, path.stem, now),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

            applied.append(version)
    finally:
        conn.isolation_level = old_isolation

    return applied


def ensure_schema(db_path: str) -> sqlite3.Connection:
    """Open (or create) a SQLite database and apply all pending migrations.

    Parameters
    ----------
    db_path:
        Filesystem path to the SQLite file, or ``":memory:"`` for an in-process
        database.  Parent directories are created automatically.

    Returns
    -------
    sqlite3.Connection
        A configured, migration-complete connection.  The caller owns the
        connection and is responsible for closing it.

    Raises
    ------
    SchemaVersionError
        If the database schema is ahead of the highest known migration.
    """
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    run_migrations(conn)
    return conn

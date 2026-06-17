"""Tests for SQLite migration runner — M2-T1 acceptance suite."""

from __future__ import annotations

import sqlite3

import pytest

from memory_layer.storage.sqlite.migration_runner import (
    _parse_version,
    ensure_schema,
    get_current_version,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _index_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# 1. ensure_schema(":memory:") succeeds and returns a connection
# ---------------------------------------------------------------------------

def test_ensure_schema_returns_connection() -> None:
    conn = ensure_schema(":memory:")
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


# ---------------------------------------------------------------------------
# 2. After ensure_schema, get_current_version returns the latest migration
# ---------------------------------------------------------------------------

def test_current_version_after_ensure_schema() -> None:
    conn = ensure_schema(":memory:")
    assert get_current_version(conn) == 2
    conn.close()


# ---------------------------------------------------------------------------
# 3. Running ensure_schema a second time is idempotent
# ---------------------------------------------------------------------------

def test_ensure_schema_idempotent(tmp_path: pytest.TempPathFactory) -> None:
    db_path = str(tmp_path / "test.db")
    conn1 = ensure_schema(db_path)
    conn1.close()
    conn2 = ensure_schema(db_path)
    assert get_current_version(conn2) == 2
    conn2.close()


# ---------------------------------------------------------------------------
# 4. All expected tables exist
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("table", [
    "memory_records",
    "facts",
    "audit_log",
    "tenant_policies",
])
def test_expected_tables_exist(table: str) -> None:
    conn = ensure_schema(":memory:")
    assert _table_exists(conn, table), f"Table {table!r} missing"
    conn.close()


# ---------------------------------------------------------------------------
# 5. idx_mr_tenant_lifecycle index exists
# ---------------------------------------------------------------------------

def test_index_mr_tenant_lifecycle_exists() -> None:
    conn = ensure_schema(":memory:")
    assert _index_exists(conn, "idx_mr_tenant_lifecycle")
    conn.close()


# ---------------------------------------------------------------------------
# 6. idx_facts_tenant_entity index exists
# ---------------------------------------------------------------------------

def test_index_facts_tenant_entity_exists() -> None:
    conn = ensure_schema(":memory:")
    assert _index_exists(conn, "idx_facts_tenant_entity")
    conn.close()


# ---------------------------------------------------------------------------
# 7. Unique index on idempotency_key enforces uniqueness per tenant
# ---------------------------------------------------------------------------

def test_idempotency_unique_constraint() -> None:
    conn = ensure_schema(":memory:")
    base = dict(
        id="mr-1",
        tenant_id="t1",
        principal_id="u1",
        principal_type="USER",
        raw_payload="hello",
        payload_type="CONVERSATION_TURN",
        sector="EPISODIC",
        recorded_at="2026-01-01T00:00:00Z",
        idempotency_key="key-abc",
    )
    conn.execute(
        """
        INSERT INTO memory_records
            (id, tenant_id, principal_id, principal_type, raw_payload,
             payload_type, sector, recorded_at, idempotency_key)
        VALUES
            (:id, :tenant_id, :principal_id, :principal_type, :raw_payload,
             :payload_type, :sector, :recorded_at, :idempotency_key)
        """,
        base,
    )
    conn.commit()

    duplicate = {**base, "id": "mr-2"}  # same tenant + idempotency_key
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO memory_records
                (id, tenant_id, principal_id, principal_type, raw_payload,
                 payload_type, sector, recorded_at, idempotency_key)
            VALUES
                (:id, :tenant_id, :principal_id, :principal_type, :raw_payload,
                 :payload_type, :sector, :recorded_at, :idempotency_key)
            """,
            duplicate,
        )
    conn.close()


# ---------------------------------------------------------------------------
# 8. Inserting a row with lifecycle_state omitted defaults to 'ACTIVE'
# ---------------------------------------------------------------------------

def test_lifecycle_state_defaults_to_active() -> None:
    conn = ensure_schema(":memory:")
    conn.execute(
        """
        INSERT INTO memory_records
            (id, tenant_id, principal_id, principal_type, raw_payload,
             payload_type, sector, recorded_at)
        VALUES
            ('mr-default', 't1', 'u1', 'USER', 'payload',
             'CONVERSATION_TURN', 'EPISODIC', '2026-01-01T00:00:00Z')
        """
    )
    conn.commit()
    row = conn.execute(
        "SELECT lifecycle_state FROM memory_records WHERE id='mr-default'"
    ).fetchone()
    assert row is not None
    assert row[0] == "ACTIVE"
    conn.close()


# ---------------------------------------------------------------------------
# 9. get_current_version returns 0 on a fresh empty DB
# ---------------------------------------------------------------------------

def test_get_current_version_returns_zero_on_fresh_db() -> None:
    conn = sqlite3.connect(":memory:")
    assert get_current_version(conn) == 0
    conn.close()


# ---------------------------------------------------------------------------
# 10. _parse_version("V1__initial_schema.sql") returns 1
# ---------------------------------------------------------------------------

def test_parse_version_v1() -> None:
    assert _parse_version("V1__initial_schema.sql") == 1


# ---------------------------------------------------------------------------
# 11. _parse_version("V12__add_embeddings.sql") returns 12
# ---------------------------------------------------------------------------

def test_parse_version_v12() -> None:
    assert _parse_version("V12__add_embeddings.sql") == 12


# ---------------------------------------------------------------------------
# 12. _parse_version("bad_name.sql") raises ValueError
# ---------------------------------------------------------------------------

def test_parse_version_bad_name_raises() -> None:
    with pytest.raises(ValueError, match="does not match pattern"):
        _parse_version("bad_name.sql")

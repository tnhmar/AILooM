"""0001_initial — create all core tables.

Revision ID: 0001_initial
Revises: (none)
Create Date: 2026-06-19
"""

from __future__ import annotations

from alembic import op

# Alembic revision identifiers.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# DDL strings (imported from adapter modules)
# ---------------------------------------------------------------------------

from memory_layer.adapters.postgres.record_repo import MEMORY_RECORDS_DDL  # noqa: E402
from memory_layer.adapters.postgres.fact_repo import FACTS_DDL  # noqa: E402
from memory_layer.adapters.postgres.audit_repo import AUDIT_LOG_DDL  # noqa: E402
from memory_layer.adapters.postgres.trace_repo import TRACES_DDL  # noqa: E402
from memory_layer.adapters.postgres.vector_index import VECTOR_INDEX_DDL  # noqa: E402


def upgrade() -> None:
    """Apply all five DDL blocks in dependency order."""
    op.execute(MEMORY_RECORDS_DDL)
    op.execute(FACTS_DDL)
    op.execute(AUDIT_LOG_DDL)
    op.execute(TRACES_DDL)
    op.execute(VECTOR_INDEX_DDL)


def downgrade() -> None:
    """Drop all five tables in reverse order."""
    op.execute("DROP TABLE IF EXISTS vector_index CASCADE")
    op.execute("DROP TABLE IF EXISTS memory_traces CASCADE")
    op.execute("DROP TABLE IF EXISTS audit_log CASCADE")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_immutable ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_immutable() CASCADE")
    op.execute("DROP TABLE IF EXISTS facts CASCADE")
    op.execute("DROP TABLE IF EXISTS memory_records CASCADE")

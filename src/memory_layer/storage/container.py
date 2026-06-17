"""LocalContainer — composition root for local (SQLite + ChromaDB) mode.

Instantiated once per process. Runs schema migrations on creation.

Design notes
------------
- ``create`` is the sole factory.  It applies all pending SQLite migrations
  via ``ensure_schema`` before constructing any adapter, so every adapter
  receives a fully migrated DB.
- No lazy initialisation, no singletons, no global state.  Each call to
  ``create`` returns an independent container with its own adapter instances.
- ``chroma_dir`` defaults to ``"./chroma_data"`` and is passed directly to
  ``ChromaVectorIndex``.  In tests, pass ``chroma_dir=""`` alongside a
  ``chroma_settings`` override, or rely on the default ephemeral path.
- The container is a plain ``@dataclass`` so fields are directly accessible
  and the object is easily inspected or replaced in tests via ``dataclasses
  .replace``.
"""

from __future__ import annotations

from dataclasses import dataclass

from memory_layer.storage.sqlite.audit_repo import SqliteAuditLog
from memory_layer.storage.sqlite.fact_repo import SqliteFactRepository
from memory_layer.storage.sqlite.migration_runner import ensure_schema
from memory_layer.storage.sqlite.policy_repo import SqliteTenantPolicyRepository
from memory_layer.storage.sqlite.record_repo import SqliteMemoryRecordRepository
from memory_layer.storage.vector.local_vector import ChromaVectorIndex


@dataclass
class LocalContainer:
    """Wired set of local storage adapters for a single process.

    Attributes
    ----------
    db_path:
        Path to the SQLite database file (or ``":memory:"`` for tests).
    chroma_dir:
        Directory used by the ChromaDB persistent client.
    records:
        SQLite adapter for :class:`~memory_layer.domain.records.MemoryRecord`.
    facts:
        SQLite adapter for :class:`~memory_layer.domain.records.Fact`.
    audit:
        SQLite append-only audit log adapter.
    policies:
        SQLite per-tenant policy repository adapter.
    vector_index:
        ChromaDB local vector similarity index.
    """

    db_path: str
    chroma_dir: str
    records: SqliteMemoryRecordRepository
    facts: SqliteFactRepository
    audit: SqliteAuditLog
    policies: SqliteTenantPolicyRepository
    vector_index: ChromaVectorIndex

    @classmethod
    def create(
        cls,
        db_path: str = "./memory_layer.db",
        chroma_dir: str = "./chroma_data",
    ) -> "LocalContainer":
        """Create a fully wired :class:`LocalContainer`.

        Applies all pending SQLite migrations before constructing adapters.

        Parameters
        ----------
        db_path:
            Filesystem path to the SQLite file, or ``":memory:"`` for an
            in-process database.  Parent directories are created automatically.
        chroma_dir:
            Directory for ChromaDB persistent storage.

        Returns
        -------
        LocalContainer
            Ready-to-use container with all adapters initialised.
        """
        conn = ensure_schema(db_path)
        conn.close()
        return cls(
            db_path=db_path,
            chroma_dir=chroma_dir,
            records=SqliteMemoryRecordRepository(db_path),
            facts=SqliteFactRepository(db_path),
            audit=SqliteAuditLog(db_path),
            policies=SqliteTenantPolicyRepository(db_path),
            vector_index=ChromaVectorIndex(chroma_dir),
        )

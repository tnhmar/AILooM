"""Acceptance tests for LocalContainer — M2-T6."""

from __future__ import annotations

import pytest
import pytest_asyncio

from memory_layer.domain.records import MemoryRecord, Scope
from memory_layer.domain.types import (
    LifecycleState,
    MemorySector,
    PayloadType,
    PipelineStatus,
    PrincipalId,
    PrincipalType,
    new_memory_id,
    new_tenant_id,
)
from memory_layer.storage.container import LocalContainer
from memory_layer.storage.sqlite.audit_repo import SqliteAuditLog
from memory_layer.storage.sqlite.fact_repo import SqliteFactRepository
from memory_layer.storage.sqlite.policy_repo import SqliteTenantPolicyRepository
from memory_layer.storage.sqlite.record_repo import SqliteMemoryRecordRepository
from memory_layer.storage.vector.local_vector import ChromaVectorIndex

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def container(tmp_path: pytest.TempPathFactory) -> LocalContainer:
    """A freshly created LocalContainer backed by a tmp SQLite DB.

    chroma_dir is also scoped to tmp_path to avoid cross-test pollution.
    """
    db = str(tmp_path / "container_test.db")
    chroma = str(tmp_path / "chroma")
    return LocalContainer.create(db_path=db, chroma_dir=chroma)


# ---------------------------------------------------------------------------
# 1. LocalContainer.create succeeds
# ---------------------------------------------------------------------------

async def test_create_succeeds(tmp_path: pytest.TempPathFactory) -> None:
    db = str(tmp_path / "smoke.db")
    c = LocalContainer.create(db_path=db, chroma_dir=str(tmp_path / "chroma"))
    assert isinstance(c, LocalContainer)


# ---------------------------------------------------------------------------
# 2. container.records is SqliteMemoryRecordRepository
# ---------------------------------------------------------------------------

async def test_records_type(container: LocalContainer) -> None:
    assert isinstance(container.records, SqliteMemoryRecordRepository)


# ---------------------------------------------------------------------------
# 3. container.facts is SqliteFactRepository
# ---------------------------------------------------------------------------

async def test_facts_type(container: LocalContainer) -> None:
    assert isinstance(container.facts, SqliteFactRepository)


# ---------------------------------------------------------------------------
# 4. container.audit is SqliteAuditLog
# ---------------------------------------------------------------------------

async def test_audit_type(container: LocalContainer) -> None:
    assert isinstance(container.audit, SqliteAuditLog)


# ---------------------------------------------------------------------------
# 5. container.policies is SqliteTenantPolicyRepository
# ---------------------------------------------------------------------------

async def test_policies_type(container: LocalContainer) -> None:
    assert isinstance(container.policies, SqliteTenantPolicyRepository)


# ---------------------------------------------------------------------------
# 6. container.vector_index is ChromaVectorIndex
# ---------------------------------------------------------------------------

async def test_vector_index_type(container: LocalContainer) -> None:
    assert isinstance(container.vector_index, ChromaVectorIndex)


# ---------------------------------------------------------------------------
# 7. Calling create twice with the same db_path is idempotent
# ---------------------------------------------------------------------------

async def test_create_twice_idempotent(tmp_path: pytest.TempPathFactory) -> None:
    db = str(tmp_path / "idempotent.db")
    chroma = str(tmp_path / "chroma")
    c1 = LocalContainer.create(db_path=db, chroma_dir=chroma)
    c2 = LocalContainer.create(db_path=db, chroma_dir=chroma)
    assert isinstance(c1, LocalContainer)
    assert isinstance(c2, LocalContainer)
    # Both containers reference the same db_path
    assert c1.db_path == c2.db_path


# ---------------------------------------------------------------------------
# 8. End-to-end smoke: save + get_by_id via container.records
# ---------------------------------------------------------------------------

async def test_e2e_save_and_retrieve(container: LocalContainer) -> None:
    tid = new_tenant_id()
    scope = Scope(
        tenant_id=tid,
        principal_id=PrincipalId("user-smoke"),
        principal_type=PrincipalType.USER,
    )
    record = MemoryRecord(
        id=new_memory_id(),
        tenant_id=tid,
        scope=scope,
        raw_payload="hello from container smoke test",
        payload_type=PayloadType.CONVERSATION_TURN,
        sector=MemorySector.EPISODIC,
        lifecycle_state=LifecycleState.ACTIVE,
        pipeline_status=PipelineStatus.PENDING,
    )
    await container.records.save(record)
    fetched = await container.records.get_by_id(record.id, tid)
    assert fetched is not None
    assert fetched.id == record.id
    assert fetched.raw_payload == record.raw_payload
    assert fetched.tenant_id == tid

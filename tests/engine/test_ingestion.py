"""Acceptance tests for WriteMemoryService — M3-T1."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from memory_layer.domain.records import MemoryRecord, Scope, WriteRequest, WriteResult
from memory_layer.domain.types import (
    LifecycleState,
    MemorySector,
    PayloadType,
    PipelineStatus,
    PrincipalType,
    TenantId,
    new_memory_id,
)
from memory_layer.engine.ingestion import WriteMemoryService
from memory_layer.ports.inbound import WriteMemoryUseCase
from memory_layer.domain.events import MemoryWrittenEvent
from memory_layer.ports.outbound import ExtractionResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TENANT = TenantId("tenant-test")
_SCOPE = Scope(
    tenant_id=TENANT,
    principal_id="user-1",  # type: ignore[arg-type]
    principal_type=PrincipalType.USER,
)


def _make_request(
    payload_type: PayloadType = PayloadType.CONVERSATION_TURN,
    sector: MemorySector | None = None,
    idempotency_key: str | None = None,
    extract: bool = False,
    wait_for_enrichment: bool = False,
) -> WriteRequest:
    return WriteRequest(
        tenant_id=TENANT,
        scope=_SCOPE,
        raw_payload="hello world",
        payload_type=payload_type,
        sector=sector,
        idempotency_key=idempotency_key,
        extract=extract,
        wait_for_enrichment=wait_for_enrichment,
    )


def _make_service(
    extraction: object = None,
    existing_record: MemoryRecord | None = None,
) -> tuple[WriteMemoryService, AsyncMock, AsyncMock, AsyncMock]:
    record_repo = AsyncMock()
    record_repo.get_by_idempotency_key.return_value = existing_record
    audit_log = AsyncMock()
    observer = AsyncMock()
    svc = WriteMemoryService(
        record_repo=record_repo,
        audit_log=audit_log,
        observer=observer,
        extraction=extraction,  # type: ignore[arg-type]
    )
    return svc, record_repo, audit_log, observer


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# 1. Protocol satisfaction
def test_isinstance_write_memory_use_case() -> None:
    svc, *_ = _make_service()
    assert isinstance(svc, WriteMemoryUseCase)


# 2. Successful write returns WriteResult with correct fields
@pytest.mark.asyncio
async def test_write_returns_write_result() -> None:
    svc, *_ = _make_service()
    result = await svc.execute(_make_request())
    assert isinstance(result, WriteResult)
    assert result.pipeline_status == PipelineStatus.PENDING
    assert not result.idempotent
    assert result.memory_id


# 3. repo.save called exactly once
@pytest.mark.asyncio
async def test_repo_save_called_once() -> None:
    svc, repo, *_ = _make_service()
    await svc.execute(_make_request())
    repo.save.assert_awaited_once()


# 4. audit_log.append called exactly once
@pytest.mark.asyncio
async def test_audit_log_append_called_once() -> None:
    svc, _, audit, _ = _make_service()
    await svc.execute(_make_request())
    audit.append.assert_awaited_once()


# 5. observer.emit called with MemoryWrittenEvent
@pytest.mark.asyncio
async def test_observer_emits_memory_written_event() -> None:
    svc, _, _, observer = _make_service()
    await svc.execute(_make_request())
    observer.emit.assert_awaited_once()
    emitted = observer.emit.call_args[0][0]
    assert isinstance(emitted, MemoryWrittenEvent)


# 6. Idempotent write returns idempotent=True, repo.save NOT called
@pytest.mark.asyncio
async def test_idempotent_write_skips_save() -> None:
    from datetime import datetime
    from memory_layer.domain.types import new_memory_id
    existing = MemoryRecord(
        id=new_memory_id(),
        tenant_id=TENANT,
        scope=_SCOPE,
        raw_payload="hello world",
        payload_type=PayloadType.CONVERSATION_TURN,
        sector=MemorySector.EPISODIC,
        lifecycle_state=LifecycleState.ACTIVE,
        pipeline_status=PipelineStatus.PENDING,
        recorded_at=datetime.utcnow(),
        idempotency_key="key-abc",
    )
    svc, repo, _, _ = _make_service(existing_record=existing)
    result = await svc.execute(_make_request(idempotency_key="key-abc"))
    assert result.idempotent is True
    repo.save.assert_not_awaited()


# 7. Explicit sector override is respected
@pytest.mark.asyncio
async def test_explicit_sector_used() -> None:
    svc, repo, *_ = _make_service()
    await svc.execute(_make_request(sector=MemorySector.IDENTITY))
    saved_record: MemoryRecord = repo.save.call_args[0][0]
    assert saved_record.sector == MemorySector.IDENTITY


# 8. CONVERSATION_TURN without sector infers EPISODIC
@pytest.mark.asyncio
async def test_conversation_turn_infers_episodic() -> None:
    svc, repo, *_ = _make_service()
    await svc.execute(_make_request(payload_type=PayloadType.CONVERSATION_TURN))
    saved_record: MemoryRecord = repo.save.call_args[0][0]
    assert saved_record.sector == MemorySector.EPISODIC


# 9. DOCUMENT without sector infers SEMANTIC
@pytest.mark.asyncio
async def test_document_infers_semantic() -> None:
    svc, repo, *_ = _make_service()
    await svc.execute(_make_request(payload_type=PayloadType.DOCUMENT))
    saved_record: MemoryRecord = repo.save.call_args[0][0]
    assert saved_record.sector == MemorySector.SEMANTIC


# 10. extraction=None with extract=True → ENRICHMENT_SKIPPED
@pytest.mark.asyncio
async def test_no_extraction_port_sets_enrichment_skipped() -> None:
    svc, repo, *_ = _make_service(extraction=None)
    result = await svc.execute(_make_request(extract=True))
    assert result.pipeline_status == PipelineStatus.ENRICHMENT_SKIPPED
    saved_record: MemoryRecord = repo.save.call_args[0][0]
    assert saved_record.pipeline_status == PipelineStatus.ENRICHMENT_SKIPPED


# 11. wait_for_enrichment=True causes extraction to be awaited before returning
@pytest.mark.asyncio
async def test_wait_for_enrichment_awaits_extract() -> None:
    extraction = AsyncMock()
    extraction.extract.return_value = ExtractionResult(
        memory_record_id=new_memory_id(), facts=[], entities=[]
    )
    svc, repo, _, _ = _make_service(extraction=extraction)
    await svc.execute(_make_request(extract=True, wait_for_enrichment=True))
    extraction.extract.assert_awaited_once()
    # pipeline_status updated to ENRICHED
    repo.update_pipeline_status.assert_awaited_once()
    _, _, status = repo.update_pipeline_status.call_args[0]
    assert status == PipelineStatus.ENRICHED


# 12. Enrichment failure does NOT raise; pipeline_status → PARTIAL_ENRICHMENT_FAILED
@pytest.mark.asyncio
async def test_enrichment_failure_does_not_raise() -> None:
    extraction = AsyncMock()
    extraction.extract.side_effect = RuntimeError("LLM timeout")
    svc, repo, _, _ = _make_service(extraction=extraction)
    # Should not raise
    await svc.execute(_make_request(extract=True, wait_for_enrichment=True))
    repo.update_pipeline_status.assert_awaited_once()
    _, _, status = repo.update_pipeline_status.call_args[0]
    assert status == PipelineStatus.PARTIAL_ENRICHMENT_FAILED

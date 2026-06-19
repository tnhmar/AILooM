"""Write pipeline use case — WriteMemoryService.

Orchestrates the hot write path:
  idempotency check → durable raw write → audit entry → event emission
  → async enrichment trigger.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from memory_layer.domain.events import (
    EnrichmentFailedEvent,
    MemoryEnrichedEvent,
    MemoryWrittenEvent,
)
from memory_layer.domain.records import (
    AuditEntry,
    MemoryRecord,
    WriteRequest,
    WriteResult,
)
from memory_layer.domain.types import (
    AuditOperation,
    AuditOutcome,
    LifecycleState,
    MemorySector,
    PayloadType,
    PipelineStatus,
    new_audit_id,
    new_memory_id,
)
from memory_layer.ports.outbound import (
    AuditLogPort,
    ExtractionPort,
    MemoryRecordRepositoryPort,
    ObserverPort,
)

log = logging.getLogger(__name__)

_SECTOR_BY_PAYLOAD: dict[PayloadType, MemorySector] = {
    PayloadType.CONVERSATION_TURN: MemorySector.EPISODIC,
    PayloadType.DOCUMENT: MemorySector.SEMANTIC,
    PayloadType.TOOL_OUTPUT: MemorySector.PROCEDURAL,
    PayloadType.EVENT: MemorySector.EPISODIC,
    PayloadType.STRUCTURED: MemorySector.SEMANTIC,
}


def _infer_sector(request: WriteRequest) -> MemorySector:
    if request.sector is not None:
        return request.sector
    return _SECTOR_BY_PAYLOAD.get(request.payload_type, MemorySector.EPISODIC)


class WriteMemoryService:
    """Concrete implementation of WriteMemoryUseCase."""

    def __init__(
        self,
        record_repo: MemoryRecordRepositoryPort,
        audit_log: AuditLogPort,
        observer: ObserverPort,
        extraction: ExtractionPort | None = None,
    ) -> None:
        self._record_repo = record_repo
        self._audit_log = audit_log
        self._observer = observer
        self._extraction = extraction

    async def execute(self, request: WriteRequest) -> WriteResult:
        """Execute the write pipeline and return a WriteResult."""
        from memory_layer.observability.metrics import (
            memory_writes_total,
            track_latency,
            write_latency_seconds,
        )

        with track_latency(write_latency_seconds, {"tenant_id": str(request.tenant_id)}):
            if request.idempotency_key is not None:
                existing = await self._record_repo.get_by_idempotency_key(
                    request.idempotency_key, request.tenant_id
                )
                if existing is not None:
                    memory_writes_total.labels(
                        tenant_id=str(request.tenant_id),
                        sector=str(existing.sector),
                        status="idempotent",
                    ).inc()
                    return WriteResult(
                        memory_id=existing.id,
                        scope=existing.scope,
                        pipeline_status=existing.pipeline_status,
                        accepted_at=existing.recorded_at,
                        idempotent=True,
                    )

            sector = _infer_sector(request)

            if request.extract and self._extraction is None:
                initial_status = PipelineStatus.ENRICHMENT_SKIPPED
            else:
                initial_status = PipelineStatus.PENDING

            memory_id = new_memory_id()
            accepted_at = datetime.utcnow()
            record = MemoryRecord(
                id=memory_id,
                tenant_id=request.tenant_id,
                scope=request.scope,
                raw_payload=request.raw_payload,
                payload_type=request.payload_type,
                sector=sector,
                lifecycle_state=LifecycleState.ACTIVE,
                pipeline_status=initial_status,
                recorded_at=accepted_at,
                idempotency_key=request.idempotency_key,
                metadata=request.metadata,
            )

            await self._record_repo.save(record)

            audit_entry = AuditEntry(
                id=new_audit_id(),
                tenant_id=request.tenant_id,
                scope=request.scope,
                operation=AuditOperation.WRITE,
                memory_id=memory_id,
                outcome=AuditOutcome.SUCCESS,
                timestamp=accepted_at,
            )
            await self._audit_log.append(audit_entry)

            await self._observer.emit(
                MemoryWrittenEvent(
                    tenant_id=request.tenant_id,
                    memory_id=memory_id,
                    scope=request.scope,
                    sector=sector,
                    pipeline_status=initial_status,
                )
            )

            if request.extract and self._extraction is not None:
                if request.wait_for_enrichment:
                    await self._enrich(record)
                else:
                    asyncio.create_task(self._enrich(record))

            memory_writes_total.labels(
                tenant_id=str(request.tenant_id),
                sector=str(sector),
                status="success",
            ).inc()

            return WriteResult(
                memory_id=memory_id,
                scope=request.scope,
                pipeline_status=initial_status,
                accepted_at=accepted_at,
                idempotent=False,
            )

    async def _enrich(self, record: MemoryRecord) -> None:
        assert self._extraction is not None
        try:
            result = await self._extraction.extract(record)
            await self._record_repo.update_pipeline_status(
                record.id, record.tenant_id, PipelineStatus.ENRICHED
            )
            await self._observer.emit(
                MemoryEnrichedEvent(
                    tenant_id=record.tenant_id,
                    memory_id=record.id,
                    scope=record.scope,
                    facts_extracted=len(result.facts),
                    entities_extracted=len(result.entities),
                )
            )
        except Exception as exc:
            log.warning("Enrichment failed for memory_id=%s: %s", record.id, exc, exc_info=True)
            await self._record_repo.update_pipeline_status(
                record.id, record.tenant_id, PipelineStatus.PARTIAL_ENRICHMENT_FAILED
            )
            await self._observer.emit(
                EnrichmentFailedEvent(
                    tenant_id=record.tenant_id,
                    memory_id=record.id,
                    scope=record.scope,
                    error=str(exc),
                )
            )

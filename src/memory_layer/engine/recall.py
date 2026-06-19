"""Recall use case — RecallMemoryService."""

from __future__ import annotations

import logging
from datetime import datetime

from memory_layer.domain.records import RecallItem, RecallRequest, RecallResult, RecallStatus
from memory_layer.domain.types import LifecycleState
from memory_layer.ports.outbound import MemoryRecordRepositoryPort, VectorIndexPort

log = logging.getLogger(__name__)


class RecallMemoryService:
    """Concrete implementation of RecallMemoryUseCase."""

    def __init__(
        self,
        record_repo: MemoryRecordRepositoryPort,
        vector_store: VectorIndexPort | None = None,
    ) -> None:
        self._record_repo = record_repo
        self._vector_store = vector_store

    async def execute(self, request: RecallRequest) -> RecallResult:
        """Execute the recall pipeline and return a RecallResult."""
        from memory_layer.observability.metrics import (
            memory_recalls_total,
            recall_latency_seconds,
            track_latency,
        )

        mode = str(request.mode) if request.mode else "HYBRID"
        with track_latency(recall_latency_seconds, {"tenant_id": str(request.tenant_id)}):
            records = await self._record_repo.search(
                tenant_id=request.tenant_id,
                query=request.query,
                mode=request.mode,
                sectors=request.sectors,
                lifecycle_states=[LifecycleState.ACTIVE],
                temporal_filter=None,
                k=request.max_items,
                scope=request.scope,
            )

        memory_recalls_total.labels(
            tenant_id=str(request.tenant_id),
            mode=mode,
            status="success",
        ).inc()

        if not records:
            return RecallResult(
                status=RecallStatus.NO_MATCH,
                items=[],
                recalled_at=datetime.utcnow(),
                no_match_reason="No active memories found for this query.",
            )

        return RecallResult(
            status=RecallStatus.MATCH,
            items=[
                RecallItem(
                    memory_id=r.id,
                    content=r.raw_payload,
                    sector=r.sector,
                    lifecycle_state=r.lifecycle_state,
                    pipeline_status=r.pipeline_status,
                    effective_from=r.recorded_at,
                )
                for r in records
            ],
            total_tokens_estimate=sum(len(r.raw_payload) // 4 for r in records),
            recall_strategy=mode,
            recalled_at=datetime.utcnow(),
        )

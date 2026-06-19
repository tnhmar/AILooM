"""Search use case — SearchMemoryService."""

from __future__ import annotations

import logging
from datetime import datetime

from memory_layer.domain.records import SearchRequest
from memory_layer.ports.inbound import SearchResult, SearchResultItem
from memory_layer.ports.outbound import MemoryRecordRepositoryPort, VectorStorePort

log = logging.getLogger(__name__)


class SearchMemoryService:
    """Concrete implementation of SearchMemoryUseCase."""

    def __init__(
        self,
        record_repo: MemoryRecordRepositoryPort,
        vector_store: VectorStorePort | None = None,
    ) -> None:
        self._record_repo = record_repo
        self._vector_store = vector_store

    async def execute(self, request: SearchRequest) -> SearchResult:
        """Execute the search pipeline and return a SearchResult."""
        from memory_layer.observability.metrics import (
            memory_searches_total,
            search_latency_seconds,
            track_latency,
        )

        mode = str(request.mode) if request.mode else "HYBRID"
        with track_latency(search_latency_seconds, {"tenant_id": str(request.tenant_id), "mode": mode}):
            items = await self._record_repo.search(
                tenant_id=request.tenant_id,
                query=request.query,
                mode=request.mode,
                sectors=request.sectors,
                lifecycle_states=request.lifecycle_states,
                temporal_filter=request.temporal_filter,
                k=request.k,
                scope=request.scope,
            )

        memory_searches_total.labels(
            tenant_id=str(request.tenant_id),
            mode=mode,
            status="success",
        ).inc()

        return SearchResult(
            items=[
                SearchResultItem(
                    memory_id=item.id,
                    content=item.raw_payload,
                    sector=item.sector,
                    score=getattr(item, "score", 1.0),
                    pipeline_status=item.pipeline_status,
                    lifecycle_state=item.lifecycle_state,
                )
                for item in items
            ],
            total=len(items),
            searched_at=datetime.utcnow(),
        )

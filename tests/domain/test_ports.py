"""Tests for inbound and outbound port protocol definitions."""

from __future__ import annotations

from typing import Any

from memory_layer.domain.types import (
    LifecycleState,
    MemoryId,
    MemorySector,
    PipelineStatus,
    TenantId,
)
from memory_layer.ports.inbound import (
    SearchResult,
    SearchResultItem,
    WriteMemoryUseCase,
)
from memory_layer.ports.outbound import (
    EmbeddingPort,
    ExtractionResult,
    FullTextDocument,
    MemoryRecordRepositoryPort,
    ObserverPort,
    VectorDocument,
    VectorIndexPort,
)

TID = TenantId("t-test")


# ---------------------------------------------------------------------------
# Minimal stub helpers
# ---------------------------------------------------------------------------


class _MinimalWrite:
    async def execute(self, request: Any) -> Any: ...


class _MinimalRepo:
    async def save(self, record: Any) -> None: ...
    async def get_by_id(self, memory_id: Any, tenant_id: Any) -> Any: ...
    async def update_lifecycle(
        self, memory_id: Any, tenant_id: Any, state: Any, actor: Any
    ) -> None: ...
    async def update_pipeline_status(
        self, memory_id: Any, tenant_id: Any, status: Any
    ) -> None: ...
    async def list_by_scope(
        self, scope: Any, lifecycle_states: Any, limit: int = 100
    ) -> Any: ...
    async def get_by_idempotency_key(self, key: Any, tenant_id: Any) -> Any: ...
    async def search(
        self,
        tenant_id: Any,
        query: Any,
        mode: Any,
        sectors: Any,
        lifecycle_states: Any,
        temporal_filter: Any,
        k: Any,
        scope: Any,
    ) -> Any: ...
    async def list_active_older_than(
        self, tenant_id: Any, cutoff: Any
    ) -> Any: ...
    async def update_lifecycle_state(
        self, record_id: Any, tenant_id: Any, new_state: Any
    ) -> None: ...
    async def list_by_lifecycle(
        self, tenant_id: Any, lifecycle_state: Any
    ) -> Any: ...


class _MinimalVectorIndex:
    async def upsert(self, doc: Any) -> None: ...
    async def search(
        self, query_embedding: Any, tenant_id: Any, k: Any, filters: Any
    ) -> Any: ...
    async def delete(self, memory_id: Any, tenant_id: Any) -> None: ...


class _MinimalObserver:
    async def emit(self, event: Any) -> None: ...


class _MinimalEmbedding:
    model_id: str = "text-embedding-3-small"
    dimensions: int = 1536

    async def embed(self, texts: Any) -> Any: ...


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInboundProtocols:
    def test_minimal_write_use_case_satisfies_protocol(self) -> None:
        assert isinstance(_MinimalWrite(), WriteMemoryUseCase)

    def test_search_result_items_defaults_to_empty_list(self) -> None:
        r1 = SearchResult()
        r2 = SearchResult()
        assert r1.items == []
        assert r1.items is not r2.items

    def test_search_result_item_signals_not_shared(self) -> None:
        i1 = SearchResultItem(
            memory_id=MemoryId("m1"),
            content="x",
            sector=MemorySector.EPISODIC,
            score=0.9,
            pipeline_status=PipelineStatus.PENDING,
            lifecycle_state=LifecycleState.ACTIVE,
        )
        i2 = SearchResultItem(
            memory_id=MemoryId("m2"),
            content="y",
            sector=MemorySector.SEMANTIC,
            score=0.8,
            pipeline_status=PipelineStatus.ENRICHED,
            lifecycle_state=LifecycleState.ACTIVE,
        )
        assert i1.signals is not i2.signals


class TestOutboundProtocols:
    def test_minimal_repo_satisfies_protocol(self) -> None:
        assert isinstance(_MinimalRepo(), MemoryRecordRepositoryPort)

    def test_minimal_vector_index_satisfies_protocol(self) -> None:
        assert isinstance(_MinimalVectorIndex(), VectorIndexPort)

    def test_minimal_observer_satisfies_protocol(self) -> None:
        assert isinstance(_MinimalObserver(), ObserverPort)

    def test_minimal_embedding_satisfies_protocol(self) -> None:
        assert isinstance(_MinimalEmbedding(), EmbeddingPort)

    def test_vector_document_requires_embedding_model_fields(self) -> None:
        doc = VectorDocument(
            memory_id=MemoryId("m1"),
            tenant_id=TID,
            embedding=[0.1, 0.2],
            embedding_model_id="text-embedding-3-small",
            embedding_dimensions=1536,
            content="hello",
            sector=MemorySector.EPISODIC,
            lifecycle_state=LifecycleState.ACTIVE,
        )
        assert doc.embedding_model_id == "text-embedding-3-small"
        assert doc.embedding_dimensions == 1536

    def test_extraction_result_facts_not_shared(self) -> None:
        r1 = ExtractionResult(memory_record_id=MemoryId("m1"))
        r2 = ExtractionResult(memory_record_id=MemoryId("m2"))
        assert r1.facts is not r2.facts

    def test_full_text_document_has_sector(self) -> None:
        doc = FullTextDocument(
            memory_id=MemoryId("m1"),
            tenant_id=TID,
            content="hello world",
            sector=MemorySector.SEMANTIC,
        )
        assert doc.sector == MemorySector.SEMANTIC

    def test_vector_document_has_sector_and_lifecycle_state(self) -> None:
        doc = VectorDocument(
            memory_id=MemoryId("m1"),
            tenant_id=TID,
            embedding=[0.0],
            embedding_model_id="m",
            embedding_dimensions=1,
            content="x",
            sector=MemorySector.PROCEDURAL,
            lifecycle_state=LifecycleState.ACTIVE,
        )
        assert doc.sector == MemorySector.PROCEDURAL
        assert doc.lifecycle_state == LifecycleState.ACTIVE

"""Pydantic v2 request/response schemas for the memory-layer HTTP API.

All schemas are intentionally decoupled from domain dataclasses.
Request models forbid extra fields; enums are serialised as strings.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from memory_layer.domain.records import RecallStatus, SearchMode
from memory_layer.domain.types import (
    LifecycleState,
    MemorySector,
    PayloadType,
    PipelineStatus,
    PrincipalType,
)


# ---------------------------------------------------------------------------
# Shared sub-models
# ---------------------------------------------------------------------------


class ScopeModel(BaseModel):
    """API representation of an identity scope."""

    model_config = ConfigDict(extra="forbid")

    principal_id: str
    principal_type: PrincipalType = PrincipalType.USER
    workspace_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None


class TemporalFilterModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: datetime | None = None
    from_dt: datetime | None = None
    until_dt: datetime | None = None


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


class WriteMemoryRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: ScopeModel
    raw_payload: str
    payload_type: PayloadType
    sector: MemorySector | None = None
    idempotency_key: str | None = None
    extract: bool = True
    wait_for_enrichment: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class WriteMemoryResponseModel(BaseModel):
    memory_id: str
    pipeline_status: PipelineStatus
    accepted_at: datetime
    idempotent: bool = False


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class SearchMemoryRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: ScopeModel
    query: str
    mode: SearchMode = SearchMode.HYBRID
    sectors: list[MemorySector] | None = None
    lifecycle_states: list[LifecycleState] = Field(
        default_factory=lambda: [LifecycleState.ACTIVE]
    )
    temporal_filter: TemporalFilterModel | None = None
    k: int = 10


class SearchResultItemModel(BaseModel):
    memory_id: str
    content: str
    sector: MemorySector
    score: float
    pipeline_status: PipelineStatus
    lifecycle_state: LifecycleState
    signals: dict[str, Any] = Field(default_factory=dict)
    effective_from: datetime | None = None


class SearchMemoryResponseModel(BaseModel):
    items: list[SearchResultItemModel] = Field(default_factory=list)
    total: int = 0
    searched_at: datetime


# ---------------------------------------------------------------------------
# Recall
# ---------------------------------------------------------------------------


class RecallMemoryRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: ScopeModel
    query: str
    max_tokens: int | None = 4000
    max_items: int = 10
    sectors: list[MemorySector] | None = None
    include_facts: bool = True
    include_verbatim: bool = True
    mode: SearchMode = SearchMode.HYBRID


class RecallItemModel(BaseModel):
    memory_id: str
    content: str
    sector: MemorySector
    lifecycle_state: LifecycleState
    pipeline_status: PipelineStatus
    effective_from: datetime | None = None
    signals: dict[str, Any] = Field(default_factory=dict)
    explanation: str = ""
    trace_id: str | None = None


class RecallMemoryResponseModel(BaseModel):
    status: RecallStatus
    no_match_reason: str | None = None
    items: list[RecallItemModel] = Field(default_factory=list)
    total_tokens_estimate: int = 0
    recall_strategy: str = ""
    recalled_at: datetime


# ---------------------------------------------------------------------------
# Explain recall (trace)
# ---------------------------------------------------------------------------


class TraceStepModel(BaseModel):
    memory_id: str
    rank: int
    score: float
    signals: dict[str, Any] = Field(default_factory=dict)
    explanation: str = ""
    record_available: bool = True


class ExplainRecallResponseModel(BaseModel):
    trace_id: str
    tenant_id: str
    query: str
    mode: str
    steps: list[TraceStepModel] = Field(default_factory=list)
    created_at: datetime


# ---------------------------------------------------------------------------
# Session end
# ---------------------------------------------------------------------------


class SessionEndRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: ScopeModel


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


class DecayResponseModel(BaseModel):
    tenant_id: str
    transitions: int


class ConsolidateResponseModel(BaseModel):
    tenant_id: str
    records_processed: int


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class ErrorResponseModel(BaseModel):
    error_code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None

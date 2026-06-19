"""memory-layer FastAPI application.

This module is intentionally thin: request validation, tenant resolution,
use-case dispatch, response mapping, and error translation only.
No business logic lives here.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException, status

from memory_layer.api.errors import register_exception_handlers
from memory_layer.api.schemas import (
    ConsolidateResponseModel,
    DecayResponseModel,
    ExplainRecallResponseModel,
    RecallMemoryRequestModel,
    RecallMemoryResponseModel,
    SearchMemoryRequestModel,
    SearchMemoryResponseModel,
    SessionEndRequestModel,
    TraceStepModel,
    WriteMemoryRequestModel,
    WriteMemoryResponseModel,
)
from memory_layer.domain.records import (
    RecallRequest,
    Scope,
    SearchRequest,
    TemporalFilter,
    WriteRequest,
)
from memory_layer.domain.types import (
    MemoryId,
    PrincipalId,
    PrincipalType,
    RunId,
    SessionId,
    TenantId,
    TraceId,
    WorkspaceId,
)
from memory_layer.ports.inbound import (
    ConsolidateUseCase,
    DecayUseCase,
    DeleteMemoryUseCase,
    ExplainRecallUseCase,
    GetMemoryUseCase,
    NotifySessionEndedUseCase,
    RecallMemoryUseCase,
    SearchMemoryUseCase,
    WriteMemoryUseCase,
)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="memory-layer", version="0.1.0")
register_exception_handlers(app)


# ---------------------------------------------------------------------------
# Dependency stubs
# (Leave as NotImplementedError; tests override via app.dependency_overrides)
# ---------------------------------------------------------------------------


def get_write_use_case() -> WriteMemoryUseCase:
    raise NotImplementedError("WriteMemoryUseCase provider not configured.")


def get_search_use_case() -> SearchMemoryUseCase:
    raise NotImplementedError("SearchMemoryUseCase provider not configured.")


def get_recall_use_case() -> RecallMemoryUseCase:
    raise NotImplementedError("RecallMemoryUseCase provider not configured.")


def get_get_memory_use_case() -> GetMemoryUseCase:
    raise NotImplementedError("GetMemoryUseCase provider not configured.")


def get_delete_use_case() -> DeleteMemoryUseCase:
    raise NotImplementedError("DeleteMemoryUseCase provider not configured.")


def get_explain_use_case() -> ExplainRecallUseCase:
    raise NotImplementedError("ExplainRecallUseCase provider not configured.")


def get_notify_session_ended_use_case() -> NotifySessionEndedUseCase:
    raise NotImplementedError("NotifySessionEndedUseCase provider not configured.")


def get_decay_use_case() -> DecayUseCase:
    raise NotImplementedError("DecayUseCase provider not configured.")


def get_consolidate_use_case() -> ConsolidateUseCase:
    raise NotImplementedError("ConsolidateUseCase provider not configured.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_tenant(x_tenant_id: str | None) -> TenantId:
    if not x_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="X-Tenant-Id header is required.",
        )
    return TenantId(x_tenant_id)


def _scope_from_model(tenant_id: TenantId, m: object) -> Scope:
    """Build a domain Scope from a ScopeModel-bearing request model attribute."""
    from memory_layer.api.schemas import ScopeModel  # local to avoid circular

    assert isinstance(m, ScopeModel)
    return Scope(
        tenant_id=tenant_id,
        principal_id=PrincipalId(m.principal_id),
        principal_type=PrincipalType(m.principal_type),
        workspace_id=WorkspaceId(m.workspace_id) if m.workspace_id else None,
        session_id=SessionId(m.session_id) if m.session_id else None,
        run_id=RunId(m.run_id) if m.run_id else None,
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/healthz", tags=["ops"])
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# 1. Write memory
# ---------------------------------------------------------------------------


@app.post("/v1/memories:write", response_model=WriteMemoryResponseModel, tags=["memories"])
async def write_memory(
    body: WriteMemoryRequestModel,
    x_tenant_id: str | None = Header(default=None),
    use_case: WriteMemoryUseCase = Depends(get_write_use_case),
) -> WriteMemoryResponseModel:
    tenant_id = _resolve_tenant(x_tenant_id)
    scope = _scope_from_model(tenant_id, body.scope)
    request = WriteRequest(
        tenant_id=tenant_id,
        scope=scope,
        raw_payload=body.raw_payload,
        payload_type=body.payload_type,
        sector=body.sector,
        idempotency_key=body.idempotency_key,
        extract=body.extract,
        wait_for_enrichment=body.wait_for_enrichment,
        metadata=body.metadata,
    )
    result = await use_case.execute(request)
    return WriteMemoryResponseModel(
        memory_id=str(result.memory_id),
        pipeline_status=result.pipeline_status,
        accepted_at=result.accepted_at,
        idempotent=result.idempotent,
    )


# ---------------------------------------------------------------------------
# 2. Search memories
# ---------------------------------------------------------------------------


@app.post(
    "/v1/memories:search", response_model=SearchMemoryResponseModel, tags=["memories"]
)
async def search_memories(
    body: SearchMemoryRequestModel,
    x_tenant_id: str | None = Header(default=None),
    use_case: SearchMemoryUseCase = Depends(get_search_use_case),
) -> SearchMemoryResponseModel:
    tenant_id = _resolve_tenant(x_tenant_id)
    scope = _scope_from_model(tenant_id, body.scope)
    tf = body.temporal_filter
    request = SearchRequest(
        tenant_id=tenant_id,
        scope=scope,
        query=body.query,
        mode=body.mode,
        sectors=body.sectors,
        lifecycle_states=body.lifecycle_states,
        temporal_filter=(
            TemporalFilter(
                as_of=tf.as_of,
                from_dt=tf.from_dt,
                until_dt=tf.until_dt,
            )
            if tf
            else None
        ),
        k=body.k,
    )
    result = await use_case.execute(request)
    from memory_layer.api.schemas import SearchResultItemModel  # local import ok

    return SearchMemoryResponseModel(
        items=[
            SearchResultItemModel(
                memory_id=str(i.memory_id),
                content=i.content,
                sector=i.sector,
                score=i.score,
                pipeline_status=i.pipeline_status,
                lifecycle_state=i.lifecycle_state,
                signals=i.signals,
                effective_from=i.effective_from,
            )
            for i in result.items
        ],
        total=result.total,
        searched_at=result.searched_at,
    )


# ---------------------------------------------------------------------------
# 3. Recall memories
# ---------------------------------------------------------------------------


@app.post(
    "/v1/memories:recall", response_model=RecallMemoryResponseModel, tags=["memories"]
)
async def recall_memories(
    body: RecallMemoryRequestModel,
    x_tenant_id: str | None = Header(default=None),
    use_case: RecallMemoryUseCase = Depends(get_recall_use_case),
) -> RecallMemoryResponseModel:
    tenant_id = _resolve_tenant(x_tenant_id)
    scope = _scope_from_model(tenant_id, body.scope)
    request = RecallRequest(
        tenant_id=tenant_id,
        scope=scope,
        query=body.query,
        max_tokens=body.max_tokens,
        max_items=body.max_items,
        sectors=body.sectors,
        include_facts=body.include_facts,
        include_verbatim=body.include_verbatim,
        mode=body.mode,
    )
    result = await use_case.execute(request)
    from memory_layer.api.schemas import RecallItemModel  # local import ok

    return RecallMemoryResponseModel(
        status=result.status,
        no_match_reason=result.no_match_reason,
        items=[
            RecallItemModel(
                memory_id=str(i.memory_id),
                content=i.content,
                sector=i.sector,
                lifecycle_state=i.lifecycle_state,
                pipeline_status=i.pipeline_status,
                effective_from=i.effective_from,
                signals=i.signals,
                explanation=i.explanation,
                trace_id=str(i.trace_id) if i.trace_id else None,
            )
            for i in result.items
        ],
        total_tokens_estimate=result.total_tokens_estimate,
        recall_strategy=result.recall_strategy,
        recalled_at=result.recalled_at,
    )


# ---------------------------------------------------------------------------
# 4. Get memory by ID
# ---------------------------------------------------------------------------


@app.get("/v1/memories/{memory_id}", tags=["memories"])
async def get_memory(
    memory_id: str,
    x_tenant_id: str | None = Header(default=None),
    use_case: GetMemoryUseCase = Depends(get_get_memory_use_case),
) -> dict[str, object]:
    tenant_id = _resolve_tenant(x_tenant_id)
    record = await use_case.execute(
        memory_id=MemoryId(memory_id), tenant_id=tenant_id
    )
    return {
        "memory_id": str(record.id),
        "sector": record.sector,
        "lifecycle_state": record.lifecycle_state,
        "pipeline_status": record.pipeline_status,
        "recorded_at": record.recorded_at.isoformat(),
        "raw_payload": record.raw_payload,
    }


# ---------------------------------------------------------------------------
# 5. Delete memory
# ---------------------------------------------------------------------------


@app.delete(
    "/v1/memories/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["memories"],
)
async def delete_memory(
    memory_id: str,
    actor: str = "api",
    x_tenant_id: str | None = Header(default=None),
    use_case: DeleteMemoryUseCase = Depends(get_delete_use_case),
) -> None:
    tenant_id = _resolve_tenant(x_tenant_id)
    await use_case.execute(
        memory_id=MemoryId(memory_id),
        tenant_id=tenant_id,
        actor=actor,
    )


# ---------------------------------------------------------------------------
# 6. Explain recall trace
# ---------------------------------------------------------------------------


@app.get("/v1/traces/{trace_id}", response_model=ExplainRecallResponseModel, tags=["traces"])
async def explain_recall(
    trace_id: str,
    x_tenant_id: str | None = Header(default=None),
    use_case: ExplainRecallUseCase = Depends(get_explain_use_case),
) -> ExplainRecallResponseModel:
    tenant_id = _resolve_tenant(x_tenant_id)
    # ExplainRecallUseCase returns MemoryTrace (provenance trace)
    trace = await use_case.execute(
        trace_id=TraceId(trace_id), tenant_id=tenant_id
    )
    return ExplainRecallResponseModel(
        trace_id=str(trace.trace_id),
        tenant_id=str(trace.scope.tenant_id),
        query=str(trace.trace_id),  # MemoryTrace has no query field; use trace_id as ref
        mode="provenance",
        steps=[
            TraceStepModel(
                memory_id=str(trace.memory_id),
                rank=0,
                score=1.0,
            )
        ],
        created_at=trace.constructed_at,
    )


# ---------------------------------------------------------------------------
# 7. Session end
# ---------------------------------------------------------------------------


@app.post(
    "/v1/sessions/{session_id}:end",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["sessions"],
)
async def session_end(
    session_id: str,
    body: SessionEndRequestModel,
    x_tenant_id: str | None = Header(default=None),
    use_case: NotifySessionEndedUseCase = Depends(get_notify_session_ended_use_case),
) -> dict[str, str]:
    tenant_id = _resolve_tenant(x_tenant_id)
    scope = _scope_from_model(tenant_id, body.scope)
    await use_case.execute(
        tenant_id=tenant_id,
        session_id=SessionId(session_id),
        scope=scope,
    )
    return {"status": "accepted"}


# ---------------------------------------------------------------------------
# 8. Admin: decay
# ---------------------------------------------------------------------------


@app.post(
    "/v1/admin/tenants/{tenant_id}:decay",
    response_model=DecayResponseModel,
    tags=["admin"],
)
async def admin_decay(
    tenant_id: str,
    use_case: DecayUseCase = Depends(get_decay_use_case),
) -> DecayResponseModel:
    count = await use_case.execute(tenant_id=TenantId(tenant_id))
    return DecayResponseModel(tenant_id=tenant_id, transitions=count)


# ---------------------------------------------------------------------------
# 9. Admin: consolidate
# ---------------------------------------------------------------------------


@app.post(
    "/v1/admin/tenants/{tenant_id}:consolidate",
    response_model=ConsolidateResponseModel,
    tags=["admin"],
)
async def admin_consolidate(
    tenant_id: str,
    use_case: ConsolidateUseCase = Depends(get_consolidate_use_case),
) -> ConsolidateResponseModel:
    count = await use_case.execute(tenant_id=TenantId(tenant_id))
    return ConsolidateResponseModel(tenant_id=tenant_id, records_processed=count)

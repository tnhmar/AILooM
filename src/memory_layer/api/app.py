"""memory-layer FastAPI application.

This module is intentionally thin: request validation, tenant resolution,
use-case dispatch, response mapping, and error translation only.
No business logic lives here.
"""

from __future__ import annotations

import os
from dataclasses import asdict

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from starlette.requests import Request

from memory_layer.api.errors import register_exception_handlers
from memory_layer.api.health import HealthChecker, HealthReport
from memory_layer.api.middleware import TenantMiddleware, get_request_tenant_id
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
app.add_middleware(TenantMiddleware)
register_exception_handlers(app)

# Module-level HealthChecker singleton — callers may register probes at boot.
_health_checker = HealthChecker(version="0.1.0")


def get_health_checker() -> HealthChecker:
    """Return the application-level HealthChecker."""
    return _health_checker


def create_app() -> FastAPI:
    """Application factory for ``uvicorn --factory`` and test bootstrapping.

    Returns the singleton :data:`app` instance.  Uvicorn calls this when
    invoked as ``uvicorn memory_layer.api.app:create_app --factory``.
    """
    return app


# ---------------------------------------------------------------------------
# /metrics endpoint (registered only when metrics_enabled)
# ---------------------------------------------------------------------------

try:
    from memory_layer.config.loader import get_settings
    from memory_layer.observability.metrics import metrics_response

    _obs = get_settings().observability
    if _obs.metrics_enabled:
        @app.get("/metrics", tags=["ops"], include_in_schema=False)
        async def _metrics_endpoint() -> object:
            return metrics_response()
except Exception:
    pass


# ---------------------------------------------------------------------------
# Health / readiness
# ---------------------------------------------------------------------------


@app.get("/healthz", tags=["ops"])
async def healthz(
    checker: HealthChecker = Depends(get_health_checker),
) -> dict:
    """Deep liveness probe — always returns 200 with a HealthReport JSON body."""
    report: HealthReport = await checker.check()
    return asdict(report)


@app.get("/readyz", tags=["ops"])
async def readyz(
    response: Response,
    checker: HealthChecker = Depends(get_health_checker),
) -> dict:
    """Readiness probe — 200 when all components ok, 503 otherwise."""
    report: HealthReport = await checker.check()
    if report.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return asdict(report)


# ---------------------------------------------------------------------------
# Dependency stubs
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


def get_schema_migrator():
    """Return the configured SchemaMigrator; override at startup."""
    raise NotImplementedError("SchemaMigrator provider not configured.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scope_from_model(tenant_id: TenantId, m: object) -> Scope:
    from memory_layer.api.schemas import ScopeModel

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
# Admin key guard
# ---------------------------------------------------------------------------


def _get_admin_key_header(x_admin_key: str | None = Header(default=None)) -> str | None:
    return x_admin_key


def _require_admin_key(x_admin_key: str | None = Depends(_get_admin_key_header)) -> None:
    """Raise 401 if MEMORY_LAYER_ADMIN_KEY env var is set and header doesn't match."""
    expected = os.environ.get("MEMORY_LAYER_ADMIN_KEY", "")
    if expected and x_admin_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing MEMORY_LAYER_ADMIN_KEY header.",
        )


# ---------------------------------------------------------------------------
# 1. Write memory
# ---------------------------------------------------------------------------


@app.post("/v1/memories:write", response_model=WriteMemoryResponseModel, tags=["memories"])
async def write_memory(
    body: WriteMemoryRequestModel,
    tenant_id: str = Depends(get_request_tenant_id),
    use_case: WriteMemoryUseCase = Depends(get_write_use_case),
) -> WriteMemoryResponseModel:
    t_id = TenantId(tenant_id)
    scope = _scope_from_model(t_id, body.scope)
    request = WriteRequest(
        tenant_id=t_id,
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
    tenant_id: str = Depends(get_request_tenant_id),
    use_case: SearchMemoryUseCase = Depends(get_search_use_case),
) -> SearchMemoryResponseModel:
    t_id = TenantId(tenant_id)
    scope = _scope_from_model(t_id, body.scope)
    tf = body.temporal_filter
    request = SearchRequest(
        tenant_id=t_id,
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
    from memory_layer.api.schemas import SearchResultItemModel

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
    tenant_id: str = Depends(get_request_tenant_id),
    use_case: RecallMemoryUseCase = Depends(get_recall_use_case),
) -> RecallMemoryResponseModel:
    t_id = TenantId(tenant_id)
    scope = _scope_from_model(t_id, body.scope)
    request = RecallRequest(
        tenant_id=t_id,
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
    from memory_layer.api.schemas import RecallItemModel

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
    tenant_id: str = Depends(get_request_tenant_id),
    use_case: GetMemoryUseCase = Depends(get_get_memory_use_case),
) -> dict[str, object]:
    record = await use_case.execute(
        memory_id=MemoryId(memory_id), tenant_id=TenantId(tenant_id)
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
    tenant_id: str = Depends(get_request_tenant_id),
    use_case: DeleteMemoryUseCase = Depends(get_delete_use_case),
) -> None:
    await use_case.execute(
        memory_id=MemoryId(memory_id),
        tenant_id=TenantId(tenant_id),
        actor=actor,
    )


# ---------------------------------------------------------------------------
# 6. Explain recall trace
# ---------------------------------------------------------------------------


@app.get("/v1/traces/{trace_id}", response_model=ExplainRecallResponseModel, tags=["traces"])
async def explain_recall(
    trace_id: str,
    tenant_id: str = Depends(get_request_tenant_id),
    use_case: ExplainRecallUseCase = Depends(get_explain_use_case),
) -> ExplainRecallResponseModel:
    trace = await use_case.execute(
        trace_id=TraceId(trace_id), tenant_id=TenantId(tenant_id)
    )
    return ExplainRecallResponseModel(
        trace_id=str(trace.trace_id),
        tenant_id=str(trace.scope.tenant_id),
        query=str(trace.trace_id),
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
    tenant_id: str = Depends(get_request_tenant_id),
    use_case: NotifySessionEndedUseCase = Depends(get_notify_session_ended_use_case),
) -> dict[str, str]:
    t_id = TenantId(tenant_id)
    scope = _scope_from_model(t_id, body.scope)
    await use_case.execute(
        tenant_id=t_id,
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


# ---------------------------------------------------------------------------
# 10. Admin: run migrations
# ---------------------------------------------------------------------------


@app.post(
    "/v1/admin/migrations:run",
    tags=["admin"],
    dependencies=[Depends(_require_admin_key)],
)
async def admin_run_migrations(
    migrator=Depends(get_schema_migrator),
) -> dict:
    """Apply all pending schema migrations.

    Gated by ``MEMORY_LAYER_ADMIN_KEY`` header when the env var is set.
    Returns a :class:`~memory_layer.adapters.postgres.migrator.MigrationResult`
    serialised as JSON.
    """
    from memory_layer.adapters.postgres.migrator import MigrationResult
    from dataclasses import asdict as _asdict

    result: MigrationResult = await migrator.run()
    return _asdict(result)

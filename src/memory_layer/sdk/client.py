"""MemoryLayerClient — async httpx-based SDK client for memory-layer."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from memory_layer.sdk.errors import MemoryLayerHTTPError, MemoryLayerTransportError
from memory_layer.sdk.models import (
    SDKMemoryTrace,
    SDKRecallItem,
    SDKRecallRequest,
    SDKRecallResponse,
    SDKSearchRequest,
    SDKSearchResponse,
    SDKSearchResultItem,
    SDKTraceStep,
    SDKWriteRequest,
    SDKWriteResponse,
)


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO-8601 datetime string, returning None if value is None."""
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _require_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class MemoryLayerClient:
    """Async HTTP client for the memory-layer REST API.

    Usage::

        async with MemoryLayerClient(base_url, tenant_id) as client:
            result = await client.write(request)

    All methods raise:
    - :exc:`MemoryLayerHTTPError` on non-2xx HTTP responses.
    - :exc:`MemoryLayerTransportError` on network / timeout failures.
    """

    def __init__(
        self,
        base_url: str,
        tenant_id: str,
        timeout: float = 10.0,
        **httpx_kwargs: Any,
    ) -> None:
        self._tenant_id = tenant_id
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"X-Tenant-Id": tenant_id},
            **httpx_kwargs,
        )

    async def aclose(self) -> None:
        """Close the underlying httpx.AsyncClient."""
        await self._client.aclose()

    async def __aenter__(self) -> MemoryLayerClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Execute *method* request, raising SDK errors on failure."""
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise MemoryLayerTransportError(
                f"Transport error calling {method} {path}: {exc}"
            ) from exc
        if not response.is_success:
            self._raise_http_error(response)
        return response

    @staticmethod
    def _raise_http_error(response: httpx.Response) -> None:
        try:
            body: dict[str, Any] = response.json()
        except Exception:  # noqa: BLE001
            body = {}
        raise MemoryLayerHTTPError(
            status_code=response.status_code,
            error_code=body.get("error_code", "UNKNOWN"),
            message=body.get("message", response.text),
            details=body.get("details"),
        )

    def _scope_payload(self, req: SDKWriteRequest | SDKSearchRequest | SDKRecallRequest) -> dict[str, Any]:
        return {
            k: v
            for k, v in {
                "principal_id": req.principal_id,
                "principal_type": req.principal_type,
                "workspace_id": req.workspace_id,
                "session_id": req.session_id,
                "run_id": req.run_id,
            }.items()
            if v is not None
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def healthz(self) -> dict[str, Any]:
        """Check server health. Returns ``{"status": "ok"}`` when healthy."""
        resp = await self._request("GET", "/healthz")
        return dict(resp.json())

    async def write(self, request: SDKWriteRequest) -> SDKWriteResponse:
        """Persist a new memory record."""
        payload: dict[str, Any] = {
            "scope": self._scope_payload(request),
            "raw_payload": request.raw_payload,
            "payload_type": request.payload_type,
            "extract": request.extract,
            "wait_for_enrichment": request.wait_for_enrichment,
            "metadata": request.metadata,
        }
        if request.sector is not None:
            payload["sector"] = request.sector
        if request.idempotency_key is not None:
            payload["idempotency_key"] = request.idempotency_key
        resp = await self._request("POST", "/v1/memories:write", json=payload)
        data: dict[str, Any] = resp.json()
        return SDKWriteResponse(
            memory_id=data["memory_id"],
            pipeline_status=data["pipeline_status"],
            accepted_at=_require_dt(data["accepted_at"]),
            idempotent=data.get("idempotent", False),
        )

    async def search(self, request: SDKSearchRequest) -> SDKSearchResponse:
        """Search the memory index."""
        payload: dict[str, Any] = {
            "scope": self._scope_payload(request),
            "query": request.query,
            "mode": request.mode,
            "lifecycle_states": request.lifecycle_states,
            "k": request.k,
        }
        if request.sectors is not None:
            payload["sectors"] = request.sectors
        resp = await self._request("POST", "/v1/memories:search", json=payload)
        data: dict[str, Any] = resp.json()
        items = [
            SDKSearchResultItem(
                memory_id=i["memory_id"],
                content=i["content"],
                sector=i["sector"],
                score=i["score"],
                pipeline_status=i["pipeline_status"],
                lifecycle_state=i["lifecycle_state"],
                signals=i.get("signals", {}),
                effective_from=_parse_dt(i.get("effective_from")),
            )
            for i in data.get("items", [])
        ]
        return SDKSearchResponse(
            items=items,
            total=data["total"],
            searched_at=_require_dt(data["searched_at"]),
        )

    async def recall(self, request: SDKRecallRequest) -> SDKRecallResponse:
        """Recall memory items for agent context injection."""
        payload: dict[str, Any] = {
            "scope": self._scope_payload(request),
            "query": request.query,
            "max_tokens": request.max_tokens,
            "max_items": request.max_items,
            "include_facts": request.include_facts,
            "include_verbatim": request.include_verbatim,
            "mode": request.mode,
        }
        if request.sectors is not None:
            payload["sectors"] = request.sectors
        resp = await self._request("POST", "/v1/memories:recall", json=payload)
        data: dict[str, Any] = resp.json()
        items = [
            SDKRecallItem(
                memory_id=i["memory_id"],
                content=i["content"],
                sector=i["sector"],
                lifecycle_state=i["lifecycle_state"],
                pipeline_status=i["pipeline_status"],
                effective_from=_parse_dt(i.get("effective_from")),
                signals=i.get("signals", {}),
                explanation=i.get("explanation", ""),
                trace_id=i.get("trace_id"),
            )
            for i in data.get("items", [])
        ]
        return SDKRecallResponse(
            status=data["status"],
            items=items,
            total_tokens_estimate=data.get("total_tokens_estimate", 0),
            recall_strategy=data.get("recall_strategy", ""),
            recalled_at=_require_dt(data["recalled_at"]),
            no_match_reason=data.get("no_match_reason"),
        )

    async def get_memory(self, memory_id: str) -> dict[str, Any]:
        """Retrieve a single memory record by ID. Returns the raw response dict."""
        resp = await self._request("GET", f"/v1/memories/{memory_id}")
        return dict(resp.json())

    async def delete_memory(self, memory_id: str, actor: str = "sdk") -> None:
        """Delete a memory record. Returns None on success (204)."""
        await self._request(
            "DELETE",
            f"/v1/memories/{memory_id}",
            params={"actor": actor},
        )

    async def explain(self, trace_id: str) -> SDKMemoryTrace:
        """Fetch the recall explanation trace for *trace_id*."""
        resp = await self._request("GET", f"/v1/traces/{trace_id}")
        data: dict[str, Any] = resp.json()
        steps = [
            SDKTraceStep(
                memory_id=s["memory_id"],
                rank=s["rank"],
                score=s["score"],
                signals=s.get("signals", {}),
                explanation=s.get("explanation", ""),
                record_available=s.get("record_available", True),
            )
            for s in data.get("steps", [])
        ]
        return SDKMemoryTrace(
            trace_id=data["trace_id"],
            tenant_id=data["tenant_id"],
            query=data["query"],
            mode=data["mode"],
            steps=steps,
            created_at=_require_dt(data["created_at"]),
        )

    async def end_session(
        self, session_id: str, scope: dict[str, Any]
    ) -> None:
        """Signal session end to trigger optional consolidation."""
        await self._request(
            "POST",
            f"/v1/sessions/{session_id}:end",
            json={"scope": scope},
        )

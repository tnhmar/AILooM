"""Tenant isolation middleware for memory-layer FastAPI application.

The middleware intercepts every request on ``/v1/`` routes (excluding admin
routes) and resolves the tenant ID from the ``X-Tenant-Id`` header, storing
it on ``request.state.tenant_id``.  Requests that fail resolution are returned
as a 403 JSON response directly from this middleware so that Starlette's
ServerErrorMiddleware never sees the exception.

Skip rules (applied in order):
1. Any path not starting with ``/v1/`` is skipped (including ``/healthz``).
2. Any path starting with ``/v1/admin/`` is skipped (tenant in URL path).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from memory_layer.api.tenant import resolve_tenant_id
from memory_layer.domain.exceptions import TenantIsolationViolation

_CallNext = Callable[[Request], Awaitable[Response]]


class TenantMiddleware(BaseHTTPMiddleware):
    """Starlette/FastAPI middleware that resolves and stores the tenant ID.

    Installs by passing the middleware class to ``app.add_middleware``:

    .. code-block:: python

        app.add_middleware(TenantMiddleware)
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: _CallNext) -> Response:
        path: str = request.url.path

        if not path.startswith("/v1/") or path.startswith("/v1/admin/"):
            # /healthz, /docs, /openapi.json and all admin routes bypass
            # header-based tenant resolution.
            return await call_next(request)

        # resolve_tenant_id raises TenantIsolationViolation when the
        # X-Tenant-Id header is absent or mismatched.  We catch it here and
        # return a 403 directly so that Starlette's ServerErrorMiddleware
        # does NOT intercept the exception (which would produce a 500).
        try:
            tenant_id = resolve_tenant_id(request)
        except TenantIsolationViolation as exc:
            return JSONResponse(
                status_code=403,
                content={
                    "error_code": "TENANT_ISOLATION_VIOLATION",
                    "message": str(exc),
                    "details": {
                        "actor": exc.actor,
                        "requested_tenant_id": exc.requested_tenant_id,
                    },
                    "trace_id": None,
                },
            )

        request.state.tenant_id = tenant_id
        return await call_next(request)


def get_request_tenant_id(request: Request) -> str:
    """FastAPI dependency that reads the tenant ID placed by :class:`TenantMiddleware`.

    Use as a ``Depends`` parameter in endpoint signatures::

        @app.get("/v1/something")
        async def handler(
            tenant_id: str = Depends(get_request_tenant_id),
        ) -> ...

    Returns
    -------
    str
        The tenant ID stored by the middleware.

    Raises
    ------
    AttributeError
        If called on a request that was not processed by :class:`TenantMiddleware`
        (e.g. admin or health routes that bypass tenant resolution).
    """
    return str(request.state.tenant_id)

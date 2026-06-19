"""Tenant isolation middleware for memory-layer FastAPI application.

The middleware intercepts every request on ``/v1/`` routes (excluding admin
routes) and resolves the tenant ID from the ``X-Tenant-Id`` header, storing
it on ``request.state.tenant_id``.  Requests that fail resolution propagate
the ``TenantIsolationViolation`` exception to the registered exception handler
in ``errors.py``, which returns a 403 JSON response.

Skip rules (applied in order):
1. Any path not starting with ``/v1/`` is skipped (including ``/healthz``).
2. Any path starting with ``/v1/admin/`` is skipped (tenant in URL path).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from memory_layer.api.tenant import resolve_tenant_id

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

        # resolve_tenant_id raises TenantIsolationViolation on failure;
        # FastAPI's exception handler converts this to a 403 JSON response.
        tenant_id = resolve_tenant_id(request)
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

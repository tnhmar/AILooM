"""Tenant resolution utilities for memory-layer API.

These functions are the canonical boundary for reading and validating
tenant identity on every inbound request.
"""

from __future__ import annotations

from starlette.requests import Request

from memory_layer.domain.exceptions import TenantIsolationViolation
from memory_layer.domain.types import TenantId

# Expose the domain class under the spec alias so imports from this module
# or from domain/exceptions.py both work.
TenantIsolationError = TenantIsolationViolation

_HEADER = "x-tenant-id"


def resolve_tenant_id(request: Request) -> str:
    """Read tenant from the ``X-Tenant-Id`` header.

    Returns
    -------
    str
        The tenant ID string.

    Raises
    ------
    TenantIsolationViolation
        If the header is absent or blank.
    """
    raw: str | None = request.headers.get(_HEADER)
    if not raw or not raw.strip():
        raise TenantIsolationViolation(
            actor="<unauthenticated>",
            requested_tenant_id="<unknown>",
        )
    return raw.strip()


def assert_tenant_match(request_tenant_id: str, target_tenant_id: str) -> None:
    """Assert that *request_tenant_id* and *target_tenant_id* are identical.

    Raises
    ------
    TenantIsolationViolation
        If the two tenant IDs differ.
    """
    if request_tenant_id != target_tenant_id:
        raise TenantIsolationViolation(
            actor=request_tenant_id,
            requested_tenant_id=target_tenant_id,
        )


__all__ = [
    "resolve_tenant_id",
    "assert_tenant_match",
    "TenantIsolationError",
    "TenantId",
]

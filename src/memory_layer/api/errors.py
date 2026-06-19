"""FastAPI exception handlers for memory-layer domain errors."""

from __future__ import annotations

import logging
import traceback
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

from memory_layer.api.schemas import ErrorResponseModel
from memory_layer.domain.exceptions import (
    CapabilityNotAvailableError,
    MemoryLayerError,
    MemoryNotFoundError,
    TenantIsolationViolation,
)

log = logging.getLogger(__name__)

# Alias so code that raises TenantIsolationError (spec name) also works.
TenantIsolationError = TenantIsolationViolation


def _error_response(
    status_code: int,
    error_code: str,
    message: str,
    details: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> JSONResponse:
    body = ErrorResponseModel(
        error_code=error_code,
        message=message,
        details=details or {},
        trace_id=trace_id,
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


async def memory_not_found_handler(
    request: Request, exc: MemoryNotFoundError
) -> JSONResponse:
    return _error_response(
        404,
        "MEMORY_NOT_FOUND",
        str(exc),
        details={"memory_id": exc.memory_id},
    )


async def tenant_isolation_handler(
    request: Request, exc: TenantIsolationViolation
) -> JSONResponse:
    return _error_response(
        403,
        "TENANT_ISOLATION_VIOLATION",
        str(exc),
        details={
            "actor": exc.actor,
            "requested_tenant_id": exc.requested_tenant_id,
        },
    )


async def capability_not_available_handler(
    request: Request, exc: CapabilityNotAvailableError
) -> JSONResponse:
    return _error_response(
        409,
        "CAPABILITY_NOT_AVAILABLE",
        str(exc),
        details={"capability": exc.capability},
    )


async def pydantic_validation_handler(
    request: Request, exc: PydanticValidationError
) -> JSONResponse:
    """Handle Pydantic ValidationError raised outside FastAPI's normal request cycle."""
    return _error_response(
        400,
        "VALIDATION_ERROR",
        "Request validation failed.",
        details={"errors": exc.errors()},
    )


async def request_validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle FastAPI RequestValidationError (path / query / header / body parse errors).

    FastAPI raises this for all request-level validation failures and returns
    422 by default. We override it to return 400 with ErrorResponseModel so
    all validation failures have a uniform shape.
    """
    return _error_response(
        400,
        "VALIDATION_ERROR",
        "Request validation failed.",
        details={"errors": exc.errors()},
    )


async def memory_layer_error_handler(
    request: Request, exc: MemoryLayerError
) -> JSONResponse:
    """Catch-all for domain errors not handled by a more specific handler."""
    return _error_response(
        400,
        "DOMAIN_ERROR",
        str(exc),
    )


async def generic_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    log.error("Unhandled exception: %s", traceback.format_exc())
    return _error_response(
        500,
        "INTERNAL_SERVER_ERROR",
        "An unexpected error occurred.",
    )


def register_exception_handlers(app: Any) -> None:
    """Register all exception handlers onto *app*.

    Handler registration order: most-specific first.
    FastAPI/Starlette matches by the exception class's MRO, so subclass
    handlers must be registered before their base-class handlers.
    """
    # 404
    app.add_exception_handler(MemoryNotFoundError, memory_not_found_handler)
    # 403
    app.add_exception_handler(TenantIsolationViolation, tenant_isolation_handler)
    # 409
    app.add_exception_handler(
        CapabilityNotAvailableError, capability_not_available_handler
    )
    # 400 — FastAPI request-level validation (path / query / header / body)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    # 400 — Pydantic ValidationError raised programmatically
    app.add_exception_handler(PydanticValidationError, pydantic_validation_handler)
    # 400 — remaining domain errors
    app.add_exception_handler(MemoryLayerError, memory_layer_error_handler)
    # 500 — everything else
    app.add_exception_handler(Exception, generic_exception_handler)

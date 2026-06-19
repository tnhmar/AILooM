"""FastAPI exception handlers for memory-layer domain errors."""

from __future__ import annotations

import logging
import traceback
from typing import Any

from fastapi import Request
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

    More-specific handlers must be registered before generic ones because
    FastAPI/Starlette matches by MRO order when the same exception class
    appears multiple times.
    """
    app.add_exception_handler(MemoryNotFoundError, memory_not_found_handler)
    app.add_exception_handler(TenantIsolationViolation, tenant_isolation_handler)
    app.add_exception_handler(
        CapabilityNotAvailableError, capability_not_available_handler
    )
    app.add_exception_handler(PydanticValidationError, pydantic_validation_handler)
    app.add_exception_handler(MemoryLayerError, memory_layer_error_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

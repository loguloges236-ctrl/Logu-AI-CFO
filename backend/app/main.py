from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

from app.api.routes import accounting, auth, commerce, documents, finance, health, security
from app.core.config import get_settings
from app.core.errors import APIError
from app.core.logging import configure_logging
from app.security.edge import REQUEST_ID_KEY, EdgeMiddleware
from app.security.proxy import parse_networks

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    expose_docs = settings.expose_api_docs
    application = FastAPI(
        title="Logu AI CFO API",
        version="0.2.0",
        description="Phase 1 secure foundation API. No bank-write or payment capability exists.",
        docs_url="/docs" if expose_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if expose_docs else None,
    )

    # Same-origin deployment (Caddy routes /api to this service) needs no CORS at all.
    # CORS is enabled only when explicit origins are configured.
    if settings.frontend_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.frontend_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Content-Type", "X-CSRF-Token"],
            max_age=600,
        )

    # Added last, so it is the outermost layer: proxy resolution and header policy apply to everything.
    application.add_middleware(
        EdgeMiddleware,
        trusted_networks=parse_networks(settings.trusted_proxy_networks),
        trusted_hosts=settings.trusted_hosts,
        internal_hosts=settings.internal_api_hosts,
        force_https=settings.force_https,
        expose_api_docs=expose_docs,
    )

    register_error_handlers(application)
    application.include_router(health.router)
    application.include_router(auth.router, prefix=settings.api_prefix)
    application.include_router(documents.router, prefix=settings.api_prefix)
    application.include_router(security.router, prefix=settings.api_prefix)
    application.include_router(accounting.router, prefix=settings.api_prefix)
    application.include_router(commerce.router, prefix=settings.api_prefix)
    application.include_router(finance.router, prefix=settings.api_prefix)
    return application


def _error_body(request: Request, code: str, message: str, details: dict[str, Any] | None = None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": request.scope.get(REQUEST_ID_KEY),
        }
    }


def register_error_handlers(application: FastAPI) -> None:
    @application.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(
                request,
                detail.get("code", "request_failed"),
                detail.get("message", "Request failed."),
                detail.get("details"),
            ),
            headers=exc.headers,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Input values are deliberately omitted: they may contain passwords or document content.
        safe_errors = [
            {"loc": [str(part) for part in error["loc"]], "type": error["type"]} for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_error_body(request, "validation_failed", "Request validation failed.", {"errors": safe_errors}),
        )

    @application.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = "The requested resource was not found." if exc.status_code == 404 else "Request failed."
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(request, "http_error", message),
        )

    @application.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        # Logs the exception type only, never the request body, headers or cookies.
        logger.error("Unhandled failure: %s (request_id=%s)", type(exc).__name__, request.scope.get(REQUEST_ID_KEY))
        return JSONResponse(
            status_code=500,
            content=_error_body(request, "internal_error", "An unexpected error occurred."),
        )


app = create_app()

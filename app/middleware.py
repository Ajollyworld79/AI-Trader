from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = structlog.get_logger()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        response.headers["Cache-Control"] = "no-store"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, max_request_bytes: int) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.max_request_bytes = max_request_bytes

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID")
        if not request_id or len(request_id) > 128:
            request_id = str(uuid.uuid4())
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.max_request_bytes:
                    return JSONResponse(
                        {"detail": "request body too large"},
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    )
            except ValueError:
                return JSONResponse(
                    {"detail": "invalid content-length"},
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

        started = time.monotonic()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_complete",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
        )
        return response

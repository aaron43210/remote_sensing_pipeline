# =============================================================
# OWNER: AARON
# =============================================================
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
import time
import logging
import uuid

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log all incoming requests with timing."""

    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())[:8]
        start = time.time()

        logger.info(
            f"[{request_id}] {request.method} {request.url.path} "
            f"from {request.client.host}"
        )

        response = await call_next(request)
        elapsed = time.time() - start

        logger.info(
            f"[{request_id}] Status={response.status_code} "
            f"Time={elapsed*1000:.1f}ms"
        )

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Processing-Time"] = f"{elapsed*1000:.1f}ms"
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Simple rate limiting: 100 requests per minute per IP.
    In production use Redis-based rate limiter.
    """

    def __init__(self, app, max_requests=100, window_seconds=60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window_seconds
        self.clients = {}

    async def dispatch(self, request: Request, call_next):
        import time

        client_ip = request.client.host
        now = time.time()

        if client_ip not in self.clients:
            self.clients[client_ip] = []

        # Remove old requests outside window
        self.clients[client_ip] = [
            t for t in self.clients[client_ip]
            if now - t < self.window
        ]

        if len(self.clients[client_ip]) >= self.max_requests:
            return Response(
                content='{"error": "Rate limit exceeded"}',
                status_code=429,
                media_type="application/json"
            )

        self.clients[client_ip].append(now)
        return await call_next(request)

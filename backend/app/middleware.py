import logging
import re
import time
import uuid
from collections import deque
from threading import Lock

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.observability import request_id_context

logger = logging.getLogger(__name__)
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class SlidingWindowLimiter:
    def __init__(self, window_seconds: int = 60, max_clients: int = 10000):
        self.window_seconds = window_seconds
        self.max_clients = max_clients
        self._requests: dict[tuple[str, str], deque[float]] = {}
        self._lock = Lock()

    def allow(self, client: str, bucket: str, limit: int) -> tuple[bool, int]:
        now = time.monotonic()
        key = (client, bucket)
        with self._lock:
            requests = self._requests.setdefault(key, deque())
            cutoff = now - self.window_seconds
            while requests and requests[0] <= cutoff:
                requests.popleft()
            if len(requests) >= limit:
                retry_after = max(
                    1,
                    int(self.window_seconds - (now - requests[0])),
                )
                return False, retry_after
            requests.append(now)
            if len(self._requests) > self.max_clients:
                self._requests = {
                    item_key: events
                    for item_key, events in self._requests.items()
                    if events and events[-1] > cutoff
                }
            return True, 0


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.limiter = SlidingWindowLimiter()

    async def dispatch(self, request, call_next):
        path = request.url.path
        supplied_request_id = request.headers.get("x-request-id", "")
        request_id = (
            supplied_request_id
            if _REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
            else uuid.uuid4().hex
        )
        request.state.request_id = request_id
        context_token = request_id_context.set(request_id)
        started = time.perf_counter()
        status_code = 500
        try:
            if path.startswith("/api/"):
                client = request.client.host if request.client else "unknown"
                bucket = "login" if path == "/api/auth/login" else "api"
                limit = 10 if bucket == "login" else 120
                allowed, retry_after = self.limiter.allow(client, bucket, limit)
                if not allowed:
                    response = JSONResponse(
                        status_code=429,
                        content={"detail": "Rate limit exceeded"},
                        headers={
                            "Retry-After": str(retry_after),
                            "X-Request-ID": request_id,
                        },
                    )
                    status_code = response.status_code
                    return response
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception:
            logger.exception(
                "HTTP request failed",
                extra={
                    "event": "http_request",
                    "method": request.method,
                    "path": path,
                    "request_id": request_id,
                },
            )
            raise
        finally:
            logger.info(
                "HTTP request completed",
                extra={
                    "event": "http_request",
                    "method": request.method,
                    "path": path,
                    "status_code": status_code,
                    "duration_ms": round(
                        (time.perf_counter() - started) * 1000,
                        2,
                    ),
                    "request_id": request_id,
                },
            )
            request_id_context.reset(context_token)

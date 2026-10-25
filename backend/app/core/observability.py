"""Body-free request/task correlation, including the lifetime of SSE responses."""

import json
import logging
import re
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from uuid import uuid4

request_id: ContextVar[str] = ContextVar("request_id", default="-")
logger = logging.getLogger(__name__)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "time": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id.get(),
            "event": record.getMessage(),
        }
        for key in ("task_id", "job_id", "method", "route", "status", "duration_ms"):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        # Exception bodies and stack locals can contain provider responses or data.
        if record.exc_info:
            data["exception_type"] = record.exc_info[0].__name__
        return json.dumps(data, ensure_ascii=False)


def configure_logging() -> None:
    app_logger = logging.getLogger("app")
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    app_logger.setLevel(logging.INFO)
    app_logger.propagate = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


class RequestLoggingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        incoming = dict(scope.get("headers", [])).get(b"x-request-id", b"")
        rid = (
            incoming.decode("ascii")
            if re.fullmatch(b"[a-fA-F0-9]{32}", incoming)
            else uuid4().hex
        )
        token = request_id.set(rid)
        started = time.monotonic()
        status = 500

        async def correlated_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = dict(message)
                message["headers"] = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ] + [(b"x-request-id", rid.encode("ascii"))]
            await send(message)

        try:
            await self.app(scope, receive, correlated_send)
        finally:
            route = scope.get("route")
            logger.info(
                "http_request",
                extra={
                    "method": scope["method"],
                    "route": getattr(route, "path", "unmatched"),
                    "status": status,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                },
            )
            request_id.reset(token)

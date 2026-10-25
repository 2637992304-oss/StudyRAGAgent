import json
import logging

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.core.observability import (
    JsonFormatter,
    RequestLoggingMiddleware,
    request_id,
)


def test_request_correlation_preserves_sse_and_omits_private_url(caplog, monkeypatch):
    monkeypatch.setattr(logging.getLogger("app"), "propagate", True)
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/stream/{private_id}")
    def stream(private_id: str):
        return StreamingResponse(
            iter(["event: done\ndata: {}\n\n"]), media_type="text/event-stream"
        )

    with caplog.at_level(logging.INFO, logger="app.core.observability"):
        response = TestClient(app).get(
            "/stream/private-document?token=secret",
            headers={"X-Request-ID": "a" * 32},
        )
    assert response.headers["x-request-id"] == "a" * 32
    assert response.text == "event: done\ndata: {}\n\n"
    record = next(r for r in caplog.records if r.message == "http_request")
    data = json.loads(JsonFormatter().format(record))
    assert data["route"] == "/stream/{private_id}"
    assert "secret" not in json.dumps(data) and "private-document" not in json.dumps(
        data
    )
    assert request_id.get() == "-"


def test_untrusted_request_id_is_replaced():
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/")
    def root():
        return {"ok": True}

    response = TestClient(app).get("/", headers={"X-Request-ID": "user-secret"})
    assert len(response.headers["x-request-id"]) == 32
    assert response.headers["x-request-id"] != "user-secret"

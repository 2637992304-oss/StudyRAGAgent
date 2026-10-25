from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from redis import Redis
from sqlalchemy import text

from app.api import router
from app.api.attachments import router as attachment_router
from app.api.chat import router as chat_router
from app.api.external_tools import router as external_router
from app.core.config import get_settings
from app.core.observability import RequestLoggingMiddleware, configure_logging
from app.db.session import get_engine

configure_logging()
settings = get_settings()
app = FastAPI(title="Study RAG API", version="0.1.0")
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(chat_router)
app.include_router(attachment_router)
app.include_router(external_router)


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        Redis.from_url(settings.redis_url, socket_connect_timeout=1).ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Dependency unavailable") from exc
    return {"status": "ok", "database": "ok", "redis": "ok"}

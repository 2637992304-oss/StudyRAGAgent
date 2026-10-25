from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    kwargs = (
        {"pool_timeout": 10, "connect_args": {"connect_timeout": 10}}
        if url.startswith("postgresql")
        else {}
    )
    return create_engine(url, pool_pre_ping=True, **kwargs)


def get_db():
    with Session(get_engine()) as session:
        yield session

from fastapi import HTTPException

from app import main


def test_health_reports_dependencies(monkeypatch):
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, _query):
            return None

    class Engine:
        def connect(self):
            return Connection()

    class RedisClient:
        def ping(self):
            return True

    monkeypatch.setattr(main, "get_engine", lambda: Engine())
    monkeypatch.setattr(main.Redis, "from_url", lambda *_args, **_kwargs: RedisClient())
    assert "/api/v1/health" in main.app.openapi()["paths"]
    assert main.health() == {"status": "ok", "database": "ok", "redis": "ok"}


def test_health_reports_unavailable_database(monkeypatch):
    class UnavailableEngine:
        def connect(self):
            raise OSError("database unavailable")

    monkeypatch.setattr(main, "get_engine", lambda: UnavailableEngine())
    try:
        main.health()
    except HTTPException as exc:
        assert exc.status_code == 503
        assert exc.detail == "Dependency unavailable"
    else:
        raise AssertionError("health must fail when database is unavailable")

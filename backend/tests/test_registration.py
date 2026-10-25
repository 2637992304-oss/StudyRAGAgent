"""Registration, validation, unique constraints and newly created account scopes."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.auth import verify_password
from app.db.base import Base
from app.db.models import Document, User
from app.db.session import get_db
from app.main import app

PASSWORD = "a long local test password"


@pytest.fixture
def registration():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    def db_override():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    try:
        with TestClient(app) as client:
            yield client, engine
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def signup(client, email="student@example.test", password=PASSWORD):
    return client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )


def auth(response):
    assert response.status_code == 201, response.text
    assert set(response.json()) == {"access_token", "token_type"}
    assert response.json()["token_type"] == "bearer"
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_registration_normalizes_email_hashes_password_and_logs_in(registration):
    client, engine = registration
    # Leading/trailing spaces in passwords are significant, unlike email spaces.
    password = f" {PASSWORD} "
    headers = auth(signup(client, " Student@Example.test ", password))
    user = client.get("/api/v1/me", headers=headers).json()
    assert user["email"] == "student@example.test"
    assert set(user) == {"id", "email"}
    assert client.get("/api/v1/courses", headers=headers).json() == []
    with Session(engine) as db:
        saved = db.get(User, user["id"])
        assert saved.password_hash != password
        assert verify_password(password, saved.password_hash)
        assert not verify_password(password.strip(), saved.password_hash)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": " STUDENT@example.test ", "password": password},
    )
    assert response.status_code == 200
    assert (
        client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {response.json()['access_token']}"},
        ).json()
        == user
    )
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": user["email"], "password": "wrong password"},
        ).status_code
        == 401
    )


def test_duplicate_registration_preserves_original_credentials(registration):
    client, engine = registration
    auth(signup(client))
    response = signup(client, " STUDENT@EXAMPLE.TEST ", "a different long password")
    assert response.status_code == 409
    assert response.json()["detail"] == "该邮箱已注册，请直接登录"
    assert "access_token" not in response.json()
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert verify_password(PASSWORD, db.scalar(select(User)).password_hash)


def test_concurrent_duplicate_uses_database_constraint_and_rolls_back(
    registration, monkeypatch
):
    client, engine = registration
    auth(signup(client))
    original_scalar = Session.scalar
    calls = 0

    def miss_preflight(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        # Simulate another registration committing after the availability check.
        if calls == 1:
            return None
        return original_scalar(self, *args, **kwargs)

    monkeypatch.setattr(Session, "scalar", miss_preflight)
    response = signup(client)
    assert response.status_code == 409
    assert calls == 2  # The second query succeeds only after session rollback.
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
    # A later valid registration can still succeed.
    auth(signup(client, "another@example.test"))


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email": "student@example.test"},
        {"password": PASSWORD},
        {"email": "not-an-email", "password": PASSWORD},
        {"email": "student@", "password": PASSWORD},
        {"email": "a b@example.test", "password": PASSWORD},
        {"email": "", "password": PASSWORD},
        {"email": "a" * 250 + "@example.test", "password": PASSWORD},
        {"email": ["student@example.test"], "password": PASSWORD},
        {"email": "student@example.test", "password": "a" * 14},
        {"email": "student@example.test", "password": "a" * 129},
        {"email": "student@example.test", "password": " " * 20},
        {"email": "student@example.test", "password": None},
    ],
)
def test_invalid_registration_does_not_create_account(registration, body):
    client, engine = registration
    response = client.post("/api/v1/auth/register", json=body)
    assert response.status_code == 422
    assert "access_token" not in response.json()
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.parametrize(
    "password", ["a" * 15, "a" * 128, "学习密码支持中文和符号！" * 2]
)
def test_password_boundaries_and_unicode(registration, password):
    client, _ = registration
    auth(signup(client, password=password))
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "student@example.test", "password": password},
        ).status_code
        == 200
    )


def test_new_accounts_cannot_access_existing_courses_documents_or_chats(registration):
    client, engine = registration
    owner = auth(signup(client, "owner@example.test"))
    course = client.post(
        "/api/v1/courses", headers=owner, json={"name": "私人课程"}
    ).json()["id"]
    chat = client.post(
        "/api/v1/chat/sessions", headers=owner, json={"course_id": course}
    ).json()["id"]
    with Session(engine) as db:
        owner_id = client.get("/api/v1/me", headers=owner).json()["id"]
        document = Document(
            owner_id=owner_id,
            course_id=course,
            filename="private.md",
            document_type="笔记",
            mime_type="text/markdown",
            storage_key="private-test-key",
            sha256="0" * 64,
            size_bytes=10,
            status="ready",
        )
        db.add(document)
        db.commit()
        document_id = document.id
    newcomer = auth(signup(client, "newcomer@example.test"))
    assert client.get("/api/v1/courses", headers=newcomer).json() == []
    for path in (
        f"/courses/{course}/documents",
        f"/documents/{document_id}",
        f"/documents/{document_id}/file",
        f"/documents/{document_id}/chunks",
        f"/chat/sessions/{chat}/messages",
    ):
        assert client.get(f"/api/v1{path}", headers=newcomer).status_code == 404
    assert (
        client.post(
            "/api/v1/chat/sessions", headers=newcomer, json={"course_id": course}
        ).status_code
        == 404
    )
    assert client.get("/api/v1/courses", headers=owner).json() == [
        {"id": course, "name": "私人课程"}
    ]
    assert client.get("/api/v1/me").status_code == 401

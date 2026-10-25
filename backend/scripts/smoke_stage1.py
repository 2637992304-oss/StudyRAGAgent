"""Run a real Redis/Celery/embedding/pgvector Stage 1 smoke test.

Start Docker services and the Celery worker first, then run from backend/ with
`uv run python scripts/smoke_stage1.py`.
"""

import time
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.auth import hash_password
from app.db.models import Course, Document, User
from app.db.session import get_engine
from app.main import app


def main() -> None:
    emails = [f"smoke-{uuid4()}@example.test" for _ in range(2)]
    ids = [str(uuid4()) for _ in range(2)]
    password = str(uuid4())
    with Session(get_engine()) as db, db.begin():
        db.add_all(
            User(id=user_id, email=email, password_hash=hash_password(password))
            for user_id, email in zip(ids, emails, strict=True)
        )
    try:
        client = TestClient(app)
        headers = []
        for email in emails:
            response = client.post(
                "/api/v1/auth/login", json={"email": email, "password": password}
            )
            response.raise_for_status()
            headers.append(
                {"Authorization": f"Bearer {response.json()['access_token']}"}
            )
        a, b = headers
        course_response = client.post(
            "/api/v1/courses", json={"name": "阶段一冒烟课程"}, headers=a
        )
        course_response.raise_for_status()
        course_id = course_response.json()["id"]
        response = client.post(
            f"/api/v1/courses/{course_id}/documents",
            headers=a,
            data={"document_type": "笔记"},
            files={
                "file": (
                    "derivative.md",
                    "# 导数\n导数表示函数在一点的瞬时变化率。".encode(),
                    "text/markdown",
                )
            },
        )
        response.raise_for_status()
        doc_id = response.json()["document_id"]
        deadline = time.monotonic() + 180
        status = None
        while time.monotonic() < deadline:
            doc_response = client.get(f"/api/v1/documents/{doc_id}", headers=a)
            doc_response.raise_for_status()
            status = doc_response.json()["status"]
            if status in {"ready", "failed"}:
                break
            time.sleep(1)
        assert status == "ready", f"ingestion did not complete: {status}"
        chunks = client.get(f"/api/v1/documents/{doc_id}/chunks", headers=a).json()
        assert chunks and any("导数" in chunk["text"] for chunk in chunks)
        search = client.post(
            f"/api/v1/courses/{course_id}/search",
            json={"query": "函数瞬时变化率是什么"},
            headers=a,
        )
        search.raise_for_status()
        assert search.json() and search.json()[0]["document_id"] == doc_id
        assert client.get(f"/api/v1/documents/{doc_id}", headers=b).status_code == 404
        delete_response = client.delete(f"/api/v1/documents/{doc_id}", headers=a)
        assert delete_response.status_code == 204, delete_response.text
        after = client.post(
            f"/api/v1/courses/{course_id}/search",
            json={"query": "函数瞬时变化率是什么"},
            headers=a,
        )
        assert after.json() == []
        print("PASS: real upload -> Celery -> embedding -> pgvector search -> delete")
        print("PASS: second account cannot read the document")
    finally:
        with Session(get_engine()) as db:
            for doc in db.scalars(
                select(Document).where(Document.owner_id.in_(ids))
            ).all():
                db.delete(doc)
            db.flush()
            db.execute(delete(Course).where(Course.owner_id.in_(ids)))
            db.execute(delete(User).where(User.id.in_(ids)))
            db.commit()


if __name__ == "__main__":
    main()

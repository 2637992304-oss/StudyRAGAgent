from datetime import UTC, datetime

from sqlalchemy.orm import Session
from test_stage2 import library  # noqa: F401

from app.db.models import Message


def test_history_pages_latest_ties_and_scope(library):  # noqa: F811
    client, headers, courses, _, users, engine = library
    sid = client.post(
        "/api/v1/chat/sessions", headers=headers[0], json={"course_id": courses[0]}
    ).json()["id"]
    other = client.post(
        "/api/v1/chat/sessions", headers=headers[0], json={"course_id": courses[0]}
    ).json()["id"]
    with Session(engine) as db, db.begin():
        for i in range(241):
            db.add(
                Message(
                    id=f"{i:036}",
                    owner_id=users[0],
                    session_id=sid,
                    role="user",
                    content=f"message {i}",
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            )
    url = f"/api/v1/chat/sessions/{sid}/messages"
    page = client.get(url, headers=headers[0], params={"page_size": 50}).json()
    assert page["items"][-1]["content"] == "message 240"
    cursor = page["next_cursor"]
    assert (
        client.get(url, headers=headers[1], params={"page_size": 50}).status_code == 404
    )
    assert (
        client.get(
            f"/api/v1/chat/sessions/{other}/messages",
            headers=headers[0],
            params={"page_size": 50, "before": cursor},
        ).status_code
        == 400
    )
    assert (
        client.get(
            url, headers=headers[0], params={"page_size": 50, "before": "bad"}
        ).status_code
        == 400
    )
    ids = [m["id"] for m in page["items"]]
    while cursor:
        page = client.get(
            url, headers=headers[0], params={"page_size": 50, "before": cursor}
        ).json()
        ids = [m["id"] for m in page["items"]] + ids
        cursor = page["next_cursor"]
    assert ids == [f"{i:036}" for i in range(241)]
    legacy = client.get(url, headers=headers[0]).json()
    assert len(legacy) == 200 and legacy[-1]["content"] == "message 240"

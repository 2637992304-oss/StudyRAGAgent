"""Conversation naming, mutable retrieval scope, and account isolation."""

from sqlalchemy.orm import Session
from test_stage2 import library  # noqa: F401

from app.api import chat as chat_api
from app.db.models import Message
from app.providers.llm import Delta


def create(context):
    client, headers, courses, *_ = context
    response = client.post(
        "/api/v1/chat/sessions", json={"course_id": courses[0]}, headers=headers[0]
    )
    assert response.status_code == 201
    return response.json()


def test_default_title_and_persistent_manual_rename(library):  # noqa: F811
    client, headers, courses, *_ = library
    created = create(library)
    assert created["title"] == "新对话"
    assert created["title_renamed"] is False
    url = f"/api/v1/chat/sessions/{created['id']}"
    renamed = client.patch(url, json={"title": "  事务与并发  "}, headers=headers[0])
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "事务与并发"
    assert renamed.json()["title_renamed"] is True
    listing = client.get(
        f"/api/v1/chat/sessions?course_id={courses[0]}", headers=headers[0]
    )
    assert listing.json()[0]["title"] == "事务与并发"
    assert (
        client.patch(url, json={"title": "跨账号"}, headers=headers[1]).status_code
        == 404
    )
    for title in ["", "   ", None, "a" * 121]:
        assert client.patch(
            url, json={"title": title}, headers=headers[0]
        ).status_code in (400, 422)


def test_scope_updates_validate_course_owner_and_document_readiness(library):  # noqa: F811
    client, headers, _, docs, *_ = library
    created = create(library)
    url = f"/api/v1/chat/sessions/{created['id']}"
    scoped = client.patch(url, json={"document_ids": [docs[0]]}, headers=headers[0])
    assert scoped.status_code == 200
    assert scoped.json()["document_ids"] == [docs[0]]
    assert scoped.json()["title_renamed"] is False
    for ids in [[docs[1]], [docs[2]], [docs[3]], ["missing"], [], [docs[0], docs[0]]]:
        assert client.patch(
            url, json={"document_ids": ids}, headers=headers[0]
        ).status_code in (400, 404)
    restored = client.patch(url, json={"document_ids": None}, headers=headers[0])
    assert restored.status_code == 200
    assert restored.json()["document_ids"] is None


def test_scope_cannot_change_during_generation_and_patch_cors(library):  # noqa: F811
    client, headers, _, docs, users, engine = library
    created = create(library)
    with Session(engine) as db:
        db.add(
            Message(
                owner_id=users[0],
                session_id=created["id"],
                role="assistant",
                content="",
                status="pending",
            )
        )
        db.commit()
    url = f"/api/v1/chat/sessions/{created['id']}"
    assert (
        client.patch(
            url, json={"document_ids": [docs[0]]}, headers=headers[0]
        ).status_code
        == 409
    )
    response = client.options(
        url,
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert "PATCH" in response.headers["access-control-allow-methods"]


def test_first_question_auto_names_once_and_manual_title_is_never_overwritten(
    library,  # noqa: F811
    monkeypatch,
):
    client, headers, courses, *_ = library

    class FakeLLM:
        def stream(self, _messages, **_kwargs):
            yield Delta("这是解答。", finish_reason="stop")

    monkeypatch.setattr(chat_api, "get_llm_provider", lambda: FakeLLM())
    for manual in (False, True):
        created = create(library)
        url = f"/api/v1/chat/sessions/{created['id']}"
        if manual:
            assert (
                client.patch(
                    url, json={"title": "我自己的名称"}, headers=headers[0]
                ).status_code
                == 200
            )
        first = "不用检索，解释数据库事务的隔离级别以及并发异常。"
        response = client.post(
            url + "/messages", json={"content": first}, headers=headers[0]
        )
        assert response.status_code == 200
        listing = client.get(
            f"/api/v1/chat/sessions?course_id={courses[0]}", headers=headers[0]
        ).json()
        actual = next(item for item in listing if item["id"] == created["id"])
        assert actual["title"] == ("我自己的名称" if manual else first[:20])
        client.post(
            url + "/messages",
            json={"content": "后续问题不应改变标题"},
            headers=headers[0],
        )
        listing = client.get(
            f"/api/v1/chat/sessions?course_id={courses[0]}", headers=headers[0]
        ).json()
        assert (
            next(item for item in listing if item["id"] == created["id"])["title"]
            == actual["title"]
        )

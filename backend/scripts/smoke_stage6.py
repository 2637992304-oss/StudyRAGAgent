"""HTTP deployment/restart/restore acceptance using generated, non-private data.

State contains disposable account credentials: keep it in .runtime/, never publish.
Run create once, verify after restart/restore, then delete for deletion assertions.
"""

import argparse
import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx

CONTENT = "# 导数\n导数表示函数在一点的瞬时变化率。\n".encode()
PASSWORD = "isolated stage six acceptance password"


def events(response):
    response.raise_for_status()
    result = []
    for block in response.text.split("\n\n"):
        if "\ndata: " in block:
            name, raw = block.split("\ndata: ", 1)
            result.append((name.removeprefix("event: "), json.loads(raw)))
    assert not any(name == "error" for name, _ in result), "SSE error received"
    assert any(
        name == "done" and data["status"] == "completed" for name, data in result
    )
    return result


def login(client, state):
    result = client.post(
        "/api/v1/auth/login", json={"email": state["email"], "password": PASSWORD}
    )
    result.raise_for_status()
    return {"Authorization": "Bearer " + result.json()["access_token"]}


def create(client, state_path):
    users = []
    for _ in range(2):
        email = f"stage6-{uuid4()}@example.test"
        result = client.post(
            "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
        )
        result.raise_for_status()
        headers = {"Authorization": "Bearer " + result.json()["access_token"]}
        users.append((email, headers))
    (email, a), (_, b) = users
    course = client.post(
        "/api/v1/courses", json={"name": "阶段6公开示例：微积分"}, headers=a
    )
    course.raise_for_status()
    course_id = course.json()["id"]
    upload = client.post(
        f"/api/v1/courses/{course_id}/documents",
        headers=a,
        data={"document_type": "笔记"},
        files={"file": ("derivative.md", CONTENT, "text/markdown")},
    )
    upload.raise_for_status()
    doc_id = upload.json()["document_id"]
    state = {
        "email": email,
        "other_email": users[1][0],
        "course_id": course_id,
        "document_id": doc_id,
        "sha256": hashlib.sha256(CONTENT).hexdigest(),
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    deadline = time.monotonic() + 360
    while time.monotonic() < deadline:
        doc = client.get(f"/api/v1/documents/{doc_id}", headers=a)
        doc.raise_for_status()
        if doc.json()["status"] == "failed":
            raise AssertionError("Ingestion failed; inspect task diagnostics")
        if doc.json()["status"] == "ready":
            break
        time.sleep(2)
    else:
        raise TimeoutError("Ingestion did not complete in 360 seconds")
    for path in (
        f"/api/v1/documents/{doc_id}",
        f"/api/v1/documents/{doc_id}/file",
        f"/api/v1/documents/{doc_id}/chunks",
    ):
        assert client.get(path, headers=b).status_code == 404
    assert client.delete(f"/api/v1/documents/{doc_id}", headers=b).status_code == 404
    assert (
        client.post(f"/api/v1/documents/{doc_id}/retry", headers=b).status_code == 404
    )
    assert (
        client.post(
            f"/api/v1/courses/{course_id}/search", headers=b, json={"query": "导数"}
        ).status_code
        == 404
    )
    chat = client.post(
        "/api/v1/chat/sessions",
        headers=a,
        json={"course_id": course_id, "document_ids": [doc_id]},
    )
    chat.raise_for_status()
    state["session_id"] = chat.json()["id"]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    result = client.post(
        f"/api/v1/chat/sessions/{state['session_id']}/messages",
        headers=a,
        json={
            "content": "严格根据课程资料，导数表示什么？简短回答。",
            "thinking": False,
            "web_enabled": False,
        },
    )
    parsed = events(result)
    citations = [data for name, data in parsed if name == "citation"]
    assert citations and all(c["document_id"] == doc_id for c in citations)
    state["citation_ids"] = [c["citation_id"] for c in citations]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    verify(client, state)
    print("PASS create: registration, async ingestion, SSE/citation, account isolation")


def verify(client, state):
    a = login(client, state)
    doc_id, course_id, session_id = (
        state[k] for k in ("document_id", "course_id", "session_id")
    )
    courses = client.get("/api/v1/courses", headers=a)
    courses.raise_for_status()
    assert any(x["id"] == course_id for x in courses.json())
    doc = client.get(f"/api/v1/documents/{doc_id}", headers=a)
    doc.raise_for_status()
    assert doc.json()["status"] == "ready"
    original = client.get(f"/api/v1/documents/{doc_id}/file", headers=a)
    original.raise_for_status()
    assert hashlib.sha256(original.content).hexdigest() == state["sha256"]
    search = client.post(
        f"/api/v1/courses/{course_id}/search", headers=a, json={"query": "导数"}
    )
    search.raise_for_status()
    assert any(x["document_id"] == doc_id for x in search.json())
    history = client.get(f"/api/v1/chat/sessions/{session_id}/messages", headers=a)
    history.raise_for_status()
    assert any(
        x["role"] == "assistant" and x["status"] == "completed" and x["citations"]
        for x in history.json()
    )
    for citation_id in state["citation_ids"]:
        citation = client.get(f"/api/v1/citations/{citation_id}", headers=a)
        citation.raise_for_status()
        assert citation.json()["document_id"] == doc_id
    health = client.get("/api/v1/health")
    health.raise_for_status()
    assert health.json()["status"] == "ok" and "x-request-id" in health.headers
    assert client.get("/a-client-side-route").status_code == 200
    print("PASS verify: login, file hash, SQL search, history, citations, SPA, health")


def delete(client, state):
    a = login(client, state)
    doc_id, course_id = state["document_id"], state["course_id"]
    assert client.delete(f"/api/v1/documents/{doc_id}", headers=a).status_code == 204
    assert client.get(f"/api/v1/documents/{doc_id}/file", headers=a).status_code == 404
    result = client.post(
        f"/api/v1/courses/{course_id}/search", headers=a, json={"query": "导数"}
    )
    result.raise_for_status()
    assert result.json() == []
    for citation_id in state["citation_ids"]:
        assert (
            client.get(f"/api/v1/citations/{citation_id}", headers=a).status_code == 404
        )
    print("PASS delete: file, index and citations unavailable after document deletion")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "verify", "delete"])
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=400, trust_env=False) as client:
        if args.action == "create":
            if args.state.exists():
                raise SystemExit(
                    "State already exists; use another isolated acceptance run"
                )
            create(client, args.state)
        else:
            globals()[args.action](
                client, json.loads(args.state.read_text(encoding="utf-8"))
            )


if __name__ == "__main__":
    main()

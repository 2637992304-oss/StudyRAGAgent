"""Bounded final-version regression against the live local API, not a holdout rerun."""

import json
import time
from pathlib import Path

import httpx


def main():
    ctx = json.loads(sorted(Path(".eval-cache/stage4").glob("*.json"))[-1].read_text())
    client = httpx.Client(
        base_url="http://127.0.0.1:8000", timeout=180, trust_env=False
    )
    token = client.post(
        "/api/v1/auth/login",
        json={"email": ctx["email"], "password": "stage4-local-only"},
    ).json()["access_token"]
    headers = {"Authorization": "Bearer " + token}

    def req(method, path, **kwargs):
        r = client.request(method, path, headers=headers, **kwargs)
        r.raise_for_status()
        return r

    sid = req(
        "POST", "/api/v1/chat/sessions", json={"course_id": ctx["course_id"]}
    ).json()["id"]
    results = []
    for prompt in [
        "严格根据课程资料，Cobalt calibration threshold 是多少？",
        "允许使用通用知识，2+2是多少？",
    ]:
        started = time.monotonic()
        response = req(
            "POST", f"/api/v1/chat/sessions/{sid}/messages", json={"content": prompt}
        )
        message = req("GET", f"/api/v1/chat/sessions/{sid}/messages").json()[-1]
        assert message["status"] == "completed", message
        results.append(
            {
                "prompt": prompt,
                "message": message,
                "done": "event: done" in response.text,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            }
        )
    assert any(
        t.get("check") == "ai_citation_support"
        for t in results[0]["message"]["agent_trace"]
    )
    image = Path("../docs/stage4/fixtures/equation.png").read_bytes()
    attachment = req(
        "POST",
        f"/api/v1/chat/sessions/{sid}/attachments",
        files={"file": ("q.png", image)},
    ).json()
    response = req(
        "POST",
        f"/api/v1/chat/sessions/{sid}/messages",
        json={"content": "讲解这道题", "attachment_ids": [attachment["id"]]},
    )
    messages = req("GET", f"/api/v1/chat/sessions/{sid}/messages").json()
    assert messages[-1]["status"] == "completed"
    results.append(
        {
            "prompt": "讲解图片题",
            "message": messages[-1],
            "done": "event: done" in response.text,
        }
    )
    mid = messages[-2]["id"]
    req("DELETE", f"/api/v1/chat/sessions/{sid}/messages/{mid}")
    removed = client.get(
        f"/api/v1/chat/attachments/{attachment['id']}/file", headers=headers
    ).status_code
    assert removed == 404
    result = {
        "type": "final-version regression, not new holdout",
        "results": results,
        "deleted_message_attachment_status": removed,
    }
    Path("../docs/stage4/final_smoke.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    req("DELETE", f"/api/v1/chat/sessions/{sid}")
    print("Final live regression passed: RAG, text, image, cleanup")


if __name__ == "__main__":
    main()

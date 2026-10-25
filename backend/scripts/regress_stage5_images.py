"""Real image/history/ingestion regression using an existing isolated run."""

import argparse
import io
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("context")
    args = parser.parse_args()
    ctx = json.loads(Path(args.context).read_text(encoding="utf-8"))
    image = Image.new("RGB", (640, 180), "white")
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 72)
    ImageDraw.Draw(image).text((40, 45), "8 + 6 = ?", fill="black", font=font)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    content = stream.getvalue()
    out = Path(ctx["output"]) / (
        "image-regression-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    out.mkdir()
    (out / "equation.png").write_bytes(content)
    frozen = {
        "image": "equation.png",
        "question": "计算图片中的算式，不联网。",
        "expected": "14",
        "followup": "刚才的结果加1是多少？",
        "expected_followup": "15",
        "frozen_before_calls": True,
        "kind": "existing_feature_regression",
    }
    (out / "manifest.json").write_text(
        json.dumps(frozen, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result = {"checks": [], "answers": []}

    def save():
        (out / "results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=400) as c:
        login = c.post(
            "/api/v1/auth/login",
            json={
                "email": ctx["email"],
                "password": "auto5-local-only"
                if ctx["email"].startswith("auto5-")
                else "stage5-local-only",
            },
        )
        login.raise_for_status()
        c.headers["Authorization"] = "Bearer " + login.json()["access_token"]

        def req(method, url, **kw):
            r = c.request(method, url, **kw)
            r.raise_for_status()
            return r

        sid = req(
            "POST", "/api/v1/chat/sessions", json={"course_id": ctx["course"]}
        ).json()["id"]
        item = req(
            "POST",
            f"/api/v1/chat/sessions/{sid}/attachments",
            files={"file": ("equation.png", content, "image/png")},
        ).json()
        result["created_resources"] = {"session": sid, "attachment": item["id"]}

        def ask(question, **kw):
            req(
                "POST",
                f"/api/v1/chat/sessions/{sid}/messages",
                json={"content": question, **kw},
            )
            answer = req("GET", f"/api/v1/chat/sessions/{sid}/messages").json()[-1]
            result["answers"].append(answer)
            save()
            return answer

        first = ask(frozen["question"], attachment_ids=[item["id"]], thinking=True)
        result["checks"].append(
            {"name": "real_image_thinking", "passed": "14" in first["content"]}
        )
        second = ask(frozen["followup"], thinking=False)
        result["checks"].append(
            {"name": "image_history_mode_switch", "passed": "15" in second["content"]}
        )
        doc = req(
            "POST",
            f"/api/v1/chat/attachments/{item['id']}/save",
            json={"document_type": "参考资料"},
        ).json()
        result["created_resources"]["document"] = doc["document_id"]
        for _ in range(120):
            status = req("GET", f"/api/v1/documents/{doc['document_id']}").json()[
                "status"
            ]
            if status in {"ready", "failed"}:
                break
            time.sleep(1)
        result["checks"].append(
            {"name": "real_image_celery_ingestion", "passed": status == "ready"}
        )
        if status == "ready":
            third = ask(
                "严格根据保存的equation.png资料，图片里写了哪道算式？附课程来源。",
                thinking=False,
            )
            result["checks"].append(
                {
                    "name": "image_course_citations",
                    "passed": any(
                        x.get("document_id") == doc["document_id"]
                        for x in third["citations"]
                    ),
                }
            )
        save()
    print(json.dumps(result["checks"], ensure_ascii=False))
    print(out)


if __name__ == "__main__":
    main()

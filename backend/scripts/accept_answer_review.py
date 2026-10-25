"""Isolated SQL image acceptance and explicitly controlled real repair probes."""

import argparse
import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import checked_answer
from app.core.auth import hash_password
from app.db.models import ChatSession, Chunk, Message, User
from app.db.session import get_engine
from app.providers.call_options import Execution, profile
from app.providers.llm import call_observer, get_llm_provider
from app.providers.vision import image_block
from scripts.smoke_stage4 import cleanup

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup")
    args = parser.parse_args()
    if args.cleanup:
        ctx = json.loads(Path(args.cleanup).read_text(encoding="utf-8"))
        expected = f"review-{ctx['run']}@example.test".lower()
        if ctx["email"] != expected or len(ctx["users"]) != 1:
            raise ValueError("Not a review isolated context")
        with Session(get_engine()) as db:
            user = db.get(User, ctx["users"][0])
            if user and user.email != expected:
                raise ValueError("Cleanup identity mismatch")
            from app.db import models

            tables = [
                User,
                models.Course,
                models.Document,
                models.IngestionJob,
                Chunk,
                ChatSession,
                Message,
                models.Citation,
                models.Attachment,
                models.RepositoryGrant,
                models.ExternalSource,
            ]
            ledger = {}
            for model in tables:
                key = model.id if model is User else model.owner_id
                ledger[model.__tablename__] = list(
                    db.scalars(select(model.id).where(key.in_(ctx["users"])))
                )
            ledger_path = Path(ctx["output"], "cleanup-resource-ids.json")
            if not ledger_path.exists():
                ledger_path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
        cleanup(args.cleanup)
        with Session(get_engine()) as db:
            assert db.get(User, ctx["users"][0]) is None
            counts = {}
            for model in tables:
                key = model.id if model is User else model.owner_id
                rows = db.scalars(select(model.id).where(key.in_(ctx["users"])))
                counts[model.__tablename__] = len(list(rows))
            assert not any(counts.values())
        Path(ctx["output"], "cleanup.json").write_text(
            json.dumps({"isolated_account_removed": True, "remaining_rows": counts}),
            encoding="utf-8",
        )
        return
    manifest_path = ROOT / "docs/answer_review/fixtures/manifest.json"
    frozen = manifest_path.read_bytes()
    manifest = json.loads(frozen)
    run = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
    out = ROOT / "docs/answer_review/runs" / run
    out.mkdir(parents=True)
    (out / "manifest.json").write_bytes(frozen)
    picture = Image.new("RGB", (1000, 550), "#ffffff")
    ImageDraw.Draw(picture).multiline_text(
        (25, 20),
        manifest["sql"],
        fill="#111111",
        font=ImageFont.load_default(size=25),
        spacing=7,
    )
    image_path = out / "sql.png"
    picture.save(image_path)
    ctx = {
        "run": run,
        "users": [str(uuid4())],
        "email": f"review-{run}@example.test".lower(),
        "output": str(out),
        "sessions": {},
    }
    cache = ROOT / "backend/.eval-cache/answer-review"
    cache.mkdir(parents=True, exist_ok=True)
    context_path = cache / (run + ".json")
    result = {
        "frozen_sha256": hashlib.sha256(frozen).hexdigest(),
        "cases": [],
        "controlled_repairs": [],
        "reviewer": manifest["reviewer"],
    }

    def save():
        context_path.write_text(json.dumps(ctx, indent=2), encoding="utf-8")
        (out / "results.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # Program check of query semantics on synthetic data; no user database SQL.
    with sqlite3.connect(":memory:") as sample:
        sample.execute("CREATE TABLE instructor(dept_name TEXT,salary INTEGER)")
        sample.executemany(
            "INSERT INTO instructor VALUES (?,?)", [("A", 100), ("B", 200), ("C", 300)]
        )
        actual = [r[0] for r in sample.execute(manifest["sql"])]
        result["synthetic_sql_result"] = actual
        assert actual == manifest["expected_example_departments"]
    save()
    with Session(get_engine()) as db:
        db.add(
            User(
                id=ctx["users"][0],
                email=ctx["email"],
                password_hash=hash_password("review-local-only"),
            )
        )
        db.commit()
    print("Context:", context_path, flush=True)
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=400) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"email": ctx["email"], "password": "review-local-only"},
        )
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]

        def req(method, url, **kwargs):
            r = client.request(method, url, **kwargs)
            r.raise_for_status()
            return r

        ctx["course"] = req(
            "POST", "/api/v1/courses", json={"name": "SQL图片核验隔离验收"}
        ).json()["id"]
        doc = req(
            "POST",
            f"/api/v1/courses/{ctx['course']}/documents",
            data={"document_type": "笔记"},
            files={
                "file": (
                    "sql-note.md",
                    manifest["course_text"].encode(),
                    "text/markdown",
                )
            },
        ).json()
        ctx["document"] = doc.get("id", doc.get("document_id"))
        save()
        for _ in range(120):
            status = req("GET", f"/api/v1/documents/{ctx['document']}").json()["status"]
            if status in {"ready", "failed"}:
                break
            time.sleep(1)
        assert status == "ready"
        for case in manifest["cases"]:
            sid = (
                ctx["sessions"].get(case.get("follow"))
                or req(
                    "POST", "/api/v1/chat/sessions", json={"course_id": ctx["course"]}
                ).json()["id"]
            )
            ctx["sessions"][case["id"]] = sid
            body = {"content": case["question"], "thinking": case["thinking"]}
            if not case.get("follow"):
                item = req(
                    "POST",
                    f"/api/v1/chat/sessions/{sid}/attachments",
                    files={"file": ("sql.png", image_path.read_bytes(), "image/png")},
                ).json()
                body["attachment_ids"] = [item["id"]]
            entry = {"id": case["id"], "request": body, "session": sid}
            save()
            response = req("POST", f"/api/v1/chat/sessions/{sid}/messages", json=body)
            history = req("GET", f"/api/v1/chat/sessions/{sid}/messages").json()
            answer = history[-1]
            entry.update(answer=answer, sse=response.text)
            entry["checks"] = {
                "answered": answer["stop_reason"] == "answer",
                "thinking": answer["thinking"] is case["thinking"],
                "no_external": not any(
                    t.get("tool", "").startswith(("web_", "read_web", "github_"))
                    for t in answer["agent_trace"]
                ),
                "course_citation": not case["require_course"]
                or any(
                    c.get("kind", "course") == "course" for c in answer["citations"]
                ),
            }
            with Session(get_engine()) as db:
                stored = db.get(Message, answer["id"])
                entry["validation"] = [
                    {k: v for k, v in a.items() if k != "message"}
                    for a in stored.protocol_state.get("validation", [])
                ]
            result["cases"].append(entry)
            save()
            print(case["id"], entry["checks"], flush=True)
        for thinking in (False, True):
            observed = []
            token = call_observer.set(observed.append)
            with Session(get_engine()) as db:
                chat = db.get(ChatSession, ctx["sessions"]["image_plain"])
                chunk = db.scalar(
                    select(Chunk).where(
                        Chunk.owner_id == ctx["users"][0],
                        Chunk.document_id == ctx["document"],
                    )
                )
                state = {
                    "question": "图片中的SQL查询是什么意思？",
                    "answer": manifest["controlled_repair"]["candidate"],
                    "stop_reason": "answer",
                    "evidence": [chunk.id],
                    "external": {},
                    "policy": {},
                    "trace": [],
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "input_context": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "图片中的SQL查询是什么意思？"},
                                image_block(image_path.read_bytes(), "image/png"),
                            ],
                        }
                    ],
                }
                answer, citations, reason = checked_answer(
                    db,
                    chat,
                    state,
                    "auto",
                    get_llm_provider(),
                    execution=Execution.start(360),
                    options=profile("agent", thinking),
                )
                audits = state.get("answer_checks", [])
                entry = {
                    "thinking": thinking,
                    "controlled_candidate": state.get("answer"),
                    "original_candidate": manifest["controlled_repair"]["candidate"],
                    "answer": answer,
                    "reason": reason,
                    "citation_labels": [r[0] for r in citations],
                    "model_calls": observed,
                    "validation": [
                        {k: v for k, v in a.items() if k != "message"} for a in audits
                    ],
                    "repair_answers": [
                        a["message"].get("content") for a in audits if a.get("message")
                    ],
                    "checks": {
                        "repaired": reason == "answer"
                        and any(a["stage"] == "repair" for a in audits),
                        "bounded": sum(a["stage"] == "repair" for a in audits) == 1,
                        "rechecked": sum(a["stage"] == "review" for a in audits) == 2,
                        "review_off": all(
                            c["thinking"]["type"] == "disabled"
                            for c in observed
                            if c["purpose"] == "citation_check"
                        ),
                        "repair_mode": all(
                            c["thinking"]["type"]
                            == ("enabled" if thinking else "disabled")
                            for c in observed
                            if c["purpose"] == "agent"
                        ),
                    },
                }
            call_observer.reset(token)
            result["controlled_repairs"].append(entry)
            save()
            print("controlled_repair", thinking, entry["checks"], flush=True)
    print("Results:", out, flush=True)


if __name__ == "__main__":
    main()

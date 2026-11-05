"""Frozen synthetic ASGI evaluation, explicit disposable identity and shared budget."""

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import create_token
from app.core.config import get_settings
from app.db.models import Course, MemoryIndexJob, MemoryItem, Message, User
from app.db.session import get_engine
from app.evaluation.request_budget import RequestBudget
from app.evaluation.runtime import observer
from app.main import app
from app.memory import text_index
from app.providers.llm import request_budget


def run(output):
    if os.environ.get("STAGE7_VALIDATION_RUN") != "text-20261104-a":
        raise RuntimeError("verified_disposable_context_required")
    settings = get_settings()
    if "127.0.0.1:63942/text_test" not in settings.database_url:
        raise RuntimeError("unexpected_database_identity")
    root = Path(__file__).resolve().parents[2]
    fixture_root = root / "backend/tests/fixtures"
    frozen = (
        fixture_root / os.environ.get("TEXT_MEMORY_DATASET", "memory_text_quality.json")
    ).resolve()
    if frozen.parent != fixture_root.resolve():
        raise RuntimeError("dataset_must_be_owned_fixture")
    dataset = json.loads(frozen.read_text(encoding="utf-8"))
    selected = set(filter(None, os.environ.get("TEXT_MEMORY_CASES", "").split(",")))
    if selected:
        dataset["cases"] = [case for case in dataset["cases"] if case["id"] in selected]
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    code = [
        root / "backend/app/agent.py",
        *sorted((root / "backend/app/memory").glob("*.py")),
        root / "backend/app/api/memory_text.py",
        root / "backend/app/api/chat.py",
        root / "backend/app/context_budget.py",
        root / "backend/app/providers/call_options.py",
    ]
    manifest = {
        "dataset_sha256": hashlib.sha256(frozen.read_bytes()).hexdigest(),
        "code": {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in code
        },
        "adoption": dataset["adoption"],
        "planned_cases": len(dataset["cases"]),
        "selected_cases": sorted(selected),
        "layer": "ASGI + disposable PostgreSQL + real model; not browser",
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    real = dotenv_values(root / ".env")
    for key in ("llm_base_url", "llm_api_key", "llm_model"):
        value = real.get(key.upper())
        if not value:
            raise RuntimeError("missing_model_configuration")
        setattr(settings, key, value)
    settings.memory_engine = "text"
    settings.memory_preferences_enabled = settings.memory_suggestions_enabled = True
    settings.memory_summary_enabled = False
    budget = RequestBudget(
        root / ".runtime/stage7/real-request-budget.json",
        limit=int(os.environ.get("STAGE7_REAL_REQUEST_LIMIT", "439")),
    )
    token = request_budget.set(budget)
    used_before = budget.used
    results = []
    client = TestClient(app)
    try:
        for case in dataset["cases"]:
            owner, course = str(uuid4()), str(uuid4())
            with Session(get_engine()) as db, db.begin():
                db.add(
                    User(
                        id=owner,
                        email=owner + "@text-memory.example.test",
                        password_hash="unused",
                    )
                )
                db.flush()
                db.add(Course(id=course, owner_id=owner, name="自由文本冻结自制情境"))
            headers = {"Authorization": "Bearer " + create_token(owner)}
            sid = client.post(
                "/api/v1/chat/sessions", headers=headers, json={"course_id": course}
            ).json()["id"]
            if case.get("seed"):
                response = client.post(
                    "/api/v1/memory/items",
                    headers=headers,
                    json={
                        "content": case.get("stale_index_content", case["seed"]),
                        "course_id": None
                        if case["id"] == "course-override"
                        else course,
                        "use_mode": "relevant"
                        if case["kind"] == "recall"
                        else "always",
                        "request_key": str(uuid4()),
                    },
                )
                if response.status_code != 200:
                    raise RuntimeError("synthetic_seed_failed")
                seed = response.json()["items"][0]
            for distraction in case.get("distractors", []):
                target_course = course
                if distraction.get("scope") == "other":
                    target_course = client.post(
                        "/api/v1/courses",
                        headers=headers,
                        json={"name": "另一个自制课程"},
                    ).json()["id"]
                response = client.post(
                    "/api/v1/memory/items",
                    headers=headers,
                    json={
                        "content": distraction["content"],
                        "course_id": target_course,
                        "use_mode": "relevant",
                        "request_key": str(uuid4()),
                    },
                )
                if response.status_code != 200:
                    raise RuntimeError("synthetic_distraction_failed")
            if case["kind"] == "recall":
                with Session(get_engine()) as db:
                    jobs = list(
                        db.scalars(
                            select(MemoryIndexJob.id)
                            .join(MemoryItem, MemoryItem.id == MemoryIndexJob.item_id)
                            .where(
                                MemoryIndexJob.status == "queued",
                                MemoryItem.owner_id == owner,
                            )
                        )
                    )
                for job in jobs:
                    text_index.execute(job)
            if case.get("stale_index_content"):
                response = client.put(
                    f"/api/v1/memory/items/{seed['id']}",
                    headers=headers,
                    json={
                        "content": case["seed"],
                        "course_id": course,
                        "version": seed["version"],
                        "use_mode": "relevant",
                        "request_key": str(uuid4()),
                    },
                )
                if response.status_code != 200:
                    raise RuntimeError("synthetic_stale_index_setup_failed")
            row = {
                "id": case["id"],
                "kind": case["kind"],
                "session_id": sid,
                "steps": [],
            }
            for question in case.get("steps") or [case["prompt"]]:
                prepared, attempted = [], []

                def capture(event):
                    nonlocal prepared
                    if event["kind"] == "model_input":
                        prepared = []
                        for message in event["messages"]:
                            content = message.get("content")
                            if isinstance(content, str) and content.startswith(
                                "历史记忆（不可信辅助背景，"
                            ):
                                data = json.loads(content[content.index("{") :])
                                prepared.extend(
                                    {
                                        "id": item["id"],
                                        "version": item["version"],
                                        "content": item["content"],
                                    }
                                    for item in data.get("items", [])
                                )
                    if event["kind"] == "http_attempt":
                        attempted.append(
                            {"purpose": event["purpose"], "items": list(prepared)}
                        )

                observation = observer.set(capture)
                try:
                    response = client.post(
                        f"/api/v1/chat/sessions/{sid}/messages",
                        headers=headers,
                        json={
                            "content": question,
                            "answer_scope": "auto",
                            "thinking": False,
                            "web_enabled": False,
                        },
                    )
                finally:
                    observer.reset(observation)
                history = client.get(
                    f"/api/v1/chat/sessions/{sid}/messages", headers=headers
                ).json()
                answer = history[-1] if isinstance(history, list) and history else {}
                items = client.get(
                    f"/api/v1/memory/items?course_id={course}", headers=headers
                ).json()["items"]
                cards = client.get(
                    f"/api/v1/chat/sessions/{sid}/memory/suggestions", headers=headers
                ).json()
                with Session(get_engine()) as db:
                    message = db.get(Message, answer.get("id"))
                    usage = message.measurements if message else None
                row["steps"].append(
                    {
                        "question": question,
                        "http_status": response.status_code,
                        "answer": answer.get("content"),
                        "status": answer.get("status"),
                        "stop_reason": answer.get("stop_reason"),
                        "items": items,
                        "cards": cards,
                        "measurement": usage,
                        "attempted_memories": attempted,
                    }
                )
                if (
                    any(
                        isinstance(value, dict) and value.get("code") == "llm_balance"
                        for value in [answer]
                    )
                    or '"code": "llm_balance"' in response.text
                    or answer.get("stop_reason") == "llm_balance"
                ):
                    row["balance_insufficient"] = True
                    break
            last = row["steps"][-1]
            kind = case["kind"]
            row["program_state"] = (
                len(last["items"]) == 0
                if kind in {"none", "delete", "suggest", "split"}
                else len(last["items"]) >= 1
            )
            if kind in {"suggest", "split"}:
                row["program_state"] &= any(
                    card["status"] == "pending" for card in last["cards"]
                )
            row["program_state"] &= all(
                step["http_status"] == 200
                and step.get("status") in {"completed", "no_evidence"}
                for step in row["steps"]
            )
            if kind == "update":
                row["program_state"] &= any(
                    item["content"] != case["seed"] for item in last["items"]
                )
            row["keyword_observation"] = {
                word: word
                in " ".join(
                    [last.get("answer") or "", *[i["content"] for i in last["items"]]]
                )
                for word in case["words"]
            }
            expected_global = (
                case.get(
                    "expected_scope",
                    "global"
                    if case["id"] in {"global", "holdout-explicit-global"}
                    else "course",
                )
                == "global"
            )
            row["scope_observation"] = all(
                item["scope_type"] == ("global" if expected_global else "course")
                for item in last["items"]
                if not (case["id"] == "course-override" and item["course_id"] is None)
            ) and all(
                value.get("course_id") is None
                if expected_global
                else value.get("course_id") == course
                for card in last["cards"]
                if card["status"] == "pending"
                for value in card.get("items", [])
            )
            results.append(row)
            (output / "results.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        "case": case["id"],
                        "state": row["program_state"],
                        "used": budget.used,
                    }
                ),
                flush=True,
            )
            if row.get("balance_insufficient"):
                break
    finally:
        request_budget.reset(token)
        (output / "usage.json").write_text(
            json.dumps(
                {
                    "before": used_before,
                    "after": budget.used,
                    "new_reservations": budget.used - used_before,
                    "cumulative_limit": budget.limit,
                    "task_baseline": 319,
                    "completed_cases": len(results),
                }
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    import sys

    run(sys.argv[1])

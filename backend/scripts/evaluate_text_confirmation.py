"""Real edited-candidate checks; proposal creation is explicitly scripted."""

import hashlib
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import create_token
from app.core.config import get_settings
from app.db.models import (
    ChatSession,
    Course,
    MemoryItem,
    MemoryOperation,
    Message,
    User,
)
from app.db.session import get_engine
from app.evaluation.request_budget import RequestBudget
from app.main import app
from app.memory import text_graph, text_service
from app.memory.text_schema import TextMemoryInput
from app.providers.llm import request_budget


def run(output):
    settings = get_settings()
    if (
        os.environ.get("STAGE7_VALIDATION_RUN") != "text-20261104-a"
        or "127.0.0.1:63942/text_test" not in settings.database_url
    ):
        raise RuntimeError("identified_disposable_database_required")
    root = Path(__file__).resolve().parents[2]
    frozen = root / "backend/tests/fixtures/memory_text_confirmation_quality.json"
    dataset = json.loads(frozen.read_text(encoding="utf-8"))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    real = dotenv_values(root / ".env")
    for key in ("llm_base_url", "llm_api_key", "llm_model"):
        if not real.get(key.upper()):
            raise RuntimeError("missing_model_configuration")
        setattr(settings, key, real[key.upper()])
    settings.memory_engine = "text"
    settings.memory_preferences_enabled = settings.memory_suggestions_enabled = True
    settings.memory_summary_enabled = False
    budget = RequestBudget(
        root / ".runtime/stage7/real-request-budget.json",
        limit=int(os.environ["STAGE7_REAL_REQUEST_LIMIT"]),
    )
    token, before = request_budget.set(budget), budget.used
    results = []
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "fixture_hash": hashlib.sha256(frozen.read_bytes()).hexdigest(),
                "layer": dataset["layer"],
                "adoption": dataset["adoption"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    try:
        client = TestClient(app)
        for case in dataset["cases"]:
            owner = str(uuid4())
            with Session(get_engine()) as db, db.begin():
                user = User(
                    id=owner,
                    email=owner + "@confirmation.example.test",
                    password_hash="unused",
                )
                db.add(user)
                db.flush()
                course = Course(owner_id=owner, name="自制候选复查")
                db.add(course)
                db.flush()
                chat = ChatSession(owner_id=owner, course_id=course.id)
                db.add(chat)
                db.flush()
                seed = text_service.revise(
                    db,
                    owner,
                    TextMemoryInput(content=case["seed"], course_id=course.id),
                )
                rows = [
                    Message(
                        owner_id=owner,
                        session_id=chat.id,
                        role="user",
                        content="这次请用生活例子或详细步骤解释。",
                        status="completed",
                    )
                    for _ in range(3)
                ]
                assistant = Message(
                    owner_id=owner,
                    session_id=chat.id,
                    role="assistant",
                    content="待确认候选",
                    status="completed",
                )
                db.add_all([*rows, assistant])
                db.flush()
                args = {
                    "content": case["candidate"],
                    "source_ids": [row.id for row in rows],
                }
                if case["target"]:
                    args.update(id=seed.id, version=seed.version)
                staged = []
                text_graph.stage_tool(
                    db,
                    chat,
                    rows[-1],
                    {
                        "id": str(uuid4()),
                        "function": {
                            "name": "propose_memory",
                            "arguments": json.dumps(args),
                        },
                    },
                    staged,
                )
                card = text_graph.commit_staged(db, chat, assistant, staged)[0]
                course_id = course.id
            response = client.post(
                f"/api/v1/memory/text-suggestions/{card['id']}/accept",
                headers={"Authorization": "Bearer " + create_token(owner)},
                json={
                    "version": card["version"],
                    "items": [{"content": case["edited"], "course_id": course_id}],
                    "request_key": str(uuid4()),
                },
            )
            with Session(get_engine()) as db:
                items = list(
                    db.scalars(
                        select(MemoryItem).where(
                            MemoryItem.owner_id == owner, MemoryItem.status == "active"
                        )
                    )
                )
                checks = list(
                    db.scalars(
                        select(MemoryOperation).where(
                            MemoryOperation.owner_id == owner,
                            MemoryOperation.request_key.like("text:conflict:%"),
                        )
                    )
                )
                results.append(
                    {
                        "id": case["id"],
                        "http_status": response.status_code,
                        "response": response.json(),
                        "contents": [item.content for item in items],
                        "usage": [row.data.get("usage") for row in checks],
                        "passed": response.status_code == case["expected_http"]
                        and len(items) == case["expected_items"]
                        and any(item.content == case["seed"] for item in items),
                    }
                )
            (output / "results.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if response.status_code == 402:
                break
    finally:
        request_budget.reset(token)
        (output / "usage.json").write_text(
            json.dumps(
                {
                    "before": before,
                    "after": budget.used,
                    "new_reservations": budget.used - before,
                    "cumulative_limit": budget.limit,
                }
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    run(sys.argv[1])

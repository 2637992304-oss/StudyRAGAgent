"""Frozen self-authored comparison; only against owned disposable PostgreSQL."""

import hashlib
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.auth import create_token
from app.core.config import get_settings
from app.db.models import Message
from app.db.session import get_engine
from app.evaluation.fixtures import fixtures, scope_for
from app.evaluation.request_budget import RequestBudget
from app.main import app
from app.providers.llm import request_budget
from scripts.evaluate_stage3 import end_to_end


def run():
    settings = get_settings()
    prefix, identity = os.environ["OPT34_CONTAINER_PREFIX"], os.environ["OPT34_RUN_ID"]
    assert prefix == "studyrag-opt34-" + identity[:12]
    container = json.loads(
        subprocess.check_output(["docker", "inspect", prefix + "-pg"], text=True)
    )[0]
    assert container["Config"]["Labels"]["studyrag.opt34"] == identity
    engine = get_engine()
    assert engine.url.username == "opt34" and engine.url.host == "127.0.0.1"
    assert (
        str(engine.url.port)
        == container["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"]
    )
    if not (settings.llm_api_key and settings.llm_base_url and settings.llm_model):
        raise RuntimeError("existing_model_configuration_unavailable")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("app.core.observability").setLevel(logging.WARNING)
    root = Path(__file__).resolve().parents[2]
    parent = root / ".runtime/optimization56"
    parent.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="quality-", dir=parent))
    sources = output / "sources"
    sources.mkdir()
    definitions = [
        (
            "pressure",
            "澄砂练习",
            [
                "澄砂练习要求预热19秒。",
                "澄砂练习每批最多7个样本。",
                "澄砂练习温度低于负3度时停止。",
                "澄砂练习校验失败时不得复用样本。",
            ],
            1,
            "请分别检索预热、批次上限、低温和校验失败规则，再完整说明澄砂练习的四项条件。",
            ["19", "7", "负3", "不得复用"],
        ),
        (
            "normal",
            "绛羽练习",
            [
                "绛羽练习静置13分钟后读取。",
                "绛羽练习每轮只能读取5组结果。",
                "绛羽练习校准前不得开始计数。",
                "绛羽练习读取结束后必须清零。",
            ],
            4,
            "请分别查找并解释绛羽练习的静置时间、每轮上限、校准顺序和读后动作。",
            ["13", "5", "校准", "清零"],
        ),
        (
            "absent",
            "寒星练习",
            ["寒星练习只规定本节读数上限为6。", "寒星练习只规定本节温度界限为41度。"],
            4,
            "严格根据资料说明寒星练习中未给出的样本保留天数和保留编号生成算法；如果未给出请明确指出，不得补充通用规则。",
            [],
        ),
    ]
    dataset = {"documents": [], "cases": []}
    for key, title, facts, rounds, question, expected in definitions:
        # Repetition is real source DATA, not instructions to the agent.
        body = (
            "# "
            + title
            + "\n"
            + "\n".join(
                fact + " 本条是自制练习规约，只适用于该练习。" * 8 for fact in facts
            )
        )
        (sources / (key + ".md")).write_text(body, encoding="utf-8")
        dataset["documents"].append(
            {
                "key": key,
                "file": key + ".md",
                "owner": "a",
                "course": "main",
                "status": "ready",
            }
        )
        dataset["cases"].append(
            {
                "id": key,
                "question": question,
                "categories": ["multi_part"] if expected else ["no_answer"],
                "key_facts": expected,
                "scope": {"owner": "a", "course": "main", "documents": [key]},
                "max_rounds": rounds,
            }
        )
    dataset_path = output / "dataset.json"
    dataset_path.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    flags = {
        name: getattr(settings, name)
        for name in (
            "retrieval_strategy",
            "retrieval_top_k",
            "retrieval_max_distance",
            "agent_plain_tokens",
            "chat_deadline_seconds",
        )
    }
    manifest = {
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "source_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sources.iterdir()
        },
        "model": settings.llm_model,
        "embedding": settings.embedding_model,
        "conditions": {
            "concurrency": 1,
            "remote_warmth": "unknown",
            "local_embedding": "existing_cache_first_load_then_warm",
        },
        "request_estimate_limit": 24000,
        "history_characters": 12000,
        "flags": flags,
        "variants": ["baseline", "budget", "budget_finalize"],
        "rating": "substring_diagnostic_then_Codex_assisted_semantic_review_not_human",
        "code_sha256": {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (
                root / "backend/app/agent.py",
                root / "backend/app/context_budget.py",
                root / "backend/app/providers/llm.py",
                Path(__file__),
            )
        },
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    budget = RequestBudget(parent / "model-budget.json", limit=60)
    token = request_budget.set(budget)
    results = []
    try:
        for variant in manifest["variants"]:
            variant_path = output / variant
            variant_path.mkdir()
            with fixtures(dataset, variant_path, 900, 0, dataset_path) as (
                owners,
                courses,
                documents,
            ):
                headers = {"Authorization": "Bearer " + create_token(owners["a"])}
                other = create_token(owners["b"])
                for case in dataset["cases"]:
                    if 60 - budget.used < 4:
                        results.append(
                            {
                                "variant": variant,
                                "id": case["id"],
                                "status": "not_run_budget",
                            }
                        )
                        continue
                    scope = scope_for(case, owners, courses, documents)

                    def history(session, db):
                        db.add(
                            Message(
                                owner_id=owners["a"],
                                session_id=session,
                                role="user",
                                content="这是上次已经结束的练习。",
                            )
                        )
                        db.add(
                            Message(
                                owner_id=owners["a"],
                                session_id=session,
                                role="assistant",
                                content=(
                                    "过去练习记录仅属于上一题，不是当前资料依据。"
                                    * 1000
                                )[:12000],
                            )
                        )
                        db.commit()

                    with patch.multiple(
                        settings,
                        context_budget_enabled=variant != "baseline",
                        agent_finalize_enabled=variant == "budget_finalize",
                        context_request_tokens=24000,
                        agent_max_rounds=case["max_rounds"],
                        retrieval_strategy="dense",
                        retrieval_top_k=2,
                        retrieval_adjacent_context=False,
                        retrieval_pair_headings=False,
                        retrieval_verified_tables=False,
                    ):
                        with TestClient(app) as client, Session(engine) as db:
                            result = end_to_end(
                                client,
                                {**case, "_other_token": other},
                                scope,
                                headers,
                                db,
                                None,
                                document_keys={v: k for k, v in documents.items()},
                                setup_session=history,
                            )
                    results.append(
                        {
                            "variant": variant,
                            "id": case["id"],
                            "exploration_rounds": case["max_rounds"],
                            **result,
                        }
                    )
                    (output / "results.json").write_text(
                        json.dumps(results, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    print(
                        json.dumps(
                            {
                                "variant": variant,
                                "case": case["id"],
                                "reserved_http_requests": budget.used,
                                "terminal": result.get(
                                    "sse_terminal", result.get("status")
                                ),
                            }
                        )
                    )
    finally:
        request_budget.reset(token)
    summary = {
        "output": str(output),
        "reserved_http_requests": budget.used,
        "rows": len(results),
        "automatically_adopted": False,
        "limits": "small sample; unknown warmth/prices; semantic review required",
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary))


if __name__ == "__main__":
    run()

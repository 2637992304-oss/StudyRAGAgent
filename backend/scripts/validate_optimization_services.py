"""Disposable O02/O03 acceptance. Requires Docker and the backend dev environment.

Only fixed, labelled validation containers and fresh temporary storage are used.
Original project containers, volumes, credentials and model services are untouched.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
LABEL = "opt12-20261029"
NETWORK = "studyrag-opt12-20261029"
NAMES = ["studyrag-opt12-pg", "studyrag-opt12-redis", "studyrag-opt12-worker"]


def docker(*args, capture=False):
    result = subprocess.run(
        ["docker", *args], check=True, text=True, capture_output=capture
    )
    return result.stdout.strip() if capture else None


def owned(name, network=False):
    target = ".Labels" if network else ".Config.Labels"
    result = subprocess.run(
        [
            "docker",
            *(["network"] if network else []),
            "inspect",
            "--format",
            "{{index " + target + ' "codex.validation"}}',
            name,
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == LABEL


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Existing backend runtime image")
    parser.add_argument(
        "--skip-full", action="store_true", help="Only run the live acceptance tests"
    )
    args = parser.parse_args()
    for name in NAMES:
        if (
            subprocess.run(["docker", "inspect", name], capture_output=True).returncode
            == 0
        ):
            raise SystemExit(
                f"Existing container {name}; refusing to reuse or replace it"
            )
    if (
        subprocess.run(
            ["docker", "network", "inspect", NETWORK], capture_output=True
        ).returncode
        == 0
    ):
        raise SystemExit("Existing validation network; refusing to reuse it")
    output = ROOT / ".runtime" / "optimization12-live"
    if not output.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Validation output must stay inside the workspace")
    output.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="run-", dir=output)).resolve()
    for name in ("storage", "control"):
        (run / name).mkdir()
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql+psycopg://opt12:opt12-test-only@127.0.0.1:55439/opt12_validation",
        "REDIS_URL": "redis://127.0.0.1:56389/0",
        "STORAGE_DIR": str(run / "storage"),
        "OPT12_CONTROL": str(run / "control"),
        "AUTH_SECRET": "opt12-validation-secret",
        "LLM_API_KEY": "",
        "TAVILY_API_KEY": "",
        "GITHUB_CREDENTIALS": "{}",
        "LLM_BASE_URL": "https://example.test/v1",
        "LLM_MODEL": "controlled-validation",
        "PYTHONPATH": str(BACKEND),
    }
    env.pop("OPT12_LIVE", None)
    attempted, created_network = [], False
    try:
        docker("network", "create", "--label", "codex.validation=" + LABEL, NETWORK)
        created_network = True
        for name, image, values, ports in [
            (
                NAMES[0],
                "pgvector/pgvector:pg16",
                [
                    "POSTGRES_USER=opt12",
                    "POSTGRES_PASSWORD=opt12-test-only",
                    "POSTGRES_DB=opt12_validation",
                ],
                "127.0.0.1:55439:5432",
            ),
            (NAMES[1], "redis:7-alpine", [], "127.0.0.1:56389:6379"),
        ]:
            attempted.append(name)
            cmd = [
                "run",
                "-d",
                "--name",
                name,
                "--label",
                "codex.validation=" + LABEL,
                "--network",
                NETWORK,
                "-p",
                ports,
            ]
            for value in values:
                cmd += ["-e", value]
            docker(*cmd, image)
        end = time.monotonic() + 45
        while subprocess.run(
            ["docker", "exec", NAMES[0], "pg_isready", "-U", "opt12"],
            capture_output=True,
        ).returncode:
            if time.monotonic() > end:
                raise TimeoutError("Isolated PostgreSQL did not start")
            time.sleep(0.5)

        def python(*cmd):
            subprocess.run([sys.executable, *cmd], cwd=BACKEND, env=env, check=True)

        python("-m", "alembic", "upgrade", "head")
        python("-m", "alembic", "check")
        if not args.skip_full:
            python(
                "-m",
                "pytest",
                "-q",
                "--basetemp=" + str(run / "pytest"),
                "--junitxml=" + str(run / "backend.xml"),
            )
        cmd = [
            "run",
            "-d",
            "--name",
            NAMES[2],
            "--label",
            "codex.validation=" + LABEL,
            "--network",
            NETWORK,
        ]
        for source, target, readonly in [
            (BACKEND / "app", "/app/backend/app", True),
            (BACKEND / "tests" / "fixtures", "/validation", True),
            (run / "control", "/validation-control", False),
            (run / "storage", "/data/storage", False),
        ]:
            cmd += [
                "--mount",
                f"type=bind,source={source},target={target}"
                + (",readonly" if readonly else ""),
            ]
        for value in [
            "PYTHONPATH=/app/backend",
            "OPT12_VALIDATION=1",
            "DATABASE_URL=postgresql+psycopg://opt12:opt12-test-only@studyrag-opt12-pg:5432/opt12_validation",
            "REDIS_URL=redis://studyrag-opt12-redis:6379/0",
            "STORAGE_DIR=/data/storage",
            "LLM_API_KEY=",
            "TAVILY_API_KEY=",
            "GITHUB_CREDENTIALS={}",
            "AUTH_SECRET=opt12-validation-secret",
            "INGESTION_LEASE_SECONDS=6",
            "INGESTION_RECOVERY_INTERVAL=1",
            "INGESTION_DELIVERY_SECONDS=2",
        ]:
            cmd += ["-e", value]
        attempted.append(NAMES[2])
        docker(*cmd, args.image, "python", "/validation/optimization_worker.py")
        env["OPT12_LIVE"] = "1"
        python(
            "-m",
            "pytest",
            "tests/test_optimization_live.py",
            "-q",
            "--basetemp=" + str(run / "live-pytest"),
            "--junitxml=" + str(run / "live.xml"),
        )
        (run / "summary.json").write_text(
            json.dumps(
                {
                    "passed": True,
                    "full_regression": not args.skip_full,
                    "actual_model_requests": 0,
                    "note": (
                        "Real PostgreSQL/Redis/Celery; "
                        "deterministic embeddings/provider."
                    ),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Acceptance passed. Evidence: {run}")
    finally:
        for name in reversed(attempted):
            if owned(name):
                docker("rm", "-f", "-v", name)
        if created_network and owned(NETWORK, network=True):
            docker("network", "rm", NETWORK)
        for name in ("storage", "control", "pytest", "live-pytest"):
            target = (run / name).resolve()
            if target.parent != run or not run.is_relative_to(output.resolve()):
                raise ValueError("Invalid temporary cleanup target")
            if target.exists():
                shutil.rmtree(target)


if __name__ == "__main__":
    main()

"""Fresh PG/Redis, real worker interruption, API wire and browser validation."""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from scripts.validate_measurement_services import command, run


def port():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


def checks(environment, output, prefix, identity):
    root = Path(__file__).resolve().parents[2]
    backend = root / "backend"
    network = prefix + "-network"
    worker = prefix + "-worker"
    processes = []
    handles = []
    created_network = False
    worker_created = False
    connected = []
    api_port, web_port = port(), port()
    api_url, web_url = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{web_port}"
    control, storage = output / "control", output / "storage"
    control.mkdir()
    storage.mkdir()

    def owned(name):
        raw = json.loads(command(["docker", "inspect", name]))[0]
        assert raw["Config"]["Labels"]["studyrag.opt34"] == identity

    def execute(args, env, cwd, name):
        result = subprocess.run(
            args,
            env=env,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        text = re.sub(
            r"Bearer [A-Za-z0-9._~+-]+",
            "Bearer [REDACTED]",
            (result.stdout or "") + (result.stderr or ""),
        )
        (output / name).write_text(text, encoding="utf-8")
        for xml in output.glob("*.xml"):
            raw = xml.read_text(encoding="utf-8")
            xml.write_text(
                re.sub(r"Bearer [A-Za-z0-9._~+-]+", "Bearer [REDACTED]", raw),
                encoding="utf-8",
            )
        if result.returncode:
            raise RuntimeError("isolated_" + name + "_failed")

    def start(args, env, cwd, name):
        stream = (output / name).open("w", encoding="utf-8")
        handles.append(stream)
        proc = subprocess.Popen(
            args,
            env=env,
            cwd=cwd,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        processes.append(proc)
        return proc

    try:
        command(
            [
                "docker",
                "network",
                "create",
                "--label",
                "studyrag.opt34=" + identity,
                network,
            ]
        )
        created_network = True
        for name in (prefix + "-pg", prefix + "-redis"):
            owned(name)
            command(["docker", "network", "connect", network, name])
            connected.append(name)
        args = [
            "docker",
            "run",
            "-d",
            "--name",
            worker,
            "--label",
            "studyrag.opt34=" + identity,
            "--network",
            network,
        ]
        for source, target, readonly in [
            (backend / "app", "/app/backend/app", True),
            (
                backend / "tests/fixtures/budget_worker.py",
                "/validation/budget_worker.py",
                True,
            ),
            (control, "/validation-control", False),
            (storage, "/data/storage", False),
        ]:
            args += [
                "--mount",
                f"type=bind,source={source},target={target}"
                + (",readonly" if readonly else ""),
            ]
        for key, value in dict(
            OPT56_VALIDATION="1",
            PYTHONIOENCODING="utf-8",
            PYTHONPATH="/app/backend",
            DATABASE_URL=f"postgresql+psycopg://opt34:disposable-only@{prefix}-pg:5432/validation",
            REDIS_URL=f"redis://{prefix}-redis:6379/0",
            STORAGE_DIR="/data/storage",
            LLM_API_KEY="",
            TAVILY_API_KEY="",
            GITHUB_CREDENTIALS="{}",
            AUTH_SECRET="opt34-isolated-only",
            INGESTION_LEASE_SECONDS="6",
            INGESTION_RECOVERY_INTERVAL="1",
            INGESTION_DELIVERY_SECONDS="2",
        ).items():
            args += ["-e", key + "=" + value]
        command(
            args
            + [
                "studyrag-backend:stage6-clean",
                "python",
                "/validation/budget_worker.py",
            ]
        )
        worker_created = True
        env = dict(
            environment,
            OPT56_VALIDATION="1",
            OPT56_LIVE="1",
            OPT56_API_URL=api_url,
            OPT56_WORKER=worker,
            OPT56_CONTROL=str(control),
            STORAGE_DIR=str(storage),
            CONTEXT_BUDGET_ENABLED="true",
            AGENT_FINALIZE_ENABLED="true",
            AGENT_MAX_ROUNDS="1",
            BACKEND_CORS_ORIGINS=web_url,
            LLM_MODEL="controlled-opt56",
            LLM_API_KEY="fixture-only",
            LLM_BASE_URL="https://example.test/v1",
        )
        start(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "budget_api:app",
                "--app-dir",
                str(backend / "tests/fixtures"),
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
            ],
            env,
            backend,
            "api.txt",
        )
        end = time.monotonic() + 40
        while time.monotonic() < end:
            try:
                response = httpx.get(api_url + "/api/v1/validation-identity", timeout=2)
                if (
                    response.status_code == 200
                    and response.json().get("run_id") == identity
                ):
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        else:
            raise RuntimeError("isolated_api_not_ready")
        execute(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/test_optimization56_live.py",
                "-q",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                str(output / "live-pytest"),
                "--junitxml",
                str(output / "live.xml"),
            ],
            env,
            backend,
            "live.txt",
        )
        node = shutil.which("node")
        if node is None:
            raise RuntimeError("node_unavailable_for_browser")
        frontend = root / "frontend"
        browser_env = dict(env, VITE_API_BASE_URL=api_url, PLAYWRIGHT_BASE_URL=web_url)
        start(
            [
                node,
                str(frontend / "node_modules/vite/bin/vite.js"),
                "--host",
                "127.0.0.1",
                "--port",
                str(web_port),
                "--strictPort",
            ],
            browser_env,
            frontend,
            "frontend.txt",
        )
        end = time.monotonic() + 30
        while time.monotonic() < end:
            try:
                if httpx.get(web_url, timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        else:
            raise RuntimeError("isolated_frontend_not_ready")
        execute(
            [
                node,
                str(frontend / "node_modules/playwright/cli.js"),
                "test",
                *(
                    ["e2e/optimization56-live.spec.ts"]
                    if os.environ.get("OPT56_BROWSER_LIVE_ONLY") == "1"
                    else []
                ),
                "--reporter=line",
                "--output",
                str(output / "browser-temp"),
            ],
            browser_env,
            frontend,
            "browser.txt",
        )
        if os.environ.get("OPT56_RUN_REAL_MODEL") == "1":
            quality_env = dict(environment, HF_HUB_OFFLINE="1")
            for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
                quality_env.pop(key, None)
            execute(
                [sys.executable, "-m", "scripts.evaluate_optimization56"],
                quality_env,
                backend,
                "quality.txt",
            )
        budget_file = root / ".runtime/optimization56/model-budget.json"
        return {
            "real_model_evaluation": os.environ.get("OPT56_RUN_REAL_MODEL") == "1",
            "task_reservations_at_finish": json.loads(budget_file.read_text())[
                "reserved"
            ]
            if budget_file.exists()
            else 0,
        }
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                    )
                else:
                    process.terminate()
                process.wait(timeout=15)
        for handle in handles:
            handle.close()
        if worker_created:
            owned(worker)
            command(["docker", "rm", "-f", "-v", worker])
        for name in reversed(connected):
            owned(name)
            command(["docker", "network", "disconnect", network, name])
        if created_network:
            data = json.loads(command(["docker", "network", "inspect", network]))[0]
            assert data["Labels"]["studyrag.opt34"] == identity
            command(["docker", "network", "rm", network])
        for name in ("control", "storage", "live-pytest", "browser-temp"):
            path = output / name
            if path.exists():
                assert path.resolve().parent == output.resolve()
                shutil.rmtree(path)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-full",
        action="store_true",
        help="Skip completed full regression; still run service acceptance",
    )
    parser.add_argument(
        "--live-model",
        action="store_true",
        help="Use existing model credentials, shared task quota of 60 requests",
    )
    parser.add_argument(
        "--live-only",
        action="store_true",
        help="Rerun only the changed live browser fixture after full regression",
    )
    args = parser.parse_args()
    if args.live_only:
        os.environ["OPT56_BROWSER_LIVE_ONLY"] = "1"
    if args.live_model:
        os.environ["OPT56_RUN_REAL_MODEL"] = "1"
    run(extra_checks=checks, output_group="optimization56", skip_full=args.skip_full)

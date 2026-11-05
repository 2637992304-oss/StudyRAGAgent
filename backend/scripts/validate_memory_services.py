"""Creates labelled disposable PG/Redis; never reads the business connection."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

from scripts.validate_measurement_services import run
from scripts.validate_optimization56 import port


def browser(environment, output, identity):
    backend = Path(__file__).resolve().parents[1]
    frontend = backend.parent / "frontend"
    api_port, web_port = port(), port()
    api, web = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{web_port}"
    env = {
        **environment,
        "STAGE7_API_PORT": str(api_port),
        "STAGE7_API_URL": api,
        "VITE_API_BASE_URL": api,
        "BACKEND_CORS_ORIGINS": web,
        "STAGE7_LIVE": "1",
        "PLAYWRIGHT_BASE_URL": web,
        "PYTHONPATH": str(backend),
        "LLM_API_KEY": "stage7-scripted-only",
        "LLM_BASE_URL": "https://unused.invalid",
        "LLM_MODEL": "scripted-only",
        "STAGE6_LIVE": "0",
        "REGISTRATION_LIVE": "0",
        "WORKSPACE_LIVE": "0",
    }
    processes, handles = [], []
    try:
        for args, cwd, name in [
            ([sys.executable, "tests/fixtures/memory_api.py"], backend, "http.log"),
            (
                [
                    shutil.which("node"),
                    str(frontend / "node_modules/vite/bin/vite.js"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(web_port),
                    "--strictPort",
                ],
                frontend,
                "vite.log",
            ),
        ]:
            handle = (output / name).open("w", encoding="utf-8")
            handles.append(handle)
            processes.append(
                subprocess.Popen(
                    args,
                    cwd=cwd,
                    env=env,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            )
        end = time.monotonic() + 30
        while time.monotonic() < end:
            try:
                if (
                    httpx.get(api + "/api/v1/_stage7_identity").json()["id"] == identity
                    and httpx.get(web).status_code == 200
                ):
                    break
            except (httpx.HTTPError, ValueError, KeyError):
                pass
            time.sleep(0.2)
        else:
            raise RuntimeError("isolated_network_not_ready")
        result = subprocess.run(
            [
                shutil.which("node"),
                str(frontend / "node_modules/playwright/cli.js"),
                "test",
                *([] if "--full" in sys.argv else ["e2e/memory-live.spec.ts"]),
                "--reporter=line",
                "--output",
                str(output / "browser-temp"),
            ],
            cwd=frontend,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        (output / "browser.txt").write_text(
            result.stdout + result.stderr, encoding="utf-8"
        )
        if result.returncode:
            raise RuntimeError("stage7_browser_failed: " + str(output))
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
        for handle in handles:
            handle.close()


def checks(environment, output, prefix, identity):
    environment = {**environment, "STAGE7_VALIDATION_RUN": identity}
    if "--extended-budget" in sys.argv:
        environment["STAGE7_REAL_REQUEST_LIMIT"] = "320"
    backend = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_memory.py",
            "tests/test_memory_worker.py",
            "tests/test_memory_migration.py",
            *(
                [
                    "tests/test_context_budget.py",
                    "tests/test_agent_finalization.py",
                    "tests/test_request_budget.py",
                ]
                if "--budget" in sys.argv
                else []
            ),
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(output / "memory-pytest"),
            "--junitxml",
            str(output / "memory.xml"),
        ],
        env=environment,
        cwd=backend,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    (output / "memory-tests.txt").write_text(
        result.stdout + result.stderr, encoding="utf-8"
    )
    if result.returncode:
        raise RuntimeError("memory_tests_failed: " + str(output))
    browser(environment, output, identity)
    if "--real" in sys.argv:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.evaluate_memory",
                str(output / "quality"),
                *(["--images-only"] if "--images-only" in sys.argv else []),
                *(["--repair"] if "--repair" in sys.argv else []),
                *(["--repair-final"] if "--repair-final" in sys.argv else []),
                *(["--refusal-only"] if "--refusal-only" in sys.argv else []),
            ],
            env=environment,
            cwd=backend,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        (output / "quality-run.txt").write_text(
            result.stdout + result.stderr, encoding="utf-8"
        )
        if result.returncode:
            raise RuntimeError("real_quality_incomplete: " + str(output))
        return {"memory_engineering": True, "real_model_evaluation": True}
    return {"memory_engineering": True, "external_model_requests": 0}


if __name__ == "__main__":
    run(extra_checks=checks, output_group="stage7", skip_full="--full" not in sys.argv)

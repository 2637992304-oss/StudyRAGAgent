"""Actual isolated HTTP/browser checks; preserves logs, owns only its children."""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run(output):
    if (
        "/text_browser" not in os.environ.get("DATABASE_URL", "")
        or os.environ.get("STAGE7_VALIDATION_RUN") != "text-20261104-a"
    ):
        raise RuntimeError("dedicated_browser_database_required")
    backend = Path(__file__).resolve().parents[1]
    frontend = backend.parent / "frontend"
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    api_port, web_port = port(), port()
    api, web = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{web_port}"
    env = {
        **os.environ,
        "MEMORY_ENGINE": "text",
        "MEMORY_PREFERENCES_ENABLED": "true",
        "MEMORY_SUGGESTIONS_ENABLED": "true",
        "MEMORY_SUMMARY_ENABLED": "true",
        "TEXT_MEMORY_LIVE": "1",
        "TEXT_API_PORT": str(api_port),
        "TEXT_API_URL": api,
        "VITE_API_BASE_URL": api,
        "BACKEND_CORS_ORIGINS": web,
        "PLAYWRIGHT_BASE_URL": web,
        "PYTHONPATH": str(backend),
        "LLM_API_KEY": "scripted-only",
        "LLM_BASE_URL": "https://unused.invalid",
    }
    for key in ("STAGE7_LIVE", "STAGE6_LIVE", "REGISTRATION_LIVE", "WORKSPACE_LIVE"):
        env.pop(key, None)
    processes, handles = [], []
    try:
        for command, cwd, filename in [
            ([sys.executable, "tests/fixtures/memory_text_api.py"], backend, "api.log"),
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
            handle = (output / filename).open("w", encoding="utf-8")
            handles.append(handle)
            processes.append(
                subprocess.Popen(
                    command,
                    cwd=cwd,
                    env=env,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            )
        until = time.monotonic() + 35
        while time.monotonic() < until:
            try:
                if (
                    httpx.get(api + "/api/v1/_text_identity").json()["id"]
                    == "text-20261104-a"
                    and httpx.get(web).status_code == 200
                ):
                    break
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.2)
        else:
            raise RuntimeError("identified_browser_services_not_ready")
        result = subprocess.run(
            [
                shutil.which("node"),
                str(frontend / "node_modules/playwright/cli.js"),
                "test",
                *(
                    []
                    if os.environ.get("TEXT_MEMORY_BROWSER_FULL") == "1"
                    else ["e2e/memory-text-live.spec.ts", "e2e/memory-ui.spec.ts"]
                ),
                "--reporter=line",
                "--output",
                str(output / "artifacts"),
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
        print(result.stdout[-3500:])
        if result.returncode:
            raise RuntimeError("browser_validation_failed")
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                else:
                    process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for handle in handles:
            handle.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run(sys.argv[1])

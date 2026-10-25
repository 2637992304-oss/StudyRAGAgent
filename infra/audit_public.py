"""Inspect tracked/staged files without printing suspected secret values.

This is a bounded leak check plus a public file/link inventory, not a guarantee
that arbitrary private prose can be identified automatically.
"""

import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
GIT = ["git", "-c", f"safe.directory={ROOT.as_posix()}"]
TOKENS = re.compile(
    rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}"
    rb"|sk-[A-Za-z0-9]{32,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)
DENIED = {
    ".venv",
    "node_modules",
    "__pycache__",
    ".runtime",
    ".eval-cache",
    "storage",
    "model_cache",
    ".model-cache",
    ".uv-cache",
}


def tracked():
    return [
        p.decode("utf-8")
        for p in subprocess.check_output([*GIT, "ls-files", "-z"], cwd=ROOT).split(
            b"\0"
        )
        if p
    ]


def known_secrets():
    path = ROOT / ".env"
    values = []
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if not sep or not re.search("SECRET|KEY|TOKEN|PASSWORD|CREDENTIALS", key):
                continue
            value = value.strip().strip("\"'")
            if value.startswith("{"):

                def collect(obj):
                    if isinstance(obj, dict):
                        for item in obj.values():
                            collect(item)
                    elif isinstance(obj, str):
                        values.append(obj.encode())

                collect(json.loads(value))
            elif len(value) >= 16 and not re.search(
                "replace|change-this|example|development|demo|^your-", value, re.I
            ):
                values.append(value.encode())
    return [x for x in values if len(x) >= 16]


def main():
    files = tracked()
    public = set(files)
    secrets = known_secrets()
    errors = []
    for name in files:
        path = Path(name)
        if (
            any(p in DENIED for p in path.parts)
            or (path.name.startswith(".env") and path.name != ".env.example")
            or path.suffix in {".dump", ".log", ".backup"}
        ):
            errors.append(f"Private/generated file: {name}")
        data = subprocess.check_output([*GIT, "show", ":" + name], cwd=ROOT)
        if TOKENS.search(data) or any(secret in data for secret in secrets):
            errors.append(f"Possible credential in: {name} (value withheld)")
        if len(data) > 10 * 1024 * 1024:
            errors.append(f"Unexpected large file: {name}")
        if path.suffix.lower() == ".md":
            text = re.sub(r"```.*?```", "", data.decode("utf-8-sig"), flags=re.S)
            for target in re.findall(r"!?\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)", text):
                target = target.strip("<>")
                url = urlsplit(target)
                if url.scheme or target.startswith("#"):
                    continue
                resolved = (ROOT / path.parent / unquote(url.path)).resolve()
                try:
                    relative = resolved.relative_to(ROOT).as_posix()
                except ValueError:
                    errors.append(f"Nonportable local link: {name}")
                    continue
                if relative not in public and not any(
                    p.startswith(relative.rstrip("/") + "/") for p in public
                ):
                    errors.append(f"Unpublished link: {name} -> {relative}")
    print(
        json.dumps(
            {"files_checked": len(files), "errors": errors},
            ensure_ascii=False,
            indent=2,
        )
    )
    raise SystemExit(bool(errors))


if __name__ == "__main__":
    main()

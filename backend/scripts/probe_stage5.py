"""Real configured provider/transport probe; never prints keys or reasoning."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.external import GitHubReader, TavilySearch, read_page
from app.providers.call_options import Execution


def main():
    results = {"type": "real_provider_transport_probe", "results": {}}
    operations = {
        "tavily": lambda: TavilySearch().search(
            "Python asyncio documentation", Execution.start(35)
        ),
        "github": lambda: GitHubReader("public-probe", "octocat/Hello-World").commit(
            "master", Execution.start(35)
        ),
        "web": lambda: read_page(
            "https://docs.python.org/3/library/asyncio.html", Execution.start(35)
        ),
    }
    for name, operation in operations.items():
        try:
            result = operation()
            if name == "tavily":
                result = {"count": len(result), "urls": [r["url"] for r in result]}
            elif name == "github":
                result = {"sha": result["sha"]}
            else:
                result = {
                    k: result[k] for k in ("url", "title", "read_status", "truncated")
                }
            results["results"][name] = {"ok": True, "metadata": result}
        except Exception as exc:
            results["results"][name] = {
                "ok": False,
                "type": type(exc).__name__,
                "code": getattr(exc, "code", "network_error"),
            }
    output = Path("../docs/stage5/probes")
    output.mkdir(parents=True, exist_ok=True)
    path = output / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6] + ".json"
    )
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path)
    print({name: item["ok"] for name, item in results["results"].items()})


if __name__ == "__main__":
    main()

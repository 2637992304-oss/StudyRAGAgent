"""Actual-network test app. All model responses are deterministic synthetic data."""

import json
import os
from uuid import uuid4

import uvicorn

from app.api import chat
from app.main import app
from app.memory import text_conflicts, text_organize
from app.providers.llm import Delta

if (
    os.environ.get("STAGE7_VALIDATION_RUN") != "text-20261104-a"
    or "/text_browser" not in os.environ["DATABASE_URL"]
):
    raise RuntimeError("identified_browser_database_required")


class Provider:
    def stream(self, messages, tools=None, **kwargs):
        if kwargs["options"].purpose == "memory_conflict":
            yield Delta('{"conflicts":[]}', finish_reason="stop")
            return
        if kwargs["options"].purpose == "memory_organize":
            yield Delta(
                json.dumps(
                    {
                        "items": [
                            {"content": "数学推导详细，其他简短。"},
                            {"content": "先给直觉，再给定义。"},
                        ]
                    }
                ),
                finish_reason="stop",
            )
            return
        if kwargs["options"].purpose == "memory_suggestion":
            data = json.loads(messages[-1]["content"])
            sources = data.get("source_messages", [])[-3:]
            targets = [
                item
                for item in data.get("items", [])
                if item["content"] == "默认回答简短。"
            ]
            if (
                targets
                and len(sources) == 3
                and all("逐行展开" in row["text"] for row in sources)
            ):
                target = targets[0]
                yield Delta(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": str(uuid4()),
                            "function": {
                                "name": "propose_memory",
                                "arguments": json.dumps(
                                    {
                                        "content": "以后讲解逐行展开。",
                                        "id": target["id"],
                                        "version": target["version"],
                                        "source_ids": [row["id"] for row in sources],
                                    }
                                ),
                            },
                        }
                    ],
                    finish_reason="tool_calls",
                )
                return
            for phrase in ("一步一步", "生活例子"):
                if len(sources) == 3 and all(phrase in row["text"] for row in sources):
                    yield Delta(
                        tool_calls=[
                            {
                                "index": 0,
                                "id": str(uuid4()),
                                "function": {
                                    "name": "propose_memory",
                                    "arguments": json.dumps(
                                        {
                                            "content": "以后使用" + phrase + "讲解。",
                                            "source_ids": [
                                                row["id"] for row in sources
                                            ],
                                            "use_mode": "always",
                                        }
                                    ),
                                },
                            }
                        ],
                        finish_reason="tool_calls",
                    )
                    return
            yield Delta("SKIP", finish_reason="stop")
            return
        if kwargs["options"].purpose == "memory_action":
            yield Delta("SKIP", finish_reason="stop")
            return
        last = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        if isinstance(last, list):
            last = "".join(
                part.get("text", "") for part in last if part.get("type") == "text"
            )
        if isinstance(last, str) and last.startswith("记住："):
            yield Delta(
                tool_calls=[
                    {
                        "index": 0,
                        "id": str(uuid4()),
                        "function": {
                            "name": "save_memory",
                            "arguments": json.dumps(
                                {
                                    "content": last.removeprefix("记住："),
                                    "use_mode": "always",
                                    "memory_only": True,
                                }
                            ),
                        },
                    }
                ],
                finish_reason="tool_calls",
            )
            return
        yield Delta("请通过记忆管理入口查看保存结果。", finish_reason="stop")


chat.get_llm_provider = Provider
text_organize.get_llm_provider = Provider
text_conflicts.get_llm_provider = Provider


@app.get("/api/v1/_text_identity")
def identity():
    return {"id": "text-20261104-a", "database": "text_browser", "model": "scripted"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["TEXT_API_PORT"]))

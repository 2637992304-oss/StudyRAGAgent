"""Isolated real HTTP fixture; deterministic provider, no external requests."""

import json
import os

import uvicorn

from app.api import chat
from app.core.config import get_settings
from app.main import app
from app.providers.llm import Delta

if not os.environ.get("STAGE7_VALIDATION_RUN"):
    raise RuntimeError("isolated_runner_required")


class Provider:
    def stream(self, messages, **kwargs):
        users = [m for m in messages if m["role"] == "user"]
        question = users[-1]["content"]
        if isinstance(question, list):
            question = "".join(p.get("text", "") for p in question)
        if question == "以后这门课都用Python举例":
            args = {
                "key": "code_language",
                "value": "Python",
                "intent_text": question,
                "memory_only": True,
            }
            yield Delta(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "memory-save",
                        "function": {
                            "name": "save_preference",
                            "arguments": json.dumps(args),
                        },
                    }
                ],
                finish_reason="tool_calls",
            )
        elif question == "用Python举例" and messages[-1]["role"] != "tool":
            context = next(
                (
                    m["content"]
                    for m in users
                    if isinstance(m["content"], str)
                    and m["content"].startswith("历史记忆（")
                ),
                "",
            )
            data = json.loads(context.split("\n", 1)[1]) if context else {}
            ids = [
                m["id"]
                for m in data.get("source_messages", [])
                if m["text"] == question
            ]
            if len(ids) >= 3:
                args = {
                    "key": "code_language",
                    "value": "Python",
                    "source_ids": ids[-3:],
                }
                yield Delta(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "memory-propose",
                            "function": {
                                "name": "propose_preference",
                                "arguments": json.dumps(args),
                            },
                        }
                    ],
                    finish_reason="tool_calls",
                )
            else:
                yield Delta("本题使用Python示例。", finish_reason="stop")
        else:
            yield Delta(
                "已完成本次示例，长期偏好仅在明确保存或确认后生效。",
                finish_reason="stop",
            )


chat.get_llm_provider = Provider
for name in (
    "memory_preferences_enabled",
    "memory_suggestions_enabled",
    "memory_summary_enabled",
):
    setattr(get_settings(), name, True)


@app.get("/api/v1/_stage7_identity")
def identity():
    return {"id": os.environ["STAGE7_VALIDATION_RUN"]}


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ["STAGE7_API_PORT"]),
        log_level="warning",
    )

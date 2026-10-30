# ruff: noqa: E402 -- fixture guard must precede app imports.
"""Isolated wire/browser fixture, never a production entry point."""

import json
import os

assert os.environ.get("OPT56_VALIDATION") == "1"

from app.api import chat
from app.main import app  # noqa: F401 -- ASGI entry


@app.get("/api/v1/validation-identity")
def validation_identity():
    return {"run_id": os.environ["OPT34_RUN_ID"]}


from app.providers.llm import Delta


class Provider:
    def stream(self, messages, tools=None, **kwargs):
        purpose = kwargs["options"].purpose
        if purpose == "citation_check":
            yield Delta(
                '{"supported":true}',
                input_tokens=5,
                output_tokens=2,
                finish_reason="stop",
            )
            return
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        if not tool_messages:
            yield Delta(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "fixture-search",
                        "function": {
                            "name": "search_course_materials",
                            "arguments": json.dumps({"query": "自制验收"}),
                        },
                    }
                ],
                input_tokens=5,
                output_tokens=2,
                finish_reason="tool_calls",
            )
            return
        sources = json.loads(tool_messages[-1]["content"]).get("sources", [])
        answer = (
            sources[0]["text"] + f" [{sources[0]['label']}]"
            if sources
            else "【限制】自制资料证据不足。"
        )
        yield Delta(answer, input_tokens=5, output_tokens=2, finish_reason="stop")


chat.embed_texts = lambda texts: [[1.0] + [0.0] * 511 for _ in texts]
chat.get_llm_provider = lambda: Provider()

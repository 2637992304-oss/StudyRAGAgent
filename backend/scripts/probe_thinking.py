"""Development protocol probe, self-authored data, no private reasoning output."""

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.agent import TOOL
from app.providers.call_options import Execution, profile
from app.providers.llm import call_observer, complete_turn, get_llm_provider


def main():
    output = Path("../docs/stage4_thinking/development")
    output.mkdir(parents=True, exist_ok=True)
    name = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results, wire = [], []
    token = call_observer.set(lambda record: wire.append(record))
    messages = [
        {
            "role": "system",
            "content": (
                "测试学习助手。按用户要求检索，只使用提供的search_course_materials工具。"
                "本测试资料：ALPHA票价7元，BETA票价11元。工具返回完整即简短回答。"
            ),
        }
    ]
    try:
        for thinking, query in [
            (True, "分别检索ALPHA票价和BETA票价，必须执行两次不同的查询，再计算合计。"),
            (True, "重新检索BETA的票价，再用刚才合计减去它。"),
            (False, "不需检索，用一句话总结刚才的计算结果。"),
            (True, "重新检索ALPHA价格并与之前BETA相加核对。"),
        ]:
            messages.append({"role": "user", "content": query})
            started, calls, rounds = time.monotonic(), 0, 0
            execution = Execution.start(360)
            try:
                while rounds < 4:
                    turn = complete_turn(
                        get_llm_provider(),
                        messages,
                        [TOOL],
                        options=profile("agent", thinking),
                        execution=execution,
                    )
                    messages.append(turn.message)
                    rounds += 1
                    if not turn.message.get("tool_calls"):
                        break
                    for call in turn.message["tool_calls"]:
                        query_value = json.loads(call["function"]["arguments"])["query"]
                        # Real local fixture tool; calls came from the provider.
                        sources = [
                            f"{word}票价{price}元"
                            for word, price in [("ALPHA", 7), ("BETA", 11)]
                            if word.casefold() in query_value.casefold()
                        ]
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call["id"],
                                "content": json.dumps(
                                    {"sources": sources}, ensure_ascii=False
                                ),
                            }
                        )
                        calls += 1
                results.append(
                    {
                        "thinking": thinking,
                        "status": "ok",
                        "calls": calls,
                        "rounds": rounds,
                        "answer": turn.message.get("content"),
                        "reasoning_received": bool(
                            turn.message.get("reasoning_content")
                        ),
                        "input_tokens": turn.input_tokens,
                        "output_tokens": turn.output_tokens,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "thinking": thinking,
                        "status": "error",
                        "type": type(exc).__name__,
                        "http_status": exc.response.status_code
                        if isinstance(exc, httpx.HTTPStatusError)
                        else None,
                    }
                )
                break
            print(thinking, results[-1]["status"], "calls", calls, flush=True)
    finally:
        call_observer.reset(token)
        (output / f"{name}.json").write_text(
            json.dumps(
                {"results": results, "wire": wire}, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()

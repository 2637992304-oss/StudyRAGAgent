"""Repeatable non-sensitive provider probe. Never prints configuration secrets."""

import io
import json
from pathlib import Path

from PIL import Image, ImageDraw

from app.agent import TOOL
from app.providers.llm import complete_turn, get_llm_provider
from app.providers.vision import image_block


def main():
    output = Path("../docs/stage4")
    output.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (700, 220), "white")
    draw = ImageDraw.Draw(im)
    draw.text((35, 40), "Solve: 2x + 3 = 11", fill="black", font_size=42)
    im.save(output / "provider_sample.png")
    buffer = io.BytesIO()
    im.save(buffer, format="PNG")
    provider = get_llm_provider()
    results = []
    for name, messages, tools in [
        (
            "image",
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "读取图片题目并求x，简短回答。"},
                        image_block(buffer.getvalue(), "image/png"),
                    ],
                }
            ],
            None,
        ),
        (
            "tool",
            [
                {
                    "role": "user",
                    "content": (
                        "请调用 search_course_materials "
                        "检索课程中的递归定义，不要凭常识回答。"
                    ),
                }
            ],
            [TOOL],
        ),
        (
            "image_tool",
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "严格根据课程资料讲解图片题目，"
                                "先调用search_course_materials检索有关知识点。"
                            ),
                        },
                        image_block(buffer.getvalue(), "image/png"),
                    ],
                }
            ],
            [TOOL],
        ),
    ]:
        try:
            turn = complete_turn(provider, messages, tools)
            results.append(
                {
                    "name": name,
                    "status": "ok",
                    "message": turn.message,
                    "input_tokens": turn.input_tokens,
                    "output_tokens": turn.output_tokens,
                }
            )
        except Exception as exc:
            import httpx

            results.append(
                {
                    "name": name,
                    "status": "error",
                    "exception": type(exc).__name__,
                    "http_status": exc.response.status_code
                    if isinstance(exc, httpx.HTTPStatusError)
                    else None,
                }
            )
        print(name, results[-1]["status"], flush=True)
    (output / "provider_probe.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()

"""Freeze NEW self-authored addendum acceptance inputs before any answers exist."""
# ruff: noqa: E501

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2] / "docs/stage4_thinking/fixtures"


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    if (ROOT / "manifest.json").exists():
        raise SystemExit("Already frozen; do not overwrite expected results")
    for filename, text in {
        "alpha.md": "# ALPHA实验手册\nALPHA实验冷却阶段为9分钟。ALPHA实验复检阶段为4分钟。两个阶段按顺序执行，不重叠。\n",
        "beta.md": "# BETA实验手册\nBETA实验的温度上限为37摄氏度。超过37摄氏度必须隔离样本。\n",
    }.items():
        (ROOT / filename).write_text(text, encoding="utf-8")
    image = Image.new("RGB", (800, 280), "white")
    draw = ImageDraw.Draw(image)
    draw.text((35, 35), "Equation: 7x - 8 = 34", fill="black", font_size=44)
    draw.text((35, 125), "Find x. Show the key steps.", fill="black", font_size=30)
    image.save(ROOT / "equation.png")
    cases = [
        {
            "id": "fact",
            "prompt": "不用检索，17乘6等于多少？简短回答。",
            "reference": "102",
            "numeric": "102",
            "tools": "none",
        },
        {
            "id": "derivation",
            "prompt": "从1到9中选三个不同整数，不考虑顺序，任意两个所选整数都不相邻。共有多少种选法？给出可核对的推导，不需要课程资料。",
            "reference": "35。排序a<b<c，映射a,b-1,c-2为1..7中任取3个，C(7,3)=35。",
            "numeric": "35",
            "tools": "none",
        },
        {
            "id": "wrong_work",
            "prompt": "我解3(x-2)=12时写成3x-2=12，所以x=14/3。请指出具体错误并正确讲解，不需要课程资料。",
            "reference": "分配律错误，3也必须乘-2，应为3x-6=12，x=6；能指出用户给定的真实错误。",
            "tools": "none",
        },
        {
            "id": "strict",
            "prompt": "严格根据课程资料，ALPHA实验的冷却和复检共需多少分钟？说明计算依据并引用。",
            "reference": "冷却9分钟，复检4分钟，依次不重叠，合计13分钟，引用ALPHA真实资料。",
            "numeric": "13",
            "tools": "rag",
        },
        {
            "id": "image",
            "prompt": "请读图并讲解这道题，区分图片条件与推导，可以用通用知识，不必检索。",
            "image": "equation.png",
            "reference": "图片是7x-8=34，移项7x=42，两边除7，x=6。",
            "tools": "none",
        },
        {
            "id": "multi_rag",
            "prompt": "严格根据课程资料，先检索ALPHA的冷却时间，再另行检索BETA的温度上限。请分别查询后再回答，分别引用，不要把两个请求合并成一次查询。",
            "reference": "ALPHA冷却9分钟；BETA温度上限37摄氏度。分别真实检索并引用对应证据。",
            "tools": "multiple",
        },
    ]
    manifest = {
        "license": "CC0；本次执行代理自制，无私人资料",
        "split": "final_acceptance_not_development",
        "rubric": {
            "correctness": "0错误/1部分/2正确",
            "explanation": "0缺失或误导/1部分/2完整且符合简短或步骤要求",
            "citations": "资料事实需当前有效且直接支持的引用；通用题不要求课程引用",
            "uncertainty": "记录歧义与AI评审异常，不将输出长度作为质量",
        },
        "cases": cases,
        "files": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in ROOT.iterdir()
            if p.is_file()
        },
    }
    (ROOT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(ROOT)


if __name__ == "__main__":
    main()

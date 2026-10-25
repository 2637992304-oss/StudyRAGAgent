"""Fresh held-out inputs after development failure fixes; never overwrite."""

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw


def main():
    root = Path(__file__).resolve().parents[2] / "docs/stage4_thinking"
    target = root / "fixtures_final"
    target.mkdir(parents=True, exist_ok=True)
    if (target / "manifest.json").exists():
        raise SystemExit("Final sample is already frozen")
    old = json.loads((root / "fixtures/manifest.json").read_text(encoding="utf-8"))
    for name in ["alpha.md", "beta.md"]:
        (target / name).write_bytes((root / "fixtures" / name).read_bytes())
    im = Image.new("RGB", (800, 280), "white")
    draw = ImageDraw.Draw(im)
    draw.text((35, 35), "Equation: 5x + 9 = 44", fill="black", font_size=44)
    draw.text((35, 125), "Find x. Show the key steps.", fill="black", font_size=30)
    im.save(target / "equation.png")
    cases = [
        ("fact", "不用检索，23乘7等于多少？简短回答。", "161", "161"),
        (
            "derivation",
            "从1到11中选4个不同整数，不计顺序且两两不相邻。"
            "请给出选法数和可核对的推导，不需课程资料。",
            "70；排序a<b<c<d映射a,b-1,c-2,d-3到1..8中任选4个，C(8,4)=70。",
            "70",
        ),
        (
            "wrong_work",
            "我解4(x-3)=20，写成4x-3=20，得到x=23/4。"
            "请指出错在哪一步并正确解答，不用课程资料。",
            "漏乘-3，正确为4x-12=20，因此x=8，指出分配律错误。",
            "8",
        ),
        (
            "strict",
            "严格根据课程资料，ALPHA的冷却比复检多几分钟？说明依据并引用。",
            "冷却9分钟，复检4分钟，差5分钟，引用ALPHA真实资料。",
            "5",
        ),
        (
            "image",
            "请依据图片条件讲解这道题，允许通用推导，不需检索。",
            "图中5x+9=44，5x=35，x=7，区分题目条件与推导。",
            "7",
        ),
        (
            "multi_rag",
            "严格按课程资料，先单独查询ALPHA复检时长，"
            "再单独查询BETA温度上限。需要分别调用检索并分别引用。",
            "ALPHA复检4分钟；BETA温度上限37摄氏度；分别检索和引用。",
            None,
        ),
    ]
    old["cases"] = [
        {
            "id": name,
            "prompt": prompt,
            "reference": ref,
            "numeric": number,
            "tools": "multiple"
            if name == "multi_rag"
            else "rag"
            if name == "strict"
            else "none",
            **({"image": "equation.png"} if name == "image" else {}),
        }
        for name, prompt, ref, number in cases
    ]
    old["split"] = "fresh_final_after_development_fixes"
    old["image_solution"] = "7"
    old["files"] = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in target.iterdir()
        if p.is_file()
    }
    (target / "manifest.json").write_text(
        json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(target)


if __name__ == "__main__":
    main()

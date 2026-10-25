"""Self-authored image fixtures and immutable expected rubrics, no external media."""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2] / "docs/stage4/fixtures"


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 32)

    def image(name, lines):
        im = Image.new("RGB", (850, 360), "white")
        d = ImageDraw.Draw(im)
        for index, line in enumerate(lines):
            d.text((30, 30 + index * 65), line, font=font, fill="black")
        im.save(ROOT / name)
        return im

    image("equation.png", ["Solve: 3x + 2 = 20"])
    image("wrong.png", ["Solve: 3x + 2 = 20", "My work: 3x = 22", "x = 22/3"])
    image(
        "table.webp",
        [
            "Item       Count",
            "A             9",
            "B             15",
            "Question: total count?",
        ],
    )
    triangle = image(
        "triangle.jpg", ["Right triangle ABC", "AB = 6, BC = 4, angle B = 90 degrees"]
    )
    d = ImageDraw.Draw(triangle)
    d.line([(100, 300), (450, 300), (450, 180), (100, 300)], fill="black", width=4)
    triangle.save(ROOT / "triangle.jpg")
    image(
        "missing.png",
        ["Triangle ABC. Find its area.", "No side lengths or angles provided."],
    )
    blur = image("blur.png", ["Symbol and conditions unavailable"])
    blur.filter(ImageFilter.GaussianBlur(35)).save(ROOT / "blur.png")
    # Pen-stroke numerals and symbols, genuinely drawn rather than font OCR.
    im = Image.new("RGB", (600, 220), "white")
    d = ImageDraw.Draw(im)
    for points in [
        [(65, 55), (125, 55), (65, 160)],
        [(190, 80), (190, 145)],
        [(160, 113), (220, 113)],
        [
            (275, 65),
            (330, 65),
            (290, 105),
            (328, 110),
            (335, 135),
            (320, 160),
            (275, 155),
        ],
        [(385, 100), (440, 100)],
        [(385, 127), (440, 127)],
    ]:
        d.line(points, fill=(30, 45, 90), width=5)
    im.save(ROOT / "handwritten.png")
    image(
        "notes.png",
        [
            "Cobalt calibration note",
            "The calibration threshold is 17.",
            "This is an experimental constant, not a general rule.",
        ],
    )
    cases = [
        {
            "id": "direct",
            "image": None,
            "prompt": "你好，请简短打个招呼。",
            "rubric": "正常问候，不需要检索。",
        },
        {
            "id": "equation",
            "image": "equation.png",
            "prompt": "讲解这道题，并给出最终答案。",
            "rubric": "准确读取3x+2=20，解得x=6。",
        },
        {
            "id": "wrong",
            "image": "wrong.png",
            "prompt": "指出我的作答哪一步错了，给出正确解法。",
            "rubric": "指出移项符号错误，3x应为18而非22，x=6。",
        },
        {
            "id": "hint",
            "image": "equation.png",
            "prompt": "只给第一步提示，不要告诉我最终答案。",
            "rubric": "提示两边减2，不透露x=6。",
        },
        {
            "id": "table",
            "image": "table.webp",
            "prompt": "读取表格并求总数量。",
            "rubric": "读取A=9、B=15，总数24。",
        },
        {
            "id": "triangle",
            "image": "triangle.jpg",
            "prompt": "求图片中三角形的面积。",
            "rubric": "直角边6和4，面积12。",
        },
        {
            "id": "handwritten",
            "image": "handwritten.png",
            "prompt": "读取手写算式并计算。",
            "rubric": "算式7+5，结果12；识别或计算失败需记录。",
        },
        {
            "id": "blur",
            "image": "blur.png",
            "prompt": "求出图中题目的答案。",
            "rubric": "指出模糊无法识别，要求清晰图片，不猜造题干。",
        },
        {
            "id": "missing",
            "image": "missing.png",
            "prompt": "计算三角形面积。",
            "rubric": "缺边长或高等条件，要求补充，不给确定面积。",
        },
    ]
    path = ROOT / "manifest.json"
    if path.exists():
        assert json.loads(path.read_text(encoding="utf-8")) == cases, (
            "Frozen manifest changed"
        )
    else:
        path.write_text(
            json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (ROOT / "LICENSE.txt").write_text(
        (
            "All images and cases self-author"
            "ed for StudyRAGAgent Stage 4, CC"
            "0. Pen-stroke image is drawn wit"
            "h line segments. No user private"
            " material. provider_sample.png i"
            "s a separate development probe.\n"
        ),
        encoding="utf-8",
    )
    print("Fixtures ready:", len(cases))


if __name__ == "__main__":
    main()

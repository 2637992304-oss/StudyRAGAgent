"""Prepare two CC0 layout fixtures; probe with the project's real PDF/OCR stack.

Preparation uses optional reportlab (bundled artifact runtime is sufficient).
Probe uses installed pypdf/RapidOCR/pypdfium2, no stand-in OCR or extracted text.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path


def prepare(root):
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas

    font_path = "C:/Windows/Fonts/simhei.ttf"
    pdfmetrics.registerFont(TTFont("ChineseFixture", font_path))
    root.mkdir(parents=True, exist_ok=True)
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=(600, 800), invariant=1)
    canvas.setFont("ChineseFixture", 18)
    canvas.drawString(40, 740, "混合文档：第一页为正常文字层")
    canvas.drawString(40, 700, "资料编号：MIXED-2026")
    canvas.showPage()
    image = Image.new("RGB", (1400, 1000), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(font_path, 48)
    draw.text((70, 100), "混合扫描页说明", font=font, fill="black")
    draw.text((70, 210), "缓存 TTL 为 47 秒。", font=font, fill="black")
    draw.text((70, 320), "超时后必须重新验证缓存。", font=font, fill="black")
    from reportlab.lib.utils import ImageReader

    canvas.drawImage(ImageReader(image), 30, 240, width=540, height=385.7)
    canvas.showPage()
    canvas.save()
    (root / "mixed_scan.pdf").write_bytes(output.getvalue())

    output = io.BytesIO()
    canvas = Canvas(output, pagesize=(600, 800), invariant=1)
    canvas.setFont("ChineseFixture", 17)
    canvas.drawString(40, 750, "双栏资料、表格与公式对照")
    canvas.setFont("ChineseFixture", 12)
    canvas.drawString(40, 690, "左栏：甲课程策略")
    canvas.drawString(40, 660, "甲课程最大重试次数为 3。")
    canvas.drawString(320, 690, "右栏：乙课程策略")
    canvas.drawString(320, 660, "乙课程最大重试次数为 7。")
    canvas.drawString(40, 560, "配置表（表头不计数据行）")
    for y in [530, 495, 460, 425]:
        canvas.line(40, y, 550, y)
    for x in [40, 290, 550]:
        canvas.line(x, 425, x, 530)
    for y, left, right in [
        (507, "course_code", "retry_limit"),
        (472, "A11", "3"),
        (437, "B22", "7"),
    ]:
        canvas.drawString(55, y, left)
        canvas.drawString(320, y, right)
    canvas.drawString(40, 360, "等差数列公式：")
    canvas.setFont("Helvetica", 13)
    canvas.drawString(40, 330, "S_n = n * (a_1 + a_n) / 2")
    canvas.showPage()
    canvas.save()
    (root / "complex_layout.pdf").write_bytes(output.getvalue())


def probe(root, output):
    import pypdfium2

    from app.ingestion.parser import make_chunks, parse_document

    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for filename, checks in [
        ("mixed_scan.pdf", [(1, "MIXED-2026"), (2, "47"), (2, "TTL")]),
        ("complex_layout.pdf", [(1, "A11"), (1, "B22"), (1, "S_n"), (1, "a_n")]),
    ]:
        content = (root / filename).read_bytes()
        row = dict(
            source=filename,
            source_sha256=hashlib.sha256(content).hexdigest(),
            license="CC0-1.0",
            semantic_review="pending_human_review",
            expected=[dict(page=p, anchor=a) for p, a in checks],
        )
        try:
            parts = parse_document(filename, content)
            chunks = make_chunks(parts, 900, 150)
            row.update(
                status="ok",
                pages=[dict(page=p.page, text=p.text) for p in parts],
                chunks=chunks,
            )
            row["missing"] = [
                dict(page=n, anchor=a)
                for n, a in checks
                if not any(p.page == n and a in p.text for p in parts)
            ]
            if row["missing"]:
                row["status"] = "failed"
        except Exception as exc:
            row.update(status="error", error_type=type(exc).__name__)
        pdf = pypdfium2.PdfDocument(content)
        for i in range(len(pdf)):
            pdf[i].render(scale=1.2).to_pil().save(
                output / f"{Path(filename).stem}_p{i + 1}.png"
            )
        rows.append(row)
    (output / "parse_quality.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    documents = [
        dict(
            key=key,
            file=file,
            owner="a",
            course="main",
            status="ready",
            license="CC0-1.0",
            authored_original_facts=facts,
        )
        for key, file, facts in [
            (
                "mixed",
                "mixed_scan.pdf",
                "第1页资料编号MIXED-2026。第2页缓存TTL为47秒，超时后必须重新验证缓存。",
            ),
            (
                "layout",
                "complex_layout.pdf",
                "第1页甲课程最大重试3次，乙课程最大重试7次。表中A11为3，B22为7，表头不计，共2条数据。公式S_n=n*(a_1+a_n)/2。",
            ),
        ]
    ]
    cases = []
    for i, (key, page, question, quote, answer) in enumerate(
        [
            (
                "mixed",
                2,
                "混合文档扫描页中缓存TTL是多少秒？",
                "缓存 TTL 为47 秒。",
                "47秒",
            ),
            (
                "mixed",
                2,
                "缓存超时后需要做什么？",
                "超时后必须重新验证缓存。",
                "重新验证缓存",
            ),
            (
                "layout",
                1,
                "双栏资料中甲课程和乙课程各允许重试多少次？",
                "甲课程最大重试次数为 3。",
                "甲3次，乙7次",
            ),
            ("layout", 1, "配置表里B22对应的retry_limit是多少？", "B22", "7"),
            (
                "layout",
                1,
                "等差数列求和公式中的除数是多少？",
                "S_n = n * (a_1 + a_n) / 2",
                "2",
            ),
        ]
    ):
        expected = [
            dict(
                unit=f"layout:{i}",
                document=key,
                page=page,
                quote=quote,
                subquestion="主问题",
            )
        ]
        if i == 2:
            expected.append(
                dict(
                    unit="layout:2b",
                    document=key,
                    page=page,
                    quote="乙课程最大重试次数为 7。",
                    subquestion="乙课程",
                )
            )
        cases.append(
            dict(
                id=f"layout{i}",
                split="holdout",
                categories=["mixed_scan" if key == "mixed" else "complex_layout"],
                question=question,
                answerable=True,
                scope=dict(owner="a", course="main", documents=[key]),
                expected=expected,
                reference_answer=answer,
                key_facts=[answer],
                annotation_status="programmatic_from_authored_pdf_pending_human_review",
                human_review="pending",
            )
        )
    cases.append(
        dict(
            id="layout_no_answer",
            split="holdout",
            categories=["no_answer", "hard_negative"],
            question="乙课程的重试间隔承诺是多少毫秒？",
            answerable=False,
            scope=dict(owner="a", course="main", documents=["layout"]),
            expected=[],
            reference_answer="当前所选资料中没有足够依据。",
            key_facts=[],
            annotation_status="programmatic_from_authored_pdf_pending_human_review",
            human_review="pending",
        )
    )
    (root.parent / "dataset.json").write_text(
        json.dumps(
            dict(
                version="stage3-layout-v1",
                license="CC0-1.0",
                documents=documents,
                cases=cases,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print([(r["source"], r["status"]) for r in rows])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument(
        "--sources", type=Path, default=Path("../docs/stage3_completion_layout/sources")
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("../docs/stage3_completion_runs/layout_probe"),
    )
    args = parser.parse_args()
    prepare(args.sources) if args.prepare else probe(args.sources, args.output)


if __name__ == "__main__":
    main()

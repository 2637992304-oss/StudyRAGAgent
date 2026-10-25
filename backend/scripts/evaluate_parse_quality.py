"""Generate self-authored table/formula/chart/scan PDFs and inspect actual parsing."""

import argparse
import hashlib
import importlib.metadata
import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from app.ingestion.parser import make_chunks, parse_document
from scripts.smoke_stage2 import pdf_bytes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    samples = [
        (
            "table",
            pdf_bytes(
                [
                    "course_id | title | dept_name | credits",
                    "CS101 | Intro | CS | 4",
                    "CS202 | Networks | CS | 3",
                    "MA101 | Calculus | Math | 4",
                ]
            ),
            ["course_id", "CS101", "CS202", "MA101"],
        ),
        (
            "formula",
            pdf_bytes(["Arithmetic series: S_n = n * [a_1 + a_n] / 2"]),
            ["S_n", "a_1", "a_n", "/ 2"],
        ),
    ]
    rows = []
    try:
        from PIL import Image, ImageDraw, ImageFont

        font = ImageFont.truetype("C:/Windows/Fonts/simhei.ttf", 48)
        for category, caption in [
            ("scan", "扫描样本：主键唯一标识记录"),
            ("chart", "图表：甲组 3 人，乙组 5 人"),
        ]:
            image = Image.new("RGB", (1400, 1000), "white")
            draw = ImageDraw.Draw(image)
            draw.text((80, 80), caption, font=font, fill="black")
            if category == "chart":
                draw.rectangle((200, 450, 400, 750), fill="gray")
                draw.rectangle((650, 250, 850, 750), fill="gray")
            buffer = io.BytesIO()
            image.save(buffer, format="PDF", resolution=150)
            samples.append(
                (
                    category,
                    buffer.getvalue(),
                    ["主键", "唯一"]
                    if category == "scan"
                    else ["甲组", "3", "乙组", "5"],
                )
            )
    except Exception as exc:
        rows.append(
            {
                "category": "scan_and_chart",
                "status": "skipped",
                "error_type": type(exc).__name__,
                "stage": "fixture_generation",
            }
        )
    for category, content, anchors in samples:
        filename = f"{category}.pdf"
        (args.output / filename).write_bytes(content)
        row = {
            "category": category,
            "source": filename,
            "page": 1,
            "expected": anchors,
            "provenance": "CC0 self-authored fixture",
            "semantic_review": "pending",
            "retrieval_and_generation": "not_evaluated_by_this_parser_probe",
            "timestamp": datetime.now(UTC).isoformat(),
            "source_sha256": hashlib.sha256(content).hexdigest(),
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
        row["package_versions"] = {}
        for name in ("pypdf", "pypdfium2", "rapidocr", "Pillow"):
            try:
                row["package_versions"][name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                row["package_versions"][name] = None
        try:
            parts = parse_document(filename, content)
            chunks = make_chunks(parts)
            extracted = " ".join(p.text for p in parts)

            def normalize(s):
                return re.sub(r"\s+", "", s).lower()

            lost = [
                anchor
                for anchor in anchors
                if normalize(anchor) not in normalize(extracted)
            ]
            split = [
                anchor
                for anchor in anchors
                if not any(
                    normalize(anchor) in normalize(chunk["text"]) for chunk in chunks
                )
            ]
            row.update(
                status="ok" if not lost and not split else "failed",
                extracted=extracted,
                missing_in_parse=lost,
                missing_in_chunks=split,
                stage="parse_or_ocr_loss"
                if lost
                else "chunk_loss"
                if split
                else "anchors_preserved",
            )
        except Exception as exc:
            row.update(
                status="error", stage="parse_or_ocr", error_type=type(exc).__name__
            )
        rows.append(row)
    (args.output / "parse_quality.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()

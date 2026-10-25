"""Check OCR with a small, locally generated image-only PDF."""

import io

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

from app.ingestion.parser import parse_document


def main() -> None:
    font = ImageFont.truetype("C:/Windows/Fonts/simhei.ttf", 68)
    image = Image.new("RGB", (1400, 1800), "white")
    ImageDraw.Draw(image).text((100, 160), "第一章 课程笔记", font=font, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PDF", resolution=150)
    content = buffer.getvalue()
    assert not (PdfReader(io.BytesIO(content)).pages[0].extract_text() or "").strip()
    parts = parse_document("scanned.pdf", content)
    assert any("课程笔记" in part.text for part in parts), parts
    assert all(part.page == 1 for part in parts), parts
    print("OCR passed:", [(part.page, part.text) for part in parts])


if __name__ == "__main__":
    main()

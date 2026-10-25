import io
import zipfile
from pathlib import Path

from fastapi import HTTPException

from app.core.config import get_settings

MIMES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}
DOCUMENT_TYPES = {"课件", "笔记", "试卷", "作业习题", "教材", "参考资料"}


def validate_upload(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in MIMES:
        raise HTTPException(
            400, "仅支持 PDF、DOCX、PPTX、TXT、Markdown、JPEG、PNG、WebP"
        )
    limit = get_settings().max_upload_mb * 1024 * 1024
    if not content or len(content) > limit:
        raise HTTPException(
            400, f"文件须非空且不超过 {get_settings().max_upload_mb} MB"
        )
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        from app.providers.vision import inspect_image

        return inspect_image(content, suffix)[0]
    if suffix == ".pdf" and not content.startswith(b"%PDF-"):
        raise HTTPException(400, "PDF 内容与扩展名不符")
    if suffix in {".docx", ".pptx"}:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = set(archive.namelist())
                expected = (
                    "word/document.xml" if suffix == ".docx" else "ppt/presentation.xml"
                )
                if "[Content_Types].xml" not in names or expected not in names:
                    raise ValueError("Office 内容结构无效")
                if (
                    len(names) > 2000
                    or sum(i.file_size for i in archive.infolist()) > limit * 10
                ):
                    raise ValueError("Office 压缩内容过大")
        except (zipfile.BadZipFile, ValueError) as exc:
            raise HTTPException(400, "Office 内容与扩展名不符或不安全") from exc
    if suffix in {".txt", ".md", ".markdown"}:
        try:
            decoded = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(400, "文本文件须为 UTF-8") from exc
        if "\x00" in decoded or not decoded.strip():
            raise HTTPException(400, "文本文件为空或含有无效字符")
    return MIMES[suffix]

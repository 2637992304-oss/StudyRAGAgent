"""Optional bounded adjacent *sources*, never anonymous appended evidence."""

from sqlalchemy import select

from app.db.models import Chunk, Document
from app.ingestion.parser import classify


def adjacent_sources(db, chat, rows, max_chars):
    result = list(rows)
    seen = {c.id for c, _ in result}
    used = sum(len(c.text) for c, _ in result)
    extra = 0
    for chunk, doc in rows:
        if doc.filename.lower().endswith(".pdf") or classify(chunk.text) != "body":
            continue
        for index in (chunk.chunk_index - 1, chunk.chunk_index + 1):
            if extra >= 2:
                return result
            row = db.execute(
                select(Chunk, Document)
                .join(Document, Chunk.document_id == Document.id)
                .where(
                    Chunk.document_id == chunk.document_id,
                    Chunk.chunk_index == index,
                    Chunk.owner_id == chat.owner_id,
                    Chunk.course_id == chat.course_id,
                    Document.owner_id == chat.owner_id,
                    Document.course_id == chat.course_id,
                    Document.status == "ready",
                    Chunk.page_start.is_(None),
                    Chunk.section_path == chunk.section_path,
                    Chunk.slide_no == chunk.slide_no,
                    *(
                        [Document.id.in_(chat.document_ids)]
                        if chat.document_ids is not None
                        else []
                    ),
                )
            ).first()
            if not row:
                continue
            candidate, _ = row
            if (
                candidate.id in seen
                or classify(candidate.text) != "body"
                or candidate.text.lstrip().startswith("#")
                or "\n" in candidate.text
                or "|" in candidate.text
                or len(candidate.text) > 900
                or used + len(candidate.text) > max_chars
            ):
                continue
            result.append(row)
            seen.add(candidate.id)
            used += len(candidate.text)
            extra += 1
    return result

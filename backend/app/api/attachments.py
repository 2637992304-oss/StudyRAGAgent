"""Session-scoped private attachments. Documents own independent file copies."""

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.chat import owned_session
from app.core.auth import current_user
from app.core.config import get_settings
from app.db.models import Attachment, Document, IngestionJob, Message, User
from app.db.session import get_db
from app.ingestion.validation import DOCUMENT_TYPES
from app.providers.local_storage import LocalStorage
from app.providers.vision import inspect_image
from app.workers.tasks import ingest_task

router = APIRouter(prefix="/api/v1")


def attachment_data(item: Attachment) -> dict:
    return {
        "id": item.id,
        "filename": item.filename,
        "mime_type": item.mime_type,
        "width": item.width,
        "height": item.height,
        "size_bytes": item.size_bytes,
        "message_id": item.message_id,
        "saved_document_id": item.saved_document_id,
        "file_url": f"/api/v1/chat/attachments/{item.id}/file",
    }


def owned_attachment(db, attachment_id, owner_id):
    item = db.scalar(
        select(Attachment)
        .where(Attachment.id == attachment_id, Attachment.owner_id == owner_id)
        .with_for_update()
    )
    if item is None:
        raise HTTPException(404, "附件不存在")
    owned_session(db, item.session_id, owner_id)
    return item


@router.post("/chat/sessions/{session_id}/attachments", status_code=201)
async def upload_attachment(
    session_id: str,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    owned_session(db, session_id, user.id)
    filename = Path(file.filename or "").name
    if not filename or len(filename) > 255:
        raise HTTPException(400, "文件名无效")
    content = await file.read(get_settings().max_upload_mb * 1024 * 1024 + 1)
    mime, width, height = inspect_image(content, Path(filename).suffix)
    key = uuid4()
    storage = LocalStorage(get_settings().storage_dir)
    storage.write(key, content)
    item = Attachment(
        id=str(uuid4()),
        owner_id=user.id,
        session_id=session_id,
        filename=filename,
        mime_type=mime,
        storage_key=str(key),
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        width=width,
        height=height,
    )
    try:
        db.add(item)
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(key)
        raise
    return attachment_data(item)


@router.get("/chat/sessions/{session_id}/attachments")
def list_attachments(
    session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    owned_session(db, session_id, user.id)
    return [
        attachment_data(a)
        for a in db.scalars(
            select(Attachment)
            .where(Attachment.session_id == session_id, Attachment.owner_id == user.id)
            .order_by(Attachment.created_at)
        )
    ]


@router.get("/chat/attachments/{attachment_id}/file")
def read_attachment(
    attachment_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    item = owned_attachment(db, attachment_id, user.id)
    try:
        content = LocalStorage(get_settings().storage_dir).read(UUID(item.storage_key))
    except FileNotFoundError as exc:
        raise HTTPException(404, "附件文件不存在") from exc
    return Response(
        content,
        media_type=item.mime_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/chat/attachments/{attachment_id}", status_code=204)
def remove_attachment(
    attachment_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    item = owned_attachment(db, attachment_id, user.id)
    if db.scalar(
        select(Message.id).where(
            Message.session_id == item.session_id, Message.status == "pending"
        )
    ):
        raise HTTPException(409, "请等待当前回答结束再移除附件")
    LocalStorage(get_settings().storage_dir).delete(UUID(item.storage_key))
    db.delete(item)
    db.commit()
    return Response(status_code=204)


class SaveInput(BaseModel):
    document_type: str = "参考资料"


@router.post("/chat/attachments/{attachment_id}/save", status_code=202)
def save_attachment(
    attachment_id: str,
    body: SaveInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    item = owned_attachment(db, attachment_id, user.id)
    chat = owned_session(db, item.session_id, user.id)
    if body.document_type not in DOCUMENT_TYPES:
        raise HTTPException(400, "资料类型无效")
    if item.saved_document_id and db.get(Document, item.saved_document_id):
        doc = db.get(Document, item.saved_document_id)
        return {
            "document_id": doc.id,
            "job_id": doc.active_job_id,
            "status": doc.status,
        }
    storage = LocalStorage(get_settings().storage_dir)
    key, doc_id, job_id = uuid4(), str(uuid4()), str(uuid4())
    try:
        content = storage.read(UUID(item.storage_key))
    except FileNotFoundError as exc:
        raise HTTPException(404, "附件文件不存在") from exc
    storage.write(key, content)
    doc = Document(
        id=doc_id,
        owner_id=user.id,
        course_id=chat.course_id,
        filename=item.filename,
        document_type=body.document_type,
        mime_type=item.mime_type,
        storage_key=str(key),
        sha256=item.sha256,
        size_bytes=item.size_bytes,
        status="queued",
        active_job_id=job_id,
    )
    job = IngestionJob(id=job_id, owner_id=user.id, document_id=doc_id)
    try:
        db.add_all([doc, job])
        db.flush()
        item.saved_document_id = doc_id
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(key)
        raise
    try:
        ingest_task.delay(job_id)
    except Exception:
        job.status, job.stage, job.error, doc.status = (
            "failed",
            "queue",
            "任务队列不可用，请重试",
            "failed",
        )
        db.commit()
    return {"document_id": doc_id, "job_id": job_id, "status": doc.status}


@router.delete("/chat/sessions/{session_id}", status_code=204)
def delete_session(
    session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    chat = owned_session(db, session_id, user.id)
    if db.scalar(
        select(Message.id).where(
            Message.session_id == session_id, Message.status == "pending"
        )
    ):
        raise HTTPException(409, "请先等待当前回答结束")
    items = db.scalars(
        select(Attachment).where(
            Attachment.session_id == session_id, Attachment.owner_id == user.id
        )
    ).all()
    storage = LocalStorage(get_settings().storage_dir)
    for item in items:
        storage.delete(UUID(item.storage_key))
        db.delete(item)
    db.flush()
    db.delete(chat)
    db.commit()
    return Response(status_code=204)


@router.delete("/chat/sessions/{session_id}/messages/{message_id}", status_code=204)
def delete_message(
    session_id: str,
    message_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    owned_session(db, session_id, user.id)
    message = db.scalar(
        select(Message).where(
            Message.id == message_id,
            Message.session_id == session_id,
            Message.owner_id == user.id,
        )
    )
    if message is None:
        raise HTTPException(404, "消息不存在")
    if db.scalar(
        select(Message.id).where(
            Message.session_id == session_id, Message.status == "pending"
        )
    ):
        raise HTTPException(409, "请等待当前回答结束")
    storage = LocalStorage(get_settings().storage_dir)
    for item in db.scalars(
        select(Attachment).where(
            Attachment.message_id == message_id, Attachment.owner_id == user.id
        )
    ):
        storage.delete(UUID(item.storage_key))
        db.delete(item)
    db.flush()
    db.delete(message)
    db.commit()
    return Response(status_code=204)

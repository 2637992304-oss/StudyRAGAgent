import hashlib
from pathlib import Path
from uuid import UUID, uuid4

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import create_token, current_user, hash_password, verify_password
from app.core.config import get_settings
from app.db.models import Chunk, Course, Document, IngestionJob, User
from app.db.session import get_db
from app.ingestion.validation import DOCUMENT_TYPES, validate_upload
from app.providers.embeddings import embed_texts
from app.providers.local_storage import LocalStorage
from app.workers.tasks import ingest_task

router = APIRouter(prefix="/api/v1")


class LoginInput(BaseModel):
    email: str
    password: str


class RegisterInput(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(min_length=15, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def validate_registration_email(cls, value):
        if not isinstance(value, str):
            return value
        email = value.strip().lower()
        try:
            # Local registration accepts reserved test domains, without DNS checks.
            validate_email(
                email,
                check_deliverability=False,
                test_environment=True,
                allow_smtputf8=False,
            )
        except EmailNotValidError as exc:
            raise ValueError("请输入有效的邮箱地址") from exc
        return email

    @field_validator("password")
    @classmethod
    def validate_registration_password(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("密码不能全部为空格")
        return value


class CourseInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class SearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)


def owned_course(db: Session, course_id: str, user_id: str) -> Course:
    course = db.scalar(
        select(Course).where(Course.id == course_id, Course.owner_id == user_id)
    )
    if course is None:
        raise HTTPException(404, "课程不存在")
    return course


def owned_document(db: Session, document_id: str, user_id: str) -> Document:
    doc = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.owner_id == user_id,
            Document.status != "deleting",
        )
    )
    if doc is None:
        raise HTTPException(404, "资料不存在")
    return doc


def document_data(doc: Document) -> dict:
    return {
        "id": doc.id,
        "course_id": doc.course_id,
        "filename": doc.filename,
        "document_type": doc.document_type,
        "mime_type": doc.mime_type,
        "size_bytes": doc.size_bytes,
        "status": doc.status,
        "job_id": doc.active_job_id,
        "created_at": doc.created_at,
    }


def job_data(job: IngestionJob) -> dict:
    return {
        "id": job.id,
        "document_id": job.document_id,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "error": job.error,
    }


@router.post("/auth/register", status_code=201)
def register(body: RegisterInput, db: Session = Depends(get_db)) -> dict:
    if db.scalar(select(User).where(User.email == body.email)) is not None:
        raise HTTPException(409, "该邮箱已注册，请直接登录")
    user = User(email=body.email, password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # The database unique constraint also covers simultaneous registrations.
        if db.scalar(select(User).where(User.email == body.email)) is not None:
            raise HTTPException(409, "该邮箱已注册，请直接登录") from exc
        raise
    return {"access_token": create_token(user.id), "token_type": "bearer"}


@router.post("/auth/login")
def login(body: LoginInput, db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "邮箱或密码错误")
    return {"access_token": create_token(user.id), "token_type": "bearer"}


@router.get("/me")
def me(user: User = Depends(current_user)) -> dict:
    return {"id": user.id, "email": user.email}


@router.get("/courses")
def list_courses(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[dict]:
    courses = db.scalars(
        select(Course).where(Course.owner_id == user.id).order_by(Course.created_at)
    ).all()
    return [{"id": course.id, "name": course.name} for course in courses]


@router.post("/courses", status_code=201)
def create_course(
    body: CourseInput, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    course = Course(id=str(uuid4()), owner_id=user.id, name=body.name.strip())
    if not course.name:
        raise HTTPException(400, "课程名称不能为空")
    db.add(course)
    db.commit()
    return {"id": course.id, "name": course.name}


@router.get("/courses/{course_id}/documents")
def list_documents(
    course_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[dict]:
    owned_course(db, course_id, user.id)
    docs = db.scalars(
        select(Document)
        .where(
            Document.course_id == course_id,
            Document.owner_id == user.id,
            Document.status != "deleting",
        )
        .order_by(Document.created_at.desc())
    ).all()
    return [document_data(doc) for doc in docs]


@router.post("/courses/{course_id}/documents", status_code=202)
async def upload_document(
    course_id: str,
    file: UploadFile = File(...),
    document_type: str = Form(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    owned_course(db, course_id, user.id)
    if document_type not in DOCUMENT_TYPES:
        raise HTTPException(400, "资料类型无效")
    filename = Path(file.filename or "").name
    if not filename or len(filename) > 255:
        raise HTTPException(400, "文件名无效")
    limit = get_settings().max_upload_mb * 1024 * 1024
    content = await file.read(limit + 1)
    mime = validate_upload(filename, content)
    storage_key = uuid4()
    storage = LocalStorage(get_settings().storage_dir)
    storage.write(storage_key, content)
    doc_id, job_id = str(uuid4()), str(uuid4())
    doc = Document(
        id=doc_id,
        owner_id=user.id,
        course_id=course_id,
        filename=filename,
        document_type=document_type,
        mime_type=mime,
        storage_key=str(storage_key),
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        status="queued",
        active_job_id=job_id,
    )
    job = IngestionJob(id=job_id, owner_id=user.id, document_id=doc_id)
    try:
        db.add_all([doc, job])
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(storage_key)
        raise
    try:
        ingest_task.delay(job_id)
    except Exception:
        job.status = "failed"
        job.stage = "queue"
        job.error = "任务队列不可用，请重试"
        doc.status = "failed"
        db.commit()
    return {"document_id": doc_id, "job_id": job_id, "status": doc.status}


@router.get("/documents/{document_id}")
def get_document(
    document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    return document_data(owned_document(db, document_id, user.id))


@router.get("/documents/{document_id}/file")
def get_original(
    document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    doc = owned_document(db, document_id, user.id)
    try:
        content = LocalStorage(get_settings().storage_dir).read(UUID(doc.storage_key))
    except FileNotFoundError as exc:
        raise HTTPException(404, "原文件不存在") from exc
    return Response(
        content,
        media_type=doc.mime_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/documents/{document_id}/chunks")
def list_chunks(
    document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[dict]:
    owned_document(db, document_id, user.id)
    chunks = db.scalars(
        select(Chunk)
        .where(Chunk.document_id == document_id, Chunk.owner_id == user.id)
        .order_by(Chunk.chunk_index)
        .limit(100)
    ).all()
    return [
        {
            "id": item.id,
            "text": item.text,
            "content_type": item.content_type,
            "section_path": item.section_path,
            "page_start": item.page_start,
            "slide_no": item.slide_no,
        }
        for item in chunks
    ]


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    job = db.scalar(
        select(IngestionJob).where(
            IngestionJob.id == job_id, IngestionJob.owner_id == user.id
        )
    )
    if job is None:
        raise HTTPException(404, "任务不存在")
    owned_document(db, job.document_id, user.id)
    return job_data(job)


@router.post("/documents/{document_id}/retry", status_code=202)
def retry_document(
    document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    doc = db.scalar(
        select(Document)
        .where(Document.id == document_id, Document.owner_id == user.id)
        .with_for_update()
    )
    if doc is None or doc.status == "deleting":
        raise HTTPException(404, "资料不存在")
    if doc.status != "failed":
        raise HTTPException(409, "仅失败资料可重试")
    job_id = str(uuid4())
    doc.active_job_id = job_id
    doc.status = "queued"
    db.add(IngestionJob(id=job_id, owner_id=user.id, document_id=doc.id))
    db.commit()
    try:
        ingest_task.delay(job_id)
    except Exception:
        job = db.get(IngestionJob, job_id)
        job.status = "failed"
        job.stage = "queue"
        job.error = "任务队列不可用，请重试"
        doc.status = "failed"
        db.commit()
    return {"document_id": doc.id, "job_id": job_id, "status": doc.status}


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(
    document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    doc = db.scalar(
        select(Document)
        .where(Document.id == document_id, Document.owner_id == user.id)
        .with_for_update()
    )
    if doc is None:
        raise HTTPException(404, "资料不存在")
    key = UUID(doc.storage_key)
    doc.status = "deleting"
    db.commit()
    try:
        LocalStorage(get_settings().storage_dir).delete(key)
    except OSError as exc:
        raise HTTPException(503, "原文件清理失败，请重试删除") from exc
    with db.begin():
        doc = db.scalar(
            select(Document)
            .where(Document.id == document_id, Document.owner_id == user.id)
            .with_for_update()
        )
        if doc:
            db.execute(delete(Chunk).where(Chunk.document_id == document_id))
            db.delete(doc)
    return Response(status_code=204)


@router.post("/courses/{course_id}/search")
def search_chunks(
    course_id: str,
    body: SearchInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    owned_course(db, course_id, user.id)
    vector = embed_texts([body.query])[0]
    distance = Chunk.embedding.cosine_distance(vector)
    rows = db.execute(
        select(Chunk, distance.label("distance"))
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.owner_id == user.id,
            Chunk.course_id == course_id,
            Document.owner_id == user.id,
            Document.status == "ready",
        )
        .order_by(distance)
        .limit(body.top_k)
    ).all()
    return [
        {
            "chunk_id": chunk.id,
            "document_id": chunk.document_id,
            "text": chunk.text,
            "page_start": chunk.page_start,
            "slide_no": chunk.slide_no,
            "distance": float(score),
        }
        for chunk, score in rows
    ]

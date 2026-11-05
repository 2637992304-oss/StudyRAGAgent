"""Free-text memory management; no model-selected ownership or confirmation."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import current_user
from app.core.config import get_settings
from app.db.models import (
    MemoryItem,
    MemoryItemRevision,
    MemoryItemSource,
    MemorySuggestion,
    Message,
    User,
)
from app.db.session import get_db
from app.memory import service as legacy
from app.memory import text_service as service
from app.memory.text_schema import TextMemoryInput, TextMemoryMutation

router = APIRouter(prefix="/api/v1/memory")


def text_mode():
    if get_settings().memory_engine not in {"text", "off"}:
        legacy.fail(409, "自由文本记忆未启用，当前操作不会保存")


class DeleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0)
    request_key: str = Field(min_length=1, max_length=100)


class AcceptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0)
    items: list[TextMemoryInput] = Field(min_length=1, max_length=8)
    request_key: str = Field(min_length=1, max_length=80)
    confirm_target_replacement: bool = False


class OrganizeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=12000)
    request_key: str = Field(min_length=1, max_length=80)


@router.post("/organize", dependencies=[Depends(text_mode)])
def organize(body: OrganizeInput, user: User = Depends(current_user)):
    from app.memory.text_organize import organize as organize_text

    if not body.content.strip() or any(
        0xD800 <= ord(c) <= 0xDFFF for c in body.content
    ):
        legacy.fail(422, "整理正文不能为空或包含无效Unicode字符")
    if (
        get_settings().memory_engine != "text"
        or not get_settings().memory_preferences_enabled
    ):
        legacy.fail(409, "长期记忆写入已关闭")
    return organize_text(user.id, body.content, body.request_key)


@router.post("/text-suggestions/{ident}/accept", dependencies=[Depends(text_mode)])
def accept(
    ident: str,
    body: AcceptInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    from app.memory.text_conflicts import check_edit
    from app.memory.text_graph import accept as accept_suggestion
    from app.memory.text_graph import suggestion_live

    card = db.get(MemorySuggestion, ident)
    if card and card.owner_id == user.id and card.status == "pending":
        if card.version != body.version or not suggestion_live(db, card):
            legacy.fail()
        check_edit(
            db,
            user.id,
            card,
            body.items,
            body.request_key,
            body.confirm_target_replacement,
        )

    op = accept_suggestion(
        db, user.id, ident, body.version, body.items, body.request_key
    )
    db.commit()
    return service.operation_data(db, op)


@router.post("/text-suggestions/{ident}/dismiss", dependencies=[Depends(text_mode)])
def dismiss(
    ident: str,
    version: int,
    suppress: bool = False,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    from app.memory.text_graph import dismiss as dismiss_suggestion
    from app.memory.text_graph import suggestion_data

    row = dismiss_suggestion(db, user.id, ident, version, suppress)
    db.commit()
    return suggestion_data(db, row)


@router.get("/capabilities")
def capabilities(user: User = Depends(current_user)):
    settings = get_settings()
    return {
        "engine": settings.memory_engine,
        "max_content_chars": settings.memory_content_max_chars,
        "schema_version": 2,
    }


@router.get("/items", dependencies=[Depends(text_mode)])
def items(
    course_id: str | None = None,
    q: str = Query(default="", max_length=200),
    include_inactive: bool = False,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    scope = legacy.scope_for(db, user.id, course_id)
    rows = [] if include_inactive else service.visible(db, user.id, course_id, q)
    if include_inactive:
        statement = select(MemoryItem).where(
            MemoryItem.owner_id == user.id,
            (MemoryItem.course_id.is_(None) | (MemoryItem.course_id == course_id))
            if course_id
            else MemoryItem.course_id.is_(None),
        )
        if q:
            statement = statement.where(
                MemoryItem.content.icontains(q, autoescape=True)
            )
        rows = list(
            db.scalars(
                statement.order_by(MemoryItem.updated_at.desc(), MemoryItem.id).limit(
                    501
                )
            )
        )
    public = []
    for item in rows[:500]:
        effective = item.status
        if item.status == "active":
            effective = (
                "active" if service.effective_time(item) else "expired_or_not_started"
            )
            if not service.sources_live(db, item):
                effective = "invalidated"
        public.append({**service.snapshot(item), "effective_status": effective})
    return {
        "items": public,
        "has_more": len(rows) > 500,
        "enabled": get_settings().memory_preferences_enabled
        and get_settings().memory_engine == "text",
        "settings": legacy.settings(db, user.id, scope),
        "global_settings": legacy.settings(db, user.id, "global"),
        "max_content_chars": get_settings().memory_content_max_chars,
        "suppressions": legacy.settings(db, user.id, scope).get("text_suppressed", []),
        "global_suppressions": legacy.settings(db, user.id, "global").get(
            "text_suppressed", []
        ),
    }


@router.delete("/text-suppressions", dependencies=[Depends(text_mode)])
def reset_suppressions(
    version: int,
    course_id: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    result = legacy.change_settings(
        db, user.id, course_id, version, {"text_suppressed": []}
    )
    db.commit()
    return result


@router.post("/items", dependencies=[Depends(text_mode)])
def create(
    body: TextMemoryMutation,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    data = TextMemoryInput.model_validate(
        {k: getattr(body, k) for k in TextMemoryInput.model_fields}
    )
    op = service.apply_manual_batch(
        db, user.id, [{"input": data, "version": body.version}], body.request_key
    )
    db.commit()
    return service.operation_data(db, op)


@router.post("/items-batch", dependencies=[Depends(text_mode)])
def create_batch(
    body: AcceptInput, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    op = service.apply_manual_batch(
        db, user.id, [{"input": value} for value in body.items], body.request_key
    )
    db.commit()
    return service.operation_data(db, op)


@router.put("/items/{ident}", dependencies=[Depends(text_mode)])
def update(
    ident: str,
    body: TextMemoryMutation,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    data = TextMemoryInput.model_validate(
        {k: getattr(body, k) for k in TextMemoryInput.model_fields}
    )
    op = service.apply_manual_batch(
        db,
        user.id,
        [{"id": ident, "version": body.version, "input": data}],
        body.request_key,
    )
    db.commit()
    return service.operation_data(db, op)


@router.delete("/items/{ident}", dependencies=[Depends(text_mode)])
def delete(
    ident: str,
    body: DeleteInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    op = service.delete_item(db, user.id, ident, body.version, body.request_key)
    db.commit()
    return service.operation_data(db, op)


@router.get("/items/{ident}", dependencies=[Depends(text_mode)])
def detail(
    ident: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    item = service.owned(db, user.id, ident)
    revisions = list(
        db.scalars(
            select(MemoryItemRevision)
            .where(MemoryItemRevision.item_id == ident)
            .order_by(MemoryItemRevision.version)
        )
    )
    source_views = []
    versions = {revision.id: revision.version for revision in revisions}
    for source in db.scalars(
        select(MemoryItemSource).where(MemoryItemSource.revision_id.in_(versions))
    ):
        view = {
            "kind": source.kind,
            "source_id": source.source_id,
            "version": versions[source.revision_id],
            "available": False,
        }
        if source.kind == "messages":
            row = db.get(Message, source.source_id, populate_existing=True)
            if (
                row
                and row.owner_id == user.id
                and row.status == "completed"
                and service.source_digest(row) == source.digest
            ):
                view.update(available=True, content=row.content)
        else:
            spec = {
                "kind": source.kind,
                "id": source.source_id,
                "digest": source.digest,
            }
            if service.dependencies_live(db, user.id, [spec]):
                row = db.get(service.DEPENDENCY_MODELS[source.kind], source.source_id)
                view.update(
                    available=True,
                    content=getattr(row, "text", None),
                    name=getattr(row, "filename", None),
                )
        source_views.append(view)
    return {
        "item": service.snapshot(item),
        "revisions": [
            {**r.data, "created_at": r.created_at.isoformat()} for r in revisions
        ],
        "sources": source_views,
    }


@router.post("/text-operations/{ident}/undo", dependencies=[Depends(text_mode)])
def undo(ident: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    op = service.undo(db, user.id, ident)
    db.commit()
    return service.operation_data(db, op)

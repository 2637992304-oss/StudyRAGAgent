"""Memory APIs share the exact mutation service used by graph tools."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.chat import owned_session
from app.core.auth import current_user
from app.core.config import get_settings
from app.db.models import MemoryPreference, MemorySuggestion, User
from app.db.session import get_db
from app.memory import service as m
from app.memory import summary

router = APIRouter(prefix="/api/v1")


class Version(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0)


class PreferenceInput(Version):
    course_id: str | None = None
    key: str
    value: str
    request_key: str = Field(min_length=1, max_length=100)


class SettingsInput(Version):
    course_id: str | None = None
    use: StrictBool | None = None
    write: StrictBool | None = None
    suggest: StrictBool | None = None
    suppressed: list[str] | None = Field(default=None, max_length=4)


class AcceptInput(Version):
    preference_version: int = Field(ge=0)
    course_id: str | None = None
    value: str
    request_key: str = Field(min_length=1, max_length=100)


class DismissInput(Version):
    suppress: StrictBool = False


class SummaryInput(Version):
    enabled: StrictBool


@router.get("/memory/preferences")
def preferences(
    course_id: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    return m.preferences(db, user.id, course_id)


@router.put("/memory/preferences")
def save(
    body: PreferenceInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if get_settings().memory_engine != "legacy":
        m.fail(409, "请升级客户端，使用自由文本长期记忆接口")
    result = m.mutate(
        db,
        user.id,
        scope=m.scope_for(db, user.id, body.course_id),
        key=body.key,
        value=body.value,
        version=body.version,
        request_key="manual:" + body.request_key,
    )
    db.commit()
    return result


@router.delete("/memory/preferences/{ident}")
def delete(
    ident: str,
    version: int = Query(ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if get_settings().memory_engine != "legacy":
        m.fail(409, "请升级客户端，使用自由文本长期记忆接口")
    row = db.scalar(
        select(MemoryPreference).where(
            MemoryPreference.id == ident, MemoryPreference.owner_id == user.id
        )
    )
    if row is None:
        m.fail(404, "偏好不存在")
    result = m.mutate(
        db,
        user.id,
        scope=row.scope,
        key=row.key,
        value=row.value,
        version=version,
        request_key=f"delete:{ident}:{version}",
        delete=True,
    )
    db.commit()
    return result


@router.patch("/memory/settings")
def settings(
    body: SettingsInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    changes = body.model_dump(exclude={"course_id", "version"}, exclude_none=True)
    if any(k not in m.VALUES for k in changes.get("suppressed", [])):
        m.fail(422, "不支持的偏好字段")
    result = m.change_settings(db, user.id, body.course_id, body.version, changes)
    db.commit()
    return result


@router.post("/memory/operations/{ident}/undo")
def undo(
    ident: str,
    body: Version,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    result = m.undo(db, user.id, ident, body.version)
    db.commit()
    return result


@router.get("/chat/sessions/{session_id}/memory/suggestions")
def suggestions(
    session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    owned_session(db, session_id, user.id)
    rows = db.scalars(
        select(MemorySuggestion)
        .where(
            MemorySuggestion.owner_id == user.id,
            MemorySuggestion.session_id == session_id,
        )
        .limit(100)
    )
    return [m.suggestion_data(db, r) for r in rows]


@router.post("/memory/suggestions/{ident}/accept")
def accept(
    ident: str,
    body: AcceptInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if get_settings().memory_engine != "legacy":
        m.fail(409, "请升级客户端，使用自由文本建议确认接口")
    result = m.accept(
        db,
        user.id,
        ident,
        body.version,
        body.preference_version,
        body.course_id,
        body.value,
        "accept:" + body.request_key,
    )
    db.commit()
    return result


@router.post("/memory/suggestions/{ident}/dismiss")
def dismiss(
    ident: str,
    body: DismissInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    result = m.dismiss(db, user.id, ident, body.version, body.suppress)
    db.commit()
    return result


@router.get("/chat/sessions/{session_id}/memory")
def session_memory(
    session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    return summary.summary_status(db, owned_session(db, session_id, user.id))


@router.patch("/chat/sessions/{session_id}/memory")
def configure(
    session_id: str,
    body: SummaryInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    chat = owned_session(db, session_id, user.id)
    summary.configure(db, chat, body.version, body.enabled)
    summary.enqueue(db, chat)
    db.commit()
    return summary.summary_status(db, chat)


@router.delete("/chat/sessions/{session_id}/memory")
def clear(
    session_id: str,
    version: int = Query(ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    chat = owned_session(db, session_id, user.id)
    summary.configure(db, chat, version, False, clear=True)
    db.commit()
    return summary.summary_status(db, chat)


@router.post("/chat/sessions/{session_id}/memory/regenerate", status_code=202)
def regenerate(
    session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    chat = owned_session(db, session_id, user.id)
    status = summary.summary_status(db, chat)
    if not status["enabled"] or not status["available"]:
        m.fail(409, "摘要未开启")
    summary.configure(db, chat, status["version"], True, clear=False)
    control = m.state(db, user.id, "session:" + chat.id)
    control.data = {"enabled": True}
    # Old queued jobs must not block the new epoch.
    from app.db.models import MemoryJob

    for job in db.scalars(
        select(MemoryJob).where(
            MemoryJob.session_id == chat.id, MemoryJob.status.in_(["queued", "running"])
        )
    ):
        job.status = "invalidated"
    ident = summary.enqueue(db, chat)
    db.commit()
    return {"job_id": ident, "status": "queued" if ident else "no_eligible_prefix"}

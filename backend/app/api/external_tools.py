"""Explicit session repository grants. Credentials never enter request models."""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.chat import owned_session
from app.core.auth import current_user
from app.core.config import get_settings
from app.db.models import RepositoryGrant, User
from app.db.session import get_db
from app.external import GitHubReader, repository_name
from app.providers.call_options import Execution, ExecutionStopped
from app.providers.safe_http import ToolError

router = APIRouter(prefix="/api/v1")


class GrantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository: str = Field(min_length=3, max_length=200)
    ref: str = Field(default="", max_length=255)


def grant_data(g):
    return {
        "automatic_tools": True,
        "id": g.id,
        "repository": g.repository,
        "ref": g.ref,
        "private": g.private,
        "active": g.active,
    }


@router.get("/tools/capabilities")
def capabilities(user: User = Depends(current_user)):
    return {
        "web_search": bool(get_settings().tavily_api_key),
        "search_provider": get_settings().web_search_provider,
        "web_reason": ""
        if get_settings().tavily_api_key
        else "服务端尚未配置 Tavily 密钥",
        "github": True,
        "github_private_repositories": list(
            get_settings().github_credentials.get(user.id, {})
        ),
    }


@router.get("/chat/sessions/{session_id}/repositories")
def grants(
    session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    owned_session(db, session_id, user.id)
    return [
        grant_data(g)
        for g in db.scalars(
            select(RepositoryGrant).where(
                RepositoryGrant.session_id == session_id,
                RepositoryGrant.owner_id == user.id,
                RepositoryGrant.active.is_(True),
            )
        )
    ]


@router.post("/chat/sessions/{session_id}/repositories", status_code=201)
def authorize(
    session_id: str,
    body: GrantInput,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    owned_session(db, session_id, user.id)
    try:
        repo = repository_name(body.repository.strip())
        if any(ord(c) < 32 for c in body.ref):
            raise ToolError("invalid_ref")
        execution = Execution.start(get_settings().external_timeout)
        reader = GitHubReader(user.id, repo)
        metadata = reader.metadata(execution)
        ref = body.ref or metadata["default_branch"]
        reader.commit(ref, execution)
    except (ToolError, OSError, ExecutionStopped) as exc:
        code = getattr(exc, "code", "connection_error")
        raise HTTPException(
            400, f"仓库验证失败：{code}；私有仓库须由服务端配置只读凭证"
        ) from None
    grant = RepositoryGrant(
        owner_id=user.id,
        session_id=session_id,
        repository=repo,
        ref=ref,
        private=bool(metadata.get("private")),
        active=True,
    )
    db.add(grant)
    db.commit()
    db.refresh(grant)
    return grant_data(grant)


@router.delete("/chat/sessions/{session_id}/repositories/{grant_id}", status_code=204)
def revoke(
    session_id: str,
    grant_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    owned_session(db, session_id, user.id)
    grant = db.scalar(
        select(RepositoryGrant)
        .where(
            RepositoryGrant.id == grant_id,
            RepositoryGrant.session_id == session_id,
            RepositoryGrant.owner_id == user.id,
        )
        .with_for_update()
    )
    if not grant:
        raise HTTPException(404, "授权不存在")
    grant.active = False
    db.commit()
    return Response(status_code=204)

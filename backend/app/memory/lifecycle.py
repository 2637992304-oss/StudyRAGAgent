"""Invalidate stored derivatives in the same transaction as source changes."""

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from app.db.models import (
    Attachment,
    ChatSession,
    Document,
    MemoryDependency,
    MemoryJob,
    MemoryPreference,
    MemoryState,
    MemorySuggestion,
    Message,
    RepositoryGrant,
)


@event.listens_for(Session, "before_flush")
def invalidate_sources(db, context, instances):
    affected, removed = set(), set()
    changed = {}
    sessions = set()
    for obj in [*db.deleted, *db.dirty]:
        if not isinstance(
            obj, Attachment | ChatSession | Document | Message | RepositoryGrant
        ):
            continue
        deleted = obj in db.deleted
        fields = {a.key for a in inspect(obj).attrs if a.history.has_changes()}
        relevant = deleted or bool(
            fields
            & {"document_ids", "active", "status", "content", "sha256", "tool_policy"}
        )
        # Normal message completion creates a new historical turn, not invalidation.
        if (
            isinstance(obj, Message)
            and not deleted
            and fields
            <= {
                "content",
                "status",
                "tool_policy",
                "measurements",
                "agent_trace",
                "protocol_state",
                "stop_reason",
                "input_tokens",
                "output_tokens",
                "total_ms",
                "first_token_ms",
                "generation_ms",
                "candidate_chunk_ids",
                "retrieval_query",
            }
        ):
            if (
                inspect(obj).attrs.status.history.deleted
                and inspect(obj).attrs.status.history.deleted[0] == "pending"
            ):
                relevant = False
        if not relevant:
            continue
        affected.add(obj.owner_id)
        kind = {
            Message: "messages",
            Document: "documents",
            Attachment: "attachments",
            RepositoryGrant: "grants",
            ChatSession: "sessions",
        }[type(obj)]
        changed.setdefault(kind, set()).add(obj.id)
        if isinstance(obj, ChatSession):
            sessions.add(obj.id)
        if deleted and isinstance(obj, Message):
            removed.add(obj.id)
        if deleted and isinstance(obj, ChatSession):
            removed.update(
                db.scalars(select(Message.id).where(Message.session_id == obj.id))
            )
            control = db.scalar(
                select(MemoryState).where(
                    MemoryState.owner_id == obj.owner_id,
                    MemoryState.scope == "session:" + obj.id,
                )
            )
            if control is not None:
                db.delete(control)
    if not affected:
        return

    def touches(deps, session_id, state_id=None):
        if session_id in sessions:
            return True
        sources = {
            key: set(deps.get(key, []))
            for key in ("messages", "documents", "attachments", "external")
        }
        if state_id:
            for item in db.scalars(
                select(MemoryDependency).where(MemoryDependency.state_id == state_id)
            ):
                sources[item.kind].add(item.source_id)
        if any(
            sources.get(key, set()) & ids
            for key, ids in changed.items()
            if key != "grants"
        ):
            return True
        grants = changed.get("grants", set())
        if grants:
            for msg in db.scalars(
                select(Message).where(Message.id.in_(sources["messages"]))
            ):
                if set((msg.tool_policy or {}).get("grant_ids", [])) & grants:
                    return True
            from app.db.models import ExternalSource

            for source in db.scalars(
                select(ExternalSource).where(ExternalSource.id.in_(sources["external"]))
            ):
                if source.data.get("grant_id") in grants:
                    return True
        return False

    invalidated_sessions = set()
    for owner in sorted(affected):
        from app.memory.service import lock

        lock(db, owner)
    for row in db.scalars(
        select(MemoryState).where(
            MemoryState.owner_id.in_(affected), MemoryState.scope.like("session:%")
        )
    ):
        if row in db.deleted:
            continue
        if not touches(row.data.get("dependencies", {}), row.scope[8:], row.id):
            continue
        invalidated_sessions.add(row.scope[8:])
        row.epoch += 1
        row.version += 1
        row.data = {**row.data, "summary": None}
    for row in db.scalars(
        select(MemoryJob).where(
            MemoryJob.owner_id.in_(affected),
            MemoryJob.status.in_(["queued", "running"]),
        )
    ):
        if row.session_id in invalidated_sessions or touches(
            row.data.get("dependencies", {}), row.session_id
        ):
            row.status = "invalidated"
    if removed:
        for row in db.scalars(
            select(MemoryPreference).where(
                MemoryPreference.owner_id.in_(affected),
                MemoryPreference.deleted.is_(False),
            )
        ):
            if set(row.sources) & removed:
                row.deleted, row.version = True, row.version + 1
        for row in db.scalars(
            select(MemorySuggestion).where(
                MemorySuggestion.owner_id.in_(affected),
                MemorySuggestion.status == "pending",
            )
        ):
            if set(row.data["sources"]) & removed:
                row.status, row.version = "invalidated", row.version + 1

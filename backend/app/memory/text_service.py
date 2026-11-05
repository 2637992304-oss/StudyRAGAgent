"""Versioned text-memory writes. Caller owns the final answer transaction."""

import hashlib
import json
import re

from sqlalchemy import select

from app.db.models import (
    Attachment,
    ChatSession,
    Chunk,
    Document,
    MemoryIndexJob,
    MemoryItem,
    MemoryItemRevision,
    MemoryItemSource,
    MemoryOperation,
    Message,
    RepositoryGrant,
    now,
)
from app.memory import service as legacy
from app.memory.text_schema import TextMemoryInput


def content_hash(content):
    return hashlib.sha256(content.encode()).hexdigest()


def mark_text_write(db, owner):
    marker = legacy.state(db, owner, "text:engine", True)
    marker.data = {"activated": True}


DEPENDENCY_MODELS = {
    "attachments": Attachment,
    "sessions": ChatSession,
    "chunks": Chunk,
    "documents": Document,
    "grants": RepositoryGrant,
    "policies": Message,
    "answers": Message,
    "memory_items": MemoryItem,
}


def dependency_digest(kind, row):
    if kind == "memory_items":
        value = [row.owner_id, row.version, row.content, row.status, row.course_id]
    elif kind == "sessions":
        value = [row.owner_id, row.course_id, row.document_ids]
    elif kind == "policies":
        value = row.tool_policy
    elif kind == "answers":
        value = [row.id, row.owner_id, row.session_id]
    elif kind == "chunks":
        value = [
            row.owner_id,
            row.course_id,
            row.document_id,
            row.text,
            row.content_hash,
        ]
    elif kind == "grants":
        value = [row.owner_id, row.session_id, row.repository, row.ref, row.active]
    else:
        value = [row.owner_id, row.sha256]
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def dependencies_live(db, owner, dependencies, seen=None):
    from app.external import dependencies_allowed

    for dependency in dependencies:
        kind = dependency["kind"]
        model = DEPENDENCY_MODELS.get(kind)
        row = db.get(model, dependency["id"], populate_existing=True) if model else None
        if (
            row is None
            or row.owner_id != owner
            or dependency_digest(kind, row) != dependency["digest"]
        ):
            return False
        if kind == "documents" and row.status != "ready":
            return False
        if kind == "memory_items" and (
            not effective_time(row)
            or not legacy.permitted(
                db, owner, legacy.scope_for(db, owner, row.course_id), "use"
            )
            or not sources_live(db, row, seen=seen)
        ):
            return False
        if kind == "chunks":
            document = db.get(Document, row.document_id, populate_existing=True)
            if not document or document.owner_id != owner or document.status != "ready":
                return False
        if kind == "grants" and not row.active:
            return False
        if kind == "policies":
            chat = db.get(ChatSession, row.session_id, populate_existing=True)
            if not chat or not dependencies_allowed(db, chat, row.tool_policy):
                return False
        if kind == "answers" and row.status not in {
            "pending",
            "completed",
            "no_evidence",
        }:
            return False
    return True


def collect_dependencies(db, owner, source_ids, evidence=(), answer_id=None):
    result = {}
    chats = []

    def add(kind, row):
        if row is None or row.owner_id != owner:
            legacy.fail(409, "来源权限已失效")
        result[(kind, row.id)] = {
            "kind": kind,
            "id": row.id,
            "digest": dependency_digest(kind, row),
        }

    for ident in source_ids:
        source = db.get(Message, ident, populate_existing=True)
        if source is None or source.owner_id != owner:
            legacy.fail(409, "来源已失效")
        chat = db.get(ChatSession, source.session_id, populate_existing=True)
        add("sessions", chat)
        chats.append(chat)
        add("policies", source)
        for attachment in db.scalars(
            select(Attachment).where(Attachment.message_id == ident)
        ):
            add("attachments", attachment)
        for grant_id in (source.tool_policy or {}).get("grant_ids", []):
            add("grants", db.get(RepositoryGrant, grant_id, populate_existing=True))
    for ident in evidence:
        if ident.startswith("ext:"):
            continue
        chunk = db.get(Chunk, ident, populate_existing=True)
        if chunk:
            if not any(
                chunk.course_id == chat.course_id
                and (
                    chat.document_ids is None or chunk.document_id in chat.document_ids
                )
                for chat in chats
            ):
                legacy.fail(409, "记忆证据超出来源会话范围")
            add("chunks", chunk)
            add(
                "documents", db.get(Document, chunk.document_id, populate_existing=True)
            )
    if answer_id:
        add("answers", db.get(Message, answer_id, populate_existing=True))
    return list(result.values())


def bind_sources(
    db, owner, operation, source_ids, basis, *, dependencies=(), inherit=False
):
    after = []
    for expected in operation.data["after"]:
        item = owned(db, owner, expected["id"])
        revision = db.scalar(
            select(MemoryItemRevision).where(
                MemoryItemRevision.item_id == item.id,
                MemoryItemRevision.version == item.version,
            )
        )
        item.write_basis = basis
        revision.data = snapshot(item)
        existing = set(
            db.scalars(
                select(MemoryItemSource.source_id).where(
                    MemoryItemSource.revision_id == revision.id
                )
            )
        )
        inherited = []
        if inherit:
            prior = next(
                (
                    value
                    for value in operation.data["before"]
                    if value and value["id"] == item.id
                ),
                None,
            )
            if prior:
                prior_revision = db.scalar(
                    select(MemoryItemRevision).where(
                        MemoryItemRevision.item_id == item.id,
                        MemoryItemRevision.version == prior["version"],
                    )
                )
                inherited = list(
                    db.scalars(
                        select(MemoryItemSource).where(
                            MemoryItemSource.revision_id == prior_revision.id
                        )
                    )
                )
        for ident in source_ids:
            source = db.get(Message, ident, populate_existing=True)
            if (
                not source
                or source.owner_id != owner
                or source.role != "user"
                or source.status != "completed"
            ):
                legacy.fail(409, "来源已失效")
            if ident not in existing:
                db.add(
                    MemoryItemSource(
                        revision_id=revision.id,
                        kind="messages",
                        source_id=ident,
                        digest=source_digest(source),
                    )
                )
        values = [*collect_dependencies(db, owner, source_ids), *dependencies]
        for dependency in values:
            if not dependencies_live(db, owner, [dependency]):
                legacy.fail(409, "来源依赖已失效")
        specs = {(value["kind"], value["id"]): value["digest"] for value in values}
        for source in inherited:
            specs.setdefault((source.kind, source.source_id), source.digest)
        for (kind, source_id), digest in specs.items():
            if kind == "messages":
                source = db.get(Message, source_id, populate_existing=True)
                if (
                    not source
                    or source.owner_id != owner
                    or source.status != "completed"
                    or source_digest(source) != digest
                ):
                    legacy.fail(409, "原版本来源已失效")
            elif not dependencies_live(
                db, owner, [{"kind": kind, "id": source_id, "digest": digest}]
            ):
                legacy.fail(409, "原版本依赖已失效")
        existing_pairs = set(
            db.execute(
                select(MemoryItemSource.kind, MemoryItemSource.source_id).where(
                    MemoryItemSource.revision_id == revision.id
                )
            ).all()
        )
        for (kind, source_id), digest in specs.items():
            if (kind, source_id) not in existing_pairs:
                db.add(
                    MemoryItemSource(
                        revision_id=revision.id,
                        kind=kind,
                        source_id=source_id,
                        digest=digest,
                    )
                )
        after.append(snapshot(item))
    operation.data = {**operation.data, "after": after}
    db.flush()


def snapshot(item):
    return {
        "id": item.id,
        "content": item.content,
        "course_id": item.course_id,
        "scope_type": item.scope_type,
        "status": item.status,
        "version": item.version,
        "write_basis": item.write_basis,
        "applies_when": item.applies_when,
        "exceptions": item.exceptions,
        "tags": item.tags,
        "use_mode": item.use_mode,
        "valid_from": item.valid_from.isoformat() if item.valid_from else None,
        "expires_at": item.expires_at.isoformat() if item.expires_at else None,
    }


def owned(db, owner, ident):
    item = db.scalar(
        select(MemoryItem)
        .where(MemoryItem.id == ident, MemoryItem.owner_id == owner)
        .execution_options(populate_existing=True)
    )
    if item is None:
        legacy.fail(404, "记忆不存在")
    return item


def revise(db, owner, data: TextMemoryInput, *, ident=None, version=0):
    """Manual input only. Graph writes must add validated provenance separately."""
    legacy.lock(db, owner)
    from app.memory.legacy_text import check_cutover

    check_cutover(db, owner)
    scope = legacy.scope_for(db, owner, data.course_id)
    if not legacy.permitted(db, owner, scope, "write"):
        legacy.fail(409, "长期记忆写入已关闭")
    mark_text_write(db, owner)
    item = owned(db, owner, ident) if ident else None
    if (item.version if item else 0) != version:
        legacy.fail()
    if item and item.status != "active":
        legacy.fail(409, "记忆已失效，请刷新后重试")
    if item is None:
        item = MemoryItem(owner_id=owner, version=0, status="active")
        db.add(item)
    for key, value in data.model_dump().items():
        setattr(item, key, value)
    item.scope_type = "course" if data.course_id else "global"
    item.write_basis = "manual"
    item.version += 1
    item.updated_at = now()
    db.flush()
    db.add(
        MemoryItemRevision(item_id=item.id, version=item.version, data=snapshot(item))
    )
    db.add(MemoryIndexJob(item_id=item.id, version=item.version, data={}))
    control = legacy.state(db, owner, scope, True)
    control.epoch += 1
    db.flush()
    return item


def effective_time(item, instant=None):
    instant = instant or now()
    return (
        item.status == "active"
        and (item.valid_from is None or legacy.utc(item.valid_from) <= instant)
        and (item.expires_at is None or legacy.utc(item.expires_at) > instant)
    )


def apply_manual_batch(db, owner, changes, request_key):
    """Idempotent all-or-nothing manual changes; does not commit the outer tx.

    Each change is {id: optional, version: int, input: TextMemoryInput}.
    Natural-language operations must not call this manual-only entry point.
    """
    if not changes or len(changes) > 8:
        legacy.fail(422, "每批需要1至8条记忆")
    if not request_key or len(request_key) > 100:
        legacy.fail(422, "无效幂等键")
    serialized = [
        {
            "id": change.get("id"),
            "version": change.get("version", 0),
            "input": change["input"].model_dump(mode="json"),
        }
        for change in changes
    ]
    fingerprint = hashlib.sha256(
        json.dumps(serialized, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    legacy.lock(db, owner)
    key = "text:manual:" + request_key
    previous = db.scalar(
        select(MemoryOperation).where(
            MemoryOperation.owner_id == owner, MemoryOperation.request_key == key
        )
    )
    if previous:
        if previous.data.get("fingerprint") != fingerprint:
            legacy.fail(409, "幂等键已被其他内容使用")
        return previous
    identifiers = [c["id"] for c in serialized if c["id"]]
    if len(identifiers) != len(set(identifiers)):
        legacy.fail(422, "同批不能重复修改同一条记忆")
    before, after = [], []
    resolved = set()
    with db.begin_nested():
        for change in changes:
            ident = change.get("id")
            effective_version = change.get("version", 0)
            if ident is None:
                if effective_version != 0:
                    legacy.fail(422, "新建记忆的预期版本必须为0")
                data = change["input"]
                candidates = db.scalars(
                    select(MemoryItem).where(
                        MemoryItem.owner_id == owner,
                        MemoryItem.content == data.content,
                        MemoryItem.course_id == data.course_id
                        if data.course_id
                        else MemoryItem.course_id.is_(None),
                        MemoryItem.status == "active",
                    )
                )
                for candidate in candidates:
                    comparable = {
                        key: snapshot(candidate).get(key)
                        for key in TextMemoryInput.model_fields
                    }
                    if TextMemoryInput.model_validate(comparable).model_dump(
                        mode="json"
                    ) == data.model_dump(mode="json") and sources_live(db, candidate):
                        ident, effective_version = candidate.id, candidate.version
                        break
            if ident and ident in resolved:
                legacy.fail(409, "同批重复修改同一条记忆")
            if ident:
                resolved.add(ident)
            before.append(snapshot(owned(db, owner, ident)) if ident else None)
            item = revise(
                db,
                owner,
                change["input"],
                ident=ident,
                version=effective_version,
            )
            after.append(snapshot(item))
        operation = MemoryOperation(
            owner_id=owner,
            request_key=key,
            status="saved",
            data={
                "schema_version": 2,
                "fingerprint": fingerprint,
                "before": before,
                "after": after,
            },
        )
        db.add(operation)
        db.flush()
    return operation


def source_digest(message):
    return hashlib.sha256(
        json.dumps(
            [
                message.id,
                message.session_id,
                message.owner_id,
                message.role,
                message.content,
            ],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def sources_live(db, item, version=None, seen=None):
    seen = set(seen or ())
    if item.id in seen:
        return False
    seen.add(item.id)
    revision = db.scalar(
        select(MemoryItemRevision).where(
            MemoryItemRevision.item_id == item.id,
            MemoryItemRevision.version == (version or item.version),
        )
    )
    if revision is None:
        return False
    for source in db.scalars(
        select(MemoryItemSource).where(MemoryItemSource.revision_id == revision.id)
    ):
        if source.kind != "messages":
            if not dependencies_live(
                db,
                item.owner_id,
                [
                    {
                        "kind": source.kind,
                        "id": source.source_id,
                        "digest": source.digest,
                    }
                ],
                seen=seen,
            ):
                return False
            continue
        message = db.get(Message, source.source_id, populate_existing=True)
        if (
            source.kind != "messages"
            or message is None
            or message.owner_id != item.owner_id
            or message.role != "user"
            or message.status != "completed"
            or source_digest(message) != source.digest
        ):
            return False
    return True


def visible(db, owner, course_id, query=""):
    legacy.scope_for(db, owner, course_id)
    statement = select(MemoryItem).where(
        MemoryItem.owner_id == owner, MemoryItem.status == "active"
    )
    statement = statement.where(
        (MemoryItem.course_id.is_(None) | (MemoryItem.course_id == course_id))
        if course_id
        else MemoryItem.course_id.is_(None)
    )
    rows = list(
        db.scalars(
            statement.order_by(MemoryItem.updated_at.desc(), MemoryItem.id).limit(501)
        )
    )
    if len(rows) > 500:
        legacy.fail(409, "当前范围超过500条有效记忆候选，请在管理入口整理后重试")
    return [
        item
        for item in rows
        if effective_time(item)
        and sources_live(db, item)
        and (not query or query.casefold() in item.content.casefold())
    ]


def allowed_for_chat(db, item, chat, grant_ids=(), seen=None):
    seen = set(seen or ())
    if item.id in seen:
        return False
    seen.add(item.id)
    revision = db.scalar(
        select(MemoryItemRevision).where(
            MemoryItemRevision.item_id == item.id,
            MemoryItemRevision.version == item.version,
        )
    )
    if revision is None:
        return False
    for source in db.scalars(
        select(MemoryItemSource).where(MemoryItemSource.revision_id == revision.id)
    ):
        if source.kind == "memory_items":
            parent = db.get(MemoryItem, source.source_id, populate_existing=True)
            if not parent or not allowed_for_chat(db, parent, chat, grant_ids, seen):
                return False
        if source.kind in {"documents", "chunks"}:
            model = Document if source.kind == "documents" else Chunk
            row = db.get(model, source.source_id, populate_existing=True)
            if row is None:
                return False
            document_id = row.id if source.kind == "documents" else row.document_id
            if row.course_id != chat.course_id or (
                chat.document_ids is not None and document_id not in chat.document_ids
            ):
                return False
        if source.kind == "attachments":
            attachment = db.get(Attachment, source.source_id, populate_existing=True)
            if attachment is None:
                return False
            if attachment.session_id != chat.id:
                return False
        if source.kind == "grants" and source.source_id not in grant_ids:
            return False
    return True


def operation_data(db, op):
    result = {
        "schema_version": 2,
        "operation_id": op.id,
        "status": op.status,
        "items": op.data.get("after", []),
        "before": op.data.get("before", []),
        "detail": op.data.get("detail"),
        "version": 1,
    }
    if op.status == "saved":
        for expected in result["items"]:
            item = db.get(MemoryItem, expected["id"], populate_existing=True)
            if (
                item is None
                or item.version != expected["version"]
                or (item.status == "active" and not sources_live(db, item))
            ):
                result["status"] = "invalidated"
                break
    return result


def delete_item(db, owner, ident, version, request_key):
    legacy.lock(db, owner)
    mark_text_write(db, owner)
    key = "text:delete:" + request_key
    existing = db.scalar(
        select(MemoryOperation).where(
            MemoryOperation.owner_id == owner, MemoryOperation.request_key == key
        )
    )
    if existing:
        if (
            existing.data["after"][0]["id"] != ident
            or existing.data["before"][0]["version"] != version
        ):
            legacy.fail(409, "幂等键已被其他操作使用")
        return existing
    item = owned(db, owner, ident)
    if item.version != version:
        legacy.fail()
    before = snapshot(item)
    item.status, item.version, item.updated_at = "deleted", item.version + 1, now()
    control = legacy.state(db, owner, legacy.scope_for(db, owner, item.course_id), True)
    control.epoch += 1
    db.add(
        MemoryItemRevision(item_id=item.id, version=item.version, data=snapshot(item))
    )
    op = MemoryOperation(
        owner_id=owner,
        request_key=key,
        status="deleted",
        data={"schema_version": 2, "before": [before], "after": [snapshot(item)]},
    )
    db.add(op)
    db.flush()
    return op


def undo(db, owner, ident):
    legacy.lock(db, owner)
    op = db.scalar(
        select(MemoryOperation).where(
            MemoryOperation.id == ident, MemoryOperation.owner_id == owner
        )
    )
    if op is None or op.data.get("schema_version") != 2:
        legacy.fail(404, "自由文本操作不存在")
    if op.status == "undone":
        return op
    if op.status not in {"saved", "deleted"}:
        legacy.fail(409, "此操作不可撤销")
    with db.begin_nested():
        for before, after in zip(op.data["before"], op.data["after"], strict=True):
            item = owned(db, owner, after["id"])
            if item.version != after["version"]:
                legacy.fail()
            if before and not sources_live(db, item, before["version"]):
                legacy.fail(409, "原记忆来源已失效")
            old_revision = (
                db.scalar(
                    select(MemoryItemRevision).where(
                        MemoryItemRevision.item_id == item.id,
                        MemoryItemRevision.version == before["version"],
                    )
                )
                if before
                else None
            )
            item.version += 1
            if before:
                fields = {key: before.get(key) for key in TextMemoryInput.model_fields}
                data = TextMemoryInput.model_validate(fields)
                for key, value in data.model_dump().items():
                    setattr(item, key, value)
                item.status, item.write_basis, item.scope_type = (
                    before["status"],
                    before["write_basis"],
                    before["scope_type"],
                )
            else:
                item.status = "deleted"
            item.updated_at = now()
            control = legacy.state(
                db, owner, legacy.scope_for(db, owner, item.course_id), True
            )
            control.epoch += 1
            revision = MemoryItemRevision(
                item_id=item.id, version=item.version, data=snapshot(item)
            )
            db.add(revision)
            db.flush()
            if old_revision:
                for source in db.scalars(
                    select(MemoryItemSource).where(
                        MemoryItemSource.revision_id == old_revision.id
                    )
                ):
                    db.add(
                        MemoryItemSource(
                            revision_id=revision.id,
                            kind=source.kind,
                            source_id=source.source_id,
                            digest=source.digest,
                        )
                    )
            if item.status == "active":
                db.add(MemoryIndexJob(item_id=item.id, version=item.version, data={}))
        op.status = "undone"
        db.flush()
    return op


def rank(db, owner, course_id, query, allowed=None):
    from app.core.config import get_settings
    from app.db.models import MemoryItemEmbedding

    rows = visible(db, owner, course_id)
    if allowed:
        rows = [row for row in rows if allowed(row)]
    always = [r for r in rows if r.use_mode == "always"]
    relevant = [r for r in rows if r.use_mode != "always"]
    scores = {}
    tokens = set(re.findall(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]{2}", query.lower()))
    for item in relevant:
        scores[item.id] = sum(t in item.content.lower() for t in tokens)
    if relevant and query and db.bind.dialect.name == "postgresql":
        from app.providers.embeddings import embed_texts

        ids = [r.id for r in relevant]
        indexed = list(
            db.scalars(
                select(MemoryItemEmbedding.item_id).where(
                    MemoryItemEmbedding.item_id.in_(ids)
                )
            )
        )
        if indexed:
            try:
                (vector,) = embed_texts([query])
                for item in relevant:
                    embedding = db.get(MemoryItemEmbedding, item.id)
                    if (
                        embedding
                        and embedding.version == item.version
                        and embedding.content_hash == content_hash(item.content)
                        and embedding.model == get_settings().embedding_model
                    ):
                        distance = db.scalar(
                            select(
                                MemoryItemEmbedding.embedding.cosine_distance(vector)
                            ).where(MemoryItemEmbedding.item_id == item.id)
                        )
                        scores[item.id] += max(0, 1 - distance)
            except Exception:
                # Keep the bounded lexical fallback; no fabricated semantic score.
                pass
    relevant.sort(key=lambda r: (-scores[r.id], r.course_id is None, r.id))
    return always + [r for r in relevant[:8] if scores[r.id] > 0]

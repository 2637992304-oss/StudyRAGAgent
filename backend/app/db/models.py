from datetime import UTC, datetime
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def new_id() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    documents: Mapped[list["Document"]] = relationship(back_populates="course")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    document_type: Mapped[str] = mapped_column(String(32))
    mime_type: Mapped[str] = mapped_column(String(120))
    storage_key: Mapped[str] = mapped_column(String(36), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    active_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    parse_warnings: Mapped[list] = mapped_column(
        JSON, default=list, server_default="[]"
    )
    parse_quality: Mapped[str | None] = mapped_column(String(24), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    course: Mapped[Course] = relationship(back_populates="documents")
    jobs: Mapped[list["IngestionJob"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(24), default="queued")
    stage: Mapped[str] = mapped_column(String(40), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    delivery_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, onupdate=now
    )
    document: Mapped[Document] = relationship(back_populates="jobs")


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index"),
        Index("ix_chunks_owner_course", "owner_id", "course_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"))
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE")
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(24), default="body")
    section_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slide_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding_model: Mapped[str] = mapped_column(String(255))
    parser_version: Mapped[str] = mapped_column(String(40))
    embedding: Mapped[list[float]] = mapped_column(Vector(512))
    document: Mapped[Document] = relationship(back_populates="chunks")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), index=True)
    document_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    title: Mapped[str] = mapped_column(
        String(120), default="新对话", server_default="新对话"
    )
    title_renamed: Mapped[bool] = mapped_column(default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    messages: Mapped[list["Message"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    repository_grants: Mapped[list["RepositoryGrant"]] = relationship(
        cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="completed")
    retrieval_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_chunk_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    retrieval_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_token_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_estimate: Mapped[float | None] = mapped_column(nullable=True)
    measurements: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_trace: Mapped[list | None] = mapped_column(JSON, nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    answer_scope: Mapped[str | None] = mapped_column(String(16), nullable=True)
    thinking: Mapped[bool | None] = mapped_column(nullable=True)
    call_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Private provider transcript. Never serialize via message_data or log it.
    protocol_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tool_policy: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reply_to_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    session: Mapped[ChatSession] = relationship(back_populates="messages")
    citations: Mapped[list["Citation"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )
    external_sources: Mapped[list["ExternalSource"]] = relationship(
        cascade="all, delete-orphan"
    )


class Citation(Base):
    __tablename__ = "citations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    chunk_id: Mapped[str | None] = mapped_column(
        ForeignKey("chunks.id", ondelete="SET NULL"), nullable=True
    )
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    label: Mapped[int] = mapped_column(Integer)
    message: Mapped[Message] = relationship(back_populates="citations")


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=True, index=True
    )
    saved_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(40))
    storage_key: Mapped[str] = mapped_column(String(36), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RepositoryGrant(Base):
    __tablename__ = "repository_grants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    repository: Mapped[str] = mapped_column(String(200))
    ref: Mapped[str] = mapped_column(String(255))
    private: Mapped[bool] = mapped_column(default=False)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ExternalSource(Base):
    __tablename__ = "external_sources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    # Snapshots contain no credentials; retained privately even when not cited.
    kind: Mapped[str] = mapped_column(String(16))
    data: Mapped[dict] = mapped_column(JSON)


class MemoryState(Base):
    __tablename__ = "memory_states"
    __table_args__ = (UniqueConstraint("owner_id", "scope"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(default=0)
    epoch: Mapped[int] = mapped_column(default=0)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class MemoryPreference(Base):
    __tablename__ = "memory_preferences"
    __table_args__ = (UniqueConstraint("owner_id", "scope", "key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String(80))
    key: Mapped[str] = mapped_column(String(40))
    value: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(default=1)
    deleted: Mapped[bool] = mapped_column(default=False)
    source_kind: Mapped[str] = mapped_column(String(32))
    sources: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MemorySuggestion(Base):
    __tablename__ = "memory_suggestions"
    __table_args__ = (UniqueConstraint("owner_id", "dedup_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE")
    )
    dedup_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    version: Mapped[int] = mapped_column(default=1)
    data: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MemoryOperation(Base):
    __tablename__ = "memory_operations"
    __table_args__ = (UniqueConstraint("owner_id", "request_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=True
    )
    request_key: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), default="saved")
    data: Mapped[dict] = mapped_column(JSON)


class MemoryJob(Base):
    __tablename__ = "memory_jobs"
    __table_args__ = (UniqueConstraint("session_id", "work_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    work_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(default=0)
    data: Mapped[dict] = mapped_column(JSON)
    measurements: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class MemoryDependency(Base):
    """Append-only provenance for the current summary generation, outside prompts."""

    __tablename__ = "memory_dependencies"
    __table_args__ = (UniqueConstraint("state_id", "kind", "source_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    state_id: Mapped[str] = mapped_column(
        ForeignKey("memory_states.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(24))
    source_id: Mapped[str] = mapped_column(String(36), index=True)
    digest: Mapped[str] = mapped_column(String(64))


class MemoryItem(Base):
    __tablename__ = "memory_items"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_memory_item_version"),
        CheckConstraint(
            "status IN ('active','superseded','deleted','invalidated')",
            name="ck_memory_item_status",
        ),
        CheckConstraint(
            "use_mode IN ('always','relevant')", name="ck_memory_item_use_mode"
        ),
        CheckConstraint(
            "write_basis IN ('manual','explicit_request','user_confirmed')",
            name="ck_memory_item_basis",
        ),
        CheckConstraint(
            "(scope_type='global' AND course_id IS NULL) OR "
            "(scope_type='course' AND course_id IS NOT NULL)",
            name="ck_memory_item_scope",
        ),
        CheckConstraint(
            "valid_from IS NULL OR expires_at IS NULL OR valid_from < expires_at",
            name="ck_memory_item_dates",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    course_id: Mapped[str | None] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    scope_type: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    version: Mapped[int] = mapped_column(default=1)
    write_basis: Mapped[str] = mapped_column(String(24))
    applies_when: Mapped[str | None] = mapped_column(Text)
    exceptions: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    use_mode: Mapped[str] = mapped_column(String(16), default="relevant")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MemoryItemRevision(Base):
    __tablename__ = "memory_item_revisions"
    __table_args__ = (UniqueConstraint("item_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("memory_items.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MemoryItemSource(Base):
    __tablename__ = "memory_item_sources"
    __table_args__ = (UniqueConstraint("revision_id", "kind", "source_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("memory_item_revisions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(24))
    source_id: Mapped[str] = mapped_column(String(36), index=True)
    digest: Mapped[str] = mapped_column(String(64))


class MemoryItemRelation(Base):
    __tablename__ = "memory_item_relations"
    __table_args__ = (UniqueConstraint("item_id", "target_id", "kind"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("memory_items.id", ondelete="CASCADE"), index=True
    )
    target_id: Mapped[str] = mapped_column(
        ForeignKey("memory_items.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(24))


class MemoryItemEmbedding(Base):
    __tablename__ = "memory_item_embeddings"
    item_id: Mapped[str] = mapped_column(
        ForeignKey("memory_items.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(200))
    embedding: Mapped[list[float]] = mapped_column(Vector(512))


class MemoryIndexJob(Base):
    __tablename__ = "memory_index_jobs"
    __table_args__ = (UniqueConstraint("item_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("memory_items.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(default=0)
    data: Mapped[dict] = mapped_column(JSON, default=dict)

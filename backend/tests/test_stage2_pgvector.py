"""Exercise the real PostgreSQL pgvector query and its account/scope filters."""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import chat as chat_api
from app.core.auth import hash_password
from app.core.config import get_settings
from app.db.models import ChatSession, Chunk, Course, Document, User
from app.db.session import get_engine


@pytest.mark.parametrize("strategy", ["dense", "keyword", "hybrid", "rerank"])
def test_pgvector_retrieval_filters_at_query_time(monkeypatch, strategy):
    from app.retrieval import FastEmbedReranker

    settings = get_settings()
    monkeypatch.setattr(settings, "retrieval_strategy", strategy)
    monkeypatch.setattr(settings, "retrieval_keyword_min_score", 0.01)
    # This test verifies scope with real SQL, not model quality.
    monkeypatch.setattr(
        FastEmbedReranker, "score", lambda self, q, texts: [2.0] * len(texts)
    )
    vector = [1.0] + [0.0] * 511
    monkeypatch.setattr(chat_api, "embed_texts", lambda _: [vector])
    owner, other = str(uuid4()), str(uuid4())
    course, other_course, foreign_course = (str(uuid4()) for _ in range(3))
    specs = [
        (owner, course, "ready"),
        (owner, course, "ready"),
        (owner, course, "failed"),
        (owner, other_course, "ready"),
        (other, foreign_course, "ready"),
    ]
    doc_ids = [str(uuid4()) for _ in specs]
    with Session(get_engine()) as db:
        try:
            db.add_all(
                User(
                    id=uid,
                    email=f"{uid}@example.test",
                    password_hash=hash_password("x"),
                )
                for uid in (owner, other)
            )
            db.flush()
            db.add_all(
                Course(id=cid, owner_id=uid, name="向量隔离测试")
                for cid, uid in (
                    (course, owner),
                    (other_course, owner),
                    (foreign_course, other),
                )
            )
            db.flush()
            for index, ((uid, cid, status), doc_id) in enumerate(
                zip(specs, doc_ids, strict=True)
            ):
                db.add(
                    Document(
                        id=doc_id,
                        owner_id=uid,
                        course_id=cid,
                        filename=f"scope-{index}.txt",
                        document_type="笔记",
                        mime_type="text/plain",
                        storage_key=str(uuid4()),
                        sha256="0" * 64,
                        size_bytes=16,
                        status=status,
                    )
                )
            db.flush()
            for index, ((uid, cid, _), doc_id) in enumerate(
                zip(specs, doc_ids, strict=True)
            ):
                db.add(
                    Chunk(
                        owner_id=uid,
                        course_id=cid,
                        document_id=doc_id,
                        chunk_index=0,
                        text=f"scope fact {index}",
                        content_hash=str(index) * 64,
                        embedding_model=get_settings().embedding_model,
                        parser_version="test",
                        embedding=vector,
                    )
                )
            db.commit()
            selected = ChatSession(
                owner_id=owner, course_id=course, document_ids=[doc_ids[0]]
            )
            evidence, candidates = chat_api.retrieve(db, selected, "scope fact")
            assert {doc.id for _, doc in evidence} == {doc_ids[0]}
            assert len(candidates) == 1
            selected.document_ids = None
            evidence, candidates = chat_api.retrieve(db, selected, "scope fact")
            assert {doc.id for _, doc in evidence} == {doc_ids[0], doc_ids[1]}
            assert len(candidates) == 2
            selected.document_ids = [doc_ids[2]]
            assert chat_api.retrieve(db, selected, "scope fact") == ([], [])
            # A fresh lexical request cannot retain a deleted/failed document.
            selected.document_ids = [doc_ids[0]]
            db.get(Document, doc_ids[0]).status = "deleted"
            db.commit()
            assert chat_api.retrieve(db, selected, "scope fact") == ([], [])
            db.get(Document, doc_ids[0]).status = "ready"
            db.commit()
            assert len(chat_api.retrieve(db, selected, "scope fact")[1]) == 1
            # Neighbor expansion must retain all scope/state constraints even
            # when supplied a foreign heading directly, rather than via retrieval.
            from app.retrieval import Candidate, PgVectorStore

            headings = []
            for i in [0, 1, 2, 3, 4]:
                c = db.scalar(select(Chunk).where(Chunk.document_id == doc_ids[i]))
                c.section_path = "scope section"
                headings.append(Candidate(c, db.get(Document, doc_ids[i])))
                db.add(
                    Chunk(
                        owner_id=specs[i][0],
                        course_id=specs[i][1],
                        document_id=doc_ids[i],
                        chunk_index=1,
                        text=f"neighbor body {i}",
                        section_path="scope section",
                        content_hash="a" * 64,
                        embedding_model=settings.embedding_model,
                        parser_version="scope-neighbor-test",
                        embedding=vector,
                    )
                )
            db.commit()
            store = PgVectorStore(db, selected, settings)
            neighbors = store.following(headings, vector)
            assert [x.chunk.text for x in neighbors] == ["neighbor body 0"]
            db.get(Document, doc_ids[0]).status = "deleted"
            db.commit()
            assert store.following(headings, vector) == []
            db.get(Document, doc_ids[0]).status = "ready"
            db.commit()
            for chunk in db.scalars(
                select(Chunk).where(Chunk.document_id == doc_ids[0])
            ).all():
                db.delete(chunk)
            db.commit()
            assert chat_api.retrieve(db, selected, "scope fact") == ([], [])
        finally:
            for doc in db.scalars(
                select(Document).where(Document.id.in_(doc_ids))
            ).all():
                db.delete(doc)
            db.flush()
            for cid in (course, other_course, foreign_course):
                item = db.get(Course, cid)
                if item:
                    db.delete(item)
            db.flush()
            for uid in (owner, other):
                item = db.get(User, uid)
                if item:
                    db.delete(item)
            db.commit()

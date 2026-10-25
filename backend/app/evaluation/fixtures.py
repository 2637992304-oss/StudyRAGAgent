"""Temporary committed accounts; explicit crash recovery manifest, no user writes."""

import hashlib
import importlib.metadata
import json
import math
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import ChatSession, Chunk, Course, Document, User
from app.db.session import get_engine
from app.ingestion.parser import make_chunks, parse_document
from app.providers.embeddings import embed_texts

DATA = Path(__file__).resolve().parents[3] / "docs/evaluation_data/dataset.json"


def load_dataset(path: Path = DATA):
    return json.loads(path.read_text(encoding="utf-8"))


def vector_cache_path(chunks, dataset_path):
    if ".eval-cache" not in dataset_path.resolve().parts:
        return None
    settings = get_settings()
    fingerprint = {
        "model": settings.embedding_model,
        "fastembed": importlib.metadata.version("fastembed"),
        "texts": [c["text"] for c in chunks],
        "revisions": sorted(
            str(file.relative_to(settings.model_cache_dir))
            for folder in settings.model_cache_dir.glob("models--*")
            if folder.name.lower().endswith(
                settings.embedding_model.rsplit("/", 1)[-1].lower()
            )
            for file in (folder / "trees").glob("*.json")
        ),
    }
    key = hashlib.sha256(
        json.dumps(fingerprint, ensure_ascii=False).encode()
    ).hexdigest()
    return dataset_path.resolve().parent / ("vectors_" + key + ".json")


def cleanup(manifest: Path):
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    owners = payload["owners"]
    if len(owners) != 2:
        raise ValueError("Invalid evaluation manifest")
    with Session(get_engine()) as db:
        users = db.scalars(select(User).where(User.id.in_(owners))).all()
        if any(u.email != f"stage3-{u.id}@example.test" for u in users):
            raise ValueError("Refusing to clean non-evaluation users")
        for model in (ChatSession, Document, Course):
            # PostgreSQL FK cascades handle messages/citations/jobs/chunks.
            # Prefix + exact-ID checks above are retained before any deletion.
            db.execute(delete(model).where(model.owner_id.in_(owners)))
        db.execute(delete(User).where(User.id.in_(owners)))
        db.commit()
    payload["cleaned"] = True
    manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")


@contextmanager
def fixtures(
    dataset,
    output: Path,
    chunk_size: int,
    overlap: int,
    dataset_path: Path = DATA,
    *,
    pair_headings: bool = False,
    pdf_layout: bool = False,
):
    owners = {"a": str(uuid4()), "b": str(uuid4())}
    courses = {
        (owner, course): str(uuid4())
        for owner in owners
        for course in ("main", "other")
    }
    docs = {item["key"]: str(uuid4()) for item in dataset["documents"]}
    manifest = output / "fixture_manifest.json"
    if manifest.exists() and not json.loads(manifest.read_text(encoding="utf-8")).get(
        "cleaned"
    ):
        raise ValueError(
            "Unclean previous evaluation: run --cleanup with its manifest first"
        )
    manifest.write_text(
        json.dumps({"owners": list(owners.values()), "cleaned": False}),
        encoding="utf-8",
    )
    prepared = []
    source_dir = dataset_path.resolve().parent / "sources"
    phase = "parsing_before_database_writes"
    try:
        for spec in dataset["documents"]:
            content = (source_dir / spec["file"]).read_bytes()
            parts = parse_document(spec["file"], content)
            chunks = make_chunks(
                parts,
                chunk_size,
                overlap,
                pair_headings=pair_headings,
                pdf_layout=pdf_layout,
            )
            prepared.append((spec, content, chunks))
        all_chunks = [chunk for _, _, chunks in prepared for chunk in chunks]
        cache = vector_cache_path(all_chunks, dataset_path)
        cache_hit = bool(cache and cache.exists())
        phase = "embedding_before_database_writes"
        if cache and cache.exists():
            vectors = json.loads(cache.read_text(encoding="utf-8"))
            if len(vectors) != len(all_chunks) or any(
                len(v) != 512 or not any(v) or not all(math.isfinite(x) for x in v)
                for v in vectors
            ):
                raise ValueError("Invalid evaluation vector cache")
        else:
            vectors = embed_texts([chunk["text"] for chunk in all_chunks])
            if cache:
                cache = vector_cache_path(all_chunks, dataset_path)
                temporary = cache.with_suffix(".tmp")
                temporary.write_text(json.dumps(vectors), encoding="utf-8")
                temporary.replace(cache)
    except BaseException as exc:
        # No accounts have been written yet; preserve the failure without a
        # misleading unclean database manifest or a second failing DB request.
        manifest.write_text(
            json.dumps(
                {
                    "owners": list(owners.values()),
                    "cleaned": True,
                    "phase": phase,
                    "error_type": type(exc).__name__,
                }
            ),
            encoding="utf-8",
        )
        raise
    manifest.write_text(
        json.dumps(
            {
                "owners": list(owners.values()),
                "cleaned": False,
                "embedding_cache_hit": cache_hit,
            }
        ),
        encoding="utf-8",
    )
    vector_iter = iter(vectors)
    try:
        with Session(get_engine()) as db:
            db.add_all(
                User(
                    id=uid,
                    email=f"stage3-{uid}@example.test",
                    password_hash="disabled-evaluation-account",
                )
                for uid in owners.values()
            )
            db.flush()
            db.add_all(
                Course(id=cid, owner_id=owners[owner], name="Stage 3 fixtures")
                for (owner, _), cid in courses.items()
            )
            db.flush()
            for spec, content, chunks in prepared:
                uid, cid = owners[spec["owner"]], courses[spec["owner"], spec["course"]]
                doc = Document(
                    id=docs[spec["key"]],
                    owner_id=uid,
                    course_id=cid,
                    filename=spec["file"],
                    document_type="笔记",
                    mime_type="text/markdown",
                    storage_key=str(uuid4()),
                    sha256=hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content),
                    status=spec["status"],
                )
                db.add(doc)
                db.flush()
                for i, chunk in enumerate(chunks):
                    db.add(
                        Chunk(
                            owner_id=uid,
                            course_id=cid,
                            document_id=doc.id,
                            chunk_index=i,
                            **chunk,
                            content_hash=hashlib.sha256(
                                chunk["text"].encode()
                            ).hexdigest(),
                            embedding_model=get_settings().embedding_model,
                            parser_version="stage3-v4-pdf-layout"
                            if pdf_layout and spec["file"].lower().endswith(".pdf")
                            else "stage3-v3-heading-pairs"
                            if pair_headings
                            else "stage3-v3-page-ocr",
                            embedding=next(vector_iter),
                        )
                    )
            db.commit()
        yield owners, courses, docs
    finally:
        cleanup(manifest)


def scope_for(case, owners, courses, docs):
    scope = case["scope"]
    selected = scope["documents"]
    # Deliberately invalid ids/statuses exercise store-level filtering. For API
    # evaluation only valid scopes are accepted by validate_scope.
    return ChatSession(
        owner_id=owners[scope["owner"]],
        course_id=courses[scope["owner"], scope["course"]],
        document_ids=[docs[key] for key in selected] if selected is not None else None,
    )

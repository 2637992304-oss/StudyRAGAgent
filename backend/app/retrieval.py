"""Shared, scope-first retrieval. BM25 is rebuilt per request: no stale cache."""

import hashlib
import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from threading import Lock
from typing import Protocol
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, defer

from app.core.config import Settings
from app.db.models import ChatSession, Chunk, Document
from app.providers.embeddings import embed_texts
from app.query_plan import normalize_query, query_facets
from app.rerank_cache import score_cache

LIGHT_RERANKER = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1-int8"
_registration_lock = Lock()


def tokens(text: str) -> list[str]:
    """Latin identifiers/numbers plus Chinese unigrams and adjacent bigrams."""
    result = []
    for word in re.findall(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
        if "\u4e00" <= word[0] <= "\u9fff":
            result.extend(word)
            result.extend(word[i : i + 2] for i in range(len(word) - 1))
        else:
            result.append(word)
    return result


@lru_cache(maxsize=8192)
def term_counts(text: str) -> Counter:
    # Content-only token statistics, never scope/results/IDF. Every request still
    # reads authorized live rows; changed/deleted text cannot restore old hits.
    return Counter(tokens(text))


def bm25(query: str, texts: list[str]) -> list[float]:
    corpus = [term_counts(text) for text in texts]
    lengths = [sum(item.values()) for item in corpus]
    average = sum(lengths) / len(lengths) if lengths else 0
    scores = [0.0] * len(texts)
    if not average:
        return scores
    for term in set(tokens(query)):
        frequency = sum(term in item for item in corpus)
        idf = math.log(1 + (len(corpus) - frequency + 0.5) / (frequency + 0.5))
        for i, item in enumerate(corpus):
            tf = item[term]
            scores[i] += (
                idf * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * lengths[i] / average))
            )
    return scores


@dataclass
class Candidate:
    chunk: Chunk
    document: Document
    distance: float | None = None
    keyword_score: float = 0.0
    fusion_score: float = 0.0
    rerank_score: float | None = None
    facet: str | None = None


def stable_order(item: Candidate):
    return (item.document.filename or "", item.chunk.chunk_index or 0, item.chunk.id)


def rrf(rankings: list[list[Candidate]], k: int) -> list[Candidate]:
    if k < 1:
        raise ValueError("RRF k must be positive")
    merged: dict[str, Candidate] = {}
    scores: Counter = Counter()
    for ranking in rankings:
        seen = set()
        for rank, item in enumerate(ranking, 1):
            key = item.chunk.id
            if key in seen:
                continue
            seen.add(key)
            scores[key] += 1 / (k + rank)
            if key not in merged:
                merged[key] = item
            else:
                previous = merged[key]
                if item.distance is not None:
                    previous.distance = item.distance
                previous.keyword_score = max(previous.keyword_score, item.keyword_score)
    for key, item in merged.items():
        item.fusion_score = scores[key]
    return sorted(merged.values(), key=lambda x: (-x.fusion_score, stable_order(x)))


class VectorStore(Protocol):
    def dense(self, vector: list[float], limit: int) -> list[Candidate]: ...

    def lexical(self, query: str, limit: int) -> list[Candidate]: ...


class PgVectorStore:
    def __init__(self, db: Session, scope: ChatSession, settings: Settings):
        self.db, self.scope, self.settings = db, scope, settings

    def statement(self):
        scope = self.scope
        statement = (
            select(Chunk, Document)
            .options(defer(Chunk.embedding))
            .join(Document, Chunk.document_id == Document.id)
            .where(
                Chunk.owner_id == scope.owner_id,
                Chunk.course_id == scope.course_id,
                Chunk.embedding_model == self.settings.embedding_model,
                Document.owner_id == scope.owner_id,
                Document.course_id == scope.course_id,
                Document.status == "ready",
            )
        )
        if scope.document_ids is not None:
            statement = statement.where(Document.id.in_(scope.document_ids))
        return statement

    def dense(self, vector: list[float], limit: int) -> list[Candidate]:
        distance = Chunk.embedding.cosine_distance(vector)
        rows = self.db.execute(
            self.statement()
            .add_columns(distance)
            .order_by(distance, Document.filename, Chunk.chunk_index, Chunk.id)
            .limit(limit)
        ).all()
        return [Candidate(c, d, distance=float(s)) for c, d, s in rows]

    def lexical(self, query: str, limit: int) -> list[Candidate]:
        cap = self.settings.retrieval_lexical_max_chunks
        rows = self.db.execute(self.statement().order_by(Chunk.id).limit(cap + 1)).all()
        if len(rows) > cap:
            raise ValueError("Scoped BM25 corpus exceeds configured limit")
        scores = bm25(query, [chunk.text for chunk, _ in rows])
        ranked = [
            Candidate(chunk, doc, keyword_score=score)
            for (chunk, doc), score in zip(rows, scores, strict=True)
            if score > 0
        ]
        return sorted(ranked, key=lambda x: (-x.keyword_score, stable_order(x)))[:limit]

    def following(self, headings: list[Candidate], vector=None) -> list[Candidate]:
        """Live scoped lookup; never inherit heading relevance scores for its body."""
        conditions = []
        for item in headings:
            chunk = item.chunk
            conditions.append(
                and_(
                    Chunk.document_id == chunk.document_id,
                    Chunk.chunk_index > chunk.chunk_index,
                    Chunk.chunk_index <= chunk.chunk_index + 2,
                    Chunk.section_path == chunk.section_path,
                    Chunk.page_start == chunk.page_start,
                    Chunk.slide_no == chunk.slide_no,
                )
            )
        if not conditions:
            return []
        statement = self.statement().where(or_(*conditions))
        if vector is not None:
            statement = statement.add_columns(Chunk.embedding.cosine_distance(vector))
        rows = self.db.execute(statement.order_by(Chunk.chunk_index, Chunk.id)).all()
        return [
            Candidate(
                row[0], row[1], distance=float(row[2]) if vector is not None else None
            )
            for row in rows
            if not is_heading(row[0])
        ]


class Reranker(Protocol):
    def score(self, query: str, texts: list[str]) -> list[float]: ...


@lru_cache(maxsize=2)
def cross_encoder(model: str, cache: str, local_only: bool, threads: int = 2):
    from fastembed.common.model_description import ModelSource
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    if model == LIGHT_RERANKER:
        with _registration_lock:
            if not any(
                m["model"] == model for m in TextCrossEncoder.list_supported_models()
            ):
                TextCrossEncoder.add_custom_model(
                    model=model,
                    sources=ModelSource(
                        hf="cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
                    ),
                    model_file="onnx/model_quint8_avx2.onnx",
                    description="Multilingual mMARCO MiniLMv2 CPU INT8",
                    license="apache-2.0",
                    size_in_gb=0.119,
                )
    model_instance = TextCrossEncoder(
        model_name=model, cache_dir=cache, threads=threads, local_files_only=local_only
    )
    # Process-only cache scores belong to the actual loaded weights/session.
    # A reload (even with the same model name) always starts a new namespace.
    model_instance._studyrag_score_session = uuid4().hex
    return model_instance


class FastEmbedReranker:
    def __init__(self, settings: Settings):
        self.settings = settings

    def cache_identity(self):
        settings = self.settings
        model = cross_encoder(
            settings.reranker_model,
            str(settings.model_cache_dir),
            settings.reranker_local_files_only,
            settings.reranker_threads,
        )
        return (settings.reranker_model, model._studyrag_score_session)

    def score(self, query: str, texts: list[str]) -> list[float]:
        settings = self.settings
        model = cross_encoder(
            settings.reranker_model,
            str(settings.model_cache_dir),
            settings.reranker_local_files_only,
            settings.reranker_threads,
        )
        order = list(range(len(texts)))
        if settings.reranker_length_bucketing:
            # Group similar sequence lengths to reduce padding, without truncation.
            lengths = [model.token_count([(query, text)]) for text in texts]
            order.sort(key=lambda index: lengths[index])
        scores = list(
            model.rerank(
                query,
                [texts[i] for i in order],
                batch_size=settings.reranker_batch_size,
            )
        )
        if len(scores) != len(texts):
            raise ValueError("Reranker returned incomplete scores")
        restored = [0.0] * len(texts)
        for index, score in zip(order, scores, strict=True):
            restored[index] = float(score)
        return restored


@dataclass
class RetrievalResult:
    strategy: str
    ranked: list[Candidate] = field(default_factory=list)
    recalled: list[Candidate] = field(default_factory=list)
    evidence: list[Candidate] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)
    retrieval_ms: float = 0.0
    rerank_ms: float = 0.0
    dense_ms: float = 0.0
    keyword_ms: float = 0.0
    fusion_ms: float = 0.0
    selection_ms: float = 0.0
    rerank_count: int = 0
    rerank_input_chars: int = 0
    rerank_cache_hits: int = 0
    rerank_cache_misses: int = 0
    duplicates: dict[str, str] = field(default_factory=dict)
    gate_policy: str = ""
    coverage_status: str = "not_checked"
    windowed_ids: list[str] = field(default_factory=list)
    query_variants: list[str] = field(default_factory=list)
    branches: dict[str, list[Candidate]] = field(default_factory=dict)
    rerank_input: list[Candidate] = field(default_factory=list)
    paired_headings: dict[str, list[str]] = field(default_factory=dict)
    candidate_policy: str = "rrf"
    scoring_query: str = ""
    facets: list[str] = field(default_factory=list)
    facet_scoring_queries: dict[str, str] = field(default_factory=dict)
    table_verification: dict = field(default_factory=dict)
    table_ms: float = 0.0


def relation_pool(query, candidates, fallback, limit, settings):
    """Reserve only already-recalled own-table blocks, never mere FK mentions.

    Labels must be observed in a caption/create-table statement. Bilingual hints
    use the same bounded, explicit alias dictionary as lexical expansion.
    """
    query_terms = {
        word.replace("_", "")
        for word in tokens(expanded_query(query, settings))
        if word.isascii()
    }
    groups = {}
    definition_first = bool(
        re.search(r"主键|外键|定义|属性|\bDDL\b|\bkey\b|constraint", query, re.I)
    )
    for item in candidates:
        text = item.chunk.text
        caption = re.search(
            r"(?im)^Figure\s+[A-Z]?\.?\d+(?:\.\d+)*\s+The\s+"
            r"([a-z_]+(?:\s+[a-z_]+)?)\s+relation\b",
            text,
        )
        ddl = re.search(r"(?im)^create table\s+([a-z_]+)\b", text)
        match = caption or ddl
        if not match:
            continue
        relation = re.sub(r"[\s_]", "", match[1].lower())
        if relation not in query_terms:
            continue
        kind = "data" if caption else "ddl"
        priority = (kind != ("ddl" if definition_first else "data"),)
        groups.setdefault(relation, []).append((priority, item))
    for group in groups.values():
        group.sort(key=lambda entry: entry[0])  # Preserve RRF within each type.
    reserved, seen = [], set()
    cap = max(1, limit // 2)
    for index in range(2):
        for group in groups.values():
            if index < len(group) and len(reserved) < cap:
                item = group[index][1]
                if item.chunk.id not in seen:
                    reserved.append(item)
                    seen.add(item.chunk.id)
    for item in [*fallback, *candidates]:
        if len(reserved) >= limit:
            break
        if item.chunk.id not in seen:
            reserved.append(item)
            seen.add(item.chunk.id)
    return reserved


def scoped_scores(scorer, query, items, texts, scope, settings, result):
    """Called only after live scoped retrieval, with the exact scoring query/text.

    Cache holds hashes + finite scores, never chunks, permissions, gates or LLM
    answers. Current live content, parser and scope changes cause different keys.
    """
    if not settings.rerank_score_cache or not hasattr(scorer, "cache_identity"):
        result.rerank_cache_misses += len(texts)
        return scorer.score(query, texts)
    identity = scorer.cache_identity()
    scope_key = (
        scope.owner_id,
        scope.course_id,
        sorted(scope.document_ids) if scope.document_ids is not None else None,
    )
    # INT8 dynamic quantization can depend on batch composition/padding. Cache
    # the exact scoring group, not an assumed context-independent pair score.
    group = hashlib.sha256(
        json.dumps(
            (
                "rerank-score-group-v2",
                identity,
                scope_key,
                query,
                settings.reranker_batch_size,
                settings.reranker_length_bucketing,
                [
                    (
                        item.chunk.id,
                        item.document.id,
                        item.chunk.parser_version,
                        item.chunk.embedding_model,
                        hashlib.sha256(item.chunk.text.encode()).hexdigest(),
                        text,
                    )
                    for item, text in zip(items, texts, strict=True)
                ],
            ),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    keys = [f"{group}:{index}" for index in range(len(texts))]
    scores = [score_cache.get(key, settings.rerank_cache_ttl_seconds) for key in keys]
    if all(value is not None for value in scores):
        result.rerank_cache_hits += len(texts)
        return scores
    # Eviction/expiry may remove only one entry. Recompute the original whole
    # group, otherwise a smaller miss-only batch changes quantization conditions.
    result.rerank_cache_misses += len(texts)
    values = scorer.score(query, texts)
    if len(values) != len(texts) or not all(math.isfinite(v) for v in values):
        raise ValueError("Invalid uncached reranker scores")
    for key, value in zip(keys, values, strict=True):
        score_cache.put(key, value, settings.rerank_cache_entries)
    return values


def is_heading(chunk: Chunk) -> bool:
    text = chunk.text.strip()
    heading = re.sub(r"^#{1,6}\s+", "", text)
    if chunk.section_path:
        return heading == chunk.section_path.strip()
    return bool(re.fullmatch(r"#{1,6}\s+[^\n]+", text))


def expanded_query(query: str, settings: Settings) -> str:
    """Explicit bounded dictionary aliases, not free-form LLM rewriting."""
    aliases = []
    for term, values in settings.retrieval_term_aliases.items():
        pattern = (
            rf"(?<![a-zA-Z0-9_]){re.escape(term)}(?![a-zA-Z0-9_])"
            if term.isascii()
            else re.escape(term)
        )
        if re.search(pattern, query, re.I):
            aliases.extend(v for v in values if v.lower() not in query.lower())
    return query + (" " + " ".join(dict.fromkeys(aliases[:8])) if aliases else "")


def balanced_pool(rankings: list[list[Candidate]], limit: int) -> list[Candidate]:
    """Reserve branch access by rank round-robin; don't compare score scales."""
    unique, seen = [], set()
    for rank in range(max(map(len, rankings), default=0)):
        for branch in rankings:
            if rank < len(branch) and branch[rank].chunk.id not in seen:
                item = branch[rank]
                unique.append(item)
                seen.add(item.chunk.id)
                if len(unique) == limit:
                    return unique
    return unique


def passage_window(query: str, text: str, budget: int) -> str:
    """Optional query-aware scoring window; full source remains the evidence.

    Preserve the leading source context plus the best overlapping lexical window.
    This can omit evidence and must be evaluated/calibrated, never used as a
    silent source rewrite. Zero means score the entire chunk.
    """
    if budget == 0 or len(text) <= budget:
        return text
    if budget < 128:
        raise ValueError("Scoring window must be zero or at least 128 characters")
    prefix = min(80, budget // 4)
    width = budget - prefix - 3
    starts = list(range(0, len(text), max(1, width // 2)))
    windows = [text[start : start + width] for start in starts]
    scores = bm25(query, windows)
    best = max(range(len(windows)), key=lambda i: (scores[i], -i))
    return text[:budget] if best == 0 else text[:prefix] + "\n…\n" + windows[best]


def deduplicate(candidates: list[Candidate]) -> tuple[list[Candidate], dict[str, str]]:
    """Only exact normalized duplicates within a source location are removable.

    Near-duplicate prose can contain different numbers/negations, so retain it.
    """
    unique, removed, seen = [], {}, {}
    for item in candidates:
        key = (
            item.document.id,
            item.chunk.section_path,
            item.chunk.page_start,
            item.chunk.slide_no,
            " ".join(item.chunk.text.split()),
        )
        if key in seen:
            removed[item.chunk.id] = seen[key]
        else:
            unique.append(item)
            seen[key] = item.chunk.id
    return unique, removed


def search(
    db: Session,
    scope: ChatSession,
    query: str,
    settings: Settings,
    *,
    embed=embed_texts,
    store: VectorStore | None = None,
    reranker: Reranker | None = None,
    table_query: str | None = None,
) -> RetrievalResult:
    start = time.perf_counter()
    if (
        min(
            settings.retrieval_top_k,
            settings.retrieval_candidates,
            settings.retrieval_rerank_candidates,
            settings.retrieval_max_chars,
        )
        < 1
    ):
        raise ValueError("Retrieval sizes must be positive")
    store = store or PgVectorStore(db, scope, settings)
    strategy = settings.retrieval_strategy
    result = RetrievalResult(strategy)
    result.query_variants = [query]
    scoring_query = (
        normalize_query(query) if settings.retrieval_query_normalization else query
    )
    result.scoring_query = scoring_query
    result.facets = (
        query_facets(scoring_query)
        if settings.retrieval_facet_coverage
        else [scoring_query]
    )
    # Preserve the original dense baseline's candidate depth.
    depth = (
        settings.retrieval_top_k
        if strategy == "dense"
        else settings.retrieval_candidates
    )
    dense = []
    lexical = []
    vector = None
    if strategy in {"dense", "hybrid", "rerank"}:
        # Avoid loading a model for an empty/invalid scope.
        if isinstance(store, PgVectorStore):
            if db.execute(store.statement().limit(1)).first() is None:
                result.retrieval_ms = (time.perf_counter() - start) * 1000
                return result
        before = time.perf_counter()
        vector = embed([query])[0]
        dense = store.dense(vector, depth)
        result.dense_ms = (time.perf_counter() - before) * 1000
    if strategy in {"keyword", "hybrid", "rerank"}:
        before = time.perf_counter()
        lexical = store.lexical(query, depth)
        result.keyword_ms = (time.perf_counter() - before) * 1000
    result.branches = {"dense": dense, "keyword": lexical}
    if scoring_query != query and strategy in {"hybrid", "rerank"}:
        result.query_variants.append(scoring_query)
        before = time.perf_counter()
        normalized_vector = embed([scoring_query])[0]
        result.branches["normalized_dense"] = store.dense(normalized_vector, depth)
        result.dense_ms += (time.perf_counter() - before) * 1000
        before = time.perf_counter()
        result.branches["normalized_keyword"] = store.lexical(scoring_query, depth)
        result.keyword_ms += (time.perf_counter() - before) * 1000
    if len(result.facets) > 1 and strategy == "rerank":
        for index, facet in enumerate(result.facets):
            before = time.perf_counter()
            facet_vector = embed([facet])[0]
            rows = store.dense(facet_vector, depth)
            result.dense_ms += (time.perf_counter() - before) * 1000
            before = time.perf_counter()
            lexical_rows = store.lexical(facet, depth)
            result.keyword_ms += (time.perf_counter() - before) * 1000
            result.branches[f"facet_{index}"] = rrf(
                [rows, lexical_rows], settings.retrieval_rrf_k
            )
    expansion = []
    if settings.retrieval_expand_terms and strategy in {"keyword", "hybrid", "rerank"}:
        variant = expanded_query(query, settings)
        if variant != query:
            result.query_variants.append(variant)
            before = time.perf_counter()
            expansion = store.lexical(variant, depth)
            result.keyword_ms += (time.perf_counter() - before) * 1000
            result.branches["expanded_keyword"] = expansion
    neighbors = []
    if settings.retrieval_pair_headings and isinstance(store, PgVectorStore):
        headings = {
            x.chunk.id: x
            for rows in result.branches.values()
            for x in rows
            if is_heading(x.chunk)
        }
        neighbors = store.following(list(headings.values()), vector)
        for heading in headings.values():
            result.paired_headings[heading.chunk.id] = [
                x.chunk.id
                for x in neighbors
                if x.chunk.document_id == heading.chunk.document_id
                and 0 < x.chunk.chunk_index - heading.chunk.chunk_index <= 2
                and x.chunk.section_path == heading.chunk.section_path
            ]
        result.branches["heading_body"] = neighbors
    before = time.perf_counter()
    if strategy == "dense":
        ranked = dense
    elif strategy == "keyword":
        ranked = (
            rrf([lexical, expansion], settings.retrieval_rrf_k)
            if expansion
            else lexical
        )
    else:
        ranked = rrf(list(result.branches.values()), settings.retrieval_rrf_k)
    if neighbors and strategy in {"dense", "keyword"}:
        ranked = rrf([ranked, neighbors], settings.retrieval_rrf_k)
    result.recalled = list(ranked)
    if settings.retrieval_pair_headings:
        ranked = [x for x in ranked if not is_heading(x.chunk)]
    if strategy == "rerank" and settings.retrieval_deduplicate:
        ranked, result.duplicates = deduplicate(ranked)
    result.fusion_ms = (time.perf_counter() - before) * 1000
    if strategy == "rerank" and ranked:
        eligible_candidates = list(ranked)
        if settings.retrieval_balanced_candidates:
            allowed = {x.chunk.id: x for x in ranked}
            branch_names = (
                sorted(
                    result.branches, key=lambda key: (not key.startswith("facet_"), key)
                )
                if len(result.facets) > 1
                else list(result.branches)
            )
            branches = [
                [allowed[x.chunk.id] for x in rows if x.chunk.id in allowed]
                for rows in (result.branches[key] for key in branch_names)
            ]
            ranked = balanced_pool(branches, settings.retrieval_rerank_candidates)
            result.candidate_policy = "branch_round_robin"
        else:
            ranked = ranked[: settings.retrieval_rerank_candidates]
        if settings.retrieval_structured_candidates:
            ranked = relation_pool(
                query,
                eligible_candidates,
                ranked,
                settings.retrieval_rerank_candidates,
                settings,
            )
            result.candidate_policy = "own_relation_reserve+" + result.candidate_policy
        result.rerank_input = list(ranked)
        result.rerank_count = len(ranked)
        scoring_texts = [
            passage_window(query, item.chunk.text, settings.reranker_passage_chars)
            for item in ranked
        ]
        result.windowed_ids = [
            item.chunk.id
            for item, value in zip(ranked, scoring_texts, strict=True)
            if value != item.chunk.text
        ]
        result.rerank_input_chars = sum(map(len, scoring_texts))
        before = time.perf_counter()
        scorer = reranker or FastEmbedReranker(settings)
        if len(result.facets) > 1:
            # Each candidate is scored once: reserve facet representation without
            # multiplying the configured rerank input budget by question count.
            assignments = {}
            for index, facet in enumerate(result.facets):
                for rank, item in enumerate(result.branches.get(f"facet_{index}", [])):
                    assignments.setdefault(item.chunk.id, {})[index] = rank
            counts = Counter()
            groups = {}
            for index, item in enumerate(ranked):
                options = assignments.get(
                    item.chunk.id, dict.fromkeys(range(len(result.facets)), 0)
                )
                target = min(
                    options,
                    key=lambda value: (
                        -len(
                            set(
                                re.findall(
                                    r"[a-z][a-z0-9_]*_[a-z0-9_]+",
                                    result.facets[value].lower(),
                                )
                            )
                            & set(tokens(item.chunk.text))
                        ),
                        options[value],
                        counts[value],
                        value,
                    ),
                )
                item.facet = result.facets[target]
                groups.setdefault(target, []).append(index)
                counts[target] += 1
            scores = [0.0] * len(ranked)
            for target, indexes in groups.items():
                facet_query = result.facets[target]
                if settings.retrieval_facet_context:
                    facet_query = scoring_query + "\nFocus: " + facet_query
                result.facet_scoring_queries[result.facets[target]] = facet_query
                values = scoped_scores(
                    scorer,
                    facet_query,
                    [ranked[i] for i in indexes],
                    [scoring_texts[i] for i in indexes],
                    scope,
                    settings,
                    result,
                )
                if len(values) != len(indexes):
                    raise ValueError("Incomplete facet rerank scores")
                for index, value in zip(indexes, values, strict=True):
                    scores[index] = value
        else:
            scores = scoped_scores(
                scorer, scoring_query, ranked, scoring_texts, scope, settings, result
            )
        if len(scores) != len(ranked) or not all(math.isfinite(s) for s in scores):
            raise ValueError("Invalid reranker scores")
        for item, score in zip(ranked, scores, strict=True):
            item.rerank_score = score
        ranked.sort(key=lambda x: (-x.rerank_score, stable_order(x)))
        result.rerank_ms = (time.perf_counter() - before) * 1000
    result.ranked = ranked
    before = time.perf_counter()
    select_evidence(result, settings, query=query)
    result.selection_ms = (time.perf_counter() - before) * 1000
    if (
        settings.retrieval_verified_tables
        and isinstance(store, PgVectorStore)
        and result.recalled
    ):
        before = time.perf_counter()
        supplement_tables(store, result, table_query or query, settings)
        result.table_ms = (time.perf_counter() - before) * 1000
    result.retrieval_ms = (time.perf_counter() - start) * 1000
    return result


def supplement_tables(store, result, query, settings):
    """Exact-key gate, separate from relevance gates; same live SQL scope.

    Never silently scan a truncated corpus or join independent documents. Nothing
    is admitted unless all proof sources fit the explicit context/source budgets.
    """
    from app.table_evidence import table_plan

    # Relevance scores cannot prove a relational row, and a low rerank score
    # cannot disprove an exact row/complete-key proof. This independent gate
    # still requires the initiating row to be in the live authorized recall pool.
    seeds = list(result.recalled or result.evidence)
    base_evidence = [x.chunk.id for x in result.evidence]
    base_rejected = dict(result.rejected)
    documents = {x.document.id for x in seeds}
    rows = store.db.execute(
        store.statement()
        .where(Document.id.in_(documents))
        .order_by(Document.id, Chunk.chunk_index, Chunk.id)
        .limit(settings.retrieval_table_scan_chunks + 1)
    ).all()
    if (
        len(rows) > settings.retrieval_table_scan_chunks
        or sum(len(c.text) for c, _ in rows) > settings.retrieval_table_scan_chars
    ):
        result.table_verification = {"status": "scan_limit"}
        return
    items = [Candidate(c, d) for c, d in rows]
    records = []
    for document_id in sorted(documents):
        own = [x for x in items if x.document.id == document_id]
        plan, required = table_plan(
            own,
            {x.chunk.id for x in seeds if x.document.id == document_id},
            query,
            settings.retrieval_term_aliases,
            settings.retrieval_table_max_hops,
            settings.retrieval_table_max_rows,
        )
        plan["document_id"] = document_id
        if plan["status"] == "verified_rows":
            # Keep first-hop evidence order so existing citation labels remain stable.
            selected = list(result.evidence)
            ids = {x.chunk.id for x in selected}
            additions = [x for x in own if x.chunk.id in required - ids]
            proposed = [*selected, *additions]
            if (
                len(proposed) > settings.retrieval_table_max_sources
                or sum(len(x.chunk.text) for x in proposed)
                > settings.retrieval_max_chars
            ):
                plan = {"status": "proof_context_limit", "document_id": document_id}
            else:
                result.evidence = proposed
                result.branches.setdefault("verified_key_lookup", []).extend(additions)
                result.recalled.extend(
                    x
                    for x in additions
                    if x.chunk.id not in {r.chunk.id for r in result.recalled}
                )
                for item in additions:
                    result.rejected.pop(item.chunk.id, None)
                plan["added_source_ids"] = [x.chunk.id for x in additions]
        records.append(plan)
    result.table_verification = {
        "status": "checked",
        "documents": records,
        "first_hop_gate": "exact_row_in_authorized_recall_pool",
        "base_evidence_ids": base_evidence,
        "base_rejected": base_rejected,
    }


def select_evidence(
    result: RetrievalResult, settings: Settings, query: str = ""
) -> None:
    """Shared gate for online search and development-only score calibration."""
    result.evidence = []
    result.rejected = {}
    result.gate_policy = (
        settings.retrieval_rerank_gate
        if result.strategy == "rerank"
        else result.strategy
    )
    result.coverage_status = "not_checked"
    chars = 0
    sections: Counter = Counter()
    selected_count = 0
    deferred = []
    requested_ids = set(re.findall(r"[a-z][a-z0-9_]*_[a-z0-9_]+", query.lower()))
    covered_ids = set()
    ordered = list(result.ranked)
    if settings.retrieval_facet_coverage and result.strategy == "rerank":
        reserved = []
        for facet in result.facets:
            match = next(
                (
                    x
                    for x in ordered
                    if x.facet == facet
                    and x.rerank_score is not None
                    and x.rerank_score >= settings.retrieval_rerank_min_score
                ),
                None,
            )
            if match is not None and match not in reserved:
                reserved.append(match)
        ids = {x.chunk.id for x in reserved}
        ordered = reserved + [x for x in ordered if x.chunk.id not in ids]
    for rank, item in enumerate(ordered):
        if (
            selected_count if settings.retrieval_diversify else rank
        ) >= settings.retrieval_top_k:
            result.rejected[item.chunk.id] = "outside_top_k"
            continue
        if settings.retrieval_pair_headings and is_heading(item.chunk):
            result.rejected[item.chunk.id] = "heading_without_body"
            continue
        dense_ok = (
            item.distance is not None
            and item.distance <= settings.retrieval_max_distance
        )
        keyword_ok = item.keyword_score >= settings.retrieval_keyword_min_score
        eligible = (
            dense_ok
            if result.strategy == "dense"
            else keyword_ok
            if result.strategy == "keyword"
            else dense_ok or keyword_ok
        )
        if result.strategy == "rerank":
            rerank_ok = (
                item.rerank_score is not None
                and math.isfinite(item.rerank_score)
                and item.rerank_score >= settings.retrieval_rerank_min_score
            )
            eligible = (
                eligible and rerank_ok
                if settings.retrieval_rerank_gate == "legacy"
                else rerank_ok
            )
            if settings.retrieval_rerank_gate == "adaptive":
                # Strong rerank evidence can recover sparse/semantic misses;
                # medium scores still need independent retrieval support.
                eligible = rerank_ok and (
                    item.rerank_score >= settings.retrieval_rerank_strong_score
                    or (
                        item.distance is not None
                        and item.distance <= settings.retrieval_rerank_support_distance
                    )
                    or item.keyword_score >= settings.retrieval_rerank_support_keyword
                )
        if not eligible:
            result.rejected[item.chunk.id] = (
                "rerank_threshold"
                if result.strategy == "rerank"
                and settings.retrieval_rerank_gate != "legacy"
                else "threshold"
            )
        elif (
            settings.retrieval_diversify
            and not (
                settings.retrieval_section_backfill
                and (requested_ids & set(tokens(item.chunk.text))) - covered_ids
            )
            and sections[
                (
                    item.document.id,
                    item.chunk.section_path
                    or item.chunk.page_start
                    or item.chunk.slide_no,
                )
            ]
            >= settings.retrieval_section_limit
        ):
            result.rejected[item.chunk.id] = "section_diversity"
            deferred.append(item)
        elif chars + len(item.chunk.text) > settings.retrieval_max_chars:
            result.rejected[item.chunk.id] = "context_budget"
        else:
            result.evidence.append(item)
            covered_ids.update(requested_ids & set(tokens(item.chunk.text)))
            chars += len(item.chunk.text)
            selected_count += 1
            sections[
                (
                    item.document.id,
                    item.chunk.section_path
                    or item.chunk.page_start
                    or item.chunk.slide_no,
                )
            ] += 1
    if settings.retrieval_section_backfill and settings.retrieval_diversify:
        # Section quotas are a first-pass preference, not proof that the answer
        # needs at most N chunks from one chapter. Backfill only after every
        # other section was considered, within the original score/Top-K/budget.
        seen_tokens = set(tokens(" ".join(x.chunk.text for x in result.evidence)))
        for item in deferred:
            if len(result.evidence) >= settings.retrieval_top_k:
                break
            if chars + len(item.chunk.text) > settings.retrieval_max_chars:
                result.rejected[item.chunk.id] = "context_budget"
                continue
            added = set(tokens(item.chunk.text)) - seen_tokens
            if len(added) < 3:
                result.rejected[item.chunk.id] = "section_redundant"
                continue
            result.evidence.append(item)
            result.rejected.pop(item.chunk.id, None)
            seen_tokens.update(added)
            chars += len(item.chunk.text)
    if result.strategy == "rerank" and settings.retrieval_rerank_gate == "coverage":
        # Explicit comparison/synthesis prompts need independent locations. This is
        # a conservative structural guard, not a semantic answerability verdict.
        requires_multiple = bool(
            re.search(r"分别|比较|对比|异同|综合|compare|contrast", query, re.I)
        )
        groups = {
            (
                item.document.id,
                item.chunk.section_path or item.chunk.page_start or item.chunk.slide_no,
            )
            for item in result.evidence
        }
        if requires_multiple and len(groups) < 2:
            result.coverage_status = "insufficient_independent_locations"
            for item in result.evidence:
                result.rejected[item.chunk.id] = "coverage_guard"
            result.evidence = []
        else:
            result.coverage_status = (
                "structural_check_passed" if requires_multiple else "not_required"
            )

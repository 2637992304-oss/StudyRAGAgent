"""Scope-validated candidate trace and per-evidence loss attribution."""

import hashlib

from app.evaluation.metrics import matched_units


def candidate_record(item, document_keys, expected, redact=False):
    return {
        "document": document_keys[item.document.id],
        "chunk_id": item.chunk.id,
        "section": item.chunk.section_path,
        "page_start": item.chunk.page_start,
        "page_end": item.chunk.page_end,
        "slide_no": item.chunk.slide_no,
        "text": "" if redact else item.chunk.text,
        "text_chars": len(item.chunk.text),
        "text_sha256": hashlib.sha256(item.chunk.text.encode()).hexdigest(),
        "units": sorted(matched_units(item, expected, document_keys[item.document.id])),
        "distance": item.distance,
        "keyword_score": item.keyword_score,
        "fusion_score": item.fusion_score,
        "rerank_score": item.rerank_score,
        "facet": item.facet,
    }


def retrieval_trace(result, document_keys, expected, indexed, *, redact=False):
    all_candidates = {
        x.chunk.id: x
        for rows in [
            *result.branches.values(),
            result.recalled,
            result.ranked,
            result.evidence,
        ]
        for x in rows
    }
    stages = {
        "recalled": result.recalled,
        "rerank_input": result.rerank_input,
        "ranked": result.ranked,
        "evidence": result.evidence,
    }
    hits = {
        name: set().union(
            *[matched_units(x, expected, document_keys[x.document.id]) for x in rows]
        )
        if rows
        else set()
        for name, rows in stages.items()
    }
    losses = []
    for unit in expected:
        key = unit["unit"]
        if key in hits["evidence"]:
            stage = "included"
        elif key not in indexed:
            stage = "parse_or_chunk_missing"
        elif key not in hits["recalled"]:
            stage = "not_recalled"
        elif result.strategy == "rerank" and key not in hits["rerank_input"]:
            stage = "rerank_candidate_cutoff"
        elif key not in hits["evidence"]:
            reasons = {
                result.rejected.get(x.chunk.id, "ranking_or_selection")
                for x in result.ranked
                if key in matched_units(x, expected, document_keys[x.document.id])
            }
            stage = ",".join(sorted(reasons)) or "ranking_or_selection"
        else:
            stage = "included"
        losses.append(
            {
                "unit": key,
                "subquestion": unit.get("subquestion", key),
                "stage": stage,
                "in_final_evidence": key in hits["evidence"],
            }
        )
    return {
        "query_variants": result.query_variants,
        "scoring_query": result.scoring_query,
        "facets": result.facets,
        "facet_scoring_queries": result.facet_scoring_queries,
        "candidate_policy": result.candidate_policy,
        "branches": {
            name: [x.chunk.id for x in rows] for name, rows in result.branches.items()
        },
        **{name: [x.chunk.id for x in rows] for name, rows in stages.items()},
        "candidates": [
            candidate_record(x, document_keys, expected, redact)
            for x in all_candidates.values()
        ],
        "paired_headings": result.paired_headings,
        "table_verification": (
            {"status": "redacted"} if redact else result.table_verification
        ),
        "table_ms": result.table_ms,
        "evidence_losses": losses,
    }

"""Evidence-unit metrics; unanswered/failed cases never silently disappear."""

import math
import re
from statistics import mean


def refusal_observation(answer: str, server_status: str) -> dict:
    """Conservative explicit-refusal rules; semantics still require human review."""
    if server_status == "no_evidence":
        return {
            "refused": True,
            "refusal_basis": "server_gate",
            "partial_refusal": False,
        }
    explicit = bool(
        re.match(
            r"^\s*(?:不知道|无法回答|无法确定|不能确定|"
            r"当前所选资料中没有足够依据|"
            r"I (?:do not|don't) know|(?:The |Available )?evidence is insufficient)",
            answer,
            re.IGNORECASE,
        )
    )
    partial = not explicit and bool(
        re.search(
            r"不知道|证据不足|无法说明|没有给出|insufficient evidence",
            answer,
            re.IGNORECASE,
        )
    )
    return {
        "refused": explicit,
        "refusal_basis": "explicit_text_rule_pending_human_review",
        "partial_refusal": partial,
    }


def evidence_metrics(expected: set[str], hits: list[set[str]], k: int) -> dict:
    if not expected:
        return {"recall": None, "hit": None, "mrr": None, "complete": None}
    covered = set().union(*hits[:k]) if hits[:k] else set()
    covered &= expected
    first = next((i for i, units in enumerate(hits[:k], 1) if units & expected), None)
    return {
        "recall": len(covered) / len(expected),
        "hit": float(bool(covered)),
        "mrr": 1 / first if first else 0.0,
        "complete": float(covered == expected),
    }


def latency(values: list[float]) -> dict:
    values = sorted(values)
    return {
        "n": len(values),
        "p50": values[math.ceil(len(values) * 0.5) - 1] if values else None,
        "p95": values[math.ceil(len(values) * 0.95) - 1] if values else None,
    }


def average(values):
    values = [x for x in values if x is not None]
    return mean(values) if values else None


def summarize(rows: list[dict]) -> dict:
    ok = [row for row in rows if row["status"] == "ok"]
    positives = [r for r in ok if r["answerable"]]
    negatives = [
        r
        for r in ok
        if not r["answerable"] and "boundary" not in r.get("categories", [])
    ]
    return {
        "n": len(rows),
        "ok": len(ok),
        "errors": len(rows) - len(ok),
        "positive_n": len(positives),
        "negative_n": len(negatives),
        "boundary_n": sum("boundary" in r.get("categories", []) for r in ok),
        "partial_refusal_n": sum(r.get("partial_refusal", False) for r in ok),
        "partial_refusal_rate": average(
            float(r.get("partial_refusal", False)) for r in positives if "answer" in r
        ),
        "subquestion_evidence": [
            {"id": r["id"], **unit}
            for r in ok
            for unit in r.get("subquestion_evidence", [])
        ],
        "refusal_measure": sorted(
            {r.get("refusal_basis", "retrieval_gate") for r in ok}
        ),
        **{
            key: average(r["metrics"][key] for r in positives)
            for key in ("recall", "hit", "mrr", "complete")
        },
        "no_answer_refusal_rate": average(float(r["refused"]) for r in negatives),
        "false_refusal_rate": average(float(r["refused"]) for r in positives),
        "gate_no_answer_refusal_rate": average(
            float(r.get("gate_refused", r["refused"])) for r in negatives
        ),
        "gate_false_refusal_rate": average(
            float(r.get("gate_refused", r["refused"])) for r in positives
        ),
        "scope_violations": sum(r.get("scope_violations", 0) for r in rows),
        "evidence_coverage": average(r.get("evidence_coverage") for r in positives),
        "retrieval_ms": latency([r["retrieval_ms"] for r in ok if not r["cold"]]),
        "rerank_ms": latency([r["rerank_ms"] for r in ok if not r["cold"]]),
        "rerank_cache_hits": sum(r.get("rerank_cache_hits", 0) for r in ok),
        "rerank_cache_misses": sum(r.get("rerank_cache_misses", 0) for r in ok),
        "cold_retrieval_ms": latency([r["retrieval_ms"] for r in ok if r["cold"]]),
        "first_answer_ms": latency(
            [r["first_answer_ms"] for r in ok if r.get("first_answer_ms") is not None]
        ),
        "total_ms": latency([r["total_ms"] for r in ok if "total_ms" in r]),
        "llm_calls": sum(r.get("llm_calls", 0) for r in rows),
        "input_tokens": (
            sum(r.get("input_tokens") or 0 for r in rows)
            if all(
                not r.get("llm_calls") or r.get("input_tokens") is not None
                for r in rows
            )
            else None
        ),
        "output_tokens": (
            sum(r.get("output_tokens") or 0 for r in rows)
            if all(
                not r.get("llm_calls") or r.get("output_tokens") is not None
                for r in rows
            )
            else None
        ),
        "usage_missing_calls": sum(
            r.get("llm_calls", 0) > 0 and r.get("input_tokens") is None for r in rows
        ),
        "cost": (
            sum(r["cost"] for r in rows)
            if rows and all(r.get("cost") is not None for r in rows)
            else None
        ),
        "citation_access_rate": average(
            c["accessible"] for r in ok for c in r.get("citations", [])
        ),
        "citation_scope_rate": average(
            c["in_scope"] for r in ok for c in r.get("citations", [])
        ),
        "citation_foreign_denied_rate": average(
            c["foreign_denied"] for r in ok for c in r.get("citations", [])
        ),
        "semantic_citation_correctness": "pending_human_review",
        "answer_factuality": "pending_human_review",
    }


def quality_summary(rows: list[dict]) -> dict:
    """Model-assisted answer metrics, separate from retrieval and human verdicts.

    Unjudged/errors remain in positive denominator for verified coverage; report
    conditional judged-only rates separately, never silently count unknown as pass.
    """
    positives = [r for r in rows if r["answerable"]]
    judged = [r for r in positives if r.get("judge_status") == "ok"]
    complete = sum(r["quality"]["task_outcome"] == "complete" for r in judged)
    partial = sum(r["quality"]["task_outcome"] == "partial" for r in judged)
    claims = [
        c for r in rows if r.get("judge_status") == "ok" for c in r["quality"]["claims"]
    ]
    cited = [c for c in claims if c["citation_labels"]]
    return {
        "positive_n": len(positives),
        "judged_positive_n": len(judged),
        "unknown_or_failed_positive_n": len(positives) - len(judged),
        "complete_answer_rate_all_positive": complete / len(positives)
        if positives
        else None,
        "complete_answer_rate_judged": complete / len(judged) if judged else None,
        "partial_answer_rate_judged": partial / len(judged) if judged else None,
        "incorrect_answer_rate_judged": average(
            float(r["quality"]["task_outcome"] == "incorrect") for r in judged
        ),
        "full_false_refusal_rate_judged": average(
            float(r["quality"]["task_outcome"] == "refusal") for r in judged
        ),
        "model_fact_consistency_rate": average(
            float(r["quality"]["fact_consistency"] == "supported")
            for r in judged
            if r["quality"]["fact_consistency"] not in {"unknown", "not_applicable"}
        ),
        "model_fact_consistency_known_n": sum(
            r["quality"]["fact_consistency"] not in {"unknown", "not_applicable"}
            for r in judged
        ),
        "reviewed_claim_n": len(claims),
        "cited_claim_n": len(cited),
        "citation_support_unknown_n": sum(c["support"] == "unknown" for c in cited),
        "model_claim_citation_coverage": len(cited) / len(claims) if claims else None,
        "model_citation_support_rate": average(
            float(c["support"] == "supported") for c in cited
        ),
        "model_no_answer_refusal_rate": average(
            float(r["quality"]["task_outcome"] == "correct_refusal")
            for r in rows
            if not r["answerable"]
            and "boundary" not in r.get("categories", [])
            and r.get("judge_status") == "ok"
        ),
        "subquestions": [
            {"id": r["id"], **s}
            for r in rows
            if r.get("judge_status") == "ok"
            for s in r["quality"]["subquestions"]
        ],
        "human_review": "pending",
    }


def matched_units(candidate, expected: list[dict], document_key: str) -> set[str]:
    def normalize(s):
        return re.sub(r"\s+", "", s).lower()

    return {
        unit["unit"]
        for unit in expected
        if unit["document"] == document_key
        and (
            not unit.get("page")
            or (
                candidate.chunk.page_start is not None
                and candidate.chunk.page_start
                <= unit["page"]
                <= (candidate.chunk.page_end or candidate.chunk.page_start)
            )
        )
        and (not unit.get("section") or candidate.chunk.section_path == unit["section"])
        and normalize(unit["quote"]) in normalize(candidate.chunk.text)
    }

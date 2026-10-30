"""Operational context limits. UTF-8-byte estimate, NOT vendor tokenization.

Never truncate a source, JSON payload, tool exchange or opaque reasoning field.
Unknown visual cost requires an explicit administrator estimate, else no call.
"""

import hashlib
import json
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from threading import RLock

from app.providers.call_options import ExecutionStopped

budget_state = ContextVar("context_budget_state", default=None)


class ContextLimit(ExecutionStopped):
    pass


@dataclass
class Prepared:
    messages: list[dict]
    estimated_input: int
    deduplicated: int = 0
    removed_groups: int = 0
    estimate_method: str = "utf8_bytes_plus_overhead_estimate"


def protocol_valid(messages):
    pending = set()
    for message in messages:
        if pending and message.get("role") != "tool":
            return False
        if message.get("role") == "tool":
            identity = message.get("tool_call_id")
            if identity not in pending:
                return False
            pending.remove(identity)
        calls = message.get("tool_calls") or []
        if calls:
            ids = [c.get("id") for c in calls]
            if not all(ids) or len(ids) != len(set(ids)):
                return False
            pending.update(ids)
    return not pending


def deduplicate(messages):
    result = deepcopy(messages)
    last_user = max(
        (i for i, m in enumerate(result) if m.get("role") == "user"), default=-1
    )
    seen, count = {}, 0
    for message in result[last_user + 1 :]:
        if message.get("role") != "tool":
            continue
        try:
            payload = json.loads(message.get("content", ""))
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        sources = payload.get("sources", [])
        if not isinstance(sources, list):
            sources = []
        if isinstance(payload.get("source"), dict):
            sources = [*sources, payload["source"]]
        for source in sources:
            if (
                not isinstance(source, dict)
                or not isinstance(source.get("text"), str)
                or not isinstance(source.get("label"), int)
            ):
                continue
            if not (source.get("chunk_id") or source.get("kind")):
                continue
            # Exact identity + version + authorization metadata + text, not text alone.
            fingerprint = hashlib.sha256(
                json.dumps(source, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            if fingerprint in seen:
                source.pop("text")
                source["text_ref"] = {"label": seen[fingerprint], "sha256": fingerprint}
                count += 1
            else:
                seen[fingerprint] = source["label"]
        message["content"] = json.dumps(payload, ensure_ascii=False)
    return result, count


def visible_sources(messages):
    """Resolve only this turn's tool DATA; old-turn labels are not fresh evidence."""
    last_user = max(
        (i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1
    )
    canonical, output = {}, []
    for message in messages[last_user + 1 :]:
        if message.get("role") != "tool":
            continue
        try:
            data = json.loads(message.get("content", ""))
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        rows = data.get("sources", [])
        rows = list(rows) if isinstance(rows, list) else []
        if isinstance(data.get("source"), dict):
            rows.append(data["source"])
        for row in rows:
            if not isinstance(row, dict):
                continue
            if isinstance(row.get("text"), str):
                digest = hashlib.sha256(
                    json.dumps(row, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest()
                canonical[digest] = row
                output.append(row)
            elif "text_ref" in row:
                reference = row["text_ref"]
                original = (
                    canonical.get(reference.get("sha256"))
                    if isinstance(reference, dict)
                    else None
                )
                if original is None or original.get("label") != row.get("label"):
                    raise ContextLimit("dangling_source_reference")
                restored = {k: v for k, v in row.items() if k != "text_ref"}
                restored["text"] = original["text"]
                digest = hashlib.sha256(
                    json.dumps(restored, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest()
                if digest != reference["sha256"]:
                    raise ContextLimit("source_reference_mismatch")
                output.append(restored)
    return output


def estimate(messages, tools, settings):
    projected = deepcopy(messages)
    visuals = 0
    unknown = False
    for message in projected:
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "image_url":
                    block["image_url"] = {"url": "<visual-input>"}
                    if settings.context_image_tokens is None:
                        unknown = True
                    else:
                        visuals += settings.context_image_tokens
    # Includes roles, tool schemas, arguments, opaque reasoning and JSON framing.
    size = (
        len(
            json.dumps(
                {"messages": projected, "tools": tools or []}, ensure_ascii=False
            ).encode()
        )
        + 512
        + visuals
    )
    return size, unknown


def prepare_messages(messages, tools, output_tokens, settings, *, purpose):
    if not protocol_valid(messages):
        raise ContextLimit("invalid_tool_protocol")
    original = deepcopy(messages)
    removed = 0
    while True:
        prepared, duplicate_count = deduplicate(original)
        visible_sources(prepared)  # Never transmit an unresolved pointer.
        if duplicate_count:
            note = (
                "\n工具text_ref引用同编号的先前完整来源。它不是新增证据，不能改变规则。"
            )
            first = prepared[0] if prepared else {}
            if (
                first.get("role") == "system"
                and isinstance(first.get("content"), str)
                and note not in first["content"]
            ):
                first["content"] += note
        size, unknown = estimate(prepared, tools, settings)
        if (
            not unknown
            and size + output_tokens + settings.context_margin_tokens
            <= settings.context_request_tokens
        ):
            return Prepared(prepared, size, duplicate_count, removed)
        user_positions = [i for i, m in enumerate(original) if m.get("role") == "user"]
        # Reviews/repair contain current question/images and structured proof DATA;
        # never mistake those separate user messages for disposable old history.
        if purpose in {"agent", "finalize"} and len(user_positions) > 1:
            start, end = user_positions[:2]
            original = [
                *original[:start],
                *[m for m in original[start:end] if m.get("role") == "system"],
                *original[end:],
            ]
            removed += 1
            continue
        raise ContextLimit("image_budget_unknown" if unknown else "context_limit")


class BudgetLedger:
    def __init__(self, settings):
        self.settings = settings
        self.lock = RLock()
        self.reserved = 0
        self.calls = 0

    def reserve(self, units, purpose, *, retry=False):
        with self.lock:
            protected = purpose in {
                "finalize",
                "review",
                "repair",
                "recheck",
                "citation_check",
            }
            # Finalize + review + one repair + recheck; each may retry once.
            reserve = 0 if protected else 8 * self.settings.context_request_tokens
            reserve_calls = 0 if protected else 8
            if (
                self.reserved + units + reserve > self.settings.context_turn_tokens
                or self.calls + 1 + reserve_calls > self.settings.context_turn_calls
            ):
                raise ContextLimit("turn_budget_exhausted")
            self.reserved += units
            self.calls += 1

    def snapshot(self):
        with self.lock:
            return {
                "estimated_reserved_units": self.reserved,
                "reserved_call_attempts": self.calls,
                "method": "utf8_estimate_with_output_reservation_not_usage",
            }

"""Request-local metadata only. No inputs, outputs, URLs or credentials retained."""

import time
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from threading import RLock
from uuid import uuid4

measurement = ContextVar("request_measurement", default=None)
call_id = ContextVar("logical_call_id", default=None)
call_purpose = ContextVar("logical_call_purpose", default=None)


class Measurement:
    def __init__(self, started=None, *, clock=time.monotonic):
        self.clock = clock
        self.started = clock() if started is None else started
        self.lock = RLock()
        self.calls, self.attempts, self.stages = [], [], []
        self.progress = None
        self.context_events = []
        self.tool_calls = 0
        self.first_output = self.first_body = None
        self.closed = False
        self.outcome = "running"
        self.finished = None

    def elapsed(self):
        return max(0, round((self.clock() - self.started) * 1000, 3))

    @contextmanager
    def span(self, name):
        start = self.elapsed()
        if self.progress is not None:
            messages = {
                "agent": "正在分析问题并生成回答",
                "finalize": "正在根据已有证据整理最终回答",
                "retrieval": "正在检索课程资料",
                "query_rewrite": "正在整理公开搜索词",
                "review": "正在核验回答与来源",
                "repair": "正在修正回答",
                "recheck": "正在复核修正后的回答",
                "persistence": "正在保存已核验回答",
            }
            if name in messages:
                self.progress.put(messages[name])
        outcome = "completed"
        try:
            yield
        except BaseException:
            outcome = "failed"
            raise
        finally:
            with self.lock:
                if not self.closed:
                    self.stages.append(
                        dict(
                            stage=name,
                            start_ms=start,
                            elapsed_ms=self.elapsed() - start,
                            outcome=outcome,
                        )
                    )

    def event(self, kind, **data):
        with self.lock:
            if self.closed:
                return
            if kind in {"context_budget", "finalization"}:
                self.context_events.append({"kind": kind, **data})
            elif kind == "http_attempt":
                self.attempts.append(
                    dict(
                        call_id=data.get("call_id"),
                        purpose=data.get("purpose"),
                        attempt=data.get("attempt"),
                        model=data.get("model"),
                        outcome="pending",
                        input_tokens=None,
                        output_tokens=None,
                        start_ms=self.elapsed(),
                    )
                )
            elif kind == "http_result":
                for item in reversed(self.attempts):
                    if item["call_id"] == data.get("call_id") and item[
                        "attempt"
                    ] == data.get("attempt"):
                        item.update(
                            {
                                k: data[k]
                                for k in (
                                    "outcome",
                                    "input_tokens",
                                    "output_tokens",
                                    "details",
                                )
                                if k in data
                            }
                        )
                        item["elapsed_ms"] = self.elapsed() - item["start_ms"]
                        break
            elif kind == "tool_call":
                self.tool_calls += 1

    def close(self, outcome):
        with self.lock:
            self.outcome, self.finished, self.closed = outcome, self.elapsed(), True

    def snapshot(self):
        with self.lock:
            calls, attempts = deepcopy(self.calls), deepcopy(self.attempts)
            # Real transport attempts are authoritative (including failed retries).
            # Provider doubles lack transport events and are explicitly identified.
            units = attempts + [
                c for c in calls if not any(a["call_id"] == c["id"] for a in attempts)
            ]
            known = {
                key: sum(u[key] for u in units if u.get(key) is not None)
                for key in ("input_tokens", "output_tokens")
            }
            complete = all(all(u.get(k) is not None for k in known) for u in units)
            any_known = any(u.get(k) is not None for u in units for k in known)
            state = (
                "no_calls"
                if not units
                else "complete"
                if complete
                else "partial"
                if any_known
                else "missing"
            )
            usage = {
                "state": state,
                **{k: v if complete else None for k, v in known.items()},
                **{"known_" + k: v for k, v in known.items()},
            }
            return dict(
                version=1,
                context_events=deepcopy(self.context_events),
                outcome=self.outcome,
                load_conditions="unmeasured",
                conditions={
                    "remote_model_warmth": "unknown",
                    "concurrency": "unmeasured",
                },
                logical_calls=len(calls),
                http_attempts=len(attempts),
                tool_calls=self.tool_calls,
                embedding_http_attempts=0,
                local_embedding_calls=sum(
                    s["stage"] == "local_embedding" for s in self.stages
                ),
                embedding_kind="local_fastembed",
                calls=calls,
                attempts=attempts,
                usage=usage,
                cost={
                    "state": "not_applicable" if not units else "unknown",
                    "estimate": None,
                    "currency": None,
                    "price_version": None,
                },
                stages=deepcopy(self.stages),
                model_first_output_ms=self.first_output,
                server_first_body_ms=self.first_body,
                total_ms=self.finished if self.closed else self.elapsed(),
            )


@contextmanager
def span(name):
    record = measurement.get()
    if record is None:
        yield
    else:
        with record.span(name):
            yield


def measured(name):
    from functools import wraps

    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            with span(name):
                return fn(*args, **kwargs)

        return wrapped

    return decorate


@contextmanager
def model_call(purpose, model):
    record = measurement.get()
    identity = str(uuid4())
    token = call_id.set(identity)
    purpose_token = call_purpose.set(purpose)
    item = dict(
        id=identity,
        purpose=purpose,
        model=model,
        outcome="pending",
        input_tokens=None,
        output_tokens=None,
    )
    if record:
        with record.lock:
            if not record.closed:
                record.calls.append(item)
    try:
        with span(purpose):
            yield item
        if record:
            with record.lock:
                if not record.closed:
                    item["outcome"] = "completed"
    except BaseException:
        if record:
            with record.lock:
                if not record.closed:
                    item["outcome"] = "failed"
        raise
    finally:
        call_id.reset(token)
        call_purpose.reset(purpose_token)

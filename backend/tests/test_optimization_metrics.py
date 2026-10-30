import json

import httpx
import pytest

from app.evaluation.telemetry import Measurement, measurement
from app.providers.llm import Delta, complete_turn


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"prompt_tokens": 0, "completion_tokens": 0},
        {
            "prompt_tokens": 9,
            "completion_tokens": 4,
            "completion_tokens_details": {"reasoning_tokens": 2},
        },
    ],
)
def test_transport_retry_usage_and_details(monkeypatch, usage):
    from app.core.config import get_settings
    from app.providers.llm import OpenAICompatibleLLM

    settings = get_settings()
    for key, value in dict(
        llm_base_url="https://example.test/v1",
        llm_api_key="test-only",
        llm_model="fixture",
    ).items():
        monkeypatch.setattr(settings, key, value)
    count = [0]

    def handler(request):
        count[0] += 1
        if count[0] == 1:
            return httpx.Response(503)
        events = [{"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}]
        if usage is not None:
            events.append({"choices": [], "usage": usage})
        return httpx.Response(
            200,
            text="".join("data: " + json.dumps(e) + "\n\n" for e in events)
            + "data: [DONE]\n\n",
        )

    original = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    monkeypatch.setattr("app.providers.llm.time.sleep", lambda _: None)
    record = Measurement()
    token = measurement.set(record)
    try:
        complete_turn(OpenAICompatibleLLM(), [])
        snapshot = record.snapshot()
        assert snapshot["logical_calls"] == 1
        assert snapshot["http_attempts"] == count[0] == 2
        assert snapshot["attempts"][0]["outcome"] == "retry"
        assert snapshot["usage"]["input_tokens"] is None  # Retry had no usage.
        assert snapshot["usage"]["known_input_tokens"] == (usage or {}).get(
            "prompt_tokens", 0
        )
        assert snapshot["model_first_output_ms"] is not None
        assert snapshot["server_first_body_ms"] is None
    finally:
        measurement.reset(token)


class Provider:
    def __init__(self, deltas):
        self.deltas = deltas

    def stream(self, *args, **kwargs):
        yield from self.deltas


def test_unknown_is_not_zero_and_known_subtotals_survive():
    record = Measurement()
    token = measurement.set(record)
    try:
        complete_turn(Provider([Delta("a", finish_reason="stop")]), [])
        complete_turn(
            Provider(
                [Delta("b", input_tokens=0, output_tokens=0, finish_reason="stop")]
            ),
            [],
        )
        result = record.snapshot()
        assert result["usage"]["state"] == "partial"
        assert result["usage"]["input_tokens"] is None
        assert result["usage"]["known_input_tokens"] == 0
        assert result["logical_calls"] == 2
        assert result["http_attempts"] == 0  # Provider double, not real HTTP.
        assert result["cost"]["state"] == "unknown"
    finally:
        measurement.reset(token)


def test_explicit_zero_empty_and_failed_calls_are_distinct():
    empty = Measurement().snapshot()
    assert empty["usage"]["state"] == "no_calls"
    assert empty["usage"]["input_tokens"] == 0
    record = Measurement()
    token = measurement.set(record)
    try:
        complete_turn(
            Provider([Delta(input_tokens=0, output_tokens=0, finish_reason="stop")]), []
        )
        assert record.snapshot()["usage"]["state"] == "complete"
        try:
            complete_turn(Provider([]), [])
        except RuntimeError:
            pass
        assert record.snapshot()["usage"]["state"] == "partial"
        assert record.snapshot()["calls"][-1]["outcome"] == "failed"
    finally:
        measurement.reset(token)


def test_stage_intervals_use_monotonic_clock_and_freeze_late_results():
    now = [10.0]
    record = Measurement(clock=lambda: now[0])
    with record.span("retrieval"):
        now[0] += 0.2
    assert 199 <= record.snapshot()["stages"][0]["elapsed_ms"] <= 201
    record.close("cancelled")
    frozen = record.snapshot()
    record.event("http_attempt", call_id="late", purpose="agent")
    assert record.snapshot() == frozen


def test_concurrent_request_records_do_not_mix():
    from concurrent.futures import ThreadPoolExecutor

    def work(value):
        record = Measurement()
        token = measurement.set(record)
        try:
            complete_turn(
                Provider(
                    [
                        Delta(
                            "ok",
                            input_tokens=value,
                            output_tokens=1,
                            finish_reason="stop",
                        )
                    ]
                ),
                [],
            )
            return record.snapshot()
        finally:
            measurement.reset(token)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(work, (7, 19)))
    assert [r["usage"]["input_tokens"] for r in results] == [7, 19]
    assert all(r["logical_calls"] == 1 for r in results)
    assert results[0]["calls"][0]["id"] != results[1]["calls"][0]["id"]


def test_deadline_late_provider_does_not_rewrite_closed_measurement():
    import time

    from app.providers.call_options import CallOptions, Execution, ExecutionStopped

    class Slow:
        def stream(self, *args, **kwargs):
            time.sleep(0.08)
            yield Delta("late", input_tokens=99, output_tokens=1, finish_reason="stop")

    record = Measurement()
    token = measurement.set(record)
    try:
        with pytest.raises(ExecutionStopped):
            complete_turn(
                Slow(),
                [],
                options=CallOptions("agent", False, 20, 0.02),
                execution=Execution.start(1),
            )
        record.close("timeout")
        frozen = record.snapshot()
        time.sleep(0.12)
        assert frozen == record.snapshot()
        assert frozen["calls"][0]["outcome"] == "failed"
        assert frozen["usage"]["state"] == "missing"
    finally:
        measurement.reset(token)

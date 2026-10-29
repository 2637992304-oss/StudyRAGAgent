"""Streaming OpenAI Chat Completions compatible provider."""

import json
import time
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.config import get_settings
from app.providers.call_options import (
    CallOptions,
    Execution,
    ExecutionStopped,
    IncompleteResponse,
    bounded_call,
)

# Opt-in in-process evaluation only. Never contains credentials or reasoning.
call_observer = ContextVar("model_call_observer", default=None)


@dataclass
class Delta:
    text: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str | None = None
    reasoning_content: str = ""


class LLMProvider(Protocol):
    def stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        options: CallOptions | None = None,
        execution: Execution | None = None,
    ) -> Iterator[Delta]: ...


def parse_sse_lines(lines: Iterator[str]) -> Iterator[dict]:
    data: list[str] = []
    for line in lines:
        if not line:
            if data:
                payload = "\n".join(data)
                data = []
                if payload == "[DONE]":
                    return
                yield json.loads(payload)
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        payload = "\n".join(data)
        if payload != "[DONE]":
            yield json.loads(payload)


class OpenAICompatibleLLM:
    def stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        options: CallOptions | None = None,
        execution: Execution | None = None,
    ) -> Iterator[Delta]:
        settings = get_settings()
        if (
            not settings.llm_base_url
            or not settings.llm_api_key
            or not settings.llm_model
        ):
            raise ValueError("LLM 配置不完整")
        if settings.llm_provider != "openai_compatible":
            raise ValueError("不支持的 LLM provider")
        url = settings.llm_base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": settings.llm_model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": settings.llm_temperature,
            "max_tokens": settings.llm_max_output_tokens,
        }
        if tools:
            payload["tools"] = tools
        if settings.llm_thinking_mode != "default":
            payload["thinking"] = {"type": settings.llm_thinking_mode}
        timeout = settings.llm_timeout_seconds
        if options is not None:
            payload["thinking"] = {
                "type": "enabled" if options.thinking else "disabled"
            }
            payload["max_tokens"] = options.max_tokens
            if options.thinking:
                payload["reasoning_effort"] = options.effort or "high"
                payload.pop("temperature", None)
            timeout = (
                min(options.timeout, execution.remaining())
                if execution
                else options.timeout
            )
        transport_timeout = (
            httpx.Timeout(
                timeout,
                connect=min(settings.llm_connect_timeout, timeout),
                read=min(settings.llm_read_timeout, timeout),
            )
            if options
            else timeout
        )
        observer = call_observer.get()
        if observer:
            observer(
                {
                    "purpose": options.purpose if options else "legacy",
                    "thinking": payload.get("thinking"),
                    "reasoning_effort": payload.get("reasoning_effort"),
                    "max_tokens": payload["max_tokens"],
                    "timeout": timeout,
                    "temperature_present": "temperature" in payload,
                    "tool_count": len(tools or []),
                    "prior_reasoning_messages": sum(
                        bool(m.get("reasoning_content")) for m in messages
                    ),
                }
            )
        with httpx.Client(timeout=transport_timeout) as client:
            for attempt in range(2):
                from app.evaluation.runtime import observe

                observe(
                    "http_attempt", purpose=options.purpose if options else "legacy"
                )
                with client.stream(
                    "POST",
                    url,
                    headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                    json=payload,
                ) as response:
                    response_started = time.monotonic()
                    if response.status_code == 503 and attempt == 0:
                        if execution:
                            execution.cancelled.wait(min(2, execution.remaining()))
                            execution.check()
                        else:
                            time.sleep(2)
                        continue
                    response.raise_for_status()
                    for event in parse_sse_lines(response.iter_lines()):
                        if execution:
                            execution.check()
                        if time.monotonic() - response_started > timeout:
                            if options:
                                raise ExecutionStopped("step_timeout")
                            raise TimeoutError("模型调用达到时间限制")
                        if event.get("error"):
                            raise RuntimeError("LLM 服务返回错误")
                        for choice in event.get("choices", []):
                            delta = choice.get("delta", {})
                            content = delta.get("content")
                            if isinstance(content, str) and content:
                                yield Delta(text=content)
                            if delta.get("tool_calls"):
                                yield Delta(tool_calls=delta["tool_calls"])
                            if delta.get("reasoning_content"):
                                yield Delta(
                                    reasoning_content=delta["reasoning_content"]
                                )
                            if choice.get("finish_reason"):
                                yield Delta(finish_reason=choice["finish_reason"])
                        usage = event.get("usage")
                        if usage:
                            yield Delta(
                                input_tokens=usage.get("prompt_tokens"),
                                output_tokens=usage.get("completion_tokens"),
                            )
                    return


def get_llm_provider() -> LLMProvider:
    return OpenAICompatibleLLM()


@dataclass
class Turn:
    message: dict[str, Any]
    input_tokens: int = 0
    output_tokens: int = 0


def complete_turn(
    provider,
    messages: list[dict],
    tools: list[dict] | None = None,
    *,
    options: CallOptions | None = None,
    execution: Execution | None = None,
) -> Turn:
    """Buffer one *complete* model turn before any side effects or public output."""
    if options is None:
        return _collect_turn(provider, messages, tools)
    execution = execution or Execution.start(options.timeout + 1)
    try:
        return bounded_call(
            lambda: _collect_turn(provider, messages, tools, options, execution),
            execution,
            options.timeout,
        )
    except httpx.ConnectTimeout as exc:
        raise ExecutionStopped("connect_timeout") from exc
    except httpx.ReadTimeout as exc:
        raise ExecutionStopped("read_timeout") from exc


def _collect_turn(provider, messages, tools, options=None, execution=None):
    text, reasoning, calls = [], [], {}
    finish = None
    from app.evaluation.runtime import observe

    observe(
        "model_input",
        messages=messages,
        tools=tools or [],
        purpose=options.purpose if options else "legacy",
    )
    turn = Turn({})
    kwargs = {"options": options, "execution": execution} if options else {}
    for delta in provider.stream(messages, tools=tools, **kwargs):
        if execution:
            execution.check()
        text.append(delta.text)
        reasoning.append(delta.reasoning_content)
        for item in delta.tool_calls:
            index = item.get("index")
            if not isinstance(index, int) or index < 0 or index > 15:
                raise ValueError("无效工具索引")
            call = calls.setdefault(
                index,
                {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                },
            )
            if item.get("id"):
                call["id"] += item["id"]
            function = item.get("function", {})
            for key in ("name", "arguments"):
                call["function"][key] += function.get(key, "")
            if len(call["function"]["arguments"]) > 16000:
                raise ValueError("工具参数过长")
        finish = delta.finish_reason or finish
        if delta.input_tokens is not None:
            turn.input_tokens = delta.input_tokens
        if delta.output_tokens is not None:
            turn.output_tokens = delta.output_tokens
    if finish not in {"stop", "tool_calls"}:
        raise IncompleteResponse("模型响应未完整结束")
    if calls and finish != "tool_calls":
        raise RuntimeError("工具响应未完整结束")
    ordered = [calls[i] for i in sorted(calls)]
    ids = [call["id"] for call in ordered]
    if any(not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("工具调用ID无效")
    turn.message = {"role": "assistant", "content": "".join(text)}
    if ordered:
        turn.message["tool_calls"] = ordered
    if any(reasoning):
        turn.message["reasoning_content"] = "".join(reasoning)
    return turn

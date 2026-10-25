"""Server-selected immutable profiles and request-local execution deadlines."""

import queue
import threading
import time
from collections.abc import Callable
from contextvars import copy_context
from dataclasses import asdict, dataclass, field
from typing import Literal

from app.core.config import get_settings


class ExecutionStopped(TimeoutError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class IncompleteResponse(RuntimeError):
    pass


@dataclass(frozen=True)
class CallOptions:
    purpose: Literal["agent", "transcription", "citation_check"]
    thinking: bool
    max_tokens: int
    timeout: float
    effort: str | None = None

    def snapshot(self):
        return asdict(self)


def profile(purpose: str, thinking: bool = False) -> CallOptions:
    s = get_settings()
    if purpose == "agent":
        return CallOptions(
            "agent",
            thinking,
            s.agent_thinking_tokens if thinking else s.agent_plain_tokens,
            s.agent_thinking_timeout if thinking else s.agent_plain_timeout,
            s.agent_thinking_effort if thinking else None,
        )
    if purpose == "transcription":
        return CallOptions(
            "transcription", False, s.transcription_tokens, s.transcription_timeout
        )
    if purpose == "citation_check":
        return CallOptions(
            "citation_check", False, s.citation_check_tokens, s.citation_check_timeout
        )
    raise ValueError("Unknown server call purpose")


@dataclass(frozen=True)
class Execution:
    deadline: float
    cancelled: threading.Event = field(default_factory=threading.Event)
    clock: Callable[[], float] = time.monotonic

    @classmethod
    def start(cls, seconds: float):
        return cls(time.monotonic() + seconds)

    def remaining(self) -> float:
        if self.cancelled.is_set():
            raise ExecutionStopped("cancelled")
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise ExecutionStopped("deadline_exceeded")
        return remaining

    def check(self):
        self.remaining()


# HTTP workers have no database access or public output. A cancelled caller never
# accepts late results. Bounded slots include orphaned transports until they exit.
_HTTP_SLOTS = threading.BoundedSemaphore(16)


def bounded_call(work, execution: Execution, seconds: float, slots=None):
    """Interrupt even when a synchronous transport produces no frames at all.

    Threads cannot be forcibly killed in Python. Transports also have finite
    connect/read limits; abandoned workers remain daemonized and hold their slot.
    """
    slots = slots or _HTTP_SLOTS
    end = min(execution.deadline, execution.clock() + seconds)

    def check():
        remaining = min(execution.remaining(), end - execution.clock())
        if remaining <= 0:
            raise ExecutionStopped("step_timeout")
        return remaining

    while not slots.acquire(timeout=min(0.05, check())):
        pass
    try:
        check()
    except BaseException:
        slots.release()
        raise
    result = queue.Queue(maxsize=1)

    def run():
        try:
            result.put((True, work()))
        except BaseException as exc:
            result.put((False, exc))
        finally:
            slots.release()

    context = copy_context()
    threading.Thread(
        target=lambda: context.run(run), daemon=True, name="bounded-model-call"
    ).start()
    while True:
        try:
            ok, value = result.get(timeout=min(0.05, check()))
        except queue.Empty:
            continue
        check()
        if not ok:
            raise value
        return value

"""Opt-in request-local observation; never logs model inputs or source bodies."""

from contextvars import ContextVar
from copy import deepcopy

observer = ContextVar("agent_evaluation_observer", default=None)


def observe(kind, **data):
    from app.evaluation.telemetry import measurement

    record = measurement.get()
    if record is not None:
        record.event(kind, **data)
    callback = observer.get()
    if callback is not None:
        callback({"kind": kind, **deepcopy(data)})


class CourseRetrieval(tuple):
    """Keep the two-value adapter contract while carrying checked table metadata."""

    def __new__(cls, rows, ids, verification=None, question=None):
        result = super().__new__(cls, (rows, ids))
        result.verification = verification or {}
        result.question = question
        return result

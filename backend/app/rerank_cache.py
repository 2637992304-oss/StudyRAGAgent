"""Bounded process-local numeric cache; never an authorization/result cache."""

import math
import time
from collections import OrderedDict
from threading import Lock


class ScoreCache:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = Lock()
        self.values: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def get(self, key: str, ttl: float) -> float | None:
        with self.lock:
            item = self.values.get(key)
            if item is None:
                return None
            inserted, value = item
            if self.clock() - inserted >= ttl:
                del self.values[key]
                return None
            self.values.move_to_end(key)
            return value

    def put(self, key: str, value: float, capacity: int) -> None:
        if not math.isfinite(value) or capacity < 1:
            raise ValueError("Invalid rerank cache value/capacity")
        with self.lock:
            self.values[key] = (self.clock(), float(value))
            self.values.move_to_end(key)
            while len(self.values) > capacity:
                self.values.popitem(last=False)

    def clear(self) -> None:
        with self.lock:
            self.values.clear()


score_cache = ScoreCache()

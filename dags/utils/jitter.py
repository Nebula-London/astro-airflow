"""Delays used to stagger job-portal requests from mapped tasks."""
from __future__ import annotations

import hashlib
import random
import time


def target_stagger(target: dict, maximum: float) -> float:
    """Return a stable per-target offset in ``[0, maximum)``.

    Mapped targets carry their index and total count, so the offsets are evenly
    spread. Older targets without those fields fall back to a hash of the key.
    """
    maximum = max(0.0, float(maximum))
    if not maximum:
        return 0.0
    index = target.get("index")
    count = target.get("target_count")
    if index is not None and count is not None:
        total = max(1, int(count))
        return maximum * (max(0, int(index)) % total) / total
    key = str(target.get("key") or "").encode("utf-8")
    value = int.from_bytes(hashlib.sha256(key).digest()[:4], "big")
    return maximum * value / 2**32


def random_delay(maximum: float) -> float:
    """Return a random pause in ``[0, maximum)``."""
    maximum = max(0.0, float(maximum))
    return random.uniform(0.0, maximum) if maximum else 0.0


def jittered_backoff(attempt: int, base: float = 1.5) -> float:
    """Return an exponential retry delay with a 0.5x-1.5x random factor."""
    return max(1.0, float(base)) ** max(0, int(attempt)) * random.uniform(0.5, 1.5)


def sleep_for(delay: float) -> None:
    """Sleep for ``delay`` seconds when it is positive."""
    if delay > 0:
        time.sleep(delay)

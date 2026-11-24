"""Retry OCI requests that are throttled with HTTP 429."""
from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable

_LOGGER = logging.getLogger(__name__)
_MAX_DELAY_SECONDS = 30.0


def is_rate_limit(exc: Exception) -> bool:
    """Whether an OCI exception is a 429 / TooManyRequests response."""
    return (
        getattr(exc, "status", None) == 429
        or str(getattr(exc, "code", "") or "").upper() == "TOOMANYREQUESTS"
    )


def call_with_retry(
    func: Callable[[], Any],
    *,
    retries: int = 5,
    base_delay: float = 1.0,
    action: str = "OCI request",
) -> Any:
    """Call ``func``, retrying 429 throttling with exponential backoff."""
    attempts = max(1, int(retries))
    base = max(0.0, float(base_delay))
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - OCI exposes several 429 types
            if not is_rate_limit(exc) or attempt >= attempts:
                raise
            delay = min(_MAX_DELAY_SECONDS, base * (2 ** (attempt - 1)))
            delay *= random.uniform(0.5, 1.0)
            _LOGGER.warning(
                "%s throttled (429); retry %d/%d in %.1fs",
                action, attempt, attempts, delay,
            )
            time.sleep(delay)

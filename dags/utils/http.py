"""HTTP GET with a browser user-agent, retrying transport errors with jitter."""
from __future__ import annotations

import logging
import urllib.request

from dags.utils.constants import HEADERS
from dags.utils.jitter import jittered_backoff, sleep_for

_LOGGER = logging.getLogger(__name__)


def fetch(url: str, timeout: float = 30.0, retries: int = 3) -> tuple[str, str]:
    """GET a URL, returning ``(body, final_url)`` after up to ``retries`` tries."""
    last_error = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, "replace"), response.geturl()
        except Exception as exc:  # noqa: BLE001 - retry any transport error
            last_error = exc
            if attempt < retries - 1:
                _LOGGER.info(
                    "Retrying a portal request after a transport error (%d/%d)",
                    attempt + 1, retries,
                )
                sleep_for(jittered_backoff(attempt))
    raise RuntimeError(f"request failed after {retries} attempt(s): {last_error}")

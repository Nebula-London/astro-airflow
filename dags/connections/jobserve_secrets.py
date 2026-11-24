"""Access to the private JobServe search URLs.

A search URL contains a private ``shid``. The URLs are read from the Airflow
Variable ``jobserve_search_urls`` (or the ``JOBSERVE_SEARCH_URLS`` environment
variable) and are never written to config, XCom or log messages. Only the
search *names* travel through the DAG.
"""
from __future__ import annotations

import json
import logging
import os

from dags.utils.constants import LEGACY_SEARCH_URLS_VARIABLE as SEARCH_URLS_VARIABLE

_LOGGER = logging.getLogger(__name__)


def read_search_urls() -> dict[str, str]:
    """Read ``{search name: URL}`` from the Airflow Variable or env fallback."""
    raw = ""
    try:
        import airflow.sdk

        raw = airflow.sdk.Variable.get(SEARCH_URLS_VARIABLE, default="")
    except Exception as exc:  # noqa: BLE001 - local/dev fallback
        _LOGGER.info(
            "Airflow Variable %s is unavailable (%s); using the environment fallback",
            SEARCH_URLS_VARIABLE, exc,
        )
    if not raw:
        raw = os.environ.get("JOBSERVE_SEARCH_URLS", "")
    if not raw:
        raise ValueError(
            "No JobServe searches configured. Set the Airflow Variable "
            f"'{SEARCH_URLS_VARIABLE}' (or the environment variable "
            "JOBSERVE_SEARCH_URLS) to a JSON object of {name: search URL}. "
            "Never commit a search URL or shid."
        )
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, dict) or not data:
        raise ValueError(
            f"'{SEARCH_URLS_VARIABLE}' must be a non-empty JSON object of name -> URL"
        )
    cleaned = {}
    for name, url in data.items():
        label, value = str(name).strip(), str(url or "").strip()
        if label and value:
            cleaned[label] = value
    if not cleaned:
        raise ValueError(f"'{SEARCH_URLS_VARIABLE}' contained no usable name/URL pairs")
    return cleaned

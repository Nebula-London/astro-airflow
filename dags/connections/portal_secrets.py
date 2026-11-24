"""Access to the job-site URL templates held outside the repository.

Each enabled portal has exactly one URL template stored in the Airflow
Variable ``job_search_urls``::

    {
      "jobserve": "https://www.jobserve.com/gb/en/JobListing.aspx?shid=...",  # fixed
      "totaljobs": "https://www.totaljobs.com/jobs/{query}/in-manchester?radius=30",
      "cw": "https://www.cwjobs.co.uk/jobs/{query}",
      "indeed": "https://uk.indeed.com/jobs?q={query}"
    }

Roles come from ``jobs_config.yaml``; the ``{query}`` placeholder is filled in
inside the mapped fetch task. That keeps the real URLs (including JobServe's
private ``shid``) out of config files, DAG code, XCom and logs.

The legacy flat ``jobserve_search_urls`` Variable and ``JOBSERVE_SEARCH_URLS``
environment variable are still accepted as a fallback so existing local setups
keep working during migration.
"""
from __future__ import annotations

import json
import logging
import os

from dags.utils.constants import (
    LEGACY_SEARCH_URLS_VARIABLE,
    PORTAL_URLS_ENV,
    PORTAL_URLS_VARIABLE,
)

_LOGGER = logging.getLogger(__name__)


def _variable_value(name: str) -> str:
    """Read an Airflow Variable, tolerating local/dev environments."""
    try:
        import airflow.sdk

        return airflow.sdk.Variable.get(name, default="")
    except Exception as exc:  # noqa: BLE001 - local/dev fallback
        _LOGGER.info(
            "Airflow Variable %s is unavailable (%s); using the environment fallback",
            name, exc,
        )
        return ""


def _raw_value(variable: str, env_name: str) -> str:
    """Read a Variable first, then an environment variable."""
    raw = _variable_value(variable)
    return raw if raw else os.environ.get(env_name, "")


def _as_dict(raw: str) -> dict:
    """Parse a JSON object, returning an empty dict for malformed values."""
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        _LOGGER.warning("URL configuration is not valid JSON; ignoring it")
        return {}
    return data if isinstance(data, dict) else {}


def _clean(templates: dict) -> dict[str, str]:
    """Lower-case portal names and drop blank templates."""
    cleaned = {}
    for name, template in templates.items():
        portal = str(name or "").strip().lower()
        value = str(template or "").strip()
        if portal and value:
            cleaned[portal] = value
    return cleaned


def read_portal_urls() -> dict[str, str]:
    """Return ``{portal: URL template}`` from the Variable or env fallback."""
    primary = _clean(_as_dict(_raw_value(PORTAL_URLS_VARIABLE, PORTAL_URLS_ENV)))
    if primary:
        return primary

    legacy_raw = _raw_value(LEGACY_SEARCH_URLS_VARIABLE, "JOBSERVE_SEARCH_URLS")
    legacy = _clean(_as_dict(legacy_raw))
    if legacy:
        # Legacy values are flat {role: saved URL} entries. There is no way to
        # inject a role into them, so reuse the first URL as a fixed JobServe
        # search and warn that migrating to `job_search_urls` enables fan-out.
        first_url = next(iter(legacy.values()))
        _LOGGER.warning(
            "Using legacy '%s'; only JobServe will be searched and every role "
            "will reuse the same saved URL. Set '%s' to one URL template per "
            "portal to enable multi-portal fan-out.",
            LEGACY_SEARCH_URLS_VARIABLE, PORTAL_URLS_VARIABLE,
        )
        return {"jobserve": first_url}

    raise ValueError(
        "No job-search URL templates configured. Set the Airflow Variable "
        f"'{PORTAL_URLS_VARIABLE}' (or environment variable '{PORTAL_URLS_ENV}') "
        "to a JSON object of {portal: URL template} with a {query} placeholder."
    )

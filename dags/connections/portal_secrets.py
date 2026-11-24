"""Access to per-portal job-search URL Variables.

Every portal has its own Variable named ``<portal>_search_urls`` holding a JSON
object of ``{role: search URL}``::

    totaljobs_search_urls = {"aws": "https://www.totaljobs.com/jobs/aws"}
    cw_search_urls        = {"aws": "https://www.cwjobs.co.uk/jobs/aws"}

Entries are returned as ``portal::role`` keys so each role fetches its own
saved URL. A generic ``portal`` key is synthesised from the first role URL as
a fallback. URLs never appear in config, DAG code, XCom or logs.

The combined ``job_search_urls`` Variable (``{portal: URL template}``) is still
accepted for backwards compatibility; per-role Variables take precedence.
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


def _portal_variable(portal: str) -> str:
    """Return the Variable name that stores a portal's role URL map."""
    return f"{portal}_search_urls"


def _portal_env(portal: str) -> str:
    """Return the environment fallback for a portal's role URL map."""
    return f"{portal.upper()}_SEARCH_URLS"


def read_portal_urls() -> dict[str, str]:
    """Return ``portal`` and ``portal::role`` URL entries from all Variables."""
    from dags.portals.registry import supported_portals

    urls = _clean(_as_dict(_raw_value(PORTAL_URLS_VARIABLE, PORTAL_URLS_ENV)))
    portal_names = set(supported_portals())

    for portal in sorted(portal_names):
        role_urls = _clean(
            _as_dict(_raw_value(_portal_variable(portal), _portal_env(portal)))
        )
        role_urls = {
            role: url for role, url in role_urls.items() if role not in portal_names
        }
        if not role_urls:
            continue
        for role, url in role_urls.items():
            urls.setdefault(f"{portal}::{role}", url)
        urls.setdefault(portal, next(iter(role_urls.values())))
        _LOGGER.info(
            "Loaded %d role URL(s) from '%s'", len(role_urls), _portal_variable(portal)
        )

    if not urls:
        raise ValueError(
            "No job-search URLs configured. Set a '<portal>_search_urls' Variable "
            "to a JSON object of {role: search URL}, for example "
            "'jobserve_search_urls'."
        )
    return urls

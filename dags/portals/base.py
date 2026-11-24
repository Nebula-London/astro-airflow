"""Shared helpers for job-portal adapters.

Adapters return listing items and normalized job dictionaries in a common shape
so the rest of the DAG does not need to know which portal a row came from.
"""
from __future__ import annotations

import re
from urllib.parse import quote, quote_plus, urlsplit, urlunsplit

from dags.utils.identity import source_key
from dags.utils.job_parser import build_jd_text, content_hash

QUERY_PLACEHOLDER = "{query}"


def has_query_placeholder(template: str) -> bool:
    """Whether a URL template can accept a role from config."""
    return QUERY_PLACEHOLDER in template


def _encode_role(role: str, in_path: bool) -> str:
    """Encode a role for a URL path (hyphens) or a query string (plus)."""
    value = " ".join(str(role or "").split())
    if in_path:
        return quote(value.lower().replace(" ", "-"), safe="-")
    return quote_plus(value)


def build_search_url(template: str, role: str, query_param: str = "") -> str:
    """Build a portal search URL from a stored template and a config role.

    Templates should use ``{query}`` where the role belongs, e.g.
    ``https://www.totaljobs.com/jobs/{query}/in-manchester?radius=30``.
    Roles placed in the path are hyphenated (``data engineer`` ->
    ``data-engineer``); roles placed in the query string are plus-encoded.
    If a template has no placeholder but the adapter declares ``query_param``,
    the role is appended as that query parameter.
    """
    template = str(template or "").strip()
    if QUERY_PLACEHOLDER in template:
        marker = template.find(QUERY_PLACEHOLDER)
        question = template.find("?")
        in_path = question == -1 or marker < question
        return template.replace(QUERY_PLACEHOLDER, _encode_role(role, in_path))
    if not query_param:
        return template
    parts = urlsplit(template)
    existing = parts.query
    query = (
        f"{existing}&{query_param}={_encode_role(role, False)}"
        if existing
        else f"{query_param}={_encode_role(role, False)}"
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def with_page(url: str, page: int) -> str:
    """Return the URL for a results page, preserving existing parameters."""
    if page <= 1:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}page={page}"


def strip_tags(value: str) -> str:
    """Turn a fragment of HTML into readable, line-preserving text."""
    value = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", value, flags=re.S | re.I)
    value = re.sub(r"<[^>]+>", "\n", value)
    lines = [" ".join(line.split()) for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def clean_lines(value: str) -> list[str]:
    """Split a text fragment into cleaned, non-empty lines."""
    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line and line not in lines:
            lines.append(line)
    return lines


def source_of(portal: str, role: str, item: dict) -> dict:
    """Build the source record attached to every normalized job."""
    return {
        "portal": portal,
        "role": role,
        "code": str(item.get("code") or ""),
        "url": str(item.get("url") or ""),
        "key": source_key(portal, item.get("code", "")),
    }


def normalize_job(job: dict, item: dict, portal: str, role: str) -> dict:
    """Add portal/source fields and recompute the canonical identity."""
    source = source_of(portal, role, item)
    job["portal"] = portal
    job["search_role"] = role
    job["search_name"] = role
    job["search"] = role
    job["canonical_hash"] = content_hash(job)
    job["hash"] = job["canonical_hash"]
    job["source_code"] = source["code"]
    job["source_url"] = source["url"]
    job["sources"] = [source]
    job["uid"] = "hash:" + job["canonical_hash"]
    if source["code"]:
        job["code"] = source["code"]
    if source["url"]:
        job["permalink"] = source["url"]
    job["jd_text"] = build_jd_text(job)
    return job


def merge_sources(jobs: list[dict]) -> dict:
    """Merge duplicate listings of the same canonical job into one row.

    The first job supplies the descriptive fields; subsequent jobs contribute
    their source records. The earliest ``first_seen`` date is preserved.
    """
    if not jobs:
        return {}
    base = dict(jobs[0])
    merged = {}
    first_seen = []
    for job in jobs:
        for source in job.get("sources") or []:
            merged[source.get("key") or source_key(source.get("portal"), source.get("code"))] = source
        if job.get("first_seen"):
            first_seen.append(job["first_seen"])
    base["sources"] = sorted(merged.values(), key=lambda item: item.get("key", ""))
    if first_seen:
        base["first_seen"] = min(first_seen)
    return base


def empty_item(code: str, url: str = "", title: str = "") -> dict:
    """Return a listing item with all expected keys present."""
    return {
        "code": str(code or ""),
        "url": str(url or ""),
        "title": str(title or ""),
        "agency": "",
        "location": "",
        "rate": "",
        "description": "",
    }

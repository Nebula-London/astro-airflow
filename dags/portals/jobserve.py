"""JobServe portal adapter."""
from __future__ import annotations

from dags.portals.base import empty_item, normalize_job
from dags.utils.constants import BASE
from dags.utils.html import find_job_codes
from dags.utils.job_parser import fallback_job as build_fallback_job
from dags.utils.job_parser import listing_title, parse_job

NAME = "jobserve"
# JobServe uses a saved search identified by an opaque `shid`. The stored URL is
# used as-is for every role; no keyword is injected. An explicit {query} placeholder
# would still be honoured if that changes later.
QUERY_PARAM = ""


def detail_url(code: str, item: dict | None = None) -> str:
    """Build a JobServe detail URL from a job code."""
    return f"{BASE}/gb/en/{code}.jsjob"


def find_items(html: str, final_url: str, src_url: str = "") -> list[dict]:
    """Extract listing items from JobServe search HTML."""
    items = []
    for code in find_job_codes(html, final_url, src_url):
        item = empty_item(code, detail_url(code), listing_title(html, code))
        items.append(item)
    return items


def parse_detail(html: str, code: str, item: dict, portal: str, role: str) -> dict:
    """Parse a JobServe detail page."""
    job = parse_job(html, code)
    return normalize_job(job, item, portal, role)


def fallback_item_job(item: dict, portal: str, role: str) -> dict:
    """Build a job from listing metadata when the detail page is unavailable."""
    job = build_fallback_job(item["code"], item.get("title", ""))
    if item.get("description"):
        job["description"] = item["description"]
    return normalize_job(job, item, portal, role)

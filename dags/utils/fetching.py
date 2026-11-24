"""Fetch one portal/role listing and follow its items to detail pages.

This module is portal-agnostic: all site-specific parsing is delegated to the
adapter returned by :func:`dags.portals.registry.get_adapter`. URLs are read
from the secret store inside the mapped task so they never enter XCom.
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from dags.connections.portal_secrets import read_portal_urls
from dags.portals.base import build_search_url, merge_sources, with_page
from dags.portals.registry import get_adapter
from dags.utils.constants import COUNT_COLS
from dags.utils.http import fetch
from dags.utils.identity import canonical_uid

_LOGGER = logging.getLogger(__name__)


def _dedupe_items(items: list[dict]) -> list[dict]:
    """Keep the first item for each portal job code."""
    seen = set()
    unique = []
    for item in items:
        code = str(item.get("code") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        unique.append(item)
    return unique


def fetch_listing(url: str, adapter, config: dict) -> list[dict]:
    """Fetch a listing and all its result pages, returning normalized items."""
    html, final_url = fetch(url, config["timeout_seconds"], config["http_retries"])
    items = _dedupe_items(adapter.find_items(html, final_url, url))
    pages = sorted({int(number) for number in re.findall(r"[?&;]page=(\d+)", html)})
    max_page = max(pages) if pages else 0
    known = {str(item.get("code") or "") for item in items}
    if max_page > 1:
        _LOGGER.info("Listing has %d page(s)", max_page)
        for page in range(2, max_page + 1):
            try:
                page_html, page_final = fetch(
                    with_page(url, page),
                    config["timeout_seconds"],
                    config["http_retries"],
                )
            except Exception:  # noqa: BLE001 - one bad page must not stop the search
                _LOGGER.warning(
                    "Page %d/%d not fetched; retrying on the next run", page, max_page
                )
                continue
            new_items = [
                item for item in adapter.find_items(page_html, page_final, url)
                if str(item.get("code") or "") not in known
            ]
            for item in new_items:
                known.add(str(item.get("code") or ""))
            items.extend(new_items)
            _LOGGER.info("Page %d/%d: %d new row(s)", page, max_page, len(new_items))
    if config["limit"]:
        items = items[: config["limit"]]
    if not items:
        _LOGGER.warning("A listing returned no roles (empty results or an invalid URL)")
    else:
        _LOGGER.info("Listing returned %d role(s)", len(items))
    return items


def fetch_target(target: dict, config: dict) -> dict:
    """Fetch one mapped target (one role on one portal)."""
    portal = str(target["portal"]).strip().lower()
    role = str(target["role"]).strip()
    adapter = get_adapter(portal)
    template = read_portal_urls().get(portal)
    if not template:
        raise ValueError(
            f"No URL template for portal '{portal}'. "
            "Add it to the 'job_search_urls' Airflow Variable."
        )
    url = build_search_url(template, role, getattr(adapter, "QUERY_PARAM", ""))
    _LOGGER.info("Fetching %s (%s on %s)", target["key"], role, portal)
    items = fetch_listing(url, adapter, config)
    return {
        "key": target["key"],
        "portal": portal,
        "role": role,
        "items": items,
        "workers": target.get("workers"),
        "delay_seconds": target.get("delay_seconds"),
    }


def parse_item(item: dict, adapter, portal: str, role: str, config: dict) -> dict:
    """Fetch and parse one detail page, falling back to listing metadata."""
    code = str(item.get("code") or "")
    detail = adapter.detail_url(code, item)
    if detail:
        for attempt in range(config["http_retries"]):
            try:
                html, _ = fetch(detail, config["timeout_seconds"], 1)
                return adapter.parse_detail(html, code, item, portal, role)
            except Exception:  # noqa: BLE001 - fall back to the listing row
                if attempt < config["http_retries"] - 1:
                    time.sleep(1.5 ** attempt)
    return adapter.fallback_item_job(item, portal, role)


def parse_target(fetched: dict, config: dict) -> dict:
    """Parse all items for one mapped target into normalized job records."""
    portal = str(fetched["portal"]).strip().lower()
    role = str(fetched["role"]).strip()
    adapter = get_adapter(portal)
    items = fetched.get("items") or []
    counts = dict.fromkeys(COUNT_COLS, 0)
    counts["found"] = len(items)

    workers = max(1, int(fetched.get("workers") or config["workers"]))
    parse_one = partial(parse_item, adapter=adapter, portal=portal, role=role, config=config)
    if items:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            found = list(pool.map(parse_one, items))
    else:
        found = []

    grouped = {}
    for job in found:
        uid = canonical_uid(job)
        if uid in grouped:
            counts["dup"] += 1
            grouped[uid] = merge_sources([grouped[uid], job])
        else:
            grouped[uid] = job
    jobs = list(grouped.values())
    for job in jobs:
        if job.get("error"):
            counts["unfetched"] += 1

    delay = fetched.get("delay_seconds")
    if delay is None:
        delay = config["delay_seconds"]
    if delay:
        time.sleep(float(delay))
    _LOGGER.info("Target %s: parsed %d job detail(s)", fetched["key"], len(found))
    return {
        "key": fetched["key"],
        "name": fetched["key"],
        "portal": portal,
        "role": role,
        "jobs": jobs,
        "counts": counts,
    }

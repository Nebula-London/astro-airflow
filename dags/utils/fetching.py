"""Fetch saved searches and follow each job code to its detail page."""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from dags.utils.constants import BASE, COUNT_COLS
from dags.utils.html import find_job_codes
from dags.utils.http import fetch
from dags.utils.job_parser import fallback_job, listing_title, parse_job

_LOGGER = logging.getLogger(__name__)


def fetch_search(url: str, config: dict) -> tuple[list[str], dict[str, str]]:
    """Fetch a listing and every results page, returning codes and fallback titles."""
    html, final_url = fetch(url, config["timeout_seconds"], config["http_retries"])
    codes = find_job_codes(html, final_url, url)
    pages = sorted({int(number) for number in re.findall(r"[?&;]page=(\d+)", html)})
    max_page = max(pages) if pages else 0
    known = set(codes)
    if max_page > 1:
        _LOGGER.info("Listing has %d page(s)", max_page)
        for page in range(2, max_page + 1):
            separator = "&" if "?" in url else "?"
            try:
                page_html, page_final = fetch(
                    f"{url}{separator}page={page}",
                    config["timeout_seconds"], config["http_retries"],
                )
            except Exception:  # noqa: BLE001 - one bad page must not stop the search
                _LOGGER.warning("Page %d/%d not fetched; retrying on the next run", page, max_page)
                continue
            new_codes = [
                code for code in find_job_codes(page_html, page_final, url)
                if code not in known
            ]
            known.update(new_codes)
            codes.extend(new_codes)
            html += page_html
            _LOGGER.info("Page %d/%d: %d new row(s)", page, max_page, len(new_codes))
    if config["limit"]:
        codes = codes[: config["limit"]]
    if not codes:
        _LOGGER.warning("A saved search returned no roles (empty results or an invalid URL)")
    else:
        _LOGGER.info("Listing returned %d role(s)", len(codes))
    fallback_titles = {code: listing_title(html, code) for code in codes}
    return codes, fallback_titles


def fetch_searches(config: dict, urls: dict[str, str]) -> dict:
    """Fetch each saved search; URLs come from the secret store, not config."""
    searches = []
    for search in config["searches"]:
        name = search["name"]
        codes, fallback_titles = fetch_search(urls[name], config)
        searches.append(
            {"name": name, "codes": codes, "fallback_titles": fallback_titles}
        )
        _LOGGER.info("Search '%s': %d listing row(s)", name, len(codes))
    return {"searches": searches}


def fetch_job_detail(code: str, fallback_titles: dict[str, str], config: dict) -> dict:
    """Fetch one detail page, falling back to the listing title on failure."""
    for attempt in range(config["http_retries"]):
        try:
            html, _ = fetch(
                f"{BASE}/gb/en/{code}.jsjob", config["timeout_seconds"], 1,
            )
            return parse_job(html, code)
        except Exception:  # noqa: BLE001 - fall back to the listing row
            if attempt < config["http_retries"] - 1:
                time.sleep(1.5 ** attempt)
    return fallback_job(code, fallback_titles.get(code, ""))


def parse_fetched_jobs(fetched: dict, config: dict) -> dict:
    """Follow every job code to its detail page, de-duplicating across searches."""
    jobs = []
    ids = set()
    stats = []
    for search in fetched["searches"]:
        name = search["name"]
        codes = search["codes"]
        counts = dict.fromkeys(COUNT_COLS, 0)
        counts["found"] = len(codes)
        detail_fetcher = partial(
            fetch_job_detail,
            fallback_titles=search.get("fallback_titles", {}),
            config=config,
        )
        with ThreadPoolExecutor(max_workers=config["workers"]) as pool:
            found = list(pool.map(detail_fetcher, codes))
        for job in found:
            key = job.get("uid") or ("hash:" + job.get("hash", ""))
            if key in ids:
                counts["dup"] += 1
                continue
            ids.add(key)
            job["search_name"] = name
            job["search"] = name  # never carry a shid-bearing URL into XCom
            jobs.append(job)
        stats.append({"name": name, "counts": counts})
        if config["delay_seconds"]:
            time.sleep(config["delay_seconds"])
        _LOGGER.info("Search '%s': parsed %d job detail(s)", name, len(found))
    return {"jobs": jobs, "stats": stats}

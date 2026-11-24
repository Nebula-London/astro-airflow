"""Totaljobs/CWJobs portal adapter.

Search URLs use the role in the path, for example
``/jobs/aws/in-manchester``. Listing cards contain enough structured text to
build a fallback job even when a detail page is blocked.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin

from dags.portals.base import clean_lines, empty_item, normalize_job, strip_tags
from dags.utils.job_parser import ir35_status, slugify

NAME = "totaljobs"
QUERY_PARAM = "q"

JOB_LINK = re.compile(
    r'<a\b[^>]*href=["\']([^"\']*/job/[^"\']*?-job(\d+)[^"\']*)["\'][^>]*>(.*?)</a>',
    re.S | re.I,
)
NOISE = {"more", "new", "premium", "featured"}


def detail_url(code: str, item: dict | None = None) -> str:
    """Return the detail URL captured from the listing card."""
    return (item or {}).get("url", "")


def _card_text(html: str, start: int, end: int) -> list[str]:
    return clean_lines(strip_tags(html[start:end]))


def find_items(html: str, final_url: str, src_url: str = "") -> list[dict]:
    """Extract listing cards from Totaljobs/CWJobs HTML."""
    matches = list(JOB_LINK.finditer(html))
    items = []
    seen = set()
    for index, match in enumerate(matches):
        code = match.group(2)
        if code in seen:
            continue
        seen.add(code)
        title = " ".join(strip_tags(match.group(3)).split())
        url = urljoin(final_url or src_url, match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else min(len(html), match.end() + 2500)
        lines = _card_text(html, match.end(), end)
        if lines and lines[0] == title:
            lines.pop(0)
        lines = [line for line in lines if line.lower() not in NOISE]
        item = empty_item(code, url, title)
        if lines:
            item["agency"] = lines.pop(0)
        if lines:
            item["location"] = lines.pop(0)
        if lines:
            item["rate"] = lines.pop(0)
        description = []
        for line in lines:
            if re.fullmatch(r"\d+\s+(?:hour|day|week|month|year)s?\s+ago", line, re.I):
                break
            description.append(line)
        item["description"] = " ".join(description)
        items.append(item)
    return items


def _jsonld_job(html: str) -> dict:
    """Read the first JobPosting JSON-LD block, if the page provides one."""
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.S | re.I,
    ):
        try:
            data = json.loads(match.group(1).strip())
        except (TypeError, ValueError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for candidate in candidates:
            if isinstance(candidate, dict) and "JobPosting" in str(candidate.get("@type", "")):
                return candidate
    return {}


def _job_from_jsonld(data: dict, code: str) -> dict:
    location = data.get("jobLocation") or {}
    if isinstance(location, list):
        location = location[0] if location else {}
    address = location.get("address") if isinstance(location, dict) else {}
    if not isinstance(address, dict):
        address = {}
    locality = str(address.get("addressLocality") or "").strip()
    place = locality or " ".join(
        str(address.get(key, "")).strip()
        for key in ("addressRegion", "addressCountry")
        if address.get(key)
    ).strip()
    title = str(data.get("title") or "")
    description = strip_tags(str(data.get("description") or ""))
    job = {
        "slug": slugify(title, code),
        "code": code,
        "url": str(data.get("url") or ""),
        "permalink": str(data.get("url") or ""),
        "title": title,
        "rate": "",
        "location": place,
        "ir35": "",
        "employment_type": str(data.get("employmentType") or ""),
        "industry": "",
        "duration": "",
        "start_date": "",
        "agency": str((data.get("hiringOrganization") or {}).get("name") or ""),
        "contact": "",
        "reference": str((data.get("identifier") or {}).get("value") or code),
        "posted_date": str(data.get("datePosted") or ""),
        "skills": [],
        "description": description,
    }
    status = ir35_status(description)
    job["ir35_status"] = status
    job["ir35"] = status.lower() if status in ("Inside", "Outside") else ""
    return job


def parse_detail(html: str, code: str, item: dict, portal: str, role: str) -> dict:
    """Parse a detail page, falling back to the listing card metadata."""
    data = _jsonld_job(html)
    if data:
        job = _job_from_jsonld(data, code)
    else:
        job = {
            "slug": slugify(item.get("title", ""), code),
            "code": code,
            "url": item.get("url", ""),
            "permalink": item.get("url", ""),
            "title": item.get("title", ""),
            "rate": item.get("rate", ""),
            "location": item.get("location", ""),
            "ir35": "",
            "employment_type": "",
            "industry": "",
            "duration": "",
            "start_date": "",
            "agency": item.get("agency", ""),
            "contact": "",
            "reference": code,
            "posted_date": "",
            "skills": [],
            "description": item.get("description", ""),
        }
        status = ir35_status(job["description"])
        job["ir35_status"] = status
        job["ir35"] = status.lower() if status in ("Inside", "Outside") else ""
    return normalize_job(job, item, portal, role)


def fallback_item_job(item: dict, portal: str, role: str) -> dict:
    """Build a job from listing metadata when the detail page is unavailable."""
    return parse_detail("", item["code"], item, portal, role)

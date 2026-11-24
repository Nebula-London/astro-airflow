"""LinkedIn adapter placeholder.

LinkedIn's public job search is heavily JavaScript-driven and anti-bot prone;
the official Jobs APIs are for employers/ATS partners rather than candidate
search. This portal stays disabled until an approved API or feed is chosen.
The module exists so the registry can be imported when LinkedIn is disabled.
"""
from __future__ import annotations

from dags.portals.registry import unsupported

NAME = "linkedin"
QUERY_PARAM = "keywords"


def detail_url(code: str, item: dict | None = None) -> str:
    unsupported(NAME)


def find_items(html: str, final_url: str, src_url: str = "") -> list[dict]:
    unsupported(NAME)


def parse_detail(html: str, code: str, item: dict, portal: str, role: str) -> dict:
    unsupported(NAME)


def fallback_item_job(item: dict, portal: str, role: str) -> dict:
    unsupported(NAME)

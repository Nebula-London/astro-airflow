"""Indeed adapter placeholder.

Indeed's public search HTML is reachable, but the old ``/rss`` feed is gone
(HTTP 404) and there is no public job-search API. Before enabling this portal,
capture a real results page and detail page and implement the four adapter
functions below. Keeping the module importable means the registry never fails
with ``ModuleNotFoundError`` when the portal is disabled in config.
"""
from __future__ import annotations

from dags.portals.registry import unsupported

NAME = "indeed"
QUERY_PARAM = "q"


def detail_url(code: str, item: dict | None = None) -> str:
    unsupported(NAME)


def find_items(html: str, final_url: str, src_url: str = "") -> list[dict]:
    unsupported(NAME)


def parse_detail(html: str, code: str, item: dict, portal: str, role: str) -> dict:
    unsupported(NAME)


def fallback_item_job(item: dict, portal: str, role: str) -> dict:
    unsupported(NAME)

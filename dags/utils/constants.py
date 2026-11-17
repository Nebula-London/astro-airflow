"""Constants shared across the JobServe DAG packages.

Keeping them here instead of in the task module avoids circular imports
between ``dags.connections`` and the ``dags.utils`` helper modules.
"""
from __future__ import annotations

import re

BASE = "https://www.jobserve.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) "
    "Gecko/20100101 Firefox/115.0"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.5",
    "Accept-Encoding": "identity",
}
VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
BLOCK = {
    "p", "div", "br", "li", "ul", "ol", "tr", "table", "h1", "h2", "h3",
    "h4", "h5", "h6", "section", "article",
}
CODE = re.compile(r"(?:/gb/en/)?([0-9A-Z]{16,})\.jsjob")
DETAIL_IDS = [
    "td_jobpositionnolink", "td_job_type", "md_rate", "md_location",
    "md_industry", "md_duration", "md_start_date", "md_recruiter",
    "md_contact", "md_ref", "md_posted_date", "md_permalink", "md_skills",
    "md_email",
]
COUNT_COLS = ("found", "dup", "tracker", "processed", "unfetched", "new")
SEARCH_URLS_VARIABLE = "jobserve_search_urls"
TRACKER_COLUMNS = [
    "code", "url", "title", "location", "rate", "rate_min", "rate_max",
    "ir35", "agent", "agency", "first_seen", "jd_file", "fit",
    "missing_terms", "cv_pdf", "cv_built_on", "status", "applied_on",
    "rate_submitted", "consultancy", "client", "notes", "reason",
]
RATE_RE = re.compile(
    r"(?P<amount>[0-9][0-9,]*(?:\.[0-9]+)?)\s*(?P<thousand>k)?\s*"
    r"(?:/|per\s+|an?\s+)?\s*"
    r"(?P<unit>hour|hours|hr|hrs|ph|day|days|pd|week|weeks|pw|month|months|pm|year|years|pa|annum)\b",
    re.I,
)
UNIT_TO_DAY = {
    "hour": 7.5, "hours": 7.5, "hr": 7.5, "hrs": 7.5, "ph": 7.5,
    "day": 1.0, "days": 1.0, "pd": 1.0,
    "week": 0.2, "weeks": 0.2, "pw": 0.2,
    "month": 12.0 / 220.0, "months": 12.0 / 220.0, "pm": 12.0 / 220.0,
    "year": 1.0 / 220.0, "years": 1.0 / 220.0, "pa": 1.0 / 220.0,
    "annum": 1.0 / 220.0,
}
UNIT_TO_YEAR = {
    "hour": 7.5 * 220, "hours": 7.5 * 220, "hr": 7.5 * 220,
    "hrs": 7.5 * 220, "ph": 7.5 * 220,
    "day": 220.0, "days": 220.0, "pd": 220.0,
    "week": 52.0, "weeks": 52.0, "pw": 52.0,
    "month": 12.0, "months": 12.0, "pm": 12.0,
    "year": 1.0, "years": 1.0, "pa": 1.0, "annum": 1.0,
}

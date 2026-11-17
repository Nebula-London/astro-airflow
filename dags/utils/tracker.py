"""Read from and append to the applications tracker CSV."""
from __future__ import annotations

import csv
import logging
import os
import re
from datetime import date

from dags.utils.constants import TRACKER_COLUMNS
from dags.utils.job_parser import agent, build_jd_text, ir35_status, rates

_LOGGER = logging.getLogger(__name__)


def normalize_code(value) -> str:
    """Strip an optional leading spreadsheet row number from a job code."""
    return re.sub(r"^\s*\d+\s*[-.]\s*", "", str(value or "")).strip()


def tracker_codes(path: str) -> set[str]:
    codes = set()
    if not path or not os.path.exists(path):
        return codes
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            code = normalize_code(row.get("code"))
            if code:
                codes.add(code)
    return codes


def _tracker_fields(job: dict) -> dict:
    text = job.get("jd_text") or build_jd_text(job)
    status = job.get("ir35_status") or ir35_status(text)
    low, high = rates(job.get("rate", ""), permanent=(status == "Permanent"))
    return {
        "ir35": status,
        "rate_min": low,
        "rate_max": high,
        "agent": agent(text),
    }


def track_code(job: dict) -> str:
    uid = job.get("uid") or ""
    return uid.split(":", 1)[1] if uid.startswith(("permalink:", "code:")) else job.get("code", "")


def track_row(job: dict, jd_dir: str = "", status_style: str = "") -> dict:
    status = "1-new" if status_style == "numbered" else "new"
    return {
        "code": track_code(job),
        "url": job.get("permalink") or job.get("url", ""),
        "title": job.get("title", ""),
        "location": job.get("location", ""),
        "rate": job.get("rate", ""),
        "status": status,
        "first_seen": date.today().isoformat(),
        "jd_file": os.path.join(jd_dir, job["slug"] + ".txt") if jd_dir else "",
        **_tracker_fields(job),
    }


def upsert_tracker(path: str, rows: list[dict], status_style: str = "") -> None:
    """Append rows that are not already present, preserving the CSV headers."""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    columns = list(TRACKER_COLUMNS)
    existing = []
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames:
                columns = list(reader.fieldnames)
                for column in TRACKER_COLUMNS:
                    if column not in columns:
                        columns.append(column)
            existing = list(reader)
    known = {normalize_code(row.get("code")) for row in existing}
    added = 0
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in existing:
            writer.writerow(row)
        for row in rows:
            if normalize_code(row.get("code")) in known:
                continue
            writer.writerow(row)
            added += 1
    _LOGGER.info("Tracker updated: %d new row(s) appended", added)

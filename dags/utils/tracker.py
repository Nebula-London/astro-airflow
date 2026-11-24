"""Read from and append to the one-row-per-job applications tracker CSV."""
from __future__ import annotations

import csv
import logging
import os
import re
from datetime import date

from dags.utils.constants import TRACKER_COLUMNS
from dags.utils.identity import canonical_uid, source_key, source_summary
from dags.utils.job_parser import agent, build_jd_text, ir35_status, rates

_LOGGER = logging.getLogger(__name__)


def normalize_code(value) -> str:
    """Strip an optional leading spreadsheet row number from a job code."""
    return re.sub(r"^\s*\d+\s*[-.]\s*", "", str(value or "")).strip()


def _split_sources(value) -> set[str]:
    return {
        part.strip()
        for part in str(value or "").split(",")
        if part.strip() and not part.strip().endswith(":")
    }


def tracker_seen_keys(path: str) -> set[str]:
    """Every canonical and source identity already recorded in the tracker."""
    keys = set()
    if not path or not os.path.exists(path):
        return keys
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("uid"):
                keys.add(row["uid"])
            digest = row.get("canonical_hash") or row.get("hash")
            if digest:
                keys.add("hash:" + str(digest))
            keys.update(_split_sources(row.get("sources")))
            code = normalize_code(row.get("source_code") or row.get("code"))
            if code:
                keys.add("code:" + code)
                portal = str(row.get("portal") or "").strip().lower()
                if portal:
                    keys.add(source_key(portal, code))
                # Legacy rows have a bare JobServe code and no portal column.
                if not portal:
                    keys.add(source_key("jobserve", code))
    return keys


def tracker_codes(path: str) -> set[str]:
    """Bare job codes still present in the tracker (legacy helper)."""
    codes = set()
    if not path or not os.path.exists(path):
        return codes
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            code = normalize_code(row.get("source_code") or row.get("code"))
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


def track_row(job: dict, jd_dir: str = "", status_style: str = "") -> dict:
    """Build one tracker row for a canonical job with all known sources."""
    status = "1-new" if status_style == "numbered" else "new"
    code = str(job.get("source_code") or job.get("code") or "")
    return {
        "uid": canonical_uid(job),
        "canonical_hash": job.get("canonical_hash") or job.get("hash", ""),
        "portal": job.get("portal", ""),
        "source_code": code,
        "sources": source_summary(job),
        "code": code,  # kept for spreadsheets that still expect this column
        "url": job.get("permalink") or job.get("url", ""),
        "title": job.get("title", ""),
        "location": job.get("location", ""),
        "rate": job.get("rate", ""),
        "status": status,
        "first_seen": job.get("first_seen") or date.today().isoformat(),
        "jd_file": os.path.join(jd_dir, job["slug"] + ".txt") if jd_dir else "",
        **_tracker_fields(job),
    }


def _row_identity(row: dict) -> str:
    """Canonical identity of an existing row, with a legacy code fallback."""
    if row.get("uid"):
        return row["uid"]
    code = normalize_code(row.get("source_code") or row.get("code"))
    return "code:" + code if code else ""


def _merge_sources(existing: str, incoming: str) -> str:
    return ",".join(sorted(_split_sources(existing) | _split_sources(incoming)))


def upsert_tracker(path: str, rows: list[dict], status_style: str = "") -> None:
    """Append new canonical rows; for known jobs only extend their sources.

    Status, notes and interview details on an existing row are never reset, so
    finding the same role on a second portal does not create a duplicate
    application or lose the work already recorded against it.
    """
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

    index = {}
    for row in existing:
        identity = _row_identity(row)
        if identity:
            index[identity] = row

    added = 0
    updated = 0
    new_rows = []
    for row in rows:
        identity = _row_identity(row)
        match = index.get(identity) if identity else None
        if match is not None:
            merged = _merge_sources(match.get("sources", ""), row.get("sources", ""))
            if merged != match.get("sources", ""):
                match["sources"] = merged
                updated += 1
            continue
        new_rows.append(row)
        if identity:
            index[identity] = row
        added += 1

    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in existing + new_rows:
            writer.writerow(row)
    _LOGGER.info(
        "Tracker updated: %d new row(s) appended, %d source list(s) extended",
        added, updated,
    )

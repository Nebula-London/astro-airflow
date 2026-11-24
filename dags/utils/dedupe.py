"""Drop roles already recorded in state, a prior extract or the tracker."""
from __future__ import annotations

import json
import logging
import os
from datetime import date

from dags.portals.base import merge_sources
from dags.utils.constants import COUNT_COLS
from dags.utils.identity import canonical_uid, job_keys, source_key
from dags.utils.tracker import tracker_seen_keys

_LOGGER = logging.getLogger(__name__)


def load_state(path: str) -> dict:
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except (ValueError, OSError):
            _LOGGER.info("State file unreadable; starting with an empty state")
    return {"version": 2, "seen": {}}


def _record_keys(uid: str, record: dict) -> set[str]:
    """Every identity stored in one state record."""
    keys = {uid}
    if not isinstance(record, dict):
        return keys
    digest = record.get("canonical_hash") or record.get("hash")
    if digest:
        keys.add("hash:" + str(digest))
    for source in record.get("sources") or []:
        if isinstance(source, dict):
            key = source.get("key") or source_key(source.get("portal"), source.get("code"))
            if not key.endswith(":"):
                keys.add(key)
    code = str(record.get("source_code") or record.get("code") or "").strip()
    if code:
        keys.add("code:" + code)
        portal = str(record.get("portal") or "").strip().lower()
        if portal:
            keys.add(source_key(portal, code))
    return keys


def seen_ids(state: dict, seen_dir: str) -> set[str]:
    """Seen identities from the state file plus any prior dated extract."""
    seen = set()
    for uid, record in (state.get("seen") or {}).items():
        seen.update(_record_keys(uid, record))
    if seen_dir and os.path.isdir(seen_dir):
        for name in os.listdir(seen_dir):
            if not name.endswith("_jd-extracted-from-jobserve.json"):
                continue
            try:
                with open(os.path.join(seen_dir, name), encoding="utf-8") as handle:
                    data = json.load(handle)
            except (ValueError, OSError):
                continue
            jobs = data.get("jobs", []) if isinstance(data, dict) else (
                data if isinstance(data, list) else []
            )
            for job in jobs:
                if isinstance(job, dict):
                    seen.update(job_keys(job))
    return seen


def is_seen(job: dict, seen: set[str]) -> bool:
    """Whether any canonical or source identity has already been seen."""
    return bool(job_keys(job) & seen)


def save_state(path: str, jobs: list[dict]) -> None:
    """Store canonical rows, merging new sources into an existing identity."""
    state = load_state(path)
    seen = state.setdefault("seen", {})
    today = date.today().isoformat()
    for job in jobs:
        uid = canonical_uid(job)
        record = dict(seen.get(uid) or {})
        sources = {}
        for source in list(record.get("sources") or []) + list(job.get("sources") or []):
            if isinstance(source, dict):
                key = source.get("key") or source_key(source.get("portal"), source.get("code"))
                if not key.endswith(":"):
                    sources[key] = source
        record.update(
            {
                "slug": job.get("slug", ""),
                "code": job.get("source_code") or job.get("code", ""),
                "hash": job.get("hash", ""),
                "canonical_hash": job.get("canonical_hash") or job.get("hash", ""),
                "portal": job.get("portal", ""),
                "source_code": job.get("source_code", ""),
                "source_url": job.get("source_url", ""),
                "sources": sorted(sources.values(), key=lambda item: item.get("key", "")),
                "permalink": job.get("permalink", ""),
                "first_seen": record.get("first_seen") or job.get("first_seen") or today,
            }
        )
        seen[uid] = record
    state["version"] = 2
    state["updated"] = today
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)


def deduplicate(mapped: list[dict], config: dict) -> dict:
    """Merge mapped results, then drop roles already seen or tracked."""
    stats = [{"name": row["key"], "counts": dict(row["counts"])} for row in mapped]
    by_key = {row["name"]: row["counts"] for row in stats}
    flattened = []
    for row in mapped:
        for job in row.get("jobs") or []:
            job.setdefault("target_key", row["key"])
            flattened.append(job)

    grouped = {}
    for job in flattened:
        uid = canonical_uid(job)
        if uid in grouped:
            by_key.setdefault(job.get("target_key", ""), dict.fromkeys(COUNT_COLS, 0))["dup"] += 1
            grouped[uid] = merge_sources([grouped[uid], job])
        else:
            grouped[uid] = job
    jobs = list(grouped.values())

    tracked = tracker_seen_keys(config["tracker_file"]) if config["use_tracker"] else set()
    prior = (
        seen_ids(load_state(config["state_file"]), config["seen_dir"])
        if config["dedupe"] else set()
    )

    new = []
    unfetched = []
    skipped = 0
    for job in jobs:
        counts = by_key.setdefault(
            job.get("target_key", ""), dict.fromkeys(COUNT_COLS, 0)
        )
        if config["dedupe"] and is_seen(job, tracked):
            counts["tracker"] += 1
            skipped += 1
            continue
        if config["dedupe"] and is_seen(job, prior):
            counts["processed"] += 1
            skipped += 1
            continue
        if job.get("error"):
            counts["unfetched"] += 1
            unfetched.append(job.get("source_code") or job.get("code", ""))
            skipped += 1
            continue
        counts["new"] += 1
        new.append(job)

    _LOGGER.info(
        "De-duplication: %d new, %d skipped, %d not fetched, %d tracked key(s)",
        len(new), skipped, len(unfetched), len(tracked),
    )
    return {
        "new": new,
        "unfetched": unfetched,
        "stats": stats,
        "skipped": skipped,
        "tracker_size": len(tracked) if config["use_tracker"] else None,
        "dedupe": config["dedupe"],
    }

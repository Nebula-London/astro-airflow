"""Drop roles already recorded in state, a prior extract or the tracker."""
from __future__ import annotations

import json
import logging
import os
from datetime import date

from dags.utils.constants import COUNT_COLS
from dags.utils.tracker import tracker_codes

_LOGGER = logging.getLogger(__name__)


def load_state(path: str) -> dict:
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except (ValueError, OSError):
            _LOGGER.info("State file unreadable; starting with an empty state")
    return {"version": 1, "seen": {}}


def seen_ids(state: dict, seen_dir: str) -> set[str]:
    """Seen roles from the state file plus any prior dated extract."""
    seen = set()
    for uid, record in (state.get("seen") or {}).items():
        seen.add(uid)
        if isinstance(record, dict) and record.get("hash"):
            seen.add("hash:" + record["hash"])
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
                    if job.get("uid"):
                        seen.add(job["uid"])
                    if job.get("hash"):
                        seen.add("hash:" + job["hash"])
    return seen


def is_seen(job: dict, seen: set[str]) -> bool:
    return job.get("uid") in seen or ("hash:" + job.get("hash", "")) in seen


def save_state(path: str, jobs: list[dict]) -> None:
    state = load_state(path)
    seen = state.setdefault("seen", {})
    for job in jobs:
        seen[job["uid"]] = {
            "slug": job["slug"],
            "code": job.get("code", ""),
            "hash": job.get("hash", ""),
            "permalink": job.get("permalink", ""),
            "first_seen": date.today().isoformat(),
        }
    state["version"] = state.get("version", 1)
    state["updated"] = date.today().isoformat()
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)


def deduplicate(parsed: dict, config: dict) -> dict:
    """Drop roles already in the tracker, state file or an earlier extract."""
    jobs = parsed["jobs"]
    stats = parsed["stats"]
    by_name = {row["name"]: row["counts"] for row in stats}
    tracked = tracker_codes(config["tracker_file"]) if config["use_tracker"] else set()
    prior = seen_ids(load_state(config["state_file"]), config["seen_dir"]) if config["dedupe"] else set()
    tracked_ids = {"permalink:" + code for code in tracked} | {
        "code:" + code for code in tracked
    }
    seen = prior | tracked_ids
    new = []
    unfetched = []
    skipped = 0
    for job in jobs:
        counts = by_name.setdefault(job.get("search_name", ""), dict.fromkeys(COUNT_COLS, 0))
        if config["dedupe"] and is_seen(job, tracked_ids):
            counts["tracker"] += 1
            skipped += 1
            continue
        if config["dedupe"] and is_seen(job, prior):
            counts["processed"] += 1
            skipped += 1
            continue
        if job.get("error"):
            counts["unfetched"] += 1
            unfetched.append(job.get("code", ""))
            skipped += 1
            continue
        counts["new"] += 1
        new.append(job)
    _LOGGER.info(
        "De-duplication: %d new, %d skipped, %d not fetched, %d role(s) in tracker",
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

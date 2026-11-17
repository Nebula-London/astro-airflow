"""Write the dated JSON, JD text files, state file and tracker CSV."""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path

from dags.utils.dedupe import save_state
from dags.utils.job_parser import build_jd_text
from dags.utils.reporting import summary_table
from dags.utils.tracker import track_row, upsert_tracker

_LOGGER = logging.getLogger(__name__)


def dated_name(day: date | None = None) -> str:
    return (day or date.today()).isoformat() + "_jd-extracted-from-jobserve.json"


def write_json(path: str, config: dict, jobs: list[dict], skipped: int) -> dict:
    """Merge today's new roles into the dated extract and write it."""
    existing = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            existing = data.get("jobs", []) if isinstance(data, dict) else (
                data if isinstance(data, list) else []
            )
        except (ValueError, OSError):
            existing = []
    by_uid = {}
    for job in existing + jobs:
        by_uid.setdefault(job.get("uid") or job.get("slug"), job)
    merged = list(by_uid.values())
    data = {
        "source": ", ".join(search["name"] for search in config["searches"]),
        "generated": date.today().isoformat(),
        "count": len(merged),
        "skipped": skipped,
        "jobs": merged,
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
    _LOGGER.info("Wrote %d job(s) to the dated JSON", len(merged))
    return data


def write_jd_dir(dirpath: str, jobs: list[dict]) -> None:
    os.makedirs(dirpath, exist_ok=True)
    for job in jobs:
        with open(os.path.join(dirpath, job["slug"] + ".txt"), "w", encoding="utf-8") as handle:
            handle.write(build_jd_text(job).rstrip() + "\n")
    _LOGGER.info("Wrote %d JD text file(s)", len(jobs))


def write_all(result: dict, config: dict) -> str:
    """Write the dated JSON, JD text, state file and tracker CSV."""
    jobs = result["new"]
    out_path = str(Path(config["output_dir"]) / dated_name())
    write_json(out_path, config, jobs, result["skipped"])
    if config["jd_dir"] and jobs:
        write_jd_dir(config["jd_dir"], jobs)
    if config["dedupe"] and jobs:
        save_state(config["state_file"], jobs)
    if config["use_tracker"] and jobs:
        rows = [
            track_row(job, config["jd_dir"], config["status_style"])
            for job in jobs
        ]
        upsert_tracker(config["tracker_file"], rows, config["status_style"])
    for line in summary_table(jobs).splitlines():
        _LOGGER.info("%s", line)
    total = sum(row["counts"]["found"] for row in result["stats"])
    already_tracked = sum(row["counts"]["tracker"] for row in result["stats"])
    _LOGGER.info(
        "RESULT new=%d seen=%d already_tracked=%d total=%d unfetched=%d json=%s",
        len(jobs), result["skipped"], already_tracked, total,
        len(result["unfetched"]), out_path,
    )
    return out_path

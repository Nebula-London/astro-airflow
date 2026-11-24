"""Build the dated JD handoff JSON from Oracle NoSQL rows."""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path

from dags.utils.nosql import read_jobs

_LOGGER = logging.getLogger(__name__)


def source_file_path(config: dict) -> str:
    """Return today's source JSON path."""
    return str(Path(config["source_dir"]) / f"{date.today().isoformat()}_jd-from-nosql.json")


def write_source_json(config: dict, jobs: list[dict], path: str) -> str:
    """Write the JSON shape expected by ``cv.py daily``."""
    data = {
        "source": f"oracle_nosql:{config['nosql_table']}",
        "generated": datetime.now(timezone.utc).isoformat(),
        "count": len(jobs),
        "skipped": 0,
        "jobs": jobs,
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
    _LOGGER.info("Wrote %d NoSQL JD(s) to %s", len(jobs), path)
    return path


def read_and_write_jobs(config: dict) -> str:
    """Read JD rows from Oracle NoSQL and return the source JSON path."""
    jobs = read_jobs(config)
    if not jobs:
        raise ValueError(
            f"No JD documents found in Oracle NoSQL table '{config['nosql_table']}'"
        )
    return write_source_json(config, jobs, source_file_path(config))

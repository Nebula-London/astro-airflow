"""Map JobServe records to rows in the ``jobserve_seen`` NoSQL table."""
from __future__ import annotations

import logging
from datetime import date

from dags.connections.nosql_connection import connection_settings
from dags.utils.dedupe import load_state
from dags.utils.output import dated_name

_LOGGER = logging.getLogger(__name__)


def state_first_seen(state: dict, uid: str) -> str:
    """Use the date recorded by the state writer, falling back to today."""
    record = (state.get("seen") or {}).get(uid) or {}
    return str(record.get("first_seen") or date.today().isoformat())


def build_row(job: dict, first_seen: str, source_file: str) -> dict:
    """Build one row matching the table schema, with the full job in the JSON column."""
    return {
        "uid": job["uid"],
        "content_hash": job.get("hash", ""),
        "code": job.get("code", ""),
        "slug": job.get("slug", ""),
        "permalink": job.get("permalink", ""),
        "first_seen": first_seen,
        "source_file": source_file,
        "document": job,
    }


def push_jobs(config: dict, jobs: list[dict]) -> int:
    """Upsert each hashed job into Oracle NoSQL and return the row count."""
    if not jobs:
        _LOGGER.info("No new JobServe roles to push to Oracle NoSQL")
        return 0
    import oci

    client, table, compartment = connection_settings()
    state = load_state(config["state_file"])
    source_file = dated_name()
    for job in jobs:
        details = oci.nosql.models.UpdateRowDetails(
            value=build_row(job, state_first_seen(state, job["uid"]), source_file),
            compartment_id=compartment,
        )
        client.update_row(table_name_or_id=table, update_row_details=details)
    _LOGGER.info("Pushed %d row(s) to Oracle NoSQL table %s", len(jobs), table)
    return len(jobs)

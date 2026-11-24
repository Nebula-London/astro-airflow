"""Read and write Oracle NoSQL rows that hold JobServe JD documents.

The ``jobserve_seen`` table stores one row per role. Its ``document`` column
holds the full job dict written by the ``jobserve_new_jobs`` DAG. This module
also reads those rows so the CV DAG can use the same JDs without scraping or
reading local extracts.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date
from functools import partial
from typing import Any

from dags.connections.nosql_connection import connection_settings
from dags.utils.dedupe import load_state
from dags.utils.identity import canonical_uid, nosql_row_key
from dags.utils.oci_retry import call_with_retry
from dags.utils.output import dated_name

_LOGGER = logging.getLogger(__name__)
_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DEFAULT_PAGE_SIZE = 10
_DEFAULT_PAGE_DELAY_SECONDS = 1.0
_DEFAULT_RETRIES = 5
_DEFAULT_RETRY_DELAY_SECONDS = 1.0


def state_first_seen(state: dict, uid: str) -> str:
    """Use the date recorded by the state writer, falling back to today."""
    record = (state.get("seen") or {}).get(uid) or {}
    return str(record.get("first_seen") or date.today().isoformat())


def build_row(job: dict, first_seen: str, source_file: str) -> dict:
    """Build one row matching the table schema, with the full job in JSON.

    The ``uid`` column is the NoSQL primary key, so it holds the hash digest
    without the ``hash:`` prefix (69 bytes would exceed the 64-byte limit).
    The full canonical uid remains in ``document`` for deduplication.
    """
    document = dict(job)
    document.setdefault("canonical_hash", job.get("hash", ""))
    document.setdefault("sources", [])
    return {
        "uid": nosql_row_key(job),
        "content_hash": job.get("hash", ""),
        "code": job.get("source_code") or job.get("code", ""),
        "slug": job.get("slug", ""),
        "permalink": job.get("permalink", ""),
        "first_seen": first_seen,
        "source_file": source_file,
        "document": document,
    }


def push_jobs(config: dict, jobs: list[dict]) -> int:
    """Upsert each canonical job into Oracle NoSQL and return the row count."""
    if not jobs:
        _LOGGER.info("No new roles to push to Oracle NoSQL")
        return 0
    import oci

    client, table, compartment = connection_settings()
    state = load_state(config["state_file"])
    source_file = dated_name()
    retries = max(1, int(config.get("nosql_retries", _DEFAULT_RETRIES)))
    retry_delay = max(
        0.0, float(config.get("nosql_retry_delay_seconds", _DEFAULT_RETRY_DELAY_SECONDS))
    )
    for job in jobs:
        uid = canonical_uid(job)
        row = build_row(job, state_first_seen(state, uid), source_file)
        _LOGGER.info(
            "Upserting Oracle NoSQL table=%s key=%s canonical_uid=%s source=%s",
            table,
            row["uid"],
            uid,
            job.get("source_code") or job.get("code") or "",
        )
        details = oci.nosql.models.UpdateRowDetails(
            value=row,
            compartment_id=compartment,
        )
        call_with_retry(
            partial(client.update_row, table_name_or_id=table, update_row_details=details),
            retries=retries,
            base_delay=retry_delay,
            action="Oracle NoSQL update_row",
        )
    _LOGGER.info("Pushed %d row(s) to Oracle NoSQL table %s", len(jobs), table)
    return len(jobs)


def validate_table_name(table: str) -> str:
    """Reject table names that are not simple SQL identifiers."""
    value = str(table or "").strip()
    if not _TABLE_NAME.match(value):
        raise ValueError(f"Invalid Oracle NoSQL table name: {value!r}")
    return value


def query_rows(config: dict) -> list[dict]:
    """Read every row from the configured table, following all result pages."""
    import oci

    table = validate_table_name(config["nosql_table"])
    client, client_table, compartment = connection_settings(table)
    statement = f"SELECT * FROM {table}"
    page_size = max(1, int(config.get("nosql_page_size", _DEFAULT_PAGE_SIZE)))
    page_delay = max(
        0.0, float(config.get("nosql_page_delay_seconds", _DEFAULT_PAGE_DELAY_SECONDS))
    )
    max_rows = max(0, int(config["nosql_max_rows"]))
    consistency = str(config.get("nosql_consistency") or "EVENTUAL").upper()
    retries = max(1, int(config.get("nosql_retries", _DEFAULT_RETRIES)))
    retry_delay = max(
        0.0, float(config.get("nosql_retry_delay_seconds", _DEFAULT_RETRY_DELAY_SECONDS))
    )
    rows: list[dict] = []
    page = ""
    _LOGGER.info(
        "Reading Oracle NoSQL table %s (page_size=%d, page_delay=%.2fs, "
        "max_rows=%d, consistency=%s)",
        client_table, page_size, page_delay, max_rows, consistency,
    )

    while True:
        current_page_size = page_size
        if max_rows:
            current_page_size = min(page_size, max_rows - len(rows))
            if current_page_size <= 0:
                break
        details = oci.nosql.models.QueryDetails(
            compartment_id=compartment,
            statement=statement,
            is_prepared=False,
            consistency=consistency,
        )
        kwargs: dict[str, Any] = {"query_details": details, "limit": current_page_size}
        if page:
            kwargs["page"] = page
        response = call_with_retry(
            partial(client.query, **kwargs),
            retries=retries,
            base_delay=retry_delay,
            action="Oracle NoSQL query",
        )
        rows.extend(list(getattr(response.data, "items", None) or []))
        if max_rows and len(rows) >= max_rows:
            break
        if not getattr(response, "has_next_page", False):
            break
        next_page = getattr(response, "next_page", "")
        if not next_page or next_page == page:
            break
        page = next_page
        if page_delay:
            _LOGGER.info(
                "Pacing Oracle NoSQL reads: sleeping %.2fs before next page", page_delay
            )
            time.sleep(page_delay)

    _LOGGER.info("Read %d row(s) from Oracle NoSQL table %s", len(rows), client_table)
    return rows[:max_rows] if max_rows else rows


def row_document(row: dict, document_field: str) -> dict:
    """Return the job dict stored in a row, accepting JSON text or a dict."""
    value = row.get(document_field) if document_field in row else row
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            _LOGGER.info("Skipping NoSQL row with invalid %s JSON", document_field)
            return {}
    return value if isinstance(value, dict) else {}


def job_identity(job: dict) -> str:
    """Return the stable identity used to de-duplicate rows."""
    return str(job.get("uid") or job.get("code") or job.get("hash") or job.get("slug") or "")


def job_sort_key(job: dict) -> tuple[str, str]:
    """Sort newest first, then by code for stable output."""
    return str(job.get("first_seen") or ""), str(job.get("code") or "")


def read_jobs(config: dict) -> list[dict]:
    """Read JD documents from Oracle NoSQL and return unique, usable jobs."""
    rows = query_rows(config)
    field = str(config.get("nosql_document_field") or "document")
    by_identity: dict[str, dict] = {}
    skipped = 0

    for row in rows:
        job = row_document(row, field)
        identity = job_identity(job)
        has_text = bool(job.get("jd_text") or job.get("description"))
        if not identity or not job.get("title") or not has_text:
            skipped += 1
            continue
        by_identity.setdefault(identity, job)

    jobs = sorted(by_identity.values(), key=job_sort_key, reverse=True)
    max_rows = max(0, int(config["nosql_max_rows"]))
    if max_rows:
        jobs = jobs[:max_rows]
    _LOGGER.info(
        "Oracle NoSQL returned %d usable JD(s), skipped %d non-JD row(s)",
        len(jobs), skipped,
    )
    return jobs

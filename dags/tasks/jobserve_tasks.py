"""Airflow 3 TaskFlow tasks for the multi-portal job-search pipeline.

The orchestration is one mapped task per ``portal::role`` target:

* ``build_search_targets`` -- expand roles from config across enabled portals;
* ``fetch_search_codes`` -- fetch one listing, returning compact items;
* ``parse_job_details`` -- parse that target's detail pages;
* ``deduplicate_jobs`` -- merge all mapped results by canonical identity.

URL templates are read from the secret store inside the mapped tasks, so they
never appear in config, XCom or logs.
"""
from __future__ import annotations

import logging

from airflow.sdk import task

from dags.connections.portal_secrets import read_portal_urls
from dags.utils.config import build_config, build_targets, load_yaml
from dags.utils.dedupe import deduplicate
from dags.utils.fetching import fetch_target, parse_target
from dags.utils.nosql import push_jobs
from dags.utils.output import write_all
from dags.utils.reporting import counts_table

_LOGGER = logging.getLogger(__name__)


@task
def load_jobserve_config(config_path: str) -> dict:
    """Read non-secret config; portal URL templates stay in the secret store."""
    return build_config(load_yaml(config_path), read_portal_urls())


@task
def build_search_targets(config_path: str) -> list[dict]:
    """Return the non-secret ``portal::role`` targets used for dynamic mapping."""
    targets, _ = build_targets(load_yaml(config_path), read_portal_urls())
    return targets


@task
def fetch_search_codes(target: dict, config: dict) -> dict:
    """Fetch one portal/role listing (the URL template is read at runtime)."""
    return fetch_target(target, config)


@task
def parse_job_details(fetched: dict, config: dict) -> dict:
    """Parse one target's job detail pages into normalized records."""
    return parse_target(fetched, config)


@task
def deduplicate_jobs(mapped: list[dict], config: dict) -> dict:
    """Merge mapped results and drop roles already seen or tracked."""
    return deduplicate(mapped, config)


@task
def report_counts(result: dict) -> None:
    """Log the counts table first, as soon as de-duplication is done."""
    for line in counts_table(result["stats"], result["tracker_size"], result["dedupe"]).splitlines():
        _LOGGER.info("%s", line)


@task
def write_results(result: dict, config: dict) -> str:
    """Write the dated JSON, JD text, state file and tracker CSV."""
    return write_all(result, config)


@task
def push_to_nosql(result: dict, config: dict) -> int:
    """Upsert the canonical hashed roles into the Oracle NoSQL seen table."""
    return push_jobs(config, result["new"])

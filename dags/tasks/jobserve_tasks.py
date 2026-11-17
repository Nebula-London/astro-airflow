"""Airflow 3 TaskFlow tasks for the ``jobserve-new-jobs`` skill.

This module is the orchestration layer only: it exposes the six module-level
``@task`` callables used by :mod:`dags.factory.jobserve_factory`. The business
logic has been split into small, focused packages under ``dags/``:

* ``dags.connections`` -- private search URLs (Airflow Variable / env var);
* ``dags.utils.config`` -- non-secret config and output paths;
* ``dags.utils.fetching`` -- listing and detail-page retrieval;
* ``dags.utils.dedupe`` -- state / prior extract de-duplication;
* ``dags.utils.reporting`` -- counts and summary tables;
* ``dags.utils.output`` -- JSON / JD / state / tracker writes;
* ``dags.utils.nosql`` -- hashed rows pushed to Oracle NoSQL.

Two rules are applied throughout: every helper is defined at module level
(there are no functions inside functions and no lambdas), and there are no
``print`` calls -- all output goes through a module logger.
"""
from __future__ import annotations

import logging

from airflow.sdk import task

from dags.connections import read_search_urls
from dags.utils.config import build_config, load_yaml
from dags.utils.dedupe import deduplicate
from dags.utils.fetching import fetch_searches, parse_fetched_jobs
from dags.utils.nosql import push_jobs
from dags.utils.output import write_all
from dags.utils.reporting import counts_table

_LOGGER = logging.getLogger(__name__)


@task
def load_jobserve_config(config_path: str) -> dict:
    """Read the non-secret config; the shid URLs are re-read by the fetch task."""
    return build_config(load_yaml(config_path), read_search_urls())


@task
def fetch_search_codes(config: dict) -> dict:
    """Fetch each saved search (URLs come from the secret store, not XCom)."""
    return fetch_searches(config, read_search_urls())


@task
def parse_job_details(fetched: dict, config: dict) -> dict:
    """Follow every job code to its detail page, de-duplicating across searches."""
    return parse_fetched_jobs(fetched, config)


@task
def deduplicate_jobs(parsed: dict, config: dict) -> dict:
    """Drop roles already in the tracker, state file or an earlier extract."""
    return deduplicate(parsed, config)


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
    """Upsert the new hashed roles into the Oracle NoSQL seen table."""
    return push_jobs(config, result["new"])

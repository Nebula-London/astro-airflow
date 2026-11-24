"""Airflow 3 TaskFlow tasks for JD-to-master matching.

Tasks stay thin: config, NoSQL reads, script calls and reporting live in
``dags/utils``. All helpers are module-level; there are no nested functions,
lambdas or ``print`` calls. No CV or PDF generation happens here.
"""
from __future__ import annotations

from airflow.sdk import task

from dags.utils.cv_config import load_cv_config
from dags.utils.cv_reporting import summarize
from dags.utils.cv_skill import check_master, match_jds
from dags.utils.cv_source import read_and_write_jobs


@task
def load_cv_config_task(config_path: str) -> dict:
    """Read the CV YAML config and resolve all runtime paths."""
    return load_cv_config(config_path)


@task
def read_nosql_jds(config: dict) -> str:
    """Read JD documents from Oracle NoSQL into the dated source JSON."""
    return read_and_write_jobs(config)


@task
def check_master_file(config: dict) -> dict:
    """Validate the configured master resume with the skill's checker."""
    return check_master(config)


@task
def match_jds_to_master(source_json: str, config: dict) -> dict:
    """Match every NoSQL JD against the master and write triage JSON."""
    return match_jds(config, source_json)


@task
def report_match_scores(match: dict, config: dict) -> dict:
    """Write and log the JD match percentage summary."""
    return summarize(match, config)

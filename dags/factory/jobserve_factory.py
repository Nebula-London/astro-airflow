"""DAG factory for the full ``jobserve-new-jobs`` find-jobs pipeline.

The task callables live in ``dags/tasks/jobserve_tasks.py`` and delegate to
helpers in ``dags/utils`` / ``dags.connections``; the final DAG file at
``dags/jobserve_dag.py`` only calls :func:`build_jobserve_dag`.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from airflow.sdk import DAG

from dags.tasks.jobserve_tasks import (
    deduplicate_jobs,
    fetch_search_codes,
    load_jobserve_config,
    parse_job_details,
    report_counts,
    write_results,
)

DEFAULT_CONFIG_PATH = str(
    Path(__file__).resolve().parents[1] / "config" / "jobserve_config.yaml"
)


def build_jobserve_dag(
    config_path: str = DEFAULT_CONFIG_PATH,
    dag_id: str = "jobserve_new_jobs",
) -> DAG:
    """Wire the module-level TaskFlow callables into the final DAG."""
    with DAG(
        dag_id=dag_id,
        description="Find new JobServe roles from saved searches and write today's JSON",
        schedule="0 7 * * *",
        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        catchup=False,
        max_active_runs=1,
        tags=["jobserve", "job-search"],
        default_args={"retries": 2, "retry_delay": timedelta(minutes=2)},
    ) as dag:
        config = load_jobserve_config(config_path)
        fetched = fetch_search_codes(config)
        parsed = parse_job_details(fetched, config)
        deduped = deduplicate_jobs(parsed, config)
        reported = report_counts(deduped)
        write_results(reported, config)
    return dag

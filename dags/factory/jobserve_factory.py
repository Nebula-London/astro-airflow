"""DAG factory for the multi-portal ``jobserve-new-jobs`` pipeline.

The task callables live in ``dags/tasks/jobserve_tasks.py`` and delegate to
helpers in ``dags/utils`` / ``dags/portals``. Each ``portal::role`` target is a
separate mapped fetch/parse pair, so adding a role in config or a URL template
in the Airflow Variable is all that is needed to search another site.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from airflow.sdk import DAG

from dags.tasks.jobserve_tasks import (
    build_search_targets,
    deduplicate_jobs,
    fetch_search_codes,
    load_jobserve_config,
    parse_job_details,
    push_to_nosql,
    report_counts,
    write_results,
)

DEFAULT_CONFIG_PATH = str(
    Path(__file__).resolve().parents[1] / "config" / "jobs_config.yaml"
)


def build_jobserve_dag(
    config_path: str = DEFAULT_CONFIG_PATH,
    dag_id: str = "jobserve_new_jobs",
) -> DAG:
    """Wire the module-level TaskFlow callables into the final DAG."""
    with DAG(
        dag_id=dag_id,
        description="Find new roles across job portals, write extracts and push hashes to NoSQL",
        schedule="0 7 * * *",
        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        catchup=False,
        max_active_runs=1,
        tags=["jobs", "job-search", "multi-portal"],
        default_args={"retries": 2, "retry_delay": timedelta(minutes=2)},
    ) as dag:
        config = load_jobserve_config(config_path)
        targets = build_search_targets(config_path)
        fetched = fetch_search_codes.partial(config=config).expand(target=targets)
        parsed = parse_job_details.partial(config=config).expand(fetched=fetched)
        deduped = deduplicate_jobs(parsed, config)
        report_counts(deduped)
        written = write_results(deduped, config)
        pushed = push_to_nosql(deduped, config)
        written >> pushed
    return dag

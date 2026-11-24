"""DAG factory for matching Oracle NoSQL JDs against the master resume.

The task callables live in ``dags/tasks/cv_tasks.py``; logic lives in
``dags/utils``. Final DAG at ``dags/cv_dag.py`` only builds the DAG object.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from airflow.sdk import DAG

from dags.tasks.cv_tasks import (
    check_master_file,
    load_cv_config_task,
    match_jds_to_master,
    read_nosql_jds,
    report_match_scores,
)

DEFAULT_CONFIG_PATH = str(
    Path(__file__).resolve().parents[1] / "config" / "cv_config.yaml"
)


def build_cv_dag(
    config_path: str = DEFAULT_CONFIG_PATH,
    dag_id: str = "cv_master_jd_match",
) -> DAG:
    """Wire the module-level TaskFlow callables into the final DAG."""
    with DAG(
        dag_id=dag_id,
        description="Match Oracle NoSQL JDs against master.json and report percentages",
        schedule="0 8 * * *",
        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        catchup=False,
        max_active_runs=1,
        tags=["cv", "job-search", "nosql"],
        default_args={"retries": 2, "retry_delay": timedelta(minutes=2)},
    ) as dag:
        config = load_cv_config_task(config_path)
        source = read_nosql_jds(config)
        master = check_master_file(config)
        match = match_jds_to_master(source, config)
        master >> match
        report_match_scores(match, config)
    return dag

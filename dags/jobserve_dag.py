"""Final DAG for the jobserve-new-jobs pipeline.

The task callables live in ``dags/tasks/jobserve_tasks.py`` and the logic they
delegate to lives in ``dags/utils`` / ``dags/connections``; the wiring lives in
``dags/factory/jobserve_factory.py``. This file only builds and exposes the DAG
object that Airflow discovers.
"""
from pathlib import Path

from dags.factory.jobserve_factory import build_jobserve_dag

CONFIG_PATH = str(Path(__file__).resolve().parent / "config" / "jobserve_config.yaml")

dag = build_jobserve_dag(config_path=CONFIG_PATH)

"""Final DAG for the jobserve-new-jobs pipeline.

All logic lives in ``dags/tasks/jobserve_tasks.py`` and the wiring lives in
``dags/factory/jobserve_factory.py``; this file only builds and exposes the
DAG object that Airflow discovers.
"""
from pathlib import Path

from dags.factory.jobserve_factory import build_jobserve_dag

CONFIG_PATH = str(Path(__file__).resolve().parent / "config" / "jobserve_config.yaml")

dag = build_jobserve_dag(config_path=CONFIG_PATH)

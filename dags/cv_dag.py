"""Final DAG for JD-to-master matching.

Task callables live in ``dags/tasks/cv_tasks.py``; wiring lives in
``dags/factory/cv_factory.py``. This file only exposes the DAG object that
Airflow discovers.
"""
from pathlib import Path

from dags.factory.cv_factory import build_cv_dag

CONFIG_PATH = str(Path(__file__).resolve().parent / "config" / "cv_config.yaml")

dag = build_cv_dag(config_path=CONFIG_PATH)

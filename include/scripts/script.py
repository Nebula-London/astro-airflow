import json
from pathlib import Path

from airflow.sdk import DAG

from include.template.template import build_file_dag


CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "dynamic_dags.json"


def build_dags() -> list[DAG]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("The dynamic DAG config must contain a JSON object.")

    folder = config.get("folder")
    files = config.get("files")
    if not isinstance(folder, str) or not folder.strip():
        raise ValueError("'folder' must be a non-empty string.")
    if not isinstance(files, list) or not all(
        isinstance(file, str) and file.strip() for file in files
    ):
        raise ValueError("'files' must be a list of non-empty strings.")

    return [
        build_file_dag(f"dynamic_dag_file_{index}", folder, file_name)
        for index, file_name in enumerate(files)
    ]

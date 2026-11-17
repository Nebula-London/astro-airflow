from datetime import datetime, timezone
from pathlib import Path

from airflow.sdk import dag, task


def build_file_dag(dag_id: str, folder: str, file_name: str) -> dag:
    @dag(
        dag_id=dag_id,
        schedule="@daily",
        start_date=datetime(2022, 1, 1, tzinfo=timezone.utc),
        catchup=False,
        tags=["generated", "files"],
        default_args={"retries": 2},
    )
    def file_dag() -> None:
        @task
        def process_file(file_path: str) -> str:
            print(f"Processing file: {file_path}")
            return file_path

        process_file(str(Path(folder) / file_name))

    return file_dag()

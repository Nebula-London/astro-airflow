"""Load non-secret config for the ``targeted-cv-from-master`` DAG."""
from __future__ import annotations

import logging
from pathlib import Path

from dags.utils.config import load_yaml, project_root

_LOGGER = logging.getLogger(__name__)
_DEFAULT_TABLE = "jobserve_seen"


def resolve_config_path(value: str, base: Path) -> str:
    """Resolve a config path against the project root, or keep absolute paths."""
    path = Path(str(value or "")).expanduser()
    if not path.is_absolute():
        path = base / path
    return str(path.resolve())


def build_cv_config(raw: dict) -> dict:
    """Validate CV settings and resolve every path used by the pipeline."""
    base = project_root()
    output_dir = resolve_config_path(
        raw.get("output_dir") or base / "include" / "cv", base
    )
    output_path = Path(output_dir)
    nosql = raw.get("oracle_nosql") if isinstance(raw.get("oracle_nosql"), dict) else {}
    consistency = str(nosql.get("consistency") or "EVENTUAL").upper()
    if consistency not in {"EVENTUAL", "ABSOLUTE"}:
        consistency = "EVENTUAL"

    config = {
        "master_file": resolve_config_path(
            raw.get("master_file") or output_path / "master.json", base
        ),
        "output_dir": output_dir,
        "source_dir": resolve_config_path(
            raw.get("source_dir") or output_path / "source", base
        ),
        "run_root": resolve_config_path(
            raw.get("run_root") or output_path / "runs", base
        ),
        "tracker_file": resolve_config_path(
            raw.get("tracker_file") or output_path / "applications.csv", base
        ),
        "job_search_home": resolve_config_path(
            raw.get("job_search_home") or output_path / "home", base
        ),
        "skill_dir": resolve_config_path(
            raw.get("skill_dir") or base / "include" / "cv_skill", base
        ),
        "answers_file": (
            resolve_config_path(raw["answers_file"], base)
            if raw.get("answers_file") else ""
        ),
        "nosql_table": str(nosql.get("table") or _DEFAULT_TABLE).strip(),
        "nosql_document_field": str(nosql.get("document_field") or "document").strip(),
        "nosql_page_size": max(1, int(nosql.get("page_size", 500))),
        "nosql_max_rows": max(0, int(nosql.get("max_rows", 0))),
        "nosql_consistency": consistency,
        "report_top_n": max(0, int(raw.get("report_top_n", 20))),
    }
    _LOGGER.info(
        "Loaded CV config: master=%s, tracker=%s, table=%s, skill=%s",
        config["master_file"], config["tracker_file"], config["nosql_table"],
        config["skill_dir"],
    )
    return config


def load_cv_config(path: str) -> dict:
    """Read a YAML file and build the CV runtime config."""
    return build_cv_config(load_yaml(path))

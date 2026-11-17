"""Load the non-secret JobServe config and resolve all output paths.

Search *names* come from the YAML file; the shid-bearing URLs are supplied
separately by :mod:`dags.connections` so they never enter config or XCom.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from dags.utils.constants import SEARCH_URLS_VARIABLE

_LOGGER = logging.getLogger(__name__)


def project_root() -> Path:
    """Return the repository root (two levels above this module)."""
    return Path(__file__).resolve().parents[2]


def load_yaml(path: str) -> dict:
    """Read a YAML file and return its mapping (or an empty dict)."""
    with open(path, encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    return loaded if isinstance(loaded, dict) else {}


def build_config(raw: dict, urls: dict[str, str]) -> dict:
    """Validate the search names and resolve every path/default into a dict."""
    names = []
    for entry in raw.get("searches") or []:
        name = str(entry.get("name") if isinstance(entry, dict) else entry).strip()
        if name and name not in names:
            names.append(name)
    if not names:
        names = list(urls)
    missing = [name for name in names if name not in urls]
    if missing:
        raise ValueError(
            f"{len(missing)} configured search name(s) have no saved URL: "
            f"{', '.join(missing)}. Update Airflow Variable '{SEARCH_URLS_VARIABLE}'."
        )

    output_dir = str(
        Path(str(raw.get("output_dir") or project_root() / "include" / "jobserve")).expanduser()
    )
    output_path = Path(output_dir)
    config = {
        "searches": [{"name": name} for name in names],
        "output_dir": output_dir,
        "state_file": str(Path(str(raw.get("state_file") or output_path / "state.json")).expanduser()),
        "seen_dir": output_dir,
        "tracker_file": str(
            Path(str(raw.get("tracker_file") or output_path / "applications.csv")).expanduser()
        ),
        "jd_dir": str(Path(str(raw.get("jd_dir") or output_path / "jds")).expanduser()),
        "workers": max(1, int(raw.get("workers", 6))),
        "delay_seconds": max(0.0, float(raw.get("delay_seconds", 0.0))),
        "timeout_seconds": float(raw.get("timeout_seconds", 30.0)),
        "http_retries": max(1, int(raw.get("http_retries", 3))),
        "limit": max(0, int(raw.get("limit", 0))),
        "dedupe": bool(raw.get("dedupe", True)),
        "use_tracker": bool(raw.get("use_tracker", True)),
        "status_style": str(raw.get("status_style") or ""),
    }
    _LOGGER.info(
        "Loaded JobServe config: %d search(es), output_dir=%s, dedupe=%s",
        len(names), config["output_dir"], config["dedupe"],
    )
    return config

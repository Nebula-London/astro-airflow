"""Run only the matching part of the vendored CV skill.

No PDFs are generated. The DAG writes JD text files from Oracle NoSQL rows,
runs ``batch_triage.py`` against the configured master and reports match
percentages.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)
_MASTER_VERSION = re.compile(r"-v(\d+)\.json$", re.I)


def prepare_runtime(config: dict) -> None:
    """Create the private skill home and all output folders before a call."""
    for key in ("job_search_home", "output_dir", "source_dir", "run_root"):
        os.makedirs(config[key], exist_ok=True)
    os.makedirs(os.path.join(config["job_search_home"], ".jobsearch"), exist_ok=True)


def script_path(config: dict, name: str) -> str:
    """Return the absolute path of a vendored skill script."""
    return str(Path(config["skill_dir"]) / "scripts" / name)


def run_script(config: dict, name: str, args: list[str], log_path: str) -> dict:
    """Run one vendored skill script, capture output and return log metadata."""
    prepare_runtime(config)
    path = script_path(config, name)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"CV skill script missing: {path}")

    command = [sys.executable, path]
    command.extend(str(arg) for arg in args)
    env = os.environ.copy()
    env["JOB_SEARCH_HOME"] = config["job_search_home"]
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    completed = subprocess.run(
        command,
        cwd=config["job_search_home"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write(output)
    for line in output.splitlines():
        _LOGGER.info("cv: %s", line)

    result = {
        "command": " ".join(command),
        "returncode": int(completed.returncode),
        "log_file": log_path,
    }
    if completed.returncode != 0:
        raise RuntimeError(
            f"CV skill script '{name}' failed with exit code "
            f"{completed.returncode}; see {log_path}"
        )
    return result


def master_version(path: str) -> int:
    """Return the ``-vN`` version of a master file."""
    match = _MASTER_VERSION.search(path)
    return int(match.group(1)) if match else 0


def resolve_master_file(path: str) -> str:
    """Accept a master file or pick the highest ``*-vN.json`` in a folder."""
    if os.path.isfile(path):
        return str(Path(path).resolve())
    if not os.path.isdir(path):
        raise FileNotFoundError(
            f"Master resume not found: {path}. Set 'master_file' in cv_config.yaml."
        )
    masters = [
        str(Path(path) / name)
        for name in os.listdir(path)
        if _MASTER_VERSION.search(name)
    ]
    if not masters:
        raise FileNotFoundError(f"No *-vN.json master found in {path}")
    return str(Path(max(masters, key=master_version)).resolve())


def check_master(config: dict) -> dict:
    """Validate the configured master JSON before matching."""
    master = resolve_master_file(config["master_file"])
    log_path = str(Path(config["output_dir"]) / "check_master.log")
    result = run_script(config, "check_master.py", [master], log_path)
    result["master_file"] = master
    return result


def load_import_jds(config: dict) -> Any:
    """Import the skill's JD composer so text shape matches the skill."""
    scripts = str(Path(config["skill_dir"]) / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import import_jds

    return import_jds


def write_jd_files(config: dict, source_json: str, jd_dir: str) -> int:
    """Write one JD text file per NoSQL job for the matcher."""
    with open(source_json, encoding="utf-8") as handle:
        data = json.load(handle)
    jobs = data.get("jobs", []) if isinstance(data, dict) else data
    import_jds = load_import_jds(config)
    os.makedirs(jd_dir, exist_ok=True)

    count = 0
    for job in jobs:
        if not isinstance(job, dict):
            continue
        slug = import_jds.slug_of(job)
        text = str(job.get("jd_text") or import_jds.compose(job)).rstrip() + "\n"
        with open(os.path.join(jd_dir, slug + ".txt"), "w", encoding="utf-8") as handle:
            handle.write(text)
        count += 1

    _LOGGER.info("Wrote %d JD text file(s) to %s", count, jd_dir)
    return count


def match_jds(config: dict, source_json: str) -> dict:
    """Match every NoSQL JD against the master and write triage JSON."""
    run_dir = str(Path(config["run_root"]) / date.today().isoformat())
    jd_dir = str(Path(run_dir) / "jds")
    triage_file = str(Path(run_dir) / "triage.json")
    log_path = str(Path(run_dir) / "match.log")
    master = resolve_master_file(config["master_file"])
    jd_count = write_jd_files(config, source_json, jd_dir)

    args = [master, jd_dir, "--out", triage_file]
    if config.get("answers_file"):
        args.extend(["--answers", config["answers_file"]])
    result = run_script(config, "batch_triage.py", args, log_path)
    result.update(
        {
            "master_file": master,
            "run_dir": run_dir,
            "jd_dir": jd_dir,
            "triage_file": triage_file,
            "jd_count": jd_count,
            "source_json": source_json,
        }
    )
    return result

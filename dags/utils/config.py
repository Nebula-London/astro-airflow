"""Load the non-secret multi-portal config and resolve all output paths.

Roles come from the YAML file; portal URL templates are read from the Airflow
Variable by :mod:`dags.connections.portal_secrets`. Only portal *names* and
generated target keys are passed through the DAG/XCom -- never a URL.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from dags.portals.registry import implemented_portals, supported_portals
from dags.utils.constants import PORTAL_URLS_VARIABLE

_LOGGER = logging.getLogger(__name__)


def project_root() -> Path:
    """Return the repository root (two levels above this module)."""
    return Path(__file__).resolve().parents[2]


def load_yaml(path: str) -> dict:
    """Read a YAML file and return its mapping (or an empty dict)."""
    with open(path, encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    return loaded if isinstance(loaded, dict) else {}


def _roles(raw: dict) -> list[dict]:
    """Return the configured role entries, de-duplicated by name."""
    roles = []
    seen = set()
    for entry in raw.get("searches") or []:
        if isinstance(entry, dict):
            name = str(entry.get("name") or "").strip()
            portal = str(entry.get("portal") or "").strip().lower() or None
            excluded = {
                str(item).strip().lower()
                for item in (entry.get("exclude_portals") or [])
                if str(item).strip()
            }
        else:
            name, portal, excluded = str(entry or "").strip(), None, set()
        if not name or name in seen:
            continue
        seen.add(name)
        roles.append({"name": name, "portal": portal, "exclude_portals": excluded})
    if not roles:
        raise ValueError(
            "jobs_config.yaml has no searches. Add at least one role under 'searches'."
        )
    return roles


def _portal_settings(raw: dict) -> dict[str, dict]:
    """Return enabled portal settings keyed by lower-case portal name."""
    configured = raw.get("portals") or {}
    if isinstance(configured, list):
        configured = {str(item): {} for item in configured}
    settings = {}
    for name, value in configured.items():
        portal = str(name or "").strip().lower()
        if not portal:
            continue
        options = value if isinstance(value, dict) else {}
        if portal not in supported_portals():
            raise ValueError(
                f"Unknown portal '{portal}' in jobs_config.yaml. "
                f"Supported portals: {', '.join(supported_portals())}"
            )
        settings[portal] = {
            "enabled": bool(options.get("enabled", True)),
            "workers": int(options.get("workers", 0)) or None,
            "delay_seconds": options.get("delay_seconds"),
        }
    if not settings:
        # Backwards compatibility: a config with no portals section searches
        # every portal that has a URL template.
        settings = {
            portal: {"enabled": True, "workers": None, "delay_seconds": None}
            for portal in implemented_portals()
        }
    return settings


def build_targets(raw: dict, urls: dict[str, str]) -> tuple[list[dict], dict[str, dict]]:
    """Build non-secret mapped targets from roles x enabled portals with URLs."""
    roles = _roles(raw)
    settings = _portal_settings(raw)
    strict = bool(raw.get("strict_searches", False))
    available = {str(name).strip().lower() for name in urls}
    targets = []

    for portal, options in settings.items():
        if options["enabled"] and portal not in available:
            message = (
                f"Portal '{portal}' is enabled but has no URL template in Airflow "
                f"Variable '{PORTAL_URLS_VARIABLE}'."
            )
            if strict:
                raise ValueError(message)
            _LOGGER.warning("%s Skipping this portal.", message)
            options["enabled"] = False

    for role in roles:
        if role["portal"]:
            candidates = [role["portal"]]
        else:
            candidates = [portal for portal, options in settings.items() if options["enabled"]]
        for portal in candidates:
            if role["portal"] and portal not in available:
                raise ValueError(
                    f"Role '{role['name']}' targets portal '{portal}', but that "
                    f"portal has no URL template in '{PORTAL_URLS_VARIABLE}'."
                )
            if portal not in available or portal in role["exclude_portals"]:
                continue
            if not settings.get(portal, {}).get("enabled", True):
                if role["portal"]:
                    raise ValueError(
                        f"Role '{role['name']}' targets portal '{portal}', which "
                        "is disabled in jobs_config.yaml."
                    )
                continue
            key = f"{portal}::{role['name']}"
            targets.append(
                {
                    "key": key,
                    "portal": portal,
                    "role": role["name"],
                    "workers": settings[portal].get("workers"),
                    "delay_seconds": settings[portal].get("delay_seconds"),
                }
            )

    if not targets:
        raise ValueError(
            "No search targets were built. Enable a portal in jobs_config.yaml "
            f"and add its URL template to Airflow Variable '{PORTAL_URLS_VARIABLE}'."
        )
    return targets, settings


def build_config(raw: dict, urls: dict[str, str]) -> dict:
    """Validate config/URL overlap and resolve every path/default into a dict."""
    targets, settings = build_targets(raw, urls)
    roles = _roles(raw)

    output_dir = str(
        Path(str(raw.get("output_dir") or project_root() / "include" / "jobserve")).expanduser()
    )
    output_path = Path(output_dir)
    config = {
        "targets": targets,
        "searches": [{"name": role["name"]} for role in roles],
        "portals": settings,
        "strict_searches": bool(raw.get("strict_searches", False)),
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
        "Loaded job config: %d role(s), %d target(s), portals=%s, output_dir=%s",
        len(roles), len(targets),
        ", ".join(sorted({target["portal"] for target in targets})), output_dir,
    )
    return config

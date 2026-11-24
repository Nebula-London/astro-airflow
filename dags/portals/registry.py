"""Registry of portal adapters used by the job-fetch pipeline."""
from __future__ import annotations

import importlib

ADAPTERS = {
    "jobserve": "dags.portals.jobserve",
    "totaljobs": "dags.portals.totaljobs",
    "cw": "dags.portals.totaljobs",
    "indeed": "dags.portals.indeed",
    "linkedin": "dags.portals.linkedin",
}

# Portals with a working adapter. The others are registered but disabled in
# config until they have been verified against saved HTML/feed samples.
IMPLEMENTED = {"jobserve", "totaljobs", "cw"}


class UnsupportedPortal(RuntimeError):
    """Raised when a portal is registered but its adapter is not implemented."""


def unsupported(portal: str) -> None:
    """Fail with a clear message when an adapter has not been implemented."""
    raise UnsupportedPortal(
        f"Portal '{portal}' is registered but its adapter is not implemented yet. "
        "Leave it disabled in jobs_config.yaml or implement the adapter first."
    )


def supported_portals() -> list[str]:
    """Return the configured portal names."""
    return sorted(ADAPTERS)


def implemented_portals() -> list[str]:
    """Return the portals with a working adapter."""
    return sorted(IMPLEMENTED)


def get_adapter(portal: str):
    """Return the adapter module for a portal, raising a clear error if unknown."""
    name = str(portal or "").strip().lower()
    module_path = ADAPTERS.get(name)
    if not module_path:
        raise ValueError(
            f"Unknown portal '{portal}'. Supported portals: {', '.join(supported_portals())}"
        )
    return importlib.import_module(module_path)

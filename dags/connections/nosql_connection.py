"""OCI settings for the ``jobserve_seen`` Oracle NoSQL table.

Credentials are read, in order, from an Airflow Connection ``oci_nosql``, then
from environment variables, then from a standard OCI config file. An
instance/resource principal can be used instead of a key. Nothing secret is
stored in the repository, config YAML or XCom.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from airflow.sdk import Connection

_LOGGER = logging.getLogger(__name__)

DEFAULT_TABLE = "jobserve_seen"
CONNECTION_ID_ENV = "OCI_NOSQL_CONNECTION_ID"
DEFAULT_CONNECTION_ID = "oci_nosql"
CONFIG_FILE_ENV = "OCI_CONFIG_FILE"
PROFILE_ENV = "OCI_CONFIG_PROFILE"
COMPARTMENT_ENV = "OCI_NOSQL_COMPARTMENT_ID"
TABLE_ENV = "OCI_NOSQL_TABLE"
INSTANCE_PRINCIPAL_ENV = "OCI_USE_INSTANCE_PRINCIPAL"
RESOURCE_PRINCIPAL_ENV = "OCI_USE_RESOURCE_PRINCIPAL"
REQUIRED_KEYS = ("user", "fingerprint", "tenancy", "region")


def enabled(name: str) -> bool:
    """Interpret a boolean feature flag environment variable."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def private_key_content(value: str) -> str:
    """Accept a PEM with real newlines or with escaped ``\\n`` sequences."""
    return value.replace("\\n", "\n").strip()


def config_from_connection(conn: Any) -> dict:
    """Build an OCI config dict from an Airflow Connection."""
    extra = conn.extra_dejson or {}
    config = {
        "user": str(extra.get("user") or conn.login or ""),
        "fingerprint": str(extra.get("fingerprint") or ""),
        "tenancy": str(extra.get("tenancy") or extra.get("tenancy_id") or ""),
        "region": str(extra.get("region") or ""),
        "key_content": private_key_content(
            str(extra.get("key_content") or conn.password or "")
        ),
        "pass_phrase": str(extra.get("pass_phrase") or extra.get("passphrase") or ""),
    }
    return config


def config_from_env() -> dict:
    """Build an OCI config dict from environment variables."""
    return {
        "user": os.environ.get("OCI_USER", "").strip(),
        "fingerprint": os.environ.get("OCI_FINGERPRINT", "").strip(),
        "tenancy": os.environ.get("OCI_TENANCY", "").strip(),
        "region": os.environ.get("OCI_REGION", "").strip(),
        "key_content": private_key_content(os.environ.get("OCI_PRIVATE_KEY", "")),
        "pass_phrase": os.environ.get("OCI_PASS_PHRASE", "").strip(),
    }


def config_from_file() -> dict:
    """Fall back to the standard OCI config file, if one is available."""
    import oci

    path = os.environ.get(CONFIG_FILE_ENV) or oci.config.DEFAULT_LOCATION
    profile = os.environ.get(PROFILE_ENV) or oci.config.DEFAULT_PROFILE
    try:
        _LOGGER.info("Loading OCI config %s (profile %s)", path, profile)
        return oci.config.from_file(path, profile)
    except Exception as exc:  # noqa: BLE001 - report a single clear error later
        _LOGGER.info("OCI config file unavailable (%s)", exc)
        return {}


def is_complete(config: dict) -> bool:
    """Check that a signing config has every required value."""
    return all(config.get(key) for key in REQUIRED_KEYS) and bool(
        config.get("key_content") or config.get("key_file")
    )


def make_client(config: dict, signer: Any | None) -> Any:
    """Build a NoSQL client from a config dict and an optional signer."""
    import oci

    if signer is None:
        return oci.nosql.NosqlClient(config)
    return oci.nosql.NosqlClient(config, signer=signer)


def connection_settings() -> tuple[Any, str, str]:
    """Return ``(client, table_name, compartment_id)`` for the NoSQL table."""
    import oci

    conn_id = os.environ.get(CONNECTION_ID_ENV, "").strip() or DEFAULT_CONNECTION_ID
    env_table = os.environ.get(TABLE_ENV, "").strip()
    env_compartment = os.environ.get(COMPARTMENT_ENV, "").strip()
    conn_extra: dict = {}
    signer = None
    config: dict = {}

    if enabled(RESOURCE_PRINCIPAL_ENV):
        _LOGGER.info("Using OCI resource principal for NoSQL")
        signer = oci.auth.signers.ResourcePrincipalsFederationSigner()
    elif enabled(INSTANCE_PRINCIPAL_ENV):
        _LOGGER.info("Using OCI instance principal for NoSQL")
        signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    else:
        try:
            conn = Connection.get(conn_id)
            conn_extra = conn.extra_dejson or {}
            config = config_from_connection(conn)
            if not is_complete(config):
                _LOGGER.info("Airflow Connection '%s' is incomplete", conn_id)
        except Exception as exc:  # noqa: BLE001 - fall back to env/config file
            _LOGGER.info("Airflow Connection '%s' unavailable (%s)", conn_id, exc)
        if not is_complete(config):
            _LOGGER.info("Using OCI environment variables for NoSQL")
            config = config_from_env()
        if not is_complete(config):
            config = config_from_file()
        if not is_complete(config):
            raise ValueError(
                "No usable OCI credentials. Add an Airflow Connection "
                f"'{conn_id}' with user/fingerprint/tenancy/region/key_content "
                "extras, or set OCI_USER, OCI_FINGERPRINT, OCI_TENANCY, "
                "OCI_REGION and OCI_PRIVATE_KEY."
            )

    compartment = str(
        env_compartment or conn_extra.get("compartment_id") or config.get("tenancy") or ""
    )
    table = str(env_table or conn_extra.get("table_name") or DEFAULT_TABLE)
    if not compartment:
        raise ValueError(f"Set '{COMPARTMENT_ENV}' to the compartment that owns '{table}'")
    return make_client(config, signer), table, compartment

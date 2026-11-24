"""Canonical identity helpers shared by adapters, dedupe and storage.

A job has one canonical identity (``hash:<canonical_hash>``) and one or more
portal-specific source keys (``portal:code``). Keeping both lets us collapse
the same role advertised on several boards into a single tracker row while
still recognising the exact same posting if it reappears on one board.
"""
from __future__ import annotations

import hashlib

NOSQL_KEY_BYTES = 64
_HASH_PREFIX = "hash:"


def source_key(portal: str, code: str) -> str:
    """Return a collision-free key for one portal's job code."""
    return f"{str(portal or '').strip().lower()}:{str(code or '').strip()}"


def canonical_uid(job: dict) -> str:
    """Return the canonical uid for a normalized job."""
    if job.get("uid") and str(job["uid"]).startswith("hash:"):
        return job["uid"]
    return "hash:" + str(job.get("canonical_hash") or job.get("hash") or "")


def nosql_row_key(job: dict) -> str:
    """Return a deterministic row key within Oracle NoSQL's 64-byte limit.

    Canonical uids are ``hash:<64-char digest>`` (69 bytes). Dropping only the
    ``hash:`` prefix keeps the full digest and therefore the same uniqueness.
    Any other identity that is still too long is hashed so it also fits.
    """
    uid = canonical_uid(job)
    digest = uid[len(_HASH_PREFIX):] if uid.startswith(_HASH_PREFIX) else ""
    if digest and len(digest.encode("utf-8")) <= NOSQL_KEY_BYTES:
        return digest
    encoded = uid.encode("utf-8")
    if len(encoded) <= NOSQL_KEY_BYTES:
        return uid
    return hashlib.sha256(encoded).hexdigest()


def job_keys(job: dict) -> set[str]:
    """Every identity by which a job may already have been seen."""
    keys = set()
    uid = canonical_uid(job)
    if uid and not uid.endswith(":"):
        keys.add(uid)
    digest = str(job.get("canonical_hash") or job.get("hash") or "").strip()
    if digest:
        keys.add("hash:" + digest)
    for source in job.get("sources") or []:
        key = source.get("key") if isinstance(source, dict) else None
        if key:
            keys.add(key)
        elif isinstance(source, dict):
            keys.add(source_key(source.get("portal"), source.get("code")))
    code = str(job.get("source_code") or job.get("code") or "").strip()
    if code:
        keys.add("code:" + code)
        portal = str(job.get("portal") or "").strip().lower()
        if portal:
            keys.add(source_key(portal, code))
    return keys


def source_summary(job: dict) -> str:
    """Compact ``portal:code`` list for logs and the tracker CSV."""
    sources = job.get("sources") or []
    parts = [
        source_key(item.get("portal"), item.get("code"))
        for item in sources
        if isinstance(item, dict)
    ]
    return ",".join(dict.fromkeys(part for part in parts if part and not part.endswith(":")))

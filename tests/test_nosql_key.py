"""Regression tests for the Oracle NoSQL primary-key size limit."""
from __future__ import annotations

from dags.utils.identity import NOSQL_KEY_BYTES, canonical_uid, nosql_row_key


def test_canonical_uid_is_too_long_for_nosql():
    digest = "a" * 64
    job = {"hash": digest, "canonical_hash": digest}
    assert len(canonical_uid(job).encode("utf-8")) == 69


def test_nosql_row_key_keeps_full_digest_within_limit():
    digest = "a" * 64
    job = {"hash": digest, "canonical_hash": digest}
    key = nosql_row_key(job)
    assert key == digest
    assert len(key.encode("utf-8")) <= NOSQL_KEY_BYTES


def test_nosql_row_key_hashes_overlong_identity():
    digest = "b" * 80
    job = {"hash": digest, "canonical_hash": digest}
    key = nosql_row_key(job)
    assert len(key.encode("utf-8")) <= NOSQL_KEY_BYTES
    assert key != canonical_uid(job)


def test_nosql_row_key_preserves_short_identity():
    digest = "c" * 32
    job = {"hash": digest, "canonical_hash": digest}
    assert nosql_row_key(job) == digest

"""Tests for OCI 429 throttling retries."""
from __future__ import annotations

from unittest.mock import patch

from dags.utils.oci_retry import call_with_retry, is_rate_limit


class _Throttle(Exception):
    status = 429
    code = "TooManyRequests"


def test_is_rate_limit() -> None:
    assert is_rate_limit(_Throttle())
    assert not is_rate_limit(ValueError("not a throttle"))


def test_call_with_retry_succeeds_after_throttle() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def func() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise _Throttle()
        return "ok"

    with (
        patch("dags.utils.oci_retry.time.sleep", sleeps.append),
        patch("dags.utils.oci_retry.random.uniform", lambda low, high: 1.0),
    ):
        assert call_with_retry(func, retries=3, base_delay=0.5, action="test") == "ok"

    assert len(calls) == 3
    assert sleeps == [0.5, 1.0]


def test_call_with_retry_gives_up_after_throttle() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def func() -> None:
        calls.append(1)
        raise _Throttle()

    with (
        patch("dags.utils.oci_retry.time.sleep", sleeps.append),
        patch("dags.utils.oci_retry.random.uniform", lambda low, high: 1.0),
    ):
        try:
            call_with_retry(func, retries=2, base_delay=0.5)
        except _Throttle:
            pass
        else:
            raise AssertionError("expected the throttle to be re-raised")

    assert len(calls) == 2
    assert sleeps == [0.5]


def test_call_with_retry_does_not_retry_other_errors() -> None:
    calls: list[int] = []

    def func() -> None:
        calls.append(1)
        raise ValueError("bad request")

    with patch("dags.utils.oci_retry.time.sleep") as sleep:
        try:
            call_with_retry(func, retries=3, base_delay=0.1)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError")

    assert len(calls) == 1
    sleep.assert_not_called()

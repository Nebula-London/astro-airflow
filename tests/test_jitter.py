"""Tests for portal request staggering and jitter."""
from __future__ import annotations

from unittest.mock import patch

from dags.utils.jitter import jittered_backoff, random_delay, sleep_for, target_stagger


def test_target_stagger_spreads_mapped_targets() -> None:
    targets = [
        {"key": f"portal::{index}", "index": index, "target_count": 4}
        for index in range(4)
    ]
    assert [target_stagger(target, 20.0) for target in targets] == [0.0, 5.0, 10.0, 15.0]


def test_target_stagger_has_stable_hash_fallback() -> None:
    target = {"key": "jobserve::aws"}
    first = target_stagger(target, 10.0)
    assert first == target_stagger(target, 10.0)
    assert 0.0 <= first < 10.0
    assert target_stagger(target, 0.0) == 0.0


def test_random_delay_uses_configured_maximum() -> None:
    with patch("dags.utils.jitter.random.uniform", lambda low, high: 0.75):
        assert random_delay(3.0) == 0.75
    assert random_delay(0.0) == 0.0


def test_jittered_backoff_multiplies_exponential_delay() -> None:
    with patch("dags.utils.jitter.random.uniform", lambda low, high: 1.0):
        assert jittered_backoff(2, base=1.5) == 2.25


def test_sleep_for_skips_nonpositive_delays() -> None:
    with patch("dags.utils.jitter.time.sleep") as sleep:
        sleep_for(0.0)
        sleep.assert_not_called()
        sleep_for(1.5)
        sleep.assert_called_once_with(1.5)

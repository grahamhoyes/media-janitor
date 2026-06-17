from datetime import UTC, datetime, timedelta

import pytest

from scanner.pipeline.seeding import SeedingReqs, evaluate_seeding

REQS = SeedingReqs(min_days=14, min_ratio=2.0)
NOW = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "completed_on,ratio,expected_met",
    [
        # ratio arm met by ratio alone, time not yet elapsed
        (NOW - timedelta(days=1), 3.0, True),
        # time arm met by elapsed days alone, ratio below min
        (NOW - timedelta(days=20), 0.5, True),
        # both arms met
        (NOW - timedelta(days=20), 3.0, True),
        # neither met: ratio below, not enough days
        (NOW - timedelta(days=1), 0.5, False),
        # completed_on undefined: time arm cannot be met, ratio satisfies
        (None, 3.0, True),
        # completed_on undefined: time arm cannot be met, ratio below
        (None, 0.5, False),
        # ratio unreadable: ratio arm cannot be met, time satisfies
        (NOW - timedelta(days=20), None, True),
        # ratio unreadable: ratio arm cannot be met, time does not
        (NOW - timedelta(days=1), None, False),
        # both undefined: not met
        (None, None, False),
        # boundary: exactly min_days elapsed counts as met
        (NOW - timedelta(days=14), 0.5, True),
        # boundary: exactly min_ratio counts as met
        (NOW - timedelta(days=1), 2.0, True),
    ],
)
def test_evaluate_seeding_met(completed_on, ratio, expected_met):
    result = evaluate_seeding(completed_on, ratio, REQS, NOW)
    assert result.met is expected_met


@pytest.mark.parametrize(
    "completed_on",
    [
        NOW - timedelta(days=20),
        NOW - timedelta(days=1),
        NOW,
    ],
)
def test_started_and_end_when_completed_on_defined(completed_on):
    result = evaluate_seeding(completed_on, 1.0, REQS, NOW)
    assert result.started == completed_on
    assert result.end == completed_on + timedelta(days=REQS.min_days)


def test_started_and_end_none_when_completed_on_undefined():
    result = evaluate_seeding(None, 1.0, REQS, NOW)
    assert result.started is None
    assert result.end is None


def test_both_undefined_started_and_end_none():
    result = evaluate_seeding(None, None, REQS, NOW)
    assert result.met is False
    assert result.started is None
    assert result.end is None

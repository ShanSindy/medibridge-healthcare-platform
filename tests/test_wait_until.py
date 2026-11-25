"""wait_until in tests/pg_helpers.py (no database)."""

import time

from tests.pg_helpers import wait_until


def test_wait_until_returns_as_soon_as_the_condition_holds():
    calls = []

    def condition():
        calls.append(None)
        return len(calls) == 3

    assert wait_until(condition, seconds=5) is True
    assert len(calls) == 3


def test_wait_until_checks_once_more_after_the_bound():
    # True only from the bound onwards: the polls inside the bound see False, and the
    # final check after it sees True.
    started = time.monotonic()

    assert wait_until(lambda: time.monotonic() >= started + 0.2, seconds=0.2) is True
    assert time.monotonic() - started < 2


def test_wait_until_gives_up_after_the_bound():
    started = time.monotonic()

    assert wait_until(lambda: False, seconds=0.2) is False
    assert 0.2 <= time.monotonic() - started < 2

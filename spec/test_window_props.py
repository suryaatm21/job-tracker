"""WIN rules: reading listing timestamps for the time windows.

state_utils.parse_epoch feeds the private digest's window and the memory's reopen
check; send_digest_multi.parse_dt feeds the channel digests' window.
"""
import contextlib
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings, strategies as st

import send_digest_multi
import state_utils

# Inside the range parse_dt accepts for epoch numbers (1990-01-01 to 2040-01-01).
EPOCHS = st.integers(631_152_000, 2_208_988_800)
NAIVE_ISO = ["2026-11-10T12:00:00", "2026-01-01T00:00:00", "2027-06-30T23:59:59"]
NON_UTC_ZONE = "America/Los_Angeles"


@contextlib.contextmanager
def machine_timezone(name):
    """Run as if the machine's clock were set to `name` (CI runs in UTC, laptops often don't)."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def utc_epoch(naive_iso):
    return int(datetime.fromisoformat(naive_iso).replace(tzinfo=timezone.utc).timestamp())


@pytest.mark.parametrize("s", NAIVE_ISO)
def test_win_1_channel_digest_reads_naive_timestamps_as_utc(s):
    with machine_timezone(NON_UTC_ZONE):
        assert send_digest_multi.parse_dt(s).timestamp() == utc_epoch(s)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="WIN-1")
@pytest.mark.parametrize("s", NAIVE_ISO)
def test_win_1_memory_and_private_digest_read_naive_timestamps_as_utc(s):
    with machine_timezone(NON_UTC_ZONE):
        assert state_utils.parse_epoch(s) == utc_epoch(s)


@settings(max_examples=1000)
@given(n=EPOCHS, as_text=st.booleans())
def test_win_2_parsers_agree_on_epoch_numbers(n, as_text):
    v = str(n) if as_text else n
    assert state_utils.parse_epoch(v) == n
    assert send_digest_multi.parse_dt(v).timestamp() == n


@settings(max_examples=1000)
@given(n=EPOCHS, offset_quarters=st.integers(-48, 56), zulu=st.booleans())
def test_win_2_parsers_agree_on_timezone_aware_iso(n, offset_quarters, zulu):
    tz = timezone(timedelta(minutes=15 * offset_quarters))
    dt = datetime.fromtimestamp(n, tz=timezone.utc if zulu else tz)
    s = dt.strftime("%Y-%m-%dT%H:%M:%SZ") if zulu else dt.isoformat()
    assert state_utils.parse_epoch(s) == n
    assert int(send_digest_multi.parse_dt(s).timestamp()) == n

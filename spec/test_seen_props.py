"""SEEN rules: the "already posted" memory and its time-to-live (state_utils)."""
import pytest
from hypothesis import given, settings, strategies as st

import state_utils

DAY = 86400
REOPEN_GRACE = 1 * DAY  # the spec's value; never read the code's constant
URL = "https://x.com/job/1"

stamps = st.integers(1_600_000_000, 1_900_000_000)
item_st = st.fixed_dictionaries({"url": st.just(URL)},
                                optional={"date_updated": st.integers(0, 3_000_000_000)})


@given(last=stamps, ttl_days=st.integers(1, 30), age=st.integers(0, 60 * DAY))
def test_seen_1_seen_listing_is_never_new_and_ttl_boundary_is_inclusive(last, ttl_days, age):
    item = {"url": URL}
    seen = {state_utils.get_cache_key(item): last}
    ttl = ttl_days * DAY
    alert, reason = state_utils.should_alert_item(item, seen, ttl, last + age)
    assert reason != "new"
    assert state_utils.should_alert_item(item, seen, ttl, last + ttl) == (False, "suppressed")
    assert state_utils.should_alert_item(item, seen, ttl, last + ttl + 1) == (True, "ttl_expired")
    assert alert == (age > ttl)


@settings(max_examples=3000)
@given(item=item_st, last=stamps, ttl_days=st.integers(1, 30),
       d1=st.integers(0, 60 * DAY), d2=st.integers(0, 60 * DAY))
def test_seen_2_once_a_listing_alerts_it_alerts_at_every_later_time(item, last, ttl_days, d1, d2):
    seen = {state_utils.get_cache_key(item): last}
    t1, t2 = sorted((last + d1, last + d2))
    a1, _ = state_utils.should_alert_item(item, seen, ttl_days * DAY, t1)
    a2, _ = state_utils.should_alert_item(item, seen, ttl_days * DAY, t2)
    assert not (a1 and not a2)


@given(last=stamps, bump=st.integers(1, 3 * DAY))
def test_seen_3_reopen_needs_a_full_day_after_the_last_alert(last, bump):
    item = {"url": URL, "date_updated": last + bump}
    seen = {state_utils.get_cache_key(item): last}
    alert, reason = state_utils.should_alert_item(item, seen, 14 * DAY, last + 3 * DAY)
    assert alert == (bump >= REOPEN_GRACE), (bump, reason)
    assert reason == ("reopen" if bump >= REOPEN_GRACE else "suppressed")


KEYLESS_ITEMS = [{}, {"url": "  "}, {"title": "SWE Intern"}, {"company_name": "Acme"},
                 {"company_name": "", "title": "SWE Intern"}, {"id": "", "company_name": "Acme"}]


@given(item=st.sampled_from(KEYLESS_ITEMS), now=stamps)
def test_seen_4_listing_without_any_key_never_alerts(item, now):
    assert state_utils.get_cache_key(item) is None
    assert state_utils.should_alert_item(item, {}, 14 * DAY, now) == (False, "no_cache_key")


def test_seen_5_one_bad_value_does_not_wipe_the_memory(tmp_path):
    p = tmp_path / "seen.json"
    p.write_text('{"https://a.com/1": 1700000000, "https://b.com/2": "not-a-number"}')
    assert state_utils.load_seen(str(p)).get("https://a.com/1") == 1700000000


def test_seen_5_good_values_load_as_integers(tmp_path):
    p = tmp_path / "seen.json"
    p.write_text('{"https://a.com/1": 1700000000, "https://b.com/2": 1700000001.0, "c": "1700000002"}')
    assert state_utils.load_seen(str(p)) == {"https://a.com/1": 1700000000,
                                            "https://b.com/2": 1700000001, "c": 1700000002}

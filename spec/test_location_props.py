"""LOC rules: the location label on a DM or digest line (format_utils.format_location)."""
import pytest
from hypothesis import example, given, settings, strategies as st

import format_utils

CALIFORNIA = ["San Francisco, CA", "Los Angeles, CA", "Mountain View, California", "San Diego, CA"]
NEW_YORK = ["New York, NY", "NYC", "Brooklyn, NY", "New York City"]
NEW_JERSEY = ["Jersey City, NJ", "Princeton, New Jersey"]
# Places the label must not mistake for those states. Several contain "ca".
ELSEWHERE = ["Chicago, IL", "Seattle, WA", "Lancaster, PA", "Austin, TX", "Boston, MA",
             "Vancouver, Canada", "Cambridge, UK", "London, UK", "Remote"]


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="LOC-1")
@settings(max_examples=500)
@given(locs=st.lists(st.sampled_from(ELSEWHERE + NEW_YORK + NEW_JERSEY), min_size=2, max_size=4))
@example(locs=["Chicago, IL", "Seattle, WA"])
@example(locs=["Chicago, IL", "NYC"])
def test_loc_1_dm_says_california_only_for_california(locs):
    assert format_utils.format_location(locs, "dm") != "California", locs


@settings(max_examples=500)
@given(ca=st.sampled_from(CALIFORNIA), others=st.lists(st.sampled_from(ELSEWHERE), min_size=1, max_size=3),
       at=st.integers(0, 3))
def test_loc_1_dm_says_california_when_one_location_is_in_california(ca, others, at):
    locs = others[:at] + [ca] + others[at:]
    assert format_utils.format_location(locs, "dm") == "California", locs


@settings(max_examples=500)
@given(locs=st.lists(st.text(min_size=1, max_size=30).filter(str.strip), min_size=2, max_size=5))
def test_loc_2_digest_says_multi_location_for_several_places(locs):
    assert format_utils.format_location(locs, "digest") == "Multi-location"


@given(loc=st.text(min_size=1, max_size=30).filter(str.strip), mode=st.sampled_from(["dm", "digest"]))
def test_loc_2_single_location_is_shown_as_is(loc, mode):
    assert format_utils.format_location([loc], mode) == loc.strip()

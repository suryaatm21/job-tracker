"""LOC rules: the location label on a DM or digest line (format_utils.format_location)."""
from hypothesis import example, given, settings, strategies as st

import format_utils

CALIFORNIA = ["San Francisco, CA", "Los Angeles, CA", "Mountain View, California", "San Diego, CA"]
NEW_YORK = ["New York, NY", "NYC", "Brooklyn, NY", "New York City"]
NEW_JERSEY = ["Jersey City, NJ", "Princeton, New Jersey"]
# Places the label must not mistake for those states. Several contain "ca".
ELSEWHERE = ["Chicago, IL", "Seattle, WA", "Lancaster, PA", "Austin, TX", "Boston, MA",
             "Vancouver, Canada", "Cambridge, UK", "London, UK", "Remote"]
# Places with no "ca", "ny" or "nj" anywhere, so the known California and New Jersey
# substring bugs cannot decide the New York and New Jersey checks below.
PLAIN_ELSEWHERE = ["Seattle, WA", "Austin, TX", "Boston, MA", "London, UK", "Remote", "Denver, CO"]
# Look like New York but are not: "ny" inside a word.
NOT_NEW_YORK = ["Germany", "Albany, OR", "Kenya", "Brittany, France"]
# Look like New Jersey but are not: "nj" inside a word.
NOT_NEW_JERSEY = ["Nanjing, China", "Tianjin, China"]


def with_one_of(places, others, at):
    return st.tuples(st.sampled_from(places), others, at).map(lambda t: t[1][:t[2]] + [t[0]] + t[1][t[2]:])


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


@settings(max_examples=500)
@given(locs=with_one_of(NEW_YORK, st.lists(st.sampled_from(PLAIN_ELSEWHERE + NEW_JERSEY), min_size=1, max_size=3),
                        st.integers(0, 3)))
def test_loc_1_dm_says_new_york_when_one_location_is_in_new_york_and_none_in_california(locs):
    assert format_utils.format_location(locs, "dm") == "New York", locs


@settings(max_examples=500)
@given(locs=st.lists(st.sampled_from(PLAIN_ELSEWHERE + NEW_JERSEY + NOT_NEW_YORK), min_size=2, max_size=4))
def test_loc_1_dm_says_new_york_only_for_new_york(locs):
    assert format_utils.format_location(locs, "dm") != "New York", locs


@settings(max_examples=500)
@given(locs=with_one_of(NEW_JERSEY, st.lists(st.sampled_from(PLAIN_ELSEWHERE), min_size=1, max_size=3),
                        st.integers(0, 3)))
def test_loc_1_dm_says_new_jersey_when_one_location_is_in_new_jersey_and_none_in_ny_or_ca(locs):
    assert format_utils.format_location(locs, "dm") == "New Jersey", locs


@settings(max_examples=500)
@given(locs=st.lists(st.sampled_from(PLAIN_ELSEWHERE + NOT_NEW_YORK), min_size=2, max_size=4))
def test_loc_1_dm_says_new_jersey_only_for_new_jersey(locs):
    assert format_utils.format_location(locs, "dm") != "New Jersey", locs


@settings(max_examples=200)
@given(locs=with_one_of(NOT_NEW_JERSEY, st.lists(st.sampled_from(PLAIN_ELSEWHERE), min_size=1, max_size=3),
                        st.integers(0, 3)))
@example(locs=["Nanjing, China", "Austin, TX"])
def test_loc_1_dm_does_not_read_nj_inside_a_word_as_new_jersey(locs):
    assert format_utils.format_location(locs, "dm") != "New Jersey", locs

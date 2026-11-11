"""DEDUP rules: when two listings count as the same posting (dedup_utils, state_utils)."""
import pytest
from hypothesis import example, given, settings, strategies as st

import dedup_utils
import send_private_digest
import state_utils

SCHEMES = ["http", "https", "HTTPS"]
HOSTS = ["boards.greenhouse.io", "jobs.lever.co", "Example.COM"]
QUERIES = ["", "?gh_jid=1", "?gh_jid=2", "?utm_source=x"]


def build_url(scheme, host, path, query, fragment, slash):
    return f"{scheme}://{host}/{path}{'/' if slash else ''}{query}{fragment}"


url_parts = st.tuples(
    st.sampled_from(SCHEMES),
    st.sampled_from(HOSTS),
    st.from_regex(r"[A-Za-z0-9/_-]{0,20}", fullmatch=True),
    st.sampled_from(QUERIES),
    st.sampled_from(["", "#apply"]),
    st.booleans(),
)
urls = url_parts.map(lambda p: build_url(*p))


@settings(max_examples=1000)
@given(parts=url_parts)
def test_dedup_1_key_ignores_trailing_slash_fragment_and_host_case(parts):
    scheme, host, path, query, _fragment, _slash = parts
    path = path.rstrip("/")
    key = dedup_utils.normalize_url(build_url(scheme, host, path, query, "", False))
    assert dedup_utils.normalize_url(build_url(scheme, host, path, query, "#x", False)) == key
    assert dedup_utils.normalize_url(build_url(scheme.swapcase(), host.swapcase(), path, query, "", False)) == key
    if not query:
        assert dedup_utils.normalize_url(build_url(scheme, host, path, query, "", True)) == key


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEDUP-2")
@settings(max_examples=500)
@given(a=urls, b=urls)
@example(a="http://jobs.lever.co/0000", b="http://jobs.lever.co/0000?gh_jid=1")
def test_dedup_2_merges_only_postings_the_reference_key_also_merges(a, b):
    """Reference: the private digest's canonical_url_key keeps identifying query params.
    Inputs are lowercased for the reference so path case (undecided, see SPEC.md) plays no part."""
    if dedup_utils.normalize_url(a) == dedup_utils.normalize_url(b):
        ref = send_private_digest.canonical_url_key
        assert ref(a.lower()) == ref(b.lower()), (a, b)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="DEDUP-3")
@settings(max_examples=2000)
@given(st.one_of(urls, st.text(max_size=30)))
@example(" ")
@example("/")
@example("://")
def test_dedup_3_digest_and_memory_normalize_urls_identically(u):
    assert dedup_utils.normalize_url(u) == state_utils.normalize_url(u)


item_st = st.fixed_dictionaries({}, optional={
    "url": urls,
    "id": st.text(alphabet="abcdef0123456789", min_size=1, max_size=8),
    "company_name": st.sampled_from(["Acme", "Globex"]),
    "title": st.sampled_from(["SWE Intern", "ML Intern"]),
})


@settings(max_examples=500)
@given(item=item_st)
def test_dedup_4_key_order_is_url_then_id_then_company_title(item):
    key = dedup_utils.get_dedup_key(item)
    memory_key = state_utils.get_cache_key(item)
    if item.get("url"):
        assert key[0] == "url"
        assert memory_key == state_utils.normalize_url(item["url"])
    elif item.get("id"):
        assert key == ("id", item["id"])
        assert memory_key == f"id:{item['id']}"
    elif item.get("company_name") and item.get("title"):
        assert key == ("company_title", (item["company_name"].lower(), item["title"].lower()))
        assert memory_key.startswith("comp_title:")
    else:
        assert key is None and memory_key is None

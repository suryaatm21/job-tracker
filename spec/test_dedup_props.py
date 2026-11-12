"""DEDUP rules: when two listings count as the same posting (dedup_utils, state_utils)."""
import re

import pytest
from hypothesis import example, given, settings, strategies as st

import dedup_utils
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
@example(parts=("https", "jobs.lever.co", "jobs/1", "", "", False))
def test_dedup_1_key_ignores_repeated_and_trailing_slashes_fragment_and_host_case(parts):
    scheme, host, path, query, _fragment, _slash = parts
    path = path.rstrip("/")
    key = dedup_utils.normalize_url(build_url(scheme, host, path, query, "", False))
    doubled = "/" + path.replace("/", "//")
    assert dedup_utils.normalize_url(build_url(scheme, host, doubled, query, "", False)) == key
    assert dedup_utils.normalize_url(build_url(scheme, host, path, query, "#x", False)) == key
    assert dedup_utils.normalize_url(build_url(scheme.swapcase(), host.swapcase(), path, query, "", False)) == key
    if not query:
        assert dedup_utils.normalize_url(build_url(scheme, host, path, query, "", True)) == key


def reference_key(url):
    """Independent rule: lowercase scheme and host, path with each run of slashes counted as one
    and without trailing slashes, no fragment, query kept except utm_* tracking parameters."""
    url = url.lower().split("#")[0]
    base, _, query = url.partition("?")
    scheme, _, rest = base.partition("://")
    host, _, path = re.sub(r"/+", "/", rest).partition("/")
    params = sorted(q for q in query.split("&") if q and not q.startswith("utm_"))
    return scheme, host, path.rstrip("/"), params


@settings(max_examples=500)
@given(a=urls, b=urls)
@example(a="http://jobs.lever.co/0000", b="http://jobs.lever.co/0000?gh_jid=1")
@example(a="https://site.com//jobs/1", b="https://site.com/jobs/1")
def test_dedup_2_merges_only_postings_the_reference_key_also_merges(a, b):
    """Reference: reference_key above keeps identifying query params.
    Inputs are lowercased for the reference so path case (undecided, see SPEC.md) plays no part."""
    if dedup_utils.normalize_url(a) == dedup_utils.normalize_url(b):
        assert reference_key(a) == reference_key(b), (a, b)


def test_dedup_3_digest_and_memory_share_one_normalize_url():
    assert state_utils.normalize_url is dedup_utils.normalize_url


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

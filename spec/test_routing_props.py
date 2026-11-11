"""ROUTE rules: which roles may reach DMs (job_filtering)."""
from hypothesis import given, settings, strategies as st

import job_filtering as jf

# The spec owns these constants. Asserting against the code's own sets would move
# with the code and pass no matter what (a mutant that adds Hardware to the code's
# DM set survived exactly that way).
DM_CATEGORIES = {"Software Engineering", "Data Science, AI & Machine Learning"}
ROLE_CHANNEL_LABELS = ["Hardware", "Product", "Quant"]
OTHER_LABELS = ["Other", "Hardware Engineering", "Product Management", "Quantitative Finance",
                "Software", "SWE", "AI/ML/Data", "Data Science", "Software Engineering"]
SIMPLIFY_REPO = "SimplifyJobs/Summer2027-Internships"

category_st = st.one_of(
    st.none(),
    st.sampled_from(ROLE_CHANNEL_LABELS + OTHER_LABELS).flatmap(
        lambda s: st.sampled_from([s, s.lower(), s.upper()])),
    st.text(max_size=20),
)


@settings(max_examples=3000)
@given(category=category_st, title=st.text(max_size=60))
def test_route_1_dm_classifier_only_returns_dm_categories(category, title):
    out = jf.classify_job_category({"category": category, "title": title})
    assert out is None or out in DM_CATEGORIES, out


@settings(max_examples=500)
@given(label=st.sampled_from(ROLE_CHANNEL_LABELS),
       case=st.sampled_from([str, str.lower, str.upper]),
       title=st.sampled_from(["Hardware Engineering Intern", "Product Manager Intern",
                              "Quantitative Trader Intern", "FPGA Intern"]))
def test_route_2_role_labels_never_reach_dm(label, case, title):
    item = {"category": case(label), "title": title, "url": "https://x.com/1", "active": True}
    assert jf.should_process_repo_item(item, SIMPLIFY_REPO) == (False, "category")

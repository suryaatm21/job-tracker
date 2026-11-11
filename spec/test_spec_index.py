"""Keeps SPEC.md and the tests linked: every rule has a test, every test names a rule,
and known-bug markers match the rules that say "Known bug"."""
import pathlib
import re

SPEC_DIR = pathlib.Path(__file__).parent
RULE = re.compile(r"^- \*\*([A-Z]+-\d+)\*\* (.*)$", re.M)
TEST = re.compile(r"^def test_([a-z]+)_(\d+)_", re.M)
XFAIL_REASON = re.compile(r'xfail\(strict=True, raises=AssertionError, reason="([A-Z]+-\d+)"\)')


def spec_rules():
    return dict(RULE.findall((SPEC_DIR / "SPEC.md").read_text(encoding="utf-8")))


def props_sources():
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(SPEC_DIR.glob("test_*_props.py")))


def test_every_rule_has_a_test_and_every_test_names_a_rule():
    rules = spec_rules()
    named = {f"{area.upper()}-{n}" for area, n in TEST.findall(props_sources())}
    assert named - rules.keys() == set(), "tests name rules SPEC.md does not have"
    checked = {rid for rid, text in rules.items() if "Unchecked:" not in text}
    assert checked - named == set(), "rules without a test (mark them Unchecked: in SPEC.md)"
    assert named & {rid for rid in rules if rid not in checked} == set(), "a tested rule is still marked Unchecked"


def test_known_bug_markers_match_spec():
    rules = spec_rules()
    marked = set(XFAIL_REASON.findall(props_sources()))
    known = {rid for rid, text in rules.items() if "Known bug" in text}
    assert marked == known, {"marked, not known in SPEC.md": marked - known, "known, not marked": known - marked}


def test_every_xfail_marker_is_strict_and_names_a_rule():
    src = props_sources()
    assert src.count("pytest.mark.xfail(") == len(XFAIL_REASON.findall(src))

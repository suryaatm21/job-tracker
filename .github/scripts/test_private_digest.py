#!/usr/bin/env python3
"""
Offline tests for the private opportunity digest. No network, no Telegram.

Run: python -m unittest discover -s .github/scripts -p 'test_private_digest.py'
"""
import json
import os
import pathlib
import re
import shutil
import tempfile
import time
import unittest
from unittest import mock

import requests

import company_signals
import send_private_digest as digest
from company_signals import CompanyProfiles, parse_simplify_company_page, score_listing, guess_simplify_slug
from markdown_listings import parse_markdown_tables, approximate_posted_epoch
from opportunity_filters import (LocationMatcher, CompanyTiers, tag_level, normalize_company, parse_pay,
                                 passes_pay_gate, is_graduate_only, LEVEL_INTERN, LEVEL_NEW_GRAD,
                                 LEVEL_UNKNOWN, LOCATION_MATCH, LOCATION_REMOTE, LOCATION_UNKNOWN,
                                 LOCATION_ELSEWHERE, PAY_OK, PAY_NONE, PAY_UNPARSED)

HERE = pathlib.Path(__file__).resolve().parent
CONFIG_DIR = HERE.parent / "private_channel"
CONFIG = json.loads((CONFIG_DIR / "config.json").read_text())
NOW = 1_794_200_000  # 2026-11-09


def not_found():
    response = requests.Response()
    response.status_code = 404
    return requests.HTTPError("404 Not Found", response=response)


class MarkdownTablesTest(unittest.TestCase):
    SPEEDY = """
### FAANG+

| Company | Position | Location | Salary | Posting | Age |
|---|---|---|---|---|---|
| <a href="https://www.figma.com"><strong>Figma</strong></a> | PhD Intern - Software Engineer | San Francisco, CA +1 | $60/hr | <a href="https://boards.greenhouse.io/figma/jobs/1"><img src="x" alt="Apply"/></a> | 4d |
| <a href="https://www.mtb.com"><strong>M&amp;T Bank</strong></a> | Data &amp; API Engineer | Buffalo, NY | | <a href="https://mtb.example/2"><img src="x"/></a> | 1mo |
"""
    ZAPPLY = """
<details>
<summary><h3>💻 <strong>Software Engineering</strong></h3></summary>

| Company | Role | Location | Posted | Visa | **Apply** |
|---------|------|----------|--------|------|----------|
| **Nominal** | Product Manager | New York, United States | 7h |  | [<img src="images/apply.png">](https://zapply.jobs/l/d/a?s=gh) |
| ↳ | Software Engineer \\[Summer 2027\\] | NYC | 2026-11-01 | ✅ | [<img src="images/apply.png">](https://zapply.jobs/l/d/b?s=gh) |
| **NoLink** | Engineer | NYC | 1d | | |
</details>
"""

    def test_speedyapply_rows(self):
        rows = parse_markdown_tables(self.SPEEDY, now_epoch=NOW)
        self.assertEqual(len(rows), 2)
        figma = rows[0]
        self.assertEqual(figma["company_name"], "Figma")
        self.assertEqual(figma["url"], "https://boards.greenhouse.io/figma/jobs/1")
        self.assertEqual(figma["locations"], ["San Francisco, CA"])
        self.assertTrue(figma["has_hidden_locations"])
        self.assertEqual(figma["salary"], "$60/hr")
        self.assertEqual(figma["category"], "FAANG+")
        self.assertEqual(figma["date_posted"], NOW - 4 * 86400)
        self.assertEqual(rows[1]["company_name"], "M&T Bank")  # entities unescaped
        self.assertEqual(rows[1]["title"], "Data & API Engineer")

    def test_zapply_rows_continuation_and_skips(self):
        rows = parse_markdown_tables(self.ZAPPLY, now_epoch=NOW)
        self.assertEqual([r["company_name"] for r in rows], ["Nominal", "Nominal"])  # ↳ repeats; no-link row skipped
        self.assertEqual(rows[0]["category"], "Software Engineering")
        self.assertEqual(rows[0]["url"], "https://zapply.jobs/l/d/a?s=gh")
        self.assertEqual(rows[0]["date_posted"], NOW - 7 * 3600)
        self.assertEqual(rows[1]["date_posted"], 1793491200)  # 2026-11-01 UTC
        self.assertEqual(rows[1]["title"], "Software Engineer [Summer 2027]")  # markdown escapes removed

    def test_unknown_age(self):
        self.assertIsNone(approximate_posted_epoch("Date unknown", NOW))


class LevelTest(unittest.TestCase):
    def test_title_overrides_source_default(self):
        self.assertEqual(tag_level({"title": "Engineer 2 New Grad"}, LEVEL_INTERN), LEVEL_NEW_GRAD)
        self.assertEqual(tag_level({"title": "Software Intern - Summer 2027"}, LEVEL_NEW_GRAD), LEVEL_INTERN)
        self.assertEqual(tag_level({"title": "Internal Tools Engineer"}, LEVEL_NEW_GRAD), LEVEL_NEW_GRAD)

    def test_both_or_neither_keeps_default(self):
        self.assertEqual(tag_level({"title": "Software Engineer"}, LEVEL_INTERN), LEVEL_INTERN)
        self.assertEqual(tag_level({"title": "New Grad / Intern SWE"}, LEVEL_UNKNOWN), LEVEL_UNKNOWN)

    def test_explicit_level_field_wins(self):
        self.assertEqual(tag_level({"title": "Software Engineer", "level": "new grad"}, LEVEL_UNKNOWN), LEVEL_NEW_GRAD)
        self.assertEqual(tag_level({"title": "Engineer", "level": "Intern"}, LEVEL_UNKNOWN), LEVEL_INTERN)
        self.assertEqual(tag_level({"title": "Engineer", "level": "unsure"}, LEVEL_UNKNOWN), LEVEL_UNKNOWN)

    def test_graduate_only(self):
        self.assertTrue(is_graduate_only({"title": "Research Intern - PhD"}))
        for degrees in (["PhD"], ["Master's", "PhD"], ["MBA"]):
            self.assertTrue(is_graduate_only({"title": "SWE", "degrees": degrees}), degrees)
        for degrees in (["Bachelor's"], ["Bachelor's", "Master's"], ["Associate's"], ["Certificate"],
                        ["Bootcamp"], ["Incomplete"], ["Master's", "PhD", "Certificate"], []):
            self.assertFalse(is_graduate_only({"title": "SWE", "degrees": degrees}), degrees)


class LocationTest(unittest.TestCase):
    matcher = LocationMatcher(CONFIG["location"])

    def label(self, location):
        return self.matcher.match_one(location)

    def test_metro_matches(self):
        cases = {
            "SF": "SF Bay Area", "South SF": "SF Bay Area", "San Jose, CA": "SF Bay Area",
            "San Jose California US": "SF Bay Area", "USA - CA - San Jose": "SF Bay Area",
            "San Jose": "SF Bay Area", "Santa Clara,CA": "SF Bay Area", "San Francisco, CA, US": "SF Bay Area",
            "NYC": "NYC", "New York, NY": "NYC", "New York, United States": "NYC", "US, NY, New York": "NYC",
            "New York, New York, United...": "NYC", "Brooklyn, NY": "NYC", "Long Island City, Queens, NY": "NYC",
            "LA": "Los Angeles", "Los Angeles, CA": "Los Angeles", "El Segundo, CA": "Los Angeles",
            "Playa Vista, LA": "Los Angeles", "Irvine, CA": "Los Angeles", "Manhattan Beach, CA": "Los Angeles",
            "Austin, TX": "Austin", "AUSTIN, TX": "Austin", "Austin, Texas, USA, 78744": "Austin", "Round Rock, TX": "Austin",
            "Miami, FL": "Miami", "Fort Lauderdale, FL": "Miami", "Hollywood, FL": "Miami",
        }
        for location, expected in cases.items():
            with self.subTest(location=location):
                self.assertEqual(self.label(location), expected)

    def test_lookalikes_do_not_match(self):
        for location in ["USA - New York - Malta", "Rochester, NY", "Holtsville, New York",
                         "Brooklyn, OH", "Brooklyn Park, MN", "Dublin, Ireland", "Newark, NJ", "Berkeley Heights, NJ",
                         "San Jose, Costa Rica", "Baton Rouge, LA", "La Jolla, San Diego, CA", "Bossier City, LA",
                         "Miamisburg, OH", "Richmond, VA", "Glendale, CO", "Austin RiverSouth", "Seattle, WA"]:
            with self.subTest(location=location):
                self.assertIsNone(self.label(location))

    def test_classify_outcomes(self):
        c = self.matcher.classify
        self.assertEqual(c({"locations": ["Seattle, WA", "New York, NY"]}), (LOCATION_MATCH, ["NYC"]))
        self.assertEqual(c({"locations": ["Remote in USA"]})[0], LOCATION_REMOTE)
        self.assertEqual(c({"locations": ["Remote - San Francisco, CA"]})[0], LOCATION_REMOTE)
        self.assertEqual(c({"locations": ["2 Locations"]})[0], LOCATION_UNKNOWN)
        self.assertEqual(c({"locations": ["United States"]})[0], LOCATION_UNKNOWN)
        self.assertEqual(c({"locations": []})[0], LOCATION_UNKNOWN)
        self.assertEqual(c({"locations": ["Seattle, WA"], "has_hidden_locations": True})[0], LOCATION_UNKNOWN)
        self.assertEqual(c({"locations": ["Seattle, WA"]})[0], LOCATION_ELSEWHERE)

    def test_one_cell_listing_several_metros_keeps_them_all(self):
        c = self.matcher.classify
        # "San Francisco, CA • New York, NY" is a live speedyapply/zapply cell
        for cell in ["San Francisco, CA / New York, NY", "San Francisco, CA • New York, NY", "SF/NYC"]:
            with self.subTest(cell=cell):
                self.assertEqual(c({"locations": [cell]}), (LOCATION_MATCH, ["SF Bay Area", "NYC"]))
        self.assertEqual(c({"locations": ["New York, NY; Remote"]}), (LOCATION_MATCH, ["NYC"]))
        self.assertEqual(c({"locations": ["Seattle, WA or Austin, TX"]}), (LOCATION_MATCH, ["Austin"]))
        self.assertEqual(c({"locations": ["Remote - San Francisco, CA"]})[0], LOCATION_REMOTE)  # remote job, excluded

    def test_remote_switch(self):
        cfg = dict(CONFIG["location"], include_remote=True)
        self.assertEqual(LocationMatcher(cfg).classify({"locations": ["Remote in USA"]}), (LOCATION_MATCH, ["Remote"]))


class PayTest(unittest.TestCase):
    def test_formats(self):
        cases = {
            "$120,000 - $180,000": ("yr", 180000), "$150K-$200K": ("yr", 200000), "$186k/yr": ("yr", 186000),
            "$55/hr": ("hr", 55), "$50 - $60 per hour": ("hr", 60), "120000-150000 USD yearly": ("yr", 150000),
            "$8,000 per month": ("mo", 8000), "$60/hr": ("hr", 60),
            # One dollar sign covering both ends of the range
            "$45-55/hr": ("hr", 55), "$120,000-160,000 per year": ("yr", 160000), "$120-160k": ("yr", 160000),
        }
        for text, (unit, top) in cases.items():
            with self.subTest(text=text):
                pay = parse_pay(text)
                self.assertEqual(pay["status"], PAY_OK)
                self.assertEqual((pay["unit"], pay["top"]), (unit, top))
        self.assertAlmostEqual(parse_pay("$55/hr")["yearly"], 55 * 2080)
        self.assertAlmostEqual(parse_pay("$8,000 per month")["hourly"], 8000 * 12 / 2080)

    def test_ambiguous_and_missing(self):
        self.assertEqual(parse_pay("")["status"], PAY_NONE)
        for text in ["£40,000", "CA$90,000", "competitive", "$1,500", "$50/hr or $120k/yr"]:
            with self.subTest(text=text):
                self.assertEqual(parse_pay(text)["status"], PAY_UNPARSED)

    def test_gate(self):
        gate = CONFIG["pay_gate"]
        self.assertTrue(passes_pay_gate(parse_pay("$120,000 - $140,000"), LEVEL_NEW_GRAD, gate))
        self.assertFalse(passes_pay_gate(parse_pay("$110K-$139K"), LEVEL_NEW_GRAD, gate))
        self.assertTrue(passes_pay_gate(parse_pay("$50/hr"), LEVEL_INTERN, gate))
        self.assertTrue(passes_pay_gate(parse_pay("$45-55/hr"), LEVEL_INTERN, gate))  # top 55 clears 50
        self.assertTrue(passes_pay_gate(parse_pay("$120,000-160,000 per year"), LEVEL_NEW_GRAD, gate))
        self.assertFalse(passes_pay_gate(parse_pay("$100-130k"), LEVEL_NEW_GRAD, gate))
        self.assertFalse(passes_pay_gate(parse_pay("$45/hr"), LEVEL_INTERN, gate))
        self.assertTrue(passes_pay_gate(parse_pay(""), LEVEL_NEW_GRAD, gate))
        self.assertTrue(passes_pay_gate(parse_pay("competitive"), LEVEL_INTERN, gate))
        self.assertTrue(passes_pay_gate(parse_pay("$55/hr"), LEVEL_UNKNOWN, gate))  # clears the intern floor
        self.assertFalse(passes_pay_gate(parse_pay("$40/hr"), LEVEL_UNKNOWN, gate))


class CompanyTiersTest(unittest.TestCase):
    tiers = CompanyTiers(json.loads((CONFIG_DIR / "company_tiers.json").read_text()))

    def test_exact_alias_matching(self):
        self.assertEqual(self.tiers.match("Meta")[0]["key"], "big_tech")
        self.assertEqual(self.tiers.match("Facebook")[0]["key"], "big_tech")
        self.assertEqual(self.tiers.match("Snap Inc.")[0]["key"], "comp")
        self.assertEqual(self.tiers.match("ByteDance")[0]["key"], "big_tech")
        for name in ["Metaview", "Applied Materials", "Apple Leisure Group", "Googleplex Foods", "Notional"]:
            with self.subTest(name=name):
                self.assertIsNone(self.tiers.match(name))

    def test_every_entry_has_reason_and_source(self):
        for tier in self.tiers.tiers:
            for company in tier["companies"]:
                with self.subTest(company=company["name"]):
                    self.assertTrue(company.get("reason"))
                    self.assertTrue(company.get("source"))

    def test_normalize(self):
        self.assertEqual(normalize_company("The New York Times"), "new york times")
        self.assertEqual(normalize_company("M&T Bank"), "m and t bank")
        self.assertEqual(guess_simplify_slug("M&T Bank"), "M-And-T-Bank")


def simplify_page(company):
    data = {"props": {"pageProps": {"company": company}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


STARTUP = {
    "name": "Acme Robotics", "stock_symbol": None, "funding_stage": "SERIES_B", "funding_total": 120_000_000,
    "fundings": [
        {"round": "SERIES_B", "amount": 80_000_000, "date": "2026-09-01T00:00:00",
         "investors": [{"name": "Founders Fund", "is_top_investor": True}]},
        {"round": "DEBT", "amount": 500_000_000, "date": "2026-10-01T00:00:00", "investors": []},
        {"round": "SEED", "amount": 5_000_000, "date": "2021-01-01T00:00:00",
         "investors": [{"name": "Old Angel", "is_top_investor": True}]},
    ],
}


class ScoringTest(unittest.TestCase):
    scoring = CONFIG["scoring"]
    tiers = CompanyTiers(json.loads((CONFIG_DIR / "company_tiers.json").read_text()))

    def test_unlisted_startup_earns_points_from_funding(self):
        profile = parse_simplify_company_page(simplify_page(STARTUP))
        score, reasons = score_listing({"title": "SWE"}, LEVEL_NEW_GRAD, None, profile, self.scoring, NOW)
        # recent round (+2) + round >= $50M (+1) + top investor in 36 months (+1); debt ignored
        self.assertEqual(score, 4)
        self.assertIn("raised Series B $80M, Sep 2026", reasons)
        self.assertIn("backed by Founders Fund", reasons)

    def test_public_company_gets_no_funding_points(self):
        profile = parse_simplify_company_page(simplify_page(dict(STARTUP, stock_symbol="ACME")))
        self.assertEqual(score_listing({"title": "SWE"}, LEVEL_NEW_GRAD, None, profile, self.scoring, NOW), (0, []))

    def test_tier_pay_and_penalties(self):
        tier = self.tiers.match("Google")
        score, reasons = score_listing({"title": "SWE", "salary": "$196k/yr"}, LEVEL_NEW_GRAD, tier, None, self.scoring, NOW)
        self.assertEqual((score, reasons), (7, ["tier: big tech", "posted pay $196k/yr"]))
        score, reasons = score_listing({"title": "Research Intern - PhD"}, LEVEL_INTERN, tier, None, self.scoring, NOW)
        self.assertLess(score, self.scoring["strong_threshold"])
        self.assertIn("PhD/MS only", reasons)
        score, reasons = score_listing({"title": "Account Executive", "category": "Business & Operations"},
                                       LEVEL_NEW_GRAD, None, None, self.scoring, NOW)
        self.assertEqual(score, -3)
        _, reasons = score_listing({"title": "SWE", "salary": "competitive"}, LEVEL_NEW_GRAD, None, None, self.scoring, NOW)
        self.assertEqual(reasons, ["pay unparsed: competitive"])


class CompanyProfilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.pages = []

    def fetch(self, url):
        self.pages.append(url)
        return None if url.endswith("/Missing-Co") else simplify_page(dict(STARTUP, name=url.rsplit("/", 1)[-1]))

    def make(self, **kw):
        return CompanyProfiles(pathlib.Path(self.tmp) / "profiles.json", fetch_page=self.fetch,
                               delay_seconds=0, now_epoch=NOW, **kw)

    def test_cache_slug_index_and_misses(self):
        profiles = self.make()
        profiles.register_listing({"company_name": "Acme", "company_url": "https://simplify.jobs/c/Acme-Robotics"})
        profile, status = profiles.lookup({"company_name": "Acme"})
        self.assertEqual((profile["name"], status), ("Acme-Robotics", profiles.PROFILE_FOUND))
        self.assertEqual(profiles.lookup({"company_name": "Missing Co"}), (None, profiles.PROFILE_NONE))
        profiles.save()

        again = self.make()
        again.lookup({"company_name": "Acme"})
        again.lookup({"company_name": "Missing Co"})
        self.assertEqual(len(self.pages), 2)  # both answered from cache, including the miss
        self.assertEqual(again.cache_hits, 2)

    def test_budget_defers_unknown_companies(self):
        profiles = self.make(max_lookups=1)
        profiles.lookup({"company_name": "First"})
        self.assertEqual(profiles.lookup({"company_name": "Second"}), (None, profiles.PROFILE_DEFERRED))


class RenderTest(unittest.TestCase):
    def test_html_is_escaped_and_overflow_counted(self):
        item = {"company_name": "M&T <Bank>", "title": "Data & API <Eng>", "url": "https://x.test/a?b=1&c=2"}
        scored = [(5, ["tier: big tech"], ["NYC"], item), (0, [], ["NYC"], dict(item)), (0, [], ["NYC"], dict(item))]
        header, lines, shown = digest.render_level(LEVEL_NEW_GRAD, scored, 48, 3, max_strong=40, max_maybe=1)
        text = header + "\n".join(lines)
        self.assertIn("M&amp;T &lt;Bank&gt;", text)
        self.assertIn('href="https://x.test/a?b=1&amp;c=2"', text)
        leftover = re.sub(r"</?(b|i)>|<a href=\"[^\"]*\">|</a>", "", text)
        self.assertNotIn("<", leftover)  # only Telegram-safe tags remain
        self.assertIn("1 lower-scored listings held", text)
        self.assertEqual(len(shown), 2)  # the held-back listing is not marked as sent


class CircuitBreakerTest(unittest.TestCase):
    def test_lookups_stop_after_consecutive_errors(self):
        calls = []

        def blocked(url):
            calls.append(url)
            raise requests.ConnectionError("blocked")

        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        profiles = CompanyProfiles(pathlib.Path(tmp) / "p.json", fetch_page=blocked, delay_seconds=0, now_epoch=NOW)
        with mock.patch("builtins.print"):
            statuses = [profiles.lookup({"company_name": n})[1] for n in "ABCDE"]
        self.assertEqual(statuses, [profiles.PROFILE_NONE] * 5)  # scored without profiles, never deferred
        self.assertEqual(len(calls), CompanyProfiles.MAX_CONSECUTIVE_ERRORS)
        self.assertTrue(profiles.disabled)


class DedupTest(unittest.TestCase):
    TTL = 14 * 86400

    def job(self, company, title, url, metros, source="s", level=LEVEL_NEW_GRAD):
        return {"company_name": company, "title": title, "url": url, "_source": source,
                "_level": level, "_metros": metros}

    def run_dedup(self, items, seen=None):
        funnel = digest.Funnel(["s", "t"])
        kept = digest.drop_seen_and_duplicates(items, dict(seen or {}), self.TTL, NOW, funnel)
        return kept, funnel

    def test_cross_source_copy_in_one_metro_posts_once(self):
        kept, funnel = self.run_dedup([
            self.job("Acme", "SWE New Grad", "https://simplify.test/1", ["SF Bay Area"], "s"),
            self.job("ACME", "SWE, New Grad", "https://speedy.test/2", ["SF Bay Area"], "t")])
        self.assertEqual([i["url"] for i in kept], ["https://simplify.test/1"])
        self.assertEqual(funnel.drops["dedup:company_title_metro"][LEVEL_NEW_GRAD], 1)

    def test_same_title_in_two_metros_posts_both(self):
        kept, _ = self.run_dedup([
            self.job("Google", "Software Engineer, New Grad", "https://g.test/1", ["SF Bay Area"]),
            self.job("Google", "Software Engineer, New Grad", "https://g.test/2", ["NYC"])])
        self.assertEqual(len(kept), 2)

    def test_bridge_posts_unsent_metro_once_in_either_order(self):
        title = "Software Engineer, New Grad"
        sent_sf = self.job("Google", title, "https://g.test/sf", ["SF Bay Area"])
        seen = {key: NOW - 3600 for key in sum(digest.listing_keys(sent_sf), [])}
        for nyc_first in (True, False):
            nyc = self.job("Google", title, "https://g.test/nyc", ["NYC"])
            bridge = self.job("Google", title, "https://g.test/bridge", ["SF Bay Area", "NYC"])
            order = [nyc, bridge] if nyc_first else [bridge, nyc]
            kept, funnel = self.run_dedup([dict(sent_sf), *order], seen)
            self.assertEqual(len(kept), 1, nyc_first)
            self.assertIn("NYC", kept[0]["_metros"])
            self.assertEqual(funnel.drops["ttl:already_sent"][LEVEL_NEW_GRAD], 1)

    def test_same_url_drops_second_and_similar_names_stay_separate(self):
        kept, funnel = self.run_dedup([
            self.job("Amazon", "SDE New Grad", "https://a.test/1", ["NYC"]),
            self.job("Other", "Different", "https://a.test/1", ["Austin"]),
            self.job("Amazon Web Services", "SDE New Grad", "https://a.test/2", ["NYC"])])
        self.assertEqual([i["company_name"] for i in kept], ["Amazon", "Amazon Web Services"])
        self.assertEqual(funnel.drops["dedup:url"][LEVEL_NEW_GRAD], 1)

    def test_postings_identified_by_query_stay_distinct(self):
        # Live case: 88 Waymo listings link to careers.withwaymo.com/jobs?gh_jid=<posting id>
        kept, _ = self.run_dedup([
            self.job("Waymo", "Software Engineer", "https://careers.withwaymo.com/jobs?gh_jid=8350687", ["SF Bay Area"]),
            self.job("Waymo", "Hardware Engineer", "https://careers.withwaymo.com/jobs?gh_jid=8350832", ["SF Bay Area"])])
        self.assertEqual(len(kept), 2)
        self.assertNotEqual(digest.canonical_url_key("https://careers.withwaymo.com/jobs?gh_jid=8350687"),
                            digest.canonical_url_key("https://careers.withwaymo.com/jobs?gh_jid=8350832"))

    def test_only_tracking_parameters_are_ignored(self):
        same = ["https://jobs.test/a/1", "https://JOBS.test/a/1/?utm_source=x&utm_medium=y",
                "https://jobs.test/a/1?ref=board&source=gh#apply", "https://jobs.test/a/1?gh_src=abc&src=feed"]
        self.assertEqual(len({digest.canonical_url_key(u) for u in same}), 1)
        self.assertEqual(digest.canonical_url_key("https://b.test/x?b=2&a=1"),
                         digest.canonical_url_key("https://b.test/x?a=1&b=2&utm_campaign=z"))
        kept, funnel = self.run_dedup([
            self.job("Acme", "SWE", same[0], ["NYC"]), self.job("Other", "Other", same[2], ["Miami"])])
        self.assertEqual(len(kept), 1)
        self.assertEqual(funnel.drops["dedup:url"][LEVEL_NEW_GRAD], 1)

    def test_intern_and_new_grad_with_same_title_stay_separate(self):
        kept, _ = self.run_dedup([
            self.job("Acme", "Software Engineer", "https://a.test/ng", ["NYC"], level=LEVEL_NEW_GRAD),
            self.job("Acme", "Software Engineer", "https://a.test/in", ["NYC"], level=LEVEL_INTERN)])
        self.assertEqual([i["_level"] for i in kept], [LEVEL_NEW_GRAD, LEVEL_INTERN])

    def test_seen_keys_suppress_only_within_ttl(self):
        item = self.job("Acme", "SWE", "https://a.test/1", ["NYC"])
        keys = sum(digest.listing_keys(item), [])
        self.assertEqual(self.run_dedup([dict(item)], {k: NOW - self.TTL + 60 for k in keys})[0], [])
        self.assertEqual(len(self.run_dedup([dict(item)], {k: NOW - self.TTL - 60 for k in keys})[0]), 1)


class FakeGitHub:
    """Stands in for the GitHub API: default branch "main" and a fixed set of existing files."""

    def __init__(self, files):
        self.files = files  # {(repo, path): parsed JSON}
        self.probes = []

    def default_branch(self, repo):
        if not any(r == repo for r, _ in self.files):
            raise not_found()
        return "main"

    def gh_get(self, url, **params):
        repo, path = url.split("/repos/", 1)[1].split("/contents/", 1)
        self.probes.append((repo, path))
        if (repo, path) not in self.files:
            raise not_found()
        return {"name": path.rsplit("/", 1)[-1]}

    def fetch_json(self, repo, path, ref=None):
        return self.files[(repo, path)]

    def patches(self):
        import repo_utils
        return [
            mock.patch.dict(repo_utils._LISTINGS_SOURCE_CACHE, clear=True),
            mock.patch.object(repo_utils, "get_default_branch", side_effect=self.default_branch),
            mock.patch.object(repo_utils, "gh_get", side_effect=self.gh_get),
            mock.patch.object(repo_utils, "fetch_file_json", side_effect=self.fetch_json),
            mock.patch.object(digest, "get_default_branch", side_effect=self.default_branch),
            mock.patch.object(digest, "gh_get", side_effect=self.gh_get),
            mock.patch.object(digest, "fetch_file_json", side_effect=self.fetch_json),
        ]


class ListingsPathTest(unittest.TestCase):
    """Every listings.json source resolves through the shared repo_utils.fetch_listings()."""

    def read(self, source, files):
        fake = FakeGitHub(files)
        with mock.patch("builtins.print"):
            for p in fake.patches():
                p.start()
                self.addCleanup(p.stop)
            return digest.read_listings_json(source, NOW), fake

    def test_configured_sources_resolve(self):
        listings_sources = [s for s in CONFIG["sources"] if s["type"] == "listings_json"]
        self.assertEqual(len(listings_sources), 4)
        for source in listings_sources:
            with self.subTest(source=source["name"]):
                path = source.get("path") or digest.DEFAULT_LISTINGS_PATH
                data, fake = self.read(source, {(source["repo"], path): [{"title": "x"}]})
                self.assertEqual(data, [{"title": "x"}])
                self.assertEqual(fake.probes[-1], (source["repo"], path))
        supra = next(s for s in listings_sources if s["name"] == "instagram:zero2sudo")
        self.assertEqual((supra["repo"], supra["path"]), ("Supra16/JobTracker", "feed/listings.json"))

    def test_default_sources_find_root_listings_json(self):
        data, _ = self.read({"repo": "o/r"}, {("o/r", "listings.json"): [{"title": "root"}]})
        self.assertEqual(data, [{"title": "root"}])

    def test_explicit_path_is_never_swapped_for_another_file(self):
        files = {("Supra16/JobTracker", "listings.json"): [{"title": "wrong file"}]}
        with self.assertRaises(digest.SourceMissing):
            self.read({"repo": "Supra16/JobTracker", "path": "feed/listings.json", "optional": True}, files)
        with self.assertRaises(RuntimeError):
            self.read({"repo": "Supra16/JobTracker", "path": "feed/listings.json"}, files)


class DigestRunTest(unittest.TestCase):
    """End-to-end dry runs with GitHub mocked; covers Supra's Instagram feed contract."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        config_dir = self.tmp / "config"
        config_dir.mkdir()
        shutil.copy(CONFIG_DIR / "company_tiers.json", config_dir / "company_tiers.json")
        config = dict(CONFIG)
        config["sources"] = [s for s in CONFIG["sources"] if s["name"] == "instagram:zero2sudo"]
        self.assertEqual(len(config["sources"]), 1, "config.json must keep the instagram:zero2sudo source")
        (config_dir / "config.json").write_text(json.dumps(config))

        now = int(time.time())
        feed = json.loads((HERE / "fixtures" / "instagram_feed.json").read_text())
        for entry in feed:
            entry["date_posted"] = now - 3600
        self.feed = feed
        self.report = self.tmp / "report.json"

        patches = [
            mock.patch.object(digest, "CONFIG_DIR", config_dir),
            mock.patch.object(digest, "STATE_DIR", self.tmp / "state"),
            mock.patch.dict(os.environ, {"DRY_RUN": "true"}),
            mock.patch.object(digest, "DRY_RUN_REPORT", str(self.report)),
            mock.patch.object(company_signals.CompanyProfiles, "_http_get", staticmethod(lambda url: None)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_digest(self, file_exists):
        files = {("Supra16/JobTracker", "feed/listings.json"): self.feed} if file_exists else \
            {("Supra16/JobTracker", "README.md"): None}
        fake = FakeGitHub(files)
        with mock.patch("builtins.print"):
            for p in fake.patches():
                p.start()
                self.addCleanup(p.stop)
            ok = digest.main()
        return ok, fake

    def test_instagram_feed_flows_through_gates_and_levels(self):
        ok, fake = self.run_digest(file_exists=True)
        self.assertTrue(ok)
        self.assertEqual(fake.probes[0], ("Supra16/JobTracker", "feed/listings.json"))
        report = json.loads(self.report.read_text())
        levels = report["levels"]
        self.assertEqual([s["company"] for s in levels["new_grad"]["scored"]], ["Stripe"])  # Seattle dropped
        self.assertEqual([s["company"] for s in levels["intern"]["scored"]], ["Notion"])    # level from title
        self.assertEqual([s["company"] for s in levels["unknown"]["scored"]], ["Some Seed Startup"])
        self.assertIn("tier: promising startup", levels["new_grad"]["scored"][0]["reasons"])
        self.assertIn("instagram:zero2sudo | new_grad | 2 | 2 | 2 | 1", report["funnel"])
        self.assertFalse((self.tmp / "state" / "seen.json").exists())  # dry run never writes seen state

    def test_missing_feed_is_skipped_quietly(self):
        ok, fake = self.run_digest(file_exists=False)
        self.assertTrue(ok)
        self.assertEqual(fake.probes, [("Supra16/JobTracker", "feed/listings.json")])  # one check, no fallback probing
        report = json.loads(self.report.read_text())
        self.assertEqual(report["errors"], {})
        self.assertIn("instagram:zero2sudo", report["skipped_optional_sources"])
        self.assertEqual(report["levels"], {})


if __name__ == "__main__":
    unittest.main()

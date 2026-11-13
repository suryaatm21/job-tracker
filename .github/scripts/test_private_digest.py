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
                                 passes_pay_gate, is_graduate_only, RoleGate, ROLE_PM, ROLE_SILICON,
                                 ROLE_FIRMWARE_ELECTRICAL, ROLE_NON_ENGINEERING, LEVEL_INTERN, LEVEL_NEW_GRAD,
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

    def test_city_followed_by_another_state_or_country_does_not_match(self):
        for location in ["Mountain View, Hawaii", "Mountain View, HI", "Irvine, Nevada", "Austin, Minnesota",
                         "Fort Lauderdale, GA", "Palo Alto, Canada", "Santa Monica, United Kingdom"]:
            with self.subTest(location=location):
                self.assertEqual(self.matcher.classify({"locations": [location]}), (LOCATION_ELSEWHERE, []))
        # Agreeing or absent states, US country segments and a following metro name keep matching
        cases = {"Mountain View, CA": ["SF Bay Area"], "Mountain View": ["SF Bay Area"],
                 "Mountain View, California, USA": ["SF Bay Area"], "Playa Vista, LA": ["Los Angeles"],
                 "San Francisco, CA/New York, NY": ["SF Bay Area", "NYC"],
                 "San Francisco, New York": ["SF Bay Area"]}
        for location, labels in cases.items():
            with self.subTest(location=location):
                self.assertEqual(self.matcher.classify({"locations": [location]}), (LOCATION_MATCH, labels))

    def test_remote_switch(self):
        cfg = dict(CONFIG["location"], include_remote=True)
        self.assertEqual(LocationMatcher(cfg).classify({"locations": ["Remote in USA"]}), (LOCATION_MATCH, ["Remote"]))


class SourceLocationLabelTest(unittest.TestCase):
    LOCATOR = LocationMatcher(CONFIG["location"])
    LABELS = {"feed": "sudo"}

    def outcome(self, source, **fields):
        return digest.location_outcome({"_source": source, **fields}, self.LOCATOR, self.LABELS)

    def test_only_a_labelled_source_without_locations_passes(self):
        self.assertEqual(self.outcome("feed", locations=[]), (LOCATION_MATCH, [], ["sudo"]))
        self.assertEqual(self.outcome("feed"), (LOCATION_MATCH, [], ["sudo"]))
        self.assertEqual(self.outcome("feed", locations=None), (LOCATION_MATCH, [], ["sudo"]))
        self.assertEqual(self.outcome("board", locations=[]), (LOCATION_UNKNOWN, [], []))
        self.assertEqual(self.outcome("board"), (LOCATION_UNKNOWN, [], []))

    def test_labelled_source_with_locations_uses_the_normal_gate(self):
        self.assertEqual(self.outcome("feed", locations=["New York, NY"]), (LOCATION_MATCH, ["NYC"], ["NYC"]))
        self.assertEqual(self.outcome("feed", locations=["Seattle, WA"]), (LOCATION_ELSEWHERE, [], []))
        self.assertEqual(self.outcome("feed", locations=["Remote"]), (LOCATION_REMOTE, [], []))
        self.assertEqual(self.outcome("feed", locations=["TBD"]), (LOCATION_UNKNOWN, [], []))

    def test_labelled_listing_dedups_on_url_only(self):
        item = {"_source": "feed", "_level": LEVEL_NEW_GRAD, "_metros": [], "company_name": "Acme",
                "title": "Software Engineer", "url": "https://jobs.example.com/acme/1"}
        url_keys, metro_keys = digest.listing_keys(item)
        self.assertEqual(metro_keys, [])
        self.assertEqual(url_keys, [digest.normalize_url(item["url"])])
        copy = {**item, "url": "https://jobs.example.com/acme/2"}  # same title, other URL: no shared key
        funnel = digest.Funnel(["feed"])
        kept = digest.drop_seen_and_duplicates([item, copy, dict(item)], {}, 86400, NOW, funnel)
        self.assertEqual([k["url"] for k in kept], [item["url"], copy["url"]])
        self.assertEqual(funnel.drops["dedup:url"][LEVEL_NEW_GRAD], 1)


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
        score, reasons, listing_reasons = score_listing({"title": "SWE"}, LEVEL_NEW_GRAD, None, profile,
                                                        self.scoring, NOW)
        self.assertEqual(listing_reasons, [])
        # recent round (+2) + round >= $50M (+1) + top investor in 36 months (+1); debt ignored
        self.assertEqual(score, 4)
        self.assertIn("raised Series B $80M, Sep 2026", reasons)
        self.assertIn("backed by Founders Fund", reasons)

    def test_public_company_gets_no_funding_points(self):
        profile = parse_simplify_company_page(simplify_page(dict(STARTUP, stock_symbol="ACME")))
        self.assertEqual(score_listing({"title": "SWE"}, LEVEL_NEW_GRAD, None, profile, self.scoring, NOW),
                         (0, [], []))

    def test_tier_pay_and_penalties(self):
        tier = self.tiers.match("Stripe")
        self.assertEqual(score_listing({"title": "SWE", "salary": "$196k/yr"}, LEVEL_NEW_GRAD, tier, None,
                                       self.scoring, NOW),
                         (6, ["tier: promising startup"], ["posted pay $196k/yr"]))
        # The big tech tier scores but never prints its label
        self.assertEqual(score_listing({"title": "SWE", "salary": "$196k/yr"}, LEVEL_NEW_GRAD,
                                       self.tiers.match("Google"), None, self.scoring, NOW),
                         (7, [], ["posted pay $196k/yr"]))
        score, _, listing_reasons = score_listing({"title": "Research Intern - PhD"}, LEVEL_INTERN, tier, None,
                                                  self.scoring, NOW)
        self.assertLess(score, self.scoring["strong_threshold"])
        self.assertEqual(listing_reasons, ["PhD/MS only"])
        # Board category no longer scores: the role gate drops off-target roles before scoring
        self.assertEqual(score_listing({"title": "SWE", "category": "Business & Operations"},
                                       LEVEL_NEW_GRAD, None, None, self.scoring, NOW), (0, [], []))
        self.assertEqual(score_listing({"title": "SWE", "salary": "competitive"}, LEVEL_NEW_GRAD, None, None,
                                       self.scoring, NOW), (0, [], ["pay unparsed: competitive"]))


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


class RoleGateTest(unittest.TestCase):
    """The role gate runs on config.json's own lists, so these cases also pin the shipped keywords."""

    gate = RoleGate(CONFIG["role_gate"])

    def classify(self, title, category=None, engineering_only=False):
        return self.gate.classify({"title": title, "category": category}, engineering_only)

    def test_listings_from_the_scheduled_runs_drop_with_the_right_reason(self):
        # (company, title, the board's category for it, reason)
        cases = [
            ("Perplexity", "Product Manager", "Software Engineering", ROLE_PM),
            ("Rubrik", "Product Manager Intern - Managed Service Provider", "Product", ROLE_PM),
            ("Anthropic", "Technical Program Manager, External Evaluators", "Other Jobs", ROLE_PM),
            ("Motorola Solutions", "Program Manager", None, ROLE_PM),
            ("Salesforce", "Program Manager", "Software Engineering", ROLE_PM),
            ("Amazon", "Post-Silicon Systems Validation Engineer I", "Hardware", ROLE_SILICON),
            ("Lightmatter", "Supply Chain Internship - Summer 2027", "Business & Operations", ROLE_NON_ENGINEERING),
            ("Amgen", "Grad Intern - Operations - Process Development", None, ROLE_NON_ENGINEERING),
            ("Arcesium", "Technology Operations Intern", "Software", ROLE_NON_ENGINEERING),
            ("Ramp", "Risk Operations Specialist, Onboarding", "Business & Operations", ROLE_NON_ENGINEERING),
            ("Wells Fargo", "Branch Operations Coordinator", "Business & Operations", ROLE_NON_ENGINEERING),
            ("Nike", "Retail Marketing Intern", None, ROLE_NON_ENGINEERING),
            ("Parspec", "Product Marketing Manager Intern", "Other Jobs", ROLE_NON_ENGINEERING),
            ("Critical Mass", "Marketing Science Associate Analyst", "AI/ML/Data", ROLE_NON_ENGINEERING),
        ]
        for company, title, category, reason in cases:
            with self.subTest(company=company, title=title):
                result = self.classify(title, category)
                self.assertIsNotNone(result)
                self.assertEqual(result[0], reason)
                # Title rules alone decide these too, wherever the listing comes from
                if category not in ("Business & Operations", "Other Jobs", "Product"):
                    self.assertEqual(self.classify(title)[0], reason)

    def test_engineering_and_research_titles_stay(self):
        for title in ["Software Engineer Intern", "Machine Learning Engineer, New Grad", "Research Scientist Intern",
                      "Product Engineer", "Applications Engineer", "Salesforce Developer",
                      "Software Engineer, Supply Chain", "Business Intelligence Engineer", "Member of Technical Staff",
                      "Software Engineer - Silicon Valley", "Applied AI Engineer - Non-Silicon",
                      "Operations Research Scientist", "Full-Stack Intern", "FPGA Design Intern"]:
            for category in (None, "Software"):
                with self.subTest(title=title, category=category):
                    self.assertIsNone(self.classify(title, category))

    def test_whole_word_matching(self):
        self.assertIsNone(self.classify("Salesforce Developer"))       # not "sales"
        self.assertIsNone(self.classify("DevOps Engineer"))            # not "ops"
        self.assertIsNone(self.classify("Applications Engineer"))      # not a PM term
        self.assertEqual(self.classify("Sales Ops Analyst")[0], ROLE_NON_ENGINEERING)

    def test_pm_and_silicon_drop_even_when_titled_engineer(self):
        self.assertEqual(self.classify("Product Management Engineer Intern", "Software"), (ROLE_PM, "product management"))
        self.assertEqual(self.classify("Associate Product Manager")[0], ROLE_PM)
        self.assertEqual(self.classify("APM Intern")[0], ROLE_PM)
        for title in ["ASIC Design Engineer", "RTL Design Engineer", "VLSI Engineer Intern", "Pre-Silicon Validation Engineer",
                      "CPU Design Verification Engineer Intern", "Physical Design Engineer", "DFT Engineer New Grad"]:
            with self.subTest(title=title):
                self.assertEqual(self.classify(title, "Hardware")[0], ROLE_SILICON)
        self.assertIsNone(self.classify("Hardware Engineer Intern", "Hardware"))  # other hardware stays

    def test_firmware_and_electrical_drop_whatever_else_the_title_says(self):
        # Titles from the 2026-11-13 digest, plus a firmware title that also says software
        cases = [("Electrical Engineer - Schie", "electrical"),
                 ("Electrical Engineering Internship - Summer 2027", "electrical"),
                 ("Fellow Firmware Engineering", "firmware"),
                 ("Firmware Engineer Intern - FW Core System Services", "firmware"),
                 ("Embedded Firmware Software Engineer", "firmware")]
        for title, term in cases:
            for category, engineering_only in ((None, False), ("Hardware", False), ("Software", True)):
                with self.subTest(title=title, category=category):
                    self.assertEqual(self.classify(title, category, engineering_only),
                                     (ROLE_FIRMWARE_ELECTRICAL, term))
        for title in ["Embedded Software Engineer Intern", "Hardware Engineer Intern", "Software Engineer Intern"]:
            for category in (None, "Hardware", "Software"):
                with self.subTest(title=title, category=category):
                    self.assertIsNone(self.classify(title, category))

    def test_categories(self):
        # Simplify's Product bucket drops unless the title names engineering work
        self.assertEqual(self.classify("Recipe Developer", "Product"), (ROLE_PM, "category: Product"))
        self.assertIsNone(self.classify("Product Engineer", "Product"))
        # zapply's non-engineering sections are title-checked: engineering/research titles stay
        for title in ["Research Intern", "R&D Engineering Intern", "Data Engineer", "Associate Scientist, Clinical Immunology",
                      "AI Solutions Engineer Intern", "Test and Launch Operations Engineer I",
                      "Intern - Component Engineering", "Assoc Detection Engineering Intern"]:
            for category in ("Other Jobs", "Business & Operations"):
                with self.subTest(title=title, category=category):
                    self.assertIsNone(self.classify(title, category))
        self.assertEqual(self.classify("Utilities Analyst Intern", "Other Jobs"),
                         (ROLE_NON_ENGINEERING, "category: Other Jobs"))
        self.assertEqual(self.classify("Supply Chain Internship", "Business & Operations"),
                         (ROLE_NON_ENGINEERING, "supply chain"))
        # An engineering category keeps a bare title; no category and no term drops
        self.assertIsNone(self.classify("Summer 2027 Intern", "AI/ML/Data"))
        self.assertEqual(self.classify("Summer 2027 Intern"), (ROLE_NON_ENGINEERING, "no engineering/research term"))
        # speedyapply's sections group companies, so the title decides
        self.assertEqual(self.classify("Customer Experience Coordinator", "FAANG+")[0], ROLE_NON_ENGINEERING)

    def test_non_engineering_terms_need_an_engineering_role_to_be_rescued(self):
        for title in ["Marketing Manager, AI", "Research Operations Manager", "Clinical Research Coordinator",
                      "Supply Chain Engineer", "Talent Engineer", "Supply Chain Data Analyst"]:
            for category in (None, "Software"):
                with self.subTest(title=title, category=category):
                    self.assertEqual(self.classify(title, category)[0], ROLE_NON_ENGINEERING)
        for title in ["Test and Launch Operations Engineer I", "Software Engineer, Supply Chain",
                      "Machine Learning Engineer, Talent", "Data Engineer - Recruiting"]:
            with self.subTest(title=title):
                self.assertIsNone(self.classify(title))

    def test_generic_titles_stay_only_on_engineering_only_sources(self):
        self.assertEqual(self.classify("Summer 2027 Internship"), (ROLE_NON_ENGINEERING, "no engineering/research term"))
        self.assertIsNone(self.classify("Summer 2027 Internship", engineering_only=True))
        self.assertIsNone(self.classify("Summer 2027 Internship", "FAANG+", engineering_only=True))
        self.assertEqual(self.classify("Supply Chain Internship", engineering_only=True)[0], ROLE_NON_ENGINEERING)
        self.assertEqual(self.classify("Product Manager", engineering_only=True)[0], ROLE_PM)

    def test_trimmed_terms_win_over_engineering_words(self):
        cases = [("Engineering Technician 2/3", ROLE_NON_ENGINEERING, "technician"),
                 ("Robotics Technician (Days)", ROLE_NON_ENGINEERING, "technician"),
                 ("Security Guard", ROLE_NON_ENGINEERING, "security guard"),
                 ("Embedded Account Executive", ROLE_NON_ENGINEERING, "account executive"),
                 ("Strategic Account Executive, Engineering and Construction", ROLE_NON_ENGINEERING, "account executive"),
                 ("Tapeout Biz Products & Engineering", ROLE_SILICON, "tapeout"),
                 ("Fab Process Engineer", ROLE_SILICON, "fab"),
                 ("Cleanroom Equipment Engineer", ROLE_SILICON, "cleanroom")]
        for title, reason, term in cases:
            for category in (None, "Software", "Other Jobs", "Business & Operations"):
                with self.subTest(title=title, category=category):
                    self.assertEqual(self.classify(title, category), (reason, term))
        self.assertIsNone(self.classify("Fabric Engineer"))  # "fab" is a whole word only

    def test_officer_titles_drop_and_security_engineering_stays(self):
        for title in ["Information Security Officer", "Chief Security Officer Intern", "Security Officer"]:
            for category in (None, "Software", "Security Engineering", "Other Jobs"):
                with self.subTest(title=title, category=category):
                    self.assertEqual(self.classify(title, category), (ROLE_NON_ENGINEERING, "officer"))
        for category in (None, "Software"):
            self.assertEqual(self.classify("Security Officers", category)[0], ROLE_NON_ENGINEERING)
        for title in ["Security Engineer Intern", "Security Analyst Intern"]:
            with self.subTest(title=title):
                self.assertIsNone(self.classify(title))

    def test_multiword_non_engineering_terms_win_over_their_own_words(self):
        self.assertEqual(self.classify("Sales Engineer"), (ROLE_NON_ENGINEERING, "sales engineer"))
        self.assertEqual(self.classify("Sales Engineer Intern")[0], ROLE_NON_ENGINEERING)
        self.assertEqual(self.classify("Sales Engineering Intern"), (ROLE_NON_ENGINEERING, "sales engineering"))
        self.assertEqual(self.classify("Quantitative Trader", "Quant")[0], ROLE_NON_ENGINEERING)
        self.assertIsNone(self.classify("Quantitative Researcher", "Quant"))


class RenderTest(unittest.TestCase):
    def test_listing_without_reasons_has_no_placeholder(self):
        item = {"company_name": "Acme", "title": "Software Engineer", "url": "https://x.test/1"}
        header, lines, _ = digest.render_level(LEVEL_INTERN, [(0, [], [], ["NYC"], item)], 48, 3, 40, 25)
        line = next(l for l in lines if "Acme" in l)
        self.assertTrue(line.endswith("NYC"), line)
        self.assertNotIn("signals", line)
        self.assertNotIn("<i>", line)
        self.assertIn("🧑‍🎓 Intern", header)
        self.assertNotIn("🧪", header)

    def test_html_is_escaped_and_overflow_counted(self):
        item = {"company_name": "M&T <Bank>", "title": "Data & API <Eng>", "url": "https://x.test/a?b=1&c=2"}
        scored = [(5, ["tier: promising startup"], [], ["NYC"], item), (0, [], [], ["NYC"], dict(item)),
                  (0, [], [], ["NYC"], dict(item))]
        header, lines, shown = digest.render_level(LEVEL_NEW_GRAD, scored, 48, 3, max_strong=40, max_maybe=1)
        text = header + "\n".join(lines)
        self.assertIn("M&amp;T &lt;Bank&gt;", text)
        self.assertIn('href="https://x.test/a?b=1&amp;c=2"', text)
        leftover = re.sub(r"</?(b|i)>|<a href=\"[^\"]*\">|</a>", "", text)
        self.assertNotIn("<", leftover)  # only Telegram-safe tags remain
        self.assertIn("1 lower-scored listings held", text)
        self.assertEqual(len(shown), 2)  # the held-back listing is not marked as sent


MUON = ["raised Series C $250M, Aug 2026"]
VITAL = ["raised Seed $18M, Dec 2025", "backed by General Catalyst"]


def entry(score, company, title, company_reasons=(), listing_reasons=(), metros=("SF Bay Area",), url=None):
    item = {"company_name": company, "title": title,
            "url": url or f"https://jobs.example.com/{normalize_company(company)}/{title}".replace(" ", "-")}
    return (score, list(company_reasons), list(listing_reasons), list(metros), item)


class GroupedRenderTest(unittest.TestCase):
    """Listings sit together under one company line that carries the company-level details once."""

    def render(self, scored, **kw):
        kw = {"max_strong": 40, "max_maybe": 25, **kw}
        return digest.render_level(LEVEL_INTERN, scored, 48, 3, **kw)

    def interleaved(self):
        return [entry(6, "Muon Space", "Flight Software Intern", MUON, ["posted pay $60/hr"]),
                entry(5, "Muon Space", "Ground Software Intern", MUON),
                entry(4, "Vital Lyfe", "Software Engineering Intern", VITAL),
                entry(4, "VITAL LYFE", "Embedded Software Engineer Intern", VITAL),
                entry(3, "Muon Space", "Mission Software Intern", MUON, metros=["Los Angeles"])]

    def test_company_listings_sit_together_in_best_first_order(self):
        header, lines, shown = self.render(self.interleaved())
        self.assertEqual(lines[0], "⭐ <b>Strong signal (5)</b>")
        self.assertIn("5 new", header)
        muon, vital = lines[1], lines[2]
        self.assertEqual(len(lines), 3)  # one splitter entry per company
        self.assertTrue(muon.startswith("• <b>Muon Space</b>"), muon)
        self.assertEqual([re.search(r">([^<]+)</a>", l).group(1) for l in muon.split("\n")[1:]],
                         ["Flight Software Intern", "Ground Software Intern", "Mission Software Intern"])
        self.assertTrue(vital.startswith("• <b>Vital Lyfe</b>"), vital)  # VITAL LYFE joins Vital Lyfe
        self.assertEqual(len(vital.split("\n")), 3)
        self.assertEqual([i["title"] for i in shown], [e[4]["title"] for e in self.interleaved()])

    def test_company_details_appear_once_and_listing_notes_stay_on_their_line(self):
        _, lines, _ = self.render(self.interleaved())
        text = "\n".join(lines)
        self.assertEqual(text.count("raised Series C $250M, Aug 2026"), 1)
        self.assertEqual(text.count("raised Seed $18M, Dec 2025 · backed by General Catalyst"), 1)
        muon_company, flight, ground, mission = lines[1].split("\n")
        self.assertIn("raised Series C $250M, Aug 2026", muon_company)
        self.assertIn("posted pay $60/hr", flight)
        self.assertTrue(flight.endswith(" · <i>posted pay $60/hr</i>"), flight)
        self.assertTrue(ground.endswith("</a> · SF Bay Area"), ground)
        self.assertIsNone(re.search(r"\(\d+\)", re.sub(r"<b>[^<]*</b>", "", text)), text)  # no scores printed
        # Muon's metros differ, so each listing names its own
        self.assertNotIn("SF Bay Area", muon_company)
        self.assertIn("Los Angeles", mission)
        # Vital Lyfe's listings share one metro, so it moves to the company line
        vital_company, *vital_listings = lines[2].split("\n")
        self.assertEqual(vital_company, "• <b>Vital Lyfe</b> · SF Bay Area · "
                                        "<i>raised Seed $18M, Dec 2025 · backed by General Catalyst</i>")
        for line in vital_listings:
            self.assertNotIn("SF Bay Area", line)
            self.assertTrue(line.startswith("    ◦ <a href="), line)

    def test_maybe_section_groups_without_scores(self):
        scored = [entry(1, "Acme", "SWE Intern", ["backed by Y Combinator"], ["PhD/MS only"]),
                  entry(0, "Other Co", "ML Intern"),
                  entry(0, "acme", "Data Intern", ["backed by Y Combinator"])]
        _, lines, _ = self.render(scored)
        self.assertEqual(lines[0], "🤔 <b>Maybe (3)</b>")
        acme = lines[1].split("\n")
        self.assertEqual(acme[0], "• Acme · SF Bay Area · <i>backed by Y Combinator</i>")
        self.assertTrue(acme[1].endswith("</a> · <i>PhD/MS only</i>"), acme[1])
        self.assertTrue(acme[2].endswith("</a>"), acme[2])
        self.assertTrue(lines[2].startswith("• Other Co — <a href="), lines[2])  # one listing: one line

    def test_a_company_detail_not_every_listing_shares_stays_on_its_listing(self):
        scored = [entry(5, "Acme", "SWE Intern", ["tier: promising startup", "backed by Y Combinator"]),
                  entry(3, "Acme", "ML Intern", ["tier: promising startup"])]
        company, first, second = self.render(scored)[1][1].split("\n")
        self.assertIn("<i>tier: promising startup</i>", company)
        self.assertIn("backed by Y Combinator", first)
        self.assertNotIn("backed by", second)

    def test_company_details_appear_once_across_strong_and_maybe(self):
        scored = [entry(5, "Acme", "SWE Intern", ["raised Series C $250M"]),
                  entry(4, "Acme", "ML Intern", ["raised Series C $250M"]),
                  entry(2, "Acme", "Data Intern", ["raised Series C $250M"], ["PhD/MS only"])]
        _, lines, _ = self.render(scored)
        text = "\n".join(lines)
        self.assertEqual(text.count("raised Series C $250M"), 1)
        maybe = lines[-1]
        self.assertTrue(maybe.startswith("• Acme — <a href="), maybe)
        self.assertTrue(maybe.endswith("<i>PhD/MS only</i>"), maybe)

    def test_caps_count_listings_not_companies(self):
        header, lines, shown = self.render(self.interleaved(), max_strong=2)
        self.assertIn("2 new", header)
        self.assertEqual(lines[0], "⭐ <b>Strong signal (2)</b>")
        self.assertEqual(len(lines[1].split("\n")), 3)  # Muon line + its two best listings
        self.assertIn("3 lower-scored listings held", lines[-1])
        self.assertEqual(len(shown), 2)

    def test_oversized_group_splits_between_listings_and_repeats_the_company_line(self):
        long_url = "https://acme.wd5.myworkdayjobs.com/en-US/External_Careers/job/San-Francisco-CA/" + "x" * 180
        scored = [entry(5, "Acme", f"Software Engineer Intern, Team {n:02d}", MUON, ["posted pay $60/hr"],
                        metros=[("SF Bay Area", "NYC")[n % 2]], url=f"{long_url}/{n}") for n in range(40)]
        header, lines, _ = self.render(scored)
        self.assertGreater(len(lines), 2)  # the group came back in parts
        batches = digest.safe_join_lines(header, lines, digest.MAX_TELEGRAM_MESSAGE_CHARS)
        self.assertGreater(len(batches), 1)
        titles = []
        for batch in batches:
            self.assertLessEqual(digest.tg_len(batch), digest.MAX_TELEGRAM_MESSAGE_CHARS)
            self.assertNotIn("…", batch)
            body = batch.split("\n\n", 1)[1]
            if body.startswith("⭐"):
                body = body.split("\n", 1)[1]
            self.assertTrue(body.startswith("• <b>Acme</b> · <i>raised Series C $250M, Aug 2026</i>\n"), body[:80])
            titles += re.findall(r"Team (\d\d)", batch)
        self.assertEqual(titles, [f"{n:02d}" for n in range(40)])  # none lost, none reordered


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
        self.assertNotEqual(digest.normalize_url("https://careers.withwaymo.com/jobs?gh_jid=8350687"),
                            digest.normalize_url("https://careers.withwaymo.com/jobs?gh_jid=8350832"))

    def test_only_tracking_parameters_are_ignored(self):
        same = ["https://jobs.test/a/1", "https://JOBS.test/a/1/?utm_source=x&utm_medium=y",
                "https://jobs.test/a/1?ref=board&source=gh#apply", "https://jobs.test/a/1?gh_src=abc&src=feed"]
        self.assertEqual(len({digest.normalize_url(u) for u in same}), 1)
        self.assertEqual(digest.normalize_url("https://b.test/x?b=2&a=1"),
                         digest.normalize_url("https://b.test/x?a=1&b=2&utm_campaign=z"))
        kept, funnel = self.run_dedup([
            self.job("Acme", "SWE", same[0], ["NYC"]), self.job("Other", "Other", same[2], ["Miami"])])
        self.assertEqual(len(kept), 1)
        self.assertEqual(funnel.drops["dedup:url"][LEVEL_NEW_GRAD], 1)

    def test_doubled_slash_in_path_shares_a_key(self):
        self.assertEqual(digest.normalize_url("https://site.com//jobs/1"),
                         digest.normalize_url("https://site.com/jobs/1"))
        self.assertEqual(digest.normalize_url("https://site.com/jobs//1"),
                         digest.normalize_url("https://site.com/jobs/1"))
        kept, funnel = self.run_dedup([
            self.job("Acme", "SWE", "https://site.com//jobs/1", ["NYC"]),
            self.job("Acme", "SWE", "https://site.com/jobs/1", ["NYC"])])
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
        # Seattle dropped; the listing with no locations posts under the source's label
        self.assertEqual([s["company"] for s in levels["new_grad"]["scored"]], ["Stripe", "Unlisted Labs"])
        self.assertEqual([s["company"] for s in levels["intern"]["scored"]], ["Notion"])    # level from title
        self.assertEqual([s["company"] for s in levels["unknown"]["scored"]], ["Some Seed Startup"])
        self.assertIn("tier: promising startup", levels["new_grad"]["scored"][0]["reasons"])
        self.assertIn("instagram:zero2sudo | new_grad | 3 | 3 | 3 | 2", report["funnel"])
        self.assertFalse((self.tmp / "state" / "seen.json").exists())  # dry run never writes seen state

    def test_role_gate_drops_every_level_and_is_counted(self):
        extra = [("Rubrik", "Product Manager Intern", "intern"),
                 ("Amazon", "Post-Silicon Systems Validation Engineer I", "new grad"),
                 ("Ramp", "Risk Operations Specialist", None),  # level unknown: still gated
                 ("Wells Fargo", "Branch Operations Coordinator", None)]
        for i, (company, title, level) in enumerate(extra):
            entry = {"source": "instagram:zero2sudo", "company_name": company, "title": title,
                     "url": f"https://jobs.example.com/role/{i}", "locations": ["New York, NY"],
                     "date_posted": self.feed[0]["date_posted"], "active": True}
            if level:
                entry["level"] = level
            self.feed.append(entry)
        ok, _ = self.run_digest(file_exists=True)
        self.assertTrue(ok)
        report = json.loads(self.report.read_text())
        self.assertIn("role:pm | 0 | 1 | 0", report["drops"])
        self.assertIn("role:silicon | 1 | 0 | 0", report["drops"])
        self.assertIn("role:non_engineering | 0 | 0 | 2", report["drops"])
        self.assertEqual(sorted((d["reason"], d["company"]) for d in report["role_drops"]),
                         [("non_engineering", "Ramp"), ("non_engineering", "Wells Fargo"),
                          ("pm", "Rubrik"), ("silicon", "Amazon")])
        posted = {s["company"] for level in report["levels"].values() for s in level["scored"]}
        self.assertEqual(posted, {"Stripe", "Notion", "Some Seed Startup", "Unlisted Labs"})
        text = "\n".join(m for level in report["levels"].values() for m in level["messages"])
        self.assertIn("🧑‍🎓 Intern", text)
        self.assertNotIn("no company signals found", text)

    def test_big_tech_reaches_strong_signal_without_its_label_and_firmware_drops(self):
        extra = [("Google", "Software Engineer Intern", "intern"),
                 ("Zoox", "Firmware Engineer Intern - FW Core System Services", "intern"),
                 ("Microsoft", "Electrical Engineer - Schie", "new grad")]
        for i, (company, title, level) in enumerate(extra):
            self.feed.append({"source": "instagram:zero2sudo", "company_name": company, "title": title,
                              "url": f"https://jobs.example.com/bigtech/{i}", "locations": ["New York, NY"],
                              "date_posted": self.feed[0]["date_posted"], "active": True, "level": level})
        ok, _ = self.run_digest(file_exists=True)
        self.assertTrue(ok)
        report = json.loads(self.report.read_text())
        google = next(s for s in report["levels"]["intern"]["scored"] if s["company"] == "Google")
        self.assertGreaterEqual(google["score"], report["strong_threshold"])
        self.assertTrue(google["on_a_tier_list"])
        intern_text = "\n".join(report["levels"]["intern"]["messages"])
        strong = intern_text.split("⭐", 1)[1].split("🤔", 1)[0]
        self.assertIn("Google", strong)
        self.assertNotIn("big tech", "\n".join(m for lv in report["levels"].values() for m in lv["messages"]))
        self.assertEqual(sorted((d["reason"], d["company"]) for d in report["role_drops"]),
                         [("firmware_electrical", "Microsoft"), ("firmware_electrical", "Zoox")])

    def test_listing_without_locations_posts_with_the_source_label(self):
        no_key = {k: v for k, v in self.feed[-1].items() if k != "locations"}
        self.feed.append({**no_key, "company_name": "Keyless Co", "url": "https://jobs.example.com/keyless/6"})
        self.feed.append({**self.feed[-1], "company_name": "Keyless Co Copy"})  # same URL: dedup drops it
        ok, _ = self.run_digest(file_exists=True)
        self.assertTrue(ok)
        report = json.loads(self.report.read_text())
        new_grad = {s["company"]: s for s in report["levels"]["new_grad"]["scored"]}
        self.assertEqual(sorted(new_grad), ["Keyless Co", "Stripe", "Unlisted Labs"])
        self.assertEqual(new_grad["Unlisted Labs"]["metros"], ["sudo"])
        self.assertEqual(new_grad["Keyless Co"]["metros"], ["sudo"])
        self.assertEqual(new_grad["Stripe"]["metros"], ["SF Bay Area"])
        messages = "\n".join(report["levels"]["new_grad"]["messages"])
        self.assertRegex(messages, r"Unlisted Labs — <a href=\"https://jobs\.example\.com/unlisted/5555555\">"
                                   r"Software Engineer, New Grad</a> · sudo")
        self.assertIn("· SF Bay Area", messages)
        self.assertIn("location:elsewhere | 1 | 0 | 0", report["drops"])  # Seattle still drops
        self.assertIn("location:unknown | 0 | 0 | 0", report["drops"])
        self.assertIn("dedup:url | 1 | 0 | 0", report["drops"])

    def test_other_sources_without_locations_still_drop(self):
        other = {"name": "other/board", "type": "listings_json", "repo": "o/board", "path": "listings.json",
                 "level": "new_grad"}
        config_path = digest.CONFIG_DIR / "config.json"
        config = json.loads(config_path.read_text())
        config["sources"].append(other)
        config_path.write_text(json.dumps(config))
        unlisted = next(e for e in self.feed if e["company_name"] == "Unlisted Labs")
        board = [{**unlisted, "url": "https://jobs.example.com/board/7"}]
        fake = FakeGitHub({("Supra16/JobTracker", "feed/listings.json"): self.feed, ("o/board", "listings.json"): board})
        with mock.patch("builtins.print"):
            for p in fake.patches():
                p.start()
                self.addCleanup(p.stop)
            self.assertTrue(digest.main())
        report = json.loads(self.report.read_text())
        self.assertIn("location:unknown | 1 | 0 | 0", report["drops"])
        self.assertIn("other/board | new_grad | 1 | 1 | 1 | 0", report["funnel"])
        posted = [(s["source"], s["company"]) for s in report["levels"]["new_grad"]["scored"]]
        self.assertIn(("instagram:zero2sudo", "Unlisted Labs"), posted)
        self.assertNotIn(("other/board", "Unlisted Labs"), posted)

    def test_labelled_listing_is_suppressed_by_its_sent_url_within_ttl(self):
        state = self.tmp / "state"
        state.mkdir()
        url_key = digest.normalize_url("https://jobs.example.com/unlisted/5555555")
        (state / "seen.json").write_text(json.dumps({url_key: int(time.time()) - 3600}))
        ok, _ = self.run_digest(file_exists=True)
        self.assertTrue(ok)
        report = json.loads(self.report.read_text())
        self.assertEqual([s["company"] for s in report["levels"]["new_grad"]["scored"]], ["Stripe"])
        self.assertIn("ttl:already_sent | 1 | 0 | 0", report["drops"])

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

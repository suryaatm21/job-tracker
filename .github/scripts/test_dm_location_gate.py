#!/usr/bin/env python3
"""
DM location gate tests: DM alerts keep only listings in the DM metros
(SF Bay Area and NYC), while public channel digests stay nationwide.

Run: python -m unittest discover -s .github/scripts -p 'test_dm_location_gate.py'
"""
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from collections import Counter
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# watch_repo and send_digest_multi create STATE_DIR at import time; keep it out of the repo
os.environ["STATE_DIR"] = tempfile.mkdtemp(prefix="dm-location-gate-")

import send_digest_multi  # noqa: E402
import watch_repo  # noqa: E402
import watcher_core  # noqa: E402
from opportunity_filters import metro_subset_matcher  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
LOCATION_CONFIG = json.loads((HERE.parent / "private_channel" / "config.json").read_text())["location"]
REPO = "vanshb03/Summer2027-Internships"


DM_METROS = '["SF Bay Area", "NYC"]'


def listing(n, locations):
    return {
        "id": f"job-{n}",
        "company_name": f"Company {n}",
        "title": "Software Engineer Intern",
        "url": f"https://example.com/jobs/{n}",
        "locations": locations,
        "category": "Software Engineering",
        "season": "Summer",
        "active": True,
        "is_visible": True,
        "date_posted": int(time.time()) - 3600,
    }


CASES = {
    "detroit": ["Detroit, MI"],
    "north_kingstown": ["North Kingstown, RI"],
    "san_francisco": ["San Francisco, CA"],
    "chicago_or_nyc": ["Chicago, IL", "NYC"],
    "remote_only": ["Remote"],
    "los_angeles": ["Los Angeles, CA"],  # a private-digest metro that is not a DM metro
    "no_location": [],
}
KEPT = {"san_francisco", "chicago_or_nyc"}


def run_watcher(items, location_gate):
    """Run process_repo_entries over one commit that adds items, with GitHub stubbed out."""
    commits = [{"sha": "b" * 40, "parents": [{"sha": "a" * 40}]}]
    files = {"b" * 40: json.dumps(items), "a" * 40: "[]"}
    drops = Counter()
    with mock.patch.object(watcher_core, "get_repo_entries", return_value=commits), \
            mock.patch.object(watcher_core, "commit_detail", return_value={"files": [{"filename": "listings.json"}]}), \
            mock.patch.object(watcher_core, "get_file_at", side_effect=lambda repo, ref, path: files[ref]):
        entries = watcher_core.process_repo_entries(
            REPO, "listings.json", None, {"listings.json"}, 24, "date_posted", "date_updated",
            location_gate=location_gate, drop_counts=drops)
    return entries, drops


class DmLocationGate(unittest.TestCase):
    def setUp(self):
        self.gate = watch_repo.build_location_gate(DM_METROS)
        self.items = {name: listing(i, locs) for i, (name, locs) in enumerate(CASES.items())}
        self.by_id = {item["id"]: name for name, item in self.items.items()}

    def test_dm_keeps_only_listings_in_dm_metros(self):
        entries, _ = run_watcher(list(self.items.values()), self.gate)
        self.assertEqual({self.by_id[e["item"]["id"]] for e in entries}, KEPT)

    def test_each_named_case(self):
        entries, _ = run_watcher(list(self.items.values()), self.gate)
        kept = {self.by_id[e["item"]["id"]] for e in entries}
        for name in ("detroit", "north_kingstown", "remote_only", "los_angeles", "no_location"):
            self.assertNotIn(name, kept, name)
        for name in ("san_francisco", "chicago_or_nyc"):
            self.assertIn(name, kept, name)

    def test_drop_counts_per_reason(self):
        _, drops = run_watcher(list(self.items.values()), self.gate)
        self.assertEqual(drops, Counter({
            "location:elsewhere": 3,  # Detroit, North Kingstown, Los Angeles
            "location:remote_only": 1,
            "location:unknown": 1,
        }))

    def test_drop_log_has_counts_only(self):
        _, drops = run_watcher(list(self.items.values()), self.gate)
        with mock.patch.object(watch_repo, "debug_log") as log:
            watch_repo.log_location_drops(drops)
        line = log.call_args.args[0]
        self.assertEqual(line, "[LOCATION] dropped location:elsewhere=3 location:remote_only=1 location:unknown=1")
        for item in self.items.values():
            self.assertNotIn(item["company_name"], line)

    def test_drop_log_is_visible_at_default_log_level(self):
        _, drops = run_watcher(list(self.items.values()), self.gate)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            watch_repo.log_location_drops(drops)
        self.assertIn("[LOCATION] dropped location:elsewhere=3", out.getvalue())

    def test_without_gate_watcher_is_unfiltered(self):
        entries, drops = run_watcher(list(self.items.values()), None)
        self.assertEqual(len(entries), len(CASES))
        self.assertEqual(drops, Counter())


class GateConfiguration(unittest.TestCase):
    def test_missing_dm_metros_fails(self):
        with self.assertRaises(ValueError):
            watch_repo.build_location_gate(None)

    def test_unknown_or_empty_label_fails(self):
        for labels in (["SF Bay Area", "Seattle"], []):
            with self.assertRaises(ValueError):
                metro_subset_matcher(LOCATION_CONFIG, labels)


class PublicDigestStaysNationwide(unittest.TestCase):
    def test_channel_digest_keeps_listings_outside_dm_metros(self):
        for name in ("detroit", "north_kingstown", "los_angeles", "remote_only"):
            item = listing(0, CASES[name])
            with self.subTest(name):
                self.assertTrue(send_digest_multi.should_include_listing(item))


if __name__ == "__main__":
    unittest.main()

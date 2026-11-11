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


class DmStateLabelTest(unittest.TestCase):
    def test_needing_state_city_names_its_state(self):
        from format_utils import format_location
        self.assertEqual(format_location(["San Jose", "Seattle, WA"], "dm"), "California")

    def test_new_jersey_without_a_metro(self):
        from format_utils import format_location
        self.assertEqual(format_location(["Jersey City, NJ", "Seattle, WA"], "dm"), "New Jersey")

    def test_no_state_is_multi_location(self):
        from format_utils import format_location
        self.assertEqual(format_location(["Chicago, IL", "Nanjing, China"], "dm"), "Multi-location")


class StateCodeTest(unittest.TestCase):
    def test_code_after_last_comma_decides(self):
        from format_utils import format_location
        self.assertEqual(format_location(["San Diego, CA", "Seattle, WA"], "dm"), "California")
        self.assertEqual(format_location(["New York Mills, MN, USA", "Seattle, WA"], "dm"), "Multi-location")
        self.assertEqual(format_location(["San Jose, CA, US", "Seattle, WA"], "dm"), "California")
        self.assertEqual(format_location(["New York, NY", "Seattle, WA"], "dm"), "New York")
        self.assertEqual(format_location(["New York, United States", "Seattle, WA"], "dm"), "New York")


class WatchRepoEmojiTest(unittest.TestCase):
    def test_emoji_heavy_alert_is_batched_not_sent_whole(self):
        import watch_repo, telegram_utils
        text = "\U0001F600" * 2100
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "c"}), \
                mock.patch.object(watch_repo, "send_message") as single, \
                mock.patch.object(watch_repo, "batch_send_message", return_value=(True, [])) as batch:
            watch_repo.send_telegram(text)
        single.assert_not_called()
        batch.assert_called_once()


class OversizedLineTest(unittest.TestCase):
    def test_html_line_is_truncated_outside_tags_with_tags_closed(self):
        import telegram_utils
        line = "• <b>Acme</b> — " + "<i>" + "word " * 1200 + "</i>"
        with contextlib.redirect_stdout(io.StringIO()) as out:
            batches = telegram_utils.safe_join_lines("Header", ["first", line, "last"])
        self.assertIn("ERROR", out.getvalue())
        body = "\n".join(batches).split("\n")
        self.assertTrue(all(telegram_utils.tg_len(b) <= telegram_utils.TELEGRAM_LIMIT for b in batches))
        cut = next(l for l in body if l.startswith("• <b>Acme</b>"))
        self.assertTrue(cut.endswith("\u2026</i>"))
        self.assertEqual(cut.count("<i>"), cut.count("</i>"))
        self.assertIn("last", body)

    def test_lone_surrogate_does_not_raise(self):
        import telegram_utils
        self.assertEqual(telegram_utils.tg_len("a\ud83db"), 3)


class OversizedHeaderTest(unittest.TestCase):
    def test_header_is_truncated_and_every_batch_fits(self):
        import telegram_utils
        header = "H" * 5000
        with contextlib.redirect_stdout(io.StringIO()) as out:
            batches = telegram_utils.safe_join_lines(header, ["a line", "b" * 6000])
        self.assertIn("ERROR", out.getvalue())
        self.assertTrue(batches[0].startswith("H" * 100))
        self.assertTrue(all(telegram_utils.tg_len(b) <= telegram_utils.TELEGRAM_LIMIT for b in batches))

    def test_header_kept_whole_when_first_line_fits(self):
        import telegram_utils
        header = "H" * 3000
        batches = telegram_utils.safe_join_lines(header, ["a" * 1000, "b" * 1000])
        self.assertTrue(batches[0].startswith(header + "\n\n"))
        self.assertTrue(all(telegram_utils.tg_len(b) <= telegram_utils.TELEGRAM_LIMIT for b in batches))

    def test_header_cut_only_as_far_as_needed(self):
        import telegram_utils
        batches = telegram_utils.safe_join_lines("H" * 4090, ["a" * 10, "b"])
        self.assertGreater(telegram_utils.tg_len(batches[0]), 4000)
        self.assertLessEqual(telegram_utils.tg_len(batches[0]), telegram_utils.TELEGRAM_LIMIT)

    def test_lone_text_that_fits_is_not_cut(self):
        import telegram_utils
        self.assertEqual(telegram_utils.safe_join_lines("x" * 4050, []), ["x" * 4050])


if __name__ == "__main__":
    unittest.main()

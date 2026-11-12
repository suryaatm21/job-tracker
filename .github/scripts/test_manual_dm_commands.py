#!/usr/bin/env python3
"""
Manual latest/today/recent command tests: with DM_METROS set (the DM workflow)
they send only listings in the DM metros; without it (the channel digests) they
send listings from every location, exactly as before.

Run: python -m unittest discover -s .github/scripts -p 'test_manual_dm_commands.py'
"""
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import time
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
WORKFLOWS = HERE.parent / "workflows"
DM_METROS = '["SF Bay Area", "NYC"]'
REPO = "vanshb03/Summer2027-Internships"
OTHER_REPO = "SimplifyJobs/Summer2027-Internships"
DAY = 24 * 3600


def noon_utc_today():
    """Fixture clock: ages count back from noon UTC today, so "today" never straddles midnight."""
    now = int(time.time())
    return now - now % DAY + DAY // 2


def listing(key, company, locations, age_seconds=0):
    return {
        "id": key,
        "company_name": company,
        "title": "Software Engineer Intern",
        "url": f"https://example.com/jobs/{key}",
        "locations": locations,
        "season": "Summer",
        "date_posted": noon_utc_today() - age_seconds,
    }


def fixture():
    """In-metro and out-of-metro listings; LA is the newest overall, Detroit the newest today besides it."""
    return [
        listing("sf", "Bayco", ["San Francisco, CA"], age_seconds=60),
        listing("detroit", "Detco", ["Detroit, MI"], age_seconds=30),
        listing("chicago-or-nyc", "Gothamco", ["Chicago, IL", "NYC"], age_seconds=90),
        listing("remote", "Anyco", ["Remote"], age_seconds=20),
        listing("la", "Angelco", ["Los Angeles, CA"], age_seconds=0),
        listing("no-location", "Blankco", [], age_seconds=10),
        listing("san-jose-old", "Valleyco", ["San Jose, CA"], age_seconds=3 * DAY),
        listing("austin-old", "Austinco", ["Austin, TX"], age_seconds=4 * DAY),
    ]


OUT_OF_METRO = ("Detco", "Anyco", "Angelco", "Blankco", "Austinco")


def load_script(name, env):
    """Import a manual script fresh (it reads TARGET_REPO(S), COUNT and MESSAGE_PREFIX at import)."""
    path = HERE / "manual" / name
    spec = importlib.util.spec_from_file_location(f"manual_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, env):
        spec.loader.exec_module(module)
    return module


def run(name, listings_by_repo, dm_metros=None, **env):
    """Run a manual script's main() against fixtures; returns the texts it sent (no network)."""
    env.setdefault("TARGET_REPO", REPO)
    env.setdefault("MESSAGE_PREFIX", "DM")
    module = load_script(name, env)
    sent = []

    def fake_send(token, chat_id, text, parse_mode=None, chat_label=None):
        sent.append(text)
        return True, 200, ""

    run_env = {k: v for k, v in os.environ.items() if k != "DM_METROS"}
    if dm_metros is not None:
        run_env["DM_METROS"] = dm_metros
    with mock.patch.dict(os.environ, run_env, clear=True), \
            mock.patch.object(module, "fetch_listings", side_effect=lambda repo, path: [dict(x) for x in listings_by_repo[repo]]), \
            mock.patch.object(module, "send_message", side_effect=fake_send), \
            mock.patch("builtins.print"):
        rc = module.main()
    assert rc == 0, rc
    return sent


class LatestCommand(unittest.TestCase):
    SCRIPT = "send_latest_listing.py"

    def test_gated_sends_newest_listing_in_dm_metros(self):
        [text] = run(self.SCRIPT, {REPO: fixture()}, DM_METROS)
        self.assertTrue(text.startswith("DM: Latest listing:\n"), text)
        self.assertIn("Bayco", text)
        self.assertNotIn("Angelco", text)

    def test_gated_with_nothing_in_metros_sends_nothing_found(self):
        out_only = [x for x in fixture() if x["company_name"] in OUT_OF_METRO]
        self.assertEqual(run(self.SCRIPT, {REPO: out_only}, DM_METROS),
                         ["DM: No listings found in SF Bay Area, NYC."])

    def test_ungated_sends_newest_listing_anywhere(self):
        [text] = run(self.SCRIPT, {REPO: fixture()})
        self.assertIn("Angelco", text)

    def test_empty_string_dm_metros_is_ungated(self):
        [text] = run(self.SCRIPT, {REPO: fixture()}, "")
        self.assertIn("Angelco", text)

    def test_invalid_dm_metros_fails_the_run(self):
        with self.assertRaises(ValueError):
            run(self.SCRIPT, {REPO: fixture()}, '["SF Bay Area", "Seattle"]')


class TodayCommand(unittest.TestCase):
    SCRIPT = "send_todays_listings.py"

    def test_gated_sends_only_todays_listings_in_dm_metros(self):
        [text] = run(self.SCRIPT, {REPO: fixture()}, DM_METROS)
        self.assertTrue(text.startswith("DM: New listings today: 2\n"), text)
        self.assertIn("Bayco", text)
        self.assertIn("Gothamco", text)
        for company in OUT_OF_METRO + ("Valleyco",):
            self.assertNotIn(company, text)

    def test_gated_with_nothing_in_metros_sends_nothing_found(self):
        out_only = [x for x in fixture() if x["company_name"] in OUT_OF_METRO]
        self.assertEqual(run(self.SCRIPT, {REPO: out_only}, DM_METROS), ["No new listings posted today."])

    def test_ungated_sends_every_listing_today(self):
        [text] = run(self.SCRIPT, {REPO: fixture()})
        self.assertTrue(text.startswith("DM: New listings today: 6\n"), text)
        for company in ("Bayco", "Gothamco", "Detco", "Anyco", "Angelco", "Blankco"):
            self.assertIn(company, text)

    def test_gate_runs_before_cross_repo_dedup(self):
        # The same URL listed as Detroit on one board and San Francisco on the other
        first = listing("shared", "Sharedco", ["Detroit, MI"])
        second = listing("shared", "Sharedco", ["San Francisco, CA"])
        [text] = run(self.SCRIPT, {OTHER_REPO: [first], REPO: [second]}, DM_METROS,
                     TARGET_REPOS=f'["{OTHER_REPO}", "{REPO}"]')
        self.assertTrue(text.startswith("DM: New listings today: 1\n"), text)
        self.assertIn("San Francisco, CA", text)


class RecentCommand(unittest.TestCase):
    SCRIPT = "send_recent_listings.py"

    def test_gated_count_applies_after_the_gate(self):
        # COUNT=3 over the gated set fills all 3 slots with in-metro listings
        [text] = run(self.SCRIPT, {REPO: fixture()}, DM_METROS, COUNT="3")
        self.assertTrue(text.startswith("DM: Most recent listings: 3\n"), text)
        for company in ("Bayco", "Gothamco", "Valleyco"):
            self.assertIn(company, text)
        for company in OUT_OF_METRO:
            self.assertNotIn(company, text)

    def test_gated_with_nothing_in_metros_sends_nothing_found(self):
        out_only = [x for x in fixture() if x["company_name"] in OUT_OF_METRO]
        self.assertEqual(run(self.SCRIPT, {REPO: out_only}, DM_METROS), ["No recent listings found."])

    def test_ungated_count_applies_to_every_listing(self):
        [text] = run(self.SCRIPT, {REPO: fixture()}, COUNT="3")
        self.assertTrue(text.startswith("DM: Most recent listings: 3\n"), text)
        # Existing order: company name, then newest first
        for company in ("Angelco", "Anyco", "Austinco"):
            self.assertIn(company, text)

    def test_gate_runs_before_cross_repo_dedup(self):
        first = listing("shared", "Sharedco", ["Detroit, MI"])
        second = listing("shared", "Sharedco", ["San Francisco, CA"])
        [text] = run(self.SCRIPT, {OTHER_REPO: [first], REPO: [second]}, DM_METROS,
                     TARGET_REPOS=f'["{OTHER_REPO}", "{REPO}"]')
        self.assertTrue(text.startswith("DM: Most recent listings: 1\n"), text)
        self.assertIn("San Francisco, CA", text)


class WorkflowWiring(unittest.TestCase):
    MANUAL_SCRIPTS = ("manual/send_latest_listing.py", "manual/send_todays_listings.py",
                      "manual/send_recent_listings.py")

    @staticmethod
    def load_workflow(path):
        ruby = shutil.which("ruby")
        if ruby is None:
            raise unittest.SkipTest("ruby is needed to parse workflow YAML")
        out = subprocess.run(
            [ruby, "-ryaml", "-rjson", "-e", "puts JSON.generate(YAML.safe_load(File.read(ARGV[0])))", str(path)],
            capture_output=True, text=True, check=True).stdout
        return json.loads(out)

    def manual_step_envs(self, workflow):
        envs = []
        for job in (workflow.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                if any(s in (step.get("run") or "") for s in self.MANUAL_SCRIPTS):
                    envs.append({**(workflow.get("env") or {}), **(job.get("env") or {}),
                                 **(step.get("env") or {})})
        return envs

    def test_only_the_dm_workflow_sets_dm_metros_for_manual_commands(self):
        by_name = {p.name: self.manual_step_envs(self.load_workflow(p)) for p in WORKFLOWS.glob("*.yml")}
        by_name = {name: envs for name, envs in by_name.items() if envs}
        self.assertIn("dm-fast-watch.yml", by_name)
        self.assertGreater(len(by_name), 1)
        for name, envs in by_name.items():
            for env in envs:
                if name == "dm-fast-watch.yml":
                    self.assertEqual(json.loads(env["DM_METROS"]), ["SF Bay Area", "NYC"])
                else:
                    self.assertNotIn("DM_METROS", env, name)

if __name__ == "__main__":
    unittest.main()

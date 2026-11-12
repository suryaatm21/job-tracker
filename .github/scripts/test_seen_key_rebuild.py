#!/usr/bin/env python3
"""
Seen-memory key version tests: the first run on new dedup keys rebuilds the memory
silently, sending nothing, so no listing already sent under an old key posts twice.

Run: python -m unittest discover -s .github/scripts -p 'test_seen_key_rebuild.py'
"""
import base64
import contextlib
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# watch_repo and send_digest_multi create STATE_DIR at import time; keep it out of the repo
os.environ.setdefault("STATE_DIR", tempfile.mkdtemp(prefix="seen-key-rebuild-"))

import send_digest_multi  # noqa: E402
import state_utils  # noqa: E402
import watch_repo  # noqa: E402
import github_helper  # noqa: E402
import repo_utils  # noqa: E402

REPO = "vanshb03/Summer2027-Internships"
PAGE = "https://boards.greenhouse.io/acme/jobs"
# The key both postings below shared before keys kept identifying query parameters
LEGACY_KEY = PAGE


def listing(n, url):
    return {
        "id": f"job-{n}",
        "company_name": f"Company {n}",
        "title": "Software Engineer Intern",
        "url": url,
        "locations": ["San Francisco, CA"],
        "category": "Software Engineering",
        "season": "Summer",
        "active": True,
        "is_visible": True,
        "date_posted": int(time.time()) - 3600,
    }


SENT = listing(1, f"{PAGE}?gh_jid=1")    # posted before the key change, under LEGACY_KEY
SIBLING = listing(2, f"{PAGE}?gh_jid=2")  # merged into SENT by the old key, never posted
NEW = listing(3, f"{PAGE}?gh_jid=3")      # appears after the rebuild


class StateDirTestCase(unittest.TestCase):
    def setUp(self):
        self.state = pathlib.Path(tempfile.mkdtemp(prefix="seen-key-rebuild-"))
        self.seen_path = self.state / "seen.json"
        self.marker = state_utils.key_version_path(self.seen_path)

    def write_legacy_memory(self):
        """seen.json as the old code left it: old keys and no key-version marker."""
        self.seen_path.write_text(json.dumps({LEGACY_KEY: int(time.time()) - 86400}))

    def memory(self):
        return json.loads(self.seen_path.read_text())


class KeyVersionMarker(StateDirTestCase):
    def test_empty_memory_needs_no_rebuild(self):
        self.assertFalse(state_utils.seen_needs_rebuild({}, str(self.seen_path)))

    def test_memory_without_marker_needs_rebuild(self):
        self.assertTrue(state_utils.seen_needs_rebuild({LEGACY_KEY: 1}, str(self.seen_path)))

    def test_older_or_garbled_marker_needs_rebuild(self):
        for text in (f"{state_utils.SEEN_KEY_VERSION - 1}\n", "not-a-number"):
            self.marker.write_text(text)
            with self.subTest(text):
                self.assertTrue(state_utils.seen_needs_rebuild({LEGACY_KEY: 1}, str(self.seen_path)))

    def test_save_writes_the_current_marker(self):
        state_utils.save_seen({"k": int(time.time())}, 14, str(self.seen_path))
        self.assertEqual(self.marker.read_text().strip(), str(state_utils.SEEN_KEY_VERSION))
        self.assertFalse(state_utils.seen_needs_rebuild({"k": 1}, str(self.seen_path)))

    def test_unfinished_rebuild_stays_pending_when_the_memory_is_empty(self):
        state_utils.save_seen({}, 14, str(self.seen_path), mark_version=False)
        self.assertTrue(state_utils.seen_needs_rebuild({}, str(self.seen_path)))
        state_utils.save_seen({}, 14, str(self.seen_path))
        self.assertFalse(state_utils.seen_needs_rebuild({}, str(self.seen_path)))

    def test_dry_run_save_writes_nothing(self):
        state_utils.save_seen({"k": int(time.time())}, 14, str(self.seen_path), dry_run=True)
        self.assertEqual(list(self.state.iterdir()), [])

    def test_rebuild_keeps_times_of_unchanged_keys(self):
        seen = {state_utils.get_cache_key(SENT): 100}
        added = state_utils.rebuild_seen(seen, [SENT, SIBLING, {}], 500)
        self.assertEqual(added, 1)
        self.assertEqual(seen, {state_utils.get_cache_key(SENT): 100,
                                state_utils.get_cache_key(SIBLING): 500})


def http_error(status):
    response = mock.Mock(status_code=status)
    return requests.HTTPError(f"{status} Error", response=response)


def failing_fetch(status):
    """Drive a real HTTP error through repo_utils.fetch_listings and github_helper.fetch_file_content."""
    stack = mock.patch.object(github_helper, "gh_get", side_effect=http_error(status))
    resolve = mock.patch.object(repo_utils, "resolve_listings_source", return_value=("dev", "listings.json"))
    return stack, resolve


def failing_seen_save():
    """Make writing seen.json fail while every other state file still writes."""
    real = state_utils.write_atomic

    def write(path, text):
        if pathlib.Path(path).name == "seen.json":
            raise OSError("disk full")
        return real(path, text)

    return mock.patch.object(state_utils, "write_atomic", side_effect=write)


OTHER = "other/Repo"
OTHER_ITEM = listing(4, "https://boards.greenhouse.io/other/jobs?gh_jid=4")
REPO_MISSING = "repo-missing"  # GitHub answers 404 for the repo itself (renamed, deleted, private)
# Only the repo lookup answers 404 (a transient GitHub error); its other endpoints still answer
REPO_LOOKUP_404 = "repo-lookup-404"
FILE_MISSING = "file-missing"  # the repo exists but has no listings file


@contextlib.contextmanager
def fake_github(repos, head="c" * 40):
    """
    Answer GitHub API calls from repos: {repo: listings | REPO_MISSING | REPO_LOOKUP_404 | FILE_MISSING}.
    Patches both modules that call gh_get, so the real resolve and fetch code runs.
    """
    def gh_get(url, **params):
        path = url[len(github_helper.GH):]
        repo = next(r for r in repos if path == f"/repos/{r}" or path.startswith(f"/repos/{r}/"))
        state = repos[repo]
        rest = path[len(f"/repos/{repo}"):]
        if state == REPO_MISSING or (state == REPO_LOOKUP_404 and rest == ""):
            raise http_error(404)
        if rest == "":
            return {"default_branch": "dev"}
        if rest == "/commits":
            return [{"sha": head}]
        if rest.startswith("/contents/"):
            if state == FILE_MISSING:
                raise http_error(404)
            return {"encoding": "base64", "content": base64.b64encode(json.dumps(state).encode()).decode()}
        raise AssertionError(f"unexpected GitHub call {url}")

    with mock.patch.object(github_helper, "gh_get", side_effect=gh_get), \
            mock.patch.object(repo_utils, "gh_get", side_effect=gh_get), \
            mock.patch.dict(repo_utils._LISTINGS_SOURCE_CACHE, clear=True):
        yield


def expired_legacy_memory(seen_path):
    """Old-key memory whose every entry is past the TTL pruning cutoff (14 + 2 days)."""
    seen_path.write_text(json.dumps({LEGACY_KEY: int(time.time()) - 20 * 86400}))


class DigestSilentFirstRun(StateDirTestCase):
    def run_digest(self, listings, dry_run=False, status=None):
        send = mock.Mock(return_value=(True, []))
        patches = failing_fetch(status) if status else (mock.patch.object(send_digest_multi, "get_listings", return_value=listings),)
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            return self._run_digest(send, dry_run)

    def _run_digest(self, send, dry_run, repos=(REPO,)):
        with mock.patch.object(send_digest_multi, "STATE_DIR", self.state), \
                mock.patch.object(send_digest_multi, "DRY_RUN", dry_run), \
                mock.patch.object(send_digest_multi, "TARGET_REPOS", list(repos)), \
                mock.patch.object(send_digest_multi, "batch_send_message", send), \
                mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "c"}):
            self.assertTrue(send_digest_multi.main())
        return send

    def sent_urls(self, send):
        lines = [line for call in send.call_args_list for line in call.kwargs["lines"]]
        return {u for item in (SENT, SIBLING, NEW) for u in [item["url"]] if any(u in l for l in lines)}

    def test_first_run_on_old_keys_sends_nothing_and_records_every_listing(self):
        self.write_legacy_memory()
        send = self.run_digest([SENT, SIBLING])
        send.assert_not_called()
        memory = self.memory()
        self.assertIn(state_utils.get_cache_key(SENT), memory)
        self.assertIn(state_utils.get_cache_key(SIBLING), memory)
        self.assertIn(LEGACY_KEY, memory)
        self.assertEqual(self.marker.read_text().strip(), str(state_utils.SEEN_KEY_VERSION))

    def test_next_run_sends_only_the_listing_that_arrived_after_the_rebuild(self):
        self.write_legacy_memory()
        first = self.run_digest([SENT, SIBLING])
        send = self.run_digest([SENT, SIBLING, NEW])
        self.assertEqual(self.sent_urls(first) | self.sent_urls(send), {NEW["url"]})

    def test_fresh_state_runs_normally(self):
        send = self.run_digest([SENT, SIBLING])
        self.assertEqual(self.sent_urls(send), {SENT["url"], SIBLING["url"]})
        self.assertEqual(self.marker.read_text().strip(), str(state_utils.SEEN_KEY_VERSION))

    def test_rebuild_with_a_failed_repo_saves_nothing(self):
        self.write_legacy_memory()
        before = self.memory()
        send = self.run_digest(None, status=500)
        send.assert_not_called()
        self.assertEqual(self.memory(), before)
        self.assertFalse(self.marker.exists())

    def test_rebuild_with_an_empty_repo_completes(self):
        self.write_legacy_memory()
        send = self.run_digest([])
        send.assert_not_called()
        self.assertEqual(self.marker.read_text().strip(), str(state_utils.SEEN_KEY_VERSION))

    def test_rebuild_with_a_missing_listings_file_completes(self):
        self.write_legacy_memory()
        send = self.run_digest(None, status=404)
        send.assert_not_called()
        self.assertEqual(self.marker.read_text().strip(), str(state_utils.SEEN_KEY_VERSION))

    def test_failed_memory_save_keeps_the_repo_in_rebuild_mode(self):
        self.write_legacy_memory()
        with failing_seen_save():
            send = self.run_digest([SENT, SIBLING])
        send.assert_not_called()
        self.assertFalse(state_utils.repo_rebuilt(self.state, REPO))
        self.assertFalse(self.marker.exists())
        send = self.run_digest([SENT, SIBLING])  # retries the rebuild instead of posting
        send.assert_not_called()
        self.assertTrue(state_utils.repo_rebuilt(self.state, REPO))

    def test_empty_repo_completes_its_rebuild_while_another_repo_fails(self):
        other = "other/Repo"
        self.write_legacy_memory()

        def run(empty_repo_listings):
            send = mock.Mock(return_value=(True, []))

            def listings(repo):
                if repo == other:
                    raise RuntimeError("boom")
                return empty_repo_listings

            with mock.patch.object(send_digest_multi, "get_listings", side_effect=listings):
                self._run_digest(send, dry_run=False, repos=(REPO, other))
            return send

        run([]).assert_not_called()
        self.assertTrue(state_utils.repo_rebuilt(self.state, REPO))
        self.assertFalse(state_utils.repo_rebuilt(self.state, other))
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.sent_urls(run([NEW])), {NEW["url"]})

    def test_rebuild_still_pending_after_pruning_empties_the_memory(self):
        expired_legacy_memory(self.seen_path)
        self.run_digest(None, status=500).assert_not_called()
        self.assertEqual(self.memory(), {})  # pruning removed every legacy entry
        send = self.run_digest([SENT, SIBLING])  # the repo recovers: rebuild silently, do not post
        send.assert_not_called()
        self.assertTrue(state_utils.repo_rebuilt(self.state, REPO))
        self.assertIn(state_utils.get_cache_key(SENT), self.memory())
        self.assertEqual(self.marker.read_text().strip(), str(state_utils.SEEN_KEY_VERSION))
        self.assertFalse(state_utils.seen_needs_rebuild(self.memory(), str(self.seen_path)))

    def run_digest_on(self, github):
        send = mock.Mock(return_value=(True, []))
        with fake_github(github):
            self._run_digest(send, dry_run=False, repos=tuple(github))
        return send

    def test_missing_repo_is_a_fetch_failure_not_an_empty_repo(self):
        self.write_legacy_memory()
        send = self.run_digest_on({REPO: FILE_MISSING, OTHER: REPO_MISSING})
        send.assert_not_called()
        self.assertTrue(state_utils.repo_rebuilt(self.state, REPO))  # missing file: an empty repo
        self.assertFalse(state_utils.repo_rebuilt(self.state, OTHER))  # missing repo: retry next run
        self.assertFalse(self.marker.exists())
        send = self.run_digest_on({REPO: FILE_MISSING, OTHER: [OTHER_ITEM]})  # the repo comes back
        send.assert_not_called()
        self.assertTrue(state_utils.repo_rebuilt(self.state, OTHER))
        self.assertEqual(self.marker.read_text().strip(), str(state_utils.SEEN_KEY_VERSION))

    def test_dry_run_rebuild_writes_nothing(self):
        self.write_legacy_memory()
        before = self.seen_path.read_text()
        send = self.run_digest([SENT, SIBLING], dry_run=True)
        send.assert_not_called()
        self.assertEqual(self.seen_path.read_text(), before)
        self.assertFalse(self.marker.exists())


class WatcherSilentFirstRun(StateDirTestCase):
    HEAD = "c" * 40

    def run_watcher(self, listings, entries=(), commits=None):
        send = mock.Mock(return_value=True)
        process = mock.Mock(return_value=list(entries))
        commits = commits or mock.Mock(return_value=[{"sha": self.HEAD}])
        with mock.patch.object(watch_repo, "STATE_DIR", self.state), \
                mock.patch.object(watch_repo, "DRY_RUN", False), \
                mock.patch.object(watch_repo, "TARGET_REPOS", [REPO]), \
                mock.patch.object(watch_repo, "fetch_listings", **({"new": repo_utils.fetch_listings} if listings is None else {"return_value": listings})), \
                mock.patch.object(watch_repo, "resolve_listings_source", return_value=("dev", "listings.json")), \
                mock.patch.object(watch_repo, "get_repo_entries", commits), \
                mock.patch.object(watch_repo, "process_repo_entries", process), \
                mock.patch.object(watch_repo, "send_telegram", send), \
                mock.patch.dict(os.environ, {"DM_METROS": '["SF Bay Area", "NYC"]'}):
            watch_repo.main()
        return send, process

    @staticmethod
    def entry(item):
        return {"key": ("url", state_utils.get_cache_key(item)), "line": f"• {item['company_name']} — {item['url']}",
                "ts": item["date_posted"], "repo": REPO, "item": item}

    def test_first_run_on_old_keys_sends_nothing_and_records_every_listing(self):
        self.write_legacy_memory()
        send, process = self.run_watcher([SENT, SIBLING])
        send.assert_not_called()
        process.assert_not_called()
        memory = self.memory()
        self.assertIn(state_utils.get_cache_key(SENT), memory)
        self.assertIn(state_utils.get_cache_key(SIBLING), memory)
        self.assertEqual(self.marker.read_text().strip(), str(state_utils.SEEN_KEY_VERSION))
        last_seen = self.state / f"last_seen_{REPO.replace('/', '_')}.txt"
        self.assertEqual(last_seen.read_text(), self.HEAD)

    def test_next_run_alerts_only_the_listing_that_arrived_after_the_rebuild(self):
        self.write_legacy_memory()
        self.run_watcher([SENT, SIBLING])
        send, _ = self.run_watcher([SENT, SIBLING, NEW], entries=[self.entry(x) for x in (SIBLING, NEW)])
        send.assert_called_once()
        message = send.call_args.args[0]
        self.assertIn(NEW["url"], message)
        self.assertNotIn(SIBLING["url"], message)

    def test_rebuild_with_a_failed_fetch_saves_nothing(self):
        self.write_legacy_memory()
        before = self.memory()
        with mock.patch.object(watch_repo, "window_listings", side_effect=RuntimeError("boom")):
            send, _ = self.run_watcher([])
        send.assert_not_called()
        self.assertEqual(self.memory(), before)
        self.assertFalse(self.marker.exists())

    def test_missing_listings_file_counts_as_an_empty_repo(self):
        self.write_legacy_memory()
        stack, resolve = failing_fetch(404)
        with stack, resolve:
            send, _ = self.run_watcher(None)
        send.assert_not_called()
        self.assertEqual(self.marker.read_text().strip(), str(state_utils.SEEN_KEY_VERSION))

    def test_failed_repo_stays_silent_while_the_other_repo_proceeds(self):
        other = "other/Repo"
        self.write_legacy_memory()

        def window(repo, now_epoch):
            if repo == other:
                raise RuntimeError("boom")
            return [SENT, SIBLING]

        def run():
            send = mock.Mock(return_value=True)
            process = mock.Mock(return_value=[self.entry(NEW)])
            with mock.patch.object(watch_repo, "STATE_DIR", self.state), \
                    mock.patch.object(watch_repo, "DRY_RUN", False), \
                    mock.patch.object(watch_repo, "TARGET_REPOS", [REPO, other]), \
                    mock.patch.object(watch_repo, "window_listings", side_effect=window), \
                    mock.patch.object(watch_repo, "resolve_listings_source", return_value=("dev", "listings.json")), \
                    mock.patch.object(watch_repo, "get_repo_entries", return_value=[{"sha": self.HEAD}]), \
                    mock.patch.object(watch_repo, "process_repo_entries", process), \
                    mock.patch.object(watch_repo, "send_telegram", send), \
                    mock.patch.dict(os.environ, {"DM_METROS": '["SF Bay Area", "NYC"]'}):
                watch_repo.main()
            return process

        first = run()
        first.assert_not_called()
        self.assertFalse(self.marker.exists())
        second = run()
        self.assertEqual([c.args[0] for c in second.call_args_list], [REPO])

    def test_unsaved_commit_position_keeps_the_repo_in_rebuild_mode(self):
        self.write_legacy_memory()
        send, process = self.run_watcher([SENT, SIBLING], commits=mock.Mock(side_effect=RuntimeError("api down")))
        send.assert_not_called()
        self.assertFalse(state_utils.repo_rebuilt(self.state, REPO))
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.state / f"last_seen_{REPO.replace('/', '_')}.txt").exists())
        send, process = self.run_watcher([SENT, SIBLING], entries=[self.entry(SENT)])
        process.assert_not_called()  # rebuilds again instead of replaying old commits
        send.assert_not_called()
        self.assertTrue(state_utils.repo_rebuilt(self.state, REPO))

    def test_rebuild_still_pending_after_pruning_empties_the_memory(self):
        expired_legacy_memory(self.seen_path)
        with mock.patch.object(watch_repo, "window_listings", side_effect=RuntimeError("HTTP 500")):
            send, _ = self.run_watcher([])
        send.assert_not_called()
        self.assertEqual(self.memory(), {})  # pruning removed every legacy entry
        send, process = self.run_watcher([SENT, SIBLING], entries=[self.entry(SENT), self.entry(SIBLING)])
        process.assert_not_called()  # the repo recovers: rebuild silently, do not alert
        send.assert_not_called()
        self.assertTrue(state_utils.repo_rebuilt(self.state, REPO))
        self.assertIn(state_utils.get_cache_key(SENT), self.memory())
        self.assertEqual(self.marker.read_text().strip(), str(state_utils.SEEN_KEY_VERSION))

    def run_watcher_on(self, github):
        send = mock.Mock(return_value=True)
        process = mock.Mock(side_effect=lambda repo, *a, **kw: [self.entry(OTHER_ITEM)] if repo == OTHER else [])
        with fake_github(github, head=self.HEAD), \
                mock.patch.object(watch_repo, "STATE_DIR", self.state), \
                mock.patch.object(watch_repo, "DRY_RUN", False), \
                mock.patch.object(watch_repo, "TARGET_REPOS", list(github)), \
                mock.patch.object(watch_repo, "process_repo_entries", process), \
                mock.patch.object(watch_repo, "send_telegram", send), \
                mock.patch.dict(os.environ, {"DM_METROS": '["SF Bay Area", "NYC"]'}):
            watch_repo.main()
        return send, process

    def test_missing_repo_is_a_fetch_failure_not_an_empty_repo(self):
        self.write_legacy_memory()
        # A fully missing repo also fails the commits fetch, which already kept it pending;
        # a 404 on the repo lookup alone is the case that counted it as empty
        send, _ = self.run_watcher_on({REPO: FILE_MISSING, OTHER: REPO_LOOKUP_404})
        send.assert_not_called()
        self.assertTrue(state_utils.repo_rebuilt(self.state, REPO))  # missing file: an empty repo
        self.assertFalse(state_utils.repo_rebuilt(self.state, OTHER))  # missing repo: retry next run
        self.assertFalse(self.marker.exists())
        send, process = self.run_watcher_on({REPO: FILE_MISSING, OTHER: [OTHER_ITEM]})  # the repo comes back
        self.assertEqual([c.args[0] for c in process.call_args_list], [REPO])  # OTHER rebuilds instead
        send.assert_not_called()
        self.assertTrue(state_utils.repo_rebuilt(self.state, OTHER))
        self.assertEqual(self.marker.read_text().strip(), str(state_utils.SEEN_KEY_VERSION))

    def test_failed_memory_save_keeps_the_repo_in_rebuild_mode(self):
        self.write_legacy_memory()
        with failing_seen_save():
            send, _ = self.run_watcher([SENT, SIBLING])
        send.assert_not_called()
        self.assertFalse(state_utils.repo_rebuilt(self.state, REPO))
        self.assertFalse(self.marker.exists())
        send, process = self.run_watcher([SENT, SIBLING], entries=[self.entry(SENT)])
        process.assert_not_called()
        send.assert_not_called()

    def test_save_reports_whether_it_wrote(self):
        self.assertTrue(state_utils.save_seen({"k": int(time.time())}, 14, str(self.seen_path)))
        self.assertFalse(state_utils.save_seen({"k": int(time.time())}, 14, str(self.seen_path), dry_run=True))
        with failing_seen_save():
            self.assertFalse(state_utils.save_seen({"k": int(time.time())}, 14, str(self.seen_path)))

    def test_save_leaves_no_temp_file(self):
        state_utils.save_seen({"k": int(time.time())}, 14, str(self.seen_path))
        self.assertEqual(sorted(p.name for p in self.state.iterdir()),
                         sorted([self.seen_path.name, self.marker.name]))


if __name__ == "__main__":
    unittest.main()

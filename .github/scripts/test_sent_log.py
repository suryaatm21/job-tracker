#!/usr/bin/env python3
"""
Tests for the sent-message log ([SENT] / [DRY-SENT] / [SENT-SUMMARY]) and its extractor.

Run: python -m unittest discover -s .github/scripts -p 'test_sent_log.py'
"""
import contextlib
import io
import json
import os
import pathlib
import sys
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import requests  # noqa: E402

import extract_sent_log  # noqa: E402
import telegram_utils  # noqa: E402

TOKEN = "123456789:AAFakeTokenForTestsOnly_abcdefghijk"
CHAT_ID = "-1001234567890"
GITHUB_ENV = {
    "GITHUB_RUN_ID": "987654321",
    "GITHUB_SHA": "fedcba9876543210fedcba9876543210fedcba98",
    "GITHUB_WORKFLOW": "Channel Digest (SWE/ML - BS)",
    "TELEGRAM_CHAT_LABEL": "swe",
}
# Emoji, HTML, quotes, backslashes and a U+2028 line separator in one listing line.
TEXT = ('🚨 <b>Acme &amp; Co</b> — "Software Engineer, New Grad" \\ C:\\path\n'
        '📍 <i>San Francisco, CA</i>\u2028<a href="https://example.com/apply?a=1&b=2">Apply</a>')


def fake_response(status, body):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    return response


class SentLogCase(unittest.TestCase):
    def setUp(self):
        patches = [
            mock.patch.dict(os.environ, GITHUB_ENV),
            mock.patch.object(telegram_utils, "_sent_counts", telegram_utils.Counter()),
            mock.patch.object(telegram_utils, "_failed_counts", telegram_utils.Counter()),
            mock.patch.object(telegram_utils, "_summary_registered", True),  # no atexit in tests
            mock.patch("time.sleep"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def send(self, dry, post=None, text=TEXT, **kwargs):
        """Call send_message; return (result, [record lines it printed])."""
        env = {"DRY_RUN": "true" if dry else "false"}
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(telegram_utils.requests, "post", post or mock.Mock()), \
                contextlib.redirect_stdout(out):
            result = telegram_utils.send_message(TOKEN, CHAT_ID, text, "HTML", **kwargs)
        lines = [l for l in out.getvalue().split("\n") if l]
        return result, lines

    def record(self, line, prefix):
        self.assertTrue(line.startswith(prefix + " "), line)
        return json.loads(line[len(prefix) + 1:])


class RecordFormatTest(SentLogCase):
    def test_live_success_is_one_valid_json_line_with_full_text(self):
        post = mock.Mock(return_value=fake_response(200, {"ok": True, "result": {"message_id": 77}}))
        (ok, _, _), lines = self.send(dry=False, post=post)
        self.assertTrue(ok)
        self.assertEqual(len(lines), 1)
        self.assertEqual(len(lines[0].splitlines()), 1)  # U+2028 is escaped, not raw
        rec = self.record(lines[0], "[SENT]")
        self.assertEqual(rec["text"], TEXT)
        self.assertEqual(rec["chars"], len(TEXT))
        self.assertEqual((rec["chat"], rec["ok"], rec["message_id"], rec["error"]), ("swe", True, 77, None))
        self.assertEqual((rec["run_id"], rec["sha"], rec["workflow"]),
                         (GITHUB_ENV["GITHUB_RUN_ID"], GITHUB_ENV["GITHUB_SHA"], GITHUB_ENV["GITHUB_WORKFLOW"]))
        self.assertIn("🚨", lines[0])  # emoji stay readable in the log

    def test_telegram_error_is_recorded(self):
        post = mock.Mock(return_value=fake_response(
            400, {"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities"}))
        (ok, _, _), lines = self.send(dry=False, post=post)
        self.assertFalse(ok)
        rec = self.record(lines[0], "[SENT]")
        self.assertEqual((rec["ok"], rec["message_id"]), (False, None))
        self.assertEqual(rec["error"], "400 Bad Request: can't parse entities")

    def test_dry_and_live_share_the_format(self):
        post = mock.Mock(return_value=fake_response(200, {"ok": True, "result": {"message_id": 1}}))
        _, live = self.send(dry=False, post=post)
        (ok, _, _), dry = self.send(dry=True, post=post)
        self.assertTrue(ok)
        self.assertEqual(post.call_count, 1)  # the dry send never reached Telegram
        live_rec, dry_rec = self.record(live[0], "[SENT]"), self.record(dry[0], "[DRY-SENT]")
        self.assertEqual(list(live_rec), list(dry_rec))
        self.assertEqual((dry_rec["ok"], dry_rec["dry_run"], live_rec["dry_run"]), (None, True, False))
        self.assertEqual(dry_rec["text"], live_rec["text"])

    def test_explicit_label_overrides_env_and_unset_is_unknown(self):
        _, lines = self.send(dry=True, chat_label="hardware")
        self.assertEqual(self.record(lines[0], "[DRY-SENT]")["chat"], "hardware")
        with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_LABEL": ""}):
            _, lines = self.send(dry=True)
        self.assertEqual(self.record(lines[0], "[DRY-SENT]")["chat"], "unknown")

    def test_batched_sends_log_every_message(self):
        post = mock.Mock(return_value=fake_response(200, {"ok": True, "result": {"message_id": 5}}))
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"DRY_RUN": "false"}), \
                mock.patch.object(telegram_utils.requests, "post", post), contextlib.redirect_stdout(out):
            ok, results = telegram_utils.batch_send_message(TOKEN, CHAT_ID, "Header", ["x" * 60] * 10,
                                                            max_chars=200, chat_label="quant")
        sent = [l for l in out.getvalue().split("\n") if l.startswith("[SENT] ")]
        self.assertTrue(ok)
        self.assertEqual(len(sent), len(results))
        self.assertGreater(len(sent), 1)
        self.assertEqual({json.loads(l[7:])["chat"] for l in sent}, {"quant"})


class NoSecretsTest(SentLogCase):
    def assert_clean(self, lines):
        for line in lines:
            self.assertNotIn(TOKEN, line)
            self.assertNotIn(TOKEN.split(":")[1], line)
            self.assertNotIn(CHAT_ID, line)
            self.assertNotIn(CHAT_ID.lstrip("-"), line)

    def test_success_failure_and_dry_lines_hold_no_token_or_chat_id(self):
        ok_post = mock.Mock(return_value=fake_response(200, {"ok": True, "result": {"message_id": 9}}))
        err_post = mock.Mock(return_value=fake_response(403, {"ok": False, "description": "Forbidden"}))
        for dry, post in ((False, ok_post), (False, err_post), (True, ok_post)):
            _, lines = self.send(dry=dry, post=post)
            self.assert_clean(lines)

    def test_request_exception_quoting_the_url_is_scrubbed(self):
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage?chat_id={CHAT_ID}"
        post = mock.Mock(side_effect=requests.ConnectionError(f"Max retries exceeded with url: {url}"))
        (ok, _, body), lines = self.send(dry=False, post=post)
        self.assertFalse(ok)
        self.assert_clean(lines)
        self.assertNotIn(TOKEN, body)  # the returned body is scrubbed too
        self.assertIn("[REDACTED]", self.record(lines[0], "[SENT]")["error"])

    def test_missing_credentials_is_logged_as_a_failure(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"DRY_RUN": "false"}), contextlib.redirect_stdout(out):
            ok, _, _ = telegram_utils.send_message("", CHAT_ID, "hi")
        self.assertFalse(ok)
        rec = self.record(out.getvalue().strip(), "[SENT]")
        self.assertEqual((rec["ok"], rec["error"]), (False, "Missing token or chat_id"))


class SummaryTest(SentLogCase):
    def test_one_summary_line_per_chat_with_counts(self):
        ok_post = mock.Mock(return_value=fake_response(200, {"ok": True, "result": {"message_id": 1}}))
        err_post = mock.Mock(return_value=fake_response(400, {"ok": False, "description": "Bad"}))
        self.send(dry=False, post=ok_post, chat_label="dm")
        self.send(dry=False, post=err_post, chat_label="dm")
        self.send(dry=False, post=ok_post, chat_label="pm")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            telegram_utils.print_sent_summary()
            telegram_utils.print_sent_summary()  # second call (atexit after an explicit call) prints nothing
        lines = [l for l in out.getvalue().split("\n") if l]
        summaries = {r["chat"]: r for r in (self.record(l, "[SENT-SUMMARY]") for l in lines)}
        self.assertEqual(len(lines), 2)
        self.assertEqual((summaries["dm"]["messages"], summaries["dm"]["failures"]), (2, 1))
        self.assertEqual((summaries["pm"]["messages"], summaries["pm"]["failures"]), (1, 0))
        self.assertEqual(summaries["dm"]["run_id"], GITHUB_ENV["GITHUB_RUN_ID"])


class ExtractorTest(SentLogCase):
    SAMPLE = HERE / "fixtures" / "sent_log_sample.txt"

    def extract(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            extract_sent_log.main(["--log-file", str(self.SAMPLE), *args])
        return [json.loads(l) for l in out.getvalue().split("\n") if l]

    def test_parses_gh_formatted_sample_log(self):
        records = self.extract()
        self.assertEqual([(r["ok"], r["message_id"]) for r in records], [(True, 4242), (False, None)])
        self.assertEqual({r["chat"] for r in records}, {"dm"})
        self.assertIn("<b>Example Co</b>", records[0]["text"])
        self.assertIn("\n📍 San Francisco", records[0]["text"])

    def test_include_dry_adds_dry_records_and_summaries_never_match(self):
        records = self.extract("--include-dry")
        self.assertEqual([r["dry_run"] for r in records], [False, False, True])
        self.assertTrue(all("messages" not in r for r in records))

    def test_gh_failure_exits_nonzero(self):
        with mock.patch.object(extract_sent_log, "_gh", return_value=None):
            self.assertEqual(extract_sent_log.main(["dm-fast-watch.yml"]), 1)

    def test_round_trip_from_the_live_logger(self):
        post = mock.Mock(return_value=fake_response(200, {"ok": True, "result": {"message_id": 3}}))
        _, lines = self.send(dry=False, post=post)
        log = "digest\tRun digest\t2026-11-11T00:00:00.0000000Z " + lines[0] + "\n"
        records = list(extract_sent_log.parse_log(log))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["text"], TEXT)


if __name__ == "__main__":
    unittest.main()

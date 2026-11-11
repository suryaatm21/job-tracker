#!/usr/bin/env python3
"""
Print the bot's sent-message records from workflow run logs as JSONL.

Every Telegram message goes through telegram_utils.send_message, which logs one
"[SENT] {json}" line (or "[DRY-SENT] {json}" under DRY_RUN). This script reads
those lines back out of GitHub Actions logs with the gh CLI, one JSON object
per output line, newest run first.

Usage:
  python .github/scripts/extract_sent_log.py dm-fast-watch.yml            # last 5 runs
  python .github/scripts/extract_sent_log.py channel-digest.yml --limit 20
  python .github/scripts/extract_sent_log.py channel-digest-private.yml --include-dry
  python .github/scripts/extract_sent_log.py --log-file saved_run.log     # parse a saved log

Needs an authenticated gh. Runs still in progress, or whose logs are gone, are
skipped with a note on stderr.
"""
import argparse
import json
import re
import subprocess
import sys
from typing import Iterable, Iterator, List, Optional

# Matches anywhere in the line: gh prefixes each log line with "job<TAB>step<TAB>timestamp ".
# The literal "] " after the tag keeps "[SENT-SUMMARY]" lines out.
RECORD_RE = re.compile(r"\[(SENT|DRY-SENT)\] (\{.*\})\s*$")


def parse_log(text: str, include_dry: bool = False) -> Iterator[dict]:
    """Yield each sent-message record found in a run log."""
    # Split on "\n" only: str.splitlines() also breaks on U+2028 and friends.
    for line in text.split("\n"):
        match = RECORD_RE.search(line)
        if not match:
            continue
        if match.group(1) == "DRY-SENT" and not include_dry:
            continue
        try:
            yield json.loads(match.group(2))
        except ValueError:
            print(f"[extract] skipped unparseable record: {line[:120]}", file=sys.stderr)


def _gh(args: List[str]) -> Optional[str]:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        print(f"[extract] gh {' '.join(args)} failed: {proc.stderr.strip()[:200]}", file=sys.stderr)
        return None
    return proc.stdout


def recent_run_ids(workflow: str, limit: int, repo: Optional[str]) -> List[int]:
    args = ["run", "list", "--workflow", workflow, "--limit", str(limit), "--json", "databaseId"]
    if repo:
        args += ["--repo", repo]
    out = _gh(args)
    return [run["databaseId"] for run in json.loads(out)] if out else []


def run_log(run_id: int, repo: Optional[str]) -> Optional[str]:
    args = ["run", "view", str(run_id), "--log"]
    if repo:
        args += ["--repo", repo]
    return _gh(args)


def emit(records: Iterable[dict]) -> int:
    count = 0
    for record in records:
        print(json.dumps(record, ensure_ascii=False))
        count += 1
    return count


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("workflow", nargs="?", help="workflow file name or name, e.g. dm-fast-watch.yml")
    parser.add_argument("--limit", type=int, default=5, help="number of recent runs to read (default 5)")
    parser.add_argument("--repo", help="OWNER/REPO (default: the repo gh resolves from the current directory)")
    parser.add_argument("--include-dry", action="store_true", help="also print [DRY-SENT] records")
    parser.add_argument("--log-file", help="parse a saved log file instead of calling gh")
    args = parser.parse_args(argv)

    if args.log_file:
        with open(args.log_file, encoding="utf-8") as f:
            emit(parse_log(f.read(), args.include_dry))
        return 0
    if not args.workflow:
        parser.error("give a workflow, or --log-file")

    for run_id in recent_run_ids(args.workflow, args.limit, args.repo):
        log = run_log(run_id, args.repo)
        if log is None:
            continue
        found = emit(parse_log(log, args.include_dry))
        print(f"[extract] run {run_id}: {found} record(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

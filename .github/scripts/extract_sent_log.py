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

Needs an authenticated gh. Exits 1 if gh cannot list the runs. Runs still in
progress, or whose logs are gone, are skipped with a note on stderr.
"""
import argparse
import json
import re
import subprocess
import sys
from typing import Iterable, Iterator, List, Optional, Tuple

# Matches anywhere in the line: gh prefixes each log line with "job<TAB>step<TAB>timestamp ".
# The literal "] " after the tag keeps "[SENT-SUMMARY]" lines out.
RECORD_RE = re.compile(r"\[(SENT|DRY-SENT)\] (\{.*\})\s*$")
# A dry run prints each message raw between these markers (telegram_utils.send_message),
# so a line of message text there can look like a record.
BEGIN_MESSAGE = "----- BEGIN MESSAGE -----"
END_MESSAGE = "----- END MESSAGE -----"


def _decode(line: str) -> Optional[Tuple[bool, Optional[dict]]]:
    """(dry, record) for a tagged line; record is None when it is not a well-formed sent record.

    None for a line with no record tag.
    """
    match = RECORD_RE.search(line)
    if not match:
        return None
    dry = match.group(1) == "DRY-SENT"
    try:
        record = json.loads(match.group(2))
    except ValueError:
        return dry, None
    if not isinstance(record, dict) or record.get("dry_run") is not dry or not isinstance(record.get("text"), str):
        return dry, None
    return dry, record


def _block_end(lines: List[str], begin: int) -> int:
    """Index of the line closing the dry-run message block that opens at lines[begin].

    The block closes at the END marker followed by the [DRY-SENT] record whose
    text is exactly the lines in between, so message text holding its own END
    marker cannot close it early. Without such a record (an edited or cut log)
    the first END marker closes it, or the log's end.
    """
    first_end = None
    for j in range(begin + 1, len(lines)):
        if not lines[j].rstrip().endswith(END_MESSAGE):
            continue
        first_end = j if first_end is None else first_end
        decoded = _decode(lines[j + 1]) if j + 1 < len(lines) else None
        if decoded and decoded[0] and decoded[1]:
            body = decoded[1]["text"].split("\n")
            shown = lines[begin + 1:j]
            if len(body) == len(shown) and all(l.endswith(b) for l, b in zip(shown, body)):
                return j
    return first_end if first_end is not None else len(lines) - 1


def parse_log(text: str, include_dry: bool = False) -> Iterator[dict]:
    """Yield each sent-message record found in a run log.

    Lines inside a dry run's readable message block are skipped, and a tagged
    line counts only when it decodes to a record whose dry_run flag agrees with its tag.
    """
    # Split on "\n" only: str.splitlines() also breaks on U+2028 and friends.
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.rstrip().endswith(BEGIN_MESSAGE):
            i = _block_end(lines, i) + 1
            continue
        i += 1
        decoded = _decode(line)
        if decoded is None or (decoded[0] and not include_dry):
            continue
        if decoded[1] is None:
            print(f"[extract] skipped line that is not a sent record: {line[:120]}", file=sys.stderr)
            continue
        yield decoded[1]


def _gh(args: List[str]) -> Optional[str]:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        print(f"[extract] gh {' '.join(args)} failed: {proc.stderr.strip()[:200]}", file=sys.stderr)
        return None
    return proc.stdout


def recent_run_ids(workflow: str, limit: int, repo: Optional[str]) -> Optional[List[int]]:
    """Newest-first run ids, or None when gh could not list them."""
    args = ["run", "list", "--workflow", workflow, "--limit", str(limit), "--json", "databaseId"]
    if repo:
        args += ["--repo", repo]
    out = _gh(args)
    return None if out is None else [run["databaseId"] for run in json.loads(out)]


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

    run_ids = recent_run_ids(args.workflow, args.limit, args.repo)
    if run_ids is None:
        return 1  # gh failed: an empty result must not look like "no messages sent"
    for run_id in run_ids:
        log = run_log(run_id, args.repo)
        if log is None:
            continue
        found = emit(parse_log(log, args.include_dry))
        print(f"[extract] run {run_id}: {found} record(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Job Tracker Testing Guide

## Overview

This guide outlines how to manually test the job tracker workflows to ensure multi-repository support, deduplication, and notification delivery work as expected.

## Spec and automated checks

`spec/SPEC.md` lists the bot's rules in plain words, each with an id such as `SEEN-3`. The property tests in `spec/test_*_props.py` check the code against each rule over many generated inputs ([Hypothesis](https://hypothesis.readthedocs.io/)), and their names carry the rule id. A test for a rule the code breaks today is marked `xfail(strict=True, reason="<id>")`: it must keep failing, so the PR that fixes the bug also removes the marker. `spec/test_spec_index.py` checks that every rule has a test and that the markers match the rules SPEC.md calls known bugs.

Run everything locally (no network, nothing sent to Telegram):

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
export DRY_RUN=true HYPOTHESIS_PROFILE=ci   # ci: fixed search order, same inputs as CI
.venv/bin/python -m unittest discover -s .github/scripts -p 'test_*.py'
.venv/bin/python -m pytest spec -p no:cacheprovider -rxXf
PYTHON=.venv/bin/python bash spec/run_mutants.sh          # or: ... run_mutants.sh M3 M8
```

**Planted bugs.** `spec/mutants/` holds one-line bugs as patches, listed in `spec/mutants/manifest.txt`. `spec/run_mutants.sh` first checks that both suites pass on unchanged code, then applies each patch, runs both suites, requires a failure, and restores the file (it verifies the restore). An `active` mutant that survives fails the run, which is how a weakened property gets noticed. A `pending` mutant cannot be caught until a known bug is fixed (none today); if it is caught anyway the run fails, so the fix marks it `active`. A patch that no longer applies fails as stale: regenerate it against the current code. An id given on the command line that is not in the manifest fails the run before anything is checked, so a typo cannot report success.

**CI.** `.github/workflows/spec.yml` runs on every pull request and on pushes to `main`, with `DRY_RUN=true` and no Telegram secrets. Job `spec` runs both suites; job `planted-bugs` runs the mutant check.

**Spec lock.** `.claude/settings.json` registers a Claude Code PreToolUse hook (`.claude/hooks/spec_lock.py`) that denies Edit, Write and MultiEdit on `spec/**` and `.github/workflows/spec.yml`. A task may unlock the spec only when its instructions explicitly say so. To unlock, create an empty `.spec-unlock` file at the repository root (it is gitignored, so it unlocks only that checkout), and delete it when the task is done. The hook guards against accidents only: it does not see shell commands, and CI is what catches a weakened spec.

## Prerequisites

Ensure GitHub repository secrets are configured:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID` (for DMs)
- `TELEGRAM_CHAT_ID_CHANNEL` (for channel broadcasts)
- Additional channel secrets as defined in `GITHUB_SECRETS_SETUP.md`

## Test Cases

### 1. DM Fast Watcher (Multi-repo with deduplication)

**Manual Test:**

```bash
# Go to Actions → "DM fast watcher" → Run workflow
# Choose:
# - Branch: main
# - Command: latest (or today/recent)
```

**Expected Results:**

- Script processes configured repos (e.g. SimplifyJobs and vanshb03)
- Detects default branches and listings paths automatically
- Deduplicates listings across repos using: `normalized_url` → `id` → `(company.lower(), title.lower())` (URL first: repos reuse ids; see `dedup_utils.get_dedup_key` and SPEC rule DEDUP-4)
- Sends single consolidated message to DM chat
- Logs show: "Starting multi-repo watch for: [...]", "Found listings file at...", "After deduplication: X unique entries"

**Validation:**

- Check Telegram DM for message
- Should see entries from both repos if available
- No duplicate companies/positions even if they exist in both repos

### 2. Channel Digest (Multi-repo)

**Manual Test:**

```bash
# Go to Actions → "Channel Digest (SWE/ML - BS)" → Run workflow
# Choose:
# - Branch: main
# - Command: digest
# - Count: 20 (optional)
```

**Expected Results:**

- Script fetches listings from repositories
- Filters to entries within the time window (default 24h)
- Deduplicates across repos
- Sends consolidated digest to channel
- Logs show: "Generating digest for repos: [...]", "Found X listings in...", "After deduplication: X unique entries"

**Validation:**

- Check Telegram channel for digest message
- Header should indicate time window
- Should contain mix of entries from both repos (if available in window)

### 3. Manual Commands

**Manual Test:**

```bash
# Go to Actions → "DM fast watcher" → Run workflow
# Choose:
# - Branch: main
# - Command: recent
# - Count: 5
```

**Expected Results:**

- Sends the 5 most recent listings to your DM
- Useful for verifying bot connectivity and listing fetching

### 4. Path Detection Robustness

**Expected Behavior:**

- Scripts try `LISTINGS_PATH` first (default `.github/scripts/listings.json`)
- Fallback to `.github/scripts/listings.json`, then `listings.json`
- Log messages show: "Found listings file at X in repo"

### 5. State Management & Caching

**Expected Behavior:**

- Uses Actions cache for state persistence
- Separate last-seen SHA per repository
- Cache keys use weekly prefixes (e.g., `dm-watcher-state-v1-2025-W40-...`)
- State files stored in `.state/` directory

### 6. Private Job Tracker Digest

**Offline tests** (no network, no Telegram; covers the markdown reader, location, pay and role gates, tiers, scoring, the Simplify profile cache, and Supra's Instagram feed including the missing-file case):

```bash
python -m unittest discover -s .github/scripts -p 'test_private_digest.py'
```

The suite also dry-runs cross-board copies of one posting (a zapply wrapper link next to the direct Greenhouse, Ashby or Workday link) from `fixtures/cross_board_zapply.md` and `fixtures/cross_board_listings.json`; each posts once.

**Dry run against live data** (sends nothing and leaves `seen.json` untouched; prints per-source/per-level counts after each stage, drops per gate, pay coverage, and the rendered messages):

```bash
export GH_TOKEN="your_token"
DRY_RUN=true STATE_DIR=/tmp/private-digest-state DRY_RUN_REPORT=/tmp/private-digest.json \
  python .github/scripts/send_private_digest.py
# Wider sample: add FORCE_WINDOW_HOURS=168 MAX_PROFILE_LOOKUPS=250
```

In Actions, a manual run of "Channel Digest (Private - Job Tracker)" defaults to `dry_run=true`.

## Validation Commands

### Check logs in Actions:

Look for these log patterns:
- "Starting multi-repo watch for: [...]"
- "Processing repo: SimplifyJobs/Summer2027-Internships"
- "Found X listings in SimplifyJobs/Summer2027-Internships"
- "Found X entries before deduplication"
- "After deduplication: X unique entries"
- "Sending message with X lines" / "Sending digest with X entries"

### Dry run locally against live data (sends nothing):

`DRY_RUN=true` makes every send path print the Telegram message (a `BEGIN/END MESSAGE` block plus a `[DRY-SENT]` record, see [Sent-message log](#sent-message-log)) instead of sending it, and skips saving the seen cache and last-seen SHAs. Unset the Telegram credentials too, so a send path that missed the switch still cannot reach Telegram.

```bash
# Set environment variables:
export DRY_RUN=true
unset TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID
export TARGET_REPOS='["SimplifyJobs/Summer2027-Internships","vanshb03/Summer2027-Internships"]'
export LISTINGS_PATH=".github/scripts/listings.json"
export DATE_FIELD="date_posted"
export WINDOW_HOURS="24"
export STATE_DIR="$(mktemp -d)"   # fresh state = what a first run after a long pause sees
export GH_TOKEN="$(gh auth token)" # unauthenticated API calls hit the 60/hour limit fast

# Channel digest (set DIGEST_CATEGORIES / FILTER_GRADUATE_DEGREES from the channel's workflow env):
python .github/scripts/send_digest_multi.py

# DM watcher (fresh state scans the last 20 commits, so expect several large downloads):
export WATCH_PATHS='["listings.json", ".github/scripts/listings.json"]'
export DM_METROS='["SF Bay Area", "NYC"]'  # required; metro labels from .github/private_channel/config.json
python .github/scripts/watch_repo.py
```

### Category routing tests (no network):

```bash
python -m unittest discover -s .github/scripts -p 'test_category_routing.py'
```

Proves Simplify's `Hardware`/`Product`/`Quant` labels reach the matching role digests and never reach DM alerts. Needs `requests` installed (imported by `github_helper`).

### DM location gate tests (no network):

```bash
python -m unittest discover -s .github/scripts -p 'test_dm_location_gate.py'
```

Proves DM alerts keep only SF Bay Area and NYC listings (a listing passes when any of its locations matches), drop remote-only and location-less listings, log drop counts per reason, and that the channel digest still keeps listings from other cities.

### Manual DM command tests (no network):

```bash
python -m unittest discover -s .github/scripts -p 'test_manual_dm_commands.py'
```

Proves the manual `latest`, `today` and `recent` scripts send only SF Bay Area and NYC listings when `DM_METROS` is set (the DM workflow), apply `COUNT` after the gate, send their usual "nothing found" message when no listing passes, and send listings from every location when `DM_METROS` is unset (the channel digests).

### Sent-message log

Every Telegram message, from every workflow and manual script, goes through `send_message` in `.github/scripts/telegram_utils.py`, which writes one line per message to the run log:

```text
[SENT] {"chat": "dm", "dry_run": false, "ok": true, "message_id": 4242, "error": null, "chars": 92, "run_id": "1234567890", "sha": "0123…", "workflow": "DM fast watcher", "ts": "2026-11-11T10:12:32+00:00", "text": "🚨 <b>Example Co</b> — …"}
```

- `chat` is the workflow's `TELEGRAM_CHAT_LABEL` (`dm`, `swe`, `quant`, `pm`, `hardware`, `private`, `testing`), never the chat id; `unknown` if unset.
- `ok`/`message_id` come from Telegram's response; on failure `ok` is `false` and `error` holds Telegram's description (token and chat id redacted).
- `run_id`, `sha` and `workflow` come from `GITHUB_RUN_ID`, `GITHUB_SHA` and `GITHUB_WORKFLOW`; `text` is the full message.
- Under `DRY_RUN` the line starts `[DRY-SENT]` with `dry_run: true` and `ok: null`; the keys are the same.
- Each script also prints one `[SENT-SUMMARY] {"chat": …, "messages": N, "failures": N, …}` line per chat when it exits. A workflow with several send steps prints one per step; group by `run_id` to total them.

**Auditing live output.** `extract_sent_log.py` reads recent runs of one workflow with `gh` and prints the records as JSONL, newest run first:

```bash
python .github/scripts/extract_sent_log.py dm-fast-watch.yml --limit 10 > sent.jsonl
python .github/scripts/extract_sent_log.py channel-digest-private.yml --include-dry   # include dry runs
python .github/scripts/extract_sent_log.py --log-file run.log                        # a saved `gh run view --log`

# Failed sends, and the last 3 messages to a chat:
jq -c 'select(.ok == false) | {run_id, chat, error}' sent.jsonl
jq -rs 'map(select(.chat == "dm")) | sort_by(.ts) | .[-3:][] | .text' sent.jsonl
```

To check for a regression, diff the `text` of new records against an earlier run's, or look for failed sends and unexpected `chars` or empty fields. Logs exist only for runs GitHub still retains, and runs in progress are skipped.

**Tests** (no network): valid JSON with emoji and HTML, no token or chat id in any line, dry and live share the format, the summary counts, and the extractor parses `fixtures/sent_log_sample.txt`:

```bash
python -m unittest discover -s .github/scripts -p 'test_sent_log.py'
```

## Success Criteria

1. ✅ Workflows run successfully
2. ✅ Manual commands work and send messages to correct Telegram destinations
3. ✅ Logs show multi-repo processing and deduplication
4. ✅ State management works via Actions cache


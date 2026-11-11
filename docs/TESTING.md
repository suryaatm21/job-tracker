# Job Tracker Testing Guide

## Overview

This guide outlines how to manually test the job tracker workflows to ensure multi-repository support, deduplication, and notification delivery work as expected.

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
- Deduplicates listings across repos using: `id` → `normalized_url` → `(company.lower(), title.lower())`
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

**Offline tests** (no network, no Telegram; covers the markdown reader, location and pay gates, tiers, scoring, the Simplify profile cache, and Supra's Instagram feed including the missing-file case):

```bash
python -m unittest discover -s .github/scripts -p 'test_private_digest.py'
```

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

`DRY_RUN=true` makes every send path print the Telegram message instead of sending it, and skips saving the seen cache and last-seen SHAs. Unset the Telegram credentials too, so a send path that missed the switch still cannot reach Telegram.

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

## Success Criteria

1. ✅ Workflows run successfully
2. ✅ Manual commands work and send messages to correct Telegram destinations
3. ✅ Logs show multi-repo processing and deduplication
4. ✅ State management works via Actions cache


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

## Validation Commands

### Check logs in Actions:

Look for these log patterns:
- "Starting multi-repo watch for: [...]"
- "Processing repo: SimplifyJobs/Summer2026-Internships"
- "Found X listings in SimplifyJobs/Summer2026-Internships"
- "Found X entries before deduplication"
- "After deduplication: X unique entries"
- "Sending message with X lines" / "Sending digest with X entries"

### Test deduplication locally (optional):

```bash
# Set environment variables:
export TARGET_REPOS='["SimplifyJobs/Summer2026-Internships","vanshb03/Summer2026-Internships"]'
export LISTINGS_PATH=".github/scripts/listings.json"
export DATE_FIELD="date_posted"
export WINDOW_HOURS="24"
export GH_TOKEN="your_token"

# Run digest script:
python .github/scripts/send_digest_multi.py
```

## Success Criteria

1. ✅ Workflows run successfully
2. ✅ Manual commands work and send messages to correct Telegram destinations
3. ✅ Logs show multi-repo processing and deduplication
4. ✅ State management works via Actions cache


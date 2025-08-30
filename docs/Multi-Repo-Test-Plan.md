# Multi-Repo Job Tracker Test Plan

## Overview
Test the multi-repository support with deduplication across SimplifyJobs/Summer2026-Internships and vanshb03/Summer2026-Internships.

## Prerequisites
1. Create a feature branch: `git checkout -b feature/multi-repo-support`
2. Ensure GitHub repository secrets are configured:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID` (for DMs)
   - `TELEGRAM_CHAT_ID_CHANNEL` (for channel broadcasts)

## Test Cases

### 1. DM Fast Watcher (Multi-repo with deduplication)

**Manual Test:**
```bash
# Go to Actions → "DM fast watcher" → Run workflow
# On feature branch, choose:
# - Branch: feature/multi-repo-support  
# - Command: latest (or today/recent)
```

**Expected Results:**
- Script processes both repos: SimplifyJobs/Summer2026-Internships and vanshb03/Summer2026-Internships
- Detects default branches and listings paths automatically
- For watcher mode: Creates separate state files (.state/last_seen_SimplifyJobs_Summer2026-Internships.txt, .state/last_seen_vanshb03_Summer2026-Internships.txt)
- Deduplicates listings across repos using: id → normalized_url → (company.lower(), title.lower())
- Sends single consolidated message to DM chat
- Logs show: "Starting multi-repo watch for: [...]", "Found listings file at...", "After deduplication: X unique entries"

**Validation:**
- Check Telegram DM for message
- Should see entries from both repos if available
- No duplicate companies/positions even if they exist in both repos

### 2. Channel Digest (Multi-repo 8h window)

**Manual Test:**
```bash
# Go to Actions → "Channel digest (8h)" → Run workflow  
# On feature branch, choose:
# - Branch: feature/multi-repo-support
# - Command: digest
# - Count: 20 (optional)
```

**Expected Results:**
- Script fetches listings from both repositories at default branch
- Filters to entries within last 8 hours based on date_posted/date_updated
- Deduplicates across repos
- Sends consolidated digest to channel
- Logs show: "Generating digest for repos: [...]", "Found X listings in...", "After deduplication: X unique entries"

**Validation:**
- Check Telegram channel for digest message
- Header should indicate time window: "New internships detected in last 8h (X)"
- Should contain mix of entries from both repos (if available in 8h window)

### 3. Single-repo Manual Commands (Backward Compatibility)

**Manual Test:**
```bash
# Go to Actions → "Channel digest (8h)" → Run workflow
# On feature branch, choose:
# - Branch: feature/multi-repo-support  
# - Command: latest (or today/recent)
```

**Expected Results:**
- Manual commands (latest/today/recent) still work with single repo (vanshb03/Summer2026-Internships)
- Send to channel using existing scripts
- No breaking changes to existing functionality

### 4. Path Detection Robustness

**Expected Behavior:**
- Scripts try LISTINGS_PATH first (.github/scripts/listings.json)
- Fallback to .github/scripts/listings.json, then listings.json
- Log messages show: "Found listings file at X in repo"
- Graceful handling if file not found in any location

### 5. State Management (DM Watcher)

**Expected Behavior:**
- Uses Actions cache instead of git commits for state
- Separate last-seen SHA per repository
- Cache key: "last-seen-multi-v1"
- State files: .state/last_seen_SimplifyJobs_Summer2026-Internships.txt, .state/last_seen_vanshb03_Summer2026-Internships.txt

## Validation Commands

### Check logs in Actions:
```bash
# Look for these log patterns:
- "Starting multi-repo watch for: [...]"  
- "Processing repo: SimplifyJobs/Summer2026-Internships"
- "Found listings file at .github/scripts/listings.json in SimplifyJobs/Summer2026-Internships"
- "Found X listings in SimplifyJobs/Summer2026-Internships" 
- "Found X entries before deduplication"
- "After deduplication: X unique entries"
- "Sending message with X lines" / "Sending digest with X entries"
```

### Test deduplication locally (optional):
```bash
# Set environment variables:
export TARGET_REPOS='["SimplifyJobs/Summer2026-Internships","vanshb03/Summer2026-Internships"]'
export LISTINGS_PATH=".github/scripts/listings.json"
export DATE_FIELD="date_posted"
export WINDOW_HOURS="8"
export GH_TOKEN="your_token"

# Run digest script:
python .github/scripts/send_digest_multi.py

# Expected output:
# - "Generating digest for repos: ['SimplifyJobs/Summer2026-Internships', 'vanshb03/Summer2026-Internships']"
# - Processing logs for each repo
# - Deduplication summary
```

## Success Criteria

1. ✅ Both workflows run successfully on feature branch
2. ✅ No scheduled jobs run (confirmed via Actions tab)
3. ✅ Manual commands work and send messages to correct Telegram destinations
4. ✅ Logs show multi-repo processing and deduplication
5. ✅ State management works via Actions cache
6. ✅ Existing single-repo manual commands remain functional
7. ✅ No duplicate listings in output even if present in both source repos

## Merge Criteria

Only merge to main after:
1. All test cases pass on feature branch
2. Telegram messages received successfully
3. No errors in Actions logs
4. Deduplication confirmed working (verify no obvious duplicates in output)

## Post-Merge Verification

After merging to main:
1. Verify scheduled jobs resume (every 5 min for DM, every 8h for channel)
2. Monitor first few runs for any issues
3. Check state persistence across runs
4. Confirm deduplication working in production

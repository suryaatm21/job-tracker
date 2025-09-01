# Project Plan: Job Tracker

**Owner:** Surya  
**Last Updated:** 2025-09-01

---

## **Current Status & Completed Work**

This document outlines the development status of the Job Tracker project. The following major features have been implemented and validated.

### 1. **GitHub Actions Cache Conflict Resolution**
- **Problem:** The `dm-fast-watch` (5-min interval) and `channel-digest` (4-hour interval) workflows were using the same cache key, causing "Unable to reserve cache" errors when they ran simultaneously.
- **Solution:** A robust, separated cache architecture was implemented.
  - **Separate State Directories:** Workflows now use distinct state directories (`.state/dm-watcher` and `.state/channel-digest`).
  - **Unique Cache Keys:** Each workflow has a unique cache key (`dm-watcher-state-v1` and `channel-digest-state-v1`).
- **Outcome:** Both workflows can now run concurrently without cache conflicts, as confirmed by the presence of two separate cache entries in the repository's Actions cache.

### 2. **Location-Aware Job Notifications**
- **Problem:** Job notifications lacked location context, making it hard to gauge relevance at a glance.
- **Solution:** A centralized formatting utility was created and integrated across all notification scripts.
  - **Helper Module:** A new script, `.github/scripts/format_utils.py`, was created to handle all location and message formatting logic.
  - **Smart Location Formatting:** The `format_location()` function provides context-aware location details:
    - **DM Watcher (`dm` mode):** For listings with multiple locations, it intelligently resolves to "California," "New York," or "New Jersey" if relevant keywords are found. Otherwise, it defaults to "Multi-location."
    - **Channel Digest (`digest` mode):** Always displays "Multi-location" for listings with more than one location to keep the digest concise.
  - **Consistent Output:** The `format_job_line()` function ensures all notifications follow a standard format, e.g., `• Company — Title [Season | Location] URL`.
- **Integration:** The new formatting logic was successfully integrated into `watch_repo.py`, `send_digest_multi.py`, and all manual-run scripts (`send_latest_listing.py`, `send_todays_listings.py`, `send_recent_listings.py`).

### 3. **Comprehensive Testing**
- **Unit & Integration Tests:** Created test scripts (`test_location_formatting.py`, `test_comprehensive_location.py`) to validate the new formatting logic against various edge cases, ensuring its correctness and stability.

---

## **Next Steps for Implementation**

The following tasks are planned for the next development cycle.

### 1. **Remove Source Attribution Tags from Notifications**
- **Task:** The current notification format includes a source tag (e.g., `[SimplifyJobs]`). This is redundant and adds clutter to the messages.
- **Plan:**
  1. Modify the `format_job_line` function in `.github/scripts/format_utils.py`.
  2. Remove the `repo_author` variable and its corresponding `[{repo_author}]` tag from the final formatted string for both HTML and plain text outputs.
- **Goal:** To simplify the notification message and improve readability.

### 2. **Fix Cache Save Warning in DM Watcher**
- **Task:** The `dm-fast-watch` workflow log sometimes shows a `Warning: Cache save failed` message. While the primary cross-workflow conflict is resolved, a race condition can still occur if a workflow run takes longer than its 5-minute interval, causing an overlap with the next run.
- **Plan:**
  1. In `.github/workflows/dm-fast-watch.yml`, modify the "Save watcher state" step.
  2. Change the cache `key` to be dynamic for saving, incorporating the run ID: `key: dm-watcher-state-v1-${{ github.run_id }}`.
  3. In the "Restore watcher state" step, add a `restore-keys` entry to fall back to the most recent cache: `restore-keys: | dm-watcher-state-v1-`.
- **Goal:** Eliminate cache write conflicts entirely by ensuring each workflow run saves to a unique key, while still allowing subsequent runs to restore the most recently saved state.

---

## **NEXT_STEPS.md**
```markdown
# Next Steps: Fixing Missing Update Notifications

## 1. Verify Change Detection
- Confirm that the GitHub Action actually detects changes in `listings.json`:
  - Add a debug step to print the diff result.
  - Run the action manually after making a known change.

## 2. Check File Path & Branch
- Ensure `TARGET_FILE` matches the exact path and case of the file in the repo.
- Verify `BRANCH` is correct (e.g., `main`, `master`).

## 3. Validate Diff Logic
- Review script: ensure it triggers a notification only when there’s a **real difference**.
- Possible bug: data may be normalized/cleaned before comparison, hiding small changes.

## 4. GitHub Action Scheduling
- Confirm cron schedule is in UTC and actually running.
- Check Actions logs for skipped runs or errors.

## 5. Telegram API
- Manual test already works (`hello from api`).
- Add error logging when sending messages in the workflow:
  - Print HTTP status and Telegram API response.

## 6. State Tracking
- If the bot stores a cached copy of the file (in `.state/` or elsewhere), make sure:
  - The state file path is correct.
  - The workflow commits or persists it properly.
  - `.state/` is not ignored incorrectly by `.gitignore`.

## 7. Potential Improvements
- **Batch changes**: send a summary of all updates since last run.
- **Selective updates**: only alert on *new listings*, not edits.
- **Configurable message templates** for easier formatting.

## Immediate Action Plan
1. Add debug logging for:
   - File fetch result.
   - Diff content.
   - Telegram API response.
2. Trigger workflow with a known `listings.json` change to see if the notification fires.
3. If detection works but message still fails, inspect Telegram send code for conditional skips.

---

**Owner:** Surya  
**Last Updated:** YYYY-MM-DD
````

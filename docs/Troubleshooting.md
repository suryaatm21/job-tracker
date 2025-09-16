# Job Tracker Troubleshooting Guide

## Quick Verification Steps

### 1. Test Telegram Bot Locally

```bash
# Set your environment variables (replace with actual values)
export TELEGRAM_BOT_TOKEN="your_bot_token_here"
export TELEGRAM_CHAT_ID="your_chat_id_here"

# Run the test script
python .github/scripts/test_telegram.py
```

### 2. Manual Workflow Trigger

- Go to GitHub Actions tab in your repository
- Click on "Watch Summer2026 repo" workflow
- Click "Run workflow" button
- Check the logs for debug output

### 3. GitHub Secrets Verification

Make sure these secrets are set in your repository settings:

- `TELEGRAM_BOT_TOKEN`: Your bot token from @BotFather
- `TELEGRAM_CHAT_ID`: Your user ID from @userinfobot

## Common Issues & Solutions

### Issue: "No commits found"

**Cause**: GitHub API rate limiting or repository access issues
**Solution**:

- Check if the target repository exists: `vanshb03/Summer2026-Internships`
- Verify GitHub token has proper permissions

### Issue: "Missing Telegram credentials"

**Cause**: Secrets not properly configured
**Solution**:

1. Go to GitHub repo → Settings → Secrets and variables → Actions
2. Add/update `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`

### Issue: "JSON parse failed"

**Cause**: `listings.json` file format issues in target repo
**Solution**: Check the target repository's `listings.json` file format

### Issue: State file conflicts

**Cause**: `.state/` directory was previously in `.gitignore`
**Solution**:

- The state directory is now tracked
- Old state files may need to be manually created

## Debug Output Examples

### Successful Run

```
[2025-01-09T10:30:00] DEBUG: Starting watch for repo: vanshb03/Summer2026-Internships
[2025-01-09T10:30:00] DEBUG: Found 15 recent commits
[2025-01-09T10:30:01] DEBUG: Processing 3 new commits
[2025-01-09T10:30:02] DEBUG: New listings detected: 2
[2025-01-09T10:30:03] DEBUG: Sending 1 messages
```

### No Changes

```
[2025-01-09T10:30:00] DEBUG: Starting watch for repo: vanshb03/Summer2026-Internships
[2025-01-09T10:30:00] DEBUG: No new commits since last run
```

## Force Reset Instructions

If you need to reset the tracker:

```bash
# Delete the state file to start fresh
rm -rf .state/
git add .state/
git commit -m "Reset tracker state"
git push
```

## Monitoring Tips

1. **Check GitHub Actions regularly** for failed runs
2. **Watch for rate limits** - GitHub API has limits
3. **Verify target repo activity** - make sure there are actually changes happening
4. **Test notifications manually** using the workflow dispatch feature

## Getting Your Telegram IDs

### Bot Token

1. Message @BotFather on Telegram
2. Create a new bot: `/newbot`
3. Follow the instructions
4. Copy the token provided

### Chat ID

1. Message @userinfobot on Telegram
2. It will reply with your user ID
3. Use this number as your `TELEGRAM_CHAT_ID`
## Duplicate messages on consecutive digest runs

Symptoms:
- Second digest run sends the same items as the first.

Causes and fixes:
- Using the tuple returned by `should_alert_item` as a boolean will always evaluate truthy.
  - Fix: unpack the tuple: `flag, reason = should_alert_item(...); if flag:`
- Items were marked seen before ensuring send success.
  - Fix: mark as seen only after Telegram send returns success.

## Cache cleanup removed too many caches

Symptoms:
- After scheduled cleanup, unrelated caches are gone; builds rebuild dependencies.

Causes and fixes:
- Scheduled run lacked `workflow_dispatch` inputs; empty `KEEP_PREFIXES` matched everything.
  - Fix: provide defaults in job env and guard against empty `KEEP_PREFIXES`.

## Cache count higher than KEEP_N after cleanup

Symptoms:
- You see 4 caches for a prefix when KEEP_N=3.

Likely cause:
- A writer workflow (e.g., DM watcher) created a new cache during cleanup execution.

Mitigations:
- Add a shared `concurrency` group across state-touching workflows.
- Run cleanup during a quiet window or re-run cleanup immediately.

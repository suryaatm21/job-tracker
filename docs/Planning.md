
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

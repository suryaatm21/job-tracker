# Internship Listing Bot

A Telegram bot that notifies you when new internship listings are added to the `listings.json` file in target GitHub repositories with GitHub Actions running at a set interval of time.

---

👉 [Get it at resources.theuntab.com](https://resources.theuntab.com)

---

## Features

- Sends a Telegram DM when new listings are detected across multiple repositories.
- Supports deduplication of listings across repositories.
- Time-boxed "seen with TTL" memory for reopened roles.
- Sorting of listings A-Z by company name.
- Configurable GitHub repos, branches, and file paths.
- Deployable as a GitHub Action for periodic checks.

## Requirements

- **Telegram Bot API token** from [@BotFather](https://t.me/BotFather).
- Your **Telegram User ID** (get via [@userinfobot](https://t.me/userinfobot)).
- GitHub Actions enabled on the target repos.
- A `GITHUB_TOKEN` or personal access token (for repo access if private).

## Environment Variables

Set these in your GitHub Actions secrets:

- `TELEGRAM_BOT_TOKEN` – Bot token from BotFather.
- `TELEGRAM_CHAT_ID` – Your user ID.
- `TARGET_REPOS` – Array of `owner/repo` format for the repos being monitored.
- `TARGET_FILE` – Path to file to watch, e.g., `listings.json`.
- `BRANCH` – Branch to monitor (e.g., `main`).

## Workflow

The GitHub Actions:

1. Runs on a schedule (default: every 5 minutes for repo watch, every 4 hours for channel digest).
2. Fetches the current `listings.json` from the target branches.
3. Deduplicates listings across repositories.
4. Filters listings based on TTL (time-to-live) memory.
5. Sorts listings A-Z by company name.
6. Sends a Telegram message with update details if new listings are detected.

## Manual Test

To send a test message:

```bash
curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
  -d chat_id="$TELEGRAM_CHAT_ID" \
  -d text="hello from api"
```

# Internship Listing Bot

A Telegram bot that notifies you when new internship listings are added to the `listings.json` file in a target GitHub repository.

## Features
- Sends a Telegram DM when `listings.json` changes.
- Manual test run command sends `"hello from api"` to verify bot connection.
- Configurable GitHub repo, branch, and file path.
- Deployable as a GitHub Action for periodic checks.

## Requirements
- **Telegram Bot API token** from [@BotFather](https://t.me/BotFather).
- Your **Telegram User ID** (get via [@userinfobot](https://t.me/userinfobot)).
- GitHub Actions enabled on the target repo.
- A `GITHUB_TOKEN` or personal access token (for repo access if private).

## Environment Variables
Set these in your GitHub Actions secrets:
- `TELEGRAM_BOT_TOKEN` – Bot token from BotFather.
- `TELEGRAM_CHAT_ID` – Your user ID.
- `GITHUB_REPO` – `owner/repo` format for the repo being monitored.
- `TARGET_FILE` – Path to file to watch, e.g. `listings.json`.
- `BRANCH` – Branch to monitor (e.g., `main`).

## Workflow
The GitHub Action:
1. Runs on a schedule (default: hourly).
2. Fetches the current `listings.json` from the target branch.
3. Compares with the last stored copy.
4. If new listings are detected, sends a Telegram message with update details.

## Manual Test
To send a test message:
```bash
curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
  -d chat_id="$TELEGRAM_CHAT_ID" \
  -d text="hello from api"

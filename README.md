# Internship Listing Bot

A Telegram bot that monitors multiple GitHub repositories for new internship listings and sends smart notifications with deduplication, TTL memory, and batched messaging.

---

👉 [Get it at resources.theuntab.com](https://resources.theuntab.com)

---

## Features

- **Multi-repo monitoring**: Tracks multiple repositories simultaneously with commit-based change detection.
- **Smart deduplication**: URL-first deduplication across repositories to avoid duplicate alerts.
- **TTL-based memory**: Remembers previously alerted jobs with configurable TTL to handle reopened positions.
- **Dual notification modes**:
  - **DM alerts** (every 5 minutes): Immediate notifications for new listings within 24 hours.
  - **Channel digest** (every 4 hours): Batched summaries with HTML formatting for channels.
- **Intelligent location formatting**: Context-aware location resolution (CA/NY/NJ for DMs, Multi-location for digests).
- **Message batching**: Automatically splits long messages to handle Telegram's 4096 character limit.
- **Robust file fetching**: Multi-strategy fallback for GitHub Contents API with truncation handling.

## Requirements

- **Telegram Bot API token** from [@BotFather](https://t.me/BotFather).
- **Telegram Chat IDs**: Your user ID and optionally a channel ID.
- **GitHub token** (uses `GITHUB_TOKEN` from Actions or personal access token).
- **Target repositories** with `listings.json` files.

## Configuration

Set these in your GitHub Actions secrets:

- `TELEGRAM_BOT_TOKEN` – Bot token from BotFather.
- `TELEGRAM_CHAT_ID` – Your user ID for DM notifications.
- `TELEGRAM_CHAT_ID_CHANNEL` – Channel ID for digest notifications (optional).
- `GITHUB_TOKEN` – Automatically provided by GitHub Actions.

Environment variables in workflows:

- `TARGET_REPOS` – JSON array of repositories: `["owner1/repo1", "owner2/repo2"]`
- `WINDOW_HOURS` – Time window for filtering (24 for DMs, 4-8 for digests)
- `SEEN_TTL_DAYS` – How long to remember alerted jobs (7 for DMs, 14 for digests)

## Workflows

### DM Fast Watch (`.github/workflows/dm-fast-watch.yml`)

- **Schedule**: Every 5 minutes
- **Purpose**: Immediate alerts for new listings
- **Features**: Commit-based change detection, 24-hour window, TTL deduplication

### Channel Digest (`.github/workflows/channel-digest.yml`)

- **Schedule**: Every 4 hours
- **Purpose**: Batched summaries for channels
- **Features**: HTML formatting, message batching, longer time windows

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Target Repos  │───▶│  GitHub Actions  │───▶│   Telegram      │
│   SimplifyJobs  │    │  - dm-fast-watch │    │   - DM alerts   │
│   vanshb03      │    │  - channel-digest│    │   - Channel     │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                              │
                              ▼
                       ┌──────────────┐
                       │  State Cache │
                       │  - Last SHAs │
                       │  - TTL cache │
                       └──────────────┘
```

## Troubleshooting

### Telegram API Errors

**400 Bad Request: chat not found**

- Check `TELEGRAM_CHAT_ID` is correct (get from [@userinfobot](https://t.me/userinfobot))
- For channels, ensure the bot is added as admin and chat ID starts with `-`

**403 Forbidden: bot was blocked by the user**

- Unblock the bot in your Telegram settings
- Send `/start` to the bot to re-enable messages

**400 Bad Request: message is too long**

- Fixed automatically with message batching (splits at 3900 chars)
- Each batch preserves whole lines and includes continuation headers

### GitHub API Errors

**404 Not Found: contents**

- Wrong repository path or branch name
- File might not exist - uses automatic path detection
- Private repos need proper `GITHUB_TOKEN` permissions

**Rate limiting (403)**

- Workflows include delays between API calls
- Uses commit-based change detection to minimize API usage
- GitHub Actions provides higher rate limits than unauthenticated requests

**Contents API truncation**

- Automatically handled with fallback strategies:
  1. Base64 content from Contents API
  2. Raw download via `download_url`
  3. Git Blobs API with SHA

### Workflow Issues

**No notifications despite new listings**

- Check Actions logs for DEBUG messages showing commit detection
- Verify `WINDOW_HOURS` allows recent items (timestamps in UTC)
- TTL cache may be suppressing recently alerted items
- Use manual runs with `FORCE_WINDOW_HOURS=720` for testing

**Cache conflicts between workflows**

- Each workflow uses separate state directories (`.state/dm-watcher`, `.state/channel-digest`)
- Each has unique cache keys to prevent conflicts

## Manual Testing

Test Telegram connectivity:

```bash
curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
  -d chat_id="$TELEGRAM_CHAT_ID" \
  -d text="Test message from job tracker"
```

Manual workflow runs support diagnostic commands:

- `latest` - Send most recent listing
- `today` - Send all listings from today
- `recent` - Send last N listings
- `digest` - Generate time-windowed digest

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

## Time Window (What It Actually Means)

- **Definition**: The time window filters listings by their own timestamps (`date_posted` → fallback `date_updated`).
- **Not commits**: We do not look for commits “within the window.” Commits are used only to detect changes; the window then filters which new/updated listings are recent enough to send.
- **Where it’s used**:
  - `dm-fast-watch.yml` (`watch_repo.py`): Detects new entries per commit since last seen SHA, then keeps only items with timestamps within `WINDOW_HOURS`.
  - `channel-digest*.yml` (`send_digest_multi.py`): Scans current listings across repos and filters to those within `WINDOW_HOURS`.
- **Typical values**: DMs use 24h by default; channel digest uses 2–8h. You can override during manual runs with the `force_window_hours` input.

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
- `SEEN_TTL_DAYS` – How long to remember alerted jobs (default 14; tune per workflow)

### State & Caching (Immutable Actions Cache)

- **State files**: Each workflow writes its own state under `.state/<workflow>`
  - DM watcher: `.state/dm-watcher`
  - Channel digest: `.state/channel-digest`
  - Channel digest testing: `.state/channel-digest-testing`
- **Why weekly keys**: GitHub Actions caches are immutable. To persist evolving state mid‑week, we use per‑run keys with a weekly prefix and rely on `restore-keys` to load the latest one.
- **Key format**:
  - `dm-watcher-state-v1-<ISO_WEEK>-<run_id>`
  - `channel-digest-state-v1-<ISO_WEEK>-<run_id>`
  - `channel-digest-testing-state-v1-<ISO_WEEK>-<run_id>`
- **Restore strategy**: Prefer the newest cache for the current ISO week, then fall back to earlier weeks via `restore-keys`.
- **Save strategy**: Always save a new cache at the end of a run (immutable), then prune old caches with a cleanup workflow (below).
- For TTL details, see `docs/TTL_IMPLEMENTATION.md`.

## Workflows

### DM Fast Watch (`.github/workflows/dm-fast-watch.yml`)

- **Schedule**: Every 5 minutes
- **Purpose**: Immediate alerts for new listings
- **Features**: Commit-based change detection, 24-hour window, TTL deduplication

### Channel Digest (`.github/workflows/channel-digest.yml`)

- **Schedule**: Every 4 hours
- **Purpose**: Batched summaries for channels
- **Features**: HTML formatting, message batching, longer time windows

### Channel Digest Testing (`.github/workflows/channel-digest-testing.yml`)

- **Schedule**: Every hour
- **Purpose**: Isolated testing of channel digest logic
- **Features**: Same as `channel-digest.yml` but designed for testing and validation

### Cache Cleanup (`.github/workflows/cache-cleanup.yml`)

- **Schedule**: Daily at 08:00 UTC; also supports manual runs (`workflow_dispatch`).
- **Purpose**: Prune immutable caches to keep only the newest N per managed prefix and delete non‑matching caches.
- **Defaults**:
  - Keep prefixes: `dm-watcher-state-v1, channel-digest-state-v1, channel-digest-testing-state-v1`
  - Keep N per prefix: `3`
- **Manual inputs**:
  - `dry_run` (true/false): list without deleting
  - `keep_prefixes`: comma‑separated list to keep
  - `keep_n_per_prefix`: how many newest to retain

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

**DM watcher runs but no Telegram DMs**

- The DM watcher only alerts on true additions in commits since the last seen SHA; edits without additions will be ignored. The channel digest can still show items because it scans the current listings snapshot, not just commit additions.
- SimplifyJobs category filter is stricter for DMs (`Software Engineering`, `Data Science, AI & Machine Learning` only). The digest allows more categories, so it may include items the DM filter suppresses.
- TTL suppression is per workflow. If the DM already alerted on an item within `SEEN_TTL_DAYS`, it won’t repeat, even if the channel digest sends it (separate TTL state).
- Validate credentials: the job checks env presence, but if the user blocked the bot or the Chat ID is wrong, you’ll see `[TELEGRAM] Send failed` in logs.
- For testing: trigger `dm-fast-watch.yml` with `reset_last_seen=true` or `back_one=true`, and/or set `force_window_hours=720` to verify end‑to‑end messaging.

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

# GitHub Secrets Setup Guide

## Required Secrets

Add these 5 new secrets to enable the multi-channel digest workflows:

### Navigation
1. Go to your repository on GitHub
2. Click **Settings** tab
3. Click **Secrets and variables** → **Actions**
4. Click **New repository secret** button

---

## Secrets to Add

### 1. TELEGRAM_CHAT_ID_CHANNEL_HARDWARE
- **Name:** `TELEGRAM_CHAT_ID_CHANNEL_HARDWARE`
- **Value:** Your Hardware channel chat ID (from `.env`)
- **Used by:** `channel-digest-hardware.yml`
- **Purpose:** Hardware Engineering jobs (all degree levels)

### 2. TELEGRAM_CHAT_ID_CHANNEL_QUANT
- **Name:** `TELEGRAM_CHAT_ID_CHANNEL_QUANT`
- **Value:** Your Quant channel chat ID (from `.env`)
- **Used by:** `channel-digest-quant.yml`
- **Purpose:** Quantitative Trading/Research jobs (all degree levels)

### 3. TELEGRAM_CHAT_ID_CHANNEL_PM
- **Name:** `TELEGRAM_CHAT_ID_CHANNEL_PM`
- **Value:** Your PM channel chat ID (from `.env`)
- **Used by:** `channel-digest-pm.yml`
- **Purpose:** Product Management jobs (all degree levels)

### 4. TELEGRAM_CHAT_ID_CHANNEL_PHD
- **Name:** `TELEGRAM_CHAT_ID_CHANNEL_PHD`
- **Value:** Your PhD channel chat ID (from `.env`)
- **Used by:** `channel-digest-phd.yml`
- **Purpose:** Graduate degree positions only (all categories)

### 5. TELEGRAM_CHAT_ID_CHANNEL (verify exists)
- **Name:** `TELEGRAM_CHAT_ID_CHANNEL`
- **Value:** Your SWE/ML channel chat ID
- **Used by:** `channel-digest.yml`
- **Purpose:** SWE/ML jobs for Bachelor's degrees
- **Note:** This should already exist - just verify it's set

---

## Get Chat IDs from .env

Your `.env` file contains these values:
```bash
TELEGRAM_CHAT_ID_CHANNEL=<your_value>
TELEGRAM_CHAT_ID_CHANNEL_HARDWARE=<your_value>
TELEGRAM_CHAT_ID_CHANNEL_QUANT=<your_value>
TELEGRAM_CHAT_ID_CHANNEL_PM=<your_value>
TELEGRAM_CHAT_ID_CHANNEL_PHD=<your_value>
```

Copy each value **exactly** (including the negative sign if present).

---

## Verification Checklist

After adding all secrets:

- [ ] `TELEGRAM_CHAT_ID_CHANNEL` (existing)
- [ ] `TELEGRAM_CHAT_ID_CHANNEL_HARDWARE` (new)
- [ ] `TELEGRAM_CHAT_ID_CHANNEL_QUANT` (new)
- [ ] `TELEGRAM_CHAT_ID_CHANNEL_PM` (new)
- [ ] `TELEGRAM_CHAT_ID_CHANNEL_PHD` (new)
- [ ] `TELEGRAM_BOT_TOKEN` (existing - verify it's set)

Total: **6 secrets** should be configured in your repository.

---

## Testing After Setup

Once secrets are added, test each workflow:

1. Go to **Actions** tab
2. Select a workflow (e.g., "Channel Digest (Hardware - All Levels)")
3. Click **Run workflow** dropdown
4. Set parameters:
   - **Command:** digest
   - **Max items:** 100
   - **Override WINDOW_HOURS:** 720 (30 days for testing)
5. Click **Run workflow** button
6. Check the workflow run logs
7. Verify message appears in the correct Telegram channel

Repeat for all 5 workflows.

---

## Common Issues

### Secret not found error
**Error:** `Error: Secret TELEGRAM_CHAT_ID_CHANNEL_HARDWARE not found`
**Fix:** Double-check the secret name matches exactly (case-sensitive)

### Invalid chat ID
**Error:** `Bad Request: chat not found`
**Fix:** Verify the chat ID is correct and includes the negative sign

### Bot not in channel
**Error:** `Forbidden: bot is not a member of the channel`
**Fix:** Add your bot as an administrator to each Telegram channel

---

## Security Notes

- **Never commit `.env` to git** - it's in `.gitignore`
- **Secrets are encrypted** by GitHub and only accessible during workflow runs
- **Rotate tokens periodically** for security best practices
- **Limit secret access** to necessary workflows only

---

## Next Steps

After adding all secrets:
1. ✅ Test each workflow manually (see Testing section above)
2. ✅ Verify messages in all 5 Telegram channels
3. ✅ Monitor first scheduled runs over 24 hours
4. ✅ Adjust schedules if needed based on job volume

**Ready to deploy!** 🚀

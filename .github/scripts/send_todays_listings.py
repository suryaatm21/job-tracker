#!/usr/bin/env python3
"""
Send all listings whose date_posted equals today's date (UTC).

Configuration via environment variables:
- TARGET_REPO (owner/repo) [required]
- LISTINGS_PATH (path within repo) [default: "listings.json"]
- GH_TOKEN [recommended for higher rate limits]
- TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID [required to send messages]
- DATE_FIELD [default: "date_posted"], DATE_FALLBACK [default: "date_updated"]

Assumptions and behavior:
- Dates are stored as Unix epoch seconds. If not, ISO-8601 is also accepted.
- "Today" is computed in UTC to match GitHub-hosted runners.
- The script sends at most 15 detailed entries to avoid overly long messages.
"""
import os, json, base64, requests
from datetime import datetime, timezone

GH = "https://api.github.com"
TARGET_REPO = os.environ["TARGET_REPO"]
LISTINGS_PATH = os.getenv("LISTINGS_PATH", "listings.json")
DATE_FIELD = os.getenv("DATE_FIELD", "date_posted")
DATE_FALLBACK = os.getenv("DATE_FALLBACK", "date_updated")
HEADERS = {
    # Auth header improves rate limits; empty token is allowed but not advised
    "Authorization": f"Bearer {os.getenv('GH_TOKEN','')}",
    "Accept": "application/vnd.github+json",
}

def gh(url: str, **params):
    """Call GitHub API and return parsed JSON, raising on HTTP errors."""
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def get_file(path: str, ref: str | None = None) -> str:
    """Fetch a file's content from the target repo at optional ref.

    Supports both base64-encoded (binary-safe) responses and plaintext content.
    """
    data = gh(f"{GH}/repos/{TARGET_REPO}/contents/{path}", ref=ref)
    if isinstance(data, dict) and data.get("encoding") == "base64":
        return base64.b64decode(data["content"]).decode("utf-8")
    if isinstance(data, dict) and "content" in data:
        return data["content"]
    raise RuntimeError("Unexpected response for contents API")

def send_telegram(text: str) -> bool:
    """Send a Telegram message to the configured chat.

    Returns True on 2xx HTTP status, False otherwise. Does not print sensitive data.
    """
    tok = os.getenv("TELEGRAM_BOT_TOKEN"); chat = os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{tok}/sendMessage",
        json={
            "chat_id": chat,
            "text": text,
            "disable_web_page_preview": True,
            "parse_mode": "HTML",
        },
        timeout=30,
    )
    print("Telegram status:", r.status_code)
    if not r.ok:
        # Print the response text only on error for diagnostics
        print(r.text)
    return r.ok

def to_epoch(v) -> int:
    """Convert value to epoch seconds. Supports int-like and ISO-8601 strings.

    Returns -1 on failure so callers can skip invalid rows.
    """
    try:
        return int(v)
    except Exception:
        try:
            return int(datetime.fromisoformat(str(v)).timestamp())
        except Exception:
            return -1

def main() -> int:
    """Entrypoint: fetch listings, filter those posted today (UTC), and notify."""
    txt = get_file(LISTINGS_PATH)
    data = json.loads(txt)
    if not isinstance(data, list):
        print("Unexpected JSON structure")
        return 1

    today = datetime.now(timezone.utc).date()
    todays = []
    for x in data:
        ts = x.get(DATE_FIELD, x.get(DATE_FALLBACK))
        epoch = to_epoch(ts)
        if epoch <= 0:
            continue
        d = datetime.fromtimestamp(epoch, tz=timezone.utc).date()
        if d == today:
            todays.append(x)

    if not todays:
        # No new items today: keep the notification short
        send_telegram("No new listings posted today.")
        return 0

    lines = []
    for x in todays[:15]:
        title = x.get("title", "")
        company = x.get("company_name", x.get("company", ""))
        url = x.get("url", x.get("application_link", ""))
        season = x.get("season", "")
        lines.append(f"• <b>{company}</b> — {title} [{season}]\n{url}")

    header = f"New listings today: {len(todays)}"
    send_telegram("\n\n".join([header, *lines]))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

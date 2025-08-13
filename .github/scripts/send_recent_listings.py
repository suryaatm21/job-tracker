#!/usr/bin/env python3
"""
Send the N most recent listings by date field (default: date_posted) to Telegram.

Configuration via environment variables:
- TARGET_REPO (owner/repo) [required]
- LISTINGS_PATH (path within repo) [default: "listings.json"]
- GH_TOKEN [recommended for higher rate limits]
- TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID [required to send messages]
- DATE_FIELD [default: "date_posted"], DATE_FALLBACK [default: "date_updated"]
- COUNT [default: 10]
"""
import os, json, base64, requests
from datetime import datetime

GH = "https://api.github.com"
TARGET_REPO = os.environ["TARGET_REPO"]
LISTINGS_PATH = os.getenv("LISTINGS_PATH", "listings.json")
DATE_FIELD = os.getenv("DATE_FIELD", "date_posted")
DATE_FALLBACK = os.getenv("DATE_FALLBACK", "date_updated")
COUNT = max(1, int(os.getenv("COUNT", "10") or 10))
HEADERS = {
    "Authorization": f"Bearer {os.getenv('GH_TOKEN','')}",
    "Accept": "application/vnd.github+json",
}

def gh(url: str, **params):
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def get_file(path: str, ref: str | None = None) -> str:
    data = gh(f"{GH}/repos/{TARGET_REPO}/contents/{path}", ref=ref)
    if isinstance(data, dict) and data.get("encoding") == "base64":
        return base64.b64decode(data["content"]).decode("utf-8")
    if isinstance(data, dict) and "content" in data:
        return data["content"]
    raise RuntimeError("Unexpected response for contents API")

def send_telegram(text: str) -> bool:
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
        print(r.text)
    return r.ok

def to_epoch(v) -> int:
    try:
        return int(v)
    except Exception:
        try:
            return int(datetime.fromisoformat(str(v)).timestamp())
        except Exception:
            return -1

def sort_key(item) -> int:
    v = item.get(DATE_FIELD, item.get(DATE_FALLBACK))
    return to_epoch(v)

def main() -> int:
    txt = get_file(LISTINGS_PATH)
    data = json.loads(txt)
    if not isinstance(data, list) or not data:
        print("No listings found in", LISTINGS_PATH)
        return 0

    # Sort by timestamp descending and take top COUNT
    sorted_items = sorted(data, key=sort_key, reverse=True)
    top = [x for x in sorted_items if sort_key(x) > 0][:COUNT]

    if not top:
        send_telegram("No recent listings found.")
        return 0

    lines = []
    for x in top:
        title = x.get("title", "")
        company = x.get("company_name", x.get("company", ""))
        url = x.get("url", x.get("application_link", ""))
        season = x.get("season", "")
        # Include a relative date if available
        ts = sort_key(x)
        when = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") if ts > 0 else ""
        lines.append(f"• <b>{company}</b> — {title} [{season}] ({when})\n{url}")

    header = f"Most recent listings: {len(top)}"
    send_telegram("\n\n".join([header, *lines]))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Fetch the latest listing from the target repository's listings file and send it via Telegram.
Relies on env vars: TARGET_REPO, LISTINGS_PATH (default listings.json), GH_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import os, json, base64, requests
from format_utils import format_location, format_job_line

GH = "https://api.github.com"
TARGET_REPO = os.environ["TARGET_REPO"]
LISTINGS_PATH = os.getenv("LISTINGS_PATH", "listings.json")
DATE_FIELD = os.getenv("DATE_FIELD", "date_posted")
DATE_FALLBACK = os.getenv("DATE_FALLBACK", "date_updated")
MESSAGE_PREFIX = os.getenv("MESSAGE_PREFIX", "")  # Context prefix for messages
HEADERS = {"Authorization": f"Bearer {os.getenv('GH_TOKEN','')}",
           "Accept": "application/vnd.github+json"}

def gh(url, **params):
    r = requests.get(url, headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json()

def get_file(path: str, ref: str | None = None):
    data = gh(f"{GH}/repos/{TARGET_REPO}/contents/{path}", ref=ref)  # default branch when ref None
    if isinstance(data, dict) and data.get("encoding") == "base64":
        return base64.b64decode(data["content"]).decode("utf-8")
    if isinstance(data, dict) and "content" in data:
        return data["content"]
    raise RuntimeError("Unexpected response for contents API")

def send_telegram(text: str):
    tok = os.getenv("TELEGRAM_BOT_TOKEN"); chat = os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False
        
    r = requests.post(
        f"https://api.telegram.org/bot{tok}/sendMessage",
        json={"chat_id": chat, "text": text, "disable_web_page_preview": True}
    )
    print("Telegram status:", r.status_code, r.text)
    return r.ok

def main():
    try:
        txt = get_file(LISTINGS_PATH)
    except Exception as e:
        print("Failed to fetch listings file:", e)
        return 1

    try:
        data = json.loads(txt)
    except Exception as e:
        print("JSON parse error:", e)
        return 1

    if not isinstance(data, list) or not data:
        print("No listings found in", LISTINGS_PATH)
        return 0

    # Pick the latest by date field, falling back if missing
    def ts(item):
        v = item.get(DATE_FIELD)
        if v is None:
            v = item.get(DATE_FALLBACK)
        try:
            return int(v)
        except Exception:
            # Try parse string timestamp (ISO8601 or date)
            try:
                from datetime import datetime
                return int(datetime.fromisoformat(str(v)).timestamp())
            except Exception:
                return -1

    latest = max(data, key=ts)
    title = latest.get("title", "")
    company = latest.get("company_name", latest.get("company", ""))
    url = latest.get("url", latest.get("application_link", ""))
    season = latest.get("season", "")
    
    # Format location - use "dm" mode for manual commands to provide specific location info
    locations = latest.get("locations", [])
    location = format_location(locations, mode="dm")
    
    # Use format_job_line for consistent formatting
    repo_author = TARGET_REPO.split('/')[0] if '/' in TARGET_REPO else TARGET_REPO
    formatted_line = format_job_line(company, title, season, location, url, repo_author, html=False)
    
    # Add context prefix if provided
    prefix = f"{MESSAGE_PREFIX}: " if MESSAGE_PREFIX else ""
    text = f"{prefix}Latest listing:\n{formatted_line}"
    send_telegram(text)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

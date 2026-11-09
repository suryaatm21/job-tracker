#!/usr/bin/env python3
"""
Fetch the latest listing from the target repository's listings file and send it via Telegram.
Relies on env vars: TARGET_REPO, LISTINGS_PATH (default listings.json), GH_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
DRY_RUN=true prints the message instead of sending it.
"""
import os, sys
# Shared modules live one level up in .github/scripts; Python only adds this file's own dir to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from format_utils import format_location, format_job_line
from repo_utils import fetch_listings
from telegram_utils import send_message

TARGET_REPO = os.environ["TARGET_REPO"]
LISTINGS_PATH = os.getenv("LISTINGS_PATH", "listings.json")
DATE_FIELD = os.getenv("DATE_FIELD", "date_posted")
DATE_FALLBACK = os.getenv("DATE_FALLBACK", "date_updated")
MESSAGE_PREFIX = os.getenv("MESSAGE_PREFIX", "")  # Context prefix for messages

def send_telegram(text: str):
    ok, status, body = send_message(os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID"), text)
    print("Telegram status:", status, body)
    return ok

def main():
    try:
        # Default-branch aware, and handles >1MB listings files via download_url
        data = fetch_listings(TARGET_REPO, LISTINGS_PATH)
    except Exception as e:
        print("Failed to fetch listings file:", e)
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
    formatted_line = format_job_line(company, title, season, location, url, html=False)
    
    # Add context prefix if provided
    prefix = f"{MESSAGE_PREFIX}: " if MESSAGE_PREFIX else ""
    text = f"{prefix}Latest listing:\n{formatted_line}"
    send_telegram(text)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

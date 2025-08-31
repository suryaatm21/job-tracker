import os, json, base64, requests
from datetime import datetime, timedelta, timezone
from github_helper import fetch_file_json, debug_log, gh_get, GH

TARGET_REPO   = os.environ["TARGET_REPO"]
LISTINGS_PATH = os.environ.get("LISTINGS_PATH","listings.json")
DATE_FIELD    = os.environ.get("DATE_FIELD","date_posted")
DATE_FALLBACK = os.environ.get("DATE_FALLBACK","date_updated")
WINDOW_HOURS  = int(os.environ.get("WINDOW_HOURS","8"))
COUNT         = int(os.environ.get("COUNT","50"))

def get_listings(ref=None):
    """Fetch listings using robust helper"""
    return fetch_file_json(TARGET_REPO, LISTINGS_PATH, ref)

def parse_dt(s):
    if not s: return None
    s = str(s)
    try:
        # Try as epoch timestamp first (this is what the listings actually use)
        return datetime.fromtimestamp(int(s), tz=timezone.utc)
    except Exception:
        pass
    try:
        # ISO-8601 or ISO with Z
        return datetime.fromisoformat(s.replace("Z","+00:00")).astimezone(timezone.utc)
    except Exception:
        pass
    try:
        # YYYY-MM-DD
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None

def send(text):
    tok = os.getenv("TELEGRAM_BOT_TOKEN"); chat = os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat: return
    requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                  json={"chat_id": chat, "text": text, "disable_web_page_preview": True})

def main():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=WINDOW_HOURS)

    listings = get_listings()
    rows = []
    for x in listings:
        dt = parse_dt(x.get(DATE_FIELD)) or parse_dt(x.get(DATE_FALLBACK))
        if dt and dt >= cutoff:
            company = x.get("company_name","").strip()
            title   = x.get("title","").strip()
            url     = x.get("url","").strip()
            season  = x.get("season","")
            rows.append((dt, f"• {company} — {title} [{season}] {url}", company))

    # Sort by company name alphabetically, then by timestamp desc
    rows.sort(key=lambda t: (t[2].lower(), -t[0].timestamp()))
    if not rows:
        return  # silent when no new items

    header = f"New internships detected in last {WINDOW_HOURS}h ({min(len(rows), COUNT)})"
    body = "\n".join(r[1] for r in rows[:COUNT])
    send(f"{header}\n{body}")

if __name__ == "__main__":
    main()

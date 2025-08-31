import os, json, base64, requests
from datetime import datetime, timedelta, timezone

TARGET_REPO   = os.environ["TARGET_REPO"]
LISTINGS_PATH = os.environ.get("LISTINGS_PATH","listings.json")
DATE_FIELD    = os.environ.get("DATE_FIELD","date_posted")
DATE_FALLBACK = os.environ.get("DATE_FALLBACK","date_updated")
WINDOW_HOURS  = int(os.environ.get("WINDOW_HOURS","8"))
COUNT         = int(os.environ.get("COUNT","50") or "50")

GH = "https://api.github.com"
HEADERS = {"Accept":"application/vnd.github+json",
           "Authorization": f"Bearer {os.getenv('GH_TOKEN','')}"}

def gh_get(url, **params):
    r = requests.get(url, headers=HEADERS, params=params); r.raise_for_status(); return r.json()

def get_listings(ref=None):
    data = gh_get(f"{GH}/repos/{TARGET_REPO}/contents/{LISTINGS_PATH}", ref=ref)
    raw = base64.b64decode(data["content"]).decode("utf-8") if data.get("encoding")=="base64" else data["content"]
    return json.loads(raw)

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
            rows.append((dt, f"• {company} — {title} [{season}] {url}"))

    rows.sort(key=lambda t: t[0], reverse=True)
    if not rows:
        return  # silent when no new items

    header = f"New internships detected in last {WINDOW_HOURS}h ({min(len(rows), COUNT)})"
    body = "\n".join(r[1] for r in rows[:COUNT])
    send(f"{header}\n{body}")

if __name__ == "__main__":
    main()

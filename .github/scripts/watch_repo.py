import os, json, requests, pathlib, base64, time
from datetime import datetime

TARGET_REPO = os.environ["TARGET_REPO"]                     # vanshb03/Summer2026-Internships
WATCH_PATHS = set(json.loads(os.environ["WATCH_PATHS"]))    # ["listings.json"]
GH = "https://api.github.com"
HEADERS = {"Authorization": f"Bearer {os.getenv('GH_TOKEN','')}",
           "Accept": "application/vnd.github+json"}

# Path to the listings file within TARGET_REPO
LISTINGS_PATH = os.getenv("LISTINGS_PATH", "listings.json")

# Date configuration for filtering new listings
DATE_FIELD = os.getenv("DATE_FIELD", "date_posted")
DATE_FALLBACK = os.getenv("DATE_FALLBACK", "date_updated")
WINDOW_HOURS = float(os.getenv("WINDOW_HOURS", "24"))  # Only alert for items in last N hours

def debug_log(msg):
    print(f"[{datetime.now().isoformat()}] DEBUG: {msg}")

STATE_DIR = pathlib.Path(".state"); STATE_DIR.mkdir(exist_ok=True, parents=True)
LAST_FILE = STATE_DIR / "last_seen_sha.txt"

def gh(url, **params):
    r = requests.get(url, headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json()

def list_commits(per_page=15):
    return gh(f"{GH}/repos/{TARGET_REPO}/commits", per_page=per_page)

def commit_detail(sha):
    return gh(f"{GH}/repos/{TARGET_REPO}/commits/{sha}")

def get_file_at(ref, path):
    # GET /repos/{owner}/{repo}/contents/{path}?ref={ref}
    try:
        data = gh(f"{GH}/repos/{TARGET_REPO}/contents/{path}", ref=ref)
        if data.get("encoding") == "base64":
            return base64.b64decode(data["content"]).decode("utf-8")
        return data["content"]
    except requests.HTTPError as e:
        # file might not exist in older commit
        if e.response.status_code == 404:
            return None
        raise

def watched(path):
    return any(path == p or path.startswith(p) for p in WATCH_PATHS)

def send_telegram(text):
    debug_log(f"Attempting to send Telegram message: {text[:100]}...")
    tok = os.getenv("TELEGRAM_BOT_TOKEN"); chat = os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat: 
        debug_log("Missing Telegram credentials - BOT_TOKEN or CHAT_ID not set")
        return False
    
    try:
        response = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": text, "disable_web_page_preview": True})
        debug_log(f"Telegram API response: {response.status_code} - {response.text}")
        response.raise_for_status()
        return True
    except Exception as e:
        debug_log(f"Telegram send failed: {e}")
        return False

def summarize_new(listings_old, listings_new):
    # Each item has fields like id, url, title, company_name, season
    def keyset(lst):
        keys = set()
        for x in lst:
            if isinstance(x, dict):
                k = x.get("id") or x.get("url") or (x.get("company_name"), x.get("title"))
                if k:
                    keys.add(k)
        return keys

    def to_epoch(v):
        try:
            return int(v)
        except Exception:
            try:
                from datetime import datetime as _dt
                return int(_dt.fromisoformat(str(v)).timestamp())
            except Exception:
                return -1

    old = keyset(listings_old or [])
    new = keyset(listings_new or [])
    delta_keys = [k for k in new - old]

    # Build entries with timestamp for filtering later
    entries = []
    for x in listings_new or []:
        k = x.get("id") or x.get("url") or (x.get("company_name"), x.get("title"))
        if k in delta_keys:
            title = x.get("title", "")
            company = x.get("company_name", "")
            url = x.get("url", "")
            season = x.get("season", "")
            ts_val = x.get(DATE_FIELD, x.get(DATE_FALLBACK))
            ts = to_epoch(ts_val)
            line = f"• {company} — {title} [{season}] {url}"
            entries.append({"line": line, "ts": ts})
    return entries

def main():
    debug_log(f"Starting watch for repo: {TARGET_REPO}")
    debug_log(f"Watching paths: {WATCH_PATHS}")
    debug_log(f"Listings file path: {LISTINGS_PATH}")
    
    last_seen = LAST_FILE.read_text().strip() if LAST_FILE.exists() else None
    debug_log(f"Last seen SHA: {last_seen}")

    commits = list_commits(per_page=20)
    if not commits: 
        debug_log("No commits found")
        return

    debug_log(f"Found {len(commits)} recent commits")

    # Collect unseen commits (newest→oldest until last_seen)
    new = []
    for c in commits:
        if c["sha"] == last_seen: 
            debug_log(f"Found last seen commit: {c['sha']}")
            break
        new.append(c)

    if not new:
        debug_log("No new commits since last run")
        return

    debug_log(f"Processing {len(new)} new commits")

    # Walk from oldest→newest, only when listings file changed, compute new entries
    messages = []
    for c in reversed(new):
        sha = c["sha"]; url = c["html_url"]; parent = c["parents"][0]["sha"] if c["parents"] else None
        debug_log(f"Processing commit: {sha[:8]} - {c.get('commit', {}).get('message', '')[:50]}")
        
        files = [f["filename"] for f in commit_detail(sha).get("files",[])]
        debug_log(f"Files changed: {files}")
        
        watched_files = [f for f in files if watched(f)]
        if not watched_files:
            debug_log("No watched files in this commit")
            continue

        debug_log(f"Watched files changed: {watched_files}")
        
        # Fetch watched listings file content at before/after refs
        after_txt = get_file_at(sha, LISTINGS_PATH)
        before_txt = get_file_at(parent, LISTINGS_PATH) if parent else None
        
        debug_log(f"File content lengths - Before: {len(before_txt) if before_txt else 0}, After: {len(after_txt) if after_txt else 0}")
        
        try:
            after = json.loads(after_txt) if after_txt else []
            before = json.loads(before_txt) if before_txt else []
            debug_log(f"Listings count - Before: {len(before)}, After: {len(after)}")
        except Exception as e:
            debug_log(f"JSON parse failed: {e}")
            continue

        new_entries = summarize_new(before, after)
        debug_log(f"New listings detected (pre-filter): {len(new_entries)}")

        # Filter to only entries within last WINDOW_HOURS
        cutoff = time.time() - (WINDOW_HOURS * 3600.0)
        filtered = [e for e in new_entries if e.get("ts", -1) >= cutoff]
        debug_log(f"New listings after {WINDOW_HOURS}h filter: {len(filtered)}")

        if filtered:
            header = f"New internships detected ({len(filtered)})"
            lines = [e["line"] for e in filtered[:10]]  # cap to 10 lines
            messages.append("\n".join([header, *lines]))

    # Update last seen to newest commit we examined
    debug_log(f"Updating last seen to: {commits[0]['sha']}")
    LAST_FILE.write_text(commits[0]["sha"])

    if messages:
        debug_log(f"Sending {len(messages)} messages")
        send_telegram("\n\n".join(messages))
    else:
        debug_log("No messages to send")

if __name__ == "__main__":
    main()

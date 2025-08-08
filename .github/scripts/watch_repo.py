import os, json, requests, pathlib, base64

TARGET_REPO = os.environ["TARGET_REPO"]                     # vanshb03/Summer2026-Internships
WATCH_PATHS = set(json.loads(os.environ["WATCH_PATHS"]))    # ["listings.json"]
GH = "https://api.github.com"
HEADERS = {"Authorization": f"Bearer {os.getenv('GH_TOKEN','')}",
           "Accept": "application/vnd.github+json"}

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
    tok = os.getenv("TELEGRAM_BOT_TOKEN"); chat = os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat: return False
    requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                  json={"chat_id": chat, "text": text, "disable_web_page_preview": True})
    return True

def summarize_new(listings_old, listings_new):
    # Each item has fields like id, url, title, company_name, season
    def keyset(lst):
        keys = set()
        for x in lst:
            if isinstance(x, dict):
                k = x.get("id") or x.get("url") or (x.get("company_name"), x.get("title"))
                if k: keys.add(k)
        return keys
    old = keyset(listings_old or [])
    new = keyset(listings_new or [])
    delta_keys = [k for k in new - old]
    # Build pretty lines by scanning new list for matching keys
    lines = []
    for x in listings_new or []:
        k = x.get("id") or x.get("url") or (x.get("company_name"), x.get("title"))
        if k in delta_keys:
            title = x.get("title","")
            company = x.get("company_name","")
            url = x.get("url","")
            season = x.get("season","")
            lines.append(f"• {company} — {title} [{season}] {url}")
    return lines

def main():
    last_seen = LAST_FILE.read_text().strip() if LAST_FILE.exists() else None

    commits = list_commits(per_page=20)
    if not commits: return

    # Collect unseen commits (newest→oldest until last_seen)
    new = []
    for c in commits:
        if c["sha"] == last_seen: break
        new.append(c)

    if not new:
        return

    # Walk from oldest→newest, only when listings.json changed, compute new entries
    messages = []
    for c in reversed(new):
        sha = c["sha"]; url = c["html_url"]; parent = c["parents"][0]["sha"] if c["parents"] else None
        files = [f["filename"] for f in commit_detail(sha).get("files",[])]
        if not any(watched(f) for f in files):
            continue

        after_txt = get_file_at(sha, "listings.json")
        before_txt = get_file_at(parent, "listings.json") if parent else None
        try:
            after = json.loads(after_txt) if after_txt else []
            before = json.loads(before_txt) if before_txt else []
        except Exception:
            # If JSON parse fails, skip notification
            continue

        new_lines = summarize_new(before, after)
        if new_lines:
            header = f"New internships detected ({len(new_lines)}) – {url}"
            messages.append("\n".join([header, *new_lines[:10]]))  # cap to 10 lines

    # Update last seen to newest commit we examined
    LAST_FILE.write_text(commits[0]["sha"])

    if messages:
        send_telegram("\n\n".join(messages))

if __name__ == "__main__":
    main()

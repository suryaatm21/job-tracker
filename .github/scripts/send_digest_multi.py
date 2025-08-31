#!/usr/bin/env python3
"""
Multi-repo digest: Fetch listings from multiple repositories, deduplicate, 
filter by time window, and send consolidated digest to Telegram.

Environment variables:
- TARGET_REPOS: JSON array of repo names (e.g., '["owner1/repo1", "owner2/repo2"]')
- LISTINGS_PATH: Hint for listings file path (default: ".github/scripts/listings.json")
- DATE_FIELD: Primary date field (default: "date_posted")
- DATE_FALLBACK: Fallback date field (default: "date_updated")
- WINDOW_HOURS: Time window in hours (default: "8")
- COUNT: Max items to include (default: "50")
- GH_TOKEN: GitHub token for API access
- TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID: Telegram credentials
"""
import os, json, base64, requests
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

# Configuration
TARGET_REPOS = json.loads(os.environ.get("TARGET_REPOS", '["vanshb03/Summer2026-Internships"]'))
LISTINGS_PATH = os.environ.get("LISTINGS_PATH", ".github/scripts/listings.json")
DATE_FIELD = os.environ.get("DATE_FIELD", "date_posted")
DATE_FALLBACK = os.environ.get("DATE_FALLBACK", "date_updated")
WINDOW_HOURS = int(os.environ.get("WINDOW_HOURS", "8"))
COUNT = int(os.environ.get("COUNT", "50") or "50")

GH = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github+json",
           "Authorization": f"Bearer {os.getenv('GH_TOKEN', '')}"}

def gh_get(url, **params):
    """Make GitHub API request with error handling"""
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def get_default_branch(repo):
    """Get default branch for a repository"""
    repo_info = gh_get(f"{GH}/repos/{repo}")
    return repo_info["default_branch"]

def detect_listings_path(repo, branch):
    """Try to find listings file in repo, trying LISTINGS_PATH first, then fallbacks"""
    paths_to_try = [LISTINGS_PATH, ".github/scripts/listings.json", "listings.json"]
    
    for path in paths_to_try:
        try:
            gh_get(f"{GH}/repos/{repo}/contents/{path}", ref=branch)
            print(f"Found listings file at {path} in {repo}")
            return path
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                continue
            raise
    
    print(f"Warning: Could not find listings file in {repo}, using {LISTINGS_PATH}")
    return LISTINGS_PATH  # fallback to configured path

def get_listings(repo, path, ref=None):
    """Fetch and parse listings JSON from a repository"""
    try:
        data = gh_get(f"{GH}/repos/{repo}/contents/{path}", ref=ref)
        raw = base64.b64decode(data["content"]).decode("utf-8") if data.get("encoding") == "base64" else data["content"]
        return json.loads(raw)
    except Exception as e:
        print(f"Error fetching listings from {repo}:{path} - {e}")
        return []

def normalize_url(url):
    """Normalize URL to scheme+host+path for deduplication"""
    if not url:
        return None
    try:
        parsed = urlparse(url)
        # Keep scheme, netloc (host), and path; drop query and fragment
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip('/')
    except Exception:
        return url

def get_dedup_key(item):
    """Get deduplication key: id -> normalized_url -> (company.lower(), title.lower())"""
    if item.get("id"):
        return ("id", item["id"])
    
    norm_url = normalize_url(item.get("url"))
    if norm_url:
        return ("url", norm_url)
    
    company = (item.get("company_name", "") or "").lower().strip()
    title = (item.get("title", "") or "").lower().strip()
    if company and title:
        return ("company_title", (company, title))
    
    return None

def parse_dt(s):
    """Parse date value - supports epoch timestamps and ISO strings"""
    if not s:
        return None
    s = str(s)
    try:
        # Try as epoch timestamp first (this is what the listings typically use)
        return datetime.fromtimestamp(int(s), tz=timezone.utc)
    except Exception:
        pass
    try:
        # ISO-8601 or ISO with Z
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        pass
    try:
        # YYYY-MM-DD
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None

def send_telegram(text):
    """Send message to Telegram"""
    tok = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False
    
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            json={
                "chat_id": chat,
                "text": text,
                "disable_web_page_preview": True,
                "parse_mode": "HTML"
            },
            timeout=30
        )
        print(f"Telegram status: {response.status_code}")
        if not response.ok:
            print(f"Telegram error: {response.text}")
        return response.ok
    except Exception as e:
        print(f"Telegram send failed: {e}")
        return False

def main():
    """Main function to generate and send multi-repo digest"""
    print(f"Generating digest for repos: {TARGET_REPOS}")
    print(f"Time window: {WINDOW_HOURS} hours, Max items: {COUNT}")
    
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    
    all_entries = []
    
    # Collect entries from all repositories
    for repo in TARGET_REPOS:
        try:
            print(f"Processing repo: {repo}")
            
            # Detect default branch and listings path
            default_branch = get_default_branch(repo)
            listings_path = detect_listings_path(repo, default_branch)
            
            # Fetch listings
            listings = get_listings(repo, listings_path)
            print(f"Found {len(listings)} listings in {repo}")
            
            # Filter and prepare entries
            for item in listings:
                dt = parse_dt(item.get(DATE_FIELD)) or parse_dt(item.get(DATE_FALLBACK))
                if dt and dt >= cutoff:
                    dedup_key = get_dedup_key(item)
                    if dedup_key:
                        company = item.get("company_name", "").strip()
                        title = item.get("title", "").strip()
                        url = item.get("url", "").strip()
                        season = item.get("season", "")
                        
                        entry = {
                            "key": dedup_key,
                            "dt": dt,
                            "line": f"• <b>{company}</b> — {title} [{season}]\n{url}",
                            "repo": repo
                        }
                        all_entries.append(entry)
            
        except Exception as e:
            print(f"Error processing repo {repo}: {e}")
            continue
    
    print(f"Found {len(all_entries)} entries before deduplication")
    
    if not all_entries:
        print("No entries found in time window, exiting silently")
        return
    
    # Deduplicate by key (keep newest by timestamp)
    seen_keys = set()
    deduped_entries = []
    
    # Sort by timestamp descending to prefer newer entries
    all_entries.sort(key=lambda x: x["dt"], reverse=True)
    
    for entry in all_entries:
        if entry["key"] not in seen_keys:
            seen_keys.add(entry["key"])
            deduped_entries.append(entry)
    
    print(f"After deduplication: {len(deduped_entries)} unique entries")
    
    if not deduped_entries:
        print("No entries after deduplication, exiting silently")
        return
    
    # Sort final entries by timestamp desc and limit to COUNT
    deduped_entries.sort(key=lambda x: x["dt"], reverse=True)
    final_entries = deduped_entries[:COUNT]
    
    # Build message
    header = f"New internships detected in last {WINDOW_HOURS}h ({len(final_entries)})"
    body = "\n\n".join(entry["line"] for entry in final_entries)
    message = f"{header}\n\n{body}"
    
    # Send to Telegram
    print(f"Sending digest with {len(final_entries)} entries")
    success = send_telegram(message)
    
    if success:
        print("Digest sent successfully")
    else:
        print("Failed to send digest")

if __name__ == "__main__":
    main()

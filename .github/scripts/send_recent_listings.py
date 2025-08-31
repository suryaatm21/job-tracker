#!/usr/bin/env python3
"""
Send the N most recent listings by date field (default: date_posted) to Telegram from multiple repos.

Configuration via environment variables:
- TARGET_REPOS (JSON array of repos) [required] OR TARGET_REPO (single repo) [fallback]
- LISTINGS_PATH (path within repo) [default: auto-detect]
- GH_TOKEN [recommended for higher rate limits]
- TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID [required to send messages]
- DATE_FIELD [default: "date_posted"], DATE_FALLBACK [default: "date_updated"]
- COUNT [default: 10]
"""
import os, json, base64, requests
from datetime import datetime
from urllib.parse import urlparse
from github_helper import fetch_file_content, fetch_file_json, debug_log, gh_get, GH

# Multi-repo support with fallback to single repo
TARGET_REPOS_STR = os.getenv("TARGET_REPOS")
if TARGET_REPOS_STR:
    TARGET_REPOS = json.loads(TARGET_REPOS_STR)
else:
    # Fallback to single repo for backward compatibility
    TARGET_REPOS = [os.environ["TARGET_REPO"]]

LISTINGS_PATH = os.getenv("LISTINGS_PATH", "listings.json")
DATE_FIELD = os.getenv("DATE_FIELD", "date_posted")
DATE_FALLBACK = os.getenv("DATE_FALLBACK", "date_updated")
COUNT = max(1, int(os.getenv("COUNT", "10") or 10))

def detect_listings_path(repo, branch="main"):
    """Auto-detect listings.json path within repo"""
    try:
        # Try listings.json in root first
        gh_get(f"{GH}/repos/{repo}/contents/listings.json", ref=branch)
        return "listings.json"
    except Exception:
        try:
            # Try in .github folder
            gh_get(f"{GH}/repos/{repo}/contents/.github/listings.json", ref=branch)
            return ".github/listings.json"
        except Exception:
            # Default fallback
            return "listings.json"

def get_file(repo, path: str, ref: str | None = None) -> str:
    """Fetch a file's content from a repo at optional ref using robust helper"""
    return fetch_file_content(repo, path, ref)

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
    """Get deduplication key: normalized_url -> id -> (company.lower(), title.lower())"""
    # Prioritize URL over ID since IDs conflict between repos but URLs are more reliable
    norm_url = normalize_url(item.get("url"))
    if norm_url:
        return ("url", norm_url)
    
    # Only use ID if URL is not available (lower priority due to conflicts)
    if item.get("id"):
        return ("id", item["id"])
    
    # Final fallback to company+title combination
    company = (item.get("company_name", "") or "").lower().strip()
    title = (item.get("title", "") or "").lower().strip()
    if company and title:
        return ("company_title", (company, title))
    
    return None

def get_unified_season(item):
    """Get unified season label: season field or first term or empty string"""
    # SimplifyJobs uses 'season' field, Vansh uses 'terms' array
    if item.get("season"):
        return item["season"]
    elif item.get("terms") and len(item["terms"]) > 0:
        return item["terms"][0]
    else:
        return ""

def should_include_listing(item):
    """Filter out listings with missing/invalid URLs for better quality"""
    url = item.get("url", "").strip()
    return bool(url)  # Skip entries with falsy URLs

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
    all_items = []
    seen_keys = set()
    
    # Process each repository
    for repo in TARGET_REPOS:
        try:
            # Auto-detect listings path for this repo
            listings_path = LISTINGS_PATH if LISTINGS_PATH != "listings.json" else detect_listings_path(repo)
            
            print(f"Fetching from {repo}:{listings_path}")
            txt = get_file(repo, listings_path)
            data = json.loads(txt)
            
            if not isinstance(data, list):
                print(f"Unexpected JSON structure in {repo}")
                continue
            
            repo_items = []
            for x in data:
                # Skip items with falsy URLs for better quality
                if not should_include_listing(x):
                    continue
                    
                if sort_key(x) > 0:  # Valid timestamp
                    dedup_key = get_dedup_key(x)
                    if dedup_key and dedup_key not in seen_keys:
                        seen_keys.add(dedup_key)
                        # Add repo info for source tracking
                        x["_source_repo"] = repo
                        repo_items.append(x)
            
            all_items.extend(repo_items)
            print(f"Found {len(repo_items)} unique listings from {repo}")
            
        except Exception as e:
            print(f"Error processing repo {repo}: {e}")
            continue

    if not all_items:
        send_telegram("No recent listings found.")
        return 0

    # Sort by timestamp descending and take top COUNT
    sorted_items = sorted(all_items, key=sort_key, reverse=True)
    top = sorted_items[:COUNT]

    lines = []
    for x in top:
        title = x.get("title", "")
        company = x.get("company_name", x.get("company", ""))
        url = x.get("url", x.get("application_link", ""))
        season = get_unified_season(x)
        # Include a relative date if available
        ts = sort_key(x)
        when = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") if ts > 0 else ""
        season_str = f"[{season}]" if season else ""
        # Add source tag with author name to distinguish repos
        repo_author = x.get("_source_repo", "").split('/')[0] if x.get("_source_repo") else ""
        repo_tag = f"[{repo_author}]" if repo_author else ""
        lines.append(f"• <b>{company}</b> — {title} {season_str} {repo_tag} ({when})\n{url}".strip())

    header = f"Most recent listings: {len(top)}"
    send_telegram("\n\n".join([header, *lines]))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

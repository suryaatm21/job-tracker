#!/usr/bin/env python3
"""
Send all listings whose date_posted equals today's date (UTC) from multiple repos.

Configuration via environment variables:
- TARGET_REPOS (JSON array of repos) [required] OR TARGET_REPO (single repo) [fallback]
- LISTINGS_PATH (path within repo) [default: auto-detect]
- GH_TOKEN [recommended for higher rate limits]
- TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID [required to send messages]
- DATE_FIELD [default: "date_posted"], DATE_FALLBACK [default: "date_updated"]

Assumptions and behavior:
- Dates are stored as Unix epoch seconds. If not, ISO-8601 is also accepted.
- "Today" is computed in UTC to match GitHub-hosted runners.
- The script sends at most 15 detailed entries to avoid overly long messages.
- Deduplicates across repos using URL-first strategy.
"""
import os, json, base64, requests
from datetime import datetime, timezone
from urllib.parse import urlparse
from github_helper import fetch_file_content, fetch_file_json, debug_log, gh_get, GH
from format_utils import format_location, format_job_line

# Multi-repo support with fallback to single repo
TARGET_REPOS_STR = os.getenv("TARGET_REPOS")
if TARGET_REPOS_STR:
    TARGET_REPOS = json.loads(TARGET_REPOS_STR)
else:
    # Fallback to single repo for backward compatibility
    TARGET_REPOS = [os.environ["TARGET_REPO"]]

MESSAGE_PREFIX = os.getenv("MESSAGE_PREFIX", "")  # Context prefix for messages

LISTINGS_PATH = os.getenv("LISTINGS_PATH", "listings.json")
DATE_FIELD = os.getenv("DATE_FIELD", "date_posted")
DATE_FALLBACK = os.getenv("DATE_FALLBACK", "date_updated")

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
    """Entrypoint: fetch listings from multiple repos, filter those posted today (UTC), and notify."""
    today = datetime.now(timezone.utc).date()
    all_todays = []
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
            
            repo_todays = []
            for x in data:
                # Skip items with falsy URLs for better quality
                if not should_include_listing(x):
                    continue
                    
                ts = x.get(DATE_FIELD, x.get(DATE_FALLBACK))
                epoch = to_epoch(ts)
                if epoch <= 0:
                    continue
                    
                d = datetime.fromtimestamp(epoch, tz=timezone.utc).date()
                if d == today:
                    dedup_key = get_dedup_key(x)
                    if dedup_key and dedup_key not in seen_keys:
                        seen_keys.add(dedup_key)
                        # Add repo info for source tracking
                        x["_source_repo"] = repo
                        repo_todays.append(x)
            
            all_todays.extend(repo_todays)
            print(f"Found {len(repo_todays)} unique listings from {repo} for today")
            
        except Exception as e:
            print(f"Error processing repo {repo}: {e}")
            continue

    if not all_todays:
        # No new items today: keep the notification short
        send_telegram("No new listings posted today.")
        return 0

    # Sort by company name alphabetically
    all_todays.sort(key=lambda x: x.get("company_name", x.get("company", "")).lower())

    lines = []
    for x in all_todays[:15]:  # Limit to 15 entries
        title = x.get("title", "")
        company = x.get("company_name", x.get("company", ""))
        url = x.get("url", x.get("application_link", ""))
        season = get_unified_season(x)
        
        # Format location - use "dm" mode for manual commands to provide specific location info
        locations = x.get("locations", [])
        location = format_location(locations, mode="dm")
        
        # Add source tag with author name to distinguish repos
        repo_author = x.get("_source_repo", "").split('/')[0] if x.get("_source_repo") else ""
        line = format_job_line(company, title, season, location, url, repo_author, html=True)
        lines.append(line)

    # Add context prefix if provided
    prefix = f"{MESSAGE_PREFIX}: " if MESSAGE_PREFIX else ""
    header = f"{prefix}New listings today: {len(all_todays)}"
    send_telegram("\n\n".join([header, *lines]))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

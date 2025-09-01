#!/usr/bin/env python3
"""
Multi-repo digest: Fetch listings from multiple repositories, deduplicate, 
filter by time window, and send consolidated digest to Telegram.

Environment variables:
- TARGET_REPOS: JSON array of repo names (e.g., '["owner1/repo1", "owner2/repo2"]')
- LISTINGS_PATH: Hint for listings file path (default: ".github/scripts/listings.json")
- DATE_FIELD: Primary date field (default: "date_posted")
- DATE_FALLBACK: Fallback date field (default: "date_updated")
- WINDOW_HOURS: Time window in hours (default: "4")
- COUNT: Max items to include (default: "50")
- GH_TOKEN: GitHub token for API access
- TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID: Telegram credentials
"""
import os, json, base64, requests, time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from github_helper import fetch_file_json, debug_log, gh_get, GH
from urllib.parse import urlparse
from state_utils import (
    load_seen, save_seen, should_alert_item, 
    get_cache_key, format_epoch_for_log
)

# Configuration
TARGET_REPOS = json.loads(os.environ.get("TARGET_REPOS", '["vanshb03/Summer2026-Internships"]'))
LISTINGS_PATH = os.environ.get("LISTINGS_PATH", ".github/scripts/listings.json")
DATE_FIELD = os.environ.get("DATE_FIELD", "date_posted")
DATE_FALLBACK = os.environ.get("DATE_FALLBACK", "date_updated")
WINDOW_HOURS = int(os.environ.get("WINDOW_HOURS", "4"))  # Default 4 hours for channel digest
COUNT = int(os.environ.get("COUNT", "50") or "50")  # Handle empty string case

# TTL configuration for seen cache
SEEN_TTL_DAYS = int(os.environ.get("SEEN_TTL_DAYS", "14"))

# State directory (configurable for cache separation)
import pathlib
STATE_DIR = pathlib.Path(os.environ.get("STATE_DIR", ".state"))
STATE_DIR.mkdir(exist_ok=True, parents=True)

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
    """Fetch and parse listings JSON from a repository using robust helper"""
    try:
        return fetch_file_json(repo, path, ref)
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
    print(f"SEEN_TTL_DAYS={SEEN_TTL_DAYS}")
    
    # Load seen cache and calculate TTL (use STATE_DIR)
    seen_cache_path = STATE_DIR / "seen.json"
    seen = load_seen(str(seen_cache_path))
    ttl_seconds = SEEN_TTL_DAYS * 24 * 3600
    now_epoch = int(time.time())
    
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
                # Skip items with falsy URLs for better quality
                if not should_include_listing(item):
                    continue
                    
                dt = parse_dt(item.get(DATE_FIELD)) or parse_dt(item.get(DATE_FALLBACK))
                if dt and dt >= cutoff:
                    # Check TTL cache to see if we should alert for this item
                    if should_alert_item(item, seen, ttl_seconds, now_epoch):
                        dedup_key = get_dedup_key(item)
                        if dedup_key:
                            company = item.get("company_name", "").strip()
                            title = item.get("title", "").strip()
                            url = item.get("url", "").strip()
                            season = get_unified_season(item)  # Use unified season handling
                            
                            season_str = f"[{season}]" if season else ""
                            # Add source tag with author name to distinguish repos
                            repo_author = repo.split('/')[0] if '/' in repo else repo
                            entry = {
                                "key": dedup_key,
                                "dt": dt,
                                "line": f"• <b>{company}</b> — {title} {season_str} [{repo_author}]\n{url}".strip(),
                                "repo": repo,
                                "item": item  # Store item for cache updates
                            }
                            all_entries.append(entry)
            
        except Exception as e:
            print(f"Error processing repo {repo}: {e}")
            continue
    
    print(f"Found {len(all_entries)} entries before deduplication and TTL filtering")
    
    if not all_entries:
        print("No entries found in time window after TTL filtering, exiting silently")
        # Still save seen cache to prune old entries
        save_seen(seen, SEEN_TTL_DAYS, str(seen_cache_path))
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
        # Still save seen cache to prune old entries
        save_seen(seen, SEEN_TTL_DAYS, str(seen_cache_path))
        return
    
    # Sort final entries by company name alphabetically, then by timestamp desc, and limit to COUNT
    deduped_entries.sort(key=lambda x: (x["line"].split(" — ")[0].replace("• <b>", "").replace("</b>", "").lower(), x["dt"]), reverse=False)
    final_entries = deduped_entries[:COUNT]
    
    # Update seen cache for entries we're about to send
    for entry in final_entries:
        if "item" in entry:
            cache_key = get_cache_key(entry["item"])
            seen[cache_key] = now_epoch
    
    # Build message
    header = f"📰 Channel Digest: New internships in last {WINDOW_HOURS}h ({len(final_entries)})"
    body = "\n\n".join(entry["line"] for entry in final_entries)
    message = f"{header}\n\n{body}"
    
    # Send to Telegram
    print(f"Sending digest with {len(final_entries)} entries")
    success = send_telegram(message)
    
    if success:
        print("Digest sent successfully")
    else:
        print("Failed to send digest")
    
    # Save updated seen cache regardless of send success
    save_seen(seen, SEEN_TTL_DAYS, str(seen_cache_path))
    print(f"Updated seen cache with {len(final_entries)} entries")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Multi-repo digest: Fetch listings from multiple repositories, deduplicate, 
filter by time window, and send consolidated digest to Telegram with batching.

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
from format_utils import format_location, log_location_resolution, format_job_line
from telegram_utils import batch_send_message

# Configuration
TARGET_REPOS = json.loads(os.environ.get("TARGET_REPOS", '["vanshb03/Summer2026-Internships"]'))
LISTINGS_PATH = os.environ.get("LISTINGS_PATH", ".github/scripts/listings.json")
DATE_FIELD = os.environ.get("DATE_FIELD", "date_posted")
DATE_FALLBACK = os.environ.get("DATE_FALLBACK", "date_updated")
WINDOW_HOURS = int(os.environ.get("WINDOW_HOURS", "4"))  # Default 4 hours for channel digest
COUNT = int(os.environ.get("COUNT", "50") or "50")  # Handle empty string case

# Support for FORCE_WINDOW_HOURS override for testing
FORCE_WINDOW_HOURS = os.environ.get("FORCE_WINDOW_HOURS")
if FORCE_WINDOW_HOURS and FORCE_WINDOW_HOURS.replace('.', '').isdigit():
    WINDOW_HOURS = int(float(FORCE_WINDOW_HOURS))
    debug_log(f"[CONFIG] FORCE_WINDOW_HOURS override: {WINDOW_HOURS} hours")

# TTL configuration for seen cache
SEEN_TTL_DAYS = int(os.environ.get("SEEN_TTL_DAYS", "14"))

# State directory (configurable for cache separation)
import pathlib
STATE_DIR = pathlib.Path(os.environ.get("STATE_DIR", ".state"))
STATE_DIR.mkdir(exist_ok=True, parents=True)

# Category filtering for digest: allow several categories, exclude "Other"
ALLOWED_CATEGORIES = {
    "Software Engineering",
    "Data Science, AI & Machine Learning",
    "Hardware Engineering",
    "Quantitative Finance",
    "Product Management",
}

def is_allowed_category(item):
    """Return True if item category is allowed for digest.
    Policy: drop explicit "Other"; allow known useful categories; allow unknowns.
    """
    cat = (item.get("category") or "").strip()
    if not cat:
        return True  # No category info → keep
    if cat == "Other":
        return False
    if cat in ALLOWED_CATEGORIES:
        return True
    # Category present but not in allowlist and not "Other" → keep (soft filter)
    return True

def get_default_branch(repo):
    """Get default branch for a repository"""
    repo_info = gh_get(f"{GH}/repos/{repo}")
    branch = repo_info["default_branch"]
    debug_log(f"[BRANCH] {repo} default branch: {branch}")
    return branch

def detect_listings_path(repo, branch):
    """Try to find listings file in repo, trying LISTINGS_PATH first, then fallbacks"""
    paths_to_try = [LISTINGS_PATH, ".github/scripts/listings.json", "listings.json"]
    
    for path in paths_to_try:
        try:
            gh_get(f"{GH}/repos/{repo}/contents/{path}", ref=branch)
            debug_log(f"[PATH] {repo} found listings at: {path}")
            return path
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                continue
            raise
    
    debug_log(f"[PATH] {repo} using fallback path: {LISTINGS_PATH}")
    return LISTINGS_PATH  # fallback to configured path

def get_listings(repo, path, ref=None):
    """Fetch and parse listings JSON from a repository using robust helper"""
    try:
        listings = fetch_file_json(repo, path, ref)
        debug_log(f"[FILE] {repo}:{path}@{ref or 'HEAD'} → {len(listings)} listings")
        return listings
    except Exception as e:
        debug_log(f"[FILE] {repo}:{path}@{ref or 'HEAD'} → error: {e}")
        return []

def normalize_url(url):
    """Normalize URL to scheme+host+path for deduplication"""
    if not url:
        return None
    try:
        parsed = urlparse(url.strip().lower())
        # Keep scheme, netloc (host), and path; drop query and fragment
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip('/')
    except Exception:
        return url

def get_primary_url(item):
    """Prefer item['url'] and fallback to item['application_link']"""
    return (item.get("url") or item.get("application_link") or "").strip()

def get_dedup_key(item):
    """Get deduplication key: normalized_url -> id -> (company.lower(), title.lower())"""
    # Prioritize URL over ID since IDs conflict between repos but URLs are more reliable
    norm_url = normalize_url(get_primary_url(item))
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

def send_telegram_batched(header, lines):
    """Send message to Telegram with batching support"""
    tok = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        debug_log("[TELEGRAM] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False
    
    # If small enough for single message, send as one
    full_message = f"{header}\n\n" + "\n\n".join(lines)
    if len(full_message) <= 4000:
        debug_log(f"[TELEGRAM] Sending single message: {len(full_message)} chars")
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{tok}/sendMessage",
                json={
                    "chat_id": chat,
                    "text": full_message,
                    "disable_web_page_preview": True,
                    "parse_mode": "HTML"
                },
                timeout=30
            )
            debug_log(f"[TELEGRAM] Single message status: {response.status_code}")
            if not response.ok:
                debug_log(f"[TELEGRAM] Single message error: {response.text}")
            return response.ok
        except Exception as e:
            debug_log(f"[TELEGRAM] Single message failed: {e}")
            return False
    
    # Use batching for long messages
    debug_log(f"[TELEGRAM] Message too long ({len(full_message)} chars), using batching")
    success, results = batch_send_message(tok, chat, header, lines, parse_mode="HTML")
    
    if not success:
        failed_batches = [(i, status, body) for i, status, body in results if status < 200 or status >= 300]
        debug_log(f"[TELEGRAM] Some batches failed: {failed_batches}")
    
    return success

def main():
    """Main function to generate and send multi-repo digest"""
    debug_log(f"[CONFIG] Generating digest for repos: {TARGET_REPOS}")
    debug_log(f"[CONFIG] Time window: {WINDOW_HOURS} hours, Max items: {COUNT}")
    debug_log(f"[CONFIG] SEEN_TTL_DAYS={SEEN_TTL_DAYS}")
    
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
            debug_log(f"[REPO] Processing: {repo}")
            
            # Detect default branch and listings path
            default_branch = get_default_branch(repo)
            listings_path = detect_listings_path(repo, default_branch)
            
            # Fetch listings
            listings = get_listings(repo, listings_path)
            debug_log(f"[REPO] {repo} → {len(listings)} listings")
            
            # Filter and prepare entries
            for item in listings:
                # Skip items with falsy URLs for better quality
                if not should_include_listing(item):
                    continue
                # Exclude explicit "Other" category to reduce noise
                if not is_allowed_category(item):
                    continue
                    
                dt = parse_dt(item.get(DATE_FIELD)) or parse_dt(item.get(DATE_FALLBACK))
                if dt and dt >= cutoff:
                    # Check TTL cache to see if we should alert for this item
                    should_alert, reason = should_alert_item(item, seen, ttl_seconds, now_epoch)
                    if should_alert:
                        dedup_key = get_dedup_key(item)
                        if dedup_key:
                            company = item.get("company_name", "").strip()
                            title = item.get("title", "").strip()
                            url = get_primary_url(item)
                            season = get_unified_season(item)  # Use unified season handling
                            
                            # Format location with digest mode (always "Multi-location" if multiple)
                            locations = item.get("locations", [])
                            location = format_location(locations, mode="digest")
                            
                            # Derive a source tag (show when not Simplify)
                            try:
                                owner = repo.split("/")[0]
                            except Exception:
                                owner = ""
                            source = "Simplify" if owner == "SimplifyJobs" else owner
                            
                            line = format_job_line(company, title, season, location, url, html=True, source=source)
                            entry = {
                                "key": dedup_key,
                                "dt": dt,
                                "line": line,
                                "repo": repo,
                                "item": item  # Store item for cache updates
                            }
                            all_entries.append(entry)
            
        except Exception as e:
            debug_log(f"[ERROR] {repo} processing failed: {e}")
            continue
    
    debug_log(f"[RESULT] Found {len(all_entries)} entries before deduplication and TTL filtering")
    
    if not all_entries:
        debug_log(f"[RESULT] No entries found in time window after TTL filtering, exiting silently")
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
    
    debug_log(f"[RESULT] After deduplication: {len(deduped_entries)} unique entries")
    
    if not deduped_entries:
        debug_log(f"[RESULT] No entries after deduplication, exiting silently")
        # Still save seen cache to prune old entries
        save_seen(seen, SEEN_TTL_DAYS, str(seen_cache_path))
        return
    
    # Sort final entries by company name alphabetically, then by timestamp desc, and limit to COUNT
    deduped_entries.sort(key=lambda x: (x["line"].split(" — ")[0].replace("• <b>", "").replace("</b>", "").lower(), x["dt"]), reverse=False)
    final_entries = deduped_entries[:COUNT]
    
    # Build message components
    header = f"📰 Channel Digest: New internships in last {WINDOW_HOURS}h ({len(final_entries)})"
    lines = [entry["line"] for entry in final_entries]
    
    # Send to Telegram with batching
    debug_log(f"[SEND] Sending digest with {len(final_entries)} entries, {sum(len(line) for line in lines)} total chars")
    success = send_telegram_batched(header, lines)
    
    if success:
        debug_log(f"[SEND] Digest sent successfully")
        # Update seen cache only after a successful send
        for entry in final_entries:
            if "item" in entry:
                cache_key = get_cache_key(entry["item"])
                seen[cache_key] = now_epoch
    else:
        debug_log(f"[SEND] Failed to send digest; not marking items as seen")
    
    # Save updated seen cache regardless of send success
    save_seen(seen, SEEN_TTL_DAYS, str(seen_cache_path))
    debug_log(f"[STATE] Updated seen cache with {len(final_entries)} entries")

if __name__ == "__main__":
    main()

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
import os
import json
import pathlib
import time
from datetime import datetime, timezone, timedelta

# Utility imports
from github_helper import fetch_file_json, debug_log
from state_utils import load_seen, save_seen, should_alert_item, get_cache_key, format_epoch_for_log
from format_utils import format_location, log_location_resolution, format_job_line
from telegram_utils import batch_send_message
from repo_utils import get_default_branch, detect_listings_path
from dedup_utils import get_dedup_key, get_primary_url, get_unified_season
from job_filtering import is_allowed_category_digest

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
STATE_DIR = pathlib.Path(os.environ.get("STATE_DIR", ".state"))
STATE_DIR.mkdir(exist_ok=True, parents=True)


def get_listings(repo, path, ref=None):
    """Fetch and parse listings JSON from a repository using robust helper"""
    try:
        listings = fetch_file_json(repo, path, ref)
        debug_log(f"[LISTINGS] {repo}:{path} → {len(listings)} items")
        return listings
    except Exception as e:
        debug_log(f"[LISTINGS] {repo}:{path} → error: {e}")
        return []


def should_include_listing(item):
    """Filter items by various criteria (existence of basic fields, etc.)"""
    return bool(item.get("title") and item.get("company_name"))


def parse_dt(s):
    """Parse datetime string with fallback formats"""
    if not s:
        return None
    
    # Try most common format first: 2024-12-19 (ISO date only)
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except:
        pass
    
    # Try with more lenient parsing
    formats = [
        "%Y-%m-%dT%H:%M:%SZ",           # ISO 8601 with Z
        "%Y-%m-%dT%H:%M:%S",            # ISO 8601 without timezone
        "%Y-%m-%d %H:%M:%S",            # Space-separated datetime
        "%m/%d/%Y",                     # US date format
        "%m/%d/%y",                     # US date format (2-digit year)
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except:
            continue
    
    debug_log(f"[PARSE] Failed to parse date: '{s}'")
    return None


def send_telegram_batched(header, lines):
    """Send digest with batching for long messages"""
    debug_log(f"[TELEGRAM] Preparing to send digest: {header}")
    debug_log(f"[TELEGRAM] Total items: {len(lines)}")
    
    # Get credentials
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        debug_log("[TELEGRAM] Missing credentials - BOT_TOKEN or CHAT_ID not set")
        return False
    
    try:
        # Use the batching utility from telegram_utils
        success = batch_send_message(
            token=token,
            chat_id=chat_id,
            header=header,
            lines=lines,
            max_chars=3900  # Leave room for headers
        )
        
        if success:
            debug_log(f"[TELEGRAM] Successfully sent digest with {len(lines)} items")
            return True
        else:
            debug_log("[TELEGRAM] Failed to send digest")
            return False
            
    except Exception as e:
        debug_log(f"[TELEGRAM] Error sending batched message: {e}")
        return False


def main():
    """Main execution logic"""
    debug_log("[DIGEST] Starting send_digest_multi.py")
    debug_log(f"[CONFIG] TARGET_REPOS: {TARGET_REPOS}")
    debug_log(f"[CONFIG] WINDOW_HOURS: {WINDOW_HOURS}, COUNT: {COUNT}")
    debug_log(f"[CONFIG] DATE_FIELD: {DATE_FIELD}, DATE_FALLBACK: {DATE_FALLBACK}")
    
    # Load seen cache for TTL-based duplicate prevention
    seen_cache_path = STATE_DIR / "seen.json"
    seen = load_seen(str(seen_cache_path))
    ttl_seconds = SEEN_TTL_DAYS * 24 * 3600
    now_epoch = int(time.time())
    
    debug_log(f"[CACHE] Loaded seen cache with {len(seen)} items")
    
    # Time window calculation
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
    cutoff_epoch = cutoff_time.timestamp()
    
    debug_log(f"[WINDOW] Time cutoff: {cutoff_time.isoformat()} ({cutoff_epoch})")
    
    all_items = []
    
    # Process each repository
    for repo in TARGET_REPOS:
        debug_log(f"[REPO] Processing {repo}")
        
        try:
            # Get repository info
            branch = get_default_branch(repo)
            listings_path = detect_listings_path(repo, branch, LISTINGS_PATH)  # Pass LISTINGS_PATH as fallback
            
            # Fetch listings
            listings = get_listings(repo, listings_path)
            if not listings:
                debug_log(f"[REPO] {repo} → No listings found")
                continue
            
            debug_log(f"[REPO] {repo} → {len(listings)} total items")
            
            # Filter items
            repo_items = []
            for item in listings:
                # Basic field check
                if not should_include_listing(item):
                    continue
                
                # Category filter for digest
                if not is_allowed_category_digest(item):
                    continue
                
                # Time window filter
                dt_val = item.get(DATE_FIELD) or item.get(DATE_FALLBACK)
                dt = parse_dt(dt_val)
                
                if dt and dt.timestamp() >= cutoff_epoch:
                    # Add repo info for tracking
                    item_copy = item.copy()
                    item_copy["_repo"] = repo
                    item_copy["_timestamp"] = dt.timestamp()
                    repo_items.append(item_copy)
            
            debug_log(f"[REPO] {repo} → {len(repo_items)} items after filtering")
            all_items.extend(repo_items)
            
        except Exception as e:
            debug_log(f"[REPO] {repo} → Error: {e}")
            continue
    
    debug_log(f"[AGGREGATE] Total items from all repos: {len(all_items)}")
    
    # Deduplication by URL/ID
    seen_keys = set()
    deduplicated = []
    
    for item in all_items:
        key = get_dedup_key(item)
        if key and key not in seen_keys:
            seen_keys.add(key)
            deduplicated.append(item)
    
    debug_log(f"[DEDUP] After deduplication: {len(deduplicated)} items")
    
    # TTL-based filtering (check if we've alerted for these recently)
    final_items = []
    for item in deduplicated:
        should_alert, reason = should_alert_item(item, seen, ttl_seconds, now_epoch)
        if should_alert:
            final_items.append(item)
        # Note: don't update seen cache yet - only do that after successful send
    
    debug_log(f"[TTL] After TTL filtering: {len(final_items)} items")
    
    # Sort by timestamp (newest first) and limit
    final_items.sort(key=lambda x: x.get("_timestamp", 0), reverse=True)
    final_items = final_items[:COUNT]
    
    debug_log(f"[LIMIT] After count limit: {len(final_items)} items")
    
    if not final_items:
        debug_log("[INFO] No items to send in digest")
        return
    
    # Format items for display
    lines = []
    for item in final_items:
        title = item.get("title", "")
        company = item.get("company_name", "")
        url = get_primary_url(item)
        season = get_unified_season(item)
        
        # Format location with channel mode (simplified)
        locations = item.get("locations", [])
        location = format_location(locations, mode="channel")
        
        # Log location resolution for debugging (if multiple locations)
        if locations and len(locations) > 1:
            log_location_resolution(company, title, locations, location, "channel")
        
        line = format_job_line(company, title, season, location, url, html=True)
        lines.append(line)
    
    # Prepare header
    time_desc = f"last {WINDOW_HOURS}h" if WINDOW_HOURS < 24 else f"last {WINDOW_HOURS//24}d"
    header = f"📈 Job Digest ({time_desc}) - {len(lines)} listings"
    
    # Send digest
    success = send_telegram_batched(header, lines)
    
    if success:
        # Update seen cache only after successful send
        for item in final_items:
            cache_key = get_cache_key(item)  # Use cache key, not dedup key
            if cache_key:
                seen[cache_key] = now_epoch
        
        # Save updated seen cache
        save_seen(seen, SEEN_TTL_DAYS, str(seen_cache_path))
        debug_log(f"[CACHE] Updated seen cache with {len(final_items)} new items")
        debug_log(f"[SUCCESS] Digest sent with {len(final_items)} items")
    else:
        debug_log("[ERROR] Failed to send digest - not updating seen cache")
        exit(1)
    
    debug_log("[DIGEST] Completed send_digest_multi.py")


if __name__ == "__main__":
    main()
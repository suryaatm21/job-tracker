#!/usr/bin/env python3
"""
Watches for new job entries across repositories.
Only sends alerts when items are truly new (not seen in cache).
"""
import os
import json
import pathlib
import time
import sys
from datetime import datetime

# Utility imports  
from github_helper import debug_log
from state_utils import load_seen, save_seen
from repo_utils import get_default_branch, detect_listings_path, get_repo_entries
from watcher_core import process_repo_entries
from telegram_utils import send_message

# Multi-repo configuration
TARGET_REPOS = json.loads(os.environ.get("TARGET_REPOS", '["vanshb03/Summer2026-Internships"]'))
WATCH_PATHS = set(json.loads(os.environ.get("WATCH_PATHS", '["listings.json"]')))

# Path to the listings file within each repo
LISTINGS_PATH = os.getenv("LISTINGS_PATH", ".github/scripts/listings.json")

# Date configuration for filtering new listings
DATE_FIELD = os.getenv("DATE_FIELD", "date_posted")
DATE_FALLBACK = os.getenv("DATE_FALLBACK", "date_updated")
WINDOW_HOURS = float(os.getenv("WINDOW_HOURS", "24"))  # Only alert for items in last N hours (default 24 hours)

# Support for FORCE_WINDOW_HOURS override for testing
FORCE_WINDOW_HOURS = os.getenv("FORCE_WINDOW_HOURS")
if FORCE_WINDOW_HOURS and FORCE_WINDOW_HOURS.replace('.', '').isdigit():
    WINDOW_HOURS = float(FORCE_WINDOW_HOURS)
    debug_log(f"[CONFIG] FORCE_WINDOW_HOURS override: {WINDOW_HOURS} hours")

# Diagnostic inputs for manual runs
RESET_LAST_SEEN = os.getenv("RESET_LAST_SEEN", "false").lower() == "true"
BACK_ONE = os.getenv("BACK_ONE", "false").lower() == "true"

if RESET_LAST_SEEN:
    debug_log(f"[CONFIG] RESET_LAST_SEEN=true - will ignore cached last_seen SHAs")
if BACK_ONE:
    debug_log(f"[CONFIG] BACK_ONE=true - will set last_seen to parent of latest commit")

# TTL configuration for seen cache
SEEN_TTL_DAYS = int(os.getenv("SEEN_TTL_DAYS", "14"))

# State directory (configurable for cache separation)
STATE_DIR = pathlib.Path(os.getenv("STATE_DIR", ".state"))
STATE_DIR.mkdir(exist_ok=True, parents=True)


def send_telegram(text):
    """Send message to Telegram with retry logic"""
    debug_log(f"[TELEGRAM] Sending message: {len(text)} chars, preview: {text[:100]}...")
    tok = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    
    if not tok or not chat: 
        debug_log("[TELEGRAM] Missing credentials - BOT_TOKEN or CHAT_ID not set")
        return False
    
    success, status, body = send_message(tok, chat, text)
    debug_log(f"[TELEGRAM] STATUS={status} BODY={body[:200] if body else 'None'}")
    
    if not success:
        debug_log(f"[TELEGRAM] Send failed: {status} - {body}")
        raise Exception(f"Telegram send failed: {status} - {body}")
    
    debug_log("[TELEGRAM] Message sent successfully")
    return True


def main():
    """Main execution logic"""
    debug_log("[WATCH] Starting watch_repo.py")
    debug_log(f"[CONFIG] TARGET_REPOS: {TARGET_REPOS}")
    debug_log(f"[CONFIG] WINDOW_HOURS: {WINDOW_HOURS}")
    debug_log(f"[CONFIG] SEEN_TTL_DAYS: {SEEN_TTL_DAYS}")
    
    # Load seen cache with TTL
    ttl_seconds = SEEN_TTL_DAYS * 86400
    now_epoch = int(time.time())
    
    seen_cache_path = STATE_DIR / "seen.json"
    seen = load_seen(str(seen_cache_path))
    debug_log(f"[CACHE] Loaded seen cache with {len(seen)} items")
    
    all_new_items = []
    new_last_seen = {}

    for repo in TARGET_REPOS:
        debug_log(f"[REPO] Processing {repo}")
        
        # Get repository info
        branch = get_default_branch(repo)
        listings_path = detect_listings_path(repo, branch)
        
        # Load last seen SHA for this repo
        last_seen_file = STATE_DIR / f"last_seen_{repo.replace('/', '_')}.txt"
        last_seen_sha = None
        
        if not RESET_LAST_SEEN and last_seen_file.exists():
            last_seen_sha = last_seen_file.read_text().strip()
            debug_log(f"[STATE] {repo} loaded last_seen: {last_seen_sha[:8]}")
        
        if BACK_ONE:
            # For debugging: set last_seen to parent of latest commit
            commits = get_repo_entries(repo, per_page=1)
            if commits and commits[0].get("parents"):
                last_seen_sha = commits[0]["parents"][0]["sha"]
                debug_log(f"[DEBUG] BACK_ONE: Setting last_seen to parent: {last_seen_sha[:8]}")
        
        # Process repository entries
        new_entries = process_repo_entries(
            repo, listings_path, last_seen_sha, WATCH_PATHS,
            WINDOW_HOURS, DATE_FIELD, DATE_FALLBACK,
            seen, ttl_seconds, now_epoch
        )
        
        if new_entries:
            all_new_items.extend(new_entries)
            debug_log(f"[REPO] {repo} contributed {len(new_entries)} new items")
        
        # Update last_seen SHA for this repo
        commits = get_repo_entries(repo, per_page=1)
        if commits:
            new_last_seen[repo] = commits[0]["sha"]

    # Update seen cache with new items
    for item_data in all_new_items:
        seen[item_data["key"]] = now_epoch
    
    # Save updated seen cache
    save_seen(seen, SEEN_TTL_DAYS, str(seen_cache_path))
    debug_log(f"[CACHE] Saved seen cache with {len(seen)} items")
    
    # Save last_seen SHAs
    for repo, sha in new_last_seen.items():
        last_seen_file = STATE_DIR / f"last_seen_{repo.replace('/', '_')}.txt"
        last_seen_file.write_text(sha)
        debug_log(f"[STATE] {repo} saved last_seen: {sha[:8]}")
    
    # Send notifications
    if all_new_items:
        debug_log(f"[ALERT] Found {len(all_new_items)} new items to alert")
        
        # Sort by timestamp (newest first)
        all_new_items.sort(key=lambda x: x["ts"], reverse=True)
        
        # Create message
        lines = [f"🔔 {len(all_new_items)} New Job Alert(s)"]
        lines.extend([item["line"] for item in all_new_items])
        
        message = "\n\n".join(lines)
        
        try:
            send_telegram(message)
            debug_log(f"[SUCCESS] Sent alert for {len(all_new_items)} items")
        except Exception as e:
            debug_log(f"[ERROR] Failed to send Telegram message: {e}")
            sys.exit(1)
    else:
        debug_log("[INFO] No new items to alert")
    
    debug_log("[WATCH] Completed watch_repo.py")


if __name__ == "__main__":
    main()
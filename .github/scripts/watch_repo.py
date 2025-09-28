#!/usr/bin/env python3
"""
Multi-repo watcher: Monitor repositories for new job listings and send DM alerts.

This module orchestrates the job watching workflow using modular utilities:
- repo_utils: Repository and branch management  
- job_filtering: Category and quality filtering
- dedup_utils: Cross-repo deduplication
- watcher_core: Commit processing and diff analysis
- format_utils: Location and message formatting
- state_utils: TTL-based seen cache management
- telegram_utils: Message sending
"""
import os, json, pathlib, time
from datetime import datetime

# Import all utility modules
from github_helper import debug_log
from state_utils import (
    load_seen, save_seen, should_alert_item, 
    get_cache_key, format_epoch_for_log, should_include_item,
    parse_epoch, get_primary_url
)
from format_utils import format_location, log_location_resolution, format_job_line
from telegram_utils import send_message
from repo_utils import get_default_branch, detect_listings_path, get_repo_entries
from dedup_utils import get_dedup_key, get_unified_season
from job_filtering import should_process_repo_item
from watcher_core import process_repo_entries

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
    """Send message to Telegram with debug logging"""
    debug_log(f"[TELEGRAM] Sending message: {len(text)} chars, preview: {text[:100]}...")
    tok = os.getenv("TELEGRAM_BOT_TOKEN"); chat = os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat: 
        debug_log("[TELEGRAM] Missing credentials - BOT_TOKEN or CHAT_ID not set")
        return False
    
    success, status, body = send_message(tok, chat, text)
    debug_log(f"[TELEGRAM] STATUS={status} BODY={body[:200] if body else 'None'}")
    
    if not success:
        debug_log(f"[TELEGRAM] Send failed: {status} - {body}")
    
    return success

def migrate_legacy_state():
    """Migrate from single last_seen_sha.txt to per-repo state files"""
    legacy_file = STATE_DIR / "last_seen_sha.txt"
    if not legacy_file.exists():
        return  # No legacy state to migrate
    
    try:
        legacy_sha = legacy_file.read_text().strip()
        if not legacy_sha:
            return
        
        debug_log(f"[MIGRATE] Found legacy state: {legacy_sha[:8]}")
        
        # Migrate to per-repo files for all configured repos
        for repo in TARGET_REPOS:
            safe_repo_name = repo.replace("/", "_")
            repo_file = STATE_DIR / f"last_seen_{safe_repo_name}.txt"
            if not repo_file.exists():
                repo_file.write_text(legacy_sha)
                debug_log(f"[MIGRATE] Created {repo_file.name} with {legacy_sha[:8]}")
        
        # Remove legacy file after successful migration
        legacy_file.unlink()
        debug_log("[MIGRATE] Legacy state migration complete")
        
    except Exception as e:
        debug_log(f"[MIGRATE] Legacy state migration failed: {e}")

def main():
    debug_log(f"[CONFIG] Starting multi-repo watch for: {TARGET_REPOS}")
    debug_log(f"[CONFIG] Watching paths: {WATCH_PATHS}")
    debug_log(f"[CONFIG] Listings file path hint: {LISTINGS_PATH}")
    debug_log(f"[CONFIG] WINDOW_HOURS={WINDOW_HOURS}, SEEN_TTL_DAYS={SEEN_TTL_DAYS}")
    debug_log(f"[CONFIG] DATE_FIELD={DATE_FIELD}, DATE_FALLBACK={DATE_FALLBACK}")
    
    # Migrate legacy state if needed
    migrate_legacy_state()
    
    # Load seen cache and calculate TTL (use STATE_DIR)
    seen_cache_path = STATE_DIR / "seen.json"
    seen = load_seen(str(seen_cache_path))
    ttl_seconds = SEEN_TTL_DAYS * 24 * 3600
    now_epoch = int(time.time())
    
    all_entries = []
    
    # Process each repository
    for repo in TARGET_REPOS:
        # Get per-repo state file
        safe_repo_name = repo.replace("/", "_")
        last_file = STATE_DIR / f"last_seen_{safe_repo_name}.txt"
        last_seen = last_file.read_text().strip() if last_file.exists() else None
        
        # Handle diagnostic inputs
        if RESET_LAST_SEEN:
            debug_log(f"[STATE] {repo} RESET_LAST_SEEN=true, ignoring cached SHA")
            last_seen = None
        elif BACK_ONE and last_seen:
            # Set last_seen to parent of current last_seen to force re-check
            try:
                from github_helper import gh_get, GH
                commit_info = gh_get(f"{GH}/repos/{repo}/commits/{last_seen}")
                if commit_info.get("parents"):
                    parent_sha = commit_info["parents"][0]["sha"]
                    debug_log(f"[STATE] {repo} BACK_ONE=true, setting last_seen from {last_seen[:8]} to parent {parent_sha[:8]}")
                    last_seen = parent_sha
                else:
                    debug_log(f"[STATE] {repo} BACK_ONE=true but commit {last_seen[:8]} has no parent")
            except Exception as e:
                debug_log(f"[STATE] {repo} BACK_ONE failed to get parent of {last_seen[:8]}: {e}")
        
        debug_log(f"[STATE] {repo} last_seen_SHA: {last_seen[:8] if last_seen else 'None'}")
        
        try:
            # Detect default branch and listings path
            default_branch = get_default_branch(repo)
            listings_path = detect_listings_path(repo, default_branch, LISTINGS_PATH)
            
            # Get new entries from this repo with TTL filtering
            repo_entries = process_repo_entries(
                repo, listings_path, last_seen, WATCH_PATHS,
                WINDOW_HOURS, DATE_FIELD, DATE_FALLBACK,
                seen, ttl_seconds, now_epoch
            )
            all_entries.extend(repo_entries)
            
            # Update last seen SHA for this repo
            commits = get_repo_entries(repo, per_page=1)
            if commits:
                newest_sha = commits[0]["sha"]
                last_file.write_text(newest_sha)
                debug_log(f"[STATE] {repo} updated last_seen_SHA: {newest_sha[:8]}")
        
        except Exception as e:
            debug_log(f"[ERROR] {repo} processing failed: {e}")
            continue
    
    if not all_entries:
        debug_log("[RESULT] No new entries found across all repos")
        # Still save seen cache to prune old entries
        save_seen(seen, SEEN_TTL_DAYS, str(seen_cache_path))
        return

    debug_log(f"[RESULT] Found {len(all_entries)} new entries before deduplication")
    
    # Deduplicate across all repos (keep first occurrence by timestamp desc)
    seen_keys = set()
    deduped_entries = []
    
    # Sort by timestamp descending to prefer newer entries
    all_entries.sort(key=lambda x: x["ts"], reverse=True)
    
    for entry in all_entries:
        if entry["key"] not in seen_keys:
            seen_keys.add(entry["key"])
            deduped_entries.append(entry)
    
    debug_log(f"[RESULT] After deduplication: {len(deduped_entries)} unique entries")
    
    # Apply TTL-based filtering (reuse should_alert_item for reopen logic)
    final_entries = []
    for entry in deduped_entries:
        # Use the same TTL logic that watcher_core used, including reopen detection
        if entry.get("item"):
            should_alert, reason = should_alert_item(entry["item"], seen, ttl_seconds, now_epoch)
            if should_alert:
                final_entries.append(entry)
                cache_key = get_cache_key(entry["item"])
                if reason == "reopen":
                    # Enhanced logging for reopen events
                    item = entry["item"]
                    url = get_primary_url(item)
                    updated_epoch = parse_epoch(item.get("date_updated")) or parse_epoch(item.get("date_posted"))
                    company = item.get("company_name", "Unknown")
                    title = item.get("title", "Unknown")
                    debug_log(f"ALLOW-REOPEN {company} - {title} | URL={url[:50]}... | updated_epoch={updated_epoch} ({format_epoch_for_log(updated_epoch)})")
                elif reason in ["ttl_expired"]:
                    last_alert = seen.get(cache_key)
                    debug_log(f"ALLOW-TTL key={cache_key} last={format_epoch_for_log(last_alert)} ttl={SEEN_TTL_DAYS}d")
                elif reason == "new":
                    debug_log(f"ALLOW-NEW key={cache_key}")
            else:
                cache_key = get_cache_key(entry["item"])
                last_alert = seen.get(cache_key)
                debug_log(f"SUPPRESS key={cache_key} last={format_epoch_for_log(last_alert)} ttl={SEEN_TTL_DAYS}d reason={reason}")
        else:
            # Fallback for entries without item data (shouldn't happen in normal flow)
            final_entries.append(entry)
    
    debug_log(f"[RESULT] After TTL filtering: {len(final_entries)} entries to alert")
    
    if final_entries:
        # Sort final entries by company name alphabetically, then by timestamp desc
        final_entries.sort(key=lambda x: (x["line"].split(" — ")[0].replace("• ", "").lower(), -x["ts"]))
        lines = [entry["line"] for entry in final_entries[:10]]
        
        header = f"🔔 DM Alert: New internships detected ({len(final_entries)})"
        message = "\n".join([header] + lines)
        
        debug_log(f"[SEND] Sending message with {len(lines)} lines, ttl_allowed={len(final_entries)}")
        sent_ok = send_telegram(message)
        if sent_ok:
            # Mark as seen only after successful send
            for entry in final_entries:
                cache_key = None
                if entry.get("item"):
                    cache_key = get_cache_key(entry["item"])
                if not cache_key:
                    cache_key = entry["key"][1] if isinstance(entry["key"], tuple) else str(entry["key"])
                if cache_key:
                    seen[cache_key] = now_epoch
        else:
            debug_log("[SEND] Failed to send DM message; not marking items as seen")
    else:
        debug_log(f"[SEND] No messages to send after TTL filtering")
    
    # Save updated seen cache
    save_seen(seen, SEEN_TTL_DAYS, str(seen_cache_path))

if __name__ == "__main__":
    main()

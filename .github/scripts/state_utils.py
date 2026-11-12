#!/usr/bin/env python3
"""
Time-boxed seen cache with TTL for job tracker.

Provides utilities to track when job listings were last alerted and allow
re-opened roles (updated date_updated) to alert again immediately.
"""
import json
import os
import pathlib
import time
from datetime import datetime, timezone
from dedup_utils import get_primary_url, normalize_url

# Reopen detection grace period - prevents identical re-additions from bypassing TTL
REOPEN_GRACE_PERIOD = 86400  # 1 day in seconds

def get_cache_key(item):
    """Get consistent cache key: normalized_url -> id -> (company.lower(), title.lower())"""
    # Priority 1: Normalized URL (most reliable)
    norm_url = normalize_url(get_primary_url(item))
    if norm_url:
        return norm_url
    
    # Priority 2: ID if available
    if item.get("id"):
        return f"id:{item['id']}"
    
    # Priority 3: Company+title combination
    company = (item.get("company_name", "") or "").lower().strip()
    title = (item.get("title", "") or "").lower().strip()
    if company and title:
        return f"comp_title:{company}|{title}"
    
    return None

def parse_epoch(value):
    """Parse date value to epoch seconds (UTC). Returns None on failure."""
    if not value:
        return None
    
    try:
        # Try as epoch timestamp first
        return int(float(value))
    except (ValueError, TypeError):
        pass
    
    try:
        # Try as ISO-8601 string
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return None

def should_include_item(item):
    """Filter items based on visibility, active status, and URL presence"""
    # Skip items marked as not visible
    if item.get("is_visible") is False:
        return False
    
    # Skip items marked as inactive/closed (active: false)
    if item.get("active") is False:
        return False
    
    # Skip items with empty/invalid URLs for better quality
    url = get_primary_url(item)
    return bool(url)

# Version of the keys seen.json holds, stored next to it in seen_key_version.
# Bump it whenever get_cache_key changes, so the first run on new keys rebuilds the
# memory silently instead of posting every listing again (see rebuild_seen).
# 2: URL keys keep identifying query parameters (?gh_jid=...) and path case.
SEEN_KEY_VERSION = 2


def key_version_path(path):
    """Path of the key-version marker for the seen cache at path."""
    return pathlib.Path(path).with_name("seen_key_version")


def rebuild_pending_path(path):
    """
    Path of the marker that a rebuild started and is not finished, for the seen cache
    at path. It lives outside seen.json so TTL pruning cannot remove it.
    """
    return pathlib.Path(path).with_name("seen_rebuild_pending")


def load_seen(path=".state/seen.json"):
    """
    Load seen cache from JSON file.

    An entry whose value is not a number is skipped and logged; it never costs
    the rest of the memory (SPEC.md, SEEN-5).

    Returns:
        dict[str, int]: Mapping of cache_key -> last_alert_epoch
    """
    try:
        seen_path = pathlib.Path(path)
        if not seen_path.exists():
            return {}
        with open(seen_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Warning: Failed to load seen cache from {path}: {e}")
        return {}
    if not isinstance(data, dict):
        print(f"Warning: Seen cache at {path} is not a JSON object; starting empty")
        return {}

    seen, bad = {}, []
    for key, value in data.items():
        try:
            if isinstance(value, bool):
                raise TypeError("boolean")
            seen[key] = int(float(value))
        except (TypeError, ValueError, OverflowError):
            bad.append(key)
    if bad:
        print(f"Warning: Skipped {len(bad)} seen cache entries with non-numeric values in {path} "
              f"(e.g. {bad[:3]})")
    return seen


def seen_needs_rebuild(seen, path=".state/seen.json"):
    """
    True when seen holds entries saved under older keys: the memory is not empty
    and its key-version marker is missing or older than SEEN_KEY_VERSION. Also
    True while an unfinished rebuild is pending, even after TTL pruning emptied the
    memory. Otherwise empty memory needs no rebuild because there are no old keys
    to translate. A seen.json that fails to load also starts empty and can repost
    the window; atomic saves only prevent new truncation.
    """
    if rebuild_pending_path(path).exists():
        return True
    if not seen:
        return False
    try:
        return int(key_version_path(path).read_text(encoding="utf-8").strip()) < SEEN_KEY_VERSION
    except (OSError, ValueError):
        return True


def repo_rebuilt(state_dir, repo):
    """True when this repo's listings were already recorded under SEEN_KEY_VERSION keys."""
    try:
        return int(rebuilt_marker_path(state_dir, repo).read_text().strip()) >= SEEN_KEY_VERSION
    except (OSError, ValueError):
        return False


def rebuilt_marker_path(state_dir, repo):
    return pathlib.Path(state_dir) / f"rebuilt_{repo.replace('/', '_')}.txt"


def mark_repo_rebuilt(state_dir, repo):
    write_atomic(rebuilt_marker_path(state_dir, repo), f"{SEEN_KEY_VERSION}\n")


def rebuild_seen(seen, items, now_epoch):
    """
    Silent first run on new keys: record every item's current key as seen at
    now_epoch. A key that is already in the memory keeps its time. The caller
    sends nothing and saves the memory, which also writes the new key version.

    Returns:
        int: number of keys added
    """
    added = 0
    for item in items:
        key = get_cache_key(item)
        if key and key not in seen:
            seen[key] = now_epoch
            added += 1
    return added


def write_atomic(path, text):
    """Write text so a killed run leaves either the old file or the new one, never a truncated one."""
    path = pathlib.Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save_seen(seen, ttl_days, path=".state/seen.json", max_entries=200000, dry_run=False, mark_version=True):
    """
    Save seen cache to JSON file with TTL-based pruning, and mark it as holding
    SEEN_KEY_VERSION keys.
    
    Args:
        seen: dict[str, int] mapping cache_key -> last_alert_epoch
        ttl_days: int, TTL in days for pruning old entries
        path: str, file path to save to
        max_entries: int, maximum entries to keep (sorted by most recent)
        dry_run: bool, if True write nothing (a dry run must not mark unsent items as seen)
        mark_version: bool, False while a rebuild is unfinished: hold back the key version
            and record the pending rebuild (see rebuild_pending_path)

    Returns:
        bool: True when seen.json was written. False on a dry run or a failed write,
        so callers can hold back anything that depends on the memory being saved.
    """
    if dry_run:
        print(f"[DRY-RUN] Not saving seen cache ({len(seen)} entries) to {path}")
        return False
    try:
        # Ensure state directory exists
        seen_path = pathlib.Path(path)
        seen_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Prune entries older than TTL + 2 days buffer
        now = int(time.time())
        prune_cutoff = now - ((ttl_days + 2) * 24 * 3600)
        
        # Filter out old entries
        pruned_seen = {k: v for k, v in seen.items() if v >= prune_cutoff}
        
        # If still too many entries, keep only the most recent ones
        if len(pruned_seen) > max_entries:
            # Sort by last alert time (most recent first) and keep top entries
            sorted_items = sorted(pruned_seen.items(), key=lambda x: x[1], reverse=True)
            pruned_seen = dict(sorted_items[:max_entries])
            print(f"Warning: Seen cache capped at {max_entries} entries (was {len(seen)})")
        
        # A rebuild is still unfinished: record that before pruning can empty the memory
        if not mark_version:
            write_atomic(rebuild_pending_path(seen_path), f"{SEEN_KEY_VERSION}\n")
        # Save to file
        write_atomic(seen_path, json.dumps(pruned_seen, indent=2))
        # Written after the memory, so a failed save leaves the old version in place
        if mark_version:
            write_atomic(key_version_path(seen_path), f"{SEEN_KEY_VERSION}\n")
            rebuild_pending_path(seen_path).unlink(missing_ok=True)
        
        pruned_count = len(seen) - len(pruned_seen)
        if pruned_count > 0:
            print(f"Pruned {pruned_count} old entries from seen cache (TTL={ttl_days}d)")
        return True
    except Exception as e:
        print(f"Warning: Failed to save seen cache to {path}: {e}")
        return False

def should_alert_item(item, seen, ttl_seconds, now_epoch):
    """
    Determine if an item should trigger an alert based on seen cache and TTL.
    
    Args:
        item: Job listing item dict
        seen: dict[str, int] seen cache
        ttl_seconds: int, TTL in seconds
        now_epoch: int, current time as epoch seconds
    
    Returns:
        tuple[bool, str]: (should_alert, reason)
        reason is one of: "new", "ttl_expired", "reopen", "suppressed"
    """
    cache_key = get_cache_key(item)
    if not cache_key:
        return False, "no_cache_key"
    
    last_alert = seen.get(cache_key)
    
    # Never seen before
    if last_alert is None:
        return True, "new"
    
    # Check if job was re-opened (updated after last alert) with grace period
    updated_epoch = parse_epoch(item.get("date_updated")) or parse_epoch(item.get("date_posted"))
    if updated_epoch is not None and updated_epoch > last_alert:
        # Grace period: only treat as reopen if update is at least 1 day (REOPEN_GRACE_PERIOD) after last alert
        # This prevents identical re-additions with bumped timestamps from bypassing TTL
        if updated_epoch - last_alert >= REOPEN_GRACE_PERIOD:
            return True, "reopen"
        # Within grace period - fall through to TTL check
    
    # Check if TTL has expired (covers both normal TTL and post-grace-period cases)
    if now_epoch - last_alert > ttl_seconds:
        return True, "ttl_expired"
    
    # Suppress: within TTL and no recent update (or update within grace period)
    return False, "suppressed"

def format_epoch_for_log(epoch):
    """Format epoch timestamp for logging (ISO format with Z suffix)"""
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OSError):
        return f"epoch:{epoch}"

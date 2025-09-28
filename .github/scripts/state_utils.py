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
from urllib.parse import urlparse

# Reopen detection grace period - prevents identical re-additions from bypassing TTL
REOPEN_GRACE_PERIOD = 3600  # 1 hour in seconds

# Per-repo grace period overrides (environment variable fallbacks)
def get_repo_grace_period(repo_name):
    """Get grace period for specific repo, with env var overrides"""
    if not repo_name:
        return REOPEN_GRACE_PERIOD
    
    repo_lower = repo_name.lower()
    if "simplify" in repo_lower:
        return int(os.getenv("SIMPLIFY_REOPEN_GRACE_SECONDS", REOPEN_GRACE_PERIOD))
    elif "vansh" in repo_lower:
        return int(os.getenv("VANSH_REOPEN_GRACE_SECONDS", REOPEN_GRACE_PERIOD))
    else:
        return REOPEN_GRACE_PERIOD

def get_primary_url(item):
    """Return the canonical URL for a listing (url or application_link)."""
    return (item.get("url") or item.get("application_link") or "").strip()

def normalize_url(url):
    """Normalize URL to scheme+host+path for consistent caching"""
    if not url or not url.strip():
        return None
    try:
        parsed = urlparse(url.strip().lower())
        # Keep scheme, netloc (host), and path; drop query and fragment
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip('/')
        return normalized if normalized != "://" else None
    except Exception:
        return None

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

def load_seen(path=".state/seen.json"):
    """
    Load seen cache from JSON file.
    
    Returns:
        dict[str, int]: Mapping of cache_key -> last_alert_epoch
    """
    try:
        seen_path = pathlib.Path(path)
        if seen_path.exists():
            with open(seen_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Ensure all values are integers (epoch seconds)
                return {k: int(v) for k, v in data.items() if isinstance(v, (int, float, str))}
        return {}
    except Exception as e:
        print(f"Warning: Failed to load seen cache from {path}: {e}")
        return {}

def save_seen(seen, ttl_days, path=".state/seen.json", max_entries=200000):
    """
    Save seen cache to JSON file with TTL-based pruning.
    
    Args:
        seen: dict[str, int] mapping cache_key -> last_alert_epoch
        ttl_days: int, TTL in days for pruning old entries
        path: str, file path to save to
        max_entries: int, maximum entries to keep (sorted by most recent)
    """
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
        
        # Save to file
        with open(seen_path, 'w', encoding='utf-8') as f:
            json.dump(pruned_seen, f, indent=2)
        
        pruned_count = len(seen) - len(pruned_seen)
        if pruned_count > 0:
            print(f"Pruned {pruned_count} old entries from seen cache (TTL={ttl_days}d)")
            
    except Exception as e:
        print(f"Warning: Failed to save seen cache to {path}: {e}")

def should_alert_item(item, seen, ttl_seconds, now_epoch, repo_override=None):
    """
    Determine if an item should trigger an alert based on seen cache and TTL.
    
    Args:
        item: Job listing item dict
        seen: dict[str, int] seen cache
        ttl_seconds: int, TTL in seconds
        now_epoch: int, current time as epoch seconds
        repo_override: Optional repo name for per-repo grace period (e.g., "SimplifyJobs/repo")
    
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
        # Get per-repo grace period if repo is provided
        grace_period = get_repo_grace_period(repo_override) if repo_override else REOPEN_GRACE_PERIOD
        
        # Grace period: only treat as reopen if enough time has passed since last alert
        # This prevents spam re-alerts for minor updates but allows genuine reopens after grace period
        if now_epoch - last_alert >= grace_period:
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

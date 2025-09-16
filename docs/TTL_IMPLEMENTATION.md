# TTL-Based Seen Cache Implementation

## Overview

This implementation adds a time-boxed "seen with TTL" memory system to the job-alerts project. This allows re-opened roles to alert again after their TTL expires, preventing permanent suppression while avoiding spam from frequently updated listings.

## Key Features

### 1. TTL-Based Duplicate Suppression
- **Default TTL**: 14 days for channel digest, 7 days for DM alerts
- **Re-open Detection**: Items can alert again after TTL expires
- **Configurable**: `SEEN_TTL_DAYS` environment variable

### 2. State Management
- **File**: `.state/seen.json` - persistent TTL cache across workflow runs
- **Format**: `{cache_key: last_alert_timestamp}` mapping
- **Auto-cleanup**: Expired entries are removed during load/save operations

### 3. Cache Key Strategy
```python
# Priority order for generating unique cache keys:
1. normalized_url (if available) 
2. item_id (if available)
3. "company_name:title" (fallback)
```

### 4. Actions Cache Strategy (Updated)

- Keys use a per-run pattern with a weekly prefix to allow mid-week state updates:
  - `dm-watcher-state-v1-<ISO_WEEK>-<run_id>`
  - `channel-digest-state-v1-<ISO_WEEK>-<run_id>`
  - `channel-digest-testing-state-v1-<ISO_WEEK>-<run_id>`
- Restore prefers the latest cache for the current week via `restore-keys` and falls back to prior weeks.
- Save runs every execution and creates a new immutable cache entry.
- A scheduled cleanup workflow prunes caches:
  - Deletes non-matching prefixes
  - Keeps the newest N per managed prefix (default 3)

### 5. When We Mark Items as Seen (Updated)

- We mark items as seen only after a successful Telegram send (both DM and digest).
- If a send fails, we do not mark items as seen so they can retry on subsequent runs.

### 6. Source Tagging

- Channel digest lines include a source tag for non-Simplify owners (e.g., `(vanshb03)`).
- DM alerts do not include source tags.

## Files Modified

### New Files
- **`.github/scripts/state_utils.py`** - TTL utility functions
  - `load_seen()` - Load and clean expired cache entries
  - `save_seen()` - Save cache with metadata
  - `should_alert_item()` - Check if item should trigger alert based on TTL
  - `get_cache_key()` - Generate consistent cache keys
  - `normalize_url()` - URL normalization for consistent caching

### Updated Files
- **`.github/scripts/watch_repo.py`** - Multi-repo watcher with TTL filtering
- **`.github/scripts/send_digest_multi.py`** - Channel digest with TTL support
- **`.github/workflows/dm-fast-watch.yml`** - Added `SEEN_TTL_DAYS: '7'`
- **`.github/workflows/channel-digest.yml`** - Added `SEEN_TTL_DAYS: '14'`

## Configuration

### Environment Variables
```yaml
SEEN_TTL_DAYS: "14"  # Default TTL in days
```

### Workflow-Specific TTLs
```yaml
# DM fast watch (frequent updates)
SEEN_TTL_DAYS: '7'

# Channel digest (less frequent)  
SEEN_TTL_DAYS: '14'
```

## How It Works

### 1. First Alert
When a new job listing is detected:
1. Check if cache key exists in seen cache
2. If not found → Alert and record timestamp
3. If found but TTL expired → Alert and update timestamp
4. If found and within TTL → Suppress alert

### 2. Re-opened Roles
When a job listing is updated (new `date_updated`):
1. Same TTL logic applies
2. If enough time has passed → Alert again
3. Prevents spam from frequent minor updates
4. Allows important re-openings to be noticed

### 3. Cache Maintenance
- **Load**: Remove entries older than TTL
- **Save**: Include metadata (creation time, TTL days)
- **Cleanup**: Automatic pruning of stale entries

## Logging and Debugging

### Debug Output
```
SEEN_TTL_DAYS=14
Loading seen cache with TTL 14 days (1209600 seconds)
ALLOW-TTL key=company_name:title last=2024-01-15_10:30:15 ttl=14d
SUPPRESS key=normalized_url last=2024-01-16_14:20:30 ttl=14d
```

### State File Location
```
.state/seen.json
```

### State File Format
```json
{
  "_metadata": {
    "created": 1705123456,
    "ttl_days": 14
  },
  "cache_key_1": 1705123456,
  "cache_key_2": 1705567890
}
```

## Testing Recommendations

### 1. Manual Testing
```bash
# Set short TTL for testing
SEEN_TTL_DAYS=0.1  # ~2.4 hours

# Check state file
cat .state/seen.json
```

### 2. Workflow Testing
```yaml
# Trigger manual run with different TTL
workflow_dispatch:
  inputs:
    seen_ttl_days: "1"  # 1 day for testing
```

### 3. Cache Inspection
```python
from state_utils import load_seen
seen = load_seen()
print(f"Cache entries: {len(seen)}")
```

## Benefits

1. **No Permanent Suppression**: Re-opened roles can alert again
2. **Spam Prevention**: Frequent updates won't flood alerts  
3. **Configurable**: Different TTLs for different notification types
4. **Robust**: Handles missing/malformed data gracefully
5. **Efficient**: Only processes items that pass TTL check
6. **Transparent**: Clear logging of TTL decisions

## Migration Notes

- **Backward Compatible**: Existing workflows continue to function
- **Gradual Rollout**: TTL checking is opt-in via environment variable
- **State Isolation**: TTL cache is separate from existing state files
- **Performance**: Minimal overhead added to existing filtering logic

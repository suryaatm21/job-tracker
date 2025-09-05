# Job Tracker Debugging Plan

## Current Issues Analysis & Solutions

### 1. **Missing Source Tags in format_utils.py**

**Problem**: Source attribution tags need to be added to `format_utils.py` for consistency with the util.py reference.

**Solution**: Add source handling to `format_job_line()` function.

**Implementation**:

```python
def format_job_line(company, title, season, location, url, html=False, source=None):
    # Add source tag logic here
    if source and source != "Simplify":
        title_with_source = f"{title} ({source})"
    else:
        title_with_source = title

    # Rest of formatting logic...
```

---

### 2. **DM-fast-watch.yml Cache Save Issue**

**Problem**: The "Save watcher state" step is **not running at all** in the workflow, despite the workflow completing successfully.

**Current Status**:

- Cache key has been fixed for **restore**: `dm-watcher-state-v1-2025-W36` (weekly rotation) ✅
- Cache **save** still uses old key: `dm-watcher-state-v1-${{ github.run_id }}` ❌
- Cache restore works: logs show "Cache hit for: dm-watcher-state-v1-2025-W36" ✅
- Cache save step runs but creates new cache entries instead of updating existing one ❌

**Root Cause Analysis**:

- **Key mismatch**: Restore uses weekly key, Save uses run_id key
- This creates a new cache entry every run instead of updating the existing weekly cache
- Save step **is running** but with wrong key, creating cache pollution (221+ entries)

**Cache Cleanup Priority**:

- Currently have 221+ cache entries from previous implementation mess
- Target: Only 2-3 cache entries total (dm-watcher, channel-digest, channel-digest-testing)
- Need to implement cache cleanup strategy

**Solution**:

1. **Fix cache key mismatch**: Update save step to use same weekly key as restore
2. **Add cache cleanup**: Implement strategy to delete old cache entries
3. **Add `if: always()`** to ensure save step runs even on failure

```yaml
# Fix the save step key to match restore:
- name: Save watcher state
  if: always() # Run even if previous steps fail
  uses: actions/cache/save@v4
  with:
    path: .state/dm-watcher
    key: dm-watcher-state-v1-${{ env.ISO_WEEK }} # Match restore key
```

**Additional Issues**:

- No Telegram messages despite successful runs suggests TTL cache is suppressing everything
- The logs show "Pre-populated 0 recent items" which means TTL cache starts empty but then gets populated immediately, suppressing all future alerts

---

### 3. **No Telegram Messages from DM Watcher**

**Problem**: Workflow runs successfully but no Telegram messages are sent.

**Root Cause Analysis from Logs**:

1. **Commit detection works**: Logs show commits are being fetched and processed
2. **File fetching works**: Both repos are successfully fetched
3. **TTL cache issue**: Cache starts empty but immediately gets populated, then suppresses all future items
4. **Window filtering**: Items might be outside the 24-hour window

**Debugging Steps**:

1. Check if items are within WINDOW_HOURS (24h)
2. Verify TTL cache logic - items might be getting marked as "seen" immediately
3. Add more detailed logging for filtering decisions
4. Test with `FORCE_WINDOW_HOURS=720` to capture 30 days of items

---

### 4. **Channel Digest "Other" Category Filtering**

**Problem**: Need to exclude "Other" category jobs from SimplifyJobs repo according to util.py reference.

**Analysis**: Current `classify_job_category()` function in `watch_repo.py` only allows "Software Engineering" and "Data Science, AI & Machine Learning" but this same logic needs to be applied to digest scripts.

**Solution**: Update `send_digest_multi.py` to add the same category filtering logic.

**Implementation**:

```python
# Add to send_digest_multi.py
ALLOWED_CATEGORIES = {
    "Software Engineering",
    "Data Science, AI & Machine Learning",
    "Hardware Engineering",
    "Quantitative Finance",
    "Product Management"
    # Exclude "Other" category
}

def classify_job_category(job):
    # Same logic as watch_repo.py but filter out "Other"
    if "category" in job and job["category"]:
        category = job["category"].strip()
        if category == "Other":
            return None  # Filter out "Other" category
        return category
    # ... rest of classification logic
```

---

### 5. **Duplicate Postings in Channel Digest**

**Problem**: Channel digest is sending duplicate job postings.

**Potential Causes**:

1. **TTL value too low** (14 days might be insufficient for channel digest)
2. **Deduplication key conflicts** between different repos
3. **Cache isolation issues** between digest-testing and main digest workflows
4. **Time window overlaps** causing same jobs to appear in multiple digests

**Analysis Required**:

1. Check if duplicate URLs are being processed with different dedup keys
2. Verify TTL cache is working properly for 14-day retention
3. Compare deduplication logic between DM watcher and digest scripts
4. Check if cache directories are properly isolated

**Solutions to Test**:

1. Increase `SEEN_TTL_DAYS` from 14 to 21 or 30 days for channel digest
2. Improve deduplication key logic to handle edge cases
3. Add debug logging for deduplication decisions
4. Verify cache isolation between testing and production workflows

---

## Implementation Plan

### Phase 1: Fix Critical Cache Issues

1. **Fix cache key mismatch in save step** - update to use weekly key like restore step ✅
2. **Add cache cleanup strategy** to reduce from 221+ caches to 2-3 total caches
3. **Add source tags to format_utils.py**
4. **Add detailed debug logging** to understand TTL and filtering decisions
5. **Add `if: always()`** to cache save step

### Phase 2: Test and Debug TTL Logic

1. **Run DM watcher manually** with `FORCE_WINDOW_HOURS=720` to test 30-day window
2. **Check TTL cache state** by examining saved cache files
3. **Test cache restore/save cycle** with known items

### Phase 3: Fix Channel Digest Issues

1. **Add "Other" category filtering** to digest scripts
2. **Investigate duplicate posting logic** in channel-digest-testing.yml
3. **Test deduplication across repos** with known overlapping items
4. **Adjust TTL values** if needed for channel digest frequency

### Phase 4: Testing and Validation

1. **Use channel-digest-testing.yml** (now scheduled hourly) for isolated testing
2. **Compare outputs** between testing and production workflows
3. **Monitor for duplicates** over multiple test runs
4. **Validate cache persistence** across workflow runs

---

## Diagnostic Commands for Testing

```bash
# Test DM watcher with extended window
gh workflow run dm-fast-watch.yml -f command=recent -f count=5 -f force_window_hours=720

# Test channel digest with debugging
gh workflow run channel-digest-testing.yml -f command=digest -f force_window_hours=720 -f count=10

# Clean up old cache entries (reduce from 221+ to 2-3 caches)
gh api repos/suryaatm21/job-tracker/actions/caches --paginate | \
  jq -r '.actions_caches[] | select(.key | contains("watcher") | not) | .id' | \
  head -200 | xargs -I {} gh api -X DELETE repos/suryaatm21/job-tracker/actions/caches/{}

# Check current cache state
gh api repos/suryaatm21/job-tracker/actions/caches | \
  jq '.actions_caches[] | {key: .key, size_in_bytes, created_at}' | head -10
```

---

## Success Criteria

1. **DM watcher**: Receives immediate alerts for new listings within 24 hours
2. **Cache persistence**: State properly saved and restored between runs
3. **No duplicates**: Channel digest doesn't send same job multiple times
4. **Category filtering**: "Other" category jobs are excluded from SimplifyJobs
5. **Source attribution**: Jobs show source when not from Simplify

This plan addresses the core caching, TTL, and filtering issues while providing a systematic approach to test and validate fixes.

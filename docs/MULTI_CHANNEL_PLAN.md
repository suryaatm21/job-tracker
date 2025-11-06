# Multi-Channel Digest Workflow Split Plan

## Overview
Split the existing `channel-digest.yml` workflow into **5 separate workflows**, each targeting a specific job category and sending to a dedicated Telegram channel.

---

## Workflow Breakdown

### 1. **channel-digest-swe-ml-bs.yml** (EXISTING - Renamed)
**Target Categories:** Software Engineering + Data Science/AI/ML  
**Degree Level:** Bachelor's (Undergraduate + non-PhD/MS roles)  
**Filter Logic:** 
- Include: `Software Engineering`, `Data Science, AI & Machine Learning`
- Exclude: PhD/MS-required roles (using `requires_graduate_degree()`)
- This is the **default undergraduate SWE/ML digest**

**New Environment Variables:**
```yaml
TELEGRAM_CHAT_ID_CHANNEL_SWE_ML_BS: <your_channel_id>
DIGEST_CATEGORIES: '["Software Engineering", "Data Science, AI & Machine Learning"]'
FILTER_GRADUATE_DEGREES: 'true'  # Exclude PhD/MS roles
```

---

### 2. **channel-digest-hardware.yml** (NEW)
**Target Categories:** Hardware Engineering  
**Degree Level:** All levels (BS/MS/PhD)  
**Filter Logic:**
- Include: `Hardware Engineering`
- No graduate degree filtering (allow all levels)

**New Environment Variables:**
```yaml
TELEGRAM_CHAT_ID_CHANNEL_HARDWARE: <your_channel_id>
DIGEST_CATEGORIES: '["Hardware Engineering"]'
FILTER_GRADUATE_DEGREES: 'false'  # Allow all degree levels
```

---

### 3. **channel-digest-quant.yml** (NEW)
**Target Categories:** Quantitative Finance  
**Degree Level:** All levels (BS/MS/PhD)  
**Filter Logic:**
- Include: `Quantitative Finance`
- No graduate degree filtering (allow all levels)

**New Environment Variables:**
```yaml
TELEGRAM_CHAT_ID_CHANNEL_QUANT: <your_channel_id>
DIGEST_CATEGORIES: '["Quantitative Finance"]'
FILTER_GRADUATE_DEGREES: 'false'  # Allow all degree levels
```

---

### 4. **channel-digest-pm.yml** (NEW)
**Target Categories:** Product Management  
**Degree Level:** All levels (BS/MS/PhD)  
**Filter Logic:**
- Include: `Product Management`
- No graduate degree filtering (allow all levels)

**New Environment Variables:**
```yaml
TELEGRAM_CHAT_ID_CHANNEL_PM: <your_channel_id>
DIGEST_CATEGORIES: '["Product Management"]'
FILTER_GRADUATE_DEGREES: 'false'  # Allow all degree levels
```

---

### 5. **channel-digest-swe-ml-phd.yml** (NEW)
**Target Categories:** Software Engineering + Data Science/AI/ML  
**Degree Level:** PhD/MS only  
**Filter Logic:**
- Include: `Software Engineering`, `Data Science, AI & Machine Learning`
- **Only include** PhD/MS-required roles (using `requires_graduate_degree()`)
- This is the **graduate-level SWE/ML digest**

**New Environment Variables:**
```yaml
TELEGRAM_CHAT_ID_CHANNEL_SWE_ML_PHD: <your_channel_id>
DIGEST_CATEGORIES: '["Software Engineering", "Data Science, AI & Machine Learning"]'
FILTER_GRADUATE_DEGREES: 'phd_only'  # NEW: Only include PhD/MS roles
```

---

## Implementation Plan

### Phase 1: Update Filtering Logic

#### 1.1 Enhance `job_filtering.py`
Add new filtering functions to support category-specific and degree-level filtering:

```python
# New function: Filter by specific categories for digest workflows
def is_allowed_category_for_digest(item, allowed_categories):
    """
    Check if item category matches the allowed categories for this digest.
    
    Args:
        item: Job listing dict
        allowed_categories: List of category strings (e.g., ["Hardware Engineering"])
    
    Returns:
        bool: True if category matches
    """
    cat = (item.get("category") or "").strip()
    
    if not cat:
        # No category - classify by title
        classified = classify_job_category(item)
        return classified in allowed_categories if classified else False
    
    # Map category to canonical form
    canonical = SIMPLIFY_CATEGORY_MAPPING.get(cat, cat)
    return canonical in allowed_categories


# New function: Inverse of requires_graduate_degree for PhD-only digest
def is_graduate_degree_only(item):
    """
    Return True if position is ONLY for graduate students (inverse filter).
    Use for PhD/MS-specific digest.
    """
    return requires_graduate_degree(item)


# Enhanced function: Support three modes for graduate filtering
def should_process_digest_item(item, allowed_categories, grad_filter_mode='false'):
    """
    Filter item for digest workflows with configurable category and degree filtering.
    
    Args:
        item: Job listing dict
        allowed_categories: List of allowed categories
        grad_filter_mode: 'true' (exclude grad), 'false' (all), 'phd_only' (only grad)
    
    Returns:
        tuple[bool, str]: (should_include, reason)
    """
    # Quality checks first
    if not should_include_item(item):
        return False, "quality"
    
    # Category filtering
    if not is_allowed_category_for_digest(item, allowed_categories):
        return False, "category"
    
    # Graduate degree filtering based on mode
    if grad_filter_mode == 'true':
        # Exclude PhD/MS roles (undergraduate digest)
        if requires_graduate_degree(item):
            return False, "graduate_degree"
    elif grad_filter_mode == 'phd_only':
        # Only include PhD/MS roles (graduate digest)
        if not requires_graduate_degree(item):
            return False, "not_graduate"
    # else: grad_filter_mode == 'false' - no filtering, allow all
    
    return True, "allowed"
```

#### 1.2 Update `send_digest_multi.py`
Add support for new environment variables:

```python
# New config variables
DIGEST_CATEGORIES = json.loads(os.environ.get("DIGEST_CATEGORIES", '["Software Engineering", "Data Science, AI & Machine Learning"]'))
GRAD_FILTER_MODE = os.environ.get("FILTER_GRADUATE_DEGREES", "false").lower()  # 'true', 'false', or 'phd_only'

# Update filtering logic to use new function
def should_include_listing(item):
    """Filter items using digest-specific requirements"""
    should_process, reason = should_process_digest_item(
        item, 
        DIGEST_CATEGORIES,
        GRAD_FILTER_MODE
    )
    
    if not should_process:
        debug_log(f"[FILTER] Excluded: {item.get('company_name')} - {item.get('title')[:50]}... | Reason: {reason}")
        return False
    
    return True
```

---

### Phase 2: Create New Workflow Files

#### 2.1 Rename existing workflow
```bash
# Keep existing workflow, just update it
# .github/workflows/channel-digest.yml stays as-is
```

Update the existing workflow:
- Change `name:` to "Channel Digest (SWE/ML - BS)"
- **Keep** `TELEGRAM_CHAT_ID_CHANNEL` secret (no rename needed)
- Add `DIGEST_CATEGORIES` and `FILTER_GRADUATE_DEGREES: 'true'`

#### 2.2 Create 4 new workflow files
Each workflow will be a copy of the renamed file with these changes:

**channel-digest-hardware.yml:**
```yaml
name: Channel Digest (Hardware - All Levels)
env:
  DIGEST_CATEGORIES: '["Hardware Engineering"]'
  FILTER_GRADUATE_DEGREES: 'false'
  # ... other env vars same
# Use TELEGRAM_CHAT_ID_CHANNEL_HARDWARE secret
```

**channel-digest-quant.yml:**
```yaml
name: Channel Digest (Quant Finance - All Levels)
env:
  DIGEST_CATEGORIES: '["Quantitative Finance"]'
  FILTER_GRADUATE_DEGREES: 'false'
```

**channel-digest-pm.yml:**
```yaml
name: Channel Digest (Product Management - All Levels)
env:
  DIGEST_CATEGORIES: '["Product Management"]'
  FILTER_GRADUATE_DEGREES: 'false'
```

**channel-digest-swe-ml-phd.yml:**
```yaml
name: Channel Digest (SWE/ML - PhD/MS)
env:
  DIGEST_CATEGORIES: '["Software Engineering", "Data Science, AI & Machine Learning"]'
  FILTER_GRADUATE_DEGREES: 'phd_only'
```

---

### Phase 3: GitHub Secrets Configuration

Add the following **4 new secrets** to your GitHub repository (keeping existing `TELEGRAM_CHAT_ID_CHANNEL`):

```
TELEGRAM_CHAT_ID_CHANNEL               # EXISTING - keep for SWE/ML BS digest
TELEGRAM_CHAT_ID_CHANNEL_HARDWARE      # NEW - hardware roles
TELEGRAM_CHAT_ID_CHANNEL_QUANT         # NEW - quant finance
TELEGRAM_CHAT_ID_CHANNEL_PM            # NEW - product management
TELEGRAM_CHAT_ID_CHANNEL_SWE_ML_PHD    # NEW - PhD/MS SWE/ML roles
```

**How to get Telegram Chat IDs:**
1. Create new Telegram channels/groups
2. Add your bot to each channel
3. Send a test message in each channel
4. Use `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates` to get chat IDs

---

### Phase 4: Workflow Schedule Optimization

To avoid rate limiting and spread out the load, stagger the schedules:

```yaml
# SWE/ML BS - Every 2 hours at :00
- cron: '0 */2 * * *'

# Hardware - Every 4 hours at :15
- cron: '15 */4 * * *'

# Quant Finance - Every 4 hours at :30
- cron: '30 */4 * * *'

# Product Management - Every 4 hours at :45
- cron: '45 */4 * * *'

# SWE/ML PhD - Every 6 hours at :00
- cron: '0 */6 * * *'
```

---

---

## GitHub Actions Constraints & Considerations

### Workflow Run Limits
GitHub has the following limits you should be aware of:

#### Free Tier (Public Repos):
- ✅ **Unlimited workflow runs** for public repositories
- ✅ **Unlimited minutes** for public repositories
- ⚠️ **API rate limits:** 5,000 requests/hour per authenticated user
- ⚠️ **1,000 API requests per hour** for GITHUB_TOKEN in workflows

#### Workflow Concurrency:
- **20 concurrent jobs** per free account across all workflows
- **500 concurrent jobs** per paid organization
- Our 5 workflows running every 2-6 hours = **very low concurrency risk**

#### Cache Storage:
- **10 GB total cache storage** per repository (across all branches)
- Caches not accessed for **7 days** are automatically deleted
- Oldest caches deleted first when limit is reached

### Impact Analysis for 5 Digest Workflows

**Current State:**
- 2 workflows: `dm-watcher` (every 5 min) + `channel-digest` (every 2 hrs)
- ~12 digest runs/day + ~288 DM runs/day = **~300 runs/day**

**After Split:**
- 1 DM workflow: ~288 runs/day
- 5 digest workflows: ~12-48 runs/day each = **~60-120 digest runs/day**
- **Total: ~350-410 runs/day** (small increase)

**Verdict:** ✅ **Well within GitHub's limits** for public repos

---

## Cache Management Updates

### Current Cache Keys:
```
dm-watcher-state-v1-{ISO_WEEK}-{run_id}
channel-digest-state-v1-{ISO_WEEK}-{run_id}
channel-digest-testing-state-v1-{ISO_WEEK}-{run_id}
```

### New Cache Keys (5 digest workflows):
```
dm-watcher-state-v1-{ISO_WEEK}-{run_id}              # Existing
channel-digest-swe-ml-bs-state-v1-{ISO_WEEK}-{run_id}  # Renamed from channel-digest-state-v1
channel-digest-hardware-state-v1-{ISO_WEEK}-{run_id}   # New
channel-digest-quant-state-v1-{ISO_WEEK}-{run_id}      # New
channel-digest-pm-state-v1-{ISO_WEEK}-{run_id}         # New
channel-digest-phd-state-v1-{ISO_WEEK}-{run_id}        # New
channel-digest-testing-state-v1-{ISO_WEEK}-{run_id}    # Existing (keep for testing)
```

### Update Required: `.github/workflows/cache-cleanup.yml`

**Change the `DEFAULT_KEEP_PREFIXES` to include all new cache prefixes:**

```yaml
env:
  DEFAULT_KEEP_PREFIXES: dm-watcher-state-v1,channel-digest-swe-ml-bs-state-v1,channel-digest-hardware-state-v1,channel-digest-quant-state-v1,channel-digest-pm-state-v1,channel-digest-phd-state-v1,channel-digest-testing-state-v1
  DEFAULT_KEEP_N: "3"
```

**Also update the workflow_dispatch default:**
```yaml
workflow_dispatch:
  inputs:
    keep_prefixes:
      description: "Comma-separated prefixes to keep"
      required: false
      default: "dm-watcher-state-v1,channel-digest-swe-ml-bs-state-v1,channel-digest-hardware-state-v1,channel-digest-quant-state-v1,channel-digest-pm-state-v1,channel-digest-phd-state-v1,channel-digest-testing-state-v1"
      type: string
```

### Cache Storage Estimate:
- Each cache file: ~50-200 KB (JSON state files are small)
- 7 cache prefixes × 3 versions kept × ~150 KB = **~3 MB total**
- **Well within 10 GB limit** ✅

---

## Migration Strategy

### Option 1: Clean Migration (Recommended)
1. **Keep existing `channel-digest.yml`** - update in place with new filtering
2. **Create 4 new workflow files** for other channels
3. **Update cache-cleanup.yml** before deploying new workflows
4. **Deploy all changes at once** in a single PR

**Advantages:**
- No breaking changes to existing channel
- All workflows start fresh with proper cache keys
- Clean separation of concerns

### Option 2: Gradual Rollout
1. Deploy workflows one at a time
2. Test each before adding the next
3. Update cache cleanup incrementally

**Advantages:**
- Lower risk - easier to debug issues
- Can validate each channel independently

**Disadvantages:**
- Takes longer to fully deploy
- More commits/PRs to manage

---

## Rate Limiting Considerations

### GitHub API Calls Per Workflow Run:
Each digest workflow run makes approximately:
- **2-4 API calls** to fetch listings from SimplifyJobs/vanshb03
- **1 API call** to get branch info
- **1-2 cache operations** (restore/save)
- **Total: ~5-8 API calls per run**

### Daily API Usage Estimate:
- 5 digest workflows × ~12-48 runs/day × ~6 API calls = **~360-1,440 API calls/day**
- DM workflow: ~288 runs/day × ~6 API calls = **~1,728 API calls/day**
- **Total: ~2,100-3,200 API calls/day**

**Compared to limit:** 1,000/hour = 24,000/day
**Usage: ~13% of hourly limit** ✅

### Telegram API Rate Limits:
- **30 messages/second** to different chats
- **1 message/second** to the same chat
- Our workflows send **1 batch every 2-6 hours** = **well within limits** ✅

---

## Recommended Schedule Distribution

To minimize concurrent runs and API burst traffic:

```yaml
# channel-digest.yml (SWE/ML BS)
schedule:
  - cron: '0 */2 * * *'    # Every 2 hours at :00 (12 runs/day)

# channel-digest-hardware.yml
schedule:
  - cron: '15 */4 * * *'   # Every 4 hours at :15 (6 runs/day)

# channel-digest-quant.yml
schedule:
  - cron: '30 */4 * * *'   # Every 4 hours at :30 (6 runs/day)

# channel-digest-pm.yml
schedule:
  - cron: '45 */4 * * *'   # Every 4 hours at :45 (6 runs/day)

# channel-digest-phd.yml
schedule:
  - cron: '0 */6 * * *'    # Every 6 hours at :00 (4 runs/day)
```

**Benefits:**
- **Staggered execution** - workflows don't all run simultaneously
- **15-minute spacing** - prevents API rate limit bursts
- **Reduced frequency for niche categories** (hardware, quant, PM, PhD) - less noise
- **Higher frequency for main channel** (SWE/ML BS) - fastest updates

**Total runs per day:** 12 + 6 + 6 + 6 + 4 = **34 digest runs/day** (vs 12 currently)

---

## Summary of Required Changes

### Code Changes:
1. ✅ **watch_repo.py** - Add batching support to DM workflow (DONE)
2. **job_filtering.py** - Add `is_allowed_category_for_digest()` and `should_process_digest_item()`
3. **send_digest_multi.py** - Update filtering to use new category-based logic

### Workflow Changes:
1. Update existing `channel-digest.yml` with category filtering (no rename)
2. Create 4 new workflow files (hardware, quant, pm, phd)
3. Update each workflow with appropriate env vars and secrets
4. **Update `cache-cleanup.yml`** with new cache prefixes

### GitHub Secrets:
1. Add 4 new `TELEGRAM_CHAT_ID_CHANNEL_*` secrets (keep existing `TELEGRAM_CHAT_ID_CHANNEL`)

### Cache Management:
1. Update cache cleanup workflow to handle 6 new cache prefixes
2. Estimated cache usage: ~3 MB (well within 10 GB limit)

---

## Testing Plan

1. **Test each workflow individually** using `workflow_dispatch`
2. **Verify filtering** - check that each channel receives only relevant categories
3. **Verify degree filtering** - ensure BS digest excludes PhD roles and vice versa
4. **Test batching** - send large digest to verify message splitting works
5. **Monitor for duplicates** - ensure same job doesn't appear in multiple channels

---

## Rollback Plan

If issues arise:
1. Disable new workflows (comment out schedule triggers)
2. Revert to original `channel-digest.yml`
3. Debug filtering issues in separate branch
4. Re-enable workflows once fixed

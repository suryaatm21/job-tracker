# Canonical Category Names Reference

## Critical: Exact Name Matching Required

All workflow `DIGEST_CATEGORIES` configurations **must** use the exact canonical category names as defined in `job_filtering.py`. The filtering logic uses exact string matching, so any deviation will cause jobs to be filtered out.

---

## Canonical Category Names

These are the **only** valid category names for digest workflows:

1. **"Software Engineering"**
   - Exact match required
   - Used for: SWE, software developer, full-stack, frontend, backend roles

2. **"Data Science, AI & Machine Learning"**
   - ⚠️ Note: Includes commas and ampersand `&`
   - NOT: "Data Science/AI/ML" ❌
   - NOT: "Data Science AI Machine Learning" ❌
   - Used for: Data science, ML engineer, AI researcher, NLP, CV roles

3. **"Hardware Engineering"**
   - Exact match required
   - Used for: Hardware engineer, embedded systems, FPGA roles

4. **"Quantitative Finance"**
   - ⚠️ Note: "Finance" not "Trading/Research"
   - NOT: "Quantitative Trading/Research" ❌
   - NOT: "Quant" ❌
   - Used for: Quant researcher, quant trader, quantitative analyst roles

5. **"Product Management"**
   - Exact match required
   - Used for: PM, product manager, TPM roles

---

## Workflow Configuration Examples

### ✅ Correct Configuration

```yaml
# Single category
DIGEST_CATEGORIES: '["Hardware Engineering"]'

# Multiple categories (PhD channel)
DIGEST_CATEGORIES: '["Software Engineering","Data Science, AI & Machine Learning","Hardware Engineering","Quantitative Finance","Product Management"]'

# SWE/ML channel
DIGEST_CATEGORIES: '["Software Engineering","Data Science, AI & Machine Learning"]'
```

### ❌ Incorrect Configuration

```yaml
# Wrong: Using slashes instead of commas
DIGEST_CATEGORIES: '["Software Engineering","Data Science/AI/ML"]'

# Wrong: Missing ampersand
DIGEST_CATEGORIES: '["Data Science, AI Machine Learning"]'

# Wrong: Using "Trading/Research" instead of "Finance"
DIGEST_CATEGORIES: '["Quantitative Trading/Research"]'

# Wrong: Using abbreviations
DIGEST_CATEGORIES: '["SWE","ML"]'
```

---

## Current Workflow Mappings

| Workflow File | Categories Used |
|--------------|----------------|
| `channel-digest.yml` | "Software Engineering", "Data Science, AI & Machine Learning" |
| `channel-digest-hardware.yml` | "Hardware Engineering" |
| `channel-digest-quant.yml` | "Quantitative Finance" |
| `channel-digest-pm.yml` | "Product Management" |
| `channel-digest-phd.yml` | All 5 canonical categories |

---

## How Categories Are Matched

The `is_allowed_category_for_digest()` function in `job_filtering.py`:

1. Checks if job has a `category` field
2. Maps it to canonical name using `SIMPLIFY_CATEGORY_MAPPING`
3. Compares against workflow's `DIGEST_CATEGORIES` list
4. Returns `True` only on **exact match** (after canonicalization)

### Example Flow:

```python
# Job from SimplifyJobs repo:
job = {"category": "AI/ML/Data", "title": "ML Engineer"}

# Step 1: Map "AI/ML/Data" → "Data Science, AI & Machine Learning" (canonical)
# Step 2: Check if "Data Science, AI & Machine Learning" in allowed_categories
# Step 3: Return True if exact match, False otherwise

# ✅ Matches: '["Data Science, AI & Machine Learning"]'
# ❌ No match: '["Data Science/AI/ML"]'
```

---

## Adding New Categories

If you need to add a new category:

1. **Update `job_filtering.py`:**
   - Add to `ALLOWED_CATEGORIES_DIGEST` set
   - Add mapping variants to `SIMPLIFY_CATEGORY_MAPPING`
   - Update `classify_job_category()` if needed

2. **Update workflow files:**
   - Use the **exact** canonical name in `DIGEST_CATEGORIES`

3. **Test thoroughly:**
   - Run workflow with `FORCE_WINDOW_HOURS: 720`
   - Verify jobs are being routed correctly

---

## Validation Checklist

Before deploying workflow changes:

- [ ] All category names match `ALLOWED_CATEGORIES_DIGEST` exactly
- [ ] No abbreviations used (no "SWE", "ML", "Quant")
- [ ] No slashes in "Data Science, AI & Machine Learning"
- [ ] "Quantitative Finance" not "Quantitative Trading/Research"
- [ ] JSON array syntax is valid (quotes, commas)
- [ ] Tested with manual workflow dispatch

---

## Common Mistakes to Avoid

1. **Using SimplifyJobs label names directly** - These vary and need canonicalization
2. **Creating workflow-specific abbreviations** - Always use canonical names
3. **Forgetting commas/ampersands** - Exact character match required
4. **Copy-paste from documentation** - Old docs may have outdated names

---

## Source of Truth

The **only** authoritative source for category names is:
```
.github/scripts/job_filtering.py
  → ALLOWED_CATEGORIES_DIGEST (set of canonical names)
  → SIMPLIFY_CATEGORY_MAPPING (label → canonical mappings)
```

When in doubt, check these two data structures.

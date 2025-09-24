import os, json, requests, pathlib, base64, time
from datetime import datetime
from urllib.parse import urlparse
from github_helper import fetch_file_content, fetch_file_json, debug_log, gh_get, GH
from state_utils import (
    load_seen, save_seen, should_alert_item, 
    get_cache_key, format_epoch_for_log, should_include_item
)
from format_utils import format_location, log_location_resolution, format_job_line
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

# Category filtering: Only allow these categories from SimplifyJobs repo
ALLOWED_CATEGORIES = {
    "Software Engineering", 
    "Data Science, AI & Machine Learning"
}

if RESET_LAST_SEEN:
    debug_log(f"[CONFIG] RESET_LAST_SEEN=true - will ignore cached last_seen SHAs")
if BACK_ONE:
    debug_log(f"[CONFIG] BACK_ONE=true - will set last_seen to parent of latest commit")

# TTL configuration for seen cache
SEEN_TTL_DAYS = int(os.getenv("SEEN_TTL_DAYS", "14"))

# State directory (configurable for cache separation)
STATE_DIR = pathlib.Path(os.getenv("STATE_DIR", ".state"))
STATE_DIR.mkdir(exist_ok=True, parents=True)

def get_default_branch(repo):
    """Get default branch for a repository"""
    repo_info = gh_get(f"{GH}/repos/{repo}")
    branch = repo_info["default_branch"]
    debug_log(f"[BRANCH] {repo} default branch: {branch}")
    return branch

def detect_listings_path(repo, branch):
    """Auto-detect listings.json path within repo"""
    for path in [".github/scripts/listings.json", "listings.json"]:
        try:
            gh_get(f"{GH}/repos/{repo}/contents/{path}", ref=branch)
            debug_log(f"[PATH] {repo} found listings at: {path}")
            return path
        except requests.HTTPError as e:
            if e.response.status_code != 404:
                raise
    debug_log(f"[PATH] {repo} using fallback path: {LISTINGS_PATH}")
    return LISTINGS_PATH  # fallback

def get_repo_entries(repo, per_page=100):
    """Fetch commits for a repository"""
    return gh_get(f"{GH}/repos/{repo}/commits", per_page=per_page)

def commit_detail(repo, sha):
    return gh_get(f"{GH}/repos/{repo}/commits/{sha}")

def get_file_at(repo, ref, path):
    """Fetch file content at specific git reference using robust helper"""
    try:
        content = fetch_file_content(repo, path, ref)
        debug_log(f"[FILE] {repo}:{path}@{ref[:8] if ref else 'HEAD'} → {len(content)} bytes")
        return content
    except Exception as e:
        # file might not exist in older commit
        if "404" in str(e):
            debug_log(f"[FILE] {repo}:{path}@{ref[:8] if ref else 'HEAD'} → not found (404)")
            return None
        debug_log(f"[FILE] {repo}:{path}@{ref[:8] if ref else 'HEAD'} → error: {e}")
        raise

def watched(path):
    return any(path == p or path.startswith(p) for p in WATCH_PATHS)

def classify_job_category(job):
    """
    Classify job category based on title and existing category field.
    Returns category string or None if job should be filtered out.
    """
    # First check if there's an existing category field (SimplifyJobs has this)
    if "category" in job and job["category"]:
        category = job["category"].strip()
        if category in ALLOWED_CATEGORIES:
            return category
        # If existing category is not in allowed list, filter out
        return None
    
    # Fallback: classify by title if no category exists
    title = job.get("title", "").lower()
    
    # Data Science & AI & Machine Learning (first priority for overlapping terms)
    if any(term in title for term in ["data science", "artificial intelligence", "data scientist", "ai &", "machine learning", "ml", "data analytics", "data analyst", "research eng", "nlp", "computer vision", "research sci", "data eng"]):
        return "Data Science, AI & Machine Learning"
    
    # Software Engineering (second priority)
    elif any(term in title for term in ["software", "software eng", "software dev", "product engineer", "fullstack", "full-stack", "full stack", "frontend", "front end", "front-end", "backend", "back end", "back-end", "founding engineer", "mobile dev", "mobile engineer", "forward deployed", "forward-deployed"]):
        return "Software Engineering"
    
    # Filter out other categories (Hardware, Quant, Product, Other, etc.)
    else:
        return None

def should_process_repo_item(item, repo):
    """
    Filter items based on:
    1. Basic quality checks (visible, has URL)
    2. Repo-specific filtering (only SimplifyJobs for category filtering)  
    3. Category filtering for SimplifyJobs
    """
    # Basic quality checks
    if not should_include_item(item):
        return False, "quality"
    
    # Only apply category filtering to SimplifyJobs repo
    if repo == "SimplifyJobs/Summer2026-Internships":
        category = classify_job_category(item)
        if category is None:
            return False, "category"
        # Store the category for later use
        item["_classified_category"] = category
        return True, "allowed"
    
    # Other repos: only basic quality checks
    return True, "allowed"

def send_telegram(text):
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

def to_epoch(v):
    try:
        return int(v)
    except Exception:
        try:
            from datetime import datetime as _dt
            return int(_dt.fromisoformat(str(v)).timestamp())
        except Exception:
            return -1

def process_repo_entries(repo, listings_path, last_seen_sha, seen=None, ttl_seconds=None, now_epoch=None):
    """Get new entries from a single repository"""
    debug_log(f"[WATCH] Processing repo: {repo}, last_seen={last_seen_sha[:8] if last_seen_sha else 'None'}")
    
    commits = get_repo_entries(repo, per_page=20)
    if not commits:
        debug_log(f"[WATCH] {repo} → No commits found")
        return []
    
    newest_sha = commits[0]['sha'][:8]
    debug_log(f"[COMMITS] {repo} newest={newest_sha}, considered_commits={len(commits)}")

    # Collect unseen commits (newest→oldest until last_seen)
    new = []
    for c in commits:
        if c["sha"] == last_seen_sha:
            debug_log(f"[COMMITS] {repo} found last seen commit: {c['sha'][:8]}")
            break
        new.append(c)

    if not new:
        if last_seen_sha and commits and commits[0]["sha"] == last_seen_sha:
            debug_log(f"[INFO] {repo} last_seen is already newest; likely no pushes since previous run")
        else:
            debug_log(f"[COMMITS] {repo} no new commits since last run")
        return []
    
    debug_log(f"[WATCH] {repo} → Processing {len(new)} new commits")
    
    # Accumulate new entries from all commits
    all_new_entries = []
    for c in reversed(new):  # oldest→newest
        sha = c["sha"]
        parent = c["parents"][0]["sha"] if c["parents"] else None
        debug_log(f"[DELTA] {repo} → commit={sha[:8]}, parent={parent[:8] if parent else 'None'}")
        
        files = [f["filename"] for f in commit_detail(repo, sha).get("files", [])]
        # Only react if any watched path changed in this commit
        watched_files = [f for f in files if watched(f)]
        
        if not watched_files:
            debug_log(f"[DELTA] {repo} → commit {sha[:8]} has no watched files (files: {files[:3]}...)")
            continue
        
        debug_log(f"[DELTA] {repo} → commit {sha[:8]} changed watched files: {watched_files}")
        
        # Fetch listings file content at before/after refs
        after_txt = get_file_at(repo, sha, listings_path)
        before_txt = get_file_at(repo, parent, listings_path) if parent else None
        
        try:
            after = json.loads(after_txt) if after_txt else []
            before = json.loads(before_txt) if before_txt else []
            debug_log(f"[DELTA] {repo} → commit {sha[:8]} parsed: before={len(before)}, after={len(after)}")
        except Exception as e:
            debug_log(f"[DELTA] {repo} → commit {sha[:8]} JSON parse failed: {e}")
            continue
        
        # Find new entries in this commit
        before_keys = {get_dedup_key(x) for x in before if get_dedup_key(x) and should_include_item(x)}
        commit_new_count = 0
        commit_window_count = 0
        commit_category_count = 0
        
        for item in after:
            key = get_dedup_key(item)
            if key and key not in before_keys:
                commit_new_count += 1
                
                # Apply time window filter first
                ts_val = item.get(DATE_FIELD, item.get(DATE_FALLBACK))
                ts = to_epoch(ts_val)
                cutoff = time.time() - (WINDOW_HOURS * 3600.0)
                
                if ts >= cutoff:
                    commit_window_count += 1
                    
                    # Apply repo-specific filtering (category filtering for SimplifyJobs)
                    should_process, filter_reason = should_process_repo_item(item, repo)
                    if should_process:
                        commit_category_count += 1
                        
                        # Check TTL cache to see if we should alert for this item (if TTL enabled)
                        should_alert = True
                        if seen is not None and ttl_seconds is not None and now_epoch is not None:
                            _flag, _reason = should_alert_item(item, seen, ttl_seconds, now_epoch)
                            should_alert = _flag
                        
                        if should_alert:
                            title = item.get("title", "")
                            company = item.get("company_name", "")
                            url = get_primary_url(item)
                            season = get_unified_season(item)  # Use unified season handling
                            
                            # Format location with DM mode (CA/NY/NJ resolution)
                            locations = item.get("locations", [])
                            location = format_location(locations, mode="dm")
                            
                            # Log location resolution for debugging
                            if locations and len(locations) > 1:
                                log_location_resolution(company, title, locations, location, "dm")
                            
                            line = format_job_line(company, title, season, location, url, html=False)
                            all_new_entries.append({
                                "key": key,
                                "line": line,
                                "ts": ts,
                                "repo": repo,
                                "item": item
                            })
        
        debug_log(f"[DELTA] {repo} → commit {sha[:8]} new_entries={commit_new_count}, after_window={commit_window_count}, after_category={commit_category_count}")
    
    debug_log(f"[WATCH] {repo} → total accumulated entries: {len(all_new_entries)}")
    return all_new_entries

def main():
    debug_log(f"[CONFIG] Starting multi-repo watch for: {TARGET_REPOS}")
    debug_log(f"[CONFIG] Watching paths: {WATCH_PATHS}")
    debug_log(f"[CONFIG] Listings file path hint: {LISTINGS_PATH}")
    debug_log(f"[CONFIG] WINDOW_HOURS={WINDOW_HOURS}, SEEN_TTL_DAYS={SEEN_TTL_DAYS}")
    debug_log(f"[CONFIG] DATE_FIELD={DATE_FIELD}, DATE_FALLBACK={DATE_FALLBACK}")
    
    # Load seen cache and calculate TTL (use STATE_DIR)
    seen_cache_path = STATE_DIR / "seen.json"
    seen = load_seen(str(seen_cache_path))
    ttl_seconds = SEEN_TTL_DAYS * 24 * 3600
    now_epoch = int(time.time())
    
    # If cache is empty (first run or after reset), pre-populate with recent jobs
    # to avoid alerting on old listings
    if not seen:
        debug_log("TTL cache is empty, pre-populating with recent jobs to avoid old alerts")
        for repo in TARGET_REPOS:
            try:
                default_branch = get_default_branch(repo)
                listings_path = detect_listings_path(repo, default_branch)
                listings = fetch_file_json(repo, listings_path, default_branch)
                
                # Pre-populate cache with jobs from last 7 days
                cutoff = now_epoch - (7 * 24 * 3600)  # 7 days ago
                # Use a timestamp from 30 days ago to mark as "old seen" instead of now
                old_seen_timestamp = now_epoch - (30 * 24 * 3600)  # 30 days ago
                
                for item in listings[:100]:  # Limit to avoid processing too many
                    if should_include_item(item):
                        ts_val = item.get(DATE_FIELD, item.get(DATE_FALLBACK))
                        ts = to_epoch(ts_val)
                        if ts >= cutoff:  # Only cache recent items
                            cache_key = get_cache_key(item)
                            # Mark with old timestamp so TTL will allow re-alerting soon
                            seen[cache_key] = old_seen_timestamp
                            
                debug_log(f"Pre-populated {len([k for k in seen.keys()])} recent items from {repo} with old timestamps")
            except Exception as e:
                debug_log(f"Failed to pre-populate cache from {repo}: {e}")
        
        if seen:
            save_seen(seen, SEEN_TTL_DAYS, str(seen_cache_path))
            debug_log(f"Saved pre-populated cache with {len(seen)} items")
    
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
            listings_path = detect_listings_path(repo, default_branch)
            
            # Get new entries from this repo with TTL filtering
            repo_entries = process_repo_entries(repo, listings_path, last_seen, seen, ttl_seconds, now_epoch)
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
    
    # Apply TTL-based filtering (using cache key from the original item when available)
    final_entries = []
    for entry in deduped_entries:
        # Prefer cache key from the original item for consistency
        cache_key = None
        if entry.get("item"):
            cache_key = get_cache_key(entry["item"])
        if not cache_key:
            # Fallback: derive from dedup key tuple
            cache_key = entry["key"][1] if isinstance(entry["key"], tuple) else str(entry["key"])
        
        # Check TTL (simplified - assumes entry represents a valid item)
        last_alert = seen.get(cache_key)
        should_alert = True
        reason = "new"
        
        if last_alert is not None:
            if now_epoch - last_alert <= ttl_seconds:
                should_alert = False
                reason = "suppressed"
                debug_log(f"SUPPRESS key={cache_key} last={format_epoch_for_log(last_alert)} ttl={SEEN_TTL_DAYS}d")
            else:
                reason = "ttl_expired"
        
        if should_alert:
            final_entries.append(entry)
            if reason == "ttl_expired":
                debug_log(f"ALLOW-TTL key={cache_key} last={format_epoch_for_log(last_alert)} ttl={SEEN_TTL_DAYS}d")
    
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

import os, json, requests, pathlib, base64, time
from datetime import datetime
from urllib.parse import urlparse
from github_helper import fetch_file_content, fetch_file_json, debug_log, gh_get, GH
from state_utils import (
    load_seen, save_seen, should_alert_item, 
    get_cache_key, format_epoch_for_log, should_include_item
)

# Multi-repo configuration
TARGET_REPOS = json.loads(os.environ.get("TARGET_REPOS", '["vanshb03/Summer2026-Internships"]'))
WATCH_PATHS = set(json.loads(os.environ.get("WATCH_PATHS", '["listings.json"]')))

# Path to the listings file within each repo
LISTINGS_PATH = os.getenv("LISTINGS_PATH", ".github/scripts/listings.json")

# Date configuration for filtering new listings
DATE_FIELD = os.getenv("DATE_FIELD", "date_posted")
DATE_FALLBACK = os.getenv("DATE_FALLBACK", "date_updated")
WINDOW_HOURS = float(os.getenv("WINDOW_HOURS", "24"))  # Only alert for items in last N hours (default 24 hours)

# TTL configuration for seen cache
SEEN_TTL_DAYS = int(os.getenv("SEEN_TTL_DAYS", "14"))

# State directory (configurable for cache separation)
STATE_DIR = pathlib.Path(os.getenv("STATE_DIR", ".state"))
STATE_DIR.mkdir(exist_ok=True, parents=True)

def get_default_branch(repo):
    """Get default branch for a repository"""
    repo_info = gh_get(f"{GH}/repos/{repo}")
    return repo_info["default_branch"]

def detect_listings_path(repo, branch):
    """Auto-detect listings.json path within repo"""
    for path in [".github/scripts/listings.json", "listings.json"]:
        try:
            gh_get(f"{GH}/repos/{repo}/contents/{path}", ref=branch)
            return path
        except requests.HTTPError as e:
            if e.response.status_code != 404:
                raise
    return LISTINGS_PATH  # fallback

def get_repo_entries(repo, per_page=100):
    """Fetch commits for a repository"""
    return gh_get(f"{GH}/repos/{repo}/commits", per_page=per_page)

def commit_detail(repo, sha):
    return gh_get(f"{GH}/repos/{repo}/commits/{sha}")

def get_file_at(repo, ref, path):
    """Fetch file content at specific git reference using robust helper"""
    try:
        return fetch_file_content(repo, path, ref)
    except Exception as e:
        # file might not exist in older commit
        if "404" in str(e):
            return None
        raise

def watched(path):
    return any(path == p or path.startswith(p) for p in WATCH_PATHS)

def send_telegram(text):
    debug_log(f"Attempting to send Telegram message: {text[:100]}...")
    tok = os.getenv("TELEGRAM_BOT_TOKEN"); chat = os.getenv("TELEGRAM_CHAT_ID")
    if not tok or not chat: 
        debug_log("Missing Telegram credentials - BOT_TOKEN or CHAT_ID not set")
        return False
    
    try:
        response = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": text, "disable_web_page_preview": True})
        debug_log(f"Telegram API response: {response.status_code} - {response.text}")
        response.raise_for_status()
        return True
    except Exception as e:
        debug_log(f"Telegram send failed: {e}")
        return False

def normalize_url(url):
    """Normalize URL to scheme+host+path for deduplication"""
    if not url:
        return None
    try:
        parsed = urlparse(url)
        # Keep scheme, netloc (host), and path; drop query and fragment
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip('/')
    except Exception:
        return url

def get_dedup_key(item):
    """Get deduplication key: normalized_url -> id -> (company.lower(), title.lower())"""
    # Prioritize URL over ID since IDs conflict between repos but URLs are more reliable
    norm_url = normalize_url(item.get("url"))
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
    debug_log(f"Processing repo: {repo}")
    
    commits = get_repo_entries(repo, per_page=20)
    if not commits:
        debug_log(f"No commits found in {repo}")
        return []
    
    debug_log(f"Found {len(commits)} recent commits in {repo}")
    
    # Collect unseen commits (newest→oldest until last_seen)
    new = []
    for c in commits:
        if c["sha"] == last_seen_sha:
            debug_log(f"Found last seen commit: {c['sha']} in {repo}")
            break
        new.append(c)
    
    if not new:
        debug_log(f"No new commits since last run in {repo}")
        return []
    
    debug_log(f"Processing {len(new)} new commits in {repo}")
    
    # Accumulate new entries from all commits
    all_new_entries = []
    for c in reversed(new):  # oldest→newest
        sha = c["sha"]
        parent = c["parents"][0]["sha"] if c["parents"] else None
        debug_log(f"Processing commit: {sha[:8]} in {repo}")
        
        files = [f["filename"] for f in commit_detail(repo, sha).get("files", [])]
        watched_files = [f for f in files if watched(f)]
        
        if not watched_files:
            debug_log(f"No watched files in commit {sha[:8]} of {repo}")
            continue
        
        debug_log(f"Watched files changed in {repo}: {watched_files}")
        
        # Fetch listings file content at before/after refs
        after_txt = get_file_at(repo, sha, listings_path)
        before_txt = get_file_at(repo, parent, listings_path) if parent else None
        
        try:
            after = json.loads(after_txt) if after_txt else []
            before = json.loads(before_txt) if before_txt else []
            debug_log(f"Listings count in {repo} - Before: {len(before)}, After: {len(after)}")
        except Exception as e:
            debug_log(f"JSON parse failed in {repo}: {e}")
            continue
        
        # Find new entries in this commit
        before_keys = {get_dedup_key(x) for x in before if get_dedup_key(x) and should_include_item(x)}
        for item in after:
            # Skip items with falsy URLs or marked as not visible
            if not should_include_item(item):
                continue
                
            key = get_dedup_key(item)
            if key and key not in before_keys:
                ts_val = item.get(DATE_FIELD, item.get(DATE_FALLBACK))
                ts = to_epoch(ts_val)
                
                # Apply time window filter
                cutoff = time.time() - (WINDOW_HOURS * 3600.0)
                if ts >= cutoff:
                    # Check TTL cache to see if we should alert for this item (if TTL enabled)
                    should_alert = True
                    if seen is not None and ttl_seconds is not None and now_epoch is not None:
                        should_alert = should_alert_item(item, seen, ttl_seconds, now_epoch)
                    
                    if should_alert:
                        title = item.get("title", "")
                        company = item.get("company_name", "")
                        url = item.get("url", "")
                        season = get_unified_season(item)  # Use unified season handling
                        season_str = f"[{season}]" if season else ""
                        # Add source tag with author name to distinguish repos
                        repo_author = repo.split('/')[0] if '/' in repo else repo
                        line = f"• {company} — {title} {season_str} [{repo_author}] {url}".strip()
                        all_new_entries.append({
                            "key": key,
                            "line": line,
                            "ts": ts,
                            "repo": repo
                        })
                        
                        # Update seen cache with current timestamp (if TTL enabled)
                        if seen is not None and now_epoch is not None:
                            cache_key = get_cache_key(item)
                            seen[cache_key] = now_epoch
    
    return all_new_entries

def main():
    debug_log(f"Starting multi-repo watch for: {TARGET_REPOS}")
    debug_log(f"Watching paths: {WATCH_PATHS}")
    debug_log(f"Listings file path hint: {LISTINGS_PATH}")
    debug_log(f"SEEN_TTL_DAYS={SEEN_TTL_DAYS}")
    
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
                for item in listings[:100]:  # Limit to avoid processing too many
                    if should_include_item(item):
                        ts_val = item.get(DATE_FIELD, item.get(DATE_FALLBACK))
                        ts = to_epoch(ts_val)
                        if ts >= cutoff:  # Only cache recent items
                            cache_key = get_cache_key(item)
                            seen[cache_key] = now_epoch
                            
                debug_log(f"Pre-populated {len([k for k in seen.keys()])} recent items from {repo}")
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
        debug_log(f"Last seen SHA for {repo}: {last_seen}")
        
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
                debug_log(f"Updated last seen SHA for {repo}: {newest_sha}")
        
        except Exception as e:
            debug_log(f"Error processing repo {repo}: {e}")
            continue
    
    if not all_entries:
        debug_log("No new entries found across all repos")
        # Still save seen cache to prune old entries
        save_seen(seen, SEEN_TTL_DAYS)
        return

    debug_log(f"Found {len(all_entries)} new entries before deduplication")
    
    # Deduplicate across all repos (keep first occurrence by timestamp desc)
    seen_keys = set()
    deduped_entries = []
    
    # Sort by timestamp descending to prefer newer entries
    all_entries.sort(key=lambda x: x["ts"], reverse=True)
    
    for entry in all_entries:
        if entry["key"] not in seen_keys:
            seen_keys.add(entry["key"])
            deduped_entries.append(entry)
    
    debug_log(f"After deduplication: {len(deduped_entries)} unique entries")
    
    # Apply TTL-based filtering
    final_entries = []
    for entry in deduped_entries:
        # Reconstruct item dict for TTL checking (we need the raw item data)
        # Note: This is a simplified approach - in practice, we'd need to pass the item through
        # For now, we'll create a minimal item dict from the entry data
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
            # Mark as seen for future runs
            seen[cache_key] = now_epoch
            if reason == "ttl_expired":
                debug_log(f"ALLOW-TTL key={cache_key} last={format_epoch_for_log(last_alert)} ttl={SEEN_TTL_DAYS}d")
    
    debug_log(f"After TTL filtering: {len(final_entries)} entries to alert")
    
    if final_entries:
        # Sort final entries by company name alphabetically, then by timestamp desc
        final_entries.sort(key=lambda x: (x["line"].split(" — ")[0].replace("• ", "").lower(), -x["ts"]))
        lines = [entry["line"] for entry in final_entries[:10]]
        
        header = f"🔔 DM Alert: New internships detected ({len(final_entries)})"
        message = "\n".join([header] + lines)
        
        debug_log(f"Sending message with {len(lines)} lines")
        send_telegram(message)
    else:
        debug_log("No messages to send after TTL filtering")
    
    # Save updated seen cache
    save_seen(seen, SEEN_TTL_DAYS, str(seen_cache_path))

if __name__ == "__main__":
    main()

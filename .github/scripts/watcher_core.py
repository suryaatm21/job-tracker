#!/usr/bin/env python3
"""
Core watching logic for the job tracker.
Handles commit processing and entry detection for repository monitoring.
"""
import json
import time
from github_helper import debug_log
from repo_utils import get_repo_entries, commit_detail, get_file_at, watched
from job_filtering import should_process_repo_item
from dedup_utils import get_dedup_key, get_primary_url, get_unified_season, to_epoch
from state_utils import should_alert_item, should_include_item
from format_utils import format_location, log_location_resolution, format_job_line


def process_repo_entries(repo, listings_path, last_seen_sha, watch_paths, 
                        window_hours, date_field, date_fallback,
                        seen=None, ttl_seconds=None, now_epoch=None):
    """Get new entries from a single repository"""
    debug_log(f"[WATCH] Processing repo: {repo}, last_seen={last_seen_sha[:8] if last_seen_sha else 'None'}")
    
    commits = get_repo_entries(repo, per_page=20)
    if not commits:
        debug_log(f"[INFO] {repo} has no commits available to scan")
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
            debug_log(f"[INFO] {repo} no new commits since last run")
        return []

    debug_log(f"[INFO] {repo} processing {len(new)} new commits")
    
    # Accumulate new entries from all commits
    all_new_entries = []
    for c in reversed(new):  # oldest→newest
        sha = c["sha"]
        parent = c["parents"][0]["sha"] if c["parents"] else None
        debug_log(f"[DELTA] {repo} → commit={sha[:8]}, parent={parent[:8] if parent else 'None'}")
        
        files = [f["filename"] for f in commit_detail(repo, sha).get("files", [])]
        # Only react if any watched path changed in this commit
        watched_files = [f for f in files if watched(f, watch_paths)]
        
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
                ts_val = item.get(date_field, item.get(date_fallback))
                ts = to_epoch(ts_val)
                cutoff = time.time() - (window_hours * 3600.0)
                
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
    
    debug_log(f"[SUMMARY] {repo} accumulated {len(all_new_entries)} new entries")
    return all_new_entries

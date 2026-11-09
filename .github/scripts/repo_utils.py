#!/usr/bin/env python3
"""
GitHub repository utilities for the job tracker.
Handles commit fetching, file operations, and repository metadata.
"""
import os
from github_helper import fetch_file_content, fetch_file_json, debug_log, gh_get, GH

DEFAULT_LISTINGS_PATH = ".github/scripts/listings.json"

# (repo, path_hint) -> (branch, path); one lookup per repo per process
_LISTINGS_SOURCE_CACHE = {}


def get_default_branch(repo):
    """Get default branch for a repository"""
    repo_info = gh_get(f"{GH}/repos/{repo}")
    branch = repo_info["default_branch"]
    debug_log(f"[BRANCH] {repo} default branch: {branch}")
    return branch


def resolve_listings_source(repo, path_hint=DEFAULT_LISTINGS_PATH):
    """
    Return (branch, path) for a repo's listings file on its actual default branch.

    Listing repos do not all use `main` (both Summer2027-Internships repos default
    to `dev`), so never assume a branch name: ask GitHub, then probe for the file
    on that branch. Results are cached per process.
    """
    cache_key = (repo, path_hint)
    if cache_key not in _LISTINGS_SOURCE_CACHE:
        branch = get_default_branch(repo)
        path = detect_listings_path(repo, branch, path_hint)
        _LISTINGS_SOURCE_CACHE[cache_key] = (branch, path)
    return _LISTINGS_SOURCE_CACHE[cache_key]


def fetch_listings(repo, path_hint=DEFAULT_LISTINGS_PATH):
    """
    Fetch and parse a repo's listings JSON from its default branch.

    Returns the parsed listings (a list for SimplifyJobs/vanshb03-style repos).
    Raises on fetch or parse failure; callers decide whether to skip the repo.
    """
    branch, path = resolve_listings_source(repo, path_hint)
    listings = fetch_file_json(repo, path, ref=branch)
    debug_log(f"[LISTINGS] {repo}@{branch}:{path} → {len(listings) if isinstance(listings, list) else 'object'} items")
    return listings


def detect_listings_path(repo, branch, fallback_path=DEFAULT_LISTINGS_PATH):
    """Auto-detect listings.json path within repo"""
    # First check the configured/fallback path if it's not one of the defaults
    paths_to_try = [DEFAULT_LISTINGS_PATH, "listings.json"]
    
    # If fallback_path is not in the default list, try it first
    if fallback_path not in paths_to_try:
        paths_to_try.insert(0, fallback_path)
    # If fallback_path is in the list, make sure it's tried first
    elif fallback_path in paths_to_try:
        paths_to_try.remove(fallback_path)
        paths_to_try.insert(0, fallback_path)
    
    for path in paths_to_try:
        try:
            gh_get(f"{GH}/repos/{repo}/contents/{path}", ref=branch)
            debug_log(f"[PATH] {repo} found listings at: {path}")
            return path
        except Exception as e:
            if "404" not in str(e):
                debug_log(f"[PATH] {repo} error checking {path}: {e}")
    
    debug_log(f"[PATH] {repo} using fallback path: {fallback_path}")
    return fallback_path  # fallback


def get_repo_entries(repo, per_page=100):
    """Fetch commits for a repository"""
    return gh_get(f"{GH}/repos/{repo}/commits", per_page=per_page)


def commit_detail(repo, sha):
    """Get detailed information about a specific commit"""
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


def watched(path, watch_paths):
    """Check if a file path should be watched based on configured watch paths"""
    return any(path == p or path.startswith(p) for p in watch_paths)
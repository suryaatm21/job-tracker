#!/usr/bin/env python3
"""
GitHub repository utilities for the job tracker.
Handles commit fetching, file operations, and repository metadata.
"""
import os
from github_helper import fetch_file_content, debug_log, gh_get, GH


def get_default_branch(repo):
    """Get default branch for a repository"""
    repo_info = gh_get(f"{GH}/repos/{repo}")
    branch = repo_info["default_branch"]
    debug_log(f"[BRANCH] {repo} default branch: {branch}")
    return branch


def detect_listings_path(repo, branch, fallback_path=".github/scripts/listings.json"):
    """Auto-detect listings.json path within repo"""
    for path in [".github/scripts/listings.json", "listings.json"]:
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
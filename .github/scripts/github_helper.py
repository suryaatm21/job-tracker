#!/usr/bin/env python3
"""
Robust GitHub Contents API helper with fallback strategies for large files.

Handles the issue where GitHub Contents API may return empty content for large files
by implementing multiple fallback strategies.
"""
import os
import json
import base64
import requests
from datetime import datetime

# GitHub API configuration
GH = "https://api.github.com"
HEADERS = {
    "Authorization": f"Bearer {os.getenv('GH_TOKEN', '')}",
    "Accept": "application/vnd.github+json",
}

def debug_log(msg):
    """Lightweight debug logging with timestamp"""
    print(f"[{datetime.now().isoformat()}] DEBUG: {msg}")

def gh_get(url, **params):
    """Call GitHub API and return parsed JSON, raising on HTTP errors"""
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def fetch_file_content(repo, path, ref=None):
    """
    Fetch file content from GitHub repo with robust fallback strategies.
    
    Args:
        repo: Repository in format "owner/repo"
        path: File path within the repository
        ref: Git reference (branch, tag, SHA). Defaults to repo's default branch.
    
    Returns:
        str: File content as text
        
    Raises:
        RuntimeError: If all fallback strategies fail
    """
    debug_log(f"Fetching {repo}:{path} (ref={ref or 'default'})")
    
    # Strategy 1: Contents API with base64 decoding
    try:
        # Detect if ref is a commit SHA (40-character hex string) vs branch name
        if ref:
            # If ref looks like a commit SHA (40-char hex), use it directly
            if len(ref) == 40 and all(c in '0123456789abcdef' for c in ref.lower()):
                params = {"ref": ref}
            else:
                # Assume it's a branch name and add heads/ prefix
                params = {"ref": f"heads/{ref}"}
        else:
            params = {}
        
        data = gh_get(f"{GH}/repos/{repo}/contents/{path}", **params)
        
        if isinstance(data, dict):
            # Check for base64 encoded content
            if data.get("encoding") == "base64" and data.get("content"):
                content = base64.b64decode(data["content"]).decode("utf-8")
                debug_log(f"Contents API success: {len(content)} bytes from {repo}:{path}")
                return content
            
            # Check for direct content
            if "content" in data and data["content"]:
                content = data["content"]
                debug_log(f"Contents API success: {len(content)} bytes from {repo}:{path}")
                return content
            
            # Strategy 2: Use download_url if available
            if "download_url" in data and data["download_url"]:
                debug_log(f"Contents API returned empty content, trying download_url for {repo}:{path}")
                r = requests.get(data["download_url"], timeout=30)
                r.raise_for_status()
                content = r.text
                debug_log(f"Download URL success: {len(content)} bytes from {repo}:{path}")
                return content
            
            # Strategy 3: Git blobs API using SHA
            if "sha" in data:
                debug_log(f"Trying git blobs API with SHA {data['sha'][:8]} for {repo}:{path}")
                blob_data = gh_get(f"{GH}/repos/{repo}/git/blobs/{data['sha']}")
                if blob_data.get("encoding") == "base64" and blob_data.get("content"):
                    content = base64.b64decode(blob_data["content"]).decode("utf-8")
                    debug_log(f"Git blobs API success: {len(content)} bytes from {repo}:{path}")
                    return content
        
        debug_log(f"Contents API returned unexpected structure for {repo}:{path}")
    
    except Exception as e:
        debug_log(f"Contents API failed for {repo}:{path}: {e}")
    
    # All strategies failed
    raise RuntimeError(f"Failed to fetch content from {repo}:{path} using all available strategies")

def fetch_file_json(repo, path, ref=None):
    """
    Fetch and parse JSON file from GitHub repo.
    
    Args:
        repo: Repository in format "owner/repo"
        path: File path within the repository
        ref: Git reference (branch, tag, SHA). Defaults to repo's default branch.
    
    Returns:
        Any: Parsed JSON data
        
    Raises:
        RuntimeError: If file cannot be fetched
        json.JSONDecodeError: If content is not valid JSON
    """
    content = fetch_file_content(repo, path, ref)
    try:
        data = json.loads(content)
        debug_log(f"JSON parse success: {len(data) if isinstance(data, list) else 'object'} items from {repo}:{path}")
        return data
    except json.JSONDecodeError as e:
        debug_log(f"JSON parse failed for {repo}:{path}: {e}")
        debug_log(f"Content preview (first 200 chars): {content[:200]}")
        raise

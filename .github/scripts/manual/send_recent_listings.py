#!/usr/bin/env python3
"""
Send the N most recent listings by date field (default: date_posted) to Telegram from multiple repos.

Configuration via environment variables:
- TARGET_REPOS (JSON array of repos) [required] OR TARGET_REPO (single repo) [fallback]
- LISTINGS_PATH (path within repo) [default: auto-detect]
- GH_TOKEN [recommended for higher rate limits]
- TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID [required to send messages]
- DATE_FIELD [default: "date_posted"], DATE_FALLBACK [default: "date_updated"]
- COUNT [default: 10], applied after the location gate
- DM_METROS (JSON list) [optional]: when set, only listings in those metros are sent, by the
  DM watcher's location gate; unset (the channel digests) sends listings from every location
"""
import os, sys
# Shared modules live one level up in .github/scripts; Python only adds this file's own dir to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json
from datetime import datetime
from dedup_utils import get_dedup_key
from repo_utils import fetch_listings
from telegram_utils import send_message
from format_utils import format_location, format_job_line
from opportunity_filters import optional_location_gate, passes_location_gate

# Multi-repo support with fallback to single repo
TARGET_REPOS_STR = os.getenv("TARGET_REPOS")
if TARGET_REPOS_STR:
    TARGET_REPOS = json.loads(TARGET_REPOS_STR)
else:
    # Fallback to single repo for backward compatibility
    TARGET_REPOS = [os.environ["TARGET_REPO"]]

LISTINGS_PATH = os.getenv("LISTINGS_PATH", "listings.json")
DATE_FIELD = os.getenv("DATE_FIELD", "date_posted")
DATE_FALLBACK = os.getenv("DATE_FALLBACK", "date_updated")
COUNT = max(1, int(os.getenv("COUNT", "10") or 10))
MESSAGE_PREFIX = os.getenv("MESSAGE_PREFIX", "")  # Context prefix for messages

def get_unified_season(item):
    """Get unified season label: season field or first term or empty string"""
    # SimplifyJobs uses 'season' field, Vansh uses 'terms' array
    if item.get("season"):
        return item["season"]
    elif item.get("terms") and len(item["terms"]) > 0:
        return item["terms"][0]
    else:
        return ""

def should_include_listing(item):
    """Filter out listings with missing/invalid URLs for better quality"""
    url = item.get("url", "").strip()
    return bool(url)  # Skip entries with falsy URLs

# Category filtering: Only allow these categories from SimplifyJobs repo
ALLOWED_CATEGORIES = {
    "Software Engineering", 
    "Data Science, AI & Machine Learning"
}

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
    if not should_include_listing(item):
        return False, "quality"
    
    # Only apply category filtering to SimplifyJobs repos (owner match survives yearly repo renames)
    if repo.startswith("SimplifyJobs/"):
        category = classify_job_category(item)
        if category is None:
            return False, "category"
        # Store the category for later use
        item["_classified_category"] = category
        return True, "allowed"
    
    # Other repos: only basic quality checks
    return True, "allowed"

def send_telegram(text: str) -> bool:
    """Send a Telegram message to the configured chat (prints instead when DRY_RUN=true).

    Returns True on 2xx HTTP status, False otherwise. Does not print sensitive data.
    """
    ok, status, body = send_message(
        os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID"), text, parse_mode="HTML"
    )
    print("Telegram status:", status)
    if not ok:
        # Print the response text only on error for diagnostics
        print(body)
    return ok

def to_epoch(v) -> int:
    try:
        return int(v)
    except Exception:
        try:
            return int(datetime.fromisoformat(str(v)).timestamp())
        except Exception:
            return -1

def sort_key(item) -> int:
    v = item.get(DATE_FIELD, item.get(DATE_FALLBACK))
    return to_epoch(v)

def main() -> int:
    location_gate = optional_location_gate(os.getenv("DM_METROS"))
    all_items = []
    seen_keys = set()
    
    # Process each repository
    for repo in TARGET_REPOS:
        try:
            # Resolve the listings path on the repo's actual default branch, then fetch it
            print(f"Fetching listings from {repo} (path hint: {LISTINGS_PATH})")
            data = fetch_listings(repo, LISTINGS_PATH)
            
            if not isinstance(data, list):
                print(f"Unexpected JSON structure in {repo}")
                continue
            
            repo_items = []
            for x in data:
                # Apply repo-specific filtering (category filtering for SimplifyJobs)
                should_process, filter_reason = should_process_repo_item(x, repo)
                if not should_process:
                    if filter_reason == "category":
                        # Debug: log filtered items for SimplifyJobs repo
                        debug_category = classify_job_category(x) or "unknown"
                        print(f"Filtered out {x.get('company_name', 'Unknown')} - {x.get('title', 'Unknown')} (category: {debug_category})")
                    continue
                    
                if sort_key(x) > 0:  # Valid timestamp
                    # Gate before dedup so an out-of-metro copy cannot claim the key
                    if not passes_location_gate(location_gate, x):
                        continue
                    dedup_key = get_dedup_key(x)
                    if dedup_key and dedup_key not in seen_keys:
                        seen_keys.add(dedup_key)
                        # Add repo info for source tracking
                        x["_source_repo"] = repo
                        repo_items.append(x)
            
            all_items.extend(repo_items)
            print(f"Found {len(repo_items)} unique listings from {repo}")
            
        except Exception as e:
            print(f"Error processing repo {repo}: {e}")
            continue

    if not all_items:
        send_telegram("No recent listings found.")
        return 0

    # Sort by company name alphabetically, then by timestamp descending
    sorted_items = sorted(all_items, key=lambda x: (x.get("company_name", x.get("company", "")).lower(), -sort_key(x)))
    top = sorted_items[:COUNT]

    lines = []
    for x in top:
        title = x.get("title", "")
        company = x.get("company_name", x.get("company", ""))
        url = x.get("url", x.get("application_link", ""))
        season = get_unified_season(x)
        
        # Format location - use "dm" mode for manual commands to provide specific location info
        locations = x.get("locations", [])
        location = format_location(locations, mode="dm")
        
        # Include a relative date if available
        ts = sort_key(x)
        when = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") if ts > 0 else ""
        
        # Format the line using the helper, then add date info
        line = format_job_line(company, title, season, location, url, html=True)
        if when:
            # Insert the date before the URL
            line_parts = line.split('\n')
            if len(line_parts) == 2:
                line = f"{line_parts[0]} ({when})\n{line_parts[1]}"
        lines.append(line)

    # Add context prefix if provided
    prefix = f"{MESSAGE_PREFIX}: " if MESSAGE_PREFIX else ""
    header = f"{prefix}Most recent listings: {len(top)}"
    send_telegram("\n\n".join([header, *lines]))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

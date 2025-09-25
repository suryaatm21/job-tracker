#!/usr/bin/env python3
"""
Deduplication utilities for the job tracker.
Handles URL normalization, dedup key generation, and data processing.
"""
from urllib.parse import urlparse
from datetime import datetime


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
    """Convert value to epoch timestamp"""
    try:
        return int(v)
    except Exception:
        try:
            return int(datetime.fromisoformat(str(v)).timestamp())
        except Exception:
            return -1
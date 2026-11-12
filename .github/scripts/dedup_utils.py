#!/usr/bin/env python3
"""
Deduplication utilities for the job tracker.
Handles URL normalization, dedup key generation, and data processing.
"""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit
from datetime import datetime


# Query parameters that only track where a click came from. Everything else in a
# query string is kept, because some boards identify the posting there
# (careers.withwaymo.com/jobs?gh_jid=8350687).
_TRACKING_PARAMS = {
    "ref", "ref_src", "referrer", "source", "src", "gh_src", "lever-source", "lever-origin",
    "trk", "fbclid", "gclid", "mc_cid", "mc_eid", "_hsenc", "_hsmi",
}


def normalize_url(url):
    """
    The one URL key for dedup and the seen memory (see SPEC.md, DEDUP-1 to DEDUP-3):
    lowercase scheme and host, path with each run of slashes cut to one and no
    trailing slash, and the query minus tracking parameters (utm_* and
    _TRACKING_PARAMS), sorted. The fragment is dropped. Returns None for a blank or unparseable URL, or one with no scheme,
    host or path.
    """
    url = (url or "").strip()
    if not url:
        return None
    try:
        parts = urlsplit(url)
        query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if not (k.lower().startswith("utm_") or k.lower() in _TRACKING_PARAMS))
    except ValueError:
        return None
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/")
    if not parts.netloc and not path:
        return None
    key = f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}"
    return f"{key}?{urlencode(query)}" if query else key


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


def in_window(item, cutoff, date_field, date_fallback):
    return to_epoch(item.get(date_field, item.get(date_fallback))) >= cutoff

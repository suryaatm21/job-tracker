#!/usr/bin/env python3
"""
Send all listings whose date_posted equals today's date (UTC) from multiple repos.

Configuration via environment variables:
- TARGET_REPOS (JSON array of repos) [required] OR TARGET_REPO (single repo) [fallback]
- LISTINGS_PATH (path within repo) [default: auto-detect]
- GH_TOKEN [recommended for higher rate limits]
- TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID [required to send messages]
- DATE_FIELD [default: "date_posted"], DATE_FALLBACK [default: "date_updated"]
- DM_METROS (JSON list) [optional]: when set, only listings in those metros are sent, by the
  DM watcher's location gate; unset (the channel digests) sends listings from every location

Assumptions and behavior:
- Dates are stored as Unix epoch seconds. If not, ISO-8601 is also accepted.
- "Today" is computed in UTC to match GitHub-hosted runners.
- The script sends at most 15 detailed entries to avoid overly long messages.
- Deduplicates across repos using URL-first strategy.
"""
import os, sys
# Shared modules live one level up in .github/scripts; Python only adds this file's own dir to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json
from datetime import datetime, timezone
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

MESSAGE_PREFIX = os.getenv("MESSAGE_PREFIX", "")  # Context prefix for messages

LISTINGS_PATH = os.getenv("LISTINGS_PATH", "listings.json")
DATE_FIELD = os.getenv("DATE_FIELD", "date_posted")
DATE_FALLBACK = os.getenv("DATE_FALLBACK", "date_updated")

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
    """Convert value to epoch seconds. Supports int-like and ISO-8601 strings.

    Returns -1 on failure so callers can skip invalid rows.
    """
    try:
        return int(v)
    except Exception:
        try:
            return int(datetime.fromisoformat(str(v)).timestamp())
        except Exception:
            return -1

def main() -> int:
    """Entrypoint: fetch listings from multiple repos, filter those posted today (UTC), and notify."""
    location_gate = optional_location_gate(os.getenv("DM_METROS"))
    gated_out = set()  # dedup keys, so a listing on both boards counts once
    today = datetime.now(timezone.utc).date()
    all_todays = []
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
            
            repo_todays = []
            for x in data:
                # Skip items with falsy URLs for better quality
                if not should_include_listing(x):
                    continue
                    
                ts = x.get(DATE_FIELD, x.get(DATE_FALLBACK))
                epoch = to_epoch(ts)
                if epoch <= 0:
                    continue
                    
                d = datetime.fromtimestamp(epoch, tz=timezone.utc).date()
                if d == today:
                    # Gate before dedup so an out-of-metro copy cannot claim the key
                    if not passes_location_gate(location_gate, x):
                        gated_out.add(get_dedup_key(x))
                        continue
                    dedup_key = get_dedup_key(x)
                    if dedup_key and dedup_key not in seen_keys:
                        seen_keys.add(dedup_key)
                        # Add repo info for source tracking
                        x["_source_repo"] = repo
                        repo_todays.append(x)
            
            all_todays.extend(repo_todays)
            print(f"Found {len(repo_todays)} unique listings from {repo} for today")
            
        except Exception as e:
            print(f"Error processing repo {repo}: {e}")
            continue

    if location_gate is not None:
        metros = ", ".join(m["label"] for m in location_gate.metros)
        print(f"Location gate (DM_METROS): dropped {len(gated_out)} listings outside {metros}")

    if not all_todays:
        # No new items today: keep the notification short
        send_telegram("No new listings posted today.")
        return 0

    # Sort by company name alphabetically
    all_todays.sort(key=lambda x: x.get("company_name", x.get("company", "")).lower())

    lines = []
    for x in all_todays[:15]:  # Limit to 15 entries
        title = x.get("title", "")
        company = x.get("company_name", x.get("company", ""))
        url = x.get("url", x.get("application_link", ""))
        season = get_unified_season(x)
        
        # Format location - use "dm" mode for manual commands to provide specific location info
        locations = x.get("locations", [])
        location = format_location(locations, mode="dm")
        
        line = format_job_line(company, title, season, location, url, html=True)
        lines.append(line)

    # Add context prefix if provided
    prefix = f"{MESSAGE_PREFIX}: " if MESSAGE_PREFIX else ""
    header = f"{prefix}New listings today: {len(all_todays)}"
    send_telegram("\n\n".join([header, *lines]))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

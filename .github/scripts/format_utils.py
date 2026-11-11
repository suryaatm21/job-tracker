#!/usr/bin/env python3
"""
Formatting utilities for job listing display.
Includes location formatting logic for different notification modes.
"""
import json
import os
import re
from functools import lru_cache

# Metro city lists live in the private digest config. A metro's cities count for a
# state through its state_tokens: "ca" -> SF Bay Area and Los Angeles, "ny" -> NYC.
_METRO_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "private_channel", "config.json")
# DM label -> state code first, then names. Checked in this order.
_STATE_TERMS = {
    "California": ("ca", "california"),
    "New York": ("ny", "new york", "nyc", "new york city"),
    "New Jersey": ("nj", "new jersey"),
}


def _word_pattern(terms):
    """Match any of terms as whole words, so "ca" never matches inside "Chicago"."""
    alternation = "|".join(re.escape(t) for t in sorted(set(terms), key=len, reverse=True))
    return re.compile(r"(?<![a-z])(?:" + alternation + r")(?![a-z])")


@lru_cache(maxsize=1)
def _state_patterns():
    """(label, pattern) per state: its code, names, and the metro cities that need no state."""
    try:
        with open(_METRO_CONFIG, encoding="utf-8") as f:
            metros = json.load(f).get("location", {}).get("metros", [])
    except (OSError, ValueError):
        metros = []
    patterns = []
    for label, terms in _STATE_TERMS.items():
        cities = [c.lower() for m in metros if terms[0] in m.get("state_tokens", [])
                  for c in m.get("cities", [])]
        patterns.append((label, _word_pattern(list(terms) + cities)))
    return patterns


def format_location(locations, mode="digest"):
    """
    Format location information for job listings.
    
    Args:
        locations (list[str]): List of location strings from job listing
        mode (str): Either "digest" or "dm" to control multi-location behavior
        
    Returns:
        str: Formatted location string or empty string if no usable location
        
    Examples:
        format_location([], "digest") -> ""
        format_location(["San Francisco, CA"], "digest") -> "San Francisco, CA"
        format_location(["CA", "NY"], "digest") -> "Multi-location"
        format_location(["San Francisco, CA", "New York, NY"], "dm") -> "California"
        format_location(["Seattle, WA", "Austin, TX"], "dm") -> "Multi-location"
    """
    if not locations or not isinstance(locations, list):
        return ""
    
    # Filter out empty/None locations
    valid_locations = [loc.strip() for loc in locations if loc and str(loc).strip()]
    
    if not valid_locations:
        return ""
    
    # Single location - return as-is
    if len(valid_locations) == 1:
        return valid_locations[0]
    
    # Multiple locations
    if mode == "digest":
        return "Multi-location"
    
    elif mode == "dm":
        # California, then New York, then New Jersey: whole words only, so
        # Chicago is not "ca" and Nanjing is not "nj"
        for label, pattern in _state_patterns():
            if any(pattern.search(loc.lower()) for loc in valid_locations):
                return label

        # Default for multi-location in DM mode
        return "Multi-location"
    
    else:
        # Unknown mode, default to multi-location
        return "Multi-location"

def log_location_resolution(company, title, locations, resolved_location, mode):
    """
    Log location resolution for debugging purposes.
    Only logs when mode="dm" and multi-location resolves to CA/NY/NJ.
    
    Args:
        company (str): Company name
        title (str): Job title
        locations (list[str]): Original locations list
        resolved_location (str): The resolved location string
        mode (str): The formatting mode used
    """
    if mode == "dm" and len(locations) > 1 and resolved_location in ["California", "New York", "New Jersey"]:
        print(f"Resolved multi-location to {resolved_location} for {company} {title}")

def format_job_line(company, title, season, location, url, source=None, html=False):
    """
    Format a complete job listing line with optional location.
    
    Args:
        company (str): Company name
        title (str): Job title
        season (str): Season/term information
        location (str): Formatted location string (from format_location)
        url (str): Job application URL
        html (bool): Whether to use HTML formatting (for Telegram)
        
    Returns:
        str: Formatted job line
        
    Examples:
        format_job_line("Tesla", "SWE Intern", "Summer 2026", "California", "https://...")
        -> "• <b>Tesla</b> — SWE Intern [Summer 2026] [California] https://..."
        
        format_job_line("Google", "DS Intern", "Summer 2026", "", "https://...", html=True)
        -> "• <b>Google</b> — DS Intern [Summer 2026]\nhttps://..."
    """
    # Build bracket content - use separate brackets for season and location
    bracket_parts = []
    if season:
        bracket_parts.append(f"[{season}]")
    if location:
        bracket_parts.append(f"[{location}]")
    
    bracket_str = " ".join(bracket_parts) if bracket_parts else ""
    
    # Optionally append source to title if provided and not Simplify
    title_with_source = title
    if source and str(source).strip() and str(source).strip().lower() != "simplify":
        title_with_source = f"{title} ({source})"

    # Format based on output type
    if html:
        # HTML format for Telegram channel digest
        company_formatted = f"<b>{company}</b>" if company else ""
        line = f"• {company_formatted} — {title_with_source} {bracket_str}".strip()
        return f"{line}\n{url}".strip()
    else:
        # Plain text format for DM alerts
        line = f"• {company} — {title_with_source} {bracket_str} {url}".strip()
        return line

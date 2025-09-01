#!/usr/bin/env python3
"""
Formatting utilities for job listing display.
Includes location formatting logic for different notification modes.
"""

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
        # Check for CA/California first
        for loc in valid_locations:
            loc_lower = loc.lower()
            if "ca" in loc_lower or "california" in loc_lower:
                return "California"
        
        # Check for New York
        for loc in valid_locations:
            loc_lower = loc.lower()
            if "new york" in loc_lower:
                return "New York"
        
        # Check for NJ/New Jersey
        for loc in valid_locations:
            loc_lower = loc.lower()
            if "nj" in loc_lower or "new jersey" in loc_lower:
                return "New Jersey"
        
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

def format_job_line(company, title, season, location, url, repo_author, html=False):
    """
    Format a complete job listing line with optional location.
    
    Args:
        company (str): Company name
        title (str): Job title
        season (str): Season/term information
        location (str): Formatted location string (from format_location)
        url (str): Job application URL
        repo_author (str): Repository author for source attribution
        html (bool): Whether to use HTML formatting (for Telegram)
        
    Returns:
        str: Formatted job line
        
    Examples:
        format_job_line("Tesla", "SWE Intern", "Summer 2026", "California", "https://...", "vanshb03")
        -> "• Tesla — SWE Intern [Summer 2026 | California] [vanshb03] https://..."
        
        format_job_line("Google", "DS Intern", "Summer 2026", "", "https://...", "SimplifyJobs", html=True)
        -> "• <b>Google</b> — DS Intern [Summer 2026] [SimplifyJobs]\nhttps://..."
    """
    # Build bracket content
    bracket_parts = []
    if season:
        bracket_parts.append(season)
    if location:
        bracket_parts.append(location)
    
    bracket_str = f"[{' | '.join(bracket_parts)}]" if bracket_parts else ""
    
    # Format based on output type
    if html:
        # HTML format for Telegram channel digest
        company_formatted = f"<b>{company}</b>" if company else ""
        line = f"• {company_formatted} — {title} {bracket_str} [{repo_author}]".strip()
        return f"{line}\n{url}".strip()
    else:
        # Plain text format for DM alerts
        line = f"• {company} — {title} {bracket_str} [{repo_author}] {url}".strip()
        return line

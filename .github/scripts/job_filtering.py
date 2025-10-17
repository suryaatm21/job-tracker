#!/usr/bin/env python3
"""
Job filtering and categorization utilities.
Handles category-based filtering for different use cases (DM alerts vs digest).
"""

import re
from state_utils import should_include_item
from github_helper import debug_log

# For monitoring category distribution (optional - set via env var)
import os
_CATEGORY_MONITORING = os.getenv("CATEGORY_MONITORING", "false").lower() == "true"
_category_stats = {}

# Graduate degree filtering (MS/PhD requirements) - configurable via env var
FILTER_GRADUATE_DEGREES = os.getenv("FILTER_GRADUATE_DEGREES", "true").lower() == "true"

# SimplifyJobs actual category labels → canonical mapping for DM filtering
# Maps various label formats (abbreviations, variations) to canonical categories
SIMPLIFY_CATEGORY_MAPPING = {
    # Software Engineering variations
    "Software": "Software Engineering",
    "SWE": "Software Engineering",
    "Software Eng": "Software Engineering",
    "Software Development": "Software Engineering",
    "Software Engineer": "Software Engineering",
    "Software Engineering": "Software Engineering",  # Already canonical
    
    # Data Science / AI / ML variations
    "AI/ML/Data": "Data Science, AI & Machine Learning", 
    "Data Science": "Data Science, AI & Machine Learning",
    "Machine Learning": "Data Science, AI & Machine Learning",
    "AI": "Data Science, AI & Machine Learning",
    "ML": "Data Science, AI & Machine Learning",
    "Data": "Data Science, AI & Machine Learning",
    "AI & ML": "Data Science, AI & Machine Learning",
    "Data Science, AI & Machine Learning": "Data Science, AI & Machine Learning"  # Already canonical
}


# Category filtering: Only allow these categories for DM alerts (strict filtering)
ALLOWED_CATEGORIES_DM = {
    "Software Engineering", 
    "Data Science, AI & Machine Learning"
}

# Category filtering for digest: allow several categories, exclude "Other"
ALLOWED_CATEGORIES_DIGEST = {
    "Software Engineering",
    "Data Science, AI & Machine Learning",
    "Hardware Engineering",
    "Quantitative Finance",
    "Product Management",
}


def classify_job_category(job):
    """
    Classify job category based on title and existing category field.
    Returns category string or None if job should be filtered out.
    
    Handles case-insensitive matching and various label formats.
    """
    # First check if there's an existing category field (SimplifyJobs has this)
    if "category" in job and job["category"]:
        category = job["category"].strip()
        
        # Try exact match first (case-sensitive)
        canonical_category = SIMPLIFY_CATEGORY_MAPPING.get(category)
        if canonical_category:
            return canonical_category
        
        # Try case-insensitive match for robustness
        category_lower = category.lower()
        for key, value in SIMPLIFY_CATEGORY_MAPPING.items():
            if key.lower() == category_lower:
                debug_log(f"[CATEGORY-CASE-MATCH] Matched '{category}' → '{value}' (case-insensitive)")
                return value
            
        # Check if it's already in canonical form (case-insensitive)
        for canonical in ALLOWED_CATEGORIES_DM:
            if canonical.lower() == category_lower:
                return canonical
        
        # Category exists but not mappable - fall back to title classification
        # This handles cases where SimplifyJobs adds new categories or changes formatting
        debug_log(f"[CATEGORY-UNMAPPED] Unknown category '{category}' for job: {job.get('company_name', 'Unknown')} - {job.get('title', 'Unknown')[:50]}... | Falling back to title classification")
    
    # Fallback: classify by title if no category exists or category not mappable
    title = job.get("title", "").lower()
    
    # Data Science & AI & Machine Learning (first priority for overlapping terms)
    # Includes common abbreviations: ML, NLP, CV
    data_ml_terms = [
        "data science", "data scientist", "data engineer", "data eng",
        "artificial intelligence", "ai engineer", "ai researcher", "ai &",
        "machine learning", "ml engineer", "ml researcher",
        "data analytics", "data analyst",
        "research engineer", "research eng", "research scientist", "research sci",
        "nlp", "natural language", "computer vision", "cv engineer",
        "deep learning", "neural network"
    ]
    if any(term in title for term in data_ml_terms):
        return "Data Science, AI & Machine Learning"
    
    # Software Engineering (second priority)
    # Includes common abbreviations: SWE, SDE, full-stack variants
    software_terms = [
        "software engineer", "software eng", "swe", "sde",
        "software developer", "software dev",
        "product engineer",
        "fullstack", "full-stack", "full stack",
        "frontend", "front end", "front-end",
        "backend", "back end", "back-end",
        "founding engineer",
        "mobile developer", "mobile dev", "mobile engineer",
        "forward deployed", "forward-deployed",
        "application developer", "app developer"
    ]
    if any(term in title for term in software_terms):
        return "Software Engineering"
    
    # Filter out other categories (Hardware, Quant, Product, Other, etc.)
    return None


def requires_graduate_degree(item):
    """
    Check if a job posting explicitly requires a graduate degree (Masters/PhD).
    
    Filters out positions explicitly for PhD/MS students or requiring advanced degrees.
    Conservative approach: only filters when there's clear evidence to avoid
    false positives on ambiguous "Graduate Internship" programs.
    
    Args:
        item: Job dict with 'title' field from SimplifyJobs/vanshb03 repos
        
    Returns:
        bool: True if the position explicitly requires MS/PhD
    """
    # Only title field is available in SimplifyJobs/vanshb03 data
    title = (item.get("title") or "").lower()
    
    # Explicit PhD/doctorate mentions are the clearest signal
    if re.search(r'\bphd\b', title) or re.search(r'ph\.d\.?', title) or re.search(r'\bdoctorate\b', title):
        return True
    
    # "Current PhD" or "PhD Student" or "PhD Candidate"
    if re.search(r'\bcurrent\s+phd\b', title):
        return True
    
    # Masters/MS explicitly required or for MS students
    masters_patterns = [
        r'\bms\s+(required|preferred|student|candidate)\b',
        r'\bmasters?\s+(required|preferred|student|candidate|degree)\b',
        r"\bmaster'?s\s+(required|preferred|student|candidate|degree)\b",
        r'\bcurrent\s+(ms|masters?|master\'?s)\b',
    ]
    if any(re.search(pattern, title) for pattern in masters_patterns):
        return True
    
    # "Graduate Student" or "Grad Student" (not just "Graduate Internship")
    if re.search(r'\bgraduate\s+student\b', title) or re.search(r'\bgrad\s+student\b', title):
        return True
    
    # "Graduate Researcher" suggests advanced degree
    if re.search(r'\bgraduate\s+researcher\b', title):
        return True
    
    # Note: We intentionally DO NOT filter generic "Graduate Internship" as these
    # programs often accept both undergrad and grad students (e.g., CVS Health)
    
    return False


def should_process_repo_item(item, repo):
    """
    Check if item should be processed based on repository-specific rules.
    For SimplifyJobs: strict category filtering
    For other repos: include all items (but still run quality checks)
    """
    # Always run quality gate first (active filtering, visibility, URL checks)
    if not should_include_item(item):
        return False, "quality"
    
    # Filter out graduate degree requirements (MS/PhD)
    if requires_graduate_degree(item):
        return False, "graduate_degree"
    
    # Apply repo-specific filtering
    if "SimplifyJobs" in repo:
        category = classify_job_category(item)
        if not category:
            return False, "category"
    
    return True, "allowed"


def is_allowed_category_digest(item):
    """Return True if item category is allowed for digest.
    Policy: drop explicit "Other"; allow known useful categories; allow unknowns.
    """
    cat = (item.get("category") or "").strip()
    
    # Optional monitoring: track category distribution
    if _CATEGORY_MONITORING:
        _category_stats[cat or "(no category)"] = _category_stats.get(cat or "(no category)", 0) + 1
    
    if not cat:
        return True  # No category info → keep
    if cat == "Other":
        return False
    if cat in ALLOWED_CATEGORIES_DIGEST:
        return True
    # Category present but not in allowlist and not "Other" → keep (soft filter)
    return True


def get_category_stats():
    """Return category distribution stats (only if monitoring enabled)"""
    if _CATEGORY_MONITORING and _category_stats:
        sorted_stats = sorted(_category_stats.items(), key=lambda x: x[1], reverse=True)
        debug_log("[CATEGORY-STATS] Distribution:")
        for cat, count in sorted_stats:
            debug_log(f"  {cat}: {count}")
    return _category_stats
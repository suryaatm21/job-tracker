#!/usr/bin/env python3
"""
Job filtering and categorization utilities.
Handles category-based filtering for different use cases (DM alerts vs digest).
"""

from state_utils import should_include_item

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
    """
    # First check if there's an existing category field (SimplifyJobs has this)
    if "category" in job and job["category"]:
        category = job["category"].strip()
        if category in ALLOWED_CATEGORIES_DM:
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
    return None


def should_process_repo_item(item, repo):
    """
    Check if item should be processed based on repository-specific rules.
    For SimplifyJobs: strict category filtering
    For other repos: include all items (but still run quality checks)
    """
    # Always run quality gate first (active filtering, visibility, URL checks)
    if not should_include_item(item):
        return False, "quality"
    
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
    if not cat:
        return True  # No category info → keep
    if cat == "Other":
        return False
    if cat in ALLOWED_CATEGORIES_DIGEST:
        return True
    # Category present but not in allowlist and not "Other" → keep (soft filter)
    return True
#!/usr/bin/env python3
"""
Markdown-table reader for job boards that publish listings as README tables
(speedyapply/2027-SWE-College-Jobs, zapplyjobs/New-Grad-Jobs-2027).

Turns each table row into a dict shaped like a listings.json entry, so the
private digest can treat every source the same way:
    company_name, title, locations, url, date_posted (approximate epoch),
    active, is_visible, category (the table's section heading)

Columns are found by header name, not position, because the boards differ:
    speedyapply: | Company | Position | Location | [Salary] | Posting | Age |
    zapplyjobs:  | Company | Role | Location | Posted | Visa | **Apply** |
"""
import calendar
import html
import re
import time

# Header name (lowercased, markup stripped) -> listing field
HEADER_FIELDS = {
    "company": "company",
    "position": "title",
    "role": "title",
    "title": "title",
    "job title": "title",
    "location": "location",
    "locations": "location",
    "posting": "link",
    "apply": "link",
    "application": "link",
    "link": "link",
    "age": "age",
    "posted": "age",
    "date posted": "age",
    "salary": "salary",
}

# Relative ages such as "24m", "2h", "3d", "1w", "1mo"
_AGE_RE = re.compile(r"^(\d+)\s*(mo|m|h|d|w|y)$", re.IGNORECASE)
_AGE_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 7 * 86400, "mo": 30 * 86400, "y": 365 * 86400}
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(r"""<a\s[^>]*href=["']([^"']+)["']""", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\]\((https?://[^)\s]+)\)")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")
_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.IGNORECASE)
_EXTRA_LOCATIONS_RE = re.compile(r"\s*\+\d+\s*$")
_CONTINUATION_MARKERS = {"", "↳", "↳"}


def cell_text(cell):
    """Visible text of a table cell: strip HTML tags and markdown emphasis, unescape entities."""
    text = _TAG_RE.sub("", cell)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # [label](url) -> label
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|])", r"\1", text)  # markdown escapes: \[ -> [
    return html.unescape(text).strip()


def cell_link(cell):
    """First link URL in a cell (HTML <a href> or markdown [x](url)), or ''."""
    m = _HREF_RE.search(cell) or _MD_LINK_RE.search(cell)
    return html.unescape(m.group(1)).strip() if m else ""


def split_row(line):
    """Split a markdown table row into raw cells."""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def is_separator_row(cells):
    return all(re.fullmatch(r":?-{3,}:?", c.replace(" ", "")) for c in cells if c)


def parse_age_seconds(age):
    """Seconds since posting for a relative age like '3d'; None when unknown."""
    m = _AGE_RE.match((age or "").strip())
    if not m:
        return None
    return int(m.group(1)) * _AGE_SECONDS[m.group(2).lower()]


def approximate_posted_epoch(age, now_epoch):
    """
    Approximate posting time from the board's age column.

    Boards round ages down ("3d" means 3-4 days), so the result is the latest
    possible posting time. Absolute dates (YYYY-MM-DD) are read as UTC midnight.
    """
    age = (age or "").strip()
    seconds = parse_age_seconds(age)
    if seconds is not None:
        return now_epoch - seconds
    if _ISO_DATE_RE.match(age):
        return calendar.timegm(time.strptime(age, "%Y-%m-%d"))
    return None


def section_title(line):
    """Section heading text from a markdown heading or a <summary> line, else None."""
    m = _HEADING_RE.match(line) or _SUMMARY_RE.search(line)
    if not m:
        return None
    # Drop leading emoji/symbols: "💻 Software Engineering" -> "Software Engineering"
    return re.sub(r"^[^\w(]+", "", cell_text(m.group(1)))


def parse_markdown_tables(text, now_epoch=None):
    """
    Parse every job table in a markdown document.

    Returns a list of listing dicts. Rows without a company, title, or link are
    skipped; a blank or "↳" company cell repeats the previous row's company.
    """
    now_epoch = int(now_epoch if now_epoch is not None else time.time())
    items = []
    section = ""
    columns = None  # field name -> column index for the current table
    last_company = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line.startswith("|"):
            title = section_title(line)
            if title:
                section = title
            columns = None  # a markdown table is a contiguous block of "|" lines
            continue

        cells = split_row(line)
        if columns is None:
            header = {}
            for idx, cell in enumerate(cells):
                field = HEADER_FIELDS.get(cell_text(cell).lower())
                if field and field not in header:
                    header[field] = idx
            if {"company", "title"} <= header.keys():
                columns = header
                last_company = ""
            continue

        if is_separator_row(cells):
            continue

        def get(field):
            idx = columns.get(field)
            return cells[idx] if idx is not None and idx < len(cells) else ""

        company = cell_text(get("company"))
        if company in _CONTINUATION_MARKERS:
            company = last_company
        last_company = company

        title = cell_text(get("title"))
        url = cell_link(get("link")) or cell_link(get("title"))
        raw_location = cell_text(get("location"))
        location = _EXTRA_LOCATIONS_RE.sub("", raw_location)
        if not (company and title and url):
            continue

        items.append({
            "company_name": company,
            "title": title,
            "locations": [location] if location else [],
            "has_hidden_locations": location != raw_location,
            "url": url,
            "date_posted": approximate_posted_epoch(cell_text(get("age")), now_epoch),
            "salary": cell_text(get("salary")),
            "category": section,
            "active": True,
            "is_visible": True,
        })

    return items

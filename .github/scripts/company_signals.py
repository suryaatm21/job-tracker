#!/usr/bin/env python3
"""
Company signals and scoring for the private opportunity digest.

The hard gates (location, pay floor, role) run earlier, in
send_private_digest.py; everything here only raises or lowers a listing's
score and explains why. Signals:

- Company tier lists (company_tiers.json): a fixed boost and label.
- Simplify company profile (https://simplify.jobs/c/<slug>): funding rounds
  with dates, amounts and investors. This is the "earn its way in" signal: a
  startup that raises a round shows up on its Simplify page within days, so
  its next listing scores higher with no list edit.
- Posted pay above the Microsoft/NVIDIA bar (speedyapply's Salary column);
  the hard pay floor itself is applied earlier, in send_private_digest.py.
- PhD/MS-only roles: a penalty, so they land in the "maybe" section.

Profiles are cached per company in the workflow's state directory and
refetched after `refresh_days`, with a per-run lookup cap, so a run makes at
most a few dozen page requests.
"""
import json
import pathlib
import re
import time
from datetime import datetime, timezone

import requests

from github_helper import debug_log
from opportunity_filters import (normalize_company, is_graduate_only, parse_pay, listing_pay_text,
                                 LEVEL_INTERN, LEVEL_NEW_GRAD, PAY_OK, PAY_UNPARSED)

SIMPLIFY_COMPANY_URL = "https://simplify.jobs/c/{slug}"
USER_AGENT = "job-tracker-private-digest/1.0 (+https://github.com/suryaatm21/job-tracker)"
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
_SIMPLIFY_SLUG_RE = re.compile(r"simplify\.jobs/c/([^/?#]+)")


def simplify_slug_from_url(url):
    m = _SIMPLIFY_SLUG_RE.search(url or "")
    return m.group(1) if m else None


def guess_simplify_slug(name):
    """Simplify's slug style: "M&T Bank" -> "M-And-T-Bank", "Scale AI" -> "Scale-AI"."""
    text = str(name or "").replace("&", " And ")
    words = re.findall(r"[A-Za-z0-9]+", text)
    return "-".join(words) or None


def _epoch_from_iso(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (TypeError, ValueError):
        return None


def parse_simplify_company_page(page_html):
    """
    Summarise the company record embedded in a Simplify company page.

    Returns a small dict (name, stock_symbol, funding_stage, funding_total,
    rounds=[{round, amount, date_epoch, top_investors}]) or None when the
    page has no company record.
    """
    m = _NEXT_DATA_RE.search(page_html or "")
    if not m:
        return None
    try:
        company = json.loads(m.group(1))["props"]["pageProps"]["company"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(company, dict) or not company.get("name"):
        return None

    rounds = []
    for f in company.get("fundings") or []:
        rounds.append({
            "round": str(f.get("round") or "").upper(),
            "amount": f.get("amount"),
            "date_epoch": _epoch_from_iso(f.get("date")),
            "top_investors": sorted({
                i.get("name") for i in (f.get("investors") or [])
                if i.get("is_top_investor") and i.get("name")
            }),
        })
    rounds.sort(key=lambda r: r["date_epoch"] or 0, reverse=True)
    return {
        "name": company.get("name"),
        "stock_symbol": company.get("stock_symbol"),
        "funding_stage": company.get("funding_stage"),
        "funding_total": company.get("funding_total"),
        "rounds": rounds,
    }


class CompanyProfiles:
    """
    Per-company Simplify profile lookup with a JSON cache.

    Cache entries are {"fetched_at": epoch, "slug": str, "profile": dict|None};
    a None profile records a miss (no Simplify page) so it is not retried until
    the entry is older than refresh_days.
    """

    MAX_CONSECUTIVE_ERRORS = 3

    def __init__(self, cache_path, refresh_days=14, max_lookups=40, delay_seconds=0.5,
                 fetch_page=None, now_epoch=None):
        self.cache_path = pathlib.Path(cache_path)
        self.refresh_seconds = int(refresh_days * 86400)
        self.max_lookups = max_lookups
        self.delay_seconds = delay_seconds
        self.now_epoch = int(now_epoch or time.time())
        self._fetch_page = fetch_page or self._http_get
        self._slug_index = {}
        self.lookups = 0
        self.cache_hits = 0
        self.skipped_budget = 0
        self.consecutive_errors = 0
        self.disabled = False  # circuit breaker: Simplify unreachable or blocking this runner
        self.cache = self._load()

    def _load(self):
        try:
            if self.cache_path.exists():
                data = json.loads(self.cache_path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError) as e:
            debug_log(f"[WARN] Could not read company cache {self.cache_path}: {e}")
        return {}

    def save(self):
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self.cache, indent=1, sort_keys=True), encoding="utf-8")
        except OSError as e:
            debug_log(f"[WARN] Could not write company cache {self.cache_path}: {e}")

    @staticmethod
    def _http_get(url):
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.text

    def register_listing(self, item):
        """Learn name -> slug from listings that carry a Simplify company_url."""
        slug = simplify_slug_from_url(item.get("company_url"))
        key = normalize_company(item.get("company_name"))
        if slug and key and key not in self._slug_index:
            self._slug_index[key] = slug

    def slug_for(self, item):
        return (simplify_slug_from_url(item.get("company_url"))
                or self._slug_index.get(normalize_company(item.get("company_name")))
                or guess_simplify_slug(item.get("company_name")))

    PROFILE_FOUND = "found"
    PROFILE_NONE = "none"          # no Simplify page, or fetch failed
    PROFILE_DEFERRED = "deferred"  # needs a lookup but this run's budget is spent

    def lookup(self, item):
        """
        Return (profile_or_None, status) for the listing's company. A stale
        cache entry is refreshed when budget allows; otherwise the stale value
        is used, and a never-seen company reports PROFILE_DEFERRED.
        """
        key = normalize_company(item.get("company_name"))
        if not key:
            return None, self.PROFILE_NONE
        entry = self.cache.get(key)
        if entry and self.now_epoch - int(entry.get("fetched_at", 0)) < self.refresh_seconds:
            self.cache_hits += 1
            return entry.get("profile"), self.PROFILE_FOUND if entry.get("profile") else self.PROFILE_NONE
        if self.disabled:
            stale = entry.get("profile") if entry else None
            return stale, self.PROFILE_FOUND if stale else self.PROFILE_NONE
        if self.lookups >= self.max_lookups:
            self.skipped_budget += 1
            if entry:
                return entry.get("profile"), self.PROFILE_FOUND if entry.get("profile") else self.PROFILE_NONE
            return None, self.PROFILE_DEFERRED

        slug = self.slug_for(item)
        if not slug:
            return None, self.PROFILE_NONE
        if self.lookups and self.delay_seconds:
            time.sleep(self.delay_seconds)
        self.lookups += 1
        try:
            page = self._fetch_page(SIMPLIFY_COMPANY_URL.format(slug=slug))
            profile = parse_simplify_company_page(page) if page else None
        except Exception as e:  # network errors keep the stale entry, if any
            debug_log(f"[WARN] Simplify profile fetch failed for {slug}: {e}")
            self.consecutive_errors += 1
            if self.consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                # Score the rest without profiles rather than deferring them forever
                self.disabled = True
                print(f"::warning::Simplify profile lookups failed {self.consecutive_errors} times in a row; "
                      "scoring the rest of this run from tiers and posted pay only")
            stale = entry.get("profile") if entry else None
            return stale, self.PROFILE_FOUND if stale else self.PROFILE_NONE
        self.consecutive_errors = 0
        self.cache[key] = {"fetched_at": self.now_epoch, "slug": slug, "profile": profile}
        return profile, self.PROFILE_FOUND if profile else self.PROFILE_NONE


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _money(amount):
    amount = float(amount or 0)
    if amount >= 1e9:
        return f"${amount / 1e9:.1f}B".replace(".0B", "B")
    if amount >= 1e6:
        return f"${amount / 1e6:.0f}M"
    return f"${amount / 1e3:.0f}K"


def _round_label(round_type):
    """'SERIES_B' -> 'Series B', 'LATE_VC' -> 'Late VC'."""
    words = [w for w in str(round_type or "").upper().split("_") if w]
    return " ".join(w if len(w) == 1 or w == "VC" else w.capitalize() for w in words)


def is_equity_round(round_type, prefixes):
    return any(str(round_type or "").upper().startswith(p) for p in prefixes)


def score_listing(item, level, tier_match, profile, scoring, now_epoch):
    """
    Score one listing. Returns (score, company_reasons, listing_reasons): short
    strings such as "tier: promising startup" or "raised Series B $45M, Oct 2026"
    describe the company (tier, unless the tier sets "show_label": false, and
    Simplify funding profile; the same for every listing of that company), and "posted pay $55/hr", "pay unparsed: ..." or
    "PhD/MS only" describe this listing alone.
    """
    score = 0
    company_reasons = []

    if tier_match:
        tier, _company = tier_match
        score += int(tier.get("points", 0))
        if tier.get("show_label", True):
            company_reasons.append(f"tier: {tier.get('label', tier.get('key', 'listed'))}")

    funding = scoring.get("funding", {})
    if profile and not profile.get("stock_symbol"):
        prefixes = [p.upper() for p in funding.get("equity_round_prefixes", [])]
        recent_cutoff = now_epoch - int(funding.get("recent_round_months", 12) * 30.4 * 86400)
        investor_cutoff = now_epoch - int(funding.get("top_investor_months", 36) * 30.4 * 86400)
        equity = [r for r in profile.get("rounds", []) if is_equity_round(r.get("round"), prefixes) and r.get("date_epoch")]

        recent = [r for r in equity if r["date_epoch"] >= recent_cutoff]
        if recent:
            latest = recent[0]
            score += int(funding.get("recent_round_points", 2))
            when = datetime.fromtimestamp(latest["date_epoch"], tz=timezone.utc).strftime("%b %Y")
            amount = f" {_money(latest['amount'])}" if latest.get("amount") else ""
            company_reasons.append(f"raised {_round_label(latest['round'])}{amount}, {when}")
            if (latest.get("amount") or 0) >= funding.get("large_round_usd", 50_000_000):
                score += int(funding.get("large_round_points", 1))

        investors = []
        for r in equity:
            if r["date_epoch"] >= investor_cutoff:
                investors.extend(i for i in r.get("top_investors", []) if i not in investors)
        if investors:
            score += int(funding.get("top_investor_points", 1))
            shown = ", ".join(investors[:2]) + (f" +{len(investors) - 2}" if len(investors) > 2 else "")
            company_reasons.append(f"backed by {shown}")

        total = profile.get("funding_total") or 0
        if total >= funding.get("large_total_usd", 500_000_000):
            score += int(funding.get("large_total_points", 1))
            company_reasons.append(f"{_money(total)} raised total")

    listing_reasons = []
    pay_cfg = scoring.get("posted_pay", {})
    pay = item.get("_pay") or parse_pay(listing_pay_text(item))
    if pay.get("status") == PAY_OK:
        intern_bar = pay["hourly"] >= float(pay_cfg.get("intern_hourly_usd", 52))
        new_grad_bar = pay["yearly"] >= float(pay_cfg.get("new_grad_yearly_usd", 170000))
        if level == LEVEL_INTERN:
            meets = intern_bar
        elif level == LEVEL_NEW_GRAD:
            meets = new_grad_bar
        else:  # unknown level: judge by the unit the feed posted
            meets = intern_bar if pay["unit"] == "hr" else new_grad_bar
        if meets:
            score += int(pay_cfg.get("points", 3))
            listing_reasons.append(f"posted pay {pay['text']}")
    elif pay.get("status") == PAY_UNPARSED:
        listing_reasons.append(f"pay unparsed: {pay.get('text', '')[:40]}")

    if is_graduate_only(item):
        score += int(scoring.get("graduate_only_points", -4))
        listing_reasons.append("PhD/MS only")

    return score, company_reasons, listing_reasons

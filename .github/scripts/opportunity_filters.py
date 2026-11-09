#!/usr/bin/env python3
"""
Filters for the private opportunity digest (send_private_digest.py).

Everything here is deterministic and driven by the JSON files in
.github/private_channel/:
- tag_level: intern vs new grad, from title keywords, else the source's default
- LocationMatcher: metro matching (SF Bay Area, NYC, LA, Austin, Miami) from config.json
- parse_pay: posted pay text -> top of range as yearly and hourly USD
- CompanyTiers: exact-alias company lookup from company_tiers.json
- is_graduate_only: PhD/MS-only roles (title rules + Simplify's degrees field)
"""
import html
import re

from job_filtering import requires_graduate_degree

LEVEL_INTERN = "intern"
LEVEL_NEW_GRAD = "new_grad"
LEVEL_UNKNOWN = "unknown"
LEVELS = (LEVEL_NEW_GRAD, LEVEL_INTERN, LEVEL_UNKNOWN)

# Values a feed may put in an explicit "level" field (Supra's Instagram feed does)
_LEVEL_FIELD_VALUES = {
    "new grad": LEVEL_NEW_GRAD, "new_grad": LEVEL_NEW_GRAD, "newgrad": LEVEL_NEW_GRAD,
    "new-grad": LEVEL_NEW_GRAD, "new graduate": LEVEL_NEW_GRAD,
    "intern": LEVEL_INTERN, "internship": LEVEL_INTERN, "co-op": LEVEL_INTERN, "coop": LEVEL_INTERN,
}

_INTERN_TITLE_RE = re.compile(r"\bintern(ship)?s?\b|\bco-?op\b", re.IGNORECASE)
_NEW_GRAD_TITLE_RE = re.compile(
    r"\bnew[\s-]?grad(uate)?s?\b|\bentry[\s-]level\b|\bearly[\s-]career\b"
    r"|\b(university|college|recent)\s+grad(uate)?s?\b",
    re.IGNORECASE,
)


def tag_level(item, default_level):
    """
    Return LEVEL_NEW_GRAD, LEVEL_INTERN or LEVEL_UNKNOWN for a listing.

    Order: an explicit "level" field from the feed's own classifier; then a
    title that names exactly one level ("Engineer 2 New Grad" in an internship
    repo, "Software Intern - Summer 2027" in a new-grad repo); then the source
    file's level, which is LEVEL_UNKNOWN for feeds that mix both.
    """
    explicit = _LEVEL_FIELD_VALUES.get(str(item.get("level") or "").strip().lower())
    if explicit:
        return explicit
    title = item.get("title") or ""
    intern = bool(_INTERN_TITLE_RE.search(title))
    new_grad = bool(_NEW_GRAD_TITLE_RE.search(title))
    if intern and not new_grad:
        return LEVEL_INTERN
    if new_grad and not intern:
        return LEVEL_NEW_GRAD
    return default_level


# Simplify's degrees field lists accepted degrees; these mean a BS student qualifies
_GRADUATE_DEGREES = {"master's", "phd", "mba", "md", "jd", "pharmd", "do", "dds", "dvm"}


def is_graduate_only(item):
    """True for PhD/MS-only roles: title says so, or every listed degree is a graduate degree."""
    if requires_graduate_degree(item):
        return True
    degrees = [str(d).strip().lower() for d in (item.get("degrees") or []) if str(d).strip()]
    return bool(degrees) and all(d in _GRADUATE_DEGREES for d in degrees)


# ---------------------------------------------------------------------------
# Location matching
# ---------------------------------------------------------------------------

LOCATION_MATCH = "match"
LOCATION_REMOTE = "remote_only"
LOCATION_UNKNOWN = "unknown"
LOCATION_ELSEWHERE = "elsewhere"

_SEPARATORS_RE = re.compile(r"[,;/|>()•·\-–—_]+")
# Applied after digits are stripped, so "2 Locations" arrives as "locations"
_UNKNOWN_RE = re.compile(r"^(locations?|multiple locations?|various locations?|multi location|tbd|n a|na)$")


def _norm_location(text):
    """Lowercase, unescape, drop digits and trailing ellipses, collapse whitespace."""
    text = html.unescape(str(text or "")).lower()
    text = text.replace("…", " ").replace("...", " ")
    text = re.sub(r"\d+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _word_pattern(phrase):
    return re.compile(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])")


class LocationMatcher:
    """
    Match listing locations against configured metros.

    Each metro in config.json "location" has:
    - cities: names that identify the metro on their own ("palo alto", "sf", "nyc")
    - cities_needing_state: names that also exist elsewhere ("san jose", "dublin",
      "brooklyn"); these match only next to one of the metro's state_tokens
      ("San Jose, CA", "USA - CA - San Jose") or when the location is that name
      alone ("San Jose", "New York, United States")
    - bare_names: names that count only as the whole location ("LA" means Los
      Angeles alone, but not in "Baton Rouge, LA" or "La Jolla, San Diego, CA")
    - state_tokens: "ca"/"california", "ny"/"new york"

    The bare-name rule is what keeps "USA - New York - Malta" (upstate) and
    "Manhattan Beach, CA" out while "New York, NY" and "NYC" match.
    """

    def __init__(self, config):
        self.include_remote = bool(config.get("include_remote", False))
        self.remote_patterns = [re.compile(p) for p in config.get("remote_patterns", [r"\bremote\b"])]
        self.filler_tokens = {t.lower() for t in config.get("filler_tokens", [])}
        self.metros = []
        for metro in config.get("metros", []):
            self.metros.append({
                "label": metro["label"],
                "cities": [_word_pattern(c.lower()) for c in metro.get("cities", [])],
                "qualified": [_word_pattern(c.lower()) for c in metro.get("cities_needing_state", [])],
                "bare": [_word_pattern(c.lower()) for c in metro.get("bare_names", [])],
                "states": [_word_pattern(s.lower()) for s in metro.get("state_tokens", [])],
            })

    def _is_remote(self, text):
        return any(p.search(text) for p in self.remote_patterns)

    def _leftover(self, text, span):
        """Location text outside the matched city, minus separators and filler words."""
        rest = text[:span[0]] + " " + text[span[1]:]
        words = _SEPARATORS_RE.sub(" ", rest).split()
        return [w for w in words if w not in self.filler_tokens]

    def match_one(self, location):
        """Metro label for a single location string, or None."""
        text = _norm_location(location)
        if not text:
            return None
        for metro in self.metros:
            if any(p.search(text) for p in metro["cities"]):
                return metro["label"]
            for pattern in metro["qualified"]:
                for m in pattern.finditer(text):
                    rest = text[:m.start()] + " " + text[m.end():]
                    if any(s.search(rest) for s in metro["states"]):
                        return metro["label"]
                    if not self._leftover(text, m.span()):
                        return metro["label"]
            for pattern in metro["bare"]:
                m = pattern.search(text)
                if m and not self._leftover(text, m.span()):
                    return metro["label"]
        return None

    def _is_unknown(self, location):
        text = _SEPARATORS_RE.sub(" ", _norm_location(location)).strip()
        words = [w for w in text.split() if w not in self.filler_tokens]
        return not words or bool(_UNKNOWN_RE.match(" ".join(words)))

    def classify(self, item):
        """
        Return (outcome, metro_labels) for a listing.

        outcome is one of LOCATION_MATCH, LOCATION_REMOTE (every location is
        remote and remote is excluded), LOCATION_UNKNOWN (no usable location,
        or the board hid extra locations behind "+N"), LOCATION_ELSEWHERE.
        """
        locations = [str(l) for l in (item.get("locations") or []) if str(l or "").strip()]
        labels = []
        remote_seen = False
        for loc in locations:
            text = _norm_location(loc)
            if self._is_remote(text):
                remote_seen = True
                if not self.include_remote:
                    continue
                label = "Remote"
            else:
                label = self.match_one(loc)
            if label and label not in labels:
                labels.append(label)

        if labels:
            return LOCATION_MATCH, labels
        if locations and remote_seen and all(self._is_remote(_norm_location(l)) for l in locations):
            return LOCATION_REMOTE, []
        if not locations or item.get("has_hidden_locations") or all(self._is_unknown(l) for l in locations):
            return LOCATION_UNKNOWN, []
        return LOCATION_ELSEWHERE, []


# ---------------------------------------------------------------------------
# Posted pay
# ---------------------------------------------------------------------------

PAY_OK = "ok"
PAY_NONE = "no_info"
PAY_UNPARSED = "unparsed"

_PAY_UNITS = [
    ("hr", re.compile(r"/\s*h(ou)?r\b|\bper\s+hour\b|\bhourly\b|\ban?\s+hour\b|\bph\b", re.I)),
    ("yr", re.compile(r"/\s*y(ea)?r\b|\bper\s+(year|annum)\b|\byearly\b|\bannual(ly)?\b|\ba\s+year\b|\bpa\b", re.I)),
    ("mo", re.compile(r"/\s*mo(nth)?\b|\bper\s+month\b|\bmonthly\b", re.I)),
    ("wk", re.compile(r"/\s*w(ee)?k\b|\bper\s+week\b|\bweekly\b", re.I)),
]
_DOLLAR_AMOUNT_RE = re.compile(r"\$\s*(\d[\d,]*(?:\.\d+)?)\s*([km])?(?![a-z])", re.I)
_BARE_AMOUNT_RE = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*([km])?(?![a-z])", re.I)
_FOREIGN_CURRENCY_RE = re.compile(r"[£€₹¥]|\b(ca|c|a|au|nz|s|hk)\$|\b(cad|eur|gbp|inr|aud|sgd|jpy|chf)\b", re.I)
_PERIODS_PER_YEAR = {"yr": 1, "mo": 12, "wk": 52}


def listing_pay_text(item):
    """Pay text a source attached to a listing, if any."""
    for field in ("salary", "compensation", "pay"):
        value = item.get(field)
        if value and str(value).strip():
            return str(value).strip()
    return ""


def parse_pay(text, hours_per_year=2080):
    """
    Parse posted pay into the top of the range, as yearly and hourly USD.

    Handles "$120,000 - $180,000", "$150K-$200K", "$186k/yr", "$55/hr",
    "$50 - $60 per hour", "120000-150000 USD yearly". With no unit, values of
    10,000+ read as yearly and values up to 300 as hourly.

    Returns {"status": PAY_OK, "top", "unit", "yearly", "hourly"}, or
    {"status": PAY_NONE} for empty text, or {"status": PAY_UNPARSED} when the
    text is ambiguous (another currency, mixed units, no amount, or a middling
    unitless number).
    """
    text = str(text or "").strip()
    if not text:
        return {"status": PAY_NONE}
    if _FOREIGN_CURRENCY_RE.search(text):
        return {"status": PAY_UNPARSED, "text": text}

    matches = _DOLLAR_AMOUNT_RE.findall(text)
    if not matches and re.search(r"\busd\b", text, re.I):
        matches = _BARE_AMOUNT_RE.findall(text)
    values = []
    for number, suffix in matches:
        try:
            value = float(number.replace(",", ""))
        except ValueError:
            continue
        value *= {"k": 1e3, "m": 1e6}.get(suffix.lower(), 1) if suffix else 1
        if value > 0:
            values.append(value)
    if not values:
        return {"status": PAY_UNPARSED, "text": text}

    units = {unit for unit, pattern in _PAY_UNITS if pattern.search(text)}
    if len(units) > 1:
        return {"status": PAY_UNPARSED, "text": text}
    top = max(values)
    unit = units.pop() if units else ("yr" if top >= 10_000 else "hr" if top <= 300 else None)
    if unit is None:
        return {"status": PAY_UNPARSED, "text": text}

    yearly = top * hours_per_year if unit == "hr" else top * _PERIODS_PER_YEAR[unit]
    return {"status": PAY_OK, "top": top, "unit": unit, "yearly": yearly,
            "hourly": yearly / hours_per_year, "text": text}


def passes_pay_gate(pay, level, gate):
    """
    Hard pay gate, applied only when pay was parsed: interns by hourly rate,
    new grads by the top of the yearly range. Missing or unparsed pay passes.
    A listing of unknown level passes if it clears either floor.
    """
    if pay.get("status") != PAY_OK or not gate.get("enabled", True):
        return True
    intern_ok = pay["hourly"] >= float(gate.get("intern_min_hourly_usd", 50))
    new_grad_ok = pay["yearly"] >= float(gate.get("new_grad_min_yearly_usd", 140000))
    if level == LEVEL_INTERN:
        return intern_ok
    if level == LEVEL_NEW_GRAD:
        return new_grad_ok
    return intern_ok or new_grad_ok


# ---------------------------------------------------------------------------
# Company tiers
# ---------------------------------------------------------------------------

_LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co",
    "company", "plc", "gmbh", "ag", "sa", "lp", "llp", "pbc",
}


def normalize_company(name):
    """
    Canonical company key: lowercase, '&' -> 'and', punctuation removed, legal
    suffixes and a leading 'the' dropped. "Snap Inc." -> "snap".
    """
    text = html.unescape(str(name or "")).lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9+ ]+", " ", text)
    words = text.split()
    if words and words[0] == "the":
        words = words[1:]
    while words and words[-1] in _LEGAL_SUFFIXES:
        words = words[:-1]
    return " ".join(words)


class CompanyTiers:
    """
    Exact-match company lookup over company_tiers.json.

    Matching is equality on normalize_company() of the name and each alias,
    never substring, so "Meta" does not catch "Metaview" and "Apple" does not
    catch "Applied Materials". A company in two tiers keeps the first tier in
    file order.
    """

    def __init__(self, config):
        self.tiers = config.get("tiers", [])
        self._index = {}
        for tier in self.tiers:
            for company in tier.get("companies", []):
                for name in [company["name"], *company.get("aliases", [])]:
                    key = normalize_company(name)
                    if key and key not in self._index:
                        self._index[key] = (tier, company)

    def match(self, company_name):
        """Return (tier_dict, company_dict) or None."""
        return self._index.get(normalize_company(company_name))

#!/usr/bin/env python3
"""
Filters for the private opportunity digest (send_private_digest.py). The DM
watcher (watch_repo.py) reuses the location gate, limited to its own metros.

Everything here is deterministic and driven by the JSON files in
.github/private_channel/:
- tag_level: intern vs new grad, from title keywords, else the source's default
- LocationMatcher: metro matching (SF Bay Area, NYC, LA, Austin, Miami) from config.json
- metro_subset_matcher: the same matcher over only the named metros (the DM gate)
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
# Separators between whole locations inside one cell: "San Francisco, CA / New York, NY",
# "San Francisco, CA • New York, NY", "New York, NY; Remote". A slash without spaces
# ("SF/NYC", "Remote/Teleworker") and a hyphen ("Remote - Austin, TX") stay in one segment.
_LOCATION_LIST_RE = re.compile(r"\s*(?:;|\||•|\s/\s|\s+or\s+)\s*", re.IGNORECASE)


def split_location(location):
    """One location string -> the separate locations it lists."""
    return [seg for seg in _LOCATION_LIST_RE.split(str(location or "")) if seg.strip()]
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


# A state with no metro in config.json still gets a label by its own words.
EXTRA_STATES = {"New Jersey": ("nj", "new jersey")}
_CODE_AT_END = re.compile(r",\s*([a-z]{2})\s*$")


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
        state_terms = {label: set(terms) for label, terms in EXTRA_STATES.items()}
        for metro in config.get("metros", []):
            tokens = {t.lower() for t in metro.get("state_tokens", [])}
            state = max(tokens, key=len).title() if tokens else None
            if state:
                state_terms.setdefault(state, set()).update(tokens)
            self.metros.append({
                "label": metro["label"],
                "state": state,
                "cities": [_word_pattern(c.lower()) for c in metro.get("cities", [])],
                "qualified": [_word_pattern(c.lower()) for c in metro.get("cities_needing_state", [])],
                "bare": [_word_pattern(c.lower()) for c in metro.get("bare_names", [])],
                "states": [_word_pattern(s.lower()) for s in metro.get("state_tokens", [])],
            })
        self.state_patterns = {label: re.compile("|".join(_word_pattern(t).pattern for t in sorted(terms)))
                               for label, terms in state_terms.items()}
        self.state_codes = {t: label for label, terms in state_terms.items() for t in terms if len(t) == 2}

    def _is_remote(self, text):
        return any(p.search(text) for p in self.remote_patterns)

    def _leftover(self, text, span):
        """Location text outside the matched city, minus separators and filler words."""
        rest = text[:span[0]] + " " + text[span[1]:]
        words = _SEPARATORS_RE.sub(" ", rest).split()
        return [w for w in words if w not in self.filler_tokens]

    def _metro_matches(self, metro, text):
        if any(p.search(text) for p in metro["cities"]):
            return True
        for pattern in metro["qualified"]:
            for m in pattern.finditer(text):
                rest = text[:m.start()] + " " + text[m.end():]
                if any(s.search(rest) for s in metro["states"]) or not self._leftover(text, m.span()):
                    return True
        return any(m and not self._leftover(text, m.span())
                   for m in (pattern.search(text) for pattern in metro["bare"]))

    def match_all(self, location):
        """Every metro label one location segment names, in config order ("SF/NYC" -> both)."""
        text = _norm_location(location)
        return [metro["label"] for metro in self.metros if text and self._metro_matches(metro, text)]

    def states(self, location):
        """State labels one location is in ("California", "New York", "New Jersey", ...).

        A two-letter state code after the last comma decides alone, so "New York
        Mills, MN" is Minnesota. Without one, a state counts when its name appears
        as a whole word or the location is in one of its metros ("San Jose").
        """
        text = _norm_location(location)
        code = _CODE_AT_END.search(text)
        if code:
            label = self.state_codes.get(code.group(1))
            return [label] if label else []
        by_metro = {m["state"] for m in self.metros if text and m["state"] and self._metro_matches(m, text)}
        return [label for label, pattern in self.state_patterns.items()
                if label in by_metro or pattern.search(text)]

    def match_one(self, location):
        """First metro label for a single location segment, or None."""
        labels = self.match_all(location)
        return labels[0] if labels else None

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
        segments = [seg for loc in (item.get("locations") or []) for seg in split_location(loc)]
        labels = []
        for seg in segments:
            if self._is_remote(_norm_location(seg)):
                found = ["Remote"] if self.include_remote else []
            else:
                found = self.match_all(seg)
            labels.extend(label for label in found if label not in labels)

        if labels:
            return LOCATION_MATCH, labels
        if segments and all(self._is_remote(_norm_location(seg)) for seg in segments):
            return LOCATION_REMOTE, []
        if not segments or item.get("has_hidden_locations") or all(self._is_unknown(seg) for seg in segments):
            return LOCATION_UNKNOWN, []
        return LOCATION_ELSEWHERE, []


def metro_subset_matcher(location_config, labels):
    """
    LocationMatcher over only the named metros of a config.json "location" section.

    Remote, filler and unknown-location handling stay those of the full section,
    so a listing outside the named metros classifies as LOCATION_ELSEWHERE.
    Raises ValueError for an empty list or a label the section does not define,
    so a typo fails the run instead of dropping every listing.
    """
    by_label = {metro["label"]: metro for metro in location_config.get("metros", [])}
    labels = list(labels or [])
    unknown = [label for label in labels if label not in by_label]
    if not labels or unknown:
        raise ValueError(f"metro labels must be a non-empty subset of {sorted(by_label)}; got {labels}")
    return LocationMatcher({**location_config, "metros": [by_label[label] for label in labels]})


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
# A range whose second end has no dollar sign: "$45-55/hr", "$120,000-160,000", "$120-160k"
_DOLLAR_RANGE_RE = re.compile(
    r"\$\s*(\d[\d,]*(?:\.\d+)?)\s*([km])?\s*(?:-|–|—|to)\s*\$?\s*(\d[\d,]*(?:\.\d+)?)\s*([km])?(?![a-z])", re.I)
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
    "$50 - $60 per hour", ranges with one dollar sign ("$45-55/hr",
    "$120,000-160,000 per year", "$120-160k") and "120000-150000 USD yearly".
    With no unit, values of
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
    for low, low_suffix, high, high_suffix in _DOLLAR_RANGE_RE.findall(text):
        # A suffix written once covers both ends: "$120-160k" is 120k-160k
        matches += [(low, low_suffix or high_suffix), (high, high_suffix or low_suffix)]
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

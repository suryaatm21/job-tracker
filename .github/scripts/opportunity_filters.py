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
- RoleGate: drops PM, silicon and non-engineering roles (title terms + board category)
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


US_STATE_CODES = frozenset(
    "al ak az ar ca co ct de dc fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv nh nj nm ny "
    "nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy".split())
_COUNTRY_SEGMENTS = {"us", "usa", "u.s.", "u.s.a.", "united states"}
US_STATE_NAMES = frozenset(
    "alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|district of columbia|"
    "florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|"
    "massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|"
    "new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|"
    "rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|"
    "west virginia|wisconsin|wyoming".split("|"))
# Countries by name only: two-letter country codes collide with state codes ("CA", "IN", "DE").
_FOREIGN_COUNTRIES = frozenset(
    "canada|mexico|brazil|argentina|chile|colombia|united kingdom|uk|england|scotland|wales|ireland|"
    "france|germany|netherlands|belgium|switzerland|austria|spain|portugal|italy|denmark|sweden|"
    "norway|finland|poland|czech republic|czechia|romania|ukraine|greece|turkey|israel|"
    "united arab emirates|uae|saudi arabia|egypt|nigeria|kenya|south africa|india|pakistan|"
    "bangladesh|china|hong kong|taiwan|japan|south korea|korea|singapore|malaysia|indonesia|"
    "philippines|vietnam|thailand|australia|new zealand|costa rica|puerto rico".split("|"))
# The comma-separated segment right after a city: "mountain view, hawaii (hybrid)" -> "hawaii "
_AFTER_CITY_RE = re.compile(r"\s*,\s*([^,;/|>()•·\-–—_]*)")
# New Jersey has no metro in config.json, so it gets its own whole-word rule.
_NEW_JERSEY = _word_pattern("nj").pattern + "|" + _word_pattern("new jersey").pattern
_NEW_JERSEY = re.compile(_NEW_JERSEY)


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

    A city named right before a comma and a US state or a country counts only
    when that state is one of its metro's state_tokens: "Mountain View, CA"
    matches, "Mountain View, Hawaii" and "Mountain View, HI" do not. A US country
    segment, an unrecognized word, or a name of any configured metro there
    ("Playa Vista, LA") leaves the match as it is.

    labels limits matching to those metros; place names from every metro in
    config still count, so a subset matcher reads "City, LA" the same way.
    """

    def __init__(self, config, labels=None):
        self.include_remote = bool(config.get("include_remote", False))
        self.remote_patterns = [re.compile(p) for p in config.get("remote_patterns", [r"\bremote\b"])]
        self.filler_tokens = {t.lower() for t in config.get("filler_tokens", [])}
        self.place_names = {name.lower() for metro in config.get("metros", [])
                            for key in ("cities", "cities_needing_state", "bare_names")
                            for name in metro.get(key, [])}
        metros = config.get("metros", [])
        if labels is not None:
            by_label = {metro["label"]: metro for metro in metros}
            metros = [by_label[label] for label in labels]
        self.metros = []
        for metro in metros:
            tokens = {t.lower() for t in metro.get("state_tokens", [])}
            state = max(tokens, key=len).title() if tokens else None
            self.metros.append({
                "label": metro["label"],
                "state": state,
                "tokens": tokens,
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

    def _other_state_follows(self, metro, text, end):
        """True when the segment after a city ending at end names a state or country not the metro's."""
        m = _AFTER_CITY_RE.match(text, end)
        if not m:
            return False
        segment = " ".join(m.group(1).split())
        words = " ".join(w for w in segment.split() if w not in self.filler_tokens)
        if not words or segment in _COUNTRY_SEGMENTS:
            return False
        if any(name in metro["tokens"] or name in self.place_names for name in (segment, words)):
            return False
        return any(name in US_STATE_CODES or name in US_STATE_NAMES or name in _FOREIGN_COUNTRIES
                   for name in (segment, words))

    def _metro_matches(self, metro, text):
        if any(not self._other_state_follows(metro, text, m.end())
               for p in metro["cities"] for m in p.finditer(text)):
            return True
        for pattern in metro["qualified"]:
            for m in pattern.finditer(text):
                if self._other_state_follows(metro, text, m.end()):
                    continue
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

        After any trailing country segment ("US", "USA", "United States"), a US
        state code ending the comma list decides alone, so "New York Mills, MN"
        is Minnesota. Otherwise a state counts when its code or name (the metros'
        state_tokens) appears as a whole word, or the location is in one of its metros.
        """
        text = _norm_location(location)
        parts = [p.strip() for p in text.split(",")]
        while len(parts) > 1 and parts[-1] in _COUNTRY_SEGMENTS:
            parts.pop()
        code = parts[-1] if len(parts) > 1 and parts[-1] in US_STATE_CODES else None
        if code:
            found = [m["state"] for m in self.metros if m["state"] and code in m["tokens"]]
            found += ["New Jersey"] if code == "nj" else []
            return list(dict.fromkeys(found))
        found = [m["state"] for m in self.metros if m["state"] and text and (
            self._metro_matches(m, text) or any(p.search(text) for p in m["states"]))]
        found += ["New Jersey"] if _NEW_JERSEY.search(text) else []
        return list(dict.fromkeys(found))

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
    return LocationMatcher(location_config, labels)


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


# ---------------------------------------------------------------------------
# Role gate
# ---------------------------------------------------------------------------

ROLE_PM = "pm"
ROLE_SILICON = "silicon"
ROLE_NON_ENGINEERING = "non_engineering"
ROLE_REASONS = (ROLE_PM, ROLE_SILICON, ROLE_NON_ENGINEERING)


def _norm_role_text(text):
    """Lowercase words separated by single spaces: "Post-Silicon R&D" -> "post silicon r d"."""
    text = html.unescape(str(text or "")).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _phrase_re(terms):
    """One regex matching any of the normalized terms as whole words, longest first."""
    phrases = sorted({_norm_role_text(t) for t in terms if _norm_role_text(t)}, key=len, reverse=True)
    if not phrases:
        return None
    return re.compile(r"(?<!\S)(?:" + "|".join(re.escape(p) for p in phrases) + r")(?!\S)")


class RoleGate:
    """
    Hard gate on the kind of work a listing is, from its title and board category.

    classify(item) returns (reason, matched) for a listing to drop, where reason
    is ROLE_PM, ROLE_SILICON or ROLE_NON_ENGINEERING and matched names the term
    or category that decided it, or None to keep the listing. Terms match whole
    words after both sides are normalized ("Post-Silicon" -> "post silicon", so
    "silicon" catches it; "sales" never catches "Salesforce"). Order:

    1. A PM title term drops, even with "technical" or "engineer" in the title.
    2. A silicon title term drops, even when the title says Engineer.
    3. An always-drop non-engineering term drops whatever else the title says
       ("Engineering Technician", "Embedded Account Executive").
    4. A PM board category drops unless the title names engineering work
       ("Product Engineer"); "Recipe Developer" in Simplify's Product bucket drops.
    5. A title with a non-engineering term stays only when the rest of it names
       an engineering or research role (engineer, developer, scientist,
       researcher), never on topical words like "AI" or "research" or via
       "manager" or "analyst": "Test and Launch Operations Engineer I" stays,
       "Marketing Manager, AI" drops. For the needs_qualifier_terms (supply
       chain, talent, recruiting) a bare engineer is not enough: the rest must
       also hold a qualifier such as software or ML. "Software Engineer, Supply
       Chain" stays, "Supply Chain Engineer" drops.
    6. With no non-engineering title term, an engineering title term keeps the
       listing ("Research Intern" in zapply's "Other Jobs"); otherwise a
       non-engineering category drops, an engineering category keeps, and with
       neither the listing stays only when engineering_only is set (sources
       that list only SWE roles) and drops as "no engineering/research term".
    Categories compare exactly, case-folded.
    """

    def __init__(self, config):
        config = config or {}
        pm = config.get("pm", {})
        silicon = config.get("silicon", {})
        non_eng = config.get("non_engineering", {})
        eng = config.get("engineering", {})
        fold = lambda names: {str(n).strip().casefold() for n in names}
        self.pm_title = _phrase_re(pm.get("title_terms", []))
        self.pm_categories = fold(pm.get("categories", []))
        self.pm_category_rescue = _phrase_re(pm.get("category_kept_when_title_has", []))
        self.silicon_title = _phrase_re(silicon.get("title_terms", []))
        self.silicon_ignore = _phrase_re(silicon.get("ignore_phrases", []))
        self.non_eng_always = _phrase_re(non_eng.get("always_drop_title_terms", []))
        self.non_eng_title = _phrase_re(non_eng.get("title_terms", []))
        self.non_eng_categories = fold(non_eng.get("categories", []))
        self.eng_title = _phrase_re(eng.get("title_terms", []))
        self.eng_role = _phrase_re(eng.get("role_terms", []))
        self.qualified_terms = {_norm_role_text(t) for t in non_eng.get("needs_qualifier_terms", [])}
        self.qualifier = _phrase_re(non_eng.get("qualifier_terms", []))
        self.eng_categories = fold(eng.get("categories", []))

    @staticmethod
    def _find(pattern, text):
        m = pattern.search(text) if pattern else None
        return m.group(0) if m else None

    def classify(self, item, engineering_only=False):
        title = _norm_role_text(item.get("title"))
        category = str(item.get("category") or "").strip().casefold()

        term = self._find(self.pm_title, title)
        if term:
            return ROLE_PM, term
        silicon_text = self.silicon_ignore.sub(" ", title) if self.silicon_ignore else title
        term = self._find(self.silicon_title, silicon_text)
        if term:
            return ROLE_SILICON, term
        term = self._find(self.non_eng_always, title)
        if term:
            return ROLE_NON_ENGINEERING, term
        if category in self.pm_categories and not self._find(self.pm_category_rescue, title):
            return ROLE_PM, f"category: {item.get('category')}"

        matches = [m.group(0) for m in self.non_eng_title.finditer(title)] if self.non_eng_title else []
        if matches:
            rest = self.non_eng_title.sub(" ", title)
            needs_qualifier = any(m in self.qualified_terms for m in matches)
            if (self._find(self.eng_role, rest)
                    and (not needs_qualifier or self._find(self.qualifier, rest))):
                return None
            return ROLE_NON_ENGINEERING, matches[0]
        if self._find(self.eng_title, title):
            return None
        if category in self.non_eng_categories:
            return ROLE_NON_ENGINEERING, f"category: {item.get('category')}"
        if category in self.eng_categories or engineering_only:
            return None
        return ROLE_NON_ENGINEERING, "no engineering/research term"

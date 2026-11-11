#!/usr/bin/env python3
"""
Private opportunity digest: new grad + intern listings in the configured metros
(SF Bay Area, NYC, Los Angeles, Austin, Miami), scored by company signals and
posted to one private Telegram chat.

Pipeline (counts are reported per source and level after every stage):
    fetch -> quality -> window -> location (hard gate)
          -> pay (hard gate, only when the listing states pay)
          -> dedup + TTL state, per listing (see drop_seen_and_duplicates)
          -> score (tiers, Simplify funding profile, posted pay) -> render

Scoring never drops a listing: low scores go to a compact "maybe" section.

Config: .github/private_channel/config.json and company_tiers.json.

Environment:
- TELEGRAM_BOT_TOKEN, PRIVATE_DIGEST_CHAT_ID: the only chat this script posts to
  (the workflow maps it from the TELEGRAM_CHAT_ID_CHANNEL_SWE_ML_PHD secret)
- GH_TOKEN: GitHub API token
- STATE_DIR: TTL + company-profile cache dir (default .state/channel-digest-private)
- WINDOW_HOURS (default 48), FORCE_WINDOW_HOURS: listing age window
- SEEN_TTL_DAYS (default 14)
- DRY_RUN=true: print counts and rendered messages; never touch Telegram or seen state
- DRY_RUN_REPORT: optional path for a JSON report of the dry run
- MAX_PROFILE_LOOKUPS: override config's per-run Simplify lookup budget (wide manual runs)
- PRIVATE_CONFIG_DIR: override the config directory
"""
import html
import json
import os
import pathlib
import re
import sys
import time
from collections import defaultdict

import requests

from github_helper import debug_log, fetch_file_content, fetch_file_json, gh_get, GH
from repo_utils import get_default_branch, fetch_listings, DEFAULT_LISTINGS_PATH
from dedup_utils import get_primary_url, normalize_url as canonical_url_key
from state_utils import (load_seen, save_seen, get_cache_key,
                         should_include_item, parse_epoch)
from telegram_utils import batch_send_message, is_dry_run, safe_join_lines, send_message
from markdown_listings import parse_markdown_tables
from opportunity_filters import (tag_level, LocationMatcher, CompanyTiers, normalize_company,
                                 parse_pay, listing_pay_text, passes_pay_gate,
                                 LEVELS, LEVEL_NEW_GRAD, LOCATION_MATCH)
from company_signals import CompanyProfiles, score_listing

CONFIG_DIR = pathlib.Path(os.environ.get("PRIVATE_CONFIG_DIR")
                          or pathlib.Path(__file__).resolve().parents[1] / "private_channel")
STATE_DIR = pathlib.Path(os.environ.get("STATE_DIR", ".state/channel-digest-private"))


def _hours(*names, default=48.0):
    """First env var among names that parses as positive hours (a bad manual input falls through)."""
    for name in names:
        try:
            value = float(os.environ.get(name) or "")
        except ValueError:
            continue
        if value > 0:
            return value
    return default


WINDOW_HOURS = _hours("FORCE_WINDOW_HOURS", "WINDOW_HOURS")
SEEN_TTL_DAYS = int(os.environ.get("SEEN_TTL_DAYS", "14"))
DRY_RUN_REPORT = os.environ.get("DRY_RUN_REPORT")
MAX_TELEGRAM_MESSAGE_CHARS = 3900

LEVEL_TITLES = {LEVEL_NEW_GRAD: "🎓 New Grad", "intern": "🧪 Intern", "unknown": "❔ Level unknown"}

STAGES = ["fetched", "quality", "window", "location", "pay", "dedup", "ttl"]


def load_json(name):
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Source readers: one function per config "type"; each returns listing dicts
# ---------------------------------------------------------------------------

class SourceMissing(Exception):
    """An optional source's repo or file does not exist (yet)."""


def _is_not_found(error):
    response = getattr(error, "response", None)
    return response is not None and response.status_code == 404


def _require_file(source):
    """
    Check that a source's configured repo and path exist on the default branch.
    A 404 raises SourceMissing for "optional": true sources (skipped quietly) and
    RuntimeError otherwise, so a configured path is never swapped for another file.
    """
    repo, path = source["repo"], source["path"]
    try:
        branch = get_default_branch(repo)
        gh_get(f"{GH}/repos/{repo}/contents/{path}", ref=branch)
    except requests.HTTPError as e:
        if not _is_not_found(e):
            raise
        if source.get("optional"):
            raise SourceMissing(f"{repo}:{path} not found") from e
        raise RuntimeError(f"configured path {repo}:{path} not found") from e


def read_listings_json(source, now_epoch):
    """
    listings.json feeds through the shared fetch_listings() (default branch, then
    path detection). Sources without a "path" use the standard
    .github/scripts/listings.json probe; an explicit "path" must exist first.
    """
    if source.get("path"):
        _require_file(source)
        repo = source["repo"]
        data = fetch_file_json(repo, source["path"], ref=get_default_branch(repo))
    else:
        data = fetch_listings(source["repo"], DEFAULT_LISTINGS_PATH)
    return data if isinstance(data, list) else []


def read_markdown_tables(source, now_epoch):
    """README-style job tables, read from the repo's default branch."""
    if source.get("optional"):
        _require_file(source)
    text = fetch_file_content(source["repo"], source["path"])
    return parse_markdown_tables(text, now_epoch)


READERS = {
    "listings_json": read_listings_json,
    "markdown_tables": read_markdown_tables,
}


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------

class Funnel:
    """Survivor counts per (source, level) per stage, and drop reasons per level."""

    def __init__(self, source_names):
        self.sources = list(source_names)
        self.counts = {s: {stage: defaultdict(int) for stage in STAGES} for s in self.sources}
        self.drops = defaultdict(lambda: defaultdict(int))  # reason -> level -> n
        for gate in ("location:elsewhere", "location:remote_only", "location:unknown", "pay:below_floor"):
            self.drops[gate]  # hard gates always get a row, even at zero
        self.notes = defaultdict(lambda: defaultdict(int))  # e.g. pay status -> level -> n
        self.errors = {}
        self.skipped = {}

    def keep(self, stage, item):
        self.counts[item["_source"]][stage][item["_level"]] += 1

    def drop(self, reason, item):
        self.drops[reason][item["_level"]] += 1

    def note(self, label, item):
        self.notes[label][item["_level"]] += 1

    def table(self):
        lines = ["source | level | " + " | ".join(STAGES), "---|---|" + "|".join("---" for _ in STAGES)]
        totals = {stage: defaultdict(int) for stage in STAGES}
        for s in self.sources:
            for level in LEVELS:
                row = [self.counts[s][stage][level] for stage in STAGES]
                if not any(row):
                    continue
                for stage, n in zip(STAGES, row):
                    totals[stage][level] += n
                lines.append(f"{s} | {level} | " + " | ".join(str(n) for n in row))
        for level in LEVELS:
            lines.append(f"**all** | {level} | " + " | ".join(str(totals[stage][level]) for stage in STAGES))
        return "\n".join(lines)

    def drop_table(self):
        return self._level_table("dropped by", self.drops)

    def notes_table(self):
        return self._level_table("listings past the location gate", self.notes)

    @staticmethod
    def _level_table(title, data):
        lines = [f"{title} | " + " | ".join(LEVELS), "---|" + "|".join("---" for _ in LEVELS)]
        for label in sorted(data):
            lines.append(f"{label} | " + " | ".join(str(data[label][lv]) for lv in LEVELS))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def fetch_all(sources, funnel, profiles, now_epoch):
    items = []
    for source in sources:
        reader = READERS.get(source.get("type"))
        if not reader:
            funnel.errors[source["name"]] = f"unknown source type {source.get('type')!r}"
            continue
        try:
            raw = reader(source, now_epoch)
        except SourceMissing as e:
            funnel.skipped[source["name"]] = str(e)
            debug_log(f"[INFO] Optional source {source['name']} skipped: {e}")
            continue
        except Exception as e:
            funnel.errors[source["name"]] = str(e)
            debug_log(f"[WARN] Source {source['name']} failed: {e}")
            continue
        debug_log(f"[REPO] {source['name']} → {len(raw)} listings")
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            item = dict(entry)
            item["_source"] = source["name"]
            item["_level"] = tag_level(item, source.get("level", LEVEL_NEW_GRAD))
            profiles.register_listing(item)
            funnel.keep("fetched", item)
            items.append(item)
    return items


def passes_quality(item):
    return (should_include_item(item)
            and bool((item.get("title") or "").strip())
            and bool((item.get("company_name") or "").strip()))


def listing_epoch(item):
    return parse_epoch(item.get("date_posted")) or parse_epoch(item.get("date_updated"))


def _title_key(item):
    return re.sub(r"[^a-z0-9]+", " ", (item.get("title") or "").lower()).strip()


def listing_keys(item):
    """
    Canonical URL key plus one company+title+level key per gated metro of the
    listing. The level keeps an intern and a new-grad posting with the same title
    apart. Listings without a URL fall back to the shared cache key (id, then
    company+title).
    """
    company, title = normalize_company(item.get("company_name")), _title_key(item)
    url_key = canonical_url_key(get_primary_url(item)) or get_cache_key(item)
    level = item.get("_level") or ""
    metro_keys = [f"ct:{company}|{title}|{level}|{metro}" for metro in item.get("_metros") or []]
    return ([url_key] if url_key else []), metro_keys


def drop_seen_and_duplicates(items, seen, ttl_seconds, now_epoch, funnel):
    """
    Judge each listing on its own keys, in input order. A listing is dropped when
    its URL key is already seen, or when every one of its company+title+metro keys
    is already seen. A key is seen when seen.json holds it within the TTL window or
    an earlier listing of this run recorded it. Cross-source copies of one posting
    in the same metro therefore post once, the same title in another metro stays a
    separate opportunity, and a listing in a never-sent metro is never suppressed
    by a copy that was sent elsewhere. Kept listings carry their keys in item["_keys"].
    """
    run_seen = set()
    kept = []
    for item in items:
        url_keys, metro_keys = listing_keys(item)

        def hit(key):
            if key in run_seen:
                return "run"
            last = seen.get(key)
            return "seen" if last is not None and now_epoch - last <= ttl_seconds else None

        url_hit = next((h for h in map(hit, url_keys) if h), None)
        metro_hits = [hit(k) for k in metro_keys]
        covering = [url_hit] if url_hit else (metro_hits if metro_hits and all(metro_hits) else [])
        if "run" in covering:
            funnel.drop("dedup:url" if url_hit else "dedup:company_title_metro", item)
            continue
        funnel.keep("dedup", item)
        if covering:
            funnel.drop("ttl:already_sent", item)
            continue
        funnel.keep("ttl", item)
        item["_keys"] = [*url_keys, *metro_keys]
        run_seen.update(item["_keys"])
        kept.append(item)
    return kept


def render_level(level, scored, window_hours, strong_threshold, max_strong, max_maybe):
    """
    Header and lines for one level. scored: [(score, reasons, metros, item)]
    sorted best first. Listings past a section's cap are counted in a closing
    line and are not marked sent, so they get another chance next run.
    """
    all_strong = [s for s in scored if s[0] >= strong_threshold]
    all_maybe = [s for s in scored if s[0] < strong_threshold]
    strong, maybe = all_strong[:max_strong], all_maybe[:max_maybe]
    held_back = len(all_strong) - len(strong) + len(all_maybe) - len(maybe)
    if not strong and not maybe:
        return None, [], []

    esc = lambda s: html.escape(str(s or ""), quote=False)
    window = f"last {int(window_hours)}h" if window_hours < 72 else f"last {int(window_hours // 24)}d"
    header = f"<b>{LEVEL_TITLES[level]}</b> — {len(strong) + len(maybe)} new ({window})"
    lines = []
    if strong:
        lines.append(f"⭐ <b>Strong signal ({len(strong)})</b>")
        for score, reasons, metros, item in strong:
            url = html.escape(get_primary_url(item), quote=True)
            lines.append(f"• <b>{esc(item['company_name'])}</b> — <a href=\"{url}\">{esc(item['title'])}</a>"
                         f" · {esc(' / '.join(metros))}\n    ↳ {esc(' · '.join(reasons))} ({score})")
    if maybe:
        if strong:
            lines.append("")
        lines.append(f"🤔 <b>Maybe ({len(maybe)})</b>")
        for score, reasons, metros, item in maybe:
            url = html.escape(get_primary_url(item), quote=True)
            why = f" · <i>{esc(' · '.join(reasons) or 'no company signals found')}</i>"
            lines.append(f"• {esc(item['company_name'])} — <a href=\"{url}\">{esc(item['title'])}</a>"
                         f" · {esc(' / '.join(metros))}{why}")
    if held_back:
        lines.append(f"\n… {held_back} lower-scored listings held for a later run")
    return header, lines, [s[3] for s in strong + maybe]


def main():
    now_epoch = int(time.time())
    config = load_json("config.json")
    tiers = CompanyTiers(load_json("company_tiers.json"))
    locator = LocationMatcher(config.get("location", {}))
    scoring = config.get("scoring", {})
    pay_gate = config.get("pay_gate", {})
    hours_per_year = float(pay_gate.get("hours_per_year", 2080))
    digest_cfg = config.get("digest", {})
    profile_cfg = config.get("company_profiles", {})
    sources = config.get("sources", [])

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    seen_path = STATE_DIR / "seen.json"
    seen = load_seen(str(seen_path))
    profiles = CompanyProfiles(
        STATE_DIR / "company_profiles.json",
        refresh_days=profile_cfg.get("refresh_days", 14),
        max_lookups=int(os.environ.get("MAX_PROFILE_LOOKUPS") or profile_cfg.get("max_lookups_per_run", 60)),
        delay_seconds=profile_cfg.get("delay_seconds", 0.5),
        now_epoch=now_epoch,
    )
    dry_run = is_dry_run()
    debug_log(f"[CONFIG] sources={len(sources)} window={WINDOW_HOURS}h ttl={SEEN_TTL_DAYS}d "
              f"dry_run={dry_run} state={STATE_DIR} remote={'on' if locator.include_remote else 'off'}")

    funnel = Funnel([s["name"] for s in sources])
    items = fetch_all(sources, funnel, profiles, now_epoch)
    if not items and funnel.errors and len(funnel.errors) + len(funnel.skipped) == len(sources):
        debug_log(f"[ERROR] Every source failed: {funnel.errors}")
        return False

    cutoff = now_epoch - WINDOW_HOURS * 3600
    located = []
    for item in items:
        if not passes_quality(item):
            funnel.drop("quality", item)
            continue
        funnel.keep("quality", item)

        ts = listing_epoch(item)
        if not ts or ts < cutoff:
            funnel.drop("window", item)
            continue
        item["_timestamp"] = ts
        funnel.keep("window", item)

        outcome, metros = locator.classify(item)
        if outcome != LOCATION_MATCH:
            funnel.drop(f"location:{outcome}", item)
            continue
        item["_metros"] = metros
        funnel.keep("location", item)

        pay = parse_pay(listing_pay_text(item), hours_per_year)
        item["_pay"] = pay
        funnel.note(f"pay:{pay['status']}", item)
        if not passes_pay_gate(pay, item["_level"], pay_gate):
            funnel.drop("pay:below_floor", item)
            continue
        funnel.keep("pay", item)
        located.append(item)

    fresh = drop_seen_and_duplicates(located, seen, SEEN_TTL_DAYS * 86400, now_epoch, funnel)

    strong_threshold = int(scoring.get("strong_threshold", 3))
    by_level = defaultdict(list)
    deferred = 0
    for kept in fresh:
        tier_match = tiers.match(kept["company_name"])
        # Listed companies already clear the bar; fetch profiles only for the rest
        profile = None
        if not tier_match or int(tier_match[0].get("points", 0)) < strong_threshold:
            profile, status = profiles.lookup(kept)
            if status == profiles.PROFILE_DEFERRED:
                # Not scored yet: hold for the next run instead of posting with half the signals
                deferred += 1
                funnel.note("deferred:profile_lookup_budget", kept)
                continue
        score, reasons = score_listing(kept, kept["_level"], tier_match, profile, scoring, now_epoch)
        by_level[kept["_level"]].append((score, reasons, kept["_metros"], kept))

    rendered = []
    for level in LEVELS:
        scored = sorted(by_level.get(level, []), key=lambda s: (s[0], s[3]["_timestamp"]), reverse=True)
        header, lines, shown = render_level(
            level, [s[:4] for s in scored], WINDOW_HOURS, strong_threshold,
            digest_cfg.get("max_strong_per_level", 40), digest_cfg.get("max_maybe_per_level", 25))
        if header:
            shown_ids = {id(i) for i in shown}
            posted = [s[3] for s in scored if id(s[3]) in shown_ids]
            rendered.append((level, header, lines, posted, scored))

    debug_log("[SUMMARY] Funnel (survivors after each stage)\n" + funnel.table())
    debug_log("[SUMMARY] Drops\n" + funnel.drop_table())
    debug_log("[SUMMARY] Pay info\n" + funnel.notes_table())
    debug_log(f"[SUMMARY] Company profiles: lookups={profiles.lookups} cache_hits={profiles.cache_hits} "
              f"over_budget={profiles.skipped_budget} listings_deferred={deferred}")
    if funnel.errors:
        debug_log(f"[WARN] Source errors: {funnel.errors}")

    if dry_run:
        report_dry_run(rendered, funnel, strong_threshold)
        profiles.save()
        return True

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("PRIVATE_DIGEST_CHAT_ID")
    if not token or not chat_id:
        print("::warning::TELEGRAM_BOT_TOKEN or PRIVATE_DIGEST_CHAT_ID is not set; "
              "skipping send. See docs/GITHUB_SECRETS_SETUP.md (private job tracker channel).")
        profiles.save()
        save_seen(seen, SEEN_TTL_DAYS, str(seen_path))
        return True

    all_ok = True
    for level, header, lines, posted, _ in rendered:
        ok, results = batch_send_message(token, chat_id, header, lines,
                                         max_chars=MAX_TELEGRAM_MESSAGE_CHARS, parse_mode="HTML")
        if not ok:
            all_ok = False
            debug_log(f"[ERROR] Send failed for {level}: {results}")
            continue
        for item in posted:
            for key in item["_keys"]:
                seen[key] = now_epoch
        debug_log(f"[SEND] {level}: {len(posted)} listings")

    profiles.save()
    save_seen(seen, SEEN_TTL_DAYS, str(seen_path))
    return all_ok


def report_dry_run(rendered, funnel, strong_threshold):
    """Print what would be sent, plus a JSON report when DRY_RUN_REPORT is set."""
    print("\n===== DRY RUN: nothing sent, seen state unchanged =====")
    print(funnel.table())
    print()
    print(funnel.drop_table())
    print()
    print(funnel.notes_table())
    report = {"funnel": funnel.table(), "drops": funnel.drop_table(), "pay_info": funnel.notes_table(),
              "errors": funnel.errors, "skipped_optional_sources": funnel.skipped,
              "strong_threshold": strong_threshold, "levels": {}}
    for level, header, lines, posted, scored in rendered:
        batches = safe_join_lines(header, lines, MAX_TELEGRAM_MESSAGE_CHARS)
        print(f"\n----- {level}: {len(posted)} listings in {len(batches)} message(s) -----")
        for b in batches:
            send_message(None, None, b, "HTML")  # DRY_RUN: logs a [DRY-SENT] record, sends nothing
        report["levels"][level] = {
            "messages": batches,
            "scored": [{"score": s, "reasons": r, "metros": m, "company": i["company_name"],
                        "title": i["title"], "source": i["_source"], "url": get_primary_url(i),
                        "on_a_tier_list": any(x.startswith("tier:") for x in r)}
                       for s, r, m, i in scored],
        }
    if DRY_RUN_REPORT:
        pathlib.Path(DRY_RUN_REPORT).write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(f"\n[dry run report] {DRY_RUN_REPORT}")


if __name__ == "__main__":
    sys.exit(0 if main() else 1)

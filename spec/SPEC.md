# Job tracker spec

The rules the bot must follow, in plain words. Every rule has an id, and the test that checks it carries that id in its name (`spec/test_*_props.py`, so `BATCH-2` is checked by `test_batch_2_*`). **Known bug** marks a rule the code breaks today. Its test is `xfail(strict=True)`, so CI goes red the day the bug is fixed until that fix removes the marker. **Unchecked** marks a rule that has no test yet. The spec is locked: change it only when a task says to (see `docs/TESTING.md`).

## Message splitting (`telegram_utils.safe_join_lines`)

- **BATCH-1** Splitting gives back exactly the input lines, in order. The first message starts with the header and a blank line; every later one starts with `(cont.)` and a blank line.
- **BATCH-2** When every line fits, no message is longer than `max_chars`.
- **BATCH-3** No message is longer than Telegram's 4096 limit, counted in UTF-16 units (an emoji outside the Basic Multilingual Plane counts 2). This holds at the default 3900 limit for lines of up to 300 one-unit characters.

## Role routing (`job_filtering`)

- **ROUTE-1** The DM classifier returns only "Software Engineering", "Data Science, AI & Machine Learning", or nothing.
- **ROUTE-2** Hardware, Product and Quant labels, in any letter case, never reach DMs.

## Duplicate removal (`dedup_utils`)

- **DEDUP-1** The key ignores letter case in the scheme and host, a trailing slash, and the `#fragment`. Letter case in the path is undecided.
- **DEDUP-2** Two listings share a key only if they are the same posting: query parameters that identify a posting (`?gh_jid=...`) count. The reference is `send_private_digest.canonical_url_key`. Known bug: the key drops the whole query, so distinct postings on one careers page merge into one.
- **DEDUP-3** The digest dedup and the "already posted" memory turn a URL into a key the same way. Known bug: the two `normalize_url` copies disagree on blank input (`" "` gives `":"` in one and `None` in the other).
- **DEDUP-4** The key is the normalized URL first, then the listing `id`, then (company, title) in lower case. The memory uses the same order.

## The "already posted" memory (`state_utils`)

- **SEEN-1** A listing in the memory is never reported as new. It stays quiet up to and including exactly `ttl` seconds after its last alert, and alerts one second later.
- **SEEN-2** Once a listing would alert at some time, it would alert at every later time.
- **SEEN-3** An update counts as a reopen only if it comes at least 1 day (86400 seconds) after the last alert. Updates inside that day fall back to the TTL rule.
- **SEEN-4** A listing with no URL, no id and no company-plus-title never alerts.
- **SEEN-5** Good values in `seen.json` load as whole seconds, and one bad value drops only its own entry. Known bug: one bad value wipes the whole memory, so every listing alerts again.

## DM location labels (`format_utils.format_location`)

- **LOC-1** A multi-location DM says "California" exactly when one of its locations is in California. Otherwise it says "New York" exactly when one is in New York, and otherwise "New Jersey" exactly when one is in New Jersey.
- **LOC-2** A single location is shown as given; several locations in a digest show as "Multi-location".

## Time windows (`state_utils.parse_epoch`, `send_digest_multi.parse_dt`)

- **WIN-1** A timestamp without a timezone means UTC. Known bug: `parse_epoch` reads it in the machine's local time, so a laptop outside UTC shifts it by hours.
- **WIN-2** Both parsers agree on epoch numbers (as numbers or text) and on ISO timestamps that carry a timezone.
- **WIN-3** A listing is inside a window of `hours` exactly when `now - hours * 3600 <= timestamp`; the boundary counts as inside. Unchecked: both checks are inline in `main()` and get a test once they are pulled out into a function.

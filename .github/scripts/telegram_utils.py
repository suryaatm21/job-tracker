#!/usr/bin/env python3
"""
Telegram messaging utilities with smart batching for long messages.

Provides utilities to send messages to Telegram with automatic batching
when messages exceed the 4096 character limit, while preserving line breaks
and providing clear continuation headers.
"""
import atexit
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
import requests
from typing import List, Tuple, Optional


def is_dry_run() -> bool:
    """True when DRY_RUN is set: print messages instead of sending, persist no state."""
    return os.getenv("DRY_RUN", "false").strip().lower() in ("1", "true", "yes")


# Sent-message log: one "[SENT] {json}" line per message (or "[DRY-SENT]" under
# DRY_RUN) so the run log records exactly what the bot posted. The chat is a
# human label from TELEGRAM_CHAT_LABEL (set per workflow), never the raw id.
SENT_PREFIX = "[SENT]"
DRY_SENT_PREFIX = "[DRY-SENT]"
SUMMARY_PREFIX = "[SENT-SUMMARY]"
UNKNOWN_CHAT_LABEL = "unknown"
_ERROR_MAX_CHARS = 300

_sent_counts: Counter = Counter()
_failed_counts: Counter = Counter()
_summary_registered = False


def _chat_label(chat_label: Optional[str]) -> str:
    return chat_label or os.getenv("TELEGRAM_CHAT_LABEL") or UNKNOWN_CHAT_LABEL


def _scrub(text: str, *secrets: Optional[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "[REDACTED]")
    return text


def _to_log_line(prefix: str, record: dict) -> str:
    # Escape the characters str.splitlines() treats as line breaks but json.dumps
    # leaves raw, so every record stays on one physical line.
    body = json.dumps(record, ensure_ascii=False)
    for raw, escaped in (("\u0085", "\\u0085"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        body = body.replace(raw, escaped)
    return f"{prefix} {body}"


def _telegram_result(ok: bool, status: int, body: str) -> Tuple[Optional[int], Optional[str]]:
    """(message_id, error) from a sendMessage response."""
    try:
        parsed = json.loads(body)
    except (TypeError, ValueError):
        parsed = None
    if ok:
        result = parsed.get("result") if isinstance(parsed, dict) else None
        return (result.get("message_id") if isinstance(result, dict) else None), None
    if isinstance(parsed, dict) and parsed.get("description"):
        return None, f"{status} {parsed['description']}"
    return None, f"{status} {body}" if status else str(body)


def log_sent(text: str, ok: Optional[bool], message_id: Optional[int] = None,
             error: Optional[str] = None, chat_label: Optional[str] = None,
             secrets: Tuple[Optional[str], ...] = ()) -> str:
    """Print one sent-message record and count it for the run summary.

    ok=None marks a dry run. Returns the printed line.
    """
    global _summary_registered
    dry_run = ok is None
    label = _chat_label(chat_label)
    if error is not None:
        error = _scrub(str(error), *secrets)[:_ERROR_MAX_CHARS]
    record = {
        "chat": label,
        "dry_run": dry_run,
        "ok": ok,
        "message_id": message_id,
        "error": error,
        "chars": len(text),
        "run_id": os.getenv("GITHUB_RUN_ID"),
        "sha": os.getenv("GITHUB_SHA"),
        "workflow": os.getenv("GITHUB_WORKFLOW"),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "text": text,
    }
    line = _to_log_line(DRY_SENT_PREFIX if dry_run else SENT_PREFIX, record)
    print(line, flush=True)

    _sent_counts[label] += 1
    if ok is False:
        _failed_counts[label] += 1
    if not _summary_registered:
        atexit.register(print_sent_summary)
        _summary_registered = True
    return line


def print_sent_summary() -> None:
    """Print one [SENT-SUMMARY] line per chat this process sent to."""
    for label in sorted(_sent_counts):
        record = {
            "chat": label,
            "dry_run": is_dry_run(),
            "messages": _sent_counts[label],
            "failures": _failed_counts[label],
            "run_id": os.getenv("GITHUB_RUN_ID"),
            "sha": os.getenv("GITHUB_SHA"),
            "workflow": os.getenv("GITHUB_WORKFLOW"),
        }
        print(_to_log_line(SUMMARY_PREFIX, record), flush=True)
    _sent_counts.clear()
    _failed_counts.clear()


def send_message(token: str, chat_id: str, text: str, parse_mode: Optional[str] = None,
                 chat_label: Optional[str] = None) -> Tuple[bool, int, str]:
    """
    Send a single message to Telegram.

    Every script the workflows run sends through here, so DRY_RUN is enforced
    here (no network call, no credentials required) and every message, dry or
    live, is logged once via log_sent().

    Args:
        token: Telegram bot token
        chat_id: Target chat ID
        text: Message text to send
        parse_mode: Optional parse mode ("HTML", "Markdown", etc.)
        chat_label: Chat name for the log; defaults to $TELEGRAM_CHAT_LABEL

    Returns:
        tuple[bool, int, str]: (success, status_code, response_body)
    """
    if is_dry_run():
        print(f"[DRY-RUN] Would send {len(text)} chars (parse_mode={parse_mode}):")
        print("----- BEGIN MESSAGE -----")
        print(text)
        print("----- END MESSAGE -----")
        log_sent(text, None, chat_label=chat_label)
        return True, 200, "dry-run: not sent"

    secrets = (token, chat_id)
    if not token or not chat_id:
        body = "Missing token or chat_id"
        log_sent(text, False, error=body, chat_label=chat_label, secrets=secrets)
        return False, 0, body

    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True
    }

    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=30
        )
        ok, status, body = response.ok, response.status_code, response.text
    except Exception as e:
        # The exception text can include the request URL, which embeds the token.
        ok, status, body = False, 0, _scrub(f"Request failed: {e}", *secrets)

    message_id, error = _telegram_result(ok, status, body)
    log_sent(text, ok, message_id=message_id, error=error, chat_label=chat_label, secrets=secrets)
    return ok, status, body

TELEGRAM_LIMIT = 4096  # sendMessage text limit, counted in UTF-16 code units
CONT_PREFIX = "(cont.)\n\n"


def tg_len(text: str) -> int:
    """Length as Telegram counts it: UTF-16 code units, so an emoji outside the BMP counts 2."""
    return len(text.encode("utf-16-le")) // 2


def _split_line(line: str, max_units: int) -> List[str]:
    """
    Cut a line into pieces of at most max_units UTF-16 units, never inside a
    surrogate pair. Each cut falls after the last space in the piece when there
    is one, so words, URLs and HTML tags usually stay whole.
    """
    pieces = []
    while tg_len(line) > max_units:
        cut, units = 0, 0
        for ch in line:
            width = 2 if ord(ch) > 0xFFFF else 1
            if units + width > max_units:
                break
            cut += 1
            units += width
        cut = max(cut, 1)
        space = line.rfind(" ", 0, cut)
        if space > 0:
            cut = space + 1
        pieces.append(line[:cut])
        line = line[cut:]
    pieces.append(line)
    return pieces


def safe_join_lines(header: str, lines: List[str], max_chars: int = 3900) -> List[str]:
    """
    Join lines into batches that fit within character limit, preserving whole lines.

    Lengths are counted in UTF-16 units, as Telegram counts them. A line that
    cannot fit in one Telegram message even on its own is split into pieces;
    every other line stays whole, even one longer than max_chars.

    Args:
        header: Header text for first batch
        lines: List of content lines to batch
        max_chars: Maximum characters per batch (capped at TELEGRAM_LIMIT)

    Returns:
        list[str]: List of message batches ready to send
    """
    max_chars = min(max_chars, TELEGRAM_LIMIT)
    if tg_len(header) > max_chars // 2:
        print(f"ERROR: Telegram header is {tg_len(header)} units; truncating to {max_chars // 2}")
        header = _split_line(header, max(max_chars // 2, 1))[0]
    if not lines:
        return [header] if header else []

    # Room for a line alone in a batch under the longer of the two prefixes
    room = TELEGRAM_LIMIT - max(tg_len(header) + 2, tg_len(CONT_PREFIX))
    fitted = []
    for line in lines:
        if tg_len(line) > room:
            fitted.extend(_split_line(line, max(room, 1)))
        else:
            fitted.append(line)

    batches = []
    current_batch = []
    current_length = tg_len(header) + 1  # header + "\n\n", less the first line's newline

    for line in fitted:
        line_length = tg_len(line) + 1  # +1 for newline

        # If adding this line would exceed limit, start new batch
        if current_batch and current_length + line_length > max_chars:
            # Finish current batch
            if batches:
                # Continuation batch
                batch_text = CONT_PREFIX + "\n".join(current_batch)
            else:
                # First batch with header
                batch_text = header + "\n\n" + "\n".join(current_batch)
            
            batches.append(batch_text)
            
            # Start new batch
            current_batch = [line]
            current_length = tg_len(CONT_PREFIX) + line_length
        else:
            # Add line to current batch
            current_batch.append(line)
            current_length += line_length
    
    # Add final batch if there's content
    if current_batch:
        if batches:
            # Final continuation batch
            batch_text = CONT_PREFIX + "\n".join(current_batch)
        else:
            # Single batch with header
            batch_text = header + "\n\n" + "\n".join(current_batch)
        
        batches.append(batch_text)
    
    return batches

def batch_send_message(
    token: str, 
    chat_id: str, 
    header: str, 
    lines: List[str], 
    max_chars: int = 3900,
    sleep_ms: int = 250,
    parse_mode: Optional[str] = None,
    chat_label: Optional[str] = None
) -> Tuple[bool, List[Tuple[int, int, str]]]:
    """
    Send a long message as multiple batched messages, preserving line integrity.
    
    Args:
        token: Telegram bot token
        chat_id: Target chat ID
        header: Header text for the first message
        lines: List of content lines
        max_chars: Maximum characters per message (default 3900 for safety)
        sleep_ms: Delay between messages in milliseconds
        parse_mode: Optional parse mode ("HTML", "Markdown", etc.)
        chat_label: Chat name for the sent-message log; defaults to $TELEGRAM_CHAT_LABEL
    
    Returns:
        tuple[bool, list]: (all_successful, [(batch_num, status_code, response_body), ...])
    """
    batches = safe_join_lines(header, lines, max_chars)
    
    if not batches:
        return True, []
    
    results = []
    all_successful = True
    
    for i, batch in enumerate(batches, 1):
        print(f"[BATCH] {i}/{len(batches)}, chars={len(batch)}, lines={batch.count(chr(10))}")
        
        success, status, body = send_message(token, chat_id, batch, parse_mode, chat_label=chat_label)
        results.append((i, status, body))
        
        if not success:
            all_successful = False
            print(f"[BATCH] {i} failed: {status} - {body}")
        
        # Sleep between batches to avoid rate limiting (except after last batch)
        if i < len(batches) and sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)
    
    return all_successful, results

def send_telegram_with_batching(text: str, parse_mode: Optional[str] = None) -> bool:
    """
    Convenience function to send a message using environment variables.
    Automatically handles batching if the message is too long.
    
    Args:
        text: Message text to send
        parse_mode: Optional parse mode ("HTML", "Markdown", etc.)
    
    Returns:
        bool: True if all messages sent successfully
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not is_dry_run() and (not token or not chat_id):
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False
    
    # If message is short enough, send as single message
    if tg_len(text) <= 4000:
        success, status, body = send_message(token, chat_id, text, parse_mode)
        if not success:
            print(f"Telegram send failed: {status} - {body}")
        return success
    
    # Split into header and lines for batching
    lines = text.split('\n')
    if not lines:
        return True
    
    # Use first line as header, rest as content
    header = lines[0]
    content_lines = lines[1:]
    
    success, results = batch_send_message(token, chat_id, header, content_lines, parse_mode=parse_mode)
    
    if not success:
        print(f"Some batches failed: {[r for r in results if r[1] < 200 or r[1] >= 300]}")
    
    return success

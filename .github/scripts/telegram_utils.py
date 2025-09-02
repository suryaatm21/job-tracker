#!/usr/bin/env python3
"""
Telegram messaging utilities with smart batching for long messages.

Provides utilities to send messages to Telegram with automatic batching
when messages exceed the 4096 character limit, while preserving line breaks
and providing clear continuation headers.
"""
import os
import time
import requests
from typing import List, Tuple, Optional

def send_message(token: str, chat_id: str, text: str, parse_mode: Optional[str] = None) -> Tuple[bool, int, str]:
    """
    Send a single message to Telegram.
    
    Args:
        token: Telegram bot token
        chat_id: Target chat ID  
        text: Message text to send
        parse_mode: Optional parse mode ("HTML", "Markdown", etc.)
    
    Returns:
        tuple[bool, int, str]: (success, status_code, response_body)
    """
    if not token or not chat_id:
        return False, 0, "Missing token or chat_id"
    
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
        
        return response.ok, response.status_code, response.text
        
    except Exception as e:
        return False, 0, f"Request failed: {e}"

def safe_join_lines(header: str, lines: List[str], max_chars: int = 3900) -> List[str]:
    """
    Join lines into batches that fit within character limit, preserving whole lines.
    
    Args:
        header: Header text for first batch
        lines: List of content lines to batch
        max_chars: Maximum characters per batch
    
    Returns:
        list[str]: List of message batches ready to send
    """
    if not lines:
        return [header] if header else []
    
    batches = []
    current_batch = []
    current_length = len(header)
    
    for line in lines:
        line_length = len(line) + 1  # +1 for newline
        
        # If adding this line would exceed limit, start new batch
        if current_batch and current_length + line_length > max_chars:
            # Finish current batch
            if batches:
                # Continuation batch
                batch_text = "(cont.)\n\n" + "\n".join(current_batch)
            else:
                # First batch with header
                batch_text = header + "\n\n" + "\n".join(current_batch)
            
            batches.append(batch_text)
            
            # Start new batch
            current_batch = [line]
            current_length = 10 + line_length  # 10 for "(cont.)\n\n"
        else:
            # Add line to current batch
            current_batch.append(line)
            current_length += line_length
    
    # Add final batch if there's content
    if current_batch:
        if batches:
            # Final continuation batch
            batch_text = "(cont.)\n\n" + "\n".join(current_batch)
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
    parse_mode: Optional[str] = None
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
        
        success, status, body = send_message(token, chat_id, batch, parse_mode)
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
    
    if not token or not chat_id:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False
    
    # If message is short enough, send as single message
    if len(text) <= 4000:
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

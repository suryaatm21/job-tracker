#!/usr/bin/env python3
"""
Test script to verify Telegram bot connectivity and send a test message.
Usage: python test_telegram.py
"""

import os
import requests

def test_telegram():
    """Send a test message to verify bot is working"""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not bot_token:
        print("❌ TELEGRAM_BOT_TOKEN environment variable not set")
        return False
        
    if not chat_id:
        print("❌ TELEGRAM_CHAT_ID environment variable not set")
        return False
    
    print(f"🤖 Bot Token: {bot_token[:10]}...{bot_token[-5:]}")
    print(f"💬 Chat ID: {chat_id}")
    
    # Test message
    test_message = "✅ Job Tracker Bot Test - Connection successful!"
    
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": test_message,
                "disable_web_page_preview": True
            }
        )
        
        print(f"📤 HTTP Status: {response.status_code}")
        print(f"📥 Response: {response.text}")
        
        if response.status_code == 200:
            print("✅ Test message sent successfully!")
            return True
        else:
            print("❌ Failed to send test message")
            return False
            
    except Exception as e:
        print(f"❌ Error sending message: {e}")
        return False

if __name__ == "__main__":
    test_telegram()

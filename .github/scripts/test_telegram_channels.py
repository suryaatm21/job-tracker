#!/usr/bin/env python3
"""
Test script to verify all Telegram channel secrets are configured correctly.
Sends a test message to each channel.
"""
import os
import sys

# Load .env file if present
try:
    from pathlib import Path
    env_file = Path(__file__).parent.parent.parent / '.env'
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    if key and value:
                        os.environ[key] = value
        print(f"✓ Loaded environment from {env_file}")
except Exception as e:
    print(f"⚠️  Could not load .env file: {e}")

from telegram_utils import send_message

# Define all channel secrets to test
CHANNELS = {
    "Main Channel (SWE/ML BS)": "TELEGRAM_CHAT_ID_CHANNEL",
    "Hardware Channel": "TELEGRAM_CHAT_ID_CHANNEL_HARDWARE",
    "Quant Finance Channel": "TELEGRAM_CHAT_ID_CHANNEL_QUANT",
    "Product Management Channel": "TELEGRAM_CHAT_ID_CHANNEL_PM",
    "PhD/MS Channel (SWE/ML)": "TELEGRAM_CHAT_ID_CHANNEL_SWE_ML_PHD",
}

def test_channel(name, env_var):
    """Test sending a message to a single channel"""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv(env_var)
    
    if not token:
        print(f"❌ {name}: Missing TELEGRAM_BOT_TOKEN")
        return False
    
    if not chat_id:
        print(f"⚠️  {name}: Missing {env_var} - skipping")
        return None  # Not configured yet, not a failure
    
    test_message = f"🧪 Test message for {name}\n\nThis is a connectivity test. If you see this, the bot can send messages to this channel successfully! ✅"
    
    success, status, body = send_message(token, chat_id, test_message)
    
    if success:
        print(f"✅ {name}: Message sent successfully (status={status})")
        return True
    else:
        print(f"❌ {name}: Failed to send message (status={status})")
        print(f"   Error: {body[:200]}")
        return False

def main():
    print("=" * 70)
    print("Testing Telegram Channel Connectivity")
    print("=" * 70)
    print()
    
    results = {}
    for name, env_var in CHANNELS.items():
        result = test_channel(name, env_var)
        results[name] = result
        print()
    
    print("=" * 70)
    print("Summary:")
    print("-" * 70)
    
    configured = [name for name, result in results.items() if result is not None]
    successful = [name for name, result in results.items() if result is True]
    failed = [name for name, result in results.items() if result is False]
    not_configured = [name for name, result in results.items() if result is None]
    
    print(f"Total channels defined: {len(CHANNELS)}")
    print(f"Configured channels: {len(configured)}")
    print(f"✅ Successful: {len(successful)}")
    print(f"❌ Failed: {len(failed)}")
    print(f"⚠️  Not configured: {len(not_configured)}")
    
    if not_configured:
        print(f"\nNot configured yet:")
        for name in not_configured:
            env_var = CHANNELS[name]
            print(f"  - {name} ({env_var})")
    
    if failed:
        print(f"\nFailed channels:")
        for name in failed:
            print(f"  - {name}")
        print("\nPlease check:")
        print("  1. Bot is added to the channel as admin")
        print("  2. Chat ID is correct (use /getUpdates API)")
        print("  3. Bot has permission to post messages")
    
    print("=" * 70)
    
    # Exit with error if any configured channel failed
    if failed:
        sys.exit(1)
    
    # Exit successfully if all configured channels work
    if successful and not failed:
        print("\n🎉 All configured channels are working!")
        sys.exit(0)
    
    # No channels configured
    if not configured:
        print("\n⚠️  No channels configured yet. Please add secrets to GitHub.")
        sys.exit(1)

if __name__ == "__main__":
    main()

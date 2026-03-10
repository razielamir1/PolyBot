"""
get_chat_id.py — Helper script to obtain your Telegram Chat ID.

Usage:
    1.  Run this script:  python get_chat_id.py
    2.  Open Telegram and send any message to your bot.
    3.  The script will print your Chat ID and exit.

Paste the Chat ID into your .env file as TELEGRAM_CHAT_ID.
"""

import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_API = "https://api.telegram.org"
POLL_INTERVAL = 2  # seconds between checks


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("ERROR: Set TELEGRAM_BOT_TOKEN in your .env file first.")
        sys.exit(1)

    url = f"{TELEGRAM_API}/bot{token}/getUpdates"
    print("Waiting for you to send a message to the bot …")
    print("(Open Telegram → find your bot → send any message)\n")

    last_update_id = 0

    while True:
        try:
            params = {"offset": last_update_id + 1, "timeout": 30}
            resp = requests.get(url, params=params, timeout=35)
            data = resp.json()

            if not data.get("ok"):
                print(f"Telegram API error: {data}")
                time.sleep(POLL_INTERVAL)
                continue

            results = data.get("result", [])
            for update in results:
                last_update_id = update["update_id"]
                message = update.get("message")
                if message:
                    chat = message["chat"]
                    chat_id = chat["id"]
                    first_name = chat.get("first_name", "")
                    print("=" * 40)
                    print(f"Chat ID:  {chat_id}")
                    print(f"Name:     {first_name}")
                    print("=" * 40)
                    print(
                        f"\nAdd this to your .env:\n"
                        f"  TELEGRAM_CHAT_ID={chat_id}\n"
                    )
                    sys.exit(0)

        except requests.exceptions.RequestException as exc:
            print(f"Network error: {exc}  — retrying …")
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()

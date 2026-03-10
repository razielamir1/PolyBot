Polymarket Trend-Spotting Bot
===========================

This bot monitors specific Polymarket events (Politics) and alerts you via Telegram
when a significant price spike is detected within a 5-minute rolling window.

Setup Instructions:
-------------------
1. Install Python 3.10 or higher.
2. Open a terminal in this project folder.
3. Create a virtual environment:
   python -m venv .venv
4. Activate the virtual environment:
   - Windows: .venv\Scripts\activate
   - Mac/Linux: source .venv/bin/activate
5. Install dependencies:
   pip install -r requirements.txt
6. Configure your credentials:
   - Open '.env' in a text editor.
   - Enter your TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
7. Run the bot:
   python main.py

Files:
------
- main.py: The main execution loop.
- fetcher.py: Handles data retrieval from Polymarket APIs.
- state.py: Manages the price rolling window and logic.
- alert.py: Handles Telegram bot messaging.
- requirements.txt: Python libraries needed.
- .env: Application settings and keys.

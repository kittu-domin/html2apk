"""
config.py — All configuration in one place.
Edit this file before deploying.
"""

import os


class Config:
    # ── Bot credentials ─────────────────────────────────────────────────────
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
    OWNER_ID:  int = int(os.getenv("OWNER_ID", "123456789"))   # Your numeric Telegram ID

    # ── Support contact ──────────────────────────────────────────────────────
    SUPPORT_USERNAME: str = os.getenv("SUPPORT_USERNAME", "your_support_username")

    # ── Telethon userbot (for >50 MB file delivery) ──────────────────────────
    # Get these from https://my.telegram.org → API development tools
    API_ID:   int = int(os.getenv("API_ID",   "0"))
    API_HASH: str = os.getenv("API_HASH",  "your_api_hash_here")

    # ── Paths ────────────────────────────────────────────────────────────────
    DB_PATH:      str = os.getenv("DB_PATH",      "apk_bot.db")
    SESSION_FILE: str = os.getenv("SESSION_FILE", "userbot_session")

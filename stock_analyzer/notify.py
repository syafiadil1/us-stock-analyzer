"""Optional Telegram alerts. Does nothing unless both env vars are set:

    TELEGRAM_BOT_TOKEN  (from @BotFather)
    TELEGRAM_CHAT_ID    (your chat id, e.g. from @userinfobot)

Never put the token in code or commit it.
"""
from __future__ import annotations

import os
import urllib.parse
import urllib.request


def enabled() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def send(text: str) -> bool:
    """Send a Telegram message. Returns True on success; never raises."""
    if not enabled():
        return False
    token, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:  # the error text can contain the token URL, so print only the type
        print(f"[notify] Telegram send failed: {type(e).__name__}")
        return False

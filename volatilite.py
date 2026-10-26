import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError
import re
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape

# AZCRYPTO SHORT SCANNER — Binance USDT-M Futures
# Sadece sinyal üretir; gerçek emir açmaz.

BASE_URL = "https://fapi.binance.com"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "1623907197").strip()
PORT = int(os.environ.get("PORT", "10000"))

SCAN_INTERVAL_SECONDS = max(
    120, int(os.environ.get("SCAN_INTERVAL_SECONDS", "180"))
)
MAX_SYMBOLS = max(
    3, min(10, int(os.environ.get("MAX_SYMBOLS", "8")))
)
MIN_QUOTE_VOLUME_USDT = float(
    os.environ.get("MIN_QUOTE_VOLUME_USDT", "5000000")
)
MIN_PUMP_5M_PCT = float(
    os.environ.get("MIN_PUMP_5M_PCT", "1.5")
)
MIN_SINGLE_CANDLE_PCT = float(
    os.environ.get("MIN_SINGLE_CANDLE_PCT", "1.0")
)
MIN_VOLUME_RATIO = float(
    os.environ.get("MIN_VOLUME_RATIO", "1.2")
)
RSI_MIN = float(os.environ.get("RSI_MIN", "60"))
COOLDOWN_SECONDS = int(
    os.environ.get("COOLDOWN_SECONDS", "1200")
)
DATA_FILE = os.environ.get("DATA_FILE", "shortbot_data.json")

TR_TZ = timezone(timedelta(hours=3))

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("azcrypto-short")

# Binance 418/429 hatalarında bekleme uygula.
BINANCE_COOLDOWN_SECONDS = int(
    os.environ.get("BINANCE_COOLDOWN_SECONDS", "7200")
)
COOLDOWN_FILE = os.environ.get(
    "COOLDOWN_FILE", "binance_cooldown.json"
)

_saved_pause = 0.0
try:
    with open(COOLDOWN_FILE, "r", encoding="utf-8") as f:
        _saved_pause = float(json.load(f).get("until", 0))
except Exception:
    pass

BINANCE_COOLDOWN_UNTIL = _saved_pause
TELEGRAM_POLLING_DISABLED = False


class BinanceRateLimitError(Exception):
    pass


def tr_saat():
    return datetime.now(TR_TZ).strftime("%Y-%m-%d %H:%M:%S")


def api_json(path, params=None, timeout=12):
    global BINANCE_COOLDOWN_UNTIL

    remaining = int(BINANCE_COOLDOWN_UNTIL - time.time())
    if remaining > 0:
        raise BinanceRateLimitError(
            f"Binance beklemede: {remaining} saniye"
        )

    query = urllib.parse.urlencode(params or {})
    url = BASE_URL + path + (("?" + query) if query else "")

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "AZCryptoShortScanner/1.0",
            "Accept": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    except HTTPError as exc:
        if exc.code in (418, 429):
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            except Exception:
                pass

            wait_seconds = (
                600 if exc.code == 429
                else BINANCE_COOLDOWN_SECONDS
            )

            retry_after = (
                exc.headers.get("Retry-After")
                if exc.headers else None
            )

            try:
                if retry_after:
                    wait_seconds = max(
                        wait_seconds, int(float(retry_after))
                    )
            except (TypeError, ValueError):
                pass

            match = re.search(r"(\d+)\s*seconds?", body, re.I)
            if match:
                wait_seconds = max(
                    wait_seconds, int(match.group(1))
                )

            BINANCE_COOLDOWN_UNTIL = time.time() + wait_seconds

            try:
                with open(COOLDOWN_FILE, "w", encoding="utf-8") as f:
                    json.dump(
                        {"until": BINANCE_COOLDOWN_UNTIL}, f
                    )
            except Exception as save_exc:
                log.warning(
                    "Bekleme süresi kaydedilemedi: %s", save_exc
                )

            log.error(
                "Binance HTTP %s: %d saniye bekleme; istekler durduruldu.",
                exc.code, wait_seconds
            )

            raise BinanceRateLimitError(
                f"Binance HTTP {exc.code}; "
                f"{wait_seconds} saniye bekleniyor"
            ) from exc

        raise


def tg(message):
    if not BOT_TOKEN or not CHAT_ID:
        log.warning(
            "BOT_TOKEN veya CHAT_ID eksik; mesaj gönderilmedi."
        )
        return False

    payload = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true"
    }).encode("utf-8")

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))

        if not result.get("ok"):
            log.error("Telegram API hatası: %s", result)
            return False

        return True

    except Exception:
        log.exception("Telegram mesajı gönderilemedi")
        return False

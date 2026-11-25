# ==============================================================================
# SPOT AKIS TERMINALI - 7/24 RENDER BOT.PY (WEB AYARLARIYLA OTOMATIK URETILDI)
# Uretim: 15:51:21 · Rev #6
# Aktif Motorlar: ATH/ATL(1M) | AI(BOTH,15dk)
# Oto Futures (AKTIF · 3x · Yon:BOTH · $25 · TP +%10 / SL -%10): ATL
# ==============================================================================
import asyncio
import base64
import concurrent.futures
import copy
import hashlib
import hmac
import json
import math
import os
import re
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib
from collections import deque
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Set, Tuple

# Web panelinden secilen guncel tikler ve parametreler (GitHub volatilite.py / bot.py ile birebir degistirin):
DEFAULT_STRATEGY_CONFIG: Dict[str, Any] = {
    "pumpEnabled": False,
    "pumpThresholdPct": 4,
    "pumpMinVolUsdt": 150000,
    "pumpRepeatHours": 2,
    "pump15mEnabled": False,
    "pump15mThresholdPct": 3.2,
    "pump15mMinRvol": 1.4,
    "athEnabled": False,
    "atlEnabled": True,
    "athAtlTimeframes": ["1M"],
    "athAtlLockPct": 1,
    "athAtlCooldownMin": 5,
    "ma200Enabled": False,
    "ma200LongEnabled": True,
    "ma200ShortEnabled": True,
    "ma200Timeframe": "4h",
    "ma200Period": 200,
    "volatilityEnabled": False,
    "volatilityTimeframes": ["15m", "1h"],
    "volatilityMajorOnly": True,
    "volatilityMajorCoins": [
        "BTC",
        "ETH",
        "BNB",
        "SOL",
        "XRP",
        "ADA",
        "DOGE",
        "AVAX",
        "LINK",
        "DOT",
        "SUI",
        "TON",
        "LTC",
        "TRX",
        "NEAR",
        "PEPE",
    ],
    "volatilityAtrMult": 2.2,
    "volatilityMinMovePct": 1.8,
    "volatilityLongEnabled": True,
    "volatilityShortEnabled": True,
    "patternsEnabled": False,
    "patternTimeframes": ["15m", "1h", "4h", "1d"],
    "enabledPatterns": {
        "DOUBLE_BOTTOM": True,
        "DOUBLE_TOP": True,
        "HEAD_SHOULDERS": True,
        "INV_HEAD_SHOULDERS": True,
        "BULL_FLAG": True,
        "BEAR_FLAG": True,
        "BEAR_RECTANGLE": True,
    },
    "patternMinHeightPct": 1.2,
    "bollingerEnabled": False,
    "bollingerTimeframes": ["15m", "1h", "4h"],
    "bollingerPeriod": 20,
    "bollingerStdDev": 2,
    "bollingerLongEnabled": True,
    "bollingerShortEnabled": True,
    "ichimokuEnabled": False,
    "ichimokuTimeframes": ["15m", "1h", "4h"],
    "ichimokuLongEnabled": True,
    "ichimokuShortEnabled": True,
    "supertrendEnabled": False,
    "supertrendTimeframes": ["15m", "1h", "4h"],
    "supertrendAtrPeriod": 10,
    "supertrendMultiplier": 3,
    "supertrendLongEnabled": True,
    "supertrendShortEnabled": True,
    "aiMarketEnabled": True,
    "aiMarketDirection": "BOTH",
    "aiMarketIntervalMin": 15,
    "aiMarketMinConfidence": 72,
    "multiExchangeEnabled": False,
    "multiExchangeDirection": "BOTH",
    "multiExchangeThresholdPct": 1.8,
    "multiExchangeMinExchanges": 3,
    "multiExchangeWindowMin": 3,
    "bigPumpEnabled": False,
    "bigPumpDirection": "BOTH",
    "bigPumpThresholdPct": 5,
    "bigPumpWindowMin": 15,
    "bigPumpCooldownMin": 10,
    "bigPumpMinVolUsdt": 100000,
    "bigPumpExchanges": ["BYBIT", "BINANCE", "BINGX", "MEXC", "OKX", "GATE", "KUCOIN", "BITGET"],
    "hourlyGainersReportEnabled": True,
    "dailyWinnerReportEnabled": True,
    "top24SummaryReportEnabled": True,
    "autoTradeEngines": {
        "PUMP": False,
        "PUMP15M": False,
        "ATL": True,
        "ATH": False,
        "MA200_4H": False,
        "VOLATILITY": False,
        "PATTERN": False,
        "BOLLINGER": False,
        "ICHIMOKU": False,
        "SUPERTREND": False,
        "AI_MARKET": False,
        "MULTI_EXCHANGE": False,
        "BIG_PUMP": False,
    },
}

STRATEGY_CONFIG: Dict[str, Any] = copy.deepcopy(DEFAULT_STRATEGY_CONFIG)

DEFAULT_AUTO_TRADE: Dict[str, Any] = {
    "enabled": True,
    "mode": "PAPER",
    "directionMode": "BOTH",
    "leverage": 3,
    "tradeUsdt": 25,
    "tpPct": 10,
    "slPct": 10,
    "maxOpenTrades": 0,
    "rsiMin": 70,
    "notifyPaperTelegram": False,
}

AUTO_TRADE: Dict[str, Any] = copy.deepcopy(DEFAULT_AUTO_TRADE)

# ==============================================================================
# ORTAM DEGISKENLERI (RENDER ENVIRONMENT VARIABLES)
# ==============================================================================
def _read_env_any(*keys: str, default: str = "") -> str:
    for k in keys:
        val = os.getenv(k, "").strip().strip('"').strip("'").strip()
        if val:
            return val
    return default


def _normalize_bot_token(raw: str) -> str:
    cleaned = raw.strip().strip('"').strip("'").strip()
    return re.sub(r"^bot(?=\d+:)", "", cleaned, flags=re.IGNORECASE)


BOT_TOKEN = _normalize_bot_token(
    _read_env_any("BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN", "TG_BOT_TOKEN")
)
CHAT_ID_RAW = _read_env_any("CHAT_ID", "TELEGRAM_CHAT_ID", "TG_CHAT_ID", "TELEGRAM_CHAT_IDS", default="1623907197")
INITIAL_CHAT_IDS = [c.strip() for c in re.split(r"[\s,;]+", CHAT_ID_RAW) if c.strip()]
CHAT_ID = INITIAL_CHAT_IDS[0] if INITIAL_CHAT_IDS else ""
PORT = int(os.getenv("PORT", "10000"))

BINANCE_API_KEY = _read_env_any("BINANCE_API_KEY")
BINANCE_API_SECRET = _read_env_any("BINANCE_API_SECRET")
BINANCE_KEYS_READY = bool(BINANCE_API_KEY and BINANCE_API_SECRET)
GEMINI_API_KEY = _read_env_any("GEMINI_API_KEY")

if os.getenv("AUTO_TRADE_ENABLED"):
    AUTO_TRADE["enabled"] = os.getenv("AUTO_TRADE_ENABLED", "").lower() in ("1", "true", "yes", "on")
if os.getenv("AUTO_TRADE_MODE"):
    AUTO_TRADE["mode"] = os.getenv("AUTO_TRADE_MODE", "PAPER").upper()
if AUTO_TRADE["mode"] in ("LIVE", "TESTNET") and not BINANCE_KEYS_READY:
    AUTO_TRADE["mode"] = "PAPER"

# Istege bagli canli web senkronizasyonu (orn. https://.../api/bot-config)
WEB_SYNC_URL = _read_env_any("WEB_SYNC_URL")

TR_TZ = timezone(timedelta(hours=3))
STATE_FILE = "/tmp/spot_bot_render_state.json"

BINANCE_MIRRORS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]

PATTERN_LABELS = {
    "DOUBLE_BOTTOM": "Double Bottom (İkili Dip)",
    "DOUBLE_TOP": "Double Top (İkili Tepe)",
    "HEAD_SHOULDERS": "Head & Shoulders (OBO)",
    "INV_HEAD_SHOULDERS": "Inverted H&S (TOBO)",
    "BULL_FLAG": "Bullish Flag (Boğa Bayrağı)",
    "BEAR_FLAG": "Bearish Flag (Ayı Bayrağı)",
    "BEAR_RECTANGLE": "Bearish Rectangle (Ayı Dikdörtgeni)",
}

# ==============================================================================
# CANLI DURUM DEGISKENLERI
# ==============================================================================
BOT_RUNNING = True
TICKER_MAP: Dict[str, Dict[str, Any]] = {}
PRICE_HIST: Dict[str, deque] = {}
ACTIVE_SPOT_SYMBOLS: Set[str] = set()
SPOT_FILTERS: Dict[str, Dict[str, Any]] = {}
FUTURES_FILTERS: Dict[str, Dict[str, Any]] = {}
SIGNALS: List[Dict[str, Any]] = []
TRADES: List[Dict[str, Any]] = []
PAPER_BALANCE_USDT = float(os.getenv("PAPER_START_USDT", "10000"))

LOCKS: Dict[str, Any] = {
    "pump": {},
    "pump15m": {},
    "patterns": {},
}
ATH_ATL_LEVELS: Dict[str, Dict[str, Any]] = {}
VERIFY_COOLDOWN: Dict[str, float] = {}
VERIFY_INFLIGHT: Set[str] = set()
ATH_ATL_COOLDOWN: Dict[str, float] = {}
ATH_ATL_INFLIGHT: Set[str] = set()
MA200_COOLDOWN: Dict[str, float] = {}
VOL_COOLDOWN: Dict[str, float] = {}
IND_COOLDOWN: Dict[str, float] = {}
MULTI_EX_HIST: Dict[str, Dict[str, deque]] = {}
MULTI_EX_COOLDOWN: Dict[str, float] = {}
AI_COOLDOWN: Dict[str, float] = {}
GEMINI_MODELS = ("gemini-3.1-flash-lite", "gemini-4-hydrogen")
GEMINI_MODEL_BLOCKED_UNTIL: Dict[str, float] = {}
BIG_PUMP_EXCHANGE_ORDER = ("BYBIT", "BINANCE", "BINGX", "MEXC", "OKX", "GATE", "KUCOIN", "BITGET")
EXCHANGE_SYMBOL_SETS: Dict[str, Set[str]] = {}
BIG_PUMP_HIST: Dict[str, deque] = {}
BIG_PUMP_ALERTS: Dict[str, List[float]] = {}
BIG_PUMP_COOLDOWN: Dict[str, float] = {}
BIG_PUMP_INFLIGHT: Set[str] = set()
TRADE_INFLIGHT: Set[str] = set()
SUBSCRIBED_CHATS: Set[str] = set(INITIAL_CHAT_IDS)

TG_BOT_USERNAME = ""
TG_OK = False
LAST_TG_ERROR = ""
TG_SENT_COUNT = 0

LAST_VICTORY_DATE_TR = ""
LAST_SUMMARY_DATE_TR = ""
LAST_HOURLY_KEY = ""
PREFERRED_MIRROR_IDX = 0
BINANCE_BAN_UNTIL = 0.0
WS_CONNECTED = False
WS_LAST_MSG = 0.0
ACTIVE_FEED_SOURCE = "Binance"
HTTP_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=24, thread_name_prefix="http")


def _log(msg: str) -> None:
    ts = datetime.now(TR_TZ).strftime("%H:%M:%S")
    print(f"[{ts} TR] {msg}", flush=True)


def fmt_price(p: float) -> str:
    if p <= 0:
        return "0"
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 10:
        return f"{p:.3f}".rstrip("0").rstrip(".")
    if p >= 1:
        return f"{p:.4f}".rstrip("0").rstrip(".")
    if p >= 0.01:
        return f"{p:.5f}".rstrip("0").rstrip(".")
    if p >= 0.0001:
        return f"{p:.6f}".rstrip("0").rstrip(".")
    return f"{p:.8f}".rstrip("0").rstrip(".")


def esc_html(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def apply_strategy_patch(raw: Any) -> None:
    if not isinstance(raw, dict):
        return
    bool_keys = [
        "pumpEnabled", "pump15mEnabled", "athEnabled", "atlEnabled",
        "ma200Enabled", "ma200LongEnabled", "ma200ShortEnabled",
        "volatilityEnabled", "volatilityMajorOnly", "volatilityLongEnabled", "volatilityShortEnabled",
        "patternsEnabled",
        "bollingerEnabled", "bollingerLongEnabled", "bollingerShortEnabled",
        "ichimokuEnabled", "ichimokuLongEnabled", "ichimokuShortEnabled",
        "supertrendEnabled", "supertrendLongEnabled", "supertrendShortEnabled",
        "aiMarketEnabled", "multiExchangeEnabled", "bigPumpEnabled",
        "hourlyGainersReportEnabled", "dailyWinnerReportEnabled", "top24SummaryReportEnabled",
    ]
    for bk in bool_keys:
        if isinstance(raw.get(bk), bool):
            STRATEGY_CONFIG[bk] = raw[bk]

    num_bounds = {
        "pumpThresholdPct": (0.5, 50.0),
        "pumpMinVolUsdt": (1000.0, 100_000_000.0),
        "pumpRepeatHours": (0.0, 72.0),
        "pump15mThresholdPct": (0.5, 50.0),
        "pump15mMinRvol": (0.5, 20.0),
        "athAtlLockPct": (0.05, 25.0),
        "athAtlCooldownMin": (0.0, 1440.0),
        "volatilityAtrMult": (1.1, 10.0),
        "volatilityMinMovePct": (0.3, 30.0),
        "patternMinHeightPct": (0.4, 25.0),
        "bollingerStdDev": (1.0, 4.0),
        "supertrendMultiplier": (1.0, 7.0),
        "multiExchangeThresholdPct": (0.5, 30.0),
        "bigPumpThresholdPct": (1.0, 50.0),
        "bigPumpMinVolUsdt": (1000.0, 100_000_000.0),
    }
    for nk, (lo, hi) in num_bounds.items():
        if nk in raw:
            try:
                v = float(raw[nk])
                STRATEGY_CONFIG[nk] = max(lo, min(hi, v))
            except (TypeError, ValueError):
                pass

    for ik, lo, hi in [
        ("ma200Period", 20, 400),
        ("bollingerPeriod", 10, 50),
        ("supertrendAtrPeriod", 5, 30),
        ("aiMarketIntervalMin", 1, 60),
        ("aiMarketMinConfidence", 40, 99),
        ("multiExchangeMinExchanges", 2, 6),
        ("multiExchangeWindowMin", 1, 60),
        ("bigPumpWindowMin", 1, 120),
        ("bigPumpCooldownMin", 1, 120),
    ]:
        if ik in raw:
            try:
                STRATEGY_CONFIG[ik] = max(lo, min(hi, int(raw[ik])))
            except (TypeError, ValueError):
                pass

    if raw.get("ma200Timeframe") in ("1h", "4h", "1d"):
        STRATEGY_CONFIG["ma200Timeframe"] = raw["ma200Timeframe"]

    for dkey in ("aiMarketDirection", "multiExchangeDirection", "bigPumpDirection"):
        dv = str(raw.get(dkey) or "").upper()
        if dv in ("LONG", "SHORT", "BOTH"):
            STRATEGY_CONFIG[dkey] = dv

    if isinstance(raw.get("bigPumpExchanges"), list):
        chosen_ex = [str(ex).strip().upper() for ex in raw["bigPumpExchanges"]]
        STRATEGY_CONFIG["bigPumpExchanges"] = [ex for ex in BIG_PUMP_EXCHANGE_ORDER if ex in chosen_ex]

    if isinstance(raw.get("athAtlTimeframes"), list):
        valid = [tf for tf in raw["athAtlTimeframes"] if tf in ("1h", "4h", "1d", "1w", "1M")]
        STRATEGY_CONFIG["athAtlTimeframes"] = list(dict.fromkeys(valid))

    if isinstance(raw.get("volatilityTimeframes"), list):
        valid_v = [tf for tf in raw["volatilityTimeframes"] if tf in ("15m", "1h")]
        STRATEGY_CONFIG["volatilityTimeframes"] = list(dict.fromkeys(valid_v))

    if isinstance(raw.get("volatilityMajorCoins"), list):
        cleaned = [str(c).strip().upper().replace("USDT", "") for c in raw["volatilityMajorCoins"] if str(c).strip()]
        if cleaned:
            STRATEGY_CONFIG["volatilityMajorCoins"] = list(dict.fromkeys(cleaned))

    if isinstance(raw.get("patternTimeframes"), list):
        valid_p = [tf for tf in raw["patternTimeframes"] if tf in ("15m", "1h", "4h", "1d")]
        STRATEGY_CONFIG["patternTimeframes"] = list(dict.fromkeys(valid_p))

    for tf_field in ("bollingerTimeframes", "ichimokuTimeframes", "supertrendTimeframes"):
        if isinstance(raw.get(tf_field), list):
            valid_i = [tf for tf in raw[tf_field] if tf in ("15m", "1h", "4h", "1d", "1w", "1M")]
            STRATEGY_CONFIG[tf_field] = list(dict.fromkeys(valid_i))

    if isinstance(raw.get("enabledPatterns"), dict):
        for pk in PATTERN_LABELS:
            if isinstance(raw["enabledPatterns"].get(pk), bool):
                STRATEGY_CONFIG["enabledPatterns"][pk] = raw["enabledPatterns"][pk]

    if isinstance(raw.get("autoTradeEngines"), dict):
        for ek in ("PUMP", "PUMP15M", "ATL", "ATH", "MA200_4H", "VOLATILITY", "PATTERN", "BOLLINGER", "ICHIMOKU", "SUPERTREND", "AI_MARKET", "MULTI_EXCHANGE", "BIG_PUMP"):
            if isinstance(raw["autoTradeEngines"].get(ek), bool):
                STRATEGY_CONFIG["autoTradeEngines"][ek] = raw["autoTradeEngines"][ek]


def apply_autotrade_patch(raw: Any) -> None:
    if not isinstance(raw, dict):
        return
    if isinstance(raw.get("enabled"), bool):
        AUTO_TRADE["enabled"] = raw["enabled"]
    if raw.get("mode") in ("PAPER", "TESTNET", "LIVE"):
        if raw["mode"] == "PAPER" or BINANCE_KEYS_READY:
            AUTO_TRADE["mode"] = raw["mode"]
    if str(raw.get("directionMode") or "").upper() in ("BOTH", "LONG", "SHORT"):
        AUTO_TRADE["directionMode"] = str(raw["directionMode"]).upper()
    for key, lo, hi, as_int in [
        ("leverage", 1, 125, True),
        ("tradeUsdt", 1, 1_000_000, False),
        ("tpPct", 0.1, 1000, False),
        ("slPct", 0.1, 99, False),
        ("maxOpenTrades", 0, 9999, True),
        ("rsiMin", 0, 95, False),
    ]:
        if key in raw:
            try:
                v = float(raw[key])
                v = max(lo, min(hi, v))
                AUTO_TRADE[key] = int(round(v)) if as_int else v
            except (TypeError, ValueError):
                pass
    if isinstance(raw.get("notifyPaperTelegram"), bool):
        AUTO_TRADE["notifyPaperTelegram"] = raw["notifyPaperTelegram"]


def save_state() -> None:
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({
                "strategyConfig": STRATEGY_CONFIG,
                "autoTrade": AUTO_TRADE,
                "paperBalanceUsdt": PAPER_BALANCE_USDT,
                "signals": SIGNALS[:150],
                "trades": TRADES[:150],
                "locks": LOCKS,
                "athAtlLevels": ATH_ATL_LEVELS,
                "chats": list(SUBSCRIBED_CHATS),
                "lastVictory": LAST_VICTORY_DATE_TR,
                "lastSummary": LAST_SUMMARY_DATE_TR,
                "lastHourly": LAST_HOURLY_KEY,
            }, f)
        os.replace(tmp, STATE_FILE)
    except Exception:
        pass


def load_state() -> None:
    global PAPER_BALANCE_USDT, SIGNALS, TRADES, ATH_ATL_LEVELS
    global LAST_VICTORY_DATE_TR, LAST_SUMMARY_DATE_TR, LAST_HOURLY_KEY
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw.get("paperBalanceUsdt"), (int, float)):
            PAPER_BALANCE_USDT = max(0.0, float(raw["paperBalanceUsdt"]))
        if isinstance(raw.get("signals"), list):
            SIGNALS = raw["signals"][:150]
        if isinstance(raw.get("trades"), list):
            TRADES = raw["trades"][:150]
        if isinstance(raw.get("locks"), dict):
            for k in ("pump", "pump15m", "patterns"):
                if isinstance(raw["locks"].get(k), dict):
                    LOCKS[k] = raw["locks"][k]
        if isinstance(raw.get("athAtlLevels"), dict):
            ATH_ATL_LEVELS = raw["athAtlLevels"]
        for cid in raw.get("chats", []):
            if str(cid).strip():
                SUBSCRIBED_CHATS.add(str(cid).strip())
        LAST_VICTORY_DATE_TR = str(raw.get("lastVictory", ""))
        LAST_SUMMARY_DATE_TR = str(raw.get("lastSummary", ""))
        LAST_HOURLY_KEY = str(raw.get("lastHourly", ""))
    except Exception:
        pass


# ==============================================================================
# TEKNIK INDIKATORLER
# ==============================================================================
def compute_sma(closes: List[float], period: int = 200) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(closes)
    if len(closes) < period:
        return out
    s = sum(closes[:period])
    out[period - 1] = s / period
    for i in range(period, len(closes)):
        s += closes[i] - closes[i - period]
        out[i] = s / period
    return out


def compute_ema(closes: List[float], period: int) -> List[float]:
    if not closes:
        return []
    k = 2.0 / (period + 1)
    out = [float(closes[0])]
    for i in range(1, len(closes)):
        out.append(closes[i] * k + out[i - 1] * (1.0 - k))
    return out


def compute_rsi(closes: List[float], period: int = 14) -> List[float]:
    n = len(closes)
    out = [50.0] * n
    if n < period + 2:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d > 0:
            gains += d
        else:
            losses += -d
    avg_g = gains / period
    avg_l = losses / period
    for i in range(period, n):
        if i > period:
            d = closes[i] - closes[i - 1]
            avg_g = (avg_g * (period - 1) + max(d, 0.0)) / period
            avg_l = (avg_l * (period - 1) + max(-d, 0.0)) / period
        if avg_l == 0:
            out[i] = 100.0 if avg_g > 0 else 50.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    return out


# ==============================================================================
# SAF PYTHON PNG OLUSTURUCU (PIL GEREKTIRMEZ)
# ==============================================================================
FONT_5X7 = {
    "0": [0x3E, 0x51, 0x49, 0x45, 0x3E], "1": [0x00, 0x42, 0x7F, 0x40, 0x00],
    "2": [0x42, 0x61, 0x51, 0x49, 0x46], "3": [0x21, 0x41, 0x45, 0x4B, 0x31],
    "4": [0x18, 0x14, 0x12, 0x7F, 0x10], "5": [0x27, 0x45, 0x45, 0x45, 0x39],
    "6": [0x3C, 0x4A, 0x49, 0x49, 0x30], "7": [0x01, 0x71, 0x09, 0x05, 0x03],
    "8": [0x36, 0x49, 0x49, 0x49, 0x36], "9": [0x06, 0x49, 0x49, 0x29, 0x1E],
    "A": [0x7E, 0x11, 0x11, 0x11, 0x7E], "B": [0x7F, 0x49, 0x49, 0x49, 0x36],
    "C": [0x3E, 0x41, 0x41, 0x41, 0x22], "D": [0x7F, 0x41, 0x41, 0x22, 0x1C],
    "E": [0x7F, 0x49, 0x49, 0x49, 0x41], "F": [0x7F, 0x09, 0x09, 0x09, 0x01],
    "G": [0x3E, 0x41, 0x49, 0x49, 0x7A], "H": [0x7F, 0x08, 0x08, 0x08, 0x7F],
    "I": [0x00, 0x41, 0x7F, 0x41, 0x00], "J": [0x20, 0x40, 0x41, 0x3F, 0x01],
    "K": [0x7F, 0x08, 0x14, 0x22, 0x41], "L": [0x7F, 0x40, 0x40, 0x40, 0x40],
    "M": [0x7F, 0x02, 0x0C, 0x02, 0x7F], "N": [0x7F, 0x04, 0x08, 0x10, 0x7F],
    "O": [0x3E, 0x41, 0x41, 0x41, 0x3E], "P": [0x7F, 0x09, 0x09, 0x09, 0x06],
    "Q": [0x3E, 0x41, 0x51, 0x21, 0x5E], "R": [0x7F, 0x09, 0x19, 0x29, 0x46],
    "S": [0x46, 0x49, 0x49, 0x49, 0x31], "T": [0x01, 0x01, 0x7F, 0x01, 0x01],
    "U": [0x3F, 0x40, 0x40, 0x40, 0x3F], "V": [0x1F, 0x20, 0x40, 0x20, 0x1F],
    "W": [0x3F, 0x40, 0x38, 0x40, 0x3F], "X": [0x63, 0x14, 0x08, 0x14, 0x63],
    "Y": [0x07, 0x08, 0x70, 0x08, 0x07], "Z": [0x61, 0x51, 0x49, 0x45, 0x43],
    ".": [0x00, 0x60, 0x60, 0x00, 0x00], ",": [0x00, 0x50, 0x30, 0x00, 0x00],
    ":": [0x00, 0x36, 0x36, 0x00, 0x00], "-": [0x08, 0x08, 0x08, 0x08, 0x08],
    "+": [0x08, 0x08, 0x3E, 0x08, 0x08], "/": [0x20, 0x10, 0x08, 0x04, 0x02],
    "%": [0x23, 0x13, 0x08, 0x64, 0x62], "$": [0x24, 0x2A, 0x7F, 0x2A, 0x12],
    "|": [0x00, 0x00, 0x7F, 0x00, 0x00], "*": [0x14, 0x08, 0x3E, 0x08, 0x14],
    "[": [0x00, 0x7F, 0x41, 0x41, 0x00], "]": [0x00, 0x41, 0x41, 0x7F, 0x00],
    "(": [0x00, 0x1C, 0x22, 0x41, 0x00], ")": [0x00, 0x41, 0x22, 0x1C, 0x00],
    " ": [0x00, 0x00, 0x00, 0x00, 0x00],
}


def encode_png(w: int, h: int, buf: bytearray) -> bytes:
    stride = w * 3
    raw = bytearray((stride + 1) * h)
    for y in range(h):
        raw[y * (stride + 1)] = 0
        raw[y * (stride + 1) + 1 : y * (stride + 1) + 1 + stride] = buf[y * stride : (y + 1) * stride]
    comp = zlib.compress(bytes(raw), 6)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", comp) + chunk(b"IEND", b"")


def _canvas(w: int, h: int, bg: Tuple[int, int, int]):
    buf = bytearray(bytes(bg) * (w * h))

    def set_px(x: int, y: int, col: Tuple[int, int, int]):
        if 0 <= x < w and 0 <= y < h:
            i = (y * w + x) * 3
            buf[i], buf[i + 1], buf[i + 2] = col

    def fill_rect(x0: int, y0: int, x1: int, y1: int, col: Tuple[int, int, int]):
        a0, a1 = max(0, min(x0, x1)), min(w - 1, max(x0, x1))
        b0, b1 = max(0, min(y0, y1)), min(h - 1, max(y0, y1))
        for y in range(b0, b1 + 1):
            for x in range(a0, a1 + 1):
                set_px(x, y, col)

    def draw_border(x0: int, y0: int, x1: int, y1: int, fill: Tuple[int, int, int], brd: Tuple[int, int, int]):
        fill_rect(x0, y0, x1, y1, fill)
        fill_rect(x0, y0, x1, y0 + 1, brd)
        fill_rect(x0, y1 - 1, x1, y1, brd)
        fill_rect(x0, y0, x0 + 1, y1, brd)
        fill_rect(x1 - 1, y0, x1, y1, brd)

    def fill_circle(cx: int, cy: int, r: int, col: Tuple[int, int, int]):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dy * dy <= r * r:
                    set_px(cx + dx, cy + dy, col)

    def draw_line(x0: int, y0: int, x1: int, y1: int, col: Tuple[int, int, int], dashed: bool = False):
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        step_n = 0
        cx, cy = x0, y0
        while True:
            if not dashed or (step_n % 8) < 5:
                set_px(cx, cy, col)
            if cx == x1 and cy == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                cx += sx
            if e2 <= dx:
                err += dx
                cy += sy
            step_n += 1

    def draw_text(x: int, y: int, text: str, col: Tuple[int, int, int], scale: int = 1):
        cx = x
        for ch in text.upper():
            glyph = FONT_5X7.get(ch, FONT_5X7[" "])
            for gx in range(5):
                bits = glyph[gx]
                for gy in range(7):
                    if bits & (1 << gy):
                        if scale == 1:
                            set_px(cx + gx, y + gy, col)
                        else:
                            fill_rect(cx + gx * scale, y + gy * scale, cx + (gx + 1) * scale - 1, y + (gy + 1) * scale - 1, col)
            cx += 6 * scale

    return buf, fill_rect, draw_border, fill_circle, draw_line, draw_text


def render_signal_candle_png(
    title: str,
    bars: List[Dict[str, float]],
    live_price: float,
    support: Optional[float] = None,
    target: Optional[float] = None,
    ma_series: Optional[List[Optional[float]]] = None,
    is_short: bool = False,
) -> bytes:
    sl = bars[-50:]
    if not sl:
        return b""
    w, h = 860, 440
    buf, fill_rect, _, _, draw_line, draw_text = _canvas(w, h, (11, 16, 27))
    pad_l, pad_r, pad_t, pad_b, vol_h, gap = 18, 118, 44, 22, 58, 12
    plot_w = w - pad_l - pad_r
    price_h = h - pad_t - pad_b - vol_h - gap

    prices = [b["high"] for b in sl] + [b["low"] for b in sl]
    if live_price > 0:
        prices.append(live_price)
    if support and support > 0:
        prices.append(support)
    if target and target > 0:
        prices.append(target)
    ma_sl = (ma_series[-len(sl):]) if ma_series else []
    for m in ma_sl:
        if m is not None and m > 0:
            prices.append(m)

    lo_p = min(prices) * 0.993
    hi_p = max(prices) * 1.007
    rng_p = max(hi_p - lo_p, lo_p * 0.004)
    max_vol = max((b["volume"] for b in sl), default=1.0) or 1.0

    def y_of(p: float) -> int:
        return round(pad_t + ((hi_p - p) / rng_p) * price_h)

    draw_text(pad_l, 14, title[:62], (248, 250, 252), 2)
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        gy = round(pad_t + price_h * frac)
        draw_line(pad_l, gy, pad_l + plot_w, gy, (30, 41, 59), True)
        draw_text(pad_l + plot_w + 8, gy - 3, fmt_price(hi_p - rng_p * frac), (148, 163, 184), 1)

    if support and support > 0:
        sy = y_of(support)
        draw_line(pad_l, sy, pad_l + plot_w, sy, (245, 158, 11), True)
        draw_text(pad_l + plot_w + 8, sy - 3, f"BYN {fmt_price(support)}", (245, 158, 11), 1)

    if target and target > 0:
        ty = y_of(target)
        tc = (244, 63, 94) if is_short else (52, 211, 153)
        draw_line(pad_l, ty, pad_l + plot_w, ty, tc, True)
        draw_text(pad_l + plot_w + 8, ty - 3, f"HDF {fmt_price(target)}", tc, 1)

    step = plot_w / len(sl)
    bw = max(3, int(step * 0.62))
    vol_top = pad_t + price_h + gap
    for idx, b in enumerate(sl):
        cx = round(pad_l + (idx + 0.5) * step)
        col = (16, 185, 129) if b["close"] >= b["open"] else (244, 63, 94)
        yo, yc, yh, yl = y_of(b["open"]), y_of(b["close"]), y_of(b["high"]), y_of(b["low"])
        draw_line(cx, yh, cx, yl, col)
        fill_rect(cx - bw // 2, min(yo, yc), cx + bw // 2, max(min(yo, yc) + 1, max(yo, yc)), col)
        vh = max(1, round((b["volume"] / max_vol) * vol_h))
        fill_rect(cx - bw // 2, vol_top + vol_h - vh, cx + bw // 2, vol_top + vol_h, (col[0] // 2, col[1] // 2, col[2] // 2))

    if len(ma_sl) == len(sl):
        for i in range(1, len(ma_sl)):
            m0, m1 = ma_sl[i - 1], ma_sl[i]
            if m0 is not None and m1 is not None:
                draw_line(round(pad_l + (i - 0.5) * step), y_of(m0), round(pad_l + (i + 0.5) * step), y_of(m1), (16, 185, 129))

    if live_price > 0:
        ly = y_of(live_price)
        draw_line(pad_l, ly, pad_l + plot_w, ly, (56, 189, 248), True)
        fill_rect(pad_l + plot_w + 4, ly - 7, w - 4, ly + 7, (2, 132, 199))
        draw_text(pad_l + plot_w + 8, ly - 3, fmt_price(live_price), (248, 250, 252), 1)
    return encode_png(w, h, buf)


def render_big_pump_png(symbol: str, bars: List[Dict[str, float]], live_price: float) -> bytes:
    sl = bars[-95:]
    if not sl:
        return b""
    w, h = 860, 420
    buf, fill_rect, draw_border, _, draw_line, draw_text = _canvas(w, h, (232, 234, 238))
    pad_l, pad_r, pad_t, pad_b = 16, 96, 34, 34
    plot_w = w - pad_l - pad_r
    plot_h = h - pad_t - pad_b

    title_txt = symbol.upper()
    title_x = max(pad_l, round(pad_l + plot_w / 2 - (len(title_txt) * 12) / 2))
    draw_text(title_x, 11, title_txt, (24, 28, 36), 2)
    draw_border(pad_l, pad_t, pad_l + plot_w, pad_t + plot_h, (226, 229, 234), (30, 36, 46))

    prices = [b["high"] for b in sl if b["high"] > 0] + [b["low"] for b in sl if b["low"] > 0]
    if live_price > 0:
        prices.append(live_price)
    if not prices:
        return b""
    raw_min, raw_max = min(prices), max(prices)
    span = max(raw_max - raw_min, raw_min * 0.002)
    lo_p = raw_min - span * 0.06
    hi_p = raw_max + span * 0.06
    rng_p = max(hi_p - lo_p, 1e-12)

    def y_of(p: float) -> int:
        return max(pad_t + 2, min(pad_t + plot_h - 2, round(pad_t + ((hi_p - p) / rng_p) * plot_h)))

    grid_col, lbl_col = (194, 199, 207), (35, 42, 54)
    for i in range(1, 7):
        frac = i / 7.0
        gy = round(pad_t + plot_h * frac)
        draw_line(pad_l + 1, gy, pad_l + plot_w - 1, gy, grid_col, True)
        draw_text(pad_l + plot_w + 6, gy - 3, fmt_price(hi_p - rng_p * frac), lbl_col, 1)

    step = plot_w / len(sl)
    bw = max(3, int(step * 0.65))
    dark = (17, 24, 39)
    for idx, b in enumerate(sl):
        cx = round(pad_l + (idx + 0.5) * step)
        if idx > 0 and idx % 15 == 0:
            draw_line(cx, pad_t + 1, cx, pad_t + plot_h - 1, grid_col, True)
            tr_dt = datetime.fromtimestamp(b["openTime"], tz=TR_TZ)
            draw_text(cx - 15, pad_t + plot_h + 8, tr_dt.strftime("%H:%M"), lbl_col, 1)
        yo, yc, yh, yl = y_of(b["open"]), y_of(b["close"]), y_of(b["high"]), y_of(b["low"])
        draw_line(cx, yh, cx, yl, dark)
        top_y = min(yo, yc)
        bot_y = max(top_y + 1, max(yo, yc))
        x0, x1 = cx - bw // 2, cx + bw // 2
        if b["close"] >= b["open"]:
            draw_border(x0, top_y, x1, bot_y, (255, 255, 255), dark)
        else:
            fill_rect(x0, top_y, x1, bot_y, dark)
    return encode_png(w, h, buf)


def render_hourly_gainers_png(gainers: List[Dict[str, Any]], tr_clock: str) -> bytes:
    rows = gainers[:8]
    if not rows:
        return b""
    w = 860
    h = 175 + len(rows) * 76 + 38
    buf, fill_rect, draw_border, fill_circle, _, draw_text = _canvas(w, h, (24, 26, 32))
    draw_border(18, 16, w - 18, h - 16, (24, 26, 32), (43, 49, 57))
    draw_text(42, 36, "GAINERS  |  24 SAATIN EN YUKSEKLERI", (248, 250, 252), 2)
    draw_text(42, 62, f"CRYPTO  |  SPOT USDT  |  SAATLIK RADAR ({tr_clock})", (132, 142, 156), 1)
    fill_rect(42, 82, 128, 85, (240, 185, 11))
    draw_text(42, 108, "NAME", (132, 142, 156), 1)
    draw_text(460, 108, "LAST PRICE", (132, 142, 156), 1)
    draw_text(w - 168, 108, "24H CHG%", (132, 142, 156), 1)
    fill_rect(36, 126, w - 36, 127, (43, 49, 57))

    bcols = [(240, 185, 11), (99, 102, 241), (6, 182, 212), (236, 72, 153), (46, 189, 133), (139, 92, 246)]
    for idx, t in enumerate(rows):
        ry0 = 142 + idx * 76
        fill_circle(66, ry0 + 30, 18, bcols[idx % len(bcols)])
        draw_text(55, ry0 + 23, t["base"][:2], (255, 255, 255), 2)
        draw_text(102, ry0 + 14, t["base"], (248, 250, 252), 2)
        vol_m = f"${t['volume24h'] / 1e6:.1f}M" if t["volume24h"] >= 1e6 else f"${t['volume24h'] / 1000:.0f}K"
        draw_text(102, ry0 + 38, f"{t['symbol']} | HACIM {vol_m}", (132, 142, 156), 1)
        p_txt = f"${fmt_price(t['price'])}"
        draw_text(560 - len(p_txt) * 12, ry0 + 22, p_txt, (248, 250, 252), 2)
        fill_rect(w - 188, ry0 + 10, w - 40, ry0 + 50, (46, 189, 133))
        chg_txt = f"+%{t['change24h']:.2f}"
        draw_text(w - 114 - (len(chg_txt) * 12) // 2, ry0 + 23, chg_txt, (255, 255, 255), 2)
        if idx < len(rows) - 1:
            fill_rect(42, ry0 + 68, w - 42, ry0 + 69, (35, 40, 48))

    draw_text(42, h - 34, "SPOT AKIS TERMINALI  |  BINANCE CANLI 24S EN YUKSEK YUZDELER", (132, 142, 156), 1)
    return encode_png(w, h, buf)


def render_top24_png(ranked: List[Dict[str, Any]], total_count: int, hit2_pct: int) -> bytes:
    rows = ranked[:5]
    if not rows:
        return b""
    w, h = 860, 700
    buf, fill_rect, draw_border, fill_circle, _, draw_text = _canvas(w, h, (11, 15, 35))
    draw_border(24, 20, w - 24, h - 20, (16, 21, 45), (45, 54, 99))
    fill_rect(54, 46, 98, 90, (99, 102, 241))
    draw_text(66, 61, "SF", (255, 255, 255), 2)
    draw_text(114, 48, "SPOT AKIS TERMINALI", (248, 250, 252), 2)
    draw_text(114, 72, "CANLI TARAYICI · FUTURES 3X", (148, 163, 184), 1)
    draw_text(54, 120, "24 SAATIN EN IYI HAREKETLERI", (255, 255, 255), 2)

    bcols = [(236, 72, 153), (249, 115, 22), (6, 182, 212), (139, 92, 246), (16, 185, 129)]
    for idx, s in enumerate(rows):
        ry0 = 180 + idx * 80
        ry1 = ry0 + 68
        if idx == 0:
            draw_border(48, ry0, w - 48, ry1, (23, 29, 61), (99, 102, 241))
        else:
            fill_rect(48, ry0, w - 48, ry1, (14, 19, 41))
        draw_text(66, ry0 + 25, str(idx + 1), (251, 191, 36) if idx == 0 else (148, 163, 184), 2)
        fill_circle(122, ry0 + 34, 18, bcols[idx % len(bcols)])
        draw_text(111, ry0 + 27, s["base"][:2], (255, 255, 255), 2)
        draw_text(158, ry0 + 16, s["base"], (255, 255, 255), 2)
        pct_txt = f"+{s['peakPct']:.0f}%" if s["peakPct"] >= 10 else f"+{s['peakPct']:.1f}%"
        sc = 3 if idx == 0 else 2
        draw_text(w - 68 - len(pct_txt) * 6 * sc, ry0 + 22, pct_txt, (52, 211, 153), sc)

    draw_text(54, 658, f"{total_count} SINYAL TAKIPTE | %{hit2_pct} +2% ULASTI", (148, 163, 184), 1)
    return encode_png(w, h, buf)


# ==============================================================================
# BINANCE & TELEGRAM AG ILETISIMI (SAF PYTHON STANDART KUTUPHANE - SIFIR EK PAKET)
# ==============================================================================
def _http_request_sync(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[bytes] = None,
    timeout_s: float = 12.0,
) -> Tuple[int, bytes]:
    req_headers = {"User-Agent": "SpotAkisBot/1.0"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read()
        except Exception:
            err_body = str(e).encode("utf-8", errors="ignore")
        return int(e.code), err_body


async def http_request(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[bytes] = None,
    timeout_s: float = 12.0,
) -> Tuple[int, bytes]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(HTTP_EXECUTOR, lambda: _http_request_sync(method, url, headers, body, timeout_s))


def _safe_json_loads(raw: bytes) -> Any:
    if not raw:
        return {}
    txt = raw.decode("utf-8", errors="replace").strip()
    if not txt:
        return {}
    try:
        return json.loads(txt)
    except Exception:
        return {"ok": False, "description": txt[:240]}


async def http_get_json(url: str, timeout_s: float = 10.0) -> Tuple[int, Any]:
    status, raw = await http_request("GET", url, timeout_s=timeout_s)
    return status, _safe_json_loads(raw)


async def http_post_json(url: str, payload: Dict[str, Any], timeout_s: float = 12.0) -> Tuple[int, Any]:
    raw_body = json.dumps(payload).encode("utf-8")
    status, raw = await http_request(
        "POST",
        url,
        headers={"Content-Type": "application/json"},
        body=raw_body,
        timeout_s=timeout_s,
    )
    return status, _safe_json_loads(raw)


async def fetch_binance_json(path: str, timeout_s: float = 9.0) -> Any:
    global PREFERRED_MIRROR_IDX, BINANCE_BAN_UNTIL
    now_s = time.time()
    if now_s < BINANCE_BAN_UNTIL:
        rem = int(BINANCE_BAN_UNTIL - now_s)
        raise RuntimeError(f"Binance cooldown aktif ({rem}sn sonra tekrar denenir)")
    n = len(BINANCE_MIRRORS)
    last_err: Any = None
    for off in range(n):
        idx = (PREFERRED_MIRROR_IDX + off) % n
        url = f"{BINANCE_MIRRORS[idx]}{path}"
        try:
            status, data = await http_get_json(url, timeout_s=timeout_s)
            if status == 200 and (isinstance(data, list) or (isinstance(data, dict) and "_raw" not in data and "code" not in data)):
                PREFERRED_MIRROR_IDX = idx
                return data
            last_err = f"HTTP {status}"
            if status == 418:
                BINANCE_BAN_UNTIL = time.time() + 180.0
                _log("[Binance 418] IP hiz limiti algilandi -> 180sn Binance REST bekleme, MEXC/Bybit/WS yedegine geciliyor.")
                break
            if status == 429:
                BINANCE_BAN_UNTIL = time.time() + 45.0
                break
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Binance erisilemedi: {last_err}")


async def fetch_spot_klines(symbol: str, interval: str, limit: int = 120) -> List[Dict[str, float]]:
    if time.time() >= BINANCE_BAN_UNTIL:
        try:
            raw = await fetch_binance_json(f"/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}")
            if isinstance(raw, list) and raw:
                return [
                    {
                        "openTime": int(k[0]) // 1000,
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                        "quoteVol": float(k[7]),
                    }
                    for k in raw
                ]
        except Exception:
            pass

    # Fallback 1: MEXC Spot Klines
    try:
        mexc_iv = "60m" if interval == "1h" else ("1W" if interval == "1w" else interval)
        st, raw = await http_get_json(
            f"https://api.mexc.com/api/v3/klines?symbol={urllib.parse.quote(symbol)}&interval={urllib.parse.quote(mexc_iv)}&limit={limit}",
            timeout_s=8.0,
        )
        if st == 200 and isinstance(raw, list) and raw:
            return [
                {
                    "openTime": int(k[0]) // 1000,
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                    "quoteVol": float(k[7]) if len(k) > 7 else float(k[5]) * float(k[4]),
                }
                for k in raw
            ]
    except Exception:
        pass

    # Fallback 2: Bybit v5 Spot Klines
    try:
        bybit_map = {"1m": "1", "15m": "15", "1h": "60", "4h": "240", "1d": "D", "1w": "W", "1M": "M"}
        b_iv = bybit_map.get(interval, interval)
        st, res = await http_get_json(
            f"https://api.bybit.com/v5/market/kline?category=spot&symbol={urllib.parse.quote(symbol)}&interval={urllib.parse.quote(b_iv)}&limit={limit}",
            timeout_s=8.0,
        )
        lst = ((res.get("result") or {}).get("list")) if isinstance(res, dict) else None
        if st == 200 and isinstance(lst, list) and lst:
            return [
                {
                    "openTime": int(k[0]) // 1000,
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                    "quoteVol": float(k[6]) if len(k) > 6 else float(k[5]) * float(k[4]),
                }
                for k in reversed(lst)
            ]
    except Exception:
        pass

    # Fallback 3: Gate.io Spot Candlesticks
    try:
        if symbol.endswith("USDT"):
            pair = f"{symbol[:-4]}_USDT"
            g_iv = "30d" if interval == "1M" else ("7d" if interval == "1w" else interval)
            st, raw = await http_get_json(
                f"https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair={urllib.parse.quote(pair)}&interval={urllib.parse.quote(g_iv)}&limit={limit}",
                timeout_s=8.0,
            )
            if st == 200 and isinstance(raw, list) and raw:
                return [
                    {
                        "openTime": int(float(k[0])),
                        "open": float(k[5]),
                        "high": float(k[3]),
                        "low": float(k[4]),
                        "close": float(k[2]),
                        "volume": float(k[6]) if len(k) > 6 else float(k[1]),
                        "quoteVol": float(k[1]),
                    }
                    for k in raw
                ]
    except Exception:
        pass
    return []


async def fetch_fallback_tickers() -> Tuple[str, List[Dict[str, Any]]]:
    # 1) MEXC 24hr
    try:
        st, raw = await http_get_json("https://api.mexc.com/api/v3/ticker/24hr", timeout_s=9.0)
        if st == 200 and isinstance(raw, list) and len(raw) > 50:
            out: List[Dict[str, Any]] = []
            for x in raw:
                sym = str(x.get("symbol") or "")
                p = float(x.get("lastPrice") or 0)
                if not sym.endswith("USDT") or p <= 0:
                    continue
                out.append({
                    "symbol": sym,
                    "lastPrice": p,
                    "bidPrice": p,
                    "priceChangePercent": float(x.get("priceChangePercent") or 0),
                    "quoteVolume": float(x.get("quoteVolume") or 0),
                    "highPrice": float(x.get("highPrice") or p),
                    "lowPrice": float(x.get("lowPrice") or p),
                })
            if len(out) > 40:
                return "MEXC", out
    except Exception:
        pass

    # 2) Bybit v5 Spot
    try:
        st, res = await http_get_json("https://api.bybit.com/v5/market/tickers?category=spot", timeout_s=9.0)
        lst = ((res.get("result") or {}).get("list")) if isinstance(res, dict) else None
        if st == 200 and isinstance(lst, list) and len(lst) > 50:
            out = []
            for x in lst:
                sym = str(x.get("symbol") or "")
                p = float(x.get("lastPrice") or 0)
                if not sym.endswith("USDT") or p <= 0:
                    continue
                out.append({
                    "symbol": sym,
                    "lastPrice": p,
                    "bidPrice": p,
                    "priceChangePercent": float(x.get("price24hPcnt") or 0) * 100.0,
                    "quoteVolume": float(x.get("turnover24h") or 0),
                    "highPrice": float(x.get("highPrice24h") or p),
                    "lowPrice": float(x.get("lowPrice24h") or p),
                })
            if len(out) > 40:
                return "Bybit", out
    except Exception:
        pass

    # 3) Gate.io Spot
    try:
        st, raw = await http_get_json("https://api.gateio.ws/api/v4/spot/tickers", timeout_s=9.0)
        if st == 200 and isinstance(raw, list) and len(raw) > 50:
            out = []
            for x in raw:
                pair = str(x.get("currency_pair") or "")
                if not pair.endswith("_USDT"):
                    continue
                sym = pair.replace("_USDT", "USDT")
                p = float(x.get("last") or 0)
                if p <= 0:
                    continue
                out.append({
                    "symbol": sym,
                    "lastPrice": p,
                    "bidPrice": p,
                    "priceChangePercent": float(x.get("change_percentage") or 0),
                    "quoteVolume": float(x.get("quote_volume") or 0),
                    "highPrice": float(x.get("high_24h") or p),
                    "lowPrice": float(x.get("low_24h") or p),
                })
            if len(out) > 40:
                return "Gate", out
    except Exception:
        pass
    return "", []


def _strip_html_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


async def tg_send_text(html_text: str, explicit_cid: Optional[str] = None) -> bool:
    global LAST_TG_ERROR, TG_OK, TG_SENT_COUNT
    if not BOT_TOKEN:
        LAST_TG_ERROR = "BOT_TOKEN tanımlı değil"
        return False
    targets = [explicit_cid] if explicit_cid else list(SUBSCRIBED_CHATS)
    if not targets:
        LAST_TG_ERROR = "CHAT_ID / hedef sohbet tanımlı değil"
        _log(f"[TG UYARI] {LAST_TG_ERROR}")
        return False

    any_ok = False
    for cid in targets:
        try:
            payload: Dict[str, Any] = {
                "chat_id": cid,
                "text": html_text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            status, js = await http_post_json(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                payload,
                timeout_s=15.0,
            )
            if status == 429 and isinstance(js, dict):
                wait_s = min(10.0, float((js.get("parameters") or {}).get("retry_after") or 2.0))
                await asyncio.sleep(wait_s + 0.3)
                status, js = await http_post_json(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    payload,
                    timeout_s=15.0,
                )
            desc = str(js.get("description") or "") if isinstance(js, dict) else ""
            if (status >= 400 or not isinstance(js, dict) or not js.get("ok")) and "parse entities" in desc.lower():
                status, js = await http_post_json(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    {"chat_id": cid, "text": _strip_html_tags(html_text), "disable_web_page_preview": True},
                    timeout_s=15.0,
                )
                desc = str(js.get("description") or "") if isinstance(js, dict) else ""
            if status < 400 and isinstance(js, dict) and js.get("ok"):
                any_ok = True
                TG_OK = True
                TG_SENT_COUNT += 1
                LAST_TG_ERROR = ""
            else:
                LAST_TG_ERROR = f"sendMessage HTTP {status}: {desc or 'Bilinmeyen Telegram hatası'}"
                _log(f"[TG HATA -> {cid}] {LAST_TG_ERROR}")
        except Exception as e:
            LAST_TG_ERROR = f"sendMessage istisna: {e}"
            _log(f"[TG BAGLANTI HATASI -> {cid}] {LAST_TG_ERROR}")
    return any_ok


def build_telegram_photo_multipart(cid: str, caption: str, png: bytes) -> Tuple[bytes, str]:
    boundary = f"----SpotAkisBoundary{uuid.uuid4().hex}"
    parts: List[bytes] = []
    for k, v in [("chat_id", cid), ("caption", caption), ("parse_mode", "HTML")]:
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{k}"\r\n\r\n'
                f"{v}\r\n"
            ).encode("utf-8")
        )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="chart.png"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode("utf-8")
        + png
        + f"\r\n--{boundary}--\r\n".encode("utf-8")
    )
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


async def tg_send_photo(png: bytes, caption: str, explicit_cid: Optional[str] = None) -> bool:
    global LAST_TG_ERROR, TG_OK, TG_SENT_COUNT
    if not BOT_TOKEN:
        LAST_TG_ERROR = "BOT_TOKEN tanımlı değil"
        return False
    if not png:
        return await tg_send_text(caption, explicit_cid)
    targets = [explicit_cid] if explicit_cid else list(SUBSCRIBED_CHATS)
    if not targets:
        LAST_TG_ERROR = "CHAT_ID / hedef sohbet tanımlı değil"
        return False
    safe_cap = caption[:995] + "..." if len(caption) > 1000 else caption
    any_ok = False
    for cid in targets:
        try:
            body, ctype = build_telegram_photo_multipart(cid, safe_cap, png)
            status, raw = await http_request(
                "POST",
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                headers={"Content-Type": ctype},
                body=body,
                timeout_s=20.0,
            )
            js = _safe_json_loads(raw)
            if status < 400 and isinstance(js, dict) and js.get("ok"):
                any_ok = True
                TG_OK = True
                TG_SENT_COUNT += 1
                LAST_TG_ERROR = ""
            else:
                desc = str(js.get("description") or "") if isinstance(js, dict) else f"HTTP {status}"
                LAST_TG_ERROR = f"sendPhoto HTTP {status}: {desc}"
                _log(f"[TG PHOTO FALLBACK -> {cid}] {LAST_TG_ERROR}")
                if await tg_send_text(caption, cid):
                    any_ok = True
        except Exception as e:
            LAST_TG_ERROR = f"sendPhoto istisna: {e}"
            _log(f"[TG PHOTO HATA -> {cid}] {LAST_TG_ERROR}")
            if await tg_send_text(caption, cid):
                any_ok = True
    return any_ok


# ==============================================================================
# BINANCE FUTURES OTO ISLEM (LONG & SHORT)
# ==============================================================================
async def futures_signed(
    method: str,
    path: str,
    params: Dict[str, Any],
    mode: str,
) -> Any:
    base_url = "https://testnet.binancefuture.com" if mode == "TESTNET" else "https://fapi.binance.com"
    qp = {k: str(v) for k, v in params.items()}
    qp["recvWindow"] = "5000"
    qp["timestamp"] = str(int(time.time() * 1000))
    qs = urllib.parse.urlencode(qp)
    sig = hmac.new(BINANCE_API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f"{base_url}{path}?{qs}&signature={sig}"
    status, raw = await http_request(method, url, headers={"X-MBX-APIKEY": BINANCE_API_KEY}, timeout_s=10.0)
    data = json.loads(raw.decode("utf-8")) if raw else {}
    if status >= 400:
        raise RuntimeError(f"Binance {status} ({data.get('code')}): {data.get('msg')}")
    return data


def round_step(val: float, step_str: str) -> float:
    step = float(step_str)
    if step <= 0:
        return val
    dec = len(step_str.split(".")[1].rstrip("0")) if "." in step_str else 0
    return round(int(val / step + 1e-9) * step, dec)


def fmt_step(val: float, step_str: str) -> str:
    step = float(step_str)
    if step <= 0:
        return f"{val:.6f}".rstrip("0").rstrip(".")
    dec = len(step_str.split(".")[1].rstrip("0")) if "." in step_str else 0
    rounded = int(val / step + 1e-9) * step
    return f"{rounded:.{dec}f}"


async def maybe_execute_auto_trade(signal: Dict[str, Any]) -> None:
    global PAPER_BALANCE_USDT
    if not BOT_RUNNING:
        return
    if not AUTO_TRADE["enabled"]:
        return
    if signal["quote"] != "USDT":
        return
    if not STRATEGY_CONFIG["autoTradeEngines"].get(signal["engine"], False):
        _log(f"[OTO FUTURES] {signal['symbol']} ({signal['engine']}) -> tikli olmadigi icin islem acilmadi.")
        return
    sym = signal["symbol"]
    if sym in TRADE_INFLIGHT:
        return
    open_tr = [t for t in TRADES if t["status"] == "OPEN"]
    if AUTO_TRADE["maxOpenTrades"] > 0 and len(open_tr) >= AUTO_TRADE["maxOpenTrades"]:
        _log(f"[OTO FUTURES] {sym} atlandi: maksimum acik pozisyon siniri ({AUTO_TRADE['maxOpenTrades']}).")
        return
    if any(t["symbol"] == sym or t["base"] == signal["base"] for t in open_tr):
        return
    flt = FUTURES_FILTERS.get(sym) or SPOT_FILTERS.get(sym)
    if not flt:
        if AUTO_TRADE["mode"] == "PAPER":
            flt = {"stepSize": "0.0001", "minQty": 0.0, "tickSize": "0.0001", "minNotional": 5.0}
        else:
            _log(f"[OTO FUTURES] {sym} ({AUTO_TRADE['mode']}) -> USD-M Futures kontrati bulunamadi.")
            return

    dir_side = "SHORT" if signal.get("direction") == "SHORT" else "LONG"
    dir_mode = str(AUTO_TRADE.get("directionMode") or "BOTH").upper()
    if dir_mode == "LONG" and dir_side != "LONG":
        _log(f"[OTO FUTURES] {sym} ({dir_side}) atlandi: yon tercihi yalnizca LONG.")
        return
    if dir_mode == "SHORT" and dir_side != "SHORT":
        _log(f"[OTO FUTURES] {sym} ({dir_side}) atlandi: yon tercihi yalnizca SHORT.")
        return
    entry_side = "SELL" if dir_side == "SHORT" else "BUY"
    close_side = "BUY" if dir_side == "SHORT" else "SELL"
    mode = AUTO_TRADE["mode"]
    lev = AUTO_TRADE["leverage"]
    margin = AUTO_TRADE["tradeUsdt"]
    notional = margin * lev
    if mode == "PAPER" and PAPER_BALANCE_USDT < margin:
        return
    if mode in ("TESTNET", "LIVE") and not BINANCE_KEYS_READY:
        return

    TRADE_INFLIGHT.add(sym)
    try:
        ref_p = signal["entryPrice"]
        tp_mult = (1 - AUTO_TRADE["tpPct"] / 100.0) if dir_side == "SHORT" else (1 + AUTO_TRADE["tpPct"] / 100.0)
        sl_mult = (1 + AUTO_TRADE["slPct"] / 100.0) if dir_side == "SHORT" else (1 - AUTO_TRADE["slPct"] / 100.0)

        if mode == "PAPER":
            qty = round_step(notional / ref_p, flt["stepSize"])
            if qty <= 0 or qty < flt["minQty"]:
                return
            actual_notional = qty * ref_p
            actual_margin = round(actual_notional / lev, 2)
            tp_p = float(fmt_step(ref_p * tp_mult, flt["tickSize"])) or ref_p * tp_mult
            sl_p = float(fmt_step(ref_p * sl_mult, flt["tickSize"])) or ref_p * sl_mult
            PAPER_BALANCE_USDT = max(0.0, round(PAPER_BALANCE_USDT - actual_margin, 4))
            tr = {
                "id": f"TR-PAPER-{sym}-{int(time.time()*1000)}",
                "symbol": sym, "base": signal["base"], "engine": signal["engine"],
                "direction": dir_side, "mode": "PAPER", "leverage": lev, "status": "OPEN",
                "entryPrice": ref_p, "targetPrice": tp_p, "stopPrice": sl_p, "currentPrice": ref_p,
                "quantity": qty, "marginUsdt": actual_margin, "notionalUsdt": round(actual_notional, 2),
                "pnlUsdt": 0.0, "pnlPct": 0.0, "roePct": 0.0, "openedAt": int(time.time() * 1000),
            }
            TRADES.insert(0, tr)
            save_state()
            if AUTO_TRADE["notifyPaperTelegram"]:
                await tg_send_text(
                    f"{'🟢' if dir_side == 'LONG' else '🔴'} <b>FUTURES {lev}x {dir_side} AÇILDI (PAPER · {esc_html(signal['engineLabel'])})</b>\n"
                    f"• #{esc_html(sym)} · Teminat: ${actual_margin:.2f} | Giriş: ${fmt_price(ref_p)}\n"
                    f"🎯 TP: <b>${fmt_price(tp_p)}</b> | 🛑 SL: <b>${fmt_price(sl_p)}</b>",
                )
            return

        try:
            await futures_signed("POST", "/fapi/v1/marginType", {"symbol": sym, "marginType": "ISOLATED"}, mode)
        except Exception:
            pass
        await futures_signed("POST", "/fapi/v1/leverage", {"symbol": sym, "leverage": lev}, mode)

        qty = round_step(notional / ref_p, flt["stepSize"])
        if qty <= 0 or qty < flt["minQty"] or qty * ref_p < flt["minNotional"]:
            return
        qty_str = fmt_step(qty, flt["stepSize"])
        hedge = False
        try:
            buy_res = await futures_signed("POST", "/fapi/v1/order", {"symbol": sym, "side": entry_side, "type": "MARKET", "quantity": qty_str}, mode)
        except Exception as e:
            if "-4061" in str(e):
                hedge = True
                buy_res = await futures_signed("POST", "/fapi/v1/order", {"symbol": sym, "side": entry_side, "positionSide": dir_side, "type": "MARKET", "quantity": qty_str}, mode)
            else:
                raise

        entry_p = float(buy_res.get("avgPrice") or 0) or ref_p
        tp_str = fmt_step(entry_p * tp_mult, flt["tickSize"])
        sl_str = fmt_step(entry_p * sl_mult, flt["tickSize"])

        tp_params: Dict[str, Any] = {"symbol": sym, "side": close_side, "type": "LIMIT", "timeInForce": "GTC", "quantity": qty_str, "price": tp_str}
        tp_params["positionSide" if hedge else "reduceOnly"] = dir_side if hedge else "true"
        tp_res = await futures_signed("POST", "/fapi/v1/order", tp_params, mode)

        sl_params: Dict[str, Any] = {"symbol": sym, "side": close_side, "type": "STOP_MARKET", "stopPrice": sl_str, "quantity": qty_str, "workingType": "MARK_PRICE", "priceProtect": "TRUE"}
        sl_params["positionSide" if hedge else "reduceOnly"] = dir_side if hedge else "true"
        sl_res = None
        try:
            sl_res = await futures_signed("POST", "/fapi/v1/order", sl_params, mode)
        except Exception:
            pass

        tr = {
            "id": f"TR-{mode}-{sym}-{int(time.time()*1000)}",
            "symbol": sym, "base": signal["base"], "engine": signal["engine"],
            "direction": dir_side, "mode": mode, "leverage": lev, "status": "OPEN",
            "entryPrice": entry_p, "targetPrice": float(tp_str), "stopPrice": float(sl_str), "currentPrice": entry_p,
            "quantity": qty, "marginUsdt": round((qty * entry_p) / lev, 2), "notionalUsdt": round(qty * entry_p, 2),
            "tpOrderId": str(tp_res.get("orderId", "")), "slOrderId": str(sl_res.get("orderId", "")) if sl_res else "",
            "pnlUsdt": 0.0, "pnlPct": 0.0, "roePct": 0.0, "openedAt": int(time.time() * 1000),
        }
        TRADES.insert(0, tr)
        save_state()
        await tg_send_text(
            f"{'🟢' if dir_side == 'LONG' else '🔴'} <b>FUTURES {lev}x {dir_side} AÇILDI ({mode} · {esc_html(signal['engineLabel'])})</b>\n"
            f"• #{esc_html(sym)} · Miktar: {qty_str} | Giriş: ${fmt_price(entry_p)}\n"
            f"🎯 +{AUTO_TRADE['tpPct']}% TP: <b>${tp_str}</b> | 🛑 -{AUTO_TRADE['slPct']}% SL: <b>${sl_str}</b>",
        )
    except Exception as e:
        await tg_send_text(f"⚠️ <b>Futures Oto İşlem Hatası (#{esc_html(sym)})</b>\n{esc_html(str(e))}")
    finally:
        TRADE_INFLIGHT.discard(sym)


async def emit_signal(signal: Dict[str, Any], png: bytes = b"") -> None:
    if any(s["id"] == signal["id"] for s in SIGNALS):
        return
    SIGNALS.insert(0, signal)
    del SIGNALS[200:]
    save_state()

    icons = {
        "ATL": "🕳",
        "ATH": "🏔",
        "PUMP15M": "🚀",
        "VOLATILITY": "⚠️",
        "BOLLINGER": "📊",
        "ICHIMOKU": "☁️",
        "SUPERTREND": "⚡",
        "AI_MARKET": "🤖",
        "MULTI_EXCHANGE": "🌐",
        "BIG_PUMP": "🟢" if signal["direction"] == "LONG" else "🔴",
    }
    ic = icons.get(signal["engine"], "🟢" if signal["direction"] == "LONG" else "🔴")
    if signal["engine"] == "BIG_PUMP":
        ex_line = (signal.get("details") or [""])[0]
        cap = f"<b>{esc_html(signal['headline'])}</b>\n<b>{esc_html(ex_line)}</b>"
    else:
        cap = f"{ic} <b>{esc_html(signal['headline'])}</b>\n\n" + "\n".join(esc_html(l) for l in signal["details"])
    if png:
        await tg_send_photo(png, cap)
    else:
        await tg_send_text(cap)
    await maybe_execute_auto_trade(signal)


# ==============================================================================
# MOTORLAR (PUMP, 15DK KIRILIM, MULTI-TF ATL/ATH, 4S MA200, VOLATILITY, FORMASYON)
# ==============================================================================
def is_pump_locked(bucket: Dict[str, Any], base: str, now_s: float) -> bool:
    rec = bucket.get(base)
    if not rec:
        return False
    hrs = STRATEGY_CONFIG["pumpRepeatHours"]
    if hrs <= 0:
        return True
    return (now_s - rec["lockedAt"]) < hrs * 3600.0


async def verify_and_emit_pump(t: Dict[str, Any], min7: Tuple[float, float], min15: Tuple[float, float]) -> None:
    sym = t["symbol"]
    if sym in VERIFY_INFLIGHT:
        return
    VERIFY_INFLIGHT.add(sym)
    try:
        c1m, c15m = await asyncio.gather(
            fetch_spot_klines(sym, "1m", 50),
            fetch_spot_klines(sym, "15m", 50),
        )
        live_cur = float((TICKER_MAP.get(sym) or t).get("price") or t["price"])
        live_vol = float((TICKER_MAP.get(sym) or t).get("volume24h") or t["volume24h"])
        if time.time() >= BINANCE_BAN_UNTIL:
            try:
                tk_raw = await fetch_binance_json(f"/api/v3/ticker/24hr?symbol={sym}", timeout_s=5.0)
                if isinstance(tk_raw, dict):
                    live_cur = float(tk_raw.get("lastPrice") or live_cur)
                    live_vol = float(tk_raw.get("quoteVolume") or live_vol)
            except Exception:
                pass
        if not c1m and c15m:
            c1m = c15m
        if live_cur <= 0 and c1m:
            live_cur = c1m[-1]["close"]

        cl1 = [live_cur if (i == len(c1m) - 1 and live_cur > 0) else b["close"] for i, b in enumerate(c1m)]
        cl15 = [live_cur if (i == len(c15m) - 1 and live_cur > 0) else b["close"] for i, b in enumerate(c15m)]
        rsi1 = compute_rsi(cl1, 14)[-1] if len(cl1) >= 16 else 0.0
        rsi15 = compute_rsi(cl15, 14)[-1] if len(cl15) >= 16 else 0.0

        ver_min7 = min([min7[1]] + [b["low"] for b in c1m[-7:] if b["low"] > 0])
        ver_pct7 = ((live_cur - ver_min7) / ver_min7) * 100.0
        cur15 = c15m[-1] if c15m else None
        ver_min15 = min([min15[1]] + [b["low"] for b in c1m[-15:] if b["low"] > 0])
        ver_pct15 = ((live_cur - ver_min15) / ver_min15) * 100.0

        base15 = c15m[-11:-1]
        avg15 = sum(b["quoteVol"] for b in base15) / max(1, len(base15))
        elapsed = max(90, min(900, int(time.time()) - int(cur15["openTime"]))) if cur15 else 900
        paced15 = (cur15["quoteVol"] * (900 / elapsed)) if cur15 else 0.0
        rvol = round(paced15 / avg15, 1) if avg15 > 0 else 1.0

        now_s = time.time()
        rsi_gate = AUTO_TRADE["rsiMin"]
        fast_ok = (
            STRATEGY_CONFIG["pumpEnabled"]
            and ver_pct7 >= STRATEGY_CONFIG["pumpThresholdPct"]
            and rsi1 >= rsi_gate
            and not is_pump_locked(LOCKS["pump"], t["base"], now_s)
        )
        b15_ok = (
            STRATEGY_CONFIG["pump15mEnabled"]
            and ver_pct15 >= STRATEGY_CONFIG["pump15mThresholdPct"]
            and rvol >= STRATEGY_CONFIG["pump15mMinRvol"]
            and rsi15 >= rsi_gate
            and not is_pump_locked(LOCKS["pump15m"], t["base"], now_s)
        )
        if not fast_ok and not b15_ok:
            VERIFY_COOLDOWN[sym] = now_s + 20.0
            return

        if fast_ok:
            LOCKS["pump"][t["base"]] = {"base": t["base"], "symbol": sym, "lockedAt": now_s}
            await emit_signal({
                "id": f"PUMP-{sym}-{int(now_s*1000)}", "engine": "PUMP", "engineLabel": "0-7 dk Ani Pump",
                "symbol": sym, "base": t["base"], "quote": "USDT", "direction": "LONG", "timeframe": "1m",
                "entryPrice": live_cur, "currentPrice": live_cur, "peakPrice": live_cur, "peakPct": 0.0,
                "headline": f"SPOT ANLIK PUMP DEDEKTÖRÜ — #{sym}",
                "details": [
                    f"📈 Değişim: +{ver_pct7:.2f}% · RSI(14): {rsi1:.1f} (≥{rsi_gate})",
                    f"💰 Başlangıç: {fmt_price(ver_min7)} ➡️ Anlık: {fmt_price(live_cur)} | Hacim: ${live_vol:,.0f}",
                ],
                "createdAt": int(now_s * 1000), "peakAt": int(now_s * 1000),
            })

        if b15_ok:
            LOCKS["pump15m"][t["base"]] = {"base": t["base"], "symbol": sym, "lockedAt": now_s}
            await emit_signal({
                "id": f"PUMP15M-{sym}-{int(now_s*1000)}", "engine": "PUMP15M", "engineLabel": "15 dk Hacimli Kırılım",
                "symbol": sym, "base": t["base"], "quote": "USDT", "direction": "LONG", "timeframe": "15m",
                "entryPrice": live_cur, "currentPrice": live_cur, "peakPrice": live_cur, "peakPct": 0.0,
                "headline": f"SPOT 15DK HACİMLİ KIRILIM — #{sym}",
                "details": [
                    f"📈 Değişim: +{ver_pct15:.2f}% · RVOL: {rvol:.1f}x · RSI(14): {rsi15:.1f}",
                    f"💰 Başlangıç: {fmt_price(ver_min15)} ➡️ Anlık: {fmt_price(live_cur)} | Hacim: ${live_vol:,.0f}",
                ],
                "createdAt": int(now_s * 1000), "peakAt": int(now_s * 1000),
            })
    except Exception as e:
        _log(f"[Pump Dogrulama Hatasi ({sym})] {e}")
    finally:
        VERIFY_INFLIGHT.discard(sym)


def evaluate_ma200_bars(bars: List[Dict[str, float]], period: int, allow_long: bool, allow_short: bool) -> Optional[Tuple[str, float, float, float]]:
    if len(bars) < period + 4:
        return None
    closes = [b["close"] for b in bars]
    sma = compute_sma(closes, period)
    i0, i1, i2 = len(bars) - 4, len(bars) - 3, len(bars) - 2
    c0, c1, c2 = bars[i0], bars[i1], bars[i2]
    ma0, ma1, ma2 = sma[i0], sma[i1], sma[i2]
    if ma1 is None or ma2 is None:
        return None

    c1_green, c2_green = c1["close"] > c1["open"], c2["close"] > c2["open"]
    c1_red, c2_red = c1["close"] < c1["open"], c2["close"] < c2["open"]
    c0_above_green = (ma0 is not None) and (c0["close"] > ma0) and (c0["close"] > c0["open"])
    c0_below_red = (ma0 is not None) and (c0["close"] < ma0) and (c0["close"] < c0["open"])

    if allow_long and c1["close"] > ma1 and c2["close"] > ma2 and c1_green and c2_green and not c0_above_green:
        return ("LONG", ma2, c2["close"], float(c2["openTime"]))
    if allow_short and c1["close"] < ma1 and c2["close"] < ma2 and c1_red and c2_red and not c0_below_red:
        return ("SHORT", ma2, c2["close"], float(c2["openTime"]))
    return None


def detect_patterns_py(bars: List[Dict[str, float]], min_height_pct: float, enabled_map: Dict[str, bool]) -> Optional[Dict[str, Any]]:
    if len(bars) < 40:
        return None
    rec = bars[-75:]
    n = len(rec)
    break_bar = rec[n - 2]
    cur = rec[n - 1]["close"]

    highs: List[Tuple[int, float]] = []
    lows: List[Tuple[int, float]] = []
    for i in range(3, n - 3):
        if all(rec[j]["high"] < rec[i]["high"] for j in range(i - 3, i + 3) if j != i):
            highs.append((i, rec[i]["high"]))
        if all(rec[j]["low"] > rec[i]["low"] for j in range(i - 3, i + 3) if j != i):
            lows.append((i, rec[i]["low"]))

    if enabled_map.get("DOUBLE_BOTTOM") and len(lows) >= 2:
        b1, b2 = lows[-2], lows[-1]
        if b2[0] - b1[0] >= 5 and (n - 2 - b2[0]) <= 12 and abs(b2[1] - b1[1]) / min(b1[1], b2[1]) * 100 <= 1.6:
            nk = max(b["high"] for b in rec[b1[0] : b2[0] + 1])
            bot = min(b1[1], b2[1])
            hp = (nk - bot) / bot * 100
            if hp >= min_height_pct and cur > nk and break_bar["close"] > nk * 0.995:
                return {"key": "DOUBLE_BOTTOM", "dir": "LONG", "support": bot, "target": nk + (nk - bot), "hp": hp}

    if enabled_map.get("DOUBLE_TOP") and len(highs) >= 2:
        t1, t2 = highs[-2], highs[-1]
        if t2[0] - t1[0] >= 5 and (n - 2 - t2[0]) <= 12 and abs(t2[1] - t1[1]) / max(t1[1], t2[1]) * 100 <= 1.6:
            nk = min(b["low"] for b in rec[t1[0] : t2[0] + 1])
            top = max(t1[1], t2[1])
            hp = (top - nk) / top * 100
            if hp >= min_height_pct and cur < nk and break_bar["close"] < nk * 1.005:
                return {"key": "DOUBLE_TOP", "dir": "SHORT", "support": nk, "target": max(cur * 0.5, nk - (top - nk)), "hp": hp}

    if enabled_map.get("HEAD_SHOULDERS") and len(highs) >= 3:
        ls, hd, rs = highs[-3], highs[-2], highs[-1]
        if hd[1] > ls[1] * 1.006 and hd[1] > rs[1] * 1.006 and abs(ls[1] - rs[1]) / hd[1] <= 0.03 and (n - 2 - rs[0]) <= 12:
            tr2 = min(b["low"] for b in rec[hd[0] : rs[0] + 1])
            hp = (hd[1] - tr2) / hd[1] * 100
            if hp >= min_height_pct and cur < tr2 and break_bar["close"] < tr2 * 1.005:
                return {"key": "HEAD_SHOULDERS", "dir": "SHORT", "support": tr2, "target": max(cur * 0.5, tr2 - (hd[1] - tr2)), "hp": hp}

    if enabled_map.get("INV_HEAD_SHOULDERS") and len(lows) >= 3:
        ls, hd, rs = lows[-3], lows[-2], lows[-1]
        if hd[1] < ls[1] * 0.994 and hd[1] < rs[1] * 0.994 and abs(ls[1] - rs[1]) / max(ls[1], 1e-9) <= 0.03 and (n - 2 - rs[0]) <= 12:
            pk2 = max(b["high"] for b in rec[hd[0] : rs[0] + 1])
            hp = (pk2 - hd[1]) / hd[1] * 100
            if hp >= min_height_pct and cur > pk2 and break_bar["close"] > pk2 * 0.995:
                return {"key": "INV_HEAD_SHOULDERS", "dir": "LONG", "support": hd[1], "target": pk2 + (pk2 - hd[1]), "hp": hp}

    if n >= 24:
        pole, flag = rec[n - 22 : n - 10], rec[n - 10 : n - 1]
        p_hi, p_lo = max(b["high"] for b in pole), min(b["low"] for b in pole)
        f_hi, f_lo = max(b["high"] for b in flag), min(b["low"] for b in flag)
        pole_pct = (p_hi - p_lo) / max(p_lo, 1e-9) * 100
        if enabled_map.get("BULL_FLAG") and pole_pct >= max(4.5, min_height_pct * 2.2):
            retrace = (p_hi - f_lo) / max(p_hi - p_lo, 1e-9) * 100
            if retrace <= 50 and cur > f_hi and break_bar["close"] > flag[-1]["open"]:
                return {"key": "BULL_FLAG", "dir": "LONG", "support": f_lo, "target": cur + (p_hi - p_lo) * 0.7, "hp": pole_pct}
        if enabled_map.get("BEAR_FLAG") and pole_pct >= max(4.5, min_height_pct * 2.2):
            retrace = (f_hi - p_lo) / max(p_hi - p_lo, 1e-9) * 100
            if retrace <= 50 and cur < f_lo and break_bar["close"] < flag[-1]["open"]:
                return {"key": "BEAR_FLAG", "dir": "SHORT", "support": f_lo, "target": max(cur * 0.45, cur - (p_hi - p_lo) * 0.7), "hp": pole_pct}

    if enabled_map.get("BEAR_RECTANGLE") and n >= 28:
        prior = rec[n - 26]["close"]
        box = rec[n - 16 : n - 1]
        b_hi, b_lo = max(b["high"] for b in box), min(b["low"] for b in box)
        bp = (b_hi - b_lo) / max(b_hi, 1e-9) * 100
        if prior > b_hi * 1.015 and min_height_pct <= bp <= 8.5 and cur < b_lo and break_bar["close"] < b_lo * 1.003:
            return {"key": "BEAR_RECTANGLE", "dir": "SHORT", "support": b_lo, "target": max(cur * 0.5, b_lo - (b_hi - b_lo)), "hp": bp}

    return None


def evaluate_bollinger_py(
    bars: List[Dict[str, float]], period: int, std_mult: float, allow_long: bool, allow_short: bool
) -> Optional[Dict[str, Any]]:
    if len(bars) < period + 4:
        return None
    closes = [b["close"] for b in bars]
    mid_s = compute_sma(closes, period)
    upper_s: List[Optional[float]] = [None] * len(bars)
    lower_s: List[Optional[float]] = [None] * len(bars)
    for i in range(period - 1, len(bars)):
        m = mid_s[i]
        if m is None:
            continue
        var = sum((closes[j] - m) ** 2 for j in range(i - period + 1, i + 1)) / period
        sd = math.sqrt(var)
        upper_s[i] = m + std_mult * sd
        lower_s[i] = m - std_mult * sd
    i1, i2 = len(bars) - 3, len(bars) - 2
    u1, u2, m2, l1, l2 = upper_s[i1], upper_s[i2], mid_s[i2], lower_s[i1], lower_s[i2]
    if u1 is None or u2 is None or m2 is None or l1 is None or l2 is None:
        return None
    c1, c2 = bars[i1], bars[i2]
    if allow_long:
        if c2["close"] > u2 and c1["close"] <= u1 and c2["close"] > c2["open"]:
            return {"dir": "LONG", "reason": "Üst Bant Yukarı Kırılımı", "upper": u2, "mid": m2, "lower": l2, "midSeries": mid_s, "openTime": c2["openTime"]}
        if c1["low"] <= l1 and c2["close"] > l2 and c2["close"] > c2["open"]:
            return {"dir": "LONG", "reason": "Alt Banttan Yukarı Dönüş", "upper": u2, "mid": m2, "lower": l2, "midSeries": mid_s, "openTime": c2["openTime"]}
    if allow_short:
        if c2["close"] < l2 and c1["close"] >= l1 and c2["close"] < c2["open"]:
            return {"dir": "SHORT", "reason": "Alt Bant Aşağı Kırılımı", "upper": u2, "mid": m2, "lower": l2, "midSeries": mid_s, "openTime": c2["openTime"]}
        if c1["high"] >= u1 and c2["close"] < u2 and c2["close"] < c2["open"]:
            return {"dir": "SHORT", "reason": "Üst Banttan Aşağı Dönüş", "upper": u2, "mid": m2, "lower": l2, "midSeries": mid_s, "openTime": c2["openTime"]}
    return None


def evaluate_ichimoku_py(bars: List[Dict[str, float]], allow_long: bool, allow_short: bool) -> Optional[Dict[str, Any]]:
    if len(bars) < 80:
        return None

    def dmid(idx: int, ln: int) -> Optional[float]:
        if idx < ln - 1:
            return None
        sl = bars[idx - ln + 1 : idx + 1]
        return (max(b["high"] for b in sl) + min(b["low"] for b in sl)) / 2.0

    i1, i2 = len(bars) - 3, len(bars) - 2
    t1, k1, t2, k2 = dmid(i1, 9), dmid(i1, 26), dmid(i2, 9), dmid(i2, 26)
    sa1t, sa1k, sb1 = dmid(i1 - 26, 9), dmid(i1 - 26, 26), dmid(i1 - 26, 52)
    sa2t, sa2k, sb2 = dmid(i2 - 26, 9), dmid(i2 - 26, 26), dmid(i2 - 26, 52)
    if None in (t1, k1, t2, k2, sa1t, sa1k, sb1, sa2t, sa2k, sb2):
        return None
    sa1, sa2 = (sa1t + sa1k) / 2.0, (sa2t + sa2k) / 2.0
    ct1, cb1 = max(sa1, sb1), min(sa1, sb1)
    ct2, cb2 = max(sa2, sb2), min(sa2, sb2)
    c1, c2 = bars[i1], bars[i2]
    if allow_long:
        if c2["close"] > ct2 and c1["close"] <= ct1 and c2["close"] > c2["open"]:
            return {"dir": "LONG", "reason": "Kumo Bulutu Yukarı Kırılımı", "tenkan": t2, "kijun": k2, "top": ct2, "bot": cb2, "openTime": c2["openTime"]}
        if t1 <= k1 and t2 > k2 and c2["close"] > ct2:
            return {"dir": "LONG", "reason": "Bulut Üstü Tenkan/Kijun Kesişimi", "tenkan": t2, "kijun": k2, "top": ct2, "bot": cb2, "openTime": c2["openTime"]}
    if allow_short:
        if c2["close"] < cb2 and c1["close"] >= cb1 and c2["close"] < c2["open"]:
            return {"dir": "SHORT", "reason": "Kumo Bulutu Aşağı Kırılımı", "tenkan": t2, "kijun": k2, "top": ct2, "bot": cb2, "openTime": c2["openTime"]}
        if t1 >= k1 and t2 < k2 and c2["close"] < cb2:
            return {"dir": "SHORT", "reason": "Bulut Altı Tenkan/Kijun Kesişimi", "tenkan": t2, "kijun": k2, "top": ct2, "bot": cb2, "openTime": c2["openTime"]}
    return None


def evaluate_supertrend_py(
    bars: List[Dict[str, float]], atr_period: int, mult: float, allow_long: bool, allow_short: bool
) -> Optional[Dict[str, Any]]:
    n = len(bars)
    if n < atr_period + 6:
        return None
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(bars[i]["high"] - bars[i]["low"], abs(bars[i]["high"] - bars[i - 1]["close"]), abs(bars[i]["low"] - bars[i - 1]["close"]))
    atr = [0.0] * n
    atr[atr_period] = sum(tr[1 : atr_period + 1]) / atr_period
    for i in range(atr_period + 1, n):
        atr[i] = (atr[i - 1] * (atr_period - 1) + tr[i]) / atr_period
    fu, fl, trend = [0.0] * n, [0.0] * n, [1] * n
    st_s: List[Optional[float]] = [None] * n
    for i in range(atr_period, n):
        hl2 = (bars[i]["high"] + bars[i]["low"]) / 2.0
        bu, bl = hl2 + mult * atr[i], hl2 - mult * atr[i]
        if i == atr_period:
            fu[i], fl[i] = bu, bl
            trend[i] = 1 if bars[i]["close"] >= hl2 else -1
        else:
            fu[i] = bu if (bu < fu[i - 1] or bars[i - 1]["close"] > fu[i - 1]) else fu[i - 1]
            fl[i] = bl if (bl > fl[i - 1] or bars[i - 1]["close"] < fl[i - 1]) else fl[i - 1]
            if trend[i - 1] == 1:
                trend[i] = -1 if bars[i]["close"] < fl[i] else 1
            else:
                trend[i] = 1 if bars[i]["close"] > fu[i] else -1
        st_s[i] = fl[i] if trend[i] == 1 else fu[i]
    i1, i2 = n - 3, n - 2
    stv = st_s[i2]
    if stv is None:
        return None
    if allow_long and trend[i1] == -1 and trend[i2] == 1:
        return {"dir": "LONG", "supertrend": stv, "stSeries": st_s, "openTime": bars[i2]["openTime"]}
    if allow_short and trend[i1] == 1 and trend[i2] == -1:
        return {"dir": "SHORT", "supertrend": stv, "stSeries": st_s, "openTime": bars[i2]["openTime"]}
    return None


# ==============================================================================
# ARKA PLAN DONGULERI (WEBSOCKET + REST YEDEK, ATL/ATH, MA200, VOL, BB, ICHI, ST)
# ==============================================================================
def _ingest_ticker_item(item: Dict[str, Any], now_s: float) -> None:
    sym = str(item.get("symbol") or item.get("s") or "")
    if not sym.endswith("USDT") or (ACTIVE_SPOT_SYMBOLS and sym not in ACTIVE_SPOT_SYMBOLS):
        return
    price = float(item.get("lastPrice") or item.get("c") or 0)
    vol = float(item.get("quoteVolume") or item.get("q") or 0)
    if price <= 0:
        return
    bid = float(item.get("bidPrice") or item.get("b") or price)
    if bid <= 0:
        return
    t = {
        "symbol": sym, "base": sym[:-4], "quote": "USDT",
        "price": price,
        "change24h": float(item.get("priceChangePercent") or item.get("P") or 0),
        "volume24h": vol,
        "high24h": float(item.get("highPrice") or item.get("h") or price),
        "low24h": float(item.get("lowPrice") or item.get("l") or price),
    }
    TICKER_MAP[sym] = t
    dq = PRICE_HIST.setdefault(sym, deque(maxlen=400))
    dq.append((now_s, price))
    while dq and dq[0][0] < now_s - 900.0:
        dq.popleft()
    if BOT_RUNNING and len(dq) >= 2 and vol >= STRATEGY_CONFIG["pumpMinVolUsdt"]:
        min15 = min(dq, key=lambda x: x[1])
        min7_list = [x for x in dq if x[0] >= now_s - 420.0]
        min7 = min(min7_list, key=lambda x: x[1]) if min7_list else min15
        p7 = ((price - min7[1]) / min7[1]) * 100.0
        p15 = ((price - min15[1]) / min15[1]) * 100.0
        w7 = STRATEGY_CONFIG["pumpEnabled"] and p7 >= STRATEGY_CONFIG["pumpThresholdPct"] and not is_pump_locked(LOCKS["pump"], t["base"], now_s)
        w15 = STRATEGY_CONFIG["pump15mEnabled"] and p15 >= STRATEGY_CONFIG["pump15mThresholdPct"] and not is_pump_locked(LOCKS["pump15m"], t["base"], now_s)
        if (w7 or w15) and now_s >= VERIFY_COOLDOWN.get(sym, 0):
            VERIFY_COOLDOWN[sym] = now_s + 15.0
            asyncio.create_task(verify_and_emit_pump(t, min7, min15))
    evaluate_big_pump_for_symbol(t, now_s)


def get_exchanges_for_symbol(sym: str) -> List[str]:
    selected = [str(x).upper() for x in (STRATEGY_CONFIG.get("bigPumpExchanges") or list(BIG_PUMP_EXCHANGE_ORDER))]
    if not selected:
        return []
    out: List[str] = []
    for ex in BIG_PUMP_EXCHANGE_ORDER:
        if ex not in selected:
            continue
        if ex == "BINANCE":
            if sym in TICKER_MAP or sym in EXCHANGE_SYMBOL_SETS.get("BINANCE", set()):
                out.append(ex)
        elif sym in EXCHANGE_SYMBOL_SETS.get(ex, set()):
            out.append(ex)
    return out


async def verify_and_emit_big_pump(t: Dict[str, Any], dr: str, pct_override: float, anchor: float) -> None:
    sym = t["symbol"]
    ck = f"{sym}:{dr}"
    if ck in BIG_PUMP_INFLIGHT:
        return
    BIG_PUMP_INFLIGHT.add(ck)
    try:
        win_m = max(1, int(STRATEGY_CONFIG.get("bigPumpWindowMin", 15)))
        bars = await fetch_spot_klines(sym, "1m", min(120, max(95, win_m + 30)))
        live_cur = float((TICKER_MAP.get(sym) or t).get("price") or t["price"])
        recent = bars[-max(2, win_m):] if bars else []
        measured = pct_override
        base_anchor = anchor
        if len(recent) >= 2:
            if dr == "LONG":
                lo = min((b["low"] for b in recent if b["low"] > 0), default=0.0)
                if lo > 0:
                    kp = ((live_cur - lo) / lo) * 100.0
                    if abs(kp) > abs(measured):
                        measured, base_anchor = kp, lo
            else:
                hi = max((b["high"] for b in recent if b["high"] > 0), default=0.0)
                if hi > 0:
                    kp = ((live_cur - hi) / hi) * 100.0
                    if abs(kp) > abs(measured):
                        measured, base_anchor = kp, hi
        thresh = max(1.0, float(STRATEGY_CONFIG.get("bigPumpThresholdPct", 5.0)))
        now_s = time.time()
        if abs(measured) < thresh or BIG_PUMP_COOLDOWN.get(ck, 0) > now_s:
            return
        ex_list = get_exchanges_for_symbol(sym)
        if not ex_list:
            return
        BIG_PUMP_COOLDOWN[ck] = now_s + max(1, int(STRATEGY_CONFIG.get("bigPumpCooldownMin", 10))) * 60.0
        al = BIG_PUMP_ALERTS.setdefault(sym, [])
        cutoff = now_s - 86400.0
        al[:] = [x for x in al if x >= cutoff] + [now_s]
        alert_num = len(al)
        png = render_big_pump_png(sym, bars, live_cur) if len(bars) > 5 else b""
        icon = "🔴" if dr == "SHORT" else "🟢"
        sign_pfx = "+ " if measured >= 0 else "- "
        headline = f"{icon}#{t['base']}: {sign_pfx}{abs(measured):.2f}%. 🔔 {alert_num}"
        await emit_signal({
            "id": f"BIGPUMP-{sym}-{dr}-{int(now_s * 1000)}",
            "engine": "BIG_PUMP",
            "engineLabel": f"Big Pumps ({win_m}dk)",
            "symbol": sym, "base": t["base"], "quote": "USDT", "direction": dr, "timeframe": "1m",
            "entryPrice": live_cur, "currentPrice": live_cur, "peakPrice": live_cur, "peakPct": 0.0,
            "headline": headline,
            "details": [
                " ".join(ex_list),
                f"📊 {win_m} dk Hareket: {measured:+.2f}% | Başlangıç: ${fmt_price(base_anchor)} ➡️ Anlık: ${fmt_price(live_cur)}",
            ],
            "createdAt": int(now_s * 1000), "peakAt": int(now_s * 1000),
        }, png)
    except Exception as e:
        _log(f"[Big Pump Hatasi ({sym})] {e}")
    finally:
        BIG_PUMP_INFLIGHT.discard(ck)


def evaluate_big_pump_for_symbol(t: Dict[str, Any], now_s: float) -> None:
    if not BOT_RUNNING or not STRATEGY_CONFIG.get("bigPumpEnabled"):
        return
    sym = t["symbol"]
    win_m = max(1, int(STRATEGY_CONFIG.get("bigPumpWindowMin", 15)))
    dq = BIG_PUMP_HIST.setdefault(sym, deque(maxlen=240))
    if not dq or (now_s - dq[-1][0]) >= 5.0:
        dq.append((now_s, t["price"]))
    else:
        dq[-1] = (dq[-1][0], t["price"])
    cutoff = now_s - win_m * 60.0
    while dq and dq[0][0] < cutoff:
        dq.popleft()
    if len(dq) < 2 or t["volume24h"] < float(STRATEGY_CONFIG.get("bigPumpMinVolUsdt", 100000.0)):
        return
    if not get_exchanges_for_symbol(sym):
        return
    min_pt = min(dq, key=lambda x: x[1])
    max_pt = max(dq, key=lambda x: x[1])
    up_pct = ((t["price"] - min_pt[1]) / min_pt[1]) * 100.0 if min_pt[1] > 0 else 0.0
    dn_pct = ((t["price"] - max_pt[1]) / max_pt[1]) * 100.0 if max_pt[1] > 0 else 0.0
    thresh = max(1.0, float(STRATEGY_CONFIG.get("bigPumpThresholdPct", 5.0)))
    dm = str(STRATEGY_CONFIG.get("bigPumpDirection", "BOTH")).upper()
    hit_l = dm != "SHORT" and up_pct >= thresh
    hit_s = dm != "LONG" and abs(dn_pct) >= thresh
    if not hit_l and not hit_s:
        return
    if hit_s and (not hit_l or abs(dn_pct) >= abs(up_pct)):
        dr, chosen, anchor = "SHORT", dn_pct, max_pt[1]
    else:
        dr, chosen, anchor = "LONG", up_pct, min_pt[1]
    ck = f"{sym}:{dr}"
    if BIG_PUMP_COOLDOWN.get(ck, 0) <= now_s and ck not in BIG_PUMP_INFLIGHT:
        asyncio.create_task(verify_and_emit_big_pump(t, dr, chosen, anchor))


async def refresh_exchange_memberships_loop() -> None:
    while True:
        try:
            async def _fetch_set(name: str, url: str) -> None:
                st, raw = await http_get_json(url, timeout_s=8.0)
                if st != 200:
                    return
                s: Set[str] = set()
                if name == "BYBIT" and isinstance(raw, dict):
                    for x in (raw.get("result") or {}).get("list") or []:
                        sym = str(x.get("symbol") or "")
                        if sym.endswith("USDT") and float(x.get("lastPrice") or 0) > 0:
                            s.add(sym)
                elif name == "MEXC" and isinstance(raw, list):
                    for x in raw:
                        sym = str(x.get("symbol") or "")
                        if sym.endswith("USDT") and float(x.get("lastPrice") or 0) > 0:
                            s.add(sym)
                elif name == "GATE" and isinstance(raw, list):
                    for x in raw:
                        p = str(x.get("currency_pair") or "")
                        if p.endswith("_USDT") and float(x.get("last") or 0) > 0:
                            s.add(p.replace("_USDT", "USDT"))
                elif name == "OKX" and isinstance(raw, dict):
                    for x in raw.get("data") or []:
                        inst = str(x.get("instId") or "")
                        if inst.endswith("-USDT") and float(x.get("last") or 0) > 0:
                            s.add(inst.replace("-USDT", "USDT"))
                elif name == "KUCOIN" and isinstance(raw, dict):
                    for x in (raw.get("data") or {}).get("ticker") or []:
                        sym = str(x.get("symbol") or "")
                        if sym.endswith("-USDT") and float(x.get("last") or 0) > 0:
                            s.add(sym.replace("-USDT", "USDT"))
                elif name == "BINGX" and isinstance(raw, dict):
                    for x in (raw.get("data") or {}).get("symbols") or []:
                        sym = str(x.get("symbol") or "")
                        if sym.endswith("-USDT"):
                            s.add(sym.replace("-USDT", "USDT"))
                elif name == "BITGET" and isinstance(raw, dict):
                    for x in raw.get("data") or []:
                        sym = str(x.get("symbol") or "")
                        if sym.endswith("USDT") and float(x.get("lastPr") or 0) > 0:
                            s.add(sym)
                if len(s) > 20:
                    EXCHANGE_SYMBOL_SETS[name] = s

            await asyncio.gather(
                _fetch_set("BYBIT", "https://api.bybit.com/v5/market/tickers?category=spot"),
                _fetch_set("MEXC", "https://api.mexc.com/api/v3/ticker/24hr"),
                _fetch_set("GATE", "https://api.gateio.ws/api/v4/spot/tickers"),
                _fetch_set("OKX", "https://www.okx.com/api/v5/market/tickers?instType=SPOT"),
                _fetch_set("KUCOIN", "https://api.kucoin.com/api/v1/market/allTickers"),
                _fetch_set("BINGX", "https://open-api.bingx.com/openApi/spot/v1/common/symbols"),
                _fetch_set("BITGET", "https://api.bitget.com/api/v2/spot/market/tickers"),
                return_exceptions=True,
            )
        except Exception:
            pass
        await asyncio.sleep(900)


def _ws_read_exact(sock: ssl.SSLSocket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("WebSocket baglantisi kapandi")
        buf.extend(chunk)
    return bytes(buf)


def _ws_send_frame(sock: ssl.SSLSocket, opcode: int, payload: bytes = b"") -> None:
    mask = os.urandom(4)
    ln = len(payload)
    hdr = bytearray([0x80 | opcode])
    if ln < 126:
        hdr.append(0x80 | ln)
    elif ln < 65536:
        hdr.append(0x80 | 126)
        hdr.extend(struct.pack("!H", ln))
    else:
        hdr.append(0x80 | 127)
        hdr.extend(struct.pack("!Q", ln))
    hdr.extend(mask)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(bytes(hdr) + masked)


def _ws_blocking_reader(loop: asyncio.AbstractEventLoop) -> None:
    global WS_CONNECTED, WS_LAST_MSG, ACTIVE_FEED_SOURCE
    hosts = [
        ("data-stream.binance.vision", 9443, "/ws/!ticker@arr"),
        ("stream.binance.com", 9443, "/ws/!ticker@arr"),
    ]
    idx = 0
    ctx = ssl.create_default_context()
    while True:
        host, port, path = hosts[idx % len(hosts)]
        idx += 1
        sock = None
        try:
            raw_sock = socket.create_connection((host, port), timeout=12)
            sock = ctx.wrap_socket(raw_sock, server_hostname=host)
            sock.settimeout(25)
            ws_key = base64.b64encode(os.urandom(16)).decode()
            req = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {ws_key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n\r\n"
            )
            sock.sendall(req.encode())
            resp = b""
            while b"\r\n\r\n" not in resp:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("WS el sikisma kesildi")
                resp += chunk
                if len(resp) > 16384:
                    raise ConnectionError("WS header cok uzun")
            if b" 101 " not in resp.split(b"\r\n")[0]:
                raise ConnectionError(f"WS reddedildi: {resp[:100]!r}")
            WS_CONNECTED = True
            ACTIVE_FEED_SOURCE = "Binance-WS"
            _log(f"[WebSocket] Canli fiyat akisi baglandi -> wss://{host}:{port}{path} (0 REST agirligi)")
            frag = bytearray()
            while True:
                b1, b2 = _ws_read_exact(sock, 2)
                fin = (b1 >> 7) & 1
                opcode = b1 & 0x0F
                masked = (b2 >> 7) & 1
                ln = b2 & 0x7F
                if ln == 126:
                    ln = struct.unpack("!H", _ws_read_exact(sock, 2))[0]
                elif ln == 127:
                    ln = struct.unpack("!Q", _ws_read_exact(sock, 8))[0]
                mask_k = _ws_read_exact(sock, 4) if masked else b""
                data = _ws_read_exact(sock, ln)
                if masked:
                    data = bytes(b ^ mask_k[i % 4] for i, b in enumerate(data))
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    _ws_send_frame(sock, 0xA, data)
                    continue
                if opcode in (0x1, 0x2, 0x0):
                    frag.extend(data)
                    if fin:
                        txt = frag.decode("utf-8", errors="ignore")
                        frag.clear()
                        WS_LAST_MSG = time.time()
                        try:
                            arr = json.loads(txt)
                            if isinstance(arr, list):
                                def _apply(batch: List[Dict[str, Any]] = arr) -> None:
                                    now_s = time.time()
                                    for item in batch:
                                        if isinstance(item, dict):
                                            _ingest_ticker_item(item, now_s)
                                loop.call_soon_threadsafe(_apply)
                        except Exception:
                            pass
        except Exception as e:
            WS_CONNECTED = False
            _log(f"[WebSocket Yeniden Baglaniliyor] {host}: {e}")
            time.sleep(5)
        finally:
            WS_CONNECTED = False
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass


async def binance_ws_loop() -> None:
    loop = asyncio.get_running_loop()
    threading.Thread(target=_ws_blocking_reader, args=(loop,), daemon=True, name="binance-ws").start()
    while True:
        await asyncio.sleep(60)


async def market_loop() -> None:
    global ACTIVE_FEED_SOURCE
    last_err_log = 0.0
    while True:
        try:
            now_s = time.time()
            ws_healthy = WS_CONNECTED and (now_s - WS_LAST_MSG < 30.0) and len(TICKER_MAP) > 40
            if not ws_healthy:
                raw: Any = None
                src = ""
                if now_s >= BINANCE_BAN_UNTIL:
                    try:
                        raw = await fetch_binance_json("/api/v3/ticker/24hr", timeout_s=10.0)
                        src = "Binance-REST"
                    except Exception:
                        raw = None
                if not isinstance(raw, list) or not raw:
                    src, raw = await fetch_fallback_tickers()
                if isinstance(raw, list) and raw:
                    ACTIVE_FEED_SOURCE = src or "Fallback"
                    t_now = time.time()
                    for item in raw:
                        if isinstance(item, dict):
                            _ingest_ticker_item(item, t_now)
                elif now_s - last_err_log > 90:
                    last_err_log = now_s
                    _log("[Market Loop] Tum REST kaynaklari gecici erisilemedi, yeniden deneniyor.")
        except Exception as e:
            if time.time() - last_err_log > 90:
                last_err_log = time.time()
                _log(f"[Market Loop Hatasi] {e}")
        await asyncio.sleep(35.0 if (WS_CONNECTED and len(TICKER_MAP) > 40) else 12.0)


async def ath_atl_loop() -> None:
    cursor = 0
    sem = asyncio.Semaphore(3)

    async def process_coin(t: Dict[str, Any], tfs: List[str], now_s: float, lock_ratio: float, cd_sec: float) -> None:
        async with sem:
            for tf in tfs:
                key = f"{t['symbol']}:{tf}"
                rec = ATH_ATL_LEVELS.get(key)
                if not rec or (now_s - rec["syncedAt"]) > 3600:
                    bars = await fetch_spot_klines(t["symbol"], tf, 120 if tf == "1M" else 90)
                    if len(bars) >= 4:
                        cl = bars[:-1]
                        lo = min(b["low"] for b in cl if b["low"] > 0)
                        hi = max(b["high"] for b in cl if b["high"] > 0)
                        prev = rec or {}
                        ATH_ATL_LEVELS[key] = {
                            "atl": min(lo, prev.get("lastAtl", lo)),
                            "ath": max(hi, prev.get("lastAth", hi)),
                            "syncedAt": now_s,
                            "lastAtl": prev.get("lastAtl"),
                            "lastAth": prev.get("lastAth"),
                            "lockedAt": prev.get("lockedAt", 0),
                        }
                        rec = ATH_ATL_LEVELS[key]
                if not rec:
                    continue
                p = t["price"]
                too_soon = (now_s - rec.get("lockedAt", 0)) < cd_sec
                if STRATEGY_CONFIG["atlEnabled"] and rec["atl"] > 0 and p <= rec["atl"] * (1 - lock_ratio if rec.get("lastAtl") else 0.9985) and not (rec.get("lastAtl") and too_soon):
                    prev_atl = rec["atl"]
                    rec["atl"] = rec["lastAtl"] = p
                    rec["lockedAt"] = now_s
                    save_state()
                    await emit_signal({
                        "id": f"ATL-{t['symbol']}-{tf}-{int(now_s*1000)}", "engine": "ATL", "engineLabel": f"{tf} ATL",
                        "symbol": t["symbol"], "base": t["base"], "quote": "USDT", "direction": "SHORT", "timeframe": tf,
                        "entryPrice": p, "currentPrice": p, "peakPrice": p, "peakPct": 0.0,
                        "headline": f"#{t['symbol']} ${fmt_price(p)} [{tf}] En Düşük Kırılımı (ATL)",
                        "details": [f"📉 Önceki {tf} ATL (${fmt_price(prev_atl)}) aşağı kırıldı."],
                        "createdAt": int(now_s * 1000), "peakAt": int(now_s * 1000),
                    })
                elif STRATEGY_CONFIG["athEnabled"] and rec["ath"] > 0 and p >= rec["ath"] * (1 + lock_ratio if rec.get("lastAth") else 1.0015) and not (rec.get("lastAth") and too_soon):
                    prev_ath = rec["ath"]
                    rec["ath"] = rec["lastAth"] = p
                    rec["lockedAt"] = now_s
                    save_state()
                    await emit_signal({
                        "id": f"ATH-{t['symbol']}-{tf}-{int(now_s*1000)}", "engine": "ATH", "engineLabel": f"{tf} ATH",
                        "symbol": t["symbol"], "base": t["base"], "quote": "USDT", "direction": "LONG", "timeframe": tf,
                        "entryPrice": p, "currentPrice": p, "peakPrice": p, "peakPct": 0.0,
                        "headline": f"#{t['symbol']} ${fmt_price(p)} [{tf}] En Yüksek Kırılımı (ATH)",
                        "details": [f"📈 Önceki {tf} ATH (${fmt_price(prev_ath)}) yukarı kırıldı."],
                        "createdAt": int(now_s * 1000), "peakAt": int(now_s * 1000),
                    })

    while True:
        try:
            tfs = STRATEGY_CONFIG["athAtlTimeframes"]
            if BOT_RUNNING and (STRATEGY_CONFIG["athEnabled"] or STRATEGY_CONFIG["atlEnabled"]) and tfs and TICKER_MAP:
                cands = sorted([t for t in TICKER_MAP.values() if t["volume24h"] >= 150000], key=lambda x: x["volume24h"], reverse=True)
                now_s = time.time()
                lock_ratio = STRATEGY_CONFIG["athAtlLockPct"] / 100.0
                cd_sec = STRATEGY_CONFIG["athAtlCooldownMin"] * 60.0
                batch_sz = min(14, len(cands))
                batch = [cands[(cursor + i) % len(cands)] for i in range(batch_sz)]
                cursor += batch_sz
                await asyncio.gather(*(process_coin(t, tfs, now_s, lock_ratio, cd_sec) for t in batch), return_exceptions=True)
        except Exception as e:
            _log(f"[ATH/ATL Loop Hatasi] {e}")
        await asyncio.sleep(15)


async def advanced_strategies_loop() -> None:
    ma_cur, pat_cur, ind_cur = 0, 0, 0
    while True:
        try:
            now_s = time.time()
            # 1) ANORMAL VOLATILITE (15m / 1h - MAJOR COINLER)
            if BOT_RUNNING and STRATEGY_CONFIG["volatilityEnabled"] and STRATEGY_CONFIG["volatilityTimeframes"] and TICKER_MAP:
                if STRATEGY_CONFIG["volatilityMajorOnly"]:
                    majors = {f"{c}USDT" for c in STRATEGY_CONFIG["volatilityMajorCoins"]}
                    v_targets = [t for sym, t in TICKER_MAP.items() if sym in majors]
                else:
                    v_targets = sorted(TICKER_MAP.values(), key=lambda x: x["volume24h"], reverse=True)[:50]
                for t in v_targets:
                    for tf in STRATEGY_CONFIG["volatilityTimeframes"]:
                        ck = f"{t['symbol']}:{tf}"
                        if VOL_COOLDOWN.get(ck, 0) > now_s:
                            continue
                        VOL_COOLDOWN[ck] = now_s + (45 if tf == "15m" else 120)
                        bars = await fetch_spot_klines(t["symbol"], tf, 35)
                        if len(bars) < 22:
                            continue
                        avg_rng = sum((b["high"] - b["low"]) / max(b["open"], 1e-9) * 100 for b in bars[-22:-2]) / 20.0
                        for bar in (bars[-1], bars[-2]):
                            rng_pct = (bar["high"] - bar["low"]) / max(bar["open"], 1e-9) * 100
                            mv_pct = (bar["close"] - bar["open"]) / max(bar["open"], 1e-9) * 100
                            ratio = rng_pct / max(avg_rng, 0.08)
                            if ratio >= STRATEGY_CONFIG["volatilityAtrMult"] and max(abs(mv_pct), rng_pct * 0.72) >= STRATEGY_CONFIG["volatilityMinMovePct"]:
                                dr = "LONG" if mv_pct >= 0 else "SHORT"
                                if (dr == "LONG" and not STRATEGY_CONFIG["volatilityLongEnabled"]) or (dr == "SHORT" and not STRATEGY_CONFIG["volatilityShortEnabled"]):
                                    continue
                                sid = f"VOL-{t['symbol']}-{tf}-{bar['openTime']}-{dr}"
                                png = render_signal_candle_png(f"[{t['symbol']}] ABNORMAL VOLATILITY ALERT ({tf})", bars, t["price"], support=bar["open"], is_short=(dr == "SHORT"))
                                await emit_signal({
                                    "id": sid, "engine": "VOLATILITY", "engineLabel": f"Abnormal Volatility ({tf})",
                                    "symbol": t["symbol"], "base": t["base"], "quote": "USDT", "direction": dr, "timeframe": tf,
                                    "entryPrice": t["price"], "currentPrice": t["price"], "peakPrice": t["price"], "peakPct": 0.0,
                                    "headline": f"[{t['symbol']}] Abnormal Volatility Alert ({tf}) — {dr}",
                                    "details": [f"⚡ Mum Genişliği: %{rng_pct:.2f} ({ratio:.1f}x ort.) | Gövde: {mv_pct:+.2f}%"],
                                    "createdAt": int(now_s * 1000), "peakAt": int(now_s * 1000),
                                }, png)
                                break
                        await asyncio.sleep(0.08)

            # 2) 4 SAATLIK MA200 2-MUM MOMENTUM
            if BOT_RUNNING and STRATEGY_CONFIG["ma200Enabled"] and TICKER_MAP:
                cands = sorted([t for t in TICKER_MAP.values() if t["volume24h"] >= 400000], key=lambda x: x["volume24h"], reverse=True)[:80]
                tf_ma = STRATEGY_CONFIG["ma200Timeframe"]
                per = STRATEGY_CONFIG["ma200Period"]
                for _ in range(min(8, len(cands))):
                    t = cands[ma_cur % len(cands)]
                    ma_cur += 1
                    bars = await fetch_spot_klines(t["symbol"], tf_ma, per + 25)
                    res = evaluate_ma200_bars(bars, per, STRATEGY_CONFIG["ma200LongEnabled"], STRATEGY_CONFIG["ma200ShortEnabled"])
                    if res:
                        dr, ma_val, _, bar_ts = res
                        sid = f"MA200-{t['symbol']}-{tf_ma}-{dr}-{int(bar_ts)}"
                        sma_line = compute_sma([b["close"] for b in bars], per)
                        png = render_signal_candle_png(f"{t['symbol']} | {tf_ma.upper()} MA{per} {dr}", bars, t["price"], support=ma_val, ma_series=sma_line, is_short=(dr == "SHORT"))
                        await emit_signal({
                            "id": sid, "engine": "MA200_4H", "engineLabel": f"{tf_ma.upper()} MA{per} 2-Mum",
                            "symbol": t["symbol"], "base": t["base"], "quote": "USDT", "direction": dr, "timeframe": tf_ma,
                            "entryPrice": t["price"], "currentPrice": t["price"], "peakPrice": t["price"], "peakPct": 0.0,
                            "headline": f"{tf_ma.upper()} MA{per} {'ÜZERİ 2 YEŞİL MUM LONG' if dr == 'LONG' else 'ALTI 2 KIRMIZI MUM SHORT'} — #{t['symbol']}",
                            "details": [f"📊 MA({per}): ${fmt_price(ma_val)} · Anlık: ${fmt_price(t['price'])}"],
                            "createdAt": int(now_s * 1000), "peakAt": int(now_s * 1000),
                        }, png)
                    await asyncio.sleep(0.08)

            # 3) 7 FORMASYON MOTORU
            if BOT_RUNNING and STRATEGY_CONFIG["patternsEnabled"] and STRATEGY_CONFIG["patternTimeframes"] and TICKER_MAP:
                cands = sorted([t for t in TICKER_MAP.values() if t["volume24h"] >= 500000], key=lambda x: x["volume24h"], reverse=True)[:55]
                for _ in range(min(6, len(cands))):
                    t = cands[pat_cur % len(cands)]
                    pat_cur += 1
                    for tf in STRATEGY_CONFIG["patternTimeframes"]:
                        bars = await fetch_spot_klines(t["symbol"], tf, 80)
                        hit = detect_patterns_py(bars, STRATEGY_CONFIG["patternMinHeightPct"], STRATEGY_CONFIG["enabledPatterns"])
                        if not hit:
                            continue
                        lk = f"{t['symbol']}:{hit['key']}:{tf}"
                        if lk in LOCKS["patterns"] and (now_s - LOCKS["patterns"][lk]["lockedAt"]) < 4 * 3600:
                            continue
                        LOCKS["patterns"][lk] = {"key": lk, "symbol": t["symbol"], "label": PATTERN_LABELS[hit["key"]], "lockedAt": now_s}
                        save_state()
                        lbl = PATTERN_LABELS[hit["key"]]
                        png = render_signal_candle_png(f"{t['symbol']} | {tf} {lbl.split('(')[0].strip()}", bars, t["price"], support=hit["support"], target=hit["target"], is_short=(hit["dir"] == "SHORT"))
                        await emit_signal({
                            "id": f"PAT-{hit['key']}-{t['symbol']}-{tf}-{int(now_s*1000)}", "engine": "PATTERN", "engineLabel": f"{lbl} ({tf})",
                            "symbol": t["symbol"], "base": t["base"], "quote": "USDT", "direction": hit["dir"], "timeframe": tf,
                            "entryPrice": t["price"], "currentPrice": t["price"], "peakPrice": t["price"], "peakPct": 0.0,
                            "headline": f"{lbl} ({tf}) — #{t['symbol']} [{hit['dir']}]",
                            "details": [f"🎯 Hedef: ${fmt_price(hit['target'])} | Destek/Boyun: ${fmt_price(hit['support'])}"],
                            "createdAt": int(now_s * 1000), "peakAt": int(now_s * 1000),
                        }, png)
                        await asyncio.sleep(0.08)

            # 4) BOLLINGER, ICHIMOKU VE SUPERTREND (COKLU ZAMAN DILIMI)
            needed_tfs: List[str] = []
            for key_on, key_tfs in [
                ("bollingerEnabled", "bollingerTimeframes"),
                ("ichimokuEnabled", "ichimokuTimeframes"),
                ("supertrendEnabled", "supertrendTimeframes"),
            ]:
                if STRATEGY_CONFIG.get(key_on):
                    for tf in STRATEGY_CONFIG.get(key_tfs, []):
                        if tf not in needed_tfs:
                            needed_tfs.append(tf)

            if BOT_RUNNING and needed_tfs and TICKER_MAP:
                cands = sorted([t for t in TICKER_MAP.values() if t["volume24h"] >= 400000], key=lambda x: x["volume24h"], reverse=True)[:55]
                for _ in range(min(6, len(cands))):
                    t = cands[ind_cur % len(cands)]
                    ind_cur += 1
                    for tf in needed_tfs:
                        ck = f"{t['symbol']}:{tf}"
                        if IND_COOLDOWN.get(ck, 0) > now_s:
                            continue
                        IND_COOLDOWN[ck] = now_s + (90 if tf == "15m" else 180)
                        bars = await fetch_spot_klines(t["symbol"], tf, 120)
                        if len(bars) < 30:
                            continue

                        if STRATEGY_CONFIG.get("bollingerEnabled") and tf in STRATEGY_CONFIG.get("bollingerTimeframes", []):
                            bb = evaluate_bollinger_py(
                                bars,
                                int(STRATEGY_CONFIG.get("bollingerPeriod", 20)),
                                float(STRATEGY_CONFIG.get("bollingerStdDev", 2.0)),
                                bool(STRATEGY_CONFIG.get("bollingerLongEnabled", True)),
                                bool(STRATEGY_CONFIG.get("bollingerShortEnabled", True)),
                            )
                            if bb:
                                sid = f"BB-{t['symbol']}-{tf}-{bb['dir']}-{int(bb['openTime'])}"
                                png = render_signal_candle_png(
                                    f"{t['symbol']} | {tf.upper()} BOLLINGER {bb['dir']}",
                                    bars,
                                    t["price"],
                                    support=bb["lower"] if bb["dir"] == "LONG" else bb["upper"],
                                    target=bb["upper"] if bb["dir"] == "LONG" else bb["lower"],
                                    ma_series=bb["midSeries"],
                                    is_short=(bb["dir"] == "SHORT"),
                                )
                                await emit_signal({
                                    "id": sid, "engine": "BOLLINGER", "engineLabel": f"Bollinger ({tf})",
                                    "symbol": t["symbol"], "base": t["base"], "quote": "USDT", "direction": bb["dir"], "timeframe": tf,
                                    "entryPrice": t["price"], "currentPrice": t["price"], "peakPrice": t["price"], "peakPct": 0.0,
                                    "headline": f"Bollinger ({tf}) {bb['reason']} — #{t['symbol']} [{bb['dir']}]",
                                    "details": [f"📊 Üst: ${fmt_price(bb['upper'])} | Orta: ${fmt_price(bb['mid'])} | Alt: ${fmt_price(bb['lower'])}"],
                                    "createdAt": int(now_s * 1000), "peakAt": int(now_s * 1000),
                                }, png)

                        if STRATEGY_CONFIG.get("ichimokuEnabled") and tf in STRATEGY_CONFIG.get("ichimokuTimeframes", []):
                            ich = evaluate_ichimoku_py(
                                bars,
                                bool(STRATEGY_CONFIG.get("ichimokuLongEnabled", True)),
                                bool(STRATEGY_CONFIG.get("ichimokuShortEnabled", True)),
                            )
                            if ich:
                                sid = f"ICH-{t['symbol']}-{tf}-{ich['dir']}-{int(ich['openTime'])}"
                                png = render_signal_candle_png(
                                    f"{t['symbol']} | {tf.upper()} ICHIMOKU {ich['dir']}",
                                    bars,
                                    t["price"],
                                    support=ich["bot"],
                                    target=ich["top"],
                                    is_short=(ich["dir"] == "SHORT"),
                                )
                                await emit_signal({
                                    "id": sid, "engine": "ICHIMOKU", "engineLabel": f"Ichimoku ({tf})",
                                    "symbol": t["symbol"], "base": t["base"], "quote": "USDT", "direction": ich["dir"], "timeframe": tf,
                                    "entryPrice": t["price"], "currentPrice": t["price"], "peakPrice": t["price"], "peakPct": 0.0,
                                    "headline": f"Ichimoku ({tf}) {ich['reason']} — #{t['symbol']} [{ich['dir']}]",
                                    "details": [f"☁️ Kumo: ${fmt_price(ich['bot'])} - ${fmt_price(ich['top'])} · Tenkan: ${fmt_price(ich['tenkan'])} | Kijun: ${fmt_price(ich['kijun'])}"],
                                    "createdAt": int(now_s * 1000), "peakAt": int(now_s * 1000),
                                }, png)

                        if STRATEGY_CONFIG.get("supertrendEnabled") and tf in STRATEGY_CONFIG.get("supertrendTimeframes", []):
                            st = evaluate_supertrend_py(
                                bars,
                                int(STRATEGY_CONFIG.get("supertrendAtrPeriod", 10)),
                                float(STRATEGY_CONFIG.get("supertrendMultiplier", 3.0)),
                                bool(STRATEGY_CONFIG.get("supertrendLongEnabled", True)),
                                bool(STRATEGY_CONFIG.get("supertrendShortEnabled", True)),
                            )
                            if st:
                                sid = f"ST-{t['symbol']}-{tf}-{st['dir']}-{int(st['openTime'])}"
                                png = render_signal_candle_png(
                                    f"{t['symbol']} | {tf.upper()} SUPERTREND {st['dir']}",
                                    bars,
                                    t["price"],
                                    support=st["supertrend"],
                                    ma_series=st["stSeries"],
                                    is_short=(st["dir"] == "SHORT"),
                                )
                                await emit_signal({
                                    "id": sid, "engine": "SUPERTREND", "engineLabel": f"Supertrend ({tf})",
                                    "symbol": t["symbol"], "base": t["base"], "quote": "USDT", "direction": st["dir"], "timeframe": tf,
                                    "entryPrice": t["price"], "currentPrice": t["price"], "peakPrice": t["price"], "peakPct": 0.0,
                                    "headline": f"Supertrend ({tf}) Trend Dönüşü — #{t['symbol']} [{st['dir']}]",
                                    "details": [f"⚡ Supertrend: ${fmt_price(st['supertrend'])} · Anlık: ${fmt_price(t['price'])}"],
                                    "createdAt": int(now_s * 1000), "peakAt": int(now_s * 1000),
                                }, png)
                        await asyncio.sleep(0.08)
        except Exception as e:
            _log(f"[Gelismis Strateji Hatasi] {e}")
        await asyncio.sleep(25)


async def multi_exchange_loop() -> None:
    async def fetch_ex_prices(name: str, url: str) -> Tuple[str, Dict[str, float]]:
        out: Dict[str, float] = {}
        try:
            st, raw = await http_get_json(url, timeout_s=8.0)
            if st != 200:
                return name, out
            if name == "MEXC" and isinstance(raw, list):
                for x in raw:
                    sym = str(x.get("symbol") or "")
                    p = float(x.get("lastPrice") or 0)
                    if sym.endswith("USDT") and p > 0 and float(x.get("quoteVolume") or 0) >= 100000:
                        out[sym] = p
            elif name == "Bybit" and isinstance(raw, dict):
                for x in (raw.get("result") or {}).get("list") or []:
                    sym = str(x.get("symbol") or "")
                    p = float(x.get("lastPrice") or 0)
                    if sym.endswith("USDT") and p > 0 and float(x.get("turnover24h") or 0) >= 100000:
                        out[sym] = p
            elif name == "Gate" and isinstance(raw, list):
                for x in raw:
                    pair = str(x.get("currency_pair") or "")
                    p = float(x.get("last") or 0)
                    if pair.endswith("_USDT") and p > 0 and float(x.get("quote_volume") or 0) >= 100000:
                        out[pair.replace("_USDT", "USDT")] = p
            elif name == "OKX" and isinstance(raw, dict):
                for x in raw.get("data") or []:
                    inst = str(x.get("instId") or "")
                    p = float(x.get("last") or 0)
                    if inst.endswith("-USDT") and p > 0 and float(x.get("volCcy24h") or 0) >= 100000:
                        out[inst.replace("-USDT", "USDT")] = p
            elif name == "KuCoin" and isinstance(raw, dict):
                for x in (raw.get("data") or {}).get("ticker") or []:
                    sym = str(x.get("symbol") or "")
                    p = float(x.get("last") or 0)
                    if sym.endswith("-USDT") and p > 0 and float(x.get("volValue") or 0) >= 100000:
                        out[sym.replace("-USDT", "USDT")] = p
        except Exception:
            pass
        return name, out

    while True:
        try:
            if BOT_RUNNING and STRATEGY_CONFIG.get("multiExchangeEnabled"):
                now_s = time.time()
                win_s = max(60.0, float(STRATEGY_CONFIG.get("multiExchangeWindowMin", 5)) * 60.0)
                min_ex = max(2, int(STRATEGY_CONFIG.get("multiExchangeMinExchanges", 3)))
                thresh = max(0.5, float(STRATEGY_CONFIG.get("multiExchangeThresholdPct", 2.5)))
                dir_mode = str(STRATEGY_CONFIG.get("multiExchangeDirection", "BOTH")).upper()
                allow_long = dir_mode != "SHORT"
                allow_short = dir_mode != "LONG"

                feeds: List[Tuple[str, Dict[str, float]]] = []
                bin_map = {sym: t["price"] for sym, t in TICKER_MAP.items() if t.get("price", 0) > 0 and t.get("volume24h", 0) >= 150000}
                if bin_map:
                    feeds.append(("Binance", bin_map))
                fetched = await asyncio.gather(
                    fetch_ex_prices("MEXC", "https://api.mexc.com/api/v3/ticker/24hr"),
                    fetch_ex_prices("Bybit", "https://api.bybit.com/v5/market/tickers?category=spot"),
                    fetch_ex_prices("Gate", "https://api.gateio.ws/api/v4/spot/tickers"),
                    fetch_ex_prices("OKX", "https://www.okx.com/api/v5/market/tickers?instType=SPOT"),
                    fetch_ex_prices("KuCoin", "https://api.kucoin.com/api/v1/market/allTickers"),
                )
                for ex_name, ex_map in fetched:
                    if len(ex_map) > 20:
                        feeds.append((ex_name, ex_map))

                for ex_name, ex_map in feeds:
                    for sym, p in ex_map.items():
                        s_hist = MULTI_EX_HIST.setdefault(sym, {})
                        dq = s_hist.setdefault(ex_name, deque(maxlen=120))
                        dq.append((now_s, p))
                        while dq and (now_s - dq[0][0]) > win_s + 30.0:
                            dq.popleft()

                for sym, s_hist in list(MULTI_EX_HIST.items()):
                    if len(s_hist) < min_ex:
                        continue
                    up_hits: List[Tuple[str, float, float]] = []
                    dn_hits: List[Tuple[str, float, float]] = []
                    for ex_name, dq in s_hist.items():
                        if len(dq) < 2 or (now_s - dq[-1][0]) > 45.0 or dq[0][1] <= 0:
                            continue
                        pct = ((dq[-1][1] - dq[0][1]) / dq[0][1]) * 100.0
                        if thresh <= pct <= 60.0:
                            up_hits.append((ex_name, pct, dq[-1][1]))
                        elif -60.0 <= pct <= -thresh:
                            dn_hits.append((ex_name, pct, dq[-1][1]))

                    for dr, hits in (("LONG", up_hits), ("SHORT", dn_hits)):
                        if (dr == "LONG" and not allow_long) or (dr == "SHORT" and not allow_short):
                            continue
                        if len(hits) < min_ex:
                            continue
                        ck = f"{sym}:{dr}"
                        if MULTI_EX_COOLDOWN.get(ck, 0) > now_s:
                            continue
                        MULTI_EX_COOLDOWN[ck] = now_s + 900.0
                        avg_pct = sum(h[1] for h in hits) / len(hits)
                        avg_p = sum(h[2] for h in hits) / len(hits)
                        ref_p = (TICKER_MAP.get(sym) or {}).get("price") or avg_p
                        ex_txt = " | ".join(f"{h[0]}: {h[1]:+.2f}%" for h in sorted(hits, key=lambda x: abs(x[1]), reverse=True))
                        await emit_signal({
                            "id": f"MX-{sym}-{dr}-{int(now_s*1000)}",
                            "engine": "MULTI_EXCHANGE",
                            "engineLabel": f"Tüm Borsalar ({len(hits)}/{len(feeds)})",
                            "symbol": sym, "base": sym[:-4], "quote": "USDT", "direction": dr,
                            "entryPrice": ref_p, "currentPrice": ref_p, "peakPrice": ref_p, "peakPct": 0.0,
                            "headline": f"🌐 TÜM BORSALARDA ANİ {'YÜKSELİŞ' if dr == 'LONG' else 'DÜŞÜŞ'} ({len(hits)}/{len(feeds)} Borsa) — #{sym}",
                            "details": [
                                f"📊 {int(STRATEGY_CONFIG.get('multiExchangeWindowMin', 5))} dk Ort. Hareket: {avg_pct:+.2f}% · Anlık: ${fmt_price(ref_p)}",
                                f"🏦 Eş Zamanlı Borsalar: {ex_txt}",
                            ],
                            "createdAt": int(now_s * 1000), "peakAt": int(now_s * 1000),
                        })
        except Exception as e:
            _log(f"[Tum Borsalar Loop Hatasi] {e}")
        await asyncio.sleep(20)


async def run_quant_ai_fallback(dir_mode: str, min_conf: int) -> Tuple[str, List[Dict[str, Any]]]:
    usdt = [t for t in TICKER_MAP.values() if t.get("volume24h", 0) >= 250000 and t.get("price", 0) > 0]
    liq = sorted(usdt, key=lambda x: x["volume24h"], reverse=True)[:35]
    bull_c = sum(1 for t in liq if t.get("change24h", 0) > 1.0 and t.get("window7mPct", 0) >= 0)
    bear_c = sum(1 for t in liq if t.get("change24h", 0) < -1.0 and t.get("window7mPct", 0) <= 0)
    regime = "Yükseliş Eğilimli" if bull_c >= max(12, int(len(liq) * 0.55)) else ("Satış Baskılı" if bear_c >= max(12, int(len(liq) * 0.55)) else "Dengeli & Seçici")

    long_pool = sorted(usdt, key=lambda x: x.get("window7mPct", 0) * 2.5 + x.get("change24h", 0) * 0.35 + (2.0 if x["symbol"] in FUTURES_SYMBOLS else 0.0), reverse=True)[:5]
    short_pool = sorted(usdt, key=lambda x: -x.get("window7mPct", 0) * 2.5 - x.get("change24h", 0) * 0.35 + (2.0 if x["symbol"] in FUTURES_SYMBOLS else 0.0), reverse=True)[:5]
    pool = long_pool if dir_mode == "LONG" else (short_pool if dir_mode == "SHORT" else long_pool + short_pool)
    seen_s: Set[str] = set()
    cands: List[Dict[str, Any]] = []
    for item in pool:
        if item["symbol"] not in seen_s:
            seen_s.add(item["symbol"])
            cands.append(item)

    evaluated: List[Dict[str, Any]] = []
    for tk in cands[:8]:
        rsi14 = 50.0
        ema9 = tk["price"]
        ema21 = tk["price"]
        try:
            bars = await fetch_spot_klines(tk["symbol"], "15m", 35)
            if len(bars) >= 22:
                closes = [b["close"] for b in bars]
                rsi14 = compute_rsi(closes, 14)[-1]
                ema9 = compute_ema(closes, 9)[-1]
                ema21 = compute_ema(closes, 21)[-1]
        except Exception:
            pass
        w7 = float(tk.get("window7mPct", 0) or 0)
        c24 = float(tk.get("change24h", 0) or 0)
        has_fut = tk["symbol"] in FUTURES_SYMBOLS
        if dir_mode in ("LONG", "BOTH"):
            sc = 54.0 + (min(16.0, w7 * 4.5) if w7 > 0.3 else 0.0) + (8.0 if ema9 > ema21 else 0.0) + (8.0 if 48 <= rsi14 <= 74 else 0.0) + (5.0 if has_fut else 0.0)
            conf = min(93, max(54, int(round(sc))))
            evaluated.append({
                "symbol": tk["symbol"], "direction": "LONG", "confidence": conf,
                "reason": f"15dk RSI {rsi14:.0f} · {'EMA9>EMA21 pozitif' if ema9 >= ema21 else 'tepki'} · 7dk {w7:+.2f}% · 24s {c24:+.1f}%",
            })
        if dir_mode in ("SHORT", "BOTH"):
            sc = 54.0 + (min(16.0, abs(w7) * 4.5) if w7 < -0.3 else 0.0) + (8.0 if ema9 < ema21 else 0.0) + (8.0 if 26 <= rsi14 <= 52 else 0.0) + (5.0 if has_fut else 0.0)
            conf = min(93, max(54, int(round(sc))))
            evaluated.append({
                "symbol": tk["symbol"], "direction": "SHORT", "confidence": conf,
                "reason": f"15dk RSI {rsi14:.0f} · {'EMA9<EMA21 satış baskısı' if ema9 <= ema21 else 'tepe dönüşü'} · 7dk {w7:+.2f}% · 24s {c24:+.1f}%",
            })

    evaluated.sort(key=lambda x: x["confidence"], reverse=True)
    picked_syms: Set[str] = set()
    picks: List[Dict[str, Any]] = []
    for item in evaluated:
        if item["symbol"] in picked_syms:
            continue
        picked_syms.add(item["symbol"])
        picks.append(item)
        if len(picks) >= 3:
            break
    return regime, picks


async def ai_market_loop() -> None:
    while True:
        try:
            interval_s = max(60.0, float(STRATEGY_CONFIG.get("aiMarketIntervalMin", 15)) * 60.0)
            if BOT_RUNNING and STRATEGY_CONFIG.get("aiMarketEnabled") and len(TICKER_MAP) >= 15:
                dir_mode = str(STRATEGY_CONFIG.get("aiMarketDirection", "BOTH")).upper()
                min_conf = int(STRATEGY_CONFIG.get("aiMarketMinConfidence", 72))
                now_s = time.time()
                gemini_used = False
                regime = "Canlı Analiz"
                picks: List[Dict[str, Any]] = []

                if GEMINI_API_KEY:
                    usdt = [t for t in TICKER_MAP.values() if t.get("volume24h", 0) >= 250000]
                    top_v = sorted(usdt, key=lambda x: x["volume24h"], reverse=True)[:12]
                    top_g = sorted(usdt, key=lambda x: x["change24h"], reverse=True)[:12]
                    top_d = sorted(usdt, key=lambda x: x["change24h"])[:10]
                    seen: Set[str] = set()
                    sample: List[Dict[str, Any]] = []
                    for lst in (top_v, top_g, top_d):
                        for it in lst:
                            if it["symbol"] not in seen:
                                seen.add(it["symbol"])
                                sample.append(it)
                    lines = [
                        f"{t['symbol']}|p={fmt_price(t['price'])}|24s={t['change24h']:+.2f}%|hacim=${int(t['volume24h']/1000)}K"
                        for t in sample[:30]
                    ]
                    prompt = (
                        f"Canli kripto piyasasini analiz et ve su an en olumlu en fazla 3 islem sec. Yon={dir_mode}, MinGuven={min_conf}.\n"
                        'Yalnizca JSON dondur: {"marketRegime":"...","summary":"...","picks":[{"symbol":"BTCUSDT","direction":"LONG","confidence":82,"reason":"..."}]}\n'
                        + "\n".join(lines)
                    )
                    body = json.dumps({
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"responseMimeType": "application/json"},
                    }).encode("utf-8")

                    for model_name in GEMINI_MODELS:
                        if GEMINI_MODEL_BLOCKED_UNTIL.get(model_name, 0.0) > now_s:
                            continue
                        st, raw = await http_request(
                            "POST",
                            f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent",
                            headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY},
                            body=body,
                            timeout_s=25.0,
                        )
                        js = _safe_json_loads(raw)
                        if st == 200 and isinstance(js, dict):
                            txt = (((js.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}])[0].get("text") or "{}"
                            parsed = json.loads(txt)
                            regime = str(parsed.get("marketRegime") or "Canlı Analiz")
                            picks = list(parsed.get("picks") or [])
                            gemini_used = True
                            break
                        wait_s = 1800.0
                        m_delay = re.search(r'"retryDelay"\s*:\s*"(\d+)s"', raw)
                        if m_delay:
                            wait_s = max(60.0, min(86400.0, float(m_delay.group(1))))
                        GEMINI_MODEL_BLOCKED_UNTIL[model_name] = now_s + (wait_s if st == 429 else 300.0)
                        _log(f"[AI Market {model_name}] HTTP {st} — yedek modele/teknik motora geçiliyor (bekleme {int(wait_s//60)}dk).")

                engine_tag = "Gemini AI" if gemini_used else "Teknik AI Yedek"
                if not gemini_used:
                    regime, picks = await run_quant_ai_fallback(dir_mode, min_conf)

                for pk in picks:
                    sym = str(pk.get("symbol") or "").upper().strip()
                    dr = str(pk.get("direction") or "").upper().strip()
                    conf = int(pk.get("confidence") or 0)
                    if dr not in ("LONG", "SHORT") or conf < min_conf or sym not in TICKER_MAP:
                        continue
                    if (dir_mode == "LONG" and dr != "LONG") or (dir_mode == "SHORT" and dr != "SHORT"):
                        continue
                    ck = f"{sym}:{dr}"
                    if AI_COOLDOWN.get(ck, 0) > now_s:
                        continue
                    AI_COOLDOWN[ck] = now_s + 900.0
                    tk = TICKER_MAP[sym]
                    await emit_signal({
                        "id": f"AI-{sym}-{dr}-{int(now_s*1000)}",
                        "engine": "AI_MARKET",
                        "engineLabel": f"{engine_tag} ({conf}%)",
                        "symbol": sym, "base": tk["base"], "quote": "USDT", "direction": dr,
                        "entryPrice": tk["price"], "currentPrice": tk["price"], "peakPrice": tk["price"], "peakPct": 0.0,
                        "headline": f"🤖 YAPAY ZEKA {dr} (%{conf}) — #{sym}",
                        "details": [
                            f"🧠 Rejim: {regime} · Güven: %{conf} ({engine_tag})",
                            f"💡 Analiz: {pk.get('reason', 'Piyasa & momentum analizi')}",
                            f"💰 Anlık Fiyat: ${fmt_price(tk['price'])} (24s: {tk['change24h']:+.2f}%)",
                        ],
                        "createdAt": int(now_s * 1000), "peakAt": int(now_s * 1000),
                    })
            await asyncio.sleep(interval_s)
        except Exception as e:
            _log(f"[AI Piyasa Loop Hatasi] {e}")
            await asyncio.sleep(60)


async def web_config_sync_loop() -> None:
    if not WEB_SYNC_URL:
        return
    while True:
        try:
            status, data = await http_get_json(WEB_SYNC_URL, timeout_s=8.0)
            if status == 200 and isinstance(data, dict):
                if isinstance(data.get("strategyConfig"), dict):
                    apply_strategy_patch(data["strategyConfig"])
                if isinstance(data.get("autoTrade"), dict):
                    apply_autotrade_patch(data["autoTrade"])
                save_state()
        except Exception:
            pass
        await asyncio.sleep(15)


async def reports_loop() -> None:
    global LAST_VICTORY_DATE_TR, LAST_SUMMARY_DATE_TR, LAST_HOURLY_KEY
    while True:
        try:
            tr_now = datetime.now(TR_TZ)
            date_s, hour, hour_key = tr_now.strftime("%Y-%m-%d"), tr_now.hour, tr_now.strftime("%Y-%m-%d-%H")
            if STRATEGY_CONFIG["hourlyGainersReportEnabled"] and len(TICKER_MAP) > 20 and LAST_HOURLY_KEY != hour_key:
                LAST_HOURLY_KEY = hour_key
                save_state()
                top = sorted([t for t in TICKER_MAP.values() if t["change24h"] > 0 and t["volume24h"] >= 150000], key=lambda x: x["change24h"], reverse=True)[:8]
                if top:
                    cap = f"📈 <b>24 SAATİN EN YÜKSEKLERİ — SAATLİK ÖZET ({tr_now.strftime('%H:%M')} TR)</b>\n\n" + "\n".join(
                        f"{i+1}. <b>#{esc_html(t['base'])}</b> · ${fmt_price(t['price'])} · <b>+%{t['change24h']:.2f}</b>" for i, t in enumerate(top)
                    )
                    await tg_send_photo(render_hourly_gainers_png(top, tr_now.strftime("%H:%M TR")), cap)

            if STRATEGY_CONFIG["dailyWinnerReportEnabled"] and hour == 21 and LAST_VICTORY_DATE_TR != date_s:
                LAST_VICTORY_DATE_TR = date_s
                save_state()
                top1 = sorted([t for t in TICKER_MAP.values() if t["change24h"] > 0 and t["volume24h"] >= 150000], key=lambda x: x["change24h"], reverse=True)
                if top1:
                    w = top1[0]
                    bars1d = await fetch_spot_klines(w["symbol"], "1d", 45)
                    png = render_signal_candle_png(f"{w['symbol']} | 1G GUNUN KAZANANI", bars1d, w["price"], support=w["low24h"])
                    await tg_send_photo(png, f"🏆 <b>GÜNÜN KAZANANI: {esc_html(w['symbol'])} (+{w['change24h']:.1f}%)</b>")

            if STRATEGY_CONFIG["top24SummaryReportEnabled"] and hour == 23 and LAST_SUMMARY_DATE_TR != date_s:
                LAST_SUMMARY_DATE_TR = date_s
                save_state()
                cutoff = int((time.time() - 86400) * 1000)
                day_sigs = [s for s in SIGNALS if s["createdAt"] >= cutoff and s["engine"] in ("PUMP", "PUMP15M")]
                if day_sigs:
                    ranked = sorted(day_sigs, key=lambda x: x["peakPct"], reverse=True)
                    h2 = round(sum(1 for s in day_sigs if s["peakPct"] >= 2.0) / len(day_sigs) * 100)
                    await tg_send_photo(render_top24_png(ranked, len(day_sigs), h2), "🔥 <b>24 Saatin TOP Hareketleri</b>")
        except Exception as e:
            _log(f"[Rapor Loop Hatasi] {e}")
        await asyncio.sleep(60)


async def telegram_startup_check() -> None:
    global TG_BOT_USERNAME, TG_OK, LAST_TG_ERROR
    _log(
        f"[BOOT] BOT_TOKEN={'Var (' + str(len(BOT_TOKEN)) + ' krk)' if BOT_TOKEN else 'YOK!'} | "
        f"Hedef Chatler={list(SUBSCRIBED_CHATS) or 'YOK!'} | Port={PORT}"
    )
    if not BOT_TOKEN:
        LAST_TG_ERROR = "BOT_TOKEN environment degiskeni tanimli degil"
        _log("[TG UYARI] Render -> Environment icinde BOT_TOKEN ve CHAT_ID tanimlayin, yoksa Telegram'a mesaj gitmez.")
        return
    try:
        st, me = await http_post_json(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", {}, timeout_s=10.0)
        if st < 400 and isinstance(me, dict) and me.get("ok"):
            TG_BOT_USERNAME = str((me.get("result") or {}).get("username") or "")
            TG_OK = True
            LAST_TG_ERROR = ""
            _log(f"[Telegram] Baglandi -> @{TG_BOT_USERNAME} | Hedefler: {', '.join(SUBSCRIBED_CHATS)}")
            await http_post_json(
                f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook",
                {"drop_pending_updates": False},
                timeout_s=10.0,
            )
            await tg_send_text(
                f"⚡ <b>7/24 Spot &amp; Futures Botu Aktif (Render)</b>\n"
                f"• Bot: @{esc_html(TG_BOT_USERNAME)} | Futures: {AUTO_TRADE['leverage']}x (${AUTO_TRADE['tradeUsdt']})\n"
                f"• Komutlar: /test · /durum · /saatlik · /24s · /sifirla"
            )
        else:
            desc = str(me.get("description") or f"HTTP {st}") if isinstance(me, dict) else f"HTTP {st}"
            LAST_TG_ERROR = f"getMe HTTP {st}: {desc}"
            TG_OK = False
            _log(f"[TG HATA] Bot token gecersiz veya erisilemedi: {LAST_TG_ERROR}")
    except Exception as e:
        LAST_TG_ERROR = f"getMe istisna: {e}"
        TG_OK = False
        _log(f"[TG BAGLANTI HATASI] {LAST_TG_ERROR}")


async def telegram_command_loop() -> None:
    global BOT_RUNNING, STRATEGY_CONFIG, AUTO_TRADE, LAST_TG_ERROR
    if not BOT_TOKEN:
        return
    offset = 0
    conflict_logged = False
    while True:
        try:
            status, js = await http_post_json(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                {"offset": offset, "timeout": 25},
                timeout_s=32.0,
            )
            if status == 409:
                desc = str(js.get("description") or "Conflict 409") if isinstance(js, dict) else "Conflict 409"
                LAST_TG_ERROR = f"getUpdates 409: {desc} (Ayni BOT_TOKEN baska yerde acik olabilir)"
                if not conflict_logged:
                    conflict_logged = True
                    _log(f"[TG 409 UYARI] {LAST_TG_ERROR}")
                await asyncio.sleep(15)
                continue
            if status >= 400 or not isinstance(js, dict) or not js.get("ok"):
                desc = str(js.get("description") or f"HTTP {status}") if isinstance(js, dict) else f"HTTP {status}"
                LAST_TG_ERROR = f"getUpdates HTTP {status}: {desc}"
                await asyncio.sleep(5)
                continue
            conflict_logged = False
            for upd in js.get("result", []):
                offset = max(offset, int(upd["update_id"]) + 1)
                msg = upd.get("message") or {}
                txt = str(msg.get("text") or "").strip()
                cid = str((msg.get("chat") or {}).get("id") or "")
                if not txt or not cid:
                    continue
                if cid not in SUBSCRIBED_CHATS:
                    SUBSCRIBED_CHATS.add(cid)
                    save_state()
                parts = txt.split()
                cmd = parts[0].split("@")[0].lower()
                if cmd in ("/start", "/help"):
                    await tg_send_text(
                        "⚡ <b>7/24 Spot &amp; Futures Botu Aktif</b>\n"
                        f"🆔 Sohbet ID: <code>{esc_html(cid)}</code>\n"
                        "Komutlar: /test · /durum · /saatlik · /24s · /islemler · /ayar_sync &lt;json&gt; · /tik &lt;motor&gt; ac|kapat · /varsayilan · /sifirla",
                        cid,
                    )
                elif cmd == "/test":
                    await tg_send_text(
                        f"🧪 <b>TEST BİLDİRİMİ — Render Telegram Aktif!</b>\n"
                        f"• Taranan Spot Çift: {len(TICKER_MAP)} · Üretilen Sinyal: {len(SIGNALS)}\n"
                        f"• RSI(14) Eşiği: ≥{AUTO_TRADE['rsiMin']} · Gönderilen Mesaj: {TG_SENT_COUNT}",
                        cid,
                    )
                elif cmd in ("/saatlik", "/yukselenler"):
                    top = sorted([t for t in TICKER_MAP.values() if t["change24h"] > 0 and t["volume24h"] >= 150000], key=lambda x: x["change24h"], reverse=True)[:8]
                    if not top:
                        await tg_send_text("⏳ Binance verisi henüz yükleniyor, birkaç saniye sonra tekrar deneyin.", cid)
                    else:
                        tr_now = datetime.now(TR_TZ)
                        cap = f"📈 <b>24 SAATİN EN YÜKSEKLERİ ({tr_now.strftime('%H:%M')} TR)</b>\n\n" + "\n".join(
                            f"{i+1}. <b>#{esc_html(t['base'])}</b> · ${fmt_price(t['price'])} · <b>+%{t['change24h']:.2f}</b>" for i, t in enumerate(top)
                        )
                        await tg_send_photo(render_hourly_gainers_png(top, tr_now.strftime("%H:%M TR")), cap, cid)
                elif cmd in ("/24s", "/piyasa"):
                    top = sorted([t for t in TICKER_MAP.values() if t["change24h"] > 0], key=lambda x: x["change24h"], reverse=True)[:15]
                    if not top:
                        await tg_send_text("⏳ Piyasa verisi yükleniyor...", cid)
                    else:
                        lines = ["📈 <b>24 SAATİN EN ÇOK YÜKSELENLERİ</b>\n"] + [
                            f"{i+1:02d}. <b>{esc_html(t['symbol'])}</b> · ${fmt_price(t['price'])} · <b>+%{t['change24h']:.2f}</b>" for i, t in enumerate(top)
                        ]
                        await tg_send_text("\n".join(lines), cid)
                elif cmd in ("/islemler", "/bakiye"):
                    open_tr = [t for t in TRADES if t["status"] == "OPEN"]
                    lines = [
                        f"🤖 <b>FUTURES {AUTO_TRADE['leverage']}x ({AUTO_TRADE['mode']})</b> · Bakiye: ${PAPER_BALANCE_USDT:.2f} · Açık: {len(open_tr)}"
                    ]
                    for tr in open_tr[:10]:
                        lines.append(f"• #{esc_html(tr['symbol'])} ({tr['direction']}) · Giriş ${fmt_price(tr['entryPrice'])}")
                    await tg_send_text("\n".join(lines), cid)
                elif cmd == "/durum":
                    await tg_send_text(
                        f"📊 <b>Bot Durumu</b> ({'🟢 Aktif' if BOT_RUNNING else '🔴 Durdu'})\n"
                        f"• Veri Kaynağı: {ACTIVE_FEED_SOURCE} | Çift: {len(TICKER_MAP)} | Sinyal: {len(SIGNALS)} | TG: {TG_SENT_COUNT}\n"
                        f"• Pump: {STRATEGY_CONFIG['pumpEnabled']} | 15m: {STRATEGY_CONFIG['pump15mEnabled']} | RSI≥{AUTO_TRADE['rsiMin']}\n"
                        f"• ATL/ATH ({','.join(STRATEGY_CONFIG['athAtlTimeframes'])}): {STRATEGY_CONFIG['atlEnabled']}/{STRATEGY_CONFIG['athEnabled']}\n"
                        f"• MA200: {STRATEGY_CONFIG['ma200Enabled']} | Vol: {STRATEGY_CONFIG['volatilityEnabled']} | Formasyon: {STRATEGY_CONFIG['patternsEnabled']}\n"
                        f"• BB ({','.join(STRATEGY_CONFIG.get('bollingerTimeframes', []))}): {STRATEGY_CONFIG.get('bollingerEnabled')} | Ichi ({','.join(STRATEGY_CONFIG.get('ichimokuTimeframes', []))}): {STRATEGY_CONFIG.get('ichimokuEnabled')} | ST ({','.join(STRATEGY_CONFIG.get('supertrendTimeframes', []))}): {STRATEGY_CONFIG.get('supertrendEnabled')}\n"
                        f"• AI: {STRATEGY_CONFIG.get('aiMarketEnabled')} ({STRATEGY_CONFIG.get('aiMarketDirection', 'BOTH')}) | TumBorsalar: {STRATEGY_CONFIG.get('multiExchangeEnabled')} ({STRATEGY_CONFIG.get('multiExchangeDirection', 'BOTH')}) | BigPumps: {STRATEGY_CONFIG.get('bigPumpEnabled')} ({','.join(STRATEGY_CONFIG.get('bigPumpExchanges', []))})"
                        + (f"\n• Son TG Hata: {esc_html(LAST_TG_ERROR)}" if LAST_TG_ERROR else ""),
                        cid,
                    )
                elif cmd == "/ayar_sync":
                    raw_j = txt[len(parts[0]):].strip()
                    try:
                        parsed = json.loads(raw_j)
                        apply_strategy_patch(parsed.get("strategyConfig", parsed))
                        if isinstance(parsed.get("autoTrade"), dict):
                            apply_autotrade_patch(parsed["autoTrade"])
                        save_state()
                        await tg_send_text("✅ Tüm Web ayarları ve tikler bota senkronize edildi!", cid)
                    except Exception:
                        await tg_send_text("❌ Geçersiz /ayar_sync JSON verisi.", cid)
                elif cmd == "/tik" and len(parts) >= 3:
                    key, val = parts[1].upper(), parts[2].upper() in ("AC", "ON", "TRUE", "1")
                    m = {
                        "PUMP": "pumpEnabled", "PUMP15M": "pump15mEnabled",
                        "ATH": "athEnabled", "ATL": "atlEnabled",
                        "MA200": "ma200Enabled", "VOL": "volatilityEnabled", "PATTERN": "patternsEnabled",
                        "BB": "bollingerEnabled", "BOLLINGER": "bollingerEnabled",
                        "ICHI": "ichimokuEnabled", "ICHIMOKU": "ichimokuEnabled",
                        "ST": "supertrendEnabled", "SUPERTREND": "supertrendEnabled",
                        "AI": "aiMarketEnabled", "AI_MARKET": "aiMarketEnabled",
                        "BORSA": "multiExchangeEnabled", "MULTI_EXCHANGE": "multiExchangeEnabled",
                        "BIGPUMP": "bigPumpEnabled", "BIG_PUMP": "bigPumpEnabled",
                    }
                    if key in m:
                        STRATEGY_CONFIG[m[key]] = val
                        save_state()
                        await tg_send_text(f"☑️ <b>{key}</b>: {'Açık ✓' if val else 'Kapalı ✗'}", cid)
                elif cmd == "/varsayilan":
                    STRATEGY_CONFIG = copy.deepcopy(DEFAULT_STRATEGY_CONFIG)
                    AUTO_TRADE = copy.deepcopy(DEFAULT_AUTO_TRADE)
                    save_state()
                    await tg_send_text("♻️ Bot ilk kurulu ayarlarına döndürüldü.", cid)
                elif cmd == "/sifirla":
                    LOCKS["pump"].clear()
                    LOCKS["pump15m"].clear()
                    LOCKS["patterns"].clear()
                    save_state()
                    await tg_send_text("🔓 Kilitler sıfırlandı.", cid)
        except Exception as e:
            LAST_TG_ERROR = f"getUpdates loop: {e}"
            await asyncio.sleep(4)


async def refresh_symbols_loop() -> None:
    fapi_warned = False
    while True:
        try:
            try:
                sp = await fetch_binance_json("/api/v3/exchangeInfo")
                if isinstance(sp, dict) and sp.get("symbols"):
                    ACTIVE_SPOT_SYMBOLS.clear()
                    for s in sp.get("symbols", []):
                        if s.get("status") == "TRADING" and s.get("isSpotTradingAllowed", True):
                            sym = s["symbol"]
                            ACTIVE_SPOT_SYMBOLS.add(sym)
                            s_info = {"stepSize": "0.001", "minQty": 0.0, "tickSize": "0.0001", "minNotional": 5.0}
                            for f in s.get("filters", []):
                                if f.get("filterType") == "LOT_SIZE":
                                    s_info["stepSize"] = f.get("stepSize", "0.001")
                                    s_info["minQty"] = float(f.get("minQty") or 0)
                                elif f.get("filterType") == "PRICE_FILTER":
                                    s_info["tickSize"] = f.get("tickSize", "0.0001")
                            SPOT_FILTERS[sym] = s_info
            except Exception:
                pass
            for fbase in ("https://fapi.binance.com", "https://fapi1.binance.com", "https://fapi2.binance.com"):
                f_status, fp = await http_get_json(f"{fbase}/fapi/v1/exchangeInfo", timeout_s=10.0)
                if f_status == 200 and isinstance(fp, dict) and fp.get("symbols"):
                    for s in fp.get("symbols", []):
                        if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT" and s.get("contractType") == "PERPETUAL":
                            info = {"stepSize": "0.001", "minQty": 0.0, "tickSize": "0.0001", "minNotional": 5.0}
                            for f in s.get("filters", []):
                                if f.get("filterType") == "LOT_SIZE":
                                    info["stepSize"] = f.get("stepSize", "0.001")
                                    info["minQty"] = float(f.get("minQty") or 0)
                                elif f.get("filterType") == "PRICE_FILTER":
                                    info["tickSize"] = f.get("tickSize", "0.0001")
                                elif f.get("filterType") == "MIN_NOTIONAL":
                                    info["minNotional"] = float(f.get("notional") or 5)
                            FUTURES_FILTERS[s["symbol"]] = info
                    break
            else:
                if not fapi_warned:
                    fapi_warned = True
                    _log("[Futures Info] fapi.binance.com erisimi kisitli (PAPER modunda Spot filtreleri kullanilacak).")
        except Exception as e:
            _log(f"[Sembol Yenileme Hatasi] {e}")
        await asyncio.sleep(900)


async def heartbeat_loop() -> None:
    while True:
        await asyncio.sleep(60)
        open_n = sum(1 for t in TRADES if t["status"] == "OPEN")
        _log(
            f"[DURUM] Kaynak={ACTIVE_FEED_SOURCE} | Ciftler={len(TICKER_MAP)} | Sinyaller={len(SIGNALS)} | "
            f"Futures={'ACIK' if AUTO_TRADE['enabled'] else 'KAPALI'}({AUTO_TRADE['mode']}:{open_n}) | "
            f"TG={'@' + TG_BOT_USERNAME if TG_BOT_USERNAME else ('Aktif' if BOT_TOKEN else 'Kapali')} "
            f"(Gonderilen={TG_SENT_COUNT}) | Chatler={len(SUBSCRIBED_CHATS)}"
            + (f" | SonHata={LAST_TG_ERROR}" if LAST_TG_ERROR else "")
        )


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/health"):
            payload = json.dumps({
                "ok": True,
                "botRunning": BOT_RUNNING,
                "feedSource": ACTIVE_FEED_SOURCE,
                "wsConnected": WS_CONNECTED,
                "telegramEnabled": bool(BOT_TOKEN),
                "tgOk": TG_OK,
                "tgBotUsername": TG_BOT_USERNAME,
                "tgSentCount": TG_SENT_COUNT,
                "lastTgError": LAST_TG_ERROR,
                "subscribedChatsCount": len(SUBSCRIBED_CHATS),
                "pairs": len(TICKER_MAP),
                "signalsCount": len(SIGNALS),
                "futuresPairs": len(FUTURES_FILTERS),
                "strategyConfig": STRATEGY_CONFIG,
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        body = b"OK - Spot & Futures 7/24 Bot Running"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def start_health_server(port: int = PORT) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


async def main() -> None:
    load_state()
    start_health_server(PORT)
    await telegram_startup_check()
    await asyncio.gather(
        binance_ws_loop(),
        refresh_symbols_loop(),
        refresh_exchange_memberships_loop(),
        market_loop(),
        ath_atl_loop(),
        advanced_strategies_loop(),
        multi_exchange_loop(),
        ai_market_loop(),
        web_config_sync_loop(),
        reports_loop(),
        telegram_command_loop(),
        heartbeat_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())

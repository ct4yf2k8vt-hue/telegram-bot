# ==============================================================================
# SPOT AKIS TERMINALI - 7/24 RENDER BOT.PY (WEB AYARLARIYLA OTOMATIK URETILDI)
# Uretim: 14:41:41 · Rev #3
# Aktif Motorlar: Pump(%3.5) | 15dk(%3.5) | ATH/ATL(1M) | 4H MA200(L:1/S:1) | Volatilite(15m,1h, Major)
# Futures: 3x | $25 | TP +%10 / SL -%10 | RSI >= 90
# ==============================================================================
import asyncio
import copy
import hashlib
import hmac
import json
import os
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

# Web panelinden secilen guncel tikler ve parametreler (GitHub bot.py ile birebir degistirin):
DEFAULT_STRATEGY_CONFIG: Dict[str, Any] = {
    "pumpEnabled": True,
    "pumpThresholdPct": 3.5,
    "pumpMinVolUsdt": 150000,
    "pumpRepeatHours": 2,
    "pump15mEnabled": True,
    "pump15mThresholdPct": 3.5,
    "pump15mMinRvol": 1.4,
    "athEnabled": True,
    "atlEnabled": True,
    "athAtlTimeframes": ["1M"],
    "athAtlLockPct": 0.5,
    "athAtlCooldownMin": 5,
    "ma200Enabled": True,
    "ma200LongEnabled": True,
    "ma200ShortEnabled": True,
    "ma200Timeframe": "4h",
    "ma200Period": 200,
    "volatilityEnabled": True,
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
    "patternTimeframes": [],
    "enabledPatterns": {
        "DOUBLE_BOTTOM": False,
        "DOUBLE_TOP": False,
        "HEAD_SHOULDERS": False,
        "INV_HEAD_SHOULDERS": False,
        "BULL_FLAG": False,
        "BEAR_FLAG": False,
        "BEAR_RECTANGLE": False,
    },
    "patternMinHeightPct": 1.2,
    "hourlyGainersReportEnabled": True,
    "dailyWinnerReportEnabled": True,
    "top24SummaryReportEnabled": True,
    "autoTradeEngines": {
        "PUMP": True,
        "PUMP15M": True,
        "ATL": False,
        "ATH": False,
        "MA200_4H": False,
        "VOLATILITY": False,
        "PATTERN": False,
    },
}

STRATEGY_CONFIG: Dict[str, Any] = copy.deepcopy(DEFAULT_STRATEGY_CONFIG)

DEFAULT_AUTO_TRADE: Dict[str, Any] = {
    "enabled": True,
    "mode": "PAPER",
    "leverage": 3,
    "tradeUsdt": 25,
    "tpPct": 10,
    "slPct": 10,
    "maxOpenTrades": 0,
    "rsiMin": 90,
    "notifyPaperTelegram": False,
}

AUTO_TRADE: Dict[str, Any] = copy.deepcopy(DEFAULT_AUTO_TRADE)

# ==============================================================================
# ORTAM DEGISKENLERI (RENDER ENVIRONMENT VARIABLES)
# ==============================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "1623907197").strip()
PORT = int(os.getenv("PORT", "10000"))

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()
BINANCE_KEYS_READY = bool(BINANCE_API_KEY and BINANCE_API_SECRET)

if os.getenv("AUTO_TRADE_ENABLED"):
    AUTO_TRADE["enabled"] = os.getenv("AUTO_TRADE_ENABLED", "").lower() in ("1", "true", "yes", "on")
if os.getenv("AUTO_TRADE_MODE"):
    AUTO_TRADE["mode"] = os.getenv("AUTO_TRADE_MODE", "PAPER").upper()
if AUTO_TRADE["mode"] in ("LIVE", "TESTNET") and not BINANCE_KEYS_READY:
    AUTO_TRADE["mode"] = "PAPER"

# Istege bagli canli web senkronizasyonu (orn. https://.../api/bot-config)
WEB_SYNC_URL = os.getenv("WEB_SYNC_URL", "").strip()

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
TRADE_INFLIGHT: Set[str] = set()
SUBSCRIBED_CHATS: Set[str] = {CHAT_ID} if CHAT_ID else set()

LAST_VICTORY_DATE_TR = ""
LAST_SUMMARY_DATE_TR = ""
LAST_HOURLY_KEY = ""
PREFERRED_MIRROR_IDX = 0


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
        "patternsEnabled", "hourlyGainersReportEnabled", "dailyWinnerReportEnabled", "top24SummaryReportEnabled",
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
    }
    for nk, (lo, hi) in num_bounds.items():
        if nk in raw:
            try:
                v = float(raw[nk])
                STRATEGY_CONFIG[nk] = max(lo, min(hi, v))
            except (TypeError, ValueError):
                pass

    if "ma200Period" in raw:
        try:
            STRATEGY_CONFIG["ma200Period"] = max(20, min(400, int(raw["ma200Period"])))
        except (TypeError, ValueError):
            pass

    if raw.get("ma200Timeframe") in ("1h", "4h", "1d"):
        STRATEGY_CONFIG["ma200Timeframe"] = raw["ma200Timeframe"]

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

    if isinstance(raw.get("enabledPatterns"), dict):
        for pk in PATTERN_LABELS:
            if isinstance(raw["enabledPatterns"].get(pk), bool):
                STRATEGY_CONFIG["enabledPatterns"][pk] = raw["enabledPatterns"][pk]

    if isinstance(raw.get("autoTradeEngines"), dict):
        for ek in ("PUMP", "PUMP15M", "ATL", "ATH", "MA200_4H", "VOLATILITY", "PATTERN"):
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
    return await loop.run_in_executor(None, lambda: _http_request_sync(method, url, headers, body, timeout_s))


async def http_get_json(url: str, timeout_s: float = 10.0) -> Tuple[int, Any]:
    status, raw = await http_request("GET", url, timeout_s=timeout_s)
    data = json.loads(raw.decode("utf-8")) if raw else {}
    return status, data


async def http_post_json(url: str, payload: Dict[str, Any], timeout_s: float = 12.0) -> Tuple[int, Any]:
    raw_body = json.dumps(payload).encode("utf-8")
    status, raw = await http_request(
        "POST",
        url,
        headers={"Content-Type": "application/json"},
        body=raw_body,
        timeout_s=timeout_s,
    )
    data = json.loads(raw.decode("utf-8")) if raw else {}
    return status, data


async def fetch_binance_json(path: str, timeout_s: float = 9.0) -> Any:
    global PREFERRED_MIRROR_IDX
    n = len(BINANCE_MIRRORS)
    last_err: Any = None
    for off in range(n):
        idx = (PREFERRED_MIRROR_IDX + off) % n
        url = f"{BINANCE_MIRRORS[idx]}{path}"
        try:
            status, data = await http_get_json(url, timeout_s=timeout_s)
            if status == 200:
                PREFERRED_MIRROR_IDX = idx
                return data
            last_err = f"HTTP {status}"
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Binance erisilemedi: {last_err}")


async def fetch_spot_klines(symbol: str, interval: str, limit: int = 120) -> List[Dict[str, float]]:
    raw = await fetch_binance_json(f"/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}")
    if not isinstance(raw, list):
        return []
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


async def tg_send_text(html_text: str, explicit_cid: Optional[str] = None) -> None:
    if not BOT_TOKEN:
        return
    targets = [explicit_cid] if explicit_cid else list(SUBSCRIBED_CHATS)
    for cid in targets:
        try:
            await http_post_json(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                {"chat_id": cid, "text": html_text, "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout_s=12.0,
            )
        except Exception:
            pass


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


async def tg_send_photo(png: bytes, caption: str, explicit_cid: Optional[str] = None) -> None:
    if not BOT_TOKEN:
        return
    if not png:
        await tg_send_text(caption, explicit_cid)
        return
    targets = [explicit_cid] if explicit_cid else list(SUBSCRIBED_CHATS)
    safe_cap = caption[:995] + "..." if len(caption) > 1000 else caption
    for cid in targets:
        try:
            body, ctype = build_telegram_photo_multipart(cid, safe_cap, png)
            status, raw = await http_request(
                "POST",
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                headers={"Content-Type": ctype},
                body=body,
                timeout_s=18.0,
            )
            js = json.loads(raw.decode("utf-8")) if raw else {}
            if status >= 400 or not js.get("ok"):
                await tg_send_text(caption, cid)
        except Exception:
            await tg_send_text(caption, cid)


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
    if not BOT_RUNNING or not AUTO_TRADE["enabled"] or signal["quote"] != "USDT":
        return
    if not STRATEGY_CONFIG["autoTradeEngines"].get(signal["engine"], False):
        return
    sym = signal["symbol"]
    if sym in TRADE_INFLIGHT:
        return
    open_tr = [t for t in TRADES if t["status"] == "OPEN"]
    if AUTO_TRADE["maxOpenTrades"] > 0 and len(open_tr) >= AUTO_TRADE["maxOpenTrades"]:
        return
    if any(t["symbol"] == sym or t["base"] == signal["base"] for t in open_tr):
        return
    flt = FUTURES_FILTERS.get(sym)
    if not flt:
        return

    dir_side = "SHORT" if signal.get("direction") == "SHORT" else "LONG"
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

    icons = {"ATL": "🕳", "ATH": "🏔", "PUMP15M": "🚀", "VOLATILITY": "⚠️"}
    ic = icons.get(signal["engine"], "🟢" if signal["direction"] == "LONG" else "🔴")
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
        tk_raw, c1m, c15m = await asyncio.gather(
            fetch_binance_json(f"/api/v3/ticker/24hr?symbol={sym}"),
            fetch_spot_klines(sym, "1m", 50),
            fetch_spot_klines(sym, "15m", 50),
        )
        live_cur = float(tk_raw.get("lastPrice") or t["price"])
        live_vol = float(tk_raw.get("quoteVolume") or t["volume24h"])

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
    except Exception:
        pass
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


# ==============================================================================
# ARKA PLAN DONGULERI (PIYASA, MA200, VOLATILITE, FORMASYON, ATL/ATH, SYNC)
# ==============================================================================
async def market_loop() -> None:
    while True:
        try:
            raw = await fetch_binance_json("/api/v3/ticker/24hr")
            now_s = time.time()
            for item in raw:
                sym = str(item.get("symbol", ""))
                if not sym.endswith("USDT") or (ACTIVE_SPOT_SYMBOLS and sym not in ACTIVE_SPOT_SYMBOLS):
                    continue
                price = float(item.get("lastPrice") or 0)
                vol = float(item.get("quoteVolume") or 0)
                if price <= 0 or float(item.get("bidPrice") or 0) <= 0:
                    continue
                t = {
                    "symbol": sym, "base": sym[:-4], "quote": "USDT",
                    "price": price, "change24h": float(item.get("priceChangePercent") or 0),
                    "volume24h": vol, "high24h": float(item.get("highPrice") or price), "low24h": float(item.get("lowPrice") or price),
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
                        asyncio.create_task(verify_and_emit_pump(t, min7, min15))
        except Exception:
            pass
        await asyncio.sleep(3.5)


async def ath_atl_loop() -> None:
    cursor = 0
    while True:
        try:
            tfs = STRATEGY_CONFIG["athAtlTimeframes"]
            if BOT_RUNNING and (STRATEGY_CONFIG["athEnabled"] or STRATEGY_CONFIG["atlEnabled"]) and tfs and TICKER_MAP:
                cands = sorted([t for t in TICKER_MAP.values() if t["volume24h"] >= 150000], key=lambda x: x["volume24h"], reverse=True)
                now_s = time.time()
                lock_ratio = STRATEGY_CONFIG["athAtlLockPct"] / 100.0
                cd_sec = STRATEGY_CONFIG["athAtlCooldownMin"] * 60.0
                for _ in range(min(15, len(cands))):
                    t = cands[cursor % len(cands)]
                    cursor += 1
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
                    await asyncio.sleep(0.1)
        except Exception:
            pass
        await asyncio.sleep(10)


async def advanced_strategies_loop() -> None:
    ma_cur, pat_cur = 0, 0
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
                for _ in range(min(10, len(cands))):
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
                for _ in range(min(8, len(cands))):
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
        except Exception:
            pass
        await asyncio.sleep(25)


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
        except Exception:
            pass
        await asyncio.sleep(60)


async def telegram_command_loop() -> None:
    global BOT_RUNNING, STRATEGY_CONFIG, AUTO_TRADE
    if not BOT_TOKEN:
        return
    offset = 0
    while True:
        try:
            status, js = await http_post_json(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                {"offset": offset, "timeout": 25},
                timeout_s=32.0,
            )
            if status >= 400 or not isinstance(js, dict) or not js.get("ok"):
                await asyncio.sleep(5)
                continue
            for upd in js.get("result", []):
                offset = max(offset, int(upd["update_id"]) + 1)
                msg = upd.get("message") or {}
                txt = str(msg.get("text") or "").strip()
                cid = str((msg.get("chat") or {}).get("id") or "")
                if not txt or not cid:
                    continue
                SUBSCRIBED_CHATS.add(cid)
                parts = txt.split()
                cmd = parts[0].split("@")[0].lower()
                if cmd in ("/start", "/help"):
                    await tg_send_text("⚡ <b>7/24 Spot & Futures Botu Aktif</b>\nKomutlar: /durum · /ayar_sync <json> · /tik <motor> ac|kapat · /varsayilan · /sifirla", cid)
                elif cmd == "/durum":
                    await tg_send_text(
                        f"📊 <b>Bot Durumu</b> ({'🟢 Aktif' if BOT_RUNNING else '🔴 Durdu'})\n"
                        f"• Pump: {STRATEGY_CONFIG['pumpEnabled']} | 15m: {STRATEGY_CONFIG['pump15mEnabled']} | RSI≥{AUTO_TRADE['rsiMin']}\n"
                        f"• ATL/ATH ({','.join(STRATEGY_CONFIG['athAtlTimeframes'])}): {STRATEGY_CONFIG['atlEnabled']}/{STRATEGY_CONFIG['athEnabled']}\n"
                        f"• 4s MA200: {STRATEGY_CONFIG['ma200Enabled']} | Vol ({','.join(STRATEGY_CONFIG['volatilityTimeframes'])}): {STRATEGY_CONFIG['volatilityEnabled']} | Formasyon: {STRATEGY_CONFIG['patternsEnabled']}",
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
                    m = {"PUMP": "pumpEnabled", "PUMP15M": "pump15mEnabled", "ATH": "athEnabled", "ATL": "atlEnabled", "MA200": "ma200Enabled", "VOL": "volatilityEnabled", "PATTERN": "patternsEnabled"}
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
        except Exception:
            await asyncio.sleep(4)


async def refresh_symbols_loop() -> None:
    while True:
        try:
            sp = await fetch_binance_json("/api/v3/exchangeInfo")
            ACTIVE_SPOT_SYMBOLS.clear()
            for s in sp.get("symbols", []):
                if s.get("status") == "TRADING" and s.get("isSpotTradingAllowed", True):
                    ACTIVE_SPOT_SYMBOLS.add(s["symbol"])
            f_status, fp = await http_get_json("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout_s=12.0)
            if f_status == 200 and isinstance(fp, dict):
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
        except Exception:
            pass
        await asyncio.sleep(900)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/health"):
            payload = json.dumps({"ok": True, "pairs": len(TICKER_MAP), "strategyConfig": STRATEGY_CONFIG}).encode("utf-8")
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
    await asyncio.gather(
        refresh_symbols_loop(),
        market_loop(),
        ath_atl_loop(),
        advanced_strategies_loop(),
        web_config_sync_loop(),
        reports_loop(),
        telegram_command_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())

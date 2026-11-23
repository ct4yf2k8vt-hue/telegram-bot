#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Binance Spot Pump, 15dk Kirilim & ATL/ATH Dedektoru + Futures Oto Trade Botu (7/24 Render Edition)
- 0-7 dk Ani Pump (+4.0%+) & 15 dk Hacimli Kirilim (+3.2%+ · RVOL >= 1.4x)
  -> HER IKISI DE RSI(14) >= RSI_MIN (varsayilan 70) + Kirilim sartini birlikte arar.
- ATL / ATH Motoru: 1M + yeni listelemeler icin 1d fallback, canli tik taramasi ve >= %1 / 5 dk kilit.
- Oto Futures: Yalnizca 0-7 dk & 15 dk sinyallerinde 3x Izole LONG (+%10 Kar Al / -%10 Stop Loss).
  -> Maksimum acik pozisyon varsayilan 0 (Sinirsiz).
  -> PAPER modunda Telegram'a acilis/kapanis mesaji GONDERMEZ (NOTIFY_PAPER_TELEGRAM=False).
- Telegram Gorsel Raporlari (Saf Python PNG - harici Pillow/matplotlib gerektirmez):
  -> Her 1 saatte bir (ve /saatlik, /yukselenler): 24 Saatin En Yuksekleri (Gainers PNG - IMG_3165)
  -> Her gun 21:00 TR (ve /kazanan): Gunun Kazanani 1G Canli Mum Grafigi (IMG_3147)
  -> Her gun 23:00 TR (ve /top24): TOP 24 Saat Hareketleri ozeti (IMG_3145)
- /ayar komutu ile tp, sl, max, rsi, usdt, lev, mod, paper_tg parametreleri anlik degistirilebilir.
"""

import hashlib
import hmac
import json
import math
import os
import struct
import threading
import time
import urllib.parse
import zlib
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional, Tuple

import requests
import websocket

# ==============================================================================
# YAPILANDIRMA (ENV UZERINDEN OZELLESTIRILEBILIR)
# ==============================================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "1623907197").strip()
PORT = int(os.environ.get("PORT", "10000"))

BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "").strip()
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "").strip()

BINANCE_REST_BASES = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://data-api.binance.vision",
]
FAPI_BASE = "https://fapi.binance.com"
FAPI_TESTNET_BASE = "https://testnet.binancefuture.com"

# Guvenlik: Yalnizca Futures emir ve kaldirac endpointlerine izin verilir (para cekme kesinlikle yasak)
ALLOWED_FUTURES_ENDPOINTS = {
    "/fapi/v1/marginType",
    "/fapi/v1/leverage",
    "/fapi/v1/order",
}

TR_TZ = timezone(timedelta(hours=3))

BLACKLIST_BASES = {
    "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "USDE", "EUR", "EURI",
    "TRY", "USDT", "PAXG", "XAUT", "WBTC", "WBETH", "STETH", "BETH", "AEUR",
    "RLUSD", "USD1", "U", "USDS", "BFUSD",
}

# Varsayilan strateji ve oto-trade ayarlari
CONFIG: Dict[str, Any] = {
    "running": True,
    "auto_trade_enabled": os.environ.get("AUTO_TRADE_ENABLED", "1") != "0",
    "trade_mode": os.environ.get("TRADE_MODE", "PAPER").upper(),  # PAPER | TESTNET | LIVE
    "leverage": int(os.environ.get("LEVERAGE", "3")),
    "trade_usdt": float(os.environ.get("TRADE_USDT", "25")),
    "tp_pct": float(os.environ.get("TP_PCT", "10")),
    "sl_pct": float(os.environ.get("SL_PCT", "10")),
    "max_open_trades": int(os.environ.get("MAX_OPEN_TRADES", "0")),  # 0 = Sinirsiz
    "rsi_min": float(os.environ.get("RSI_MIN", "70")),
    "notify_paper_telegram": os.environ.get("NOTIFY_PAPER_TELEGRAM", "0") == "1",
    "paper_balance_usdt": float(os.environ.get("PAPER_BALANCE_USDT", "10000")),
    "pump_threshold_pct": float(os.environ.get("PUMP_THRESHOLD_PCT", "4.0")),
    "pump15_threshold_pct": float(os.environ.get("PUMP15_THRESHOLD_PCT", "3.2")),
    "pump15_min_rvol": float(os.environ.get("PUMP15_MIN_RVOL", "1.4")),
    "min_vol_usdt": float(os.environ.get("MIN_VOL_USDT", "300000")),
}

STATE_FILE = os.environ.get("BOT_STATE_FILE", "/tmp/spot_pump_bot_state.json")
STATE_LOCK = threading.RLock()

# ==============================================================================
# CANLI DURUM DEPOLARI
# ==============================================================================
TICKER_MAP: Dict[str, Dict[str, Any]] = {}
HISTORY_MAP: Dict[str, List[Tuple[float, float]]] = {}  # sym -> [(ts, price)]
PREV_PRICES: Dict[str, float] = {}

SIGNALS: List[Dict[str, Any]] = []  # son 150 sinyal
TRADES: List[Dict[str, Any]] = []   # acik + son kapanan islemler

PUMP_LOCKS: Dict[str, Dict[str, Any]] = {}      # base -> {symbol, locked_at}
PUMP15M_LOCKS: Dict[str, Dict[str, Any]] = {}   # base -> {symbol, locked_at}
ATH_ATL_LOCKS: Dict[str, Dict[str, Any]] = {}   # f"{sym}:{kind}" -> {level_price, locked_at}
ATH_ATL_CACHE: Dict[str, Dict[str, Any]] = {}   # sym -> {ath, atl, source, updated_at}
STRUCTURE_VERIFY_AT: Dict[str, float] = {}

FUTURES_FILTERS: Dict[str, Dict[str, float]] = {}  # sym -> {step_size, tick_size, min_qty}
LAST_SIGNAL_EVAL_AT: Dict[str, float] = {}

REPORT_SENT = {
    "hourly_key": "",
    "winner_date": "",
    "top24_date": "",
}
BOT_STATS = {
    "started_at": time.time(),
    "last_tick_at": 0.0,
    "pump_evals": 0,
    "ws_connected": False,
}

# ==============================================================================
# YARDIMCI FONKSIYONLAR
# ==============================================================================
def fmt_price(p: float) -> str:
    if p <= 0 or not math.isfinite(p):
        return "$0"
    if p >= 1000:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:.4f}".rstrip("0").rstrip(".")
    if p >= 0.01:
        return f"${p:.5f}".rstrip("0").rstrip(".")
    return f"${p:.8f}".rstrip("0").rstrip(".")


def fmt_price_raw(p: float) -> str:
    return fmt_price(p).lstrip("$")


def escape_html(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def compute_rsi_series(closes: List[float], period: int = 14) -> List[float]:
    """Wilder RSI(14) serisi dondurur; en az period+1 mum gerektirir."""
    n = len(closes)
    if n == 0:
        return []
    out = [50.0] * n
    if n <= period:
        return out

    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses += -diff

    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = (100.0 if avg_gain > 0 else 50.0) if avg_loss == 0 else (100.0 - 100.0 / (1.0 + avg_gain / avg_loss))

    for i in range(period + 1, n):
        diff = closes[i] - closes[i - 1]
        g = diff if diff > 0 else 0.0
        l = -diff if diff < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        out[i] = (100.0 if avg_gain > 0 else 50.0) if avg_loss == 0 else (100.0 - 100.0 / (1.0 + avg_gain / avg_loss))

    return out


def save_state() -> None:
    try:
        with STATE_LOCK:
            payload = {
                "config": CONFIG,
                "pump_locks": PUMP_LOCKS,
                "pump15m_locks": PUMP15M_LOCKS,
                "ath_atl_locks": ATH_ATL_LOCKS,
                "signals": SIGNALS[:120],
                "trades": TRADES[:150],
                "report_sent": REPORT_SENT,
            }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        pass


def load_state() -> None:
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        with STATE_LOCK:
            cfg = data.get("config") or {}
            if "rsi_min" in cfg:
                for k, v in cfg.items():
                    if k in CONFIG:
                        CONFIG[k] = v
            PUMP_LOCKS.update(data.get("pump_locks") or {})
            PUMP15M_LOCKS.update(data.get("pump15m_locks") or {})
            ATH_ATL_LOCKS.update(data.get("ath_atl_locks") or {})
            SIGNALS[:] = (data.get("signals") or [])[:120]
            TRADES[:] = (data.get("trades") or [])[:150]
            REPORT_SENT.update(data.get("report_sent") or {})
    except Exception:
        pass


# ==============================================================================
# BINANCE REST & FUTURES YARDIMCILARI
# ==============================================================================
HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update({"User-Agent": "SpotPumpTerminalBot/2.0"})


def binance_spot_get(path: str, params: Optional[Dict[str, Any]] = None, timeout: float = 8.0) -> Any:
    last_err: Optional[Exception] = None
    for base in BINANCE_REST_BASES:
        try:
            r = HTTP_SESSION.get(f"{base}{path}", params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            last_err = e
    if last_err:
        raise last_err
    raise RuntimeError(f"Binance GET failed: {path}")


def fetch_spot_klines(symbol: str, interval: str, limit: int = 50) -> List[Dict[str, float]]:
    raw = binance_spot_get(
        "/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=8.0,
    )
    bars: List[Dict[str, float]] = []
    for k in raw:
        bars.append({
            "open_time": float(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "quote_vol": float(k[7]),
        })
    return bars


def load_futures_exchange_info() -> None:
    try:
        r = HTTP_SESSION.get(f"{FAPI_BASE}/fapi/v1/exchangeInfo", timeout=10.0)
        if r.status_code != 200:
            return
        data = r.json()
        parsed: Dict[str, Dict[str, float]] = {}
        for s in data.get("symbols", []):
            if s.get("contractType") != "PERPETUAL" or s.get("quoteAsset") != "USDT" or s.get("status") != "TRADING":
                continue
            step_size = 0.001
            tick_size = 0.0001
            min_qty = 0.001
            for flt in s.get("filters", []):
                if flt.get("filterType") == "LOT_SIZE":
                    step_size = float(flt.get("stepSize") or 0.001)
                    min_qty = float(flt.get("minQty") or 0.001)
                elif flt.get("filterType") == "PRICE_FILTER":
                    tick_size = float(flt.get("tickSize") or 0.0001)
            parsed[s["symbol"]] = {
                "step_size": step_size,
                "tick_size": tick_size,
                "min_qty": min_qty,
            }
        if parsed:
            with STATE_LOCK:
                FUTURES_FILTERS.clear()
                FUTURES_FILTERS.update(parsed)
    except Exception:
        pass


def round_step(val: float, step: float) -> float:
    if step <= 0:
        return val
    prec = max(0, int(round(-math.log10(step))))
    floored = math.floor(val / step + 1e-9) * step
    return round(floored, prec)


def binance_futures_signed_post(endpoint: str, params: Dict[str, Any], testnet: bool = False) -> Any:
    if endpoint not in ALLOWED_FUTURES_ENDPOINTS:
        raise PermissionError(f"Blocked endpoint: {endpoint}")
    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        raise RuntimeError("BINANCE_API_KEY / BINANCE_API_SECRET tanimli degil")

    base = FAPI_TESTNET_BASE if testnet else FAPI_BASE
    p = dict(params)
    p["timestamp"] = int(time.time() * 1000)
    p["recvWindow"] = 5000
    qs = urllib.parse.urlencode(p)
    sig = hmac.new(BINANCE_API_SECRET.encode("utf-8"), qs.encode("utf-8"), hashlib.sha256).hexdigest()
    url = f"{base}{endpoint}?{qs}&signature={sig}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    r = HTTP_SESSION.post(url, headers=headers, timeout=10.0)
    data = r.json() if r.content else {}
    if r.status_code >= 400:
        raise RuntimeError(f"Futures API {r.status_code}: {data}")
    return data


# ==============================================================================
# SAF PYTHON BITMAP FONT + PNG RENDERER (IMG_3147, IMG_3165, IMG_3145)
# ==============================================================================
FONT_5X7: Dict[str, List[str]] = {
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01111", "10000", "10000", "10000", "10000", "10000", "01111"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01111", "10000", "10000", "10111", "10001", "10001", "01111"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    "J": ["00111", "00010", "00010", "00010", "10010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    "0": ["01110", "10011", "10101", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00110", "01000", "10000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["01110", "10000", "11110", "10001", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00001", "01110"],
    "+": ["00000", "00100", "00100", "11111", "00100", "00100", "00000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    ",": ["00000", "00000", "00000", "00000", "00100", "00100", "01000"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
    "/": ["00001", "00010", "00010", "00100", "01000", "01000", "10000"],
    "|": ["00100", "00100", "00100", "00100", "00100", "00100", "00100"],
    "%": ["11001", "11010", "00010", "00100", "01000", "01011", "10011"],
    "$": ["00100", "01111", "10100", "01110", "00101", "11110", "00100"],
    "(": ["00010", "00100", "01000", "01000", "01000", "00100", "00010"],
    ")": ["01000", "00100", "00010", "00010", "00010", "00100", "01000"],
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
}


class PixelCanvas:
    def __init__(self, width: int, height: int, bg: Tuple[int, int, int]) -> None:
        self.w = width
        self.h = height
        self.buf = bytearray(width * height * 3)
        r, g, b = bg
        row = bytes([r, g, b]) * width
        for y in range(height):
            start = y * width * 3
            self.buf[start : start + width * 3] = row

    def set_px(self, x: int, y: int, rgb: Tuple[int, int, int]) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.buf[i] = rgb[0]
            self.buf[i + 1] = rgb[1]
            self.buf[i + 2] = rgb[2]

    def fill_rect(self, x: int, y: int, rw: int, rh: int, rgb: Tuple[int, int, int]) -> None:
        x0 = max(0, int(x))
        y0 = max(0, int(y))
        x1 = min(self.w, int(x + rw))
        y1 = min(self.h, int(y + rh))
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes([rgb[0], rgb[1], rgb[2]]) * (x1 - x0)
        for yy in range(y0, y1):
            start = (yy * self.w + x0) * 3
            self.buf[start : start + len(row)] = row

    def draw_circle(self, cx: int, cy: int, radius: int, rgb: Tuple[int, int, int]) -> None:
        r2 = radius * radius
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx * dx + dy * dy <= r2:
                    self.set_px(cx + dx, cy + dy, rgb)

    def draw_dashed_hline(self, x0: int, x1: int, y: int, rgb: Tuple[int, int, int], dash: int = 6, gap: int = 5) -> None:
        if y < 0 or y >= self.h:
            return
        x = max(0, x0)
        end = min(self.w - 1, x1)
        while x <= end:
            self.fill_rect(x, y, min(dash, end - x + 1), 1, rgb)
            x += dash + gap

    def draw_line(self, x0: float, y0: float, x1: float, y1: float, rgb: Tuple[int, int, int], thickness: int = 2) -> None:
        steps = max(1, int(max(abs(x1 - x0), abs(y1 - y0))))
        r = thickness // 2
        for s in range(steps + 1):
            t = s / steps
            px = int(round(x0 + (x1 - x0) * t))
            py = int(round(y0 + (y1 - y0) * t))
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    self.set_px(px + dx, py + dy, rgb)

    def draw_text(self, text: str, x: int, y: int, rgb: Tuple[int, int, int], scale: int = 2) -> None:
        tr_map = str.maketrans("ÇĞİÖŞÜçğıöşü", "CGIOSUCGIOSU")
        clean = str(text).translate(tr_map).upper()
        cur_x = int(x)
        for ch in clean:
            glyph = FONT_5X7.get(ch) or FONT_5X7[" "]
            for ry, row in enumerate(glyph):
                for rx, bit in enumerate(row):
                    if bit == "1":
                        self.fill_rect(cur_x + rx * scale, y + ry * scale, scale, scale, rgb)
            cur_x += 6 * scale

    def to_png_bytes(self) -> bytes:
        stride = self.w * 3
        raw = bytearray(self.h * (stride + 1))
        for y in range(self.h):
            raw_off = y * (stride + 1)
            raw[raw_off] = 0
            src_off = y * stride
            raw[raw_off + 1 : raw_off + 1 + stride] = self.buf[src_off : src_off + stride]

        def chunk(ctype: bytes, data: bytes) -> bytes:
            crc = zlib.crc32(ctype + data) & 0xFFFFFFFF
            return struct.pack("!I", len(data)) + ctype + data + struct.pack("!I", crc)

        ihdr = struct.pack("!IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)
        idat = zlib.compress(bytes(raw), 6)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def render_hourly_gainers_png(gainers: List[Dict[str, Any]], now_tr: datetime) -> bytes:
    """IMG_3165 tarzinda Saatlik Binance Gainers PNG karti."""
    rows = gainers[:8]
    W = 900
    row_h = 84
    header_h = 138
    H = header_h + max(len(rows), 1) * row_h + 44
    cv = PixelCanvas(W, H, (24, 26, 32))

    cv.draw_text("GAINERS  |  24 SAATIN EN YUKSEKLERI (SPOT)", 34, 24, (248, 250, 252), 3)
    cv.draw_text(f"SAAT: {now_tr.strftime('%H:00 TR')}  |  BINANCE LIVE", 34, 60, (148, 163, 184), 2)
    cv.fill_rect(28, 88, W - 56, 1, (43, 49, 57))
    cv.draw_text("NAME", 38, 102, (132, 142, 156), 2)
    cv.draw_text("LAST PRICE", 448, 102, (132, 142, 156), 2)
    cv.draw_text("24H CHG%", 700, 102, (132, 142, 156), 2)
    cv.fill_rect(28, 130, W - 56, 1, (43, 49, 57))

    badge_colors = [
        (245, 158, 11),
        (99, 102, 241),
        (6, 182, 212),
        (236, 72, 153),
        (16, 185, 129),
        (139, 92, 246),
        (59, 130, 246),
        (249, 115, 22),
    ]

    for idx, t in enumerate(rows):
        ry = header_h + idx * row_h
        cv.fill_rect(28, ry + row_h - 1, W - 56, 1, (34, 38, 46))
        cv.draw_circle(60, ry + 42, 22, badge_colors[idx % len(badge_colors)])
        cv.draw_text(t["base"][:2], 48, ry + 35, (255, 255, 255), 2)
        cv.draw_text(t["base"], 100, ry + 20, (248, 250, 252), 3)
        cv.draw_text(t["symbol"], 100, ry + 50, (132, 142, 156), 2)

        cv.draw_text(fmt_price_raw(t["price"]), 448, ry + 30, (248, 250, 252), 3)

        cv.fill_rect(688, ry + 18, 176, 48, (46, 189, 133))
        cv.draw_text(f"+%{t['change_24h']:.2f}", 704, ry + 32, (255, 255, 255), 3)

    return cv.to_png_bytes()


def render_winner_candle_png(
    symbol: str,
    bars: List[Dict[str, float]],
    live_price: float,
    dip_price: float,
) -> bytes:
    """IMG_3147 tarzinda 1G Canli Mum + Hacim + Dip/Anlik cizgili Gunun Kazanani PNG grafigi."""
    W, H = 960, 520
    cv = PixelCanvas(W, H, (9, 13, 22))
    cv.draw_text(
        f"{symbol} | 1G CANLI | GUNUN KAZANANI | ANLIK {fmt_price(live_price)}",
        22,
        16,
        (226, 232, 240),
        2,
    )
    cv.fill_rect(18, 44, W - 36, 1, (30, 41, 59))

    slice_bars = bars[-42:] if len(bars) > 42 else bars
    if not slice_bars:
        return cv.to_png_bytes()

    chart_x, chart_y, chart_w, chart_h = 22, 56, 780, 320
    vol_y, vol_h = 390, 96

    all_highs = [b["high"] for b in slice_bars] + [live_price, dip_price]
    all_lows = [b["low"] for b in slice_bars] + [live_price, dip_price]
    max_p = max(all_highs) * 1.03
    min_p = min(all_lows) * 0.97
    range_p = max(max_p - min_p, min_p * 0.01, 1e-8)
    max_v = max([b["quote_vol"] for b in slice_bars] + [1.0])

    for gi in range(5):
        gy = int(chart_y + (chart_h / 4) * gi)
        cv.draw_dashed_hline(chart_x, chart_x + chart_w, gy, (24, 34, 52), 4, 4)
        gp = max_p - (range_p / 4) * gi
        cv.draw_text(fmt_price_raw(gp)[:9], chart_x + chart_w + 10, gy - 6, (100, 116, 139), 2)

    slot = chart_w / len(slice_bars)
    body_w = max(5, int(slot * 0.62))

    for idx, b in enumerate(slice_bars):
        cx = int(chart_x + idx * slot + slot * 0.5)
        is_up = b["close"] >= b["open"]
        col = (14, 203, 129) if is_up else (246, 70, 93)

        y_high = int(chart_y + ((max_p - b["high"]) / range_p) * chart_h)
        y_low = int(chart_y + ((max_p - b["low"]) / range_p) * chart_h)
        y_open = int(chart_y + ((max_p - b["open"]) / range_p) * chart_h)
        y_close = int(chart_y + ((max_p - b["close"]) / range_p) * chart_h)

        cv.fill_rect(cx, y_high, 1, max(1, y_low - y_high), col)
        top_b = min(y_open, y_close)
        bot_b = max(y_open, y_close)
        cv.fill_rect(cx - body_w // 2, top_b, body_w, max(2, bot_b - top_b), col)

        vh = max(2, int((b["quote_vol"] / max_v) * vol_h))
        cv.fill_rect(cx - body_w // 2, vol_y + vol_h - vh, body_w, vh, (13, 132, 92) if is_up else (150, 46, 62))

    if dip_price > 0:
        y_dip = int(chart_y + ((max_p - dip_price) / range_p) * chart_h)
        cv.draw_dashed_hline(chart_x, chart_x + chart_w, y_dip, (245, 158, 11), 6, 4)
        cv.draw_text(f"BAS {fmt_price_raw(dip_price)[:8]}", chart_x + chart_w + 8, y_dip - 6, (245, 158, 11), 2)

    if live_price > 0:
        y_live = int(chart_y + ((max_p - live_price) / range_p) * chart_h)
        cv.draw_dashed_hline(chart_x, chart_x + chart_w, y_live, (56, 189, 248), 5, 4)
        cv.fill_rect(chart_x + chart_w + 6, y_live - 12, 136, 24, (2, 132, 199))
        cv.draw_text(fmt_price_raw(live_price)[:9], chart_x + chart_w + 12, y_live - 6, (255, 255, 255), 2)

    return cv.to_png_bytes()


def render_top24_png(ranked: List[Dict[str, Any]]) -> bytes:
    """IMG_3145 tarzinda Gunun Zaferi ve Top 24 Saat performans PNG karti."""
    if not ranked:
        return b""
    W, H = 980, 560
    cv = PixelCanvas(W, H, (12, 16, 32))
    cv.fill_rect(18, 18, 456, H - 36, (17, 22, 42))
    cv.fill_rect(492, 18, 470, H - 36, (17, 22, 42))

    champ = ranked[0]
    cv.draw_text("GUNUN ZAFERI", 38, 36, (248, 250, 252), 3)
    cv.draw_text(f"{champ['base']} / USDT  +{champ['peak_pct']:.1f}%", 38, 78, (52, 211, 153), 3)
    cv.draw_text(
        f"GIRIS {fmt_price_raw(champ['entry_price'])} -> ZIRVE {fmt_price_raw(champ['peak_price'])}",
        38,
        120,
        (148, 163, 184),
        2,
    )

    gx, gy, gw, gh = 44, 175, 404, 240
    cv.fill_rect(gx, gy, gw, gh, (11, 15, 30))
    pts = [
        (gx + 18, gy + gh - 34),
        (gx + int(gw * 0.35), gy + int(gh * 0.62)),
        (gx + int(gw * 0.72), gy + 36),
        (gx + gw - 18, gy + 56),
    ]
    for i in range(len(pts) - 1):
        cv.draw_line(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], (129, 140, 248), 3)
    cv.draw_circle(pts[0][0], pts[0][1], 5, (248, 250, 252))
    cv.draw_circle(pts[2][0], pts[2][1], 6, (16, 185, 129))

    cv.draw_text("TOP 24 SAAT HAREKETLERI", 514, 36, (248, 250, 252), 3)
    for idx, item in enumerate(ranked[:5]):
        ry = 92 + idx * 82
        cv.fill_rect(514, ry, 426, 70, (24, 30, 56) if idx == 0 else (14, 19, 36))
        cv.draw_text(f"{idx + 1}. {item['base']}", 530, ry + 14, (245, 158, 11) if idx == 0 else (226, 232, 240), 3)
        cv.draw_text(f"+{item['peak_pct']:.1f}%", 812, ry + 14, (52, 211, 153), 3)
        cv.draw_text(
            f"{fmt_price_raw(item['entry_price'])} -> {fmt_price_raw(item['peak_price'])}",
            530,
            ry + 44,
            (148, 163, 184),
            2,
        )

    return cv.to_png_bytes()


# ==============================================================================
# TELEGRAM GONDERIM & KOMUT YONETIMI
# ==============================================================================
def tg_send_message(text: str, chat_id: Optional[str] = None) -> None:
    if not BOT_TOKEN:
        return
    cid = chat_id or CHAT_ID
    try:
        HTTP_SESSION.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": cid, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10.0,
        )
    except Exception:
        pass


def tg_send_photo(png_bytes: bytes, caption: str, filename: str = "chart.png", chat_id: Optional[str] = None) -> None:
    if not BOT_TOKEN:
        return
    cid = chat_id or CHAT_ID
    if not png_bytes:
        tg_send_message(caption, cid)
        return
    try:
        HTTP_SESSION.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data={"chat_id": cid, "caption": caption, "parse_mode": "HTML"},
            files={"photo": (filename, png_bytes, "image/png")},
            timeout=20.0,
        )
    except Exception:
        tg_send_message(caption, cid)


def build_hourly_gainers_payload(now_tr: Optional[datetime] = None) -> Tuple[str, bytes]:
    now_tr = now_tr or datetime.now(TR_TZ)
    with STATE_LOCK:
        gainers = sorted(
            [t for t in TICKER_MAP.values() if t["quote"] == "USDT" and t["change_24h"] > 0 and t["volume_24h"] >= 150000],
            key=lambda x: x["change_24h"],
            reverse=True,
        )[:10]
    if not gainers:
        return ("🔥 <b>SAATLİK 24s EN YÜKSEKLER</b>\n\nCanlı piyasa verisi yükleniyor...", b"")
    lines = [
        f"🔥 <b>SAATLİK PİYASA RAPORU · 24 SAATİN EN YÜKSEKLERİ ({now_tr.strftime('%H:00 TR')})</b>\n",
    ]
    for i, g in enumerate(gainers[:8], start=1):
        lines.append(
            f"{i}. <b>#{escape_html(g['base'])}</b> · {fmt_price(g['price'])} · <b>+%{g['change_24h']:.2f}</b>"
        )
    png = render_hourly_gainers_png(gainers, now_tr)
    return ("\n".join(lines), png)


def build_daily_winner_payload() -> Tuple[str, bytes]:
    cutoff = time.time() - 24 * 3600
    with STATE_LOCK:
        day_sigs = [s for s in SIGNALS if s["created_at"] >= cutoff and s["engine"] in ("PUMP", "PUMP15M")]
        sig_winner = max(day_sigs, key=lambda x: x["peak_pct"], default=None)
        mkt_gainers = [t for t in TICKER_MAP.values() if t["quote"] == "USDT" and t["change_24h"] > 0 and t["volume_24h"] >= 150000]
        mkt_winner = max(mkt_gainers, key=lambda x: x["change_24h"], default=None)

    symbol = sig_winner["symbol"] if sig_winner else (mkt_winner["symbol"] if mkt_winner else "")
    if not symbol:
        return ("🏆 <b>GÜNÜN KAZANANI</b>\n\nSon 24 saatte öne çıkan coin henüz oluşmadı.", b"")

    if sig_winner:
        live_p = sig_winner["current_price"]
        dip_p = sig_winner["entry_price"]
        peak_p = max(sig_winner["peak_price"], live_p, dip_p)
        max_pct = sig_winner["peak_pct"]
    else:
        assert mkt_winner is not None
        live_p = mkt_winner["price"]
        dip_p = mkt_winner["low_24h"] or live_p
        peak_p = mkt_winner["high_24h"] or live_p
        max_pct = max(mkt_winner["change_24h"], ((peak_p - dip_p) / dip_p) * 100 if dip_p > 0 else 0.0)

    png = b""
    try:
        bars_1d = fetch_spot_klines(symbol, "1d", 45)
        png = render_winner_candle_png(symbol, bars_1d, live_p, dip_p)
    except Exception:
        png = b""

    caption = (
        f"🏆 <b>GÜNÜN KAZANANI: {escape_html(symbol)}</b>\n\n"
        f"🔥 24 saat içinde en çok yükselen coin!\n\n"
        f"📉 Dip Fiyat: <b>{fmt_price(dip_p)}</b>\n"
        f"🚀 Zirve Fiyat: <b>{fmt_price(peak_p)}</b>\n"
        f"📈 Maksimum Yükseliş: <b>+{max_pct:.1f}%</b>\n\n"
        f"💡 <b>Değerlendirme:</b> {escape_html(symbol)} gün içerisinde muazzam bir hacim ve fiyat patlaması yaşadı. "
        f"Pump Dedektör verilerimize göre günün en kârlı hareketi bu coinde gerçekleşti."
    )
    return (caption, png)


def build_top24_payload() -> Tuple[str, bytes]:
    cutoff = time.time() - 24 * 3600
    with STATE_LOCK:
        day_sigs = [s for s in SIGNALS if s["created_at"] >= cutoff and s["engine"] in ("PUMP", "PUMP15M")]
    by_sym: Dict[str, Dict[str, Any]] = {}
    for s in day_sigs:
        prev = by_sym.get(s["symbol"])
        if not prev or s["peak_pct"] > prev["peak_pct"]:
            by_sym[s["symbol"]] = s
    ranked = sorted(by_sym.values(), key=lambda x: x["peak_pct"], reverse=True)
    if not ranked:
        return ("🔥 <b>TOP 24 Saat Hareketleri</b>\n\nSon 24 saatte kaydedilen Pump / Kırılım sinyali henüz oluşmadı.", b"")

    hit2 = sum(1 for s in day_sigs if s["peak_pct"] >= 2.0)
    hit2_pct = round((hit2 / max(1, len(day_sigs))) * 100)
    medals = ["🥇", "🥈", "🥉", "🏅", "⭐️"]
    lines = ["🔥 <b>24 saatin TOP hareketleri</b>\n", "sinyal yönündeki maksimum yükseliş:\n"]
    for i, s in enumerate(ranked[:5]):
        lines.append(
            f"{medals[i]} <b>#{escape_html(s['base'])}</b>   <b>+{s['peak_pct']:.1f}%</b>  ·  "
            f"Giriş {fmt_price(s['entry_price'])} ➡️ Zirve {fmt_price(s['peak_price'])}"
        )
    lines.append(f"\n📌 {len(day_sigs)} sinyal · %{hit2_pct} en az +2% ulaştı")
    png = render_top24_png(ranked)
    return ("\n".join(lines), png)


# ==============================================================================
# OTO FUTURES ISLEM MOTORU (YALNIZCA 0-7dk PUMP & 15dk KIRILIM)
# ==============================================================================
def maybe_open_auto_trade(sig: Dict[str, Any]) -> None:
    if sig["engine"] not in ("PUMP", "PUMP15M"):
        return
    with STATE_LOCK:
        if not CONFIG["auto_trade_enabled"]:
            return
        mode = str(CONFIG["trade_mode"]).upper()
        max_open = int(CONFIG["max_open_trades"])
        open_trades = [t for t in TRADES if t["status"] == "OPEN"]
        if max_open > 0 and len(open_trades) >= max_open:
            return
        if any(t["symbol"] == sig["symbol"] for t in open_trades):
            return
        flt = FUTURES_FILTERS.get(sig["symbol"])
        if not flt:
            return
        lev = max(1, int(CONFIG["leverage"]))
        margin = max(1.0, float(CONFIG["trade_usdt"]))
        tp_pct = max(0.1, float(CONFIG["tp_pct"]))
        sl_pct = max(0.1, float(CONFIG["sl_pct"]))

        if mode == "PAPER" and CONFIG["paper_balance_usdt"] < margin:
            return

        entry = float(sig["entry_price"])
        if entry <= 0:
            return

        raw_qty = (margin * lev) / entry
        qty = round_step(raw_qty, flt["step_size"])
        if qty < flt["min_qty"]:
            return
        real_margin = (qty * entry) / lev
        target = round_step(entry * (1.0 + tp_pct / 100.0), flt["tick_size"])
        stop = round_step(entry * (1.0 - sl_pct / 100.0), flt["tick_size"])

    if mode in ("LIVE", "TESTNET"):
        is_test = mode == "TESTNET"
        try:
            try:
                binance_futures_signed_post("/fapi/v1/marginType", {"symbol": sig["symbol"], "marginType": "ISOLATED"}, is_test)
            except Exception:
                pass
            binance_futures_signed_post("/fapi/v1/leverage", {"symbol": sig["symbol"], "leverage": lev}, is_test)
            buy_res = binance_futures_signed_post(
                "/fapi/v1/order",
                {"symbol": sig["symbol"], "side": "BUY", "type": "MARKET", "quantity": qty},
                is_test,
            )
            avg_p = float(buy_res.get("avgPrice") or 0)
            if avg_p > 0:
                entry = avg_p
                target = round_step(entry * (1.0 + tp_pct / 100.0), flt["tick_size"])
                stop = round_step(entry * (1.0 - sl_pct / 100.0), flt["tick_size"])
            binance_futures_signed_post(
                "/fapi/v1/order",
                {"symbol": sig["symbol"], "side": "SELL", "type": "TAKE_PROFIT_MARKET", "stopPrice": target, "closePosition": "true", "workingType": "MARK_PRICE"},
                is_test,
            )
            binance_futures_signed_post(
                "/fapi/v1/order",
                {"symbol": sig["symbol"], "side": "SELL", "type": "STOP_MARKET", "stopPrice": stop, "closePosition": "true", "workingType": "MARK_PRICE"},
                is_test,
            )
        except Exception as e:
            tg_send_message(f"⚠️ <b>FUTURES EMİR HATASI (#{escape_html(sig['symbol'])})</b>\n{escape_html(str(e))}")
            return

    trade = {
        "id": f"TR-{mode}-{sig['symbol']}-{int(time.time() * 1000)}",
        "symbol": sig["symbol"],
        "base": sig["base"],
        "engine": sig["engine"],
        "mode": mode,
        "leverage": lev,
        "status": "OPEN",
        "entry_price": entry,
        "target_price": target,
        "stop_price": stop,
        "current_price": entry,
        "quantity": qty,
        "margin_usdt": round(real_margin, 2),
        "notional_usdt": round(qty * entry, 2),
        "pnl_usdt": 0.0,
        "pnl_pct": 0.0,
        "roe_pct": 0.0,
        "opened_at": time.time(),
    }
    with STATE_LOCK:
        if mode == "PAPER":
            CONFIG["paper_balance_usdt"] = round(CONFIG["paper_balance_usdt"] - real_margin, 2)
        TRADES.insert(0, trade)
        if len(TRADES) > 150:
            del TRADES[150:]
        notify_paper = bool(CONFIG["notify_paper_telegram"])
    save_state()

    # PAPER modunda Telegram mesaji kullanici istemedikce GONDERILMEZ
    if mode != "PAPER" or notify_paper:
        tg_send_message(
            f"🟢 <b>FUTURES {lev}x İZOLE LONG AÇILDI ({mode} · {escape_html(sig['engine'])})</b>\n"
            f"• Parite: <b>#{escape_html(sig['symbol'])}</b> · Teminat: <b>${real_margin:.2f}</b> ({lev}x = ${qty * entry:.2f})\n"
            f"• Giriş: <b>{fmt_price(entry)}</b>\n"
            f"🎯 +%{tp_pct:.1f} Kâr Al: <b>{fmt_price(target)}</b> | 🛑 -%{sl_pct:.1f} Stop: <b>{fmt_price(stop)}</b>"
        )


def update_open_trades_for_symbol(symbol: str, live_price: float) -> None:
    to_notify: List[str] = []
    changed = False
    with STATE_LOCK:
        tp_pct = float(CONFIG["tp_pct"])
        sl_pct = float(CONFIG["sl_pct"])
        notify_paper = bool(CONFIG["notify_paper_telegram"])
        for t in TRADES:
            if t["status"] != "OPEN" or t["symbol"] != symbol:
                continue
            t["current_price"] = live_price
            gross_pnl = (live_price - t["entry_price"]) * t["quantity"]
            fee = (t["notional_usdt"] + live_price * t["quantity"]) * 0.0005
            net_pnl = round(gross_pnl - fee, 2)
            t["pnl_usdt"] = net_pnl
            t["pnl_pct"] = round(((live_price - t["entry_price"]) / t["entry_price"]) * 100.0, 2)
            t["roe_pct"] = round((net_pnl / max(0.01, t["margin_usdt"])) * 100.0, 2)

            reason = None
            if live_price >= t["target_price"]:
                reason = "TP"
            elif live_price <= t["stop_price"]:
                reason = "SL"

            if reason:
                t["status"] = "CLOSED"
                t["close_reason"] = reason
                t["exit_price"] = live_price
                t["closed_at"] = time.time()
                if t["mode"] == "PAPER":
                    CONFIG["paper_balance_usdt"] = round(CONFIG["paper_balance_usdt"] + t["margin_usdt"] + net_pnl, 2)
                changed = True
                if t["mode"] != "PAPER" or notify_paper:
                    icon = "🎯" if reason == "TP" else "🛑"
                    label = f"+%{tp_pct:.1f} Kâr Al (TP)" if reason == "TP" else f"-%{sl_pct:.1f} Stop Loss (SL)"
                    to_notify.append(
                        f"{icon} <b>FUTURES {t['leverage']}x KAPANDI — {label} ({t['mode']})</b>\n"
                        f"• Parite: <b>#{escape_html(t['symbol'])}</b>\n"
                        f"• Giriş: {fmt_price(t['entry_price'])} ➡️ Çıkış: <b>{fmt_price(live_price)}</b>\n"
                        f"• Fiyat: <b>{t['pnl_pct']:+.2f}%</b> | Net K/Z: <b>{'+' if net_pnl >= 0 else ''}${net_pnl:.2f}</b> (ROE: {t['roe_pct']:+.1f}%)"
                    )
    if changed:
        save_state()
    for msg in to_notify:
        tg_send_message(msg)


# ==============================================================================
# SINYAL MOTORLARI: 0-7DK PUMP (+RSI70), 15DK KIRILIM (+RSI70), ATL/ATH
# ==============================================================================
def record_and_broadcast_signal(sig: Dict[str, Any]) -> None:
    with STATE_LOCK:
        SIGNALS.insert(0, sig)
        if len(SIGNALS) > 150:
            del SIGNALS[150:]
    save_state()
    tg_send_message(sig["tg_message"])
    threading.Thread(target=maybe_open_auto_trade, args=(sig,), daemon=True).start()


def evaluate_pump_and_15m(symbol: str, base: str, live_cur: float, vol_24h: float, chg_24h: float) -> None:
    now = time.time()
    hist = HISTORY_MAP.get(symbol) or []
    pts_7m = [p for (ts, p) in hist if ts >= now - 420]
    pts_15m = [p for (ts, p) in hist if ts >= now - 900]

    with STATE_LOCK:
        pump_thresh = float(CONFIG["pump_threshold_pct"])
        pump15_thresh = float(CONFIG["pump15_threshold_pct"])
        pump15_rvol = float(CONFIG["pump15_min_rvol"])
        rsi_min = float(CONFIG["rsi_min"])
        pump_locked = base in PUMP_LOCKS and (now - PUMP_LOCKS[base]["locked_at"] < 7200)
        p15_locked = base in PUMP15M_LOCKS and (now - PUMP15M_LOCKS[base]["locked_at"] < 7200)

    is_7m_candidate = False
    if not pump_locked and len(pts_7m) >= 2:
        min_7m = min(pts_7m)
        if min_7m > 0 and ((live_cur - min_7m) / min_7m) * 100.0 >= pump_thresh:
            is_7m_candidate = True

    is_15m_candidate = False
    if not p15_locked and len(pts_15m) >= 2:
        min_15m = min(pts_15m)
        if min_15m > 0 and ((live_cur - min_15m) / min_15m) * 100.0 >= 1.8:
            is_15m_candidate = True

    if not is_7m_candidate and not is_15m_candidate:
        return

    with STATE_LOCK:
        BOT_STATS["pump_evals"] += 1

    # 1) 0-7 dk Ani Pump + RSI(14) >= rsi_min
    if is_7m_candidate:
        try:
            bars_1m = fetch_spot_klines(symbol, "1m", 50)
            if len(bars_1m) >= 20:
                bars_1m[-1]["close"] = live_cur
                bars_1m[-1]["high"] = max(bars_1m[-1]["high"], live_cur)
                bars_1m[-1]["low"] = min(bars_1m[-1]["low"], live_cur)
                rsi_1m = compute_rsi_series([b["close"] for b in bars_1m], 14)[-1]

                w7 = bars_1m[-7:]
                min_b = min(w7, key=lambda b: b["low"])
                max_b = max(w7, key=lambda b: b["high"])
                start_p = min_b["low"]
                peak_p = max(max_b["high"], live_cur)
                jump_pct = ((live_cur - start_p) / start_p) * 100.0 if start_p > 0 else 0.0
                hold_ratio = (live_cur - start_p) / (peak_p - start_p) if peak_p > start_p else 1.0

                if jump_pct >= pump_thresh and hold_ratio >= 0.45 and rsi_1m >= rsi_min:
                    dur_min = max(1, min(7, round((bars_1m[-1]["open_time"] - min_b["open_time"]) / 60000) + 1))
                    tg_msg = (
                        f"⚡ <b>SPOT ANLIK PUMP DEDEKTÖRÜ — #{escape_html(symbol)}</b>\n\n"
                        f"📈 Değişim: <b>+{jump_pct:.2f}%</b> ({dur_min} dk içinde!)\n"
                        f"📊 RSI(14): <b>{rsi_1m:.1f}</b> (≥ {rsi_min:.0f} onaylı)\n"
                        f"💰 Başlangıç: {fmt_price_raw(start_p)} ➡️ Anlık: <b>{fmt_price_raw(live_cur)}</b>\n"
                        f"💧 24s Parite Hacmi: {vol_24h:,.0f} (USDT)\n"
                        f"🔒 2 saatlik dalga kilidi aktif."
                    )
                    locked_ok = False
                    with STATE_LOCK:
                        if not (base in PUMP_LOCKS and (now - PUMP_LOCKS[base]["locked_at"] < 7200)):
                            PUMP_LOCKS[base] = {"symbol": symbol, "locked_at": now}
                            locked_ok = True
                    if locked_ok:
                        record_and_broadcast_signal({
                            "id": f"PUMP-{symbol}-{int(now * 1000)}",
                            "engine": "PUMP",
                            "symbol": symbol,
                            "base": base,
                            "entry_price": live_cur,
                            "current_price": live_cur,
                            "peak_price": live_cur,
                            "peak_pct": 0.0,
                            "rsi14": round(rsi_1m, 1),
                            "created_at": now,
                            "tg_message": tg_msg,
                        })
        except Exception:
            pass

    # 2) 15 dk Hacimli Kirilim + RSI(14) >= rsi_min
    if is_15m_candidate:
        try:
            bars_15m = fetch_spot_klines(symbol, "15m", 50)
            if len(bars_15m) >= 20:
                bars_15m[-1]["close"] = live_cur
                bars_15m[-1]["high"] = max(bars_15m[-1]["high"], live_cur)
                bars_15m[-1]["low"] = min(bars_15m[-1]["low"], live_cur)
                rsi_15m = compute_rsi_series([b["close"] for b in bars_15m], 14)[-1]

                cur_b = bars_15m[-1]
                prev_vols = [b["quote_vol"] for b in bars_15m[-15:-1]]
                avg_vol = sum(prev_vols) / max(1, len(prev_vols))
                rvol = (cur_b["quote_vol"] / avg_vol) if avg_vol > 0 else 1.0
                chg_15m = ((live_cur - cur_b["open"]) / cur_b["open"]) * 100.0 if cur_b["open"] > 0 else 0.0
                high_15m = max(cur_b["high"], live_cur)
                hold_15m = (live_cur - cur_b["open"]) / (high_15m - cur_b["open"]) if high_15m > cur_b["open"] else 1.0

                if chg_15m >= pump15_thresh and rvol >= pump15_rvol and hold_15m >= 0.40 and rsi_15m >= rsi_min:
                    tg_msg = (
                        f"🚀 <b>SPOT 15DK HACİMLİ KIRILIM — #{escape_html(symbol)}</b>\n\n"
                        f"📈 Değişim: <b>+{chg_15m:.2f}%</b> (15 dk içinde! · RVOL: <b>{rvol:.1f}x</b>)\n"
                        f"📊 RSI(14): <b>{rsi_15m:.1f}</b> (≥ {rsi_min:.0f} onaylı)\n"
                        f"💰 Başlangıç: {fmt_price_raw(cur_b['open'])} ➡️ Anlık: <b>{fmt_price_raw(live_cur)}</b>\n"
                        f"💧 24s Parite Hacmi: {vol_24h:,.0f} (USDT)\n"
                        f"🔒 15dk 2 saatlik dalga kilidi aktif."
                    )
                    locked_ok = False
                    with STATE_LOCK:
                        if not (base in PUMP15M_LOCKS and (now - PUMP15M_LOCKS[base]["locked_at"] < 7200)):
                            PUMP15M_LOCKS[base] = {"symbol": symbol, "locked_at": now}
                            locked_ok = True
                    if locked_ok:
                        record_and_broadcast_signal({
                            "id": f"PUMP15M-{symbol}-{int(now * 1000)}",
                            "engine": "PUMP15M",
                            "symbol": symbol,
                            "base": base,
                            "entry_price": live_cur,
                            "current_price": live_cur,
                            "peak_price": live_cur,
                            "peak_pct": 0.0,
                            "rsi14": round(rsi_15m, 1),
                            "created_at": now,
                            "tg_message": tg_msg,
                        })
        except Exception:
            pass


def build_ath_atl_entry(symbol: str) -> Optional[Dict[str, Any]]:
    try:
        bars_1m = fetch_spot_klines(symbol, "1M", 120)
        source = "1M"
        bars = bars_1m
        if len(bars) < 2:
            bars_1d = fetch_spot_klines(symbol, "1d", 500)
            if len(bars_1d) < 2:
                return None
            bars = bars_1d
            source = "1d"

        closed = bars[:-1]
        prev_ath = max(b["high"] for b in closed)
        prev_atl = min(b["low"] for b in closed)
        if prev_ath <= 0 or prev_atl <= 0:
            return None
        return {
            "ath": prev_ath,
            "atl": prev_atl,
            "source": source,
            "updated_at": time.time(),
        }
    except Exception:
        return None


def evaluate_ath_atl_tick(symbol: str, base: str, live_cur: float, chg_24h: float) -> None:
    now = time.time()
    cached = ATH_ATL_CACHE.get(symbol)
    if not cached:
        return
    hit_atl = live_cur <= cached["atl"] * 0.9999
    hit_ath = live_cur >= cached["ath"] * 1.0001
    if not hit_atl and not hit_ath:
        return

    if now - STRUCTURE_VERIFY_AT.get(symbol, 0.0) < 60.0:
        return
    STRUCTURE_VERIFY_AT[symbol] = now

    fresh = build_ath_atl_entry(symbol)
    if not fresh:
        return
    ATH_ATL_CACHE[symbol] = fresh

    if live_cur < fresh["atl"]:
        prev_atl = fresh["atl"]
        lock_key = f"{symbol}:ATL"
        with STATE_LOCK:
            existing = ATH_ATL_LOCKS.get(lock_key)
            if existing:
                if live_cur >= existing["level_price"] * 0.99 or (now - existing["locked_at"] < 300.0):
                    return
            ATH_ATL_LOCKS[lock_key] = {"level_price": live_cur, "locked_at": now}
        ATH_ATL_CACHE[symbol]["atl"] = live_cur
        tg_msg = (
            f"🕳 <b>#{escape_html(symbol)} {fmt_price(live_cur)} tüm zamanların en düşüğü (ATL)</b>\n"
            f"📉 Önceki ATL ({fmt_price(prev_atl)}) kesin olarak aşağı kırıldı · 24s: {chg_24h:+.2f}%\n"
            f"🔒 Kilit aktif: {fmt_price(live_cur)} (%1+) altına düşmedikçe tekrar gelmez."
        )
        record_and_broadcast_signal({
            "id": f"ATL-{symbol}-{int(now * 1000)}",
            "engine": "ATL",
            "symbol": symbol,
            "base": base,
            "entry_price": live_cur,
            "current_price": live_cur,
            "peak_price": live_cur,
            "peak_pct": 0.0,
            "created_at": now,
            "tg_message": tg_msg,
        })
    elif live_cur > fresh["ath"]:
        prev_ath = fresh["ath"]
        lock_key = f"{symbol}:ATH"
        with STATE_LOCK:
            existing = ATH_ATL_LOCKS.get(lock_key)
            if existing:
                if live_cur <= existing["level_price"] * 1.01 or (now - existing["locked_at"] < 300.0):
                    return
            ATH_ATL_LOCKS[lock_key] = {"level_price": live_cur, "locked_at": now}
        ATH_ATL_CACHE[symbol]["ath"] = live_cur
        tg_msg = (
            f"🏔 <b>#{escape_html(symbol)} {fmt_price(live_cur)} tüm zamanların en yükseği (ATH)</b>\n"
            f"📈 Önceki ATH ({fmt_price(prev_ath)}) kesin olarak yukarı kırıldı · 24s: {chg_24h:+.2f}%\n"
            f"🔒 Kilit aktif: {fmt_price(live_cur)} (%1+) üzerine çıkmadıkça tekrar gelmez."
        )
        record_and_broadcast_signal({
            "id": f"ATH-{symbol}-{int(now * 1000)}",
            "engine": "ATH",
            "symbol": symbol,
            "base": base,
            "entry_price": live_cur,
            "current_price": live_cur,
            "peak_price": live_cur,
            "peak_pct": 0.0,
            "created_at": now,
            "tg_message": tg_msg,
        })


# ==============================================================================
# ARKA PLAN DONGULERI (TICKER WS, ATL/ATH CACHE, ZAMANLANMIS PNG RAPORLAR, TG BOT)
# ==============================================================================
def handle_ticker_batch(items: List[Dict[str, Any]]) -> None:
    now = time.time()
    min_vol = float(CONFIG["min_vol_usdt"])
    eval_queue: List[Tuple[str, str, float, float, float]] = []

    with STATE_LOCK:
        BOT_STATS["last_tick_at"] = now
        running = bool(CONFIG["running"])

    for row in items:
        sym = str(row.get("s") or "")
        if not sym.endswith("USDT"):
            continue
        base = sym[:-4]
        if base in BLACKLIST_BASES:
            continue
        price = float(row.get("c") or 0.0)
        vol = float(row.get("q") or 0.0)
        chg = float(row.get("P") or 0.0)
        high24 = float(row.get("h") or price)
        low24 = float(row.get("l") or price)
        if price <= 0:
            continue

        prev_p = PREV_PRICES.get(sym, price)
        PREV_PRICES[sym] = price

        with STATE_LOCK:
            TICKER_MAP[sym] = {
                "symbol": sym,
                "base": base,
                "quote": "USDT",
                "price": price,
                "prev_price": prev_p,
                "change_24h": chg,
                "volume_24h": vol,
                "high_24h": high24,
                "low_24h": low24,
                "updated_at": now,
            }
            hist = HISTORY_MAP.setdefault(sym, [])
            hist.append((now, price))
            cutoff_h = now - 900
            if len(hist) > 120 and hist[0][0] < cutoff_h:
                HISTORY_MAP[sym] = [x for x in hist if x[0] >= cutoff_h]

            for s in SIGNALS:
                if s["symbol"] == sym and s["entry_price"] > 0:
                    s["current_price"] = price
                    pct = ((s["entry_price"] - price) / s["entry_price"]) * 100.0 if s["engine"] == "ATL" else ((price - s["entry_price"]) / s["entry_price"]) * 100.0
                    if pct > s["peak_pct"]:
                        s["peak_pct"] = round(pct, 2)
                        s["peak_price"] = price

        update_open_trades_for_symbol(sym, price)

        if running and vol >= min_vol:
            last_ev = LAST_SIGNAL_EVAL_AT.get(sym, 0.0)
            if now - last_ev >= 2.0:
                LAST_SIGNAL_EVAL_AT[sym] = now
                eval_queue.append((sym, base, price, vol, chg))

    for sym, base, price, vol, chg in eval_queue:
        evaluate_pump_and_15m(sym, base, price, vol, chg)
        evaluate_ath_atl_tick(sym, base, price, chg)


def binance_ws_loop() -> None:
    ws_url = "wss://stream.binance.com:9443/ws/!miniTicker@arr"

    def on_open(_ws: Any) -> None:
        BOT_STATS["ws_connected"] = True

    def on_close(_ws: Any, *_args: Any) -> None:
        BOT_STATS["ws_connected"] = False

    def on_message(_ws: Any, message: str) -> None:
        try:
            data = json.loads(message)
            if isinstance(data, list):
                norm = []
                for r in data:
                    sym = r.get("s", "")
                    open_p = float(r.get("o") or 0.0)
                    close_p = float(r.get("c") or 0.0)
                    pct = ((close_p - open_p) / open_p) * 100.0 if open_p > 0 else 0.0
                    norm.append({
                        "s": sym,
                        "c": r.get("c"),
                        "q": r.get("q"),
                        "h": r.get("h"),
                        "l": r.get("l"),
                        "P": pct,
                    })
                handle_ticker_batch(norm)
        except Exception:
            pass

    while True:
        try:
            app = websocket.WebSocketApp(
                ws_url,
                on_open=on_open,
                on_close=on_close,
                on_message=on_message,
            )
            app.run_forever(ping_interval=30, ping_timeout=10)
        except Exception:
            pass
        BOT_STATS["ws_connected"] = False
        time.sleep(3)


def ath_atl_cache_loop() -> None:
    while True:
        try:
            with STATE_LOCK:
                syms = [
                    t["symbol"]
                    for t in sorted(TICKER_MAP.values(), key=lambda x: x["volume_24h"], reverse=True)
                    if t["volume_24h"] >= 100000
                ]
            for sym in syms:
                cur = ATH_ATL_CACHE.get(sym)
                if cur and (time.time() - cur["updated_at"] < 6 * 3600):
                    continue
                entry = build_ath_atl_entry(sym)
                if entry:
                    ATH_ATL_CACHE[sym] = entry
                time.sleep(0.18)
        except Exception:
            pass
        time.sleep(30)


def scheduler_loop() -> None:
    while True:
        try:
            now_tr = datetime.now(TR_TZ)
            hour_key = now_tr.strftime("%Y-%m-%d-%H")
            day_key = now_tr.strftime("%Y-%m-%d")

            # 1) Her 1 saatte bir 24s En Yuksekler (IMG_3165)
            if now_tr.minute < 3 and REPORT_SENT.get("hourly_key") != hour_key:
                REPORT_SENT["hourly_key"] = hour_key
                save_state()
                cap, png = build_hourly_gainers_payload(now_tr)
                tg_send_photo(png, cap, filename="saatlik_en_yuksekler.png")

            # 2) Her gun 21:00 TR Gunun Kazanani (IMG_3147)
            if now_tr.hour == 21 and REPORT_SENT.get("winner_date") != day_key:
                REPORT_SENT["winner_date"] = day_key
                save_state()
                cap, png = build_daily_winner_payload()
                tg_send_photo(png, cap, filename="gunun_kazanani.png")

            # 3) Her gun 23:00 TR Top 24 Ozet (IMG_3145)
            if now_tr.hour == 23 and REPORT_SENT.get("top24_date") != day_key:
                REPORT_SENT["top24_date"] = day_key
                save_state()
                cap, png = build_top24_payload()
                tg_send_photo(png, cap, filename="top_24_ozet.png")
        except Exception:
            pass
        time.sleep(20)


def handle_telegram_command(text: str, cid: str) -> None:
    parts = text.strip().split()
    if not parts:
        return
    cmd = parts[0].lower().split("@")[0]

    if cmd in ("/start", "/yardim", "/help"):
        tg_send_message(
            "🤖 <b>Spot Akış Terminali & Futures Botu</b>\n\n"
            "• <code>/durum</code> — Canlı ayarlar & pozisyon özeti\n"
            "• <code>/kazanan</code> — 🏆 Günün Kazananı 1G canlı grafik PNG\n"
            "• <code>/saatlik</code> veya <code>/yukselenler</code> — 🔥 24 Saatin En Yüksekleri PNG\n"
            "• <code>/top24</code> — 📊 TOP 24 Saat Hareketleri PNG\n"
            "• <code>/pozisyonlar</code> — Açık Futures pozisyonları\n"
            "• <code>/kilit_sifirla</code> — Pump / Kırılım / ATL-ATH kilitlerini sıfırlar\n"
            "• <code>/ayar &lt;alan&gt; &lt;deger&gt;</code> — Tüm parametreleri değiştir:\n"
            "  Örn: <code>/ayar tp 10</code> | <code>/ayar sl 10</code> | <code>/ayar max 0</code> (0=Sınırsız) | "
            "<code>/ayar rsi 70</code> | <code>/ayar usdt 25</code> | <code>/ayar lev 3</code> | <code>/ayar paper_tg off</code>",
            cid,
        )
    elif cmd == "/durum":
        with STATE_LOCK:
            open_c = sum(1 for t in TRADES if t["status"] == "OPEN")
            max_str = "∞ Sınırsız" if CONFIG["max_open_trades"] == 0 else str(CONFIG["max_open_trades"])
            msg = (
                f"📊 <b>BOT DURUMU ({'AKTİF' if CONFIG['running'] else 'DURAKLATILDI'})</b>\n"
                f"• Mod: <b>{CONFIG['trade_mode']}</b> | Oto Trade: <b>{'AÇIK' if CONFIG['auto_trade_enabled'] else 'KAPALI'}</b>\n"
                f"• Kaldıraç: <b>{CONFIG['leverage']}x</b> | Teminat: <b>${CONFIG['trade_usdt']:.2f}</b>\n"
                f"• TP / SL: <b>+%{CONFIG['tp_pct']:.1f} / -%{CONFIG['sl_pct']:.1f}</b>\n"
                f"• Maks Pozisyon: <b>{max_str}</b> (Açık: {open_c})\n"
                f"• RSI(14) Eşiği: <b>≥ {CONFIG['rsi_min']:.0f}</b> (0-7dk & 15dk)\n"
                f"• Paper Telegram Bildirimi: <b>{'AÇIK' if CONFIG['notify_paper_telegram'] else 'KAPALI (Sessiz)'}</b>\n"
                f"• Paper Bakiye: <b>${CONFIG['paper_balance_usdt']:.2f}</b>\n"
                f"• Taranan Parite: <b>{len(TICKER_MAP)}</b> | ATL/ATH Cache: <b>{len(ATH_ATL_CACHE)}</b>"
            )
        tg_send_message(msg, cid)
    elif cmd == "/kazanan":
        cap, png = build_daily_winner_payload()
        tg_send_photo(png, cap, filename="gunun_kazanani.png", chat_id=cid)
    elif cmd in ("/saatlik", "/yukselenler"):
        cap, png = build_hourly_gainers_payload()
        tg_send_photo(png, cap, filename="saatlik_en_yuksekler.png", chat_id=cid)
    elif cmd == "/top24":
        cap, png = build_top24_payload()
        tg_send_photo(png, cap, filename="top_24_ozet.png", chat_id=cid)
    elif cmd == "/pozisyonlar":
        with STATE_LOCK:
            opens = [t for t in TRADES if t["status"] == "OPEN"]
        if not opens:
            tg_send_message("Açık Futures pozisyonu yok.", cid)
            return
        lines = [f"📈 <b>AÇIK FUTURES POZİSYONLARI ({len(opens)})</b>\n"]
        for t in opens[:20]:
            lines.append(
                f"• <b>#{escape_html(t['symbol'])}</b> ({t['leverage']}x {t['mode']}) · "
                f"Giriş: {fmt_price_raw(t['entry_price'])} ➡️ Anlık: {fmt_price_raw(t['current_price'])} · "
                f"K/Z: <b>{'+' if t['pnl_usdt'] >= 0 else ''}${t['pnl_usdt']:.2f} ({t['roe_pct']:+.1f}%)</b>"
            )
        tg_send_message("\n".join(lines), cid)
    elif cmd == "/kilit_sifirla":
        with STATE_LOCK:
            PUMP_LOCKS.clear()
            PUMP15M_LOCKS.clear()
            ATH_ATL_LOCKS.clear()
        save_state()
        tg_send_message("🔓 Tüm Pump, 15dk Kırılım ve ATL/ATH kilitleri sıfırlandı.", cid)
    elif cmd == "/ayar" and len(parts) >= 3:
        key = parts[1].lower()
        val_s = parts[2].strip()
        try:
            with STATE_LOCK:
                if key == "tp":
                    CONFIG["tp_pct"] = max(0.1, min(1000.0, float(val_s)))
                elif key == "sl":
                    CONFIG["sl_pct"] = max(0.1, min(99.0, float(val_s)))
                elif key == "max":
                    CONFIG["max_open_trades"] = max(0, min(9999, int(float(val_s))))
                elif key == "rsi":
                    CONFIG["rsi_min"] = max(0.0, min(95.0, float(val_s)))
                elif key == "usdt":
                    CONFIG["trade_usdt"] = max(1.0, float(val_s))
                elif key == "lev":
                    CONFIG["leverage"] = max(1, min(125, int(float(val_s))))
                elif key == "balance":
                    CONFIG["paper_balance_usdt"] = max(10.0, float(val_s))
                elif key == "paper_tg":
                    CONFIG["notify_paper_telegram"] = val_s.lower() in ("1", "on", "ac", "true")
                elif key == "mod" and val_s.upper() in ("PAPER", "TESTNET", "LIVE"):
                    CONFIG["trade_mode"] = val_s.upper()
                else:
                    tg_send_message("❌ Geçersiz ayar anahtarı. Kullanım: tp, sl, max, rsi, usdt, lev, balance, paper_tg, mod", cid)
                    return
            save_state()
            tg_send_message(f"✅ Ayar güncellendi: <b>{escape_html(key)} = {escape_html(val_s)}</b>", cid)
        except Exception as e:
            tg_send_message(f"❌ Ayar hatası: {escape_html(str(e))}", cid)


def telegram_poll_loop() -> None:
    if not BOT_TOKEN:
        return
    offset = 0
    while True:
        try:
            r = HTTP_SESSION.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                params={"timeout": 25, "offset": offset},
                timeout=35.0,
            )
            if r.status_code == 200:
                for upd in r.json().get("result", []):
                    offset = max(offset, upd["update_id"] + 1)
                    msg = upd.get("message") or {}
                    text = msg.get("text") or ""
                    cid = str((msg.get("chat") or {}).get("id") or CHAT_ID)
                    if text.startswith("/"):
                        handle_telegram_command(text, cid)
        except Exception:
            time.sleep(3)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        with STATE_LOCK:
            body = json.dumps({
                "ok": True,
                "ws_connected": BOT_STATS["ws_connected"],
                "pairs": len(TICKER_MAP),
                "ath_atl_cache": len(ATH_ATL_CACHE),
                "signals": len(SIGNALS),
                "open_trades": sum(1 for t in TRADES if t["status"] == "OPEN"),
                "config": CONFIG,
            }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    load_state()
    load_futures_exchange_info()

    try:
        rows = binance_spot_get("/api/v3/ticker/24hr", timeout=10.0)
        if isinstance(rows, list):
            norm = []
            for r in rows:
                norm.append({
                    "s": r.get("symbol"),
                    "c": r.get("lastPrice"),
                    "q": r.get("quoteVolume"),
                    "h": r.get("highPrice"),
                    "l": r.get("lowPrice"),
                    "P": r.get("priceChangePercent"),
                })
            handle_ticker_batch(norm)
    except Exception:
        pass

    threading.Thread(target=binance_ws_loop, daemon=True).start()
    threading.Thread(target=ath_atl_cache_loop, daemon=True).start()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    threading.Thread(target=telegram_poll_loop, daemon=True).start()

    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"Spot Pump Bot active on port {PORT} (RSI>={CONFIG['rsi_min']}, TP/SL={CONFIG['tp_pct']}%/{CONFIG['sl_pct']}%, Max={CONFIG['max_open_trades']})")
    server.serve_forever()


if __name__ == "__main__":
    main()

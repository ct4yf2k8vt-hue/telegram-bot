#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Render Spot Crypto Bot (8 Motor - Tam Turkce & Tekilleştirilmiş Pump)
--------------------------------------------------------------------
1) 0-7 dk Spot Anlik Pump Dedektoru (Sade, ayni coinden sadece 1 defa sinyal)
2) W1 Tum Zamanlarin En Dusugu (ATL) (Fiyat + Hacim$M + RSI)
3) 4h Akis / Emir Defteri Duvari & POC Analizi (Spot Duvar + Fib/POC/Borsa Senkron)
4) Gunun Zaferi Karti (21:00 TR & /zafer)
5) Son 24 Saatin TOP 5 Hareketi Karti (23:00 TR & /top24)
6) 1D MA200 (SMA200) Kirilim & Dusus (Ters kirilima kadar ayni coinden tekrar sinyal atmaz)
7) PABS Tarzi Spot Alim Bolgesi Sinyali (#seq, Alim Bolgesi bandi, Stop, TP1-TP4)
8) Binance Resmi Duyuru & Haber Takibi (Listeleme, Token Merge/Swap, Cekim, Delist)

Harici paket (pip install) gerektirmez — saf Python 3 standart kutuphanesi.
"""

import os
import re
import time
import json
import uuid
import zlib
import struct
import threading
import urllib.request
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

# ==============================================================================
# AYARLAR & ENV
# ==============================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "1623907197").strip()
PORT = int(os.getenv("PORT", "10000"))

TR_TZ = timezone(timedelta(hours=3))

BINANCE_MIRRORS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]

SIGNALS_STATE_FILE = "/tmp/spot_bot_signals_v5.json"
PUMP_SEEN_FILE = "/tmp/spot_pump_seen_v5.json"
MA200_STATE_FILE = "/tmp/spot_ma200_state_v5.json"
PABS_SEQ_FILE = "/tmp/pabs_seq_v5.json"
BINANCE_NEWS_SEEN_FILE = "/tmp/binance_news_seen_v5.json"

# Motor 1: 0-7 dk Spot Pump (0 = Ayni coinden sadece 1 defa sinyal gelir)
PUMP_SCAN_SEC = 20
PUMP_WINDOW_SEC = 420
PUMP_USDT_PCT = 4.5
PUMP_BTC_BNB_PCT = 4.0
PUMP_MIN_VOL_USDT = 150_000.0
PUMP_MIN_VOL_BTC = 3.0
PUMP_MIN_VOL_BNB = 250.0
PUMP_REPEAT_HOURS = int(os.getenv("PUMP_REPEAT_HOURS", "0"))

# Motor 2: W1 ATL
ATL_SCAN_SEC = 300
ATL_MIN_VOL_USDT = 500_000.0
ATL_MIN_WEEKS = 12
ATL_COOLDOWN_SEC = 43200

# Motor 3: 4h Emir Defteri Duvari & POC
FLOW_SCAN_SEC = 180
FLOW_TOP_LIMIT = 45
FLOW_MIN_VOL_USDT = 2_000_000.0
FLOW_MIN_WALL_USD = 120_000.0
FLOW_COOLDOWN_SEC = 7200

# Motor 6: 1D MA200 (SMA200 - Kilitli)
MA200_SCAN_SEC = 300
MA200_MIN_VOL_USDT = 1_500_000.0
MA200_BUFFER_PCT = 0.25

# Motor 7: PABS Spot Alim Bolgesi
PABS_SCAN_SEC = 240
PABS_TOP_LIMIT = 45
PABS_MIN_VOL_USDT = 2_000_000.0
PABS_COOLDOWN_SEC = 14400

# Motor 8: Binance Duyuru Takibi
NEWS_SCAN_SEC = 90
BINANCE_CATALOGS = [
    (48, "YENİ LİSTELEME"),
    (49, "BİNANCE DUYURU / HABER"),
    (161, "DELİST / ÇİFT KALDIRMA"),
]

_STATE_LOCK = threading.Lock()
_PRICE_HISTORY = {}
_PUMP_SENT = {}                # {base_coin: first_sent_ts} -> coin bazli kilit
_ATL_CACHE = {}
_ATL_COOLDOWN = {}
_FLOW_COOLDOWN = {}
_PABS_COOLDOWN = {}
_SIGNALS_LOG = []
_MA200_STATE = {}
_PABS_SEQ = 48670
_NEWS_SEEN = set()
_NEWS_SEEDED = False
_LAST_REPORT_DAYS = {"win": "", "top24": ""}


# ==============================================================================
# YARDIMCI FORMATLAMA & DURUM YONETIMI
# ==============================================================================
def fmt_price(p):
    if p is None or p <= 0:
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


def fmt_usd_compact(v):
    if v >= 1_000_000_000:
        return f"${v/1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"{v/1_000_000:.0f}M" if v >= 10_000_000 else f"{v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v/1_000:.0f}K"
    return f"{v:.0f}"


def fmt_vol_k(v):
    return f"{int(round(v / 1000.0))}K" if v >= 1000 else f"{int(round(v))}"


def extract_base_asset(sym):
    for q in ("USDT", "BTC", "BNB"):
        if sym.endswith(q):
            return sym[:-len(q)], q
    return sym, ""


def is_pump_locked(base, now_ts):
    last_ts = _PUMP_SENT.get(base)
    if last_ts is None:
        return False
    if PUMP_REPEAT_HOURS <= 0:
        return True
    return (now_ts - float(last_ts)) < (PUMP_REPEAT_HOURS * 3600)


def load_json_file(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def save_json_file(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def load_all_states():
    global _SIGNALS_LOG, _PUMP_SENT, _MA200_STATE, _PABS_SEQ, _NEWS_SEEN, _NEWS_SEEDED
    with _STATE_LOCK:
        _SIGNALS_LOG = load_json_file(SIGNALS_STATE_FILE, [])
        if not isinstance(_SIGNALS_LOG, list):
            _SIGNALS_LOG = []
        _PUMP_SENT = load_json_file(PUMP_SEEN_FILE, {})
        if not isinstance(_PUMP_SENT, dict):
            _PUMP_SENT = {}
        _MA200_STATE = load_json_file(MA200_STATE_FILE, {})
        if not isinstance(_MA200_STATE, dict):
            _MA200_STATE = {}
        seq_data = load_json_file(PABS_SEQ_FILE, {"seq": 48670})
        _PABS_SEQ = int(seq_data.get("seq", 48670)) if isinstance(seq_data, dict) else 48670
        seen_data = load_json_file(BINANCE_NEWS_SEEN_FILE, [])
        if isinstance(seen_data, list) and len(seen_data) > 0:
            _NEWS_SEEN = set(str(x) for x in seen_data)
            _NEWS_SEEDED = True


def record_signal_for_reports(symbol, side, entry_price, source_label):
    now_ts = time.time()
    base, _ = extract_base_asset(symbol)
    with _STATE_LOCK:
        _SIGNALS_LOG.append({
            "symbol": symbol,
            "base": base,
            "side": side,
            "entry": float(entry_price),
            "peak_price": float(entry_price),
            "peak_pct": 0.0,
            "entry_ts": now_ts,
            "peak_ts": now_ts,
            "source": source_label,
        })
        cutoff = now_ts - 48 * 3600
        _SIGNALS_LOG[:] = [s for s in _SIGNALS_LOG if s.get("entry_ts", 0) >= cutoff][-300:]
        save_json_file(SIGNALS_STATE_FILE, _SIGNALS_LOG)


def update_signals_peaks(price_map):
    now_ts = time.time()
    changed = False
    with _STATE_LOCK:
        for s in _SIGNALS_LOG:
            sym = s.get("symbol")
            cur = price_map.get(sym)
            if not cur or cur <= 0:
                continue
            entry = float(s.get("entry", 0) or 0)
            if entry <= 0:
                continue
            pct = ((entry - cur) / entry) * 100.0 if s.get("side") == "SHORT" else ((cur - entry) / entry) * 100.0
            if pct > float(s.get("peak_pct", 0.0)):
                s["peak_pct"] = round(pct, 2)
                s["peak_price"] = float(cur)
                s["peak_ts"] = now_ts
                changed = True
        if changed:
            save_json_file(SIGNALS_STATE_FILE, _SIGNALS_LOG)


# ==============================================================================
# HTTP & BINANCE / COKLU BORSA API
# ==============================================================================
def http_get_json(url, timeout=10):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; CryptoMonitor/5.0)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def fetch_binance_json(path_and_query, timeout=10):
    last_err = None
    for base in BINANCE_MIRRORS:
        try:
            return http_get_json(f"{base}{path_and_query}", timeout=timeout)
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Binance mirror erisim hatasi: {last_err}")


def fetch_spot_tickers():
    data = fetch_binance_json("/api/v3/ticker/24hr", timeout=12)
    return data if isinstance(data, list) else []


def fetch_spot_klines(symbol, interval, limit=250):
    raw = fetch_binance_json(
        f"/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}",
        timeout=10,
    )
    out = []
    if isinstance(raw, list):
        for k in raw:
            out.append({
                "open_time": int(k[0]) // 1000,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "quote_vol": float(k[7]),
            })
    return out


def fetch_spot_depth(symbol, limit=100):
    return fetch_binance_json(f"/api/v3/depth?symbol={symbol}&limit={limit}", timeout=8)


def fetch_multi_exchange_sync(symbol, binance_price):
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    exchanges = ["Binance"]
    prices = [binance_price]

    try:
        d = http_get_json(f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}", timeout=4)
        lst = d.get("result", {}).get("list", [])
        if lst:
            p = float(lst[0].get("lastPrice", 0))
            if p > 0 and abs(p - binance_price) / binance_price < 0.02:
                exchanges.append("Bybit")
                prices.append(p)
    except Exception:
        pass

    try:
        d = http_get_json(f"https://www.okx.com/api/v5/market/ticker?instId={base}-USDT", timeout=4)
        lst = d.get("data", [])
        if lst:
            p = float(lst[0].get("last", 0))
            if p > 0 and abs(p - binance_price) / binance_price < 0.02:
                exchanges.append("OKX")
                prices.append(p)
    except Exception:
        pass

    try:
        d = http_get_json(f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={base}_USDT", timeout=4)
        if isinstance(d, list) and d:
            p = float(d[0].get("last", 0))
            if p > 0 and abs(p - binance_price) / binance_price < 0.02:
                exchanges.append("Gate")
                prices.append(p)
    except Exception:
        pass

    return exchanges, prices


# ==============================================================================
# SAF PYTHON PNG GRAFIK MOTORU
# ==============================================================================
FONT_5X7 = {
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    ",": ["00000", "00000", "00000", "00000", "00000", "01100", "01000"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    "+": ["00000", "00100", "00100", "11111", "00100", "00100", "00000"],
    "/": ["00001", "00010", "00010", "00100", "01000", "01000", "10000"],
    "%": ["11001", "11010", "00010", "00100", "01000", "01011", "10011"],
    "$": ["00100", "01111", "10100", "01110", "00101", "11110", "00100"],
    "#": ["01010", "11111", "01010", "01010", "11111", "01010", "00000"],
    "(": ["00010", "00100", "01000", "01000", "01000", "00100", "00010"],
    ")": ["01000", "00100", "00010", "00010", "00010", "00100", "01000"],
    "|": ["00100", "00100", "00100", "00100", "00100", "00100", "00100"],
    "_": ["00000", "00000", "00000", "00000", "00000", "00000", "11111"],
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00110", "01000", "10000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "D": ["11100", "10010", "10001", "10001", "10001", "10010", "11100"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01111"],
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
}

TR_ASCII_MAP = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")


class PngCanvas:
    def __init__(self, width, height, bg=(12, 16, 24)):
        self.w = width
        self.h = height
        r, g, b = bg
        self.buf = bytearray([r, g, b] * (width * height))

    def set_px(self, x, y, color, alpha=1.0):
        if x < 0 or y < 0 or x >= self.w or y >= self.h:
            return
        idx = (y * self.w + x) * 3
        if alpha >= 0.999:
            self.buf[idx] = color[0]
            self.buf[idx + 1] = color[1]
            self.buf[idx + 2] = color[2]
        else:
            inv = 1.0 - alpha
            self.buf[idx] = int(self.buf[idx] * inv + color[0] * alpha)
            self.buf[idx + 1] = int(self.buf[idx + 1] * inv + color[1] * alpha)
            self.buf[idx + 2] = int(self.buf[idx + 2] * inv + color[2] * alpha)

    def fill_rect(self, x1, y1, x2, y2, color, alpha=1.0):
        x1 = max(0, min(self.w - 1, int(x1)))
        x2 = max(0, min(self.w - 1, int(x2)))
        y1 = max(0, min(self.h - 1, int(y1)))
        y2 = max(0, min(self.h - 1, int(y2)))
        if x2 < x1 or y2 < y1:
            return
        for y in range(y1, y2 + 1):
            for x in range(x1, x2 + 1):
                self.set_px(x, y, color, alpha)

    def draw_rect_outline(self, x1, y1, x2, y2, color, alpha=1.0):
        self.draw_line(x1, y1, x2, y1, color, alpha=alpha)
        self.draw_line(x1, y2, x2, y2, color, alpha=alpha)
        self.draw_line(x1, y1, x1, y2, color, alpha=alpha)
        self.draw_line(x2, y1, x2, y2, color, alpha=alpha)

    def draw_line(self, x1, y1, x2, y2, color, thickness=1, alpha=1.0, dashed=False):
        x1, y1, x2, y2 = int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))
        steps = max(abs(x2 - x1), abs(y2 - y1), 1)
        for i in range(steps + 1):
            if dashed and (i // 5) % 2 == 1:
                continue
            t = i / steps
            x = int(round(x1 + (x2 - x1) * t))
            y = int(round(y1 + (y2 - y1) * t))
            if thickness <= 1:
                self.set_px(x, y, color, alpha)
            else:
                rad = thickness // 2
                for oy in range(-rad, rad + 1):
                    for ox in range(-rad, rad + 1):
                        self.set_px(x + ox, y + oy, color, alpha)

    def fill_circle(self, cx, cy, r, color, alpha=1.0):
        r2 = r * r
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dy * dy <= r2:
                    self.set_px(int(cx + dx), int(cy + dy), color, alpha)

    def draw_text(self, x, y, text, color=(235, 240, 248), scale=1):
        s = str(text).translate(TR_ASCII_MAP).upper()
        cx, cy = int(x), int(y)
        for ch in s:
            glyph = FONT_5X7.get(ch, FONT_5X7[" "])
            for row_i, row in enumerate(glyph):
                for col_i, bit in enumerate(row):
                    if bit == "1":
                        if scale == 1:
                            self.set_px(cx + col_i, cy + row_i, color)
                        else:
                            self.fill_rect(
                                cx + col_i * scale,
                                cy + row_i * scale,
                                cx + (col_i + 1) * scale - 1,
                                cy + (row_i + 1) * scale - 1,
                                color,
                            )
            cx += 6 * scale

    def text_width(self, text, scale=1):
        return len(str(text)) * 6 * scale

    def draw_pill(self, x, y, text, bg_color, fg_color=(255, 255, 255), scale=1, pad_x=5, pad_y=3):
        tw = self.text_width(text, scale=scale)
        th = 7 * scale
        self.fill_rect(x, y, x + tw + pad_x * 2, y + th + pad_y * 2, bg_color)
        self.draw_text(x + pad_x, y + pad_y, text, color=fg_color, scale=scale)
        return tw + pad_x * 2

    def to_png_bytes(self):
        raw_rows = []
        stride = self.w * 3
        for y in range(self.h):
            start = y * stride
            raw_rows.append(b"\x00" + bytes(self.buf[start:start + stride]))
        compressed = zlib.compress(b"".join(raw_rows), level=6)

        def chunk(tag, data):
            return (
                struct.pack("!I", len(data))
                + tag
                + data
                + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            )

        ihdr = struct.pack("!IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", compressed)
            + chunk(b"IEND", b"")
        )


def calc_rsi_series(closes, period=14):
    if len(closes) < period + 2:
        return [50.0] * len(closes)
    rsis = [50.0] * len(closes)
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    for i in range(period, len(closes)):
        if i > period:
            diff = closes[i] - closes[i - 1]
            avg_g = (avg_g * (period - 1) + max(diff, 0.0)) / period
            avg_l = (avg_l * (period - 1) + max(-diff, 0.0)) / period
        if avg_l == 0:
            rsis[i] = 100.0 if avg_g > 0 else 50.0
        else:
            rsis[i] = 100.0 - (100.0 / (1.0 + avg_g / avg_l))
    return rsis


def prevent_label_overlaps(items, min_gap=18, y_min=48, y_max=430):
    ordered = sorted(items, key=lambda it: it["y"])
    last_y = y_min - min_gap
    for it in ordered:
        yy = max(it["y"], last_y + min_gap)
        yy = min(yy, y_max)
        it["label_y"] = yy
        last_y = yy
    return ordered


# ==============================================================================
# GRAFIKLER (1 - 7)
# ==============================================================================
def render_pump_chart_png(symbol, quote, candles_1m, start_price, cur_price, pct):
    W, H = 900, 480
    cv = PngCanvas(W, H, bg=(12, 16, 24))
    pad_l, pad_r, pad_t, pad_b = 32, 125, 58, 42
    vol_h = 75
    chart_h = H - pad_t - pad_b - vol_h - 14
    chart_w = W - pad_l - pad_r

    cv.fill_rect(0, 0, W - 1, 44, (18, 24, 36))
    cv.draw_text(22, 14, f"{symbol} | 1M SPOT PUMP", color=(240, 245, 255), scale=2)
    cv.draw_pill(W - 180, 10, f"+{pct:.2f}%", bg_color=(16, 155, 92), scale=2)

    bars = candles_1m[-45:] if len(candles_1m) >= 10 else candles_1m
    if not bars:
        return cv.to_png_bytes()

    lo = min(min(b["low"] for b in bars), start_price) * 0.997
    hi = max(max(b["high"] for b in bars), cur_price) * 1.003
    if hi <= lo:
        hi = lo * 1.01
    max_vol = max((b["volume"] for b in bars), default=1.0) or 1.0

    def py(val):
        return int(pad_t + (hi - val) / (hi - lo) * chart_h)

    for i in range(5):
        gy = int(pad_t + chart_h * i / 4)
        cv.draw_line(pad_l, gy, pad_l + chart_w, gy, (28, 36, 50))
        gv = hi - (hi - lo) * i / 4
        cv.draw_text(pad_l + chart_w + 8, gy - 3, fmt_price(gv), color=(140, 155, 175), scale=1)

    step = chart_w / max(len(bars), 1)
    bw = max(3, int(step * 0.62))
    vol_base = pad_t + chart_h + 14 + vol_h
    for idx, b in enumerate(bars):
        cx = int(pad_l + (idx + 0.5) * step)
        up = b["close"] >= b["open"]
        col = (22, 185, 118) if up else (225, 72, 82)
        cv.draw_line(cx, py(b["high"]), cx, py(b["low"]), col)
        y1, y2 = py(b["open"]), py(b["close"])
        if abs(y2 - y1) < 2:
            y2 = y1 + 2
        cv.fill_rect(cx - bw // 2, min(y1, y2), cx + bw // 2, max(y1, y2), col)
        vh = int((b["volume"] / max_vol) * vol_h)
        cv.fill_rect(cx - bw // 2, vol_base - vh, cx + bw // 2, vol_base, col, alpha=0.65)

    y_start, y_cur = py(start_price), py(cur_price)
    cv.draw_line(pad_l, y_start, pad_l + chart_w, y_start, (245, 185, 65), dashed=True)
    cv.draw_line(pad_l, y_cur, pad_l + chart_w, y_cur, (30, 215, 140), dashed=True)
    tags = prevent_label_overlaps([
        {"y": y_cur, "txt": f"ANLIK {fmt_price(cur_price)}", "bg": (16, 155, 92)},
        {"y": y_start, "txt": f"BASL {fmt_price(start_price)}", "bg": (180, 120, 25)},
    ], min_gap=22, y_min=pad_t, y_max=pad_t + chart_h - 16)
    for t in tags:
        cv.draw_pill(pad_l + chart_w + 4, t["label_y"] - 6, t["txt"], bg_color=t["bg"], scale=1)

    return cv.to_png_bytes()


def render_atl_w1_chart_png(symbol, candles_w1, cur_price):
    W, H = 900, 560
    cv = PngCanvas(W, H, bg=(18, 20, 24))
    pad_l, pad_r, pad_t = 76, 30, 44
    price_h = 285
    vol_top = pad_t + price_h + 24
    vol_h = 80
    rsi_top = vol_top + vol_h + 20
    rsi_h = 65
    chart_w = W - pad_l - pad_r

    cv.draw_text(W // 2 - 110, 14, f"{symbol} W1 BINANCE", color=(235, 240, 245), scale=2)
    cv.draw_text(8, pad_t + price_h // 2 - 10, "FIYAT", color=(150, 160, 175), scale=1)
    cv.draw_text(8, vol_top + vol_h // 2 - 4, "HACIM$M", color=(150, 160, 175), scale=1)
    cv.draw_text(8, rsi_top + rsi_h // 2 - 4, "RSI", color=(150, 160, 175), scale=1)

    bars = candles_w1[-55:]
    lo = min(min(b["low"] for b in bars), cur_price) * 0.96
    hi = max(max(b["high"] for b in bars), cur_price) * 1.03
    if hi <= lo:
        hi = lo * 1.05
    max_vol_usd = max((b["quote_vol"] for b in bars), default=1.0) or 1.0
    rsis = calc_rsi_series([b["close"] for b in candles_w1], 14)[-len(bars):]

    def py(v):
        return int(pad_t + (hi - v) / (hi - lo) * price_h)

    cv.draw_rect_outline(pad_l, pad_t, pad_l + chart_w, pad_t + price_h, (60, 66, 75))
    cv.draw_rect_outline(pad_l, vol_top, pad_l + chart_w, vol_top + vol_h, (60, 66, 75))
    cv.draw_rect_outline(pad_l, rsi_top, pad_l + chart_w, rsi_top + rsi_h, (60, 66, 75))

    for i in range(6):
        gy = int(pad_t + price_h * i / 5)
        cv.draw_line(pad_l, gy, pad_l + chart_w, gy, (36, 40, 48))
        gv = hi - (hi - lo) * i / 5
        cv.draw_text(pad_l - 62, gy - 3, fmt_price(gv)[:9], color=(160, 170, 180), scale=1)

    step = chart_w / max(len(bars), 1)
    bw = max(3, int(step * 0.65))
    for i, b in enumerate(bars):
        cx = int(pad_l + (i + 0.5) * step)
        up = b["close"] >= b["open"]
        col = (18, 168, 128) if up else (232, 65, 78)
        cv.draw_line(cx, py(b["high"]), cx, py(b["low"]), col)
        y1, y2 = py(b["open"]), py(b["close"])
        if abs(y2 - y1) < 2:
            y2 = y1 + 2
        cv.fill_rect(cx - bw // 2, min(y1, y2), cx + bw // 2, max(y1, y2), col)
        vh = int((b["quote_vol"] / max_vol_usd) * vol_h)
        cv.fill_rect(cx - bw // 2, vol_top + vol_h - vh, cx + bw // 2, vol_top + vol_h, col)

    for i in range(1, len(rsis)):
        x1 = int(pad_l + (i - 0.5) * step)
        x2 = int(pad_l + (i + 0.5) * step)
        y1 = int(rsi_top + (100.0 - rsis[i - 1]) / 100.0 * rsi_h)
        y2 = int(rsi_top + (100.0 - rsis[i]) / 100.0 * rsi_h)
        cv.draw_line(x1, y1, x2, y2, (26, 185, 145), thickness=2)

    cv.draw_text(pad_l + chart_w // 2 - 75, pad_t + price_h // 2, "DIP RADAR BOT", color=(90, 96, 108), scale=1)
    return cv.to_png_bytes()


def render_flow_structure_png(symbol, candles_4h, flow):
    W, H = 960, 560
    cv = PngCanvas(W, H, bg=(10, 13, 22))
    pad_l, pad_r, pad_t, pad_b = 58, 220, 62, 42
    vol_h = 85
    chart_h = H - pad_t - pad_b - vol_h - 14
    chart_w = W - pad_l - pad_r

    bias = flow["bias"]
    base = symbol.replace("USDT", "")
    cv.fill_rect(0, 0, W - 1, 52, (16, 20, 32))
    cv.draw_text(18, 12, base, color=(245, 248, 255), scale=2)
    badge_bg = (16, 150, 92) if bias == "LONG" else (195, 55, 68)
    badge_txt = "YUKARI KIRILIM" if bias == "LONG" else "ERKEN KIRILIM"
    cv.draw_pill(110, 12, badge_txt, bg_color=badge_bg, scale=1)
    cv.draw_text(18, 34, "4H YAPI VE EMIR DEFTERI AKISI • COKLU BORSA SENKRON", color=(140, 155, 178), scale=1)
    cv.draw_text(W - 210, 14, "AKIS TARAYICI", color=(205, 215, 235), scale=2)

    bars = candles_4h[-56:]
    levels = [flow["cur_price"], flow["tp1"], flow["tp2"], flow["tp3"], flow["stop_tight"], flow["stop_wide"], flow["poc"], flow["trigger"]]
    lo = min(min(b["low"] for b in bars), min(levels)) * 0.992
    hi = max(max(b["high"] for b in bars), max(levels)) * 1.008
    if hi <= lo:
        hi = lo * 1.02
    max_vol = max((b["volume"] for b in bars), default=1.0) or 1.0

    def py(val):
        return int(pad_t + (hi - val) / (hi - lo) * chart_h)

    for i in range(6):
        gy = int(pad_t + chart_h * i / 5)
        cv.draw_line(pad_l, gy, pad_l + chart_w, gy, (24, 30, 44))
        gv = hi - (hi - lo) * i / 5
        cv.draw_text(6, gy - 3, fmt_price(gv)[:7], color=(130, 145, 165), scale=1)

    step = chart_w / max(len(bars), 1)
    bw = max(3, int(step * 0.62))
    vol_base = pad_t + chart_h + 14 + vol_h
    for i, b in enumerate(bars):
        cx = int(pad_l + (i + 0.5) * step)
        up = b["close"] >= b["open"]
        col = (20, 185, 120) if up else (225, 68, 82)
        cv.draw_line(cx, py(b["high"]), cx, py(b["low"]), col)
        y1, y2 = py(b["open"]), py(b["close"])
        if abs(y2 - y1) < 2:
            y2 = y1 + 2
        cv.fill_rect(cx - bw // 2, min(y1, y2), cx + bw // 2, max(y1, y2), col)
        vh = int((b["volume"] / max_vol) * vol_h)
        cv.fill_rect(cx - bw // 2, vol_base - vh, cx + bw // 2, vol_base, col, alpha=0.55)

    tp_col = (18, 162, 96) if bias == "LONG" else (210, 52, 68)
    stop_col = (190, 50, 62) if bias == "LONG" else (22, 145, 92)
    lines = [
        ("stop_wide", flow["stop_wide"], f"GENIS STOP {fmt_price(flow['stop_wide'])}", (80, 120, 80)),
        ("stop_tight", flow["stop_tight"], f"DAR STOP {fmt_price(flow['stop_tight'])}", stop_col),
        ("cur", flow["cur_price"], f"ANLIK {fmt_price(flow['cur_price'])}", (70, 95, 140)),
        ("trig", flow["trigger"], f"TETIK {fmt_price(flow['trigger'])}", (140, 110, 40)),
        ("tp1", flow["tp1"], f"H1 {fmt_price(flow['tp1'])} ({flow['tp1_pct']:+.1f}%)", tp_col),
        ("tp2", flow["tp2"], f"H2 {fmt_price(flow['tp2'])} ({flow['tp2_pct']:+.1f}%)", tp_col),
        ("tp3", flow["tp3"], f"H3 {fmt_price(flow['tp3'])} ({flow['tp3_pct']:+.1f}%)", tp_col),
    ]
    items = []
    for key, val, txt, col in lines:
        yy = py(val)
        cv.draw_line(pad_l, yy, pad_l + chart_w, yy, col, dashed=(key != "cur"))
        items.append({"y": yy, "txt": txt, "bg": col})
    prevent_label_overlaps(items, min_gap=20, y_min=pad_t, y_max=pad_t + chart_h - 14)
    for it in items:
        cv.draw_pill(pad_l + chart_w + 6, it["label_y"] - 6, it["txt"], bg_color=it["bg"], scale=1)

    return cv.to_png_bytes()


def render_win_of_day_png(best_sig, candles_15m, total_tracked=1240):
    W, H = 820, 560
    cv = PngCanvas(W, H, bg=(18, 16, 34))
    cv.fill_rect(28, 22, W - 28, H - 22, (24, 21, 46))
    cv.draw_rect_outline(28, 22, W - 28, H - 22, (72, 58, 130))
    cv.draw_pill(52, 42, "AKIS TARAYICI", bg_color=(98, 70, 210), scale=1)
    cv.draw_pill(W - 190, 42, "GUNUN ZAFERI", bg_color=(45, 38, 85), scale=1)

    base = best_sig["base"]
    pct = best_sig["peak_pct"]
    dur_h = max(0.1, (best_sig["peak_ts"] - best_sig["entry_ts"]) / 3600.0)
    cv.draw_text(52, 86, f"{base} ({best_sig['side']})", color=(248, 250, 255), scale=3)
    cv.draw_text(W - 290, 82, f"+{pct:.1f}%", color=(48, 228, 135), scale=4)
    cv.draw_text(W - 280, 120, f"SINYALDEN {dur_h:.1f} SAATTE", color=(155, 165, 195), scale=1)

    gx1, gy1, gx2, gy2 = 52, 150, W - 52, 400
    cv.fill_rect(gx1, gy1, gx2, gy2, (16, 14, 30))
    cv.draw_rect_outline(gx1, gy1, gx2, gy2, (52, 45, 92))
    closes = [b["close"] for b in candles_15m[-48:]] if candles_15m else [best_sig["entry"], best_sig["peak_price"]]
    lo, hi = min(closes) * 0.985, max(closes) * 1.015
    if hi <= lo:
        hi = lo * 1.02

    pts = []
    for i, val in enumerate(closes):
        px = int(gx1 + 14 + (gx2 - gx1 - 28) * i / max(len(closes) - 1, 1))
        py_v = int(gy1 + 18 + (hi - val) / (hi - lo) * (gy2 - gy1 - 36))
        pts.append((px, py_v))
    for i in range(1, len(pts)):
        cv.draw_line(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1], (112, 102, 245), thickness=2)

    peak_idx = max(range(len(closes)), key=lambda i: closes[i])
    cv.fill_circle(pts[0][0], pts[0][1], 5, (145, 135, 255))
    cv.fill_circle(pts[peak_idx][0], pts[peak_idx][1], 6, (50, 232, 140))
    cv.draw_pill(min(pts[peak_idx][0] - 80, gx2 - 170), max(gy1 + 10, pts[peak_idx][1] - 26),
                 f"ZIRVE ${fmt_price(best_sig['peak_price'])}", bg_color=(24, 90, 58), scale=1)

    entry_t = datetime.fromtimestamp(best_sig["entry_ts"], TR_TZ).strftime("%H:%M")
    peak_t = datetime.fromtimestamp(best_sig["peak_ts"], TR_TZ).strftime("%H:%M")
    cv.draw_text(52, 430, f"{entry_t} TR SINYAL: ${fmt_price(best_sig['entry'])} -> {peak_t} ZIRVE: ${fmt_price(best_sig['peak_price'])}", color=(230, 235, 248), scale=1)
    cv.draw_text(52, 462, f"TAKIP EDILEN: {total_tracked} SINYAL | SPOT AKIS TARAMA", color=(140, 150, 180), scale=1)
    return cv.to_png_bytes()


def render_top24_png(top_list):
    W, H = 820, 580
    cv = PngCanvas(W, H, bg=(18, 16, 34))
    cv.fill_rect(28, 22, W - 28, H - 22, (24, 21, 46))
    cv.draw_rect_outline(28, 22, W - 28, H - 22, (72, 58, 130))
    cv.draw_pill(52, 42, "AKIS TARAYICI", bg_color=(98, 70, 210), scale=1)
    cv.draw_pill(W - 190, 42, "TOP 24 SAAT", bg_color=(45, 38, 85), scale=1)
    cv.draw_text(52, 84, "SON 24 SAATIN EN IYI HAREKETLERI", color=(245, 248, 255), scale=2)
    cv.draw_text(52, 112, "SINYAL ANINDAN ZIRVEYE MAKSIMUM YUKSELIS", color=(150, 160, 190), scale=1)

    y = 148
    for idx, item in enumerate(top_list[:5], 1):
        row_h = 68
        cv.fill_rect(52, y, W - 52, y + row_h, (36, 31, 72) if idx == 1 else (28, 25, 54))
        if idx == 1:
            cv.draw_rect_outline(52, y, W - 52, y + row_h, (112, 90, 200))
        cv.draw_text(72, y + 24, f"{idx}", color=(235, 195, 75) if idx == 1 else (160, 170, 195), scale=2)
        cv.draw_text(120, y + 14, f"{item['base']} ({item['side']})", color=(245, 248, 255), scale=2)
        dur_h = max(0.1, (item["peak_ts"] - item["entry_ts"]) / 3600.0)
        cv.draw_text(120, y + 40, f"{dur_h:.1f} SAATTE ZIRVE • ${fmt_price(item['peak_price'])}", color=(145, 155, 185), scale=1)
        cv.draw_text(W - 225, y + 18, f"+{item['peak_pct']:.1f}%", color=(52, 232, 138), scale=3)
        y += row_h + 12
    return cv.to_png_bytes()


def render_ma200_chart_png(symbol, candles_1d, ma200_series, direction, cur_price, ma_now):
    W, H = 920, 500
    cv = PngCanvas(W, H, bg=(11, 15, 22))
    pad_l, pad_r, pad_t, pad_b = 32, 145, 58, 40
    chart_h = H - pad_t - pad_b
    chart_w = W - pad_l - pad_r

    bars = candles_1d[-90:]
    mas = ma200_series[-90:]
    cv.fill_rect(0, 0, W - 1, 46, (18, 23, 35))
    cv.draw_text(20, 14, f"{symbol} | 1D MA200 KIRILIMI", color=(240, 245, 255), scale=2)
    badge_col = (16, 155, 92) if direction == "ABOVE" else (210, 52, 68)
    badge_txt = "MA200 YUKARI KIRILIM" if direction == "ABOVE" else "MA200 ASAGI DUSUS"
    cv.draw_pill(W - 255, 11, badge_txt, bg_color=badge_col, scale=1)

    valid_ma = [m for m in mas if m is not None]
    lo = min(min(b["low"] for b in bars), min(valid_ma) if valid_ma else cur_price) * 0.97
    hi = max(max(b["high"] for b in bars), max(valid_ma) if valid_ma else cur_price) * 1.03
    if hi <= lo:
        hi = lo * 1.02

    def py(val):
        return int(pad_t + (hi - val) / (hi - lo) * chart_h)

    for i in range(6):
        gy = int(pad_t + chart_h * i / 5)
        cv.draw_line(pad_l, gy, pad_l + chart_w, gy, (24, 32, 46))
        gv = hi - (hi - lo) * i / 5
        cv.draw_text(pad_l + chart_w + 8, gy - 3, fmt_price(gv), color=(130, 145, 165), scale=1)

    step = chart_w / max(len(bars), 1)
    bw = max(3, int(step * 0.6))
    for i, b in enumerate(bars):
        cx = int(pad_l + (i + 0.5) * step)
        up = b["close"] >= b["open"]
        col = (22, 185, 118) if up else (225, 68, 80)
        cv.draw_line(cx, py(b["high"]), cx, py(b["low"]), col)
        y1, y2 = py(b["open"]), py(b["close"])
        if abs(y2 - y1) < 2:
            y2 = y1 + 2
        cv.fill_rect(cx - bw // 2, min(y1, y2), cx + bw // 2, max(y1, y2), col)

    ma_pts = [(int(pad_l + (i + 0.5) * step), py(m)) for i, m in enumerate(mas) if m is not None]
    for i in range(1, len(ma_pts)):
        cv.draw_line(ma_pts[i - 1][0], ma_pts[i - 1][1], ma_pts[i][0], ma_pts[i][1], (250, 190, 45), thickness=2)

    y_cur, y_ma = py(cur_price), py(ma_now)
    cv.draw_line(pad_l, y_cur, pad_l + chart_w, y_cur, badge_col, dashed=True)
    tags = prevent_label_overlaps([
        {"y": y_cur, "txt": f"FIYAT {fmt_price(cur_price)}", "bg": badge_col},
        {"y": y_ma, "txt": f"MA200 {fmt_price(ma_now)}", "bg": (185, 130, 20)},
    ], min_gap=20, y_min=pad_t, y_max=pad_t + chart_h - 14)
    for t in tags:
        cv.draw_pill(pad_l + chart_w + 6, t["label_y"] - 6, t["txt"], bg_color=t["bg"], scale=1)

    return cv.to_png_bytes()


def render_pabs_chart_png(symbol, candles_1h, buy_low, buy_high, stop_loss, tp1):
    W, H = 920, 500
    cv = PngCanvas(W, H, bg=(6, 8, 11))
    pad_l, pad_r, pad_t, pad_b = 76, 28, 30, 36
    chart_h = H - pad_t - pad_b
    chart_w = W - pad_l - pad_r

    bars = candles_1h[-90:]
    lo = min(min(b["low"] for b in bars), stop_loss, buy_low) * 0.992
    hi = max(max(b["high"] for b in bars), tp1, buy_high) * 1.008
    if hi <= lo:
        hi = lo * 1.02

    def py(val):
        return int(pad_t + (hi - val) / (hi - lo) * chart_h)

    for i in range(9):
        gy = int(pad_t + chart_h * i / 8)
        cv.draw_line(pad_l, gy, pad_l + chart_w, gy, (20, 24, 30))
        gv = hi - (hi - lo) * i / 8
        cv.draw_text(8, gy - 3, fmt_price(gv)[:8], color=(135, 145, 160), scale=1)

    by1, by2 = min(py(buy_high), py(buy_low)), max(py(buy_high), py(buy_low))
    cv.fill_rect(pad_l, by1, pad_l + chart_w, by2, (28, 128, 52), alpha=0.28)
    cv.draw_line(pad_l, by1, pad_l + chart_w, by1, (45, 165, 72), alpha=0.55)
    cv.draw_line(pad_l, by2, pad_l + chart_w, by2, (45, 165, 72), alpha=0.55)

    step = chart_w / max(len(bars), 1)
    bw = max(3, int(step * 0.6))
    for i, b in enumerate(bars):
        cx = int(pad_l + (i + 0.5) * step)
        up = b["close"] >= b["open"]
        col = (60, 198, 72) if up else (206, 54, 118)
        cv.draw_line(cx, py(b["high"]), cx, py(b["low"]), col)
        y1, y2 = py(b["open"]), py(b["close"])
        if abs(y2 - y1) < 2:
            y2 = y1 + 2
        cv.fill_rect(cx - bw // 2, min(y1, y2), cx + bw // 2, max(y1, y2), col)

    y_tp1, y_stop = py(tp1), py(stop_loss)
    cv.draw_line(pad_l, y_tp1, pad_l + chart_w, y_tp1, (40, 175, 75), dashed=True)
    cv.draw_line(pad_l, y_stop, pad_l + chart_w, y_stop, (215, 45, 58), dashed=True)
    cv.draw_pill(8, max(pad_t, min(y_tp1 - 7, pad_t + chart_h - 32)), "TP 1", bg_color=(36, 158, 68), scale=1)
    cv.draw_pill(8, max(pad_t + 18, min(y_stop - 7, pad_t + chart_h - 14)), "STOP", bg_color=(205, 40, 52), scale=1)

    cv.draw_text(W - 155, pad_t + 8, symbol, color=(235, 240, 245), scale=2)
    cv.draw_text(W - 155, pad_t + 28, "ALIM BOLGESI", color=(90, 200, 110), scale=1)
    return cv.to_png_bytes()


# ==============================================================================
# TELEGRAM GONDERIM KATMANI
# ==============================================================================
def tg_send_text(text):
    if not BOT_TOKEN:
        print(f"[DRY-TEXT]\n{text}\n")
        return True
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[TG TEXT HATA] {e}")
        return False


def tg_send_photo(png_bytes, caption):
    if not BOT_TOKEN:
        print(f"[DRY-PHOTO] ({len(png_bytes)} bytes)\n{caption}\n")
        return True
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    boundary = uuid.uuid4().hex
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{CHAT_ID}\r\n".encode("utf-8"),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n".encode("utf-8"),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"parse_mode\"\r\n\r\nHTML\r\n".encode("utf-8"),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"chart.png\"\r\nContent-Type: image/png\r\n\r\n".encode("utf-8")
        + png_bytes
        + f"\r\n--{boundary}--\r\n".encode("utf-8"),
    ]
    req = urllib.request.Request(
        url,
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[TG PHOTO HATA] {e}")
        return False


# ==============================================================================
# MOTOR 1: 0-7 DK SPOT ANLIK PUMP (SADE & TEK SEFERLIK COIN KILIDI)
# ==============================================================================
def format_pump_message(symbol, quote, pct, minutes_used, start_price, cur_price, vol_24h):
    vol_str = f"{vol_24h:,.0f}" if quote == "USDT" else f"{vol_24h:,.2f}"
    return (
        f"⚡ <b>SPOT ANLIK PUMP DEDEKTORU — {symbol}</b>\n\n"
        f"📈 Degisim: <b>+{pct:.2f}%</b> ({minutes_used} dk icinde!)\n"
        f"💰 Baslangic: <code>{fmt_price(start_price)}</code>  ➡️  Anlik: <code>{fmt_price(cur_price)}</code>\n"
        f"💧 24s Parite Hacmi: {vol_str} ({quote})"
    )


def pump_worker():
    print("[MOTOR 1] 0-7 dk Spot Pump (Tek Sinyal Kilidi) baslatildi.")
    while True:
        try:
            tickers = fetch_spot_tickers()
            now_ts = time.time()
            price_map = {}
            for t in tickers:
                sym = t.get("symbol", "")
                if sym.endswith("USDT"):
                    quote, min_vol, th_pct = "USDT", PUMP_MIN_VOL_USDT, PUMP_USDT_PCT
                elif sym.endswith("BTC"):
                    quote, min_vol, th_pct = "BTC", PUMP_MIN_VOL_BTC, PUMP_BTC_BNB_PCT
                elif sym.endswith("BNB"):
                    quote, min_vol, th_pct = "BNB", PUMP_MIN_VOL_BNB, PUMP_BTC_BNB_PCT
                else:
                    continue
                if any(bad in sym for bad in ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT", "USDC", "FDUSD", "TUSD", "USDP")):
                    continue
                try:
                    cur = float(t.get("lastPrice", 0))
                    vol = float(t.get("quoteVolume", 0))
                except Exception:
                    continue
                if cur <= 0 or vol < min_vol:
                    continue
                if quote == "USDT":
                    price_map[sym] = cur

                base, _ = extract_base_asset(sym)
                with _STATE_LOCK:
                    locked = is_pump_locked(base, now_ts)
                if locked:
                    continue

                hist = _PRICE_HISTORY.setdefault(sym, [])
                hist.append((now_ts, cur))
                cutoff = now_ts - PUMP_WINDOW_SEC
                while hist and hist[0][0] < cutoff:
                    hist.pop(0)
                if len(hist) < 2:
                    continue

                base_ts, base_price = min(hist, key=lambda x: x[1])
                if base_price <= 0:
                    continue
                pct = ((cur - base_price) / base_price) * 100.0
                if pct >= th_pct:
                    mins = max(1, int(round((now_ts - base_ts) / 60.0)))
                    with _STATE_LOCK:
                        if is_pump_locked(base, now_ts):
                            continue
                        _PUMP_SENT[base] = now_ts
                        save_json_file(PUMP_SEEN_FILE, _PUMP_SENT)
                    _PRICE_HISTORY.pop(sym, None)
                    try:
                        png = render_pump_chart_png(sym, quote, fetch_spot_klines(sym, "1m", limit=50), base_price, cur, pct)
                    except Exception:
                        png = None
                    msg = format_pump_message(sym, quote, pct, mins, base_price, cur, vol)
                    tg_send_photo(png, msg) if png else tg_send_text(msg)
                    if quote == "USDT":
                        record_signal_for_reports(sym, "LONG", cur, "0-7dk Spot Pump")
            if price_map:
                update_signals_peaks(price_map)
        except Exception as e:
            print(f"[MOTOR 1 HATA] {e}")
        time.sleep(PUMP_SCAN_SEC)


# ==============================================================================
# MOTOR 2: W1 ATL
# ==============================================================================
def format_atl_message(symbol, cur_price, chg_24h, vol_24h):
    return (
        f"<b>#{symbol}</b> ${fmt_price(cur_price)} tüm zamanların en düşüğü 👎📛😭\n"
        f"24s {chg_24h:+.2f}% Hacim {fmt_usd_compact(vol_24h)}"
    )


def atl_worker():
    print("[MOTOR 2] W1 ATL baslatildi.")
    while True:
        try:
            tickers = fetch_spot_tickers()
            now_ts = time.time()
            for t in tickers:
                sym = t.get("symbol", "")
                if not sym.endswith("USDT") or any(b in sym for b in ("USDC", "FDUSD", "TUSD", "USDP", "UPUSDT", "DOWNUSDT")):
                    continue
                try:
                    cur, low_24h = float(t.get("lastPrice", 0)), float(t.get("lowPrice", 0))
                    vol, chg = float(t.get("quoteVolume", 0)), float(t.get("priceChangePercent", 0))
                except Exception:
                    continue
                if cur <= 0 or low_24h <= 0 or vol < ATL_MIN_VOL_USDT:
                    continue
                try:
                    cached = _ATL_CACHE.get(sym)
                    if cached and now_ts - cached["ts"] < 3600:
                        prev_atl, candles_w1 = cached["prev_atl"], cached["candles"]
                    else:
                        candles_w1 = fetch_spot_klines(sym, "1w", limit=1000)
                        if len(candles_w1) < ATL_MIN_WEEKS:
                            continue
                        prev_atl = min(c["low"] for c in candles_w1[:-1])
                        _ATL_CACHE[sym] = {"prev_atl": prev_atl, "candles": candles_w1, "ts": now_ts}
                        time.sleep(0.08)
                    if cur <= prev_atl * 1.003 and low_24h <= prev_atl:
                        cd = _ATL_COOLDOWN.get(sym)
                        if cd and not ((now_ts - cd["ts"]) >= ATL_COOLDOWN_SEC or cur <= cd["atl"] * 0.98):
                            continue
                        _ATL_COOLDOWN[sym] = {"ts": now_ts, "atl": cur}
                        tg_send_photo(render_atl_w1_chart_png(sym, candles_w1, cur), format_atl_message(sym, cur, chg, vol))
                except Exception:
                    continue
        except Exception as e:
            print(f"[MOTOR 2 HATA] {e}")
        time.sleep(ATL_SCAN_SEC)


# ==============================================================================
# MOTOR 3: 4H AKIS / EMIR DEFTERI DUVARI & POC
# ==============================================================================
def analyze_flow_structure(symbol, cur_price, vol_24h, depth, candles_15m, candles_4h, candles_1d):
    if len(candles_15m) < 40 or len(candles_4h) < 30 or len(candles_1d) < 5:
        return None
    day_bars = candles_15m[-96:]
    d_lo, d_hi = min(b["low"] for b in day_bars), max(b["high"] for b in day_bars)
    if d_hi <= d_lo:
        return None
    buckets = 40
    step_p = (d_hi - d_lo) / buckets
    hist = [0.0] * buckets
    for b in day_bars:
        mid = (b["high"] + b["low"] + b["close"]) / 3.0
        hist[min(buckets - 1, max(0, int((mid - d_lo) / step_p)))] += b["quote_vol"]
    poc_idx = max(range(buckets), key=lambda i: hist[i])
    poc_price = d_lo + (poc_idx + 0.5) * step_p
    total_v = sum(hist) or 1.0
    acc, l_i, r_i = hist[poc_idx], poc_idx, poc_idx
    while acc / total_v < 0.70 and (l_i > 0 or r_i < buckets - 1):
        lv = hist[l_i - 1] if l_i > 0 else -1
        rv = hist[r_i + 1] if r_i < buckets - 1 else -1
        if rv >= lv and r_i < buckets - 1:
            r_i += 1
            acc += hist[r_i]
        elif l_i > 0:
            l_i -= 1
            acc += hist[l_i]
        else:
            break
    val_price, vah_price = d_lo + l_i * step_p, d_lo + (r_i + 1) * step_p

    best_bid_wall, best_ask_wall = (0.0, 0.0), (0.0, 0.0)
    bid_sum, ask_sum = 0.0, 0.0
    for p_s, q_s in depth.get("bids", []):
        p, q = float(p_s), float(q_s)
        usd = p * q
        if abs(cur_price - p) / cur_price <= 0.025:
            bid_sum += usd
            if usd > best_bid_wall[1]:
                best_bid_wall = (p, usd)
    for p_s, q_s in depth.get("asks", []):
        p, q = float(p_s), float(q_s)
        usd = p * q
        if abs(p - cur_price) / cur_price <= 0.025:
            ask_sum += usd
            if usd > best_ask_wall[1]:
                best_ask_wall = (p, usd)

    if best_ask_wall[1] >= FLOW_MIN_WALL_USD and cur_price <= best_ask_wall[0] * 1.004 and best_ask_wall[1] >= best_bid_wall[1] * 1.15:
        bias, wall_price, wall_usd, wall_type = "SHORT", best_ask_wall[0], best_ask_wall[1], "Ask (Satış) Duvarı"
    elif best_bid_wall[1] >= FLOW_MIN_WALL_USD and cur_price >= best_bid_wall[0] * 0.996 and best_bid_wall[1] >= best_ask_wall[1] * 1.15:
        bias, wall_price, wall_usd, wall_type = "LONG", best_bid_wall[0], best_bid_wall[1], "Bid (Alış) Duvarı"
    else:
        return None

    near_poc = abs(wall_price - poc_price) / poc_price <= 0.012
    daily_down = candles_1d[-1]["close"] < candles_1d[-4]["open"]
    daily_aligned = (bias == "SHORT" and daily_down) or (bias == "LONG" and not daily_down)
    sw_bars = candles_4h[-28:]
    sw_hi, sw_lo = max(b["high"] for b in sw_bars), min(b["low"] for b in sw_bars)
    rng = max(sw_hi - sw_lo, cur_price * 0.03)

    if bias == "SHORT":
        tp1, tp2 = min(cur_price * 0.97, cur_price - rng * 0.25), min(cur_price * 0.948, cur_price - rng * 0.45)
        tp3 = min(val_price, tp2 * 0.975)
        stop_tight, stop_wide, trigger = max(wall_price * 1.005, cur_price * 1.008), max(sw_hi * 1.003, cur_price * 1.045), cur_price * 0.992
    else:
        tp1, tp2 = max(cur_price * 1.03, cur_price + rng * 0.25), max(cur_price * 1.052, cur_price + rng * 0.45)
        tp3 = max(vah_price, tp2 * 1.025)
        stop_tight, stop_wide, trigger = min(wall_price * 0.995, cur_price * 0.992), min(sw_lo * 0.997, cur_price * 0.955), cur_price * 1.008

    return {
        "bias": bias, "cur_price": cur_price, "wall_price": wall_price, "wall_usd": wall_usd,
        "wall_type": wall_type, "poc": poc_price, "near_poc": near_poc,
        "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "tp1_pct": ((tp1 - cur_price) / cur_price) * 100.0,
        "tp2_pct": ((tp2 - cur_price) / cur_price) * 100.0,
        "tp3_pct": ((tp3 - cur_price) / cur_price) * 100.0,
        "stop_tight": stop_tight, "stop_wide": stop_wide,
        "stop_pct": ((stop_tight - cur_price) / cur_price) * 100.0,
        "trigger": trigger, "bid_sum": bid_sum, "ask_sum": ask_sum,
        "daily_aligned": daily_aligned, "vol_24h": vol_24h,
    }


def format_flow_message(symbol, flow, exchanges):
    base = symbol.replace("USDT", "")
    imbalance_txt = (
        "Alıcı tarafı hafif önde ama duvar sert" if flow["bid_sum"] >= flow["ask_sum"] and flow["bias"] == "SHORT"
        else "Satıcı tarafı hafif önde ama alış duvarı sert" if flow["ask_sum"] >= flow["bid_sum"] and flow["bias"] == "LONG"
        else "Spot akışı yönü destekliyor"
    )
    poc_txt = (
        "Üstelik burası sadece bir duvar değil — son 24 saatin en çok işlem gören hacim merkezi (POC) seviyesi. Hakem seviye."
        if flow["near_poc"] else f"Günlük hacim merkezi (POC) {fmt_price(flow['poc'])} bölgesinde konumlanıyor."
    )
    daily_txt = (
        "Günlük (1D) grafik aynı yönü gösteriyor — bu bir karşı-trend değil, trend yönünde akış."
        if flow["daily_aligned"] else "Günlük (1D) grafik henüz tam dönmedi — kısa vadeli yapısal fırsat."
    )
    cancel_word = "üstünde" if flow["bias"] == "SHORT" else "altında"
    hold_word = "altında" if flow["bias"] == "SHORT" else "üstünde"
    wall_dir = "yukarı sıçramasını tutuyor" if flow["bias"] == "SHORT" else "aşağı sarkmasını tutuyor"
    return (
        f"<b>#{symbol}</b>\n\n"
        f"<b>Fiyat:</b> <code>{fmt_price(flow['cur_price'])}</code>\n"
        f"<b>Hedefler:</b> <code>{fmt_price(flow['tp1'])}</code> ({flow['tp1_pct']:+.1f}%) → "
        f"<code>{fmt_price(flow['tp2'])}</code> ({flow['tp2_pct']:+.1f}%) → "
        f"<code>{fmt_price(flow['tp3'])}</code> ({flow['tp3_pct']:+.1f}%)\n"
        f"<b>İptal:</b> <code>{fmt_price(flow['stop_tight'])}</code> {cancel_word} ({flow['stop_pct']:+.1f}%)\n\n"
        f"{base} iki seviye arasında sıkışmış durumda ancak duvarlardan biri gerçekten tutuyor. "
        f"<b>{fmt_price(flow['wall_price'])}</b> seviyesindeki {flow['wall_type']} — tek bir kademede yoğunlaşmış <b>${flow['wall_usd']/1000:.0f}k</b> emir bulunuyor.\n\n"
        f"Şu an fiyatı {wall_dir}. {poc_txt}\n\n"
        f"Fiyat zaten bu seviyenin {hold_word}, spot dengesizlik zayıf ({imbalance_txt}). "
        f"Çoklu borsa akışı senkronize ({', '.join(exchanges)}), ayrışma yok. {daily_txt}\n\n"
        f"<b>Anahtar tetik:</b> <code>{fmt_price(flow['trigger'])}</code> {hold_word} kapanış/kalıcılık."
    )


def flow_structure_worker():
    print("[MOTOR 3] 4h Akis & Emir Defteri Duvari tarayicisi baslatildi.")
    while True:
        try:
            liq = []
            for t in fetch_spot_tickers():
                sym = t.get("symbol", "")
                if not sym.endswith("USDT") or any(b in sym for b in ("USDC", "FDUSD", "TUSD", "USDP", "UPUSDT", "DOWNUSDT")):
                    continue
                try:
                    cur, vol = float(t.get("lastPrice", 0)), float(t.get("quoteVolume", 0))
                except Exception:
                    continue
                if cur > 0 and vol >= FLOW_MIN_VOL_USDT:
                    liq.append((vol, sym, cur))
            liq.sort(reverse=True)
            now_ts = time.time()
            for vol, sym, cur in liq[:FLOW_TOP_LIMIT]:
                if now_ts - _FLOW_COOLDOWN.get(sym, 0) < FLOW_COOLDOWN_SEC:
                    continue
                try:
                    c4h = fetch_spot_klines(sym, "4h", limit=60)
                    flow = analyze_flow_structure(
                        sym, cur, vol, fetch_spot_depth(sym, limit=100),
                        fetch_spot_klines(sym, "15m", limit=100), c4h, fetch_spot_klines(sym, "1d", limit=10)
                    )
                    if flow:
                        exchanges, _ = fetch_multi_exchange_sync(sym, cur)
                        _FLOW_COOLDOWN[sym] = now_ts
                        tg_send_photo(render_flow_structure_png(sym, c4h, flow), format_flow_message(sym, flow, exchanges))
                        record_signal_for_reports(sym, flow["bias"], cur, "4h Akış Duvarı")
                    time.sleep(0.15)
                except Exception:
                    continue
        except Exception as e:
            print(f"[MOTOR 3 HATA] {e}")
        time.sleep(FLOW_SCAN_SEC)


# ==============================================================================
# MOTOR 4 & 5: GUNUN ZAFERI (21:00 TR) & TOP 24 (23:00 TR)
# ==============================================================================
def send_win_of_day_report(manual=False):
    with _STATE_LOCK:
        now_ts = time.time()
        recent = [s for s in _SIGNALS_LOG if now_ts - s.get("entry_ts", 0) <= 24 * 3600 and s.get("peak_pct", 0) > 0]
        total_tracked = max(len(_SIGNALS_LOG), 1)
    if not recent:
        if manual:
            tg_send_text("🏆 <b>Günün Zaferi</b>\n\nHenüz son 24 saatte kâra geçen kayıtlı sinyal oluşmadı.")
        return
    best = max(recent, key=lambda x: x.get("peak_pct", 0))
    try:
        c15m = fetch_spot_klines(best["symbol"], "15m", limit=60)
    except Exception:
        c15m = []
    entry_t = datetime.fromtimestamp(best["entry_ts"], TR_TZ).strftime("%H:%M")
    peak_t = datetime.fromtimestamp(best["peak_ts"], TR_TZ).strftime("%H:%M")
    dur_h = max(0.1, (best["peak_ts"] - best["entry_ts"]) / 3600.0)
    caption = (
        f"🏆 <b>Günün Zaferi: {best['base']} +{best['peak_pct']:.1f}%</b>\n\n"
        f"{entry_t} TR — tarayıcı sinyal verdi: <b>{best['symbol']} {best['side']}</b> @ <code>${fmt_price(best['entry'])}</code>\n"
        f"{peak_t} TR — zirve <code>${fmt_price(best['peak_price'])}</code>. Sinyalden itibaren {dur_h:.1f} saatte <b>+{best['peak_pct']:.1f}%</b> hareket.\n\n"
        f"#{best['base']} #AkisTarayici #Kripto"
    )
    tg_send_photo(render_win_of_day_png(best, c15m, total_tracked=total_tracked), caption)


def send_top24_report(manual=False):
    with _STATE_LOCK:
        now_ts = time.time()
        recent = [s for s in _SIGNALS_LOG if now_ts - s.get("entry_ts", 0) <= 24 * 3600 and s.get("peak_pct", 0) > 0]
    if not recent:
        if manual:
            tg_send_text("🔥 <b>Son 24 Saatin TOP 5 Hareketi</b>\n\nHenüz son 24 saatte listelenecek performans oluşmadı.")
        return
    best_per_sym = {}
    for s in recent:
        sym = s["symbol"]
        if sym not in best_per_sym or s["peak_pct"] > best_per_sym[sym]["peak_pct"]:
            best_per_sym[sym] = s
    top_list = sorted(best_per_sym.values(), key=lambda x: x["peak_pct"], reverse=True)[:5]
    medals = ["🥇", "🥈", "🥉", "🏅", "⭐"]
    lines = ["🔥 <b>Son 24 Saatin EN İYİ Hareketleri</b>\n", "sinyal anından zirveye maksimum yükseliş:\n"]
    tags = []
    for i, item in enumerate(top_list):
        dur_h = max(0.1, (item["peak_ts"] - item["entry_ts"]) / 3600.0)
        lines.append(f"{medals[i]} <b>{item['base']} +{item['peak_pct']:.1f}%</b> · {dur_h:.1f} saatte zirve")
        tags.append(f"#{item['base']}")
    lines.append("\n" + " ".join(tags))
    tg_send_photo(render_top24_png(top_list), "\n".join(lines))


def report_scheduler_worker():
    print("[MOTOR 4&5] Gunluk Zafer (21:00 TR) & Top24 (23:00 TR) baslatildi.")
    while True:
        try:
            now_tr = datetime.now(TR_TZ)
            day_key = now_tr.strftime("%Y-%m-%d")
            if now_tr.hour == 21 and now_tr.minute < 5 and _LAST_REPORT_DAYS["win"] != day_key:
                _LAST_REPORT_DAYS["win"] = day_key
                send_win_of_day_report(manual=False)
            if now_tr.hour == 23 and now_tr.minute < 5 and _LAST_REPORT_DAYS["top24"] != day_key:
                _LAST_REPORT_DAYS["top24"] = day_key
                send_top24_report(manual=False)
        except Exception as e:
            print(f"[MOTOR 4&5 HATA] {e}")
        time.sleep(30)


# ==============================================================================
# MOTOR 6: 1D MA200 (SMA200) KIRILIM & DUSUS (KILITLI)
# ==============================================================================
def compute_sma_series(closes, period=200):
    out = [None] * len(closes)
    if len(closes) < period:
        return out
    win_sum = sum(closes[:period])
    out[period - 1] = win_sum / period
    for i in range(period, len(closes)):
        win_sum += closes[i] - closes[i - period]
        out[i] = win_sum / period
    return out


def evaluate_ma200_cross(symbol, candles_1d, state_dict, buffer_pct=MA200_BUFFER_PCT):
    if len(candles_1d) < 203:
        return None
    closes = [c["close"] for c in candles_1d]
    sma200 = compute_sma_series(closes, 200)
    prev_close, last_close = closes[-3], closes[-2]
    ma_prev, ma_last = sma200[-3], sma200[-2]
    if ma_prev is None or ma_last is None or ma_last <= 0 or ma_prev <= 0:
        return None

    if last_close >= ma_last * (1.0 + buffer_pct / 100.0):
        new_pos = "ABOVE"
    elif last_close <= ma_last * (1.0 - buffer_pct / 100.0):
        new_pos = "BELOW"
    else:
        return None

    prev_state = state_dict.get(symbol)
    if prev_state is None:
        prev_pos_raw = "ABOVE" if prev_close >= ma_prev else "BELOW"
        state_dict[symbol] = new_pos
        if prev_pos_raw == new_pos:
            return None
        direction = new_pos
    elif prev_state == new_pos:
        return None
    else:
        state_dict[symbol] = new_pos
        direction = new_pos

    return {
        "direction": direction,
        "close_1d": last_close,
        "ma200": ma_last,
        "diff_pct": ((last_close - ma_last) / ma_last) * 100.0,
        "sma_series": sma200,
    }


def format_ma200_message(symbol, info, vol_24h):
    if info["direction"] == "ABOVE":
        title = f"🟢 <b>1G MA200 YUKARI KIRILIMI — #{symbol}</b>"
        desc = "Günlük mum kapanışı <b>200 Günlük MA</b> ortalamasının üzerine çıktı."
        lock_note = "🔒 <i>Kilit Aktif: MA200 altına tekrar düşüş kırılımı gelene kadar bu coinden yeni sinyal gelmeyecek.</i>"
    else:
        title = f"🔴 <b>1G MA200 AŞAĞI DÜŞÜŞ — #{symbol}</b>"
        desc = "Günlük mum kapanışı <b>200 Günlük MA</b> ortalamasının altına kırıldı."
        lock_note = "🔒 <i>Kilit Aktif: MA200 üzerine tekrar yukarı kırılım gelene kadar bu coinden yeni sinyal gelmeyecek.</i>"

    return (
        f"{title}\n\n"
        f"{desc}\n"
        f"💰 1G Kapanış: <code>${fmt_price(info['close_1d'])}</code>\n"
        f"📏 MA200 Değeri: <code>${fmt_price(info['ma200'])}</code> ({info['diff_pct']:+.2f}%)\n"
        f"💧 24s Hacim: {fmt_usd_compact(vol_24h)} USDT\n\n"
        f"{lock_note}"
    )


def ma200_worker():
    print("[MOTOR 6] 1D MA200 (SMA200) Kilitli Kirilim/Dusus baslatildi.")
    while True:
        try:
            liq = []
            for t in fetch_spot_tickers():
                sym = t.get("symbol", "")
                if not sym.endswith("USDT") or any(b in sym for b in ("USDC", "FDUSD", "TUSD", "USDP", "UPUSDT", "DOWNUSDT")):
                    continue
                try:
                    vol = float(t.get("quoteVolume", 0))
                except Exception:
                    continue
                if vol >= MA200_MIN_VOL_USDT:
                    liq.append((vol, sym))
            liq.sort(reverse=True)
            state_changed = False
            for vol, sym in liq[:80]:
                try:
                    c1d = fetch_spot_klines(sym, "1d", limit=240)
                    if len(c1d) < 203:
                        continue
                    with _STATE_LOCK:
                        before = _MA200_STATE.get(sym)
                        res = evaluate_ma200_cross(sym, c1d, _MA200_STATE)
                        if before != _MA200_STATE.get(sym):
                            state_changed = True
                    if res:
                        png = render_ma200_chart_png(sym, c1d[:-1], res["sma_series"][:-1], res["direction"], res["close_1d"], res["ma200"])
                        tg_send_photo(png, format_ma200_message(sym, res, vol))
                        if res["direction"] == "ABOVE":
                            record_signal_for_reports(sym, "LONG", res["close_1d"], "1D MA200 Kırılım")
                    time.sleep(0.08)
                except Exception:
                    continue
            if state_changed:
                with _STATE_LOCK:
                    save_json_file(MA200_STATE_FILE, _MA200_STATE)
        except Exception as e:
            print(f"[MOTOR 6 HATA] {e}")
        time.sleep(MA200_SCAN_SEC)


# ==============================================================================
# MOTOR 7: PABS TARZI SPOT ALIM BOLGESI
# ==============================================================================
def analyze_pabs_spot_signal(symbol, cur_price, vol_24h, candles_1h):
    if len(candles_1h) < 80:
        return None
    bars = candles_1h[-72:]
    rsis = calc_rsi_series([b["close"] for b in candles_1h], 14)
    rsi_now, rsi_prev = rsis[-2], rsis[-3]
    recent_high = max(b["high"] for b in bars[:-3])
    recent_low = min(b["low"] for b in bars[-24:])
    if recent_high <= 0 or recent_low <= 0:
        return None

    drawdown_pct = ((recent_high - cur_price) / recent_high) * 100.0
    dist_from_low_pct = ((cur_price - recent_low) / recent_low) * 100.0
    last_bar = candles_1h[-2]
    bull_reaction = last_bar["close"] > last_bar["open"] or cur_price > last_bar["close"]
    if not (3.5 <= drawdown_pct <= 22.0 and 0.0 <= dist_from_low_pct <= 4.5 and 22.0 <= rsi_now <= 44.0 and rsi_now >= rsi_prev - 0.5 and bull_reaction):
        return None

    buy_low = recent_low * 0.992
    buy_high = max(cur_price * 1.008, recent_low * 1.032)
    mid_buy = (buy_low + buy_high) / 2.0
    stop_loss = buy_low * 0.962
    risk_pct = max(3, int(round(((mid_buy - stop_loss) / mid_buy) * 100.0)))
    tp1 = max(cur_price * 1.048, buy_high + (recent_high - buy_high) * 0.45)
    tp2 = max(tp1 * 1.038, recent_high * 0.995)
    tp3 = max(tp2 * 1.040, recent_high * 1.035)
    tp4 = max(tp3 * 1.045, recent_high * 1.078)
    avg_profit_pct = (((tp2 + tp3) / 2.0 - mid_buy) / mid_buy) * 100.0

    return {
        "symbol": symbol, "base": symbol.replace("USDT", ""), "cur_price": cur_price, "vol_24h": vol_24h,
        "buy_low": buy_low, "buy_high": buy_high, "stop_loss": stop_loss, "risk_pct": risk_pct,
        "tp1": tp1, "tp2": tp2, "tp3": tp3, "tp4": tp4,
        "avg_profit_pct": avg_profit_pct, "drawdown_pct": drawdown_pct,
    }


def format_pabs_message(seq_no, pabs):
    return (
        f"Yeni <b>#{pabs['symbol']}</b> spot sinyali <b>{seq_no}</b>\n"
        f"Binance <b>{pabs['symbol']}</b>: 24s Hacim: <code>{fmt_vol_k(pabs['vol_24h'])} USDT</code>\n\n"
        f"Ort: tepeden <code>-{pabs['drawdown_pct']:.1f}%</code> bölgede alım, ortalama hedef kâr <b>+{pabs['avg_profit_pct']:.2f}%</b>\n"
        f"Risk: <b>%{pabs['risk_pct']}</b>\n"
        f"Alım Bölgesi : <code>{fmt_price(pabs['buy_low'])} – {fmt_price(pabs['buy_high'])}</code>\n"
        f"Altcoin <b>#{pabs['base']}</b>\n"
        f"Zarar Kes (Stop) : <code>{fmt_price(pabs['stop_loss'])}</code>\n\n"
        f"<b>HEDEFLER</b>\n"
        f"TP1 : <code>{fmt_price(pabs['tp1'])}</code>\n"
        f"TP2 : <code>{fmt_price(pabs['tp2'])}</code>\n"
        f"TP3 : <code>{fmt_price(pabs['tp3'])}</code>\n"
        f"TP4 : <code>{fmt_price(pabs['tp4'])}</code>"
    )


def next_pabs_seq():
    global _PABS_SEQ
    with _STATE_LOCK:
        _PABS_SEQ += 1
        save_json_file(PABS_SEQ_FILE, {"seq": _PABS_SEQ})
        return _PABS_SEQ


def pabs_spot_worker():
    print("[MOTOR 7] PABS Spot Alim Bolgesi tarayicisi baslatildi.")
    while True:
        try:
            liq = []
            for t in fetch_spot_tickers():
                sym = t.get("symbol", "")
                if not sym.endswith("USDT") or any(b in sym for b in ("USDC", "FDUSD", "TUSD", "USDP", "UPUSDT", "DOWNUSDT")):
                    continue
                try:
                    cur, vol = float(t.get("lastPrice", 0)), float(t.get("quoteVolume", 0))
                except Exception:
                    continue
                if cur > 0 and vol >= PABS_MIN_VOL_USDT:
                    liq.append((vol, sym, cur))
            liq.sort(reverse=True)
            now_ts = time.time()
            for vol, sym, cur in liq[:PABS_TOP_LIMIT]:
                if now_ts - _PABS_COOLDOWN.get(sym, 0) < PABS_COOLDOWN_SEC:
                    continue
                try:
                    c1h = fetch_spot_klines(sym, "1h", limit=120)
                    pabs = analyze_pabs_spot_signal(sym, cur, vol, c1h)
                    if pabs:
                        _PABS_COOLDOWN[sym] = now_ts
                        seq_no = next_pabs_seq()
                        png = render_pabs_chart_png(sym, c1h, pabs["buy_low"], pabs["buy_high"], pabs["stop_loss"], pabs["tp1"])
                        tg_send_photo(png, format_pabs_message(seq_no, pabs))
                        record_signal_for_reports(sym, "LONG", cur, f"PABS #{seq_no}")
                    time.sleep(0.10)
                except Exception:
                    continue
        except Exception as e:
            print(f"[MOTOR 7 HATA] {e}")
        time.sleep(PABS_SCAN_SEC)


# ==============================================================================
# MOTOR 8: BINANCE RESMI DUYURU TAKIBI
# ==============================================================================
def fetch_binance_announcements(catalog_id, page_size=8):
    url = (
        "https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query"
        f"?catalogId={catalog_id}&pageNo=1&pageSize={page_size}"
    )
    data = http_get_json(url, timeout=10)
    articles = data.get("data", {}).get("articles", [])
    return articles if isinstance(articles, list) else []


def translate_binance_title_tr(title):
    tr = title.strip()
    replacements = [
        ("Binance Will List", "Binance Listeliyor:"),
        ("Binance Will Add", "Binance Ekliyor:"),
        ("Binance Will Support", "Binance Destekliyor:"),
        ("Binance Has Completed the", "Binance Tamamladı:"),
        ("Binance Futures Will Launch", "Binance Vadeli Başlatıyor:"),
        ("Token Merge", "Token Birleşmesi (Merge)"),
        ("Contract Swap", "Kontrat Değişimi (Swap)"),
        ("Token Swap and Rebranding", "Token Değişimi ve Yeniden Markalaşma"),
        ("Withdrawals Reopened", "Çekim İşlemleri Yeniden Açıldı"),
        ("Notice of Removal of Spot Trading Pairs", "Spot İşlem Çiftleri Kaldırma (Delist) Duyurusu"),
        ("Binance Will Delist", "Binance Delist Ediyor:"),
        ("Network Upgrade", "Ağ Güncellemesi"),
        ("Hard Fork", "Ağ Çatallanması (Hard Fork)"),
    ]
    for eng, tur in replacements:
        tr = re.sub(re.escape(eng), tur, tr, flags=re.IGNORECASE)
    return tr


def format_binance_news_message(category_label, article):
    code = article.get("code", "")
    title = str(article.get("title", "")).strip()
    pub_ms = int(article.get("releaseDate", 0) or 0)
    time_str = (
        datetime.fromtimestamp(pub_ms / 1000.0, TR_TZ).strftime("%d.%m.%Y %H:%M TR")
        if pub_ms > 0 else datetime.now(TR_TZ).strftime("%d.%m.%Y %H:%M TR")
    )
    link = f"https://www.binance.com/en/support/announcement/detail/{code}" if code else "https://www.binance.com/en/support/announcement"
    return (
        f"📰 <b>Binance | {category_label}</b>\n\n"
        f"<b>{translate_binance_title_tr(title)}</b>\n"
        f"<i>Orijinal: {title}</i>\n"
        f"🕒 {time_str}\n"
        f"🔗 {link}"
    )


def binance_news_worker():
    global _NEWS_SEEDED
    print("[MOTOR 8] Binance Resmi Duyuru Takibi baslatildi.")
    while True:
        try:
            fresh_articles = []
            for cat_id, cat_label in BINANCE_CATALOGS:
                try:
                    arts = fetch_binance_announcements(cat_id, page_size=8)
                except Exception:
                    continue
                for art in arts:
                    aid = str(art.get("id") or art.get("code") or art.get("title") or "")
                    if not aid:
                        continue
                    if not _NEWS_SEEDED:
                        _NEWS_SEEN.add(aid)
                    elif aid not in _NEWS_SEEN:
                        _NEWS_SEEN.add(aid)
                        fresh_articles.append((cat_label, art))
                time.sleep(0.2)

            if not _NEWS_SEEDED:
                _NEWS_SEEDED = True
                save_json_file(BINANCE_NEWS_SEEN_FILE, list(_NEWS_SEEN)[-300:])
            elif fresh_articles:
                save_json_file(BINANCE_NEWS_SEEN_FILE, list(_NEWS_SEEN)[-300:])
                for cat_label, art in fresh_articles[:5]:
                    tg_send_text(format_binance_news_message(cat_label, art))
                    time.sleep(0.5)
        except Exception as e:
            print(f"[MOTOR 8 HATA] {e}")
        time.sleep(NEWS_SCAN_SEC)


# ==============================================================================
# TELEGRAM KOMUTLARI & RENDER HEALTH SUNUCUSU
# ==============================================================================
def telegram_command_worker():
    if not BOT_TOKEN:
        return
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?timeout=25&offset={offset}"
            data = http_get_json(url, timeout=30)
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                txt = ((upd.get("message") or {}).get("text") or "").strip().lower()
                if txt in ("/start", "/durum"):
                    with _STATE_LOCK:
                        sig_cnt = len(_SIGNALS_LOG)
                        pump_cnt = len(_PUMP_SENT)
                        ma_cnt = len(_MA200_STATE)
                        above_cnt = sum(1 for v in _MA200_STATE.values() if v == "ABOVE")
                        below_cnt = sum(1 for v in _MA200_STATE.values() if v == "BELOW")
                        seq_no = _PABS_SEQ
                    pump_rule = "Sadece 1 defa (Kalıcı Kilit)" if PUMP_REPEAT_HOURS <= 0 else f"{PUMP_REPEAT_HOURS} saatte 1 defa"
                    tg_send_text(
                        "🤖 <b>8 Motorlu Kripto Spot Tarayıcı Aktif</b>\n\n"
                        f"⚡ 1) 0–7 dk Spot Pump ({pump_rule} · {pump_cnt} coin kilitli)\n"
                        "📉 2) W1 Tüm Zamanların En Düşüğü (ATL)\n"
                        "🧱 3) 4h Akış / Emir Defteri Duvarı & POC\n"
                        "🏆 4) Günün Zaferi Kartı (21:00 TR & /zafer)\n"
                        "🔥 5) TOP 24 Saat Kartı (23:00 TR & /top24)\n"
                        f"📏 6) 1G MA200 Kilitli Kırılım ({ma_cnt} coin: {above_cnt} üst / {below_cnt} alt)\n"
                        f"🎯 7) PABS Spot Alım Bölgesi (Sıra #{seq_no})\n"
                        "📰 8) Binance Resmi Duyuru & Haber Takibi\n\n"
                        f"📊 Son 48s takip edilen sinyal: <b>{sig_cnt}</b>\n"
                        "🔄 Pump kilidini temizlemek için: /pumpsifirla"
                    )
                elif txt == "/pumpsifirla":
                    with _STATE_LOCK:
                        cleared = len(_PUMP_SENT)
                        _PUMP_SENT.clear()
                        save_json_file(PUMP_SEEN_FILE, _PUMP_SENT)
                    tg_send_text(f"🧹 Spot Pump tek-sinyal kilidi sıfırlandı ({cleared} coin temizlendi).")
                elif txt == "/zafer":
                    send_win_of_day_report(manual=True)
                elif txt == "/top24":
                    send_top24_report(manual=True)
        except Exception:
            time.sleep(5)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK - 8-Motor Spot Bot Aktif")

    def log_message(self, format, *args):
        pass


def main():
    load_all_states()
    for target in (
        pump_worker, atl_worker, flow_structure_worker, report_scheduler_worker,
        ma200_worker, pabs_spot_worker, binance_news_worker, telegram_command_worker
    ):
        threading.Thread(target=target, daemon=True).start()

    print(f"[BASLADI] HTTP Health sunucusu :{PORT} uzerinde dinliyor...")
    HTTPServer(("0.0.0.0", PORT), HealthHandler).serve_forever()


if __name__ == "__main__":
    main()

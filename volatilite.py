#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Binance Spot Anlık Pump Dedektörü & Canlı Piyasa Radarı — Render / Standalone Telegram Bot
- Sadece Motor #1 (Spot Anlık Pump: 15dk Hacimli Mum Patlaması + 0-7dk Ani Pump) sinyal üretir.
- 2 Saatlik Dalga Kilidi (PUMP_REPEAT_HOURS, varsayılan 2) ile aynı dalgada spam önler,
  ikinci kırılım dalgasını kaçırmaz.
- Yalnızca aktif TRADING durumundaki güncel pariteleri tarar (BREAK / delist / donmuş pariteleri eler).
- /piyasa (ve inline butonlar: 7dk % | 24s % | Hacim | Güncelle) + /piyasagorsel ile
  Anlık Spot Piyasa ekranını Telegram içinde canlı kullanabilirsiniz.
- Ek Telegram Yardımcıları: 21:00 TR Günün Zaferi (>= %3), 23:00 TR Top 24 Özeti, 60sn Binance Duyuruları.
"""

import os
import re
import json
import time
import zlib
import struct
import signal
import threading
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

# ==============================================================================
# YAPILANDIRMA
# ==============================================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "1623907197").strip()
PORT = int(os.environ.get("PORT", "10000"))

_repeat_raw = os.environ.get("PUMP_REPEAT_HOURS", "").strip()
try:
    PUMP_REPEAT_HOURS = float(_repeat_raw) if _repeat_raw != "" else 2.0
    if PUMP_REPEAT_HOURS < 0:
        PUMP_REPEAT_HOURS = 2.0
except ValueError:
    PUMP_REPEAT_HOURS = 2.0

STATE_FILE = os.environ.get("STATE_FILE", "/tmp/spot_pump_bot_state.json")
TR_TZ = timezone(timedelta(hours=3))

BINANCE_MIRRORS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://data-api.binance.vision",
]

EXCLUDED_SUBSTRINGS = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
EXCLUDED_PREFIXES = ("USDC", "FDUSD", "TUSD", "USDP", "EUR")

# ==============================================================================
# DURUM YONETIMI (THREAD-SAFE STATE)
# ==============================================================================
state_lock = threading.RLock()
running_event = threading.Event()
running_event.set()
bot_paused = False
started_at = time.time()
last_scan_at = 0.0
scan_loops = 0
preferred_mirror_idx = 0

# Yalnızca aktif spot TRADING pariteleri (/api/v3/exchangeInfo)
active_trading_symbols = set()
# { symbol: [ (ts_sec, price), ... ] } -> 15 dakikalik canli fiyat penceresi
price_history = {}
# { symbol: retry_ts } -> RVOL/mum dogrulamasi gecemeyenler icin 20sn bekleme
verify_cooldown = {}
# { symbol: { "symbol", "base", "quote", "price", "chg24h", "vol24h", "window7mPct", "updatedAt" } }
latest_tickers = {}
# { base: { "base": str, "symbol": str, "lockedAt": float } }
pump_locks = {}
# Son 48 saat sinyaller
tracked_signals = []
# Binance resmi duyuru ID seti
seen_news_ids = set()
news_seeded = False
daily_flags = {"zafer_date": "", "top24_date": ""}


def load_state():
    global tracked_signals, pump_locks, daily_flags
    with state_lock:
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data.get("pump_locks"), dict):
                pump_locks = data["pump_locks"]
            if isinstance(data.get("tracked_signals"), list):
                tracked_signals = data["tracked_signals"][:250]
            if isinstance(data.get("daily_flags"), dict):
                daily_flags.update(data["daily_flags"])
        except Exception:
            pass


def save_state():
    with state_lock:
        try:
            tmp = STATE_FILE + ".tmp"
            payload = {
                "pump_locks": pump_locks,
                "tracked_signals": tracked_signals[:250],
                "daily_flags": daily_flags,
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, STATE_FILE)
        except Exception:
            pass


# ==============================================================================
# YARDIMCI FONKSIYONLAR
# ==============================================================================
def extract_base_quote(sym):
    for q in ("USDT", "BTC", "BNB"):
        if sym.endswith(q) and len(sym) > len(q):
            return sym[: -len(q)], q
    return None, None


def is_excluded_pair(sym):
    for sub in EXCLUDED_SUBSTRINGS:
        if sub in sym:
            return True
    for pre in EXCLUDED_PREFIXES:
        if sym.startswith(pre):
            return True
    return False


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


def fmt_compact_usd(v):
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${round(v / 1_000)}K"
    return f"${round(v)}"


def compute_sma(values, period):
    out = [None] * len(values)
    if len(values) < period:
        return out
    s = sum(values[:period])
    out[period - 1] = s / period
    for i in range(period, len(values)):
        s += values[i] - values[i - period]
        out[i] = s / period
    return out


# ==============================================================================
# HTTP / BINANCE MIRROR & TELEGRAM ILETISIM
# ==============================================================================
def http_get_json(url, timeout=9):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; SpotPumpBot/2.1)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_binance_json(path_and_query, timeout=9):
    global preferred_mirror_idx
    last_err = None
    n = len(BINANCE_MIRRORS)
    for offset in range(n):
        idx = (preferred_mirror_idx + offset) % n
        try:
            data = http_get_json(BINANCE_MIRRORS[idx] + path_and_query, timeout=timeout)
            preferred_mirror_idx = idx
            return data
        except Exception as exc:
            last_err = exc
    raise RuntimeError(f"Binance mirror error: {last_err}")


def refresh_active_trading_symbols():
    global active_trading_symbols
    try:
        info = fetch_binance_json("/api/v3/exchangeInfo", timeout=12)
        next_set = set()
        for s in info.get("symbols") or []:
            sym = s.get("symbol")
            if sym and s.get("status") == "TRADING" and s.get("isSpotTradingAllowed", True):
                next_set.add(sym)
        if len(next_set) > 50:
            with state_lock:
                active_trading_symbols = next_set
                for sym in list(latest_tickers.keys()):
                    if sym not in active_trading_symbols:
                        latest_tickers.pop(sym, None)
                        price_history.pop(sym, None)
    except Exception:
        pass


def fetch_spot_klines(symbol, interval="15m", limit=65):
    q_sym = urllib.parse.quote(symbol)
    raw = fetch_binance_json(f"/api/v3/klines?symbol={q_sym}&interval={interval}&limit={limit}", timeout=8)
    bars = []
    if not isinstance(raw, list):
        return bars
    for k in raw:
        bars.append({
            "t": int(float(k[0]) / 1000.0),
            "o": float(k[1]),
            "h": float(k[2]),
            "l": float(k[3]),
            "c": float(k[4]),
            "v": float(k[5]),
            "qv": float(k[7]),
        })
    return bars


def tg_request(method, payload=None, timeout=12):
    if not BOT_TOKEN:
        return None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def tg_send_message(text, chat_id=None, reply_markup=None):
    if not BOT_TOKEN:
        return None
    payload = {
        "chat_id": chat_id or CHAT_ID,
        "text": text[:3900],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_request("sendMessage", payload)


def tg_edit_message(chat_id, message_id, text, reply_markup=None):
    if not BOT_TOKEN:
        return None
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text[:3900],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_request("editMessageText", payload)


def tg_send_photo(png_bytes, caption, chat_id=None):
    if not BOT_TOKEN or not png_bytes:
        return tg_send_message(caption, chat_id=chat_id)
    boundary = f"----SpotPumpBoundary{int(time.time() * 1000)}"
    target_chat = str(chat_id or CHAT_ID)
    caption_safe = caption[:990]
    parts = []
    for name, val in (("chat_id", target_chat), ("caption", caption_safe), ("parse_mode", "HTML")):
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{val}\r\n"
            ).encode("utf-8")
        )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="chart.png"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(png_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return tg_send_message(caption, chat_id=target_chat)


# ==============================================================================
# SIFIR BAGIMLILIKLI PNG GRAFIK MOTORU (MUM + MA7 + MA25 + VOL & ANLIK SPOT PIYASA)
# ==============================================================================
FONT_5X7 = {
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
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01110"],
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
    "$": ["00100", "01111", "10100", "01110", "00101", "11110", "00100"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    ",": ["00000", "00000", "00000", "00000", "00100", "00100", "01000"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
    "+": ["00000", "00100", "00100", "11111", "00100", "00100", "00000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    "%": ["11001", "11010", "00010", "00100", "01000", "01011", "10011"],
    "/": ["00001", "00010", "00010", "00100", "01000", "01000", "10000"],
    "(": ["00010", "00100", "01000", "01000", "01000", "00100", "00010"],
    ")": ["01000", "00100", "00010", "00010", "00010", "00100", "01000"],
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
}


class PixelCanvas:
    def __init__(self, width, height, bg=(9, 13, 22)):
        self.w = width
        self.h = height
        r, g, b = bg
        row = bytearray([r, g, b] * width)
        self.buf = bytearray(row * height)

    def set_px(self, x, y, col):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.buf[i] = col[0]
            self.buf[i + 1] = col[1]
            self.buf[i + 2] = col[2]

    def fill_rect(self, x, y, w, h, col):
        x0 = max(0, int(x))
        y0 = max(0, int(y))
        x1 = min(self.w, int(x + w))
        y1 = min(self.h, int(y + h))
        if x1 <= x0 or y1 <= y0:
            return
        chunk = bytes(col) * (x1 - x0)
        stride = self.w * 3
        for yy in range(y0, y1):
            start = yy * stride + x0 * 3
            self.buf[start : start + len(chunk)] = chunk

    def draw_line(self, x0, y0, x1, y1, col, dashed=False, thickness=1):
        x0, y0, x1, y1 = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        step = 0
        while True:
            if not dashed or (step // 4) % 2 == 0:
                if thickness <= 1:
                    self.set_px(x0, y0, col)
                else:
                    self.fill_rect(x0, y0, thickness, thickness, col)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy
            step += 1

    def draw_text(self, x, y, text, col=(226, 232, 240), scale=1):
        clean = (
            text.upper()
            .replace("İ", "I")
            .replace("Ş", "S")
            .replace("Ğ", "G")
            .replace("Ü", "U")
            .replace("Ö", "O")
            .replace("Ç", "C")
            .replace("·", ".")
        )
        cx = int(x)
        cy = int(y)
        for ch in clean:
            glyph = FONT_5X7.get(ch, FONT_5X7[" "])
            for gy, row in enumerate(glyph):
                for gx, bit in enumerate(row):
                    if bit == "1":
                        if scale == 1:
                            self.set_px(cx + gx, cy + gy, col)
                        else:
                            self.fill_rect(cx + gx * scale, cy + gy * scale, scale, scale, col)
            cx += 6 * scale

    def draw_text_right(self, right_x, y, text, col=(226, 232, 240), scale=1):
        w = len(text) * 6 * scale
        self.draw_text(right_x - w, y, text, col=col, scale=scale)

    def to_png(self):
        def chunk(tag, data):
            return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        raw_rows = bytearray()
        stride = self.w * 3
        for y in range(self.h):
            raw_rows.append(0)
            s = y * stride
            raw_rows.extend(self.buf[s : s + stride])

        ihdr = struct.pack("!IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)
        idat = zlib.compress(bytes(raw_rows), level=6)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def build_synthetic_ws_bars(symbol, live_cur, base_price):
    with state_lock:
        pts = list(price_history.get(symbol, []))
    if not pts:
        pts = [(time.time() - 60, base_price), (time.time(), live_cur)]
    buckets = {}
    for ts, p in pts:
        bkey = int(ts // 15) * 15
        if bkey not in buckets:
            buckets[bkey] = {"t": bkey, "o": p, "h": p, "l": p, "c": p, "v": 1.0, "qv": 1.0}
        else:
            b = buckets[bkey]
            b["h"] = max(b["h"], p)
            b["l"] = min(b["l"], p)
            b["c"] = p
            b["v"] += 1.0
    bars = [buckets[k] for k in sorted(buckets.keys())][-45:]
    if bars:
        bars[-1]["c"] = live_cur
        bars[-1]["h"] = max(bars[-1]["h"], live_cur)
        bars[-1]["l"] = min(bars[-1]["l"], live_cur)
    return bars, "15SN CANLI"


def render_pump_png(symbol, base_price, live_cur, pct_change, rvol, candles_15m=None):
    W, H = 900, 500
    PAD_L, PAD_R, PAD_T, PAD_B = 22, 118, 62, 34
    VOL_H = 82
    GAP = 16
    PRICE_H = H - PAD_T - VOL_H - GAP - PAD_B
    PLOT_W = W - PAD_L - PAD_R

    tf_label = "15DK CANLI"
    bars = list(candles_15m or [])
    if not bars:
        try:
            bars = fetch_spot_klines(symbol, "15m", limit=65)
        except Exception:
            bars = []
    if len(bars) < 5:
        try:
            bars = fetch_spot_klines(symbol, "1m", limit=55)
            tf_label = "1DK CANLI"
        except Exception:
            bars = []
    if len(bars) < 3:
        bars, tf_label = build_synthetic_ws_bars(symbol, live_cur, base_price)

    bars = [dict(b) for b in bars]
    if bars:
        bars[-1]["c"] = live_cur
        bars[-1]["h"] = max(bars[-1]["h"], live_cur)
        bars[-1]["l"] = min(bars[-1]["l"], live_cur)

    closes = [b["c"] for b in bars]
    ma7_full = compute_sma(closes, 7)
    ma25_full = compute_sma(closes, 25)
    vols = [b["v"] for b in bars]
    vol_ma5_full = compute_sma(vols, 5)

    slice_n = min(len(bars), 48)
    bars = bars[-slice_n:]
    ma7 = ma7_full[-slice_n:]
    ma25 = ma25_full[-slice_n:]
    vol_ma5 = vol_ma5_full[-slice_n:]

    c = PixelCanvas(W, H, bg=(10, 14, 24))
    c.fill_rect(0, 0, W, 46, (17, 24, 39))
    c.draw_line(0, 46, W, 46, (30, 41, 59))

    title = f"PUMP . {symbol} . {tf_label} . +{pct_change:.2f}% . RVOL {rvol:.1f}X"
    c.draw_text(PAD_L, 11, title, col=(248, 250, 252), scale=2)
    last_ma7 = next((m for m in reversed(ma7) if m is not None), None)
    last_ma25 = next((m for m in reversed(ma25) if m is not None), None)
    c.draw_text(PAD_L, 33, f"MA(7): {fmt_price(last_ma7) if last_ma7 else '-'}", col=(245, 158, 11), scale=1)
    c.draw_text(PAD_L + 150, 33, f"MA(25): {fmt_price(last_ma25) if last_ma25 else '-'}", col=(236, 72, 153), scale=1)
    c.draw_text(PAD_L + 310, 33, f"BAS: {fmt_price(base_price)}", col=(251, 191, 36), scale=1)

    lows = [b["l"] for b in bars] + [base_price, live_cur]
    highs = [b["h"] for b in bars] + [base_price, live_cur]
    for m in ma7 + ma25:
        if m is not None:
            lows.append(m)
            highs.append(m)

    min_p = min(lows) * 0.995
    max_p = max(highs) * 1.005
    range_p = max(max_p - min_p, min_p * 0.005)
    max_vol = max([b["v"] for b in bars] + [1.0])

    def y_of(price):
        return PAD_T + ((min_p + range_p - price) / range_p) * PRICE_H

    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        gy = int(PAD_T + PRICE_H * frac)
        val = min_p + range_p * (1.0 - frac)
        c.draw_line(PAD_L, gy, PAD_L + PLOT_W, gy, (24, 34, 52), dashed=True)
        c.draw_text(PAD_L + PLOT_W + 8, gy - 3, fmt_price(val), col=(148, 163, 184), scale=1)

    y_bas = int(y_of(base_price))
    c.draw_line(PAD_L, y_bas, PAD_L + PLOT_W, y_bas, (245, 158, 11), dashed=True)
    c.draw_text(PAD_L + PLOT_W + 8, max(PAD_T, min(PAD_T + PRICE_H - 10, y_bas - 3)), f"BAS {fmt_price(base_price)}", col=(251, 191, 36), scale=1)

    n_bars = len(bars)
    step = PLOT_W / max(1, n_bars)
    bw = max(3, int(step * 0.62))
    vol_top = PAD_T + PRICE_H + GAP

    for idx, b in enumerate(bars):
        cx = int(PAD_L + (idx + 0.5) * step)
        is_up = b["c"] >= b["o"]
        col = (16, 185, 129) if is_up else (244, 63, 94)
        yo = int(y_of(b["o"]))
        yc = int(y_of(b["c"]))
        yh = int(y_of(b["h"]))
        yl = int(y_of(b["l"]))
        c.draw_line(cx, yh, cx, yl, col)
        top_y = min(yo, yc)
        body_h = max(2, abs(yc - yo))
        c.fill_rect(cx - bw // 2, top_y, bw, body_h, col)

        vh = max(1, int((b["v"] / max_vol) * VOL_H))
        vcol = (14, 130, 95) if is_up else (180, 48, 72)
        c.fill_rect(cx - bw // 2, vol_top + VOL_H - vh, bw, vh, vcol)

    for series, scol in ((ma25, (236, 72, 153)), (ma7, (245, 158, 11))):
        prev_pt = None
        for idx, m in enumerate(series):
            if m is None:
                continue
            cx = PAD_L + (idx + 0.5) * step
            cy = y_of(m)
            if prev_pt is not None:
                c.draw_line(prev_pt[0], prev_pt[1], cx, cy, scol, thickness=2)
            prev_pt = (cx, cy)

    prev_vpt = None
    for idx, vm in enumerate(vol_ma5):
        if vm is None:
            continue
        cx = PAD_L + (idx + 0.5) * step
        cy = vol_top + VOL_H - (vm / max_vol) * VOL_H
        if prev_vpt is not None:
            c.draw_line(prev_vpt[0], prev_vpt[1], cx, cy, (245, 158, 11), thickness=1)
        prev_vpt = (cx, cy)

    y_cur = int(max(PAD_T + 8, min(PAD_T + PRICE_H - 10, y_of(live_cur))))
    c.draw_line(PAD_L, y_cur, PAD_L + PLOT_W, y_cur, (56, 189, 248), dashed=True)
    c.fill_rect(PAD_L + PLOT_W + 4, y_cur - 9, 108, 18, (2, 132, 199))
    c.draw_text(PAD_L + PLOT_W + 9, y_cur - 3, fmt_price(live_cur), col=(248, 250, 252), scale=1)

    for frac in (0.05, 0.35, 0.65, 0.95):
        idx = min(n_bars - 1, int(frac * (n_bars - 1)))
        cx = int(PAD_L + (idx + 0.5) * step)
        dt = datetime.fromtimestamp(bars[idx]["t"], tz=TR_TZ)
        c.draw_text(cx - 14, H - 18, dt.strftime("%H:%M"), col=(148, 163, 184), scale=1)

    return c.to_png()


# ==============================================================================
# ANLIK SPOT PIYASA RADARI (IMG_3153 KARTI + CANLI VERILER + PNG)
# ==============================================================================
def get_live_spot_market_rows(sort_by="change24h", limit=12, query=""):
    with state_lock:
        rows = [
            dict(tk)
            for tk in latest_tickers.values()
            if tk.get("quote") == "USDT" and tk.get("price", 0) > 0 and tk.get("vol24h", 0) >= 150_000.0
        ]
    q = (query or "").strip().upper()
    if q:
        rows = [r for r in rows if q in r["symbol"] or q in r["base"]]

    if sort_by == "window7m":
        rows.sort(key=lambda r: (r.get("window7mPct", 0.0), r.get("chg24h", 0.0)), reverse=True)
    elif sort_by == "volume":
        rows.sort(key=lambda r: r.get("vol24h", 0.0), reverse=True)
    else:
        rows.sort(key=lambda r: (r.get("chg24h", 0.0), r.get("vol24h", 0.0)), reverse=True)
    return rows[:limit]


def build_spot_market_text(sort_by="change24h", query=""):
    rows = get_live_spot_market_rows(sort_by=sort_by, limit=12, query=query)
    now_str = datetime.now(TR_TZ).strftime("%H:%M:%S")
    sort_labels = {
        "change24h": "24s %",
        "window7m": "7dk %",
        "volume": "Hacim",
    }
    sort_label = sort_labels.get(sort_by, "24s %")

    if not rows:
        return (
            f"📊 <b>ANLIK SPOT PİYASA</b> ({sort_label} · {now_str} TR)\n"
            f"Canlı TRADING verileri yükleniyor, birkaç saniye sonra tekrar deneyin."
        )

    lines = [
        f"📊 <b>ANLIK SPOT PİYASA</b> · <i>{sort_label}</i> · <code>{now_str} TR</code>",
        "<i>Yalnızca anlık aktif TRADING USDT pariteleri</i>\n",
    ]
    for r in rows:
        p_str = fmt_price(r["price"])
        v_str = fmt_compact_usd(r["vol24h"])
        w7 = r.get("window7mPct", 0.0)
        c24 = r.get("chg24h", 0.0)
        w7_str = f"{'+' if w7 >= 0 else ''}{w7:.2f}%"
        c24_str = f"{'+' if c24 >= 0 else ''}{c24:.1f}%"
        lines.append(
            f"<b>{r['symbol']}</b>   <code>${p_str}</code>\n"
            f"Hacim {v_str}  ·  7dk <code>{w7_str}</code>  ·  24s <b>{c24_str}</b>"
        )
    return "\n".join(lines)


def build_spot_market_keyboard(active_sort="change24h"):
    def lbl(key, title):
        return f"• {title} •" if active_sort == key else title

    return {
        "inline_keyboard": [
            [
                {"text": lbl("window7m", "7dk %"), "callback_data": "mkt:window7m"},
                {"text": lbl("change24h", "24s %"), "callback_data": "mkt:change24h"},
                {"text": lbl("volume", "Hacim"), "callback_data": "mkt:volume"},
            ],
            [
                {"text": "🔄 Güncelle", "callback_data": f"mkt:{active_sort}"},
                {"text": "🖼 Görsel Kart", "callback_data": f"mktimg:{active_sort}"},
            ],
        ]
    }


def render_spot_market_png(sort_by="change24h", query=""):
    rows = get_live_spot_market_rows(sort_by=sort_by, limit=9, query=query)
    W = 620
    ROW_H = 66
    HDR_H = 68
    H = HDR_H + max(1, len(rows)) * ROW_H + 18
    c = PixelCanvas(W, H, bg=(11, 16, 27))

    c.fill_rect(0, 0, W, HDR_H, (17, 24, 39))
    c.draw_line(0, HDR_H, W, HDR_H, (30, 41, 59))
    c.draw_text(22, 22, "ANLIK SPOT PIYASA", col=(241, 245, 249), scale=2)

    tabs = [("window7m", "7DK %", 336), ("change24h", "24S %", 422), ("volume", "HACIM", 508)]
    c.fill_rect(330, 14, 268, 34, (11, 16, 27))
    for key, label, tx in tabs:
        if sort_by == key:
            c.fill_rect(tx, 17, 82, 28, (30, 41, 59))
            c.draw_text(tx + 14, 26, label, col=(248, 250, 252), scale=1)
        else:
            c.draw_text(tx + 14, 26, label, col=(148, 163, 184), scale=1)

    now_str = datetime.now(TR_TZ).strftime("%H:%M:%S")
    c.draw_text(22, 48, f"CANLI TRADING . {now_str} TR", col=(100, 116, 139), scale=1)

    if not rows:
        c.draw_text(22, HDR_H + 26, "CANLI VERI BEKLENIYOR...", col=(148, 163, 184), scale=1)
        return c.to_png()

    for idx, r in enumerate(rows):
        ry = HDR_H + idx * ROW_H
        if idx > 0:
            c.draw_line(20, ry, W - 20, ry, (24, 34, 52))
        p_str = "$" + fmt_price(r["price"])
        v_str = "HACIM " + fmt_compact_usd(r["vol24h"])
        w7 = r.get("window7mPct", 0.0)
        c24 = r.get("chg24h", 0.0)
        w7_str = f"7DK {'+' if w7 >= 0 else ''}{w7:.2f}%"
        c24_str = f"24S {'+' if c24 >= 0 else ''}{c24:.1f}%"

        c.draw_text(22, ry + 14, r["symbol"], col=(241, 245, 249), scale=2)
        c.draw_text(22, ry + 40, v_str, col=(148, 163, 184), scale=1)

        c.draw_text_right(W - 22, ry + 14, p_str, col=(241, 245, 249), scale=2)
        w7_col = (52, 211, 153) if w7 >= 0 else (251, 113, 133)
        c24_col = (226, 232, 240) if c24 >= 0 else (251, 113, 133)
        c.draw_text_right(W - 22, ry + 40, c24_str, col=c24_col, scale=1)
        c.draw_text_right(W - 140, ry + 40, w7_str, col=w7_col, scale=1)

    return c.to_png()


# ==============================================================================
# MOTOR 1: SPOT ANLIK PUMP (15DK HACIMLI MUM KIRILIMI + 0-7DK ANI SPIKE)
# ==============================================================================
def is_pump_locked(base, now_sec):
    with state_lock:
        rec = pump_locks.get(base)
        if not rec:
            return False
        if PUMP_REPEAT_HOURS <= 0:
            return True
        return (now_sec - float(rec.get("lockedAt", 0))) < (PUMP_REPEAT_HOURS * 3600.0)


def verify_and_emit_pump(symbol, base, quote, min7_price, min7_ts, min15_price, min15_ts, approx_price, approx_vol):
    try:
        q_sym = urllib.parse.quote(symbol)
        fresh_tk = fetch_binance_json(f"/api/v3/ticker/24hr?symbol={q_sym}", timeout=5)
        c1m = fetch_spot_klines(symbol, "1m", limit=20)
        c15m = fetch_spot_klines(symbol, "15m", limit=65)

        live_cur = float(fresh_tk.get("lastPrice") or approx_price)
        live_vol = float(fresh_tk.get("quoteVolume") or approx_vol)

        fast_th = 4.2 if quote == "USDT" else 4.0
        breakout_th = 3.2

        recent_1m = c1m[-7:] if c1m else []
        recent_15m_1m = c1m[-15:] if c1m else []

        bar_min_7m = min((b["l"] for b in recent_1m), default=min7_price)
        verified_min_7m = min(min7_price, bar_min_7m if bar_min_7m > 0 else min7_price)
        verified_pct_7m = ((live_cur - verified_min_7m) / verified_min_7m) * 100.0 if verified_min_7m > 0 else 0.0

        cur_15m = c15m[-1] if c15m else None
        prev_15m = c15m[-2] if len(c15m) > 1 else None
        lows_15m = [b["l"] for b in recent_15m_1m] + ([cur_15m["l"]] if cur_15m else [])
        bar_min_15m = min(lows_15m) if lows_15m else min15_price
        verified_min_15m = min(min15_price, bar_min_15m if bar_min_15m > 0 else min15_price)
        verified_pct_15m = ((live_cur - verified_min_15m) / verified_min_15m) * 100.0 if verified_min_15m > 0 else 0.0

        def qvol(b):
            return b["qv"] if b["qv"] > 0 else b["v"] * b["c"]

        base_15m = c15m[-11:-1] if len(c15m) >= 4 else []
        avg_15m_q = sum(qvol(b) for b in base_15m) / len(base_15m) if len(base_15m) >= 3 else 0.0
        active_15m_q = 0.0
        if cur_15m:
            active_15m_q = qvol(cur_15m)
            if prev_15m and prev_15m["o"] > 0 and ((prev_15m["c"] - prev_15m["o"]) / prev_15m["o"]) * 100.0 >= 2.0:
                active_15m_q = max(active_15m_q, qvol(prev_15m))
        rvol_15m = (active_15m_q / avg_15m_q) if avg_15m_q > 0 else 1.0

        last_3m = c1m[-3:] if c1m else []
        base_1m = c1m[-13:-3] if len(c1m) >= 6 else []
        avg_1m_recent = sum(qvol(b) for b in last_3m) / len(last_3m) if last_3m else 0.0
        avg_1m_base = sum(qvol(b) for b in base_1m) / len(base_1m) if len(base_1m) >= 3 else 0.0
        rvol_1m = (avg_1m_recent / avg_1m_base) if avg_1m_base > 0 else 1.0

        rvol = round(max(rvol_15m, rvol_1m), 1)

        candle_high = max(live_cur, cur_15m["h"] if cur_15m else live_cur)
        candle_low = min(verified_min_15m, cur_15m["l"] if cur_15m else verified_min_15m)
        hold_ratio = (live_cur - candle_low) / max(candle_high - candle_low, 1e-12)
        is_green_15m = True
        if cur_15m:
            is_green_15m = (live_cur > cur_15m["o"]) or (
                prev_15m is not None and live_cur > prev_15m["o"] and prev_15m["c"] > prev_15m["o"]
            )

        fast_ok = verified_pct_7m >= fast_th and rvol >= 1.5 and hold_ratio >= 0.45
        breakout_15m_ok = verified_pct_15m >= breakout_th and rvol >= 2.0 and is_green_15m and hold_ratio >= 0.50

        if not fast_ok and not breakout_15m_ok:
            with state_lock:
                pump_locks.pop(base, None)
                verify_cooldown[symbol] = time.time() + 20.0
            return

        use_fast = fast_ok and (not breakout_15m_ok or verified_pct_7m >= fast_th)
        v_min = verified_min_7m if use_fast else verified_min_15m
        v_pct = verified_pct_7m if use_fast else verified_pct_15m
        ref_ts = min7_ts if use_fast else min15_ts
        mins = max(1, min(7 if use_fast else 15, round((time.time() - ref_ts) / 60.0)))
        mode_text = "0-7dk Ani Spike" if use_fast else "15m Hacimli Kırılım"

        vol_str = f"{round(live_vol):,}" if quote == "USDT" else f"{live_vol:.2f}"
        lock_msg = f"{PUMP_REPEAT_HOURS:g} saatlik dalga kilidi aktif" if PUMP_REPEAT_HOURS > 0 else "Tekil coin kilidi aktif"

        with state_lock:
            price_history.pop(symbol, None)
            verify_cooldown.pop(symbol, None)
            pump_locks[base] = {"base": base, "symbol": symbol, "lockedAt": time.time()}
            tracked_signals.insert(
                0,
                {
                    "id": f"PUMP-{symbol}-{int(time.time())}",
                    "symbol": symbol,
                    "base": base,
                    "quote": quote,
                    "entryPrice": live_cur,
                    "currentPrice": live_cur,
                    "peakPrice": live_cur,
                    "peakPct": 0.0,
                    "changePct": round(v_pct, 2),
                    "rvol": rvol,
                    "mode": mode_text,
                    "createdAt": time.time(),
                },
            )
            if len(tracked_signals) > 250:
                del tracked_signals[250:]
        save_state()

        caption = (
            f"🚀 <b>SPOT ANLIK PUMP DEDEKTÖRÜ</b> — <b>#{symbol}</b>\n\n"
            f"📈 <b>Değişim:</b> +{v_pct:.2f}% ({mins} dk · {mode_text})\n"
            f"🎯 <b>Başlangıç:</b> ${fmt_price(v_min)} → <b>Anlık:</b> ${fmt_price(live_cur)}\n"
            f"🔥 <b>Hacim İvmesi (RVOL):</b> {rvol:.1f}x\n"
            f"💧 <b>24s Hacim:</b> {vol_str} {quote}\n"
            f"🔒 <i>{lock_msg}</i>"
        )
        png = render_pump_png(symbol, v_min, live_cur, v_pct, rvol, candles_15m=c15m)
        tg_send_photo(png, caption)
    except Exception:
        with state_lock:
            pump_locks.pop(base, None)


def evaluate_ticker_record(sym, price, chg24h, vol24h, now_sec, is_fresh=True):
    if price <= 0 or is_excluded_pair(sym):
        return
    base, quote = extract_base_quote(sym)
    if not base:
        return

    with state_lock:
        if active_trading_symbols and sym not in active_trading_symbols:
            latest_tickers.pop(sym, None)
            price_history.pop(sym, None)
            return
        if not is_fresh:
            latest_tickers.pop(sym, None)
            price_history.pop(sym, None)
            return

        min_vol = 150_000.0 if quote == "USDT" else (3.0 if quote == "BTC" else 250.0)
        hist = price_history.setdefault(sym, [])
        hist.append((now_sec, price))
        cutoff_15m = now_sec - 900.0
        cutoff_7m = now_sec - 420.0
        while hist and hist[0][0] < cutoff_15m:
            hist.pop(0)

        pct7m = 0.0
        pct15m = 0.0
        min15_ts, min15_p = hist[0]
        min7_ts, min7_p = None, None
        for ts_pt, p_pt in hist:
            if p_pt < min15_p:
                min15_ts, min15_p = ts_pt, p_pt
            if ts_pt >= cutoff_7m and (min7_p is None or p_pt < min7_p):
                min7_ts, min7_p = ts_pt, p_pt
        if min7_p is None:
            min7_ts, min7_p = min15_ts, min15_p

        if min7_p > 0:
            pct7m = ((price - min7_p) / min7_p) * 100.0
        if min15_p > 0:
            pct15m = ((price - min15_p) / min15_p) * 100.0

        latest_tickers[sym] = {
            "symbol": sym,
            "base": base,
            "quote": quote,
            "price": price,
            "chg24h": chg24h,
            "vol24h": vol24h,
            "window7mPct": round(pct7m, 2),
            "updatedAt": now_sec,
        }

        if len(hist) < 2 or vol24h < min_vol:
            return

        fast_th = 4.2 if quote == "USDT" else 4.0
        breakout_th = 3.2

        if bot_paused or (pct7m < fast_th and pct15m < breakout_th):
            return
        if is_pump_locked(base, now_sec):
            return
        if now_sec < verify_cooldown.get(sym, 0.0):
            return

        pump_locks[base] = {"base": base, "symbol": sym, "lockedAt": now_sec}

    threading.Thread(
        target=verify_and_emit_pump,
        args=(sym, base, quote, min7_p, min7_ts, min15_p, min15_ts, price, vol24h),
        daemon=True,
    ).start()


def update_signal_peaks():
    changed = False
    with state_lock:
        for s in tracked_signals:
            tk = latest_tickers.get(s["symbol"])
            if not tk or tk["price"] <= 0:
                continue
            s["currentPrice"] = tk["price"]
            pct = ((tk["price"] - s["entryPrice"]) / s["entryPrice"]) * 100.0
            if pct > s.get("peakPct", 0.0):
                s["peakPct"] = round(pct, 2)
                s["peakPrice"] = tk["price"]
                changed = True
    if changed:
        save_state()


# ==============================================================================
# BINANCE RESMI DUYURULARI & GUNLUK OZET RAPORLARI (21:00 / 23:00 TR)
# ==============================================================================
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
        ("Withdrawals Reopened", "Çekim İşlemleri Yeniden Açıldı"),
        ("Notice of Removal of Spot Trading Pairs", "Spot İşlem Çiftleri Kaldırma (Delist)"),
        ("Binance Will Delist", "Binance Delist Ediyor:"),
        ("Network Upgrade", "Ağ Güncellemesi"),
    ]
    for eng, tur in replacements:
        tr = re.sub(re.escape(eng), tur, tr, flags=re.IGNORECASE)
    return tr


def poll_binance_announcements():
    global news_seeded
    catalogs = [(48, "Yeni Listeleme"), (49, "Binance Duyuru"), (161, "Delist / Kaldırma")]
    newly_found = []
    for cat_id, cat_label in catalogs:
        try:
            url = f"https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query?catalogId={cat_id}&pageNo=1&pageSize=8"
            data = http_get_json(url, timeout=8)
            arts = (data.get("data") or {}).get("articles") or []
            for art in arts:
                aid = str(art.get("id") or art.get("code") or art.get("title") or "")
                if not aid or aid in seen_news_ids:
                    continue
                seen_news_ids.add(aid)
                title_orig = str(art.get("title") or "").strip()
                code = str(art.get("code") or "")
                link = (
                    f"https://www.binance.com/en/support/announcement/detail/{code}"
                    if code
                    else "https://www.binance.com/en/support/announcement"
                )
                newly_found.append((cat_label, translate_binance_title_tr(title_orig), link))
        except Exception:
            pass

    if news_seeded and newly_found:
        for cat_label, title_tr, link in newly_found[:5]:
            tg_send_message(f"📰 <b>Binance | {cat_label}</b>\n{title_tr}\n{link}")
    news_seeded = True


def build_daily_zafer_text():
    cutoff = time.time() - 86400.0
    with state_lock:
        winners = [s for s in tracked_signals if s.get("createdAt", 0) >= cutoff and s.get("peakPct", 0.0) >= 3.0]
    winners.sort(key=lambda x: x.get("peakPct", 0.0), reverse=True)
    if not winners:
        return "🏆 <b>GÜNÜN ZAFERİ (Son 24s ≥ +%3)</b>\nSon 24 saatte +%3 barajını aşan pump sinyali henüz oluşmadı."
    lines = ["🏆 <b>GÜNÜN ZAFERİ — Başarı Sıralaması (Son 24s)</b>\n"]
    for i, s in enumerate(winners[:15], 1):
        lines.append(
            f"{i}. <b>#{s['symbol']}</b> · Pik: <b>+{s['peakPct']:.2f}%</b> "
            f"(Giriş ${fmt_price(s['entryPrice'])} → ${fmt_price(s['peakPrice'])})"
        )
    return "\n".join(lines)


def build_top24_summary_text():
    cutoff = time.time() - 86400.0
    with state_lock:
        recents = [s for s in tracked_signals if s.get("createdAt", 0) >= cutoff]
    recents.sort(key=lambda x: x.get("peakPct", 0.0), reverse=True)
    if not recents:
        return "📊 <b>TOP 24 SAAT ÖZETİ</b>\nSon 24 saatte kaydedilmiş pump sinyali yok."
    lines = [f"📊 <b>TOP 24 SAAT ÖZETİ</b> (Toplam {len(recents)} Sinyal)\n"]
    for i, s in enumerate(recents[:20], 1):
        lines.append(
            f"{i}. <b>#{s['symbol']}</b> · Pik: <b>+{s.get('peakPct', 0):.2f}%</b> · Anlık: ${fmt_price(s['currentPrice'])}"
        )
    return "\n".join(lines)


def check_scheduled_reports():
    now_tr = datetime.now(TR_TZ)
    today_str = now_tr.strftime("%Y-%m-%d")
    if now_tr.hour == 21 and now_tr.minute == 0 and daily_flags.get("zafer_date") != today_str:
        daily_flags["zafer_date"] = today_str
        save_state()
        tg_send_message(build_daily_zafer_text())
    if now_tr.hour == 23 and now_tr.minute == 0 and daily_flags.get("top24_date") != today_str:
        daily_flags["top24_date"] = today_str
        save_state()
        tg_send_message(build_top24_summary_text())


# ==============================================================================
# ARKA PLAN TARAMA & TELEGRAM KOMUT DONGULERI
# ==============================================================================
def scanner_loop():
    global last_scan_at, scan_loops
    refresh_active_trading_symbols()
    last_exinfo_at = time.time()
    while running_event.is_set():
        now_sec = time.time()
        if now_sec - last_exinfo_at >= 900.0:
            refresh_active_trading_symbols()
            last_exinfo_at = now_sec
        try:
            tickers = fetch_binance_json("/api/v3/ticker/24hr", timeout=10)
            now_sec = time.time()
            now_ms = now_sec * 1000.0
            if isinstance(tickers, list):
                for item in tickers:
                    try:
                        bid = float(item.get("bidPrice") or 0)
                        ask = float(item.get("askPrice") or 0)
                        cnt = int(item.get("count") or 0)
                        close_ms = float(item.get("closeTime") or 0)
                        is_fresh = (
                            bid > 0
                            and ask > 0
                            and cnt > 0
                            and (close_ms <= 0 or (now_ms - close_ms) <= 1_800_000.0)
                        )
                        evaluate_ticker_record(
                            item["symbol"],
                            float(item["lastPrice"]),
                            float(item["priceChangePercent"]),
                            float(item["quoteVolume"]),
                            now_sec,
                            is_fresh=is_fresh,
                        )
                    except Exception:
                        continue
                update_signal_peaks()
                last_scan_at = now_sec
                scan_loops += 1
        except Exception:
            pass
        time.sleep(3.0)


def aux_loop():
    last_news_at = 0.0
    while running_event.is_set():
        now_sec = time.time()
        if now_sec - last_news_at >= 60.0:
            poll_binance_announcements()
            last_news_at = now_sec
        check_scheduled_reports()
        time.sleep(15.0)


def telegram_commands_loop():
    if not BOT_TOKEN:
        return
    offset = 0
    while running_event.is_set():
        try:
            res = tg_request("getUpdates", {"offset": offset, "timeout": 25}, timeout=30)
            if res and res.get("ok"):
                for upd in res.get("result", []):
                    offset = upd["update_id"] + 1

                    # Inline buton callback islemleri (Anlık Spot Piyasa 7dk / 24s / Hacim / Güncelle)
                    cb = upd.get("callback_query")
                    if cb:
                        cb_id = cb.get("id")
                        cb_data = cb.get("data") or ""
                        cb_msg = cb.get("message") or {}
                        cid = str((cb_msg.get("chat") or {}).get("id") or CHAT_ID)
                        mid = cb_msg.get("message_id")
                        if cb_data.startswith("mkt:"):
                            sort_key = cb_data.split(":", 1)[1]
                            if mid:
                                tg_edit_message(
                                    cid,
                                    mid,
                                    build_spot_market_text(sort_by=sort_key),
                                    reply_markup=build_spot_market_keyboard(sort_key),
                                )
                            tg_request("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Piyasa güncellendi"})
                        elif cb_data.startswith("mktimg:"):
                            sort_key = cb_data.split(":", 1)[1]
                            png = render_spot_market_png(sort_by=sort_key)
                            tg_send_photo(png, f"📊 <b>Anlık Spot Piyasa</b> ({sort_key}) · Güncel TR")
                            tg_request("answerCallbackQuery", {"callback_query_id": cb_id})
                        continue

                    msg = upd.get("message") or {}
                    raw_text = (msg.get("text") or "").strip()
                    text = raw_text.lower()
                    cid = str((msg.get("chat") or {}).get("id") or CHAT_ID)
                    if not text:
                        continue

                    if text.startswith("/piyasagorsel"):
                        arg = text.replace("/piyasagorsel", "", 1).strip()
                        sort_key = "window7m" if "7" in arg else ("volume" if "hacim" in arg else "change24h")
                        png = render_spot_market_png(sort_by=sort_key)
                        tg_send_photo(png, build_spot_market_text(sort_by=sort_key)[:980], chat_id=cid)
                    elif text.startswith("/piyasa") or text.startswith("/radar") or text.startswith("/yukselenler"):
                        arg = raw_text.split(" ", 1)[1].strip() if " " in raw_text else ""
                        sort_key = "change24h"
                        query = ""
                        if arg.lower() in ("7dk", "7", "7m"):
                            sort_key = "window7m"
                        elif arg.lower() in ("hacim", "vol", "volume"):
                            sort_key = "volume"
                        elif arg.lower() in ("24s", "24h", "24"):
                            sort_key = "change24h"
                        else:
                            query = arg
                        tg_send_message(
                            build_spot_market_text(sort_by=sort_key, query=query),
                            chat_id=cid,
                            reply_markup=build_spot_market_keyboard(sort_key) if not query else None,
                        )
                    elif text.startswith("/durum") or text.startswith("/start"):
                        with state_lock:
                            lk_cnt = len(pump_locks)
                            sig_cnt = len(tracked_signals)
                            act_cnt = len(latest_tickers)
                        tg_send_message(
                            f"🤖 <b>Spot Anlık Pump Dedektörü Aktif</b>\n"
                            f"• Motor: 15dk Hacimli (≥+3.2%, RVOL ≥2.0x) & 0-7dk Ani (+4.2%)\n"
                            f"• Dalga Kilidi: {PUMP_REPEAT_HOURS:g} saat ({lk_cnt} coin kilitli)\n"
                            f"• Aktif TRADING Parite: {act_cnt} | Tarama Turu: {scan_loops} | Sinyal: {sig_cnt}\n\n"
                            f"📌 <b>Komutlar:</b>\n"
                            f"/piyasa — Canlı Anlık Spot Piyasa (7dk % / 24s % / Hacim)\n"
                            f"/piyasagorsel — Anlık Spot Piyasa PNG Kartı\n"
                            f"/zafer · /top24 · /kilitler · /kilitsifirla",
                            chat_id=cid,
                            reply_markup={"inline_keyboard": [[{"text": "📊 Anlık Spot Piyasa", "callback_data": "mkt:change24h"}]]},
                        )
                    elif text.startswith("/kilitsifirla"):
                        with state_lock:
                            pump_locks.clear()
                        save_state()
                        tg_send_message("🔓 Tüm pump dalga kilitleri sıfırlandı.", chat_id=cid)
                    elif text.startswith("/kilitler"):
                        with state_lock:
                            items = [f"• {v['base']} ({v['symbol']})" for v in list(pump_locks.values())[:25]]
                        tg_send_message(
                            "🔒 <b>Aktif Pump Dalga Kilitleri</b>\n" + ("\n".join(items) if items else "Kilitli coin yok."),
                            chat_id=cid,
                        )
                    elif text.startswith("/zafer"):
                        tg_send_message(build_daily_zafer_text(), chat_id=cid)
                    elif text.startswith("/top24"):
                        tg_send_message(build_top24_summary_text(), chat_id=cid)
        except Exception:
            time.sleep(5.0)


# ==============================================================================
# RENDER HEALTH CHECK HTTP SUNUCUSU
# ==============================================================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        with state_lock:
            payload = {
                "ok": True,
                "engine": "PUMP_ONLY",
                "repeatHours": PUMP_REPEAT_HOURS,
                "scanLoops": scan_loops,
                "trackedPairs": len(latest_tickers),
                "signalsCount": len(tracked_signals),
                "lockedCoins": len(pump_locks),
            }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def main():
    load_state()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: running_event.clear())

    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=aux_loop, daemon=True).start()
    threading.Thread(target=telegram_commands_loop, daemon=True).start()

    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"[Spot Pump Bot] PORT={PORT} | Dalga Kilidi={PUMP_REPEAT_HOURS}h | Aktif.")
    try:
        while running_event.is_set():
            server.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        running_event.clear()
        save_state()


if __name__ == "__main__":
    main()

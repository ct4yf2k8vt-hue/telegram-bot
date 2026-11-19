#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CRYPTO SPOT BOTU (2 AKTIF MOTOR - SIFIR HARICI KUTUPHANE)
1) SPOT ANLIK PUMP DEDEKTORU (0-7 Dk Canli Pencere + 1m Mum Grafikli PNG)
2) TUM ZAMANLARIN EN DUSUGU - ATL (Binance W1 Grafikli: Fiyat + Hacim $M + RSI)
"""

import os
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
# 1. YAPILANDIRMA
# ==============================================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "1623907197").strip()
PORT = int(os.environ.get("PORT", "10000"))

TR_TZ = timezone(timedelta(hours=3))

# Motor 1: 0-7 Dk Spot Pump Ayarlari
SCAN_INTERVAL_SEC = 20          # Kac saniyede bir canli fiyat cekilsin
PUMP_WINDOW_SEC = 420           # 7 dakika = 420 saniye
PUMP_COOLDOWN_SEC = 1800        # Ayni parite pump bekleme suresi (30 dk)
MIN_PUMP_PCT_USDT = 4.5         # USDT paritelerinde min %4.5 sicrama
MIN_PUMP_PCT_ALTQ = 4.0         # BTC / BNB paritelerinde min %4.0 sicrama

MIN_VOL_24H = {
    "USDT": 500_000.0,
    "BTC": 8.0,
    "BNB": 400.0,
}

# Motor 2: Tum Zamanlarin En Dusugu (ATL - W1) Ayarlari
ATL_SCAN_INTERVAL_SEC = 300     # 5 dakikada bir ATL taramasi
ATL_MIN_VOL_24H_USDT = 500_000.0
ATL_COOLDOWN_SEC = 43200        # Ayni coin icin 12 saat bekleme (veya %2 daha duserse)

# Sabit coinler ve kaldiracli/hisse/istenmeyen token filtreleri
HARIC_SEMBOLLER = {
    "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT", "USDPUSDT", "DAIUSDT",
    "EURUSDT", "AEURUSDT", "USD1USDT", "RLUSDUSDT", "USDEUSDT", "XUSDUSDT",
    "EURIUSDT", "BFUSDUSDT", "PAXGUSDT", "ORCLBUSDT",
    "BTCUSDC", "ETHUSDC", "BNBUSDC", "SOLUSDC",
}
HARIC_BASLANGIC = ("USD", "EUR", "GBP", "AUD", "TRY", "BRL", "JPY")
HARIC_BITIS_PARCA = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

BINANCE_SPOT_MIRRORS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]

# Bellek durumlari
_SPOT_WINDOW = {}       # symbol -> list of (ts, price)
_PUMP_COOLDOWN = {}     # symbol -> last_alert_ts
_ATL_HISTORY = {}       # symbol -> (last_ts, last_reported_atl_price)
_STATE_LOCK = threading.Lock()
_STATS = {
    "started_at": time.time(),
    "pump_scans": 0,
    "pump_signals": 0,
    "atl_scans": 0,
    "atl_signals": 0,
    "last_pump_symbol": "-",
    "last_atl_symbol": "-",
}


# ==============================================================================
# 2. YARDIMCI FONKSIYONLAR & API
# ==============================================================================
def tr_now_str():
    return datetime.now(TR_TZ).strftime("%d.%m.%Y %H:%M:%S")


def fmt_price(p):
    if p <= 0:
        return "0"
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:.4f}"
    if p >= 0.01:
        return f"{p:.6f}"
    if p >= 0.0001:
        return f"{p:.8f}"
    return f"{p:.10f}"


def fmt_price_short(p):
    """ATL mesaj basligi icin sade fiyat gosterimi ($0.177 gibi)."""
    if p <= 0:
        return "0"
    if p >= 1000:
        return f"{p:,.0f}"
    if p >= 10:
        return f"{p:.2f}"
    if p >= 0.1:
        return f"{p:.3f}"
    if p >= 0.001:
        return f"{p:.5f}"
    return f"{p:.7f}"


def fmt_vol_short(v):
    """Hacmi 8M, 12.4M, 850K formatinda kisaltir."""
    if v >= 1_000_000_000:
        val = v / 1_000_000_000.0
        return f"{val:.1f}B" if val < 10 else f"{val:.0f}B"
    if v >= 1_000_000:
        val = v / 1_000_000.0
        return f"{val:.1f}M" if val < 10 and abs(val - round(val)) > 0.05 else f"{val:.0f}M"
    if v >= 1_000:
        return f"{v / 1_000.0:.0f}K"
    return f"{v:.0f}"


def http_get_json(url, timeout=8):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; SpotPumpATLBot/2.0)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_spot_tickers():
    """Binance Spot 24hr tum pariteleri ayna listesi uzerinden guvenle ceker."""
    last_err = None
    for base in BINANCE_SPOT_MIRRORS:
        try:
            data = http_get_json(f"{base}/api/v3/ticker/24hr", timeout=10)
            if isinstance(data, list) and len(data) > 100:
                return data, base
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"Binance Spot ticker alinamadi: {last_err}")


def fetch_spot_klines(symbol, interval="1m", limit=60, preferred_base=None):
    """Spot mum verisini ceker (quoteVolume dahil)."""
    mirrors = list(BINANCE_SPOT_MIRRORS)
    if preferred_base and preferred_base in mirrors:
        mirrors.remove(preferred_base)
        mirrors.insert(0, preferred_base)

    for base in mirrors:
        try:
            url = f"{base}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
            raw = http_get_json(url, timeout=8)
            if isinstance(raw, list) and len(raw) >= 2:
                candles = []
                for k in raw:
                    candles.append({
                        "t": int(k[0]) / 1000.0,
                        "o": float(k[1]),
                        "h": float(k[2]),
                        "l": float(k[3]),
                        "c": float(k[4]),
                        "v": float(k[5]),
                        "qv": float(k[7]) if len(k) > 7 else float(k[5]) * float(k[4]),
                    })
                return candles
        except Exception:
            continue
    return []


def split_quote(symbol):
    for q in ("USDT", "BTC", "BNB"):
        if symbol.endswith(q) and len(symbol) > len(q):
            return symbol[:-len(q)], q
    return None, None


def is_valid_symbol(symbol):
    if symbol in HARIC_SEMBOLLER:
        return False
    base, quote = split_quote(symbol)
    if not base or not quote:
        return False
    if symbol.endswith(HARIC_BITIS_PARCA):
        return False
    if quote == "USDT" and base in HARIC_BASLANGIC:
        return False
    if base.endswith(("UP", "DOWN", "BULL", "BEAR")) and len(base) > 4:
        return False
    return True


def calc_rsi_series(closes, period=14):
    """Her mum icin RSI dizisi uretir."""
    n = len(closes)
    if n < 2:
        return [50.0] * n
    rsis = [50.0] * n
    gains = []
    losses = []
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    p = min(period, len(gains))
    avg_gain = sum(gains[:p]) / p
    avg_loss = sum(losses[:p]) / p

    for i in range(1, n):
        idx = i - 1
        if idx >= p:
            avg_gain = (avg_gain * (p - 1) + gains[idx]) / p
            avg_loss = (avg_loss * (p - 1) + losses[idx]) / p
        if avg_loss == 0:
            rsis[i] = 100.0 if avg_gain > 0 else 50.0
        else:
            rs = avg_gain / avg_loss
            rsis[i] = 100.0 - (100.0 / (1.0 + rs))
    return rsis


# ==============================================================================
# 3. TELEGRAM GONDERIM (HTML & CANLI PNG FOTO)
# ==============================================================================
def tg_send_text(chat_id, text):
    if not BOT_TOKEN:
        print("[TG TEXT YOK - BOT_TOKEN BOS]\n" + text)
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text[:4090],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[TG TEXT HATA] {e}")
        return False


def tg_send_photo(chat_id, png_bytes, caption, filename="chart.png"):
    if not BOT_TOKEN:
        print(f"[TG PHOTO YOK - BOT_TOKEN BOS] bytes={len(png_bytes)}\n{caption}")
        return False

    boundary = uuid.uuid4().hex
    parts = []

    def add_field(name, value):
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
        )
        parts.append(str(value).encode("utf-8") + b"\r\n")

    add_field("chat_id", chat_id)
    add_field("parse_mode", "HTML")
    add_field("caption", caption[:1020])

    parts.append(f"--{boundary}\r\n".encode("utf-8"))
    parts.append(
        f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'.encode("utf-8")
    )
    parts.append(b"Content-Type: image/png\r\n\r\n")
    parts.append(png_bytes)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))

    body = b"".join(parts)
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=18) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[TG PHOTO HATA] {e} -> Metin olarak deneniyor...")
        return tg_send_text(chat_id, caption)


# ==============================================================================
# 4. SAF PYTHON CANLI GRAFIK PNG MOTORU (5x7 BITMAP FONT + CANDLESTICK)
# ==============================================================================
_FONT5X7 = {
    "A": [0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
    "B": [0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E],
    "C": [0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E],
    "D": [0x1C, 0x12, 0x11, 0x11, 0x11, 0x12, 0x1C],
    "E": [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F],
    "F": [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10],
    "G": [0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F],
    "H": [0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
    "I": [0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E],
    "J": [0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C],
    "K": [0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11],
    "L": [0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F],
    "M": [0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11],
    "N": [0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11],
    "O": [0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
    "P": [0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10],
    "Q": [0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D],
    "R": [0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11],
    "S": [0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E],
    "T": [0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04],
    "U": [0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
    "V": [0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04],
    "W": [0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11],
    "X": [0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11],
    "Y": [0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04],
    "Z": [0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F],
    "0": [0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E],
    "1": [0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E],
    "2": [0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F],
    "3": [0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E],
    "4": [0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02],
    "5": [0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E],
    "6": [0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E],
    "7": [0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08],
    "8": [0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E],
    "9": [0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C],
    "+": [0x00, 0x04, 0x04, 0x1F, 0x04, 0x04, 0x00],
    "-": [0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00],
    ".": [0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C],
    ",": [0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x08],
    ":": [0x00, 0x0C, 0x0C, 0x00, 0x0C, 0x0C, 0x00],
    "/": [0x01, 0x02, 0x02, 0x04, 0x08, 0x08, 0x10],
    "%": [0x18, 0x19, 0x02, 0x04, 0x08, 0x13, 0x03],
    "$": [0x04, 0x0F, 0x14, 0x0E, 0x05, 0x1E, 0x04],
    "(": [0x02, 0x04, 0x08, 0x08, 0x08, 0x04, 0x02],
    ")": [0x08, 0x04, 0x02, 0x02, 0x02, 0x04, 0x08],
    ">": [0x10, 0x08, 0x04, 0x02, 0x04, 0x08, 0x10],
    "|": [0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04],
    " ": [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
}


class _Canvas:
    def __init__(self, w, h, bg=(11, 15, 25)):
        self.w = w
        self.h = h
        self.buf = bytearray(bg * (w * h))

    def px(self, x, y, col):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.buf[i] = col[0]
            self.buf[i + 1] = col[1]
            self.buf[i + 2] = col[2]

    def rect(self, x1, y1, x2, y2, col):
        x1, x2 = max(0, min(x1, x2)), min(self.w - 1, max(x1, x2))
        y1, y2 = max(0, min(y1, y2)), min(self.h - 1, max(y1, y2))
        for y in range(y1, y2 + 1):
            row_start = (y * self.w + x1) * 3
            for x in range(x1, x2 + 1):
                i = row_start + (x - x1) * 3
                self.buf[i] = col[0]
                self.buf[i + 1] = col[1]
                self.buf[i + 2] = col[2]

    def rect_outline(self, x1, y1, x2, y2, col):
        self.line(x1, y1, x2, y1, col)
        self.line(x1, y2, x2, y2, col)
        self.line(x1, y1, x1, y2, col)
        self.line(x2, y1, x2, y2, col)

    def line(self, x0, y0, x1, y1, col):
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self.px(x0, y0, col)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def thick_line(self, x0, y0, x1, y1, col):
        self.line(x0, y0, x1, y1, col)
        self.line(x0, y0 + 1, x1, y1 + 1, col)

    def dashed_h(self, x0, x1, y, col, dash=6, gap=4):
        x = x0
        while x <= x1:
            end_x = min(x1, x + dash - 1)
            self.line(x, y, end_x, y, col)
            x += dash + gap

    def text(self, x, y, s, col=(235, 240, 248), scale=2):
        s = (
            s.upper()
            .replace("İ", "I")
            .replace("Ğ", "G")
            .replace("Ü", "U")
            .replace("Ş", "S")
            .replace("Ö", "O")
            .replace("Ç", "C")
        )
        cx = x
        for ch in s:
            glyph = _FONT5X7.get(ch, _FONT5X7[" "])
            for row_i, bits in enumerate(glyph):
                for col_i in range(5):
                    if bits & (1 << (4 - col_i)):
                        if scale == 1:
                            self.px(cx + col_i, y + row_i, col)
                        else:
                            px0 = cx + col_i * scale
                            py0 = y + row_i * scale
                            self.rect(px0, py0, px0 + scale - 1, py0 + scale - 1, col)
            cx += 6 * scale

    def text_v(self, x, y, s, col=(180, 186, 196)):
        """Dikey eksen basligi (harfleri yukaridan asagi dizer)."""
        s = (
            s.upper()
            .replace("İ", "I")
            .replace("Ğ", "G")
            .replace("Ü", "U")
            .replace("Ş", "S")
            .replace("Ö", "O")
            .replace("Ç", "C")
        )
        cy = y
        for ch in s:
            glyph = _FONT5X7.get(ch, _FONT5X7[" "])
            for row_i, bits in enumerate(glyph):
                for col_i in range(5):
                    if bits & (1 << (4 - col_i)):
                        self.px(x + col_i, cy + row_i, col)
            cy += 9

    def to_png(self):
        raw = bytearray()
        stride = self.w * 3
        for y in range(self.h):
            raw.append(0)
            start = y * stride
            raw.extend(self.buf[start : start + stride])

        def chunk(tag, data):
            return (
                struct.pack(">I", len(data))
                + tag
                + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            )

        ihdr = struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)
        idat = zlib.compress(bytes(raw), level=6)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", idat)
            + chunk(b"IEND", b"")
        )


def render_pump_chart_png(symbol, candles, start_price, cur_price, pct, elapsed_sec, vol_24h, quote):
    """Motor 1: Canli 1m mum grafigi + hacim barlari + 7 dk baslangic & anlik pump cizgileri."""
    W, H = 960, 540
    c = _Canvas(W, H, bg=(10, 14, 23))

    c.rect(0, 0, W - 1, 64, (17, 24, 39))
    c.line(0, 64, W - 1, 64, (36, 48, 72))

    c.rect(18, 15, 110, 47, (16, 120, 72))
    c.text(28, 25, "CANLI", (255, 255, 255), scale=2)

    elapsed_min = max(1, int(round(elapsed_sec / 60.0)))
    c.text(126, 16, f"SPOT PUMP: {symbol}", (240, 246, 255), scale=2)
    c.text(
        126,
        38,
        f"+{pct:.2f}% ({elapsed_min} DK) | 24S HACIM: {vol_24h:,.0f} {quote}",
        (134, 239, 172),
        scale=1,
    )
    c.text(W - 140, 26, tr_now_str(), (148, 163, 184), scale=1)

    plot_l, plot_r = 24, 795
    plot_t, plot_b = 84, 385
    vol_t, vol_b = 402, 492

    c.rect(plot_l, plot_t, plot_r, plot_b, (13, 19, 31))
    c.rect_outline(plot_l, plot_t, plot_r, plot_b, (31, 41, 61))
    c.rect(plot_l, vol_t, plot_r, vol_b, (13, 19, 31))
    c.rect_outline(plot_l, vol_t, plot_r, vol_b, (31, 41, 61))

    if not candles:
        candles = [
            {
                "o": start_price,
                "h": max(start_price, cur_price),
                "l": min(start_price, cur_price),
                "c": cur_price,
                "v": 1.0,
            }
        ]

    hi = max(max(k["h"] for k in candles), start_price, cur_price)
    lo = min(min(k["l"] for k in candles), start_price, cur_price)
    span = max(hi - lo, cur_price * 0.005, 1e-12)
    hi += span * 0.12
    lo -= span * 0.10
    total_span = max(hi - lo, 1e-12)

    def y_of(price):
        ratio = (hi - price) / total_span
        return int(plot_t + 8 + ratio * (plot_b - plot_t - 16))

    for i in range(5):
        gy = int(plot_t + 8 + i * (plot_b - plot_t - 16) / 4.0)
        gp = hi - (i / 4.0) * total_span
        c.line(plot_l + 1, gy, plot_r - 1, gy, (24, 33, 52))
        c.text(plot_r + 10, gy - 4, fmt_price(gp), (148, 163, 184), scale=1)

    n = len(candles)
    usable_w = plot_r - plot_l - 20
    step = usable_w / max(n, 1)
    body_w = max(3, min(14, int(step * 0.66)))
    max_vol = max((k["v"] for k in candles), default=1.0) or 1.0

    pump_bars = max(1, min(n, elapsed_min + 1))
    pump_x_start = int(plot_l + 10 + (n - pump_bars) * step)
    c.rect(pump_x_start, plot_t + 1, plot_r - 1, plot_b - 1, (16, 33, 30))

    for i, k in enumerate(candles):
        cx = int(plot_l + 10 + (i + 0.5) * step)
        bull = k["c"] >= k["o"]
        col = (34, 197, 94) if bull else (239, 68, 68)

        yh = y_of(k["h"])
        yl = y_of(k["l"])
        yo = y_of(k["o"])
        yc = y_of(k["c"])

        c.line(cx, yh, cx, yl, col)
        bx1 = cx - body_w // 2
        bx2 = cx + body_w // 2
        top_b, bot_b = min(yo, yc), max(yo, yc)
        if bot_b == top_b:
            bot_b += 1
        c.rect(bx1, top_b, bx2, bot_b, col)

        vh = int((k["v"] / max_vol) * (vol_b - vol_t - 10))
        if vh < 2:
            vh = 2
        vcol = (22, 163, 74) if bull else (185, 28, 28)
        c.rect(bx1, vol_b - 2 - vh, bx2, vol_b - 2, vcol)

    y_start = y_of(start_price)
    y_cur = y_of(cur_price)
    c.dashed_h(plot_l + 2, plot_r - 2, y_start, (251, 191, 36))
    c.dashed_h(plot_l + 2, plot_r - 2, y_cur, (52, 211, 153))

    lbl_cur_y = y_cur
    lbl_start_y = y_start
    if abs(lbl_start_y - lbl_cur_y) < 20:
        lbl_start_y = min(plot_b - 10, lbl_cur_y + 20)

    c.rect(plot_r + 6, lbl_start_y - 9, W - 8, lbl_start_y + 9, (120, 80, 10))
    c.text(plot_r + 12, lbl_start_y - 4, f"BAS: {fmt_price(start_price)}", (254, 240, 138), scale=1)

    c.rect(plot_r + 6, lbl_cur_y - 9, W - 8, lbl_cur_y + 9, (6, 95, 70))
    c.text(plot_r + 12, lbl_cur_y - 4, f"SON: {fmt_price(cur_price)}", (236, 253, 245), scale=1)

    c.text(plot_l, vol_t - 12, "1M MUM HACMI (SON 60 DK)", (148, 163, 184), scale=1)
    c.text(
        plot_l,
        508,
        f"BASLANGIC: {fmt_price(start_price)}  ->  ANLIK: {fmt_price(cur_price)}  |  DEGISIM: +{pct:.2f}%",
        (203, 213, 225),
        scale=1,
    )

    return c.to_png()


def render_atl_w1_chart_png(symbol, w1_candles, cur_price):
    """
    Motor 2: Gorseldeki (HYPERUSDT W1 Binance) duzeniyle birebir
    Fiyat (W1 Mum) + Hacim $M + RSI panelli PNG grafigi.
    """
    W, H = 900, 740
    bg = (22, 25, 30)
    panel_bg = (26, 29, 35)
    grid_col = (44, 48, 56)
    border_col = (70, 76, 86)
    green_col = (8, 153, 129)
    red_col = (242, 54, 69)
    txt_col = (209, 213, 219)
    muted_col = (148, 154, 166)

    c = _Canvas(W, H, bg=bg)

    show = w1_candles[-40:] if len(w1_candles) > 40 else list(w1_candles)
    if not show:
        show = [{"t": time.time(), "o": cur_price, "h": cur_price, "l": cur_price, "c": cur_price, "v": 1.0, "qv": 1_000_000.0}]

    title = f"{symbol} W1 BINANCE"
    tx = max(20, (W - len(title) * 12) // 2)
    c.text(tx, 16, title, (235, 238, 244), scale=2)

    pl, pr = 92, W - 26
    p_top, p_bot = 46, 480         # Ana Fiyat Paneli
    v_top, v_bot = 508, 608        # Hacim $M Paneli
    r_top, r_bot = 626, 718        # RSI Paneli

    for top, bot in ((p_top, p_bot), (v_top, v_bot), (r_top, r_bot)):
        c.rect(pl, top, pr, bot, panel_bg)
        c.rect_outline(pl, top, pr, bot, border_col)

    c.text_v(16, (p_top + p_bot) // 2 - 22, "FIYAT", muted_col)
    c.text_v(16, (v_top + v_bot) // 2 - 30, "HACIM$M", muted_col)
    c.text_v(16, (r_top + r_bot) // 2 - 12, "RSI", muted_col)

    hi = max(max(k["h"] for k in show), cur_price)
    lo = min(min(k["l"] for k in show), cur_price)
    span = max(hi - lo, cur_price * 0.02, 1e-12)
    hi += span * 0.08
    lo = max(0.0, lo - span * 0.08)
    total_span = max(hi - lo, 1e-12)

    def y_price(p):
        return int(p_top + 6 + ((hi - p) / total_span) * (p_bot - p_top - 12))

    for i in range(9):
        gy = int(p_top + 8 + i * (p_bot - p_top - 16) / 8.0)
        gp = hi - (i / 8.0) * total_span
        c.line(pl + 1, gy, pr - 1, gy, grid_col)
        lbl = fmt_price_short(gp)
        lx = max(32, pl - 8 - len(lbl) * 6)
        c.text(lx, gy - 3, lbl, muted_col, scale=1)

    n = len(show)
    usable_w = pr - pl - 30
    step = usable_w / max(n, 1)
    body_w = max(6, min(int(step * 0.72), 160))

    date_step = max(1, n // 5)
    for i, k in enumerate(show):
        cx = int(pl + 15 + (i + 0.5) * step)
        if i % date_step == 0 or i == n - 1:
            c.line(cx, p_top + 1, cx, p_bot - 1, grid_col)
            c.line(cx, v_top + 1, cx, v_bot - 1, grid_col)
            c.line(cx, r_top + 1, cx, r_bot - 1, grid_col)
            dt_str = datetime.fromtimestamp(k["t"], tz=TR_TZ).strftime("%d.%m.%Y")
            dx = max(pl, min(pr - 60, cx - 28))
            c.text(dx, p_bot + 10, dt_str, txt_col, scale=1)

        bull = k["c"] >= k["o"]
        col = green_col if bull else red_col
        yh = y_price(k["h"])
        yl = y_price(k["l"])
        yo = y_price(k["o"])
        yc = y_price(k["c"])

        c.line(cx, yh, cx, yl, col)
        c.line(cx + 1, yh, cx + 1, yl, col)

        bx1 = cx - body_w // 2
        bx2 = cx + body_w // 2
        top_b, bot_b = min(yo, yc), max(yo, yc)
        if bot_b - top_b < 2:
            bot_b = top_b + 2
        c.rect(bx1, top_b, bx2, bot_b, col)

    y_atl = y_price(cur_price)
    c.dashed_h(pl + 2, pr - 2, y_atl, (251, 146, 60))

    # Hacim $M Paneli
    vols_m = [max(0.0, k.get("qv", 0.0) / 1_000_000.0) for k in show]
    max_vm = max(vols_m) if vols_m else 1.0
    if max_vm <= 0:
        max_vm = 1.0

    for frac, val in ((0.15, max_vm * 0.85), (0.85, 0.0)):
        vy = int(v_top + frac * (v_bot - v_top))
        c.line(pl + 1, vy, pr - 1, vy, grid_col)
        vlbl = f"{val:.0f}" if val >= 10 else f"{val:.1f}"
        c.text(max(32, pl - 8 - len(vlbl) * 6), vy - 3, vlbl, muted_col, scale=1)

    for i, k in enumerate(show):
        cx = int(pl + 15 + (i + 0.5) * step)
        bx1 = cx - body_w // 2
        bx2 = cx + body_w // 2
        col = green_col if k["c"] >= k["o"] else red_col
        vh = int((vols_m[i] / (max_vm * 1.15)) * (v_bot - v_top - 10))
        if vh < 3:
            vh = 3
        c.rect(bx1, v_bot - 3 - vh, bx2, v_bot - 3, col)

    # RSI Paneli
    closes_all = [k["c"] for k in w1_candles]
    rsis_all = calc_rsi_series(closes_all, period=14)
    rsis_show = rsis_all[-len(show):]

    def y_rsi(val):
        clamped = max(0.0, min(100.0, val))
        return int(r_bot - 6 - (clamped / 100.0) * (r_bot - r_top - 12))

    for r_ref in (70, 50, 30):
        ry = y_rsi(r_ref)
        c.line(pl + 1, ry, pr - 1, ry, grid_col)
        rlbl = str(r_ref)
        c.text(max(32, pl - 8 - len(rlbl) * 6), ry - 3, rlbl, muted_col, scale=1)

    if len(rsis_show) >= 2:
        pts = []
        for i, rv in enumerate(rsis_show):
            cx = int(pl + 15 + (i + 0.5) * step)
            pts.append((cx, y_rsi(rv)))
        for i in range(1, len(pts)):
            c.thick_line(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1], green_col)
    else:
        ry = y_rsi(rsis_show[0] if rsis_show else 50.0)
        c.thick_line(pl + 15, ry, pr - 15, ry, green_col)

    return c.to_png()


# ==============================================================================
# 5. MESAJ FORMATLAYICILARI
# ==============================================================================
def format_pump_message(symbol, pct, start_price, cur_price, vol_24h, quote, elapsed_sec):
    dk = max(1, int(round(elapsed_sec / 60.0)))
    return (
        f"⚡ <b>SPOT ANLIK PUMP DEDEKTORU — {symbol}</b>\n\n"
        f"📈 Degisim: <b>+{pct:.2f}%</b> ({dk} dk icinde!)\n"
        f"💰 Baslangic: <code>{fmt_price(start_price)}</code>  ➡️  Anlik: <code>{fmt_price(cur_price)}</code>\n"
        f"💧 24s Parite Hacmi: <b>{vol_24h:,.0f}</b> ({quote})"
    )


def format_atl_message(symbol, cur_price, change_24h_pct, vol_24h_usdt):
    p_str = fmt_price_short(cur_price)
    v_str = fmt_vol_short(vol_24h_usdt)
    return (
        f"<b>#{symbol}</b> ${p_str} tüm zamanların en düşüğü 👎📛😭\n"
        f"24s {change_24h_pct:+.2f}% Hacim {v_str}"
    )


# ==============================================================================
# 6. MOTOR 1: 0-7 DK SPOT PUMP TARAYICI
# ==============================================================================
def process_spot_snapshot(tickers, now_ts):
    alerts = []
    with _STATE_LOCK:
        for t in tickers:
            sym = t.get("symbol", "")
            if not is_valid_symbol(sym):
                continue

            _, quote = split_quote(sym)
            try:
                cur_price = float(t.get("lastPrice", 0))
                vol_24h = float(t.get("quoteVolume", 0))
            except Exception:
                continue

            if cur_price <= 0 or vol_24h < MIN_VOL_24H.get(quote, 500_000.0):
                continue

            hist = _SPOT_WINDOW.get(sym)
            if hist is None:
                hist = []
                _SPOT_WINDOW[sym] = hist
            hist.append((now_ts, cur_price))

            cutoff = now_ts - PUMP_WINDOW_SEC
            while hist and hist[0][0] < cutoff:
                hist.pop(0)

            if len(hist) < 2:
                continue

            min_ts, min_price = min(hist, key=lambda x: x[1])
            if min_price <= 0 or min_ts >= now_ts:
                continue

            pct = ((cur_price - min_price) / min_price) * 100.0
            threshold = MIN_PUMP_PCT_USDT if quote == "USDT" else MIN_PUMP_PCT_ALTQ

            if pct >= threshold:
                last_cd = _PUMP_COOLDOWN.get(sym, 0.0)
                if now_ts - last_cd >= PUMP_COOLDOWN_SEC:
                    _PUMP_COOLDOWN[sym] = now_ts
                    alerts.append({
                        "symbol": sym,
                        "quote": quote,
                        "pct": pct,
                        "start_price": min_price,
                        "cur_price": cur_price,
                        "vol_24h": vol_24h,
                        "elapsed_sec": max(30.0, now_ts - min_ts),
                    })
    return alerts


def spot_pump_worker():
    print("[MOTOR 1 - SPOT PUMP] Baslatildi (0-7 dk pencere).")
    while True:
        try:
            now_ts = time.time()
            tickers, mirror_used = fetch_spot_tickers()
            alerts = process_spot_snapshot(tickers, now_ts)

            with _STATE_LOCK:
                _STATS["pump_scans"] += 1

            for al in alerts:
                sym = al["symbol"]
                candles = fetch_spot_klines(
                    sym, interval="1m", limit=60, preferred_base=mirror_used
                )
                png_bytes = render_pump_chart_png(
                    symbol=sym,
                    candles=candles,
                    start_price=al["start_price"],
                    cur_price=al["cur_price"],
                    pct=al["pct"],
                    elapsed_sec=al["elapsed_sec"],
                    vol_24h=al["vol_24h"],
                    quote=al["quote"],
                )
                msg = format_pump_message(
                    symbol=sym,
                    pct=al["pct"],
                    start_price=al["start_price"],
                    cur_price=al["cur_price"],
                    vol_24h=al["vol_24h"],
                    quote=al["quote"],
                    elapsed_sec=al["elapsed_sec"],
                )
                tg_send_photo(CHAT_ID, png_bytes, msg, filename=f"{sym}_pump.png")
                with _STATE_LOCK:
                    _STATS["pump_signals"] += 1
                    _STATS["last_pump_symbol"] = sym
                time.sleep(0.5)

        except Exception as e:
            print(f"[SPOT PUMP HATA] {e}")

        time.sleep(SCAN_INTERVAL_SEC)


# ==============================================================================
# 7. MOTOR 2: TUM ZAMANLARIN EN DUSUGU (ATL - BINANCE W1) TARAYICI
# ==============================================================================
def check_symbol_atl(symbol, cur_price, low_24h, change_24h_pct, vol_24h_usdt, w1_candles, now_ts):
    if not w1_candles or len(w1_candles) < 2 or cur_price <= 0:
        return None

    prev_atl = min(k["l"] for k in w1_candles[:-1])
    if prev_atl <= 0:
        return None

    if not (cur_price <= prev_atl * 1.003 and low_24h <= prev_atl):
        return None

    with _STATE_LOCK:
        last_ts, last_p = _ATL_HISTORY.get(symbol, (0.0, 0.0))
        if now_ts - last_ts < ATL_COOLDOWN_SEC and (last_p == 0 or cur_price > last_p * 0.98):
            return None
        _ATL_HISTORY[symbol] = (now_ts, cur_price)

    return {
        "symbol": symbol,
        "cur_price": cur_price,
        "prev_atl": prev_atl,
        "change_24h_pct": change_24h_pct,
        "vol_24h_usdt": vol_24h_usdt,
        "w1_candles": w1_candles,
    }


def atl_scanner_worker():
    print("[MOTOR 2 - ATL W1] Tum zamanlarin en dusugu tarayicisi baslatildi.")
    time.sleep(8)
    while True:
        try:
            tickers, mirror_used = fetch_spot_tickers()
            candidates = []
            for t in tickers:
                sym = t.get("symbol", "")
                if not sym.endswith("USDT") or not is_valid_symbol(sym):
                    continue
                try:
                    cur_p = float(t.get("lastPrice", 0))
                    low_24h = float(t.get("lowPrice", 0))
                    vol_24h = float(t.get("quoteVolume", 0))
                    chg_24h = float(t.get("priceChangePercent", 0))
                except Exception:
                    continue
                if cur_p <= 0 or vol_24h < ATL_MIN_VOL_24H_USDT:
                    continue
                if chg_24h < 0 and low_24h > 0 and cur_p <= low_24h * 1.03:
                    candidates.append((sym, cur_p, low_24h, chg_24h, vol_24h))

            for sym, cur_p, low_24h, chg_24h, vol_24h in candidates:
                w1 = fetch_spot_klines(sym, interval="1w", limit=1000, preferred_base=mirror_used)
                hit = check_symbol_atl(sym, cur_p, low_24h, chg_24h, vol_24h, w1, time.time())
                if hit:
                    png_bytes = render_atl_w1_chart_png(sym, hit["w1_candles"], cur_p)
                    msg = format_atl_message(sym, cur_p, chg_24h, vol_24h)
                    tg_send_photo(CHAT_ID, png_bytes, msg, filename=f"{sym}_atl_w1.png")
                    with _STATE_LOCK:
                        _STATS["atl_signals"] += 1
                        _STATS["last_atl_symbol"] = sym
                    time.sleep(0.6)
                time.sleep(0.15)

            with _STATE_LOCK:
                _STATS["atl_scans"] += 1

        except Exception as e:
            print(f"[ATL W1 HATA] {e}")

        time.sleep(ATL_SCAN_INTERVAL_SEC)


# ==============================================================================
# 8. BASIT TELEGRAM KOMUT DINLEYICI (/durum, /start)
# ==============================================================================
def telegram_command_worker():
    if not BOT_TOKEN:
        return
    offset = 0
    print("[TG POLL] Komut dinleyici aktif (/durum).")
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?timeout=25&offset={offset}"
            data = http_get_json(url, timeout=30)
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                txt = (msg.get("text") or "").strip().lower()
                cid = str((msg.get("chat") or {}).get("id") or "")
                if not cid:
                    continue
                if txt in ("/start", "/durum", "durum"):
                    with _STATE_LOCK:
                        p_scans = _STATS["pump_scans"]
                        p_sigs = _STATS["pump_signals"]
                        a_scans = _STATS["atl_scans"]
                        a_sigs = _STATS["atl_signals"]
                        tracked = len(_SPOT_WINDOW)
                        last_p = _STATS["last_pump_symbol"]
                        last_a = _STATS["last_atl_symbol"]
                        uptime_m = int((time.time() - _STATS["started_at"]) / 60)
                    reply = (
                        f"🤖 <b>SPOT PUMP & ATL BOTU AKTIF</b>\n\n"
                        f"• Calisma Suresi: <b>{uptime_m} dk</b>\n"
                        f"• Izlenen Parite: <b>{tracked}</b> (USDT/BTC/BNB)\n"
                        f"• ⚡ 0-7 Dk Pump: <b>{p_sigs}</b> sinyal ({p_scans} tarama) | Son: <b>{last_p}</b>\n"
                        f"• 👎 W1 Tum Zamanlar Dip (ATL): <b>{a_sigs}</b> sinyal ({a_scans} tarama) | Son: <b>{last_a}</b>"
                    )
                    tg_send_text(cid, reply)
        except Exception:
            time.sleep(5)


# ==============================================================================
# 9. RENDER HTTP HEALTH SUNUCUSU
# ==============================================================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        with _STATE_LOCK:
            payload = json.dumps({
                "status": "ok",
                "bot": "spot-pump-and-atl-bot",
                "pump_scans": _STATS["pump_scans"],
                "pump_signals": _STATS["pump_signals"],
                "atl_scans": _STATS["atl_scans"],
                "atl_signals": _STATS["atl_signals"],
                "tracked_pairs": len(_SPOT_WINDOW),
            }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


def main():
    print(f"[BASLATILIYOR] Spot Pump + W1 ATL Botu | Port={PORT} | Saat={tr_now_str()}")

    if BOT_TOKEN:
        tg_send_text(
            CHAT_ID,
            "🤖 <b>SPOT PUMP & W1 ATL BOTU BASLATILDI</b>\n"
            "• ⚡ <b>0-7 Dk Spot Pump</b> (1m canli mum grafikli)\n"
            "• 👎 <b>Tüm Zamanların En Düşüğü (ATL)</b> (Binance W1 Fiyat + Hacim $M + RSI grafikli)\n"
            "• Durum icin <code>/durum</code> yazabilirsiniz.",
        )

    t1 = threading.Thread(target=spot_pump_worker, daemon=True)
    t1.start()

    t2 = threading.Thread(target=atl_scanner_worker, daemon=True)
    t2.start()

    t3 = threading.Thread(target=telegram_command_worker, daemon=True)
    t3.start()

    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"[HTTP] 0.0.0.0:{PORT} dinleniyor...")
    server.serve_forever()


if __name__ == "__main__":
    main()

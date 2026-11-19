#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SPOT ANLIK PUMP DEDEKTORU (0-7 Dk Canli Pencere + Canli Mum Grafikli PNG)
Sifir harici kutuphane - Yalnizca Python 3 standart kutuphanesi (Render uyumlu)
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

# Tarama ve pencere ayarlari
SCAN_INTERVAL_SEC = 20          # Kac saniyede bir canli fiyat cekilsin
PUMP_WINDOW_SEC = 420           # 7 dakika = 420 saniye
COOLDOWN_SEC = 1800             # Ayni parite icin tekrar bildirim bekleme suresi (30 dk)

# Esik degerleri
MIN_PUMP_PCT_USDT = 4.5         # USDT paritelerinde 0-7 dk icinde min %4.5 sicrama
MIN_PUMP_PCT_ALTQ = 4.0         # BTC / BNB paritelerinde min %4.0 sicrama

MIN_VOL_24H = {
    "USDT": 500_000.0,          # 24s hacim en az 500k USDT
    "BTC": 8.0,                 # 24s hacim en az 8 BTC
    "BNB": 400.0,               # 24s hacim en az 400 BNB
}

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
_COOLDOWN = {}          # symbol -> last_alert_ts
_STATE_LOCK = threading.Lock()
_STATS = {
    "started_at": time.time(),
    "scans": 0,
    "signals": 0,
    "last_scan_ok": 0.0,
    "last_symbol": "-",
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


def http_get_json(url, timeout=8):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; SpotPumpBot/1.0)",
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
    """Canli grafik icin spot mum verisini ceker (1m son 60 dk)."""
    mirrors = list(BINANCE_SPOT_MIRRORS)
    if preferred_base and preferred_base in mirrors:
        mirrors.remove(preferred_base)
        mirrors.insert(0, preferred_base)

    for base in mirrors:
        try:
            url = f"{base}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
            raw = http_get_json(url, timeout=8)
            if isinstance(raw, list) and len(raw) >= 10:
                candles = []
                for k in raw:
                    candles.append({
                        "t": int(k[0]) / 1000.0,
                        "o": float(k[1]),
                        "h": float(k[2]),
                        "l": float(k[3]),
                        "c": float(k[4]),
                        "v": float(k[5]),
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


def tg_send_photo(chat_id, png_bytes, caption):
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
        b'Content-Disposition: form-data; name="photo"; filename="spot_pump.png"\r\n'
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
# 4. SAF PYTHON CANLI MUM GRAFIGI PNG MOTORU (5x7 BITMAP FONT + CANDLESTICK)
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
    """Canli 1m mum grafigi + hacim barlari + 7 dk baslangic & anlik pump cizgileri."""
    W, H = 960, 540
    c = _Canvas(W, H, bg=(10, 14, 23))

    # Ust baslik seridi
    c.rect(0, 0, W - 1, 64, (17, 24, 39))
    c.line(0, 64, W - 1, 64, (36, 48, 72))

    # Yesil canli rozet
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

    # Grafik alanlari
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

    # Yatay grid
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

    # Son pump penceresi arka plan vurgusu
    pump_bars = max(1, min(n, elapsed_min + 1))
    pump_x_start = int(plot_l + 10 + (n - pump_bars) * step)
    c.rect(pump_x_start, plot_t + 1, plot_r - 1, plot_b - 1, (16, 33, 30))

    # Mumlar & Hacim barlari
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

    # 7 dk Baslangic ve Anlik Fiyat cizgileri (etiket cakismasi engelli)
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

    # Alt bilgi seridi
    c.text(plot_l, vol_t - 12, "1M MUM HACMI (SON 60 DK)", (148, 163, 184), scale=1)
    c.text(
        plot_l,
        508,
        f"BASLANGIC: {fmt_price(start_price)}  ->  ANLIK: {fmt_price(cur_price)}  |  DEGISIM: +{pct:.2f}%",
        (203, 213, 225),
        scale=1,
    )

    return c.to_png()


# ==============================================================================
# 5. SADE MESAJ FORMATLAYICI
# ==============================================================================
def format_pump_message(symbol, pct, start_price, cur_price, vol_24h, quote, elapsed_sec):
    dk = max(1, int(round(elapsed_sec / 60.0)))
    return (
        f"⚡ <b>SPOT ANLIK PUMP DEDEKTORU — {symbol}</b>\n\n"
        f"📈 Degisim: <b>+{pct:.2f}%</b> ({dk} dk icinde!)\n"
        f"💰 Baslangic: <code>{fmt_price(start_price)}</code>  ➡️  Anlik: <code>{fmt_price(cur_price)}</code>\n"
        f"💧 24s Parite Hacmi: <b>{vol_24h:,.0f}</b> ({quote})"
    )


# ==============================================================================
# 6. ANA 0-7 DK SPOT PUMP TARAYICI MOTORU
# ==============================================================================
def process_spot_snapshot(tickers, now_ts):
    """
    Tum USDT/BTC/BNB Spot paritelerinin 0-7 dakika icindeki minimum fiyattan
    anlik fiyata sicramasini tarar.
    """
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
                last_cd = _COOLDOWN.get(sym, 0.0)
                if now_ts - last_cd >= COOLDOWN_SEC:
                    _COOLDOWN[sym] = now_ts
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
    print("[SPOT PUMP] Tarama motoru baslatildi (0-7 dk pencere).")
    while True:
        try:
            now_ts = time.time()
            tickers, mirror_used = fetch_spot_tickers()
            alerts = process_spot_snapshot(tickers, now_ts)

            with _STATE_LOCK:
                _STATS["scans"] += 1
                _STATS["last_scan_ok"] = now_ts

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
                tg_send_photo(CHAT_ID, png_bytes, msg)
                with _STATE_LOCK:
                    _STATS["signals"] += 1
                    _STATS["last_symbol"] = sym
                time.sleep(0.5)

        except Exception as e:
            print(f"[SPOT PUMP HATA] {e}")

        time.sleep(SCAN_INTERVAL_SEC)


# ==============================================================================
# 7. BASIT TELEGRAM KOMUT DINLEYICI (/durum, /start)
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
                        scans = _STATS["scans"]
                        sigs = _STATS["signals"]
                        tracked = len(_SPOT_WINDOW)
                        last_sym = _STATS["last_symbol"]
                        uptime_m = int((time.time() - _STATS["started_at"]) / 60)
                    reply = (
                        f"⚡ <b>SPOT ANLIK PUMP DEDEKTORU AKTIF</b>\n\n"
                        f"• Calisma Suresi: <b>{uptime_m} dk</b>\n"
                        f"• Toplam Tarama: <b>{scans}</b>\n"
                        f"• Izlenen Parite: <b>{tracked}</b> (USDT/BTC/BNB)\n"
                        f"• Uretilen Sinyal: <b>{sigs}</b>\n"
                        f"• Son Sinyal: <b>{last_sym}</b>"
                    )
                    tg_send_text(cid, reply)
        except Exception:
            time.sleep(5)


# ==============================================================================
# 8. RENDER HTTP HEALTH SUNUCUSU
# ==============================================================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        with _STATE_LOCK:
            payload = json.dumps({
                "status": "ok",
                "bot": "spot-pump-detector",
                "scans": _STATS["scans"],
                "signals": _STATS["signals"],
                "tracked_pairs": len(_SPOT_WINDOW),
                "last_symbol": _STATS["last_symbol"],
            }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


def main():
    print(f"[BASLATILIYOR] Spot Anlik Pump Dedektoru | Port={PORT} | Saat={tr_now_str()}")

    if BOT_TOKEN:
        tg_send_text(
            CHAT_ID,
            "⚡ <b>SPOT ANLIK PUMP DEDEKTORU BASLATILDI</b>\n"
            "• Tarama: 0-7 dk canli pencere (USDT / BTC / BNB)\n"
            "• Gorsel: Canli 1m mum grafigi + hacim\n"
            "• Durum icin <code>/durum</code> yazabilirsiniz.",
        )

    t1 = threading.Thread(target=spot_pump_worker, daemon=True)
    t1.start()

    t2 = threading.Thread(target=telegram_command_worker, daemon=True)
    t2.start()

    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"[HTTP] 0.0.0.0:{PORT} dinleniyor...")
    server.serve_forever()


if __name__ == "__main__":
    main()

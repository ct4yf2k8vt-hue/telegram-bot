#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CRYPTO SPOT BOTU (5 AKTIF MOTOR - SIFIR HARICI KUTUPHANE)
1) SPOT ANLIK PUMP DEDEKTORU (0-7 Dk Canli Pencere + 1m Mum Grafikli PNG)
2) TUM ZAMANLARIN EN DUSUGU - ATL (Binance W1 Grafikli: Fiyat + Hacim $M + RSI)
3) AKIS & DUVAR YAPISI - ERKEN KIRILIM (Emir Defteri Duvari + POC + Fib + Coklu Borsa 4h PNG)
4) GUNUN ZAFERI (Son 24s En Yuksek Kazanc Sinyali + Cizgi Grafik Kart PNG)
5) SON 24 SAATIN EN IYI HAREKETLERI (Top 5 Lider Tablosu Kart PNG)
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
TRACKER_FILE = "/tmp/spot_bot_signals_v3.json"

# Motor 1: 0-7 Dk Spot Pump Ayarlari
SCAN_INTERVAL_SEC = 20
PUMP_WINDOW_SEC = 420
PUMP_COOLDOWN_SEC = 1800
MIN_PUMP_PCT_USDT = 4.5
MIN_PUMP_PCT_ALTQ = 4.0

MIN_VOL_24H = {
    "USDT": 500_000.0,
    "BTC": 8.0,
    "BNB": 400.0,
}

# Motor 2: ATL (W1) Ayarlari
ATL_SCAN_INTERVAL_SEC = 300
ATL_MIN_VOL_24H_USDT = 500_000.0
ATL_COOLDOWN_SEC = 43200

# Motor 3: Akis & Duvar Yapisi (4h Erken Kirilim) Ayarlari
FLOW_SCAN_INTERVAL_SEC = 240    # 4 dakikada bir tarama
FLOW_MIN_VOL_24H_USDT = 2_000_000.0
FLOW_MIN_WALL_USD = 120_000.0   # En az $120K tek fiyat seviyesi emir duvari
FLOW_COOLDOWN_SEC = 21600       # Ayni coin icin 6 saat bekleme

# Motor 4 & 5: Sinyal Takip & Gunluk Rapor Ayarlari
TRACKER_INTERVAL_SEC = 120      # 2 dakikada bir zirve fiyatlari guncelle
DAILY_WIN_HOUR_TR = 21          # 21:00 TR'de Gunun Zaferi
DAILY_TOP24_HOUR_TR = 23        # 23:00 TR'de 24s En Iyi Hareketler

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
_SPOT_WINDOW = {}
_PUMP_COOLDOWN = {}
_ATL_HISTORY = {}
_FLOW_COOLDOWN = {}
_TRACKED_SIGNALS = []
_LAST_REPORT_DAY = {"win": "", "top24": ""}
_STATE_LOCK = threading.Lock()
_STATS = {
    "started_at": time.time(),
    "pump_scans": 0,
    "pump_signals": 0,
    "atl_scans": 0,
    "atl_signals": 0,
    "flow_scans": 0,
    "flow_signals": 0,
    "last_pump_symbol": "-",
    "last_atl_symbol": "-",
    "last_flow_symbol": "-",
}


# ==============================================================================
# 2. YARDIMCI FONKSIYONLAR & KALICILIK
# ==============================================================================
def tr_now():
    return datetime.now(TR_TZ)


def tr_now_str():
    return tr_now().strftime("%d.%m.%Y %H:%M:%S")


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
    if v >= 1_000_000_000:
        val = v / 1_000_000_000.0
        return f"{val:.1f}B" if val < 10 else f"{val:.0f}B"
    if v >= 1_000_000:
        val = v / 1_000_000.0
        return f"{val:.1f}M" if val < 10 and abs(val - round(val)) > 0.05 else f"{val:.0f}M"
    if v >= 1_000:
        return f"{v / 1_000.0:.0f}K"
    return f"{v:.0f}"


def fmt_usd_k(v):
    if v >= 1_000_000:
        return f"${v / 1_000_000.0:.2f}M"
    return f"${v / 1_000.0:.0f}K"


def load_tracked_signals():
    global _TRACKED_SIGNALS
    try:
        if os.path.exists(TRACKER_FILE):
            with open(TRACKER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                cutoff = time.time() - 7 * 86400
                _TRACKED_SIGNALS = [s for s in data if s.get("ts", 0) >= cutoff]
    except Exception:
        _TRACKED_SIGNALS = []


def save_tracked_signals():
    try:
        cutoff = time.time() - 7 * 86400
        cleaned = [s for s in _TRACKED_SIGNALS if s.get("ts", 0) >= cutoff]
        with open(TRACKER_FILE, "w", encoding="utf-8") as f:
            json.dump(cleaned, f)
    except Exception:
        pass


def register_tracked_signal(symbol, direction, entry_price, source="PUMP"):
    """Uretilen sinyali Gunun Zaferi ve Top 24 takibi icin kaydeder."""
    if not symbol.endswith("USDT") or entry_price <= 0:
        return
    now_ts = time.time()
    rec = {
        "id": uuid.uuid4().hex[:10],
        "symbol": symbol,
        "coin": symbol[:-4],
        "direction": direction,
        "source": source,
        "entry": float(entry_price),
        "peak_price": float(entry_price),
        "peak_pct": 0.0,
        "ts": now_ts,
        "peak_ts": now_ts,
    }
    with _STATE_LOCK:
        _TRACKED_SIGNALS.append(rec)
        if len(_TRACKED_SIGNALS) > 500:
            del _TRACKED_SIGNALS[:-500]
        save_tracked_signals()


def http_get_json(url, timeout=8):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; SpotBot/3.0)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_spot_tickers():
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


def fetch_spot_depth(symbol, limit=100, preferred_base=None):
    mirrors = list(BINANCE_SPOT_MIRRORS)
    if preferred_base and preferred_base in mirrors:
        mirrors.remove(preferred_base)
        mirrors.insert(0, preferred_base)

    for base in mirrors:
        try:
            url = f"{base}/api/v3/depth?symbol={symbol}&limit={limit}"
            raw = http_get_json(url, timeout=6)
            if isinstance(raw, dict) and "bids" in raw and "asks" in raw:
                return raw
        except Exception:
            continue
    return None


def fetch_multi_exchange_prices(symbol):
    """Binance disinda Bybit, OKX ve Gate spot fiyatlarini ceker."""
    prices = {}
    try:
        d = http_get_json(f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}", timeout=4)
        lst = (d.get("result") or {}).get("list") or []
        if lst:
            prices["Bybit"] = float(lst[0]["lastPrice"])
    except Exception:
        pass
    try:
        if symbol.endswith("USDT"):
            inst = f"{symbol[:-4]}-USDT"
            d = http_get_json(f"https://www.okx.com/api/v5/market/ticker?instId={inst}", timeout=4)
            lst = d.get("data") or []
            if lst:
                prices["OKX"] = float(lst[0]["last"])
    except Exception:
        pass
    try:
        if symbol.endswith("USDT"):
            pair = f"{symbol[:-4]}_USDT"
            d = http_get_json(f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={pair}", timeout=4)
            if isinstance(d, list) and d:
                prices["Gate"] = float(d[0]["last"])
    except Exception:
        pass
    return prices


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
    n = len(closes)
    if n < 2:
        return [50.0] * n
    rsis = [50.0] * n
    gains, losses = [], []
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

    short_cap = caption[:1020]
    overflow = caption if len(caption) > 1020 else ""
    if overflow:
        short_cap = caption[:1000] + "..."

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
    add_field("caption", short_cap)

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
            ok = resp.status == 200
        if ok and overflow:
            tg_send_text(chat_id, overflow)
        return ok
    except Exception as e:
        print(f"[TG PHOTO HATA] {e} -> Metin olarak deneniyor...")
        return tg_send_text(chat_id, caption)


# ==============================================================================
# 4. SAF PYTHON CANLI GRAFIK PNG MOTORU (5x7 BITMAP FONT + TUM KARTLAR)
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
    "#": [0x0A, 0x1F, 0x0A, 0x0A, 0x1F, 0x0A, 0x00],
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

    def circle(self, cx, cy, r, col):
        r2 = r * r
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dy * dy <= r2:
                    self.px(cx + dx, cy + dy, col)

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
        self.line(x0 + 1, y0, x1 + 1, y1, col)

    def dashed_h(self, x0, x1, y, col, dash=6, gap=4):
        x = x0
        while x <= x1:
            end_x = min(x1, x + dash - 1)
            self.line(x, y, end_x, y, col)
            x += dash + gap

    def dashed_v(self, x, y0, y1, col, dash=5, gap=4):
        y = y0
        while y <= y1:
            end_y = min(y1, y + dash - 1)
            self.line(x, y, x, end_y, col)
            y += dash + gap

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
    """Motor 1: Canli 1m mum grafigi + hacim barlari."""
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
        candles = [{"o": start_price, "h": max(start_price, cur_price), "l": min(start_price, cur_price), "c": cur_price, "v": 1.0}]

    hi = max(max(k["h"] for k in candles), start_price, cur_price)
    lo = min(min(k["l"] for k in candles), start_price, cur_price)
    span = max(hi - lo, cur_price * 0.005, 1e-12)
    hi += span * 0.12
    lo -= span * 0.10
    total_span = max(hi - lo, 1e-12)

    def y_of(price):
        return int(plot_t + 8 + ((hi - price) / total_span) * (plot_b - plot_t - 16))

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
        c.line(cx, y_of(k["h"]), cx, y_of(k["l"]), col)
        bx1, bx2 = cx - body_w // 2, cx + body_w // 2
        yo, yc = y_of(k["o"]), y_of(k["c"])
        top_b, bot_b = min(yo, yc), max(yo, yc)
        if bot_b == top_b:
            bot_b += 1
        c.rect(bx1, top_b, bx2, bot_b, col)

        vh = max(2, int((k["v"] / max_vol) * (vol_b - vol_t - 10)))
        vcol = (22, 163, 74) if bull else (185, 28, 28)
        c.rect(bx1, vol_b - 2 - vh, bx2, vol_b - 2, vcol)

    y_start = y_of(start_price)
    y_cur = y_of(cur_price)
    c.dashed_h(plot_l + 2, plot_r - 2, y_start, (251, 191, 36))
    c.dashed_h(plot_l + 2, plot_r - 2, y_cur, (52, 211, 153))

    lbl_cur_y = y_cur
    lbl_start_y = y_start if abs(y_start - y_cur) >= 20 else min(plot_b - 10, y_cur + 20)

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
    """Motor 2: W1 Binance (Fiyat + Hacim $M + RSI) grafigi."""
    W, H = 900, 740
    bg, panel_bg, grid_col, border_col = (22, 25, 30), (26, 29, 35), (44, 48, 56), (70, 76, 86)
    green_col, red_col = (8, 153, 129), (242, 54, 69)
    txt_col, muted_col = (209, 213, 219), (148, 154, 166)

    c = _Canvas(W, H, bg=bg)
    show = w1_candles[-40:] if len(w1_candles) > 40 else list(w1_candles)
    if not show:
        show = [{"t": time.time(), "o": cur_price, "h": cur_price, "l": cur_price, "c": cur_price, "v": 1.0, "qv": 1_000_000.0}]

    title = f"{symbol} W1 BINANCE"
    c.text(max(20, (W - len(title) * 12) // 2), 16, title, (235, 238, 244), scale=2)

    pl, pr = 92, W - 26
    p_top, p_bot = 46, 480
    v_top, v_bot = 508, 608
    r_top, r_bot = 626, 718

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
        c.text(max(32, pl - 8 - len(lbl) * 6), gy - 3, lbl, muted_col, scale=1)

    n = len(show)
    step = (pr - pl - 30) / max(n, 1)
    body_w = max(6, min(int(step * 0.72), 160))
    date_step = max(1, n // 5)

    for i, k in enumerate(show):
        cx = int(pl + 15 + (i + 0.5) * step)
        if i % date_step == 0 or i == n - 1:
            for top, bot in ((p_top, p_bot), (v_top, v_bot), (r_top, r_bot)):
                c.line(cx, top + 1, cx, bot - 1, grid_col)
            dt_str = datetime.fromtimestamp(k["t"], tz=TR_TZ).strftime("%d.%m.%Y")
            c.text(max(pl, min(pr - 60, cx - 28)), p_bot + 10, dt_str, txt_col, scale=1)

        bull = k["c"] >= k["o"]
        col = green_col if bull else red_col
        c.line(cx, y_price(k["h"]), cx, y_price(k["l"]), col)
        c.line(cx + 1, y_price(k["h"]), cx + 1, y_price(k["l"]), col)
        bx1, bx2 = cx - body_w // 2, cx + body_w // 2
        yo, yc = y_price(k["o"]), y_price(k["c"])
        top_b, bot_b = min(yo, yc), max(yo, yc)
        if bot_b - top_b < 2:
            bot_b = top_b + 2
        c.rect(bx1, top_b, bx2, bot_b, col)

    c.dashed_h(pl + 2, pr - 2, y_price(cur_price), (251, 146, 60))

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
        col = green_col if k["c"] >= k["o"] else red_col
        vh = max(3, int((vols_m[i] / (max_vm * 1.15)) * (v_bot - v_top - 10)))
        c.rect(cx - body_w // 2, v_bot - 3 - vh, cx + body_w // 2, v_bot - 3, col)

    rsis_show = calc_rsi_series([k["c"] for k in w1_candles], 14)[-len(show):]

    def y_rsi(val):
        return int(r_bot - 6 - (max(0.0, min(100.0, val)) / 100.0) * (r_bot - r_top - 12))

    for r_ref in (70, 50, 30):
        ry = y_rsi(r_ref)
        c.line(pl + 1, ry, pr - 1, ry, grid_col)
        c.text(max(32, pl - 8 - len(str(r_ref)) * 6), ry - 3, str(r_ref), muted_col, scale=1)

    if len(rsis_show) >= 2:
        pts = [(int(pl + 15 + (i + 0.5) * step), y_rsi(rv)) for i, rv in enumerate(rsis_show)]
        for i in range(1, len(pts)):
            c.thick_line(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1], green_col)
    else:
        ry = y_rsi(rsis_show[0] if rsis_show else 50.0)
        c.thick_line(pl + 15, ry, pr - 15, ry, green_col)

    return c.to_png()


def render_flow_structure_png(setup, candles_4h):
    """Motor 3 (IMG_3070 duzeni): 4h Mum + Hacim + Stop/POC/Tetik + H1/H2/H3 Fib hedefleri."""
    W, H = 1040, 580
    c = _Canvas(W, H, bg=(13, 15, 22))
    coin = setup["coin"]
    is_short = setup["direction"] == "SHORT"

    c.rect(0, 0, W - 1, 56, (17, 20, 29))
    c.text(18, 12, coin, (240, 244, 250), scale=2)
    badge_bg = (153, 27, 27) if is_short else (6, 95, 70)
    c.rect(96, 10, 268, 30, badge_bg)
    c.text(104, 16, "ERKEN KIRILIM", (254, 226, 226) if is_short else (209, 250, 229), scale=1)

    slope_tr = "EGIM ASAGI" if is_short else "EGIM YUKARI"
    c.text(18, 38, f"4H - {slope_tr} - YAPIYA GORE HEDEFLER - {setup['ex_count']} BORSA AKISI", (148, 163, 184), scale=1)
    c.text(W - 185, 12, "TURKCE AKIS RADAR", (196, 181, 253), scale=1)
    c.text(W - 185, 28, tr_now_str(), (100, 116, 139), scale=1)

    pl, pr = 68, 760
    pt, pb = 70, 445
    vt, vb = 456, 535

    c.rect(pl, pt, pr, pb, (15, 18, 27))
    c.rect_outline(pl, pt, pr, pb, (35, 41, 58))
    c.rect(pl, vt, pr, vb, (15, 18, 27))
    c.rect_outline(pl, vt, pr, vb, (35, 41, 58))

    levels_all = [
        setup["cur_price"], setup["stop_cons"], setup["stop_tight"],
        setup["trigger"], setup["tp1"], setup["tp2"], setup["tp3"],
    ]
    show = candles_4h[-42:] if len(candles_4h) > 42 else list(candles_4h)
    hi = max(max(k["h"] for k in show), max(levels_all))
    lo = min(min(k["l"] for k in show), min(levels_all))
    span = max(hi - lo, setup["cur_price"] * 0.01, 1e-12)
    hi += span * 0.08
    lo = max(0.0, lo - span * 0.08)
    total_span = max(hi - lo, 1e-12)

    def y_of(p):
        return int(pt + 8 + ((hi - p) / total_span) * (pb - pt - 16))

    for i in range(6):
        gy = int(pt + 8 + i * (pb - pt - 16) / 5.0)
        gp = hi - (i / 5.0) * total_span
        c.line(pl + 1, gy, pr - 1, gy, (24, 29, 43))
        lbl = fmt_price(gp)
        c.text(max(4, pl - 6 - len(lbl) * 6), gy - 3, lbl, (120, 132, 152), scale=1)

    n = len(show)
    step = (pr - pl - 20) / max(n, 1)
    body_w = max(4, min(14, int(step * 0.65)))
    max_v = max((k["qv"] for k in show), default=1.0) or 1.0

    for i, k in enumerate(show):
        cx = int(pl + 10 + (i + 0.5) * step)
        bull = k["c"] >= k["o"]
        col = (16, 185, 129) if bull else (239, 68, 68)
        c.line(cx, y_of(k["h"]), cx, y_of(k["l"]), col)
        yo, yc = y_of(k["o"]), y_of(k["c"])
        tb, bb = min(yo, yc), max(yo, yc)
        if bb == tb:
            bb += 1
        c.rect(cx - body_w // 2, tb, cx + body_w // 2, bb, col)

        vh = max(2, int((k["qv"] / max_v) * (vb - vt - 8)))
        vcol = (16, 120, 88) if bull else (153, 45, 52)
        c.rect(cx - body_w // 2, vb - 2 - vh, cx + body_w // 2, vb - 2, vcol)

    lvl_specs = [
        (setup["stop_cons"], f"STOP GENIS {fmt_price(setup['stop_cons'])} ({setup['stop_cons_pct']:+.1f}%)", (74, 222, 128), (15, 65, 45)),
        (setup["stop_tight"], f"DAR STOP {fmt_price(setup['stop_tight'])} ({setup['stop_tight_pct']:+.1f}%) POC", (250, 204, 21), (85, 65, 12)),
        (setup["cur_price"], f"SU AN {fmt_price(setup['cur_price'])}", (226, 232, 240), (45, 55, 72)),
        (setup["trigger"], f"TETIK {fmt_price(setup['trigger'])} ({setup['trig_pct']:+.1f}%)", (148, 163, 184), (35, 42, 58)),
        (setup["tp1"], f"H1 {fmt_price(setup['tp1'])} ({setup['tp1_pct']:+.1f}%) FIB 0.5", (254, 226, 226), (185, 28, 28) if is_short else (16, 120, 72)),
        (setup["tp2"], f"H2 {fmt_price(setup['tp2'])} ({setup['tp2_pct']:+.1f}%) FIB 0.618", (254, 226, 226), (185, 28, 28) if is_short else (16, 120, 72)),
        (setup["tp3"], f"H3 {fmt_price(setup['tp3'])} ({setup['tp3_pct']:+.1f}%) {'VAL' if is_short else 'VAH'}", (254, 226, 226), (153, 27, 27) if is_short else (6, 95, 70)),
    ]

    positioned = []
    for price, text, fg, bg_pill in lvl_specs:
        yy = y_of(price)
        c.dashed_h(pl + 2, pr - 2, yy, bg_pill if bg_pill[0] > 100 or bg_pill[1] > 100 else fg)
        positioned.append([yy, yy, text, fg, bg_pill])

    positioned.sort(key=lambda x: x[0])
    for idx in range(1, len(positioned)):
        if positioned[idx][0] - positioned[idx - 1][0] < 19:
            positioned[idx][0] = positioned[idx - 1][0] + 19

    for y_lbl, y_line, text, fg, bg_pill in positioned:
        y_lbl = max(pt + 9, min(pb - 9, y_lbl))
        c.line(pr, y_line, pr + 8, y_lbl, fg)
        c.rect(pr + 8, y_lbl - 8, W - 8, y_lbl + 8, bg_pill)
        c.text(pr + 12, y_lbl - 4, text, fg, scale=1)

    c.text(
        pl,
        548,
        f"DUVAR: {fmt_usd_k(setup['wall_usd'])} @ {fmt_price(setup['wall_price'])} | POC: {fmt_price(setup['poc'])} | VAL/VAH: {fmt_price(setup['val'])}-{fmt_price(setup['vah'])}",
        (186, 200, 220),
        scale=1,
    )
    return c.to_png()


def render_win_of_day_png(sig, candles_after):
    """Motor 4 (IMG_2835 duzeni): Gunun Zaferi koyu mor karti."""
    W, H = 820, 560
    c = _Canvas(W, H, bg=(19, 17, 36))
    c.rect(18, 18, W - 18, H - 18, (25, 22, 48))
    c.rect_outline(18, 18, W - 18, H - 18, (55, 48, 92))

    c.text(44, 40, "SPOT & AKIS TARAYICI", (236, 240, 250), scale=2)
    c.text(44, 62, "CANLI SINYAL PERFORMANS TAKIBI", (148, 144, 184), scale=1)

    c.rect(W - 220, 38, W - 40, 68, (39, 32, 74))
    c.rect_outline(W - 220, 38, W - 40, 68, (124, 58, 237))
    c.text(W - 200, 48, "GUNUN ZAFERI", (196, 181, 253), scale=1)

    c.circle(64, 122, 22, (49, 44, 88))
    c.text(58, 115, sig["coin"][:1], (224, 231, 255), scale=2)
    c.text(102, 104, sig["coin"], (248, 250, 252), scale=3)

    dir_col = (22, 163, 74) if sig["direction"] == "LONG" else (220, 38, 38)
    c.rect(102, 134, 162, 154, (20, 55, 45) if sig["direction"] == "LONG" else (70, 24, 34))
    c.rect_outline(102, 134, 162, 154, dir_col)
    c.text(114, 140, sig["direction"], (134, 239, 172) if sig["direction"] == "LONG" else (254, 202, 202), scale=1)

    pct_txt = f"+{sig['peak_pct']:.0f}%"
    c.text(W - 44 - len(pct_txt) * 18, 102, pct_txt, (74, 222, 128), scale=3)
    hrs = max(0.1, (sig["peak_ts"] - sig["ts"]) / 3600.0)
    sub_h = f"SINYALDEN {hrs:.1f}S SONRA"
    c.text(W - 44 - len(sub_h) * 6, 138, sub_h, (148, 163, 184), scale=1)

    gl, gr = 44, W - 44
    gt, gb = 176, 420
    c.rect(gl, gt, gr, gb, (21, 19, 42))
    c.rect_outline(gl, gt, gr, gb, (45, 40, 78))

    pts_prices = [k["c"] for k in candles_after] if candles_after else [sig["entry"], sig["peak_price"]]
    if len(pts_prices) < 2:
        pts_prices = [sig["entry"], sig["peak_price"]]

    hi = max(max(pts_prices), sig["peak_price"], sig["entry"])
    lo = min(min(pts_prices), sig["entry"])
    span = max(hi - lo, sig["entry"] * 0.01, 1e-12)
    hi += span * 0.14
    lo -= span * 0.14
    tot = max(hi - lo, 1e-12)

    def y_p(v):
        return int(gt + 12 + ((hi - v) / tot) * (gb - gt - 24))

    def x_i(idx, count):
        return int(gl + 20 + (idx / max(count - 1, 1)) * (gr - gl - 40))

    n = len(pts_prices)
    coords = [(x_i(i, n), y_p(v)) for i, v in enumerate(pts_prices)]
    for i in range(1, len(coords)):
        c.thick_line(coords[i - 1][0], coords[i - 1][1], coords[i][0], coords[i][1], (129, 140, 248))

    ex, ey = coords[0][0], y_p(sig["entry"])
    c.dashed_v(ex, gt + 4, gb - 4, (148, 163, 184))
    c.circle(ex, ey, 5, (255, 255, 255))
    t_entry = datetime.fromtimestamp(sig["ts"], tz=TR_TZ).strftime("%H:%M")
    c.rect(ex + 8, min(gb - 22, ey - 8), ex + 190, min(gb - 4, ey + 10), (35, 32, 66))
    c.text(ex + 12, min(gb - 17, ey - 3), f"SINYAL {t_entry} TR ${fmt_price_short(sig['entry'])}", (224, 231, 255), scale=1)

    peak_idx = pts_prices.index(max(pts_prices)) if sig["direction"] == "LONG" else pts_prices.index(min(pts_prices))
    px_pt, py_pt = coords[peak_idx][0], y_p(sig["peak_price"])
    c.circle(px_pt, py_pt, 6, (74, 222, 128))
    lbl_x = max(gl + 10, px_pt - 155)
    c.rect(lbl_x, max(gt + 6, py_pt - 10), lbl_x + 145, max(gt + 24, py_pt + 8), (22, 101, 52))
    c.text(lbl_x + 6, max(gt + 11, py_pt - 5), f"ZIRVE ${fmt_price_short(sig['peak_price'])}", (236, 253, 245), scale=1)

    t_peak = datetime.fromtimestamp(sig["peak_ts"], tz=TR_TZ).strftime("%H:%M")
    summary_line = f"{t_entry} - SINYAL ${fmt_price_short(sig['entry'])}  ->  {t_peak} - ZIRVE ${fmt_price_short(sig['peak_price'])}"
    c.text(max(44, (W - len(summary_line) * 6) // 2), 446, summary_line, (226, 232, 240), scale=1)
    c.text(44, 488, "OTOMATIK CANLI TAKIP | HAREKET KAR GARANTISI DEGILDIR", (148, 144, 184), scale=1)
    return c.to_png()


def render_top24_png(top_list, total_tracked, win_2pct_rate):
    """Motor 5 (IMG_2834 duzeni): Son 24 Saatin En Iyi Hareketleri lider karti."""
    W, H = 820, 620
    c = _Canvas(W, H, bg=(19, 17, 36))
    c.rect(18, 18, W - 18, H - 18, (25, 22, 48))
    c.rect_outline(18, 18, W - 18, H - 18, (55, 48, 92))

    c.text(44, 40, "SPOT & AKIS TARAYICI", (236, 240, 250), scale=2)
    c.text(44, 62, "24 SAATLIK LIDERLIK TABLOSU", (148, 144, 184), scale=1)

    c.rect(W - 210, 38, W - 40, 68, (39, 32, 74))
    c.rect_outline(W - 210, 38, W - 40, 68, (124, 58, 237))
    c.text(W - 190, 48, "TOP 24 SAAT", (196, 181, 253), scale=1)

    c.text(44, 96, "EN IYI 24 SAAT HAREKETLERI", (248, 250, 252), scale=2)
    c.text(44, 122, "SINYAL YONUNDEKI MAKSIMUM HAREKETLER", (148, 144, 184), scale=1)

    y = 156
    for idx, s in enumerate(top_list[:5], start=1):
        row_h = 70
        bg_row = (36, 31, 68) if idx == 1 else (29, 26, 56)
        b_col = (124, 58, 237) if idx == 1 else (52, 45, 88)
        c.rect(44, y, W - 44, y + row_h, bg_row)
        c.rect_outline(44, y, W - 44, y + row_h, b_col)

        c.text(64, y + 26, str(idx), (196, 181, 253) if idx == 1 else (148, 163, 184), scale=2)
        c.circle(115, y + 35, 18, (67, 56, 122))
        c.text(109, y + 28, s["coin"][:1], (238, 242, 255), scale=2)

        c.text(152, y + 18, s["coin"], (248, 250, 252), scale=2)
        hrs = max(0.1, (s["peak_ts"] - s["ts"]) / 3600.0)
        c.text(152, y + 42, f"ZIRVE {hrs:.1f}S ICINDE ({s['direction']})", (148, 163, 184), scale=1)

        pct_s = f"+{s['peak_pct']:.0f}%"
        c.text(W - 64 - len(pct_s) * 12, y + 24, pct_s, (74, 222, 128), scale=2)
        y += row_h + 12

    c.text(
        44,
        H - 46,
        f"{total_tracked} SINYAL TAKIPTE | %{win_2pct_rate:.0f} EN AZ +2% ULASTI",
        (148, 144, 184),
        scale=1,
    )
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


def format_flow_message(setup):
    sym = setup["symbol"]
    coin = setup["coin"]
    is_short = setup["direction"] == "SHORT"
    dir_lbl = "🔴 ERKEN KIRILIM (SHORT)" if is_short else "🟢 ERKEN KIRILIM (LONG)"
    wall_type = "Satış (Ask)" if is_short else "Alış (Bid)"
    pos_txt = "altında" if is_short else "üstünde"
    stop_dir = "üzeri" if is_short else "altı"
    trend_txt = "aşağı" if is_short else "yukarı"
    trig_dir = "altında" if is_short else "üstünde"

    return (
        f"<b>#{sym}</b>  |  {dir_lbl}\n\n"
        f"Fiyat: <code>{fmt_price(setup['cur_price'])}</code>\n"
        f"Hedefler: <code>{fmt_price(setup['tp1'])}</code> ({setup['tp1_pct']:+.1f}%) → "
        f"<code>{fmt_price(setup['tp2'])}</code> ({setup['tp2_pct']:+.1f}%) → "
        f"<code>{fmt_price(setup['tp3'])}</code> ({setup['tp3_pct']:+.1f}%)\n"
        f"İptal (Stop): <code>{fmt_price(setup['stop_tight'])}</code> {stop_dir} ({setup['stop_tight_pct']:+.1f}%)\n\n"
        f"{coin} iki likidite duvarı arasında sıkışmış durumda, ancak bunlardan biri fiyatı tutuyor. "
        f"<code>{fmt_price(setup['wall_price'])}</code> seviyesindeki {wall_type} duvarı — <b>{fmt_usd_k(setup['wall_usd'])}</b> "
        f"büyüklüğünde tek noktada yoğunlaşmış emir bloğu. Bu sadece bir duvar değil — son 24 saatin en yüksek hacminin "
        f"toplandığı günlük Değer Alanı <b>POC</b> ({fmt_price(setup['poc'])}) bölgesi.\n\n"
        f"Fiyat bu seviyenin {pos_txt}, spot defter dengesi zayıf (Bid/Ask oranı: <b>{setup['imbalance']:.2f}x</b>). "
        f"Çoklu borsa akışı ({setup['ex_names']}) eşzamanlı. Günlük yapı {trend_txt} yönlü — trend içi kırılım fırsatı. "
        f"Kilit eşik: <code>{fmt_price(setup['trigger'])}</code> (Fib 0.382) {trig_dir} kalıcılık."
    )


def format_win_of_day_message(sig):
    t1 = datetime.fromtimestamp(sig["ts"], tz=TR_TZ).strftime("%H:%M TR")
    t2 = datetime.fromtimestamp(sig["peak_ts"], tz=TR_TZ).strftime("%H:%M TR")
    hrs = max(0.1, (sig["peak_ts"] - sig["ts"]) / 3600.0)
    return (
        f"🏆 <b>Günün Zaferi: {sig['coin']} +{sig['peak_pct']:.0f}%</b>\n\n"
        f"{t1} — dedektör sinyal verdi: <b>{sig['symbol']} {sig['direction']}</b> <code>${fmt_price_short(sig['entry'])}</code>\n"
        f"{t2} — zirve <code>${fmt_price_short(sig['peak_price'])}</code>. Girişten itibaren <b>{hrs:.1f} saatte +{sig['peak_pct']:.0f}%</b>.\n\n"
        f"⚠️ Hareket potansiyeli ifade eder, kesin kâr garantisi değildir.\n"
        f"#{sig['coin']} #GununZaferi #kripto"
    )


def format_top24_message(top_list):
    medals = ["🥇", "🥈", "🥉", "🏅", "⭐"]
    lines = [
        "🔥 <b>Son 24 Saatin En İyi Hareketleri</b>\n",
        "sinyal yönündeki maksimum hareket:\n",
    ]
    for i, s in enumerate(top_list[:5]):
        hrs = max(0.1, (s["peak_ts"] - s["ts"]) / 3600.0)
        m = medals[i] if i < len(medals) else "•"
        lines.append(f"{m} <b>{s['coin']} +{s['peak_pct']:.0f}%</b> · zirve {hrs:.1f}s içinde ({s['direction']})")
    lines.append("\n⚠️ Hareket potansiyeli ifade eder, kesin kâr garantisi değildir.")
    return "\n".join(lines)


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
    print("[MOTOR 1 - SPOT PUMP] Baslatildi.")
    while True:
        try:
            now_ts = time.time()
            tickers, mirror_used = fetch_spot_tickers()
            alerts = process_spot_snapshot(tickers, now_ts)

            with _STATE_LOCK:
                _STATS["pump_scans"] += 1

            for al in alerts:
                sym = al["symbol"]
                candles = fetch_spot_klines(sym, interval="1m", limit=60, preferred_base=mirror_used)
                png_bytes = render_pump_chart_png(
                    sym, candles, al["start_price"], al["cur_price"], al["pct"],
                    al["elapsed_sec"], al["vol_24h"], al["quote"],
                )
                msg = format_pump_message(
                    sym, al["pct"], al["start_price"], al["cur_price"],
                    al["vol_24h"], al["quote"], al["elapsed_sec"],
                )
                tg_send_photo(CHAT_ID, png_bytes, msg, filename=f"{sym}_pump.png")
                register_tracked_signal(sym, "LONG", al["start_price"], source="PUMP")
                with _STATE_LOCK:
                    _STATS["pump_signals"] += 1
                    _STATS["last_pump_symbol"] = sym
                time.sleep(0.5)

        except Exception as e:
            print(f"[SPOT PUMP HATA] {e}")

        time.sleep(SCAN_INTERVAL_SEC)


# ==============================================================================
# 7. MOTOR 2: TUM ZAMANLARIN EN DUSUGU (ATL - W1)
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
    print("[MOTOR 2 - ATL W1] Baslatildi.")
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
# 8. MOTOR 3: AKIS & EMIR DUVARI + POC YAPISI (4H ERKEN KIRILIM)
# ==============================================================================
def calc_volume_profile_poc_va(candles_15m, bins=32):
    hi = max(k["h"] for k in candles_15m)
    lo = min(k["l"] for k in candles_15m)
    if hi <= lo:
        return hi, lo, hi
    step = (hi - lo) / bins
    buckets = [0.0] * bins
    for k in candles_15m:
        mid = (k["h"] + k["l"] + k["c"]) / 3.0
        idx = min(bins - 1, max(0, int((mid - lo) / step)))
        buckets[idx] += k.get("qv", k["v"] * k["c"])

    poc_idx = max(range(bins), key=lambda i: buckets[i])
    poc = lo + (poc_idx + 0.5) * step

    tot_vol = sum(buckets) or 1.0
    acc = buckets[poc_idx]
    l_i, r_i = poc_idx, poc_idx
    while acc < tot_vol * 0.70 and (l_i > 0 or r_i < bins - 1):
        v_l = buckets[l_i - 1] if l_i > 0 else -1.0
        v_r = buckets[r_i + 1] if r_i < bins - 1 else -1.0
        if v_l >= v_r and l_i > 0:
            l_i -= 1
            acc += buckets[l_i]
        elif r_i < bins - 1:
            r_i += 1
            acc += buckets[r_i]
        else:
            break

    val = lo + l_i * step
    vah = lo + (r_i + 1) * step
    return poc, val, vah


def analyze_flow_wall_setup(symbol, cur_price, depth, candles_15m, candles_4h, candles_1d, extra_prices=None):
    if not depth or not candles_15m or len(candles_4h) < 20 or len(candles_1d) < 7:
        return None

    bids = [(float(p), float(q)) for p, q in depth.get("bids", [])]
    asks = [(float(p), float(q)) for p, q in depth.get("asks", [])]
    if not bids or not asks:
        return None

    bid_walls = [(p, p * q) for p, q in bids if 0.0005 <= (cur_price - p) / cur_price <= 0.025]
    ask_walls = [(p, p * q) for p, q in asks if 0.0005 <= (p - cur_price) / cur_price <= 0.025]

    best_bid = max(bid_walls, key=lambda x: x[1]) if bid_walls else (0.0, 0.0)
    best_ask = max(ask_walls, key=lambda x: x[1]) if ask_walls else (0.0, 0.0)

    total_bid_usd = sum(p * q for p, q in bids[:40]) or 1.0
    total_ask_usd = sum(p * q for p, q in asks[:40]) or 1.0
    imbalance = total_bid_usd / total_ask_usd

    poc, val, vah = calc_volume_profile_poc_va(candles_15m[-96:])
    if poc <= 0 or vah <= val:
        return None

    c1d_now = candles_1d[-1]["c"]
    c1d_prev = candles_1d[-7]["c"]
    trend_1d = "DOWN" if c1d_now < c1d_prev * 0.995 else ("UP" if c1d_now > c1d_prev * 1.005 else "FLAT")

    swing_hi = max(k["h"] for k in candles_4h[-30:])
    swing_lo = min(k["l"] for k in candles_4h[-30:])
    swing_rng = swing_hi - swing_lo
    if swing_rng <= cur_price * 0.03:
        return None

    ex_list = ["Binance"]
    if extra_prices:
        for name, ep in extra_prices.items():
            if ep > 0 and abs(ep - cur_price) / cur_price <= 0.006:
                ex_list.append(name)

    direction = None
    wall_price, wall_usd = 0.0, 0.0

    if (
        best_ask[1] >= FLOW_MIN_WALL_USD
        and best_ask[1] > best_bid[1] * 1.25
        and cur_price <= poc * 1.004
        and abs(best_ask[0] - poc) / cur_price <= 0.025
        and trend_1d == "DOWN"
    ):
        direction = "SHORT"
        wall_price, wall_usd = best_ask
        stop_tight = max(wall_price * 1.004, poc * 1.005)
        stop_cons = max(swing_hi, stop_tight * 1.02)
        trigger = cur_price - swing_rng * 0.10
        tp1 = cur_price - swing_rng * 0.30
        tp2 = cur_price - swing_rng * 0.50
        tp3 = min(val, cur_price - swing_rng * 0.72)

    elif (
        best_bid[1] >= FLOW_MIN_WALL_USD
        and best_bid[1] > best_ask[1] * 1.25
        and cur_price >= poc * 0.996
        and abs(best_bid[0] - poc) / cur_price <= 0.025
        and trend_1d == "UP"
    ):
        direction = "LONG"
        wall_price, wall_usd = best_bid
        stop_tight = min(wall_price * 0.996, poc * 0.995)
        stop_cons = min(swing_lo, stop_tight * 0.98)
        trigger = cur_price + swing_rng * 0.10
        tp1 = cur_price + swing_rng * 0.30
        tp2 = cur_price + swing_rng * 0.50
        tp3 = max(vah, cur_price + swing_rng * 0.72)

    if not direction:
        return None

    risk = abs(stop_tight - cur_price)
    reward2 = abs(tp2 - cur_price)
    if risk <= 0 or reward2 / risk < 1.5 or abs(tp1 - cur_price) / cur_price < 0.015:
        return None

    return {
        "symbol": symbol,
        "coin": symbol[:-4],
        "direction": direction,
        "cur_price": cur_price,
        "wall_price": wall_price,
        "wall_usd": wall_usd,
        "imbalance": imbalance,
        "poc": poc,
        "val": val,
        "vah": vah,
        "stop_tight": stop_tight,
        "stop_cons": stop_cons,
        "trigger": trigger,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "stop_tight_pct": ((stop_tight - cur_price) / cur_price) * 100.0,
        "stop_cons_pct": ((stop_cons - cur_price) / cur_price) * 100.0,
        "trig_pct": ((trigger - cur_price) / cur_price) * 100.0,
        "tp1_pct": ((tp1 - cur_price) / cur_price) * 100.0,
        "tp2_pct": ((tp2 - cur_price) / cur_price) * 100.0,
        "tp3_pct": ((tp3 - cur_price) / cur_price) * 100.0,
        "ex_count": len(ex_list),
        "ex_names": " + ".join(ex_list),
    }


def flow_structure_worker():
    print("[MOTOR 3 - AKIS & DUVAR YAPISI] Baslatildi.")
    time.sleep(15)
    while True:
        try:
            tickers, mirror_used = fetch_spot_tickers()
            usdt_list = []
            for t in tickers:
                sym = t.get("symbol", "")
                if not sym.endswith("USDT") or not is_valid_symbol(sym):
                    continue
                try:
                    vol = float(t.get("quoteVolume", 0))
                    cp = float(t.get("lastPrice", 0))
                except Exception:
                    continue
                if cp > 0 and vol >= FLOW_MIN_VOL_24H_USDT:
                    usdt_list.append((sym, cp, vol))

            usdt_list.sort(key=lambda x: x[2], reverse=True)
            for sym, cp, _ in usdt_list[:45]:
                now_ts = time.time()
                with _STATE_LOCK:
                    if now_ts - _FLOW_COOLDOWN.get(sym, 0.0) < FLOW_COOLDOWN_SEC:
                        continue

                depth = fetch_spot_depth(sym, limit=100, preferred_base=mirror_used)
                if not depth:
                    continue
                c15 = fetch_spot_klines(sym, interval="15m", limit=96, preferred_base=mirror_used)
                c4h = fetch_spot_klines(sym, interval="4h", limit=50, preferred_base=mirror_used)
                c1d = fetch_spot_klines(sym, interval="1d", limit=14, preferred_base=mirror_used)
                ex_p = fetch_multi_exchange_prices(sym)

                setup = analyze_flow_wall_setup(sym, cp, depth, c15, c4h, c1d, extra_prices=ex_p)
                if setup:
                    with _STATE_LOCK:
                        _FLOW_COOLDOWN[sym] = time.time()
                    png_bytes = render_flow_structure_png(setup, c4h)
                    msg = format_flow_message(setup)
                    tg_send_photo(CHAT_ID, png_bytes, msg, filename=f"{sym}_flow4h.png")
                    register_tracked_signal(sym, setup["direction"], cp, source="FLOW4H")
                    with _STATE_LOCK:
                        _STATS["flow_signals"] += 1
                        _STATS["last_flow_symbol"] = sym
                    time.sleep(0.8)

                time.sleep(0.25)

            with _STATE_LOCK:
                _STATS["flow_scans"] += 1

        except Exception as e:
            print(f"[FLOW YAPI HATA] {e}")

        time.sleep(FLOW_SCAN_INTERVAL_SEC)


# ==============================================================================
# 9. MOTOR 4 & 5: ZIRVE TAKIP, GUNUN ZAFERI & TOP 24 HAREKETLERI
# ==============================================================================
def update_tracked_peaks(price_map, now_ts):
    updated = False
    with _STATE_LOCK:
        for s in _TRACKED_SIGNALS:
            if now_ts - s["ts"] > 86400:
                continue
            cur = price_map.get(s["symbol"], 0.0)
            if cur <= 0:
                continue
            entry = s["entry"]
            if s["direction"] == "LONG":
                pct = ((cur - entry) / entry) * 100.0
                if cur > s["peak_price"] and pct > s["peak_pct"]:
                    s["peak_price"] = cur
                    s["peak_pct"] = pct
                    s["peak_ts"] = now_ts
                    updated = True
            else:
                pct = ((entry - cur) / entry) * 100.0
                if cur < s["peak_price"] and pct > s["peak_pct"]:
                    s["peak_price"] = cur
                    s["peak_pct"] = pct
                    s["peak_ts"] = now_ts
                    updated = True
        if updated:
            save_tracked_signals()


def get_top24_signals(now_ts):
    with _STATE_LOCK:
        recent = [dict(s) for s in _TRACKED_SIGNALS if now_ts - s["ts"] <= 86400]
    if not recent:
        return [], 0, 0.0
    best_by_coin = {}
    for s in recent:
        c = s["coin"]
        if c not in best_by_coin or s["peak_pct"] > best_by_coin[c]["peak_pct"]:
            best_by_coin[c] = s
    ranked = sorted(best_by_coin.values(), key=lambda x: x["peak_pct"], reverse=True)
    ranked_pos = [x for x in ranked if x["peak_pct"] >= 2.0]
    total_cnt = len(recent)
    pos_cnt = sum(1 for x in recent if x["peak_pct"] >= 2.0)
    win_rate = (pos_cnt / total_cnt) * 100.0 if total_cnt else 0.0
    return ranked_pos, total_cnt, win_rate


def send_win_of_day_report(target_chat=None):
    cid = target_chat or CHAT_ID
    now_ts = time.time()
    ranked, _, _ = get_top24_signals(now_ts)
    if not ranked:
        return False
    best = ranked[0]
    c15 = fetch_spot_klines(best["symbol"], interval="15m", limit=48)
    candles_after = [k for k in c15 if k["t"] >= best["ts"] - 900]
    png_bytes = render_win_of_day_png(best, candles_after)
    msg = format_win_of_day_message(best)
    return tg_send_photo(cid, png_bytes, msg, filename=f"{best['coin']}_zafer.png")


def send_top24_report(target_chat=None):
    cid = target_chat or CHAT_ID
    now_ts = time.time()
    ranked, total_cnt, win_rate = get_top24_signals(now_ts)
    if not ranked:
        return False
    png_bytes = render_top24_png(ranked[:5], total_cnt, win_rate)
    msg = format_top24_message(ranked[:5])
    return tg_send_photo(cid, png_bytes, msg, filename="top24_hareketler.png")


def tracker_and_daily_reports_worker():
    print("[MOTOR 4 & 5 - ZAFER & TOP24] Baslatildi.")
    time.sleep(12)
    while True:
        try:
            now_ts = time.time()
            tickers, _ = fetch_spot_tickers()
            pmap = {}
            for t in tickers:
                try:
                    pmap[t["symbol"]] = float(t["lastPrice"])
                except Exception:
                    pass
            update_tracked_peaks(pmap, now_ts)

            now_tr = tr_now()
            day_key = now_tr.strftime("%Y-%m-%d")
            if now_tr.hour == DAILY_WIN_HOUR_TR and _LAST_REPORT_DAY["win"] != day_key:
                _LAST_REPORT_DAY["win"] = day_key
                send_win_of_day_report(CHAT_ID)

            if now_tr.hour == DAILY_TOP24_HOUR_TR and _LAST_REPORT_DAY["top24"] != day_key:
                _LAST_REPORT_DAY["top24"] = day_key
                send_top24_report(CHAT_ID)

        except Exception as e:
            print(f"[TRACKER HATA] {e}")

        time.sleep(TRACKER_INTERVAL_SEC)


# ==============================================================================
# 10. BASIT TELEGRAM KOMUT DINLEYICI (/durum, /zafer, /top24)
# ==============================================================================
def telegram_command_worker():
    if not BOT_TOKEN:
        return
    offset = 0
    print("[TG POLL] Komut dinleyici aktif (/durum, /zafer, /top24).")
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
                        p_s, p_g = _STATS["pump_scans"], _STATS["pump_signals"]
                        a_s, a_g = _STATS["atl_scans"], _STATS["atl_signals"]
                        f_s, f_g = _STATS["flow_scans"], _STATS["flow_signals"]
                        tracked_pairs = len(_SPOT_WINDOW)
                        tracked_sigs = len(_TRACKED_SIGNALS)
                        uptime_m = int((time.time() - _STATS["started_at"]) / 60)
                    reply = (
                        f"🤖 <b>SPOT ANALIZ BOTU AKTIF (5 MOTOR)</b>\n\n"
                        f"• Calisma Suresi: <b>{uptime_m} dk</b> | Takip Havuzu: <b>{tracked_sigs} sinyal</b>\n"
                        f"• ⚡ 0-7 Dk Spot Pump ({tracked_pairs} parite): <b>{p_g}</b> sinyal ({p_s} tarama)\n"
                        f"• 👎 W1 Tum Zamanlar Dip (ATL): <b>{a_g}</b> sinyal ({a_s} tarama)\n"
                        f"• 🧱 Akis & Duvar Yapisi (4h): <b>{f_g}</b> sinyal ({f_s} tarama)\n"
                        f"• 🏆 Komutlar: <code>/zafer</code> (Gunun Zaferi) | <code>/top24</code> (En Iyi 24s)"
                    )
                    tg_send_text(cid, reply)
                elif txt in ("/zafer", "zafer"):
                    if not send_win_of_day_report(cid):
                        tg_send_text(cid, "⏳ Son 24 saatte henuz >= %2 kazanc goren kayitli sinyal olusmadi.")
                elif txt in ("/top24", "top24"):
                    if not send_top24_report(cid):
                        tg_send_text(cid, "⏳ Son 24 saatte henuz liderlik tablosu icin yeterli sinyal olusmadi.")
        except Exception:
            time.sleep(5)


# ==============================================================================
# 11. RENDER HTTP HEALTH SUNUCUSU
# ==============================================================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        with _STATE_LOCK:
            payload = json.dumps({
                "status": "ok",
                "bot": "spot-5-motor-turkish-bot",
                "pump_signals": _STATS["pump_signals"],
                "atl_signals": _STATS["atl_signals"],
                "flow_signals": _STATS["flow_signals"],
                "tracked_signals": len(_TRACKED_SIGNALS),
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
    load_tracked_signals()
    print(f"[BASLATILIYOR] 5 Motorlu Spot Botu | Port={PORT} | Saat={tr_now_str()}")

    if BOT_TOKEN:
        tg_send_text(
            CHAT_ID,
            "🤖 <b>SPOT ANALIZ BOTU BASLATILDI (5 MOTOR)</b>\n"
            "1️⃣ ⚡ <b>0-7 Dk Spot Pump</b> (Canli 1m mum grafikli)\n"
            "2️⃣ 👎 <b>W1 Tum Zamanlarin En Dusugu (ATL)</b>\n"
            "3️⃣ 🧱 <b>Akis & Emir Duvari + POC (4h Erken Kirilim)</b>\n"
            "4️⃣ 🏆 <b>Gunun Zaferi</b> (21:00 TR & <code>/zafer</code>)\n"
            "5️⃣ 🔥 <b>24 Saatin En Iyi Hareketleri</b> (23:00 TR & <code>/top24</code>)\n\n"
            "Durum icin <code>/durum</code> yazabilirsiniz.",
        )

    for target in (
        spot_pump_worker,
        atl_scanner_worker,
        flow_structure_worker,
        tracker_and_daily_reports_worker,
        telegram_command_worker,
    ):
        threading.Thread(target=target, daemon=True).start()

    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"[HTTP] 0.0.0.0:{PORT} dinleniyor...")
    server.serve_forever()


if __name__ == "__main__":
    main()

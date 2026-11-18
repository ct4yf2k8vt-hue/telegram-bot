import urllib.request
import urllib.parse
import json
import time
import socket
import os
import threading
import http.server
import math
import zlib
import struct
from datetime import datetime, timedelta, timezone

# =====================================================
# AYARLAR
# =====================================================

socket.setdefaulttimeout(15)

T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()

TR_Saati = timezone(timedelta(hours=3))
BASE = "https://api.binance.com"
DATA_API = "https://data-api.binance.vision"
FAPI = "https://fapi.binance.com"
BYBIT = "https://api.bybit.com"
OKX = "https://www.okx.com"
BITGET = "https://api.bitget.com"
GATE = "https://api.gateio.ws"
MEXC = "https://api.mexc.com"
KUCOIN = "https://api.kucoin.com"
COINGECKO = "https://api.coingecko.com/api/v3"

# MOTOR CALISMA SURELERI
CHARTIST_ARALIGI = 3600         # 1 Saat
PUMP_ARALIGI = 60               # 1 Dakika
VOLATILITE_ARALIGI = 60         # 1 Dakika
FORMASYON_ARALIGI = 3600        # 1 Saat
EMA_RIBBON_ARALIGI = 3600       # 1 Saat
MACD_ARALIGI = 3600             # 1 Saat
SMA200_ARALIGI = 3600           # 1 Saat
ATH_ATL_ARALIGI = 3600          # 1 Saat
ICHIMOKU_ARALIGI = 3600         # 1 Saat
CHART_PATTERN_ARALIGI = 1800    # 30 Dakika
ANLIK_ATH_ATL_ARALIGI = 180     # 3 Dakika
TOP_MOVERS_ARALIGI = 3600       # 1 Saat
ORDERFLOW_FIBO_ARALIGI = 900    # 15 Dakika
CVD_FLOW_ARALIGI = 1800         # 30 Dakika (Motor 19)

# RAPOR VE TAKIP MOTORLARI SURELERI
TOP_GAINERS_ARALIGI = 14400     # 4 Saat
PUMP_WINNER_ARALIGI = 21600     # 6 Saat
DOMINANS_ARALIGI = 14400        # 4 Saat
SINYAL_ZAFERI_ARALIGI = 21600   # 6 Saat
SINYAL_TOP24_ARALIGI = 14400    # 4 Saat

# FILTRELER
MIN_HACIM_CHARTIST = 10_000_000
MIN_HACIM_PUMP = 5_000_000
MIN_HACIM_VOLATILITE = 10_000_000
MIN_HACIM_FORMASYON = 15_000_000
MIN_HACIM_EMA_RIBBON = 10_000_000
MIN_HACIM_MACD = 10_000_000
MIN_HACIM_SMA200 = 5_000_000
MIN_HACIM_ATH_ATL = 5_000_000
MIN_HACIM_ICHIMOKU = 10_000_000
MIN_HACIM_PATTERN = 15_000_000
MIN_HACIM_ANLIK_ATH = 3_000_000
MIN_HACIM_MOVERS = 5_000_000
MIN_HACIM_ORDERFLOW = 5_000_000
MIN_HACIM_CVD = 15_000_000

MAX_TARAMA = 60
CACHE_TTL = 45
TRACKER_FILE = "/tmp/tracked_signals_v19.json"

# YASAKLI VARLIKLAR (HISSELER, ETF'LER VE EMTIALAR)
YASAKLI_HISSELER = {
    "AAPLUSDT", "TSLAUSDT", "NVDAUSDT", "COINUSDT", "MSTRUSDT",
    "SQQQUSDT", "TQQQUSDT", "SPYUSDT", "QQQUSDT", "AMZNUSDT",
    "GOOGLUSDT", "METAUSDT", "NFLXUSDT", "BABAUSDT", "MSFTUSDT",
    "HOODUSDT", "CRCLUSDT", "SOXLUSDT", "SOXLBUSDT", "SOXSUSDT",
    "SOXSBUSDT", "SNXXUSDT", "SNXXBUSDT", "INTCUSDT", "INTCBUSDT",
    "CRDOUSDT", "XAUUSDT", "XAGUSDT", "PAXGUSDT",
}

# SINYAL HAFIZALARI
son_sinyaller = {}
son_pump_sinyalleri = {}
son_volatilite_sinyalleri = {}
son_formasyon_sinyalleri = {}
son_ema_ribbon_sinyalleri = {}
son_macd_sinyalleri = {}
son_sma200_sinyalleri = {}
son_ath_atl_sinyalleri = {}
son_ichimoku_sinyalleri = {}
son_chart_pattern_sinyalleri = {}
son_anlik_ath_atl = {}
son_orderflow_fibo_sinyalleri = {}
son_cvd_sinyalleri = {}
son_zafer_bildirimleri = {}

anlik_ath_cache = {}
anlik_atl_cache = {}

TRACKER_LOCK = threading.Lock()
TRACKED_SIGNALS = []

_STATE_LOCK = threading.Lock()
_TICKER_CACHE = {}
_SOURCE_COOLDOWN = {}
_SOURCE_LAST_ERROR = {}
_LAST_REPORTS = {"TOP_GAINERS": "-", "DOMINANS": "-", "SINYAL_ZAFERI": "-", "SINYAL_TOP24": "-"}


# =====================================================
# ZAMAN VE FORMAT YARDIMCILARI
# =====================================================

def simdi():
    return datetime.now(TR_Saati)


def log(prefix, m):
    print(f"[{simdi().strftime('%H:%M:%S')}] [{prefix}] {m}", flush=True)


def akilli_fiyat(p):
    if p is None:
        return "0"
    p = float(p)
    if p >= 1000:
        return f"{p:.2f}"
    if p >= 1:
        return f"{p:.4f}"
    if p >= 0.01:
        return f"{p:.6f}"
    return f"{p:.8f}"


def _kisa_usd(val):
    sign = "+" if val >= 0 else "-"
    a = abs(val)
    if a >= 1_000_000_000:
        return f"{sign}${a / 1_000_000_000:.2f}B"
    if a >= 1_000_000:
        return f"{sign}${a / 1_000_000:.1f}M"
    if a >= 1_000:
        return f"{sign}${a / 1_000:.0f}K"
    return f"{sign}${a:.0f}"


def _interval_ms(tf):
    units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
    return int(tf[:-1]) * units[tf[-1]]


def _bar_open_ms(k):
    return int(float(k[0]))


def _bar_fresh(closed_bar, interval_ms, max_age_ms):
    now_ms = int(time.time() * 1000)
    close_ms = _bar_open_ms(closed_bar) + interval_ms
    age = now_ms - close_ms
    return 0 <= age <= max_age_ms


# =====================================================
# SAF PYTHON PNG GORSEL MOTORU (KUTUPHANESIZ)
# =====================================================

_FONT_5X7 = {
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "Ç": ["01110", "10001", "10000", "10000", "10001", "01110", "00100"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01110"],
    "Ğ": ["01110", "00000", "01110", "10000", "10111", "10001", "01110"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    "İ": ["00100", "00000", "01110", "00100", "00100", "00100", "01110"],
    "J": ["00111", "00010", "00010", "00010", "10010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "Ö": ["01010", "00000", "01110", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "Ş": ["01111", "10000", "01110", "00001", "11110", "00100", "01000"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "Ü": ["01010", "00000", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
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
    "+": ["00000", "00100", "00100", "11111", "00100", "00100", "00000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    "%": ["11001", "11010", "00010", "00100", "01000", "01011", "10011"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    ",": ["00000", "00000", "00000", "00000", "00100", "00100", "01000"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
    "/": ["00001", "00010", "00010", "00100", "01000", "01000", "10000"],
    "|": ["00100", "00100", "00100", "00100", "00100", "00100", "00100"],
    "(": ["00010", "00100", "01000", "01000", "01000", "00100", "00010"],
    ")": ["01000", "00100", "00010", "00010", "00010", "00100", "01000"],
    "$": ["00100", "01111", "10100", "01110", "00101", "11110", "00100"],
    "#": ["01010", "11111", "01010", "01010", "11111", "01010", "00000"],
    ">": ["10000", "01000", "00100", "00010", "00100", "01000", "10000"],
    "<": ["00010", "00100", "01000", "10000", "01000", "00100", "00010"],
    "=": ["00000", "00000", "11111", "00000", "11111", "00000", "00000"],
    "_": ["00000", "00000", "00000", "00000", "00000", "00000", "11111"],
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
}

_TR_UPPER = str.maketrans("çğıöşüi", "ÇĞİÖŞÜİ")


class _Canvas:
    def __init__(self, w, h, bg=(14, 18, 25)):
        self.w = w
        self.h = h
        self.buf = bytearray(bg * (w * h))

    def px(self, x, y, col):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.buf[i] = col[0]
            self.buf[i + 1] = col[1]
            self.buf[i + 2] = col[2]

    def rect(self, x0, y0, x1, y1, col):
        x0, x1 = max(0, min(x0, x1)), min(self.w - 1, max(x0, x1))
        y0, y1 = max(0, min(y0, y1)), min(self.h - 1, max(y0, y1))
        if x0 > x1 or y0 > y1:
            return
        row = bytes(col) * (x1 - x0 + 1)
        for y in range(y0, y1 + 1):
            i = (y * self.w + x0) * 3
            self.buf[i : i + len(row)] = row

    def circle(self, cx, cy, r, col):
        r2 = r * r
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dy * dy <= r2:
                    self.px(cx + dx, cy + dy, col)

    def line(self, x0, y0, x1, y1, col, dashed=False):
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        step = 0
        while True:
            if not dashed or (step // 4) % 2 == 0:
                self.px(x0, y0, col)
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

    def hline(self, x0, x1, y, col, dashed=False):
        if not (0 <= y < self.h):
            return
        x0, x1 = max(0, x0), min(self.w - 1, x1)
        if not dashed:
            self.rect(x0, y, x1, y, col)
        else:
            for x in range(x0, x1 + 1):
                if ((x - x0) // 5) % 2 == 0:
                    self.px(x, y, col)

    def vline(self, x, y0, y1, col, dashed=False):
        if not (0 <= x < self.w):
            return
        y0, y1 = max(0, y0), min(self.h - 1, y1)
        for y in range(y0, y1 + 1):
            if not dashed or ((y - y0) // 5) % 2 == 0:
                self.px(x, y, col)

    def text(self, x, y, txt, col, scale=2):
        s = txt.translate(_TR_UPPER).upper()
        cx = x
        for ch in s:
            glyph = _FONT_5X7.get(ch, _FONT_5X7[" "])
            for ry, row in enumerate(glyph):
                for rx, bit in enumerate(row):
                    if bit == "1":
                        if scale == 1:
                            self.px(cx + rx, y + ry, col)
                        else:
                            self.rect(
                                cx + rx * scale,
                                y + ry * scale,
                                cx + rx * scale + scale - 1,
                                y + ry * scale + scale - 1,
                                col,
                            )
            cx += 6 * scale

    def to_png(self):
        raw = bytearray()
        stride = self.w * 3
        for y in range(self.h):
            raw.append(0)
            s = y * stride
            raw.extend(self.buf[s : s + stride])

        def chunk(tag, data):
            crc = zlib.crc32(tag + data) & 0xFFFFFFFF
            return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", crc)

        ihdr = struct.pack("!IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b"")
        )


def _ema_series(vals, period):
    if len(vals) < period:
        return [None] * len(vals)
    k = 2 / (period + 1)
    out = [None] * (period - 1)
    cur = sum(vals[:period]) / period
    out.append(cur)
    for v in vals[period:]:
        cur = v * k + cur * (1 - k)
        out.append(cur)
    return out


def _sma_series(vals, period):
    out = [None] * len(vals)
    for i in range(period - 1, len(vals)):
        out[i] = sum(vals[i - period + 1 : i + 1]) / period
    return out


def render_chart_png(symbol, tf_label, title_badge, klines, hlines=None, overlays=None, bars=60, accent=(0, 210, 140)):
    W, H = 960, 560
    c = _Canvas(W, H, bg=(13, 17, 23))
    c.rect(0, 0, W - 1, 58, (20, 26, 36))
    c.hline(0, W - 1, 58, (42, 52, 68))

    c.text(24, 18, f"{symbol}  {tf_label}", (240, 246, 252), scale=3)
    bx = W - len(title_badge) * 12 - 36
    c.rect(max(360, bx - 10), 12, W - 20, 44, (28, 36, 50))
    c.text(max(370, bx), 21, title_badge, accent, scale=2)

    data = klines[-bars:] if len(klines) > bars else klines
    if not data:
        return c.to_png()

    left, right, top, bot = 24, W - 148, 76, 405
    v_top, v_bot = 425, 515

    opens = [float(k[1]) for k in data]
    highs = [float(k[2]) for k in data]
    lows = [float(k[3]) for k in data]
    closes = [float(k[4]) for k in data]
    vols = [float(k[7]) for k in data]

    pmax = max(highs)
    pmin = min(lows)
    if hlines:
        for val, _, _ in hlines:
            pmax = max(pmax, float(val))
            pmin = min(pmin, float(val))
    if overlays:
        for series, _ in overlays:
            for val in series[-len(data) :]:
                if val is not None:
                    pmax = max(pmax, float(val))
                    pmin = min(pmin, float(val))
    pad = (pmax - pmin) * 0.08 or pmax * 0.02 or 1e-8
    pmax += pad
    pmin -= pad
    prange = pmax - pmin

    def py(val):
        return int(top + (pmax - val) / prange * (bot - top))

    for i in range(5):
        gy = int(top + i * (bot - top) / 4)
        c.hline(left, right, gy, (28, 35, 46))
        pv = pmax - i * prange / 4
        c.text(right + 10, gy - 5, akilli_fiyat(pv), (138, 148, 166), scale=1)

    vmax = max(vols) or 1.0
    n = len(data)
    step = (right - left) / max(1, n)
    cw = max(2, int(step * 0.62))

    for i in range(n):
        cx = int(left + (i + 0.5) * step)
        up = closes[i] >= opens[i]
        col = (14, 203, 129) if up else (246, 70, 93)
        vcol = (12, 105, 72) if up else (130, 44, 58)
        y_hi, y_lo = py(highs[i]), py(lows[i])
        y_op, y_cl = py(opens[i]), py(closes[i])
        c.vline(cx, y_hi, y_lo, col)
        c.rect(cx - cw // 2, min(y_op, y_cl), cx + cw // 2, max(y_op, y_cl, min(y_op, y_cl) + 1), col)
        vh = int((vols[i] / vmax) * (v_bot - v_top))
        c.rect(cx - cw // 2, v_bot - vh, cx + cw // 2, v_bot, vcol)

    if overlays:
        for series, ocol in overlays:
            sub = series[-n:]
            pts = []
            for i, val in enumerate(sub):
                if val is not None:
                    pts.append((int(left + (i + 0.5) * step), py(val)))
            for i in range(1, len(pts)):
                c.line(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1], ocol)
                c.line(pts[i - 1][0], pts[i - 1][1] + 1, pts[i][0], pts[i][1] + 1, ocol)

    if hlines:
        for val, label, lcol in hlines:
            ly = py(val)
            if top <= ly <= bot:
                c.hline(left, right, ly, lcol, dashed=True)
                c.rect(right + 4, ly - 9, W - 8, ly + 9, lcol)
                c.text(right + 8, ly - 5, f"{label} {akilli_fiyat(val)}"[:19], (13, 17, 23), scale=1)

    c.rect(0, H - 30, W - 1, H - 1, (18, 24, 33))
    c.text(24, H - 20, f"SON FIYAT: {akilli_fiyat(closes[-1])}   TSI {simdi().strftime('%H:%M')}", (155, 165, 180), scale=1)
    return c.to_png()


def render_card_png(title, subtitle, rows, footer="", accent=(0, 210, 140)):
    W, H = 920, 170 + len(rows) * 66
    c = _Canvas(W, H, bg=(13, 17, 23))
    c.rect(0, 0, W - 1, 78, (20, 26, 36))
    c.hline(0, W - 1, 78, (42, 52, 68))
    c.text(26, 16, title, (240, 246, 252), scale=2)
    c.text(26, 46, subtitle, accent, scale=2)

    y = 102
    max_abs = max([abs(r[2]) for r in rows] + [1.0])
    for label, price_txt, pct_val, col in rows:
        c.rect(24, y, W - 24, y + 52, (20, 26, 36))
        c.text(38, y + 17, label[:28], (235, 242, 250), scale=2)
        bar_w = int((abs(pct_val) / max_abs) * 180)
        bx0 = 515
        c.rect(bx0, y + 17, bx0 + max(8, bar_w), y + 35, col)
        c.text(715, y + 17, f"{pct_val:+.2f}%", col, scale=2)
        if price_txt:
            c.text(360, y + 20, price_txt[:15], (155, 165, 180), scale=1)
        y += 64

    if footer:
        c.text(26, H - 28, footer[:110], (145, 156, 172), scale=1)
    return c.to_png()


def render_win_chart_png(symbol, yon, motor, gain_pct, hours_elapsed, entry_price, peak_price, entry_label, peak_label, series_points, tracker_footer):
    W, H = 960, 560
    is_long = yon == "LONG"
    accent = (14, 203, 129) if is_long else (246, 70, 93)
    line_col = (128, 120, 255)
    c = _Canvas(W, H, bg=(14, 15, 28))

    c.rect(0, 0, W - 1, 88, (22, 23, 44))
    c.hline(0, W - 1, 88, (48, 52, 82))
    c.text(26, 16, f"{symbol}  {yon}", (242, 245, 255), scale=3)
    c.text(26, 52, f"KAYNAK: {motor}   SINYAL: {entry_label}", (160, 170, 205), scale=2)

    badge = "GUNUN ZAFERI"
    c.rect(W - 220, 14, W - 24, 38, (45, 40, 88))
    c.text(W - 205, 20, badge, (195, 185, 255), scale=2)
    pct_txt = f"+{gain_pct:.1f}%"
    c.text(W - len(pct_txt) * 18 - 28, 48, pct_txt, accent, scale=3)

    if not series_points or len(series_points) < 2:
        return c.to_png()

    left, right, top, bot = 36, W - 138, 112, 442
    prices = [p[1] for p in series_points] + [entry_price, peak_price]
    pmax, pmin = max(prices), min(prices)
    pad = (pmax - pmin) * 0.14 or pmax * 0.02 or 1e-8
    pmax += pad
    pmin -= pad
    prange = pmax - pmin

    def py(v):
        return int(top + (pmax - v) / prange * (bot - top))

    for i in range(5):
        gy = int(top + i * (bot - top) / 4)
        c.hline(left, right, gy, (30, 32, 54))
        pv = pmax - i * prange / 4
        c.text(right + 10, gy - 4, akilli_fiyat(pv), (135, 145, 175), scale=1)

    n = len(series_points)
    pts = [(int(left + (i / max(1, n - 1)) * (right - left)), py(val)) for i, (_, val, _) in enumerate(series_points)]

    for i in range(1, len(pts)):
        x0, y0 = pts[i - 1]
        x1, y1 = pts[i]
        for x in range(x0, x1 + 1):
            t = (x - x0) / max(1, x1 - x0)
            c.vline(x, int(y0 + t * (y1 - y0)), bot, (22, 24, 52))

    for i in range(1, len(pts)):
        x0, y0 = pts[i - 1]
        x1, y1 = pts[i]
        c.line(x0, y0, x1, y1, line_col)
        c.line(x0, y0 + 1, x1, y1 + 1, line_col)

    entry_idx, peak_idx = 0, 0
    for i, (_, _, tag) in enumerate(series_points):
        if tag == "entry":
            entry_idx = i
        elif tag == "peak":
            peak_idx = i

    ex, ey = pts[entry_idx]
    kx, ky = pts[peak_idx]

    c.vline(ex, top, bot, (130, 140, 185), dashed=True)
    c.circle(ex, ey, 6, (255, 255, 255))
    c.circle(ex, ey, 4, (90, 120, 255))
    lbl_e = f"GIRIS ${akilli_fiyat(entry_price)}"
    elx = min(max(left + 6, ex + 8), right - len(lbl_e) * 7)
    c.rect(elx - 4, min(bot - 18, ey + 8), elx + len(lbl_e) * 6 + 4, min(bot - 2, ey + 22), (24, 28, 56))
    c.text(elx, min(bot - 15, ey + 11), lbl_e, (220, 228, 255), scale=1)

    c.circle(kx, ky, 7, (255, 255, 255))
    c.circle(kx, ky, 5, accent)
    lbl_p = f"PIK ${akilli_fiyat(peak_price)} ({hours_elapsed:.1f}S)"
    klx = min(max(left + 6, kx - len(lbl_p) * 6 - 10), right - len(lbl_p) * 6 - 4)
    kly = max(top + 4, ky - 22)
    c.rect(klx - 4, kly, klx + len(lbl_p) * 6 + 4, kly + 15, (20, 52, 42) if is_long else (64, 24, 34))
    c.text(klx, kly + 4, lbl_p, accent, scale=1)

    c.rect(0, H - 78, W - 1, H - 1, (19, 21, 38))
    c.hline(0, W - 1, H - 78, (48, 52, 82))
    c.text(26, H - 64, f"{entry_label} GIRIS ${akilli_fiyat(entry_price)}  ->  {peak_label} PIK ${akilli_fiyat(peak_price)} ({hours_elapsed:.1f}S)", (225, 232, 248), scale=2)
    c.text(26, H - 30, tracker_footer[:110], (145, 155, 185), scale=1)
    return c.to_png()


def render_cvd_chart_png(symbol, badge, fut_klines, spot_cvd, fut_cvd, oi_pct, div_pts=None, accent=(14, 203, 129)):
    W, H = 960, 590
    c = _Canvas(W, H, bg=(13, 17, 23))
    c.rect(0, 0, W - 1, 58, (20, 26, 36))
    c.hline(0, W - 1, 58, (42, 52, 68))
    c.text(24, 18, f"{symbol}  1H CVD", (240, 246, 252), scale=3)

    bx = W - len(badge) * 12 - 36
    c.rect(max(360, bx - 10), 12, W - 20, 44, (28, 36, 50))
    c.text(max(370, bx), 21, badge, accent, scale=2)

    n = min(len(fut_klines), len(spot_cvd), len(fut_cvd))
    if n < 5:
        return c.to_png()
    ks = fut_klines[-n:]
    sc = spot_cvd[-n:]
    fc = fut_cvd[-n:]

    left, right, p_top, p_bot = 24, W - 148, 74, 302
    opens = [float(k[1]) for k in ks]
    highs = [float(k[2]) for k in ks]
    lows = [float(k[3]) for k in ks]
    closes = [float(k[4]) for k in ks]
    pmax, pmin = max(highs), min(lows)
    pad = (pmax - pmin) * 0.08 or pmax * 0.02 or 1e-8
    pmax += pad
    pmin -= pad
    prange = pmax - pmin

    def py(v):
        return int(p_top + (pmax - v) / prange * (p_bot - p_top))

    for i in range(4):
        gy = int(p_top + i * (p_bot - p_top) / 3)
        c.hline(left, right, gy, (28, 35, 46))
        pv = pmax - i * prange / 3
        c.text(right + 10, gy - 4, akilli_fiyat(pv), (138, 148, 166), scale=1)

    step = (right - left) / max(1, n)
    cw = max(3, int(step * 0.58))
    for i in range(n):
        cx = int(left + (i + 0.5) * step)
        up = closes[i] >= opens[i]
        col = (14, 203, 129) if up else (246, 70, 93)
        c.vline(cx, py(highs[i]), py(lows[i]), col)
        y_op, y_cl = py(opens[i]), py(closes[i])
        c.rect(cx - cw // 2, min(y_op, y_cl), cx + cw // 2, max(y_op, y_cl, min(y_op, y_cl) + 1), col)

    if div_pts:
        i1, v1, i2, v2, lbl, dcol = div_pts
        x1 = int(left + (i1 + 0.5) * step)
        x2 = int(left + (i2 + 0.5) * step)
        y1, y2 = py(v1), py(v2)
        c.line(x1, y1, x2, y2, dcol)
        c.line(x1, y1 + 1, x2, y2 + 1, dcol)
        c.circle(x1, y1, 4, dcol)
        c.circle(x2, y2, 4, dcol)
        c.text(min(right - 80, x2 + 6), max(p_top + 4, min(p_bot - 12, y2 - 8)), lbl, dcol, scale=1)

    c.rect(24, 314, W - 24, 344, (20, 26, 36))
    c.text(36, 322, f"SPOT CVD: {_kisa_usd(sc[-1])}", (245, 166, 35), scale=2)
    c.text(350, 322, f"FUTURES CVD: {_kisa_usd(fc[-1])}", (70, 155, 255), scale=2)
    oi_col = (14, 203, 129) if oi_pct >= 0 else (246, 70, 93)
    c.text(705, 322, f"OI: {oi_pct:+.2f}%", oi_col, scale=2)

    c_top, c_bot = 358, 540
    all_cvd = sc + fc + [0.0]
    cmax, cmin = max(all_cvd), min(all_cvd)
    cpad = (cmax - cmin) * 0.12 or 1.0
    cmax += cpad
    cmin -= cpad
    crange = cmax - cmin

    def cy(v):
        return int(c_top + (cmax - v) / crange * (c_bot - c_top))

    zy = cy(0.0)
    if c_top <= zy <= c_bot:
        c.hline(left, right, zy, (55, 65, 82), dashed=True)

    s_pts = [(int(left + (i + 0.5) * step), cy(sc[i])) for i in range(n)]
    f_pts = [(int(left + (i + 0.5) * step), cy(fc[i])) for i in range(n)]
    for i in range(1, n):
        c.line(f_pts[i - 1][0], f_pts[i - 1][1], f_pts[i][0], f_pts[i][1], (70, 155, 255))
        c.line(f_pts[i - 1][0], f_pts[i - 1][1] + 1, f_pts[i][0], f_pts[i][1] + 1, (70, 155, 255))
        c.line(s_pts[i - 1][0], s_pts[i - 1][1], s_pts[i][0], s_pts[i][1], (245, 166, 35))
        c.line(s_pts[i - 1][0], s_pts[i - 1][1] + 1, s_pts[i][0], s_pts[i][1] + 1, (245, 166, 35))

    sy_end = s_pts[-1][1]
    fy_end = f_pts[-1][1]
    if abs(sy_end - fy_end) < 18:
        fy_end = sy_end + 19 if fy_end >= sy_end else sy_end - 19
    c.rect(right + 4, max(c_top, sy_end - 9), W - 8, min(c_bot, sy_end + 9), (245, 166, 35))
    c.text(right + 8, max(c_top + 2, sy_end - 4), f"SPT {_kisa_usd(sc[-1])}"[:18], (13, 17, 23), scale=1)
    c.rect(right + 4, max(c_top, fy_end - 9), W - 8, min(c_bot, fy_end + 9), (70, 155, 255))
    c.text(right + 8, max(c_top + 2, fy_end - 4), f"FUT {_kisa_usd(fc[-1])}"[:18], (13, 17, 23), scale=1)

    c.rect(0, H - 32, W - 1, H - 1, (18, 24, 33))
    c.text(24, H - 22, f"TOPLAM CVD: {_kisa_usd(sc[-1] + fc[-1])} | SON FIYAT: {akilli_fiyat(closes[-1])} | TSI {simdi().strftime('%H:%M')}", (155, 165, 180), scale=1)
    return c.to_png()


# =====================================================
# TELEGRAM GONDERIM (1024 KARAKTER KORUMALI)
# =====================================================

def tg(chat_id, message):
    if not T or not chat_id:
        return False
    try:
        data = urllib.parse.urlencode(
            {"chat_id": str(chat_id), "text": message, "parse_mode": "HTML", "disable_web_page_preview": "true"}
        ).encode("utf-8")
        req = urllib.request.Request(f"https://api.telegram.org/bot{T}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8")).get("ok", False)
    except Exception:
        return False


def tg_photo(chat_id, png_bytes, caption):
    if not T or not chat_id:
        return False
    if len(caption) > 1000:
        short_cap = caption[:960].rsplit("\n", 1)[0] + "\n..."
        ok = tg_photo(chat_id, png_bytes, short_cap)
        if ok:
            tg(chat_id, caption)
        return ok
    try:
        boundary = f"----BotBoundary{int(time.time() * 1000)}"
        parts = []
        for name, val in [("chat_id", str(chat_id)), ("caption", caption), ("parse_mode", "HTML")]:
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{val}\r\n".encode("utf-8")
            )
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"chart.png\"\r\nContent-Type: image/png\r\n\r\n".encode("utf-8")
            + png_bytes
            + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{T}/sendPhoto",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            res = json.loads(r.read().decode("utf-8"))
            if res.get("ok"):
                return True
    except Exception as e:
        log("TG_PHOTO", f"Foto gonderim hatasi ({e}), metin dusuluyor")
    return tg(chat_id, caption)


# =====================================================
# COKLU BORSA VERI KATMANI (FALLBACK + RATE LIMIT)
# =====================================================

def _in_cooldown(src):
    with _STATE_LOCK:
        return time.time() < _SOURCE_COOLDOWN.get(src, 0)


def _mark_error(src, err, cd=120):
    with _STATE_LOCK:
        _SOURCE_LAST_ERROR[src] = f"{simdi().strftime('%H:%M:%S')} {err}"
        _SOURCE_COOLDOWN[src] = time.time() + cd


def gj(url, src=None, timeout=12):
    if src and _in_cooldown(src):
        return None
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if src:
            _mark_error(src, f"HTTP {e.code}", 180 if e.code in (418, 429, 451, 403) else 60)
        return None
    except Exception as e:
        if src:
            _mark_error(src, str(e)[:60], 45)
        return None


def _normalize_bybit_tickers(res):
    out = []
    if not isinstance(res, dict) or res.get("retCode") != 0:
        return out
    for t in res.get("result", {}).get("list", []):
        sym = t.get("symbol", "")
        if not sym.endswith("USDT") or sym in YASAKLI_HISSELER:
            continue
        try:
            out.append(
                {
                    "symbol": sym,
                    "lastPrice": str(float(t.get("lastPrice", 0))),
                    "priceChangePercent": str(float(t.get("price24hPcnt", 0)) * 100),
                    "quoteVolume": str(float(t.get("turnover24h", 0))),
                }
            )
        except Exception:
            continue
    return out


def _clean_tickers(raw, min_vol=0):
    out = []
    if not isinstance(raw, list):
        return out
    for t in raw:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT") or sym in YASAKLI_HISSELER:
            continue
        try:
            qv = float(t.get("quoteVolume", 0))
            if qv >= min_vol and float(t.get("lastPrice", 0)) > 0:
                out.append(t)
        except Exception:
            continue
    out.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
    return out


def get_futures_tickers(min_vol=0):
    with _STATE_LOCK:
        cached = _TICKER_CACHE.get("futures")
        if cached and time.time() - cached[0] < CACHE_TTL:
            return [t for t in cached[1] if float(t.get("quoteVolume", 0)) >= min_vol]

    rows = _clean_tickers(gj(f"{FAPI}/fapi/v1/ticker/24hr", "binance_fapi"))
    if not rows:
        rows = _clean_tickers(_normalize_bybit_tickers(gj(f"{BYBIT}/v5/market/tickers?category=linear", "bybit_linear")))
    if rows:
        with _STATE_LOCK:
            _TICKER_CACHE["futures"] = (time.time(), rows)
    return [t for t in rows if float(t.get("quoteVolume", 0)) >= min_vol]


def get_spot_tickers(min_vol=0):
    with _STATE_LOCK:
        cached = _TICKER_CACHE.get("spot")
        if cached and time.time() - cached[0] < CACHE_TTL:
            return [t for t in cached[1] if float(t.get("quoteVolume", 0)) >= min_vol]

    rows = _clean_tickers(gj(f"{BASE}/api/v3/ticker/24hr", "binance_spot"))
    if not rows:
        rows = _clean_tickers(gj(f"{DATA_API}/api/v3/ticker/24hr", "binance_vision"))
    if not rows:
        rows = _clean_tickers(_normalize_bybit_tickers(gj(f"{BYBIT}/v5/market/tickers?category=spot", "bybit_spot")))
    if rows:
        with _STATE_LOCK:
            _TICKER_CACHE["spot"] = (time.time(), rows)
    return [t for t in rows if float(t.get("quoteVolume", 0)) >= min_vol]


_BYBIT_TF = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "4h": "240", "1d": "D"}


def _fetch_bybit_klines(symbol, tf, limit, category):
    itv = _BYBIT_TF.get(tf)
    if not itv:
        return None
    res = gj(f"{BYBIT}/v5/market/kline?category={category}&symbol={symbol}&interval={itv}&limit={min(limit, 1000)}", f"bybit_{category}")
    if not isinstance(res, dict) or res.get("retCode") != 0:
        return None
    lst = res.get("result", {}).get("list", [])
    if not lst:
        return None
    out = []
    for row in reversed(lst):
        out.append([int(row[0]), row[1], row[2], row[3], row[4], row[5], int(row[0]), row[6]])
    return out


def get_klines(symbol, tf, limit=100, market="futures"):
    if market == "futures":
        ks = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval={tf}&limit={limit}", "binance_fapi")
        if isinstance(ks, list) and ks:
            return ks
        return _fetch_bybit_klines(symbol, tf, limit, "linear")
    ks = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit={limit}", "binance_spot")
    if isinstance(ks, list) and ks:
        return ks
    ks = gj(f"{DATA_API}/api/v3/klines?symbol={symbol}&interval={tf}&limit={limit}", "binance_vision")
    if isinstance(ks, list) and ks:
        return ks
    return _fetch_bybit_klines(symbol, tf, limit, "spot")


# =====================================================
# SPOT + FUTURES CVD + OI VERI MOTORU
# =====================================================

def get_oi_change_pct(symbol, period="1h"):
    oi_data = gj(f"{FAPI}/futures/data/openInterestHist?symbol={symbol}&period={period}&limit=5", "binance_fapi", timeout=8)
    if isinstance(oi_data, list) and len(oi_data) >= 2:
        o_old = float(oi_data[0]["sumOpenInterest"])
        o_new = float(oi_data[-1]["sumOpenInterest"])
        if o_old > 0:
            return ((o_new - o_old) / o_old) * 100.0
    b_oi = gj(f"{BYBIT}/v5/market/open-interest?category=linear&symbol={symbol}&intervalTime=1h&limit=5", "bybit_linear", timeout=8)
    if isinstance(b_oi, dict) and b_oi.get("retCode") == 0:
        lst = b_oi.get("result", {}).get("list", [])
        if len(lst) >= 2:
            o_new = float(lst[0]["openInterest"])
            o_old = float(lst[-1]["openInterest"])
            if o_old > 0:
                return ((o_new - o_old) / o_old) * 100.0
    return None


def get_spot_fut_cvd(symbol, limit=40):
    ks_spot = gj(f"{DATA_API}/api/v3/klines?symbol={symbol}&interval=1h&limit={limit}", "binance_vision", timeout=10)
    if not isinstance(ks_spot, list) or len(ks_spot) < 20:
        ks_spot = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1h&limit={limit}", "binance_spot", timeout=10)
    if not isinstance(ks_spot, list) or len(ks_spot) < 20 or len(ks_spot[0]) < 11:
        return None

    ks_fut = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1h&limit={limit}", "binance_fapi", timeout=10)
    fut_deltas_by_ts = {}
    fut_bars_by_ts = {}
    if isinstance(ks_fut, list) and len(ks_fut) >= 20 and len(ks_fut[0]) >= 11:
        for k in ks_fut:
            ts = int(float(k[0]))
            qv = float(k[7])
            tbq = float(k[10])
            fut_bars_by_ts[ts] = k
            fut_deltas_by_ts[ts] = (2.0 * tbq) - qv
    else:
        ks_bybit = _fetch_bybit_klines(symbol, "1h", limit, "linear")
        base = symbol[:-4] if symbol.endswith("USDT") else symbol
        okx_tv = gj(f"{OKX}/api/v5/rubik/stat/taker-volume-contract?instId={base}-USDT-SWAP&period=1H&limit={limit}", "okx", timeout=8)
        if not ks_bybit or not isinstance(okx_tv, dict) or okx_tv.get("code") != "0":
            return None
        tv_map = {}
        for row in okx_tv.get("data", []):
            try:
                sell_v, buy_v = float(row[1]), float(row[2])
                tot_v = sell_v + buy_v
                if tot_v > 0:
                    tv_map[int(float(row[0]))] = (buy_v - sell_v) / tot_v
            except Exception:
                continue
        for k in ks_bybit:
            ts = int(float(k[0]))
            if ts in tv_map:
                fut_bars_by_ts[ts] = k
                fut_deltas_by_ts[ts] = tv_map[ts] * float(k[7])

    aligned_fut = []
    spot_deltas, fut_deltas = [], []
    spot_vols, fut_vols = [], []
    for k in ks_spot:
        ts = int(float(k[0]))
        if ts in fut_bars_by_ts and ts in fut_deltas_by_ts:
            qv_s = float(k[7])
            tbq_s = float(k[10])
            aligned_fut.append(fut_bars_by_ts[ts])
            spot_deltas.append((2.0 * tbq_s) - qv_s)
            fut_deltas.append(fut_deltas_by_ts[ts])
            spot_vols.append(qv_s)
            fut_vols.append(float(fut_bars_by_ts[ts][7]))

    if len(aligned_fut) < 20:
        return None

    spot_cvd, fut_cvd, comb_cvd = [], [], []
    sa = fa = 0.0
    for sd, fd in zip(spot_deltas, fut_deltas):
        sa += sd
        fa += fd
        spot_cvd.append(sa)
        fut_cvd.append(fa)
        comb_cvd.append(sa + fa)

    return {
        "fut_klines": aligned_fut,
        "spot_deltas": spot_deltas,
        "fut_deltas": fut_deltas,
        "spot_cvd": spot_cvd,
        "fut_cvd": fut_cvd,
        "comb_cvd": comb_cvd,
        "spot_vols": spot_vols,
        "fut_vols": fut_vols,
    }


# =====================================================
# TEKNIK HESAPLAMALAR
# =====================================================

def calculate_ema(prices, period):
    if len(prices) < period:
        return []
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for price in prices[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema


def calculate_sma(prices, period):
    if len(prices) < period:
        return []
    return [sum(prices[i - period + 1 : i + 1]) / period for i in range(period - 1, len(prices))]


def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))


def calculate_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow + signal:
        return None
    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)
    if not ema_fast or not ema_slow:
        return None
    min_len = min(len(ema_fast), len(ema_slow))
    macd_line = [ema_fast[-min_len + i] - ema_slow[-min_len + i] for i in range(min_len)]
    signal_line = calculate_ema(macd_line, signal)
    if len(signal_line) < 2:
        return None
    return macd_line[-1], signal_line[-1], macd_line[-2], signal_line[-2]


def find_pivots(highs, lows, window=3):
    ph, pl = [], []
    for i in range(window, len(highs) - window):
        if highs[i] == max(highs[i - window : i + window + 1]):
            ph.append((i, highs[i]))
        if lows[i] == min(lows[i - window : i + window + 1]):
            pl.append((i, lows[i]))
    return ph, pl


def trap_radar_check(symbol):
    try:
        oi_data = gj(f"{FAPI}/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=2", "binance_fapi")
        oi_artiyor = False
        if isinstance(oi_data, list) and len(oi_data) >= 2:
            oi_old = float(oi_data[0]["sumOpenInterest"])
            oi_new = float(oi_data[1]["sumOpenInterest"])
            if oi_old > 0 and ((oi_new - oi_old) / oi_old) * 100 > 0.2:
                oi_artiyor = True

        taker_data = gj(f"{FAPI}/futures/data/takerlongshortRatio?symbol={symbol}&period=5m&limit=1", "binance_fapi")
        alici_baskin = False
        if isinstance(taker_data, list) and taker_data:
            bv, sv = float(taker_data[0]["buyVol"]), float(taker_data[0]["sellVol"])
            if bv + sv > 0 and (bv / (bv + sv)) * 100 > 55:
                alici_baskin = True

        if not oi_data and not taker_data:
            bybit_oi = gj(f"{BYBIT}/v5/market/open-interest?category=linear&symbol={symbol}&intervalTime=5min&limit=2", "bybit_linear")
            if isinstance(bybit_oi, dict) and bybit_oi.get("retCode") == 0:
                lst = bybit_oi.get("result", {}).get("list", [])
                if len(lst) >= 2:
                    oi_new = float(lst[0]["openInterest"])
                    oi_old = float(lst[1]["openInterest"])
                    if oi_old > 0 and ((oi_new - oi_old) / oi_old) * 100 > 0.2:
                        oi_artiyor = True

        if oi_artiyor and alici_baskin:
            return "🐋 <b>BALİNA TEYİDİ (Gerçek Pump)</b>"
        elif not alici_baskin and taker_data:
            return "⚠️ <b>DİKKAT: SAHTE PUMP (TUZAK OLABİLİR)</b>"
        elif oi_artiyor:
            return "⚖️ <b>Kısmi Teyit (OI Artışı)</b>"
        return "⚖️ <b>Kısmi Teyit</b>"
    except Exception:
        return ""


# =====================================================
# 7 BORSA L2 EMIR DEFTERI + HACIM PROFILI (VAH / VAL)
# =====================================================

def _bucket_decimals(live_price):
    if live_price >= 1000:
        return 1
    if live_price >= 100:
        return 2
    if live_price >= 10:
        return 3
    if live_price >= 1:
        return 4
    if live_price >= 0.01:
        return 5
    if live_price >= 0.0001:
        return 6
    return 7


def _collect_ob_levels(bids, asks, bid_buckets, ask_buckets, dec):
    b_tot = 0.0
    a_tot = 0.0
    for row in bids:
        p, q = float(row[0]), float(row[1])
        usd = p * q
        b_tot += usd
        key = round(p, dec)
        bid_buckets[key] = bid_buckets.get(key, 0.0) + usd
    for row in asks:
        p, q = float(row[0]), float(row[1])
        usd = p * q
        a_tot += usd
        key = round(p, dec)
        ask_buckets[key] = ask_buckets.get(key, 0.0) + usd
    return b_tot, a_tot


def get_multi_orderbook_flow(symbol, live_price):
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    dec = _bucket_decimals(live_price)
    bid_buckets, ask_buckets = {}, {}
    exchanges = 0
    total_bid = 0.0
    total_ask = 0.0

    r = gj(f"{BYBIT}/v5/market/orderbook?category=linear&symbol={symbol}&limit=50", "bybit_linear", timeout=8)
    if isinstance(r, dict) and r.get("retCode") == 0:
        res = r.get("result", {})
        if res.get("b") and res.get("a"):
            b, a = _collect_ob_levels(res["b"], res["a"], bid_buckets, ask_buckets, dec)
            total_bid += b
            total_ask += a
            exchanges += 1

    r = gj(f"{BITGET}/api/v2/mix/market/merge-depth?symbol={symbol}&productType=USDT-FUTURES&limit=50", "bitget", timeout=8)
    if isinstance(r, dict) and r.get("code") == "00000":
        res = r.get("data", {})
        if res.get("bids") and res.get("asks"):
            b, a = _collect_ob_levels(res["bids"], res["asks"], bid_buckets, ask_buckets, dec)
            total_bid += b
            total_ask += a
            exchanges += 1

    r = gj(f"{OKX}/api/v5/market/books?instId={base}-USDT&sz=50", "okx", timeout=8)
    if isinstance(r, dict) and r.get("code") == "0" and r.get("data"):
        res = r["data"][0]
        if res.get("bids") and res.get("asks"):
            b, a = _collect_ob_levels(res["bids"], res["asks"], bid_buckets, ask_buckets, dec)
            total_bid += b
            total_ask += a
            exchanges += 1

    r = gj(f"{GATE}/api/v4/spot/order_book?currency_pair={base}_USDT&limit=50", "gate", timeout=8)
    if isinstance(r, dict) and r.get("bids") and r.get("asks"):
        b, a = _collect_ob_levels(r["bids"], r["asks"], bid_buckets, ask_buckets, dec)
        total_bid += b
        total_ask += a
        exchanges += 1

    r = gj(f"{MEXC}/api/v3/depth?symbol={symbol}&limit=50", "mexc", timeout=8)
    if isinstance(r, dict) and r.get("bids") and r.get("asks"):
        b, a = _collect_ob_levels(r["bids"], r["asks"], bid_buckets, ask_buckets, dec)
        total_bid += b
        total_ask += a
        exchanges += 1

    r = gj(f"{KUCOIN}/api/v1/market/orderbook/level2_20?symbol={base}-USDT", "kucoin", timeout=8)
    if isinstance(r, dict) and r.get("code") == "200000":
        res = r.get("data", {})
        if res.get("bids") and res.get("asks"):
            b, a = _collect_ob_levels(res["bids"], res["asks"], bid_buckets, ask_buckets, dec)
            total_bid += b
            total_ask += a
            exchanges += 1

    r = gj(f"{DATA_API}/api/v3/depth?symbol={symbol}&limit=50", "binance_vision", timeout=8)
    if isinstance(r, dict) and r.get("bids") and r.get("asks"):
        b, a = _collect_ob_levels(r["bids"], r["asks"], bid_buckets, ask_buckets, dec)
        total_bid += b
        total_ask += a
        exchanges += 1

    if exchanges < 3 or not bid_buckets or not ask_buckets:
        return None

    bid_wall_price, bid_wall_usd = max(bid_buckets.items(), key=lambda x: x[1])
    ask_wall_price, ask_wall_usd = max(ask_buckets.items(), key=lambda x: x[1])
    return {
        "exchanges": exchanges,
        "total_bid": total_bid,
        "total_ask": total_ask,
        "bid_wall_price": bid_wall_price,
        "bid_wall_usd": bid_wall_usd,
        "ask_wall_price": ask_wall_price,
        "ask_wall_usd": ask_wall_usd,
    }


def calculate_volume_profile(klines, bins=24):
    if not klines or len(klines) < 10:
        return None
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    pmax, pmin = max(highs), min(lows)
    if pmax <= pmin:
        return None
    step = (pmax - pmin) / bins
    hist = [0.0] * bins
    for k in klines:
        typical = (float(k[2]) + float(k[3]) + float(k[4])) / 3.0
        qv = float(k[7])
        idx = min(bins - 1, max(0, int((typical - pmin) / step)))
        hist[idx] += qv
    poc_idx = max(range(bins), key=lambda i: hist[i])
    total_v = sum(hist)
    if total_v <= 0:
        return None
    target_v = total_v * 0.70
    acc = hist[poc_idx]
    lo_i, hi_i = poc_idx, poc_idx
    while acc < target_v and (lo_i > 0 or hi_i < bins - 1):
        left_v = hist[lo_i - 1] if lo_i > 0 else -1.0
        right_v = hist[hi_i + 1] if hi_i < bins - 1 else -1.0
        if right_v >= left_v:
            hi_i += 1
            acc += right_v
        else:
            lo_i -= 1
            acc += left_v
    return {"poc": pmin + (poc_idx + 0.5) * step, "vah": pmin + (hi_i + 1) * step, "val": pmin + lo_i * step}


# =====================================================
# SINYAL PERFORMANS TAKIP SISTEMI
# =====================================================

def _load_tracked_signals():
    global TRACKED_SIGNALS
    try:
        if os.path.exists(TRACKER_FILE):
            with open(TRACKER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                cutoff = int(time.time() * 1000) - 72 * 3600 * 1000
                TRACKED_SIGNALS = [x for x in data if isinstance(x, dict) and x.get("entry_ms", 0) >= cutoff][-1500:]
    except Exception:
        pass


def _save_tracked_signals():
    try:
        tmp = TRACKER_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(TRACKED_SIGNALS[-1500:], f)
        os.replace(tmp, TRACKER_FILE)
    except Exception:
        pass


def record_tracked_signal(symbol, yon, motor, entry_price, market="futures"):
    try:
        yon_norm = "SHORT" if "SHORT" in str(yon).upper() else "LONG"
        ep = float(entry_price)
        if ep <= 0:
            return
        now_ms = int(time.time() * 1000)
        with TRACKER_LOCK:
            for s in reversed(TRACKED_SIGNALS[-200:]):
                if s["symbol"] == symbol and s["yon"] == yon_norm and s["motor"] == motor and now_ms - s["entry_ms"] < 7200 * 1000:
                    return
            TRACKED_SIGNALS.append(
                {
                    "id": f"{symbol}_{yon_norm}_{motor}_{now_ms}",
                    "symbol": symbol,
                    "yon": yon_norm,
                    "motor": motor,
                    "market": market,
                    "entry_price": ep,
                    "entry_ms": now_ms,
                    "best_price": ep,
                    "best_pct": 0.0,
                    "peak_ms": now_ms,
                    "hit_2pct": False,
                    "last_checked_ms": now_ms,
                }
            )
            if len(TRACKED_SIGNALS) > 1500:
                del TRACKED_SIGNALS[: len(TRACKED_SIGNALS) - 1500]
            _save_tracked_signals()
    except Exception:
        pass


def update_tracked_signals():
    now_ms = int(time.time() * 1000)
    cutoff_24h = now_ms - 24 * 3600 * 1000
    with TRACKER_LOCK:
        active = [s for s in TRACKED_SIGNALS if s["entry_ms"] >= cutoff_24h]
    if not active:
        return []

    pairs = list({(s["symbol"], s.get("market", "futures")) for s in active[-45:]})
    kline_map = {}
    for sym, mkt in pairs:
        ks = get_klines(sym, "5m", 288, market=mkt)
        if isinstance(ks, list) and ks:
            kline_map[(sym, mkt)] = ks
        time.sleep(0.08)

    with TRACKER_LOCK:
        for s in TRACKED_SIGNALS:
            if s["entry_ms"] < cutoff_24h:
                continue
            ks = kline_map.get((s["symbol"], s.get("market", "futures")))
            if not ks:
                continue
            ep = s["entry_price"]
            is_long = s["yon"] == "LONG"
            for k in ks:
                k_open = int(float(k[0]))
                if k_open + 300_000 < s["entry_ms"]:
                    continue
                hi, lo = float(k[2]), float(k[3])
                pct, cand_price = (((hi - ep) / ep) * 100.0, hi) if is_long else (((ep - lo) / ep) * 100.0, lo)
                if pct > s["best_pct"]:
                    s["best_pct"] = pct
                    s["best_price"] = cand_price
                    s["peak_ms"] = max(s["entry_ms"], k_open)
                if pct >= 2.0:
                    s["hit_2pct"] = True
            s["last_checked_ms"] = now_ms
        _save_tracked_signals()
        return [dict(s) for s in TRACKED_SIGNALS if s["entry_ms"] >= cutoff_24h]


def get_tracker_stats():
    with TRACKER_LOCK:
        total = len(TRACKED_SIGNALS)
        if total == 0:
            return 0, 0.0
        hits = sum(1 for s in TRACKED_SIGNALS if s.get("hit_2pct"))
        return total, (hits / total) * 100.0


# =====================================================
# MOTOR 1-19 LOOPS
# =====================================================

def top_gainers_loop():
    log("TOP_GAINERS", "En Cok Yukselenler Rapor Motoru Basladi.")
    time.sleep(10)
    while True:
        try:
            tickers = get_spot_tickers(5_000_000)
            if not tickers:
                time.sleep(300)
                continue
            tickers.sort(key=lambda x: float(x["priceChangePercent"]), reverse=True)
            top_5 = tickers[:5]
            if top_5:
                medals = ["🥇", "🥈", "🥉", "🏅", "⭐️"]
                rows = []
                msg = "🔥 <b>Günün En İyi Hareketleri (24 Saat)</b>\n<i>Spot piyasa, hacim teyitli</i>\n\n"
                for i, t in enumerate(top_5):
                    sym = t["symbol"].replace("USDT", "")
                    chg = float(t["priceChangePercent"])
                    prc = float(t["lastPrice"])
                    rows.append((f"#{i+1} {sym}", akilli_fiyat(prc), chg, (14, 203, 129) if chg >= 0 else (246, 70, 93)))
                    msg += f"{medals[i]} <b>{sym}</b> {chg:+.1f}% | Fiyat: {akilli_fiyat(prc)}\n"
                msg += "\n👉 <i>En çok kazandıran coinler listelendi.</i>"
                png = render_card_png("GUNUN EN IYI HAREKETLERI", "24 SAAT SPOT HACIM TEYITLI", rows, f"TSI {simdi().strftime('%d.%m %H:%M')}")
                tg_photo(C, png, msg)
                _LAST_REPORTS["TOP_GAINERS"] = simdi().strftime("%H:%M:%S")
            time.sleep(TOP_GAINERS_ARALIGI)
        except Exception:
            time.sleep(60)


def pump_winner_loop():
    log("PUMP_WINNER", "Gunun Kazanani Motoru Basladi.")
    time.sleep(20)
    while True:
        try:
            tickers = get_futures_tickers(10_000_000)
            if not tickers:
                time.sleep(300)
                continue
            tickers.sort(key=lambda x: float(x["priceChangePercent"]), reverse=True)
            winner = tickers[0]
            symbol = winner["symbol"]
            if float(winner["priceChangePercent"]) < 15:
                time.sleep(PUMP_WINNER_ARALIGI)
                continue
            ks = get_klines(symbol, "1h", 30, market="futures")
            ks_1d = get_klines(symbol, "1d", 1, market="futures")
            if not ks or not ks_1d:
                time.sleep(PUMP_WINNER_ARALIGI)
                continue
            day_low, day_high = float(ks_1d[0][3]), float(ks_1d[0][2])
            max_move = ((day_high - day_low) / day_low) * 100
            msg = f"🏆 <b>GÜNÜN KAZANANI: {symbol}</b>\n\n📉 <b>Dip:</b> {akilli_fiyat(day_low)} | 🚀 <b>Zirve:</b> {akilli_fiyat(day_high)} (<b>+{max_move:.1f}%</b>)"
            png = render_chart_png(symbol, "1H", f"+{max_move:.1f}% KAZANAN", ks, hlines=[(day_high, "ZIRVE", (14, 203, 129)), (day_low, "DIP", (246, 70, 93))])
            tg_photo(C, png, msg)
            time.sleep(PUMP_WINNER_ARALIGI)
        except Exception:
            time.sleep(60)


def dominans_loop():
    log("DOMINANS", "Piyasa Dominans Motoru Basladi.")
    time.sleep(25)
    while True:
        try:
            res = gj(f"{COINGECKO}/global", "coingecko")
            data = res.get("data", {}) if isinstance(res, dict) else {}
            if not data:
                time.sleep(600)
                continue
            pcts = data.get("market_cap_percentage", {})
            btc, eth, usdt = float(pcts.get("btc", 0)), float(pcts.get("eth", 0)), float(pcts.get("usdt", 0))
            others = max(0.0, 100.0 - btc - eth - usdt)
            mcap_chg = float(data.get("market_cap_change_percentage_24h_usd", 0))
            rows = [("BTC DOMINANS", "", btc, (245, 166, 35)), ("ETH DOMINANS", "", eth, (98, 126, 234)), ("USDT DOMINANS", "", usdt, (38, 161, 123)), ("ALTCOINLER", "", others, (0, 210, 140))]
            msg = f"🌍 <b>KÜRESEL PİYASA DOMİNANSI</b>\n₿ BTC: %{btc:.2f} | ♦️ ETH: %{eth:.2f} | 💵 USDT: %{usdt:.2f} | 🪙 Alt: %{others:.2f} (24s: {mcap_chg:+.2f}%)"
            png = render_card_png("KURESEL PIYASA DOMINANSI", f"24S DEGISIM: {mcap_chg:+.2f}%", rows, f"TSI {simdi().strftime('%d.%m %H:%M')}")
            tg_photo(C, png, msg)
            _LAST_REPORTS["DOMINANS"] = simdi().strftime("%H:%M:%S")
            time.sleep(DOMINANS_ARALIGI)
        except Exception:
            time.sleep(300)


def chartist_loop():
    log("CHARTIST", "Spot EMA200 Motoru Basladi.")
    time.sleep(30)
    while True:
        try:
            tickers = get_spot_tickers(MIN_HACIM_CHARTIST)[:MAX_TARAMA]
            for t in tickers:
                symbol, live_price = t["symbol"], float(t["lastPrice"])
                for tf in ["8h", "12h", "1d", "3d"]:
                    ck = f"CH_{tf}_{symbol}"
                    if ck in son_sinyaller and time.time() - son_sinyaller[ck] < 86400:
                        continue
                    ks = get_klines(symbol, tf, 260, market="spot")
                    if not ks or len(ks) < 205 or not _bar_fresh(ks[-2], _interval_ms(tf), 4 * 3_600_000):
                        continue
                    closes = [float(k[4]) for k in ks[:-1]]
                    curr, prev = closes[-1], closes[-2]
                    ema200 = calculate_ema(closes, 200)
                    if len(ema200) >= 2 and prev <= ema200[-2] and curr > ema200[-1] * 1.01 and live_price > ema200[-1]:
                        vols = [float(k[7]) for k in ks[:-1]]
                        if vols[-1] > sum(vols[-21:-1]) / 20:
                            sl, tp = ema200[-1] * 0.97, curr * 1.15
                            msg = f"📈 <b>UZUN VADELİ SPOT (LONG): {symbol}</b> ({tf.upper()} EMA200)\n💰 Giriş: {akilli_fiyat(curr)} | 🎯 Hedef: {akilli_fiyat(tp)} | 🛑 SL: {akilli_fiyat(sl)}"
                            png = render_chart_png(symbol, tf.upper(), "SPOT EMA200 LONG", ks, hlines=[(tp, "TP", (14, 203, 129)), (curr, "GIRIS", (90, 165, 255)), (sl, "SL", (246, 70, 93))], overlays=[(_ema_series([float(k[4]) for k in ks], 200), (245, 166, 35))])
                            if tg_photo(C, png, msg):
                                son_sinyaller[ck] = time.time()
                                record_tracked_signal(symbol, "LONG", f"CHARTIST_{tf.upper()}", live_price, market="spot")
                    time.sleep(0.05)
            time.sleep(CHARTIST_ARALIGI)
        except Exception:
            time.sleep(60)


def pump_loop():
    log("PUMP", "Anlik Pump Avcisi Basladi.")
    time.sleep(45)
    while True:
        try:
            tickers = get_futures_tickers(MIN_HACIM_PUMP)[:MAX_TARAMA]
            for t in tickers:
                symbol = t["symbol"]
                if symbol in son_pump_sinyalleri and time.time() - son_pump_sinyalleri[symbol] < 900:
                    continue
                ks = get_klines(symbol, "5m", 45, market="futures")
                if not ks or len(ks) < 30 or not _bar_fresh(ks[-2], 300_000, 600_000):
                    continue
                cc, pc = float(ks[-2][4]), float(ks[-3][4])
                cv, pv = float(ks[-2][7]), float(ks[-3][7])
                if pc <= 0 or pv <= 0:
                    continue
                p_chg, v_chg = ((cc - pc) / pc) * 100, ((cv - pv) / pv) * 100
                if p_chg >= 3.0 and v_chg >= 100.0 and (cv - pv) >= 50_000:
                    radar = trap_radar_check(symbol)
                    msg = f"🚀 <b>Pump - {symbol}</b>\n💰 Fiyat: {akilli_fiyat(cc)} (<b>{p_chg:+.2f}%</b>)\n📊 Hacim Artışı: +{v_chg:.0f}%\n{radar}"
                    png = render_chart_png(symbol, "5M", f"PUMP {p_chg:+.1f}%", ks, hlines=[(cc, "FIYAT", (14, 203, 129))])
                    if tg_photo(C, png, msg):
                        son_pump_sinyalleri[symbol] = time.time()
                        record_tracked_signal(symbol, "LONG", "PUMP_5M", cc, market="futures")
                time.sleep(0.05)
            time.sleep(PUMP_ARALIGI)
        except Exception:
            time.sleep(60)


def volatilite_loop():
    log("VOLATILITE", "Anormal Volatilite Motoru Basladi (15m).")
    time.sleep(55)
    while True:
        try:
            tickers = get_futures_tickers(MIN_HACIM_VOLATILITE)[:MAX_TARAMA]
            for t in tickers:
                symbol = t["symbol"]
                if symbol in son_volatilite_sinyalleri and time.time() - son_volatilite_sinyalleri[symbol] < 1800:
                    continue
                ks = get_klines(symbol, "15m", 45, market="futures")
                if not ks or len(ks) < 25 or not _bar_fresh(ks[-2], 900_000, 1_800_000):
                    continue
                co, cc, cv = float(ks[-2][1]), float(ks[-2][4]), float(ks[-2][7])
                av = sum(float(k[7]) for k in ks[-22:-2]) / 20
                if co <= 0 or av <= 0:
                    continue
                pct, vm = ((cc - co) / co) * 100, cv / av
                if abs(pct) >= 2.5 and vm >= 3.0:
                    yon_tag = "SHORT" if pct < 0 else "LONG"
                    accent = (246, 70, 93) if pct < 0 else (14, 203, 129)
                    msg = f"⚡ <b>ANORMAL VOLATİLİTE: {symbol}</b> ({yon_tag})\n📊 Değişim: <b>{pct:+.2f}%</b> | Hacim: {vm:.1f}x | 💰 {akilli_fiyat(cc)}"
                    png = render_chart_png(symbol, "15M", f"{yon_tag} {pct:+.1f}%", ks, hlines=[(cc, "KAPANIS", accent)], accent=accent)
                    if tg_photo(C, png, msg):
                        son_volatilite_sinyalleri[symbol] = time.time()
                        record_tracked_signal(symbol, yon_tag, "VOLATILITE_15M", cc, market="futures")
                time.sleep(0.05)
            time.sleep(VOLATILITE_ARALIGI)
        except Exception:
            time.sleep(60)


def formasyon_loop():
    log("FORMASYON", "Teyitli Formasyon ve Likidite Avcisi Basladi.")
    time.sleep(60)
    while True:
        try:
            tickers = get_futures_tickers(MIN_HACIM_FORMASYON)[:MAX_TARAMA]
            for t in tickers:
                symbol = t["symbol"]
                if symbol in son_formasyon_sinyalleri and time.time() - son_formasyon_sinyalleri[symbol] < 86400:
                    continue
                k5 = get_klines(symbol, "5m", 100, market="futures")
                k15 = get_klines(symbol, "15m", 50, market="futures")
                if not k5 or not k15 or len(k5) < 80 or len(k15) < 40:
                    continue
                highs, lows, closes, vols = [float(k[2]) for k in k5], [float(k[3]) for k in k5], [float(k[4]) for k in k5], [float(k[7]) for k in k5]
                c15 = [float(k[4]) for k in k15]
                e11, e21, rsi = calculate_ema(c15, 11)[-1], calculate_ema(c15, 21)[-1], calculate_rsi(c15)
                cp, avg_v = closes[-1], (sum(vols[-21:-1]) / 20 or 1.0)
                if vols[-1] <= avg_v * 1.5:
                    continue
                ph, pl = find_pivots(highs, lows)
                if len(ph) < 3 or len(pl) < 3:
                    continue
                lh, ll = ph[-3:], pl[-3:]
                sinyal, fname, yon, hedef, stop = False, "", "", 0.0, 0.0
                if abs(lh[0][1] - lh[2][1]) / lh[0][1] < 0.03 and cp < ll[-1][1] and rsi < 50 and e11 < e21:
                    sinyal, fname, yon, hedef, stop = True, "Çift Tepe (M)", "SHORT", ll[-1][1] - (lh[1][1] - ll[-1][1]), lh[2][1] * 1.02
                elif abs(ll[0][1] - ll[2][1]) / ll[0][1] < 0.03 and cp > lh[-1][1] and rsi > 50 and e11 > e21:
                    sinyal, fname, yon, hedef, stop = True, "Çift Dip (W)", "LONG", lh[-1][1] + (lh[-1][1] - ll[1][1]), ll[2][1] * 0.98
                elif lh[1][1] > lh[0][1] and lh[1][1] > lh[2][1] and abs(lh[0][1] - lh[2][1]) / lh[0][1] < 0.05 and cp < ll[-1][1] and rsi < 50 and e11 < e21:
                    sinyal, fname, yon, hedef, stop = True, "Omuz-Baş-Omuz", "SHORT", ll[-1][1] - (lh[1][1] - ll[-1][1]), lh[2][1] * 1.02
                elif highs[-2] > max(highs[-20:-2]) and cp < max(highs[-20:-2]) and rsi < 50 and e11 < e21:
                    sinyal, fname, yon, hedef, stop = True, "Likidite Avı (Sweep)", "SHORT", cp * 0.97, highs[-2] * 1.01
                elif lows[-2] < min(lows[-20:-2]) and cp > min(lows[-20:-2]) and rsi > 50 and e11 > e21:
                    sinyal, fname, yon, hedef, stop = True, "Likidite Avı (Sweep)", "LONG", cp * 1.03, lows[-2] * 0.99
                if not sinyal:
                    h20, l20 = max(highs[-20:]), min(lows[-20:])
                    rng = ((h20 - l20) / l20) * 100
                    if rng < 5 and cp > h20 and rsi > 50:
                        sinyal, fname, yon, hedef, stop = True, "Sıkışma (Bayrak)", "LONG", cp + (h20 - l20), l20 * 0.98
                    elif rng < 5 and cp < l20 and rsi < 50:
                        sinyal, fname, yon, hedef, stop = True, "Sıkışma (Ters Bayrak)", "SHORT", cp - (h20 - l20), h20 * 1.02
                if sinyal:
                    hp, sp = ((hedef - cp) / cp) * 100, ((stop - cp) / cp) * 100
                    if (yon == "SHORT" and hedef >= cp) or (yon == "LONG" and hedef <= cp) or abs(sp) > 10.0 or not (1.0 <= abs(hp) <= 20.0):
                        continue
                    accent = (14, 203, 129) if yon == "LONG" else (246, 70, 93)
                    msg = f"{'🚀' if yon == 'LONG' else '🔻'} <b>TEYİTLİ DÖNÜŞ ({yon}): {symbol}</b>\n<i>5m {fname}</i>\n💰 Giriş: {akilli_fiyat(cp)} | 🎯 {akilli_fiyat(hedef)} ({hp:+.1f}%) | 🛑 {akilli_fiyat(stop)} ({sp:+.1f}%)"
                    png = render_chart_png(symbol, "5M", f"{yon} {fname}", k5, hlines=[(hedef, "TP", (14, 203, 129)), (cp, "GIRIS", (90, 165, 255)), (stop, "SL", (246, 70, 93))], accent=accent)
                    if tg_photo(C, png, msg):
                        son_formasyon_sinyalleri[symbol] = time.time()
                        record_tracked_signal(symbol, yon, "FORMASYON_5M", cp, market="futures")
                time.sleep(0.06)
            time.sleep(FORMASYON_ARALIGI)
        except Exception:
            time.sleep(60)


def ichimoku_loop():
    log("ICHIMOKU", "Ichimoku Bulut Motoru Basladi.")
    time.sleep(75)
    while True:
        try:
            tickers = get_futures_tickers(MIN_HACIM_ICHIMOKU)[:MAX_TARAMA]
            for t in tickers:
                symbol, live_price = t["symbol"], float(t["lastPrice"])
                if symbol in son_ichimoku_sinyalleri and time.time() - son_ichimoku_sinyalleri[symbol] < 14400:
                    continue
                ks = get_klines(symbol, "1h", 100, market="futures")
                if not ks or len(ks) < 85 or not _bar_fresh(ks[-2], 3_600_000, 5_400_000):
                    continue
                hi, lo, cl, vl = [float(k[2]) for k in ks], [float(k[3]) for k in ks], [float(k[4]) for k in ks], [float(k[7]) for k in ks]
                idx = len(cl) - 2
                if vl[idx] <= sum(vl[-21:-1]) / 20:
                    continue
                t_c = (max(hi[idx - 8 : idx + 1]) + min(lo[idx - 8 : idx + 1])) / 2
                k_c = (max(hi[idx - 25 : idx + 1]) + min(lo[idx - 25 : idx + 1])) / 2
                sa_c = ((max(hi[idx - 34 : idx - 25]) + min(lo[idx - 34 : idx - 25])) / 2 + (max(hi[idx - 51 : idx - 25]) + min(lo[idx - 51 : idx - 25])) / 2) / 2
                sb_c = (max(hi[idx - 77 : idx - 26]) + min(lo[idx - 77 : idx - 26])) / 2
                top_c, bot_c = max(sa_c, sb_c), min(sa_c, sb_c)
                thick, cc, pc = top_c - bot_c, cl[idx], cl[idx - 1]
                sinyal, yon, hedef, stop = False, "", 0.0, 0.0
                if pc <= top_c and cc > top_c and live_price > top_c and t_c > k_c and thick > cc * 0.005:
                    sinyal, yon, stop, hedef = True, "LONG", min(k_c, bot_c) * 0.99, cc + thick * 2
                elif pc >= bot_c and cc < bot_c and live_price < bot_c and t_c < k_c and thick > cc * 0.005:
                    sinyal, yon, stop, hedef = True, "SHORT", max(k_c, top_c) * 1.01, cc - thick * 2
                if sinyal and abs((hedef - cc) / cc) * 100 >= 1.0:
                    accent = (14, 203, 129) if yon == "LONG" else (246, 70, 93)
                    msg = f"{'🚀' if yon == 'LONG' else '🔻'} <b>ICHIMOKU ({yon}): {symbol}</b>\n💰 {akilli_fiyat(cc)} | 🎯 {akilli_fiyat(hedef)} | 🛑 {akilli_fiyat(stop)}"
                    png = render_chart_png(symbol, "1H", f"ICHIMOKU {yon}", ks, hlines=[(hedef, "TP", (14, 203, 129)), (top_c, "KUMO+", (245, 166, 35)), (bot_c, "KUMO-", (245, 166, 35)), (stop, "SL", (246, 70, 93))], accent=accent)
                    if tg_photo(C, png, msg):
                        son_ichimoku_sinyalleri[symbol] = time.time()
                        record_tracked_signal(symbol, yon, "ICHIMOKU_1H", live_price, market="futures")
                time.sleep(0.06)
            time.sleep(ICHIMOKU_ARALIGI)
        except Exception:
            time.sleep(60)


def ema_ribbon_loop():
    log("EMA_RIBBON", "EMA Dizilim Motoru Basladi.")
    time.sleep(90)
    while True:
        try:
            tickers = get_futures_tickers(MIN_HACIM_EMA_RIBBON)[:MAX_TARAMA]
            for t in tickers:
                symbol, live_price = t["symbol"], float(t["lastPrice"])
                if symbol in son_ema_ribbon_sinyalleri and time.time() - son_ema_ribbon_sinyalleri[symbol] < 86400:
                    continue
                ks = get_klines(symbol, "1d", 220, market="futures")
                if not ks or len(ks) < 205 or not _bar_fresh(ks[-2], 86_400_000, 7_200_000):
                    continue
                cl, vl = [float(k[4]) for k in ks[:-1]], [float(k[7]) for k in ks[:-1]]
                curr = cl[-1]
                if vl[-1] <= (sum(vl[-21:-1]) / 20 or 1.0) * 1.2:
                    continue
                e7, e25, e99, e200 = calculate_ema(cl, 7)[-1], calculate_ema(cl, 25)[-1], calculate_ema(cl, 99)[-1], calculate_ema(cl, 200)[-1]
                if curr > e7 > e25 > e99 > e200 and live_price > e25:
                    tp, sl = curr * 1.20, e25 * 0.95
                    msg = f"🚀 <b>EMA DİZİLİMİ (LONG): {symbol}</b>\n💰 {akilli_fiyat(curr)} | 🎯 {akilli_fiyat(tp)} (+20%) | 🛑 {akilli_fiyat(sl)}"
                    full_c = [float(k[4]) for k in ks]
                    png = render_chart_png(symbol, "1D", "EMA RIBBON LONG", ks, hlines=[(tp, "TP", (14, 203, 129)), (sl, "SL", (246, 70, 93))], overlays=[(_ema_series(full_c, 7), (14, 203, 129)), (_ema_series(full_c, 25), (90, 165, 255)), (_ema_series(full_c, 200), (245, 166, 35))])
                    if tg_photo(C, png, msg):
                        son_ema_ribbon_sinyalleri[symbol] = time.time()
                        record_tracked_signal(symbol, "LONG", "EMA_RIBBON_1D", live_price, market="futures")
                time.sleep(0.06)
            time.sleep(EMA_RIBBON_ARALIGI)
        except Exception:
            time.sleep(60)


def macd_loop():
    log("MACD", "MACD Motoru Basladi.")
    time.sleep(105)
    while True:
        try:
            tickers = get_futures_tickers(MIN_HACIM_MACD)[:MAX_TARAMA]
            for t in tickers:
                symbol, live_price = t["symbol"], float(t["lastPrice"])
                if symbol in son_macd_sinyalleri and time.time() - son_macd_sinyalleri[symbol] < 14400:
                    continue
                ks = get_klines(symbol, "1d", 100, market="futures")
                if not ks or len(ks) < 80 or not _bar_fresh(ks[-2], 86_400_000, 7_200_000):
                    continue
                cl, vl = [float(k[4]) for k in ks[:-1]], [float(k[7]) for k in ks[:-1]]
                if vl[-1] <= (sum(vl[-21:-1]) / 20) * 1.1:
                    continue
                res = calculate_macd(cl)
                if not res:
                    continue
                cm, cs, pm, ps = res
                if pm <= ps and cm > cs and live_price >= cl[-1] * 0.98:
                    cp = cl[-1]
                    tp, sl = cp * 1.10, cp * 0.97
                    msg = f"🚀 <b>MACD GOLDEN CROSS (LONG): {symbol}</b>\n💰 {akilli_fiyat(cp)} | 🎯 {akilli_fiyat(tp)} (+10%) | 🛑 {akilli_fiyat(sl)}"
                    full_c = [float(k[4]) for k in ks]
                    png = render_chart_png(symbol, "1D", "MACD CROSS LONG", ks, hlines=[(tp, "TP", (14, 203, 129)), (cp, "GIRIS", (90, 165, 255)), (sl, "SL", (246, 70, 93))], overlays=[(_ema_series(full_c, 12), (14, 203, 129)), (_ema_series(full_c, 26), (245, 166, 35))])
                    if tg_photo(C, png, msg):
                        son_macd_sinyalleri[symbol] = time.time()
                        record_tracked_signal(symbol, "LONG", "MACD_1D", live_price, market="futures")
                time.sleep(0.06)
            time.sleep(MACD_ARALIGI)
        except Exception:
            time.sleep(60)


def sma200_loop():
    log("SMA200", "200 Gunluk SMA Kirilim Motoru Basladi.")
    time.sleep(120)
    while True:
        try:
            tickers = get_futures_tickers(MIN_HACIM_SMA200)[:MAX_TARAMA]
            for t in tickers:
                symbol, live_price = t["symbol"], float(t["lastPrice"])
                if symbol in son_sma200_sinyalleri and time.time() - son_sma200_sinyalleri[symbol] < 86400:
                    continue
                ks = get_klines(symbol, "1d", 230, market="futures")
                if not ks or len(ks) < 205 or not _bar_fresh(ks[-2], 86_400_000, 7_200_000):
                    continue
                cl = [float(k[4]) for k in ks[:-1]]
                smas = calculate_sma(cl, 200)
                if len(smas) < 2:
                    continue
                curr_c, prev_c, sma_c, sma_p = cl[-1], cl[-2], smas[-1], smas[-2]
                full_c = [float(k[4]) for k in ks]
                sma_live = sum(full_c[-200:]) / 200
                yon = ""
                if prev_c <= sma_p and curr_c > sma_c and live_price > max(sma_c, sma_live):
                    yon = "LONG"
                elif prev_c >= sma_p and curr_c < sma_c and live_price < min(sma_c, sma_live):
                    yon = "SHORT"
                if yon:
                    fark = ((curr_c - sma_c) / sma_c) * 100
                    accent = (14, 203, 129) if yon == "LONG" else (246, 70, 93)
                    msg = f"{'🚀' if yon == 'LONG' else '🔻'} <b>200 GÜNLÜK SMA KIRILIMI ({yon}): {symbol}</b>\n💰 Kapanış: {akilli_fiyat(curr_c)} | Canlı: {akilli_fiyat(live_price)}\n📊 200 SMA: {akilli_fiyat(sma_c)} ({fark:+.2f}%)"
                    png = render_chart_png(symbol, "1D", f"200 SMA {yon}", ks, hlines=[(sma_c, "SMA200", (245, 166, 35))], overlays=[(_sma_series(full_c, 200), (245, 166, 35))], accent=accent)
                    if tg_photo(C, png, msg):
                        son_sma200_sinyalleri[symbol] = time.time()
                        record_tracked_signal(symbol, yon, "SMA200_1D", live_price, market="futures")
                time.sleep(0.06)
            time.sleep(SMA200_ARALIGI)
        except Exception:
            time.sleep(60)


def ath_atl_loop():
    log("ATH_ATL", "Tarihi Zirve/Dip Kirilim Motoru Basladi.")
    time.sleep(135)
    while True:
        try:
            tickers = get_futures_tickers(MIN_HACIM_ATH_ATL)[:MAX_TARAMA]
            for t in tickers:
                symbol, live_price = t["symbol"], float(t["lastPrice"])
                if symbol in son_ath_atl_sinyalleri and time.time() - son_ath_atl_sinyalleri[symbol] < 86400:
                    continue
                ks = get_klines(symbol, "1d", 500, market="futures")
                if not ks or len(ks) < 120 or not _bar_fresh(ks[-2], 86_400_000, 7_200_000):
                    continue
                hi, lo, cl, vl = [float(k[2]) for k in ks[:-2]], [float(k[3]) for k in ks[:-2]], [float(k[4]) for k in ks[:-1]], [float(k[7]) for k in ks[:-1]]
                if vl[-1] <= (sum(vl[-21:-1]) / 20) * 1.2:
                    continue
                ath, atl, cp, pc = max(hi), min(lo), cl[-1], cl[-2]
                yon, level, tp, sl = "", 0.0, 0.0, 0.0
                if pc <= ath and cp > ath and live_price > ath:
                    yon, level, tp, sl = "LONG", ath, cp * 1.20, ath * 0.95
                elif pc >= atl and cp < atl and live_price < atl:
                    yon, level, tp, sl = "SHORT", atl, cp * 0.80, atl * 1.05
                if yon:
                    accent = (14, 203, 129) if yon == "LONG" else (246, 70, 93)
                    msg = f"{'🚀' if yon == 'LONG' else '💀'} <b>TARİHİ SEVİYE KIRILIMI ({yon}): {symbol}</b>\n💰 Giriş: {akilli_fiyat(cp)} | 📊 {akilli_fiyat(level)} | 🎯 {akilli_fiyat(tp)}"
                    png = render_chart_png(symbol, "1D", f"{'ATH' if yon == 'LONG' else 'ATL'} {yon}", ks, hlines=[(tp, "TP", (14, 203, 129)), (level, "SEVIYE", (245, 166, 35)), (sl, "SL", (246, 70, 93))], accent=accent)
                    if tg_photo(C, png, msg):
                        son_ath_atl_sinyalleri[symbol] = time.time()
                        record_tracked_signal(symbol, yon, "ATH_ATL_1D", live_price, market="futures")
                time.sleep(0.06)
            time.sleep(ATH_ATL_ARALIGI)
        except Exception:
            time.sleep(60)


def chart_patterns_loop():
    log("PATTERN", "Grafik Formasyon Motoru Basladi (4h/1d).")
    time.sleep(150)
    while True:
        try:
            tickers = get_futures_tickers(MIN_HACIM_PATTERN)[:MAX_TARAMA]
            for t in tickers:
                symbol = t["symbol"]
                if symbol in son_chart_pattern_sinyalleri and time.time() - son_chart_pattern_sinyalleri[symbol] < 14400:
                    continue
                for tf in ["4h", "1d"]:
                    ks = get_klines(symbol, tf, 100, market="futures")
                    if not ks or len(ks) < 50 or not _bar_fresh(ks[-2], _interval_ms(tf), 4 * 3_600_000):
                        continue
                    hi, lo, cl, vl = [float(k[2]) for k in ks], [float(k[3]) for k in ks], [float(k[4]) for k in ks], [float(k[7]) for k in ks]
                    cc = cl[-2]
                    if vl[-2] < (sum(vl[-22:-2]) / 20) * 1.2:
                        continue
                    ph, pl = find_pivots(hi, lo)
                    sinyal, fname, yon, hedef, stop, neck = False, "", "", 0.0, 0.0, 0.0
                    if len(ph) >= 2:
                        i1, p1 = ph[-2]
                        i2, p2 = ph[-1]
                        if 5 <= i2 - i1 <= 30 and abs(p1 - p2) / p1 <= 0.02:
                            neck = min(lo[i1 : i2 + 1])
                            if cc < neck * 0.99:
                                sinyal, fname, yon, stop, hedef = True, "İkili Tepe (M)", "SHORT", max(p1, p2) * 1.02, neck - (max(p1, p2) - neck)
                    if not sinyal and len(pl) >= 2:
                        i1, p1 = pl[-2]
                        i2, p2 = pl[-1]
                        if 5 <= i2 - i1 <= 30 and abs(p1 - p2) / p1 <= 0.02:
                            neck = max(hi[i1 : i2 + 1])
                            if cc > neck * 1.01:
                                sinyal, fname, yon, stop, hedef = True, "İkili Dip (W)", "LONG", min(p1, p2) * 0.98, neck + (neck - min(p1, p2))
                    if not sinyal and len(ph) >= 3:
                        i1, p1 = ph[-3]
                        i2, p2 = ph[-2]
                        i3, p3 = ph[-1]
                        if p2 > p1 and p2 > p3 and abs(p1 - p3) / p1 <= 0.03:
                            neck = min(lo[i1 : i3 + 1])
                            if cc < neck * 0.99:
                                sinyal, fname, yon, stop, hedef = True, "Omuz-Baş-Omuz", "SHORT", p3 * 1.02, neck - (p2 - neck)
                    if not sinyal and len(pl) >= 3:
                        i1, p1 = pl[-3]
                        i2, p2 = pl[-2]
                        i3, p3 = pl[-1]
                        if p2 < p1 and p2 < p3 and abs(p1 - p3) / p1 <= 0.03:
                            neck = max(hi[i1 : i3 + 1])
                            if cc > neck * 1.01:
                                sinyal, fname, yon, stop, hedef = True, "Ters Omuz-Baş-Omuz", "LONG", p3 * 0.98, neck + (neck - p2)
                    if sinyal:
                        hp, sp = ((hedef - cc) / cc) * 100, ((stop - cc) / cc) * 100
                        if (yon == "SHORT" and hedef >= cc) or (yon == "LONG" and hedef <= cc) or abs(sp) > 15.0 or not (1.5 <= abs(hp) <= 40.0):
                            continue
                        accent = (14, 203, 129) if yon == "LONG" else (246, 70, 93)
                        msg = f"{'🚀' if yon == 'LONG' else '🔻'} <b>GRAFİK FORMASYONU ({yon}): {symbol}</b>\n<i>{tf.upper()} - {fname}</i>\n💰 {akilli_fiyat(cc)} | 🎯 {akilli_fiyat(hedef)} ({hp:+.1f}%) | 🛑 {akilli_fiyat(stop)} ({sp:+.1f}%)"
                        png = render_chart_png(symbol, tf.upper(), f"{yon} {fname}", ks, hlines=[(hedef, "TP", (14, 203, 129)), (neck, "BOYUN", (245, 166, 35)), (stop, "SL", (246, 70, 93))], accent=accent)
                        if tg_photo(C, png, msg):
                            son_chart_pattern_sinyalleri[symbol] = time.time()
                            record_tracked_signal(symbol, yon, f"PATTERN_{tf.upper()}", cc, market="futures")
                            break
                time.sleep(0.06)
            time.sleep(CHART_PATTERN_ARALIGI)
        except Exception:
            time.sleep(60)


def anlik_ath_atl_loop():
    log("ANLIK_ATH_ATL", "Canli Anlik ATH/ATL Motoru Basladi.")
    time.sleep(160)
    while True:
        try:
            tickers = get_futures_tickers(MIN_HACIM_ANLIK_ATH)[:MAX_TARAMA]
            for t in tickers:
                symbol, live_price = t["symbol"], float(t["lastPrice"])
                if symbol in son_anlik_ath_atl and time.time() - son_anlik_ath_atl[symbol] < 21600:
                    continue
                if symbol not in anlik_ath_cache or symbol not in anlik_atl_cache:
                    ks_hist = get_klines(symbol, "1d", 500, market="futures")
                    if not ks_hist or len(ks_hist) < 3:
                        continue
                    anlik_ath_cache[symbol] = max(float(k[2]) for k in ks_hist[:-1])
                    anlik_atl_cache[symbol] = min(float(k[3]) for k in ks_hist[:-1])
                ath, atl = anlik_ath_cache[symbol], anlik_atl_cache[symbol]
                yon = ""
                if live_price > ath:
                    yon, level = "LONG", ath
                    anlik_ath_cache[symbol] = live_price
                elif live_price < atl:
                    yon, level = "SHORT", atl
                    anlik_atl_cache[symbol] = live_price
                if yon:
                    ks_draw = get_klines(symbol, "1d", 60, market="futures") or []
                    chg, qv = float(t.get("priceChangePercent", 0)), float(t.get("quoteVolume", 0)) / 1_000_000
                    accent = (14, 203, 129) if yon == "LONG" else (246, 70, 93)
                    title = "ALL TIME HIGH!" if yon == "LONG" else "ALL TIME LOW!"
                    msg = f"{'🚀' if yon == 'LONG' else '💀'} <b>#{symbol} ${akilli_fiyat(live_price)} {title}</b>\n24s: {chg:+.2f}% | Hacim: {qv:.1f}M USDT"
                    png = render_chart_png(symbol, "1D", "ANLIK ATH" if yon == "LONG" else "ANLIK ATL", ks_draw, hlines=[(level, "ONCEKI", (245, 166, 35)), (live_price, "CANLI", accent)], accent=accent)
                    if tg_photo(C, png, msg):
                        son_anlik_ath_atl[symbol] = time.time()
                        record_tracked_signal(symbol, yon, "ANLIK_ATH_ATL", live_price, market="futures")
                time.sleep(0.04)
            time.sleep(ANLIK_ATH_ATL_ARALIGI)
        except Exception:
            time.sleep(60)


def top_movers_loop():
    log("TOP_MOVERS", "Saatlik En Cok Hareket Edenler Motoru Basladi.")
    time.sleep(170)
    while True:
        try:
            tickers = get_futures_tickers(MIN_HACIM_MOVERS)[:MAX_TARAMA]
            res = []
            for t in tickers:
                ks = get_klines(t["symbol"], "1h", 3, market="futures")
                if not ks or len(ks) < 2 or not _bar_fresh(ks[-2], 3_600_000, 3_600_000):
                    continue
                op, cl = float(ks[-2][1]), float(ks[-2][4])
                if op > 0:
                    res.append((t["symbol"], ((cl - op) / op) * 100, cl))
                time.sleep(0.04)
            if len(res) >= 6:
                res.sort(key=lambda x: x[1], reverse=True)
                up, down = res[:3], list(reversed(res[-3:]))
                rows = [(f"UP {s.replace('USDT','')}", akilli_fiyat(p), c, (14, 203, 129)) for s, c, p in up]
                rows += [(f"DN {s.replace('USDT','')}", akilli_fiyat(p), c, (246, 70, 93)) for s, c, p in down]
                msg = "⏱️ <b>Son 1 Saatin Hareketlileri</b>\n\n🟢 <b>En Çok Yükselenler:</b>\n"
                for i, (s, c, p) in enumerate(up, 1):
                    msg += f"{i}. <b>{s.replace('USDT','')}</b> {c:+.2f}% | {akilli_fiyat(p)}\n"
                msg += "\n🔴 <b>En Çok Düşenler:</b>\n"
                for i, (s, c, p) in enumerate(down, 1):
                    msg += f"{i}. <b>{s.replace('USDT','')}</b> {c:+.2f}% | {akilli_fiyat(p)}\n"
                png = render_card_png("SON 1 SAATIN HAREKETLILERI", "TAMAMLANMIS 1H FUTURES MUMLARI", rows, f"TSI {simdi().strftime('%d.%m %H:%M')}")
                tg_photo(C, png, msg)
            time.sleep(TOP_MOVERS_ARALIGI)
        except Exception:
            time.sleep(60)


def orderflow_fibo_loop():
    log("ORDERFLOW_FIBO", "7 Borsa Orderflow + Fibo 0.618 + VAH/VAL Motoru Basladi.")
    time.sleep(180)
    while True:
        try:
            tickers = get_futures_tickers(MIN_HACIM_ORDERFLOW)[:MAX_TARAMA]
            for t in tickers:
                symbol, live_price = t["symbol"], float(t["lastPrice"])
                if symbol in son_orderflow_fibo_sinyalleri and time.time() - son_orderflow_fibo_sinyalleri[symbol] < 21600:
                    continue
                ks = get_klines(symbol, "4h", 90, market="futures")
                if not ks or len(ks) < 40:
                    continue
                sw_hi, sw_lo = max(float(k[2]) for k in ks[-30:]), min(float(k[3]) for k in ks[-30:])
                if sw_hi <= sw_lo * 1.04:
                    continue
                diff = sw_hi - sw_lo
                fib618, fib500, fib382 = sw_hi - 0.618 * diff, sw_hi - 0.500 * diff, sw_hi - 0.382 * diff
                if abs(live_price - fib618) / fib618 > 0.015:
                    continue
                ob, vp = get_multi_orderbook_flow(symbol, live_price), calculate_volume_profile(ks[-45:])
                if not ob or not vp or ob["total_ask"] <= 0 or ob["total_bid"] < ob["total_ask"] * 1.10:
                    continue
                tight_sl = min(live_price * 0.985, ob["bid_wall_price"] * 0.996)
                cons_sl = min(tight_sl * 0.98, vp["val"] * 0.995)
                tp1, tp2, tp3 = fib500, fib382, sw_hi
                if tp1 <= live_price * 1.005:
                    continue
                p1, p2, p3 = ((tp1 - live_price) / live_price) * 100, ((tp2 - live_price) / live_price) * 100, ((tp3 - live_price) / live_price) * 100
                sl_pct, csl_pct = ((tight_sl - live_price) / live_price) * 100, ((cons_sl - live_price) / live_price) * 100
                msg = (
                    f"⚡ <b>#{symbol} (ORDERFLOW LONG)</b>\n💰 Fiyat: {akilli_fiyat(live_price)}\n"
                    f"🎯 Hedefler: {akilli_fiyat(tp1)} (+{p1:.1f}%) → {akilli_fiyat(tp2)} (+{p2:.1f}%) → {akilli_fiyat(tp3)} (+{p3:.1f}%)\n"
                    f"🛑 Stop: {akilli_fiyat(tight_sl)} ({sl_pct:.1f}%) | VAL: {akilli_fiyat(cons_sl)} ({csl_pct:.1f}%)\n"
                    f"📊 {ob['exchanges']} Borsa: Bid ${ob['total_bid']:,.0f} vs Ask ${ob['total_ask']:,.0f}"
                )
                png = render_chart_png(symbol, "4H", f"{ob['exchanges']} BORSA LONG", ks, hlines=[(tp3, "TP3 SWING", (14, 203, 129)), (tp2, "TP2 F382", (14, 203, 129)), (tp1, "TP1 F500", (14, 203, 129)), (fib618, "FIB 0.618", (90, 165, 255)), (tight_sl, "SIKI SL", (246, 70, 93)), (cons_sl, "VAL SL", (210, 60, 130))])
                if tg_photo(C, png, msg):
                    son_orderflow_fibo_sinyalleri[symbol] = time.time()
                    record_tracked_signal(symbol, "LONG", "ORDERFLOW_FIBO", live_price, market="futures")
                time.sleep(0.08)
            time.sleep(ORDERFLOW_FIBO_ARALIGI)
        except Exception:
            time.sleep(60)


def sinyal_zaferi_loop():
    log("SINYAL_ZAFERI", "Gunun Zaferi (Sinyal Performans Takip) Motoru Basladi.")
    time.sleep(190)
    while True:
        try:
            active = update_tracked_signals()
            now_ms = int(time.time() * 1000)
            candidates = [s for s in active if s["best_pct"] >= 5.0 and (now_ms - s["entry_ms"]) >= 600_000]
            if not candidates:
                time.sleep(900)
                continue
            candidates.sort(key=lambda x: x["best_pct"], reverse=True)
            win = candidates[0]
            prev_best = son_zafer_bildirimleri.get(win["id"], 0.0)
            if win["best_pct"] < prev_best + 10.0 and prev_best > 0:
                time.sleep(SINYAL_ZAFERI_ARALIGI)
                continue
            ks = get_klines(win["symbol"], "5m", 288, market=win.get("market", "futures"))
            if not ks or len(ks) < 5:
                time.sleep(900)
                continue
            start_ms = win["entry_ms"] - 3_600_000
            sub = [k for k in ks if int(float(k[0])) >= start_ms] or ks[-48:]
            entry_idx = min(range(len(sub)), key=lambda i: abs(int(float(sub[i][0])) - win["entry_ms"]))
            peak_idx = min(range(len(sub)), key=lambda i: abs(int(float(sub[i][0])) - win["peak_ms"]))
            pts = []
            for i, k in enumerate(sub):
                tag, val = "", float(k[4])
                if i == entry_idx:
                    tag, val = "entry", win["entry_price"]
                elif i == peak_idx:
                    tag, val = "peak", win["best_price"]
                pts.append((int(float(k[0])), val, tag))
            hours = max(0.1, (win["peak_ms"] - win["entry_ms"]) / 3_600_000.0)
            entry_dt = datetime.fromtimestamp(win["entry_ms"] / 1000, TR_Saati).strftime("%H:%M")
            peak_dt = datetime.fromtimestamp(win["peak_ms"] / 1000, TR_Saati).strftime("%H:%M")
            tot_cnt, hit_rate = get_tracker_stats()
            png = render_win_chart_png(win["symbol"].replace("USDT", ""), win["yon"], win["motor"], win["best_pct"], hours, win["entry_price"], win["best_price"], entry_dt, peak_dt, pts, f"{tot_cnt} SINYAL TAKIPTE | %{hit_rate:.0f} >= +2%")
            msg = f"🏆 <b>GÜNÜN ZAFERİ: {win['symbol'].replace('USDT', '')} +{win['best_pct']:.1f}%</b>\n🕒 {entry_dt} TSI ({win['motor']}) ${akilli_fiyat(win['entry_price'])} → 🚀 {peak_dt} TSI ${akilli_fiyat(win['best_price'])} ({hours:.1f}s)\n📊 <i>{tot_cnt} sinyal takipte · %{hit_rate:.0f} en az +%2 gördü</i>"
            if tg_photo(C, png, msg):
                son_zafer_bildirimleri[win["id"]] = win["best_pct"]
                _LAST_REPORTS["SINYAL_ZAFERI"] = simdi().strftime("%H:%M:%S")
            time.sleep(SINYAL_ZAFERI_ARALIGI)
        except Exception:
            time.sleep(120)


def sinyal_top24_loop():
    log("SINYAL_TOP24", "24 Saatlik Sinyal Liderlik Kart Motoru Basladi.")
    time.sleep(200)
    while True:
        try:
            active = update_tracked_signals()
            best_by_coin = {}
            for s in active:
                if s["best_pct"] >= 1.5:
                    coin = s["symbol"]
                    if coin not in best_by_coin or s["best_pct"] > best_by_coin[coin]["best_pct"]:
                        best_by_coin[coin] = s
            ranked = sorted(best_by_coin.values(), key=lambda x: x["best_pct"], reverse=True)[:5]
            if not ranked:
                time.sleep(900)
                continue
            tot_cnt, hit_rate = get_tracker_stats()
            medals = ["🥇", "🥈", "🥉", "🏅", "⭐️"]
            rows = []
            msg = "🔥 <b>SON 24 SAATİN EN İYİ SİNYAL HAREKETLERİ</b>\n\n"
            for i, s in enumerate(ranked):
                coin = s["symbol"].replace("USDT", "")
                hrs = max(0.1, (s["peak_ms"] - s["entry_ms"]) / 3_600_000.0)
                col = (14, 203, 129) if s["yon"] == "LONG" else (246, 70, 93)
                rows.append((f"#{i+1} {coin} ({s['yon']})", f"PIK {hrs:.1f}S | {s['motor'][:8]}", s["best_pct"], col))
                msg += f"{medals[i]} <b>{coin}</b> ({s['yon']}) <b>+{s['best_pct']:.1f}%</b> · pik {hrs:.1f}s ({s['motor']})\n"
            msg += f"\n📊 <i>{tot_cnt} sinyal takipte · %{hit_rate:.0f} en az +%2 gördü</i>"
            png = render_card_png("24 SAATIN EN IYI SINYALLERI", "SINYAL YONUNDE MAKSIMUM KAZANC (TAKIP)", rows, f"{tot_cnt} SINYAL TAKIPTE | %{hit_rate:.0f} >= +2%")
            if tg_photo(C, png, msg):
                _LAST_REPORTS["SINYAL_TOP24"] = simdi().strftime("%H:%M:%S")
            time.sleep(SINYAL_TOP24_ARALIGI)
        except Exception:
            time.sleep(120)


# =====================================================
# MOTOR 19: SPOT vs FUTURES CVD + OI + BULL/BEAR DIV
# =====================================================

def analyze_cvd_signal(symbol, cvd_res, oi_pct):
    ks = cvd_res["fut_klines"]
    sc = cvd_res["spot_cvd"]
    fc = cvd_res["fut_cvd"]
    cc = cvd_res["comb_cvd"]
    sd = cvd_res["spot_deltas"]
    fd = cvd_res["fut_deltas"]
    sv = cvd_res["spot_vols"]
    fv = cvd_res["fut_vols"]

    highs = [float(k[2]) for k in ks]
    lows = [float(k[3]) for k in ks]
    closes = [float(k[4]) for k in ks]
    n = len(ks)
    live_price = closes[-1]

    ph, pl = find_pivots(highs, lows, window=2)
    div_pts = None
    signal_type = ""
    yon = ""

    if len(pl) >= 2:
        i1, p1 = pl[-2]
        i2, p2 = pl[-1]
        if i2 >= n - 8 and (i2 - i1) >= 4 and p2 < p1 * 0.997:
            if cc[i2] > cc[i1] and sc[i2] >= sc[i1] and live_price >= p2 * 0.995:
                signal_type, yon = "BULL DIV", "LONG"
                div_pts = (i1, p1, i2, p2, "BULL DIV", (14, 203, 129))

    if not yon and len(ph) >= 2:
        i1, p1 = ph[-2]
        i2, p2 = ph[-1]
        if i2 >= n - 8 and (i2 - i1) >= 4 and p2 > p1 * 1.003:
            if cc[i2] < cc[i1] and sc[i2] <= sc[i1] and live_price <= p2 * 1.005:
                signal_type, yon = "BEAR DIV", "SHORT"
                div_pts = (i1, p1, i2, p2, "BEAR DIV", (246, 70, 93))

    s6, f6 = sum(sd[-6:]), sum(fd[-6:])
    v6 = sum(sv[-6:]) + sum(fv[-6:])
    net6 = s6 + f6
    if not yon and oi_pct is not None and v6 > 0:
        if s6 > 0 and f6 > 0 and net6 >= 1_500_000 and (net6 / v6) >= 0.04 and oi_pct >= 1.5:
            signal_type, yon = "BOTH BUYING + OI", "LONG"
        elif s6 < 0 and f6 < 0 and net6 <= -1_500_000 and (abs(net6) / v6) >= 0.04 and oi_pct >= 1.5:
            signal_type, yon = "BOTH SELLING + OI", "SHORT"

    if not yon:
        return None
    return {
        "symbol": symbol,
        "yon": yon,
        "signal_type": signal_type,
        "live_price": live_price,
        "s6": s6,
        "f6": f6,
        "net6": net6,
        "spot_total": sc[-1],
        "fut_total": fc[-1],
        "oi_pct": oi_pct if oi_pct is not None else 0.0,
        "div_pts": div_pts,
    }


def cvd_flow_loop():
    log("CVD_FLOW", "Spot vs Futures CVD + OI + Bull/Bear Divergence Motoru Basladi.")
    time.sleep(210)
    while True:
        try:
            tickers = get_futures_tickers(MIN_HACIM_CVD)[:40]
            for t in tickers:
                symbol = t["symbol"]
                if symbol in son_cvd_sinyalleri and time.time() - son_cvd_sinyalleri[symbol] < 14400:
                    continue
                cvd_res = get_spot_fut_cvd(symbol, limit=40)
                if not cvd_res:
                    time.sleep(0.08)
                    continue
                oi_pct = get_oi_change_pct(symbol, period="1h")
                sig = analyze_cvd_signal(symbol, cvd_res, oi_pct)
                if sig:
                    is_long = sig["yon"] == "LONG"
                    accent = (14, 203, 129) if is_long else (246, 70, 93)
                    verdict = "Yeni longlar eklendi" if is_long else "Yeni shortlar / agresif satış baskısı"
                    msg = (
                        f"{'🟢' if is_long else '🔴'} <b>#{symbol} CVD ANALİZİ ({sig['signal_type']} - {sig['yon']})</b>\n"
                        f"<i>1 Saatlik Spot vs Futures Kümülatif Hacim Deltası</i>\n\n"
                        f"💰 <b>Fiyat:</b> {akilli_fiyat(sig['live_price'])}\n"
                        f"🟠 <b>Spot CVD (6s):</b> {_kisa_usd(sig['s6'])} | Toplam: {_kisa_usd(sig['spot_total'])}\n"
                        f"🔵 <b>Futures CVD (6s):</b> {_kisa_usd(sig['f6'])} | Toplam: {_kisa_usd(sig['fut_total'])}\n"
                        f"📈 <b>Net Akış (6s):</b> <b>{_kisa_usd(sig['net6'])}</b> | <b>OI (4s):</b> {sig['oi_pct']:+.2f}%\n"
                        f"💡 <b>Yorum:</b> {verdict}."
                    )
                    png = render_cvd_chart_png(
                        symbol,
                        sig["signal_type"],
                        cvd_res["fut_klines"],
                        cvd_res["spot_cvd"],
                        cvd_res["fut_cvd"],
                        sig["oi_pct"],
                        div_pts=sig["div_pts"],
                        accent=accent,
                    )
                    if tg_photo(C, png, msg):
                        son_cvd_sinyalleri[symbol] = time.time()
                        record_tracked_signal(symbol, sig["yon"], f"CVD_{sig['signal_type'].replace(' ', '_')}", sig["live_price"], market="futures")
                time.sleep(0.12)
            time.sleep(CVD_FLOW_ARALIGI)
        except Exception as e:
            log("CVD_FLOW", f"Hata: {e}")
            time.sleep(90)


# =====================================================
# RENDER WEB SUNUCUSU
# =====================================================

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        tot_cnt, hit_rate = get_tracker_stats()
        with _STATE_LOCK:
            err_lines = "\n".join(f"  - {k}: {v}" for k, v in _SOURCE_LAST_ERROR.items()) or "  Hata yok"
        body = (
            f"Ultimate Crypto Signal Bot Aktif!\n"
            f"Motor Sayisi: 19 (Tam Gorsel Mod + 7 Borsa Orderflow + Sinyal Takip + Spot/Futures CVD)\n"
            f"Takipteki Sinyaller: {tot_cnt} (Basari >= +2%: %{hit_rate:.1f})\n"
            f"Son Raporlar -> TOP_GAINERS: {_LAST_REPORTS['TOP_GAINERS']} | DOMINANS: {_LAST_REPORTS['DOMINANS']} | ZAFER: {_LAST_REPORTS['SINYAL_ZAFERI']} | TOP24: {_LAST_REPORTS['SINYAL_TOP24']}\n"
            f"Kaynak Durumlari:\n{err_lines}\n"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string, *args):
        return


def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    log("SISTEM", f"WEB SUNUCUSU BASLADI - PORT: {port}")
    server.serve_forever()


if __name__ == "__main__":
    _load_tracked_signals()
    motors = [
        ("TOP_GAINERS", top_gainers_loop),
        ("PUMP_WINNER", pump_winner_loop),
        ("DOMINANS", dominans_loop),
        ("CHARTIST", chartist_loop),
        ("PUMP", pump_loop),
        ("VOLATILITE", volatilite_loop),
        ("FORMASYON", formasyon_loop),
        ("ICHIMOKU", ichimoku_loop),
        ("EMA_RIBBON", ema_ribbon_loop),
        ("MACD", macd_loop),
        ("SMA200", sma200_loop),
        ("ATH_ATL", ath_atl_loop),
        ("PATTERN", chart_patterns_loop),
        ("ANLIK_ATH_ATL", anlik_ath_atl_loop),
        ("TOP_MOVERS", top_movers_loop),
        ("ORDERFLOW_FIBO", orderflow_fibo_loop),
        ("SINYAL_ZAFERI", sinyal_zaferi_loop),
        ("SINYAL_TOP24", sinyal_top24_loop),
        ("CVD_FLOW", cvd_flow_loop),
    ]
    for name, fn in motors:
        threading.Thread(target=fn, name=name, daemon=True).start()
    run_web_server()

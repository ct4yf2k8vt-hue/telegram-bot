import urllib.request
import urllib.parse
import json
import time
import socket
import os
import threading
import http.server
import traceback
import math
import zlib
import struct
import uuid
from datetime import datetime, timedelta, timezone

# =====================================================
# MULTI-EXCHANGE CRYPTO SIGNAL BOT v20 (20 MOTOR + PNG + TRACKER + CVD + DCA WATCHLIST)
# Sadece Python standart kutuphaneleri kullanilir (Render uyumlu, pip gerektirmez)
# =====================================================

socket.setdefaulttimeout(20)

T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()
TR_Saati = timezone(timedelta(hours=3))

FAPI = "https://fapi.binance.com"
SPOT = "https://api.binance.com"
VISION = "https://data-api.binance.vision"
BYBIT = "https://api.bybit.com"
OKX = "https://www.okx.com"
BITGET = "https://api.bitget.com"
GATE = "https://api.gateio.ws"
MEXC = "https://api.mexc.com"
KUCOIN = "https://api.kucoin.com"
CG = "https://api.coingecko.com"

# Motor aralıkları (saniye)
TOP_GAINERS_ARALIGI = 14400
PUMP_WINNER_ARALIGI = 21600
DOMINANS_ARALIGI = 14400
CHARTIST_ARALIGI = 3600
PUMP_ARALIGI = 60
VOLATILITE_ARALIGI = 60
FORMASYON_ARALIGI = 3600
ICHIMOKU_ARALIGI = 3600
EMA_RIBBON_ARALIGI = 3600
MACD_ARALIGI = 3600
SMA200_ARALIGI = 3600
ATH_ATL_ARALIGI = 3600
CHART_PATTERN_ARALIGI = 1800
ANLIK_ATH_ATL_ARALIGI = 180
TOP_MOVERS_ARALIGI = 3600
ORDERFLOW_FIBO_ARALIGI = 900
SINYAL_ZAFERI_ARALIGI = 21600
SINYAL_TOP24_ARALIGI = 14400
CVD_FLOW_ARALIGI = 1800
DCA_WATCHLIST_ARALIGI = 14400

# Minimum 24s hacimler (USDT)
MIN_HACIM_CHARTIST = 10_000_000
MIN_HACIM_PUMP = 5_000_000
MIN_HACIM_VOLATILITE = 10_000_000
MIN_HACIM_FORMASYON = 15_000_000
MIN_HACIM_ICHIMOKU = 10_000_000
MIN_HACIM_EMA_RIBBON = 10_000_000
MIN_HACIM_MACD = 10_000_000
MIN_HACIM_SMA200 = 10_000_000
MIN_HACIM_ATH_ATL = 5_000_000
MIN_HACIM_PATTERN = 15_000_000
MIN_HACIM_ANLIK_ATH = 5_000_000
MIN_HACIM_MOVERS = 5_000_000
MIN_HACIM_ORDERFLOW = 15_000_000
MIN_HACIM_CVD = 25_000_000
MIN_HACIM_DCA = 10_000_000

MAX_TARAMA = 60

YASAKLI_HISSELER = {
    "AAPLUSDT", "TSLAUSDT", "NVDAUSDT", "COINUSDT", "MSTRUSDT",
    "SQQQUSDT", "TQQQUSDT", "SPYUSDT", "QQQUSDT", "AMZNUSDT",
    "GOOGLUSDT", "METAUSDT", "NFLXUSDT", "BABAUSDT", "MSFTUSDT",
    "HOODUSDT", "CRCLUSDT", "PLTRUSDT", "AMDUSDT", "INTCUSDT",
    "GMEUSDT", "AMCUSDT", "NVDAXUSDT", "TSLAXUSDT", "AAPLXUSDT",
    "XAUUSDT", "XAGUSDT", "PAXGUSDT", "USOILUSDT", "ORCLBUSDT"
}

# Cooldown hafızaları
son_sinyaller = {}
son_pump_sinyalleri = {}
son_volatilite_sinyalleri = {}
son_formasyon_sinyalleri = {}
son_ichimoku_sinyalleri = {}
son_ema_ribbon_sinyalleri = {}
son_macd_sinyalleri = {}
son_sma200_sinyalleri = {}
son_ath_atl_sinyalleri = {}
son_chart_pattern_sinyalleri = {}
son_anlik_ath_sinyalleri = {}
son_orderflow_sinyalleri = {}
son_cvd_sinyalleri = {}
son_dca_sinyalleri = {}

# Kaynak sağlığı ve önbellek
_api_lock = threading.Lock()
_cache_lock = threading.Lock()
_SOURCE_COOLDOWN = {}
_SOURCE_LAST_ERROR = {}
_ticker_cache = {"fut": (0.0, []), "spot": (0.0, [])}
_kline_cache = {}
_oi_cache = {}
_cvd_cache = {}

TRACKER_FILE = "/tmp/tracked_signals_v20.json"
TRACKER_LOCK = threading.Lock()
_TRACKED_SIGNALS = []


def simdi():
    return datetime.now(TR_Saati)


def log(prefix, msg):
    print(f"[{simdi().strftime('%H:%M:%S')}] [{prefix}] {msg}", flush=True)


def akilli_fiyat(p):
    if p is None:
        return "0"
    p = float(p)
    ap = abs(p)
    if ap >= 1000:
        return f"{p:.2f}"
    if ap >= 1:
        return f"{p:.4f}"
    if ap >= 0.01:
        return f"{p:.6f}"
    return f"{p:.8f}"


def fmt_usd(v):
    av = abs(float(v))
    sign = "+" if v > 0 else ("-" if v < 0 else "")
    if av >= 1_000_000_000:
        return f"{sign}${av/1_000_000_000:.2f}B"
    if av >= 1_000_000:
        return f"{sign}${av/1_000_000:.1f}M"
    if av >= 1_000:
        return f"{sign}${av/1_000:.1f}K"
    return f"{sign}${av:.0f}"


def _source_ok(name):
    return time.time() >= _SOURCE_COOLDOWN.get(name, 0.0)


def _mark_source_down(name, seconds=180, err=""):
    _SOURCE_COOLDOWN[name] = time.time() + seconds
    _SOURCE_LAST_ERROR[name] = err
    log("API", f"{name} gecici devre disi ({seconds}s): {err}")


def gj(url, source="GENERIC", cache_key=None, cache_ttl=0, headers=None, timeout=15):
    if source and not _source_ok(source):
        return None
    if cache_key and cache_ttl > 0:
        with _cache_lock:
            item = _kline_cache.get(cache_key)
            if item and time.time() - item[0] < cache_ttl:
                return item[1]
    hdrs = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if cache_key and cache_ttl > 0:
            with _cache_lock:
                _kline_cache[cache_key] = (time.time(), data)
        return data
    except urllib.error.HTTPError as e:
        if e.code in (403, 418, 429, 451):
            _mark_source_down(source, 300, f"HTTP {e.code}")
        return None
    except Exception as e:
        if source != "GENERIC":
            _mark_source_down(source, 60, str(e)[:80])
        return None


def tg(chat_id, message):
    if not T or not chat_id:
        return False
    try:
        data = urllib.parse.urlencode({
            "chat_id": str(chat_id),
            "text": message[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": "true"
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{T}/sendMessage",
            data=data,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8")).get("ok", False)
    except Exception as e:
        log("TELEGRAM", f"Mesaj hatasi: {e}")
        return False


def tg_photo(chat_id, png_bytes, caption=""):
    if not T or not chat_id or not png_bytes:
        return tg(chat_id, caption)
    cap = (caption or "")[:1020]
    boundary = "----BotBoundary" + uuid.uuid4().hex
    parts = []
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n".encode("utf-8"))
    if cap:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{cap}\r\n".encode("utf-8"))
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"parse_mode\"\r\n\r\nHTML\r\n".encode("utf-8"))
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"chart.png\"\r\n"
        f"Content-Type: image/png\r\n\r\n".encode("utf-8") + png_bytes + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{T}/sendPhoto",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
                "User-Agent": "Mozilla/5.0"
            }
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            ok = json.loads(resp.read().decode("utf-8")).get("ok", False)
            if ok:
                return True
    except Exception as e:
        log("TELEGRAM", f"Foto hatasi: {e}")
    return tg(chat_id, caption)


# =====================================================
# SAF PYTHON PNG CIZIM MOTORU (PIL / MATPLOTLIB YOK)
# =====================================================
_FONT_5X7 = {
    '0': ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    '1': ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    '2': ["01110", "10001", "00001", "00110", "01000", "10000", "11111"],
    '3': ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    '4': ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    '5': ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    '6': ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    '7': ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    '8': ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    '9': ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    'A': ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    'B': ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    'C': ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    'D': ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    'E': ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    'F': ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    'G': ["01110", "10001", "10000", "10111", "10001", "10001", "01110"],
    'H': ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    'I': ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    'J': ["00111", "00010", "00010", "00010", "10010", "10010", "01100"],
    'K': ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    'L': ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    'M': ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    'N': ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    'O': ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    'P': ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    'Q': ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    'R': ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    'S': ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    'T': ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    'U': ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    'V': ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    'W': ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
    'X': ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    'Y': ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    'Z': ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    '.': ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    ',': ["00000", "00000", "00000", "00000", "00000", "01100", "01000"],
    ':': ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
    '+': ["00000", "00100", "00100", "11111", "00100", "00100", "00000"],
    '-': ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    '/': ["00001", "00010", "00100", "00100", "01000", "10000", "00000"],
    '%': ["11001", "11010", "00100", "00100", "01011", "10011", "00000"],
    '$': ["00100", "01111", "10100", "01110", "00101", "11110", "00100"],
    '(': ["00010", "00100", "01000", "01000", "01000", "00100", "00010"],
    ')': ["01000", "00100", "00010", "00010", "00010", "00100", "01000"],
    '|': ["00100", "00100", "00100", "00100", "00100", "00100", "00100"],
    '>': ["10000", "01000", "00100", "00010", "00100", "01000", "10000"],
    '<': ["00001", "00010", "00100", "01000", "00100", "00010", "00001"],
    ' ': ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
    '_': ["00000", "00000", "00000", "00000", "00000", "00000", "11111"],
    '=': ["00000", "00000", "11111", "00000", "11111", "00000", "00000"],
}


class _Canvas:
    def __init__(self, w, h, bg=(14, 18, 28)):
        self.w = w
        self.h = h
        self.buf = bytearray(bytes(bg) * (w * h))

    def px(self, x, y, col):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.buf[i] = col[0]
            self.buf[i + 1] = col[1]
            self.buf[i + 2] = col[2]

    def rect(self, x, y, rw, rh, col):
        x0 = max(0, int(x))
        y0 = max(0, int(y))
        x1 = min(self.w, int(x + rw))
        y1 = min(self.h, int(y + rh))
        if x1 <= x0 or y1 <= y0:
            return
        row_bytes = bytes(col) * (x1 - x0)
        for py in range(y0, y1):
            s = (py * self.w + x0) * 3
            self.buf[s:s + len(row_bytes)] = row_bytes

    def line(self, x0, y0, x1, y1, col, thickness=1):
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            if thickness <= 1:
                self.px(x0, y0, col)
            else:
                self.rect(x0 - thickness // 2, y0 - thickness // 2, thickness, thickness, col)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def hline_dashed(self, x0, x1, y, col, dash=6, gap=4):
        y = int(y)
        if not (0 <= y < self.h):
            return
        x = int(x0)
        while x < x1:
            self.rect(x, y, min(dash, x1 - x), 1, col)
            x += dash + gap

    def text(self, x, y, s, col=(230, 236, 245), scale=1):
        s = str(s).upper()
        s = s.replace("Ş", "S").replace("Ğ", "G").replace("Ü", "U").replace("Ö", "O").replace("Ç", "C").replace("İ", "I")
        cx = int(x)
        cy = int(y)
        for ch in s:
            glyph = _FONT_5X7.get(ch, _FONT_5X7[' '])
            for r_i, row in enumerate(glyph):
                for c_i, bit in enumerate(row):
                    if bit == '1':
                        if scale == 1:
                            self.px(cx + c_i, cy + r_i, col)
                        else:
                            self.rect(cx + c_i * scale, cy + r_i * scale, scale, scale, col)
            cx += 6 * scale

    def to_png(self):
        raw = bytearray()
        stride = self.w * 3
        for y in range(self.h):
            raw.append(0)
            s = y * stride
            raw.extend(self.buf[s:s + stride])
        def chunk(tag, data):
            return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        ihdr = struct.pack("!IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)
        idat = zlib.compress(bytes(raw), 6)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def render_chart_png(symbol, tf, klines_list, title="", lines=None, emas=None):
    try:
        ks = klines_list[-70:] if len(klines_list) > 70 else list(klines_list)
        if len(ks) < 10:
            return b""
        W, H = 820, 440
        cv = _Canvas(W, H, (13, 17, 26))
        cv.rect(0, 0, W, 42, (20, 26, 40))
        cv.text(16, 13, f"{symbol} | {tf.upper()} | {title}", (240, 244, 250), scale=2)

        L, R, T_Y, B_Y = 18, 690, 56, 335
        VOL_T, VOL_B = 345, 415

        highs = [float(k[2]) for k in ks]
        lows = [float(k[3]) for k in ks]
        closes = [float(k[4]) for k in ks]
        opens = [float(k[1]) for k in ks]
        vols = [float(k[7]) for k in ks]

        p_min = min(lows)
        p_max = max(highs)
        if lines:
            for item in lines:
                val = item[0]
                if val and val > 0:
                    p_min = min(p_min, val)
                    p_max = max(p_max, val)
        pad = (p_max - p_min) * 0.08 or p_max * 0.02 or 1e-6
        p_min -= pad
        p_max += pad
        span = p_max - p_min

        def py(val):
            return int(B_Y - ((val - p_min) / span) * (B_Y - T_Y))

        for g in range(5):
            gy = int(T_Y + g * (B_Y - T_Y) / 4)
            cv.hline_dashed(L, R, gy, (30, 38, 56), 4, 6)
            gp = p_max - g * span / 4
            cv.text(R + 8, gy - 4, akilli_fiyat(gp), (140, 152, 175), scale=1)

        n = len(ks)
        slot = (R - L) / max(n, 1)
        cw = max(3, int(slot * 0.65))
        v_max = max(vols) or 1.0

        for i in range(n):
            cx = int(L + i * slot + slot * 0.5)
            o, h, l, c, v = opens[i], highs[i], lows[i], closes[i], vols[i]
            up = c >= o
            col = (38, 198, 132) if up else (235, 84, 98)
            cv.line(cx, py(h), cx, py(l), col, 1)
            y_o, y_c = py(o), py(c)
            top_b = min(y_o, y_c)
            h_b = max(2, abs(y_c - y_o))
            cv.rect(cx - cw // 2, top_b, cw, h_b, col)

            vh = int((v / v_max) * (VOL_B - VOL_T))
            vcol = (26, 110, 82) if up else (130, 48, 58)
            cv.rect(cx - cw // 2, VOL_B - vh, cw, vh, vcol)

        if emas:
            for e_vals, e_col in emas:
                if len(e_vals) >= n:
                    sub = e_vals[-n:]
                    for i in range(1, n):
                        x0 = int(L + (i - 1) * slot + slot * 0.5)
                        x1 = int(L + i * slot + slot * 0.5)
                        cv.line(x0, py(sub[i - 1]), x1, py(sub[i]), e_col, 2)

        if lines:
            for item in lines:
                val, lbl, col = item
                if val and p_min <= val <= p_max:
                    ly = py(val)
                    cv.hline_dashed(L, R, ly, col, 7, 4)
                    cv.rect(R + 4, ly - 7, 120, 14, col)
                    cv.text(R + 8, ly - 3, f"{lbl} {akilli_fiyat(val)}"[:19], (12, 16, 24), scale=1)

        cv.text(18, 424, f"SON: {akilli_fiyat(closes[-1])}", (180, 195, 220), scale=1)
        return cv.to_png()
    except Exception:
        return b""


def render_card_png(title, subtitle, rows, accent=(56, 189, 248)):
    try:
        W = 780
        H = 120 + len(rows) * 58
        cv = _Canvas(W, H, (13, 17, 26))
        cv.rect(0, 0, W, 68, (22, 29, 44))
        cv.rect(0, 66, W, 2, accent)
        cv.text(24, 16, title[:38], (244, 248, 255), scale=2)
        cv.text(24, 44, subtitle[:70], (150, 165, 190), scale=1)

        max_abs = max([abs(r[2]) for r in rows] + [1.0])
        for idx, (label, right_txt, pct) in enumerate(rows):
            y = 92 + idx * 58
            cv.rect(24, y, W - 48, 46, (21, 27, 40))
            bar_col = (34, 197, 94) if pct >= 0 else (239, 68, 68)
            bar_w = int((abs(pct) / max_abs) * 260)
            cv.rect(24, y + 38, max(6, bar_w), 5, bar_col)
            cv.text(38, y + 14, label[:28], (235, 242, 252), scale=2)
            cv.text(W - 250, y + 16, right_txt[:22], bar_col, scale=2)
        return cv.to_png()
    except Exception:
        return b""


def render_win_chart_png(symbol, sig_time_str, entry, peak, peak_pct, pts):
    try:
        W, H = 820, 400
        cv = _Canvas(W, H, (13, 17, 26))
        cv.rect(0, 0, W, 56, (22, 29, 44))
        cv.text(20, 14, f"GUNUN ZAFERI: {symbol}", (245, 248, 255), scale=2)
        cv.text(W - 210, 14, f"+{peak_pct:.1f}%", (38, 215, 132), scale=3)

        L, R, T_Y, B_Y = 30, 740, 82, 340
        if len(pts) < 2:
            pts = [entry, peak]
        mn = min(min(pts), entry) * 0.995
        mx = max(max(pts), peak) * 1.005
        span = max(mx - mn, entry * 0.01, 1e-8)

        def py(v):
            return int(B_Y - ((v - mn) / span) * (B_Y - T_Y))

        for g in range(4):
            gy = int(T_Y + g * (B_Y - T_Y) / 3)
            cv.hline_dashed(L, R, gy, (32, 42, 60), 4, 6)

        n = len(pts)
        for i in range(1, n):
            x0 = int(L + ((i - 1) / (n - 1)) * (R - L))
            x1 = int(L + (i / (n - 1)) * (R - L))
            y0, y1 = py(pts[i - 1]), py(pts[i])
            cv.line(x0, y0, x1, y1, (56, 189, 248), 3)

        cv.hline_dashed(L, R, py(entry), (250, 204, 21), 6, 4)
        cv.text(L, 358, f"SINYAL {sig_time_str} ${akilli_fiyat(entry)}", (250, 204, 21), scale=1)
        cv.text(440, 358, f"PIK ${akilli_fiyat(peak)} (+{peak_pct:.1f}%)", (38, 215, 132), scale=1)
        return cv.to_png()
    except Exception:
        return b""


def render_cvd_chart_png(symbol, klines_list, spot_cvd, fut_cvd, oi_pct, status_txt, div_txt):
    try:
        ks = klines_list[-36:]
        n = min(len(ks), len(spot_cvd), len(fut_cvd))
        if n < 10:
            return b""
        ks = ks[-n:]
        sc = spot_cvd[-n:]
        fc = fut_cvd[-n:]

        W, H = 820, 480
        cv = _Canvas(W, H, (13, 17, 26))
        cv.rect(0, 0, W, 44, (20, 26, 40))
        cv.text(16, 13, f"{symbol} | 1H CVD | {status_txt}", (240, 245, 255), scale=2)
        if div_txt:
            cv.rect(W - 180, 8, 165, 28, (34, 197, 94) if "BULL" in div_txt else (239, 68, 68))
            cv.text(W - 170, 15, div_txt[:15], (10, 14, 22), scale=2)

        L, R = 20, 700
        T1, B1 = 56, 248
        highs = [float(k[2]) for k in ks]
        lows = [float(k[3]) for k in ks]
        opens = [float(k[1]) for k in ks]
        closes = [float(k[4]) for k in ks]
        p_min, p_max = min(lows), max(highs)
        pad = (p_max - p_min) * 0.08 or p_max * 0.01
        p_min -= pad
        p_max += pad
        pspan = p_max - p_min

        def py1(v):
            return int(B1 - ((v - p_min) / pspan) * (B1 - T1))

        slot = (R - L) / n
        cw = max(4, int(slot * 0.6))
        for i in range(n):
            cx = int(L + i * slot + slot * 0.5)
            up = closes[i] >= opens[i]
            col = (38, 198, 132) if up else (235, 84, 98)
            cv.line(cx, py1(highs[i]), cx, py1(lows[i]), col, 1)
            yo, yc = py1(opens[i]), py1(closes[i])
            cv.rect(cx - cw // 2, min(yo, yc), cw, max(2, abs(yc - yo)), col)

        cv.rect(0, 256, W, 24, (22, 30, 46))
        cv.text(20, 264, f"SPOT CVD: {fmt_usd(sc[-1])}", (251, 146, 60), scale=1)
        cv.text(260, 264, f"FUTURES CVD: {fmt_usd(fc[-1])}", (56, 189, 248), scale=1)
        cv.text(520, 264, f"OI DEGISIM: {oi_pct:+.2f}%", (220, 230, 245), scale=1)

        T2, B2 = 290, 456
        c_min = min(min(sc), min(fc), 0.0)
        c_max = max(max(sc), max(fc), 0.0)
        cspan = max(c_max - c_min, 1.0)

        def py2(v):
            return int(B2 - ((v - c_min) / cspan) * (B2 - T2))

        cv.hline_dashed(L, R, py2(0.0), (70, 82, 104), 5, 5)
        for i in range(1, n):
            x0 = int(L + (i - 1) * slot + slot * 0.5)
            x1 = int(L + i * slot + slot * 0.5)
            cv.line(x0, py2(sc[i - 1]), x1, py2(sc[i]), (251, 146, 60), 2)
            cv.line(x0, py2(fc[i - 1]), x1, py2(fc[i]), (56, 189, 248), 2)

        return cv.to_png()
    except Exception:
        return b""


def render_dca_watchlist_png(items):
    """Crypto Watchlist DCA Kademeli Alim Karti (Base / -5% / -13% / -34% / TP +%2+)."""
    try:
        W = 820
        H = 128 + len(items) * 86
        cv = _Canvas(W, H, (13, 17, 26))
        cv.rect(0, 0, W, 72, (20, 28, 44))
        cv.rect(0, 70, W, 2, (52, 211, 153))
        cv.text(24, 14, "CRYPTO DCA WATCHLIST", (245, 248, 255), scale=2)
        cv.text(24, 42, "BASE + DCA -5% / -13% / -34% (EXPONENTIAL) | TP +%2+", (148, 168, 196), scale=1)

        for idx, it in enumerate(items):
            y = 92 + idx * 86
            cv.rect(20, y, W - 40, 74, (22, 29, 44))
            cv.rect(20, y, 5, 74, (52, 211, 153))
            sym = it["symbol"].replace("USDT", "")
            cv.text(36, y + 10, f"{idx+1}. {sym}/USDT", (240, 246, 255), scale=2)
            cv.text(280, y + 12, f"RSI:{it['rsi']:.0f} | 24S:{it['chg']:+.1f}%", (250, 204, 21), scale=1)
            cv.text(560, y + 10, f"BASE {akilli_fiyat(it['entry'])}", (52, 211, 153), scale=2)
            cv.text(
                36, y + 40,
                f"DCA1:{akilli_fiyat(it['dca1'])}  DCA2:{akilli_fiyat(it['dca2'])}  DCA3:{akilli_fiyat(it['dca3'])}",
                (165, 185, 215), scale=1
            )
            cv.text(
                36, y + 56,
                f"ORT MALIYET:{akilli_fiyat(it['avg_all'])}  |  HEDEF TP(+%2+): {akilli_fiyat(it['tp_base'])}",
                (56, 189, 248), scale=1
            )
        return cv.to_png()
    except Exception:
        return b""


# =====================================================
# COKLU BORSA VERI KATMANI & ANTI-STALE KONTROL
# =====================================================
def _bar_fresh(klines_list, interval, max_mult=3.0):
    if not klines_list or len(klines_list) < 2:
        return False
    iv_sec = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "8h": 28800, "12h": 43200, "1d": 86400, "3d": 259200}.get(interval, 3600)
    try:
        ts_ms = int(klines_list[-1][0])
        age = (time.time() * 1000 - ts_ms) / 1000.0
        return age <= iv_sec * max_mult
    except Exception:
        return True


def _norm_ticker(sym, last_p, chg_pct, quote_vol, src):
    return {
        "symbol": sym,
        "_price": float(last_p),
        "_change": float(chg_pct),
        "_quote": float(quote_vol),
        "_src": src
    }


def futures_tickers(force=False):
    now = time.time()
    with _cache_lock:
        ts, cached = _ticker_cache["fut"]
        if not force and cached and now - ts < 45:
            return cached

    rows = []
    data = gj(f"{FAPI}/fapi/v1/ticker/24hr", "BINANCE_FAPI")
    if isinstance(data, list):
        for t in data:
            s = t.get("symbol", "")
            if s.endswith("USDT") and s not in YASAKLI_HISSELER and not any(x in s for x in ("3L", "3S", "5L", "5S")):
                try:
                    rows.append(_norm_ticker(s, t["lastPrice"], t["priceChangePercent"], t["quoteVolume"], "BINANCE_FAPI"))
                except Exception:
                    pass

    if not rows:
        bdata = gj(f"{BYBIT}/v5/market/tickers?category=linear", "BYBIT")
        blist = (bdata or {}).get("result", {}).get("list", []) if isinstance(bdata, dict) else []
        for t in blist:
            s = t.get("symbol", "")
            if s.endswith("USDT") and s not in YASAKLI_HISSELER:
                try:
                    rows.append(_norm_ticker(s, t["lastPrice"], float(t.get("price24hPcnt", 0)) * 100, t.get("turnover24h", 0), "BYBIT"))
                except Exception:
                    pass

    if rows:
        with _cache_lock:
            _ticker_cache["fut"] = (now, rows)
    return rows or cached


def spot_tickers(force=False):
    now = time.time()
    with _cache_lock:
        ts, cached = _ticker_cache["spot"]
        if not force and cached and now - ts < 45:
            return cached

    rows = []
    for src, base in (("BINANCE_VISION", VISION), ("BINANCE_SPOT", SPOT)):
        data = gj(f"{base}/api/v3/ticker/24hr", src)
        if isinstance(data, list):
            for t in data:
                s = t.get("symbol", "")
                if s.endswith("USDT") and s not in YASAKLI_HISSELER and not any(x in s for x in ("UPUSDT", "DOWNUSDT")):
                    try:
                        rows.append(_norm_ticker(s, t["lastPrice"], t["priceChangePercent"], t["quoteVolume"], src))
                    except Exception:
                        pass
            if rows:
                break

    if not rows:
        bdata = gj(f"{BYBIT}/v5/market/tickers?category=spot", "BYBIT")
        blist = (bdata or {}).get("result", {}).get("list", []) if isinstance(bdata, dict) else []
        for t in blist:
            s = t.get("symbol", "")
            if s.endswith("USDT") and s not in YASAKLI_HISSELER:
                try:
                    rows.append(_norm_ticker(s, t["lastPrice"], float(t.get("price24hPcnt", 0)) * 100, t.get("turnover24h", 0), "BYBIT"))
                except Exception:
                    pass

    if rows:
        with _cache_lock:
            _ticker_cache["spot"] = (now, rows)
    return rows or cached


def adaylar(min_hacim, limit=MAX_TARAMA, market="fut"):
    src = futures_tickers() if market == "fut" else spot_tickers()
    rows = [t for t in src if t["_quote"] >= min_hacim and t["_price"] > 0]
    rows.sort(key=lambda x: x["_quote"], reverse=True)
    return rows[:limit]


def _bybit_klines(symbol, interval, limit, category="linear"):
    iv_map = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}
    biv = iv_map.get(interval)
    if not biv:
        return None
    data = gj(f"{BYBIT}/v5/market/kline?category={category}&symbol={symbol}&interval={biv}&limit={min(limit, 200)}", "BYBIT")
    lst = (data or {}).get("result", {}).get("list", []) if isinstance(data, dict) else []
    if not lst:
        return None
    out = []
    for k in reversed(lst):
        out.append([int(k[0]), k[1], k[2], k[3], k[4], k[5], int(k[0]), k[6], 0, "0", "0", "0"])
    return out


def klines(symbol, interval, limit=100, market="fut"):
    key = f"{market}:{symbol}:{interval}:{limit}"
    with _cache_lock:
        c = _kline_cache.get(key)
        if c and time.time() - c[0] < 45:
            return c[1]

    res = None
    if market == "fut":
        res = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}", "BINANCE_FAPI")
        if not isinstance(res, list) or not _bar_fresh(res, interval):
            res = _bybit_klines(symbol, interval, limit, "linear")
    else:
        for src, base in (("BINANCE_VISION", VISION), ("BINANCE_SPOT", SPOT)):
            res = gj(f"{base}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}", src)
            if isinstance(res, list) and _bar_fresh(res, interval):
                break
            res = None
        if not res:
            res = _bybit_klines(symbol, interval, limit, "spot")

    if isinstance(res, list) and res:
        with _cache_lock:
            _kline_cache[key] = (time.time(), res)
        return res
    return None


def tamamlanmis(ks):
    return ks[:-1] if isinstance(ks, list) and len(ks) >= 2 else []


# =====================================================
# TEKNIK GOSTERGELER & COKLU BORSA OI / CVD / ORDERBOOK
# =====================================================
def calculate_ema(prices, period):
    if len(prices) < period:
        return []
    k = 2.0 / (period + 1.0)
    ema = [sum(prices[:period]) / period]
    for p in prices[period:]:
        ema.append(p * k + ema[-1] * (1.0 - k))
    return ema


def calculate_sma(prices, period):
    if len(prices) < period:
        return []
    return [sum(prices[i - period:i]) / period for i in range(period, len(prices) + 1)]


def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + ag / al))


def calculate_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow + signal + 2:
        return None
    ef = calculate_ema(prices, fast)
    es = calculate_ema(prices, slow)
    if not ef or not es:
        return None
    offset = slow - fast
    macd_line = [ef[i + offset] - es[i] for i in range(len(es))]
    sig = calculate_ema(macd_line, signal)
    if len(sig) < 2:
        return None
    return macd_line[-1], sig[-1], macd_line[-2], sig[-2]


def find_pivots(highs, lows, window=3):
    ph, pl = [], []
    for i in range(window, len(highs) - window):
        if highs[i] == max(highs[i - window:i + window + 1]):
            ph.append((i, highs[i]))
        if lows[i] == min(lows[i - window:i + window + 1]):
            pl.append((i, lows[i]))
    return ph, pl


def trap_radar_check(symbol):
    oi = gj(f"{FAPI}/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=2", "BINANCE_FAPI")
    taker = gj(f"{FAPI}/futures/data/takerlongshortRatio?symbol={symbol}&period=5m&limit=1", "BINANCE_FAPI")
    oi_up, buyers = False, False
    try:
        if isinstance(oi, list) and len(oi) >= 2:
            old = float(oi[-2]["sumOpenInterest"])
            new = float(oi[-1]["sumOpenInterest"])
            oi_up = old > 0 and (new - old) / old * 100 > 0.2
        if isinstance(taker, list) and taker:
            b, s = float(taker[-1]["buyVol"]), float(taker[-1]["sellVol"])
            buyers = b + s > 0 and b / (b + s) > 0.55
    except Exception:
        pass
    if oi_up and buyers:
        return "🐋 <b>Balina Teyidi (OI + Alici Baskin)</b>"
    if not buyers:
        return "⚠️ <b>Alici Teyidi Yok (Tuzak Riski)</b>"
    return "⚖️ <b>Kismi Teyit</b>"


def fetch_orderbook_imbalance(symbol, price):
    base = symbol.replace("USDT", "")
    lo, hi = price * 0.985, price * 1.015
    bid_usd, ask_usd, used = 0.0, 0.0, 0

    def _add(bids, asks):
        nonlocal bid_usd, ask_usd, used
        b_s = sum(float(p) * float(q) for p, q in bids if lo <= float(p) <= price)
        a_s = sum(float(p) * float(q) for p, q in asks if price <= float(p) <= hi)
        if b_s + a_s > 0:
            bid_usd += b_s
            ask_usd += a_s
            used += 1

    d1 = gj(f"{SPOT}/api/v3/depth?symbol={symbol}&limit=50", "BINANCE_SPOT")
    if isinstance(d1, dict) and d1.get("bids"):
        _add(d1["bids"], d1["asks"])
    d2 = gj(f"{BYBIT}/v5/market/orderbook?category=spot&symbol={symbol}&limit=50", "BYBIT")
    r2 = (d2 or {}).get("result", {}) if isinstance(d2, dict) else {}
    if r2.get("b"):
        _add(r2["b"], r2["a"])
    d3 = gj(f"{OKX}/api/v5/market/books?instId={base}-USDT&sz=50", "OKX")
    r3 = ((d3 or {}).get("data") or [{}])[0] if isinstance(d3, dict) else {}
    if r3.get("bids"):
        _add([(x[0], x[1]) for x in r3["bids"]], [(x[0], x[1]) for x in r3["asks"]])
    d4 = gj(f"{GATE}/api/v4/spot/order_book?currency_pair={base}_USDT&limit=50", "GATE")
    if isinstance(d4, dict) and d4.get("bids"):
        _add(d4["bids"], d4["asks"])

    tot = bid_usd + ask_usd
    if tot <= 0:
        return 50.0, 0.0, 0.0, 0
    return (bid_usd / tot) * 100.0, bid_usd, ask_usd, used


def fetch_spot_futures_cvd(symbol, limit=36):
    key = f"cvd:{symbol}:{limit}"
    with _cache_lock:
        c = _cvd_cache.get(key)
        if c and time.time() - c[0] < 120:
            return c[1]

    sk = None
    for src, base in (("BINANCE_VISION", VISION), ("BINANCE_SPOT", SPOT)):
        tmp = gj(f"{base}/api/v3/klines?symbol={symbol}&interval=1h&limit={limit}", src)
        if isinstance(tmp, list) and len(tmp) >= 15 and _bar_fresh(tmp, "1h"):
            sk = tmp
            break

    fk = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1h&limit={limit}", "BINANCE_FAPI")
    fut_deltas = []
    if isinstance(fk, list) and len(fk) >= 15 and _bar_fresh(fk, "1h"):
        for k in fk:
            qv = float(k[7])
            tbq = float(k[10])
            fut_deltas.append(2.0 * tbq - qv)
    else:
        fk = _bybit_klines(symbol, "1h", limit, "linear")
        base_coin = symbol.replace("USDT", "")
        okx_tv = gj(f"{OKX}/api/v5/rubik/stat/taker-volume-contract?instId={base_coin}-USDT-SWAP&period=1H&limit={limit}", "OKX")
        tv_map = {}
        if isinstance(okx_tv, dict) and isinstance(okx_tv.get("data"), list):
            for row in okx_tv["data"]:
                ts_hr = int(row[0]) // 3600000
                sv, bv = float(row[1]), float(row[2])
                if bv + sv > 0:
                    tv_map[ts_hr] = (bv - sv) / (bv + sv)
        if isinstance(fk, list) and len(fk) >= 15:
            for k in fk:
                ts_hr = int(k[0]) // 3600000
                qv = float(k[7])
                ratio = tv_map.get(ts_hr, 0.0)
                fut_deltas.append(qv * ratio)

    if not sk or not fk or not fut_deltas:
        return None

    spot_deltas = [2.0 * float(k[10]) - float(k[7]) for k in sk]
    n = min(len(spot_deltas), len(fut_deltas), len(fk))
    if n < 15:
        return None
    spot_deltas = spot_deltas[-n:]
    fut_deltas = fut_deltas[-n:]
    fk = fk[-n:]

    sc, fc = [], []
    sa = fa = 0.0
    for sd, fd in zip(spot_deltas, fut_deltas):
        sa += sd
        fa += fd
        sc.append(sa)
        fc.append(fa)

    oi_pct = 0.0
    oi = gj(f"{FAPI}/futures/data/openInterestHist?symbol={symbol}&period=1h&limit={min(n, 24)}", "BINANCE_FAPI")
    if isinstance(oi, list) and len(oi) >= 4:
        o0 = float(oi[0]["sumOpenInterestValue"])
        o1 = float(oi[-1]["sumOpenInterestValue"])
        if o0 > 0:
            oi_pct = ((o1 - o0) / o0) * 100.0
    else:
        boi = gj(f"{BYBIT}/v5/market/open-interest?category=linear&symbol={symbol}&intervalTime=1h&limit=24", "BYBIT")
        lst = (boi or {}).get("result", {}).get("list", []) if isinstance(boi, dict) else []
        if len(lst) >= 4:
            o0 = float(lst[-1]["openInterest"])
            o1 = float(lst[0]["openInterest"])
            if o0 > 0:
                oi_pct = ((o1 - o0) / o0) * 100.0

    out = (fk, sc, fc, oi_pct)
    with _cache_lock:
        _cvd_cache[key] = (time.time(), out)
    return out


# =====================================================
# SIGNAL PERFORMANCE TRACKER
# =====================================================
def _load_tracker():
    global _TRACKED_SIGNALS
    try:
        if os.path.exists(TRACKER_FILE):
            with open(TRACKER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    _TRACKED_SIGNALS = data[-1500:]
    except Exception:
        _TRACKED_SIGNALS = []


def _save_tracker():
    try:
        with open(TRACKER_FILE, "w", encoding="utf-8") as f:
            json.dump(_TRACKED_SIGNALS[-1500:], f)
    except Exception:
        pass


def record_tracked_signal(motor, symbol, direction, entry):
    if not entry or entry <= 0:
        return
    now = time.time()
    with TRACKER_LOCK:
        _TRACKED_SIGNALS.append({
            "motor": motor,
            "symbol": symbol,
            "dir": direction,
            "entry": float(entry),
            "peak": float(entry),
            "peak_pct": 0.0,
            "ts": now,
            "peak_ts": now,
            "pts": [float(entry)]
        })
        if len(_TRACKED_SIGNALS) > 1500:
            del _TRACKED_SIGNALS[:-1500]
        _save_tracker()


def update_tracked_signals():
    now = time.time()
    fut_map = {t["symbol"]: t["_price"] for t in futures_tickers()}
    spot_map = {t["symbol"]: t["_price"] for t in spot_tickers()}
    with TRACKER_LOCK:
        cutoff = now - 72 * 3600
        _TRACKED_SIGNALS[:] = [s for s in _TRACKED_SIGNALS if s["ts"] >= cutoff]
        for s in _TRACKED_SIGNALS:
            p = fut_map.get(s["symbol"]) or spot_map.get(s["symbol"])
            if not p or p <= 0:
                continue
            pts = s.setdefault("pts", [s["entry"]])
            if len(pts) < 40:
                pts.append(p)
            else:
                pts[-1] = p
            if s["dir"] == "LONG":
                pct = ((p - s["entry"]) / s["entry"]) * 100.0
            else:
                pct = ((s["entry"] - p) / s["entry"]) * 100.0
            if pct > s.get("peak_pct", 0.0):
                s["peak_pct"] = pct
                s["peak"] = p
                s["peak_ts"] = now
        _save_tracker()


def get_tracker_stats(hours=24, min_pct=1.5):
    update_tracked_signals()
    cutoff = time.time() - hours * 3600
    with TRACKER_LOCK:
        best_by_sym = {}
        for s in _TRACKED_SIGNALS:
            if s["ts"] >= cutoff and s["peak_pct"] >= min_pct:
                cur = best_by_sym.get(s["symbol"])
                if not cur or s["peak_pct"] > cur["peak_pct"]:
                    best_by_sym[s["symbol"]] = dict(s)
        out = list(best_by_sym.values())
        out.sort(key=lambda x: x["peak_pct"], reverse=True)
        return out


# =====================================================
# GENEL YARDIMCILAR
# =====================================================
def cooldown_ok(store, key, sec):
    return time.time() - store.get(key, 0.0) >= sec


def signal_sent(store, key):
    store[key] = time.time()


def make_signal(title, symbol, direction, entry, target, stop, detail=""):
    tp = ((target - entry) / entry) * 100.0
    sl = ((stop - entry) / entry) * 100.0
    em = "🚀" if direction == "LONG" else "🔻"
    return (
        f"{em} <b>{title} ({direction}): {symbol}</b>\n\n"
        f"💰 <b>Giris:</b> {akilli_fiyat(entry)}\n"
        f"🎯 <b>Hedef:</b> {akilli_fiyat(target)} ({tp:+.1f}%)\n"
        f"🛑 <b>Stop:</b> {akilli_fiyat(stop)} ({sl:+.1f}%)"
        f"{detail}"
    )


def safe_loop(name, interval, worker, startup=5):
    log(name, "Motor baslatildi.")
    time.sleep(startup)
    while True:
        t0 = time.time()
        try:
            worker()
        except Exception as e:
            log(name, f"Hata: {e}")
            log(name, traceback.format_exc()[-800:])
        time.sleep(max(5, interval - (time.time() - t0)))


# =====================================================
# MOTOR 1: TOP GAINERS
# =====================================================
def top_gainers_worker():
    rows = [t for t in spot_tickers() if t["_quote"] > 5_000_000 and t["_change"] > 0]
    rows.sort(key=lambda x: x["_change"], reverse=True)
    top5 = rows[:5]
    if not top5:
        return
    card_rows = [(f"{i+1}. {t['symbol'].replace('USDT','')}", f"+{t['_change']:.1f}% | {akilli_fiyat(t['_price'])}", t["_change"]) for i, t in enumerate(top5)]
    png = render_card_png("TOP GAINERS (24 SAAT)", "Spot | 5M+ USDT Hacim", card_rows, (34, 197, 94))
    msg = "🔥 <b>Gunun En Cok Yukselenleri (24s Spot)</b>\n\n" + "\n".join(
        f"{i+1}. <b>{t['symbol'].replace('USDT','')}</b> +{t['_change']:.1f}% | {akilli_fiyat(t['_price'])}" for i, t in enumerate(top5)
    )
    tg_photo(C, png, msg)


# =====================================================
# MOTOR 2: PUMP WINNER
# =====================================================
def pump_winner_worker():
    rows = [t for t in futures_tickers() if t["_quote"] > 10_000_000 and t["_change"] >= 15]
    rows.sort(key=lambda x: x["_change"], reverse=True)
    if not rows:
        return
    w = rows[0]
    sym = w["symbol"]
    ks = klines(sym, "1d", 2, "fut")
    if not ks:
        return
    lo, hi = float(ks[-1][3]), float(ks[-1][2])
    mv = ((hi - lo) / lo) * 100.0 if lo > 0 else w["_change"]
    ks_1h = klines(sym, "1h", 36, "fut") or []
    png = render_chart_png(sym, "1h", ks_1h, f"GUNUN KAZANANI +{mv:.1f}%", [(lo, "DIP", (250, 204, 21)), (hi, "PIK", (34, 197, 94))]) if ks_1h else b""
    msg = f"🏆 <b>GUNUN KAZANANI: {sym}</b>\n📉 Dip: {akilli_fiyat(lo)} | 🚀 Pik: {akilli_fiyat(hi)} (<b>+{mv:.1f}%</b>)"
    tg_photo(C, png, msg)


# =====================================================
# MOTOR 3: DOMINANS
# =====================================================
def dominans_worker():
    g = gj(f"{CG}/api/v3/global", "COINGECKO")
    pct = (g or {}).get("data", {}).get("market_cap_percentage", {})
    if not pct:
        return
    btc = float(pct.get("btc", 0))
    eth = float(pct.get("eth", 0))
    oth = max(0.0, 100.0 - btc - eth)
    rows = [("BTC DOMINANS", f"%{btc:.2f}", btc), ("ETH DOMINANS", f"%{eth:.2f}", eth), ("DIGER ALTCOINLER", f"%{oth:.2f}", oth)]
    png = render_card_png("PIYASA DOMINANSI", "CoinGecko Global Market Cap", rows, (250, 204, 21))
    msg = f"🌐 <b>PIYASA DOMINANSI</b>\n₿ <b>BTC:</b> %{btc:.2f} | ♦️ <b>ETH:</b> %{eth:.2f} | 🪙 <b>Diger:</b> %{oth:.2f}"
    tg_photo(C, png, msg)


# =====================================================
# MOTOR 4: CHARTIST (SPOT EMA200)
# =====================================================
def chartist_worker():
    for t in adaylar(MIN_HACIM_CHARTIST, 50, "spot"):
        sym = t["symbol"]
        for tf in ("8h", "12h", "1d", "3d"):
            ck = f"CH_{tf}_{sym}"
            if not cooldown_ok(son_sinyaller, ck, 86400):
                continue
            ks = tamamlanmis(klines(sym, tf, 220, "spot"))
            if len(ks) < 205:
                continue
            closes = [float(k[4]) for k in ks]
            vols = [float(k[7]) for k in ks]
            e200 = calculate_ema(closes, 200)
            if len(e200) < 2:
                continue
            curr, prev = closes[-1], closes[-2]
            avg = sum(vols[-21:-1]) / 20
            if prev <= e200[-2] and curr > e200[-1] * 1.01 and vols[-1] > avg:
                sl = e200[-1] * 0.97
                tp = curr * 1.15
                png = render_chart_png(sym, tf, ks, "SPOT EMA200 LONG", [(curr, "GIRIS", (56, 189, 248)), (tp, "TP", (34, 197, 94)), (sl, "SL", (239, 68, 68))], [(e200, (250, 204, 21))])
                msg = make_signal(f"CHARTIST {tf.upper()} EMA200", sym, "LONG", curr, tp, sl)
                if tg_photo(C, png, msg):
                    signal_sent(son_sinyaller, ck)
                    record_tracked_signal("CHARTIST", sym, "LONG", curr)


# =====================================================
# MOTOR 5: PUMP AVCISI (5m + TRAP RADAR)
# =====================================================
def pump_worker():
    for t in adaylar(MIN_HACIM_PUMP, MAX_TARAMA, "fut"):
        sym = t["symbol"]
        if not cooldown_ok(son_pump_sinyalleri, sym, 900):
            continue
        ks = klines(sym, "5m", 35, "fut")
        if not ks or len(ks) < 4:
            continue
        ck, pk = ks[-2], ks[-3]
        cc, pc = float(ck[4]), float(pk[4])
        cv, pv = float(ck[7]), float(pk[7])
        if pc <= 0 or pv <= 0:
            continue
        pchg = ((cc - pc) / pc) * 100.0
        vchg = ((cv - pv) / pv) * 100.0
        if pchg >= 3.0 and vchg >= 100.0 and (cv - pv) >= 50_000:
            trap = trap_radar_check(sym)
            png = render_chart_png(sym, "5m", ks[:-1], f"PUMP +{pchg:.1f}%", [(cc, "GIRIS", (56, 189, 248))])
            msg = f"🚀 <b>Pump - {sym}</b>\n💰 Fiyat: {akilli_fiyat(cc)} (<b>{pchg:+.2f}%</b>)\n📊 Hacim: {vchg:+.0f}%\n{trap}"
            if tg_photo(C, png, msg):
                signal_sent(son_pump_sinyalleri, sym)
                record_tracked_signal("PUMP", sym, "LONG", cc)


# =====================================================
# MOTOR 6: VOLATILITE (15m)
# =====================================================
def volatilite_worker():
    for t in adaylar(MIN_HACIM_VOLATILITE, MAX_TARAMA, "fut"):
        sym = t["symbol"]
        if not cooldown_ok(son_volatilite_sinyalleri, sym, 1800):
            continue
        ks = klines(sym, "15m", 30, "fut")
        if not ks or len(ks) < 25:
            continue
        ck = ks[-2]
        op, cl, vol = float(ck[1]), float(ck[4]), float(ck[7])
        avg = sum(float(k[7]) for k in ks[-22:-2]) / 20
        if op <= 0 or avg <= 0:
            continue
        pct = ((cl - op) / op) * 100.0
        vm = vol / avg
        if abs(pct) >= 2.5 and vm >= 3.0:
            yon = "LONG" if pct > 0 else "SHORT"
            png = render_chart_png(sym, "15m", ks[:-1], f"VOLATILITE {pct:+.1f}%", [(cl, "KAPANIS", (250, 204, 21))])
            msg = f"⚡ <b>ANORMAL VOLATILITE ({yon}): {sym}</b>\n📊 15m Degisim: <b>{pct:+.2f}%</b> | Hacim: <b>{vm:.1f}x</b>\n💰 Fiyat: {akilli_fiyat(cl)}"
            if tg_photo(C, png, msg):
                signal_sent(son_volatilite_sinyalleri, sym)
                record_tracked_signal("VOLATILITE", sym, yon, cl)


# =====================================================
# MOTOR 7: FORMASYON (5m + 15m EMA11/21 + RSI)
# =====================================================
def formasyon_worker():
    for t in adaylar(MIN_HACIM_FORMASYON, MAX_TARAMA, "fut"):
        sym = t["symbol"]
        if not cooldown_ok(son_formasyon_sinyalleri, sym, 14400):
            continue
        k5 = klines(sym, "5m", 100, "fut")
        k15 = klines(sym, "15m", 50, "fut")
        if not k5 or not k15 or len(k5) < 90 or len(k15) < 40:
            continue
        highs = [float(k[2]) for k in k5]
        lows = [float(k[3]) for k in k5]
        closes = [float(k[4]) for k in k5]
        vols = [float(k[7]) for k in k5]
        c15 = [float(k[4]) for k in k15]
        e11 = calculate_ema(c15, 11)[-1]
        e21 = calculate_ema(c15, 21)[-1]
        rsi = calculate_rsi(c15)
        price = closes[-1]
        avg = sum(vols[-21:-1]) / 20
        if avg <= 0 or vols[-1] <= avg * 1.5:
            continue
        ph, pl = find_pivots(highs, lows)
        if len(ph) < 3 or len(pl) < 3:
            continue
        lh, ll = ph[-3:], pl[-3:]
        sig = None
        if abs(lh[0][1] - lh[2][1]) / lh[0][1] < 0.03 and price < ll[-1][1] and rsi < 50 and e11 < e21:
            sig = ("SHORT", "Cift Tepe (M)", ll[-1][1] - (lh[1][1] - ll[-1][1]), lh[2][1] * 1.02)
        elif abs(ll[0][1] - ll[2][1]) / ll[0][1] < 0.03 and price > lh[-1][1] and rsi > 50 and e11 > e21:
            sig = ("LONG", "Cift Dip (W)", lh[-1][1] + (lh[-1][1] - ll[1][1]), ll[2][1] * 0.98)
        elif lh[1][1] > lh[0][1] and lh[1][1] > lh[2][1] and abs(lh[0][1] - lh[2][1]) / lh[0][1] < 0.05 and price < ll[-1][1] and rsi < 50 and e11 < e21:
            sig = ("SHORT", "Omuz-Bas-Omuz", ll[-1][1] - (lh[1][1] - ll[-1][1]), lh[2][1] * 1.02)
        elif highs[-2] > max(highs[-20:-2]) and price < max(highs[-20:-2]) and rsi < 50 and e11 < e21:
            sig = ("SHORT", "Likidite Avi (Sweep)", price * 0.97, highs[-2] * 1.01)
        elif lows[-2] < min(lows[-20:-2]) and price > min(lows[-20:-2]) and rsi > 50 and e11 > e21:
            sig = ("LONG", "Likidite Avi (Sweep)", price * 1.03, lows[-2] * 0.99)
        else:
            h20, l20 = max(highs[-20:]), min(lows[-20:])
            rp = ((h20 - l20) / l20) * 100.0
            if rp < 5 and price > h20 and rsi > 50:
                sig = ("LONG", "Sikisma Bayrak", price + (h20 - l20), l20 * 0.98)
            elif rp < 5 and price < l20 and rsi < 50:
                sig = ("SHORT", "Sikisma Ters Bayrak", price - (h20 - l20), h20 * 1.02)

        if not sig:
            continue
        yon, ad, hedef, stop = sig
        tp = ((hedef - price) / price) * 100.0
        sl = ((stop - price) / price) * 100.0
        if abs(sl) > 10.0 or not (1.0 <= abs(tp) <= 20.0):
            continue
        png = render_chart_png(sym, "5m", k5, ad, [(price, "GIRIS", (56, 189, 248)), (hedef, "TP", (34, 197, 94)), (stop, "SL", (239, 68, 68))])
        msg = make_signal(f"FORMASYON - {ad}", sym, yon, price, hedef, stop, f"\n📊 <b>RSI(15m):</b> {rsi:.1f} | <b>Hacim:</b> {vols[-1]/avg:.1f}x")
        if tg_photo(C, png, msg):
            signal_sent(son_formasyon_sinyalleri, sym)
            record_tracked_signal("FORMASYON", sym, yon, price)


# =====================================================
# MOTOR 8: ICHIMOKU (1h KUMO)
# =====================================================
def ichimoku_worker():
    for t in adaylar(MIN_HACIM_ICHIMOKU, MAX_TARAMA, "fut"):
        sym = t["symbol"]
        if not cooldown_ok(son_ichimoku_sinyalleri, sym, 14400):
            continue
        ks = klines(sym, "1h", 100, "fut")
        if not ks or len(ks) < 85:
            continue
        H = [float(k[2]) for k in ks]
        L = [float(k[3]) for k in ks]
        Cl = [float(k[4]) for k in ks]
        V = [float(k[7]) for k in ks]
        idx = len(Cl) - 2
        if V[idx] <= sum(V[idx - 20:idx]) / 20:
            continue
        def tk(i): return (max(H[i - 8:i + 1]) + min(L[i - 8:i + 1])) / 2.0
        def kj(i): return (max(H[i - 25:i + 1]) + min(L[i - 25:i + 1])) / 2.0
        t_c, k_c = tk(idx), kj(idx)
        sa_c = (tk(idx - 26) + kj(idx - 26)) / 2.0
        sa_p = (tk(idx - 27) + kj(idx - 27)) / 2.0
        sb_c = (max(H[idx - 77:idx - 25]) + min(L[idx - 77:idx - 25])) / 2.0
        sb_p = (max(H[idx - 78:idx - 26]) + min(L[idx - 78:idx - 26])) / 2.0
        top_c, bot_c = max(sa_c, sb_c), min(sa_c, sb_c)
        top_p, bot_p = max(sa_p, sb_p), min(sa_p, sb_p)
        thk = top_c - bot_c
        cc, pc = Cl[idx], Cl[idx - 1]
        if thk <= cc * 0.005:
            continue
        sig = None
        if pc <= top_p and cc > top_c and cc > k_c and t_c > k_c:
            sig = ("LONG", cc + thk * 2, min(k_c, bot_c) * 0.99)
        elif pc >= bot_p and cc < bot_c and cc < k_c and t_c < k_c:
            sig = ("SHORT", cc - thk * 2, max(k_c, top_c) * 1.01)
        if not sig:
            continue
        yon, hedef, stop = sig
        if abs(((hedef - cc) / cc) * 100) < 1.0:
            continue
        png = render_chart_png(sym, "1h", ks[:-1], f"ICHIMOKU {yon}", [(cc, "GIRIS", (56, 189, 248)), (hedef, "TP", (34, 197, 94)), (stop, "SL", (239, 68, 68))])
        msg = make_signal("ICHIMOKU BULUTU", sym, yon, cc, hedef, stop)
        if tg_photo(C, png, msg):
            signal_sent(son_ichimoku_sinyalleri, sym)
            record_tracked_signal("ICHIMOKU", sym, yon, cc)


# =====================================================
# MOTOR 9: EMA RIBBON (1d)
# =====================================================
def ema_ribbon_worker():
    for t in adaylar(MIN_HACIM_EMA_RIBBON, MAX_TARAMA, "fut"):
        sym = t["symbol"]
        if not cooldown_ok(son_ema_ribbon_sinyalleri, sym, 86400):
            continue
        ks = tamamlanmis(klines(sym, "1d", 220, "fut"))
        if len(ks) < 205:
            continue
        Cl = [float(k[4]) for k in ks]
        V = [float(k[7]) for k in ks]
        if V[-1] <= sum(V[-21:-1]) / 20 * 1.2:
            continue
        e7, e25, e99, e200 = calculate_ema(Cl, 7), calculate_ema(Cl, 25), calculate_ema(Cl, 99), calculate_ema(Cl, 200)
        if Cl[-1] > e7[-1] > e25[-1] > e99[-1] > e200[-1]:
            c = Cl[-1]
            tp, sl = c * 1.20, e25[-1] * 0.95
            png = render_chart_png(sym, "1d", ks, "EMA RIBBON LONG", [(c, "GIRIS", (56, 189, 248)), (tp, "TP", (34, 197, 94)), (sl, "SL", (239, 68, 68))], [(e7, (56, 189, 248)), (e25, (250, 204, 21)), (e200, (239, 68, 68))])
            msg = make_signal("EMA RIBBON (7>25>99>200)", sym, "LONG", c, tp, sl)
            if tg_photo(C, png, msg):
                signal_sent(son_ema_ribbon_sinyalleri, sym)
                record_tracked_signal("EMA_RIBBON", sym, "LONG", c)


# =====================================================
# MOTOR 10: MACD (1d)
# =====================================================
def macd_worker():
    for t in adaylar(MIN_HACIM_MACD, MAX_TARAMA, "fut"):
        sym = t["symbol"]
        if not cooldown_ok(son_macd_sinyalleri, sym, 14400):
            continue
        ks = tamamlanmis(klines(sym, "1d", 100, "fut"))
        if len(ks) < 70:
            continue
        Cl = [float(k[4]) for k in ks]
        V = [float(k[7]) for k in ks]
        if V[-1] <= sum(V[-21:-1]) / 20 * 1.1:
            continue
        m = calculate_macd(Cl)
        if not m:
            continue
        cm, cs, pm, ps = m
        if pm <= ps and cm > cs:
            c = Cl[-1]
            tp, sl = c * 1.10, c * 0.97
            png = render_chart_png(sym, "1d", ks, "MACD GOLDEN CROSS", [(c, "GIRIS", (56, 189, 248)), (tp, "TP", (34, 197, 94)), (sl, "SL", (239, 68, 68))])
            msg = make_signal("MACD GOLDEN CROSS", sym, "LONG", c, tp, sl)
            if tg_photo(C, png, msg):
                signal_sent(son_macd_sinyalleri, sym)
                record_tracked_signal("MACD", sym, "LONG", c)


# =====================================================
# MOTOR 11: SMA200 (1d TAZE KIRILIM + CANLI FIYAT KILIDI)
# =====================================================
def sma200_worker():
    for t in adaylar(MIN_HACIM_SMA200, MAX_TARAMA, "fut"):
        sym = t["symbol"]
        if not cooldown_ok(son_sma200_sinyalleri, sym, 86400):
            continue
        ks = tamamlanmis(klines(sym, "1d", 220, "fut"))
        if len(ks) < 205:
            continue
        Cl = [float(k[4]) for k in ks]
        s200 = calculate_sma(Cl, 200)
        if len(s200) < 2:
            continue
        live = t["_price"]
        if Cl[-2] <= s200[-2] and Cl[-1] > s200[-1] and live > s200[-1]:
            c = Cl[-1]
            tp, sl = c * 1.15, s200[-1] * 0.97
            png = render_chart_png(sym, "1d", ks, "SMA200 KIRILIMI", [(c, "GIRIS", (56, 189, 248)), (tp, "TP", (34, 197, 94)), (sl, "SL", (239, 68, 68))], [(s200, (250, 204, 21))])
            msg = make_signal("SMA200 KIRILIMI", sym, "LONG", c, tp, sl)
            if tg_photo(C, png, msg):
                signal_sent(son_sma200_sinyalleri, sym)
                record_tracked_signal("SMA200", sym, "LONG", c)


# =====================================================
# MOTOR 12: ATH / ATL (1d KAPANIS)
# =====================================================
def ath_atl_worker():
    for t in adaylar(MIN_HACIM_ATH_ATL, MAX_TARAMA, "fut"):
        sym = t["symbol"]
        if not cooldown_ok(son_ath_atl_sinyalleri, sym, 86400):
            continue
        ks = tamamlanmis(klines(sym, "1d", 250, "fut"))
        if len(ks) < 150:
            continue
        H = [float(k[2]) for k in ks]
        L = [float(k[3]) for k in ks]
        Cl = [float(k[4]) for k in ks]
        V = [float(k[7]) for k in ks]
        if V[-1] <= sum(V[-21:-1]) / 20 * 1.2:
            continue
        ath, atl = max(H[:-1]), min(L[:-1])
        c, p = Cl[-1], Cl[-2]
        if p <= ath and c > ath:
            yon, tp, sl, lvl = "LONG", c * 1.20, ath * 0.95, ath
        elif p >= atl and c < atl:
            yon, tp, sl, lvl = "SHORT", c * 0.80, atl * 1.05, atl
        else:
            continue
        png = render_chart_png(sym, "1d", ks, f"TARIHI {'ATH' if yon=='LONG' else 'ATL'}", [(lvl, "SEVIYE", (250, 204, 21)), (tp, "TP", (34, 197, 94)), (sl, "SL", (239, 68, 68))])
        msg = make_signal("TARIHI SEVIYE KIRILIMI", sym, yon, c, tp, sl)
        if tg_photo(C, png, msg):
            signal_sent(son_ath_atl_sinyalleri, sym)
            record_tracked_signal("ATH_ATL", sym, yon, c)


# =====================================================
# MOTOR 13: PATTERN (4h / 1d IKILI TEPE-DIP, OBO, TOBO)
# =====================================================
def chart_patterns_worker():
    for t in adaylar(MIN_HACIM_PATTERN, MAX_TARAMA, "fut"):
        sym = t["symbol"]
        if not cooldown_ok(son_chart_pattern_sinyalleri, sym, 14400):
            continue
        for tf in ("4h", "1d"):
            ks = tamamlanmis(klines(sym, tf, 100, "fut"))
            if len(ks) < 50:
                continue
            H = [float(k[2]) for k in ks]
            L = [float(k[3]) for k in ks]
            Cl = [float(k[4]) for k in ks]
            V = [float(k[7]) for k in ks]
            if V[-1] < sum(V[-21:-1]) / 20 * 1.2:
                continue
            ph, pl = find_pivots(H, L)
            c = Cl[-1]
            sig = None
            if len(ph) >= 2:
                i1, p1 = ph[-2]
                i2, p2 = ph[-1]
                if 5 <= i2 - i1 <= 30 and abs(p1 - p2) / p1 <= 0.02:
                    nk = min(L[i1:i2 + 1])
                    if c < nk * 0.99:
                        sig = ("SHORT", "Ikili Tepe (M)", nk - (max(p1, p2) - nk), max(p1, p2) * 1.02, nk)
            if not sig and len(pl) >= 2:
                i1, p1 = pl[-2]
                i2, p2 = pl[-1]
                if 5 <= i2 - i1 <= 30 and abs(p1 - p2) / p1 <= 0.02:
                    nk = max(H[i1:i2 + 1])
                    if c > nk * 1.01:
                        sig = ("LONG", "Ikili Dip (W)", nk + (nk - min(p1, p2)), min(p1, p2) * 0.98, nk)
            if not sig and len(ph) >= 3:
                (i1, p1), (i2, p2), (i3, p3) = ph[-3:]
                if p2 > p1 and p2 > p3 and abs(p1 - p3) / p1 <= 0.03:
                    nk = min(L[i1:i3 + 1])
                    if c < nk * 0.99:
                        sig = ("SHORT", "Omuz-Bas-Omuz", nk - (p2 - nk), p3 * 1.02, nk)
            if not sig and len(pl) >= 3:
                (i1, p1), (i2, p2), (i3, p3) = pl[-3:]
                if p2 < p1 and p2 < p3 and abs(p1 - p3) / p1 <= 0.03:
                    nk = max(H[i1:i3 + 1])
                    if c > nk * 1.01:
                        sig = ("LONG", "Ters Omuz-Bas-Omuz", nk + (nk - p2), p3 * 0.98, nk)
            if not sig:
                continue
            yon, ad, tp_p, sl_p, nk = sig
            tp_pct = ((tp_p - c) / c) * 100.0
            sl_pct = ((sl_p - c) / c) * 100.0
            if abs(sl_pct) > 15 or not (1.5 <= abs(tp_pct) <= 40):
                continue
            png = render_chart_png(sym, tf, ks, ad, [(nk, "BOYUN", (250, 204, 21)), (tp_p, "TP", (34, 197, 94)), (sl_p, "SL", (239, 68, 68))])
            msg = make_signal(f"PATTERN - {ad}", sym, yon, c, tp_p, sl_p, f"\n📊 <b>Boyun:</b> {akilli_fiyat(nk)} | <b>TF:</b> {tf}")
            if tg_photo(C, png, msg):
                signal_sent(son_chart_pattern_sinyalleri, sym)
                record_tracked_signal("PATTERN", sym, yon, c)
                break


# =====================================================
# MOTOR 14: ANLIK ATH / ATL (CANLI)
# =====================================================
def anlik_ath_atl_worker():
    for t in adaylar(MIN_HACIM_ANLIK_ATH, MAX_TARAMA, "fut"):
        sym = t["symbol"]
        if not cooldown_ok(son_anlik_ath_sinyalleri, sym, 14400):
            continue
        ks = klines(sym, "1d", 200, "fut")
        if not ks or len(ks) < 100:
            continue
        prev = ks[:-1]
        ath = max(float(k[2]) for k in prev)
        atl = min(float(k[3]) for k in prev)
        live = t["_price"]
        if live > ath:
            yon, tp, sl, lvl = "LONG", live * 1.15, ath * 0.97, ath
        elif live < atl:
            yon, tp, sl, lvl = "SHORT", live * 0.85, atl * 1.03, atl
        else:
            continue
        png = render_chart_png(sym, "1d", ks, f"ANLIK {'ATH' if yon=='LONG' else 'ATL'}", [(lvl, "SEVIYE", (250, 204, 21)), (tp, "TP", (34, 197, 94))])
        msg = make_signal("ANLIK KIRILIM", sym, yon, live, tp, sl)
        if tg_photo(C, png, msg):
            signal_sent(son_anlik_ath_sinyalleri, sym)
            record_tracked_signal("ANLIK_ATH_ATL", sym, yon, live)


# =====================================================
# MOTOR 15: TOP MOVERS (FUTURES SON 1 SAAT)
# =====================================================
def top_movers_worker():
    rows = adaylar(MIN_HACIM_MOVERS, 50, "fut")
    res = []
    for t in rows:
        ks = tamamlanmis(klines(t["symbol"], "1h", 3, "fut"))
        if not ks:
            continue
        op, cl = float(ks[-1][1]), float(ks[-1][4])
        if op > 0:
            res.append({"sym": t["symbol"], "chg": ((cl - op) / op) * 100.0, "p": cl})
    if len(res) < 6:
        return
    res.sort(key=lambda x: x["chg"], reverse=True)
    up, dn = res[:3], list(reversed(res[-3:]))
    c_rows = [(x["sym"].replace("USDT", ""), f"{x['chg']:+.2f}% | {akilli_fiyat(x['p'])}", x["chg"]) for x in up + dn]
    png = render_card_png("FUTURES SON 1 SAAT", "En Cok Yukselen 3 & Dusen 3", c_rows, (56, 189, 248))
    msg = "⏱️ <b>Futures Son 1 Saat Ozeti</b>\n\n🟢 " + ", ".join(f"{x['sym'].replace('USDT','')} ({x['chg']:+.2f}%)" for x in up) + "\n🔴 " + ", ".join(f"{x['sym'].replace('USDT','')} ({x['chg']:+.2f}%)" for x in dn)
    tg_photo(C, png, msg)


# =====================================================
# MOTOR 16: ORDERFLOW + FIBO 0.618 + VAH/VAL
# =====================================================
def orderflow_fibo_worker():
    for t in adaylar(MIN_HACIM_ORDERFLOW, 30, "fut"):
        sym = t["symbol"]
        if not cooldown_ok(son_orderflow_sinyalleri, sym, 14400):
            continue
        ks = klines(sym, "4h", 60, "fut")
        if not ks or len(ks) < 40:
            continue
        H = [float(k[2]) for k in ks]
        L = [float(k[3]) for k in ks]
        hi, lo = max(H[-40:]), min(L[-40:])
        f618 = hi - 0.618 * (hi - lo)
        price = t["_price"]
        if abs(price - f618) / f618 > 0.015:
            continue
        imb, bid_u, ask_u, n_ex = fetch_orderbook_imbalance(sym, price)
        if n_ex < 2:
            continue
        yon = "LONG" if imb >= 62 else ("SHORT" if imb <= 38 else None)
        if not yon:
            continue
        tp = price * (1.08 if yon == "LONG" else 0.92)
        sl = price * (0.97 if yon == "LONG" else 1.03)
        png = render_chart_png(sym, "4h", ks, f"ORDERFLOW FIB618 {yon}", [(f618, "FIB618", (250, 204, 21)), (tp, "TP", (34, 197, 94)), (sl, "SL", (239, 68, 68))])
        msg = make_signal("ORDERFLOW + FIB 0.618", sym, yon, price, tp, sl, f"\n📊 <b>Emir Defteri ({n_ex} Borsa):</b> %{imb:.1f} Alis")
        if tg_photo(C, png, msg):
            signal_sent(son_orderflow_sinyalleri, sym)
            record_tracked_signal("ORDERFLOW_FIBO", sym, yon, price)


# =====================================================
# MOTOR 17: SINYAL ZAFERI (GUNUN ZAFERI)
# =====================================================
def sinyal_zaferi_worker():
    wins = get_tracker_stats(hours=24, min_pct=5.0)
    if not wins:
        return
    w = wins[0]
    tstr = datetime.fromtimestamp(w["ts"], TR_Saati).strftime("%H:%M")
    png = render_win_chart_png(w["symbol"], tstr, w["entry"], w["peak"], w["peak_pct"], w.get("pts", []))
    msg = (
        f"🏆 <b>GUNUN ZAFERI: {w['symbol']} +{w['peak_pct']:.1f}%</b>\n"
        f"⏰ Sinyal: {tstr} ({w['motor']} - {w['dir']})\n"
        f"💰 Giris: {akilli_fiyat(w['entry'])} -> Pik: {akilli_fiyat(w['peak'])}"
    )
    tg_photo(C, png, msg)


# =====================================================
# MOTOR 18: SINYAL TOP 24 (24s EN IYI TAKIPLI SINYALLER)
# =====================================================
def sinyal_top24_worker():
    top5 = get_tracker_stats(hours=24, min_pct=1.5)[:5]
    if not top5:
        return
    rows = [(f"{i+1}. {s['symbol'].replace('USDT','')} ({s['dir']})", f"+{s['peak_pct']:.1f}% | {s['motor'][:8]}", s["peak_pct"]) for i, s in enumerate(top5)]
    png = render_card_png("24 SAAT EN IYI SINYALLER", "Bot Sinyal Takip Performansi", rows, (168, 85, 247))
    msg = "🔥 <b>Son 24s En Iyi Bot Sinyalleri</b>\n\n" + "\n".join(
        f"{i+1}. <b>{s['symbol']}</b> ({s['dir']}) <b>+{s['peak_pct']:.1f}%</b>" for i, s in enumerate(top5)
    )
    tg_photo(C, png, msg)


# =====================================================
# MOTOR 19: CVD FLOW (SPOT vs FUTURES CVD + OI + UYUMSUZLUK)
# =====================================================
def analyze_cvd_signal(fk, sc, fc, oi_pct):
    if len(fk) < 20:
        return None
    lows = [float(k[3]) for k in fk]
    highs = [float(k[2]) for k in fk]
    mid = len(fk) // 2
    p_low1, p_low2 = min(lows[:mid]), min(lows[mid:])
    p_hi1, p_hi2 = max(highs[:mid]), max(highs[mid:])
    s1, s2 = sc[mid - 1], sc[-1]
    f1, f2 = fc[mid - 1], fc[-1]
    sd_recent, fd_recent = sc[-1] - sc[-7], fc[-1] - fc[-7]

    if p_low2 < p_low1 * 0.995 and (s2 > s1 or f2 > f1) and (sd_recent > 0 or fd_recent > 0):
        return ("LONG", "BULL DIV", "Spot/Vadeli CVD dipten alici topluyor")
    if p_hi2 > p_hi1 * 1.005 and (s2 < s1 or f2 < f1) and (sd_recent < 0 or fd_recent < 0):
        return ("SHORT", "BEAR DIV", "Fiyat tepe yaparken CVD zayifliyor")
    if sd_recent > 0 and fd_recent > 0 and oi_pct >= 2.0:
        return ("LONG", "BOTH BUYING + OI", "Spot ve Futures birlikte aliyor")
    if sd_recent < 0 and fd_recent < 0 and oi_pct >= 2.0:
        return ("SHORT", "BOTH SELLING + OI", "Spot ve Futures birlikte satiyor")
    return None


def cvd_flow_worker():
    for t in adaylar(MIN_HACIM_CVD, 25, "fut"):
        sym = t["symbol"]
        if not cooldown_ok(son_cvd_sinyalleri, sym, 14400):
            continue
        res = fetch_spot_futures_cvd(sym, 36)
        if not res:
            continue
        fk, sc, fc, oi_pct = res
        sig = analyze_cvd_signal(fk, sc, fc, oi_pct)
        if not sig:
            continue
        yon, durum, aciklama = sig
        c = float(fk[-1][4])
        tp = c * (1.08 if yon == "LONG" else 0.92)
        sl = c * (0.96 if yon == "LONG" else 1.04)
        png = render_cvd_chart_png(sym, fk, sc, fc, oi_pct, durum, durum if "DIV" in durum else "")
        msg = make_signal(f"CVD AKIS ({durum})", sym, yon, c, tp, sl, f"\n📊 Spot CVD: {fmt_usd(sc[-1])} | Fut CVD: {fmt_usd(fc[-1])} | OI: {oi_pct:+.2f}%\n💡 {aciklama}")
        if tg_photo(C, png, msg):
            signal_sent(son_cvd_sinyalleri, sym)
            record_tracked_signal("CVD_FLOW", sym, yon, c)


# =====================================================
# MOTOR 20: DCA WATCHLIST (3COMMAS EKSPONANSIYEL KADEMELI ALIM)
# - Base Order (1x)
# - DCA1 (-5%, 2x), DCA2 (-13%, 4x), DCA3 (-34%, 8x)
# - Hedef: Ortalama maliyet uzerinden +%2 ve uzeri
# =====================================================
def calc_dca_plan(entry):
    entry = float(entry)
    dca1 = entry * 0.95   # -%5 (2x boyut)
    dca2 = entry * 0.87   # -%13 (4x boyut)
    dca3 = entry * 0.66   # -%34 (8x boyut)
    avg1 = (entry * 1.0 + dca1 * 2.0) / 3.0
    avg2 = (entry * 1.0 + dca1 * 2.0 + dca2 * 4.0) / 7.0
    avg_all = (entry * 1.0 + dca1 * 2.0 + dca2 * 4.0 + dca3 * 8.0) / 15.0
    return {
        "entry": entry,
        "dca1": dca1,
        "dca2": dca2,
        "dca3": dca3,
        "avg1": avg1,
        "avg2": avg2,
        "avg_all": avg_all,
        "tp_base": entry * 1.025,
        "tp_dca1": avg1 * 1.022,
        "tp_dca2": avg2 * 1.022,
        "tp_dca3": avg_all * 1.022,
    }


def evaluate_dca_candidate(symbol, chg24, ks_4h, ks_1d):
    if not ks_4h or len(ks_4h) < 30 or not ks_1d or len(ks_1d) < 14:
        return None
    closes_4h = [float(k[4]) for k in ks_4h]
    highs_1d = [float(k[2]) for k in ks_1d[-10:]]
    vols_4h = [float(k[7]) for k in ks_4h]

    curr = closes_4h[-1]
    if curr <= 0:
        return None
    rsi = calculate_rsi(closes_4h, 14)
    peak_10d = max(highs_1d)
    pullback_pct = ((peak_10d - curr) / peak_10d) * 100.0 if peak_10d > 0 else 0.0

    avg_vol = sum(vols_4h[-21:-1]) / 20.0 if len(vols_4h) >= 21 else 1.0
    vol_ratio = vols_4h[-1] / avg_vol if avg_vol > 0 else 1.0

    if not (rsi <= 42.0 or (10.0 <= pullback_pct <= 42.0)):
        return None
    if pullback_pct < 6.0 or pullback_pct > 55.0:
        return None
    if vol_ratio < 0.75:
        return None

    score = max(0.0, (48.0 - rsi)) * 1.6 + min(pullback_pct, 35.0) * 0.9 + min(vol_ratio, 3.0) * 8.0
    plan = calc_dca_plan(curr)
    plan.update({
        "symbol": symbol,
        "rsi": rsi,
        "chg": chg24,
        "pullback": pullback_pct,
        "vol_ratio": vol_ratio,
        "score": score,
    })
    return plan


def dca_watchlist_worker():
    aday_list = adaylar(MIN_HACIM_DCA, 40, "fut")
    candidates = []
    for t in aday_list:
        sym = t["symbol"]
        if not cooldown_ok(son_dca_sinyalleri, sym, 43200):
            continue
        k4 = klines(sym, "4h", 48, "fut")
        k1d = klines(sym, "1d", 14, "fut")
        cand = evaluate_dca_candidate(sym, t["_change"], k4, k1d)
        if cand:
            candidates.append(cand)

    if not candidates:
        return

    candidates.sort(key=lambda x: x["score"], reverse=True)
    top = candidates[:5]
    png = render_dca_watchlist_png(top)

    lines = [
        "💎 <b>CRYPTO WATCHLIST - DCA STRATEJISI (YAKIN GUNLER)</b>",
        "<i>Asiri satim &amp; geri cekilme sonrasi kademeli alim adaylari</i>",
        "<i>Kademeler: Base (1x) | -5% (2x) | -13% (4x) | -34% (8x) | TP: +%2+</i>\n"
    ]
    for i, c in enumerate(top, 1):
        lines.append(
            f"{i}. <b>{c['symbol']}</b> | Giris: <b>{akilli_fiyat(c['entry'])}</b> (RSI: {c['rsi']:.0f}, Cekilme: -{c['pullback']:.1f}%)\n"
            f"   📥 DCA1(-5%): {akilli_fiyat(c['dca1'])} | DCA2(-13%): {akilli_fiyat(c['dca2'])} | DCA3(-34%): {akilli_fiyat(c['dca3'])}\n"
            f"   ⚖️ Ort. Maliyet: {akilli_fiyat(c['avg_all'])} | 🎯 TP(+%2+): {akilli_fiyat(c['tp_base'])} (DCA TP: {akilli_fiyat(c['tp_dca3'])})"
        )

    msg = "\n".join(lines)
    if tg_photo(C, png, msg):
        for c in top:
            signal_sent(son_dca_sinyalleri, c["symbol"])
        record_tracked_signal("DCA_WATCHLIST", top[0]["symbol"], "LONG", top[0]["entry"])
        log("DCA_WATCHLIST", f"{len(top)} coinlik DCA Watchlist gonderildi.")


# =====================================================
# RENDER WEB SUNUCUSU
# =====================================================
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"Crypto Signal Bot v20 (20 Motors) is running."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string, *args):
        return


def run_web_server():
    port = int(os.environ.get("PORT", "10000"))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    log("SISTEM", f"WEB SUNUCUSU BASLADI - PORT: {port}")
    server.serve_forever()


if __name__ == "__main__":
    _load_tracker()
    log("SISTEM", "Multi-Exchange Sinyal Botu v20 (20 Motor + DCA Watchlist) baslatiliyor.")

    motors = [
        ("TOP_GAINERS", TOP_GAINERS_ARALIGI, top_gainers_worker, 10),
        ("PUMP_WINNER", PUMP_WINNER_ARALIGI, pump_winner_worker, 20),
        ("DOMINANS", DOMINANS_ARALIGI, dominans_worker, 25),
        ("CHARTIST", CHARTIST_ARALIGI, chartist_worker, 30),
        ("PUMP", PUMP_ARALIGI, pump_worker, 40),
        ("VOLATILITE", VOLATILITE_ARALIGI, volatilite_worker, 50),
        ("FORMASYON", FORMASYON_ARALIGI, formasyon_worker, 60),
        ("ICHIMOKU", ICHIMOKU_ARALIGI, ichimoku_worker, 75),
        ("EMA_RIBBON", EMA_RIBBON_ARALIGI, ema_ribbon_worker, 90),
        ("MACD", MACD_ARALIGI, macd_worker, 105),
        ("SMA200", SMA200_ARALIGI, sma200_worker, 120),
        ("ATH_ATL", ATH_ATL_ARALIGI, ath_atl_worker, 135),
        ("PATTERN", CHART_PATTERN_ARALIGI, chart_patterns_worker, 150),
        ("ANLIK_ATH_ATL", ANLIK_ATH_ATL_ARALIGI, anlik_ath_atl_worker, 165),
        ("TOP_MOVERS", TOP_MOVERS_ARALIGI, top_movers_worker, 180),
        ("ORDERFLOW_FIBO", ORDERFLOW_FIBO_ARALIGI, orderflow_fibo_worker, 195),
        ("SINYAL_ZAFERI", SINYAL_ZAFERI_ARALIGI, sinyal_zaferi_worker, 210),
        ("SINYAL_TOP24", SINYAL_TOP24_ARALIGI, sinyal_top24_worker, 225),
        ("CVD_FLOW", CVD_FLOW_ARALIGI, cvd_flow_worker, 240),
        ("DCA_WATCHLIST", DCA_WATCHLIST_ARALIGI, dca_watchlist_worker, 255),
    ]

    for name, interval, fn, startup in motors:
        threading.Thread(target=safe_loop, args=(name, interval, fn, startup), name=name, daemon=True).start()

    try:
        run_web_server()
    except Exception as e:
        log("RENDER", f"Web sunucusu baslatilamadi: {e}")
        while True:
            time.sleep(60)

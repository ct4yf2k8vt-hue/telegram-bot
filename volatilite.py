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
# TURKCE EGITICI KRIPTO SINYAL BOTU v21 (25 MOTOR)
# Saf Python Standart Kutuphane (Render Uyumlu, pip yok)
# Yuksek Kaliteli Coklu Teyit + Detayli Turkce Egitici Anlatim
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
GATE = "https://api.gateio.ws"
CG = "https://api.coingecko.com"

# Motor calisma araliklari (saniye)
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
SPOT_PUMP_7M_ARALIGI = 60
YATAY_SR_1H_ARALIGI = 1800
BTC_LIKIDITE_ARALIGI = 3600
CANLI_TP_TAKIP_ARALIGI = 180
RR_KARNE_ARALIGI = 43200

# Minimum 24s hacimler (USDT) - Yuksek Kalite Esikleri
MIN_HACIM_CHARTIST = 12_000_000
MIN_HACIM_PUMP = 8_000_000
MIN_HACIM_VOLATILITE = 12_000_000
MIN_HACIM_FORMASYON = 15_000_000
MIN_HACIM_ICHIMOKU = 12_000_000
MIN_HACIM_EMA_RIBBON = 12_000_000
MIN_HACIM_MACD = 12_000_000
MIN_HACIM_SMA200 = 12_000_000
MIN_HACIM_ATH_ATL = 8_000_000
MIN_HACIM_PATTERN = 15_000_000
MIN_HACIM_ANLIK_ATH = 10_000_000
MIN_HACIM_MOVERS = 8_000_000
MIN_HACIM_ORDERFLOW = 15_000_000
MIN_HACIM_CVD = 25_000_000
MIN_HACIM_DCA = 12_000_000
MIN_HACIM_YATAY_SR = 15_000_000

MAX_TARAMA = 60

YASAKLI_HISSELER = {
    "AAPLUSDT", "TSLAUSDT", "NVDAUSDT", "COINUSDT", "MSTRUSDT",
    "SQQQUSDT", "TQQQUSDT", "SPYUSDT", "QQQUSDT", "AMZNUSDT",
    "GOOGLUSDT", "METAUSDT", "NFLXUSDT", "BABAUSDT", "MSFTUSDT",
    "HOODUSDT", "CRCLUSDT", "PLTRUSDT", "AMDUSDT", "INTCUSDT",
    "GMEUSDT", "AMCUSDT", "NVDAXUSDT", "TSLAXUSDT", "AAPLXUSDT",
    "XAUUSDT", "XAGUSDT", "PAXGUSDT", "USOILUSDT", "ORCLBUSDT"
}

# Cooldown hafizalari
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
son_spot_pump7m_sinyalleri = {}
son_yatay_sr_sinyalleri = {}
son_btc_fvg_sinyalleri = {}

# Kaynak sagligi ve onbellekler
_cache_lock = threading.Lock()
_SOURCE_COOLDOWN = {}
_SOURCE_LAST_ERROR = {}
_ticker_cache = {"fut": (0.0, []), "spot": (0.0, []), "spot_all": (0.0, [])}
_kline_cache = {}
_cvd_cache = {}
_SPOT_WINDOW = {}  # {symbol: [(ts, price), ...]} 0-7 dk kayan pencere

TRACKER_FILE = "/tmp/tracked_signals_v21.json"
TRACKER_LOCK = threading.Lock()
_TRACKED_SIGNALS = []


# =====================================================
# TEMEL YARDIMCILAR
# =====================================================
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


def tg_photo(chat_id, png_bytes, caption="", full_edu_text=""):
    """Grafik + kisa ozet caption (<=1020) gonderir; uzun egitici anlatimi kesintisiz ikinci mesajda yollar."""
    if not T or not chat_id:
        return False
    if not png_bytes:
        combined = f"{caption}\n\n{full_edu_text}".strip() if full_edu_text else caption
        return tg(chat_id, combined)

    cap = (caption or "")[:1020]
    boundary = "----BotBoundary" + uuid.uuid4().hex
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n".encode("utf-8")
    ]
    if cap:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{cap}\r\n".encode("utf-8"))
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"parse_mode\"\r\n\r\nHTML\r\n".encode("utf-8"))
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"chart.png\"\r\n"
        f"Content-Type: image/png\r\n\r\n".encode("utf-8") + png_bytes + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)

    photo_ok = False
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
            photo_ok = json.loads(resp.read().decode("utf-8")).get("ok", False)
    except Exception as e:
        log("TELEGRAM", f"Foto hatasi: {e}")

    if not photo_ok:
        combined = f"{caption}\n\n{full_edu_text}".strip() if full_edu_text else caption
        return tg(chat_id, combined)

    if full_edu_text:
        tg(chat_id, full_edu_text)
    return True


# =====================================================
# DETAYLI TURKCE EGITICI BILDIRIM SABLONU
# =====================================================
def build_edu_signal(
    motor_baslik, symbol, tf, yon,
    entry, tp1, tp2, tp3, sl,
    teyit_satirlari,
    neden_aciklama, terim_sozlugu, risk_notu
):
    em = "🚀" if yon == "LONG" else ("🔻" if yon == "SHORT" else "👀")
    tp1_p = ((tp1 - entry) / entry) * 100.0 if entry > 0 and tp1 else 0.0
    tp2_p = ((tp2 - entry) / entry) * 100.0 if entry > 0 and tp2 else 0.0
    tp3_p = ((tp3 - entry) / entry) * 100.0 if entry > 0 and tp3 else 0.0
    sl_p = ((sl - entry) / entry) * 100.0 if entry > 0 and sl else 0.0
    risk = abs(entry - sl) if sl else 0.0
    rr2 = abs(tp2 - entry) / risk if risk > 0 and tp2 else (abs(tp1 - entry) / risk if risk > 0 and tp1 else 0.0)

    caption = (
        f"{em} <b>{motor_baslik} ({yon}) — {symbol}</b> [{tf.upper()}]\n\n"
        f"💰 <b>Giris:</b> {akilli_fiyat(entry)}\n"
        f"🎯 <b>TP1:</b> {akilli_fiyat(tp1)} ({tp1_p:+.1f}%)\n"
        f"🎯 <b>TP2:</b> {akilli_fiyat(tp2)} ({tp2_p:+.1f}%)\n"
        f"🎯 <b>TP3:</b> {akilli_fiyat(tp3)} ({tp3_p:+.1f}%)\n"
        f"🛑 <b>Stop-Loss (SL):</b> {akilli_fiyat(sl)} ({sl_p:+.1f}%)\n"
        f"⚖️ <b>Risk/Odul (R:R):</b> 1 : {rr2:.2f}\n\n"
        f"🔍 <b>Coklu Teyitler:</b>\n{teyit_satirlari}"
    )

    edu_text = (
        f"📚 <b>EGITICI ANALIZ REHBERI — {symbol} ({motor_baslik})</b>\n\n"
        f"🧠 <b>Bu Sinyal Neden Olustu?</b>\n{neden_aciklama}\n\n"
        f"📖 <b>Teknik Terimler Sozlugu (Bu Sinyal Icin):</b>\n{terim_sozlugu}\n\n"
        f"⚠️ <b>Islem Yonetimi &amp; Risk Kurali:</b>\n{risk_notu}"
    )
    return caption, edu_text


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

    def box_outline(self, x, y, rw, rh, col):
        self.rect(x, y, rw, 1, col)
        self.rect(x, y + rh - 1, rw, 1, col)
        self.rect(x, y, 1, rh, col)
        self.rect(x + rw - 1, y, 1, rh, col)

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
        for src_c, dst_c in (("Ş", "S"), ("Ğ", "G"), ("Ü", "U"), ("Ö", "O"), ("Ç", "C"), ("İ", "I")):
            s = s.replace(src_c, dst_c)
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


def render_chart_png(symbol, tf, klines_list, title="", lines=None, emas=None, trend_lines=None, boxes=None):
    try:
        full_n = len(klines_list)
        ks = klines_list[-70:] if full_n > 70 else list(klines_list)
        offset = max(0, full_n - len(ks))
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

        p_min, p_max = min(lows), max(highs)
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

        n = len(ks)
        slot = (R - L) / max(n, 1)

        def px_idx(idx):
            local = idx - offset
            local = max(0, min(n - 1, local))
            return int(L + local * slot + slot * 0.5)

        for g in range(5):
            gy = int(T_Y + g * (B_Y - T_Y) / 4)
            cv.hline_dashed(L, R, gy, (30, 38, 56), 4, 6)
            gp = p_max - g * span / 4
            cv.text(R + 8, gy - 4, akilli_fiyat(gp), (140, 152, 175), scale=1)

        if boxes:
            for i0, i1, vlo, vhi, bcol in boxes:
                bx0, bx1 = px_idx(i0), px_idx(i1)
                by0, by1 = py(vhi), py(vlo)
                cv.rect(min(bx0, bx1), min(by0, by1), max(6, abs(bx1 - bx0)), max(4, abs(by1 - by0)), (32, 46, 70))
                cv.box_outline(min(bx0, bx1), min(by0, by1), max(6, abs(bx1 - bx0)), max(4, abs(by1 - by0)), bcol)

        cw = max(3, int(slot * 0.65))
        v_max = max(vols) or 1.0
        for i in range(n):
            cx = int(L + i * slot + slot * 0.5)
            o, h, l, c, v = opens[i], highs[i], lows[i], closes[i], vols[i]
            up = c >= o
            col = (38, 198, 132) if up else (235, 84, 98)
            cv.line(cx, py(h), cx, py(l), col, 1)
            y_o, y_c = py(o), py(c)
            cv.rect(cx - cw // 2, min(y_o, y_c), cw, max(2, abs(y_c - y_o)), col)
            vh = int((v / v_max) * (VOL_B - VOL_T))
            cv.rect(cx - cw // 2, VOL_B - vh, cw, vh, (26, 110, 82) if up else (130, 48, 58))

        if emas:
            for e_vals, e_col in emas:
                if len(e_vals) >= n:
                    sub = e_vals[-n:]
                    for i in range(1, n):
                        x0 = int(L + (i - 1) * slot + slot * 0.5)
                        x1 = int(L + i * slot + slot * 0.5)
                        cv.line(x0, py(sub[i - 1]), x1, py(sub[i]), e_col, 2)

        if trend_lines:
            for i0, p0, i1, p1, tcol in trend_lines:
                cv.line(px_idx(i0), py(p0), px_idx(i1), py(p1), tcol, 2)

        if lines:
            for item in lines:
                val, lbl, col = item
                if val and p_min <= val <= p_max:
                    ly = py(val)
                    cv.hline_dashed(L, R, ly, col, 7, 4)
                    cv.rect(R + 4, ly - 7, 120, 14, col)
                    cv.text(R + 8, ly - 3, f"{lbl} {akilli_fiyat(val)}"[:19], (12, 16, 24), scale=1)

        cv.text(18, 424, f"SON FIYAT: {akilli_fiyat(closes[-1])}", (180, 195, 220), scale=1)
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
            cv.line(x0, py(pts[i - 1]), x1, py(pts[i]), (56, 189, 248), 3)

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
        ks, sc, fc = ks[-n:], spot_cvd[-n:], fut_cvd[-n:]

        W, H = 820, 480
        cv = _Canvas(W, H, (13, 17, 26))
        cv.rect(0, 0, W, 44, (20, 26, 40))
        cv.text(16, 13, f"{symbol} | 1H CVD | {status_txt}", (240, 245, 255), scale=2)
        if div_txt:
            cv.rect(W - 180, 8, 165, 28, (34, 197, 94) if "BULL" in div_txt else (239, 68, 68))
            cv.text(W - 170, 15, div_txt[:15], (10, 14, 22), scale=2)

        L, R, T1, B1 = 20, 700, 56, 248
        highs = [float(k[2]) for k in ks]
        lows = [float(k[3]) for k in ks]
        opens = [float(k[1]) for k in ks]
        closes = [float(k[4]) for k in ks]
        p_min, p_max = min(lows), max(highs)
        pad = (p_max - p_min) * 0.08 or p_max * 0.01
        p_min, p_max = p_min - pad, p_max + pad
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
            cv.text(36, y + 40, f"DCA1:{akilli_fiyat(it['dca1'])}  DCA2:{akilli_fiyat(it['dca2'])}  DCA3:{akilli_fiyat(it['dca3'])}", (165, 185, 215), scale=1)
            cv.text(36, y + 56, f"ORT MALIYET:{akilli_fiyat(it['avg_all'])}  |  HEDEF TP(+%2+): {akilli_fiyat(it['tp_base'])}", (56, 189, 248), scale=1)
        return cv.to_png()
    except Exception:
        return b""


# =====================================================
# COKLU BORSA VERI KATMANI & ANTI-STALE KONTROL
# =====================================================
def _bar_fresh(klines_list, interval, max_mult=3.0):
    if not klines_list or len(klines_list) < 2:
        return False
    iv_sec = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "8h": 28800, "12h": 43200, "1d": 86400, "3d": 259200, "1w": 604800}.get(interval, 3600)
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


def spot_all_quote_tickers(force=False):
    """USDT, BTC ve BNB Spot pariteleri (Anlik 0-7 dk Pump Dedektoru icin)."""
    now = time.time()
    with _cache_lock:
        ts, cached = _ticker_cache["spot_all"]
        if not force and cached and now - ts < 40:
            return cached

    rows = []
    for src, base in (("BINANCE_VISION", VISION), ("BINANCE_SPOT", SPOT)):
        data = gj(f"{base}/api/v3/ticker/24hr", src)
        if isinstance(data, list):
            for t in data:
                s = t.get("symbol", "")
                if s in YASAKLI_HISSELER or any(x in s for x in ("UPUSDT", "DOWNUSDT", "BULL", "BEAR")):
                    continue
                if s.endswith(("USDT", "BTC", "BNB")):
                    try:
                        lp = float(t["lastPrice"])
                        qv = float(t["quoteVolume"])
                        if lp > 0 and qv > 0:
                            rows.append(_norm_ticker(s, lp, t["priceChangePercent"], qv, src))
                    except Exception:
                        pass
            if rows:
                break

    if rows:
        with _cache_lock:
            _ticker_cache["spot_all"] = (now, rows)
    return rows or cached


def adaylar(min_hacim, limit=MAX_TARAMA, market="fut"):
    src = futures_tickers() if market == "fut" else spot_tickers()
    rows = [t for t in src if t["_quote"] >= min_hacim and t["_price"] > 0]
    rows.sort(key=lambda x: x["_quote"], reverse=True)
    return rows[:limit]


def _bybit_klines(symbol, interval, limit, category="linear"):
    iv_map = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D", "1w": "W"}
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


def _linreg_slope(vals):
    n = len(vals)
    if n < 2:
        return 0.0
    xm = (n - 1) / 2.0
    ym = sum(vals) / n
    num = sum((i - xm) * (v - ym) for i, v in enumerate(vals))
    den = sum((i - xm) ** 2 for i in range(n))
    return num / den if den > 0 else 0.0


def trap_radar_check(symbol):
    oi = gj(f"{FAPI}/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=2", "BINANCE_FAPI")
    taker = gj(f"{FAPI}/futures/data/takerlongshortRatio?symbol={symbol}&period=5m&limit=1", "BINANCE_FAPI")
    oi_up, buyers, buy_pct = False, False, 50.0
    try:
        if isinstance(oi, list) and len(oi) >= 2:
            old = float(oi[-2]["sumOpenInterest"])
            new = float(oi[-1]["sumOpenInterest"])
            oi_up = old > 0 and (new - old) / old * 100 > 0.2
        if isinstance(taker, list) and taker:
            b, s = float(taker[-1]["buyVol"]), float(taker[-1]["sellVol"])
            if b + s > 0:
                buy_pct = (b / (b + s)) * 100.0
                buyers = buy_pct > 55.0
    except Exception:
        pass
    if oi_up and buyers:
        return True, f"🐋 Balina Teyidi (Acik Pozisyon Artiyor + %{buy_pct:.0f} Alici Baskin)"
    if not buyers:
        return False, f"⚠️ Alici Teyidi Zayif (%{buy_pct:.0f} Alici — Fake/Tuzak Riski)"
    return True, f"⚖️ Kismi Teyit (%{buy_pct:.0f} Alici)"


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
            qv, tbq = float(k[7]), float(k[10])
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
                fut_deltas.append(qv * tv_map.get(ts_hr, 0.0))

    if not sk or not fk or not fut_deltas:
        return None

    spot_deltas = [2.0 * float(k[10]) - float(k[7]) for k in sk]
    n = min(len(spot_deltas), len(fut_deltas), len(fk))
    if n < 15:
        return None
    spot_deltas, fut_deltas, fk = spot_deltas[-n:], fut_deltas[-n:], fk[-n:]

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
        o0, o1 = float(oi[0]["sumOpenInterestValue"]), float(oi[-1]["sumOpenInterestValue"])
        if o0 > 0:
            oi_pct = ((o1 - o0) / o0) * 100.0
    else:
        boi = gj(f"{BYBIT}/v5/market/open-interest?category=linear&symbol={symbol}&intervalTime=1h&limit=24", "BYBIT")
        lst = (boi or {}).get("result", {}).get("list", []) if isinstance(boi, dict) else []
        if len(lst) >= 4:
            o0, o1 = float(lst[-1]["openInterest"]), float(lst[0]["openInterest"])
            if o0 > 0:
                oi_pct = ((o1 - o0) / o0) * 100.0

    out = (fk, sc, fc, oi_pct)
    with _cache_lock:
        _cvd_cache[key] = (time.time(), out)
    return out


# =====================================================
# GELISMIS SIGNAL PERFORMANCE TRACKER (TP1/TP2/TP3 + SL + R-MULTIPLE)
# =====================================================
def _load_tracker():
    global _TRACKED_SIGNALS
    try:
        if os.path.exists(TRACKER_FILE):
            with open(TRACKER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    _TRACKED_SIGNALS = data[-2000:]
    except Exception:
        _TRACKED_SIGNALS = []


def _save_tracker():
    try:
        with open(TRACKER_FILE, "w", encoding="utf-8") as f:
            json.dump(_TRACKED_SIGNALS[-2000:], f)
    except Exception:
        pass


def record_tracked_signal(motor, symbol, direction, entry, tp1=None, tp2=None, tp3=None, sl=None):
    if not entry or entry <= 0:
        return
    entry = float(entry)
    if not tp1:
        tp1 = entry * (1.03 if direction == "LONG" else 0.97)
    if not tp2:
        tp2 = entry * (1.06 if direction == "LONG" else 0.94)
    if not tp3:
        tp3 = entry * (1.10 if direction == "LONG" else 0.90)
    if not sl:
        sl = entry * (0.97 if direction == "LONG" else 1.03)
    now = time.time()
    with TRACKER_LOCK:
        _TRACKED_SIGNALS.append({
            "motor": motor,
            "symbol": symbol,
            "dir": direction,
            "entry": entry,
            "tp1": float(tp1),
            "tp2": float(tp2),
            "tp3": float(tp3),
            "sl": float(sl),
            "hit_tp1": False,
            "hit_tp2": False,
            "hit_tp3": False,
            "stopped": False,
            "peak": entry,
            "peak_pct": 0.0,
            "max_r": 0.0,
            "ts": now,
            "peak_ts": now,
            "pts": [entry]
        })
        if len(_TRACKED_SIGNALS) > 2000:
            del _TRACKED_SIGNALS[:-2000]
        _save_tracker()


def update_tracked_signals(notify_tp_hits=False):
    now = time.time()
    fut_map = {t["symbol"]: t["_price"] for t in futures_tickers()}
    spot_map = {t["symbol"]: t["_price"] for t in spot_tickers()}
    tp_alerts = []

    with TRACKER_LOCK:
        cutoff = now - 7 * 86400
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

            is_long = s["dir"] == "LONG"
            entry = s["entry"]
            sl = s.get("sl", entry * (0.97 if is_long else 1.03))
            risk = max(abs(entry - sl), entry * 0.005)

            pct = ((p - entry) / entry) * 100.0 if is_long else ((entry - p) / entry) * 100.0
            r_now = ((p - entry) / risk) if is_long else ((entry - p) / risk)

            if pct > s.get("peak_pct", 0.0):
                s["peak_pct"] = pct
                s["peak"] = p
                s["peak_ts"] = now
            if r_now > s.get("max_r", 0.0):
                s["max_r"] = r_now

            if not s.get("stopped"):
                sl_hit = (p <= sl) if is_long else (p >= sl)
                if sl_hit and not s.get("hit_tp1"):
                    s["stopped"] = True

            if not s.get("stopped"):
                for lvl, key in (("TP3", "hit_tp3"), ("TP2", "hit_tp2"), ("TP1", "hit_tp1")):
                    target_val = s.get(lvl.lower())
                    if target_val and not s.get(key):
                        reached = (p >= target_val) if is_long else (p <= target_val)
                        if reached:
                            s[key] = True
                            if lvl == "TP3":
                                s["hit_tp1"] = True
                                s["hit_tp2"] = True
                            elif lvl == "TP2":
                                s["hit_tp1"] = True
                            if notify_tp_hits:
                                tp_alerts.append((dict(s), lvl, p, pct, r_now))
                            break
        _save_tracker()

    for sig_copy, lvl, live_p, pct, r_now in tp_alerts:
        tstr = datetime.fromtimestamp(sig_copy["ts"], TR_Saati).strftime("%d.%m %H:%M")
        msg = (
            f"🎯 <b>HEDEF VURDU ({lvl} Ulasildi!) — {sig_copy['symbol']} ({sig_copy['dir']})</b>\n\n"
            f"📌 <b>Kaynak Motor:</b> {sig_copy['motor']}\n"
            f"⏰ <b>Sinyal Saati:</b> {tstr}\n"
            f"💰 <b>Giris:</b> {akilli_fiyat(sig_copy['entry'])}  ➡️  <b>Anlik:</b> {akilli_fiyat(live_p)}\n"
            f"📈 <b>Net Hareket:</b> <b>{pct:+.2f}%</b>  |  ⚖️ <b>Kazanilan R:</b> <b>{r_now:.2f}R</b>\n\n"
            f"💡 <b>Egitici Not:</b> <i>Fiyat {lvl} hedefimize ulasti. Profesyonel risk yonetiminde TP1/TP2 seviyelerinde kismi kar alip Stop-Loss seviyesini giris fiyatina (Break-Even) cekmek kalan pozisyonu risksiz tasimanizi saglar.</i>"
        )
        tg(C, msg)


def get_tracker_stats(hours=24, min_pct=1.5):
    update_tracked_signals(False)
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


def compute_rr_report(hours=168):
    update_tracked_signals(False)
    cutoff = time.time() - hours * 3600
    with TRACKER_LOCK:
        items = [dict(s) for s in _TRACKED_SIGNALS if s["ts"] >= cutoff]
    if not items:
        return None
    total = len(items)
    losses = sum(1 for x in items if x.get("stopped") or x.get("max_r", 0.0) < 1.0)
    r1 = sum(1 for x in items if x.get("max_r", 0.0) >= 1.0)
    r2 = sum(1 for x in items if x.get("max_r", 0.0) >= 2.0)
    r3 = sum(1 for x in items if x.get("max_r", 0.0) >= 3.0)
    r5 = sum(1 for x in items if x.get("max_r", 0.0) >= 5.0)
    best_item = max(items, key=lambda x: x.get("max_r", 0.0))
    max_roi = sum((x["max_r"] if x.get("max_r", 0.0) >= 1.0 else -1.0) for x in items)
    return {
        "total": total,
        "losses": losses,
        "r1": r1,
        "r2": r2,
        "r3": r3,
        "r5": r5,
        "win_rate_1r": (r1 / total) * 100.0,
        "win_rate_2r": (r2 / total) * 100.0,
        "max_roi": max_roi,
        "best_sym": best_item["symbol"],
        "best_r": best_item.get("max_r", 0.0),
    }


def cooldown_ok(store, key, sec):
    return time.time() - store.get(key, 0.0) >= sec


def signal_sent(store, key):
    store[key] = time.time()


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
# MOTOR 1-3: PIYASA RAPORLARI (TOP GAINERS / PUMP WINNER / DOMINANS)
# =====================================================
def top_gainers_worker():
    rows = [t for t in spot_tickers() if t["_quote"] > 5_000_000 and t["_change"] > 0]
    rows.sort(key=lambda x: x["_change"], reverse=True)
    top5 = rows[:5]
    if not top5:
        return
    c_rows = [(f"{i+1}. {t['symbol'].replace('USDT','')}", f"+{t['_change']:.1f}% | {akilli_fiyat(t['_price'])}", t["_change"]) for i, t in enumerate(top5)]
    png = render_card_png("TOP GAINERS (24 SAAT SPOT)", "5M+ USDT Hacim Teyitli En Cok Yukselenler", c_rows, (34, 197, 94))
    cap = "🔥 <b>Gunun En Cok Yukselenleri (24s Spot)</b>\n\n" + "\n".join(
        f"{i+1}. <b>{t['symbol'].replace('USDT','')}</b> +{t['_change']:.1f}% | {akilli_fiyat(t['_price'])}" for i, t in enumerate(top5)
    )
    edu = (
        "📚 <b>EGITICI PIYASA NOTU (TOP GAINERS):</b>\n"
        "• Bu liste son 24 saatte en az 5 Milyon $ hacim uretmis ve en yuksek yuzdelik yukselisi yakalamis 5 coini gosterir.\n"
        "• <b>Nasil Kullanilir?</b> Zirveden FOMO ile kovalamak yerine, bu coinlerdeki para girisini izleyin; ilk geri cekilmede (destek/Fib 0.618 retesti) veya asiri sismede SHORT donus firsati verebilirler."
    )
    tg_photo(C, png, cap, edu)


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
    png = render_chart_png(sym, "1h", ks_1h, f"GUNUN KAZANANI +{mv:.1f}%", [(lo, "DIP", (250, 204, 21)), (hi, "ZIRVE", (34, 197, 94))]) if ks_1h else b""
    cap = f"🏆 <b>GUNUN PUMP KAZANANI: {sym}</b>\n📉 Dip: {akilli_fiyat(lo)} ➡️ 🚀 Zirve: {akilli_fiyat(hi)} (<b>+{mv:.1f}%</b>)"
    edu = (
        f"📚 <b>EGITICI ANALIZ — {sym}:</b>\n"
        "• Vadeli piyasada 10M+ USDT hacimle gunun en guclu yukselis genisligini yakaladi.\n"
        f"• <b>Dip-Zirve Mesafesi:</b> Gun ici en dusuk ve en yuksek nokta arasindaki %{mv:.1f} marji piyasanin bu coine agresif likidite aktardigini gosterir."
    )
    tg_photo(C, png, cap, edu)


def dominans_worker():
    g = gj(f"{CG}/api/v3/global", "COINGECKO")
    pct = (g or {}).get("data", {}).get("market_cap_percentage", {})
    if not pct:
        return
    btc, eth = float(pct.get("btc", 0)), float(pct.get("eth", 0))
    oth = max(0.0, 100.0 - btc - eth)
    rows = [("BTC DOMINANS", f"%{btc:.2f}", btc), ("ETH DOMINANS", f"%{eth:.2f}", eth), ("DIGER ALTCOINLER", f"%{oth:.2f}", oth)]
    png = render_card_png("PIYASA DOMINANSI", "CoinGecko Global Piyasa Hakimiyeti", rows, (250, 204, 21))
    cap = f"🌐 <b>PIYASA DOMINANSI</b>\n₿ BTC: %{btc:.2f} | ♦️ ETH: %{eth:.2f} | 🪙 Altlar: %{oth:.2f}"
    edu = (
        "📚 <b>DOMINANS NASIL OKUNUR?</b>\n"
        "• <b>BTC Dominansi (BTC.D):</b> Toplam kripto para piyasasindaki paranin yuzde kacinin Bitcoin'de tutuldugunu gosterir.\n"
        "• <b>Para Akisi Kurali:</b> BTC fiyati yatayken BTC.D dusuyorsa para altcoinlere kayiyor (Altcoin Rallisi sinyali). BTC.D hizla yukseliyorsa sermaye guvenli liman olarak Bitcoin'e donuyordur."
    )
    tg_photo(C, png, cap, edu)


# =====================================================
# MOTOR 4: CHARTIST (SPOT EMA200 + COKLU TEYIT)
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
            rsi = calculate_rsi(closes, 14)
            vm = vols[-1] / avg if avg > 0 else 0
            if prev <= e200[-2] and curr > e200[-1] * 1.01 and vm >= 1.3 and 50 <= rsi <= 76:
                sl = e200[-1] * 0.97
                tp1, tp2, tp3 = curr * 1.07, curr * 1.15, curr * 1.25
                png = render_chart_png(sym, tf, ks, "SPOT EMA200 KIRILIMI", [(curr, "GIRIS", (56, 189, 248)), (tp2, "TP2", (34, 197, 94)), (sl, "SL", (239, 68, 68))], [(e200, (250, 204, 21))])
                cap, edu = build_edu_signal(
                    f"CHARTIST {tf.upper()} EMA200", sym, tf, "LONG", curr, tp1, tp2, tp3, sl,
                    f"• Hacim: Ortalamanin {vm:.1f}x kati\n• RSI(14): {rsi:.1f} (Momentum saglikli)",
                    f"Fiyat {tf.upper()} grafikte uzun vadeli ana trend siniri olan 200 periyotluk Ussel Hareketli Ortalamayi (EMA200) en az %1 farkla ve yuksek hacimle yukari kirdi.",
                    "• <b>EMA200:</b> Kurumsal yatirimcilarin ayi ve boga piyasasini ayirmak icin kullandigi en guclu dinamik destek/direnc cizgisidir.\n• <b>Hacim Teyidi:</b> Kirilimin sahte (fakeout) olmamasi icin ortalama ustu alim gucu gerektigini gosterir.",
                    "Stop-Loss seviyesi EMA200'un %3 altindadir. Fiyat tekrar EMA200 altinda gunluk kapanis yaparsa trend gecersiz kalir."
                )
                if tg_photo(C, png, cap, edu):
                    signal_sent(son_sinyaller, ck)
                    record_tracked_signal("CHARTIST", sym, "LONG", curr, tp1, tp2, tp3, sl)


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
        if pchg >= 3.2 and vchg >= 120.0 and (cv - pv) >= 75_000:
            ok_trap, trap_txt = trap_radar_check(sym)
            if not ok_trap:
                continue
            sl = float(ck[3]) * 0.992
            tp1, tp2, tp3 = cc * 1.025, cc * 1.05, cc * 1.09
            png = render_chart_png(sym, "5m", ks[:-1], f"PUMP +{pchg:.1f}%", [(cc, "GIRIS", (56, 189, 248)), (tp2, "TP2", (34, 197, 94)), (sl, "SL", (239, 68, 68))])
            cap, edu = build_edu_signal(
                "FUTURES 5M PUMP + TRAP RADAR", sym, "5m", "LONG", cc, tp1, tp2, tp3, sl,
                f"• 5m Fiyat Artisi: +{pchg:.2f}% | Hacim Artisi: +{vchg:.0f}%\n• {trap_txt}",
                "5 dakikalik grafikte ani bir fiyat ve hacim sicramasi yasandi. Trap Radar bu hareketin arkasinda gercek taker alici ve acik pozisyon destegi oldugunu dogruladi.",
                "• <b>Trap Radar (Tuzak Radari):</b> Ani yukselislerin gercek balina alimi mi yoksa likidasyon avi (sahte pump) mi oldugunu ayirt etmek icin Acik Pozisyon (OI) ve Taker Alis/Satis oranina bakar.",
                "Kisa vadeli momentum islemidir. Stop-Loss pump mumunun dibinin hemen altindadir; hizli kar realizasyonu onerilir."
            )
            if tg_photo(C, png, cap, edu):
                signal_sent(son_pump_sinyalleri, sym)
                record_tracked_signal("PUMP", sym, "LONG", cc, tp1, tp2, tp3, sl)


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
        if abs(pct) >= 2.8 and vm >= 3.2:
            yon = "LONG" if pct > 0 else "SHORT"
            sl = cl * (0.975 if yon == "LONG" else 1.025)
            tp1 = cl * (1.025 if yon == "LONG" else 0.975)
            tp2 = cl * (1.05 if yon == "LONG" else 0.95)
            tp3 = cl * (1.08 if yon == "LONG" else 0.92)
            png = render_chart_png(sym, "15m", ks[:-1], f"VOLATILITE {pct:+.1f}%", [(cl, "KAPANIS", (250, 204, 21)), (tp2, "TP2", (34, 197, 94)), (sl, "SL", (239, 68, 68))])
            cap, edu = build_edu_signal(
                "ANORMAL VOLATILITE (15M)", sym, "15m", yon, cl, tp1, tp2, tp3, sl,
                f"• 15m Mum Hareketi: {pct:+.2f}%\n• Hacim Patlamasi: Son 20 mum ortalamasinin {vm:.1f}x kati",
                "15 dakikalik tek mumda normal piyasa akisinin cok uzerinde sert bir fiyat ve hacim patlamasi (haber akisi, buyuk balina emri veya stop patlatma) tespit edildi.",
                "• <b>Anormal Volatilite:</b> Fiyatin kisa surede ortalama dalga boyunun katlarina cikmasidir. Genellikle yeni bir trend dalgasinin veya sert geri cekilme firsatinin habercisidir.",
                "Yuksek oynaklikta kaldirac dusuk tutulmalidir. Mumun %50 geri cekilme (imbalance) bolgesi destek/direnc olarak izlenmelidir."
            )
            if tg_photo(C, png, cap, edu):
                signal_sent(son_volatilite_sinyalleri, sym)
                record_tracked_signal("VOLATILITE", sym, yon, cl, tp1, tp2, tp3, sl)


# =====================================================
# MOTOR 7: FORMASYON (5m + 15m EMA11/21 + RSI + 5m TOBO)
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
        H = [float(k[2]) for k in k5]
        L = [float(k[3]) for k in k5]
        Cl = [float(k[4]) for k in k5]
        V = [float(k[7]) for k in k5]
        c15 = [float(k[4]) for k in k15]
        e11, e21, rsi = calculate_ema(c15, 11)[-1], calculate_ema(c15, 21)[-1], calculate_rsi(c15)
        price = Cl[-1]
        avg = sum(V[-21:-1]) / 20
        if avg <= 0 or V[-1] <= avg * 1.5:
            continue
        ph, pl = find_pivots(H, L)
        if len(ph) < 3 or len(pl) < 3:
            continue
        lh, ll = ph[-3:], pl[-3:]
        sig = None

        if abs(lh[0][1] - lh[2][1]) / lh[0][1] < 0.03 and price < ll[-1][1] and rsi < 50 and e11 < e21:
            sig = ("SHORT", "Cift Tepe (M)", ll[-1][1] - (lh[1][1] - ll[-1][1]), lh[2][1] * 1.015, "Iki kez ayni direnci test edip boyun cizgisini hacimle asagi kirdi.")
        elif abs(ll[0][1] - ll[2][1]) / ll[0][1] < 0.03 and price > lh[-1][1] and rsi > 50 and e11 > e21:
            sig = ("LONG", "Cift Dip (W)", lh[-1][1] + (lh[-1][1] - ll[1][1]), ll[2][1] * 0.985, "Iki kez ayni destekte satici tukendi ve boyun cizgisi yukari kirildi.")
        elif lh[1][1] > lh[0][1] and lh[1][1] > lh[2][1] and abs(lh[0][1] - lh[2][1]) / lh[0][1] < 0.04 and price < ll[-1][1] and rsi < 50 and e11 < e21:
            sig = ("SHORT", "Omuz-Bas-Omuz (OBO)", ll[-1][1] - (lh[1][1] - ll[-1][1]), lh[2][1] * 1.015, "Bas zirvesi sonrasi sag omuz dusuk kaldi ve boyun destegi kirildi.")
        elif ll[1][1] < ll[0][1] and ll[1][1] < ll[2][1] and abs(ll[0][1] - ll[2][1]) / ll[0][1] < 0.04 and price > lh[-1][1] and rsi > 50 and e11 > e21:
            sig = ("LONG", "Ters Omuz-Bas-Omuz (TOBO)", lh[-1][1] + (lh[-1][1] - ll[1][1]), ll[2][1] * 0.985, "Derin bas dibinden sonra sag omuz yukarida tutundu ve boyun direnci kirildi.")
        elif H[-2] > max(H[-20:-2]) and price < max(H[-20:-2]) and rsi < 50 and e11 < e21:
            sig = ("SHORT", "Likidite Avi (Sweep)", price * 0.97, H[-2] * 1.008, "Eski tepe ustundeki stoplar fitille temizlendi ve fiyat tekrar altina kapandi.")
        elif L[-2] < min(L[-20:-2]) and price > min(L[-20:-2]) and rsi > 50 and e11 > e21:
            sig = ("LONG", "Likidite Avi (Sweep)", price * 1.03, L[-2] * 0.992, "Eski dip altindaki stoplar fitille temizlendi ve alicilar fiyati hizla iceri cekti.")
        else:
            h20, l20 = max(H[-20:]), min(L[-20:])
            rp = ((h20 - l20) / l20) * 100.0
            if rp < 4.5 and price > h20 and rsi > 52 and e11 > e21:
                sig = ("LONG", "Sikisma ve Yukari Kirilim", price + (h20 - l20), l20 * 0.985, "Dar bantta biriken enerji 15m trend uyumuyla yukari patladi.")
            elif rp < 4.5 and price < l20 and rsi < 48 and e11 < e21:
                sig = ("SHORT", "Sikisma ve Asagi Kirilim", price - (h20 - l20), h20 * 1.015, "Dar bantta biriken enerji 15m trend uyumuyla asagi kirildi.")

        if not sig:
            continue
        yon, ad, tp2, sl, neden = sig
        risk = abs(price - sl)
        reward = abs(tp2 - price)
        if risk <= 0 or (reward / risk) < 1.5 or abs(((sl - price) / price) * 100) > 8.0:
            continue
        tp1 = price + (tp2 - price) * 0.55
        tp3 = price + (tp2 - price) * 1.45
        png = render_chart_png(sym, "5m", k5, ad, [(price, "GIRIS", (56, 189, 248)), (tp2, "TP2", (34, 197, 94)), (sl, "SL", (239, 68, 68))])
        cap, edu = build_edu_signal(
            f"FORMASYON - {ad}", sym, "5m/15m", yon, price, tp1, tp2, tp3, sl,
            f"• Hacim Teyidi: {V[-1]/avg:.1f}x\n• 15m EMA11/EMA21 & RSI({rsi:.1f}) Yon Uyumlu",
            neden,
            f"• <b>{ad}:</b> Piyasa yapisinin (Market Structure) kirildigini gosteren klasik donus veya devam formasyonudur.\n• <b>Coklu Zaman Teyidi:</b> 5m formasyonunun sahte olmamasi icin 15m grafikte EMA11/21 ve RSI teyidi aranmistir.",
            "Formasyonun bozulma noktasi olan son tepe/dip (SL) disina stop konmustur. TP1'de %50 kar alip stobu girise cekmek guvenlidir."
        )
        if tg_photo(C, png, cap, edu):
            signal_sent(son_formasyon_sinyalleri, sym)
            record_tracked_signal("FORMASYON", sym, yon, price, tp1, tp2, tp3, sl)


# =====================================================
# MOTOR 8-12: ICHIMOKU / EMA RIBBON / MACD / SMA200 / ATH-ATL
# =====================================================
def ichimoku_worker():
    for t in adaylar(MIN_HACIM_ICHIMOKU, MAX_TARAMA, "fut"):
        sym = t["symbol"]
        if not cooldown_ok(son_ichimoku_sinyalleri, sym, 14400):
            continue
        ks = klines(sym, "1h", 100, "fut")
        if not ks or len(ks) < 85:
            continue
        H, L, Cl, V = [float(k[2]) for k in ks], [float(k[3]) for k in ks], [float(k[4]) for k in ks], [float(k[7]) for k in ks]
        idx = len(Cl) - 2
        avg_v = sum(V[idx - 20:idx]) / 20
        if avg_v <= 0 or V[idx] <= avg_v * 1.2:
            continue
        def tk(i): return (max(H[i - 8:i + 1]) + min(L[i - 8:i + 1])) / 2.0
        def kj(i): return (max(H[i - 25:i + 1]) + min(L[i - 25:i + 1])) / 2.0
        t_c, k_c = tk(idx), kj(idx)
        sa_c, sa_p = (tk(idx - 26) + kj(idx - 26)) / 2.0, (tk(idx - 27) + kj(idx - 27)) / 2.0
        sb_c, sb_p = (max(H[idx - 77:idx - 25]) + min(L[idx - 77:idx - 25])) / 2.0, (max(H[idx - 78:idx - 26]) + min(L[idx - 78:idx - 26])) / 2.0
        top_c, bot_c = max(sa_c, sb_c), min(sa_c, sb_c)
        top_p, bot_p = max(sa_p, sb_p), min(sa_p, sb_p)
        thk, cc, pc = top_c - bot_c, Cl[idx], Cl[idx - 1]
        if thk <= cc * 0.006:
            continue
        sig = None
        if pc <= top_p and cc > top_c and cc > k_c and t_c > k_c:
            sig = ("LONG", cc + thk * 2, min(k_c, bot_c) * 0.99)
        elif pc >= bot_p and cc < bot_c and cc < k_c and t_c < k_c:
            sig = ("SHORT", cc - thk * 2, max(k_c, top_c) * 1.01)
        if not sig:
            continue
        yon, tp2, sl = sig
        if abs(((tp2 - cc) / cc) * 100) < 1.5 or abs(tp2 - cc) / max(abs(cc - sl), 1e-8) < 1.3:
            continue
        tp1, tp3 = cc + (tp2 - cc) * 0.55, cc + (tp2 - cc) * 1.5
        png = render_chart_png(sym, "1h", ks[:-1], f"ICHIMOKU KUMO {yon}", [(cc, "GIRIS", (56, 189, 248)), (tp2, "TP2", (34, 197, 94)), (sl, "SL", (239, 68, 68))])
        cap, edu = build_edu_signal(
            "ICHIMOKU BULUT KIRILIMI", sym, "1h", yon, cc, tp1, tp2, tp3, sl,
            f"• Hacim: {V[idx]/avg_v:.1f}x\n• Tenkan-sen / Kijun-sen Kesisimi Uyumlu",
            "Fiyat 1 saatlik grafikte Ichimoku Kumo Bulutu'nu hacimli olarak kirdi ve kisa/orta vadeli denge cizgileri (Tenkan & Kijun) ayni yone dondu.",
            "• <b>Kumo Bulutu:</b> Gelecekteki destek/direnc bolgesini gosteren alandir.\n• <b>Tenkan &amp; Kijun:</b> Kisa ve orta vadeli momentum cizgileridir; bunlarin ayni yonde olmasi trend gucunu teyit eder.",
            "Bulutun icine geri donulmesi ve Kijun-sen'in kirilmasi kurulumu iptal eder."
        )
        if tg_photo(C, png, cap, edu):
            signal_sent(son_ichimoku_sinyalleri, sym)
            record_tracked_signal("ICHIMOKU", sym, yon, cc, tp1, tp2, tp3, sl)


def ema_ribbon_worker():
    for t in adaylar(MIN_HACIM_EMA_RIBBON, MAX_TARAMA, "fut"):
        sym = t["symbol"]
        if not cooldown_ok(son_ema_ribbon_sinyalleri, sym, 86400):
            continue
        ks = tamamlanmis(klines(sym, "1d", 220, "fut"))
        if len(ks) < 205:
            continue
        Cl, V = [float(k[4]) for k in ks], [float(k[7]) for k in ks]
        avg = sum(V[-21:-1]) / 20
        if avg <= 0 or V[-1] <= avg * 1.3:
            continue
        e7, e25, e99, e200 = calculate_ema(Cl, 7), calculate_ema(Cl, 25), calculate_ema(Cl, 99), calculate_ema(Cl, 200)
        rsi = calculate_rsi(Cl, 14)
        c = Cl[-1]
        if c > e7[-1] > e25[-1] > e99[-1] > e200[-1] and 50 <= rsi <= 75:
            tp1, tp2, tp3, sl = c * 1.10, c * 1.20, c * 1.32, e25[-1] * 0.95
            png = render_chart_png(sym, "1d", ks, "EMA RIBBON LONG", [(c, "GIRIS", (56, 189, 248)), (tp2, "TP2", (34, 197, 94)), (sl, "SL", (239, 68, 68))], [(e7, (56, 189, 248)), (e25, (250, 204, 21)), (e200, (239, 68, 68))])
            cap, edu = build_edu_signal(
                "EMA RIBBON DIZILIMI", sym, "1d", "LONG", c, tp1, tp2, tp3, sl,
                f"• Hacim: {V[-1]/avg:.1f}x | Gunluk RSI: {rsi:.1f}\n• Kusursuz Dizilim: EMA7 > EMA25 > EMA99 > EMA200",
                "Gunluk grafikte tum kisa, orta ve uzun vadeli hareketli ortalamalar yukari yonlu kusursuz bir yelpaze (Ribbon) seklinde siralandi.",
                "• <b>EMA Ribbon:</b> Farkli vadelerdeki ortalamalarin siralanarak guclu bir boga trendi olusturmasidir. Piyasada trendin en saglikli aktigi 'tatli nokta'dir.",
                "EMA25'in %5 altindaki kapanis ana momentum kaybi anlamina gelir."
            )
            if tg_photo(C, png, cap, edu):
                signal_sent(son_ema_ribbon_sinyalleri, sym)
                record_tracked_signal("EMA_RIBBON", sym, "LONG", c, tp1, tp2, tp3, sl)


def macd_worker():
    for t in adaylar(MIN_HACIM_MACD, MAX_TARAMA, "fut"):
        sym = t["symbol"]
        if not cooldown_ok(son_macd_sinyalleri, sym, 14400):
            continue
        ks = tamamlanmis(klines(sym, "1d", 100, "fut"))
        if len(ks) < 70:
            continue
        Cl, V = [float(k[4]) for k in ks], [float(k[7]) for k in ks]
        avg = sum(V[-21:-1]) / 20
        if avg <= 0 or V[-1] <= avg * 1.2:
            continue
        m = calculate_macd(Cl)
        e21 = calculate_ema(Cl, 21)
        rsi = calculate_rsi(Cl, 14)
        if not m or not e21:
            continue
        cm, cs, pm, ps = m
        c = Cl[-1]
        if pm <= ps and cm > cs and c > e21[-1] and rsi > 50:
            tp1, tp2, tp3, sl = c * 1.05, c * 1.10, c * 1.18, c * 0.965
            png = render_chart_png(sym, "1d", ks, "MACD GOLDEN CROSS", [(c, "GIRIS", (56, 189, 248)), (tp2, "TP2", (34, 197, 94)), (sl, "SL", (239, 68, 68))], [(e21, (250, 204, 21))])
            cap, edu = build_edu_signal(
                "MACD ALTIN KESISIM (1D)", sym, "1d", "LONG", c, tp1, tp2, tp3, sl,
                f"• Hacim: {V[-1]/avg:.1f}x | RSI: {rsi:.1f}\n• Fiyat EMA21 Uzerinde + MACD Sinyal Kesisimi",
                "Gunluk grafikte MACD cizgisi Sinyal cizgisini yukari keserken fiyat da EMA21 dinamik desteginin uzerinde tutundu.",
                "• <b>MACD Golden Cross:</b> Satis baskisinin tukenip yukselis momentumunun basladigini gosteren klasik trend donus sinyalidir.",
                "Son dip veya girisin %3.5 altinda kapanis momentumu bozar."
            )
            if tg_photo(C, png, cap, edu):
                signal_sent(son_macd_sinyalleri, sym)
                record_tracked_signal("MACD", sym, "LONG", c, tp1, tp2, tp3, sl)


def sma200_worker():
    for t in adaylar(MIN_HACIM_SMA200, MAX_TARAMA, "fut"):
        sym = t["symbol"]
        if not cooldown_ok(son_sma200_sinyalleri, sym, 86400):
            continue
        ks = tamamlanmis(klines(sym, "1d", 220, "fut"))
        if len(ks) < 205:
            continue
        Cl, V = [float(k[4]) for k in ks], [float(k[7]) for k in ks]
        s200 = calculate_sma(Cl, 200)
        avg = sum(V[-21:-1]) / 20
        if len(s200) < 2 or avg <= 0 or V[-1] <= avg * 1.2:
            continue
        live, c = t["_price"], Cl[-1]
        if Cl[-2] <= s200[-2] and c > s200[-1] and live > s200[-1]:
            tp1, tp2, tp3, sl = c * 1.07, c * 1.15, c * 1.25, s200[-1] * 0.97
            png = render_chart_png(sym, "1d", ks, "SMA200 TAZE KIRILIM", [(c, "GIRIS", (56, 189, 248)), (tp2, "TP2", (34, 197, 94)), (sl, "SL", (239, 68, 68))], [(s200, (250, 204, 21))])
            cap, edu = build_edu_signal(
                "SMA200 TAZE KIRILIM", sym, "1d", "LONG", c, tp1, tp2, tp3, sl,
                f"• Canli Fiyat ({akilli_fiyat(live)}) SMA200 Uzerinde Kilitli\n• Hacim: {V[-1]/avg:.1f}x",
                "Gunluk mum SMA200 uzerinde taze kapanis yapti ve anlik canli fiyat da bu ana destegin uzerinde kaliciligini koruyor.",
                "• <b>SMA200 (200 Gunluk Basit Ortalama):</b> Dunya genelinde tum fonlarin ve algoritmalarin ana trend referansi olarak kabul ettigi en onemli ortalama cizgisidir.",
                "SMA200'un %3 altinda gunluk kapanis gelirse sahte kirilim sayilir ve stop olunur."
            )
            if tg_photo(C, png, cap, edu):
                signal_sent(son_sma200_sinyalleri, sym)
                record_tracked_signal("SMA200", sym, "LONG", c, tp1, tp2, tp3, sl)


def ath_atl_worker():
    for t in adaylar(MIN_HACIM_ATH_ATL, MAX_TARAMA, "fut"):
        sym = t["symbol"]
        if not cooldown_ok(son_ath_atl_sinyalleri, sym, 86400):
            continue
        ks = tamamlanmis(klines(sym, "1d", 250, "fut"))
        if len(ks) < 150:
            continue
        H, L, Cl, V = [float(k[2]) for k in ks], [float(k[3]) for k in ks], [float(k[4]) for k in ks], [float(k[7]) for k in ks]
        avg = sum(V[-21:-1]) / 20
        if avg <= 0 or V[-1] <= avg * 1.3:
            continue
        ath, atl = max(H[:-1]), min(L[:-1])
        c, p = Cl[-1], Cl[-2]
        if p <= ath and c > ath:
            yon, tp1, tp2, tp3, sl, lvl, lbl = "LONG", c * 1.10, c * 1.20, c * 1.35, ath * 0.95, ath, "ATH"
        elif p >= atl and c < atl:
            yon, tp1, tp2, tp3, sl, lvl, lbl = "SHORT", c * 0.90, c * 0.80, c * 0.68, atl * 1.05, atl, "ATL"
        else:
            continue
        png = render_chart_png(sym, "1d", ks, f"TARIHI {lbl} KIRILIMI", [(lvl, lbl, (250, 204, 21)), (tp2, "TP2", (34, 197, 94)), (sl, "SL", (239, 68, 68))])
        cap, edu = build_edu_signal(
            f"TARIHI {lbl} KIRILIMI", sym, "1d", yon, c, tp1, tp2, tp3, sl,
            f"• Kirilan Seviye: {akilli_fiyat(lvl)}\n• Hacim Teyidi: {V[-1]/avg:.1f}x",
            f"Fiyat son 250 gunun en yuksek/en dusuk siniri olan {akilli_fiyat(lvl)} seviyesini hacimli bir gunluk kapanisla kirdi.",
            f"• <b>{lbl} (All-Time High/Low):</b> Fiyatin onunde gecmisten kalan hicbir psikolojik engel kalmadigi 'kesif bolgesi' kirilimidir.",
            "Kirilan seviyenin tekrar icine sert geri donus (reclaim) yasanirsa hareket tuzaga donusebilir."
        )
        if tg_photo(C, png, cap, edu):
            signal_sent(son_ath_atl_sinyalleri, sym)
            record_tracked_signal("ATH_ATL", sym, yon, c, tp1, tp2, tp3, sl)


# =====================================================
# MOTOR 13: GELISMIS PATTERN (4h / 1d + Fincan Kulp, Bayrak, Dikdortgen)
# =====================================================
def detect_advanced_pattern(ks):
    H = [float(k[2]) for k in ks]
    L = [float(k[3]) for k in ks]
    Cl = [float(k[4]) for k in ks]
    V = [float(k[7]) for k in ks]
    if len(Cl) < 55:
        return None
    avg = sum(V[-21:-1]) / 20.0
    if avg <= 0 or V[-1] < avg * 1.25:
        return None
    ph, pl = find_pivots(H, L)
    c = Cl[-1]

    if len(ph) >= 2:
        i1, p1 = ph[-2]
        i2, p2 = ph[-1]
        if 5 <= i2 - i1 <= 30 and abs(p1 - p2) / p1 <= 0.02:
            nk = min(L[i1:i2 + 1])
            if c < nk * 0.992:
                return ("SHORT", "Ikili Tepe (M)", nk - (max(p1, p2) - nk), max(p1, p2) * 1.02, nk, [(i1, nk, len(ks) - 1, nk, (250, 204, 21))], [],
                        "Fiyat ayni zirvede iki kez saticiyla karsilasti ve ortadaki boyun destegini hacimle kirdi.")
    if len(pl) >= 2:
        i1, p1 = pl[-2]
        i2, p2 = pl[-1]
        if 5 <= i2 - i1 <= 30 and abs(p1 - p2) / p1 <= 0.02:
            nk = max(H[i1:i2 + 1])
            if c > nk * 1.008:
                return ("LONG", "Ikili Dip (W)", nk + (nk - min(p1, p2)), min(p1, p2) * 0.98, nk, [(i1, nk, len(ks) - 1, nk, (250, 204, 21))], [],
                        "Ikinci dipte satisi karsilayan guclu alicilar boyun direncini yukari kirdi.")
    if len(ph) >= 3:
        (i1, p1), (i2, p2), (i3, p3) = ph[-3:]
        if p2 > p1 and p2 > p3 and abs(p1 - p3) / p1 <= 0.03:
            nk = min(L[i1:i3 + 1])
            if c < nk * 0.992:
                return ("SHORT", "Omuz-Bas-Omuz (OBO)", nk - (p2 - nk), p3 * 1.02, nk, [(i1, nk, len(ks) - 1, nk, (250, 204, 21))], [],
                        "Bas zirvesinden sonra alicilar zayifladi ve sag omuz altindaki boyun destegi kirildi.")
    if len(pl) >= 3:
        (i1, p1), (i2, p2), (i3, p3) = pl[-3:]
        if p2 < p1 and p2 < p3 and abs(p1 - p3) / p1 <= 0.03:
            nk = max(H[i1:i3 + 1])
            if c > nk * 1.008:
                return ("LONG", "Ters Omuz-Bas-Omuz (TOBO)", nk + (nk - p2), p3 * 0.98, nk, [(i1, nk, len(ks) - 1, nk, (250, 204, 21))], [],
                        "Derin dip sonrasi sag omuz yukarida olustu ve boyun direnci hacimle asildi.")

    n = len(Cl)
    if n >= 55:
        cup_slice = Cl[-48:-12]
        cup_h_slice = H[-48:-12]
        cup_l_slice = L[-48:-12]
        mid_c = len(cup_slice) // 2
        rim_left, rim_right = max(cup_h_slice[:8]), max(cup_h_slice[-8:])
        cup_bot = min(cup_l_slice[8:-8]) if len(cup_l_slice) > 16 else min(cup_l_slice)
        rim_lvl = (rim_left + rim_right) / 2.0
        cup_depth = (rim_lvl - cup_bot) / rim_lvl if rim_lvl > 0 else 0
        h_low, h_high = min(L[-12:-1]), max(H[-12:-1])
        if (0.06 <= cup_depth <= 0.35 and abs(rim_left - rim_right) / rim_lvl <= 0.04
                and min(cup_slice[mid_c - 4:mid_c + 4]) < rim_lvl * 0.96
                and h_low >= cup_bot + (rim_lvl - cup_bot) * 0.45
                and c > max(rim_lvl, h_high)):
            target = c + (rim_lvl - cup_bot)
            stop = h_low * 0.985
            tlines = [(n - 48, rim_lvl, n - 1, rim_lvl, (250, 204, 21)), (n - 12, h_high, n - 1, h_low, (56, 189, 248))]
            return ("LONG", "Fincan & Kulp (Cup & Handle)", target, stop, rim_lvl, tlines, [],
                    "U seklinde yuvarlak dip (Fincan) tamamlandiktan sonra kulp kisminda saticilar azaldi ve rim direnci hacimle yukari kirildi.")

        bot_left, bot_right = min(cup_l_slice[:8]), min(cup_l_slice[-8:])
        cup_top = max(cup_h_slice[8:-8]) if len(cup_h_slice) > 16 else max(cup_h_slice)
        inv_rim = (bot_left + bot_right) / 2.0
        inv_depth = (cup_top - inv_rim) / cup_top if cup_top > 0 else 0
        if (0.06 <= inv_depth <= 0.35 and abs(bot_left - bot_right) / max(inv_rim, 1e-8) <= 0.04
                and max(cup_slice[mid_c - 4:mid_c + 4]) > inv_rim * 1.04
                and h_high <= cup_top - (cup_top - inv_rim) * 0.45
                and c < min(inv_rim, h_low)):
            target = c - (cup_top - inv_rim)
            stop = h_high * 1.015
            tlines = [(n - 48, inv_rim, n - 1, inv_rim, (250, 204, 21)), (n - 12, h_low, n - 1, h_high, (239, 68, 68))]
            return ("SHORT", "Ters Fincan & Kulp (Inv. Cup & Handle)", target, stop, inv_rim, tlines, [],
                    "Ters kubbe seklindeki dagitim tamamlandi, kisa kulp tepkisi sonrasi ana boyun destegi asagi kirildi.")

    pole_len, flag_len = 12, 14
    if n >= pole_len + flag_len + 5:
        p_start, p_end = Cl[-(pole_len + flag_len)], Cl[-flag_len]
        pole_move = ((p_end - p_start) / p_start) * 100.0 if p_start > 0 else 0.0
        ch_highs, ch_lows = H[-flag_len:-1], L[-flag_len:-1]
        ch_slope = _linreg_slope(Cl[-flag_len:-1])
        ch_range = ((max(ch_highs) - min(ch_lows)) / min(ch_lows)) * 100.0 if min(ch_lows) > 0 else 99
        if pole_move >= 6.5 and ch_slope <= 0 and ch_range <= 7.0 and c > max(ch_highs):
            target = c + abs(p_end - p_start)
            stop = min(ch_lows) * 0.985
            tlines = [(n - flag_len, max(ch_highs), n - 1, ch_highs[-1], (56, 189, 248)),
                      (n - flag_len, min(ch_lows), n - 1, ch_lows[-1], (56, 189, 248))]
            return ("LONG", "Boga Bayragi (Bullish Flag)", target, stop, max(ch_highs), tlines, [],
                    f"Once %{pole_move:.1f} sert yukselis diregi olustu, ardindan hafif asagi egimli dar bayrak kanali yukari kirildi.")
        if pole_move <= -6.5 and ch_slope >= 0 and ch_range <= 7.0 and c < min(ch_lows):
            target = c - abs(p_end - p_start)
            stop = max(ch_highs) * 1.015
            tlines = [(n - flag_len, max(ch_highs), n - 1, ch_highs[-1], (239, 68, 68)),
                      (n - flag_len, min(ch_lows), n - 1, ch_lows[-1], (239, 68, 68))]
            return ("SHORT", "Ayi Bayragi (Bearish Flag)", target, stop, min(ch_lows), tlines, [],
                    f"Once %{pole_move:.1f} sert dusus diregi olustu, ardindan hafif yukari egimli dar kanal asagi kirildi.")

    rect_w = 24
    if n >= rect_w + 2:
        r_hi, r_lo = max(H[-rect_w:-1]), min(L[-rect_w:-1])
        r_h_pct = ((r_hi - r_lo) / r_lo) * 100.0 if r_lo > 0 else 99
        hi_touches = sum(1 for x in H[-rect_w:-1] if x >= r_hi * 0.992)
        lo_touches = sum(1 for x in L[-rect_w:-1] if x <= r_lo * 1.008)
        if 1.5 <= r_h_pct <= 8.0 and hi_touches >= 2 and lo_touches >= 2:
            boxes = [(n - rect_w, n - 1, r_lo, r_hi, (250, 204, 21))]
            if c > r_hi * 1.003:
                return ("LONG", "Boga Dikdortgeni (Bullish Rectangle)", c + (r_hi - r_lo), r_lo * 0.99, r_hi, [], boxes,
                        "Fiyat en az iki kez ust ve alt sinirda test edilen yatay dikdortgen kutusunu hacimle yukari kirdi.")
            if c < r_lo * 0.997:
                return ("SHORT", "Ayi Dikdortgeni (Bearish Rectangle)", c - (r_hi - r_lo), r_hi * 1.01, r_lo, [], boxes,
                        "Yatay kutu icindeki dagitim sureci tamamlandi ve alt destek siniri hacimle asagi kirildi.")

    return None


def chart_patterns_worker():
    for t in adaylar(MIN_HACIM_PATTERN, MAX_TARAMA, "fut"):
        sym = t["symbol"]
        if not cooldown_ok(son_chart_pattern_sinyalleri, sym, 14400):
            continue
        for tf in ("4h", "1d"):
            ks = tamamlanmis(klines(sym, tf, 100, "fut"))
            if len(ks) < 55:
                continue
            res = detect_advanced_pattern(ks)
            if not res:
                continue
            yon, ad, tp2, sl, nk, tlines, boxes, neden = res
            c = float(ks[-1][4])
            risk, rew = abs(c - sl), abs(tp2 - c)
            if risk <= 0 or (rew / risk) < 1.5 or abs(((sl - c) / c) * 100) > 15 or not (2.0 <= abs(((tp2 - c) / c) * 100) <= 45):
                continue
            tp1 = c + (tp2 - c) * 0.55
            tp3 = c + (tp2 - c) * 1.45
            png = render_chart_png(sym, tf, ks, ad, [(nk, "KIRILIM", (250, 204, 21)), (tp2, "TP2", (34, 197, 94)), (sl, "SL", (239, 68, 68))], trend_lines=tlines, boxes=boxes)
            cap, edu = build_edu_signal(
                f"GRAFIK FORMASYONU - {ad}", sym, tf, yon, c, tp1, tp2, tp3, sl,
                f"• Kirilim Seviyesi: {akilli_fiyat(nk)}\n• Hacim Teyitli & Minimum 1:1.5 R:R Saglandi",
                neden,
                f"• <b>{ad}:</b> {tf.upper()} gibi buyuk zaman diliminde olusan yapisal formasyonlar yuksek guvenirlikli hedef derinligi (formasyon boyu kadar hareket) uretir.",
                "Kirilan boyun/kanal cizgisine geri cekilme (retest) yasanabilir; Stop-Loss karsi kenar disindadir."
            )
            if tg_photo(C, png, cap, edu):
                signal_sent(son_chart_pattern_sinyalleri, sym)
                record_tracked_signal("PATTERN", sym, yon, c, tp1, tp2, tp3, sl)
                break


# =====================================================
# MOTOR 14-20: ANLIK ATH, TOP MOVERS, ORDERFLOW, ZAFER, TOP24, CVD, DCA
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
        ath, atl = max(float(k[2]) for k in prev), min(float(k[3]) for k in prev)
        vols = [float(k[7]) for k in prev]
        avg_v = sum(vols[-20:]) / 20.0
        if avg_v <= 0 or float(ks[-1][7]) < avg_v * 0.9:
            continue
        live = t["_price"]
        if live > ath * 1.002:
            yon, tp1, tp2, tp3, sl, lvl, lbl = "LONG", live * 1.07, live * 1.15, live * 1.25, ath * 0.97, ath, "ANLIK ATH"
        elif live < atl * 0.998:
            yon, tp1, tp2, tp3, sl, lvl, lbl = "SHORT", live * 0.93, live * 0.85, live * 0.75, atl * 1.03, atl, "ANLIK ATL"
        else:
            continue
        png = render_chart_png(sym, "1d", ks, lbl, [(lvl, "SEVIYE", (250, 204, 21)), (tp2, "TP2", (34, 197, 94)), (sl, "SL", (239, 68, 68))])
        cap, edu = build_edu_signal(
            lbl, sym, "1d Canli", yon, live, tp1, tp2, tp3, sl,
            f"• Kirilan Seviye: {akilli_fiyat(lvl)}\n• Gunluk Mum Kapanisi Beklenmeden Anlik Teyit",
            f"Fiyat son 200 gunun tarihi zirve/dip seviyesi olan {akilli_fiyat(lvl)} sinirini canli piyasada kirdi.",
            "• <b>Anlik ATH/ATL:</b> Gunluk kapanisi beklemeden hizli momentum dalgasini erken yakalamak icin kullanilir.",
            "Canli kirilimlarda fakeout riski kapanisli kirilima gore daha yuksektir; kirilan seviyenin altinda kalici kalirsa stop calismalidir."
        )
        if tg_photo(C, png, cap, edu):
            signal_sent(son_anlik_ath_sinyalleri, sym)
            record_tracked_signal("ANLIK_ATH_ATL", sym, yon, live, tp1, tp2, tp3, sl)


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
    png = render_card_png("FUTURES SON 1 SAAT OZETI", "Kapanan Son 1h En Cok Yukselen 3 & Dusen 3", c_rows, (56, 189, 248))
    cap = "⏱️ <b>Futures Son 1 Saat Liderleri</b>\n\n🟢 " + ", ".join(f"{x['sym'].replace('USDT','')} ({x['chg']:+.2f}%)" for x in up) + "\n🔴 " + ", ".join(f"{x['sym'].replace('USDT','')} ({x['chg']:+.2f}%)" for x in dn)
    edu = "📚 <b>EGITICI NOT:</b> Saatlik kapanista en sert hareket eden coinler kisa vadeli para giris/cikis odak noktalaridir; scalping ve tepki arayan traderlar icin likidite haritasi sunar."
    tg_photo(C, png, cap, edu)


def orderflow_fibo_worker():
    for t in adaylar(MIN_HACIM_ORDERFLOW, 30, "fut"):
        sym = t["symbol"]
        if not cooldown_ok(son_orderflow_sinyalleri, sym, 14400):
            continue
        ks = klines(sym, "4h", 60, "fut")
        if not ks or len(ks) < 40:
            continue
        H, L = [float(k[2]) for k in ks], [float(k[3]) for k in ks]
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
        sl = price * (0.97 if yon == "LONG" else 1.03)
        tp1 = price * (1.04 if yon == "LONG" else 0.96)
        tp2 = price * (1.08 if yon == "LONG" else 0.92)
        tp3 = price * (1.13 if yon == "LONG" else 0.87)
        png = render_chart_png(sym, "4h", ks, f"ORDERFLOW + FIBO 0.618 {yon}", [(f618, "FIB618", (250, 204, 21)), (tp2, "TP2", (34, 197, 94)), (sl, "SL", (239, 68, 68))])
        cap, edu = build_edu_signal(
            "ORDERFLOW + FIBONACCI 0.618", sym, "4h", yon, price, tp1, tp2, tp3, sl,
            f"• Fib 0.618 Altin Oran: {akilli_fiyat(f618)}\n• {n_ex} Borsa Emir Defteri: %{imb:.1f} Alis ({fmt_usd(bid_u)} Alim / {fmt_usd(ask_u)} Satim)",
            "Fiyat 4 saatlik grafikteki en kritik geri cekilme bolgesi olan Fibonacci 0.618 seviyesine indi ve birden fazla borsanin gercek emir defterinde (L2 Orderbook) belirgin emir dengesizligi olustu.",
            "• <b>Fibonacci 0.618 (Altin Oran):</b> Trend devam etmeden once kurumsal emirlerin bekledigi en guclu geri cekilme seviyesidir.\n• <b>Orderbook Dengesizligi:</b> Fiyatin +/-%1.5 cevresindeki aktif alis ve satis duvarlarinin oranidir.",
            "Emir duvarlari iptal edilebilir (spoofing); bu nedenle %3'luk Stop-Loss korunmalidir."
        )
        if tg_photo(C, png, cap, edu):
            signal_sent(son_orderflow_sinyalleri, sym)
            record_tracked_signal("ORDERFLOW_FIBO", sym, yon, price, tp1, tp2, tp3, sl)


def sinyal_zaferi_worker():
    wins = get_tracker_stats(hours=24, min_pct=5.0)
    if not wins:
        return
    w = wins[0]
    tstr = datetime.fromtimestamp(w["ts"], TR_Saati).strftime("%H:%M")
    png = render_win_chart_png(w["symbol"], tstr, w["entry"], w["peak"], w["peak_pct"], w.get("pts", []))
    cap = f"🏆 <b>GUNUN ZAFERI: {w['symbol']} +{w['peak_pct']:.1f}%</b>\n⏰ {tstr} ({w['motor']} - {w['dir']}) | Giris: {akilli_fiyat(w['entry'])} ➡️ Pik: {akilli_fiyat(w['peak'])}"
    edu = f"📚 <b>PERFORMANS NOTU:</b> Botumuzun urettigi bu sinyal giristen itibaren maksimum <b>{w.get('max_r', 0):.2f}R</b> odul uretmistir."
    tg_photo(C, png, cap, edu)


def sinyal_top24_worker():
    top5 = get_tracker_stats(hours=24, min_pct=1.5)[:5]
    if not top5:
        return
    rows = [(f"{i+1}. {s['symbol'].replace('USDT','')} ({s['dir']})", f"+{s['peak_pct']:.1f}% | {s.get('max_r',0):.1f}R", s["peak_pct"]) for i, s in enumerate(top5)]
    png = render_card_png("24 SAAT EN IYI SINYALLER", "Bot Sinyal Takip Performansi", rows, (168, 85, 247))
    cap = "🔥 <b>Son 24s En Iyi Bot Sinyalleri</b>\n\n" + "\n".join(
        f"{i+1}. <b>{s['symbol']}</b> ({s['dir']}) <b>+{s['peak_pct']:.1f}%</b> ({s.get('max_r',0):.1f}R)" for i, s in enumerate(top5)
    )
    tg_photo(C, png, cap)


def analyze_cvd_signal(fk, sc, fc, oi_pct):
    if len(fk) < 20:
        return None
    lows, highs = [float(k[3]) for k in fk], [float(k[2]) for k in fk]
    mid = len(fk) // 2
    p_low1, p_low2 = min(lows[:mid]), min(lows[mid:])
    p_hi1, p_hi2 = max(highs[:mid]), max(highs[mid:])
    s1, s2, f1, f2 = sc[mid - 1], sc[-1], fc[mid - 1], fc[-1]
    sd_recent, fd_recent = sc[-1] - sc[-7], fc[-1] - fc[-7]

    if p_low2 < p_low1 * 0.995 and (s2 > s1 or f2 > f1) and (sd_recent > 0 or fd_recent > 0):
        return ("LONG", "BULL DIV", "Fiyat daha dusuk dip yaparken Spot/Futures CVD yukseliyor (Gizli Akumulasyon).")
    if p_hi2 > p_hi1 * 1.005 and (s2 < s1 or f2 < f1) and (sd_recent < 0 or fd_recent < 0):
        return ("SHORT", "BEAR DIV", "Fiyat yeni tepe yaparken CVD geriliyor (Gizli Dagitim/Satis).")
    if sd_recent > 0 and fd_recent > 0 and oi_pct >= 2.0:
        return ("LONG", "BOTH BUYING + OI", "Hem Spot hem Futures net alimda ve Acik Pozisyon (OI) guclu artiyor.")
    if sd_recent < 0 and fd_recent < 0 and oi_pct >= 2.0:
        return ("SHORT", "BOTH SELLING + OI", "Hem Spot hem Futures net satista ve yeni short pozisyonlar aciliyor.")
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
        yon, durum, neden = sig
        c = float(fk[-1][4])
        sl = c * (0.96 if yon == "LONG" else 1.04)
        tp1 = c * (1.04 if yon == "LONG" else 0.96)
        tp2 = c * (1.08 if yon == "LONG" else 0.92)
        tp3 = c * (1.14 if yon == "LONG" else 0.86)
        png = render_cvd_chart_png(sym, fk, sc, fc, oi_pct, durum, durum if "DIV" in durum else "")
        cap, edu = build_edu_signal(
            f"CVD EMIR AKISI ({durum})", sym, "1h", yon, c, tp1, tp2, tp3, sl,
            f"• Spot CVD: {fmt_usd(sc[-1])} | Futures CVD: {fmt_usd(fc[-1])}\n• Acik Pozisyon (OI): {oi_pct:+.2f}%",
            neden,
            "• <b>CVD (Kumulatif Hacim Deltasi):</b> Piyasadaki aktif alici (market buy) ile aktif satici (market sell) farkinin birikimli toplamidir.\n• <b>Spot vs Futures:</b> Spot alimi kalici birikimi, Futures ise kaldiracli istahi gosterir. Uyumsuzluk (Divergence) donus sinyali verir.",
            "Spot CVD tersine doner ve fiyat SL sinirini kirarsa akis tezi bozulmus demektir."
        )
        if tg_photo(C, png, cap, edu):
            signal_sent(son_cvd_sinyalleri, sym)
            record_tracked_signal("CVD_FLOW", sym, yon, c, tp1, tp2, tp3, sl)


def calc_dca_plan(entry):
    entry = float(entry)
    dca1, dca2, dca3 = entry * 0.95, entry * 0.87, entry * 0.66
    avg1 = (entry + dca1 * 2.0) / 3.0
    avg2 = (entry + dca1 * 2.0 + dca2 * 4.0) / 7.0
    avg_all = (entry + dca1 * 2.0 + dca2 * 4.0 + dca3 * 8.0) / 15.0
    return {
        "entry": entry, "dca1": dca1, "dca2": dca2, "dca3": dca3,
        "avg1": avg1, "avg2": avg2, "avg_all": avg_all,
        "tp_base": entry * 1.025, "tp_dca3": avg_all * 1.022
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
    if not (rsi <= 40.0 and 8.0 <= pullback_pct <= 45.0 and vol_ratio >= 0.8):
        return None
    score = max(0.0, (48.0 - rsi)) * 1.6 + min(pullback_pct, 35.0) * 0.9 + min(vol_ratio, 3.0) * 8.0
    plan = calc_dca_plan(curr)
    plan.update({"symbol": symbol, "rsi": rsi, "chg": chg24, "pullback": pullback_pct, "vol_ratio": vol_ratio, "score": score})
    return plan


def dca_watchlist_worker():
    cands = []
    for t in adaylar(MIN_HACIM_DCA, 40, "fut"):
        sym = t["symbol"]
        if not cooldown_ok(son_dca_sinyalleri, sym, 43200):
            continue
        cand = evaluate_dca_candidate(sym, t["_change"], klines(sym, "4h", 48, "fut"), klines(sym, "1d", 14, "fut"))
        if cand:
            cands.append(cand)
    if not cands:
        return
    cands.sort(key=lambda x: x["score"], reverse=True)
    top = cands[:5]
    png = render_dca_watchlist_png(top)
    cap = "💎 <b>CRYPTO DCA WATCHLIST (Toparlanma Adaylari)</b>\n" + "\n".join(
        f"{i+1}. <b>{c['symbol']}</b> Base:{akilli_fiyat(c['entry'])} | DCA3(-34%):{akilli_fiyat(c['dca3'])} | TP:{akilli_fiyat(c['tp_base'])}"
        for i, c in enumerate(top)
    )
    edu = (
        "📚 <b>EGITICI DCA REHBERI (3COMMAS EKSPONANSIYEL STRATEJI):</b>\n"
        "• <b>DCA (Kademeli Ortalama Dusurme):</b> Tek fiyattan tum sermayeyle girmek yerine, fiyat -%5 (2x), -%13 (4x) ve -%34 (8x) dustukce katlanarak artan alim emirleri koyma yontemidir.\n"
        "• <b>Amaci:</b> Coin sert dusse bile toplam ortalama maliyeti dibe yaklastirip sadece +%2'lik kucuk bir tepkide tum sepeti karla kapatmaktir.\n"
        "• <b>Kasa Kurali:</b> Toplam bakiyenizin 15 birimlik bolunebilir kismi (1+2+4+8) planlanarak islem acilmalidir."
    )
    if tg_photo(C, png, cap, edu):
        for c in top:
            signal_sent(son_dca_sinyalleri, c["symbol"])
        record_tracked_signal("DCA_WATCHLIST", top[0]["symbol"], "LONG", top[0]["entry"], top[0]["tp_base"], top[0]["entry"] * 1.05, top[0]["entry"] * 1.09, top[0]["dca3"] * 0.95)


# =====================================================
# MOTOR 21 (YENI): ANLIK 0-7 DK SPOT PUMP DEDEKTORU (USDT / BTC / BNB)
# =====================================================
def scan_spot_pump_window(rows, now_ts=None):
    now_ts = now_ts or time.time()
    alerts = []
    for t in rows:
        sym = t["symbol"]
        p = t["_price"]
        qv = t["_quote"]
        min_q = 400_000 if sym.endswith("USDT") else (8.0 if sym.endswith("BTC") else 400.0)
        if qv < min_q or p <= 0:
            continue

        buf = _SPOT_WINDOW.setdefault(sym, [])
        buf.append((now_ts, p))
        cutoff = now_ts - 430.0
        while buf and buf[0][0] < cutoff:
            buf.pop(0)

        if len(buf) < 2:
            continue
        start_ts, start_p = buf[0]
        elapsed_min = max(0, int((now_ts - start_ts) / 60))
        if start_p <= 0 or elapsed_min > 7:
            continue
        pct = ((p - start_p) / start_p) * 100.0
        req_pct = 5.5 if sym.endswith("USDT") else 4.5
        if pct >= req_pct and cooldown_ok(son_spot_pump7m_sinyalleri, sym, 1200):
            alerts.append((sym, start_p, p, pct, elapsed_min, qv))
    return alerts


def spot_pump_7m_worker():
    rows = spot_all_quote_tickers()
    if not rows:
        return
    alerts = scan_spot_pump_window(rows)
    for sym, sp, cp, pct, mins, qv in alerts[:5]:
        msg = (
            f"⚡ <b>SPOT ANLIK PUMP DEDEKTORU — {sym}</b>\n\n"
            f"📈 <b>Degisim:</b> <b>+{pct:.2f}%</b> ({mins} dk icinde!)\n"
            f"💰 <b>Baslangic:</b> {akilli_fiyat(sp)}  ➡️  <b>Anlik:</b> {akilli_fiyat(cp)}\n"
            f"💧 <b>24s Parite Hacmi:</b> {qv:,.0f} ({'USDT' if sym.endswith('USDT') else ('BTC' if sym.endswith('BTC') else 'BNB')})\n\n"
            f"📚 <b>EGITICI NOT (0-7 DK SPOT PUMP):</b>\n"
            f"• Mum kapanisini beklemeden canli fiyat penceresinde ani para akisi tespit edildi.\n"
            f"• <b>Dikkat:</b> BTC/BNB paritelerindeki ani hareketler cogunlukla USDT paritesine sicramadan onceki erken sinyaldir. Kademeli kar alip agresif kovalamaktan kacinin."
        )
        if tg(C, msg):
            signal_sent(son_spot_pump7m_sinyalleri, sym)


# =====================================================
# MOTOR 22 (YENI): 1H YATAY DESTEK & DIRENC YAKINLIK TARAYICISI
# =====================================================
def find_horizontal_sr_levels(ks_1h, price):
    if not ks_1h or len(ks_1h) < 60:
        return None
    H = [float(k[2]) for k in ks_1h[:-1]]
    L = [float(k[3]) for k in ks_1h[:-1]]
    Cl = [float(k[4]) for k in ks_1h[:-1]]
    ph, pl = find_pivots(H, L, window=3)
    raw_levels = [p[1] for p in ph] + [p[1] for p in pl]
    if not raw_levels:
        return None

    clusters = []
    for lv in sorted(raw_levels):
        placed = False
        for cl in clusters:
            if abs(lv - cl["avg"]) / cl["avg"] <= 0.0055:
                cl["vals"].append(lv)
                cl["avg"] = sum(cl["vals"]) / len(cl["vals"])
                placed = True
                break
        if not placed:
            clusters.append({"vals": [lv], "avg": lv})

    strong = [c for c in clusters if len(c["vals"]) >= 3]
    if not strong:
        return None

    rsi = calculate_rsi(Cl, 14)
    best = None
    for c in strong:
        lvl = c["avg"]
        dist_pct = ((price - lvl) / lvl) * 100.0
        if 0.15 <= dist_pct <= 1.15 and rsi <= 52:
            best = ("LONG", "Yatay Destege Yakin (Near Horizontal Support)", lvl, len(c["vals"]), dist_pct, rsi)
            break
        if -1.15 <= dist_pct <= -0.15 and rsi >= 48:
            best = ("SHORT", "Yatay Dirence Yakin (Near Horizontal Resistance)", lvl, len(c["vals"]), abs(dist_pct), rsi)
            break
    return best


def yatay_sr_1h_worker():
    for t in adaylar(MIN_HACIM_YATAY_SR, 35, "fut"):
        sym = t["symbol"]
        if not cooldown_ok(son_yatay_sr_sinyalleri, sym, 14400):
            continue
        ks = klines(sym, "1h", 120, "fut")
        if not ks or len(ks) < 80:
            continue
        price = t["_price"]
        res = find_horizontal_sr_levels(ks, price)
        if not res:
            continue
        yon, baslik, lvl, touches, dist_pct, rsi = res
        sl = lvl * (0.982 if yon == "LONG" else 1.018)
        tp1 = price * (1.028 if yon == "LONG" else 0.972)
        tp2 = price * (1.055 if yon == "LONG" else 0.945)
        tp3 = price * (1.090 if yon == "LONG" else 0.910)
        png = render_chart_png(sym, "1h", ks, f"1H YATAY {'DESTEK' if yon=='LONG' else 'DIRENC'} ({touches} TEMAS)", [(lvl, "YATAY S/R", (250, 204, 21)), (tp2, "TP2", (34, 197, 94)), (sl, "SL", (239, 68, 68))])
        cap, edu = build_edu_signal(
            baslik, sym, "1h", yon, price, tp1, tp2, tp3, sl,
            f"• Yatay Seviye: {akilli_fiyat(lvl)} (Gecmiste {touches} kez test edildi!)\n• Seviyeye Uzaklik: %{dist_pct:.2f} | 1h RSI: {rsi:.1f}",
            f"Fiyat son 120 saatte {touches} kez calismis cok guclu bir yatay {'destek' if yon=='LONG' else 'direnc'} bolgesine %{dist_pct:.2f} mesafeye yaklasti.",
            "• <b>Yatay Destek / Direnc:</b> Piyasanin hafizasidir. Fiyat ayni yatay cizgiden ne kadar cok donduyse, orada bekleyen emir sayisi o kadar yuksektir.\n• <b>Yakinlik Uyarisi:</b> En iyi R:R orani fiyat kirildiktan sonra degil, destege/dirence yakin bolgede Stop-Loss dar tutularak yakalanir.",
            "Seviyenin %1.8 disinda saatlik kapanis gelirse destek/direnc kirilmis sayilir ve stop olunur."
        )
        if tg_photo(C, png, cap, edu):
            signal_sent(son_yatay_sr_sinyalleri, sym)
            record_tracked_signal("YATAY_SR_1H", sym, yon, price, tp1, tp2, tp3, sl)


# =====================================================
# MOTOR 23 (YENI): BTC GUNLUK LIKIDITE HAVUZU + FVG/GAP + DAR BANT
# =====================================================
def analyze_btc_liquidity_fvg(ks_1d, price):
    if not ks_1d or len(ks_1d) < 35:
        return None
    H = [float(k[2]) for k in ks_1d]
    L = [float(k[3]) for k in ks_1d]
    Cl = [float(k[4]) for k in ks_1d]
    n = len(ks_1d)

    fvg_found = None
    for i in range(n - 20, n - 2):
        if H[i] < L[i + 2] and min(L[i + 2:]) > H[i]:
            gap_lo, gap_hi = H[i], L[i + 2]
            if (gap_hi - gap_lo) / price >= 0.005 and gap_lo * 0.995 <= price <= gap_hi * 1.015:
                fvg_found = ("LONG", "Bullish FVG (Fiyat Boslugu)", i, n - 1, gap_lo, gap_hi,
                             "BTC gunluk grafikteki doldurulmamis Bullish FVG (alim dengesizligi) bolgesine temas etti.")
                break
        if L[i] > H[i + 2] and max(H[i + 2:]) < L[i]:
            gap_lo, gap_hi = H[i + 2], L[i]
            if (gap_hi - gap_lo) / price >= 0.005 and gap_lo * 0.985 <= price <= gap_hi * 1.005:
                fvg_found = ("SHORT", "Bearish FVG (Fiyat Boslugu)", i, n - 1, gap_lo, gap_hi,
                             "BTC gunluk grafikteki doldurulmamis Bearish FVG (satis dengesizligi) bolgesine ulasti.")
                break
    if fvg_found:
        return ("FVG", fvg_found)

    prev_hi, prev_lo = max(H[-25:-2]), min(L[-25:-2])
    if L[-2] < prev_lo and Cl[-2] > prev_lo:
        return ("LIQ", ("LONG", "BTC Gunluk Dip Likidite Temizligi", n - 25, n - 1, prev_lo, prev_lo,
                        "BTC son 25 gunun en buyuk alt likidite havuzundaki stoplari fitille temizleyip tekrar ustune kapandi."))
    if H[-2] > prev_hi and Cl[-2] < prev_hi:
        return ("LIQ", ("SHORT", "BTC Gunluk Tepe Likidite Temizligi", n - 25, n - 1, prev_hi, prev_hi,
                        "BTC son 25 gunun tepe likidite havuzunu fitille supurup altina geri dondu."))

    rng7 = ((max(H[-8:-1]) - min(L[-8:-1])) / min(L[-8:-1])) * 100.0
    if rng7 <= 3.5:
        return ("RANGE", ("LONG" if Cl[-2] > Cl[-8] else "SHORT", "BTC Dar Bant Sikismasi", n - 8, n - 1, min(L[-8:-1]), max(H[-8:-1]),
                          f"BTC son 7 gunde %{rng7:.1f} gibi cok dar bir bantta sikisti; sert yonlu patlama cok yakin."))
    return None


def btc_likidite_fvg_worker():
    if not cooldown_ok(son_btc_fvg_sinyalleri, "BTCUSDT", 21600):
        return
    ks = klines("BTCUSDT", "1d", 50, "fut")
    if not ks or len(ks) < 35:
        return
    price = float(ks[-1][4])
    res = analyze_btc_liquidity_fvg(ks, price)
    if not res:
        return
    kind, (yon, baslik, i0, i1, vlo, vhi, neden) = res
    sl = (vlo * 0.975) if yon == "LONG" else (vhi * 1.025)
    tp1 = price * (1.03 if yon == "LONG" else 0.97)
    tp2 = price * (1.06 if yon == "LONG" else 0.94)
    tp3 = price * (1.10 if yon == "LONG" else 0.90)
    boxes = [(i0, i1, vlo, vhi, (56, 189, 248))] if kind in ("FVG", "RANGE") else []
    lvls = [(vlo, "LIKIDITE", (250, 204, 21)), (tp2, "TP2", (34, 197, 94)), (sl, "SL", (239, 68, 68))]
    png = render_chart_png("BTCUSDT", "1d", ks, baslik.upper(), lvls, boxes=boxes)
    cap, edu = build_edu_signal(
        f"BTC GUNLUK — {baslik}", "BTCUSDT", "1d", yon, price, tp1, tp2, tp3, sl,
        f"• Bolge: {akilli_fiyat(vlo)} - {akilli_fiyat(vhi)}\n• Analiz Tipi: Kurumsal Likidite & FVG",
        neden,
        "• <b>FVG (Fair Value Gap):</b> Fiyat bir yone cok hizli hareket ettiginde olusan emir boslugudur; piyasa cogu zaman bu dengesizligi doldurmak icin geri doner.\n• <b>Likidite Havuzu (Liquidity Pool):</b> Cok sayida traderin stop-loss emrinin biriktigi eski tepe/diplerdir.",
        "Bitcoin tum altcoinlerin yon belirleyicisidir; BTC bu bolgeden sert tepki verirse tum piyasada domino etkisi olusur."
    )
    if tg_photo(C, png, cap, edu):
        signal_sent(son_btc_fvg_sinyalleri, "BTCUSDT")


# =====================================================
# MOTOR 24 (YENI): CANLI TP1/TP2/TP3 HEDEF VURDU TAKIP MOTORU
# =====================================================
def canli_tp_takip_worker():
    update_tracked_signals(notify_tp_hits=True)


# =====================================================
# MOTOR 25 (YENI): R-MULTIPLE & WIN-RATE ISTATISTIK KARNESI
# =====================================================
def rr_karne_rapor_worker():
    rep = compute_rr_report(hours=168)
    if not rep or rep["total"] < 2:
        return
    rows = [
        ("TOPLAM SINYAL", f"{rep['total']} ADET", rep["total"]),
        ("WIN RATE (RR>=1:1)", f"%{rep['win_rate_1r']:.1f} ({rep['r1']})", rep["win_rate_1r"]),
        ("GUCLU ISLEM (RR>=2:1)", f"%{rep['win_rate_2r']:.1f} ({rep['r2']})", rep["win_rate_2r"]),
        ("STOP OLAN (LOSS)", f"{rep['losses']} ADET", -float(rep["losses"])),
        ("EN IYI R (PIK)", f"{rep['best_sym'].replace('USDT','')} {rep['best_r']:.1f}R", rep["best_r"]),
    ]
    png = render_card_png("7 GUNLUK RR & WIN RATE KARNESI", "Islem Basi %1 Riskle R-Multiple Performansi", rows, (56, 189, 248))
    cap = (
        f"📊 <b>BOT PERFORMANS KARNESI (Son 7 Gun)</b>\n\n"
        f"📌 <b>Toplam Islem:</b> {rep['total']}  |  🛑 <b>Stop (Loss):</b> {rep['losses']}\n"
        f"✅ <b>Basari Orani (Min RR 1:1):</b> <b>%{rep['win_rate_1r']:.1f}</b> ({rep['r1']} islem)\n"
        f"🔥 <b>RR 2:1 ve Uzeri:</b> <b>%{rep['win_rate_2r']:.1f}</b> ({rep['r2']} islem)\n"
        f"🚀 <b>RR 3:1+:</b> {rep['r3']} | <b>RR 5:1+:</b> {rep['r5']}\n"
        f"🏆 <b>En Yuksek Odul:</b> {rep['best_sym']} (<b>{rep['best_r']:.1f} : 1</b>)\n"
        f"💰 <b>Max ROI (%1 Riskle):</b> <b>{rep['max_roi']:+.1f}%</b>"
    )
    edu = (
        "📚 <b>R-MULTIPLE (R:R) KARNESI NASIL OKUNUR?</b>\n"
        "• <b>1R Nedir?</b> Bir islemde Stop-Loss mesafeniz kadar riske attiginiz tutardir (ornegin kasanizin %1'i).\n"
        "• <b>Neden Onemli?</b> %50 basari oraniniz olsa bile ortalama kazanciniz 2R (2 birim) ise, 10 islemde 5 kayip (-5R) ve 5 kazanc (+10R) ile net +5R kar elde edersiniz."
    )
    tg_photo(C, png, cap, edu)


# =====================================================
# RENDER WEB SUNUCUSU
# =====================================================
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"Turkish Educational Crypto Signal Bot v21 (25 Motors) is running."
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
    log("SISTEM", "Turkce Egitici Kripto Sinyal Botu v21 (25 Motor) baslatiliyor.")

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
        ("SPOT_PUMP_7M", SPOT_PUMP_7M_ARALIGI, spot_pump_7m_worker, 35),
        ("YATAY_SR_1H", YATAY_SR_1H_ARALIGI, yatay_sr_1h_worker, 115),
        ("BTC_LIKIDITE_FVG", BTC_LIKIDITE_ARALIGI, btc_likidite_fvg_worker, 145),
        ("CANLI_TP_TAKIP", CANLI_TP_TAKIP_ARALIGI, canli_tp_takip_worker, 95),
        ("RR_KARNE_RAPOR", RR_KARNE_ARALIGI, rr_karne_rapor_worker, 270),
    ]

    for name, interval, fn, startup in motors:
        threading.Thread(target=safe_loop, args=(name, interval, fn, startup), name=name, daemon=True).start()

    try:
        run_web_server()
    except Exception as e:
        log("RENDER", f"Web sunucusu baslatilamadi: {e}")
        while True:
            time.sleep(60)

import urllib.request
import urllib.parse
import urllib.error
import json
import time
import socket
import os
import threading
import http.server
import zlib
import struct
import uuid
from datetime import datetime, timedelta, timezone

# =====================================================
# AYARLAR
# =====================================================
socket.setdefaulttimeout(15)

T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()
PROXY_URL = os.environ.get("PROXY_URL", "").strip()

TR_Saati = timezone(timedelta(hours=3))
DATA_API = "https://data-api.binance.vision"
FAPI = "https://fapi.binance.com"
BYBIT_API = "https://api.bybit.com"
GATE_API = "https://api.gateio.ws"
COINGECKO_API = "https://api.coingecko.com"

# MOTOR ÇALIŞMA SÜRELERİ
CHARTIST_ARALIGI = 3600       # 1 Saat
PUMP_ARALIGI = 60             # 1 Dakika
VOLATILITE_ARALIGI = 60       # 1 Dakika
FORMASYON_ARALIGI = 3600      # 1 Saat
EMA_RIBBON_ARALIGI = 3600     # 1 Saat
SMA200_ARALIGI = 3600         # 1 Saat
MACD_ARALIGI = 3600           # 1 Saat
ATH_ATL_ARALIGI = 3600        # 1 Saat (Kapanmış mum, 200+ gün)
ANLIK_ATH_ATL_ARALIGI = 180   # 3 Dakika (Anlık ATH/ATL + Grafik)
ICHIMOKU_ARALIGI = 3600       # 1 Saat
CHART_PATTERN_ARALIGI = 1800  # 30 Dakika
TOP_MOVERS_ARALIGI = 3600     # 1 Saat

# RAPOR MOTORLARI SÜRELERİ
TOP_GAINERS_ARALIGI = 14400   # 4 Saat
DOMINANS_ARALIGI = 14400      # 4 Saat
PUMP_WINNER_ARALIGI = 21600   # 6 Saat

# FİLTRELER
MIN_HACIM_CHARTIST = 10_000_000
MIN_HACIM_PUMP = 5_000_000
MIN_HACIM_VOLATILITE = 10_000_000
MIN_HACIM_FORMASYON = 15_000_000
MIN_HACIM_EMA_RIBBON = 10_000_000
MIN_HACIM_SMA200 = 5_000_000
MIN_HACIM_MACD = 10_000_000
MIN_HACIM_ATH_ATL = 5_000_000
MIN_HACIM_ANLIK_ATH_ATL = 3_000_000
MIN_HACIM_ICHIMOKU = 10_000_000
MIN_HACIM_PATTERN = 15_000_000
MIN_HACIM_MOVERS = 5_000_000

# API limit koruması: her döngüde en yüksek hacimli ilk N coin taranır
MAX_TARAMA = 60

YASAKLI_HISSELER = {
    "AAPLUSDT", "TSLAUSDT", "NVDAUSDT", "COINUSDT", "MSTRUSDT",
    "SQQQUSDT", "TQQQUSDT", "SPYUSDT", "QQQUSDT", "AMZNUSDT",
    "GOOGLUSDT", "METAUSDT", "NFLXUSDT", "BABAUSDT", "MSFTUSDT",
    "HOODUSDT", "CRCLUSDT", "XAUUSDT", "XAGUSDT", "PAXGUSDT",
    "SOXLUSDT", "SOXLBUSDT", "SOXSUSDT", "SOXSBUSDT",
    "SNXXUSDT", "SNXXBUSDT", "INTCUSDT", "INTCBUSDT", "CRDOUSDT"
}

# Sinyal Hafızaları
son_sinyaller = {}
son_pump_sinyalleri = {}
son_volatilite_sinyalleri = {}
son_formasyon_sinyalleri = {}
son_ema_ribbon_sinyalleri = {}
son_sma200_sinyalleri = {}
son_macd_sinyalleri = {}
son_ath_atl_sinyalleri = {}
son_anlik_ath_atl_sinyalleri = {}
son_ichimoku_sinyalleri = {}
son_chart_pattern_sinyalleri = {}

# Hız kontrolü, önbellekler ve durum takibi
_api_lock = threading.Lock()
_ticker_lock = threading.Lock()
_symbol_lock = threading.Lock()
_cache_lock = threading.Lock()

_last_api_call = {"FAPI": 0.0, "BYBIT": 0.0, "GATE": 0.0, "DATA_API": 0.0, "COINGECKO": 0.0}
_source_block_until = {"FAPI": 0.0, "BYBIT": 0.0, "GATE": 0.0, "DATA_API": 0.0, "COINGECKO": 0.0}
_source_status = {
    "FAPI": "Kontrol ediliyor...",
    "BYBIT": "Hazır (yedek)",
    "GATE": "Hazır (yedek)",
    "DATA_API": "Hazır (yedek)",
    "COINGECKO": "Hazır",
}
_telegram_status = "Kontrol ediliyor..."
_last_top_gainers = "Henüz gönderilmedi"
_last_dominans = "Henüz gönderilmedi"
_prev_btc_dom = None
_active_futures_source = "FAPI"

_futures_ticker_cache = {"time": 0.0, "data": [], "source": "FAPI"}
_spot_ticker_cache = {"time": 0.0, "data": [], "source": "DATA_API"}
_symbol_cache = {"time": 0.0, "symbols": set()}
_gate_contracts = {}
_kline_cache = {}

API_MIN_GAP = 0.15
KLINE_CACHE_TTL = 45
TICKER_CACHE_TTL = 45

if PROXY_URL:
    _opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY_URL, "https": PROXY_URL})
    )
else:
    _opener = urllib.request.build_opener()


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


def format_hacim(v):
    v = float(v)
    if v >= 1_000_000_000:
        return f"{v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.0f}M"
    return f"{v / 1_000:.0f}K"


def tg(chat_id, message):
    global _telegram_status
    if not T or not chat_id:
        _telegram_status = "HATA: BOT_TOKEN veya CHAT_ID boş"
        return False
    try:
        data = urllib.parse.urlencode({
            "chat_id": str(chat_id),
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true"
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{T}/sendMessage",
            data=data,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            res = json.loads(response.read().decode("utf-8"))
            ok = res.get("ok", False)
            _telegram_status = "OK" if ok else f"Reddedildi: {res}"
            if not ok:
                log("TELEGRAM", f"Gönderim reddedildi: {res}")
            return ok
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="ignore")[:200]
        except Exception:
            pass
        _telegram_status = f"HTTP {e.code}: {err_body}"
        log("TELEGRAM", f"HTTP {e.code} hatası: {err_body}")
        return False
    except Exception as e:
        _telegram_status = f"Hata: {e}"
        log("TELEGRAM", f"Gönderim hatası: {e}")
        return False


def tg_photo(chat_id, png_bytes, caption):
    """Ek kütüphanesiz multipart/form-data ile Telegram'a grafik PNG gönderir."""
    global _telegram_status
    if not T or not chat_id:
        return False
    if not png_bytes:
        return tg(chat_id, caption)
    boundary = f"----BotBoundary{uuid.uuid4().hex}"
    parts = []
    for name, val in (("chat_id", str(chat_id)), ("caption", caption), ("parse_mode", "HTML")):
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{val}\r\n".encode("utf-8")
        )
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
                "User-Agent": "Mozilla/5.0",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body))
            }
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            res = json.loads(response.read().decode("utf-8"))
            ok = res.get("ok", False)
            if ok:
                _telegram_status = "OK"
                return True
    except Exception as e:
        log("TELEGRAM", f"Fotoğraf gönderim hatası, metin olarak deneniyor: {e}")
    return tg(chat_id, caption)


# =====================================================
# SAF PYTHON GRAFİK PNG ÇİZİCİ (Ek kütüphane gerektirmez)
# =====================================================
_FONT_5X7 = {
    "0": [0x0E,0x11,0x13,0x15,0x19,0x11,0x0E], "1": [0x04,0x0C,0x04,0x04,0x04,0x04,0x0E],
    "2": [0x0E,0x11,0x01,0x06,0x08,0x10,0x1F], "3": [0x1F,0x02,0x04,0x06,0x01,0x11,0x0E],
    "4": [0x02,0x06,0x0A,0x12,0x1F,0x02,0x02], "5": [0x1F,0x10,0x1E,0x01,0x01,0x11,0x0E],
    "6": [0x06,0x08,0x10,0x1E,0x11,0x11,0x0E], "7": [0x1F,0x01,0x02,0x04,0x08,0x08,0x08],
    "8": [0x0E,0x11,0x11,0x0E,0x11,0x11,0x0E], "9": [0x0E,0x11,0x11,0x0F,0x01,0x02,0x0C],
    "A": [0x0E,0x11,0x11,0x1F,0x11,0x11,0x11], "B": [0x1E,0x11,0x11,0x1E,0x11,0x11,0x1E],
    "C": [0x0E,0x11,0x10,0x10,0x10,0x11,0x0E], "D": [0x1C,0x12,0x11,0x11,0x11,0x12,0x1C],
    "E": [0x1F,0x10,0x10,0x1E,0x10,0x10,0x1F], "F": [0x1F,0x10,0x10,0x1E,0x10,0x10,0x10],
    "G": [0x0E,0x11,0x10,0x17,0x11,0x11,0x0F], "H": [0x11,0x11,0x11,0x1F,0x11,0x11,0x11],
    "I": [0x0E,0x04,0x04,0x04,0x04,0x04,0x0E], "J": [0x07,0x02,0x02,0x02,0x12,0x12,0x0C],
    "K": [0x11,0x12,0x14,0x18,0x14,0x12,0x11], "L": [0x10,0x10,0x10,0x10,0x10,0x10,0x1F],
    "M": [0x11,0x1B,0x15,0x15,0x11,0x11,0x11], "N": [0x11,0x19,0x15,0x13,0x11,0x11,0x11],
    "O": [0x0E,0x11,0x11,0x11,0x11,0x11,0x0E], "P": [0x1E,0x11,0x11,0x1E,0x10,0x10,0x10],
    "Q": [0x0E,0x11,0x11,0x11,0x15,0x12,0x0D], "R": [0x1E,0x11,0x11,0x1E,0x14,0x12,0x11],
    "S": [0x0F,0x10,0x10,0x0E,0x01,0x01,0x1E], "T": [0x1F,0x04,0x04,0x04,0x04,0x04,0x04],
    "U": [0x11,0x11,0x11,0x11,0x11,0x11,0x0E], "V": [0x11,0x11,0x11,0x11,0x11,0x0A,0x04],
    "W": [0x11,0x11,0x11,0x15,0x15,0x1B,0x11], "X": [0x11,0x11,0x0A,0x04,0x0A,0x11,0x11],
    "Y": [0x11,0x11,0x0A,0x04,0x04,0x04,0x04], "Z": [0x1F,0x01,0x02,0x04,0x08,0x10,0x1F],
    ".": [0x00,0x00,0x00,0x00,0x00,0x0C,0x0C], "-": [0x00,0x00,0x00,0x1F,0x00,0x00,0x00],
    "+": [0x00,0x04,0x04,0x1F,0x04,0x04,0x00], "%": [0x19,0x19,0x02,0x04,0x08,0x13,0x13],
    ":": [0x00,0x0C,0x0C,0x00,0x0C,0x0C,0x00], "/": [0x01,0x02,0x02,0x04,0x08,0x08,0x10],
    " ": [0x00,0x00,0x00,0x00,0x00,0x00,0x00],
}


def render_chart_png(symbol, klines, title_text):
    """Saf Python ile Mum + Hacim + RSI koyu tema PNG grafik üretir."""
    if not klines or len(klines) < 2:
        return None
    bars = klines[-50:]
    W, H = 720, 460
    BG = (20, 22, 27)
    GRID = (42, 46, 55)
    GREEN = (14, 173, 128)
    RED = (234, 67, 83)
    TEXT = (225, 229, 236)
    RSI_CLR = (78, 185, 166)
    LINE_CLR = (245, 190, 65)

    img = bytearray(BG * (W * H))

    def set_px(x, y, clr):
        if 0 <= x < W and 0 <= y < H:
            i = (y * W + x) * 3
            img[i], img[i + 1], img[i + 2] = clr

    def fill_rect(x0, y0, x1, y1, clr):
        x0, x1 = max(0, min(x0, x1)), min(W - 1, max(x0, x1))
        y0, y1 = max(0, min(y0, y1)), min(H - 1, max(y0, y1))
        for yy in range(y0, y1 + 1):
            row_start = (yy * W + x0) * 3
            img[row_start:row_start + (x1 - x0 + 1) * 3] = bytes(clr) * (x1 - x0 + 1)

    def draw_line(x0, y0, x1, y1, clr):
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        while True:
            set_px(x0, y0, clr)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy; x0 += sx
            if e2 <= dx:
                err += dx; y0 += sy

    def draw_text(x, y, text, clr=TEXT, scale=2):
        cx = x
        for ch in str(text).upper():
            glyph = _FONT_5X7.get(ch, _FONT_5X7[" "])
            for r in range(7):
                bits = glyph[r]
                for c in range(5):
                    if bits & (1 << (4 - c)):
                        fill_rect(cx + c * scale, y + r * scale, cx + c * scale + scale - 1, y + r * scale + scale - 1, clr)
            cx += 6 * scale

    L, R = 66, W - 24
    P_TOP, P_BOT = 36, 290
    V_TOP, V_BOT = 304, 368
    R_TOP, R_BOT = 382, 442

    for y_g in (P_TOP, 99, 163, 226, P_BOT, V_TOP, V_BOT, R_TOP, 412, R_BOT):
        draw_line(L, y_g, R, y_g, GRID)
    draw_line(L, P_TOP, L, R_BOT, GRID)
    draw_line(R, P_TOP, R, R_BOT, GRID)

    opens = [float(k[1]) for k in bars]
    highs = [float(k[2]) for k in bars]
    lows = [float(k[3]) for k in bars]
    closes = [float(k[4]) for k in bars]
    vols = [float(k[7]) for k in bars]

    p_min, p_max = min(lows), max(highs)
    if p_max <= p_min:
        p_max = p_min * 1.01 + 1e-9
    pad = (p_max - p_min) * 0.08
    p_min, p_max = p_min - pad, p_max + pad
    v_max = max(vols) if max(vols) > 0 else 1.0

    def py(val):
        return int(P_BOT - ((val - p_min) / (p_max - p_min)) * (P_BOT - P_TOP))

    n = len(bars)
    slot = (R - L) / max(n, 1)
    bw = max(2, int(slot * 0.68))

    for i in range(n):
        xc = int(L + (i + 0.5) * slot)
        clr = GREEN if closes[i] >= opens[i] else RED
        draw_line(xc, py(highs[i]), xc, py(lows[i]), clr)
        y_o, y_c = py(opens[i]), py(closes[i])
        if abs(y_o - y_c) < 2:
            y_c = y_o + 2
        fill_rect(xc - bw // 2, min(y_o, y_c), xc + bw // 2, max(y_o, y_c), clr)
        vh = int((vols[i] / v_max) * (V_BOT - V_TOP))
        fill_rect(xc - bw // 2, V_BOT - vh, xc + bw // 2, V_BOT, clr)

    y_last = py(closes[-1])
    for xx in range(L, R, 6):
        draw_line(xx, y_last, min(xx + 3, R), y_last, LINE_CLR)

    if n >= 15:
        rsi_pts = []
        for i in range(14, n):
            rv = calculate_rsi(closes[:i + 1], 14)
            rx = int(L + (i + 0.5) * slot)
            ry = int(R_BOT - (rv / 100.0) * (R_BOT - R_TOP))
            rsi_pts.append((rx, ry))
        for j in range(1, len(rsi_pts)):
            draw_line(rsi_pts[j - 1][0], rsi_pts[j - 1][1], rsi_pts[j][0], rsi_pts[j][1], RSI_CLR)

    draw_text(L, 12, title_text, TEXT, scale=2)
    draw_text(6, P_TOP, akilli_fiyat(p_max)[:8], TEXT, scale=1)
    draw_text(6, (P_TOP + P_BOT) // 2, akilli_fiyat((p_max + p_min) / 2)[:8], TEXT, scale=1)
    draw_text(6, P_BOT - 7, akilli_fiyat(p_min)[:8], TEXT, scale=1)
    draw_text(6, V_TOP + 2, "VOL", TEXT, scale=1)
    draw_text(6, R_TOP + 2, "RSI", TEXT, scale=1)

    raw_rows = bytearray()
    stride = W * 3
    for y in range(H):
        raw_rows.append(0)
        raw_rows.extend(img[y * stride:(y + 1) * stride])

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw_rows), 6)) + chunk(b"IEND", b"")


def _source_blocked(source):
    return time.time() < _source_block_until.get(source, 0.0)


def _source_remaining(source):
    return max(0, int(_source_block_until.get(source, 0.0) - time.time()))


def gj(url, source="FAPI", cache_key=None, cache_ttl=0):
    if cache_key and cache_ttl:
        with _cache_lock:
            item = _kline_cache.get(cache_key)
            if item and time.time() - item[0] < cache_ttl:
                return item[1]

    with _api_lock:
        now = time.time()
        if now < _source_block_until.get(source, 0.0):
            return None
        wait = API_MIN_GAP - (now - _last_api_call.get(source, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_api_call[source] = time.time()

    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with _opener.open(req, timeout=15) as response:
            weight = response.headers.get("X-MBX-USED-WEIGHT-1M")
            if weight and weight.isdigit() and int(weight) > 1800:
                log(source, f"Yüksek API ağırlığı ({weight}/dk), 5 sn yavaşlatılıyor...")
                time.sleep(5)
            result = json.loads(response.read().decode("utf-8"))
        _source_status[source] = "OK"
        if cache_key and cache_ttl:
            with _cache_lock:
                _kline_cache[cache_key] = (time.time(), result)
        return result
    except urllib.error.HTTPError as e:
        if e.code in (418, 429):
            retry_after = e.headers.get("Retry-After")
            try:
                pause = max(120, int(retry_after)) if retry_after else 600
            except Exception:
                pause = 600
            with _api_lock:
                _source_block_until[source] = time.time() + pause
            _source_status[source] = f"HTTP {e.code} ({pause}sn banlı)"
            log(source, f"HTTP {e.code}; {pause} sn durduruldu -> yedek kaynağa geçilecek.")
        elif e.code == 451:
            with _api_lock:
                _source_block_until[source] = time.time() + 3600
            _source_status[source] = "HTTP 451 (Bölge engeli - sunucu IP)"
            log(source, "HTTP 451 (Binance bölge engeli); yedek kaynağa geçilecek.")
        elif e.code == 403:
            with _api_lock:
                _source_block_until[source] = time.time() + 1800
            _source_status[source] = "HTTP 403 (Erişim engeli)"
            log(source, f"HTTP 403: {url.split('?')[0]}")
        else:
            _source_status[source] = f"HTTP {e.code}"
            log(source, f"HTTP {e.code}: {url.split('?')[0]}")
        return None
    except Exception as e:
        _source_status[source] = f"Bağlantı hatası: {e}"
        log(source, f"Bağlantı/veri hatası: {e}")
        return None


def _aktif_fapi_semboller():
    with _symbol_lock:
        if time.time() - _symbol_cache["time"] < 21600 and _symbol_cache["symbols"]:
            return _symbol_cache["symbols"]
    data = gj(f"{FAPI}/fapi/v1/exchangeInfo", source="FAPI")
    if not isinstance(data, dict):
        return _symbol_cache["symbols"] or None
    symbols = set()
    for item in data.get("symbols", []):
        sym = item.get("symbol", "")
        if (item.get("status") == "TRADING"
                and item.get("contractType") == "PERPETUAL"
                and item.get("quoteAsset") == "USDT"
                and sym.endswith("USDT")
                and sym not in YASAKLI_HISSELER):
            symbols.add(sym)
    with _symbol_lock:
        _symbol_cache["time"] = time.time()
        _symbol_cache["symbols"] = symbols
    log("FAPI", f"Aktif USDT-M Perpetual sözleşme sayısı: {len(symbols)}")
    return symbols


def _fetch_tickers_from(source):
    if _source_blocked(source):
        return None

    if source == "FAPI":
        raw = gj(f"{FAPI}/fapi/v1/ticker/24hr", source="FAPI")
        if not isinstance(raw, list):
            return None
        active = _aktif_fapi_semboller()
        out = []
        for t in raw:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT") or sym in YASAKLI_HISSELER:
                continue
            if active and sym not in active:
                continue
            try:
                out.append({
                    "symbol": sym,
                    "priceChangePercent": float(t.get("priceChangePercent", 0)),
                    "quoteVolume": float(t.get("quoteVolume", 0)),
                    "lastPrice": float(t.get("lastPrice", 0)),
                    "highPrice": float(t.get("highPrice", 0)),
                    "lowPrice": float(t.get("lowPrice", 0)),
                })
            except (TypeError, ValueError):
                continue
        return out or None

    if source == "BYBIT":
        raw = gj(f"{BYBIT_API}/v5/market/tickers?category=linear", source="BYBIT")
        if not isinstance(raw, dict) or raw.get("retCode") != 0:
            return None
        out = []
        for t in raw.get("result", {}).get("list", []):
            sym = t.get("symbol", "")
            if not sym.endswith("USDT") or "-" in sym or sym in YASAKLI_HISSELER:
                continue
            try:
                out.append({
                    "symbol": sym,
                    "priceChangePercent": float(t.get("price24hPcnt", 0)) * 100.0,
                    "quoteVolume": float(t.get("turnover24h", 0)),
                    "lastPrice": float(t.get("lastPrice", 0)),
                    "highPrice": float(t.get("highPrice24h", 0)),
                    "lowPrice": float(t.get("lowPrice24h", 0)),
                })
            except (TypeError, ValueError):
                continue
        return out or None

    if source == "GATE":
        raw = gj(f"{GATE_API}/api/v4/futures/usdt/tickers", source="GATE")
        if not isinstance(raw, list):
            return None
        out = []
        for t in raw:
            contract = t.get("contract", "")
            if not contract.endswith("_USDT"):
                continue
            sym = contract.replace("_", "")
            if sym in YASAKLI_HISSELER:
                continue
            _gate_contracts[sym] = contract
            try:
                out.append({
                    "symbol": sym,
                    "priceChangePercent": float(t.get("change_percentage", 0)),
                    "quoteVolume": float(t.get("volume_24h_quote", 0)),
                    "lastPrice": float(t.get("last", 0)),
                    "highPrice": float(t.get("high_24h", 0)),
                    "lowPrice": float(t.get("low_24h", 0)),
                })
            except (TypeError, ValueError):
                continue
        return out or None

    if source == "DATA_API":
        raw = gj(f"{DATA_API}/api/v3/ticker/24hr", source="DATA_API")
        if not isinstance(raw, list):
            return None
        out = []
        for t in raw:
            sym = t.get("symbol", "")
            if (not sym.endswith("USDT") or sym in YASAKLI_HISSELER
                    or any(x in sym for x in ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT"))):
                continue
            try:
                out.append({
                    "symbol": sym,
                    "priceChangePercent": float(t.get("priceChangePercent", 0)),
                    "quoteVolume": float(t.get("quoteVolume", 0)),
                    "lastPrice": float(t.get("lastPrice", 0)),
                    "highPrice": float(t.get("highPrice", 0)),
                    "lowPrice": float(t.get("lowPrice", 0)),
                })
            except (TypeError, ValueError):
                continue
        return out or None

    return None


def get_futures_tickers(force=False):
    global _active_futures_source
    with _ticker_lock:
        if (not force and _futures_ticker_cache["data"]
                and not _source_blocked(_futures_ticker_cache["source"])
                and time.time() - _futures_ticker_cache["time"] < TICKER_CACHE_TTL):
            return _futures_ticker_cache["data"], _futures_ticker_cache["source"]

        for src in ("FAPI", "BYBIT", "GATE", "DATA_API"):
            rows = _fetch_tickers_from(src)
            if rows:
                _active_futures_source = src
                _futures_ticker_cache["time"] = time.time()
                _futures_ticker_cache["data"] = rows
                _futures_ticker_cache["source"] = src
                return rows, src

        return _futures_ticker_cache["data"], _futures_ticker_cache["source"]


def get_spot_tickers():
    with _ticker_lock:
        if (_spot_ticker_cache["data"]
                and not _source_blocked(_spot_ticker_cache["source"])
                and time.time() - _spot_ticker_cache["time"] < TICKER_CACHE_TTL):
            return _spot_ticker_cache["data"], _spot_ticker_cache["source"]

        for src in ("DATA_API", "BYBIT", "FAPI"):
            rows = _fetch_tickers_from(src)
            if rows:
                _spot_ticker_cache["time"] = time.time()
                _spot_ticker_cache["data"] = rows
                _spot_ticker_cache["source"] = src
                return rows, src
        return _spot_ticker_cache["data"], _spot_ticker_cache["source"]


def get_klines(symbol, interval, limit, source=None):
    src = source or _active_futures_source
    base_key = f"{src}:{symbol}:{interval}"
    with _cache_lock:
        item = _kline_cache.get(base_key)
        if item and time.time() - item[0] < KLINE_CACHE_TTL and len(item[1]) >= limit:
            return item[1][-limit:]

    res = None
    if src == "FAPI":
        res = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}", source="FAPI")
    elif src == "DATA_API":
        res = gj(f"{DATA_API}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}", source="DATA_API")
    elif src == "BYBIT":
        b_iv = {"5m": "5", "15m": "15", "1h": "60", "4h": "240", "12h": "720", "1d": "D"}.get(interval)
        if not b_iv:
            return None
        raw = gj(
            f"{BYBIT_API}/v5/market/kline?category=linear&symbol={symbol}&interval={b_iv}&limit={min(limit, 1000)}",
            source="BYBIT"
        )
        if isinstance(raw, dict) and raw.get("retCode") == 0:
            raw_list = raw.get("result", {}).get("list", [])
            if raw_list:
                res = [[int(k[0]), k[1], k[2], k[3], k[4], k[5], int(k[0]), k[6]] for k in reversed(raw_list)]
    elif src == "GATE":
        g_iv = {"5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "8h": "8h", "1d": "1d"}.get(interval)
        if not g_iv:
            return None
        contract = _gate_contracts.get(symbol) or symbol.replace("USDT", "_USDT")
        raw = gj(
            f"{GATE_API}/api/v4/futures/usdt/candlesticks?contract={contract}&interval={g_iv}&limit={min(limit, 1000)}",
            source="GATE"
        )
        if isinstance(raw, list) and raw:
            res = [
                [int(d.get("t", 0)) * 1000, d.get("o", "0"), d.get("h", "0"), d.get("l", "0"), d.get("c", "0"),
                 d.get("v", "0"), int(d.get("t", 0)) * 1000, d.get("sum", "0")]
                for d in raw
            ]

    if isinstance(res, list) and res:
        with _cache_lock:
            existing = _kline_cache.get(base_key)
            if not existing or time.time() - existing[0] >= KLINE_CACHE_TTL or len(res) >= len(existing[1]):
                _kline_cache[base_key] = (time.time(), res)
    return res


def filter_candidates(rows, min_hacim, limit=MAX_TARAMA):
    aday = [t for t in rows if t["quoteVolume"] >= min_hacim]
    aday.sort(key=lambda x: x["quoteVolume"], reverse=True)
    return aday[:limit]


def calculate_ema(prices, period):
    if len(prices) < period:
        return []
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for price in prices[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema


def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100
    return 100 - (100 / (1 + (avg_gain / avg_loss)))


def calculate_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow + signal + 2:
        return None, None, None, None
    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)
    if not ema_fast or not ema_slow:
        return None, None, None, None
    offset = slow - fast
    macd_line = [ema_fast[i + offset] - ema_slow[i] for i in range(len(ema_slow))]
    signal_line = calculate_ema(macd_line, signal)
    if len(signal_line) < 2:
        return None, None, None, None
    return macd_line[-1], signal_line[-1], macd_line[-2], signal_line[-2]


def trap_radar_check(symbol, source=None):
    src = source or _active_futures_source
    try:
        oi_artiyor = False
        alici_baskin = False
        veri_var = False

        if src == "FAPI" and not _source_blocked("FAPI"):
            oi_data = gj(f"{FAPI}/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=2", source="FAPI")
            taker_data = gj(f"{FAPI}/futures/data/takerlongshortRatio?symbol={symbol}&period=5m&limit=1", source="FAPI")
            if isinstance(oi_data, list) and len(oi_data) >= 2:
                veri_var = True
                oi_old = float(oi_data[0]["sumOpenInterest"])
                oi_new = float(oi_data[1]["sumOpenInterest"])
                if oi_old > 0 and ((oi_new - oi_old) / oi_old) * 100 > 0.2:
                    oi_artiyor = True
            if isinstance(taker_data, list) and len(taker_data) > 0:
                veri_var = True
                buy_vol = float(taker_data[0]["buyVol"])
                sell_vol = float(taker_data[0]["sellVol"])
                total = buy_vol + sell_vol
                if total > 0 and (buy_vol / total) * 100 > 55:
                    alici_baskin = True

        elif src == "BYBIT" and not _source_blocked("BYBIT"):
            oi_res = gj(f"{BYBIT_API}/v5/market/open-interest?category=linear&symbol={symbol}&intervalTime=5min&limit=2", source="BYBIT")
            ratio_res = gj(f"{BYBIT_API}/v5/market/account-ratio?category=linear&symbol={symbol}&period=5min&limit=1", source="BYBIT")
            oi_list = oi_res.get("result", {}).get("list", []) if isinstance(oi_res, dict) else []
            ratio_list = ratio_res.get("result", {}).get("list", []) if isinstance(ratio_res, dict) else []
            if len(oi_list) >= 2:
                veri_var = True
                oi_new = float(oi_list[0].get("openInterest", 0))
                oi_old = float(oi_list[1].get("openInterest", 0))
                if oi_old > 0 and ((oi_new - oi_old) / oi_old) * 100 > 0.2:
                    oi_artiyor = True
            if ratio_list:
                veri_var = True
                if float(ratio_list[0].get("buyRatio", 0)) > 0.55:
                    alici_baskin = True

        if not veri_var:
            return ""
        if oi_artiyor and alici_baskin:
            return "🐋 <b>BALİNA TEYİDİ (Gerçek Pump)</b>"
        elif not alici_baskin:
            return "⚠️ <b>DİKKAT: SAHTE PUMP (TUZAK OLABİLİR)</b>"
        else:
            return "⚖️ <b>Kısmi Teyit</b>"
    except Exception:
        return ""


def find_pivots(highs, lows, window=3):
    pivot_highs, pivot_lows = [], []
    for i in range(window, len(highs) - window):
        if highs[i] == max(highs[i - window:i + window + 1]):
            pivot_highs.append((i, highs[i]))
        if lows[i] == min(lows[i - window:i + window + 1]):
            pivot_lows.append((i, lows[i]))
    return pivot_highs, pivot_lows


# =====================================================
# MOTOR 1: TOP GAINERS
# =====================================================
def top_gainers_loop():
    global _last_top_gainers
    log("TOP_GAINERS", "Günlük En İyi Hareketler Rapor Botu Başladı.")
    time.sleep(10)
    while True:
        try:
            tickers, src = get_futures_tickers()
            if not tickers:
                time.sleep(60)
                continue

            temiz = [t for t in tickers if t["quoteVolume"] > 5_000_000 and t["priceChangePercent"] > 0]
            temiz.sort(key=lambda x: x["priceChangePercent"], reverse=True)
            top_5 = temiz[:5]

            if top_5:
                kaynak_etiket = "Binance Futures" if src == "FAPI" else f"Futures ({src})" if src in ("BYBIT", "GATE") else "Binance Spot Yedek"
                msg = f"🔥 <b>Günün En İyi Hareketleri ({kaynak_etiket})</b>\n"
                msg += "<i>24 saatlik yükseliş ve USDT hacmine göre</i>\n\n"
                medals = ["🥇", "🥈", "🥉", "🏅", "⭐"]
                for i, t in enumerate(top_5):
                    symbol = t["symbol"].replace("USDT", "")
                    msg += f"{medals[i]} <b>{symbol}</b> +{t['priceChangePercent']:.2f}% | {akilli_fiyat(t['lastPrice'])}\n"

                if tg(C, msg):
                    _last_top_gainers = f"{simdi().strftime('%H:%M:%S')} ({src})"
                    log("TOP_GAINERS", f"Rapor gönderildi ({src}).")
                time.sleep(TOP_GAINERS_ARALIGI)
            else:
                time.sleep(300)
        except Exception as e:
            log("TOP_GAINERS", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 2: PUMP WINNER
# =====================================================
def pump_winner_loop():
    log("PUMP_WINNER", "Günün Kazananı (Pump Dedektör) Rapor Botu Başladı.")
    time.sleep(20)
    while True:
        try:
            tickers, src = get_futures_tickers()
            if not tickers:
                time.sleep(120)
                continue

            temiz = [t for t in tickers if t["quoteVolume"] > 10_000_000]
            temiz.sort(key=lambda x: x["priceChangePercent"], reverse=True)
            if not temiz or temiz[0]["priceChangePercent"] < 15:
                time.sleep(PUMP_WINNER_ARALIGI)
                continue

            winner = temiz[0]
            symbol = winner["symbol"]
            day_low = winner.get("lowPrice", 0.0)
            day_high = winner.get("highPrice", 0.0)

            if day_low <= 0 or day_high <= 0:
                ks = get_klines(symbol, "1d", 2, source=src)
                if isinstance(ks, list) and ks:
                    day_low = float(ks[-1][3])
                    day_high = float(ks[-1][2])

            if day_low <= 0:
                time.sleep(300)
                continue

            max_move = ((day_high - day_low) / day_low) * 100
            msg = (
                f"🏆 <b>GÜNÜN KAZANANI: {symbol}</b>\n\n"
                f"🔥 24 saat içinde en çok yükselen coin! ({src})\n\n"
                f"📉 <b>Dip Fiyat:</b> {akilli_fiyat(day_low)}\n"
                f"🚀 <b>Zirve Fiyat:</b> {akilli_fiyat(day_high)}\n"
                f"📈 <b>Maksimum Yükseliş:</b> <b>+{max_move:.1f}%</b>\n\n"
                f"💡 <b>Değerlendirme:</b> {symbol} gün içerisinde muazzam bir hacim ve fiyat patlaması yaşadı. Pump Dedektör verilerimize göre günün en kârlı hareketi bu coinde gerçekleşti."
            )
            if tg(C, msg):
                log("PUMP_WINNER", f"Rapor gönderildi: {symbol}")
            time.sleep(PUMP_WINNER_ARALIGI)
        except Exception as e:
            log("PUMP_WINNER", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 3: PİYASA YÖNÜ & DOMİNANS RAPORU
# =====================================================
def dominans_loop():
    global _prev_btc_dom, _last_dominans
    log("DOMINANS", "Piyasa Yönü ve Dominans Rapor Motoru Başladı.")
    time.sleep(25)
    while True:
        try:
            data = gj(f"{COINGECKO_API}/api/v3/global", source="COINGECKO")
            gdata = data.get("data", {}) if isinstance(data, dict) else {}
            mcap = gdata.get("market_cap_percentage", {})
            btc_dom = mcap.get("btc")
            eth_dom = mcap.get("eth")

            if btc_dom is None or eth_dom is None:
                time.sleep(300)
                continue

            btc_dom = float(btc_dom)
            eth_dom = float(eth_dom)

            durum = "🟡 Yatay / Karışık"
            yorum = "Bitcoin dominansı dengede. Piyasa yön arıyor; hacim teyidi olan güçlü coinlere odaklanılmalı."

            if _prev_btc_dom is not None:
                diff = btc_dom - _prev_btc_dom
                if diff >= 0.08:
                    durum = "🟢 Yükselişte"
                    yorum = "Piyasadaki para Bitcoin'e akıyor. Altcoinlerde (küçük coinlerde) dikkatli olunmalı. BTC ağırlıklı işlemler ön planda."
                elif diff <= -0.08:
                    durum = "🔴 Düşüşte (Altcoin Lehine)"
                    yorum = "Bitcoin dominansı geri çekiliyor; sermaye altcoinlere kayıyor. Güçlü hacimli altcoinlerde fırsatlar artabilir."
            else:
                tickers, _ = get_futures_tickers()
                btc = next((x for x in tickers if x["symbol"] == "BTCUSDT"), None)
                eth = next((x for x in tickers if x["symbol"] == "ETHUSDT"), None)
                if btc and eth:
                    if btc["priceChangePercent"] - eth["priceChangePercent"] >= 0.5:
                        durum = "🟢 Yükselişte"
                        yorum = "Piyasadaki para Bitcoin'e akıyor. Altcoinlerde (küçük coinlerde) dikkatli olunmalı. BTC ağırlıklı işlemler ön planda."
                    elif eth["priceChangePercent"] - btc["priceChangePercent"] >= 0.5:
                        durum = "🔴 Düşüşte (Altcoin Lehine)"
                        yorum = "Bitcoin dominansı geri çekiliyor; sermaye altcoinlere kayıyor. Güçlü hacimli altcoinlerde fırsatlar artabilir."

            _prev_btc_dom = btc_dom

            msg = (
                f"👑 <b>PİYASA YÖNÜ &amp; DOMİNANS RAPORU</b>\n\n"
                f"🪙 <b>BTC Dominansı:</b> %{btc_dom:.2f}\n"
                f"💎 <b>ETH Dominansı:</b> %{eth_dom:.2f}\n\n"
                f"📊 <b>Durum:</b> {durum}\n"
                f"💡 <b>Değerlendirme:</b> {yorum}"
            )
            if tg(C, msg):
                _last_dominans = f"{simdi().strftime('%H:%M:%S')} (BTC %{btc_dom:.2f})"
                log("DOMINANS", f"Rapor gönderildi (BTC %{btc_dom:.2f})")
            time.sleep(DOMINANS_ARALIGI)
        except Exception as e:
            log("DOMINANS", f"Hata: {e}")
            time.sleep(120)


# =====================================================
# MOTOR 4: CHARTIST (SPOT EMA200)
# =====================================================
def chartist_loop():
    log("CHARTIST", "Zırhlı EMA200 Motoru Başladı.")
    time.sleep(30)
    while True:
        try:
            tickers, src = get_spot_tickers()
            if not tickers:
                time.sleep(60)
                continue
            for t in filter_candidates(tickers, MIN_HACIM_CHARTIST, MAX_TARAMA):
                symbol = t["symbol"]
                for tf in ["8h", "12h", "1d", "3d"]:
                    cooldown_key = f"CH_{tf}_{symbol}"
                    if cooldown_key in son_sinyaller and time.time() - son_sinyaller[cooldown_key] < 86400:
                        continue
                    ks = get_klines(symbol, tf, 250, source=src)
                    if not isinstance(ks, list) or len(ks) < 205:
                        continue
                    closes = [float(k[4]) for k in ks[:-1]]
                    if len(closes) < 202:
                        continue
                    curr, prev = closes[-1], closes[-2]
                    ema200 = calculate_ema(closes, 200)
                    if len(ema200) >= 2 and prev <= ema200[-2] and curr > ema200[-1] and curr > ema200[-1] * 1.01:
                        vol = [float(k[7]) for k in ks[:-1]]
                        avg_v = sum(vol[-21:-1]) / 20
                        if avg_v > 0 and vol[-1] > avg_v:
                            sl = ema200[-1] * 0.97
                            msg = (
                                f"📈 <b>UZUN VADELİ SPOT (LONG): {symbol}</b>\n"
                                f"<i>{tf.upper()} EMA200 Kırılımı</i>\n\n"
                                f"💰 <b>Giriş:</b> {akilli_fiyat(curr)}\n"
                                f"🛑 <b>SL:</b> {akilli_fiyat(sl)}\n"
                                f"🎯 <b>Hedef:</b> {akilli_fiyat(curr * 1.15)}\n\n"
                                f"📊 <b>Teyit:</b> Hacim Onaylı ve %1 üzeri kapanış.\n\n"
                                f"💡 <b>Açıklama:</b> EMA200, uzun vadeli trend çizgisidir. Fiyatın bu devasa çizgiyi hacimle yukarı kırması, ayı piyasasının sona erip boğa piyasasının başladığını gösterir."
                            )
                            if tg(C, msg):
                                son_sinyaller[cooldown_key] = time.time()
            time.sleep(CHARTIST_ARALIGI)
        except Exception as e:
            log("CHARTIST", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 5: PUMP AVCISI (5m + TRAP RADAR)
# =====================================================
def pump_loop():
    log("PUMP", "Anlık Pump Avcısı Başladı.")
    time.sleep(45)
    while True:
        try:
            tickers, src = get_futures_tickers()
            if not tickers:
                time.sleep(60)
                continue
            for t in filter_candidates(tickers, MIN_HACIM_PUMP, MAX_TARAMA):
                symbol = t["symbol"]
                if symbol in son_pump_sinyalleri and time.time() - son_pump_sinyalleri[symbol] < 900:
                    continue
                ks = get_klines(symbol, "5m", 4, source=src)
                if not isinstance(ks, list) or len(ks) < 3:
                    continue
                curr_k, prev_k = ks[-2], ks[-3]
                cc, pc = float(curr_k[4]), float(prev_k[4])
                cv, pv = float(curr_k[7]), float(prev_k[7])
                if pc <= 0 or pv <= 0:
                    continue
                price_change = ((cc - pc) / pc) * 100
                vol_change = ((cv - pv) / pv) * 100
                vol_diff = cv - pv
                if price_change >= 3.0 and vol_change >= 100.0 and vol_diff >= 50_000:
                    trap_radar = trap_radar_check(symbol, source=src)
                    radar_line = f"\n{trap_radar}\n" if trap_radar else ""
                    msg = (
                        f"🚀 <b>Pump - {symbol}</b>\n"
                        f"💰 <b>Fiyat:</b> {akilli_fiyat(cc)} (<b>{price_change:+.2f}%</b>)\n"
                        f"📊 <b>Hacim Artışı:</b> {vol_change:+.0f}%\n"
                        f"Volume increased by {vol_diff:.0f} ⬆️\n"
                        f"{radar_line}\n"
                        f"💡 <b>Açıklama:</b> 5 dakikalık grafikte hacim ve fiyat artışı tespit edildi."
                    )
                    if tg(C, msg):
                        son_pump_sinyalleri[symbol] = time.time()
                        log("PUMP", f"SİNYAL: {symbol} ({price_change:+.2f}%)")
            time.sleep(PUMP_ARALIGI)
        except Exception as e:
            log("PUMP", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 6: ANORMAL VOLATİLİTE VE DUMP AVCISI (15m)
# =====================================================
def volatilite_loop():
    log("VOLATILITE", "Anormal Volatilite ve Dump Avcısı Başladı (15m).")
    time.sleep(55)
    while True:
        try:
            tickers, src = get_futures_tickers()
            if not tickers:
                time.sleep(60)
                continue
            for t in filter_candidates(tickers, MIN_HACIM_VOLATILITE, MAX_TARAMA):
                symbol = t["symbol"]
                if symbol in son_volatilite_sinyalleri and time.time() - son_volatilite_sinyalleri[symbol] < 1800:
                    continue
                ks = get_klines(symbol, "15m", 25, source=src)
                if not isinstance(ks, list) or len(ks) < 23:
                    continue
                curr_open = float(ks[-2][1])
                curr_close = float(ks[-2][4])
                curr_vol = float(ks[-2][7])
                vols = [float(k[7]) for k in ks]
                avg_vol = sum(vols[-22:-2]) / 20
                if curr_open <= 0 or avg_vol <= 0:
                    continue
                price_change_pct = ((curr_close - curr_open) / curr_open) * 100
                vol_multiplier = curr_vol / avg_vol
                if abs(price_change_pct) >= 2.5 and vol_multiplier >= 3.0:
                    yon = "DUMP (ANI DÜŞÜŞ) 🔻" if price_change_pct < 0 else "PUMP (ANI YÜKSELİŞ) 🚀"
                    emoji = "🚨" if price_change_pct < 0 else "⚡"
                    msg = (
                        f"{emoji} <b>ANORMAL VOLATİLİTE UYARISI</b> {emoji}\n"
                        f"<b>Coin:</b> {symbol} (15m Grafik)\n\n"
                        f"📉 <b>Hareket:</b> {yon}\n"
                        f"📊 <b>Değişim:</b> <b>{price_change_pct:+.2f}%</b>\n"
                        f"📈 <b>Hacim Patlaması:</b> Ortalamanın <b>{vol_multiplier:.1f}x</b> katı\n"
                        f"💰 <b>Kapanış Fiyatı:</b> {akilli_fiyat(curr_close)}\n\n"
                        f"⚠️ <i>Piyasada ani bir likidite değişimi veya büyük bir emir gerçekleşmiş olabilir. Dikkatli olun!</i>"
                    )
                    if tg(C, msg):
                        son_volatilite_sinyalleri[symbol] = time.time()
                        log("VOLATILITE", f"🔥 SİNYAL: {symbol} ({yon})")
            time.sleep(VOLATILITE_ARALIGI)
        except Exception as e:
            log("VOLATILITE", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 7: FORMASYON (5m-15m TEYİTLİ DÖNÜŞ)
# =====================================================
def formasyon_loop():
    log("FORMASYON", "Teyitli Formasyon ve Likidite Avcısı Başladı.")
    time.sleep(60)
    while True:
        try:
            tickers, src = get_futures_tickers()
            if not tickers:
                time.sleep(60)
                continue
            for t in filter_candidates(tickers, MIN_HACIM_FORMASYON, MAX_TARAMA):
                symbol = t["symbol"]
                if symbol in son_formasyon_sinyalleri and time.time() - son_formasyon_sinyalleri[symbol] < 86400:
                    continue
                k5 = get_klines(symbol, "5m", 100, source=src)
                k15 = get_klines(symbol, "15m", 50, source=src)
                if not isinstance(k5, list) or not isinstance(k15, list) or len(k5) < 95 or len(k15) < 40:
                    continue

                highs = [float(k[2]) for k in k5[:-1]]
                lows = [float(k[3]) for k in k5[:-1]]
                closes = [float(k[4]) for k in k5[:-1]]
                vols = [float(k[7]) for k in k5[:-1]]
                c15 = [float(k[4]) for k in k15[:-1]]
                e11, e21 = calculate_ema(c15, 11), calculate_ema(c15, 21)
                if not e11 or not e21:
                    continue
                ema11, ema21 = e11[-1], e21[-1]
                rsi = calculate_rsi(c15)

                current_price = closes[-1]
                avg_vol = sum(vols[-21:-1]) / 20
                if avg_vol <= 0:
                    continue
                current_vol = vols[-1]
                is_vol_teyit = current_vol > (avg_vol * 1.5)

                p_highs, p_lows = find_pivots(highs, lows)
                if len(p_highs) < 3 or len(p_lows) < 3:
                    continue
                last_h, last_l = p_highs[-3:], p_lows[-3:]

                sinyal = None
                formasyon_adi = yon = emoji = aciklama = ""
                hedef = stop = 0.0

                if abs(last_h[0][1] - last_h[2][1]) / max(last_h[0][1], 1e-12) < 0.03 and current_price < last_l[-1][1] and is_vol_teyit:
                    if rsi < 50 and ema11 < ema21:
                        sinyal = True; formasyon_adi = "Çift Tepe (M)"; yon = "SHORT"; emoji = "🔻"
                        hedef = last_l[-1][1] - (last_h[1][1] - last_l[-1][1]); stop = last_h[2][1] * 1.02
                        aciklama = "İkinci tepe ilkini geçemedi ve boyun çizgisi aşağı kırıldı."
                elif abs(last_l[0][1] - last_l[2][1]) / max(last_l[0][1], 1e-12) < 0.03 and current_price > last_h[-1][1] and is_vol_teyit:
                    if rsi > 50 and ema11 > ema21:
                        sinyal = True; formasyon_adi = "Çift Dip (W)"; yon = "LONG"; emoji = "🚀"
                        hedef = last_h[-1][1] + (last_h[-1][1] - last_l[1][1]); stop = last_l[2][1] * 0.98
                        aciklama = "İkinci dip satıcıları tüketti ve boyun çizgisi yukarı kırıldı."
                elif last_h[1][1] > last_h[0][1] and last_h[1][1] > last_h[2][1] and abs(last_h[0][1] - last_h[2][1]) / max(last_h[0][1], 1e-12) < 0.05 and current_price < last_l[-1][1] and is_vol_teyit:
                    if rsi < 50 and ema11 < ema21:
                        sinyal = True; formasyon_adi = "Omuz-Baş-Omuz"; yon = "SHORT"; emoji = "🔻"
                        hedef = last_l[-1][1] - (last_h[1][1] - last_l[-1][1]); stop = last_h[2][1] * 1.02
                        aciklama = "Klasik dönüş formasyonu. Boyun çizgisi kırıldı, 15m trend aşağı yönlü teyit veriyor."
                elif highs[-1] > max(highs[-21:-1]) and current_price < max(highs[-21:-1]) and is_vol_teyit:
                    if rsi < 50 and ema11 < ema21:
                        sinyal = True; formasyon_adi = "Likidite Avı (Sweep)"; yon = "SHORT"; emoji = "💀"
                        stop = highs[-1] * 1.01; hedef = current_price * 0.97
                        aciklama = "Fiyat eski tepenin üzerine fitil attı ancak hemen altına kapandı. Akıllı para satışa geçmiş olabilir."
                elif lows[-1] < min(lows[-21:-1]) and current_price > min(lows[-21:-1]) and is_vol_teyit:
                    if rsi > 50 and ema11 > ema21:
                        sinyal = True; formasyon_adi = "Likidite Avı (Sweep)"; yon = "LONG"; emoji = "🚀"
                        stop = lows[-1] * 0.99; hedef = current_price * 1.03
                        aciklama = "Fiyat eski dibin altına fitil attı ancak hemen üstüne kapandı. Akıllı para alıma geçmiş olabilir."

                if not sinyal:
                    hi, lo = max(highs[-21:-1]), min(lows[-21:-1])
                    rng = ((hi - lo) / lo) * 100 if lo > 0 else 999
                    if rng < 5 and current_price > hi and is_vol_teyit and rsi > 50:
                        sinyal = True; formasyon_adi = "Sıkışma ve Yukarı Kırılım (Bayrak)"; yon = "LONG"; emoji = "🚀"
                        hedef = current_price + (hi - lo); stop = lo * 0.98
                        aciklama = "Fiyat dar bir bantta sıkıştı ve hacimle yukarı kırıldı. Volatilite patlaması başlıyor."
                    elif rng < 5 and current_price < lo and is_vol_teyit and rsi < 50:
                        sinyal = True; formasyon_adi = "Sıkışma ve Aşağı Kırılım (Ters Bayrak)"; yon = "SHORT"; emoji = "🔻"
                        hedef = current_price - (hi - lo); stop = hi * 1.02
                        aciklama = "Fiyat dar bir bantta sıkıştı ve hacimle aşağı kırıldı. Volatilite patlaması başlıyor."

                if sinyal:
                    hedef_pct = ((hedef - current_price) / current_price) * 100
                    stop_pct = ((stop - current_price) / current_price) * 100
                    if yon == "SHORT" and (hedef >= current_price or stop <= current_price):
                        continue
                    if yon == "LONG" and (hedef <= current_price or stop >= current_price):
                        continue
                    if abs(stop_pct) > 10.0 or abs(hedef_pct) < 1.0 or abs(hedef_pct) > 20.0:
                        continue
                    msg = (
                        f"{emoji} <b>TEYİTLİ DÖNÜŞ ({yon}): {symbol}</b>\n"
                        f"<i>5m Formasyon - 15m Trend Teyitli</i>\n\n"
                        f"💰 <b>Giriş:</b> {akilli_fiyat(current_price)}\n"
                        f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                        f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                        f"📊 <b>Formasyon:</b> {formasyon_adi}\n"
                        f"📊 <b>Teyitler:</b> Hacim ({current_vol/avg_vol:.1f}x), RSI ({rsi:.1f}), EMA (15m)\n\n"
                        f"💡 <b>Açıklama:</b> {aciklama}"
                    )
                    if tg(C, msg):
                        son_formasyon_sinyalleri[symbol] = time.time()
                        log("FORMASYON", f"🔥 SİNYAL: {symbol} ({yon} - {formasyon_adi})")
            time.sleep(FORMASYON_ARALIGI)
        except Exception as e:
            log("FORMASYON", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 8: ICHIMOKU BULUTU (1h)
# =====================================================
def ichimoku_loop():
    log("ICHIMOKU", "Zırhlı Ichimoku Bulut Motoru Başladı.")
    time.sleep(75)
    while True:
        try:
            tickers, src = get_futures_tickers()
            if not tickers:
                time.sleep(60)
                continue
            for t in filter_candidates(tickers, MIN_HACIM_ICHIMOKU, MAX_TARAMA):
                symbol = t["symbol"]
                if symbol in son_ichimoku_sinyalleri and time.time() - son_ichimoku_sinyalleri[symbol] < 14400:
                    continue
                ks = get_klines(symbol, "1h", 120, source=src)
                if not isinstance(ks, list) or len(ks) < 90:
                    continue
                highs = [float(k[2]) for k in ks[:-1]]
                lows = [float(k[3]) for k in ks[:-1]]
                closes = [float(k[4]) for k in ks[:-1]]
                vols = [float(k[7]) for k in ks[:-1]]
                idx = len(closes) - 1
                curr_close, prev_close = closes[idx], closes[idx - 1]
                avg_vol = sum(vols[-21:-1]) / 20
                if avg_vol <= 0 or vols[-1] <= avg_vol:
                    continue

                def mid(period, end):
                    start = end - period + 1
                    if start < 0:
                        return None
                    return (max(highs[start:end + 1]) + min(lows[start:end + 1])) / 2

                tenkan, kijun = mid(9, idx), mid(26, idx)
                a_curr = (mid(9, idx - 26) + mid(26, idx - 26)) / 2 if idx >= 77 else None
                a_prev = (mid(9, idx - 27) + mid(26, idx - 27)) / 2 if idx >= 78 else None
                b_curr = mid(52, idx - 26) if idx >= 77 else None
                b_prev = mid(52, idx - 27) if idx >= 78 else None
                if None in (tenkan, kijun, a_curr, a_prev, b_curr, b_prev):
                    continue

                top, bottom = max(a_curr, b_curr), min(a_curr, b_curr)
                ptop, pbottom = max(a_prev, b_prev), min(a_prev, b_prev)
                thickness = top - bottom
                sinyal = None
                yon = emoji = aciklama = ""
                stop = hedef = 0.0

                if prev_close <= ptop and curr_close > top and curr_close > kijun and tenkan > kijun and thickness > curr_close * 0.005:
                    sinyal = True; yon = "LONG (Bulut Üstü Kırılım)"; emoji = "🚀"
                    stop = min(kijun, bottom) * 0.99; hedef = curr_close + thickness * 2
                    aciklama = "Fiyat Ichimoku bulutunu yukarı yönlü kırdı."
                elif prev_close >= pbottom and curr_close < bottom and curr_close < kijun and tenkan < kijun and thickness > curr_close * 0.005:
                    sinyal = True; yon = "SHORT (Bulut Altı Kırılım)"; emoji = "🔻"
                    stop = max(kijun, top) * 1.01; hedef = curr_close - thickness * 2
                    aciklama = "Fiyat Ichimoku bulutunu aşağı yönlü kırdı."

                if sinyal:
                    hedef_pct = ((hedef - curr_close) / curr_close) * 100
                    stop_pct = ((stop - curr_close) / curr_close) * 100
                    if abs(hedef_pct) < 1.0:
                        continue
                    msg = (
                        f"{emoji} <b>ICHIMOKU SİNYALİ ({yon}): {symbol}</b>\n"
                        f"<i>1 Saatlik Grafik - Ichimoku Bulut Kırılımı</i>\n\n"
                        f"💰 <b>Kapanış Fiyatı:</b> {akilli_fiyat(curr_close)}\n"
                        f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                        f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                        f"☁️ <b>Bulut (Kumo) Bölgesi:</b> {akilli_fiyat(bottom)} - {akilli_fiyat(top)}\n"
                        f"📈 <b>Tenkan-sen:</b> {akilli_fiyat(tenkan)} | <b>Kijun-sen:</b> {akilli_fiyat(kijun)}\n"
                        f"📊 <b>Hacim Teyidi:</b> Alındı\n\n"
                        f"💡 <b>Açıklama:</b> {aciklama}"
                    )
                    if tg(C, msg):
                        son_ichimoku_sinyalleri[symbol] = time.time()
                        log("ICHIMOKU", f"🔥 SİNYAL: {symbol} ({yon})")
            time.sleep(ICHIMOKU_ARALIGI)
        except Exception as e:
            log("ICHIMOKU", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 9: EMA RIBBON (1d)
# =====================================================
def ema_ribbon_loop():
    log("EMA_RIBBON", "EMA Dizilim Motoru Başladı.")
    time.sleep(90)
    while True:
        try:
            tickers, src = get_futures_tickers()
            if not tickers:
                time.sleep(60)
                continue
            for t in filter_candidates(tickers, MIN_HACIM_EMA_RIBBON, MAX_TARAMA):
                symbol = t["symbol"]
                if symbol in son_ema_ribbon_sinyalleri and time.time() - son_ema_ribbon_sinyalleri[symbol] < 86400:
                    continue
                ks = get_klines(symbol, "1d", 230, source=src)
                if not isinstance(ks, list) or len(ks) < 205:
                    continue
                closes = [float(k[4]) for k in ks[:-1]]
                vols = [float(k[7]) for k in ks[:-1]]
                curr, curr_vol = closes[-1], vols[-1]
                avg_vol = sum(vols[-21:-1]) / 20
                if avg_vol <= 0 or curr_vol <= (avg_vol * 1.2):
                    continue
                e7_l, e25_l = calculate_ema(closes, 7), calculate_ema(closes, 25)
                e99_l, e200_l = calculate_ema(closes, 99), calculate_ema(closes, 200)
                if not (e7_l and e25_l and e99_l and e200_l):
                    continue
                e7, e25, e99, e200 = e7_l[-1], e25_l[-1], e99_l[-1], e200_l[-1]
                if curr > e7 > e25 > e99 > e200:
                    msg = (
                        f"🚀 <b>EMA DİZİLİMİ (LONG): {symbol}</b>\n"
                        f"<i>1 Günlük Grafik - Çoklu EMA Dizilimi</i>\n\n"
                        f"💰 <b>Giriş:</b> {akilli_fiyat(curr)}\n"
                        f"🎯 <b>Hedef:</b> {akilli_fiyat(curr * 1.20)} (+20.0%)\n"
                        f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(e25 * 0.95)}\n\n"
                        f"📊 <b>EMA Dizilimi:</b>\n"
                        f"EMA7: {akilli_fiyat(e7)} | EMA25: {akilli_fiyat(e25)}\n"
                        f"EMA99: {akilli_fiyat(e99)} | EMA200: {akilli_fiyat(e200)}\n"
                        f"📈 <b>Hacim Teyidi:</b> Alındı ({curr_vol/avg_vol:.1f}x)\n\n"
                        f"💡 <b>Açıklama:</b> EMA Ribbon dizilimi, piyasanın tüm zaman dilimlerinde mükemmel bir uyum içinde olduğunu gösterir."
                    )
                    if tg(C, msg):
                        son_ema_ribbon_sinyalleri[symbol] = time.time()
                        log("EMA_RIBBON", f"SİNYAL: {symbol}")
            time.sleep(EMA_RIBBON_ARALIGI)
        except Exception as e:
            log("EMA_RIBBON", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 10: MACD (1d)
# =====================================================
def macd_loop():
    log("MACD", "MACD Motoru Başladı.")
    time.sleep(105)
    while True:
        try:
            tickers, src = get_futures_tickers()
            if not tickers:
                time.sleep(60)
                continue
            for t in filter_candidates(tickers, MIN_HACIM_MACD, MAX_TARAMA):
                symbol = t["symbol"]
                if symbol in son_macd_sinyalleri and time.time() - son_macd_sinyalleri[symbol] < 14400:
                    continue
                ks = get_klines(symbol, "1d", 110, source=src)
                if not isinstance(ks, list) or len(ks) < 80:
                    continue
                closes = [float(k[4]) for k in ks[:-1]]
                vols = [float(k[7]) for k in ks[:-1]]
                curr_price, curr_vol = closes[-1], vols[-1]
                avg_vol = sum(vols[-21:-1]) / 20
                if avg_vol <= 0 or curr_vol <= (avg_vol * 1.1):
                    continue
                cm, cs, pm, ps = calculate_macd(closes)
                if cm is None:
                    continue
                if pm <= ps and cm > cs:
                    msg = (
                        f"🚀 <b>MACD SİNYALİ (LONG): {symbol}</b>\n"
                        f"<i>1 Günlük Grafik - MACD Momentum Kırılımı</i>\n\n"
                        f"💰 <b>Giriş:</b> {akilli_fiyat(curr_price)}\n"
                        f"🎯 <b>Hedef:</b> {akilli_fiyat(curr_price * 1.10)} (+10.0%)\n"
                        f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(curr_price * 0.97)} (-3.0%)\n\n"
                        f"📊 <b>MACD Çizgisi:</b> {cm:.5f} | <b>Sinyal Çizgisi:</b> {cs:.5f}\n"
                        f"📈 <b>Hacim Teyidi:</b> Alındı\n\n"
                        f"💡 <b>Açıklama:</b> MACD çizgisinin Sinyal çizgisini yukarı yönlü kesmesine 'Golden Cross' denir."
                    )
                    if tg(C, msg):
                        son_macd_sinyalleri[symbol] = time.time()
                        log("MACD", f"SİNYAL: {symbol}")
            time.sleep(MACD_ARALIGI)
        except Exception as e:
            log("MACD", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 11: 200 GÜNLÜK SMA KIRILIMI (1d LONG/SHORT)
# =====================================================
def sma200_loop():
    log("SMA200", "200 Günlük SMA Kırılım Motoru Başladı.")
    time.sleep(120)
    while True:
        try:
            tickers, src = get_futures_tickers()
            if not tickers:
                time.sleep(60)
                continue
            kaynak_ad = "Binance Futures" if src == "FAPI" else f"Futures ({src})" if src in ("BYBIT", "GATE") else "Binance Spot Yedek"
            for t in filter_candidates(tickers, MIN_HACIM_SMA200, MAX_TARAMA):
                symbol = t["symbol"]
                ks = get_klines(symbol, "1d", 230, source=src)
                if not isinstance(ks, list) or len(ks) < 203:
                    continue
                closes = [float(k[4]) for k in ks[:-1]]
                if len(closes) < 201:
                    continue
                curr_close, prev_close = closes[-1], closes[-2]
                sma_curr = sum(closes[-200:]) / 200.0
                sma_prev = sum(closes[-201:-1]) / 200.0
                if sma_curr <= 0:
                    continue

                yon = None
                if prev_close <= sma_prev and curr_close > sma_curr:
                    yon = "LONG"
                elif prev_close >= sma_prev and curr_close < sma_curr:
                    yon = "SHORT"

                if not yon:
                    continue
                ckey = f"{yon}_{symbol}"
                if ckey in son_sma200_sinyalleri and time.time() - son_sma200_sinyalleri[ckey] < 86400:
                    continue

                fark_pct = ((curr_close - sma_curr) / sma_curr) * 100.0
                emoji = "🚀" if yon == "LONG" else "🔻"
                aciklama = (
                    "Günlük kapanış 200 günlük basit hareketli ortalamanın üzerine çıktı."
                    if yon == "LONG"
                    else "Günlük kapanış 200 günlük basit hareketli ortalamanın altına indi."
                )
                msg = (
                    f"{emoji} <b>200 GÜNLÜK SMA KIRILIMI ({yon}): {symbol}</b>\n"
                    f"<i>{kaynak_ad} - 1 Günlük Grafik</i>\n\n"
                    f"💰 <b>Günlük Kapanış:</b> {akilli_fiyat(curr_close)}\n"
                    f"📉 <b>Önceki Kapanış:</b> {akilli_fiyat(prev_close)}\n"
                    f"📊 <b>Önceki 200 SMA:</b> {akilli_fiyat(sma_prev)}\n"
                    f"📊 <b>Güncel 200 SMA:</b> {akilli_fiyat(sma_curr)}\n"
                    f"📈 <b>Ortalamaya Göre Fark:</b> {fark_pct:+.2f}%\n\n"
                    f"💡 <b>Açıklama:</b> {aciklama}\n"
                    f"ℹ️ <i>Sinyal yalnızca kapanmış günlük mumla oluşturulmuştur.</i>"
                )
                if tg(C, msg):
                    son_sma200_sinyalleri[ckey] = time.time()
                    log("SMA200", f"SİNYAL: {symbol} ({yon})")
            time.sleep(SMA200_ARALIGI)
        except Exception as e:
            log("SMA200", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 12: TARİHİ ZİRVE / DİP KIRILIMI (1d Kapanış)
# =====================================================
def ath_atl_loop():
    log("ATH_ATL", "Tarihi Zirve/Dip Kırılım Motoru Başladı.")
    time.sleep(135)
    while True:
        try:
            tickers, src = get_futures_tickers()
            if not tickers:
                time.sleep(60)
                continue
            for t in filter_candidates(tickers, MIN_HACIM_ATH_ATL, MAX_TARAMA):
                symbol = t["symbol"]
                if symbol in son_ath_atl_sinyalleri and time.time() - son_ath_atl_sinyalleri[symbol] < 86400:
                    continue
                ks = get_klines(symbol, "1d", 500, source=src)
                if not isinstance(ks, list) or len(ks) < 200:
                    continue
                highs = [float(k[2]) for k in ks[:-1]]
                lows = [float(k[3]) for k in ks[:-1]]
                closes = [float(k[4]) for k in ks[:-1]]
                vols = [float(k[7]) for k in ks[:-1]]
                current_price, prev_close, current_vol = closes[-1], closes[-2], vols[-1]
                prior_ath, prior_atl = max(highs[:-1]), min(lows[:-1])
                avg_vol = sum(vols[-21:-1]) / 20
                if avg_vol <= 0 or current_vol <= (avg_vol * 1.2):
                    continue

                sinyal = None
                yon = emoji = aciklama = ""
                stop = hedef = 0.0
                if prev_close >= prior_atl and current_price < prior_atl:
                    sinyal = True; yon = "SHORT"; emoji = "💀"
                    stop = prior_atl * 1.05; hedef = current_price * 0.80
                    aciklama = "Fiyat, geçmişteki en düşük seviyeyi (Tarihi Dibi) hacimle aşağı kırdı."
                elif prev_close <= prior_ath and current_price > prior_ath:
                    sinyal = True; yon = "LONG"; emoji = "🚀"
                    stop = prior_ath * 0.95; hedef = current_price * 1.20
                    aciklama = "Fiyat, geçmişteki en yüksek seviyeyi (Tarihi Zirveyi) hacimle yukarı kırdı."

                if sinyal:
                    hedef_pct = ((hedef - current_price) / current_price) * 100
                    stop_pct = ((stop - current_price) / current_price) * 100
                    msg = (
                        f"{emoji} <b>TARİHİ SEVİYE KIRILIMI ({yon}): {symbol}</b>\n"
                        f"<i>1 Günlük Grafik</i>\n\n"
                        f"💰 <b>Giriş:</b> {akilli_fiyat(current_price)}\n"
                        f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                        f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                        f"📊 <b>Kırılan Seviye:</b> {akilli_fiyat(prior_ath if yon == 'LONG' else prior_atl)}\n"
                        f"📈 <b>Hacim:</b> {current_vol/avg_vol:.1f}x\n\n"
                        f"💡 <b>Açıklama:</b> {aciklama}"
                    )
                    if tg(C, msg):
                        son_ath_atl_sinyalleri[symbol] = time.time()
                        log("ATH_ATL", f"🔥 SİNYAL: {symbol} ({yon})")
            time.sleep(ATH_ATL_ARALIGI)
        except Exception as e:
            log("ATH_ATL", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 13: GRAFİK FORMASYONLARI (4h / 1d)
# =====================================================
def chart_patterns_loop():
    log("PATTERN", "Grafik Formasyon Avcısı Başladı (İkili Tepe/Dip, OBO).")
    time.sleep(150)
    while True:
        try:
            tickers, src = get_futures_tickers()
            if not tickers:
                time.sleep(60)
                continue
            for t in filter_candidates(tickers, MIN_HACIM_PATTERN, MAX_TARAMA):
                symbol = t["symbol"]
                if symbol in son_chart_pattern_sinyalleri and time.time() - son_chart_pattern_sinyalleri[symbol] < 14400:
                    continue
                for tf in ["4h", "1d"]:
                    ks = get_klines(symbol, tf, 100, source=src)
                    if not isinstance(ks, list) or len(ks) < 50:
                        continue
                    highs = [float(k[2]) for k in ks[:-1]]
                    lows = [float(k[3]) for k in ks[:-1]]
                    closes = [float(k[4]) for k in ks[:-1]]
                    vols = [float(k[7]) for k in ks[:-1]]
                    curr_close, curr_vol = closes[-1], vols[-1]
                    avg_vol = sum(vols[-21:-1]) / 20
                    if avg_vol <= 0 or curr_vol < avg_vol * 1.2:
                        continue
                    p_highs, p_lows = find_pivots(highs, lows)
                    sinyal = None
                    formasyon_adi = yon = emoji = ""
                    hedef = stop = neckline = 0.0

                    if len(p_highs) >= 2:
                        i1, p1 = p_highs[-2]
                        i2, p2 = p_highs[-1]
                        if 5 <= (i2 - i1) <= 30 and abs(p1 - p2) / max(p1, 1e-12) <= 0.02:
                            neckline = min(lows[i1:i2 + 1])
                            if curr_close < neckline * 0.99:
                                sinyal = True; formasyon_adi = "İkili Tepe (M)"; yon = "SHORT"; emoji = "🔻"
                                stop = max(p1, p2) * 1.02; hedef = neckline - (max(p1, p2) - neckline)

                    if not sinyal and len(p_lows) >= 2:
                        i1, p1 = p_lows[-2]
                        i2, p2 = p_lows[-1]
                        if 5 <= (i2 - i1) <= 30 and abs(p1 - p2) / max(p1, 1e-12) <= 0.02:
                            neckline = max(highs[i1:i2 + 1])
                            if curr_close > neckline * 1.01:
                                sinyal = True; formasyon_adi = "İkili Dip (W)"; yon = "LONG"; emoji = "🚀"
                                stop = min(p1, p2) * 0.98; hedef = neckline + (neckline - min(p1, p2))

                    if not sinyal and len(p_highs) >= 3:
                        (i1, p1), (i2, p2), (i3, p3) = p_highs[-3:]
                        if p2 > p1 and p2 > p3 and abs(p1 - p3) / max(p1, 1e-12) <= 0.03:
                            neckline = min(lows[i1:i3 + 1])
                            if curr_close < neckline * 0.99:
                                sinyal = True; formasyon_adi = "Omuz-Baş-Omuz"; yon = "SHORT"; emoji = "💀"
                                stop = p3 * 1.02; hedef = neckline - (p2 - neckline)

                    if not sinyal and len(p_lows) >= 3:
                        (i1, p1), (i2, p2), (i3, p3) = p_lows[-3:]
                        if p2 < p1 and p2 < p3 and abs(p1 - p3) / max(p1, 1e-12) <= 0.03:
                            neckline = max(highs[i1:i3 + 1])
                            if curr_close > neckline * 1.01:
                                sinyal = True; formasyon_adi = "Ters Omuz-Baş-Omuz"; yon = "LONG"; emoji = "🚀"
                                stop = p3 * 0.98; hedef = neckline + (neckline - p2)

                    if sinyal:
                        hedef_pct = ((hedef - curr_close) / curr_close) * 100
                        stop_pct = ((stop - curr_close) / curr_close) * 100
                        if yon == "SHORT" and (hedef >= curr_close or stop <= curr_close):
                            continue
                        if yon == "LONG" and (hedef <= curr_close or stop >= curr_close):
                            continue
                        if abs(stop_pct) > 15.0 or abs(hedef_pct) < 1.5 or abs(hedef_pct) > 40.0:
                            continue
                        msg = (
                            f"{emoji} <b>GRAFİK FORMASYONU ({yon}): {symbol}</b>\n"
                            f"<i>{tf.upper()} Grafik - {formasyon_adi}</i>\n\n"
                            f"💰 <b>Giriş:</b> {akilli_fiyat(curr_close)}\n"
                            f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                            f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                            f"📊 <b>Boyun Çizgisi:</b> {akilli_fiyat(neckline)}\n"
                            f"📈 <b>Hacim Teyidi:</b> Alındı ({curr_vol/avg_vol:.1f}x)\n\n"
                            f"💡 <b>Açıklama:</b> Fiyat klasik dönüş formasyonunu tamamlayarak boyun çizgisini hacimle kırdı."
                        )
                        if tg(C, msg):
                            son_chart_pattern_sinyalleri[symbol] = time.time()
                            log("PATTERN", f"🔥 SİNYAL: {symbol} ({yon} - {formasyon_adi})")
                            break
            time.sleep(CHART_PATTERN_ARALIGI)
        except Exception as e:
            log("PATTERN", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 14: ANLIK ALL-TIME HIGH / LOW (Yeni Coinler + PNG Grafik)
# =====================================================
def anlik_ath_atl_loop():
    log("ANLIK_ATH_ATL", "Anlık Tarihi Zirve/Dip + Grafik Motoru Başladı.")
    time.sleep(160)
    while True:
        try:
            tickers, src = get_futures_tickers()
            if not tickers:
                time.sleep(60)
                continue
            for t in filter_candidates(tickers, MIN_HACIM_ANLIK_ATH_ATL, MAX_TARAMA):
                symbol = t["symbol"]
                curr_price = t["lastPrice"]
                if curr_price <= 0:
                    continue
                ks = get_klines(symbol, "1d", 500, source=src)
                if not isinstance(ks, list) or len(ks) < 2:
                    continue

                prior_highs = [float(k[2]) for k in ks[:-1]]
                prior_lows = [float(k[3]) for k in ks[:-1]]
                prior_ath = max(prior_highs)
                prior_atl = min(prior_lows)
                today_high = float(ks[-1][2])
                today_low = float(ks[-1][3])

                tip = None
                if curr_price < prior_atl and curr_price <= today_low * 1.003:
                    tip = "ATL"
                elif curr_price > prior_ath and curr_price >= today_high * 0.997:
                    tip = "ATH"

                if not tip:
                    continue
                ckey = f"{tip}_{symbol}"
                if ckey in son_anlik_ath_atl_sinyalleri and time.time() - son_anlik_ath_atl_sinyalleri[ckey] < 86400:
                    continue

                chg = t["priceChangePercent"]
                vol_txt = format_hacim(t["quoteVolume"])
                if tip == "ATL":
                    caption = (
                        f"#{symbol} ${akilli_fiyat(curr_price)} <b>all time low</b> 👎🚨😭\n"
                        f"24s <b>{chg:+.2f}%</b> | Hacim <b>{vol_txt}</b>\n"
                        f"📉 Önceki Tarihi Dip: {akilli_fiyat(prior_atl)} ({len(prior_lows)} günlük geçmiş)"
                    )
                else:
                    caption = (
                        f"#{symbol} ${akilli_fiyat(curr_price)} <b>all time high</b> 🚀🔥🏆\n"
                        f"24s <b>{chg:+.2f}%</b> | Hacim <b>{vol_txt}</b>\n"
                        f"📈 Önceki Tarihi Zirve: {akilli_fiyat(prior_ath)} ({len(prior_highs)} günlük geçmiş)"
                    )

                png = render_chart_png(symbol, ks, f"{symbol} 1D {tip}")
                if tg_photo(C, png, caption):
                    son_anlik_ath_atl_sinyalleri[ckey] = time.time()
                    log("ANLIK_ATH_ATL", f"Grafikli {tip} sinyali gönderildi: {symbol}")
            time.sleep(ANLIK_ATH_ATL_ARALIGI)
        except Exception as e:
            log("ANLIK_ATH_ATL", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 15: SON 1 SAATİN HAREKETLİLERİ (TOP MOVERS)
# =====================================================
def top_movers_loop():
    log("TOP_MOVERS", "Son 1 Saatin Hareketlileri Motoru Başladı.")
    time.sleep(170)
    while True:
        try:
            tickers, src = get_futures_tickers()
            if not tickers:
                time.sleep(120)
                continue
            results = []
            for t in filter_candidates(tickers, MIN_HACIM_MOVERS, MAX_TARAMA):
                ks = get_klines(t["symbol"], "1h", 3, source=src)
                if not isinstance(ks, list) or len(ks) < 2:
                    continue
                last_closed = ks[-2]
                op, cl = float(last_closed[1]), float(last_closed[4])
                if op > 0:
                    results.append({
                        "symbol": t["symbol"].replace("USDT", ""),
                        "change": ((cl - op) / op) * 100.0,
                        "price": cl
                    })
            if results:
                results.sort(key=lambda x: x["change"], reverse=True)
                up = results[:3]
                down = list(reversed(results[-3:]))
                msg = "⏱️ <b>Son 1 Saatin Hareketlileri</b>\n\n🟢 <b>En Çok Yükselenler:</b>\n"
                for i, x in enumerate(up, 1):
                    msg += f"{i}. <b>{x['symbol']}</b> {x['change']:+.2f}% | {akilli_fiyat(x['price'])}\n"
                msg += "\n🔴 <b>En Çok Düşenler:</b>\n"
                for i, x in enumerate(down, 1):
                    msg += f"{i}. <b>{x['symbol']}</b> {x['change']:+.2f}% | {akilli_fiyat(x['price'])}\n"
                if tg(C, msg):
                    log("TOP_MOVERS", f"Saatlik rapor gönderildi ({src}).")
            time.sleep(TOP_MOVERS_ARALIGI)
        except Exception as e:
            log("TOP_MOVERS", f"Hata: {e}")
            time.sleep(120)


def startup_health_check():
    global _telegram_status
    if not T:
        _telegram_status = "HATA: BOT_TOKEN boş"
        log("UYARI", "BOT_TOKEN Render Environment Variables içinde tanımlı değil!")
    else:
        try:
            req = urllib.request.Request(f"https://api.telegram.org/bot{T}/getMe", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                info = json.loads(r.read().decode("utf-8"))
                uname = info.get("result", {}).get("username", "ok")
                _telegram_status = f"OK (@{uname})"
                log("TELEGRAM", f"Bağlantı başarılı: @{uname}")
        except Exception as e:
            _telegram_status = f"Hata: {e}"
            log("TELEGRAM", f"Bağlantı kontrol hatası: {e}")

    rows, src = get_futures_tickers(force=True)
    log("KONTROL", f"Aktif veri kaynağı: {src} | Coin sayısı: {len(rows)} | FAPI: {_source_status['FAPI']}")


class Handler(http.server.BaseHTTPRequestHandler):
    def _status_bytes(self):
        lines = [
            "Ultimate Futures Bot - Canli Durum (15 Motor)",
            f"Saat (TR): {simdi().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Telegram: {_telegram_status}",
            f"Aktif Futures Kaynagi: {_active_futures_source}",
            f"Binance FAPI: {_source_status['FAPI']} (kalan ban: {_source_remaining('FAPI')}sn)",
            f"Bybit Futures: {_source_status['BYBIT']}",
            f"Gate Futures: {_source_status['GATE']}",
            f"Binance Vision (Spot): {_source_status['DATA_API']}",
            f"CoinGecko (Dominans): {_source_status['COINGECKO']}",
            f"Son TOP_GAINERS: {_last_top_gainers}",
            f"Son DOMINANS: {_last_dominans}",
        ]
        return ("\n".join(lines) + "\n").encode("utf-8")

    def do_GET(self):
        body = self._status_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        body = self._status_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

    def log_message(self, format_string, *args):
        return


def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    log("SİSTEM", f"WEB SUNUCUSU BAŞLADI - PORT: {port}")
    server.serve_forever()


if __name__ == "__main__":
    threading.Thread(target=startup_health_check, daemon=True).start()
    threading.Thread(target=top_gainers_loop, daemon=True).start()
    threading.Thread(target=pump_winner_loop, daemon=True).start()
    threading.Thread(target=dominans_loop, daemon=True).start()
    threading.Thread(target=chartist_loop, daemon=True).start()
    threading.Thread(target=pump_loop, daemon=True).start()
    threading.Thread(target=volatilite_loop, daemon=True).start()
    threading.Thread(target=formasyon_loop, daemon=True).start()
    threading.Thread(target=ichimoku_loop, daemon=True).start()
    threading.Thread(target=ema_ribbon_loop, daemon=True).start()
    threading.Thread(target=macd_loop, daemon=True).start()
    threading.Thread(target=sma200_loop, daemon=True).start()
    threading.Thread(target=ath_atl_loop, daemon=True).start()
    threading.Thread(target=chart_patterns_loop, daemon=True).start()
    threading.Thread(target=anlik_ath_atl_loop, daemon=True).start()
    threading.Thread(target=top_movers_loop, daemon=True).start()
    run_web_server()

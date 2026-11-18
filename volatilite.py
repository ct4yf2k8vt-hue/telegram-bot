import urllib.request
import urllib.parse
import urllib.error
import json
import time
import socket
import os
import threading
import http.server
import math
import zlib
import struct
import uuid
import traceback
from datetime import datetime, timedelta, timezone

# =====================================================
# AYARLAR VE API KAYNAKLARI
# =====================================================

socket.setdefaulttimeout(15)

T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()

TR_Saati = timezone(timedelta(hours=3))

FAPI = "https://fapi.binance.com"
BASE = "https://api.binance.com"
DATA_API = "https://data-api.binance.vision"
BYBIT = "https://api.bybit.com"
GATE = "https://api.gateio.ws/api/v4"
COINGECKO = "https://api.coingecko.com/api/v3"

MAX_TARAMA = 60

# MOTOR ÇALIŞMA SÜRELERİ (SANİYE)
TOP_GAINERS_ARALIGI = 14400    # 4 Saat
PUMP_WINNER_ARALIGI = 21600    # 6 Saat
DOMINANS_ARALIGI = 14400       # 4 Saat
CHARTIST_ARALIGI = 3600        # 1 Saat
PUMP_ARALIGI = 60              # 1 Dakika
VOLATILITE_ARALIGI = 60        # 1 Dakika
FORMASYON_ARALIGI = 3600       # 1 Saat
ICHIMOKU_ARALIGI = 3600        # 1 Saat
EMA_RIBBON_ARALIGI = 3600      # 1 Saat
MACD_ARALIGI = 3600            # 1 Saat
SMA200_ARALIGI = 3600          # 1 Saat
ATH_ATL_ARALIGI = 3600         # 1 Saat
CHART_PATTERN_ARALIGI = 1800   # 30 Dakika
ANLIK_ATH_ATL_ARALIGI = 180    # 3 Dakika
TOP_MOVERS_ARALIGI = 3600      # 1 Saat

# MİNİMUM HACİM FİLTRELERİ (USDT)
MIN_HACIM_TOP_GAINERS = 5_000_000
MIN_HACIM_PUMP_WINNER = 10_000_000
MIN_HACIM_CHARTIST = 10_000_000
MIN_HACIM_PUMP = 5_000_000
MIN_HACIM_VOLATILITE = 10_000_000
MIN_HACIM_FORMASYON = 15_000_000
MIN_HACIM_ICHIMOKU = 10_000_000
MIN_HACIM_EMA_RIBBON = 10_000_000
MIN_HACIM_MACD = 10_000_000
MIN_HACIM_SMA200 = 5_000_000
MIN_HACIM_ATH_ATL = 5_000_000
MIN_HACIM_PATTERN = 15_000_000
MIN_HACIM_ANLIK_ATH_ATL = 3_000_000
MIN_HACIM_TOP_MOVERS = 5_000_000

# YASAKLI VARLIKLAR (HİSSE SENETLERİ, ETF'LER VE EMTİALAR)
YASAKLI_HISSELER = {
    "AAPLUSDT", "TSLAUSDT", "NVDAUSDT", "COINUSDT", "MSTRUSDT",
    "SQQQUSDT", "TQQQUSDT", "SPYUSDT", "QQQUSDT", "AMZNUSDT",
    "GOOGLUSDT", "METAUSDT", "NFLXUSDT", "BABAUSDT", "MSFTUSDT",
    "HOODUSDT", "CRCLUSDT", "XAUUSDT", "XAGUSDT", "PAXGUSDT",
    "SOXLUSDT", "SOXLBUSDT", "SOXSUSDT", "SOXSBUSDT",
    "SNXXUSDT", "SNXXBUSDT", "INTCUSDT", "INTCBUSDT", "CRDOUSDT",
}

# SİNYAL HAFIZALARI
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
son_anlik_ath_atl_sinyalleri = {}

# DURUM VE ÖNBELLEK DEĞİŞKENLERİ
_lock = threading.RLock()
_source_backoff_until = {}
_source_last_error = {}
_last_top_gainers_ts = 0
_last_dominans_ts = 0
_ticker_cache = {}   # key -> (ts, data, src)
_kline_cache = {}    # key -> (ts, data, src)
CACHE_TTL_TICKER = 45
CACHE_TTL_KLINE = 45


# =====================================================
# YARDIMCI VE AĞ FONKSİYONLARI
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


def format_vol(v):
    v = float(v)
    if v >= 1_000_000_000:
        return f"{v/1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"{v/1_000_000:.0f}M"
    if v >= 1_000:
        return f"{v/1_000:.0f}K"
    return f"{v:.0f}"


def _bar_open_ms(kline_row):
    ts = int(float(kline_row[0]))
    return ts if ts > 10_000_000_000 else ts * 1000


def _bar_fresh(kline_row, interval_ms, max_age_ms):
    now_ms = int(time.time() * 1000)
    close_ms = _bar_open_ms(kline_row) + interval_ms
    age = now_ms - close_ms
    return 0 <= age <= max_age_ms


def _source_blocked(src):
    with _lock:
        return time.time() < _source_backoff_until.get(src, 0)


def _mark_source_error(src, err_msg, cooldown_sec=60):
    with _lock:
        _source_backoff_until[src] = time.time() + cooldown_sec
        _source_last_error[src] = f"{simdi().strftime('%H:%M:%S')} | {err_msg}"


def _mark_source_ok(src):
    with _lock:
        _source_backoff_until[src] = 0
        _source_last_error[src] = "OK"


def gj(url, src="NET", timeout=15):
    if _source_blocked(src):
        return None
    headers = {"User-Agent": "Mozilla/5.0 (compatible; VolatilitySignalBot/1.0)"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            _mark_source_ok(src)
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        code = e.code
        retry_after = 0
        try:
            ra = e.headers.get("Retry-After")
            if ra:
                retry_after = int(float(ra))
        except Exception:
            retry_after = 0
        if code in (418, 429):
            cd = max(retry_after, 600 if code == 418 else 180)
        elif code == 451:
            cd = 3600
        elif code in (403, 500, 502, 503, 504):
            cd = 120
        else:
            cd = 60
        _mark_source_error(src, f"HTTP {code} (bekleme {cd}s)", cd)
        log("NET", f"{src} HTTP {code} -> {cd}s bekleme ({url.split('?')[0]})")
        return None
    except Exception as e:
        _mark_source_error(src, str(e)[:80], 45)
        log("NET", f"{src} bağlantı hatası: {e}")
        return None


def tg(chat_id, message):
    if not T or not chat_id:
        log("TG", "BOT_TOKEN veya CHAT_ID eksik!")
        return False
    try:
        data = urllib.parse.urlencode({
            "chat_id": str(chat_id),
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        req = urllib.request.Request(f"https://api.telegram.org/bot{T}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = json.loads(resp.read().decode("utf-8")).get("ok", False)
            if not ok:
                log("TG", "Telegram ok=False döndü")
            return ok
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="ignore")[:200]
        except Exception:
            pass
        log("TG", f"Telegram HTTP {e.code}: {body}")
        return False
    except Exception as e:
        log("TG", f"Telegram gönderim hatası: {e}")
        return False


def tg_photo(chat_id, png_bytes, caption):
    if not T or not chat_id:
        return False
    if not png_bytes:
        return tg(chat_id, caption)
    boundary = uuid.uuid4().hex
    parts = []

    def add_field(name, value):
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        parts.append(str(value).encode("utf-8"))
        parts.append(b"\r\n")

    add_field("chat_id", chat_id)
    add_field("caption", caption)
    add_field("parse_mode", "HTML")

    parts.append(f"--{boundary}\r\n".encode("utf-8"))
    parts.append(b'Content-Disposition: form-data; name="photo"; filename="chart.png"\r\n')
    parts.append(b"Content-Type: image/png\r\n\r\n")
    parts.append(png_bytes)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))

    body = b"".join(parts)
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{T}/sendPhoto",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            ok = json.loads(resp.read().decode("utf-8")).get("ok", False)
            if ok:
                return True
    except Exception as e:
        log("TG_PHOTO", f"Fotoğraf hatası (metin gönderilecek): {e}")
    return tg(chat_id, caption)


# =====================================================
# VERİ ÇEKİCİLER (ÇOKLU KAYNAK VE ÖNBELLEK)
# =====================================================

def get_futures_tickers():
    now = time.time()
    with _lock:
        c = _ticker_cache.get("FUTURES")
        if c and (now - c[0]) < CACHE_TTL_TICKER and not _source_blocked(c[2]):
            return c[1], c[2]

    # 1. Binance Futures
    data = gj(f"{FAPI}/fapi/v1/ticker/24hr", "FAPI")
    if isinstance(data, list) and len(data) > 0:
        out = [{
            "symbol": t["symbol"],
            "priceChangePercent": float(t.get("priceChangePercent", 0)),
            "lastPrice": float(t.get("lastPrice", 0)),
            "quoteVolume": float(t.get("quoteVolume", 0)),
        } for t in data if "symbol" in t]
        with _lock:
            _ticker_cache["FUTURES"] = (now, out, "Binance Futures")
        return out, "Binance Futures"

    # 2. Bybit Linear Futures
    b = gj(f"{BYBIT}/v5/market/tickers?category=linear", "BYBIT")
    if isinstance(b, dict) and b.get("retCode") == 0:
        lst = b.get("result", {}).get("list", [])
        out = []
        for t in lst:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            try:
                pct = float(t.get("price24hPcnt", 0)) * 100.0
                lp = float(t.get("lastPrice", 0))
                qv = float(t.get("turnover24h", 0))
                out.append({
                    "symbol": sym,
                    "priceChangePercent": pct,
                    "lastPrice": lp,
                    "quoteVolume": qv,
                })
            except Exception:
                continue
        if out:
            with _lock:
                _ticker_cache["FUTURES"] = (now, out, "Futures (BYBIT)")
            return out, "Futures (BYBIT)"

    # 3. Gate.io Futures
    g = gj(f"{GATE}/futures/usdt/tickers", "GATE")
    if isinstance(g, list) and len(g) > 0:
        out = []
        for t in g:
            c_name = t.get("contract", "")
            if not c_name.endswith("_USDT"):
                continue
            try:
                sym = c_name.replace("_", "")
                out.append({
                    "symbol": sym,
                    "priceChangePercent": float(t.get("change_percentage", 0)),
                    "lastPrice": float(t.get("last", 0)),
                    "quoteVolume": float(t.get("volume_24h_quote", 0)),
                })
            except Exception:
                continue
        if out:
            with _lock:
                _ticker_cache["FUTURES"] = (now, out, "Futures (GATE)")
            return out, "Futures (GATE)"

    # 4. Son çare Binance Spot Data-API
    d = gj(f"{DATA_API}/api/v3/ticker/24hr", "DATA_API")
    if isinstance(d, list) and len(d) > 0:
        out = [{
            "symbol": t["symbol"],
            "priceChangePercent": float(t.get("priceChangePercent", 0)),
            "lastPrice": float(t.get("lastPrice", 0)),
            "quoteVolume": float(t.get("quoteVolume", 0)),
        } for t in d if "symbol" in t]
        with _lock:
            _ticker_cache["FUTURES"] = (now, out, "Spot Yedek (Binance)")
        return out, "Spot Yedek (Binance)"

    return None, "NONE"


def get_spot_tickers():
    now = time.time()
    with _lock:
        c = _ticker_cache.get("SPOT")
        if c and (now - c[0]) < CACHE_TTL_TICKER and not _source_blocked(c[2]):
            return c[1], c[2]

    for src, url, label in [
        ("DATA_API", f"{DATA_API}/api/v3/ticker/24hr", "Binance Spot"),
        ("BASE", f"{BASE}/api/v3/ticker/24hr", "Binance Spot"),
    ]:
        data = gj(url, src)
        if isinstance(data, list) and len(data) > 0:
            out = [{
                "symbol": t["symbol"],
                "priceChangePercent": float(t.get("priceChangePercent", 0)),
                "lastPrice": float(t.get("lastPrice", 0)),
                "quoteVolume": float(t.get("quoteVolume", 0)),
            } for t in data if "symbol" in t]
            with _lock:
                _ticker_cache["SPOT"] = (now, out, label)
            return out, label

    b = gj(f"{BYBIT}/v5/market/tickers?category=spot", "BYBIT")
    if isinstance(b, dict) and b.get("retCode") == 0:
        lst = b.get("result", {}).get("list", [])
        out = []
        for t in lst:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            try:
                out.append({
                    "symbol": sym,
                    "priceChangePercent": float(t.get("price24hPcnt", 0)) * 100.0,
                    "lastPrice": float(t.get("lastPrice", 0)),
                    "quoteVolume": float(t.get("turnover24h", 0)),
                })
            except Exception:
                continue
        if out:
            with _lock:
                _ticker_cache["SPOT"] = (now, out, "Spot (BYBIT)")
            return out, "Spot (BYBIT)"

    return get_futures_tickers()


_BYBIT_TF = {
    "5m": "5", "15m": "15", "1h": "60", "4h": "240",
    "8h": "480", "12h": "720", "1d": "D", "3d": "D",
}
_GATE_TF = {
    "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h",
    "8h": "8h", "1d": "1d",
}


def get_klines(symbol, interval, limit, market="FUTURES"):
    now = time.time()
    cache_key = f"{market}:{symbol}:{interval}"
    with _lock:
        c = _kline_cache.get(cache_key)
        if c and (now - c[0]) < CACHE_TTL_KLINE and len(c[1]) >= limit:
            return c[1][-limit:], c[2]

    # 1. Binance Futures / Spot
    if market == "FUTURES":
        sources = [("FAPI", f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}", "Binance Futures")]
    else:
        sources = [
            ("DATA_API", f"{DATA_API}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}", "Binance Spot"),
            ("BASE", f"{BASE}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}", "Binance Spot"),
        ]

    for src, url, label in sources:
        k = gj(url, src)
        if isinstance(k, list) and len(k) > 0:
            with _lock:
                _kline_cache[cache_key] = (now, k, label)
            return k, label

    # 2. Bybit fallback (8. eleman turnover/quote volume olduğu için indeks 7'ye konur)
    btf = _BYBIT_TF.get(interval)
    if btf and interval != "3d":
        bcat = "linear" if market == "FUTURES" else "spot"
        b = gj(f"{BYBIT}/v5/market/kline?category={bcat}&symbol={symbol}&interval={btf}&limit={min(limit, 1000)}", "BYBIT")
        if isinstance(b, dict) and b.get("retCode") == 0:
            raw_list = b.get("result", {}).get("list", [])
            if raw_list:
                norm = []
                for row in reversed(raw_list):
                    # [openTime, open, high, low, close, baseVol, closeTime, quoteVol]
                    norm.append([int(row[0]), row[1], row[2], row[3], row[4], row[5], int(row[0]), row[6]])
                blabel = "Futures (BYBIT)" if market == "FUTURES" else "Spot (BYBIT)"
                with _lock:
                    _kline_cache[cache_key] = (now, norm, blabel)
                return norm, blabel

    # 3. Gate.io Futures fallback
    gtf = _GATE_TF.get(interval)
    if market == "FUTURES" and gtf and symbol.endswith("USDT"):
        contract = symbol[:-4] + "_USDT"
        g = gj(f"{GATE}/futures/usdt/candlesticks?contract={contract}&interval={gtf}&limit={min(limit, 500)}", "GATE")
        if isinstance(g, list) and len(g) > 0:
            norm = []
            for row in g:
                ts_ms = int(float(row["t"])) * 1000
                vol_q = float(row.get("sum", row.get("v", 0)))
                norm.append([ts_ms, row["o"], row["h"], row["l"], row["c"], row["v"], ts_ms, str(vol_q)])
            with _lock:
                _kline_cache[cache_key] = (now, norm, "Futures (GATE)")
            return norm, "Futures (GATE)"

    # 4. Son çare Binance Data-API
    k = gj(f"{DATA_API}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}", "DATA_API")
    if isinstance(k, list) and len(k) > 0:
        with _lock:
            _kline_cache[cache_key] = (now, k, "Spot Yedek (Binance)")
        return k, "Spot Yedek (Binance)"

    return None, "NONE"


# =====================================================
# TEKNİK ANALİZ VE SAF PYTHON PNG GRAFİK MOTORU
# =====================================================

def calculate_ema(prices, period):
    if not prices or len(prices) < period:
        return []
    k = 2.0 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for price in prices[period:]:
        ema.append(price * k + ema[-1] * (1.0 - k))
    return ema


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


def calculate_rsi_series(prices, period=14):
    if len(prices) < period + 1:
        return [50.0] * len(prices)
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsis = [50.0] * period
    rs = (avg_gain / avg_loss) if avg_loss > 0 else 999.0
    rsis.append(100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + rs)))
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsis.append(100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss))))
    return rsis


def calculate_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow + signal:
        return None
    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)
    if not ema_fast or not ema_slow:
        return None
    offset = slow - fast
    if len(ema_fast) <= offset:
        return None
    aligned_fast = ema_fast[offset:]
    min_len = min(len(aligned_fast), len(ema_slow))
    macd_line = [aligned_fast[i] - ema_slow[i] for i in range(min_len)]
    signal_line = calculate_ema(macd_line, signal)
    if len(signal_line) < 2 or len(macd_line) < 2:
        return None
    return macd_line[-1], signal_line[-1], macd_line[-2], signal_line[-2]


def find_pivots(highs, lows, window=3):
    pivot_highs = []
    pivot_lows = []
    for i in range(window, len(highs) - window):
        if highs[i] == max(highs[i - window:i + window + 1]):
            pivot_highs.append((i, highs[i]))
        if lows[i] == min(lows[i - window:i + window + 1]):
            pivot_lows.append((i, lows[i]))
    return pivot_highs, pivot_lows


def trap_radar_check(symbol, ksrc="Futures"):
    try:
        if "BYBIT" in ksrc:
            oi_res = gj(f"{BYBIT}/v5/market/open-interest?category=linear&symbol={symbol}&intervalTime=5min&limit=2", "BYBIT")
            ratio_res = gj(f"{BYBIT}/v5/market/account-ratio?category=linear&symbol={symbol}&period=5min&limit=2", "BYBIT")
            if not isinstance(oi_res, dict) or oi_res.get("retCode") != 0:
                return ""
            oi_list = oi_res.get("result", {}).get("list", [])
            if len(oi_list) < 2:
                return ""
            oi_new = float(oi_list[0]["openInterest"])
            oi_old = float(oi_list[1]["openInterest"])
            oi_artiyor = oi_old > 0 and ((oi_new - oi_old) / oi_old) * 100 > 0.2

            alici_baskin = None
            if isinstance(ratio_res, dict) and ratio_res.get("retCode") == 0:
                rlist = ratio_res.get("result", {}).get("list", [])
                if rlist:
                    buy_r = float(rlist[0].get("buyRatio", 0.5))
                    alici_baskin = buy_r > 0.55

            if alici_baskin is None:
                return ""
            if oi_artiyor and alici_baskin:
                return "🐋 <b>BALİNA TEYİDİ (Gerçek Pump)</b>"
            if not alici_baskin:
                return "⚠️ <b>DİKKAT: SAHTE PUMP (TUZAK OLABİLİR)</b>"
            return "⚖️ <b>Kısmi Teyit</b>"

        oi_data = gj(f"{FAPI}/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=2", "FAPI")
        taker_data = gj(f"{FAPI}/futures/data/takerlongshortRatio?symbol={symbol}&period=5m&limit=1", "FAPI")
        if not isinstance(oi_data, list) or len(oi_data) < 2:
            return ""
        if not isinstance(taker_data, list) or len(taker_data) < 1:
            return ""

        oi_old = float(oi_data[0]["sumOpenInterest"])
        oi_new = float(oi_data[1]["sumOpenInterest"])
        oi_artiyor = oi_old > 0 and ((oi_new - oi_old) / oi_old) * 100 > 0.2

        buy_vol = float(taker_data[0]["buyVol"])
        sell_vol = float(taker_data[0]["sellVol"])
        total = buy_vol + sell_vol
        alici_baskin = total > 0 and (buy_vol / total) * 100 > 55

        if oi_artiyor and alici_baskin:
            return "🐋 <b>BALİNA TEYİDİ (Gerçek Pump)</b>"
        elif not alici_baskin:
            return "⚠️ <b>DİKKAT: SAHTE PUMP (TUZAK OLABİLİR)</b>"
        else:
            return "⚖️ <b>Kısmi Teyit</b>"
    except Exception:
        return ""


_FONT_5X7 = {
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
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
    "0": ["01110", "10011", "10101", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00110", "01000", "10000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["01110", "10000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00001", "01110"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
    "$": ["00100", "01111", "10100", "01110", "00101", "11110", "00100"],
    "%": ["11001", "11010", "00010", "00100", "01000", "01011", "10011"],
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
}


def render_chart_png(symbol, klines, title_suffix="D1"):
    try:
        candles = klines[-60:] if len(klines) > 60 else klines
        if len(candles) < 2:
            return None

        W, H = 720, 460
        bg = (19, 23, 34)
        grid = (42, 46, 57)
        green = (8, 153, 129)
        red = (242, 54, 69)
        white = (225, 228, 235)
        cyan = (56, 189, 248)
        muted = (140, 146, 160)

        buf = bytearray(bytes(bg) * (W * H))

        def px(x, y, col):
            if 0 <= x < W and 0 <= y < H:
                i = (y * W + x) * 3
                buf[i], buf[i + 1], buf[i + 2] = col

        def rect(x0, y0, x1, y1, col):
            x0, x1 = max(0, min(x0, x1)), min(W - 1, max(x0, x1))
            y0, y1 = max(0, min(y0, y1)), min(H - 1, max(y0, y1))
            r_bytes = bytes(col) * (x1 - x0 + 1)
            for yy in range(y0, y1 + 1):
                i = (yy * W + x0) * 3
                buf[i:i + len(r_bytes)] = r_bytes

        def line(x0, y0, x1, y1, col):
            dx, dy = abs(x1 - x0), -abs(y1 - y0)
            sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
            err = dx + dy
            while True:
                px(x0, y0, col)
                if x0 == x1 and y0 == y1:
                    break
                e2 = 2 * err
                if e2 >= dy:
                    err += dy
                    x0 += sx
                if e2 <= dx:
                    err += dx
                    y0 += sy

        def draw_text(x, y, text, col, scale=2):
            cx = x
            for ch in text.upper():
                glyph = _FONT_5X7.get(ch)
                if glyph:
                    for ry, row in enumerate(glyph):
                        for rx, bit in enumerate(row):
                            if bit == "1":
                                rect(cx + rx * scale, y + ry * scale, cx + (rx + 1) * scale - 1, y + (ry + 1) * scale - 1, col)
                cx += 6 * scale

        # Bölgeler: Fiyat [36..275], Hacim [288..360], RSI [374..440]
        left, right = 18, W - 76
        for gy in (36, 95, 155, 215, 275, 288, 360, 374, 407, 440):
            line(left, gy, right, gy, grid)
        for gx in range(left, right + 1, (right - left) // 5):
            line(gx, 36, gx, 440, grid)

        opens = [float(k[1]) for k in candles]
        highs = [float(k[2]) for k in candles]
        lows = [float(k[3]) for k in candles]
        closes = [float(k[4]) for k in candles]
        vols = [float(k[7]) for k in candles]
        rsis = calculate_rsi_series([float(k[4]) for k in klines], 14)[-len(candles):]

        p_min, p_max = min(lows), max(highs)
        if p_max == p_min:
            p_max += p_min * 0.01 or 1.0
        pad = (p_max - p_min) * 0.08
        p_min -= pad
        p_max += pad

        def py_price(val):
            return int(275 - ((val - p_min) / (p_max - p_min)) * (275 - 36))

        v_max = max(vols) or 1.0
        n = len(candles)
        slot_w = max(4.0, (right - left) / n)
        body_w = max(2, int(slot_w * 0.64))

        for i in range(n):
            cx = int(left + (i + 0.5) * slot_w)
            col = green if closes[i] >= opens[i] else red
            yh = py_price(highs[i])
            yl = py_price(lows[i])
            yo = py_price(opens[i])
            yc = py_price(closes[i])
            line(cx, yh, cx, yl, col)
            y_top, y_bot = min(yo, yc), max(yo, yc)
            if y_bot - y_top < 2:
                y_bot = y_top + 2
            rect(cx - body_w // 2, y_top, cx + body_w // 2, y_bot, col)

            vh = int((vols[i] / v_max) * (360 - 292))
            if vh > 0:
                rect(cx - body_w // 2, 360 - vh, cx + body_w // 2, 360, col)

        for i in range(1, n):
            x0 = int(left + (i - 0.5) * slot_w)
            x1 = int(left + (i + 0.5) * slot_w)
            y0 = int(440 - (max(0.0, min(100.0, rsis[i - 1])) / 100.0) * (440 - 374))
            y1 = int(440 - (max(0.0, min(100.0, rsis[i])) / 100.0) * (440 - 374))
            line(x0, y0, x1, y1, cyan)

        draw_text(left, 12, f"{symbol} {title_suffix}", white, scale=2)
        draw_text(right + 6, 36, akilli_fiyat(p_max), muted, scale=1)
        draw_text(right + 6, 152, akilli_fiyat((p_max + p_min) / 2), muted, scale=1)
        draw_text(right + 6, 268, akilli_fiyat(p_min), muted, scale=1)
        draw_text(right + 6, 290, "VOL", muted, scale=1)
        draw_text(right + 6, 376, f"RSI {rsis[-1]:.0f}", cyan, scale=1)

        raw_rows = bytearray()
        stride = W * 3
        for y in range(H):
            raw_rows.append(0)
            raw_rows.extend(buf[y * stride:(y + 1) * stride])

        def chunk(ctype, cdata):
            crc = zlib.crc32(ctype + cdata) & 0xFFFFFFFF
            return struct.pack(">I", len(cdata)) + ctype + cdata + struct.pack(">I", crc)

        png = bytearray(b"\x89PNG\r\n\x1a\n")
        png.extend(chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)))
        png.extend(chunk(b"IDAT", zlib.compress(bytes(raw_rows), 6)))
        png.extend(chunk(b"IEND", b""))
        return bytes(png)
    except Exception:
        return None


# =====================================================
# MOTOR 1: GÜNÜN EN İYİ HAREKETLERİ (TOP GAINERS)
# =====================================================
def top_gainers_loop():
    global _last_top_gainers_ts
    log("TOP_GAINERS", "Günlük En İyi Hareketler Rapor Botu Başladı.")
    time.sleep(10)
    while True:
        try:
            tickers, src = get_futures_tickers()
            if not isinstance(tickers, list):
                time.sleep(120)
                continue

            temiz = [
                t for t in tickers
                if t["symbol"].endswith("USDT")
                and t["symbol"] not in YASAKLI_HISSELER
                and float(t.get("quoteVolume", 0)) >= MIN_HACIM_TOP_GAINERS
            ]
            temiz.sort(key=lambda x: float(x["priceChangePercent"]), reverse=True)
            top_5 = temiz[:5]

            if top_5:
                msg = f"🔥 <b>Günün En İyi Hareketleri ({src})</b>\n"
                msg += "<i>24 saatlik yükseliş ve USDT hacmine göre</i>\n\n"
                medals = ["🥇", "🥈", "🥉", "🏅", "⭐"]
                for i, t in enumerate(top_5):
                    sym = t["symbol"].replace("USDT", "")
                    chg = float(t["priceChangePercent"])
                    prc = float(t["lastPrice"])
                    chg_str = f"+{chg:.2f}%" if chg >= 0 else f"{chg:.2f}%"
                    msg += f"{medals[i]} <b>{sym}</b> {chg_str} | {akilli_fiyat(prc)}\n"

                if tg(C, msg):
                    _last_top_gainers_ts = time.time()
                    log("TOP_GAINERS", f"Rapor gönderildi ({src}).")
            time.sleep(TOP_GAINERS_ARALIGI)
        except Exception as e:
            log("TOP_GAINERS HATA", e)
            time.sleep(60)


# =====================================================
# MOTOR 2: GÜNÜN KAZANANI (PUMP WINNER)
# =====================================================
def pump_winner_loop():
    log("PUMP_WINNER", "Günün Kazananı Rapor Botu Başladı.")
    time.sleep(20)
    while True:
        try:
            tickers, src = get_futures_tickers()
            if not isinstance(tickers, list):
                time.sleep(120)
                continue

            temiz = [
                t for t in tickers
                if t["symbol"].endswith("USDT")
                and t["symbol"] not in YASAKLI_HISSELER
                and float(t.get("quoteVolume", 0)) >= MIN_HACIM_PUMP_WINNER
            ]
            temiz.sort(key=lambda x: float(x["priceChangePercent"]), reverse=True)
            if not temiz or float(temiz[0]["priceChangePercent"]) < 15.0:
                time.sleep(PUMP_WINNER_ARALIGI)
                continue

            winner = temiz[0]
            sym = winner["symbol"]
            klines, ksrc = get_klines(sym, "1d", 2, "FUTURES")
            if not klines:
                time.sleep(PUMP_WINNER_ARALIGI)
                continue

            day_low = float(klines[-1][3])
            day_high = float(klines[-1][2])
            if day_low <= 0:
                time.sleep(PUMP_WINNER_ARALIGI)
                continue
            max_move = ((day_high - day_low) / day_low) * 100.0

            msg = (
                f"🏆 <b>GÜNÜN KAZANANI: {sym}</b>\n"
                f"<i>{ksrc} - 24 Saat Özeti</i>\n\n"
                f"🔥 24 saat içinde en çok yükselen coin!\n\n"
                f"📉 <b>Dip Fiyat:</b> {akilli_fiyat(day_low)}\n"
                f"🚀 <b>Zirve Fiyat:</b> {akilli_fiyat(day_high)}\n"
                f"📈 <b>Maksimum Yükseliş:</b> <b>+{max_move:.1f}%</b>\n\n"
                f"💡 <b>Değerlendirme:</b> {sym} gün içerisinde güçlü hacim ve fiyat patlaması yaşadı."
            )
            tg(C, msg)
            time.sleep(PUMP_WINNER_ARALIGI)
        except Exception as e:
            log("PUMP_WINNER HATA", e)
            time.sleep(60)


# =====================================================
# MOTOR 3: PİYASA YÖNÜ & DOMİNANS RAPORU
# =====================================================
def dominans_loop():
    global _last_dominans_ts
    log("DOMINANS", "BTC & ETH Dominans Rapor Motoru Başladı.")
    time.sleep(25)
    while True:
        try:
            g = gj(f"{COINGECKO}/global", "COINGECKO")
            if isinstance(g, dict) and isinstance(g.get("data"), dict):
                d = g["data"]
                pct_map = d.get("market_cap_percentage", {})
                btc_d = float(pct_map.get("btc", 0))
                eth_d = float(pct_map.get("eth", 0))
                mcap_chg = float(d.get("market_cap_change_percentage_24h_usd", 0))

                if btc_d > 0:
                    if mcap_chg >= 0.5:
                        durum = "🟢 Yükselişte"
                    elif mcap_chg <= -0.5:
                        durum = "🔴 Düşüşte"
                    else:
                        durum = "🟡 Yatay"

                    if btc_d >= 56.0:
                        degerlendirme = (
                            "Piyasadaki para Bitcoin'e akıyor. Altcoinlerde (küçük coinlerde) "
                            "dikkatli olunmalı. BTC ağırlıklı işlemler ön planda."
                        )
                    elif btc_d <= 50.0:
                        degerlendirme = (
                            "BTC dominansı gevşiyor, sermaye altcoinlere kayıyor. "
                            "Güçlü hacimli altcoinlerde fırsatlar artabilir."
                        )
                    else:
                        degerlendirme = (
                            "Piyasada BTC ve altcoinler arasında dengeli bir dağılım var. "
                            "Hacim teyitli kırılımlar takip edilmeli."
                        )

                    msg = (
                        f"👑 <b>PİYASA YÖNÜ & DOMİNANS RAPORU</b>\n\n"
                        f"🪙 <b>BTC Dominansı:</b> %{btc_d:.2f}\n"
                        f"💎 <b>ETH Dominansı:</b> %{eth_d:.2f}\n\n"
                        f"📊 <b>Durum:</b> {durum} ({mcap_chg:+.2f}% 24s)\n"
                        f"💡 <b>Değerlendirme:</b> {degerlendirme}"
                    )
                    if tg(C, msg):
                        _last_dominans_ts = time.time()
                        log("DOMINANS", f"Rapor gönderildi (BTC={btc_d:.2f}%).")
            time.sleep(DOMINANS_ARALIGI)
        except Exception as e:
            log("DOMINANS HATA", e)
            time.sleep(120)


# =====================================================
# MOTOR 4: CHARTIST (SPOT EMA200 KIRILIMI)
# =====================================================
def chartist_loop():
    log("CHARTIST", "Zırhlı Spot EMA200 Motoru Başladı.")
    time.sleep(30)
    tf_ms_map = {"8h": 8 * 3600_000, "12h": 12 * 3600_000, "1d": 24 * 3600_000, "3d": 72 * 3600_000}
    while True:
        try:
            tickers, _ = get_spot_tickers()
            if tickers:
                adaylar = [
                    t for t in tickers
                    if t["symbol"].endswith("USDT")
                    and t["symbol"] not in YASAKLI_HISSELER
                    and float(t.get("quoteVolume", 0)) >= MIN_HACIM_CHARTIST
                ]
                adaylar.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
                for coin in adaylar[:MAX_TARAMA]:
                    symbol = coin["symbol"]
                    for tf in ("8h", "12h", "1d", "3d"):
                        cooldown_key = f"CH_{tf}_{symbol}"
                        if cooldown_key in son_sinyaller and time.time() - son_sinyaller[cooldown_key] < 86400:
                            continue
                        klines, ksrc = get_klines(symbol, tf, 250, "SPOT")
                        if not klines or len(klines) < 205:
                            continue
                        tf_ms = tf_ms_map[tf]
                        if not _bar_fresh(klines[-2], tf_ms, min(tf_ms, 4 * 3600_000)):
                            continue
                        closes_all = [float(k[4]) for k in klines]
                        live_price = closes_all[-1]
                        closes = closes_all[:-1]
                        curr, prev = closes[-1], closes[-2]
                        ema200 = calculate_ema(closes, 200)
                        if len(ema200) >= 2 and prev <= ema200[-2] and curr > ema200[-1] * 1.01 and live_price > ema200[-1]:
                            vol = [float(k[7]) for k in klines[:-1]]
                            if len(vol) >= 21 and vol[-1] > sum(vol[-21:-1]) / 20.0:
                                sl = ema200[-1] * 0.97
                                msg = (
                                    f"📈 <b>UZUN VADELİ SPOT (LONG): {symbol}</b>\n"
                                    f"<i>{ksrc} - {tf.upper()} EMA200 Kırılımı</i>\n\n"
                                    f"💰 <b>Giriş (Kapanış):</b> {akilli_fiyat(curr)}\n"
                                    f"⚡ <b>Anlık Fiyat:</b> {akilli_fiyat(live_price)}\n"
                                    f"🛑 <b>SL:</b> {akilli_fiyat(sl)}\n"
                                    f"🎯 <b>Hedef:</b> {akilli_fiyat(curr * 1.15)}\n\n"
                                    f"📊 <b>Teyit:</b> Hacim Onaylı ve %1 üzeri kapanış.\n\n"
                                    f"💡 <b>Açıklama:</b> EMA200 uzun vadeli ana trend çizgisidir. Fiyatın hacimle yukarı kırıp üstünde kalması güçlü boğa sinyalidir."
                                )
                                if tg(C, msg):
                                    son_sinyaller[cooldown_key] = time.time()
                    time.sleep(0.05)
            time.sleep(CHARTIST_ARALIGI)
        except Exception as e:
            log("CHARTIST HATA", e)
            time.sleep(60)


# =====================================================
# MOTOR 5: ANLIK PUMP AVCISI (5m + TRAP RADAR)
# =====================================================
def pump_loop():
    log("PUMP", "Anlık Pump Avcısı Başladı (5m).")
    time.sleep(45)
    while True:
        try:
            tickers, _ = get_futures_tickers()
            if tickers:
                adaylar = [
                    t for t in tickers
                    if t["symbol"].endswith("USDT")
                    and t["symbol"] not in YASAKLI_HISSELER
                    and float(t.get("quoteVolume", 0)) >= MIN_HACIM_PUMP
                ]
                adaylar.sort(key=lambda x: abs(float(x["priceChangePercent"])), reverse=True)
                for coin in adaylar[:MAX_TARAMA]:
                    symbol = coin["symbol"]
                    if symbol in son_pump_sinyalleri and time.time() - son_pump_sinyalleri[symbol] < 900:
                        continue
                    klines, ksrc = get_klines(symbol, "5m", 3, "FUTURES")
                    if not klines or len(klines) < 3:
                        continue
                    curr_k, prev_k = klines[-2], klines[-3]
                    cc, pc = float(curr_k[4]), float(prev_k[4])
                    cv, pv = float(curr_k[7]), float(prev_k[7])
                    if pc <= 0 or pv <= 0:
                        continue
                    price_change = ((cc - pc) / pc) * 100.0
                    vol_change = ((cv - pv) / pv) * 100.0
                    vol_diff = cv - pv
                    if price_change >= 3.0 and vol_change >= 100.0 and vol_diff >= 50_000:
                        trap_radar = trap_radar_check(symbol, ksrc)
                        trap_line = f"{trap_radar}\n\n" if trap_radar else ""
                        msg = (
                            f"🚀 <b>Pump - {symbol}</b>\n"
                            f"<i>{ksrc}</i>\n"
                            f"💰 <b>Fiyat:</b> {akilli_fiyat(cc)} (<b>{price_change:+.2f}%</b>)\n"
                            f"📊 <b>Hacim Artışı:</b> {vol_change:+.0f}%\n"
                            f"Volume increased by {vol_diff:.0f} ⬆️\n\n"
                            f"{trap_line}"
                            f"💡 <b>Açıklama:</b> 5 dakikalık grafikte hacim ve fiyat artışı tespit edildi."
                        )
                        if tg(C, msg):
                            son_pump_sinyalleri[symbol] = time.time()
                            log("PUMP", f"🔥 {symbol} ({price_change:+.2f}%)")
                    time.sleep(0.05)
            time.sleep(PUMP_ARALIGI)
        except Exception as e:
            log("PUMP HATA", e)
            time.sleep(60)


# =====================================================
# MOTOR 6: ANORMAL VOLATİLİTE VE DUMP AVCISI (15m)
# =====================================================
def volatilite_loop():
    log("VOLATILITE", "Anormal Volatilite ve Dump Avcısı Başladı (15m).")
    time.sleep(55)
    while True:
        try:
            tickers, _ = get_futures_tickers()
            if tickers:
                adaylar = [
                    t for t in tickers
                    if t["symbol"].endswith("USDT")
                    and t["symbol"] not in YASAKLI_HISSELER
                    and float(t.get("quoteVolume", 0)) >= MIN_HACIM_VOLATILITE
                ]
                adaylar.sort(key=lambda x: abs(float(x["priceChangePercent"])), reverse=True)
                for coin in adaylar[:MAX_TARAMA]:
                    symbol = coin["symbol"]
                    if symbol in son_volatilite_sinyalleri and time.time() - son_volatilite_sinyalleri[symbol] < 1800:
                        continue
                    klines, ksrc = get_klines(symbol, "15m", 25, "FUTURES")
                    if not klines or len(klines) < 25:
                        continue
                    curr_open = float(klines[-2][1])
                    curr_close = float(klines[-2][4])
                    curr_vol = float(klines[-2][7])
                    vols = [float(k[7]) for k in klines]
                    avg_vol = sum(vols[-22:-2]) / 20.0
                    if curr_open <= 0 or avg_vol <= 0:
                        continue

                    price_change_pct = ((curr_close - curr_open) / curr_open) * 100.0
                    vol_multiplier = curr_vol / avg_vol
                    if abs(price_change_pct) >= 2.5 and vol_multiplier >= 3.0:
                        yon = "DUMP (ANI DÜŞÜŞ) 🔻" if price_change_pct < 0 else "PUMP (ANI YÜKSELİŞ) 🚀"
                        emoji = "🚨" if price_change_pct < 0 else "⚡"
                        msg = (
                            f"{emoji} <b>ANORMAL VOLATİLİTE UYARISI</b> {emoji}\n"
                            f"<b>Coin:</b> {symbol} ({ksrc} - 15m)\n\n"
                            f"📉 <b>Hareket:</b> {yon}\n"
                            f"📊 <b>Değişim:</b> <b>{price_change_pct:+.2f}%</b>\n"
                            f"📈 <b>Hacim Patlaması:</b> Ortalamanın <b>{vol_multiplier:.1f}x</b> katı\n"
                            f"💰 <b>Kapanış Fiyatı:</b> {akilli_fiyat(curr_close)}\n\n"
                            f"⚠️ <i>Piyasada ani likidite değişimi tespit edildi. Dikkatli olun!</i>"
                        )
                        if tg(C, msg):
                            son_volatilite_sinyalleri[symbol] = time.time()
                            log("VOLATILITE", f"🔥 {symbol} ({yon})")
                    time.sleep(0.05)
            time.sleep(VOLATILITE_ARALIGI)
        except Exception as e:
            log("VOLATILITE HATA", e)
            time.sleep(60)


# =====================================================
# MOTOR 7: TEYİTLİ FORMASYON (5m FORMASYON - 15m TREND)
# =====================================================
def formasyon_loop():
    log("FORMASYON", "Teyitli Formasyon ve Likidite Avcısı Başladı.")
    time.sleep(60)
    while True:
        try:
            tickers, _ = get_futures_tickers()
            if tickers:
                adaylar = [
                    t for t in tickers
                    if t["symbol"].endswith("USDT")
                    and t["symbol"] not in YASAKLI_HISSELER
                    and float(t.get("quoteVolume", 0)) >= MIN_HACIM_FORMASYON
                ]
                adaylar.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
                for coin in adaylar[:MAX_TARAMA]:
                    symbol = coin["symbol"]
                    if symbol in son_formasyon_sinyalleri and time.time() - son_formasyon_sinyalleri[symbol] < 86400:
                        continue
                    klines_5m, ksrc = get_klines(symbol, "5m", 100, "FUTURES")
                    klines_15m, _ = get_klines(symbol, "15m", 50, "FUTURES")
                    if not klines_5m or not klines_15m or len(klines_5m) < 100 or len(klines_15m) < 50:
                        continue

                    highs = [float(k[2]) for k in klines_5m]
                    lows = [float(k[3]) for k in klines_5m]
                    closes = [float(k[4]) for k in klines_5m]
                    vols = [float(k[7]) for k in klines_5m]

                    closes_15m = [float(k[4]) for k in klines_15m]
                    ema11_list = calculate_ema(closes_15m, 11)
                    ema21_list = calculate_ema(closes_15m, 21)
                    if not ema11_list or not ema21_list:
                        continue
                    ema11, ema21 = ema11_list[-1], ema21_list[-1]
                    rsi = calculate_rsi(closes_15m)

                    current_price = closes[-1]
                    avg_vol = sum(vols[-21:-1]) / 20.0
                    current_vol = vols[-1]
                    if avg_vol <= 0 or current_price <= 0:
                        continue
                    is_vol_teyit = current_vol > (avg_vol * 1.5)

                    p_highs, p_lows = find_pivots(highs, lows)
                    if len(p_highs) < 3 or len(p_lows) < 3:
                        continue
                    last_h, last_l = p_highs[-3:], p_lows[-3:]

                    sinyal = False
                    formasyon_adi = ""
                    yon = ""
                    emoji = ""
                    hedef = 0.0
                    stop = 0.0
                    aciklama = ""

                    if abs(last_h[0][1] - last_h[2][1]) / last_h[0][1] < 0.03 and current_price < last_l[-1][1] and is_vol_teyit:
                        if rsi < 50 and ema11 < ema21:
                            sinyal = True; formasyon_adi = "Çift Tepe (M)"; yon = "SHORT"; emoji = "🔻"
                            hedef = last_l[-1][1] - (last_h[1][1] - last_l[-1][1]); stop = last_h[2][1] * 1.02
                            aciklama = "İkinci tepe ilkini geçemedi ve boyun çizgisi hacimle aşağı kırıldı."

                    elif abs(last_l[0][1] - last_l[2][1]) / last_l[0][1] < 0.03 and current_price > last_h[-1][1] and is_vol_teyit:
                        if rsi > 50 and ema11 > ema21:
                            sinyal = True; formasyon_adi = "Çift Dip (W)"; yon = "LONG"; emoji = "🚀"
                            hedef = last_h[-1][1] + (last_h[-1][1] - last_l[1][1]); stop = last_l[2][1] * 0.98
                            aciklama = "İkinci dip satıcıları tüketti ve boyun çizgisi hacimle yukarı kırıldı."

                    elif last_h[1][1] > last_h[0][1] and last_h[1][1] > last_h[2][1] and abs(last_h[0][1] - last_h[2][1]) / last_h[0][1] < 0.05 and current_price < last_l[-1][1] and is_vol_teyit:
                        if rsi < 50 and ema11 < ema21:
                            sinyal = True; formasyon_adi = "Omuz-Baş-Omuz"; yon = "SHORT"; emoji = "🔻"
                            hedef = last_l[-1][1] - (last_h[1][1] - last_l[-1][1]); stop = last_h[2][1] * 1.02
                            aciklama = "Klasik OBO dönüş formasyonu. Boyun çizgisi aşağı kırıldı."

                    elif highs[-2] > max(highs[-20:-2]) and current_price < max(highs[-20:-2]) and is_vol_teyit:
                        if rsi < 50 and ema11 < ema21:
                            sinyal = True; formasyon_adi = "Likidite Avı (Sweep)"; yon = "SHORT"; emoji = "💀"
                            stop = highs[-2] * 1.01; hedef = current_price * 0.97
                            aciklama = "Fiyat eski tepenin üzerine fitil attı ancak altında kapandı."

                    elif lows[-2] < min(lows[-20:-2]) and current_price > min(lows[-20:-2]) and is_vol_teyit:
                        if rsi > 50 and ema11 > ema21:
                            sinyal = True; formasyon_adi = "Likidite Avı (Sweep)"; yon = "LONG"; emoji = "🚀"
                            stop = lows[-2] * 0.99; hedef = current_price * 1.03
                            aciklama = "Fiyat eski dibin altına fitil attı ancak üstünde kapandı."

                    if not sinyal:
                        l20h, l20l = max(highs[-20:]), min(lows[-20:])
                        range_pct = ((l20h - l20l) / l20l) * 100.0 if l20l > 0 else 99.0
                        if range_pct < 5 and current_price > l20h and is_vol_teyit and rsi > 50:
                            sinyal = True; formasyon_adi = "Sıkışma ve Yukarı Kırılım (Bayrak)"; yon = "LONG"; emoji = "🚀"
                            hedef = current_price + (l20h - l20l); stop = l20l * 0.98
                            aciklama = "Dar bant sıkışması hacimle yukarı kırıldı."
                        elif range_pct < 5 and current_price < l20l and is_vol_teyit and rsi < 50:
                            sinyal = True; formasyon_adi = "Sıkışma ve Aşağı Kırılım (Ters Bayrak)"; yon = "SHORT"; emoji = "🔻"
                            hedef = current_price - (l20h - l20l); stop = l20h * 1.02
                            aciklama = "Dar bant sıkışması hacimle aşağı kırıldı."

                    if sinyal:
                        hedef_pct = ((hedef - current_price) / current_price) * 100.0
                        stop_pct = ((stop - current_price) / current_price) * 100.0
                        if yon == "SHORT" and hedef >= current_price:
                            continue
                        if yon == "LONG" and hedef <= current_price:
                            continue
                        if abs(stop_pct) > 10.0 or abs(hedef_pct) < 1.0 or abs(hedef_pct) > 20.0:
                            continue

                        msg = (
                            f"{emoji} <b>TEYİTLİ DÖNÜŞ ({yon}): {symbol}</b>\n"
                            f"<i>{ksrc} | 5m Formasyon - 15m Trend Teyitli</i>\n\n"
                            f"💰 <b>Giriş:</b> {akilli_fiyat(current_price)}\n"
                            f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                            f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                            f"📊 <b>Formasyon:</b> {formasyon_adi}\n"
                            f"📊 <b>Teyitler:</b> Hacim ({current_vol/avg_vol:.1f}x), RSI ({rsi:.1f}), EMA (15m)\n\n"
                            f"💡 <b>Açıklama:</b> {aciklama}"
                        )
                        if tg(C, msg):
                            son_formasyon_sinyalleri[symbol] = time.time()
                            log("FORMASYON", f"🔥 {symbol} ({yon} - {formasyon_adi})")
                    time.sleep(0.05)
            time.sleep(FORMASYON_ARALIGI)
        except Exception as e:
            log("FORMASYON HATA", e)
            time.sleep(60)


# =====================================================
# MOTOR 8: ICHIMOKU BULUTU (1h)
# =====================================================
def ichimoku_loop():
    log("ICHIMOKU", "Zırhlı Ichimoku Bulut Motoru Başladı.")
    time.sleep(75)
    while True:
        try:
            tickers, _ = get_futures_tickers()
            if tickers:
                adaylar = [
                    t for t in tickers
                    if t["symbol"].endswith("USDT")
                    and t["symbol"] not in YASAKLI_HISSELER
                    and float(t.get("quoteVolume", 0)) >= MIN_HACIM_ICHIMOKU
                ]
                adaylar.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
                for coin in adaylar[:MAX_TARAMA]:
                    symbol = coin["symbol"]
                    if symbol in son_ichimoku_sinyalleri and time.time() - son_ichimoku_sinyalleri[symbol] < 14400:
                        continue
                    klines, ksrc = get_klines(symbol, "1h", 100, "FUTURES")
                    if not klines or len(klines) < 82:
                        continue
                    if not _bar_fresh(klines[-2], 3600_000, 90 * 60_000):
                        continue

                    highs = [float(k[2]) for k in klines]
                    lows = [float(k[3]) for k in klines]
                    closes = [float(k[4]) for k in klines]
                    vols = [float(k[7]) for k in klines]

                    curr_close, prev_close, live_price = closes[-2], closes[-3], closes[-1]
                    avg_vol = sum(vols[-21:-1]) / 20.0
                    if vols[-2] <= avg_vol:
                        continue

                    def calc_tenkan(idx):
                        return (max(highs[idx - 8:idx + 1]) + min(lows[idx - 8:idx + 1])) / 2.0

                    def calc_kijun(idx):
                        return (max(highs[idx - 25:idx + 1]) + min(lows[idx - 25:idx + 1])) / 2.0

                    idx_curr = len(closes) - 2
                    tenkan_curr = calc_tenkan(idx_curr)
                    kijun_curr = calc_kijun(idx_curr)
                    span_a_curr = (calc_tenkan(idx_curr - 26) + calc_kijun(idx_curr - 26)) / 2.0
                    span_a_prev = (calc_tenkan(idx_curr - 27) + calc_kijun(idx_curr - 27)) / 2.0
                    span_b_curr = (max(highs[idx_curr - 77:idx_curr - 26]) + min(lows[idx_curr - 77:idx_curr - 26])) / 2.0
                    span_b_prev = (max(highs[idx_curr - 78:idx_curr - 27]) + min(lows[idx_curr - 78:idx_curr - 27])) / 2.0

                    cloud_top_curr = max(span_a_curr, span_b_curr)
                    cloud_bottom_curr = min(span_a_curr, span_b_curr)
                    cloud_thickness = cloud_top_curr - cloud_bottom_curr
                    cloud_top_prev = max(span_a_prev, span_b_prev)
                    cloud_bottom_prev = min(span_a_prev, span_b_prev)

                    sinyal = False
                    yon = ""
                    emoji = ""
                    stop = 0.0
                    hedef = 0.0
                    aciklama = ""

                    if (
                        prev_close <= cloud_top_prev
                        and curr_close > cloud_top_curr
                        and live_price > cloud_top_curr
                        and curr_close > kijun_curr
                        and tenkan_curr > kijun_curr
                        and cloud_thickness > (curr_close * 0.005)
                    ):
                        sinyal = True; yon = "LONG (Bulut Üstü Kırılım)"; emoji = "🚀"
                        stop = min(kijun_curr, cloud_bottom_curr) * 0.99
                        hedef = curr_close + (cloud_thickness * 2.0)
                        aciklama = "Fiyat Ichimoku bulutunu yukarı kırdı ve Tenkan-sen Kijun-sen üzerinde."
                    elif (
                        prev_close >= cloud_bottom_prev
                        and curr_close < cloud_bottom_curr
                        and live_price < cloud_bottom_curr
                        and curr_close < kijun_curr
                        and tenkan_curr < kijun_curr
                        and cloud_thickness > (curr_close * 0.005)
                    ):
                        sinyal = True; yon = "SHORT (Bulut Altı Kırılım)"; emoji = "🔻"
                        stop = max(kijun_curr, cloud_top_curr) * 1.01
                        hedef = curr_close - (cloud_thickness * 2.0)
                        aciklama = "Fiyat Ichimoku bulutunu aşağı kırdı ve Tenkan-sen Kijun-sen altında."

                    if sinyal:
                        hedef_pct = ((hedef - curr_close) / curr_close) * 100.0
                        stop_pct = ((stop - curr_close) / curr_close) * 100.0
                        if abs(hedef_pct) < 1.0:
                            continue
                        msg = (
                            f"{emoji} <b>ICHIMOKU SİNYALİ ({yon}): {symbol}</b>\n"
                            f"<i>{ksrc} - 1 Saatlik Grafik</i>\n\n"
                            f"💰 <b>Kapanış Fiyatı:</b> {akilli_fiyat(curr_close)}\n"
                            f"⚡ <b>Anlık Fiyat:</b> {akilli_fiyat(live_price)}\n"
                            f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                            f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                            f"☁️ <b>Bulut:</b> {akilli_fiyat(cloud_bottom_curr)} - {akilli_fiyat(cloud_top_curr)}\n"
                            f"📈 <b>Tenkan:</b> {akilli_fiyat(tenkan_curr)} | <b>Kijun:</b> {akilli_fiyat(kijun_curr)}\n\n"
                            f"💡 <b>Açıklama:</b> {aciklama}"
                        )
                        if tg(C, msg):
                            son_ichimoku_sinyalleri[symbol] = time.time()
                            log("ICHIMOKU", f"🔥 {symbol} ({yon})")
                    time.sleep(0.05)
            time.sleep(ICHIMOKU_ARALIGI)
        except Exception as e:
            log("ICHIMOKU HATA", e)
            time.sleep(60)


# =====================================================
# MOTOR 9: EMA RIBBON (1d - TAZELİK & CANLI FİYAT TEYİTLİ)
# =====================================================
def ema_ribbon_loop():
    log("EMA_RIBBON", "EMA Dizilim Motoru Başladı.")
    time.sleep(90)
    while True:
        try:
            tickers, _ = get_futures_tickers()
            if tickers:
                adaylar = [
                    t for t in tickers
                    if t["symbol"].endswith("USDT")
                    and t["symbol"] not in YASAKLI_HISSELER
                    and float(t.get("quoteVolume", 0)) >= MIN_HACIM_EMA_RIBBON
                ]
                adaylar.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
                for coin in adaylar[:MAX_TARAMA]:
                    symbol = coin["symbol"]
                    if symbol in son_ema_ribbon_sinyalleri and time.time() - son_ema_ribbon_sinyalleri[symbol] < 86400:
                        continue
                    klines, ksrc = get_klines(symbol, "1d", 230, "FUTURES")
                    if not klines or len(klines) < 203:
                        continue
                    if not _bar_fresh(klines[-2], 86_400_000, 2 * 3600_000):
                        continue

                    closes_all = [float(k[4]) for k in klines]
                    live_price = closes_all[-1]
                    closes = closes_all[:-1]
                    vols = [float(k[7]) for k in klines[:-1]]
                    if len(closes) < 200 or len(vols) < 21:
                        continue
                    curr, curr_vol = closes[-1], vols[-1]
                    avg_vol = sum(vols[-21:-1]) / 20.0
                    if avg_vol <= 0 or curr_vol <= (avg_vol * 1.2):
                        continue

                    ema7 = calculate_ema(closes, 7)
                    ema25 = calculate_ema(closes, 25)
                    ema99 = calculate_ema(closes, 99)
                    ema200 = calculate_ema(closes, 200)
                    if not (ema7 and ema25 and ema99 and ema200):
                        continue
                    e7, e25, e99, e200 = ema7[-1], ema25[-1], ema99[-1], ema200[-1]

                    if curr > e7 > e25 > e99 > e200 and live_price > e25:
                        msg = (
                            f"🚀 <b>EMA DİZİLİMİ (LONG): {symbol}</b>\n"
                            f"<i>{ksrc} - 1 Günlük Grafik</i>\n\n"
                            f"💰 <b>Giriş (Kapanış):</b> {akilli_fiyat(curr)}\n"
                            f"⚡ <b>Anlık Fiyat:</b> {akilli_fiyat(live_price)}\n"
                            f"🎯 <b>Hedef:</b> {akilli_fiyat(curr * 1.20)} (+20.0%)\n"
                            f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(e25 * 0.95)}\n\n"
                            f"📊 <b>EMA Dizilimi:</b>\n"
                            f"EMA7: {akilli_fiyat(e7)} | EMA25: {akilli_fiyat(e25)}\n"
                            f"EMA99: {akilli_fiyat(e99)} | EMA200: {akilli_fiyat(e200)}\n"
                            f"📈 <b>Hacim Teyidi:</b> {curr_vol/avg_vol:.1f}x\n\n"
                            f"💡 <b>Açıklama:</b> Tüm EMA ortalamaları boğa dizilimine geçti."
                        )
                        if tg(C, msg):
                            son_ema_ribbon_sinyalleri[symbol] = time.time()
                    time.sleep(0.05)
            time.sleep(EMA_RIBBON_ARALIGI)
        except Exception as e:
            log("EMA_RIBBON HATA", e)
            time.sleep(60)


# =====================================================
# MOTOR 10: MACD GOLDEN CROSS (1d - TAZELİK & CANLI FİYAT TEYİTLİ)
# =====================================================
def macd_loop():
    log("MACD", "MACD Motoru Başladı.")
    time.sleep(105)
    while True:
        try:
            tickers, _ = get_futures_tickers()
            if tickers:
                adaylar = [
                    t for t in tickers
                    if t["symbol"].endswith("USDT")
                    and t["symbol"] not in YASAKLI_HISSELER
                    and float(t.get("quoteVolume", 0)) >= MIN_HACIM_MACD
                ]
                adaylar.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
                for coin in adaylar[:MAX_TARAMA]:
                    symbol = coin["symbol"]
                    if symbol in son_macd_sinyalleri and time.time() - son_macd_sinyalleri[symbol] < 86400:
                        continue
                    klines, ksrc = get_klines(symbol, "1d", 100, "FUTURES")
                    if not klines or len(klines) < 80:
                        continue
                    if not _bar_fresh(klines[-2], 86_400_000, 2 * 3600_000):
                        continue

                    closes_all = [float(k[4]) for k in klines]
                    live_price = closes_all[-1]
                    closes = closes_all[:-1]
                    vols = [float(k[7]) for k in klines[:-1]]
                    if len(vols) < 21:
                        continue
                    curr_price, prev_price, curr_vol = closes[-1], closes[-2], vols[-1]
                    avg_vol = sum(vols[-21:-1]) / 20.0
                    if avg_vol <= 0 or curr_vol <= (avg_vol * 1.1):
                        continue

                    macd_res = calculate_macd(closes)
                    if not macd_res:
                        continue
                    cm, cs, pm, ps = macd_res
                    if pm <= ps and cm > cs and live_price >= prev_price:
                        msg = (
                            f"🚀 <b>MACD SİNYALİ (LONG): {symbol}</b>\n"
                            f"<i>{ksrc} - 1 Günlük Grafik</i>\n\n"
                            f"💰 <b>Giriş (Kapanış):</b> {akilli_fiyat(curr_price)}\n"
                            f"⚡ <b>Anlık Fiyat:</b> {akilli_fiyat(live_price)}\n"
                            f"🎯 <b>Hedef:</b> {akilli_fiyat(curr_price * 1.10)} (+10.0%)\n"
                            f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(curr_price * 0.97)} (-3.0%)\n\n"
                            f"📊 <b>MACD:</b> {cm:.5f} | <b>Sinyal:</b> {cs:.5f}\n\n"
                            f"💡 <b>Açıklama:</b> MACD çizgisi sinyal çizgisini hacimle yukarı kesti (Golden Cross)."
                        )
                        if tg(C, msg):
                            son_macd_sinyalleri[symbol] = time.time()
                    time.sleep(0.05)
            time.sleep(MACD_ARALIGI)
        except Exception as e:
            log("MACD HATA", e)
            time.sleep(60)


# =====================================================
# MOTOR 11: 200 GÜNLÜK SMA KIRILIMI (DÜZELTİLMİŞ)
# =====================================================
def sma200_loop():
    log("SMA200", "200 Günlük SMA Kırılım Botu Başladı.")
    time.sleep(120)
    while True:
        try:
            tickers, _ = get_futures_tickers()
            if tickers:
                t_day = simdi().strftime("%Y-%m-%d")
                hedefler = [
                    x for x in tickers
                    if x["symbol"].endswith("USDT")
                    and x["symbol"] not in YASAKLI_HISSELER
                    and float(x.get("quoteVolume", 0)) >= MIN_HACIM_SMA200
                ]
                hedefler.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
                for coin in hedefler[:MAX_TARAMA]:
                    sym = coin["symbol"]
                    klines, ksrc = get_klines(sym, "1d", 210, "FUTURES")
                    if not klines or len(klines) < 203:
                        continue

                    # 1) Tazelik kontrolü: Günlük mum kapanalı en fazla 2 saat geçmiş olmalı
                    if not _bar_fresh(klines[-2], 86_400_000, 2 * 3600_000):
                        continue

                    closes_all = [float(k[4]) for k in klines]
                    live_price = closes_all[-1]
                    closes = closes_all[:-1]  # Sadece kapanmış mumlar
                    if len(closes) < 201:
                        continue

                    c_curr = closes[-1]
                    c_prev = closes[-2]
                    sma_curr = sum(closes[-200:]) / 200.0
                    sma_prev = sum(closes[-201:-1]) / 200.0
                    sma_live = sum(closes_all[-200:]) / 200.0
                    if sma_curr <= 0 or sma_live <= 0:
                        continue

                    # 2) Kapanışta kırılım + Anlık canlı fiyatın hâlâ kırılım yönünde olması şartı
                    long_cross = (c_prev <= sma_prev) and (c_curr > sma_curr) and (live_price > max(sma_curr, sma_live))
                    short_cross = (c_prev >= sma_prev) and (c_curr < sma_curr) and (live_price < min(sma_curr, sma_live))
                    if not (long_cross or short_cross):
                        continue

                    yon = "LONG" if long_cross else "SHORT"
                    key = f"{sym}_{yon}_{t_day}"
                    if key in son_sma200_sinyalleri:
                        continue

                    fark_pct = ((c_curr - sma_curr) / sma_curr) * 100.0
                    live_fark_pct = ((live_price - sma_live) / sma_live) * 100.0

                    if yon == "LONG":
                        baslik = f"🚀 <b>200 GÜNLÜK SMA KIRILIMI (LONG): {sym}</b>"
                        aciklama = "Günlük kapanış 200 günlük basit hareketli ortalamanın üzerine çıktı ve canlı fiyat üstünde kalıcı."
                    else:
                        baslik = f"🔻 <b>200 GÜNLÜK SMA KIRILIMI (SHORT): {sym}</b>"
                        aciklama = "Günlük kapanış 200 günlük basit hareketli ortalamanın altına indi ve canlı fiyat altında kalıcı."

                    msg = (
                        f"{baslik}\n"
                        f"<i>{ksrc} - 1 Günlük Grafik</i>\n\n"
                        f"💰 <b>Günlük Kapanış:</b> {akilli_fiyat(c_curr)}\n"
                        f"⚡ <b>Anlık Fiyat:</b> {akilli_fiyat(live_price)} ({live_fark_pct:+.2f}%)\n"
                        f"📉 <b>Önceki Kapanış:</b> {akilli_fiyat(c_prev)}\n"
                        f"📊 <b>Önceki 200 SMA:</b> {akilli_fiyat(sma_prev)}\n"
                        f"📊 <b>Güncel 200 SMA:</b> {akilli_fiyat(sma_curr)}\n"
                        f"📈 <b>Kapanış Farkı:</b> {fark_pct:+.2f}%\n\n"
                        f"💡 <b>Açıklama:</b> {aciklama}\n"
                        f"ℹ️ <i>Sinyal yalnızca yeni kapanmış günlük mum + canlı fiyat teyidiyle oluşturulmuştur.</i>"
                    )
                    if tg(C, msg):
                        son_sma200_sinyalleri[key] = True
                        log("SMA200", f"{sym} {yon} (kapanış={c_curr}, canlı={live_price})")
                    time.sleep(0.05)
            time.sleep(SMA200_ARALIGI)
        except Exception as e:
            log("SMA200 HATA", e)
            time.sleep(60)


# =====================================================
# MOTOR 12: TARİHİ ZİRVE / DİP KIRILIMI (ATH / ATL)
# =====================================================
def ath_atl_loop():
    log("ATH_ATL", "Tarihi Zirve/Dip Kırılım Motoru Başladı.")
    time.sleep(135)
    while True:
        try:
            tickers, _ = get_futures_tickers()
            if tickers:
                adaylar = [
                    t for t in tickers
                    if t["symbol"].endswith("USDT")
                    and t["symbol"] not in YASAKLI_HISSELER
                    and float(t.get("quoteVolume", 0)) >= MIN_HACIM_ATH_ATL
                ]
                adaylar.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
                for coin in adaylar[:MAX_TARAMA]:
                    symbol = coin["symbol"]
                    if symbol in son_ath_atl_sinyalleri and time.time() - son_ath_atl_sinyalleri[symbol] < 86400:
                        continue
                    klines, ksrc = get_klines(symbol, "1d", 500, "FUTURES")
                    if not klines or len(klines) < 202:
                        continue
                    if not _bar_fresh(klines[-2], 86_400_000, 2 * 3600_000):
                        continue

                    live_price = float(klines[-1][4])
                    closed = klines[:-1]
                    highs = [float(k[2]) for k in closed]
                    lows = [float(k[3]) for k in closed]
                    closes = [float(k[4]) for k in closed]
                    vols = [float(k[7]) for k in closed]
                    current_price, prev_close = closes[-1], closes[-2]
                    current_vol = vols[-1]
                    ath = max(highs[:-1])
                    atl = min(lows[:-1])
                    avg_vol = sum(vols[-21:-1]) / 20.0
                    if avg_vol <= 0 or current_vol <= (avg_vol * 1.2):
                        continue

                    sinyal = False
                    yon = ""
                    emoji = ""
                    stop = 0.0
                    hedef = 0.0
                    aciklama = ""

                    if prev_close >= atl and current_price < atl and live_price <= atl * 1.01:
                        sinyal = True; yon = "SHORT"; emoji = "💀"
                        stop = atl * 1.05; hedef = current_price * 0.80
                        aciklama = "Fiyat geçmişteki en düşük seviyeyi (Tarihi Dip) hacimle aşağı kırdı."
                    elif prev_close <= ath and current_price > ath and live_price >= ath * 0.99:
                        sinyal = True; yon = "LONG"; emoji = "🚀"
                        stop = ath * 0.95; hedef = current_price * 1.20
                        aciklama = "Fiyat geçmişteki en yüksek seviyeyi (Tarihi Zirve) hacimle yukarı kırdı."

                    if sinyal:
                        hedef_pct = ((hedef - current_price) / current_price) * 100.0
                        stop_pct = ((stop - current_price) / current_price) * 100.0
                        msg = (
                            f"{emoji} <b>TARİHİ SEVİYE KIRILIMI ({yon}): {symbol}</b>\n"
                            f"<i>{ksrc} - 1 Günlük Grafik</i>\n\n"
                            f"💰 <b>Giriş (Kapanış):</b> {akilli_fiyat(current_price)}\n"
                            f"⚡ <b>Anlık Fiyat:</b> {akilli_fiyat(live_price)}\n"
                            f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                            f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                            f"📊 <b>Kırılan Seviye:</b> {akilli_fiyat(ath if yon == 'LONG' else atl)}\n"
                            f"📈 <b>Hacim:</b> {current_vol/avg_vol:.1f}x\n\n"
                            f"💡 <b>Açıklama:</b> {aciklama}"
                        )
                        if tg(C, msg):
                            son_ath_atl_sinyalleri[symbol] = time.time()
                            log("ATH_ATL", f"🔥 {symbol} ({yon})")
                    time.sleep(0.05)
            time.sleep(ATH_ATL_ARALIGI)
        except Exception as e:
            log("ATH_ATL HATA", e)
            time.sleep(60)


# =====================================================
# MOTOR 13: GRAFİK FORMASYONLARI (4h / 1d)
# =====================================================
def chart_patterns_loop():
    log("PATTERN", "Grafik Formasyon Avcısı Başladı (4h/1d).")
    time.sleep(150)
    while True:
        try:
            tickers, _ = get_futures_tickers()
            if tickers:
                adaylar = [
                    t for t in tickers
                    if t["symbol"].endswith("USDT")
                    and t["symbol"] not in YASAKLI_HISSELER
                    and float(t.get("quoteVolume", 0)) >= MIN_HACIM_PATTERN
                ]
                adaylar.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
                for coin in adaylar[:MAX_TARAMA]:
                    symbol = coin["symbol"]
                    if symbol in son_chart_pattern_sinyalleri and time.time() - son_chart_pattern_sinyalleri[symbol] < 14400:
                        continue
                    for tf in ("4h", "1d"):
                        klines, ksrc = get_klines(symbol, tf, 100, "FUTURES")
                        if not klines or len(klines) < 50:
                            continue
                        highs = [float(k[2]) for k in klines]
                        lows = [float(k[3]) for k in klines]
                        closes = [float(k[4]) for k in klines]
                        vols = [float(k[7]) for k in klines]

                        curr_close, live_price = closes[-2], closes[-1]
                        curr_vol = vols[-2]
                        avg_vol = sum(vols[-22:-2]) / 20.0
                        if avg_vol <= 0 or curr_vol < avg_vol * 1.2:
                            continue

                        p_highs, p_lows = find_pivots(highs[:-1], lows[:-1])
                        sinyal = False
                        formasyon_adi = ""
                        yon = ""
                        emoji = ""
                        hedef = 0.0
                        stop = 0.0
                        neckline = 0.0

                        if len(p_highs) >= 2:
                            p1i, p1p = p_highs[-2]
                            p2i, p2p = p_highs[-1]
                            if 5 <= (p2i - p1i) <= 30 and abs(p1p - p2p) / p1p <= 0.02:
                                neckline = min(lows[p1i:p2i + 1])
                                if curr_close < neckline * 0.99 and live_price < neckline:
                                    sinyal = True; formasyon_adi = "İkili Tepe (M)"; yon = "SHORT"; emoji = "🔻"
                                    stop = max(p1p, p2p) * 1.02
                                    hedef = neckline - (max(p1p, p2p) - neckline)

                        if not sinyal and len(p_lows) >= 2:
                            p1i, p1p = p_lows[-2]
                            p2i, p2p = p_lows[-1]
                            if 5 <= (p2i - p1i) <= 30 and abs(p1p - p2p) / p1p <= 0.02:
                                neckline = max(highs[p1i:p2i + 1])
                                if curr_close > neckline * 1.01 and live_price > neckline:
                                    sinyal = True; formasyon_adi = "İkili Dip (W)"; yon = "LONG"; emoji = "🚀"
                                    stop = min(p1p, p2p) * 0.98
                                    hedef = neckline + (neckline - min(p1p, p2p))

                        if not sinyal and len(p_highs) >= 3:
                            p1i, p1p = p_highs[-3]
                            p2i, p2p = p_highs[-2]
                            p3i, p3p = p_highs[-1]
                            if p2p > p1p and p2p > p3p and abs(p1p - p3p) / p1p <= 0.03:
                                neckline = min(lows[p1i:p3i + 1])
                                if curr_close < neckline * 0.99 and live_price < neckline:
                                    sinyal = True; formasyon_adi = "Omuz-Baş-Omuz"; yon = "SHORT"; emoji = "💀"
                                    stop = p3p * 1.02
                                    hedef = neckline - (p2p - neckline)

                        if not sinyal and len(p_lows) >= 3:
                            p1i, p1p = p_lows[-3]
                            p2i, p2p = p_lows[-2]
                            p3i, p3p = p_lows[-1]
                            if p2p < p1p and p2p < p3p and abs(p1p - p3p) / p1p <= 0.03:
                                neckline = max(highs[p1i:p3i + 1])
                                if curr_close > neckline * 1.01 and live_price > neckline:
                                    sinyal = True; formasyon_adi = "Ters Omuz-Baş-Omuz"; yon = "LONG"; emoji = "🚀"
                                    stop = p3p * 0.98
                                    hedef = neckline + (neckline - p2p)

                        if sinyal:
                            hedef_pct = ((hedef - curr_close) / curr_close) * 100.0
                            stop_pct = ((stop - curr_close) / curr_close) * 100.0
                            if yon == "SHORT" and (hedef >= curr_close or curr_close < hedef * 1.01):
                                continue
                            if yon == "LONG" and (hedef <= curr_close or curr_close > hedef * 0.99):
                                continue
                            if abs(stop_pct) > 15.0 or abs(hedef_pct) < 1.5 or abs(hedef_pct) > 40.0:
                                continue

                            msg = (
                                f"{emoji} <b>GRAFİK FORMASYONU ({yon}): {symbol}</b>\n"
                                f"<i>{ksrc} | {tf.upper()} Grafik - {formasyon_adi}</i>\n\n"
                                f"💰 <b>Giriş (Kapanış):</b> {akilli_fiyat(curr_close)}\n"
                                f"⚡ <b>Anlık Fiyat:</b> {akilli_fiyat(live_price)}\n"
                                f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                                f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                                f"📊 <b>Boyun Çizgisi:</b> {akilli_fiyat(neckline)}\n"
                                f"📈 <b>Hacim Teyidi:</b> {curr_vol/avg_vol:.1f}x\n\n"
                                f"💡 <b>Açıklama:</b> Fiyat formasyonu tamamlayıp boyun çizgisini hacimle kırdı."
                            )
                            if tg(C, msg):
                                son_chart_pattern_sinyalleri[symbol] = time.time()
                                log("PATTERN", f"🔥 {symbol} ({yon} - {formasyon_adi})")
                                break
                        time.sleep(0.05)
            time.sleep(CHART_PATTERN_ARALIGI)
        except Exception as e:
            log("PATTERN HATA", e)
            time.sleep(60)


# =====================================================
# MOTOR 14: ANLIK ALL TIME LOW / HIGH + GRAFİK RESMİ
# =====================================================
def anlik_ath_atl_loop():
    log("ANLIK_ATH_ATL", "Anlık All-Time High/Low + Grafik Motoru Başladı.")
    time.sleep(160)
    while True:
        try:
            tickers, _ = get_futures_tickers()
            if tickers:
                adaylar = [
                    t for t in tickers
                    if t["symbol"].endswith("USDT")
                    and t["symbol"] not in YASAKLI_HISSELER
                    and float(t.get("quoteVolume", 0)) >= MIN_HACIM_ANLIK_ATH_ATL
                ]
                adaylar.sort(key=lambda x: abs(float(x["priceChangePercent"])), reverse=True)
                for coin in adaylar[:MAX_TARAMA]:
                    symbol = coin["symbol"]
                    klines, ksrc = get_klines(symbol, "1d", 500, "FUTURES")
                    if not klines or len(klines) < 2:
                        continue

                    past_highs = [float(k[2]) for k in klines[:-1]]
                    past_lows = [float(k[3]) for k in klines[:-1]]
                    hist_ath = max(past_highs)
                    hist_atl = min(past_lows)

                    live_price = float(coin["lastPrice"]) or float(klines[-1][4])
                    pct_24h = float(coin["priceChangePercent"])
                    vol_24h = float(coin["quoteVolume"])

                    yon = None
                    if live_price <= hist_atl:
                        yon = "ATL"
                    elif live_price >= hist_ath:
                        yon = "ATH"

                    if not yon:
                        continue

                    last_sent = son_anlik_ath_atl_sinyalleri.get(f"{symbol}_{yon}", 0)
                    if time.time() - last_sent < 43200:
                        continue

                    if yon == "ATL":
                        caption = (
                            f"#{symbol} ${akilli_fiyat(live_price)} all time low 👎🚨😭\n"
                            f"24h {pct_24h:+.2f}% Vol {format_vol(vol_24h)}\n"
                            f"<i>{ksrc} | Önceki Dip: ${akilli_fiyat(hist_atl)}</i>"
                        )
                    else:
                        caption = (
                            f"#{symbol} ${akilli_fiyat(live_price)} all time high 🚀🔥🎉\n"
                            f"24h {pct_24h:+.2f}% Vol {format_vol(vol_24h)}\n"
                            f"<i>{ksrc} | Önceki Zirve: ${akilli_fiyat(hist_ath)}</i>"
                        )

                    png = render_chart_png(symbol, klines, "D1")
                    if tg_photo(C, png, caption):
                        son_anlik_ath_atl_sinyalleri[f"{symbol}_{yon}"] = time.time()
                        log("ANLIK_ATH_ATL", f"🔥 {symbol} {yon} @ {live_price}")
                    time.sleep(0.05)
            time.sleep(ANLIK_ATH_ATL_ARALIGI)
        except Exception as e:
            log("ANLIK_ATH_ATL HATA", e)
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
            if tickers:
                adaylar = [
                    t for t in tickers
                    if t["symbol"].endswith("USDT")
                    and t["symbol"] not in YASAKLI_HISSELER
                    and float(t.get("quoteVolume", 0)) >= MIN_HACIM_TOP_MOVERS
                ]
                adaylar.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
                sonuclar = []
                for coin in adaylar[:MAX_TARAMA]:
                    sym = coin["symbol"]
                    klines, _ = get_klines(sym, "1h", 3, "FUTURES")
                    if not klines or len(klines) < 2:
                        continue
                    closed_1h = klines[-2]
                    if not _bar_fresh(closed_1h, 3600_000, 90 * 60_000):
                        continue
                    op, cl = float(closed_1h[1]), float(closed_1h[4])
                    if op > 0:
                        sonuclar.append({
                            "symbol": sym.replace("USDT", ""),
                            "chg": ((cl - op) / op) * 100.0,
                            "price": cl,
                        })

                if len(sonuclar) >= 6:
                    sonuclar.sort(key=lambda x: x["chg"], reverse=True)
                    up = sonuclar[:3]
                    down = list(reversed(sonuclar[-3:]))

                    msg = f"⏱️ <b>Son 1 Saatin Hareketlileri</b> ({src})\n\n🟢 <b>En Çok Yükselenler:</b>\n"
                    for i, x in enumerate(up, 1):
                        msg += f"{i}. <b>{x['symbol']}</b> {x['chg']:+.2f}% | {akilli_fiyat(x['price'])}\n"
                    msg += "\n🔴 <b>En Çok Düşenler:</b>\n"
                    for i, x in enumerate(down, 1):
                        msg += f"{i}. <b>{x['symbol']}</b> {x['chg']:+.2f}% | {akilli_fiyat(x['price'])}\n"

                    if tg(C, msg):
                        log("TOP_MOVERS", "Saatlik hareketliler raporu gönderildi.")
            time.sleep(TOP_MOVERS_ARALIGI)
        except Exception as e:
            log("TOP_MOVERS HATA", e)
            time.sleep(60)


# =====================================================
# RENDER WEB SUNUCUSU VE CANLI DURUM PANELİ
# =====================================================
class Handler(http.server.BaseHTTPRequestHandler):
    def _build_status(self):
        with _lock:
            errs = dict(_source_last_error)
        tg_str = datetime.fromtimestamp(_last_top_gainers_ts, TR_Saati).strftime("%H:%M:%S") if _last_top_gainers_ts else "Henüz yok"
        dom_str = datetime.fromtimestamp(_last_dominans_ts, TR_Saati).strftime("%H:%M:%S") if _last_dominans_ts else "Henüz yok"
        lines = [
            f"Ultimate Futures Bot Aktif! ({simdi().strftime('%Y-%m-%d %H:%M:%S')} TSI)",
            f"Motor Sayisi: 15 | MAX_TARAMA: {MAX_TARAMA}",
            f"Son TOP_GAINERS: {tg_str} | Son DOMINANS: {dom_str}",
            "Kaynak Durumlari: " + (", ".join(f"{k}={v}" for k, v in errs.items()) or "Baslatiliyor"),
        ]
        return ("\n".join(lines)).encode("utf-8")

    def do_GET(self):
        body = self._build_status()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        body = self._build_status()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

    def log_message(self, format_string, *args):
        return


def run_web_server():
    port = int(os.environ.get("PORT", "10000"))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    log("SİSTEM", f"WEB SUNUCUSU BAŞLADI - PORT: {port}")
    server.serve_forever()


# =====================================================
# BAŞLAT
# =====================================================
if __name__ == "__main__":
    log("SİSTEM", "15 Motorlu Zırhlı Sinyal Botu Başlatılıyor...")
    if not T:
        log("UYARI", "BOT_TOKEN ortam değişkeni boş! Telegram mesajı gönderilemez.")

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
    ]

    for name, fn in motors:
        threading.Thread(target=fn, name=name, daemon=True).start()

    try:
        run_web_server()
    except Exception as e:
        log("RENDER", f"Web sunucusu hatası: {e}")
        log("RENDER", traceback.format_exc())
        while True:
            time.sleep(60)

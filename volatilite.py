import urllib.request
import urllib.parse
import urllib.error
import json
import time
import socket
import os
import threading
import http.server
import traceback
from datetime import datetime, timedelta, timezone

# =====================================================
# BINANCE USDT-M FUTURES TELEGRAM SIGNAL BOT
# Yalnızca sinyal üretir; otomatik emir göndermez.
# =====================================================

socket.setdefaulttimeout(20)

T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()
PROXY_URL = os.environ.get("PROXY_URL", "").strip()

TR_Saati = timezone(timedelta(hours=3))
FAPI = "https://fapi.binance.com"
DATA_API = "https://data-api.binance.vision"

_binance_opener = (
    urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY_URL, "https": PROXY_URL})
    )
    if PROXY_URL
    else urllib.request.build_opener()
)

# Motor aralıkları (saniye)
CHARTIST_ARALIGI = 3600
PUMP_ARALIGI = 120
VOLATILITE_ARALIGI = 240
FORMASYON_ARALIGI = 3600
EMA_RIBBON_ARALIGI = 3600
MACD_ARALIGI = 3600
ATH_ATL_ARALIGI = 3600
ICHIMOKU_ARALIGI = 3600
CHART_PATTERN_ARALIGI = 1800
SHORT_GAINERS_ARALIGI = 300
TOP_MOVERS_ARALIGI = 3600
DOMINANS_ARALIGI = 14400
TOP_GAINERS_ARALIGI = 14400
PUMP_WINNER_ARALIGI = 21600

# Minimum 24 saatlik quote hacimleri (USDT)
MIN_HACIM_CHARTIST = 10_000_000
MIN_HACIM_PUMP = 5_000_000
MIN_HACIM_VOLATILITE = 10_000_000
MIN_HACIM_FORMASYON = 15_000_000
MIN_HACIM_EMA_RIBBON = 10_000_000
MIN_HACIM_MACD = 10_000_000
MIN_HACIM_ATH_ATL = 5_000_000
MIN_HACIM_ICHIMOKU = 10_000_000
MIN_HACIM_PATTERN = 15_000_000
MIN_HACIM_MOVERS = 5_000_000

# Her motor en likit ilk N sözleşmeyi tarar. API limitlerini korur.
MAX_TARAMA = 60
MAX_CHARTIST = 50
MAX_PATTERN = 45
MAX_FORMASYON = 50
MAX_ATH_ATL = 50

YASAKLI_HISSELER = {
    "AAPLUSDT", "TSLAUSDT", "NVDAUSDT", "COINUSDT", "MSTRUSDT",
    "SQQQUSDT", "TQQQUSDT", "SPYUSDT", "QQQUSDT", "AMZNUSDT",
    "GOOGLUSDT", "METAUSDT", "NFLXUSDT", "BABAUSDT", "MSFTUSDT",
    "HOODUSDT", "CRCLUSDT", "XAUUSDT", "XAGUSDT", "PAXGUSDT"
}

# Sinyal hafızaları
son_sinyaller = {}
son_pump_sinyalleri = {}
son_volatilite_sinyalleri = {}
son_formasyon_sinyalleri = {}
son_ema_ribbon_sinyalleri = {}
son_macd_sinyalleri = {}
son_ath_atl_sinyalleri = {}
son_ichimoku_sinyalleri = {}
son_chart_pattern_sinyalleri = {}
son_short_gainers_sinyalleri = {}

# Paylaşılan veri önbellekleri ve API hız kontrolü
_api_locks = {"fapi": threading.Lock(), "data": threading.Lock()}
_last_api_call = {"fapi": 0.0, "data": 0.0}
_api_block_until = {"fapi": 0.0, "data": 0.0}

_ticker_locks = {"fapi": threading.Lock(), "data": threading.Lock()}
_ticker_cache = {
    "fapi": {"time": 0.0, "data": None},
    "data": {"time": 0.0, "data": None},
}
_symbol_lock = threading.Lock()
_symbol_cache = {"time": 0.0, "symbols": set()}

_cache_lock = threading.Lock()
_kline_cache = {}

_status = {
    "telegram": "Kontrol ediliyor...",
    "fapi": "Bekleniyor",
    "data_api": "Bekleniyor",
    "last_top_gainers": "Henüz gönderilmedi",
}

API_MIN_GAP = 0.25
KLINE_TTL_MAP = {
    "5m": 90,
    "15m": 210,
    "1h": 600,
    "4h": 1800,
    "1d": 3600,
    "3d": 3600,
}


def simdi():
    return datetime.now(TR_Saati)


def log(prefix, message):
    print(f"[{simdi().strftime('%H:%M:%S')}] [{prefix}] {message}", flush=True)


def api_blocked(market):
    return time.time() < _api_block_until[market]


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


def tg(chat_id, message):
    if not T or not chat_id:
        log("TELEGRAM", "BOT_TOKEN veya CHAT_ID boş olduğu için gönderilmedi.")
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
        with urllib.request.urlopen(req, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
            if not result.get("ok"):
                log("TELEGRAM", f"Gönderim reddedildi: {result}")
                _status["telegram"] = f"Reddedildi: {result}"
            return result.get("ok", False)
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            pass
        log("TELEGRAM", f"HTTP {e.code} hatası: {err_body}")
        _status["telegram"] = f"HTTP {e.code}: {err_body}"
        return False
    except Exception as e:
        log("TELEGRAM", f"Gönderim hatası: {e}")
        _status["telegram"] = f"Hata: {e}"
        return False


def _cleanup_kline_cache(now):
    if len(_kline_cache) <= 2000:
        return
    expired = [k for k, v in _kline_cache.items() if now - v[0] > 3600]
    for k in expired:
        _kline_cache.pop(k, None)
    if len(_kline_cache) > 2000:
        _kline_cache.clear()


def gj(url, cache_key=None, cache_ttl=0):
    """Binance JSON isteği. 418/429 durumunda yedek DATA_API devreye girer."""
    market = "fapi" if url.startswith(FAPI) else "data"

    if cache_key and cache_ttl:
        with _cache_lock:
            item = _kline_cache.get(cache_key)
            if item and time.time() - item[0] < cache_ttl:
                return item[1]

    with _api_locks[market]:
        now = time.time()
        if now < _api_block_until[market]:
            return None
        wait = API_MIN_GAP - (now - _last_api_call[market])
        if wait > 0:
            time.sleep(wait)
        _last_api_call[market] = time.time()

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        )
        with _binance_opener.open(req, timeout=20) as response:
            used_weight = response.headers.get("X-MBX-USED-WEIGHT-1M")
            weight_limit = 1600 if market == "fapi" else 4500
            if used_weight and used_weight.isdigit() and int(used_weight) >= weight_limit:
                with _api_locks[market]:
                    _api_block_until[market] = max(_api_block_until[market], time.time() + 45)
                log("BINANCE", f"[{market.upper()}] Yüksek API weight ({used_weight}); 45 saniye yavaşlatıldı.")
            result = json.loads(response.read().decode("utf-8"))

        _status["fapi" if market == "fapi" else "data_api"] = "OK"
        if cache_key and cache_ttl:
            with _cache_lock:
                _cleanup_kline_cache(time.time())
                _kline_cache[cache_key] = (time.time(), result)
        return result

    except urllib.error.HTTPError as e:
        if e.code in (418, 429):
            retry_after = e.headers.get("Retry-After")
            try:
                pause = max(120, int(retry_after) + 5) if retry_after else 300
            except Exception:
                pause = 600
            with _api_locks[market]:
                already_blocked = time.time() < _api_block_until[market]
                _api_block_until[market] = max(_api_block_until[market], time.time() + pause)
            _status["fapi" if market == "fapi" else "data_api"] = f"HTTP {e.code} ({pause}s)"
            if not already_blocked:
                yedek = " (DATA_API yedeğine geçildi)" if market == "fapi" else ""
                log("BINANCE", f"[{market.upper()}] HTTP {e.code}; {pause}s durduruldu{yedek}.")
        elif e.code == 451:
            with _api_locks[market]:
                already_blocked = time.time() < _api_block_until[market]
                _api_block_until[market] = max(_api_block_until[market], time.time() + 3600)
            _status["fapi" if market == "fapi" else "data_api"] = "HTTP 451"
            if not already_blocked:
                log("BINANCE", f"[{market.upper()}] HTTP 451 (Bölge engeli); DATA_API yedeği kullanılacak.")
        else:
            log("BINANCE", f"[{market.upper()}] HTTP {e.code}: {url.split('?')[0]}")
        return None
    except Exception as e:
        _status["fapi" if market == "fapi" else "data_api"] = f"Hata: {e}"
        log("BINANCE", f"[{market.upper()}] Bağlantı/veri hatası: {e}")
        return None


def aktif_usdt_perpetual():
    """Sadece TRADING durumundaki USDT PERPETUAL sözleşmeleri."""
    with _symbol_lock:
        if time.time() - _symbol_cache["time"] < 21600 and _symbol_cache["symbols"]:
            return _symbol_cache["symbols"]

        data = gj(f"{FAPI}/fapi/v1/exchangeInfo")
        if not isinstance(data, dict):
            return _symbol_cache["symbols"] or set()

        symbols = set()
        for item in data.get("symbols", []):
            symbol = item.get("symbol", "")
            if (item.get("status") == "TRADING"
                    and item.get("contractType") == "PERPETUAL"
                    and item.get("quoteAsset") == "USDT"
                    and symbol.endswith("USDT")
                    and symbol not in YASAKLI_HISSELER
                    and not any(x in symbol for x in ("3L", "3S", "5L", "5S"))):
                symbols.add(symbol)

        _symbol_cache["time"] = time.time()
        _symbol_cache["symbols"] = symbols
        log("SİSTEM", f"Aktif USDT-M PERPETUAL sözleşme sayısı: {len(symbols)}")
        return symbols


def _active_source():
    if not api_blocked("fapi"):
        return "fapi"
    if not api_blocked("data"):
        return "data"
    return "fapi"


def futures_tickers(force=False):
    src = _active_source()
    with _ticker_locks[src]:
        if not force and _ticker_cache[src]["data"] is not None and time.time() - _ticker_cache[src]["time"] < 55:
            return _ticker_cache[src]["data"]

        url = f"{FAPI}/fapi/v1/ticker/24hr" if src == "fapi" else f"{DATA_API}/api/v3/ticker/24hr"
        data = gj(url)

        if not isinstance(data, list) and src == "fapi" and not api_blocked("data"):
            src = "data"
            with _ticker_locks["data"]:
                if not force and _ticker_cache["data"]["data"] is not None and time.time() - _ticker_cache["data"]["time"] < 55:
                    return _ticker_cache["data"]["data"]
                data = gj(f"{DATA_API}/api/v3/ticker/24hr")

        if not isinstance(data, list):
            return _ticker_cache[src]["data"] or []

        active = aktif_usdt_perpetual() if src == "fapi" else None
        filtered = []
        for t in data:
            symbol = t.get("symbol", "")
            if src == "fapi":
                if active and symbol not in active:
                    continue
            else:
                if (not symbol.endswith("USDT")
                        or symbol in YASAKLI_HISSELER
                        or any(x in symbol for x in ("3L", "3S", "5L", "5S", "UPUSDT", "DOWNUSDT"))):
                    continue
            try:
                quote_vol = float(t.get("quoteVolume", 0))
                price = float(t.get("lastPrice", 0))
                if quote_vol <= 0 or price <= 0:
                    continue
                t["_change"] = float(t.get("priceChangePercent", 0))
                t["_quote"] = quote_vol
                t["_price"] = price
                filtered.append(t)
            except (TypeError, ValueError):
                continue

        _ticker_cache[src]["time"] = time.time()
        _ticker_cache[src]["data"] = filtered
        return filtered


def adaylar(min_hacim, limit=MAX_TARAMA):
    rows = [t for t in futures_tickers() if t["_quote"] >= min_hacim]
    rows.sort(key=lambda x: x["_quote"], reverse=True)
    return rows[:limit]


def klines(symbol, interval, limit):
    src = _active_source()
    key = f"{src}:{symbol}:{interval}"
    ttl = KLINE_TTL_MAP.get(interval, 60)

    with _cache_lock:
        item = _kline_cache.get(key)
        if item and time.time() - item[0] < ttl and isinstance(item[1], list) and len(item[1]) >= limit:
            return item[1][-limit:]

    fetch_limit = max(limit, 230) if interval == "1h" else limit
    url = (
        f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={fetch_limit}"
        if src == "fapi"
        else f"{DATA_API}/api/v3/klines?symbol={symbol}&interval={interval}&limit={fetch_limit}"
    )
    data = gj(url, cache_key=key, cache_ttl=ttl)

    if not isinstance(data, list) and src == "fapi" and not api_blocked("data"):
        key = f"data:{symbol}:{interval}"
        with _cache_lock:
            item = _kline_cache.get(key)
            if item and time.time() - item[0] < ttl and isinstance(item[1], list) and len(item[1]) >= limit:
                return item[1][-limit:]
        data = gj(
            f"{DATA_API}/api/v3/klines?symbol={symbol}&interval={interval}&limit={fetch_limit}",
            cache_key=key,
            cache_ttl=ttl
        )

    if isinstance(data, list) and len(data) > limit:
        return data[-limit:]
    return data


def tamamlanmis(ks):
    """Açık olan son mumu çıkarır."""
    return ks[:-1] if isinstance(ks, list) and len(ks) >= 2 else []


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
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


def calculate_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow + signal + 2:
        return None, None, None, None
    ef = calculate_ema(prices, fast)
    es = calculate_ema(prices, slow)
    if not ef or not es:
        return None, None, None, None
    offset = slow - fast
    macd_line = [ef[i + offset] - es[i] for i in range(len(es))]
    sig = calculate_ema(macd_line, signal)
    if len(sig) < 2:
        return None, None, None, None
    return macd_line[-1], sig[-1], macd_line[-2], sig[-2]


def trap_radar_check(symbol):
    if api_blocked("fapi"):
        return ""
    oi = gj(f"{FAPI}/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=2")
    taker = gj(f"{FAPI}/futures/data/takerlongshortRatio?symbol={symbol}&period=5m&limit=1")
    if oi is None and taker is None:
        return ""
    oi_up = False
    buyers = False
    try:
        if isinstance(oi, list) and len(oi) >= 2:
            old = float(oi[-2]["sumOpenInterest"])
            new = float(oi[-1]["sumOpenInterest"])
            oi_up = old > 0 and (new - old) / old * 100 > 0.2
        if isinstance(taker, list) and taker:
            buy = float(taker[-1]["buyVol"])
            sell = float(taker[-1]["sellVol"])
            buyers = buy + sell > 0 and buy / (buy + sell) > 0.55
    except (KeyError, TypeError, ValueError):
        pass
    if oi_up and buyers:
        return "🐋 <b>Açık pozisyon ve alıcı hacmi artıyor</b>"
    if not buyers:
        return "⚠️ <b>Alıcı teyidi yok; pump tuzak olabilir</b>"
    return "⚖️ <b>Kısmi teyit</b>"


def find_pivots(highs, lows, window=3):
    ph, pl = [], []
    for i in range(window, len(highs) - window):
        if highs[i] == max(highs[i - window:i + window + 1]):
            ph.append((i, highs[i]))
        if lows[i] == min(lows[i - window:i + window + 1]):
            pl.append((i, lows[i]))
    return ph, pl


def cooldown_ok(store, key, seconds):
    return time.time() - store.get(key, 0) >= seconds


def signal_sent(store, key):
    store[key] = time.time()


def make_signal(title, symbol, direction, entry, target, stop, detail=""):
    tp_pct = (target - entry) / entry * 100
    sl_pct = (stop - entry) / entry * 100
    emoji = "🚀" if direction == "LONG" else "🔻"
    return (
        f"{emoji} <b>{title} ({direction}): {symbol}</b>\n\n"
        f"💰 <b>Giriş:</b> {akilli_fiyat(entry)}\n"
        f"🎯 <b>Hedef:</b> {akilli_fiyat(target)} ({tp_pct:+.1f}%)\n"
        f"🛑 <b>Stop:</b> {akilli_fiyat(stop)} ({sl_pct:+.1f}%)\n"
        f"{detail}"
    )


def safe_loop(name, interval, worker, startup=5):
    log(name, "Motor başlatıldı.")
    time.sleep(startup)
    while True:
        started = time.time()
        try:
            worker()
        except Exception as e:
            log(name, f"Hata: {e}")
            log(name, traceback.format_exc()[-1200:])
        elapsed = time.time() - started
        time.sleep(max(5, interval - elapsed))


# =====================================================
# MOTOR 1: TOP GAINERS - FUTURES
# =====================================================
def top_gainers_worker():
    rows = [t for t in futures_tickers() if t["_quote"] > 5_000_000]
    rows.sort(key=lambda x: x["_change"], reverse=True)
    rows = [t for t in rows if t["_change"] > 0][:5]
    if not rows:
        log("TOP_GAINERS", "Uygun coin verisi alınamadı.")
        return
    msg = "🔥 <b>Günün En İyi Hareketleri (Binance Futures)</b>\n"
    msg += "<i>24 saatlik yükseliş ve USDT hacmine göre</i>\n\n"
    medals = ["🥇", "🥈", "🥉", "🏅", "⭐"]
    for i, t in enumerate(rows):
        msg += f"{medals[i]} <b>{t['symbol'].replace('USDT','')}</b> +{t['_change']:.2f}% | {akilli_fiyat(t['_price'])}\n"
    if tg(C, msg):
        _status["last_top_gainers"] = simdi().strftime("%H:%M:%S")
        log("TOP_GAINERS", "Telegram'a gönderildi.")


def top_gainers_loop():
    safe_loop("TOP_GAINERS", TOP_GAINERS_ARALIGI, top_gainers_worker, 8)


# =====================================================
# MOTOR 2: PUMP WINNER - FUTURES
# =====================================================
def pump_winner_worker():
    rows = [t for t in futures_tickers() if t["_quote"] > 10_000_000]
    rows.sort(key=lambda x: x["_change"], reverse=True)
    if not rows or rows[0]["_change"] < 15:
        return
    t = rows[0]
    low = float(t.get("lowPrice") or 0)
    high = float(t.get("highPrice") or 0)
    if low <= 0 or high <= 0:
        return
    move = (high - low) / low * 100
    msg = (
        f"🏆 <b>GÜNÜN KAZANANI: {t['symbol']}</b>\n\n"
        f"🔥 Binance USDT-M Futures 24 saatlik en güçlü yükseliş\n"
        f"📈 <b>24s değişim:</b> +{t['_change']:.2f}%\n"
        f"📉 <b>Gün içi dip:</b> {akilli_fiyat(low)}\n"
        f"🚀 <b>Gün içi zirve:</b> {akilli_fiyat(high)}\n"
        f"📊 <b>Dip-zirve hareketi:</b> {move:.1f}%\n"
        f"💰 <b>24s USDT hacmi:</b> {t['_quote']:,.0f}"
    )
    tg(C, msg)


def pump_winner_loop():
    safe_loop("PUMP_WINNER", PUMP_WINNER_ARALIGI, pump_winner_worker, 20)


# =====================================================
# MOTOR 3: CHARTIST - SAATLİK EMA200 LONG / SHORT
# =====================================================
def chartist_worker():
    for t in adaylar(MIN_HACIM_CHARTIST, MAX_CHARTIST):
        symbol = t["symbol"]
        if not cooldown_ok(son_sinyaller, symbol, 86400):
            continue
        ks = tamamlanmis(klines(symbol, "1h", 230))
        if len(ks) < 205:
            continue
        closes = [float(k[4]) for k in ks]
        vols = [float(k[7]) for k in ks]
        ema = calculate_ema(closes, 200)
        if len(ema) < 2:
            continue
        curr, prev = closes[-1], closes[-2]
        e_now, e_prev = ema[-1], ema[-2]
        avg_vol = sum(vols[-21:-1]) / 20
        if avg_vol <= 0 or vols[-1] <= avg_vol:
            continue

        direction = None
        if prev <= e_prev and curr > e_now * 1.01:
            direction = "LONG"
        elif prev >= e_prev and curr < e_now * 0.99:
            direction = "SHORT"
        if not direction:
            continue

        if direction == "LONG":
            stop, target = e_now * 0.97, curr * 1.15
        else:
            stop, target = e_now * 1.03, curr * 0.85
        if abs((stop - curr) / curr * 100) > 12:
            continue
        msg = make_signal(
            "SAATLİK EMA200 KIRILIMI", symbol, direction, curr, target, stop,
            f"\n📊 <b>EMA200:</b> {akilli_fiyat(e_now)}\n"
            f"📈 <b>Hacim:</b> Ortalama hacmin {vols[-1]/avg_vol:.1f}x'i\n"
            f"⏱️ <b>Zaman dilimi:</b> 1 saat"
        )
        if tg(C, msg):
            signal_sent(son_sinyaller, symbol)
            log("CHARTIST", f"{symbol} {direction}")


def chartist_loop():
    safe_loop("CHARTIST", CHARTIST_ARALIGI, chartist_worker, 35)


# =====================================================
# MOTOR 4: PUMP AVCISI
# =====================================================
def pump_worker():
    for t in adaylar(MIN_HACIM_PUMP, MAX_TARAMA):
        symbol = t["symbol"]
        if not cooldown_ok(son_pump_sinyalleri, symbol, 900):
            continue
        ks = tamamlanmis(klines(symbol, "5m", 4))
        if len(ks) < 3:
            continue
        curr_k, prev_k = ks[-1], ks[-2]
        pc, cc = float(prev_k[4]), float(curr_k[4])
        pv, cv = float(prev_k[7]), float(curr_k[7])
        if pc <= 0 or pv <= 0:
            continue
        price_change = (cc - pc) / pc * 100
        vol_change = (cv - pv) / pv * 100
        vol_diff = cv - pv
        if price_change >= 3 and vol_change >= 100 and vol_diff >= 50_000:
            radar = trap_radar_check(symbol)
            radar_block = f"\n\n{radar}\n" if radar else "\n"
            msg = (
                f"🚀 <b>PUMP AVCISI: {symbol}</b>\n"
                f"💰 <b>Fiyat:</b> {akilli_fiyat(cc)} ({price_change:+.2f}%)\n"
                f"📊 <b>5m hacim artışı:</b> {vol_change:+.0f}%\n"
                f"💵 <b>Ek hacim:</b> {vol_diff:,.0f} USDT{radar_block}"
                f"\n⚠️ <i>Bu bir işlem emri değil, hareket uyarısıdır.</i>"
            )
            if tg(C, msg):
                signal_sent(son_pump_sinyalleri, symbol)
                log("PUMP", f"{symbol} pump uyarısı")


def pump_loop():
    safe_loop("PUMP", PUMP_ARALIGI, pump_worker, 55)


# =====================================================
# MOTOR 5: ANORMAL VOLATİLİTE / DUMP
# =====================================================
def volatilite_worker():
    for t in adaylar(MIN_HACIM_VOLATILITE, MAX_TARAMA):
        symbol = t["symbol"]
        if not cooldown_ok(son_volatilite_sinyalleri, symbol, 1800):
            continue
        ks = tamamlanmis(klines(symbol, "15m", 26))
        if len(ks) < 22:
            continue
        curr = ks[-1]
        op, close, volume = float(curr[1]), float(curr[4]), float(curr[7])
        avg = sum(float(k[7]) for k in ks[-21:-1]) / 20
        if op <= 0 or avg <= 0:
            continue
        change = (close - op) / op * 100
        mult = volume / avg
        if abs(change) >= 2.5 and mult >= 3:
            direction = "DUMP (ANI DÜŞÜŞ) 🔻" if change < 0 else "PUMP (ANI YÜKSELİŞ) 🚀"
            msg = (
                f"🚨 <b>ANORMAL VOLATİLİTE UYARISI</b>\n"
                f"<b>Coin:</b> {symbol} (15m)\n"
                f"📉 <b>Hareket:</b> {direction}\n"
                f"📊 <b>Değişim:</b> {change:+.2f}%\n"
                f"📈 <b>Hacim:</b> Ortalama hacmin {mult:.1f}x'i\n"
                f"💰 <b>Kapanış:</b> {akilli_fiyat(close)}"
            )
            if tg(C, msg):
                signal_sent(son_volatilite_sinyalleri, symbol)
                log("VOLATILITE", f"{symbol} {change:+.2f}%")


def volatilite_loop():
    safe_loop("VOLATILITE", VOLATILITE_ARALIGI, volatilite_worker, 75)


# =====================================================
# MOTOR 6: FORMASYON / MSS / LİKİDİTE SWEEP
# =====================================================
def formasyon_worker():
    for t in adaylar(MIN_HACIM_FORMASYON, MAX_FORMASYON):
        symbol = t["symbol"]
        if not cooldown_ok(son_formasyon_sinyalleri, symbol, 86400):
            continue
        k5 = tamamlanmis(klines(symbol, "5m", 105))
        k15 = tamamlanmis(klines(symbol, "15m", 55))
        if len(k5) < 95 or len(k15) < 30:
            continue

        highs = [float(k[2]) for k in k5]
        lows = [float(k[3]) for k in k5]
        closes = [float(k[4]) for k in k5]
        vols = [float(k[7]) for k in k5]
        c15 = [float(k[4]) for k in k15]
        e11, e21 = calculate_ema(c15, 11), calculate_ema(c15, 21)
        if not e11 or not e21:
            continue
        rsi = calculate_rsi(c15)
        price = closes[-1]
        avg_vol = sum(vols[-21:-1]) / 20
        if avg_vol <= 0:
            continue
        vol_ok = vols[-1] > avg_vol * 1.5
        ph, pl = find_pivots(highs, lows)
        if len(ph) < 2 or len(pl) < 2:
            continue
        h, l = ph[-2:], pl[-2:]
        signal = None

        peak_top = max(h[0][1], h[1][1])
        bottom_low = min(l[0][1], l[1][1])

        if abs(h[0][1] - h[1][1]) / max(h[0][1], 1e-12) < .03 and price < l[-1][1] and vol_ok and rsi < 50 and e11[-1] < e21[-1]:
            signal = ("SHORT", "Çift Tepe (M)", l[-1][1] - (peak_top - l[-1][1]), peak_top * 1.02)
        elif abs(l[0][1] - l[1][1]) / max(l[0][1], 1e-12) < .03 and price > h[-1][1] and vol_ok and rsi > 50 and e11[-1] > e21[-1]:
            signal = ("LONG", "Çift Dip (W)", h[-1][1] + (h[-1][1] - bottom_low), bottom_low * .98)
        elif highs[-1] > max(highs[-21:-1]) and price < max(highs[-21:-1]) and vol_ok and rsi < 50 and e11[-1] < e21[-1]:
            signal = ("SHORT", "Likidite Sweep", price * .97, highs[-1] * 1.01)
        elif lows[-1] < min(lows[-21:-1]) and price > min(lows[-21:-1]) and vol_ok and rsi > 50 and e11[-1] > e21[-1]:
            signal = ("LONG", "Likidite Sweep", price * 1.03, lows[-1] * .99)

        if not signal:
            hi, lo = max(highs[-21:-1]), min(lows[-21:-1])
            rng = (hi - lo) / lo * 100 if lo else 999
            if rng < 5 and price > hi and vol_ok and rsi > 50:
                signal = ("LONG", "Sıkışma yukarı kırılım", price + (hi - lo), lo * .98)
            elif rng < 5 and price < lo and vol_ok and rsi < 50:
                signal = ("SHORT", "Sıkışma aşağı kırılım", price - (hi - lo), hi * 1.02)

        if not signal:
            continue
        direction, name, target, stop = signal
        tp_pct = (target - price) / price * 100
        sl_pct = (stop - price) / price * 100
        if (direction == "LONG" and (target <= price or stop >= price)) or (direction == "SHORT" and (target >= price or stop <= price)):
            continue
        if abs(sl_pct) > 10 or not 1 <= abs(tp_pct) <= 20:
            continue
        msg = make_signal(
            f"TEYİTLİ DÖNÜŞ - {name}", symbol, direction, price, target, stop,
            f"\n📊 <b>RSI:</b> {rsi:.1f}\n"
            f"📈 <b>Hacim teyidi:</b> {vols[-1]/avg_vol:.1f}x\n"
            f"⏱️ <b>Grafikler:</b> 5m formasyon / 15m trend"
        )
        if tg(C, msg):
            signal_sent(son_formasyon_sinyalleri, symbol)
            log("FORMASYON", f"{symbol} {direction} {name}")


def formasyon_loop():
    safe_loop("FORMASYON", FORMASYON_ARALIGI, formasyon_worker, 100)


# =====================================================
# MOTOR 7: EMA RIBBON - SAATLİK EMA7/25/99/200 LONG / SHORT
# =====================================================
def ema_ribbon_worker():
    for t in adaylar(MIN_HACIM_EMA_RIBBON, MAX_TARAMA):
        symbol = t["symbol"]
        if not cooldown_ok(son_ema_ribbon_sinyalleri, symbol, 86400):
            continue
        ks = tamamlanmis(klines(symbol, "1h", 230))
        if len(ks) < 205:
            continue
        closes = [float(k[4]) for k in ks]
        vols = [float(k[7]) for k in ks]
        e7, e25 = calculate_ema(closes, 7), calculate_ema(closes, 25)
        e99, e200 = calculate_ema(closes, 99), calculate_ema(closes, 200)
        if not all((e7, e25, e99, e200)):
            continue
        curr = closes[-1]
        avg_vol = sum(vols[-21:-1]) / 20
        if avg_vol <= 0 or vols[-1] <= avg_vol * 1.2:
            continue
        a, b, c, d = e7[-1], e25[-1], e99[-1], e200[-1]
        direction = None
        if curr > a > b > c > d:
            direction = "LONG"
        elif curr < a < b < c < d:
            direction = "SHORT"
        if not direction:
            continue
        if direction == "LONG":
            target, stop = curr * 1.12, b * .97
        else:
            target, stop = curr * .88, b * 1.03
        if (direction == "LONG" and stop >= curr) or (direction == "SHORT" and stop <= curr):
            continue
        msg = make_signal(
            "SAATLİK EMA RIBBON", symbol, direction, curr, target, stop,
            f"\n📊 <b>EMA7:</b> {akilli_fiyat(a)} | <b>EMA25:</b> {akilli_fiyat(b)}\n"
            f"<b>EMA99:</b> {akilli_fiyat(c)} | <b>EMA200:</b> {akilli_fiyat(d)}\n"
            f"📈 <b>Hacim:</b> Ortalama hacmin {vols[-1]/avg_vol:.1f}x'i\n"
            f"⏱️ <b>Zaman dilimi:</b> 1 saat"
        )
        if tg(C, msg):
            signal_sent(son_ema_ribbon_sinyalleri, symbol)
            log("EMA_RIBBON", f"{symbol} {direction}")


def ema_ribbon_loop():
    safe_loop("EMA_RIBBON", EMA_RIBBON_ARALIGI, ema_ribbon_worker, 130)


# =====================================================
# MOTOR 8: ATH / ATL KIRILIMI
# =====================================================
def ath_atl_worker():
    for t in adaylar(MIN_HACIM_ATH_ATL, MAX_ATH_ATL):
        symbol = t["symbol"]
        if not cooldown_ok(son_ath_atl_sinyalleri, symbol, 86400):
            continue
        ks = tamamlanmis(klines(symbol, "1d", 500))
        if len(ks) < 200:
            continue
        highs = [float(k[2]) for k in ks]
        lows = [float(k[3]) for k in ks]
        closes = [float(k[4]) for k in ks]
        vols = [float(k[7]) for k in ks]
        if len(closes) < 22:
            continue
        price, prev = closes[-1], closes[-2]
        prior_ath = max(highs[:-1])
        prior_atl = min(lows[:-1])
        avg = sum(vols[-21:-1]) / 20
        if avg <= 0 or vols[-1] <= avg * 1.2:
            continue
        direction = "LONG" if prev <= prior_ath and price > prior_ath else "SHORT" if prev >= prior_atl and price < prior_atl else None
        if not direction:
            continue
        if direction == "LONG":
            level, target, stop = prior_ath, price * 1.20, prior_ath * .95
        else:
            level, target, stop = prior_atl, price * .80, prior_atl * 1.05
        msg = make_signal(
            "TARİHİ SEVİYE KIRILIMI", symbol, direction, price, target, stop,
            f"\n📊 <b>Kırılan geçmiş seviye:</b> {akilli_fiyat(level)}\n"
            f"📈 <b>Hacim teyidi:</b> {vols[-1]/avg:.1f}x\n"
            f"⏱️ <b>Zaman dilimi:</b> 1 gün"
        )
        if tg(C, msg):
            signal_sent(son_ath_atl_sinyalleri, symbol)
            log("ATH_ATL", f"{symbol} {direction}")


def ath_atl_loop():
    safe_loop("ATH_ATL", ATH_ATL_ARALIGI, ath_atl_worker, 160)


# =====================================================
# MOTOR 9: MACD - GÜNLÜK LONG / SHORT KESİŞİM
# =====================================================
def macd_worker():
    for t in adaylar(MIN_HACIM_MACD, MAX_TARAMA):
        symbol = t["symbol"]
        if not cooldown_ok(son_macd_sinyalleri, symbol, 14400):
            continue
        ks = tamamlanmis(klines(symbol, "1d", 110))
        if len(ks) < 80:
            continue
        closes = [float(k[4]) for k in ks]
        vols = [float(k[7]) for k in ks]
        avg = sum(vols[-21:-1]) / 20
        if avg <= 0 or vols[-1] <= avg * 1.1:
            continue
        cm, cs, pm, ps = calculate_macd(closes)
        if cm is None:
            continue
        direction = "LONG" if pm <= ps and cm > cs else "SHORT" if pm >= ps and cm < cs else None
        if not direction:
            continue
        price = closes[-1]
        target, stop = (price * 1.10, price * .97) if direction == "LONG" else (price * .90, price * 1.03)
        msg = make_signal(
            "GÜNLÜK MACD KESİŞİMİ", symbol, direction, price, target, stop,
            f"\n📊 <b>MACD:</b> {cm:.6f}\n<b>Sinyal çizgisi:</b> {cs:.6f}\n"
            f"📈 <b>Hacim:</b> Ortalama hacmin {vols[-1]/avg:.1f}x'i"
        )
        if tg(C, msg):
            signal_sent(son_macd_sinyalleri, symbol)
            log("MACD", f"{symbol} {direction}")


def macd_loop():
    safe_loop("MACD", MACD_ARALIGI, macd_worker, 190)


# =====================================================
# MOTOR 10: ICHIMOKU BULUTU
# =====================================================
def ichimoku_worker():
    for t in adaylar(MIN_HACIM_ICHIMOKU, MAX_TARAMA):
        symbol = t["symbol"]
        if not cooldown_ok(son_ichimoku_sinyalleri, symbol, 14400):
            continue
        ks = tamamlanmis(klines(symbol, "1h", 120))
        if len(ks) < 90:
            continue
        highs = [float(k[2]) for k in ks]
        lows = [float(k[3]) for k in ks]
        closes = [float(k[4]) for k in ks]
        vols = [float(k[7]) for k in ks]
        i = len(ks) - 1
        price, prev = closes[i], closes[i - 1]
        avg = sum(vols[-21:-1]) / 20
        if avg <= 0 or vols[-1] <= avg:
            continue

        def mid(period, end):
            start = end - period + 1
            if start < 0:
                return None
            return (max(highs[start:end + 1]) + min(lows[start:end + 1])) / 2

        tenkan = mid(9, i)
        kijun = mid(26, i)
        tenkan_prev = mid(9, i - 1)
        kijun_prev = mid(26, i - 1)
        a_now = (mid(9, i - 26) + mid(26, i - 26)) / 2 if i >= 77 else None
        a_prev = (mid(9, i - 27) + mid(26, i - 27)) / 2 if i >= 78 else None
        b_now = mid(52, i - 26) if i >= 77 else None
        b_prev = mid(52, i - 27) if i >= 78 else None
        if None in (tenkan, kijun, tenkan_prev, kijun_prev, a_now, a_prev, b_now, b_prev):
            continue
        top, bottom = max(a_now, b_now), min(a_now, b_now)
        ptop, pbottom = max(a_prev, b_prev), min(a_prev, b_prev)
        thickness = top - bottom
        direction = None
        if prev <= ptop and price > top and price > kijun and tenkan > kijun and thickness > price * .005:
            direction = "LONG"
        elif prev >= pbottom and price < bottom and price < kijun and tenkan < kijun and thickness > price * .005:
            direction = "SHORT"
        if not direction:
            continue
        target = price + thickness * 2 if direction == "LONG" else price - thickness * 2
        stop = min(kijun, bottom) * .99 if direction == "LONG" else max(kijun, top) * 1.01
        if (direction == "LONG" and stop >= price) or (direction == "SHORT" and stop <= price):
            continue
        msg = make_signal(
            "ICHIMOKU BULUT KIRILIMI", symbol, direction, price, target, stop,
            f"\n☁️ <b>Kumo:</b> {akilli_fiyat(bottom)} - {akilli_fiyat(top)}\n"
            f"📈 <b>Tenkan:</b> {akilli_fiyat(tenkan)} | <b>Kijun:</b> {akilli_fiyat(kijun)}\n"
            f"📊 <b>Hacim teyidi:</b> {vols[-1]/avg:.1f}x"
        )
        if tg(C, msg):
            signal_sent(son_ichimoku_sinyalleri, symbol)
            log("ICHIMOKU", f"{symbol} {direction}")


def ichimoku_loop():
    safe_loop("ICHIMOKU", ICHIMOKU_ARALIGI, ichimoku_worker, 220)


# =====================================================
# MOTOR 11: GRAFİK FORMASYONLARI
# =====================================================
def chart_patterns_worker():
    for t in adaylar(MIN_HACIM_PATTERN, MAX_PATTERN):
        symbol = t["symbol"]
        if not cooldown_ok(son_chart_pattern_sinyalleri, symbol, 14400):
            continue
        for tf in ("4h", "1d", "3d"):
            ks = tamamlanmis(klines(symbol, tf, 110))
            if len(ks) < 50:
                continue
            highs = [float(k[2]) for k in ks]
            lows = [float(k[3]) for k in ks]
            closes = [float(k[4]) for k in ks]
            vols = [float(k[7]) for k in ks]
            price = closes[-1]
            avg = sum(vols[-21:-1]) / 20
            if avg <= 0 or vols[-1] < avg * 1.2:
                continue
            ph, pl = find_pivots(highs, lows)
            signal = None
            if len(ph) >= 2:
                i1, p1 = ph[-2]
                i2, p2 = ph[-1]
                if 5 <= i2 - i1 <= 30 and abs(p1 - p2) / p1 <= .02:
                    neckline = min(lows[i1:i2 + 1])
                    if price < neckline * .99:
                        signal = ("SHORT", "İkili Tepe (M)", neckline - (max(p1, p2) - neckline), max(p1, p2) * 1.02, neckline)
            if not signal and len(pl) >= 2:
                i1, p1 = pl[-2]
                i2, p2 = pl[-1]
                if 5 <= i2 - i1 <= 30 and abs(p1 - p2) / p1 <= .02:
                    neckline = max(highs[i1:i2 + 1])
                    if price > neckline * 1.01:
                        signal = ("LONG", "İkili Dip (W)", neckline + (neckline - min(p1, p2)), min(p1, p2) * .98, neckline)
            if not signal and len(ph) >= 3:
                (i1, p1), (i2, p2), (i3, p3) = ph[-3:]
                if p2 > p1 and p2 > p3 and abs(p1 - p3) / p1 <= .03:
                    neckline = min(lows[i1:i3 + 1])
                    if price < neckline * .99:
                        signal = ("SHORT", "Omuz-Baş-Omuz", neckline - (p2 - neckline), p3 * 1.02, neckline)
            if not signal and len(pl) >= 3:
                (i1, p1), (i2, p2), (i3, p3) = pl[-3:]
                if p2 < p1 and p2 < p3 and abs(p1 - p3) / p1 <= .03:
                    neckline = max(highs[i1:i3 + 1])
                    if price > neckline * 1.01:
                        signal = ("LONG", "Ters Omuz-Baş-Omuz", neckline + (neckline - p2), p3 * .98, neckline)
            if not signal:
                continue
            direction, name, target, stop, neckline = signal
            tp = (target - price) / price * 100
            sl = (stop - price) / price * 100
            if (direction == "LONG" and (target <= price or stop >= price)) or (direction == "SHORT" and (target >= price or stop <= price)):
                continue
            if abs(sl) > 15 or not 1.5 <= abs(tp) <= 40:
                continue
            msg = make_signal(
                f"GRAFİK FORMASYONU - {name}", symbol, direction, price, target, stop,
                f"\n📊 <b>Boyun çizgisi:</b> {akilli_fiyat(neckline)}\n"
                f"📈 <b>Hacim:</b> {vols[-1]/avg:.1f}x\n"
                f"⏱️ <b>Zaman dilimi:</b> {tf}"
            )
            if tg(C, msg):
                signal_sent(son_chart_pattern_sinyalleri, symbol)
                log("PATTERN", f"{symbol} {direction} {name}")
            break


def chart_patterns_loop():
    safe_loop("PATTERN", CHART_PATTERN_ARALIGI, chart_patterns_worker, 250)


# =====================================================
# MOTOR 12: TOP GAINERS SHORT AVCISI
# =====================================================
def top_gainers_short_worker():
    rows = [t for t in futures_tickers() if t["_quote"] >= MIN_HACIM_PUMP]
    rows.sort(key=lambda x: x["_change"], reverse=True)
    for t in [x for x in rows if x["_change"] >= 15][:5]:
        symbol = t["symbol"]
        if not cooldown_ok(son_short_gainers_sinyalleri, symbol, 43200):
            continue
        ks = tamamlanmis(klines(symbol, "15m", 55))
        if len(ks) < 30:
            continue
        closes = [float(k[4]) for k in ks]
        highs = [float(k[2]) for k in ks]
        vols = [float(k[7]) for k in ks]
        e9, e21 = calculate_ema(closes, 9), calculate_ema(closes, 21)
        if not e9 or not e21:
            continue
        avg = sum(vols[-21:-1]) / 20
        if avg <= 0 or vols[-1] < avg:
            continue
        rsi = calculate_rsi(closes)
        recent_high = max(highs[-20:])
        drop = (recent_high - closes[-1]) / recent_high * 100
        if rsi > 55 and e9[-1] < e21[-1] and closes[-1] < closes[-2] and drop > 2:
            price = closes[-1]
            msg = (
                f"🩸 <b>TOP GAINERS SHORT ADAYI: {symbol}</b>\n"
                f"<i>Binance Futures | 15m kâr realizasyonu takibi</i>\n\n"
                f"📈 <b>24s yükseliş:</b> +{t['_change']:.1f}%\n"
                f"💰 <b>Gözlem fiyatı:</b> {akilli_fiyat(price)}\n"
                f"🛑 <b>İptal seviyesi:</b> {akilli_fiyat(recent_high * 1.01)}\n"
                f"🎯 <b>Hedef 1:</b> {akilli_fiyat(price * .95)}\n"
                f"🎯 <b>Hedef 2:</b> {akilli_fiyat(price * .90)}\n"
                f"📊 <b>RSI:</b> {rsi:.1f} | EMA9, EMA21 altında\n"
                f"📉 <b>Zirveden geri çekilme:</b> {drop:.2f}%\n"
                f"\n⚠️ <i>Bu bir short emri değil, olası dönüş uyarısıdır.</i>"
            )
            if tg(C, msg):
                signal_sent(son_short_gainers_sinyalleri, symbol)
                log("SHORT_GAINERS", f"{symbol} SHORT adayı")


def top_gainers_short_loop():
    safe_loop("SHORT_GAINERS", SHORT_GAINERS_ARALIGI, top_gainers_short_worker, 280)


# =====================================================
# MOTOR 13: TOP MOVERS - FUTURES SAATLİK HAREKET
# =====================================================
def top_movers_worker():
    rows = [t for t in futures_tickers() if t["_quote"] > MIN_HACIM_MOVERS]
    rows.sort(key=lambda x: x["_quote"], reverse=True)
    results = []
    for t in rows[:50]:
        ks = tamamlanmis(klines(t["symbol"], "1h", 3))
        if not ks:
            continue
        op, close = float(ks[-1][1]), float(ks[-1][4])
        if op > 0:
            results.append({
                "symbol": t["symbol"],
                "change": (close - op) / op * 100,
                "price": close
            })
    if not results:
        return
    results.sort(key=lambda x: x["change"], reverse=True)
    up, down = results[:3], list(reversed(results[-3:]))
    msg = "⏱️ <b>Binance Futures - Son Tamamlanan 1 Saat</b>\n\n🟢 <b>En Çok Yükselenler:</b>\n"
    for i, x in enumerate(up, 1):
        msg += f"{i}. <b>{x['symbol'].replace('USDT','')}</b> {x['change']:+.2f}% | {akilli_fiyat(x['price'])}\n"
    msg += "\n🔴 <b>En Çok Düşenler:</b>\n"
    for i, x in enumerate(down, 1):
        msg += f"{i}. <b>{x['symbol'].replace('USDT','')}</b> {x['change']:+.2f}% | {akilli_fiyat(x['price'])}\n"
    tg(C, msg)


def top_movers_loop():
    safe_loop("TOP_MOVERS", TOP_MOVERS_ARALIGI, top_movers_worker, 310)


# =====================================================
# MOTOR 14: FUTURES PİYASA GENİŞLİĞİ
# =====================================================
def dominans_worker():
    rows = futures_tickers()
    if not rows:
        return
    positive = [x for x in rows if x["_change"] > 0]
    negative = [x for x in rows if x["_change"] < 0]
    total = len(rows)
    up_pct = len(positive) / total * 100
    down_pct = len(negative) / total * 100
    up_vol = sum(x["_quote"] for x in positive)
    down_vol = sum(x["_quote"] for x in negative)
    btc = next((x for x in rows if x["symbol"] == "BTCUSDT"), None)
    eth = next((x for x in rows if x["symbol"] == "ETHUSDT"), None)
    if up_pct >= 60 and up_vol > down_vol:
        status = "🟢 GENEL PİYASA POZİTİF"
        detail = "USDT sürekli vadeli sözleşmelerin çoğu yükselişte; yükselenlerin hacmi de daha yüksek."
    elif down_pct >= 60 and down_vol > up_vol:
        status = "🔴 GENEL PİYASA NEGATİF"
        detail = "Sözleşmelerin çoğu düşüşte; düşenlerin hacmi daha yüksek."
    else:
        status = "🟡 KARIŞIK / YATAY PİYASA"
        detail = "Yükseliş ve düşüş tarafında belirgin bir piyasa üstünlüğü yok."
    msg = (
        "🌐 <b>BINANCE FUTURES PİYASA GENİŞLİĞİ</b>\n\n"
        f"🟢 <b>Yükselen sözleşmeler:</b> %{up_pct:.1f} ({len(positive)})\n"
        f"🔴 <b>Düşen sözleşmeler:</b> %{down_pct:.1f} ({len(negative)})\n"
        f"💵 <b>Yükselenlerin 24s hacmi:</b> {up_vol:,.0f} USDT\n"
        f"💵 <b>Düşenlerin 24s hacmi:</b> {down_vol:,.0f} USDT\n"
    )
    msg += f"₿ <b>BTC 24s:</b> {btc['_change']:+.2f}%\n" if btc else "₿ <b>BTC:</b> veri yok\n"
    if eth:
        msg += f"♦️ <b>ETH 24s:</b> {eth['_change']:+.2f}%\n"
    msg += f"\n📊 <b>Durum:</b> {status}\n💡 {detail}\n\n<i>Bu gösterge piyasa genişliğidir; BTC/ETH piyasa değeri dominansı değildir.</i>"
    tg(C, msg)


def dominans_loop():
    safe_loop("PIYASA_GENISLIGI", DOMINANS_ARALIGI, dominans_worker, 340)


# =====================================================
# AÇILIŞ KONTROLÜ (ARKA PLANDA)
# =====================================================
def startup_health_check():
    if not T:
        _status["telegram"] = "BOT_TOKEN boş"
        log("TELEGRAM", "HATA: BOT_TOKEN boş!")
    else:
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{T}/getMe",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                res = json.loads(r.read().decode("utf-8"))
                if res.get("ok"):
                    _status["telegram"] = f"OK (@{res['result'].get('username')})"
                    log("TELEGRAM", f"Bağlantı OK: @{res['result'].get('username')}")
                else:
                    _status["telegram"] = f"Token hatası: {res}"
                    log("TELEGRAM", f"Token hatası: {res}")
        except Exception as e:
            _status["telegram"] = f"Hata: {e}"
            log("TELEGRAM", f"Bağlantı hatası: {e}")

    fapi_res = gj(f"{FAPI}/fapi/v1/ticker/24hr?symbol=BTCUSDT")
    log("KONTROL", f"FAPI: {'OK' if isinstance(fapi_res, dict) else _status['fapi']}")

    data_res = gj(f"{DATA_API}/api/v3/ticker/24hr?symbol=BTCUSDT")
    log("KONTROL", f"DATA_API: {'OK' if isinstance(data_res, dict) else _status['data_api']}")


# =====================================================
# RENDER WEB SUNUCUSU
# =====================================================
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        text = (
            "Binance USDT-M Futures Signal Bot is running.\n"
            f"Telegram: {_status['telegram']}\n"
            f"FAPI: {_status['fapi']}\n"
            f"DATA_API: {_status['data_api']}\n"
            f"Son TOP_GAINERS: {_status['last_top_gainers']}\n"
        )
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        body = b"OK"
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
    log("SİSTEM", "Binance USDT-M Futures sinyal botu başlatılıyor.")
    threading.Thread(target=startup_health_check, name="HEALTH", daemon=True).start()

    motors = [
        ("TOP_GAINERS", top_gainers_loop),
        ("PUMP_WINNER", pump_winner_loop),
        ("CHARTIST", chartist_loop),
        ("PUMP", pump_loop),
        ("VOLATILITE", volatilite_loop),
        ("FORMASYON", formasyon_loop),
        ("EMA_RIBBON", ema_ribbon_loop),
        ("ATH_ATL", ath_atl_loop),
        ("MACD", macd_loop),
        ("ICHIMOKU", ichimoku_loop),
        ("PATTERN", chart_patterns_loop),
        ("SHORT_GAINERS", top_gainers_short_loop),
        ("TOP_MOVERS", top_movers_loop),
        ("PIYASA_GENISLIGI", dominans_loop),
    ]

    for name, fn in motors:
        threading.Thread(target=fn, name=name, daemon=True).start()

    try:
        run_web_server()
    except Exception as e:
        log("RENDER", f"Web sunucusu başlatılamadı: {e}")
        log("RENDER", traceback.format_exc())
        while True:
            time.sleep(60)

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
# AYARLAR
# =====================================================

socket.setdefaulttimeout(20)

T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()
PROXY_URL = os.environ.get("PROXY_URL", "").strip()

TR_Saati = timezone(timedelta(hours=3))
BASE = "https://api.binance.com"
FAPI = "https://fapi.binance.com"
DATA_API = "https://data-api.binance.vision"

_binance_opener = (
    urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY_URL, "https": PROXY_URL})
    )
    if PROXY_URL
    else urllib.request.build_opener()
)

# MOTOR ÇALIŞMA SÜRELERİ
RAPOR_ARALIGI = 14400       # 4 Saat
CHARTIST_ARALIGI = 3600     # 1 Saat
PUMP_ARALIGI = 120          # 2 Dakika
FORMASYON_ARALIGI = 3600    # 1 Saat
EMA_RIBBON_ARALIGI = 3600   # 1 Saat
MACD_ARALIGI = 3600         # 1 Saat
MIDAS_SETUP_ARALIGI = 7200  # 2 Saat
ATH_ATL_ARALIGI = 3600      # 1 Saat
ICHIMOKU_ARALIGI = 3600     # 1 Saat

# FİLTRELER
MIN_HACIM_CHARTIST = 10_000_000
MIN_HACIM_PUMP = 5_000_000
MIN_HACIM_FORMASYON = 15_000_000
MIN_HACIM_EMA_RIBBON = 10_000_000
MIN_HACIM_MACD = 10_000_000
MIN_HACIM_MIDAS_SETUP = 15_000_000
MIN_HACIM_ATH_ATL = 5_000_000
MIN_HACIM_ICHIMOKU = 10_000_000

MAX_TARAMA = 60
MAX_CHARTIST = 45
MAX_FORMASYON = 50
MAX_ATH_ATL = 50

YASAKLI_HISSELER = {
    "AAPLUSDT", "TSLAUSDT", "NVDAUSDT", "COINUSDT", "MSTRUSDT",
    "SQQQUSDT", "TQQQUSDT", "SPYUSDT", "QQQUSDT", "AMZNUSDT",
    "GOOGLUSDT", "METAUSDT", "NFLXUSDT", "BABAUSDT", "MSFTUSDT",
    "HOODUSDT", "CRCLUSDT", "XAUUSDT", "XAGUSDT", "PAXGUSDT"
}

son_sinyaller = {}
son_pump_sinyalleri = {}
son_formasyon_sinyalleri = {}
son_ema_ribbon_sinyalleri = {}
son_macd_sinyalleri = {}
son_midas_setup_sinyalleri = {}
son_ath_atl_sinyalleri = {}
son_ichimoku_sinyalleri = {}

# Paylaşılan önbellek ve API hız kontrolü
_api_locks = {
    "spot": threading.Lock(),
    "fapi": threading.Lock(),
    "data": threading.Lock(),
}
_last_api_call = {"spot": 0.0, "fapi": 0.0, "data": 0.0}
_api_block_until = {"spot": 0.0, "fapi": 0.0, "data": 0.0}

_ticker_locks = {
    "spot": threading.Lock(),
    "fapi": threading.Lock(),
    "data": threading.Lock(),
}
_ticker_cache = {
    "spot": {"time": 0.0, "data": None},
    "fapi": {"time": 0.0, "data": None},
    "data": {"time": 0.0, "data": None},
}

_symbol_locks = {
    "spot": threading.Lock(),
    "fapi": threading.Lock(),
    "data": threading.Lock(),
}
_symbol_cache = {
    "spot": {"time": 0.0, "symbols": set()},
    "fapi": {"time": 0.0, "symbols": set()},
    "data": {"time": 0.0, "symbols": set()},
}

_cache_lock = threading.Lock()
_kline_cache = {}

API_MIN_GAP = 0.25
KLINE_TTL_MAP = {
    "5m": 100,
    "15m": 240,
    "1h": 900,
    "8h": 3600,
    "12h": 3600,
    "1d": 3600,
    "3d": 3600,
}

# =====================================================
# YARDIMCI FONKSİYONLAR
# =====================================================

def simdi():
    return datetime.now(TR_Saati)


def log(prefix, m):
    print(f"[{simdi().strftime('%H:%M:%S')}] [{prefix}] {m}", flush=True)


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
            return result.get("ok", False)
    except Exception as e:
        log("TELEGRAM", f"Gönderim hatası: {e}")
        return False


def _cleanup_kline_cache(now):
    if len(_kline_cache) <= 2000:
        return
    expired = [k for k, v in _kline_cache.items() if now - v[0] > 3600]
    for k in expired:
        _kline_cache.pop(k, None)
    if len(_kline_cache) > 2000:
        _kline_cache.clear()


def _detect_market(url):
    if url.startswith(FAPI):
        return "fapi"
    if url.startswith(DATA_API):
        return "data"
    return "spot"


def gj(url, cache_key=None, cache_ttl=0):
    market = _detect_market(url)

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
            if not already_blocked:
                yedek = " (DATA_API yedeğine geçildi)" if market != "data" else ""
                log("BINANCE", f"[{market.upper()}] HTTP {e.code}; {pause}s durduruldu{yedek}.")
        elif e.code == 451:
            with _api_locks[market]:
                already_blocked = time.time() < _api_block_until[market]
                _api_block_until[market] = max(_api_block_until[market], time.time() + 3600)
            if not already_blocked:
                log("BINANCE", f"[{market.upper()}] HTTP 451 (Bölge engeli); DATA_API yedeği kullanılacak.")
        else:
            log("BINANCE", f"[{market.upper()}] HTTP {e.code}: {url.split('?')[0]}")
        return None
    except Exception as e:
        log("BINANCE", f"[{market.upper()}] Bağlantı hatası: {e}")
        return None


def _active_source(preferred_market):
    if not api_blocked(preferred_market):
        return preferred_market
    if not api_blocked("data"):
        return "data"
    return preferred_market


def aktif_semboller(market="fapi"):
    with _symbol_locks[market]:
        if time.time() - _symbol_cache[market]["time"] < 21600 and _symbol_cache[market]["symbols"]:
            return _symbol_cache[market]["symbols"]

        if market == "fapi":
            url = f"{FAPI}/fapi/v1/exchangeInfo"
        elif market == "data":
            url = f"{DATA_API}/api/v3/exchangeInfo"
        else:
            url = f"{BASE}/api/v3/exchangeInfo"

        data = gj(url)
        if not isinstance(data, dict):
            return _symbol_cache[market]["symbols"] or set()

        symbols = set()
        for item in data.get("symbols", []):
            symbol = item.get("symbol", "")
            if market == "fapi":
                ok = (
                    item.get("status") == "TRADING"
                    and item.get("contractType") == "PERPETUAL"
                    and item.get("quoteAsset") == "USDT"
                )
            else:
                ok = (
                    item.get("status") == "TRADING"
                    and item.get("quoteAsset") == "USDT"
                    and item.get("isSpotTradingAllowed", True)
                )
            if (ok
                    and symbol.endswith("USDT")
                    and symbol not in YASAKLI_HISSELER
                    and not any(x in symbol for x in ("3L", "3S", "5L", "5S", "UPUSDT", "DOWNUSDT"))):
                symbols.add(symbol)

        _symbol_cache[market]["time"] = time.time()
        _symbol_cache[market]["symbols"] = symbols
        log("SİSTEM", f"Aktif {market.upper()} USDT parite sayısı: {len(symbols)}")
        return symbols


def get_tickers(preferred_market="fapi"):
    src = _active_source(preferred_market)
    with _ticker_locks[src]:
        if _ticker_cache[src]["data"] is not None and time.time() - _ticker_cache[src]["time"] < 55:
            return _ticker_cache[src]["data"], src

        if src == "fapi":
            url = f"{FAPI}/fapi/v1/ticker/24hr"
        elif src == "data":
            url = f"{DATA_API}/api/v3/ticker/24hr"
        else:
            url = f"{BASE}/api/v3/ticker/24hr"

        data = gj(url)
        # Eğer fapi/spot az önce 418 yediyse hemen DATA_API dene
        if not isinstance(data, list) and src != "data" and not api_blocked("data"):
            src = "data"
            with _ticker_locks["data"]:
                if _ticker_cache["data"]["data"] is not None and time.time() - _ticker_cache["data"]["time"] < 55:
                    return _ticker_cache["data"]["data"], "data"
                data = gj(f"{DATA_API}/api/v3/ticker/24hr")

        if not isinstance(data, list):
            return (_ticker_cache[src]["data"] or []), src

        active = aktif_semboller(src)
        filtered = []
        for t in data:
            if not active or t.get("symbol") in active:
                sym = t.get("symbol", "")
                if not sym.endswith("USDT") or sym in YASAKLI_HISSELER:
                    continue
                try:
                    t["_change"] = float(t.get("priceChangePercent", 0))
                    t["_quote"] = float(t.get("quoteVolume", 0))
                    filtered.append(t)
                except (TypeError, ValueError):
                    continue

        _ticker_cache[src]["time"] = time.time()
        _ticker_cache[src]["data"] = filtered
        return filtered, src


def adaylar_getir(preferred_market, min_hacim, limit=MAX_TARAMA):
    tickers, src = get_tickers(preferred_market)
    rows = [t for t in tickers if t["_quote"] >= min_hacim]
    rows.sort(key=lambda x: x["_quote"], reverse=True)
    return [t["symbol"] for t in rows[:limit]], src


def get_klines(market, symbol, interval, limit):
    src = _active_source(market)
    key = f"{src}:{symbol}:{interval}"
    ttl = KLINE_TTL_MAP.get(interval, 60)

    with _cache_lock:
        item = _kline_cache.get(key)
        if item and time.time() - item[0] < ttl and isinstance(item[1], list) and len(item[1]) >= limit:
            return item[1][-limit:]

    fetch_limit = max(limit, 120) if (interval == "1h" and limit <= 120) else limit
    if src == "fapi":
        url = f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={fetch_limit}"
    elif src == "data":
        url = f"{DATA_API}/api/v3/klines?symbol={symbol}&interval={interval}&limit={fetch_limit}"
    else:
        url = f"{BASE}/api/v3/klines?symbol={symbol}&interval={interval}&limit={fetch_limit}"

    data = gj(url, cache_key=key, cache_ttl=ttl)
    if not isinstance(data, list) and src != "data" and not api_blocked("data"):
        src = "data"
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
    if len(prices) < slow + signal + 1:
        return None, None, None, None
    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)
    if not ema_fast or not ema_slow:
        return None, None, None, None
    min_len = min(len(ema_fast), len(ema_slow))
    macd_line = [ema_fast[-min_len + i] - ema_slow[-min_len + i] for i in range(min_len)]
    signal_line = calculate_ema(macd_line, signal)
    if len(signal_line) < 2:
        return None, None, None, None
    return macd_line[-1], signal_line[-1], macd_line[-2], signal_line[-2]


def get_market_trend(tickers):
    try:
        sorted_tickers = sorted(tickers, key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)[:100]
        changes, up_count, down_count = [], 0, 0
        for t in sorted_tickers:
            change = float(t.get("priceChangePercent", 0))
            changes.append(change)
            if change > 0:
                up_count += 1
            elif change < 0:
                down_count += 1
        if not changes:
            return "Bilinmiyor", 0, 0, 0
        avg_change = sum(changes) / len(changes)
        if avg_change > 1.5 and up_count > down_count:
            trend = "🟢 YUKARI (Boğa)"
        elif avg_change < -1.5 and down_count > up_count:
            trend = "🔴 AŞAĞI (Ayı)"
        else:
            trend = "🟡 YATAY (Kararsız)"
        return trend, avg_change, up_count, down_count
    except Exception:
        return "Bilinmiyor", 0, 0, 0


def trap_radar_check(symbol):
    if api_blocked("fapi"):
        return ""
    try:
        oi_data = gj(f"{FAPI}/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=2")
        taker_data = gj(f"{FAPI}/futures/data/takerlongshortRatio?symbol={symbol}&period=5m&limit=1")
        if oi_data is None and taker_data is None:
            return ""

        oi_artiyor = False
        if isinstance(oi_data, list) and len(oi_data) >= 2:
            oi_old = float(oi_data[0]["sumOpenInterest"])
            oi_new = float(oi_data[1]["sumOpenInterest"])
            if oi_old > 0 and ((oi_new - oi_old) / oi_old) * 100 > 0.2:
                oi_artiyor = True

        alici_baskin = False
        if isinstance(taker_data, list) and len(taker_data) > 0:
            buy_vol = float(taker_data[0]["buyVol"])
            sell_vol = float(taker_data[0]["sellVol"])
            total = buy_vol + sell_vol
            if total > 0 and (buy_vol / total) * 100 > 55:
                alici_baskin = True

        if oi_artiyor and alici_baskin:
            return "🐋 <b>BALİNA TEYİDİ (Gerçek Pump)</b>"
        elif not alici_baskin:
            return "⚠️ <b>DİKKAT: SAHTE PUMP (TUZAK OLABİLİR)</b>"
        else:
            return "⚖️ <b>Kısmi Teyit</b>"
    except Exception:
        return ""


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
# MOTOR 1: MİDAS FLOW (RAPOR)
# =====================================================
def midas_flow_worker():
    tickers, _ = get_tickers("spot")
    if not tickers:
        return
    trend_text, avg_chg, ups, downs = get_market_trend(tickers)
    usdt_pairs = [t for t in tickers if t["_quote"] > 5_000_000]
    usdt_pairs.sort(key=lambda x: x["_change"], reverse=True)
    top_5 = usdt_pairs[:5]

    if top_5:
        msg = "🔥 <b>24 saatteki en iyi hareketler</b>\n"
        msg += f"\n🌍 <b>Piyasa Yönü (24s):</b> {trend_text}\n"
        msg += f"📊 Ortalama Değişim: %{avg_chg:.2f} | Yükselen: {ups} | Düşen: {downs}\n\n"
        medals = ["🥇", "🥈", "🥉", "🏅", "⭐️"]
        for i, t in enumerate(top_5):
            symbol = t["symbol"].replace("USDT", "")
            change = t["_change"]
            change_str = f"+{change:.0f}%" if change > 0 else f"{change:.0f}%"
            msg += f"{medals[i]} {symbol} {change_str}\n"
        tg(C, msg)


def midas_flow_loop():
    safe_loop("RAPOR", RAPOR_ARALIGI, midas_flow_worker, 10)


# =====================================================
# MOTOR 2: CHARTIST (EMA200)
# =====================================================
def chartist_worker():
    symbols, src = adaylar_getir("spot", MIN_HACIM_CHARTIST, MAX_CHARTIST)
    for symbol in symbols:
        for tf in ("8h", "12h", "1d", "3d"):
            cooldown_key = f"CH_{tf}_{symbol}"
            if cooldown_key in son_sinyaller and time.time() - son_sinyaller[cooldown_key] < 86400:
                continue
            klines = get_klines(src, symbol, tf, 500)
            if not isinstance(klines, list) or len(klines) < 205:
                continue
            closes = [float(k[4]) for k in klines[:-1]]
            curr, prev = closes[-1], closes[-2]
            ema200 = calculate_ema(closes, 200)
            if len(ema200) >= 2 and prev <= ema200[-2] and curr > ema200[-1] and curr > ema200[-1] * 1.01:
                vol = [float(k[7]) for k in klines[:-1]]
                avg_vol = sum(vol[-21:-1]) / 20
                if avg_vol > 0 and vol[-1] > avg_vol:
                    sl = ema200[-1] * 0.97
                    aciklama = "EMA200, uzun vadeli trend çizgisidir. Fiyatın bu devasa çizgiyi hacimle yukarı kırması, ayı piyasasının sona erip boğa piyasasının başladığını gösterir."
                    msg = (f"📈 <b>UZUN VADELİ SPOT (LONG): {symbol}</b>\n"
                           f"<i>{tf.upper()} EMA200 Kırılımı</i>\n\n"
                           f"💰 <b>Giriş:</b> {akilli_fiyat(curr)}\n"
                           f"🛑 <b>SL:</b> {akilli_fiyat(sl)}\n"
                           f"🎯 <b>Hedef:</b> {akilli_fiyat(curr * 1.15)}\n\n"
                           f"📊 <b>Teyit:</b> Hacim Onaylı ve %1 üzeri kapanış.\n\n"
                           f"💡 <b>Açıklama:</b> {aciklama}")
                    if tg(C, msg):
                        son_sinyaller[cooldown_key] = time.time()
                        log("CHARTIST", f"🔥 SİNYAL: {symbol} ({tf.upper()})")


def chartist_loop():
    safe_loop("CHARTIST", CHARTIST_ARALIGI, chartist_worker, 25)


# =====================================================
# MOTOR 3: PUMP AVCISI
# =====================================================
def pump_worker():
    symbols, src = adaylar_getir("fapi", MIN_HACIM_PUMP, MAX_TARAMA)
    for symbol in symbols:
        if symbol in son_pump_sinyalleri and time.time() - son_pump_sinyalleri[symbol] < 900:
            continue
        klines = get_klines(src, symbol, "5m", 3)
        if not isinstance(klines, list) or len(klines) < 3:
            continue
        curr_k, prev_k = klines[-2], klines[-3]
        cc, pc = float(curr_k[4]), float(prev_k[4])
        cv, pv = float(curr_k[7]), float(prev_k[7])
        if pc == 0 or pv == 0:
            continue
        price_change = ((cc - pc) / pc) * 100
        vol_change = ((cv - pv) / pv) * 100
        vol_diff = cv - pv

        if price_change >= 3.0 and vol_change >= 100.0 and vol_diff >= 50_000:
            trap_radar = trap_radar_check(symbol)
            trap_block = f"{trap_radar}\n\n" if trap_radar else ""
            aciklama = "5 dakikalık grafikte aniden sert bir hacim ve fiyat patlaması yaşandı. Trap Radar sayesinde bu hareketin arkasında gerçek balinaların mı yoksa sahte botların mı olduğu analiz edildi."
            msg = (f"🚀 <b>Pump - {symbol}</b>\n"
                   f"💰 <b>Fiyat:</b> {akilli_fiyat(cc)} (<b>{price_change:+.2f}%</b>)\n"
                   f"📊 <b>Hacim Artışı:</b> {vol_change:+.0f}%\n"
                   f"Volume increased by {vol_diff:.0f} ⬆️\n\n"
                   f"{trap_block}"
                   f"💡 <b>Açıklama:</b> {aciklama}")
            if tg(C, msg):
                son_pump_sinyalleri[symbol] = time.time()
                log("PUMP", f"🔥 SİNYAL: {symbol}")


def pump_loop():
    safe_loop("PUMP", PUMP_ARALIGI, pump_worker, 45)


# =====================================================
# MOTOR 4: FORMASYON (MSS)
# =====================================================
def find_pivots(highs, lows, window=3):
    pivot_highs = []
    pivot_lows = []
    for i in range(window, len(highs) - window):
        if highs[i] == max(highs[i - window:i + window + 1]):
            pivot_highs.append((i, highs[i]))
        if lows[i] == min(lows[i - window:i + window + 1]):
            pivot_lows.append((i, lows[i]))
    return pivot_highs, pivot_lows


def formasyon_worker():
    symbols, src = adaylar_getir("fapi", MIN_HACIM_FORMASYON, MAX_FORMASYON)
    for symbol in symbols:
        if symbol in son_formasyon_sinyalleri and time.time() - son_formasyon_sinyalleri[symbol] < 86400:
            continue

        klines_5m = get_klines(src, symbol, "5m", 100)
        klines_15m = get_klines(src, symbol, "15m", 50)

        if not isinstance(klines_5m, list) or not isinstance(klines_15m, list):
            continue
        if len(klines_5m) < 100 or len(klines_15m) < 50:
            continue

        highs = [float(k[2]) for k in klines_5m]
        lows = [float(k[3]) for k in klines_5m]
        closes = [float(k[4]) for k in klines_5m]
        vols = [float(k[7]) for k in klines_5m]

        closes_15m = [float(k[4]) for k in klines_15m]
        e11 = calculate_ema(closes_15m, 11)
        e21 = calculate_ema(closes_15m, 21)
        if not e11 or not e21:
            continue
        ema11 = e11[-1]
        ema21 = e21[-1]
        rsi = calculate_rsi(closes_15m)

        current_price = closes[-1]
        avg_vol = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 1
        if avg_vol <= 0 or current_price <= 0:
            continue
        current_vol = vols[-1]
        is_vol_teyit = current_vol > (avg_vol * 1.5)

        p_highs, p_lows = find_pivots(highs, lows)
        if len(p_highs) < 3 or len(p_lows) < 3:
            continue

        last_h = p_highs[-3:]
        last_l = p_lows[-3:]

        sinyal = None
        formasyon_adi = ""
        yon = ""
        emoji = ""
        hedef = 0
        stop = 0
        aciklama = ""

        if abs(last_h[1][1] - last_h[2][1]) / max(last_h[1][1], 1e-12) < 0.03 and current_price < last_l[-1][1] and is_vol_teyit:
            if rsi < 50 and ema11 < ema21:
                peak_top = max(last_h[1][1], last_h[2][1])
                sinyal = True
                formasyon_adi = "Çift Tepe (M)"
                yon = "SHORT"
                emoji = "🔻"
                hedef = last_l[-1][1] - (peak_top - last_l[-1][1])
                stop = peak_top * 1.02
                aciklama = "İkinci tepe ilkini geçemedi ve boyun çizgisi hacimle kırıldı. 15m trend ve RSI düşüşü destekliyor."

        elif abs(last_l[1][1] - last_l[2][1]) / max(last_l[1][1], 1e-12) < 0.03 and current_price > last_h[-1][1] and is_vol_teyit:
            if rsi > 50 and ema11 > ema21:
                bottom_low = min(last_l[1][1], last_l[2][1])
                sinyal = True
                formasyon_adi = "Çift Dip (W)"
                yon = "LONG"
                emoji = "🚀"
                hedef = last_h[-1][1] + (last_h[-1][1] - bottom_low)
                stop = bottom_low * 0.98
                aciklama = "İkinci dip satıcıları tüketti ve boyun çizgisi hacimle kırıldı. 15m trend ve RSI yükselişi destekliyor."

        elif last_h[1][1] > last_h[0][1] and last_h[1][1] > last_h[2][1] and abs(last_h[0][1] - last_h[2][1]) / max(last_h[0][1], 1e-12) < 0.05 and current_price < last_l[-1][1] and is_vol_teyit:
            if rsi < 50 and ema11 < ema21:
                sinyal = True
                formasyon_adi = "Omuz-Baş-Omuz"
                yon = "SHORT"
                emoji = "🔻"
                hedef = last_l[-1][1] - (last_h[1][1] - last_l[-1][1])
                stop = last_h[2][1] * 1.02
                aciklama = "Klasik dönüş formasyonu. Boyun çizgisi kırıldı, 15m trend aşağı yönlü teyit veriyor."

        elif highs[-2] > max(highs[-20:-2]) and current_price < max(highs[-20:-2]) and is_vol_teyit:
            if rsi < 50 and ema11 < ema21:
                sinyal = True
                formasyon_adi = "Likidite Avı (Sweep)"
                yon = "SHORT"
                emoji = "💀"
                stop = highs[-2] * 1.01
                hedef = current_price * 0.97
                aciklama = "Fiyat eski tepenin üzerine fitil attı (Stop-loss avı) ancak hemen altına kapandı. Akıllı para satışa geçmiş olabilir."

        elif lows[-2] < min(lows[-20:-2]) and current_price > min(lows[-20:-2]) and is_vol_teyit:
            if rsi > 50 and ema11 > ema21:
                sinyal = True
                formasyon_adi = "Likidite Avı (Sweep)"
                yon = "LONG"
                emoji = "🚀"
                stop = lows[-2] * 0.99
                hedef = current_price * 1.03
                aciklama = "Fiyat eski dibin altına fitil attı (Stop-loss avı) ancak hemen üstüne kapandı. Akıllı para alıma geçmiş olabilir."

        if not sinyal:
            last_20_high = max(highs[-20:-1])
            last_20_low = min(lows[-20:-1])
            range_pct = ((last_20_high - last_20_low) / last_20_low) * 100 if last_20_low > 0 else 999

            if range_pct < 5 and current_price > last_20_high and is_vol_teyit and rsi > 50:
                sinyal = True
                formasyon_adi = "Sıkışma ve Yukarı Kırılım (Bayrak)"
                yon = "LONG"
                emoji = "🚀"
                hedef = current_price + (last_20_high - last_20_low)
                stop = last_20_low * 0.98
                aciklama = "Fiyat dar bir bantta sıkıştı ve hacimle yukarı kırıldı. Volatilite patlaması başlıyor."
            elif range_pct < 5 and current_price < last_20_low and is_vol_teyit and rsi < 50:
                sinyal = True
                formasyon_adi = "Sıkışma ve Aşağı Kırılım (Ters Bayrak)"
                yon = "SHORT"
                emoji = "🔻"
                hedef = current_price - (last_20_high - last_20_low)
                stop = last_20_high * 1.02
                aciklama = "Fiyat dar bir bantta sıkıştı ve hacimle aşağı kırıldı. Volatilite patlaması başlıyor."

        if sinyal:
            hedef_pct = ((hedef - current_price) / current_price) * 100
            stop_pct = ((stop - current_price) / current_price) * 100
            if (yon == "LONG" and (hedef <= current_price or stop >= current_price)) or (yon == "SHORT" and (hedef >= current_price or stop <= current_price)):
                continue

            msg = (f"{emoji} <b>TEYİTLİ DÖNÜŞ ({yon}): {symbol}</b>\n"
                   f"<i>5m Formasyon - 15m Trend Teyitli</i>\n\n"
                   f"💰 <b>Giriş:</b> {akilli_fiyat(current_price)}\n"
                   f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                   f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                   f"📊 <b>Formasyon:</b> {formasyon_adi}\n"
                   f"📊 <b>Teyitler:</b> Hacim ({current_vol/avg_vol:.1f}x), RSI ({rsi:.1f}), EMA (15m)\n\n"
                   f"💡 <b>Açıklama:</b> {aciklama}")

            if tg(C, msg):
                son_formasyon_sinyalleri[symbol] = time.time()
                log("FORMASYON", f"🔥 SİNYAL: {symbol} ({yon} - {formasyon_adi})")


def formasyon_loop():
    safe_loop("FORMASYON", FORMASYON_ARALIGI, formasyon_worker, 65)


# =====================================================
# MOTOR 5: EMA RIBBON
# =====================================================
def ema_ribbon_worker():
    symbols, src = adaylar_getir("fapi", MIN_HACIM_EMA_RIBBON, MAX_TARAMA)
    for symbol in symbols:
        if symbol in son_ema_ribbon_sinyalleri and time.time() - son_ema_ribbon_sinyalleri[symbol] < 86400:
            continue
        klines = get_klines(src, symbol, "1d", 230)
        if not isinstance(klines, list) or len(klines) < 205:
            continue
        closes = [float(k[4]) for k in klines[:-1]]
        vols = [float(k[7]) for k in klines[:-1]]
        curr = closes[-1]
        curr_vol = vols[-1]
        avg_vol = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 1
        if avg_vol <= 0 or curr_vol <= (avg_vol * 1.2):
            continue

        e7_list = calculate_ema(closes, 7)
        e25_list = calculate_ema(closes, 25)
        e99_list = calculate_ema(closes, 99)
        e200_list = calculate_ema(closes, 200)
        if not all((e7_list, e25_list, e99_list, e200_list)):
            continue
        e7, e25, e99, e200 = e7_list[-1], e25_list[-1], e99_list[-1], e200_list[-1]

        if curr > e7 > e25 > e99 > e200:
            aciklama = "EMA Ribbon (Hareketli Ortalama Şeridi) dizilimi, piyasanın tüm zaman dilimlerinde mükemmel bir uyum içinde olduğunu gösterir. Trendin en güçlü olduğu 'tatlı nokta'dır."
            msg = (f"🚀 <b>EMA DİZİLİMİ (LONG): {symbol}</b>\n"
                   f"<i>1 Günlük Grafik - Çoklu EMA Dizilimi</i>\n\n"
                   f"💰 <b>Giriş:</b> {akilli_fiyat(curr)}\n"
                   f"🎯 <b>Hedef:</b> {akilli_fiyat(curr * 1.20)} (+20.0%)\n"
                   f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(e25 * 0.95)}\n\n"
                   f"📊 <b>EMA Dizilimi:</b>\n"
                   f"EMA7: {akilli_fiyat(e7)} | EMA25: {akilli_fiyat(e25)}\n"
                   f"EMA99: {akilli_fiyat(e99)} | EMA200: {akilli_fiyat(e200)}\n"
                   f"📈 <b>Hacim Teyidi:</b> Alındı ({curr_vol/avg_vol:.1f}x)\n\n"
                   f"💡 <b>Açıklama:</b> {aciklama}")
            if tg(C, msg):
                son_ema_ribbon_sinyalleri[symbol] = time.time()
                log("EMA_RIBBON", f"🔥 SİNYAL: {symbol}")


def ema_ribbon_loop():
    safe_loop("EMA_RIBBON", EMA_RIBBON_ARALIGI, ema_ribbon_worker, 120)


# =====================================================
# MOTOR 6: MACD
# =====================================================
def macd_worker():
    symbols, src = adaylar_getir("fapi", MIN_HACIM_MACD, MAX_TARAMA)
    for symbol in symbols:
        if symbol in son_macd_sinyalleri and time.time() - son_macd_sinyalleri[symbol] < 14400:
            continue
        klines = get_klines(src, symbol, "1d", 100)
        if not isinstance(klines, list) or len(klines) < 80:
            continue
        closes = [float(k[4]) for k in klines[:-1]]
        vols = [float(k[7]) for k in klines[:-1]]
        curr_price = closes[-1]
        curr_vol = vols[-1]
        avg_vol = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 1
        if avg_vol <= 0 or curr_vol <= (avg_vol * 1.1):
            continue

        cm, cs, pm, ps = calculate_macd(closes)
        if cm is None:
            continue

        if pm <= ps and cm > cs:
            aciklama = "MACD çizgisinin (Mavi) Sinyal çizgisini (Turuncu) yukarı yönlü kesmesine 'Golden Cross' (Altın Kesişim) denir. Bu, satıcıların gücünü kaybettiğini ve yükseliş trendinin başladığını teyit eder."
            msg = (f"🚀 <b>MACD SİNYALİ (LONG): {symbol}</b>\n"
                   f"<i>1 Günlük Grafik - MACD Momentum Kırılımı</i>\n\n"
                   f"💰 <b>Giriş:</b> {akilli_fiyat(curr_price)}\n"
                   f"🎯 <b>Hedef:</b> {akilli_fiyat(curr_price * 1.10)} (+10.0%)\n"
                   f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(curr_price * 0.97)} (-3.0%)\n\n"
                   f"📊 <b>MACD Çizgisi:</b> {cm:.5f} | <b>Sinyal Çizgisi:</b> {cs:.5f}\n"
                   f"📈 <b>Hacim Teyidi:</b> Alındı\n\n"
                   f"💡 <b>Açıklama:</b> {aciklama}")
            if tg(C, msg):
                son_macd_sinyalleri[symbol] = time.time()
                log("MACD", f"🔥 SİNYAL: {symbol}")


def macd_loop():
    safe_loop("MACD", MACD_ARALIGI, macd_worker, 150)


# =====================================================
# 7. MOTOR: MIDAS SETUP (POC / VAH / VAL)
# =====================================================
def midas_setup_worker():
    symbols, src = adaylar_getir("fapi", MIN_HACIM_MIDAS_SETUP, MAX_TARAMA)
    for symbol in symbols:
        if symbol in son_midas_setup_sinyalleri and time.time() - son_midas_setup_sinyalleri[symbol] < 14400:
            continue

        klines = get_klines(src, symbol, "1h", 50)
        if not isinstance(klines, list) or len(klines) < 50:
            continue

        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        closes = [float(k[4]) for k in klines]
        vols = [float(k[7]) for k in klines]

        current_price = closes[-1]
        if current_price <= 0:
            continue
        recent_highs = highs[-24:]
        recent_lows = lows[-24:]
        recent_vols = vols[-24:]

        vah = max(recent_highs)
        val = min(recent_lows)

        poc_idx = recent_vols.index(max(recent_vols))
        poc = closes[-24 + poc_idx]

        if current_price <= poc * 1.01 and current_price < vah:
            low_1h = min(lows[-1:])
            low_4h = min(lows[-4:])

            hedefler = sorted(list(set([low_1h, low_4h, val])), reverse=True)
            hedefler = [h for h in hedefler if h < current_price]

            if len(hedefler) >= 3:
                tp1, tp2, tp3 = hedefler[0], hedefler[1], hedefler[2]
            elif len(hedefler) == 2:
                tp1, tp2, tp3 = hedefler[0], hedefler[1], hedefler[1] * 0.98
            else:
                continue

            stop = vah * 1.02

            tp1_pct = ((tp1 - current_price) / current_price) * 100
            tp2_pct = ((tp2 - current_price) / current_price) * 100
            tp3_pct = ((tp3 - current_price) / current_price) * 100
            stop_pct = ((stop - current_price) / current_price) * 100

            coin = symbol.replace("USDT", "")

            msg = (f"🔻 <b>SETUP SİNYALİ (SHORT): {symbol}</b>\n\n"
                   f"Fiyat: <b>{akilli_fiyat(current_price)}</b>\n"
                   f"Hedefler: <b>{akilli_fiyat(tp1)}</b> ({tp1_pct:+.1f}%) → <b>{akilli_fiyat(tp2)}</b> ({tp2_pct:+.1f}%) → <b>{akilli_fiyat(tp3)}</b> ({tp3_pct:+.1f}%)\n"
                   f"İptal: <b>{akilli_fiyat(stop)}</b> ({stop_pct:+.1f}%) üzerinde\n\n"
                   f"{coin}, VAH <b>{akilli_fiyat(vah)}</b> ile VAL <b>{akilli_fiyat(val)}</b> arasında havada asılı kaldı. Merkezde POC <b>{akilli_fiyat(poc)}</b> var ve şu an hakem konumunda. Fiyat <b>{akilli_fiyat(current_price)}</b> ile onun üzerinde tutunmaya çalışıyor ancak yukarı ivme zayıf. 🧊\n\n"
                   f"Kilit ayrım: POC kaybı aşağıya ilk olarak 1s dip <b>{akilli_fiyat(low_1h)}</b> ve 4s dip <b>{akilli_fiyat(low_4h)}</b> yolunu açar. Akış borsalarda senkronize, zayıflık devam ederse VAL <b>{akilli_fiyat(val)}</b> hedefe girer. 🎯")

            if tg(C, msg):
                son_midas_setup_sinyalleri[symbol] = time.time()
                log("MIDAS_SETUP", f"🔥 SİNYAL: {symbol} (SHORT)")


def midas_setup_loop():
    safe_loop("MIDAS_SETUP", MIDAS_SETUP_ARALIGI, midas_setup_worker, 180)


# =====================================================
# 8. MOTOR: TARİHİ ZİRVE / DİP KIRILIMI (ATH / ATL)
# =====================================================
def ath_atl_worker():
    symbols, src = adaylar_getir("fapi", MIN_HACIM_ATH_ATL, MAX_ATH_ATL)
    for symbol in symbols:
        if symbol in son_ath_atl_sinyalleri and time.time() - son_ath_atl_sinyalleri[symbol] < 86400:
            continue
        klines = get_klines(src, symbol, "1d", 500)
        if not isinstance(klines, list) or len(klines) < 200:
            continue
        highs = [float(k[2]) for k in klines[:-1]]
        lows = [float(k[3]) for k in klines[:-1]]
        closes = [float(k[4]) for k in klines[:-1]]
        vols = [float(k[7]) for k in klines[:-1]]
        if len(closes) < 22:
            continue
        current_price = closes[-1]
        prev_close = closes[-2]
        current_vol = vols[-1]
        ath = max(highs[:-1])
        atl = min(lows[:-1])
        avg_vol = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 1
        if avg_vol <= 0 or current_price <= 0:
            continue
        is_vol_teyit = current_vol > (avg_vol * 1.2)

        if not is_vol_teyit:
            continue

        sinyal = None
        yon = ""
        emoji = ""
        stop = 0
        hedef = 0
        aciklama = ""

        if prev_close >= atl and current_price < atl:
            sinyal = True
            yon = "SHORT"
            emoji = "💀"
            stop = atl * 1.05
            hedef = current_price * 0.80
            aciklama = "Fiyat, geçmişteki en düşük seviyeyi (Tarihi Dibi) hacimle aşağı kırdı. Uzun vadeli bir çöküşün başlangıcı olabilir."
        elif prev_close <= ath and current_price > ath:
            sinyal = True
            yon = "LONG"
            emoji = "🚀"
            stop = ath * 0.95
            hedef = current_price * 1.20
            aciklama = "Fiyat, geçmişteki en yüksek seviyeyi (Tarihi Zirveyi) hacimle yukarı kırdı. Yeni bir boğa trendinin başlangıcı olabilir."

        if sinyal:
            hedef_pct = ((hedef - current_price) / current_price) * 100
            stop_pct = ((stop - current_price) / current_price) * 100

            msg = (f"{emoji} <b>TARİHİ SEVİYE KIRILIMI ({yon}): {symbol}</b>\n"
                   f"<i>1 Günlük Grafik</i>\n\n"
                   f"💰 <b>Giriş:</b> {akilli_fiyat(current_price)}\n"
                   f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                   f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                   f"📊 <b>Kırılan Seviye:</b> {akilli_fiyat(ath if yon == 'LONG' else atl)}\n"
                   f"📈 <b>Hacim:</b> {current_vol/avg_vol:.1f}x\n\n"
                   f"💡 <b>Açıklama:</b> {aciklama}")

            if tg(C, msg):
                son_ath_atl_sinyalleri[symbol] = time.time()
                log("ATH_ATL", f"🔥 SİNYAL: {symbol} ({yon})")


def ath_atl_loop():
    safe_loop("ATH_ATL", ATH_ATL_ARALIGI, ath_atl_worker, 95)


# =====================================================
# 9. MOTOR: ICHIMOKU BULUTU
# =====================================================
def ichimoku_worker():
    symbols, src = adaylar_getir("fapi", MIN_HACIM_ICHIMOKU, MAX_TARAMA)
    for symbol in symbols:
        if symbol in son_ichimoku_sinyalleri and time.time() - son_ichimoku_sinyalleri[symbol] < 14400:
            continue

        klines = get_klines(src, symbol, "1h", 120)
        if not isinstance(klines, list) or len(klines) < 85:
            continue

        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        closes = [float(k[4]) for k in klines]
        vols = [float(k[7]) for k in klines]

        curr_close = closes[-2]
        prev_close = closes[-3]

        avg_vol = sum(vols[-21:-1]) / 20
        if avg_vol <= 0 or curr_close <= 0:
            continue
        curr_vol = vols[-2]
        is_vol_teyit = curr_vol > avg_vol

        if not is_vol_teyit:
            continue

        def calc_tenkan(idx):
            return (max(highs[idx - 8:idx + 1]) + min(lows[idx - 8:idx + 1])) / 2

        def calc_kijun(idx):
            return (max(highs[idx - 25:idx + 1]) + min(lows[idx - 25:idx + 1])) / 2

        idx_curr = len(closes) - 2

        tenkan_curr = calc_tenkan(idx_curr)
        kijun_curr = calc_kijun(idx_curr)

        span_a_curr = (calc_tenkan(idx_curr - 26) + calc_kijun(idx_curr - 26)) / 2
        span_a_prev = (calc_tenkan(idx_curr - 27) + calc_kijun(idx_curr - 27)) / 2

        span_b_curr = (max(highs[idx_curr - 77:idx_curr - 26]) + min(lows[idx_curr - 77:idx_curr - 26])) / 2
        span_b_prev = (max(highs[idx_curr - 78:idx_curr - 27]) + min(lows[idx_curr - 78:idx_curr - 27])) / 2

        cloud_top_curr = max(span_a_curr, span_b_curr)
        cloud_bottom_curr = min(span_a_curr, span_b_curr)
        cloud_thickness = cloud_top_curr - cloud_bottom_curr

        cloud_top_prev = max(span_a_prev, span_b_prev)
        cloud_bottom_prev = min(span_a_prev, span_b_prev)

        sinyal = None
        yon = ""
        emoji = ""
        stop = 0
        hedef = 0
        aciklama = ""

        if prev_close <= cloud_top_prev and curr_close > cloud_top_curr and curr_close > kijun_curr and tenkan_curr > kijun_curr:
            if cloud_thickness > (curr_close * 0.005):
                sinyal = True
                yon = "LONG (Bulut Üstü Kırılım)"
                emoji = "🚀"
                stop = min(kijun_curr, cloud_bottom_curr) * 0.99
                hedef = curr_close + (cloud_thickness * 2)
                aciklama = "Fiyat Ichimoku bulutunu yukarı yönlü kırdı. Tenkan-sen'in Kijun-sen'in üzerinde olması kısa vadeli momentumun güçlü olduğunu gösterir."

        elif prev_close >= cloud_bottom_prev and curr_close < cloud_bottom_curr and curr_close < kijun_curr and tenkan_curr < kijun_curr:
            if cloud_thickness > (curr_close * 0.005):
                sinyal = True
                yon = "SHORT (Bulut Altı Kırılım)"
                emoji = "🔻"
                stop = max(kijun_curr, cloud_top_curr) * 1.01
                hedef = curr_close - (cloud_thickness * 2)
                aciklama = "Fiyat Ichimoku bulutunu aşağı yönlü kırdı. Tenkan-sen'in Kijun-sen'in altında olması satıcıların güçlü olduğunu gösterir."

        if sinyal:
            hedef_pct = ((hedef - curr_close) / curr_close) * 100
            stop_pct = ((stop - curr_close) / curr_close) * 100

            if abs(hedef_pct) < 1.0:
                continue

            msg = (f"{emoji} <b>ICHIMOKU SİNYALİ ({yon}): {symbol}</b>\n"
                   f"<i>1 Saatlik Grafik - Ichimoku Bulut Kırılımı</i>\n\n"
                   f"💰 <b>Kapanış Fiyatı:</b> {akilli_fiyat(curr_close)}\n"
                   f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                   f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                   f"☁️ <b>Bulut (Kumo) Bölgesi:</b> {akilli_fiyat(cloud_bottom_curr)} - {akilli_fiyat(cloud_top_curr)}\n"
                   f"📈 <b>Tenkan-sen:</b> {akilli_fiyat(tenkan_curr)} | <b>Kijun-sen:</b> {akilli_fiyat(kijun_curr)}\n"
                   f"📊 <b>Hacim Teyidi:</b> Alındı\n\n"
                   f"💡 <b>Açıklama:</b> {aciklama}")

            if tg(C, msg):
                son_ichimoku_sinyalleri[symbol] = time.time()
                log("ICHIMOKU", f"🔥 SİNYAL: {symbol} ({yon})")


def ichimoku_loop():
    safe_loop("ICHIMOKU", ICHIMOKU_ARALIGI, ichimoku_worker, 210)


# =====================================================
# AÇILIŞ BAĞLANTI TESTİ
# =====================================================
def startup_health_check():
    if not T:
        log("TELEGRAM", "HATA: BOT_TOKEN boş! Render Environment kısmından ekleyin.")
    else:
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{T}/getMe",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                res = json.loads(r.read().decode("utf-8"))
                if res.get("ok"):
                    log("TELEGRAM", f"Bağlantı OK: @{res['result'].get('username')}")
                else:
                    log("TELEGRAM", f"Bot token hatası: {res}")
        except Exception as e:
            log("TELEGRAM", f"Bağlantı hatası: {e}")

    for label, url in (
        ("SPOT", f"{BASE}/api/v3/ticker/24hr?symbol=BTCUSDT"),
        ("FAPI", f"{FAPI}/fapi/v1/ticker/24hr?symbol=BTCUSDT"),
        ("DATA_API", f"{DATA_API}/api/v3/ticker/24hr?symbol=BTCUSDT"),
    ):
        res = gj(url)
        log("KONTROL", f"{label}: {'OK' if isinstance(res, dict) else 'ENGEL / YANIT YOK'}")


# =====================================================
# RENDER WEB SUNUCUSU
# =====================================================
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"Ultimate Futures Bot is running!"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        body = b"Ultimate Futures Bot is running!"
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


# =====================================================
# BAŞLAT
# =====================================================
if __name__ == "__main__":
    startup_health_check()

    threading.Thread(target=midas_flow_loop, daemon=True).start()
    threading.Thread(target=chartist_loop, daemon=True).start()
    threading.Thread(target=pump_loop, daemon=True).start()
    threading.Thread(target=formasyon_loop, daemon=True).start()
    threading.Thread(target=ath_atl_loop, daemon=True).start()
    threading.Thread(target=ema_ribbon_loop, daemon=True).start()
    threading.Thread(target=macd_loop, daemon=True).start()
    threading.Thread(target=midas_setup_loop, daemon=True).start()
    threading.Thread(target=ichimoku_loop, daemon=True).start()

    try:
        run_web_server()
    except Exception as e:
        log("RENDER", f"Web sunucusu başlatılamadı: {e}")
        log("RENDER", traceback.format_exc())
        while True:
            time.sleep(60)

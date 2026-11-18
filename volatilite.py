import urllib.request
import urllib.parse
import json
import time
import socket
import os
import threading
import http.server
import traceback
from datetime import datetime, timedelta, timezone

# =====================================================
# AYARLAR VE BAĞLANTI YAPILANDIRMASI
# =====================================================

socket.setdefaulttimeout(20)

T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()

TR_Saati = timezone(timedelta(hours=3))
FAPI = "https://fapi.binance.com"
BASE = "https://api.binance.com"

# MOTOR ÇALIŞMA SÜRELERİ (Saniye)
CHARTIST_ARALIGI = 3600    # 1 Saat
PUMP_ARALIGI = 60          # 1 Dakika
VOLATILITE_ARALIGI = 60    # 1 Dakika
FORMASYON_ARALIGI = 3600   # 1 Saat
EMA_RIBBON_ARALIGI = 3600  # 1 Saat
MACD_ARALIGI = 3600        # 1 Saat
ATH_ATL_ARALIGI = 3600     # 1 Saat
ICHIMOKU_ARALIGI = 3600    # 1 Saat
CHART_PATTERN_ARALIGI = 1800 # 30 Dakika
SHORT_GAINERS_ARALIGI = 300  # 5 Dakika
TOP_MOVERS_ARALIGI = 3600  # 1 Saat
DOMINANS_ARALIGI = 14400   # 4 Saat

# RAPOR MOTORLARI SÜRELERİ
TOP_GAINERS_ARALIGI = 14400  # 4 Saat
PUMP_WINNER_ARALIGI = 21600  # 6 Saat

# FİLTRELER (MİNİMUM HACİMLER)
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

# TARAMA LİMİTLERİ (API Hız Koruması)
MAX_TARAMA = 100
MAX_CHARTIST = 70
MAX_PATTERN = 60
MAX_FORMASYON = 70
MAX_ATH_ATL = 70

# YASAKLI VARLIKLAR
YASAKLI_HISSELER = {
    "AAPLUSDT", "TSLAUSDT", "NVDAUSDT", "COINUSDT", "MSTRUSDT", 
    "SQQQUSDT", "TQQQUSDT", "SPYUSDT", "QQQUSDT", "AMZNUSDT", 
    "GOOGLUSDT", "METAUSDT", "NFLXUSDT", "BABAUSDT", "MSFTUSDT", 
    "HOODUSDT", "CRCLUSDT", "XAUUSDT", "XAGUSDT", "PAXGUSDT"
}

# Sinyal Hafızaları
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
son_movers_sinyalleri = {}
son_dominans_sinyalleri = {}

# Önbellek ve İstek Sınırlayıcı (Rate Limit & Cache)
_api_lock = threading.Lock()
_last_api_call = 0.0
_api_block_until = 0.0
_ticker_cache = {"time": 0.0, "data": None}
_symbol_cache = {"time": 0.0, "symbols": set()}
_kline_cache = {}
_cache_lock = threading.Lock()
API_MIN_GAP = 0.12
KLINE_CACHE_TTL = 45

# =====================================================
# YARDIMCI FONKSİYONLAR
# =====================================================

def simdi():
    return datetime.now(TR_Saati)

def log(prefix, m):
    print(f"[{simdi().strftime('%H:%M:%S')}] [{prefix}] {m}", flush=True)

def akilli_fiyat(p):
    if p is None: return "0"
    p = float(p)
    if p >= 1000: return f"{p:.2f}"
    if p >= 1: return f"{p:.4f}"
    if p >= 0.01: return f"{p:.6f}"
    return f"{p:.8f}"

def tg(chat_id, message):
    if not T or not chat_id: return False
    try:
        data = urllib.parse.urlencode({
            "chat_id": str(chat_id), "text": message, "parse_mode": "HTML", "disable_web_page_preview": "true"
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

def gj(url, cache_key=None, cache_ttl=0):
    global _last_api_call, _api_block_until

    if cache_key and cache_ttl:
        with _cache_lock:
            item = _kline_cache.get(cache_key)
            if item and time.time() - item[0] < cache_ttl:
                return item[1]

    with _api_lock:
        now = time.time()
        if now < _api_block_until:
            return None
        wait = API_MIN_GAP - (now - _last_api_call)
        if wait > 0:
            time.sleep(wait)
        _last_api_call = time.time()

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
        if cache_key and cache_ttl:
            with _cache_lock:
                _kline_cache[cache_key] = (time.time(), result)
        return result
    except urllib.error.HTTPError as e:
        if e.code in (418, 429):
            retry_after = e.headers.get("Retry-After")
            try:
                pause = max(120, min(int(retry_after), 1800)) if retry_after else 300
            except Exception:
                pause = 300
            with _api_lock:
                _api_block_until = time.time() + pause
            log("BINANCE", f"HTTP {e.code}; API istekleri {pause} saniye durduruldu.")
        else:
            log("BINANCE", f"HTTP {e.code}: {url.split('?')[0]}")
        return None
    except Exception as e:
        log("BINANCE", f"Bağlantı/veri hatası: {e}")
        return None

def aktif_usdt_perpetual():
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

def futures_tickers(force=False):
    if not force and _ticker_cache["data"] is not None and time.time() - _ticker_cache["time"] < 45:
        return _ticker_cache["data"]

    data = gj(f"{FAPI}/fapi/v1/ticker/24hr")
    if not isinstance(data, list):
        return _ticker_cache["data"] or []

    active = aktif_usdt_perpetual()
    filtered = []
    for t in data:
        if t.get("symbol") in active:
            try:
                t["_change"] = float(t.get("priceChangePercent", 0))
                t["_quote"] = float(t.get("quoteVolume", 0))
                t["_price"] = float(t.get("lastPrice", 0))
                filtered.append(t)
            except (TypeError, ValueError):
                continue

    _ticker_cache["time"] = time.time()
    _ticker_cache["data"] = filtered
    return filtered

def adaylar(min_hacim, limit=MAX_TARAMA):
    rows = [t for t in futures_tickers() if t["_quote"] >= min_hacim]
    rows.sort(key=lambda x: x["_quote"], reverse=True)
    return rows[:limit]

def klines(symbol, interval, limit):
    key = f"{symbol}:{interval}:{limit}"
    return gj(
        f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}",
        cache_key=key,
        cache_ttl=KLINE_CACHE_TTL
    )

def tamamlanmis(ks):
    return ks[:-1] if isinstance(ks, list) and len(ks) >= 2 else []

def calculate_ema(prices, period):
    if len(prices) < period: return []
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for price in prices[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1: return 50.0
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0: return 100.0
    return 100 - (100 / (1 + (avg_gain / avg_loss)))

def calculate_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow + signal + 2: return None, None, None, None
    ef = calculate_ema(prices, fast)
    es = calculate_ema(prices, slow)
    if not ef or not es: return None, None, None, None
    offset = slow - fast
    macd_line = [ef[i + offset] - es[i] for i in range(len(es))]
    sig = calculate_ema(macd_line, signal)
    if len(sig) < 2: return None, None, None, None
    return macd_line[-1], sig[-1], macd_line[-2], sig[-2]

def trap_radar_check(symbol):
    try:
        oi = gj(f"{FAPI}/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=2")
        taker = gj(f"{FAPI}/futures/data/takerlongshortRatio?symbol={symbol}&period=5m&limit=1")
        oi_artiyor = False
        alici_baskin = False
        if isinstance(oi, list) and len(oi) >= 2:
            old = float(oi[-2]['sumOpenInterest'])
            new = float(oi[-1]['sumOpenInterest'])
            if old > 0 and ((new - old) / old) * 100 > 0.2: oi_artiyor = True
        if isinstance(taker, list) and taker:
            buy_vol = float(taker[-1]['buyVol'])
            sell_vol = float(taker[-1]['sellVol'])
            total = buy_vol + sell_vol
            if total > 0 and (buy_vol / total) * 100 > 55: alici_baskin = True

        if oi_artiyor and alici_baskin: return "🐋 <b>BALİNA TEYİDİ (Gerçek Pump)</b>"
        elif not alici_baskin: return "⚠️ <b>DİKKAT: SAHTE PUMP (TUZAK OLABİLİR)</b>"
        else: return "⚖️ <b>Kısmi Teyit</b>"
    except: return ""

def find_pivots(highs, lows, window=3):
    pivot_highs, pivot_lows = [], []
    for i in range(window, len(highs) - window):
        if highs[i] == max(highs[i-window:i+window+1]): pivot_highs.append((i, highs[i]))
        if lows[i] == min(lows[i-window:i+window+1]): pivot_lows.append((i, lows[i]))
    return pivot_highs, pivot_lows

def cooldown_ok(store, key, seconds):
    return time.time() - store.get(key, 0) >= seconds

def signal_sent(store, key):
    store[key] = time.time()

def make_signal(title, symbol, direction, entry, target, stop, detail=""):
    tp_pct = ((target - entry) / entry) * 100
    sl_pct = ((stop - entry) / entry) * 100
    emoji = "🚀" if direction == "LONG" else "🔻"
    return (
        f"{emoji} <b>{title} ({direction}): {symbol}</b>\n\n"
        f"💰 <b>Giriş:</b> {akilli_fiyat(entry)}\n"
        f"🎯 <b>Hedef:</b> {akilli_fiyat(target)} (<b>{tp_pct:+.1f}%</b>)\n"
        f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({sl_pct:+.1f}%)\n"
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
# MOTOR 1: TOP GAINERS (Görsel 1 Tarzı Rapor)
# =====================================================
def top_gainers_worker():
    rows = [t for t in futures_tickers() if t["_quote"] > 5_000_000]
    rows.sort(key=lambda x: x["_change"], reverse=True)
    top_5 = [t for t in rows if t["_change"] > 0][:5]
    if not top_5: return

    msg = "🔥 <b>Günün En İyi Hareketleri (24 Saat)</b>\n"
    msg += "<i>Maksimum yükseliş yönü, hacim teyitli</i>\n\n"
    medals = ["🥇", "🥈", "🥉", "🏅", "⭐️"]
    for i, t in enumerate(top_5):
        symbol = t['symbol'].replace("USDT", "")
        change = t['_change']
        price = t['_price']
        change_str = f"+{change:.0f}%" if change > 0 else f"{change:.0f}%"
        msg += f"{medals[i]} <b>{symbol}</b> {change_str} | Fiyat: {akilli_fiyat(price)}\n"
    
    msg += "\n👉 <i>En çok kazandıran coinler listelendi.</i>"
    tg(C, msg)

def top_gainers_loop():
    safe_loop("TOP_GAINERS", TOP_GAINERS_ARALIGI, top_gainers_worker, 10)

# =====================================================
# MOTOR 2: PUMP WINNER (Görsel 2 Tarzı Rapor)
# =====================================================
def pump_winner_worker():
    rows = [t for t in futures_tickers() if t["_quote"] > 10_000_000]
    rows.sort(key=lambda x: x["_change"], reverse=True)
    if not rows or rows[0]["_change"] < 15: return

    winner = rows[0]
    symbol = winner['symbol']
    change_pct = winner['_change']

    ks = tamamlanmis(klines(symbol, "1d", 1))
    if not ks: return
    day_low = float(ks[0][3])
    day_high = float(ks[0][2])
    max_move = ((day_high - day_low) / day_low) * 100 if day_low > 0 else 0

    msg = (f"🏆 <b>GÜNÜN KAZANANI: {symbol}</b>\n\n"
           f"🔥 24 saat içinde en çok yükselen coin!\n\n"
           f"📉 <b>Dip Fiyat:</b> {akilli_fiyat(day_low)}\n"
           f"🚀 <b>Zirve Fiyat:</b> {akilli_fiyat(day_high)}\n"
           f"📈 <b>Maksimum Yükseliş:</b> <b>+{max_move:.1f}%</b>\n\n"
           f"💡 <b>Değerlendirme:</b> {symbol} gün içerisinde muazzam bir hacim ve fiyat patlaması yaşadı. Pump Dedektör verilerimize göre günün en kârlı hareketi bu coinde gerçekleşti.")
    tg(C, msg)

def pump_winner_loop():
    safe_loop("PUMP_WINNER", PUMP_WINNER_ARALIGI, pump_winner_worker, 20)

# =====================================================
# MOTOR 3: CHARTIST (EMA200)
# =====================================================
def chartist_worker():
    for t in adaylar(MIN_HACIM_CHARTIST, MAX_CHARTIST):
        symbol = t["symbol"]
        if not cooldown_ok(son_sinyaller, symbol, 86400): continue
        ks = tamamlanmis(klines(symbol, "1h", 230))
        if len(ks) < 205: continue
        closes = [float(k[4]) for k in ks]
        vols = [float(k[7]) for k in ks]
        curr, prev = closes[-1], closes[-2]
        ema200 = calculate_ema(closes, 200)
        if len(ema200) < 2: continue
        
        if prev <= ema200[-2] and curr > ema200[-1] and curr > ema200[-1]*1.01:
            avg_vol = sum(vols[-21:-1]) / 20
            if avg_vol > 0 and vols[-1] > avg_vol:
                sl = ema200[-1] * 0.97
                hedef = curr * 1.15
                sl_pct = ((sl - curr) / curr) * 100
                hedef_pct = ((hedef - curr) / curr) * 100
                if abs(sl_pct) > 12.0 or abs(sl_pct) > abs(hedef_pct): continue

                msg = (f"📈 <b>UZUN VADELİ SPOT (LONG): {symbol}</b>\n"
                       f"<i>1H EMA200 Kırılımı</i>\n\n"
                       f"💰 <b>Giriş:</b> {akilli_fiyat(curr)}\n"
                       f"🛑 <b>SL:</b> {akilli_fiyat(sl)} (<b>{sl_pct:+.1f}%</b>)\n"
                       f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n\n"
                       f"📊 <b>Teyit:</b> Hacim Onaylı ve %1 üzeri kapanış.\n\n"
                       f"💡 <b>Açıklama:</b> EMA200, uzun vadeli trend çizgisidir. Fiyatın bu devasa çizgiyi hacimle yukarı kırması, ayı piyasasının sona erip boğa piyasasının başladığını gösterir.")
                if tg(C, msg):
                    signal_sent(son_sinyaller, symbol)
                    log("CHARTIST", f"{symbol} LONG")

def chartist_loop():
    safe_loop("CHARTIST", CHARTIST_ARALIGI, chartist_worker, 30)

# =====================================================
# MOTOR 4: PUMP AVCISI
# =====================================================
def pump_worker():
    for t in adaylar(MIN_HACIM_PUMP, MAX_TARAMA):
        symbol = t["symbol"]
        if not cooldown_ok(son_pump_sinyalleri, symbol, 900): continue
        ks = tamamlanmis(klines(symbol, "5m", 4))
        if len(ks) < 3: continue
        curr_k, prev_k = ks[-1], ks[-2]
        cc, pc = float(curr_k[4]), float(prev_k[4])
        cv, pv = float(curr_k[7]), float(prev_k[7])
        if pc == 0 or pv == 0: continue
        price_change = ((cc - pc) / pc) * 100
        vol_change = ((cv - pv) / pv) * 100
        vol_diff = cv - pv

        if price_change >= 3.0 and vol_change >= 100.0 and vol_diff >= 50_000:
            trap_radar = trap_radar_check(symbol)
            msg = (f"🚀 <b>Pump - {symbol}</b>\n"
                   f"💰 <b>Fiyat:</b> {akilli_fiyat(cc)} (<b>{price_change:+.2f}%</b>)\n"
                   f"📊 <b>Hacim Artışı:</b> {vol_change:+.0f}%\n"
                   f"Volume increased by {vol_diff:.0f} ⬆️\n\n"
                   f"{trap_radar}\n\n"
                   f"💡 <b>Açıklama:</b> 5 dakikalık grafikte aniden sert bir hacim ve fiyat patlaması yaşandı. Trap Radar sayesinde bu hareketin arkasında gerçek balinaların mı yoksa sahte botların mı olduğu analiz edildi.")
            if tg(C, msg):
                signal_sent(son_pump_sinyalleri, symbol)
                log("PUMP", f"{symbol} Pump")

def pump_loop():
    safe_loop("PUMP", PUMP_ARALIGI, pump_worker, 45)

# =====================================================
# MOTOR 5: ANORMAL VOLATİLİTE VE DUMP AVCISI
# =====================================================
def volatilite_worker():
    for t in adaylar(MIN_HACIM_VOLATILITE, MAX_TARAMA):
        symbol = t["symbol"]
        if not cooldown_ok(son_volatilite_sinyalleri, symbol, 1800): continue
        ks = tamamlanmis(klines(symbol, "15m", 25))
        if len(ks) < 22: continue
        
        opens = [float(k[1]) for k in ks]
        closes = [float(k[4]) for k in ks]
        vols = [float(k[7]) for k in ks]
        
        curr_open = opens[-1]
        curr_close = closes[-1]
        curr_vol = vols[-1]
        avg_vol = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 1
        
        if curr_open == 0 or avg_vol == 0: continue
        price_change_pct = ((curr_close - curr_open) / curr_open) * 100
        vol_multiplier = curr_vol / avg_vol
        
        if abs(price_change_pct) >= 2.5 and vol_multiplier >= 3.0:
            yon = "DUMP (ANI DÜŞÜŞ) 🔻" if price_change_pct < 0 else "PUMP (ANI YÜKSELİŞ) 🚀"
            emoji = "🚨" if price_change_pct < 0 else "⚡"
            msg = (f"{emoji} <b>ANORMAL VOLATİLİTE UYARISI</b> {emoji}\n"
                   f"<b>Coin:</b> {symbol} (15m Grafik)\n\n"
                   f"📉 <b>Hareket:</b> {yon}\n"
                   f"📊 <b>Değişim:</b> <b>{price_change_pct:+.2f}%</b>\n"
                   f"📈 <b>Hacim Patlaması:</b> Ortalamanın <b>{vol_multiplier:.1f}x</b> katı\n"
                   f"💰 <b>Kapanış Fiyatı:</b> {akilli_fiyat(curr_close)}\n\n"
                   f"⚠️ <i>Piyasada ani bir likidite değişimi veya büyük bir emir gerçekleşmiş olabilir. Dikkatli olun!</i>")
            if tg(C, msg):
                signal_sent(son_volatilite_sinyalleri, symbol)
                log("VOLATILITE", f"{symbol} Volatilite")

def volatilite_loop():
    safe_loop("VOLATILITE", VOLATILITE_ARALIGI, volatilite_worker, 55)

# =====================================================
# MOTOR 6: FORMASYON (MSS)
# =====================================================
def formasyon_worker():
    for t in adaylar(MIN_HACIM_FORMASYON, MAX_FORMASYON):
        symbol = t["symbol"]
        if not cooldown_ok(son_formasyon_sinyalleri, symbol, 86400): continue
        
        k5 = tamamlanmis(klines(symbol, "5m", 100))
        k15 = tamamlanmis(klines(symbol, "15m", 50))
        if not k5 or not k15 or len(k5) < 95 or len(k15) < 30: continue
        
        highs = [float(k[2]) for k in k5]
        lows = [float(k[3]) for k in k5]
        closes = [float(k[4]) for k in k5]
        vols = [float(k[7]) for k in k5]
        
        closes_15m = [float(k[4]) for k in k15]
        ema11 = calculate_ema(closes_15m, 11)
        ema21 = calculate_ema(closes_15m, 21)
        if not ema11 or not ema21: continue
        
        rsi = calculate_rsi(closes_15m)
        current_price = closes[-1]
        avg_vol = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 1
        current_vol = vols[-1]
        is_vol_teyit = current_vol > (avg_vol * 1.5)
        
        p_highs, p_lows = find_pivots(highs, lows)
        if len(p_highs) < 3 or len(p_lows) < 3: continue
        
        last_h = p_highs[-3:]
        last_l = p_lows[-3:]
        sinyal = None; formasyon_adi = ""; yon = ""; emoji = ""; hedef = 0; stop = 0; aciklama = ""

        if abs(last_h[0][1] - last_h[2][1]) / last_h[0][1] < 0.03 and current_price < last_l[-1][1] and is_vol_teyit:
            if rsi < 50 and ema11[-1] < ema21[-1]:
                sinyal = True; formasyon_adi = "Çift Tepe (M)"; yon = "SHORT"; emoji = "🔻"
                hedef = last_l[-1][1] - (last_h[1][1] - last_l[-1][1]); stop = last_h[2][1] * 1.02
                aciklama = "İkinci tepe ilkini geçemedi ve boyun çizgisi hacimle kırıldı. 15m trend ve RSI düşüşü destekliyor."

        elif abs(last_l[0][1] - last_l[2][1]) / last_l[0][1] < 0.03 and current_price > last_h[-1][1] and is_vol_teyit:
            if rsi > 50 and ema11[-1] > ema21[-1]:
                sinyal = True; formasyon_adi = "Çift Dip (W)"; yon = "LONG"; emoji = "🚀"
                hedef = last_h[-1][1] + (last_h[-1][1] - last_l[1][1]); stop = last_l[2][1] * 0.98
                aciklama = "İkinci dip satıcıları tüketti ve boyun çizgisi hacimle kırıldı. 15m trend ve RSI yükselişi destekliyor."

        elif highs[-1] > max(highs[-21:-1]) and current_price < max(highs[-21:-1]) and is_vol_teyit:
            if rsi < 50 and ema11[-1] < ema21[-1]:
                sinyal = True; formasyon_adi = "Likidite Avı (Sweep)"; yon = "SHORT"; emoji = "💀"
                stop = highs[-1] * 1.01; hedef = current_price * 0.97
                aciklama = "Fiyat eski tepenin üzerine fitil attı ancak hemen altına kapandı. Akıllı para satışa geçmiş olabilir."

        elif lows[-1] < min(lows[-21:-1]) and current_price > min(lows[-21:-1]) and is_vol_teyit:
            if rsi > 50 and ema11[-1] > ema21[-1]:
                sinyal = True; formasyon_adi = "Likidite Avı (Sweep)"; yon = "LONG"; emoji = "🚀"
                stop = lows[-1] * 0.99; hedef = current_price * 1.03
                aciklama = "Fiyat eski dibin altına fitil attı ancak hemen üstüne kapandı. Akıllı para alıma geçmiş olabilir."

        if sinyal:
            hedef_pct = ((hedef - current_price) / current_price) * 100
            stop_pct = ((stop - current_price) / current_price) * 100
            if yon == "SHORT" and hedef >= current_price: continue
            if yon == "LONG" and hedef <= current_price: continue
            if abs(stop_pct) > 10.0 or not 1 <= abs(hedef_pct) <= 20: continue

            msg = (f"{emoji} <b>TEYİTLİ DÖNÜŞ ({yon}): {symbol}</b>\n"
                   f"<i>5m Formasyon - 15m Trend Teyitli</i>\n\n"
                   f"💰 <b>Giriş:</b> {akilli_fiyat(current_price)}\n"
                   f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                   f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                   f"📊 <b>Formasyon:</b> {formasyon_adi}\n"
                   f"📊 <b>Teyitler:</b> Hacim ({current_vol/avg_vol:.1f}x), RSI ({rsi:.1f}), EMA (15m)\n\n"
                   f"💡 <b>Açıklama:</b> {aciklama}")
            if tg(C, msg):
                signal_sent(son_formasyon_sinyalleri, symbol)
                log("FORMASYON", f"{symbol} Formasyon")

def formasyon_loop():
    safe_loop("FORMASYON", FORMASYON_ARALIGI, formasyon_worker, 60)

# =====================================================
# MOTOR 7: EMA RIBBON
# =====================================================
def ema_ribbon_worker():
    for t in adaylar(MIN_HACIM_EMA_RIBBON, MAX_TARAMA):
        symbol = t["symbol"]
        if not cooldown_ok(son_ema_ribbon_sinyalleri, symbol, 86400): continue
        ks = tamamlanmis(klines(symbol, "1h", 230))
        if len(ks) < 205: continue
        closes = [float(k[4]) for k in ks]
        vols = [float(k[7]) for k in ks]
        curr = closes[-1]
        curr_vol = vols[-1]
        avg_vol = sum(vols[-21:-1]) / 20
        if avg_vol <= 0 or curr_vol <= (avg_vol * 1.2): continue 
        
        e7 = calculate_ema(closes, 7)
        e25 = calculate_ema(closes, 25)
        e99 = calculate_ema(closes, 99)
        e200 = calculate_ema(closes, 200)
        if not all((e7, e25, e99, e200)): continue
        
        a, b, c, d = e7[-1], e25[-1], e99[-1], e200[-1]
        if curr > a > b > c > d:
            msg = (f"🚀 <b>EMA DİZİLİMİ (LONG): {symbol}</b>\n"
                   f"<i>1 Saatlik Grafik - Çoklu EMA Dizilimi</i>\n\n"
                   f"💰 <b>Giriş:</b> {akilli_fiyat(curr)}\n"
                   f"🎯 <b>Hedef:</b> {akilli_fiyat(curr * 1.20)} (+20.0%)\n"
                   f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(b * 0.95)}\n\n"
                   f"📊 <b>EMA Dizilimi:</b>\n"
                   f"EMA7: {akilli_fiyat(a)} | EMA25: {akilli_fiyat(b)}\n"
                   f"EMA99: {akilli_fiyat(c)} | EMA200: {akilli_fiyat(d)}\n"
                   f"📈 <b>Hacim Teyidi:</b> Alındı ({curr_vol/avg_vol:.1f}x)\n\n"
                   f"💡 <b>Açıklama:</b> EMA Ribbon dizilimi, piyasanın tüm zaman dilimlerinde mükemmel bir uyum içinde olduğunu gösterir.")
            if tg(C, msg):
                signal_sent(son_ema_ribbon_sinyalleri, symbol)
                log("EMA_RIBBON", f"{symbol} Ribbon")

def ema_ribbon_loop():
    safe_loop("EMA_RIBBON", EMA_RIBBON_ARALIGI, ema_ribbon_worker, 90)

# =====================================================
# MOTOR 8: MACD
# =====================================================
def macd_worker():
    for t in adaylar(MIN_HACIM_MACD, MAX_TARAMA):
        symbol = t["symbol"]
        if not cooldown_ok(son_macd_sinyalleri, symbol, 14400): continue
        ks = tamamlanmis(klines(symbol, "1d", 100))
        if len(ks) < 80: continue
        closes = [float(k[4]) for k in ks]
        vols = [float(k[7]) for k in ks]
        curr_price = closes[-1]
        curr_vol = vols[-1]
        avg_vol = sum(vols[-21:-1]) / 20
        if avg_vol <= 0 or curr_vol <= (avg_vol * 1.1): continue 
        
        cm, cs, pm, ps = calculate_macd(closes)
        if cm is None: continue
        
        if pm <= ps and cm > cs:
            msg = (f"🚀 <b>MACD SİNYALİ (LONG): {symbol}</b>\n"
                   f"<i>1 Günlük Grafik - MACD Momentum Kırılımı</i>\n\n"
                   f"💰 <b>Giriş:</b> {akilli_fiyat(curr_price)}\n"
                   f"🎯 <b>Hedef:</b> {akilli_fiyat(curr_price * 1.10)} (+10.0%)\n"
                   f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(curr_price * 0.97)} (-3.0%)\n\n"
                   f"📊 <b>MACD Çizgisi:</b> {cm:.5f} | <b>Sinyal Çizgisi:</b> {cs:.5f}\n"
                   f"📈 <b>Hacim Teyidi:</b> Alındı\n\n"
                   f"💡 <b>Açıklama:</b> MACD çizgisinin Sinyal çizgisini yukarı yönlü kesmesine 'Golden Cross' denir.")
            if tg(C, msg):
                signal_sent(son_macd_sinyalleri, symbol)
                log("MACD", f"{symbol} MACD")

def macd_loop():
    safe_loop("MACD", MACD_ARALIGI, macd_worker, 105)

# =====================================================
# MOTOR 9: ATH / ATL
# =====================================================
def ath_atl_worker():
    for t in adaylar(MIN_HACIM_ATH_ATL, MAX_ATH_ATL):
        symbol = t["symbol"]
        if not cooldown_ok(son_ath_atl_sinyalleri, symbol, 86400): continue
        ks = tamamlanmis(klines(symbol, "1d", 500))
        if len(ks) < 200: continue
        highs = [float(k[2]) for k in ks]
        lows = [float(k[3]) for k in ks]
        closes = [float(k[4]) for k in ks]
        vols = [float(k[7]) for k in ks]
        
        current_price = closes[-1]
        prev_close = closes[-2]
        current_vol = vols[-1]
        ath = max(highs[:-1])
        atl = min(lows[:-1])
        avg_vol = sum(vols[-21:-1]) / 20
        if avg_vol <= 0 or current_vol <= (avg_vol * 1.2): continue
        
        sinyal = None; yon = ""; emoji = ""; stop = 0; hedef = 0; aciklama = ""
        if prev_close >= atl and current_price < atl:
            sinyal = True; yon = "SHORT"; emoji = "💀"
            stop = atl * 1.05; hedef = current_price * 0.80
            aciklama = "Fiyat, geçmişteki en düşük seviyeyi hacimle aşağı kırdı."
        elif prev_close <= ath and current_price > ath:
            sinyal = True; yon = "LONG"; emoji = "🚀"
            stop = ath * 0.95; hedef = current_price * 1.20
            aciklama = "Fiyat, geçmişteki en yüksek seviyeyi hacimle yukarı kırdı."

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
                signal_sent(son_ath_atl_sinyalleri, symbol)
                log("ATH_ATL", f"{symbol} ATH/ATL")

def ath_atl_loop():
    safe_loop("ATH_ATL", ATH_ATL_ARALIGI, ath_atl_worker, 135)

# =====================================================
# MOTOR 10: ICHIMOKU BULUTU
# =====================================================
def ichimoku_worker():
    for t in adaylar(MIN_HACIM_ICHIMOKU, MAX_TARAMA):
        symbol = t["symbol"]
        if not cooldown_ok(son_ichimoku_sinyalleri, symbol, 14400): continue
        ks = tamamlanmis(klines(symbol, "1h", 120))
        if len(ks) < 90: continue
        
        highs = [float(k[2]) for k in ks]
        lows = [float(k[3]) for k in ks]
        closes = [float(k[4]) for k in ks]
        vols = [float(k[7]) for k in ks]
        
        curr_close = closes[-1]
        prev_close = closes[-2]
        avg_vol = sum(vols[-21:-1]) / 20
        if avg_vol <= 0 or vols[-1] <= avg_vol: continue 
        
        def calc_tenkan(idx): return (max(highs[idx-8:idx+1]) + min(lows[idx-8:idx+1])) / 2
        def calc_kijun(idx): return (max(highs[idx-25:idx+1]) + min(lows[idx-25:idx+1])) / 2
        
        idx_curr = len(closes) - 1
        tenkan_curr = calc_tenkan(idx_curr)
        kijun_curr = calc_kijun(idx_curr)
        
        span_a_curr = (calc_tenkan(idx_curr - 26) + calc_kijun(idx_curr - 26)) / 2 if idx_curr >= 26 else None
        span_a_prev = (calc_tenkan(idx_curr - 27) + calc_kijun(idx_curr - 27)) / 2 if idx_curr >= 27 else None
        span_b_curr = (max(highs[idx_curr-77:idx_curr-26]) + min(lows[idx_curr-77:idx_curr-26])) / 2 if idx_curr >= 77 else None
        span_b_prev = (max(highs[idx_curr-78:idx_curr-27]) + min(lows[idx_curr-78:idx_curr-27])) / 2 if idx_curr >= 78 else None
        
        if None in (span_a_curr, span_a_prev, span_b_curr, span_b_prev): continue

        cloud_top_curr = max(span_a_curr, span_b_curr)
        cloud_bottom_curr = min(span_a_curr, span_b_curr)
        cloud_thickness = cloud_top_curr - cloud_bottom_curr
        cloud_top_prev = max(span_a_prev, span_b_prev)
        cloud_bottom_prev = min(span_a_prev, span_b_prev)

        sinyal = None; yon = ""; emoji = ""; stop = 0; hedef = 0; aciklama = ""
        if prev_close <= cloud_top_prev and curr_close > cloud_top_curr and curr_close > kijun_curr and tenkan_curr > kijun_curr:
            if cloud_thickness > (curr_close * 0.005):
                sinyal = True; yon = "LONG (Bulut Üstü Kırılım)"; emoji = "🚀"
                stop = min(kijun_curr, cloud_bottom_curr) * 0.99 
                hedef = curr_close + (cloud_thickness * 2) 
                aciklama = "Fiyat Ichimoku bulutunu yukarı yönlü kırdı."

        elif prev_close >= cloud_bottom_prev and curr_close < cloud_bottom_curr and curr_close < kijun_curr and tenkan_curr < kijun_curr:
            if cloud_thickness > (curr_close * 0.005):
                sinyal = True; yon = "SHORT (Bulut Altı Kırılım)"; emoji = "🔻"
                stop = max(kijun_curr, cloud_top_curr) * 1.01 
                hedef = curr_close - (cloud_thickness * 2)
                aciklama = "Fiyat Ichimoku bulutunu aşağı yönlü kırdı."

        if sinyal:
            hedef_pct = ((hedef - curr_close) / curr_close) * 100
            stop_pct = ((stop - curr_close) / curr_close) * 100
            if abs(hedef_pct) < 1.0: continue 

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
                signal_sent(son_ichimoku_sinyalleri, symbol)
                log("ICHIMOKU", f"{symbol} Ichimoku")

def ichimoku_loop():
    safe_loop("ICHIMOKU", ICHIMOKU_ARALIGI, ichimoku_worker, 75)

# =====================================================
# MOTOR 11: GRAFİK FORMASYONLARI
# =====================================================
def chart_patterns_worker():
    for t in adaylar(MIN_HACIM_PATTERN, MAX_PATTERN):
        symbol = t["symbol"]
        if not cooldown_ok(son_chart_pattern_sinyalleri, symbol, 14400): continue
        for tf in ['4h', '1d', '3d']:
            ks = tamamlanmis(klines(symbol, tf, 110))
            if len(ks) < 50: continue
            
            highs = [float(k[2]) for k in ks]
            lows = [float(k[3]) for k in ks]
            closes = [float(k[4]) for k in ks]
            vols = [float(k[7]) for k in ks]
            
            curr_close = closes[-1] 
            curr_vol = vols[-1]
            avg_vol = sum(vols[-21:-1]) / 20 
            if avg_vol <= 0 or curr_vol < avg_vol * 1.2: continue

            p_highs, p_lows = find_pivots(highs, lows) 
            sinyal = None; formasyon_adi = ""; yon = ""; emoji = ""; hedef = 0; stop = 0; neckline = 0
            
            if len(p_highs) >= 2:
                p1_idx, p1_price = p_highs[-2]
                p2_idx, p2_price = p_highs[-1]
                if 5 <= (p2_idx - p1_idx) <= 30 and abs(p1_price - p2_price) / p1_price <= 0.02:
                    neckline = min(lows[p1_idx:p2_idx+1])
                    if curr_close < neckline * 0.99:
                        sinyal = True; formasyon_adi = "İkili Tepe (M)"; yon = "SHORT"; emoji = "🔻"
                        stop = max(p1_price, p2_price) * 1.02
                        hedef = neckline - (max(p1_price, p2_price) - neckline)

            if not sinyal and len(p_lows) >= 2:
                p1_idx, p1_price = p_lows[-2]
                p2_idx, p2_price = p_lows[-1]
                if 5 <= (p2_idx - p1_idx) <= 30 and abs(p1_price - p2_price) / p1_price <= 0.02:
                    neckline = max(highs[p1_idx:p2_idx+1])
                    if curr_close > neckline * 1.01:
                        sinyal = True; formasyon_adi = "İkili Dip (W)"; yon = "LONG"; emoji = "🚀"
                        stop = min(p1_price, p2_price) * 0.98
                        hedef = neckline + (neckline - min(p1_price, p2_price))

            if sinyal:
                hedef_pct = ((hedef - curr_close) / curr_close) * 100
                stop_pct = ((stop - curr_close) / curr_close) * 100
                if yon == "SHORT" and hedef >= curr_close: continue
                if yon == "LONG" and hedef <= curr_close: continue
                if abs(stop_pct) > 15.0 or not 1.5 <= abs(hedef_pct) <= 40.0: continue

                msg = (f"{emoji} <b>GRAFİK FORMASYONU ({yon}): {symbol}</b>\n"
                       f"<i>{tf.upper()} Grafik - {formasyon_adi}</i>\n\n"
                       f"💰 <b>Giriş:</b> {akilli_fiyat(curr_close)}\n"
                       f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                       f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                       f"📊 <b>Boyun Çizgisi:</b> {akilli_fiyat(neckline)}\n"
                       f"📈 <b>Hacim Teyidi:</b> Alındı ({curr_vol/avg_vol:.1f}x)\n\n"
                       f"💡 <b>Açıklama:</b> Fiyat, klasik bir dönüş formasyonunu tamamlayarak boyun çizgisini hacimle kırdı.")
                if tg(C, msg):
                    signal_sent(son_chart_pattern_sinyalleri, symbol)
                    log("PATTERN", f"{symbol} Pattern")
                break

def chart_patterns_loop():
    safe_loop("PATTERN", CHART_PATTERN_ARALIGI, chart_patterns_worker, 150)

# =====================================================
# MOTOR 12: TOP GAINERS SHORT AVCISI
# =====================================================
def top_gainers_short_worker():
    rows = [t for t in futures_tickers() if t["_quote"] >= MIN_HACIM_PUMP]
    rows.sort(key=lambda x: x["_change"], reverse=True)
    top_gainers = [t for t in rows if t["_change"] >= 15][:5]

    for t in top_gainers:
        symbol = t['symbol']
        if not cooldown_ok(son_short_gainers_sinyalleri, symbol, 43200): continue
        ks = tamamlanmis(klines(symbol, "15m", 55))
        if len(ks) < 30: continue
        
        closes = [float(k[4]) for k in ks]
        highs = [float(k[2]) for k in ks]
        vols = [float(k[7]) for k in ks]
        
        e9 = calculate_ema(closes, 9)
        e21 = calculate_ema(closes, 21)
        if not e9 or not e21: continue
        
        avg_vol = sum(vols[-21:-1]) / 20
        if avg_vol <= 0 or vols[-1] < avg_vol: continue
        
        rsi = calculate_rsi(closes)
        recent_high = max(highs[-20:])
        drop_from_top = ((recent_high - closes[-1]) / recent_high) * 100
        
        if rsi > 55 and e9[-1] < e21[-1] and closes[-1] < closes[-2] and drop_from_top > 2.0:
            price = closes[-1]
            msg = (f"🩸 <b>TOP GAINERS SHORT FIRSATI: {symbol}</b>\n"
                   f"<i>15m Grafik - Kâr Realizasyonu</i>\n\n"
                   f"⚠️ <i>Coin 24 saatte <b>+{t['_change']:.1f}%</b> yükselmiş ve aşırı alım bölgesinde. Short fırsatı başlıyor olabilir!</i>\n\n"
                   f"💰 <b>Giriş (Short):</b> {akilli_fiyat(price)}\n"
                   f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(recent_high * 1.01)}\n"
                   f"🎯 <b>Hedef 1:</b> {akilli_fiyat(price * 0.95)} (-5.0%)\n"
                   f"🎯 <b>Hedef 2:</b> {akilli_fiyat(price * 0.90)} (-10.0%)\n\n"
                   f"📊 <b>RSI:</b> {rsi:.1f} | 📉 <b>EMA Kesişimi:</b> EMA9 < EMA21\n")
            if tg(C, msg):
                signal_sent(son_short_gainers_sinyalleri, symbol)
                log("SHORT_GAINERS", f"{symbol} Short Adayı")

def top_gainers_short_loop():
    safe_loop("SHORT_GAINERS", SHORT_GAINERS_ARALIGI, top_gainers_short_worker, 180)

# =====================================================
# MOTOR 13: TOP MOVERS (SAATLİK HAREKETLİLER)
# =====================================================
def top_movers_worker():
    rows = [t for t in futures_tickers() if t["_quote"] > MIN_HACIM_MOVERS]
    rows.sort(key=lambda x: x["_quote"], reverse=True)
    
    saatlik_veriler = []
    for t in rows[:80]:
        symbol = t['symbol']
        ks = tamamlanmis(klines(symbol, "1h", 2))
        if not ks: continue
        open_price = float(ks[-1][1])
        close_price = float(ks[-1][4])
        if open_price > 0:
            degisim = ((close_price - open_price) / open_price) * 100
            saatlik_veriler.append({'symbol': symbol, 'change': degisim, 'price': close_price})

    if not saatlik_veriler: return
    saatlik_veriler.sort(key=lambda x: x['change'], reverse=True)
    
    top_3_up = saatlik_veriler[:3]
    top_3_down = list(reversed(saatlik_veriler[-3:]))

    msg = "⏱️ <b>Son 1 Saatin Hareketlileri (Futures)</b>\n\n"
    msg += "🟢 <b>En Çok Yükselenler:</b>\n"
    for i, item in enumerate(top_3_up, 1):
        sym = item['symbol'].replace("USDT", "")
        msg += f"{i}. <b>{sym}</b> {item['change']:+.2f}% | {akilli_fiyat(item['price'])}\n"
        
    msg += "\n🔴 <b>En Çok Düşenler:</b>\n"
    for i, item in enumerate(top_3_down, 1):
        sym = item['symbol'].replace("USDT", "")
        msg += f"{i}. <b>{sym}</b> {item['change']:+.2f}% | {akilli_fiyat(item['price'])}\n"
        
    tg(C, msg)

def top_movers_loop():
    safe_loop("TOP_MOVERS", TOP_MOVERS_ARALIGI, top_movers_worker, 200)

# =====================================================
# MOTOR 14: PİYASA GENİŞLİĞİ VE BİLGİ RAPORU
# =====================================================
def dominans_worker():
    rows = futures_tickers()
    if not rows: return
    positive = [x for x in rows if x["_change"] > 0]
    negative = [x for x in rows if x["_change"] < 0]
    total = len(rows)
    up_pct = len(positive)/total*100
    down_pct = len(negative)/total*100
    up_vol = sum(x["_quote"] for x in positive)
    down_vol = sum(x["_quote"] for x in negative)
    btc = next((x for x in rows if x["symbol"] == "BTCUSDT"), None)
    eth = next((x for x in rows if x["symbol"] == "ETHUSDT"), None)

    if up_pct >= 60 and up_vol > down_vol:
        status = "🟢 GENEL PİYASA POZİTİF"
        detail = "USDT vadeli sözleşmelerin çoğu yükselişte."
    elif down_pct >= 60 and down_vol > up_vol:
        status = "🔴 GENEL PİYASA NEGATİF"
        detail = "Sözleşmelerin çoğu düşüş baskısı altında."
    else:
        status = "🟡 KARIŞIK / YATAY PİYASA"
        detail = "Piyasada net bir üstünlük bulunmuyor."

    msg = (f"🌐 <b>BİNANCE FUTURES PİYASA GENİŞLİĞİ</b>\n\n"
           f"🟢 <b>Yükselen sözleşmeler:</b> %{up_pct:.1f} ({len(positive)})\n"
           f"🔴 <b>Düşen sözleşmeler:</b> %{down_pct:.1f} ({len(negative)})\n")
    if btc: msg += f"₿ <b>BTC 24s:</b> {btc['_change']:+.2f}%\n"
    if eth: msg += f"♦️ <b>ETH 24s:</b> {eth['_change']:+.2f}%\n"
    msg += f"\n📊 <b>Durum:</b> {status}\n💡 {detail}"
    tg(C, msg)

def dominans_loop():
    safe_loop("PIYASA_GENISLIGI", DOMINANS_ARALIGI, dominans_worker, 240)

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
    def log_message(self, format_string, *args): return

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    log("SİSTEM", f"WEB SUNUCUSU BAŞLADI - PORT: {port}")
    server.serve_forever()

# =====================================================
# ANA ÇALIŞTIRMA BLOKU
# =====================================================
if __name__ == "__main__":
    log("SİSTEM", "Bot başlatılıyor...")
    
    motors = [
        ("TOP_GAINERS", top_gainers_loop),
        ("PUMP_WINNER", pump_winner_loop),
        ("CHARTIST", chartist_loop),
        ("PUMP", pump_loop),
        ("VOLATILITE", volatilite_loop),
        ("FORMASYON", formasyon_loop),
        ("EMA_RIBBON", ema_ribbon_loop),
        ("MACD", macd_loop),
        ("ATH_ATL", ath_atl_loop),
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
        log("RENDER", f"Web sunucusu hatası: {e}")
        while True:
            time.sleep(60)

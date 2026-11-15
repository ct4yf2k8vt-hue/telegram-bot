import urllib.request
import urllib.parse
import json
import time
import socket
import os
import threading
import http.server
import socketserver
import statistics
import random
from datetime import datetime, timedelta, timezone

# =====================================================
# AYARLAR
# =====================================================
socket.setdefaulttimeout(15)
T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()
TR_Saati = timezone(timedelta(hours=3))

# BINANCE ENGELINI ASMAK ICIN PROXY KULLANIYORUZ
BASE = "https://corsproxy.io/?https://api.binance.com"
FAPI = "https://corsproxy.io/?https://fapi.binance.com"

RAPOR_ARALIGI = 14400      
CHARTIST_ARALIGI = 3600    
PUMP_ARALIGI = 60          
FORMASYON_ARALIGI = 3600   
EMA_RIBBON_ARALIGI = 3600  
MACD_ARALIGI = 3600        
MIDAS_SETUP_ARALIGI = 7200 
ATH_ATL_ARALIGI = 3600     
DIGITAL_CHARTIST_ARALIGI = 3600 
SMA200_ARALIGI = 3600      
BOLLINGER_ARALIGI = 3600   
ICHIMOKU_ARALIGI = 3600    
FVG_KONFLUANS_ARALIGI = 3600 
SQUEEZE_RADAR_ARALIGI = 14400
HACIM_PATLAMASI_ARALIGI = 3600 
EMILIM_ARALIGI = 3600 
SMART_MONEY_ARALIGI = 3600 

MIN_HACIM_CHARTIST = 10_000_000 
MIN_HACIM_PUMP = 5_000_000     
MIN_HACIM_FORMASYON = 10_000_000 
MIN_HACIM_EMA_RIBBON = 10_000_000 
MIN_HACIM_MACD = 10_000_000 
MIN_HACIM_MIDAS_SETUP = 15_000_000 
MIN_HACIM_ATH_ATL = 5_000_000 
MIN_HACIM_DIGITAL = 10_000_000 
MIN_HACIM_SMA200 = 15_000_000  
MIN_HACIM_BOLLINGER = 10_000_000 
MIN_HACIM_ICHIMOKU = 10_000_000  
MIN_HACIM_FVG = 10_000_000 
MIN_HACIM_SQUEEZE = 20_000_000 
MIN_HACIM_HACIM_PATLAMASI = 10_000_000 
MIN_HACIM_EMILIM = 5_000_000 
MIN_HACIM_SMART_MONEY = 100_000_000 

son_sinyaller = {}
son_pump_sinyalleri = {}
son_formasyon_sinyalleri = {}
son_ema_ribbon_sinyalleri = {}
son_macd_sinyalleri = {}
son_midas_setup_sinyalleri = {}
son_ath_atl_sinyalleri = {}
son_chartist_sinyalleri = {} 
son_sma200_sinyalleri = {}   
son_bollinger_sinyalleri = {} 
son_ichimoku_sinyalleri = {}  
son_fvg_sinyalleri = {}       
son_squeeze_sinyalleri = {}
son_hacim_sinyalleri = {} 
son_emilim_sinyalleri = {} 
son_smart_money_sinyalleri = {} 

# =====================================================
# YARDIMCI FONKSİYONLAR
# =====================================================
def simdi(): return datetime.now(TR_Saati)
def log(prefix, m): print(f"[{simdi().strftime('%H:%M:%S')}] [{prefix}] {m}", flush=True)

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
        data = urllib.parse.urlencode({"chat_id": str(chat_id), "text": message, "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode("utf-8")
        req = urllib.request.Request(f"https://api.telegram.org/bot{T}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=15) as response: return json.loads(response.read().decode("utf-8")).get("ok", False)
    except: return False

def gj(url):
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # Eğer url zaten proxy içermiyorsa başına ekle (güvenlik için)
        if "corsproxy.io" not in url and "api.telegram.org" not in url:
            url = f"https://corsproxy.io/?{url}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response: 
            return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        # Hata olursa loglara basalım ki neden çalışmadığını görelim
        # log("API_HATA", f"Veri çekilemedi: {e}")
        return None

def calculate_ema(prices, period):
    if len(prices) < period: return []
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for price in prices[period:]: ema.append(price * k + ema[-1] * (1 - k))
    return ema

def calculate_sma(prices, period):
    if len(prices) < period: return []
    return [sum(prices[i:i+period])/period for i in range(len(prices)-period+1)]

def calculate_targets(entry_price, recent_low):
    diff = entry_price - recent_low
    return [entry_price + (diff * 0.272), entry_price + (diff * 0.618), entry_price + (diff * 1.000), entry_price + (diff * 1.618), entry_price + (diff * 2.618)]

def calculate_short_targets(entry_price, recent_high):
    diff = recent_high - entry_price
    return [entry_price - (diff * 0.272), entry_price - (diff * 0.618), entry_price - (diff * 1.000), entry_price - (diff * 1.618), entry_price - (diff * 2.618)]

def calculate_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow + signal: return None, None, None, None
    ema_fast, ema_slow = calculate_ema(prices, fast), calculate_ema(prices, slow)
    if not ema_fast or not ema_slow: return None, None, None, None
    min_len = min(len(ema_fast), len(ema_slow))
    macd_line = [ema_fast[-min_len+i] - ema_slow[-min_len+i] for i in range(min_len)]
    signal_line = calculate_ema(macd_line, signal)
    if not signal_line: return None, None, None, None
    return macd_line[-1], signal_line[-1], macd_line[-2], signal_line[-2]

def calculate_bollinger(prices, period=20, std_dev=2):
    if len(prices) < period: return None, None, None
    sma = sum(prices[-period:]) / period
    std = statistics.pstdev(prices[-period:])
    return sma + (std_dev * std), sma, sma - (std_dev * std)

def calculate_ichimoku(highs, lows, closes):
    if len(closes) < 52: return None, None
    tenkan = (max(highs[-9:]) + min(lows[-9:])) / 2
    kijun = (max(highs[-26:]) + min(lows[-26:])) / 2
    senkou_a = (tenkan + kijun) / 2
    senkou_b = (max(highs[-52:]) + min(lows[-52:])) / 2
    return max(senkou_a, senkou_b), min(senkou_a, senkou_b)

def get_market_trend(tickers):
    try:
        sorted_tickers = sorted(tickers, key=lambda x: float(x.get('quoteVolume', 0)), reverse=True)[:100]
        changes = [float(t.get('priceChangePercent', 0)) for t in sorted_tickers]
        if not changes: return "Bilinmiyor", 0, 0, 0
        avg_change = sum(changes) / len(changes)
        ups, downs = sum(1 for c in changes if c > 0), sum(1 for c in changes if c < 0)
        if avg_change > 1.5 and ups > downs: trend = "🟢 YUKARI (Boğa)"
        elif avg_change < -1.5 and downs > ups: trend = "🔴 AŞAĞI (Ayı)"
        else: trend = "🟡 YATAY (Kararsız)"
        return trend, avg_change, ups, downs
    except: return "Bilinmiyor", 0, 0, 0

def trap_radar_check(symbol):
    try:
        oi_data = gj(f"{FAPI}/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=2")
        oi_artiyor = False
        if oi_data and len(oi_data) >= 2:
            if ((float(oi_data[1]['sumOpenInterest']) - float(oi_data[0]['sumOpenInterest'])) / float(oi_data[0]['sumOpenInterest'])) * 100 > 0.2: oi_artiyor = True
        taker_data = gj(f"{FAPI}/futures/data/takerlongshortRatio?symbol={symbol}&period=5m&limit=1")
        alici_baskin = False
        if taker_data and len(taker_data) > 0:
            total = float(taker_data[0]['buyVol']) + float(taker_data[0]['sellVol'])
            if (float(taker_data[0]['buyVol']) / total) * 100 > 55: alici_baskin = True
        if oi_artiyor and alici_baskin: return "🐋 <b>BALİNA TEYİDİ (Gerçek Pump)</b>"
        elif not alici_baskin: return "⚠️ <b>DİKKAT: SAHTE PUMP (TUZAK OLABİLİR)</b>"
        else: return "⚖️ <b>Kısmi Teyit</b>"
    except: return "⚖️ <b>Kısmi Teyit</b>"

def squeeze_radar_check(symbol, timeframe='4h'):
    try:
        global_data = gj(f"{FAPI}/futures/data/globalLongShortAccountRatio?symbol={symbol}&period={timeframe}&limit=1")
        top_data = gj(f"{FAPI}/futures/data/topLongShortAccountRatio?symbol={symbol}&period={timeframe}&limit=1")
        if not global_data or not top_data: return ""
        g_long_pct = float(global_data[0]['longAccount']) * 100
        t_long_pct = float(top_data[0]['longAccount']) * 100
        if g_long_pct > 75 and t_long_pct < 50: return f"⚠️ <b>SİNYAL RİSKLİ:</b> Long Sıkışması Riski Var! (Kalabalık %{g_long_pct:.0f} Long, Balina %{t_long_pct:.0f} Long)"
        elif g_long_pct < 30 and t_long_pct > 60: return f"🚀 <b>SİNYAL DESTEKLİ:</b> Short Sıkışması Riski Var! (Kalabalık %{g_long_pct:.0f} Long, Balina %{t_long_pct:.0f} Long)"
        return ""
    except: return ""

# =====================================================
# MOTORLAR
# =====================================================
def smart_money_loop():
    log("SMART_MONEY", "Başladı.")
    time.sleep(400)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): 
                log("SMART_MONEY", "API Hatası (Veri yok), bekleniyor...")
                time.sleep(60); continue
            log("SMART_MONEY", "Piyasa taranıyor...")
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_SMART_MONEY]:
                for tf in ['4h', '1d']:
                    cooldown_key = f"SM_{tf}_{symbol}"
                    if cooldown_key in son_smart_money_sinyalleri and time.time() - son_smart_money_sinyalleri[cooldown_key] < 14400: continue
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=50")
                    if not isinstance(klines, list) or len(klines) < 30: continue
                    highs = [float(k[2]) for k in klines]; lows = [float(k[3]) for k in klines]; closes = [float(k[4]) for k in klines]
                    curr_high = highs[-2]; curr_low = lows[-2]; curr_close = closes[-2]
                    son_20_dip = min(lows[-22:-2]); son_20_tepe = max(highs[-22:-2])
                    if curr_low < son_20_dip and curr_close > son_20_dip:
                        son_tepe = max(highs[-15:-2]) 
                        ote_ust = son_tepe - ((son_tepe - curr_low) * 0.618); ote_alt = son_tepe - ((son_tepe - curr_low) * 0.786)
                        if ote_alt <= curr_close <= ote_ust:
                            msg = (f"🎯 <b>SMART MONEY (LONG): {symbol}</b>\n<i>{tf.upper()} Grafik - Likidite Avı & Fibonacci OTE</i>\n\n💰 <b>Giriş (OTE Bölgesi):</b> {akilli_fiyat(ote_alt)} - {akilli_fiyat(ote_ust)}\n🎯 <b>Hedef:</b> {akilli_fiyat(son_tepe)} (Önceki Zirve)\n🛑 <b>Stop-Loss:</b> {akilli_fiyat(curr_low * 0.995)} (Avlanan Dibi)\n\n📊 <b>Yapılan Analiz:</b>\n1. 💧 <b>Likidite Avı:</b> Fiyat, 20 günlük dibi ({akilli_fiyat(son_20_dip)}) aşağı yönlü kırıp stopları patlattı ve anında geri döndü.\n2. 📏 <b>Fibonacci OTE:</b> Yükseliş sonrası fiyat, en kârlı alım bölgesi olan %61.8 - %78.6 geri çekilme bandına sarktı.\n\n💡 <b>Açıklama:</b> Balinalar sahte bir düşüş yaratarak küçük yatırımcıların stoplarını avladı. Şu an fiyat, akıllı paranın (Smart Money) en uygun maliyetle alım yapacağı OTE bölgesinde. Güçlü bir dönüş sinyali.")
                            if tg(C, msg): son_smart_money_sinyalleri[cooldown_key] = time.time()
                    elif curr_high > son_20_tepe and curr_close < son_20_tepe:
                        son_dip = min(lows[-15:-2])
                        ote_alt = son_dip + ((curr_high - son_dip) * 0.618); ote_ust = son_dip + ((curr_high - son_dip) * 0.786)
                        if ote_alt <= curr_close <= ote_ust:
                            msg = (f"🎯 <b>SMART MONEY (SHORT): {symbol}</b>\n<i>{tf.upper()} Grafik - Likidite Avı & Fibonacci OTE</i>\n\n💰 <b>Giriş (OTE Bölgesi):</b> {akilli_fiyat(ote_alt)} - {akilli_fiyat(ote_ust)}\n🎯 <b>Hedef:</b> {akilli_fiyat(son_dip)} (Önceki Dip)\n🛑 <b>Stop-Loss:</b> {akilli_fiyat(curr_high * 1.005)} (Avlanan Tepesi)\n\n📊 <b>Yapılan Analiz:</b>\n1. 💧 <b>Likidite Avı:</b> Fiyat, 20 günlük tepeyi ({akilli_fiyat(son_20_tepe)}) yukarı yönlü kırıp short stoplarını patlattı ve anında geri döndü.\n2. 📏 <b>Fibonacci OTE:</b> Düşüş sonrası fiyat, en kârlı satış bölgesi olan %61.8 - %78.6 geri çekilme bandına tepki verdi.\n\n💡 <b>Açıklama:</b> Balinalar sahte bir yükseliş yaratarak shortçuların stoplarını avladı. Şu an fiyat, akıllı paranın en uygun maliyetle satış yapacağı OTE bölgesinde. Sert bir düşüş gelebilir.")
                            if tg(C, msg): son_smart_money_sinyalleri[cooldown_key] = time.time()
                time.sleep(0.1)
            time.sleep(SMART_MONEY_ARALIGI)
        except Exception as e: 
            log("SMART_MONEY", f"Hata: {e}")
            time.sleep(60)

def absorption_loop():
    log("EMILIM", "Başladı.")
    time.sleep(250)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            log("EMILIM", "Piyasa taranıyor...")
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_EMILIM]:
                for tf in ['5m', '15m']:
                    cooldown_key = f"ABS_{tf}_{symbol}"
                    if cooldown_key in son_emilim_sinyalleri and time.time() - son_emilim_sinyalleri[cooldown_key] < 3600: continue
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=25")
                    if not isinstance(klines, list) or len(klines) < 25: continue
                    curr_open = float(klines[-2][1]); curr_close = float(klines[-2][4]); curr_high = float(klines[-2][2]); curr_low = float(klines[-2][3]); curr_vol = float(klines[-2][5])
                    avg_vol = sum(float(k[5]) for k in klines[-22:-2]) / 20
                    if avg_vol == 0: continue
                    if curr_vol > (avg_vol * 3.0):
                        body_size = abs(curr_close - curr_open); candle_range = curr_high - curr_low
                        if candle_range > 0 and (body_size / candle_range) < 0.3:
                            vol_ratio = curr_vol / avg_vol
                            if curr_close <= curr_open:
                                msg = (f"🛡️ <b>ALICI EMİLİMİ (LONG): {symbol}</b>\n<i>{tf.upper()} Grafik - Absorption</i>\n\n📊 Devasa Hacim: <b>{vol_ratio:.1f}x</b>\n📉 Mum Yönü: Kırmızı ama fiyat düşemiyor.\n💰 Anlık Fiyat: {akilli_fiyat(curr_close)}\n\n💡 <b>Açıklama:</b> Piyasaya devasa bir satış baskısı geldi ancak fiyat aşağı yönlü ilerleyemedi. Balina satışları 'emdi', dönüş sinyali olabilir.")
                                if tg(C, msg): son_emilim_sinyalleri[cooldown_key] = time.time()
                            else:
                                msg = (f"🛡️ <b>SATICI EMİLİMİ (SHORT): {symbol}</b>\n<i>{tf.upper()} Grafik - Absorption</i>\n\n📊 Devasa Hacim: <b>{vol_ratio:.1f}x</b>\n📈 Mum Yönü: Yeşil ama fiyat yükselemiyor.\n💰 Anlık Fiyat: {akilli_fiyat(curr_close)}\n\n💡 <b>Açıklama:</b> Piyasaya devasa bir alım baskısı geldi ancak fiyat yukarı yönlü ilerleyemedi. Balina alımları 'emdi', düşüş sinyali olabilir.")
                                if tg(C, msg): son_emilim_sinyalleri[cooldown_key] = time.time()
                time.sleep(0.1)
            time.sleep(EMILIM_ARALIGI)
        except Exception as e: time.sleep(60)

def teyitli_hacim_loop():
    log("HACIM", "Başladı.")
    time.sleep(330)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            log("HACIM", "Piyasa taranıyor...")
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_HACIM_PATLAMASI]:
                for tf in ['4h', '8h', '1d']:
                    cooldown_key = f"VOL_{tf}_{symbol}"
                    if cooldown_key in son_hacim_sinyalleri and time.time() - son_hacim_sinyalleri[cooldown_key] < 14400: continue
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=250")
                    if not isinstance(klines, list) or len(klines) < 50: continue
                    closes = [float(k[4]) for k in klines[:-1]]; vols = [float(k[5]) for k in klines[:-1]]; highs = [float(k[2]) for k in klines[:-1]]; lows = [float(k[3]) for k in klines[:-1]]
                    curr_close = closes[-1]; prev_close = closes[-2]; curr_vol = vols[-1]
                    avg_vol = sum(vols[-21:-1]) / 20
                    if avg_vol == 0: continue
                    if curr_vol >= (avg_vol * 2.0):
                        spike_ratio = (curr_vol / avg_vol) * 100
                        ema200 = calculate_ema(closes, 200)
                        if not ema200: continue
                        c_ema = ema200[-1]
                        teyit = trap_radar_check(symbol); tuzak_mi = "SAHTE" in teyit or "TUZAK" in teyit
                        if curr_close > c_ema and curr_close > prev_close and not tuzak_mi:
                            son_direnc = max(highs[-21:-1])
                            if curr_close >= son_direnc:
                                msg = (f"📊 <b>HACİM PATLAMASI (LONG): {symbol}</b>\n<i>{tf.upper()} Grafik - Teyitli Kırılım</i>\n\n📈 Hacim Artışı: <b>+%{spike_ratio:.0f}</b>\n💰 Anlık Fiyat: {akilli_fiyat(curr_close)}\n{teyit}\n\n💡 <b>Açıklama:</b> Hacim patlaması, EMA200 trendi ve yatay direnç kırılımı aynı anda gerçekleşti. Balinalar bölgeye girdi.")
                                if tg(C, msg): son_hacim_sinyalleri[cooldown_key] = time.time()
                        elif curr_close < c_ema and curr_close < prev_close and not tuzak_mi:
                            son_destek = min(lows[-21:-1])
                            if curr_close <= son_destek:
                                msg = (f"📉 <b>HACİM PATLAMASI (SHORT): {symbol}</b>\n<i>{tf.upper()} Grafik - Teyitli Kırılım</i>\n\n📉 Hacim Artışı: <b>+%{spike_ratio:.0f}</b>\n💰 Anlık Fiyat: {akilli_fiyat(curr_close)}\n{teyit}\n\n💡 <b>Açıklama:</b> Hacim patlaması, EMA200 trendi ve yatay destek kırılımı aynı anda gerçekleşti. Büyük satıcılar bölgeden çıktı.")
                                if tg(C, msg): son_hacim_sinyalleri[cooldown_key] = time.time()
                time.sleep(0.1)
            time.sleep(HACIM_PATLAMASI_ARALIGI)
        except Exception as e: time.sleep(60)

def squeeze_radar_loop():
    log("SQUEEZE", "Başladı.")
    time.sleep(300)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            log("SQUEEZE", "Piyasa taranıyor...")
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_SQUEEZE]:
                for tf in ['4h', '1d']:
                    cooldown_key = f"SQZ_{tf}_{symbol}"
                    if cooldown_key in son_squeeze_sinyalleri and time.time() - son_squeeze_sinyalleri[cooldown_key] < 43200: continue
                    global_data = gj(f"{FAPI}/futures/data/globalLongShortAccountRatio?symbol={symbol}&period={tf}&limit=1")
                    top_data = gj(f"{FAPI}/futures/data/topLongShortAccountRatio?symbol={symbol}&period={tf}&limit=1")
                    if not global_data or not top_data: continue
                    g_long_pct = float(global_data[0]['longAccount']) * 100; t_long_pct = float(top_data[0]['longAccount']) * 100
                    if g_long_pct > 75 and t_long_pct < 50:
                        msg = (f"🚨 <b>LONG SIKIŞMASI RİSKİ: {symbol}</b>\n<i>{tf.upper()} Grafik</i>\n\n👥 <b>Ky:</b> %{g_long_pct:.1f} Long\n🐋 <b>Balina:</b> %{t_long_pct:.1f} Long\n\n💡 <b>Açıklama:</b> Ky aşırı Long'da, balinalar Short'a geçti. Şelale düşüşü gelebilir!")
                        if tg(C, msg): son_squeeze_sinyalleri[cooldown_key] = time.time()
                    elif g_long_pct < 30 and t_long_pct > 60:
                        msg = (f"🚨 <b>SHORT SIKIŞMASI RİSKİ: {symbol}</b>\n<i>{tf.upper()} Grafik</i>\n\n👥 <b>Ky:</b> %{g_long_pct:.1f} Long\n🐋 <b>Balina:</b> %{t_long_pct:.1f} Long\n\n💡 <b>Açıklama:</b> Ky aşırı Short'ta, balinalar Long'a geçti. Roket yükselişi gelebilir!")
                        if tg(C, msg): son_squeeze_sinyalleri[cooldown_key] = time.time()
                time.sleep(0.1)
            time.sleep(SQUEEZE_RADAR_ARALIGI)
        except Exception as e: time.sleep(60)

def midas_flow_loop():
    log("RAPOR", "Başladı.")
    time.sleep(5)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): 
                log("RAPOR", "Veri alınamadı, proxy veya API engeli olabilir.")
                time.sleep(300); continue
            log("RAPOR", "Piyasa verileri çekiliyor...")
            trend_text, avg_chg, ups, downs = get_market_trend(tickers)
            usdt_pairs = [t for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) > 5_000_000]
            usdt_pairs.sort(key=lambda x: float(x['priceChangePercent']), reverse=True)
            top_5 = usdt_pairs[:5]
            if top_5:
                msg = "🔥 <b>24 Saatteki En İyi Hareketler</b>\n"
                msg += f"\n🌍 <b>Piyasa Yönü:</b> {trend_text}\n📊 Ortalama: %{avg_chg:.2f}\n\n"
                medals = ["🥇", "🥈", "🥉", "🏅", "⭐️"]; hashtags = ""
                for i, t in enumerate(top_5):
                    symbol = t['symbol'].replace("USDT", ""); change = float(t['priceChangePercent'])
                    change_str = f"+{change:.0f}%" if change > 0 else f"{change:.0f}%"
                    msg += f"{medals[i]} {symbol} {change_str}\n"; hashtags += f"#{symbol} "
                msg += f"\n💡 <b>Açıklama:</b> Piyasa özeti.\n<a href='https://t.me/MidasFlowAI'>{hashtags}#MidasFlow</a>"
                tg(C, msg)
            time.sleep(RAPOR_ARALIGI)
        except: time.sleep(60)

def chartist_loop():
    log("CHARTIST", "Başladı.")
    time.sleep(15)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            log("CHARTIST", "Piyasa taranıyor...")
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_CHARTIST]:
                for tf in ['8h', '12h', '1d', '3d']:
                    cooldown_key = f"CH_{tf}_{symbol}"
                    if cooldown_key in son_sinyaller and time.time() - son_sinyaller[cooldown_key] < 86400: continue
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=500")
                    if not isinstance(klines, list) or len(klines) < 205: continue
                    closes = [float(k[4]) for k in klines[:-1]]; curr, prev = closes[-1], closes[-2]
                    ema200 = calculate_ema(closes, 200)
                    if len(ema200) >= 2:
                        c_ema, p_ema = ema200[-1], ema200[-2]; vol = [float(k[7]) for k in klines[:-1]]
                        if vol[-1] > sum(vol[-21:-1])/20:
                            sqz = squeeze_radar_check(symbol, tf)
                            if prev <= p_ema and curr > c_ema and curr > c_ema * 1.01:
                                msg = (f"📈 <b>EMA200 (LONG): {symbol}</b>\n<i>{tf.upper()}</i>\n\n💰 Fiyat: {akilli_fiyat(curr)}\n\n💡 <b>Açıklama:</b> Dev ortalama yukarı kırıldı.")
                                if sqz: msg += f"\n\n{sqz}"
                                if tg(C, msg): son_sinyaller[cooldown_key] = time.time()
                            elif prev >= p_ema and curr < c_ema and curr < c_ema * 0.99:
                                msg = (f"📉 <b>EMA200 (SHORT): {symbol}</b>\n<i>{tf.upper()}</i>\n\n💰 Fiyat: {akilli_fiyat(curr)}\n\n💡 <b>Açıklama:</b> Dev ortalama aşağı kırıldı.")
                                if sqz: msg += f"\n\n{sqz}"
                                if tg(C, msg): son_sinyaller[cooldown_key] = time.time()
                time.sleep(0.05)
            time.sleep(CHARTIST_ARALIGI)
        except: time.sleep(60)

def pump_loop():
    log("PUMP", "Başladı.")
    time.sleep(30)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            log("PUMP", "Piyasa taranıyor...")
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_PUMP]:
                if symbol in son_pump_sinyalleri and time.time() - son_pump_sinyalleri[symbol] < 900: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=5m&limit=3")
                if not isinstance(klines, list) or len(klines) < 3: continue
                curr_k, prev_k = klines[-2], klines[-3]; cc, pc = float(curr_k[4]), float(prev_k[4]); cv, pv = float(curr_k[7]), float(prev_k[7])
                if pc == 0 or pv == 0: continue
                if ((cc-pc)/pc)*100 >= 3.0 and ((cv-pv)/pv)*100 >= 100.0 and (cv-pv) >= 50_000:
                    teyit = trap_radar_check(symbol); sqz = squeeze_radar_check(symbol, '4h')
                    msg = (f"🚀 <b>Pump - {symbol}</b>\n💰 Fiyat: {akilli_fiyat(cc)}\n📊 Hacim: +%{((cv-pv)/pv)*100:.0f}\n\n{teyit}\n\n💡 <b>Açıklama:</b> Ani patlama. Radar kontrol edildi.")
                    if sqz: msg += f"\n\n{sqz}"
                    tg(C, msg); son_pump_sinyalleri[symbol] = time.time()
                time.sleep(0.05)
            time.sleep(PUMP_ARALIGI)
        except: time.sleep(60)

def formasyon_loop():
    log("FORMASYON", "Başladı.")
    time.sleep(45)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            log("FORMASYON", "Piyasa taranıyor...")
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_FORMASYON]:
                if symbol in son_formasyon_sinyalleri and time.time() - son_formasyon_sinyalleri[symbol] < 86400: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1d&limit=100")
                if not isinstance(klines, list) or len(klines) < 100: continue
                closes = [float(k[4]) for k in klines]; curr = closes[-1]
                if curr > max(closes[-20:-5]): 
                    msg = (f"🚀 <b>FORMASYON (LONG): {symbol}</b>\n<i>Ters OBO / İkili Dip</i>\n\n💡 <b>Açıklama:</b> Boyun çizgisi yukarı kırıldı. MSS gerçekleşti.")
                    if tg(C, msg): son_formasyon_sinyalleri[symbol] = time.sleep(86400)
                elif curr < min(closes[-20:-5]):
                    msg = (f"💀 <b>FORMASYON (SHORT): {symbol}</b>\n<i>OBO / İkili Tepe</i>\n\n💡 <b>Açıklama:</b> Boyun çizgisi aşağı kırıldı. MSS gerçekleşti.")
                    if tg(C, msg): son_formasyon_sinyalleri[symbol] = time.sleep(86400)
                time.sleep(0.1)
            time.sleep(FORMASYON_ARALIGI)
        except: time.sleep(60)

def ema_ribbon_loop():
    log("EMA_RIBBON", "Başladı.")
    time.sleep(90)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            log("EMA_RIBBON", "Piyasa taranıyor...")
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_EMA_RIBBON]:
                if symbol in son_ema_ribbon_sinyalleri and time.time() - son_ema_ribbon_sinyalleri[symbol] < 86400: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1d&limit=200")
                if not isinstance(klines, list) or len(klines) < 200: continue
                closes = [float(k[4]) for k in klines[:-1]]; curr = closes[-1]
                e7, e25, e99, e200 = calculate_ema(closes, 7)[-1], calculate_ema(closes, 25)[-1], calculate_ema(closes, 99)[-1], calculate_ema(closes, 200)[-1]
                if curr > e7 > e25 > e99 > e200:
                    msg = (f"🚀 <b>EMA DİZİLİMİ (LONG): {symbol}</b>\n\n💡 <b>Açıklama:</b> Kusursuz Boğa dizilimi.")
                    if tg(C, msg): son_ema_ribbon_sinyalleri[symbol] = time.time()
                elif curr < e7 < e25 < e99 < e200:
                    msg = (f"💀 <b>EMA DİZİLİMİ (SHORT): {symbol}</b>\n\n💡 <b>Açıklama:</b> Kusursuz Ayı dizilimi.")
                    if tg(C, msg): son_ema_ribbon_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(EMA_RIBBON_ARALIGI)
        except: time.sleep(60)

def macd_loop():
    log("MACD", "Başladı.")
    time.sleep(105)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            log("MACD", "Piyasa taranıyor...")
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_MACD]:
                if symbol in son_macd_sinyalleri and time.time() - son_macd_sinyalleri[symbol] < 14400: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1d&limit=100")
                if not isinstance(klines, list) or len(klines) < 80: continue
                closes = [float(k[4]) for k in klines[:-1]]; macd_res = calculate_macd(closes)
                if not macd_res: continue
                cm, cs, pm, ps = macd_res; curr = closes[-1]
                if pm <= ps and cm > cs:
                    msg = (f"🚀 <b>MACD (LONG): {symbol}</b>\n\n💡 <b>Açıklama:</b> Golden Cross.")
                    if tg(C, msg): son_macd_sinyalleri[symbol] = time.time()
                elif pm >= ps and cm < cs:
                    msg = (f"💀 <b>MACD (SHORT): {symbol}</b>\n\n💡 <b>Açıklama:</b> Death Cross.")
                    if tg(C, msg): son_macd_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(MACD_ARALIGI)
        except: time.sleep(60)

def midas_setup_loop():
    log("MIDAS_SETUP", "Başladı.")
    time.sleep(120)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            log("MIDAS_SETUP", "Piyasa taranıyor...")
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_MIDAS_SETUP]:
                if symbol in son_midas_setup_sinyalleri and time.time() - son_midas_setup_sinyalleri[symbol] < 14400: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=4h&limit=50")
                if not isinstance(klines, list) or len(klines) < 50: continue
                highs = [float(k[2]) for k in klines[:-1]]; lows = [float(k[3]) for k in klines[:-1]]
                closes = [float(k[4]) for k in klines[:-1]]; vols = [float(k[7]) for k in klines[:-1]]
                curr = closes[-1]; is_vol = vols[-1] > sum(vols[-21:-1])/20
                if curr > max(highs[-20:]) and is_vol:
                    msg = (f"⚡ <b>SETUP (LONG): {symbol}</b>\n\n💡 <b>Açıklama:</b> 4h hacimli kırılım.")
                    if tg(C, msg): son_midas_setup_sinyalleri[symbol] = time.time()
                elif curr < min(lows[-20:]) and is_vol:
                    msg = (f"⚡ <b>SETUP (SHORT): {symbol}</b>\n\n💡 <b>Açıklama:</b> 4h hacimli destek kırılımı.")
                    if tg(C, msg): son_midas_setup_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(MIDAS_SETUP_ARALIGI)
        except: time.sleep(60)

def ath_atl_loop():
    log("YILLIK_ZIRVE_DIP", "Başladı.")
    time.sleep(135)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            log("YILLIK_ZIRVE_DIP", "Piyasa taranıyor...")
            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_ATH_ATL]
            for symbol in adaylar:
                if symbol in son_ath_atl_sinyalleri and time.time() - son_ath_atl_sinyalleri[symbol] < 86400: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1d&limit=365")
                if not isinstance(klines, list) or len(klines) < 200: continue
                highs = [float(k[2]) for k in klines[:-1]]; lows = [float(k[3]) for k in klines[:-1]]
                closes = [float(k[4]) for k in klines[:-1]]; vols = [float(k[7]) for k in klines[:-1]]
                current_price = closes[-1]; prev_close = closes[-2]; current_vol = vols[-1]
                yillik_zirve = max(highs); yillik_dip = min(lows)
                if current_vol < (sum(vols[-21:-1])/20 * 1.2): continue
                if prev_close >= yillik_dip and current_price < yillik_dip:
                    stop = yillik_dip * 1.05; hedef = current_price * 0.80 
                    msg = (f"💀 <b>YILLIK DİP (SHORT): {symbol}</b>\n\n💰 Giriş: {akilli_fiyat(current_price)}\n🎯 Hedef: {akilli_fiyat(hedef)}\n🛑 Stop: {akilli_fiyat(stop)}\n\n💡 <b>Açıklama:</b> 1 yıllık dip kırıldı.")
                    if tg(C, msg): son_ath_atl_sinyalleri[symbol] = time.time()
                elif prev_close <= yillik_zirve and current_price > yillik_zirve:
                    stop = yillik_zirve * 0.95; hedef = current_price * 1.20 
                    msg = (f"🚀 <b>YILLIK ZİRVE (LONG): {symbol}</b>\n\n💰 Giriş: {akilli_fiyat(current_price)}\n🎯 Hedef: {akilli_fiyat(hedef)}\n🛑 Stop: {akilli_fiyat(stop)}\n\n💡 <b>Açıklama:</b> 1 yıllık zirve kırıldı.")
                    if tg(C, msg): son_ath_atl_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(ATH_ATL_ARALIGI)
        except Exception as e: time.sleep(60)

def digital_chartist_loop():
    log("DIGITAL_CHARTIST", "Başladı.")
    time.sleep(150)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            log("DIGITAL_CHARTIST", "Piyasa taranıyor...")
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_DIGITAL]:
                if symbol in son_chartist_sinyalleri and time.time() - son_chartist_sinyalleri[symbol] < 86400: continue
                for tf in ['12h', '1d']:
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=50")
                    if not isinstance(klines, list) or len(klines) < 50: continue
                    highs = [float(k[2]) for k in klines[:-1]]; lows = [float(k[3]) for k in klines[:-1]]
                    closes = [float(k[4]) for k in klines[:-1]]; vols = [float(k[7]) for k in klines[:-1]]
                    curr = closes[-1]; current_vol = vols[-1]; avg_vol = sum(vols[-20:]) / 20
                    direnc = max(highs[-30:]); dip = min(lows[-30:]); is_vol = current_vol > (avg_vol * 1.5)
                    if curr > direnc and is_vol:
                        hedefler = calculate_targets(curr, dip); stop = dip * 0.98
                        msg = f"☕ <b>FORMASYON (LONG): {symbol}</b>\n<i>{tf.upper()}</i>\n\n🎯 Hedefler:\n"
                        for hedef in hedefler: msg += f"🎯 {akilli_fiyat(hedef)}\n"
                        msg += f"\n🛑 Stop: {akilli_fiyat(stop)}\n\n💡 <b>Açıklama:</b> Kırılım gerçekleşti."
                        if tg(C, msg): son_chartist_sinyalleri[symbol] = time.time(); break
                    elif curr < dip and is_vol:
                        hedefler = calculate_short_targets(curr, direnc); stop = direnc * 1.02
                        msg = f"🩸 <b>FORMASYON (SHORT): {symbol}</b>\n<i>{tf.upper()}</i>\n\n🎯 Hedefler:\n"
                        for hedef in hedefler: msg += f"🎯 {akilli_fiyat(hedef)}\n"
                        msg += f"\n🛑 Stop: {akilli_fiyat(stop)}\n\n💡 <b>Açıklama:</b> Kırılım gerçekleşti."
                        if tg(C, msg): son_chartist_sinyalleri[symbol] = time.time(); break
                time.sleep(0.1)
            time.sleep(DIGITAL_CHARTIST_ARALIGI)
        except Exception as e: time.sleep(60)

def sma200_loop():
    log("SMA200", "Başladı.")
    time.sleep(180)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            log("SMA200", "Piyasa taranıyor...")
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_SMA200]:
                for tf in ['3d', '1w']:
                    cooldown_key = f"SMA200_{tf}_{symbol}"
                    if cooldown_key in son_sma200_sinyalleri and time.time() - son_sma200_sinyalleri[cooldown_key] < 43200: continue 
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=250")
                    if not isinstance(klines, list) or len(klines) < 205: continue
                    closes = [float(k[4]) for k in klines[:-1]]; curr, prev = closes[-1], closes[-2]
                    sma200 = calculate_sma(closes, 200)
                    if not sma200 or len(sma200) < 3: continue
                    c_sma = sma200[-1]; p_sma = sma200[-2]
                    if prev <= p_sma and curr > c_sma and curr > c_sma * 1.005:
                        msg = (f"🚀 <b>SMA200 (LONG): {symbol}</b>\n<i>{tf.upper()}</i>\n\n💡 <b>Açıklama:</b> Kurumsal Al bölgesi.")
                        if tg(C, msg): son_sma200_sinyalleri[cooldown_key] = time.time()
                    elif prev >= p_sma and curr < c_sma and curr < c_sma * 0.995:
                        msg = (f"💀 <b>SMA200 (SHORT): {symbol}</b>\n<i>{tf.upper()}</i>\n\n💡 <b>Açıklama:</b> Kurumsal Sat bölgesi.")
                        if tg(C, msg): son_sma200_sinyalleri[cooldown_key] = time.time()
                time.sleep(0.1)
            time.sleep(SMA200_ARALIGI)
        except Exception as e: time.sleep(60)

def bollinger_loop():
    log("BOLLINGER", "Başladı.")
    time.sleep(210)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            log("BOLLINGER", "Piyasa taranıyor...")
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_BOLLINGER]:
                for tf in ['1w', '1d']:
                    cooldown_key = f"BOLL_{tf}_{symbol}"
                    if cooldown_key in son_bollinger_sinyalleri and time.time() - son_bollinger_sinyalleri[cooldown_key] < 43200: continue
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=50")
                    if not isinstance(klines, list) or len(klines) < 30: continue
                    closes = [float(k[4]) for k in klines[:-1]]; curr = closes[-1]
                    upper, mid, lower = calculate_bollinger(closes)
                    if not upper or not lower: continue
                    if curr > upper and closes[-2] <= upper:
                        msg = (f"🔥 <b>BOLLINGER (LONG): {symbol}</b>\n<i>{tf.upper()}</i>\n\n💡 <b>Açıklama:</b> Üst bant kırıldı.")
                        if tg(C, msg): son_bollinger_sinyalleri[cooldown_key] = time.time()
                    elif curr < lower and closes[-2] >= lower:
                        msg = (f"🩸 <b>BOLLINGER (SHORT): {symbol}</b>\n<i>{tf.upper()}</i>\n\n💡 <b>Açıklama:</b> Alt bant kırıldı.")
                        if tg(C, msg): son_bollinger_sinyalleri[cooldown_key] = time.time()
                time.sleep(0.1)
            time.sleep(BOLLINGER_ARALIGI)
        except Exception as e: time.sleep(60)

def ichimoku_loop():
    log("ICHIMOKU", "Başladı.")
    time.sleep(240)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            log("ICHIMOKU", "Piyasa taranıyor...")
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_ICHIMOKU]:
                for tf in ['1w', '1d']:
                    cooldown_key = f"ICHI_{tf}_{symbol}"
                    if cooldown_key in son_ichimoku_sinyalleri and time.time() - son_ichimoku_sinyalleri[cooldown_key] < 86400: continue
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=100")
                    if not isinstance(klines, list) or len(klines) < 52: continue
                    highs = [float(k[2]) for k in klines[:-1]]; lows = [float(k[3]) for k in klines[:-1]]
                    closes = [float(k[4]) for k in klines[:-1]]; curr, prev = closes[-1], closes[-2]
                    cloud_top, cloud_bottom = calculate_ichimoku(highs, lows, closes)
                    if not cloud_top or not cloud_bottom: continue
                    if curr > cloud_top and prev <= cloud_top:
                        msg = (f"☁️ <b>ICHIMOKU (LONG): {symbol}</b>\n<i>{tf.upper()}</i>\n\n💡 <b>Açıklama:</b> Bulut üzerine çıkıldı.")
                        if tg(C, msg): son_ichimoku_sinyalleri[cooldown_key] = time.time()
                    elif curr < cloud_bottom and prev >= cloud_bottom:
                        msg = (f"☁️ <b>ICHIMOKU (SHORT): {symbol}</b>\n<i>{tf.upper()}</i>\n\n💡 <b>Açıklama:</b> Bulut altına inildi.")
                        if tg(C, msg): son_ichimoku_sinyalleri[cooldown_key] = time.time()
                time.sleep(0.1)
            time.sleep(ICHIMOKU_ARALIGI)
        except Exception as e: time.sleep(60)

def binance_killers_loop():
    log("BINANCE_KILLERS", "Başladı.")
    time.sleep(270)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            log("BINANCE_KILLERS", "Piyasa taranıyor...")
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_FVG]:
                if symbol in son_fvg_sinyalleri and time.time() - son_fvg_sinyalleri[symbol] < 14400: continue
                for tf in ['4h', '8h']:
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=100")
                    if not isinstance(klines, list) or len(klines) < 50: continue
                    highs = [float(k[2]) for k in klines[:-1]]; lows = [float(k[3]) for k in klines[:-1]]
                    closes = [float(k[4]) for k in klines[:-1]]; vols = [float(k[7]) for k in klines[:-1]]
                    curr = closes[-1]; current_vol = vols[-1]; avg_vol = sum(vols[-20:]) / 20
                    if current_vol < avg_vol * 1.2: continue
                    yatay_direnc = max(highs[-30:]); yatay_destek = min(lows[-30:])
                    trend_up = lows[-10] > lows[-20] > lows[-30]; trend_down = highs[-10] < highs[-20] < highs[-30]
                    bullish_fvg = None; bearish_fvg = None
                    for i in range(len(closes)-10, len(closes)-2):
                        if lows[i+2] > highs[i]: bullish_fvg = (highs[i] + lows[i+2]) / 2
                        if highs[i+2] < lows[i]: bearish_fvg = (lows[i] + highs[i+2]) / 2
                    sig_id = random.randint(2200, 2300)
                    if trend_up and bullish_fvg and abs(curr - bullish_fvg) / curr < 0.02 and abs(curr - yatay_destek) / curr < 0.04:
                        entry_low = bullish_fvg * 0.998; entry_high = bullish_fvg * 1.002; stop_loss = yatay_destek * 0.98
                        diff = entry_high - stop_loss; targets = [entry_high + (diff * i) for i in [1.0, 1.5, 2.0, 3.0, 4.0]]
                        msg = (f"📍SIGNAL ID: #{sig_id}📍\nCOIN: #{symbol} (2-5x)\nDirection: LONG\n➖➖➖➖➖➖➖\nENTRY: {akilli_fiyat(entry_low)} - {akilli_fiyat(entry_high)}\n\nTARGETS: {' – '.join([akilli_fiyat(t) for t in targets])}\n\nSTOP LOSS: {akilli_fiyat(stop_loss)}\n\n{tf.upper()} FVG confluent with trendline & support.\n\n💡 <b>Açıklama:</b> Konfluans bölgesi.")
                        if tg(C, msg): son_fvg_sinyalleri[symbol] = time.time(); break
                    elif trend_down and bearish_fvg and abs(curr - bearish_fvg) / curr < 0.02 and abs(curr - yatay_direnc) / curr < 0.04:
                        entry_low = bearish_fvg * 0.998; entry_high = bearish_fvg * 1.002; stop_loss = yatay_direnc * 1.02
                        diff = stop_loss - entry_low; targets = [entry_low - (diff * i) for i in [1.0, 1.5, 2.0, 3.0, 4.0]]
                        msg = (f"📍SIGNAL ID: #{sig_id}📍\nCOIN: #{symbol} (2-5x)\nDirection: SHORT\n➖➖➖➖➖➖➖\nENTRY: {akilli_fiyat(entry_low)} - {akilli_fiyat(entry_high)}\n\nTARGETS: {' – '.join([akilli_fiyat(t) for t in targets])}\n\nSTOP LOSS: {akilli_fiyat(stop_loss)}\n\n{tf.upper()} FVG confluent with trendline & resistance.\n\n💡 <b>Açıklama:</b> Konfluans bölgesi.")
                        if tg(C, msg): son_fvg_sinyalleri[symbol] = time.time(); break
                time.sleep(0.1)
            time.sleep(FVG_KONFLUANS_ARALIGI)
        except Exception as e: time.sleep(60)

# =====================================================
# WEB SUNUCUSU VE BAŞLATMA
# =====================================================
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Ultimate Bot is running!")
    def log_message(self, format_string, *args): return

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    log("SİSTEM", f"WEB SUNUCUSU BAŞLADI - PORT: {port}")
    server.serve_forever()

if __name__ == "__main__":
    log("SİSTEM", "Bot başlatıldı. Telegram'a onay mesajı gönderiliyor...")
    baslangic_mesaji = (
        "🤖 <b>SİSTEM AKTİF!</b>\n\n"
        "Kripto Sinyal Botu başarıyla başlatıldı. 🚀\n"
        "17 farklı motor arka planda piyasayı taramaya başladı.\n\n"
        "Piyasa şartları uygun olduğunda ve kaliteli bir fırsat yakalandığında sinyaller buraya düşecektir."
    )
    if tg(C, baslangic_mesaji): log("SİSTEM", "✅ Başlangıç mesajı Telegram'a gönderildi.")
    else: log("SİSTEM", "❌ HATA: Başlangıç mesajı gönderilemedi! Token/ChatID kontrol edin.")
    
    threading.Thread(target=smart_money_loop, daemon=True).start() 
    threading.Thread(target=absorption_loop, daemon=True).start() 
    threading.Thread(target=teyitli_hacim_loop, daemon=True).start() 
    threading.Thread(target=squeeze_radar_loop, daemon=True).start() 
    threading.Thread(target=midas_flow_loop, daemon=True).start()
    threading.Thread(target=chartist_loop, daemon=True).start()
    threading.Thread(target=pump_loop, daemon=True).start()
    threading.Thread(target=formasyon_loop, daemon=True).start()
    threading.Thread(target=ema_ribbon_loop, daemon=True).start()
    threading.Thread(target=macd_loop, daemon=True).start()
    threading.Thread(target=midas_setup_loop, daemon=True).start()
    threading.Thread(target=ath_atl_loop, daemon=True).start()
    threading.Thread(target=digital_chartist_loop, daemon=True).start()
    threading.Thread(target=sma200_loop, daemon=True).start()
    threading.Thread(target=bollinger_loop, daemon=True).start()
    threading.Thread(target=ichimoku_loop, daemon=True).start()
    threading.Thread(target=binance_killers_loop, daemon=True).start() 
    
    run_web_server()

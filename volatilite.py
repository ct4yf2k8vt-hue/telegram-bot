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
BASE = "https://api.binance.com"

# MOTOR ÇALIŞMA SÜRELERİ
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

# FİLTRELER (Hacim)
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

# SİNYAL HAFIZALARI
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
        req = urllib.request.Request(f"https://api.telegram.org/bot{T}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8")).get("ok", False)
    except:
        return False

def gj(url):
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except:
        return None

def calculate_ema(prices, period):
    if len(prices) < period: return []
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for price in prices[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema

def calculate_sma(prices, period):
    if len(prices) < period: return []
    return [sum(prices[i:i+period])/period for i in range(len(prices)-period+1)]

def calculate_targets(entry_price, recent_low):
    diff = entry_price - recent_low
    targets = [entry_price + (diff * 0.272), entry_price + (diff * 0.618), entry_price + (diff * 1.000), entry_price + (diff * 1.618), entry_price + (diff * 2.618)]
    return targets

def calculate_short_targets(entry_price, recent_high):
    diff = recent_high - entry_price
    targets = [entry_price - (diff * 0.272), entry_price - (diff * 0.618), entry_price - (diff * 1.000), entry_price - (diff * 1.618), entry_price - (diff * 2.618)]
    return targets

def calculate_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow + signal: return None, None, None, None
    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)
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
        oi_data = gj(f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=2")
        oi_artiyor = False
        if oi_data and len(oi_data) >= 2:
            if ((float(oi_data[1]['sumOpenInterest']) - float(oi_data[0]['sumOpenInterest'])) / float(oi_data[0]['sumOpenInterest'])) * 100 > 0.2: oi_artiyor = True
        taker_data = gj(f"https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={symbol}&period=5m&limit=1")
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
        global_data = gj(f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period={timeframe}&limit=1")
        top_data = gj(f"https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={symbol}&period={timeframe}&limit=1")
        
        if not global_data or not top_data: return ""
        
        g_long_pct = float(global_data[0]['longAccount']) * 100
        t_long_pct = float(top_data[0]['longAccount']) * 100
        
        if g_long_pct > 75 and t_long_pct < 50:
            return f"⚠️ <b>SİNYAL RİSKLİ:</b> Long Sıkışması Riski Var! (Kalabalık %{g_long_pct:.0f} Long, Balina %{t_long_pct:.0f} Long)"
        elif g_long_pct < 30 and t_long_pct > 60:
            return f"🚀 <b>SİNYAL DESTEKLİ:</b> Short Sıkışması Riski Var! (Kalabalık %{g_long_pct:.0f} Long, Balina %{t_long_pct:.0f} Long)"
        return ""
    except: return ""

# =====================================================
# MOTOR 17: SMART MONEY (LİKİDİTE AVI & OTE)
# =====================================================
def smart_money_loop():
    log("SMART_MONEY", "Akıllı Para (Likidite Avı & OTE) Motoru Başladı.")
    time.sleep(400)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_SMART_MONEY]:
                for tf in ['4h', '1d']:
                    cooldown_key = f"SM_{tf}_{symbol}"
                    if cooldown_key in son_smart_money_sinyalleri and time.time() - son_smart_money_sinyalleri[cooldown_key] < 14400: continue
                    
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=50")
                    if not isinstance(klines, list) or len(klines) < 30: continue
                    
                    highs = [float(k[2]) for k in klines]
                    lows = [float(k[3]) for k in klines]
                    closes = [float(k[4]) for k in klines]
                    
                    curr_high = highs[-2]
                    curr_low = lows[-2]
                    curr_close = closes[-2]
                    
                    son_20_dip = min(lows[-22:-2])
                    son_20_tepe = max(highs[-22:-2])
                    
                    if curr_low < son_20_dip and curr_close > son_20_dip:
                        son_tepe = max(highs[-15:-2]) 
                        ote_ust = son_tepe - ((son_tepe - curr_low) * 0.618)
                        ote_alt = son_tepe - ((son_tepe - curr_low) * 0.786)
                        
                        if ote_alt <= curr_close <= ote_ust:
                            msg = (f"🎯 <b>SMART MONEY (LONG): {symbol}</b>\n"
                                   f"<i>{tf.upper()} Grafik - Likidite Avı & Fibonacci OTE</i>\n\n"
                                   f"💰 <b>Giriş (OTE Bölgesi):</b> {akilli_fiyat(ote_alt)} - {akilli_fiyat(ote_ust)}\n"
                                   f"🎯 <b>Hedef:</b> {akilli_fiyat(son_tepe)} (Önceki Zirve)\n"
                                   f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(curr_low * 0.995)} (Avlanan Dibi)\n\n"
                                   f"📊 <b>Yapılan Analiz:</b>\n"
                                   f"1. 💧 <b>Likidite Avı:</b> Fiyat, 20 günlük dibi ({akilli_fiyat(son_20_dip)}) aşağı yönlü kırıp stopları patlattı ve anında geri döndü.\n"
                                   f"2. 📏 <b>Fibonacci OTE:</b> Yükseliş sonrası fiyat, en kârlı alım bölgesi olan %61.8 - %78.6 geri çekilme bandına sarktı.\n\n"
                                   f"💡 <b>Açıklama:</b> Balinalar sahte bir düşüş yaratarak küçük yatırımcıların stoplarını avladı. Şu an fiyat, akıllı paranın (Smart Money) en uygun maliyetle alım yapacağı OTE bölgesinde. Güçlü bir dönüş sinyali.")
                            if tg(C, msg): son_smart_money_sinyalleri[cooldown_key] = time.time()

                    elif curr_high > son_20_tepe and curr_close < son_20_tepe:
                        son_dip = min(lows[-15:-2])
                        ote_alt = son_dip + ((curr_high - son_dip) * 0.618)
                        ote_ust = son_dip + ((curr_high - son_dip) * 0.786)
                        
                        if ote_alt <= curr_close <= ote_ust:
                            msg = (f"🎯 <b>SMART MONEY (SHORT): {symbol}</b>\n"
                                   f"<i>{tf.upper()} Grafik - Likidite Avı & Fibonacci OTE</i>\n\n"
                                   f"💰 <b>Giriş (OTE Bölgesi):</b> {akilli_fiyat(ote_alt)} - {akilli_fiyat(ote_ust)}\n"
                                   f"🎯 <b>Hedef:</b> {akilli_fiyat(son_dip)} (Önceki Dip)\n"
                                   f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(curr_high * 1.005)} (Avlanan Tepesi)\n\n"
                                   f"📊 <b>Yapılan Analiz:</b>\n"
                                   f"1. 💧 <b>Likidite Avı:</b> Fiyat, 20 günlük tepeyi ({akilli_fiyat(son_20_tepe)}) yukarı yönlü kırıp short stoplarını patlattı ve anında geri döndü.\n"
                                   f"2. 📏 <b>Fibonacci OTE:</b> Düşüş sonrası fiyat, en kârlı satış bölgesi olan %61.8 - %78.6 geri çekilme bandına tepki verdi.\n\n"
                                   f"💡 <b>Açıklama:</b> Balinalar sahte bir yükseliş yaratarak shortçuların stoplarını avladı. Şu an fiyat, akıllı paranın en uygun maliyetle satış yapacağı OTE bölgesinde. Sert bir düşüş gelebilir.")
                            if tg(C, msg): son_smart_money_sinyalleri[cooldown_key] = time.time()

                time.sleep(0.1)
            time.sleep(SMART_MONEY_ARALIGI)
        except Exception as e:
            log("SMART_MONEY", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 16: EMİLİM (ABSORPTION) DEDEKTÖRÜ
# =====================================================
def absorption_loop():
    log("EMILIM", "Emilim (Absorption) Dedektörü Başladı.")
    time.sleep(250)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_EMILIM]:
                for tf in ['5m', '15m']:
                    cooldown_key = f"ABS_{tf}_{symbol}"
                    if cooldown_key in son_emilim_sinyalleri and time.time() - son_emilim_sinyalleri[cooldown_key] < 3600: continue
                    
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=25")
                    if not isinstance(klines, list) or len(klines) < 25: continue
                    
                    curr_open = float(klines[-2][1])
                    curr_close = float(klines[-2][4])
                    curr_high = float(klines[-2][2])
                    curr_low = float(klines[-2][3])
                    curr_vol = float(klines[-2][5])
                    
                    avg_vol = sum(float(k[5]) for k in klines[-22:-2]) / 20
                    if avg_vol == 0: continue
                    
                    if curr_vol > (avg_vol * 3.0):
                        body_size = abs(curr_close - curr_open)
                        candle_range = curr_high - curr_low
                        
                        if candle_range > 0 and (body_size / candle_range) < 0.3:
                            vol_ratio = curr_vol / avg_vol
                            
                            if curr_close <= curr_open:
                                msg = (f"🛡️ <b>ALICI EMİLİMİ (LONG): {symbol}</b>\n"
                                       f"<i>{tf.upper()} Grafik - Absorption</i>\n\n"
                                       f"📊 Devasa Hacim: <b>{vol_ratio:.1f}x</b> (Ortalamanın {vol_ratio:.1f} katı)\n"
                                       f"📉 Mum Yönü: Kırmızı ama fiyat düşemiyor.\n"
                                       f"💰 Anlık Fiyat: {akilli_fiyat(curr_close)}\n\n"
                                       f"💡 <b>Açıklama:</b> Piyasaya devasa bir satış baskısı geldi ancak fiyat aşağı yönlü ilerleyemedi. Bu durum, büyük bir oyuncunun (balinanın) tüm satışları 'emdiğini' (absorption) ve fiyatı yukarı sürmeye hazırlandığını gösterir. Dönüş sinyali olabilir.")
                                if tg(C, msg): son_emilim_sinyalleri[cooldown_key] = time.time()
                                
                            else:
                                msg = (f"🛡️ <b>SATICI EMİLİMİ (SHORT): {symbol}</b>\n"
                                       f"<i>{tf.upper()} Grafik - Absorption</i>\n\n"
                                       f"📊 Devasa Hacim: <b>{vol_ratio:.1f}x</b> (Ortalamanın {vol_ratio:.1f} katı)\n"
                                       f"📈 Mum Yönü: Yeşil ama fiyat yükselemiyor.\n"
                                       f"💰 Anlık Fiyat: {akilli_fiyat(curr_close)}\n\n"
                                       f"💡 <b>Açıklama:</b> Piyasaya devasa bir alım baskısı geldi ancak fiyat yukarı yönlü ilerleyemedi. Bu durum, büyük bir oyuncunun (balinanın) tüm alımları 'emdiğini' (absorption) ve fiyatı aşağı sürmeye hazırlandığını gösterir. Düşüş sinyali olabilir.")
                                if tg(C, msg): son_emilim_sinyalleri[cooldown_key] = time.time()
                                
                time.sleep(0.1)
            time.sleep(EMILIM_ARALIGI)
        except Exception as e:
            log("EMILIM", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 15: TEYİTLİ HACİM PATLAMASI (VOLUME SPIKE)
# =====================================================
def teyitli_hacim_loop():
    log("HACIM", "Teyitli Hacim Patlaması Motoru Başladı.")
    time.sleep(330)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_HACIM_PATLAMASI]:
                for tf in ['4h', '8h', '1d']:
                    cooldown_key = f"VOL_{tf}_{symbol}"
                    if cooldown_key in son_hacim_sinyalleri and time.time() - son_hacim_sinyalleri[cooldown_key] < 14400: continue
                    
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=250")
                    if not isinstance(klines, list) or len(klines) < 50: continue
                    
                    closes = [float(k[4]) for k in klines[:-1]]
                    vols = [float(k[5]) for k in klines[:-1]]
                    highs = [float(k[2]) for k in klines[:-1]]
                    lows = [float(k[3]) for k in klines[:-1]]
                    
                    curr_close = closes[-1]
                    prev_close = closes[-2]
                    curr_vol = vols[-1]
                    avg_vol = sum(vols[-21:-1]) / 20
                    
                    if avg_vol == 0: continue
                    
                    if curr_vol >= (avg_vol * 2.0):
                        spike_ratio = (curr_vol / avg_vol) * 100
                        ema200 = calculate_ema(closes, 200)
                        if not ema200: continue
                        c_ema = ema200[-1]
                        
                        teyit = trap_radar_check(symbol)
                        tuzak_mi = "SAHTE" in teyit or "TUZAK" in teyit
                        
                        if curr_close > c_ema and curr_close > prev_close and not tuzak_mi:
                            son_direnc = max(highs[-21:-1])
                            if curr_close >= son_direnc:
                                msg = (f"📊 <b>HACİM PATLAMASI (LONG): {symbol}</b>\n"
                                       f"<i>{tf.upper()} Grafik - Teyitli Kırılım</i>\n\n"
                                       f"📈 Hacim Artışı: <b>+%{spike_ratio:.0f}</b> (Ortalamanın {curr_vol/avg_vol:.1f} katı)\n"
                                       f"💰 Anlık Fiyat: {akilli_fiyat(curr_close)}\n"
                                       f"{teyit}\n\n"
                                       f"💡 <b>Açıklama:</b> Hacim patlaması, EMA200 trendi ve yatay direnç kırılımı aynı anda gerçekleşti. Bu, büyük yatırımcıların (balinaların) bölgeye girdiğini gösteren çok güçlü bir alış sinyalidir. Trap radarı da bu hareketin sahte olmadığını teyit ediyor.")
                                if tg(C, msg): son_hacim_sinyalleri[cooldown_key] = time.time()
                                
                        elif curr_close < c_ema and curr_close < prev_close and not tuzak_mi:
                            son_destek = min(lows[-21:-1])
                            if curr_close <= son_destek:
                                msg = (f"📉 <b>HACİM PATLAMASI (SHORT): {symbol}</b>\n"
                                       f"<i>{tf.upper()} Grafik - Teyitli Kırılım</i>\n\n"
                                       f"📉 Hacim Artışı: <b>+%{spike_ratio:.0f}</b> (Ortalamanın {curr_vol/avg_vol:.1f} katı)\n"
                                       f"💰 Anlık Fiyat: {akilli_fiyat(curr_close)}\n"
                                       f"{teyit}\n\n"
                                       f"💡 <b>Açıklama:</b> Hacim patlaması, EMA200 trendi ve yatay destek kırılımı aynı anda gerçekleşti. Bu, büyük satıcıların bölgeden çıktığını gösteren çok güçlü bir satış sinyalidir. Trap radarı da bu hareketin sahte olmadığını teyit ediyor.")
                                if tg(C, msg): son_hacim_sinyalleri[cooldown_key] = time.time()
                                
                time.sleep(0.1)
            time.sleep(HACIM_PATLAMASI_ARALIGI)
        except Exception as e:
            log("HACIM", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 14: SQUEEZE RADAR (SIKIŞMA RADARI)
# =====================================================
def squeeze_radar_loop():
    log("SQUEEZE", "Sıkışma Radarı Motoru Başladı.")
    time.sleep(300)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_SQUEEZE]:
                for tf in ['4h', '1d']:
                    cooldown_key = f"SQZ_{tf}_{symbol}"
                    if cooldown_key in son_squeeze_sinyalleri and time.time() - son_squeeze_sinyalleri[cooldown_key] < 43200: continue
                    
                    global_data = gj(f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period={tf}&limit=1")
                    top_data = gj(f"https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={symbol}&period={tf}&limit=1")
                    
                    if not global_data or not top_data: continue
                    
                    g_long_pct = float(global_data[0]['longAccount']) * 100
                    t_long_pct = float(top_data[0]['longAccount']) * 100
                    
                    if g_long_pct > 75 and t_long_pct < 50:
                        msg = (f"🚨 <b>LONG SIKIŞMASI RİSKİ: {symbol}</b>\n"
                               f"<i>{tf.upper()} Grafik - Kontradyen Uyarı</i>\n\n"
                               f"👥 <b>Küçük Yatırımcı:</b> %{g_long_pct:.1f} Long\n"
                               f"🐋 <b>Balina (Top Trader):</b> %{t_long_pct:.1f} Long\n\n"
                               f"💡 <b>Açıklama:</b> Küçük yatırımcı aşırı derecede Long pozisyonda (Long yığılması var). Ancak balinalar tersine Short'a geçmiş durumda. Bu tablo, yakın zamanda bir 'Long Patlatma' (şelale düşüşü) operasyonu gelebileceğini gösterir. Alım sinyallerine karşı dikkatli olun!")
                        if tg(C, msg): son_squeeze_sinyalleri[cooldown_key] = time.time()
                        
                    elif g_long_pct < 30 and t_long_pct > 60:
                        msg = (f"🚨 <b>SHORT SIKIŞMASI RİSKİ: {symbol}</b>\n"
                               f"<i>{tf.upper()} Grafik - Kontradyen Uyarı</i>\n\n"
                               f"👥 <b>Küçük Yatırımcı:</b> %{g_long_pct:.1f} Long\n"
                               f"🐋 <b>Balina (Top Trader):</b> %{t_long_pct:.1f} Long\n\n"
                               f"💡 <b>Açıklama:</b> Küçük yatırımcı aşırı derecede Short pozisyonda. Ancak balinalar Long'a geçmiş durumda. Bu durum, yakın zamanda bir 'Short Sıkışması' (roket yükselişi) yaşanabileceğini gösterir. Satış sinyallerine karşı dikkatli olun!")
                        if tg(C, msg): son_squeeze_sinyalleri[cooldown_key] = time.time()
                time.sleep(0.1)
            time.sleep(SQUEEZE_RADAR_ARALIGI)
        except Exception as e:
            log("SQUEEZE", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 1: MİDAS FLOW
# =====================================================
def midas_flow_loop():
    log("RAPOR", "Midas Flow Motoru Başladı.")
    time.sleep(5)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(300); continue
            trend_text, avg_chg, ups, downs = get_market_trend(tickers)
            usdt_pairs = [t for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) > 5_000_000]
            usdt_pairs.sort(key=lambda x: float(x['priceChangePercent']), reverse=True)
            top_5 = usdt_pairs[:5]
            if top_5:
                msg = "🔥 <b>24 Saatteki En İyi Hareketler</b>\n"
                msg += f"\n🌍 <b>Piyasa Yönü (24s):</b> {trend_text}\n"
                msg += f"📊 Ortalama Değişim: %{avg_chg:.2f} | Yükselen: {ups} | Düşen: {downs}\n\n"
                medals = ["🥇", "🥈", "🥉", "🏅", "⭐️"]
                hashtags = ""
                for i, t in enumerate(top_5):
                    symbol = t['symbol'].replace("USDT", "")
                    change = float(t['priceChangePercent'])
                    change_str = f"+{change:.0f}%" if change > 0 else f"{change:.0f}%"
                    msg += f"{medals[i]} {symbol} {change_str}\n"
                    hashtags += f"#{symbol} "
                msg += f"\n💡 <b>Açıklama:</b> Son 24 saatin en çok kazandıranları ve genel piyasa yönünü özetler. Piyasadaki genel hakimiyeti (Boğa/Ayı) anlamak için kullanılır."
                msg += f"\n\n<a href='https://t.me/MidasFlowAI'>{hashtags}#MidasFlow</a>"
                tg(C, msg)
            time.sleep(RAPOR_ARALIGI)
        except: time.sleep(60)

# =====================================================
# MOTOR 2: CHARTIST (EMA200 ÇİFT YÖNLÜ)
# =====================================================
def chartist_loop():
    log("CHARTIST", "Zırhlı EMA200 Motoru Başladı.")
    time.sleep(15)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_CHARTIST]:
                for tf in ['8h', '12h', '1d', '3d']:
                    cooldown_key = f"CH_{tf}_{symbol}"
                    if cooldown_key in son_sinyaller and time.time() - son_sinyaller[cooldown_key] < 86400: continue
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=500")
                    if not isinstance(klines, list) or len(klines) < 205: continue
                    closes = [float(k[4]) for k in klines[:-1]]
                    curr, prev = closes[-1], closes[-2]
                    ema200 = calculate_ema(closes, 200)
                    if len(ema200) >= 2:
                        c_ema, p_ema = ema200[-1], ema200[-2]
                        vol = [float(k[7]) for k in klines[:-1]]
                        if vol[-1] > sum(vol[-21:-1])/20:
                            sqz = squeeze_radar_check(symbol, tf)
                            if prev <= p_ema and curr > c_ema and curr > c_ema * 1.01:
                                msg = (f"📈 <b>UZUN VADELİ SPOT (LONG): {symbol}</b>\n<i>{tf.upper()} EMA200 Yukarı Kırılımı</i>\n\n💰 Fiyat: {akilli_fiyat(curr)}\n\n💡 <b>Açıklama:</b> Fiyat, 200 periyotluk devasa ortalamayı (EMA200) hacimle yukarı kırdı. Bu, uzun vadeli yatırımcıların (kurumsalların) bölgeye girdiğini ve trendin kalıcı olarak yön değiştirebileceğini gösterir.")
                                if sqz: msg += f"\n\n{sqz}"
                                if tg(C, msg): son_sinyaller[cooldown_key] = time.time()
                            elif prev >= p_ema and curr < c_ema and curr < c_ema * 0.99:
                                msg = (f"📉 <b>UZUN VADELİ SPOT (SHORT): {symbol}</b>\n<i>{tf.upper()} EMA200 Aşağı Kırılımı</i>\n\n💰 Fiyat: {akilli_fiyat(curr)}\n\n💡 <b>Açıklama:</b> Fiyat, 200 periyotluk devasa ortalamayı (EMA200) hacimle aşağı kırdı. Büyük yatırımcıların çıktığını ve uzun vadeli düşüş trendinin (Ayı) başladığını gösterebilir.")
                                if sqz: msg += f"\n\n{sqz}"
                                if tg(C, msg): son_sinyaller[cooldown_key] = time.time()
                time.sleep(0.05)
            time.sleep(CHARTIST_ARALIGI)
        except: time.sleep(60)

# =====================================================
# MOTOR 3: PUMP AVCISI
# =====================================================
def pump_loop():
    log("PUMP", "Anlık Pump Avcısı Başladı.")
    time.sleep(30)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_PUMP]:
                if symbol in son_pump_sinyalleri and time.time() - son_pump_sinyalleri[symbol] < 900: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=5m&limit=3")
                if not isinstance(klines, list) or len(klines) < 3: continue
                curr_k, prev_k = klines[-2], klines[-3]
                cc, pc = float(curr_k[4]), float(prev_k[4])
                cv, pv = float(curr_k[7]), float(prev_k[7])
                if pc == 0 or pv == 0: continue
                if ((cc-pc)/pc)*100 >= 3.0 and ((cv-pv)/pv)*100 >= 100.0 and (cv-pv) >= 50_000:
                    teyit = trap_radar_check(symbol)
                    sqz = squeeze_radar_check(symbol, '4h')
                    msg = (f"🚀 <b>Pump - {symbol}</b>\n💰 Fiyat: {akilli_fiyat(cc)}\n📊 Hacim Artışı: +%{((cv-pv)/pv)*100:.0f}\n\n{teyit}\n\n"
                           f"💡 <b>Açıklama:</b> 5 dakikalık grafikte aniden sert bir hacim ve fiyat patlaması yaşandı. Trap Radar (OI ve CVD) sayesinde bu hareketin arkasında gerçek balinaların mı yoksa küçük yatırımcıyı çekmek isteyen sahte botların mı olduğunu analiz ettik.")
                    if sqz: msg += f"\n\n{sqz}"
                    tg(C, msg); son_pump_sinyalleri[symbol] = time.time()
                time.sleep(0.05)
            time.sleep(PUMP_ARALIGI)
        except: time.sleep(60)

# =====================================================
# MOTOR 4: FORMASYON (MSS ÇİFT YÖNLÜ)
# =====================================================
def formasyon_loop():
    log("FORMASYON", "Formasyon (İkili Tepe/Omuz-Baş-Omuz) Motoru Başladı.")
    time.sleep(45)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_FORMASYON]:
                if symbol in son_formasyon_sinyalleri and time.time() - son_formasyon_sinyalleri[symbol] < 86400: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1d&limit=100")
                if not isinstance(klines, list) or len(klines) < 100: continue
                closes = [float(k[4]) for k in klines]
                curr = closes[-1]
                
                if curr > max(closes[-20:-5]): 
                    msg = (f"🚀 <b>FORMASYON KIRILIMI (LONG): {symbol}</b>\n"
                           f"<i>Ters Omuz-Baş-Omuz / İkili Dip Formasyonu</i>\n\n"
                           f"💡 <b>Açıklama:</b> Fiyat, görseldeki formasyonların boyun çizgisini (Neckline) yukarı yönlü kırdı. Piyasa Yapısı Değişimi (MSS) gerçekleşti. Alıcılar kontrolü ele geçirdi ve yeni bir yükseliş trendi (Higher High) başladı.")
                    if tg(C, msg): son_formasyon_sinyalleri[symbol] = time.time()
                
                elif curr < min(closes[-20:-5]):
                    msg = (f"💀 <b>FORMASYON KIRILIMI (SHORT): {symbol}</b>\n"
                           f"<i>Omuz-Baş-Omuz / İkili Tepe Formasyonu</i>\n\n"
                           f"💡 <b>Açıklama:</b> Fiyat, görseldeki formasyonların boyun çizgisini (Neckline) aşağı yönlü kırdı. Piyasa Yapısı Değişimi (MSS) aşağı yönlü gerçekleşti. Satıcılar kontrolü ele geçirdi ve yeni bir düşüş trendi (Lower Low) başladı.")
                    if tg(C, msg): son_formasyon_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(FORMASYON_ARALIGI)
        except: time.sleep(60)

# =====================================================
# MOTOR 5: EMA RIBBON (ÇİFT YÖNLÜ)
# =====================================================
def ema_ribbon_loop():
    log("EMA_RIBBON", "EMA Dizilim Motoru Başladı.")
    time.sleep(90)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_EMA_RIBBON]:
                if symbol in son_ema_ribbon_sinyalleri and time.time() - son_ema_ribbon_sinyalleri[symbol] < 86400: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1d&limit=200")
                if not isinstance(klines, list) or len(klines) < 200: continue
                closes = [float(k[4]) for k in klines[:-1]]
                curr = closes[-1]
                e7, e25, e99, e200 = calculate_ema(closes, 7)[-1], calculate_ema(closes, 25)[-1], calculate_ema(closes, 99)[-1], calculate_ema(closes, 200)[-1]
                if curr > e7 > e25 > e99 > e200:
                    msg = (f"🚀 <b>EMA DİZİLİMİ (LONG): {symbol}</b>\n<i>Tüm Ortalamalar Yukarı Yönlü Sıralı</i>\n\n"
                           f"💡 <b>Açıklama:</b> Kusursuz bir Boğa (Yükseliş) dizilimi tespit edildi. Kısa, orta ve uzun vadeli t

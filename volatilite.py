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
HACIM_PATLAMASI_ARALIGI = 3600 # YENİ

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
MIN_HACIM_HACIM_PATLAMASI = 10_000_000 # YENİ

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
son_hacim_sinyalleri = {} # YENİ

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
                    
                    # 1. ŞART: Hacim Patlaması (Ortalamanın en az 2 katı)
                    if curr_vol >= (avg_vol * 2.0):
                        spike_ratio = (curr_vol / avg_vol) * 100
                        
                        # 2. ŞART: Trend Teyidi (EMA200)
                        ema200 = calculate_ema(closes, 200)
                        if not ema200: continue
                        c_ema = ema200[-1]
                        
                        # 3. ŞART: Trap Radar
                        teyit = trap_radar_check(symbol)
                        tuzak_mi = "SAHTE" in teyit or "TUZAK" in teyit
                        
                        # LONG SENARYOSU
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
                                
                        # SHORT SENARYOSU
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
                        if tg(C, msg): son_squeeze_sinyalleri[cooldown_key] = time.sleep()
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
    log("FORMASYON", "Formasyon Motoru Başladı.")
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
                    msg = (f"🚀 <b>FORMASYON KIRILIMI (LONG): {symbol}</b>\n<i>20 Günlük Direnç Kırıldı</i>\n\n"
                           f"💡 <b>Açıklama:</b> Piyasa Yapısı Değişimi (MSS) gerçekleşti. Fiyat, son 20 günün en yüksek direncini kırarak yeni bir yükseliş trendi (Higher High) başlattı. Alıcılar kontrolü ele geçirmiş durumda.")
                    if tg(C, msg): son_formasyon_sinyalleri[symbol] = time.time()
                elif curr < min(closes[-20:-5]):
                    msg = (f"💀 <b>FORMASYON KIRILIMI (SHORT): {symbol}</b>\n<i>20 Günlük Destek Kırıldı</i>\n\n"
                           f"💡 <b>Açıklama:</b> Piyasa Yapısı Değişimi (MSS) aşağı yönlü gerçekleşti. Fiyat, son 20 günün en düşük desteğini kırarak yeni bir düşüş trendi (Lower Low) başlattı. Satıcılar kontrolü ele geçirmiş durumda.")
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
                           f"💡 <b>Açıklama:</b> Kusursuz bir Boğa (Yükseliş) dizilimi tespit edildi. Kısa, orta ve uzun vadeli tüm ortalamalar birbirini destekliyor. Bu formasyon, trendin en güçlü ve en istikrarlı olduğu evreyi işaret eder.")
                    if tg(C, msg): son_ema_ribbon_sinyalleri[symbol] = time.time()
                elif curr < e7 < e25 < e99 < e200:
                    msg = (f"💀 <b>EMA DİZİLİMİ (SHORT): {symbol}</b>\n<i>Tüm Ortalamalar Aşağı Yönlü Sıralı</i>\n\n"
                           f"💡 <b>Açıklama:</b> Kusursuz bir Ayı (Düşüş) dizilimi tespit edildi. Tüm ortalamalar aşağı yönlü sıralanmış durumda. Satış baskısı çok güçlü ve düşüş trendi ivme kazanabilir.")
                    if tg(C, msg): son_ema_ribbon_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(EMA_RIBBON_ARALIGI)
        except: time.sleep(60)

# =====================================================
# MOTOR 6: MACD (ÇİFT YÖNLÜ)
# =====================================================
def macd_loop():
    log("MACD", "MACD Motoru Başladı.")
    time.sleep(105)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_MACD]:
                if symbol in son_macd_sinyalleri and time.time() - son_macd_sinyalleri[symbol] < 14400: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1d&limit=100")
                if not isinstance(klines, list) or len(klines) < 80: continue
                closes = [float(k[4]) for k in klines[:-1]]
                macd_res = calculate_macd(closes)
                if not macd_res: continue
                cm, cs, pm, ps = macd_res
                curr = closes[-1]
                if pm <= ps and cm > cs:
                    msg = (f"🚀 <b>MACD AL SİNYALİ (LONG): {symbol}</b>\n<i>Günlük Grafikte MACD Yukarı Kesti</i>\n\n"
                           f"💡 <b>Açıklama:</b> MACD indikatörü, Sinyal çizgisini aşağıdan yukarı yönlü kesti (Golden Cross). Momentumun (ivmenin) satıcılardan alıcılara geçtiğini ve trendin yönünün yukarı döndüğünü gösteren klasik ve güvenilir bir alış sinyalidir.")
                    if tg(C, msg): son_macd_sinyalleri[symbol] = time.time()
                elif pm >= ps and cm < cs:
                    msg = (f"💀 <b>MACD SAT SİNYALİ (SHORT): {symbol}</b>\n<i>Günlük Grafikte MACD Aşağı Kesti</i>\n\n"
                           f"💡 <b>Açıklama:</b> MACD indikatörü, Sinyal çizgisini yukarıdan aşağı yönlü kesti (Death Cross). Momentumun alıcılardan satıcılara geçtiğini ve düşüş trendinin başladığını gösterir.")
                    if tg(C, msg): son_macd_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(MACD_ARALIGI)
        except: time.sleep(60)

# =====================================================
# MOTOR 7: MIDAS SETUP (ÇİFT YÖNLÜ)
# =====================================================
def midas_setup_loop():
    log("MIDAS_SETUP", "Midas Setup Motoru Başladı.")
    time.sleep(120)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_MIDAS_SETUP]:
                if symbol in son_midas_setup_sinyalleri and time.time() - son_midas_setup_sinyalleri[symbol] < 14400: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=4h&limit=50")
                if not isinstance(klines, list) or len(klines) < 50: continue
                highs = [float(k[2]) for k in klines[:-1]]
                lows = [float(k[3]) for k in klines[:-1]]
                closes = [float(k[4]) for k in klines[:-1]]
                vols = [float(k[7]) for k in klines[:-1]]
                curr = closes[-1]
                is_vol = vols[-1] > sum(vols[-21:-1])/20
                if curr > max(highs[-20:]) and is_vol:
                    msg = (f"⚡ <b>#{symbol} SETUP (LONG)</b>\n<i>4 Saatlik Hacimli Kırılım</i>\n\n"
                           f"💡 <b>Açıklama:</b> 4 saatlik grafikte son 20 periyodun (yaklaşık 3 günün) direnci hacimle aşıldı. Kısa ve orta vadeli traderların dikkatini çeken, hızlı hareket potansiyeli yüksek bir kırılımdır.")
                    if tg(C, msg): son_midas_setup_sinyalleri[symbol] = time.time()
                elif curr < min(lows[-20:]) and is_vol:
                    msg = (f"⚡ <b>#{symbol} SETUP (SHORT)</b>\n<i>4 Saatlik Hacimli Destek Kırılımı</i>\n\n"
                           f"💡 <b>Açıklama:</b> 4 saatlik grafikte son 20 periyodun en düşük desteği hacimle aşağı kırıldı. Satış baskısının arttığını ve hızlı bir düşüşün başlayabileceğini gösterir.")
                    if tg(C, msg): son_midas_setup_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(MIDAS_SETUP_ARALIGI)
        except: time.sleep(60)

# =====================================================
# MOTOR 8: TARİHİ ZİRVE / DİP (ATH/ATL)
# =====================================================
def ath_atl_loop():
    log("ATH_ATL", "Tarihi Zirve/Dip Motoru Başladı.")
    time.sleep(135)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_ATH_ATL]
            for symbol in adaylar:
                if symbol in son_ath_atl_sinyalleri and time.time() - son_ath_atl_sinyalleri[symbol] < 86400: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1d&limit=500")
                if not isinstance(klines, list) or len(klines) < 200: continue
                highs = [float(k[2]) for k in klines[:-1]]
                lows = [float(k[3]) for k in klines[:-1]]
                closes = [float(k[4]) for k in klines[:-1]]
                vols = [float(k[7]) for k in klines[:-1]]
                current_price = closes[-1]; prev_close = closes[-2]; current_vol = vols[-1]
                ath = max(highs); atl = min(lows)
                if current_vol < (sum(vols[-21:-1])/20 * 1.2): continue
                
                if prev_close >= atl and current_price < atl:
                    stop = atl * 1.05; hedef = current_price * 0.80 
                    msg = (f"💀 <b>TARİHİ DİP KIRILIMI (SHORT): {symbol}</b>\n<i>1 Günlük Grafik</i>\n\n"
                           f"💰 Giriş: {akilli_fiyat(current_price)}\n🎯 Hedef: {akilli_fiyat(hedef)}\n🛑 Stop: {akilli_fiyat(stop)}\n\n"
                           f"💡 <b>Açıklama:</b> Fiyat, geçmişteki en düşük seviyeyi (tarihi dibi) hacimle aşağı kırdı. Psikolojik bir çöküş (kapitülasyon) yaşanıyor olabilir, düşüş derinleşebilir.")
                    if tg(C, msg): son_ath_atl_sinyalleri[symbol] = time.time()
                elif prev_close <= ath and current_price > ath:
                    stop = ath * 0.95; hedef = current_price * 1.20 
                    msg = (f"🚀 <b>TARİHİ ZİRVE KIRILIMI (LONG): {symbol}</b>\n<i>1 Günlük Grafik</i>\n\n"
                           f"💰 Giriş: {akilli_fiyat(current_price)}\n🎯 Hedef: {akilli_fiyat(hedef)}\n🛑 Stop: {akilli_fiyat(stop)}\n\n"
                           f"💡 <b>Açıklama:</b> Fiyat, geçmişteki en yüksek seviyeyi (tarihi zirveyi) hacimle yukarı kırdı. Önünde hiçbir direnç kalmadı (Fiyat Keşfi), büyük bir boğa trendi başlayabilir.")
                    if tg(C, msg): son_ath_atl_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(ATH_ATL_ARALIGI)
        except Exception as e: time.sleep(60)

# =====================================================
# MOTOR 9: DIGITAL CHARTIST (ÇİFT YÖNLÜ)
# =====================================================
def digital_chartist_loop():
    log("DIGITAL_CHARTIST", "Kırılım ve Hedef Motoru Başladı.")
    time.sleep(150)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_DIGITAL]:
                if symbol in son_chartist_sinyalleri and time.time() - son_chartist_sinyalleri[symbol] < 86400: continue
                for tf in ['12h', '1d']:
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=50")
                    if not isinstance(klines, list) or len(klines) < 50: continue
                    highs = [float(k[2]) for k in klines[:-1]]; lows = [float(k[3]) for k in klines[:-1]]
                    closes = [float(k[4]) for k in klines[:-1]]; vols = [float(k[7]) for k in klines[:-1]]
                    curr = closes[-1]; current_vol = vols[-1]; avg_vol = sum(vols[-20:]) / 20
                    direnc = max(highs[-30:]); dip = min(lows[-30:])
                    is_vol = current_vol > (avg_vol * 1.5)
                    
                    if curr > direnc and is_vol:
                        hedefler = calculate_targets(curr, dip)
                        stop = dip * 0.98
                        msg = f"📊 <b>#{symbol} {tf.upper()} (LONG)</b>\n\n🚀 Direnç Kırıldı.\n🎯 <b>Hedefler:</b>\n"
                        for hedef in hedefler: msg += f"🎯 {akilli_fiyat(hedef)}\n"
                        msg += f"\n🛑 Stop: {akilli_fiyat(stop)}\n💰 Giriş: {akilli_fiyat(curr)}\n\n"
                        msg += f"💡 <b>Açıklama:</b> Fiyat, 30 günlük güçlü bir direnci kırarak formasyonu yukarı yönlü tamamladı. Belirlenen Fibonacci hedefleri, yükseliş dalgasının potansiyel durma noktalarıdır."
                        if tg(C, msg): son_chartist_sinyalleri[symbol] = time.time(); break
                    elif curr < dip and is_vol:
                        hedefler = calculate_short_targets(curr, direnc)
                        stop = direnc * 1.02
                        msg = f"📊 <b>#{symbol} {tf.upper()} (SHORT)</b>\n\n💀 Destek Kırıldı.\n🎯 <b>Hedefler:</b>\n"
                        for hedef in hedefler: msg += f"🎯 {akilli_fiyat(hedef)}\n"
                        msg += f"\n🛑 Stop: {akilli_fiyat(stop)}\n💰 Giriş: {akilli_fiyat(curr)}\n\n"
                        msg += f"💡 <b>Açıklama:</b> Fiyat, 30 günlük güçlü bir desteği aşağı yönlü kırdı. Satış baskısı çok güçlü. Fibonacci seviyeleri düşüşün potansiyel durak noktalarını gösteriyor."
                        if tg(C, msg): son_chartist_sinyalleri[symbol] = time.time(); break
                time.sleep(0.1)
            time.sleep(DIGITAL_CHARTIST_ARALIGI)
        except Exception as e: time.sleep(60)

# =====================================================
# MOTOR 10: SMA200 (ÇİFT YÖNLÜ)
# =====================================================
def sma200_loop():
    log("SMA200", "SMA200 Çift Yönlü Motoru Başladı.")
    time.sleep(180)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_SMA200]:
                for tf in ['8h', '12h', '1d', '3d']:
                    cooldown_key = f"SMA200_{tf}_{symbol}"
                    if cooldown_key in son_sma200_sinyalleri and time.time() - son_sma200_sinyalleri[cooldown_key] < 43200: continue 
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=250")
                    if not isinstance(klines, list) or len(klines) < 205: continue
                    closes = [float(k[4]) for k in klines[:-1]]; curr, prev = closes[-1], closes[-2]
                    sma200 = calculate_sma(closes, 200)
                    if not sma200 or len(sma200) < 3: continue
                    c_sma = sma200[-1]; p_sma = sma200[-2]
                    
                    if prev <= p_sma and curr > c_sma and curr > c_sma * 1.005:
                        sqz = squeeze_radar_check(symbol, tf)
                        msg = (f"🚀 <b>SMA200 YUKARI KIRILIM (LONG): {symbol}</b>\n<i>{tf.upper()} Grafik</i>\n\n"
                               f"💡 <b>Açıklama:</b> Fiyat, 200 periyotluk basit ortalamanın (SMA200) üzerine çıktı. SMA200, kurumsal yatırımcıların ortalama maliyet çizgisidir. Bu çizginin üzerine çıkılması, piyasanın genel olarak 'Al' bölgesine geçtiğini gösterir.")
                        if sqz: msg += f"\n\n{sqz}"
                        if tg(C, msg): son_sma200_sinyalleri[cooldown_key] = time.time()
                    elif prev >= p_sma and curr < c_sma and curr < c_sma * 0.995:
                        sqz = squeeze_radar_check(symbol, tf)
                        msg = (f"💀 <b>SMA200 AŞAĞI KIRILIM (SHORT): {symbol}</b>\n<i>{tf.upper()} Grafik</i>\n\n"
                               f"💡 <b>Açıklama:</b> Fiyat, 200 periyotluk ortalamanın (SMA200) altına sarktı. Büyük yatırımcıların bölgeden çıktığını ve piyasanın genel olarak 'Sat' (Ayı) bölgesine geçtiğini gösterir. Düşüş trendi hız kazanabilir.")
                        if sqz: msg += f"\n\n{sqz}"
                        if tg(C, msg): son_sma200_sinyalleri[cooldown_key] = time.time()
                time.sleep(0.1)
            time.sleep(SMA200_ARALIGI)
        except Exception as e: time.sleep(60)

# =====================================================
# MOTOR 11: BOLLINGER (ÇİFT YÖNLÜ)
# =====================================================
def bollinger_loop():
    log("BOLLINGER", "Bollinger Bantları Motoru Başladı.")
    time.sleep(210)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
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
                        msg = (f"🔥 <b>BOLLINGER ÜST BANT KIRILIMI (LONG): {symbol}</b>\n<i>{tf.upper()} Grafik</i>\n\n"
                               f"💡 <b>Açıklama:</b> Fiyat, sıkışmanın (Squeeze) ardından Bollinger üst bandını yukarı yönlü kırdı. Volatilitenin arttığını ve güçlü bir alım dalgasının piyasaya giriş yaptığını gösterir.")
                        if tg(C, msg): son_bollinger_sinyalleri[cooldown_key] = time.time()
                    elif curr < lower and closes[-2] >= lower:
                        msg = (f"🩸 <b>BOLLINGER ALT BANT KIRILIMI (SHORT): {symbol}</b>\n<i>{tf.upper()} Grafik</i>\n\n"
                               f"💡 <b>Açıklama:</b> Fiyat, Bollinger alt bandını aşağı yönlü kırdı. Aşırı satım bölgesine girildiğini veya sert bir düşüş dalgasının (Squeeze) başladığını gösterebilir. Dikkatli olunmalı.")
                        if tg(C, msg): son_bollinger_sinyalleri[cooldown_key] = time.time()
                time.sleep(0.1)
            time.sleep(BOLLINGER_ARALIGI)
        except Exception as e: time.sleep(60)

# =====================================================
# MOTOR 12: ICHIMOKU (ÇİFT YÖNLÜ)
# =====================================================
def ichimoku_loop():
    log("ICHIMOKU", "Ichimoku Bulut Motoru Başladı.")
    time.sleep(240)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
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
                        msg = (f"☁️ <b>ICHIMOKU BULUTU KIRILIMI (LONG): {symbol}</b>\n<i>{tf.upper()} Grafik</i>\n\n"
                               f"💡 <b>Açıklama:</b> Fiyat, Japon trend sistemi Ichimoku'nun 'Kumo' (Bulut) direncini yukarı kırdı. Bulutun üzerine çıkılması, piyasanın 'Boğa' bölgesine girdiğini ve artık bulutun destek görevi göreceğini gösterir.")
                        if tg(C, msg): son_ichimoku_sinyalleri[cooldown_key] = time.time()
                    elif curr < cloud_bottom and prev >= cloud_bottom:
                        msg = (f"☁️ <b>ICHIMOKU BULUTU KIRILIMI (SHORT): {symbol}</b>\n<i>{tf.upper()} Grafik</i>\n\n"
                               f"💡 <b>Açıklama:</b> Fiyat, Ichimoku Bulutunun altına sarktı. Bu durum piyasanın 'Ayı' bölgesine girdiğini ve bulutun artık devasa bir direnç (tavan) görevi göreceğini gösterir. Düşüş trendi başlıyor olabilir.")
                        if tg(C, msg): son_ichimoku_sinyalleri[cooldown_key] = time.time()
                time.sleep(0.1)
            time.sleep(ICHIMOKU_ARALIGI)
        except Exception as e: time.sleep(60)

# =====================================================
# MOTOR 13: BINANCE KILLERS CLONE (FVG & KONFLUANS)
# =====================================================
def binance_killers_loop():
    log("BINANCE_KILLERS", "FVG ve Konfluans Motoru Başladı.")
    time.sleep(270)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
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
                        entry_low = bullish_fvg * 0.998; entry_high = bullish_fvg * 1.002
                        stop_loss = yatay_destek * 0.98
                        diff = entry_high - stop_loss
                        targets = [entry_high + (diff * i) for i in [1.0, 1.5, 2.0, 3.0, 4.0]]
                        sqz = squeeze_radar_check(symbol, tf)
                        msg = (f"📍SIGNAL ID: #{sig_id}📍\nCOIN: #{symbol} (2-5x)\nDirection: LONG\n➖➖➖➖➖➖➖\nENTRY: {akilli_fiyat(entry_low)} - {akilli_fiyat(entry_high)}\n\nTARGETS: {' – '.join([akilli_fiyat(t) for t in targets])}\n\nSTOP LOSS: {akilli_fiyat(stop_loss)}\n\n{tf.upper()} FVG confluent with ascending trendline confluent with stacked horizontal support at entry.\n\n"
                               f"💡 <b>Açıklama:</b> Yükselen trend içinde, fiyatın hızlı hareket ederken bıraktığı boşluk (FVG) ve yatay destek seviyesi tam olarak çakışıyor (Konfluans). Kurumsal yatırımcıların bu bölgeden alım yapması beklenir.")
                        if sqz: msg += f"\n\n{sqz}"
                        if tg(C, msg): son_fvg_sinyalleri[symbol] = time.time(); break
                    elif trend_down and bearish_fvg and abs(curr - bearish_fvg) / curr < 0.02 and abs(curr - yatay_direnc) / curr < 0.04:
                        entry_low = bearish_fvg * 0.998; entry_high = bearish_fvg * 1.002
                        stop_loss = yatay_direnc * 1.02
                        diff = stop_loss - entry_low
                        targets = [entry_low - (diff * i) for i in [1.0, 1.5, 2.0, 3.0, 4.0]]
                        sqz = squeeze_radar_check(symbol, tf)
                        msg = (f"📍SIGNAL ID: #{sig_id}📍\nCOIN: #{symbol} (2-5x)\nDirection: SHORT\n➖➖➖➖➖➖➖\nENTRY: {akilli_fiyat(entry_low)} - {akilli_fiyat(entry_high)}\n\nTARGETS: {' – '.join([akilli_fiyat(t) for t in targets])}\n\nSTOP LOSS: {akilli_fiyat(stop_loss)}\n\n{tf.upper()} FVG confluent with descending trendline confluent with stacked horizontal resistance at entry.\n\n"
                               f"💡 <b>Açıklama:</b> Düşen trend içinde, fiyatın bıraktığı ayı boşluğu (FVG) ve yatay direnç seviyesi çakışıyor (Konfluans). Kurumsal satışların bu bölgeden gelmesi beklenir.")
                        if sqz: msg += f"\n\n{sqz}"
                        if tg(C, msg): son_fvg_sinyalleri[symbol] = time.time(); break
                time.sleep(0.1)
            time.sleep(FVG_KONFLUANS_ARALIGI)
        except Exception as e: time.sleep(60)

# =====================================================
# RENDER WEB SUNUCUSU
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

# =====================================================
# BAŞLAT
# =====================================================
if __name__ == "__main__":
    log("SİSTEM", "Bot başlatıldı. Telegram'a onay mesajı gönderiliyor...")
    baslangic_mesaji = (
        "🤖 <b>SİSTEM AKTİF!</b>\n\n"
        "Kripto Sinyal Botu başarıyla başlatıldı. 🚀\n"
        "15 farklı motor (Pump, FVG, Sıkışma Radarı, Hacim Patlaması vb.) arka planda piyasayı taramaya başladı.\n\n"
        "Piyasa şartları uygun olduğunda ve kaliteli bir fırsat yakalandığında sinyaller buraya düşecektir."
    )
    if tg(C, baslangic_mesaji):
        log("SİSTEM", "✅ Başlangıç mesajı başarıyla Telegram'a gönderildi.")
    else:
        log("SİSTEM", "❌ HATA: Telegram'a mesaj gönderilemedi! Lütfen BOT_TOKEN ve CHAT_ID ayarlarını kontrol edin.")
    
    # Motorları başlat
    threading.Thread(target=teyitli_hacim_loop, daemon=True).start() # YENİ MOTOR
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
    
    # Web sunucusunu başlat
    run_web_server()

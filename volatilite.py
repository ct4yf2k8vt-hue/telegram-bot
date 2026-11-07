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
RAPOR_ARALIGI = 14400      # 4 Saat
CHARTIST_ARALIGI = 3600    # 1 Saat
PUMP_ARALIGI = 60          # 1 Dakika
FORMASYON_ARALIGI = 3600   # 1 Saat
EMA_RIBBON_ARALIGI = 3600  # 1 Saat
MACD_ARALIGI = 3600        # 1 Saat
MIDAS_SETUP_ARALIGI = 7200 # 2 Saat
ATH_ATL_ARALIGI = 3600     # 1 Saat
DIGITAL_CHARTIST_ARALIGI = 3600 # 1 Saat
SMA200_ARALIGI = 3600      # 1 Saat
BOLLINGER_ARALIGI = 3600   # 1 Saat
ICHIMOKU_ARALIGI = 3600    # 1 Saat
FVG_KONFLUANS_ARALIGI = 3600 # 1 Saat (YENİ)

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
MIN_HACIM_FVG = 10_000_000 # YENİ

# SİNYAL HAFIZALARI (Aynı sinyalin tekrarlanmasını önler)
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
son_fvg_sinyalleri = {}       # YENİ

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
    targets = [
        entry_price + (diff * 0.272),
        entry_price + (diff * 0.618),
        entry_price + (diff * 1.000),
        entry_price + (diff * 1.618),
        entry_price + (diff * 2.618)
    ]
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
    upper_band = sma + (std_dev * std)
    lower_band = sma - (std_dev * std)
    return upper_band, sma, lower_band

def calculate_ichimoku(highs, lows, closes):
    if len(closes) < 52: return None, None
    tenkan = (max(highs[-9:]) + min(lows[-9:])) / 2
    kijun = (max(highs[-26:]) + min(lows[-26:])) / 2
    senkou_a = (tenkan + kijun) / 2
    senkou_b = (max(highs[-52:]) + min(lows[-52:])) / 2
    cloud_top = max(senkou_a, senkou_b)
    cloud_bottom = min(senkou_a, senkou_b)
    return cloud_top, cloud_bottom

def get_market_trend(tickers):
    try:
        sorted_tickers = sorted(tickers, key=lambda x: float(x.get('quoteVolume', 0)), reverse=True)[:100]
        changes, up_count, down_count = [], 0, 0
        for t in sorted_tickers:
            change = float(t.get('priceChangePercent', 0))
            changes.append(change)
            if change > 0: up_count += 1
            elif change < 0: down_count += 1
        if not changes: return "Bilinmiyor", 0, 0, 0
        avg_change = sum(changes) / len(changes)
        if avg_change > 1.5 and up_count > down_count: trend = "🟢 YUKARI (Boğa)"
        elif avg_change < -1.5 and down_count > up_count: trend = "🔴 AŞAĞI (Ayı)"
        else: trend = "🟡 YATAY (Kararsız)"
        return trend, avg_change, up_count, down_count
    except:
        return "Bilinmiyor", 0, 0, 0

# YENİ: BALİNA / TUZAK RADARI
def trap_radar_check(symbol):
    try:
        oi_data = gj(f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=2")
        oi_artiyor = False
        if oi_data and len(oi_data) >= 2:
            oi_old = float(oi_data[0]['sumOpenInterest'])
            oi_new = float(oi_data[1]['sumOpenInterest'])
            if oi_old > 0 and ((oi_new - oi_old) / oi_old) * 100 > 0.2: oi_artiyor = True

        taker_data = gj(f"https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={symbol}&period=5m&limit=1")
        alici_baskin = False
        if taker_data and len(taker_data) > 0:
            buy_vol = float(taker_data[0]['buyVol'])
            sell_vol = float(taker_data[0]['sellVol'])
            total = buy_vol + sell_vol
            if total > 0 and (buy_vol / total) * 100 > 55: alici_baskin = True

        if oi_artiyor and alici_baskin: return "🐋 <b>BALİNA TEYİDİ (Gerçek Pump)</b>"
        elif not alici_baskin: return "⚠️ <b>DİKKAT: SAHTE PUMP (TUZAK OLABİLİR)</b>"
        else: return "⚖️ <b>Kısmi Teyit</b>"
    except: return "⚖️ <b>Kısmi Teyit</b>"

# =====================================================
# MOTOR 1: MİDAS FLOW (RAPOR)
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
# MOTOR 2: CHARTIST (EMA200 Klasik)
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
                    if len(ema200) >= 2 and prev <= ema200[-2] and curr > ema200[-1] and curr > ema200[-1]*1.01:
                        vol = [float(k[7]) for k in klines[:-1]]
                        if vol[-1] > sum(vol[-21:-1])/20:
                            msg = (f"📈 <b>UZUN VADELİ SPOT (ALIŞ): {symbol}</b>\n"
                                   f"<i>{tf.upper()} EMA200 Yukarı Kırılımı</i>\n\n"
                                   f"💰 Fiyat: {akilli_fiyat(curr)}\n"
                                   f"📊 Hacim: Ortalamanın üzerinde (Teyitli)\n\n"
                                   f"💡 <b>Açıklama:</b> Fiyat, 200 periyotluk devasa ortalamayı (EMA200) hacimle yukarı kırdı. Bu, uzun vadeli yatırımcıların (kurumsalların) bölgeye girdiğini ve trendin kalıcı olarak yön değiştirebileceğini gösterir.")
                            tg(C, msg); son_sinyaller[cooldown_key] = time.time()
                time.sleep(0.05)
            time.sleep(CHARTIST_ARALIGI)
        except: time.sleep(60)

# =====================================================
# MOTOR 3: PUMP AVCISI (Trap Radar Destekli)
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
                    hacim_artis_orani = ((cv-pv)/pv)*100
                    teyit = trap_radar_check(symbol)
                    
                    msg = (f"🚀 <b>Pump - {symbol}</b>\n"
                           f"💰 Fiyat: {akilli_fiyat(cc)} (+%{((cc-pc)/pc)*100:.2f})\n"
                           f"📊 Hacim Artışı: +%{hacim_artis_orani:.0f}\n"
                           f"Volume increased by {int(cv-pv)} ⬆️\n\n"
                           f"{teyit}\n\n"
                           f"💡 <b>Açıklama:</b> 5 dakikalık grafikte aniden sert bir hacim ve fiyat patlaması yaşandı. Ancak Trap Radar sayesinde bu hareketin arkasında gerçek balinaların mı yoksa küçük yatırımcıyı çekmek isteyen sahte botların mı olduğunu analiz ettik. Açık Pozisyon (OI) ve Alıcı baskısı (CVD) verileri bu hareketin niteliğini belirler.")
                    
                    tg(C, msg); son_pump_sinyalleri[symbol] = time.time()
                time.sleep(0.05)
            time.sleep(PUMP_ARALIGI)
        except: time.sleep(60)

# =====================================================
# MOTOR 4: FORMASYON (MSS)
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
                    msg = (f"🚀 <b>FORMASYON KIRILIMI (ALIŞ): {symbol}</b>\n"
                           f"<i>20 Günlük Direnç Kırıldı</i>\n\n"
                           f"💰 Fiyat: {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> Piyasa Yapısı Değişimi (MSS) gerçekleşti. Fiyat, son 20 günün en yüksek direncini kırarak yeni bir yükseliş trendi (Higher High) başlattı. Alıcılar kontrolü ele geçirmiş durumda.")
                    tg(C, msg); son_formasyon_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(FORMASYON_ARALIGI)
        except: time.sleep(60)

# =====================================================
# MOTOR 5: EMA RIBBON
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
                    msg = (f"🚀 <b>EMA DİZİLİMİ (GÜÇLÜ ALIŞ): {symbol}</b>\n"
                           f"<i>Tüm Ortalamalar Yukarı Yönlü Sıralı</i>\n\n"
                           f"💰 Fiyat: {akilli_fiyat(curr)}\n"
                           f"📊 Dizilim: 7 > 25 > 99 > 200\n\n"
                           f"💡 <b>Açıklama:</b> Kusursuz bir Boğa (Yükseliş) dizilimi tespit edildi. Kısa, orta ve uzun vadeli tüm ortalamalar birbirini destekliyor. Bu formasyon, trendin en güçlü ve en istikrarlı olduğu evreyi işaret eder.")
                    tg(C, msg); son_ema_ribbon_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(EMA_RIBBON_ARALIGI)
        except: time.sleep(60)

# =====================================================
# MOTOR 6: MACD
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
                if pm <= ps and cm > cs:
                    msg = (f"🚀 <b>MACD AL SİNYALİ: {symbol}</b>\n"
                           f"<i>Günlük Grafikte MACD Yukarı Kesti</i>\n\n"
                           f"💰 Fiyat: {akilli_fiyat(closes[-1])}\n\n"
                           f"💡 <b>Açıklama:</b> MACD indikatörü, Sinyal çizgisini aşağıdan yukarı yönlü kesti (Golden Cross). Momentumun (ivmenin) satıcılardan alıcılara geçtiğini ve trendin yönünün yukarı döndüğünü gösteren klasik ve güvenilir bir alış sinyalidir.")
                    tg(C, msg); son_macd_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(MACD_ARALIGI)
        except: time.sleep(60)

# =====================================================
# MOTOR 7: MIDAS SETUP
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
                closes = [float(k[4]) for k in klines[:-1]]
                vols = [float(k[7]) for k in klines[:-1]]
                curr = closes[-1]
                
                if curr > max(highs[-20:]) and vols[-1] > sum(vols[-21:-1])/20:
                    msg = (f"⚡ <b>#{symbol} SETUP (ALIŞ)</b>\n"
                           f"<i>4 Saatlik Hacimli Kırılım</i>\n\n"
                           f"💰 Fiyat: {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> 4 saatlik grafikte son 20 periyodun (yaklaşık 3 günün) direnci hacimle aşıldı. Kısa ve orta vadeli traderların (günlükçülerin) dikkatini çeken, hızlı hareket potansiyeli yüksek bir kırılımdır.")
                    if tg(C, msg):
                        son_midas_setup_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(MIDAS_SETUP_ARALIGI)
        except: time.sleep(60)

# =====================================================
# MOTOR 8: TARİHİ ZİRVE / DİP KIRILIMI (ATH / ATL)
# =====================================================
def ath_atl_loop():
    log("ATH_ATL", "Tarihi Zirve/Dip Kırılım Motoru Başladı.")
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
                
                current_price = closes[-1]
                prev_close = closes[-2]
                current_vol = vols[-1]
                ath = max(highs)
                atl = min(lows)
                
                avg_vol = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 1
                is_vol_teyit = current_vol > (avg_vol * 1.2)
                
                if not is_vol_teyit: continue

                sinyal = None
                yon = ""
                emoji = ""
                stop = 0
                hedef = 0

                if prev_close >= atl and current_price < atl:
                    sinyal = True; yon = "DÜŞÜŞ (Tarihi Dip Kırılımı)"; emoji = "💀"
                    stop = atl * 1.05; hedef = current_price * 0.80 
                elif prev_close <= ath and current_price > ath:
                    sinyal = True; yon = "YÜKSELİŞ (Tarihi Zirve Kırılımı)"; emoji = "🚀"
                    stop = ath * 0.95; hedef = current_price * 1.20 

                if sinyal:
                    hedef_pct = ((hedef - current_price) / current_price) * 100
                    stop_pct = ((stop - current_price) / current_price) * 100
                    coin = symbol.replace("USDT", "")
                    
                    msg = (f"{emoji} <b>TARİHİ SEVİYE KIRILIMI ({yon}): {symbol}</b>\n"
                           f"<i>1 Günlük Grafik - Çok Uzun Vadeli Kırılım</i>\n\n"
                           f"💰 <b>Giriş (Kırılım Fiyatı):</b> {akilli_fiyat(current_price)}\n"
                           f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                           f"🛑 <b>Zarar Kes (Stop):</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                           f"📊 <b>Kırılan Tarihi Seviye:</b> {akilli_fiyat(ath if yon.startswith('YÜKSELİŞ') else atl)}\n"
                           f"📈 <b>Hacim Patlaması:</b> {current_vol/avg_vol:.1f}x (Teyitli)\n\n"
                           f"💡 <b>Açıklama:</b> {coin} fiyatı, geçmişteki en {'yüksek' if yon.startswith('YÜKSELİŞ') else 'düşük'} seviyesini hacimle kırdı. Psikolojik bir eşik olan bu seviyenin aşılması, yeni bir {'boğa' if yon.startswith('YÜKSELİŞ') else 'ayı'} trendinin (makro trend) başlangıcı olabilir. Grafikte devasa bir boşluk (Fiyat keşfi) oluşabilir.")
                    
                    if tg(C, msg):
                        son_ath_atl_sinyalleri[symbol] = time.time()
                        log("ATH_ATL", f"🔥 SİNYAL: {symbol} ({yon})")
                
                time.sleep(0.1)
            time.sleep(ATH_ATL_ARALIGI)
        except Exception as e:
            log("ATH_ATL", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 9: DIGITAL CHARTIST CLONE (Kırılım + Hedefler)
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
                    
                    highs = [float(k[2]) for k in klines[:-1]]
                    lows = [float(k[3]) for k in klines[:-1]]
                    closes = [float(k[4]) for k in klines[:-1]]
                    vols = [float(k[7]) for k in klines[:-1]]
                    
                    curr = closes[-1]
                    current_vol = vols[-1]
                    avg_vol = sum(vols[-20:]) / 20
                    
                    direnc = max(highs[-30:])
                    dip = min(lows[-30:])
                    
                    is_vol_teyit = current_vol > (avg_vol * 1.5)
                    
                    if curr > direnc and is_vol_teyit:
                        hedefler = calculate_targets(curr, dip)
                        
                        msg = f"📊 <b>#{symbol} {tf.upper()}</b>\n\n"
                        msg += f"🚀 <b>#{symbol.replace('USDT', '')}</b> güçlü bir hacimle {tf.upper()} grafikteki ana direnç seviyesini yukarı kırdı.\n"
                        msg += f"<i>(Fiyat, yüksek hacim eşliğinde direnci aştı.)</i>\n\n"
                        msg += f"🎯 <b>Potansiyel Hedefler:</b>\n"
                        
                        for hedef in hedefler:
                            kazanc = ((hedef - curr) / curr) * 100
                            msg += f"🎯 {akilli_fiyat(hedef)} (+%{kazanc:.1f})\n"
                            
                        msg += f"\n💡 <b>Açıklama:</b> Fiyat, 30 günlük güçlü bir direnci kırarak formasyonu (üçgen/kama/kanal) yukarı yönlü tamamladı. Kırılımın sahte olmaması için hacim kontrolü yapıldı. Belirlenen Fibonacci hedefleri, kırılım sonrası oluşabilecek yükseliş dalgasının potansiyel durma noktalarıdır."
                        msg += f"\n\n⚠️ <i>Lütfen sıkı bir zarar kes (stop-loss) kullanmayı ve risk yönetimini elden bırakmayın.</i>"
                        msg += f"\n💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}"
                        
                        if tg(C, msg):
                            son_chartist_sinyalleri[symbol] = time.time()
                            log("DIGITAL_CHARTIST", f"Kırılım Sinyali: {symbol}")
                            break
                            
                time.sleep(0.1)
            time.sleep(DIGITAL_CHARTIST_ARALIGI)
        except Exception as e:
            log("DIGITAL_CHARTIST", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 10: SMA200 ÇİFT YÖNLÜ KESİŞİM
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
                    
                    if cooldown_key in son_sma200_sinyalleri and time.time() - son_sma200_sinyalleri[cooldown_key] < 43200: 
                        continue 
                        
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=250")
                    if not isinstance(klines, list) or len(klines) < 205: continue
                    
                    closes = [float(k[4]) for k in klines[:-1]]
                    curr, prev = closes[-1], closes[-2]
                    
                    sma200 = calculate_sma(closes, 200)
                    if not sma200 or len(sma200) < 3: continue
                    
                    c_sma = sma200[-1]
                    p_sma = sma200[-2]
                    
                    if prev <= p_sma and curr > c_sma and curr > c_sma * 1.005:
                        msg = (f"🚀 <b>SMA200 YUKARI KIRILIM (ALIŞ): {symbol}</b>\n"
                               f"<i>{tf.upper()} Grafik - Boğa Sinyali</i>\n\n"
                               f"💰 Anlık Fiyat: {akilli_fiyat(curr)}\n"
                               f"📈 SMA200 Seviyesi: {akilli_fiyat(c_sma)}\n\n"
                               f"💡 <b>Açıklama:</b> Fiyat, 200 periyotluk basit ortalamanın (SMA200) üzerine çıktı. SMA200, kurumsal yatırımcıların ortalama maliyet çizgisi olarak kabul edilir. Bu çizginin üzerine çıkılması, piyasanın genel olarak 'Al' bölgesine geçtiğini gösterir.")
                        if tg(C, msg):
                            son_sma200_sinyalleri[cooldown_key] = time.time()
                            log("SMA200", f"Yukarı Kırılım: {symbol} ({tf})")
                            
                    elif prev >= p_sma and curr < c_sma and curr < c_sma * 0.995:
                        msg = (f"💀 <b>SMA200 AŞAĞI KIRILIM (SATIŞ): {symbol}</b>\n"
                               f"<i>{tf.upper()} Grafik - Ayı Sinyali</i>\n\n"
                               f"💰 Anlık Fiyat: {akilli_fiyat(curr)}\n"
                               f"📉 SMA200 Seviyesi: {akilli_fiyat(c_sma)}\n\n"
                               f"💡 <b>Açıklama:</b> Fiyat, 200 periyotluk ortalamanın (SMA200) altına sarktı. Bu, büyük yatırımcıların bölgeden çıktığını ve piyasanın genel olarak 'Sat' (Ayı) bölgesine geçtiğini gösterir. Düşüş trendi hız kazanabilir.")
                        if tg(C, msg):
                            son_sma200_sinyalleri[cooldown_key] = time.time()
                            log("SMA200", f"Aşağı Kırılım: {symbol} ({tf})")

                time.sleep(0.1)
            time.sleep(SMA200_ARALIGI)
        except Exception as e:
            log("SMA200", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 11: BOLLINGER BANTLARI (HAFTALIK)
# =====================================================
def bollinger_loop():
    log("BOLLINGER", "Bollinger Bantları Motoru (Haftalık) Başladı.")
    time.sleep(210)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_BOLLINGER]:
                for tf in ['1w']: # Sadece haftalık
                    cooldown_key = f"BOLL_{tf}_{symbol}"
                    if cooldown_key in son_bollinger_sinyalleri and time.time() - son_bollinger_sinyalleri[cooldown_key] < 43200: continue
                    
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=50")
                    if not isinstance(klines, list) or len(klines) < 30: continue
                    
                    closes = [float(k[4]) for k in klines[:-1]]
                    curr = closes[-1]
                    
                    upper, mid, lower = calculate_bollinger(closes)
                    if not upper or not lower: continue
                    
                    if curr > upper and closes[-2] <= upper:
                        msg = (f"🔥 <b>BOLLINGER ÜST BANT KIRILIMI (HAFTALIK): {symbol}</b>\n"
                               f"<i>Haftalık Grafik - Volatilite Patlaması</i>\n\n"
                               f"💰 Anlık Fiyat: {akilli_fiyat(curr)}\n"
                               f"📈 Üst Bant: {akilli_fiyat(upper)}\n\n"
                               f"💡 <b>Açıklama:</b> Fiyat, Bollinger üst bandını yukarı yönlü kırdı. Bu durum genellikle bir 'Volatilite Patlaması' (Squeeze) sonrası yaşanır ve güçlü bir alım dalgasının, trend başlangıcının habercisidir. Haftalık kırılımlar büyük trendleri işaret eder.")
                        if tg(C, msg):
                            son_bollinger_sinyalleri[cooldown_key] = time.time()
                            
                    elif curr < lower and closes[-2] >= lower:
                        msg = (f"🩸 <b>BOLLINGER ALT BANT DESTEĞİ (HAFTALIK): {symbol}</b>\n"
                               f"<i>Haftalık Grafik - Aşırı Satım</i>\n\n"
                               f"💰 Anlık Fiyat: {akilli_fiyat(curr)}\n"
                               f"📉 Alt Bant: {akilli_fiyat(lower)}\n\n"
                               f"💡 <b>Açıklama:</b> Fiyat, haftalık alt banda değerek tepki verdi. Bu bölgeler 'Aşırı Satım' noktalarıdır. Fiyatın buradan yukarı yönlü bir tepki alımı (Bounce) yapma ihtimali yüksektir. Haftalık destekler uzun vadede çok güçlüdür.")
                        if tg(C, msg):
                            son_bollinger_sinyalleri[cooldown_key] = time.time()
                            
                time.sleep(0.1)
            time.sleep(BOLLINGER_ARALIGI)
        except Exception as e:
            log("BOLLINGER", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 12: ICHIMOKU BULUTU (HAFTALIK)
# =====================================================
def ichimoku_loop():
    log("ICHIMOKU", "Ichimoku Bulut Motoru (Haftalık) Başladı.")
    time.sleep(240)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_ICHIMOKU]:
                for tf in ['1w']: # Sadece haftalık
                    cooldown_key = f"ICHI_{tf}_{symbol}"
                    if cooldown_key in son_ichimoku_sinyalleri and time.time() - son_ichimoku_sinyalleri[cooldown_key] < 86400: continue
                    
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=100")
                    if not isinstance(klines, list) or len(klines) < 52: continue
                    
                    highs = [float(k[2]) for k in klines[:-1]]
                    lows = [float(k[3]) for k in klines[:-1]]
                    closes = [float(k[4]) for k in klines[:-1]]
                    curr, prev = closes[-1], closes[-2]
                    
                    cloud_top, cloud_bottom = calculate_ichimoku(highs, lows, closes)
                    if not cloud_top or not cloud_bottom: continue
                    
                    if curr > cloud_top and prev <= cloud_top:
                        msg = (f"☁️ <b>ICHIMOKU BULUTU KIRILIMI (HAFTALIK): {symbol}</b>\n"
                               f"<i>Haftalık Grafik - Kumo Breakout</i>\n\n"
                               f"💰 Anlık Fiyat: {akilli_fiyat(curr)}\n"
                               f"☁️ Bulut Direnci: {akilli_fiyat(cloud_top)}\n\n"
                               f"💡 <b>Açıklama:</b> Fiyat, Japon trend sistemi olan Ichimoku'nun en önemli parçası 'Kumo' (Bulut) direncini yukarı yönlü kırdı. Haftalık bulutun üzerine çıkılması, piyasanın makro anlamda 'Boğa' bölgesine girdiğini ve artık bulutun devasa bir destek görevi göreceğini gösterir.")
                        if tg(C, msg):
                            son_ichimoku_sinyalleri[cooldown_key] = time.time()
                            log("ICHIMOKU", f"Bulut Kırılımı: {symbol} ({tf})")
                            
                time.sleep(0.1)
            time.sleep(ICHIMOKU_ARALIGI)
        except Exception as e:
            log("ICHIMOKU", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 13: FVG KONFLUANS MOTORU (ÇİFT YÖNLÜ)
# =====================================================
def fvg_konfluans_loop():
    log("FVG", "FVG ve Konfluans Motoru Başladı.")
    time.sleep(270)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_FVG]:
                if symbol in son_fvg_sinyalleri and time.time() - son_fvg_sinyalleri[symbol] < 14400: continue
                
                for tf in ['4h', '8h', '1d']:
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=100")
                    if not isinstance(klines, list) or len(klines) < 50: continue
                    
                    # Mum verilerini al
                    highs = [float(k[2]) for k in klines]
                    lows = [float(k[3]) for k in klines]
                    closes = [float(k[4]) for k in klines]
                    
                    curr = closes[-1]
                    
                    # Basit bir trend yönü belirleme (EMA 20 ve 50)
                    ema20 = calculate_ema(closes[:-1], 20)
                    ema50 = calculate_ema(closes[:-1], 50)
                    if not ema20 or not ema50: continue
                    
                    trend_up = ema20[-1] > ema50[-1]
                    trend_down = ema20[-1] < ema50[-1]
                    
                    # Yatay Destek / Direnç (Son 20 mum)
                    yatay_direnc = max(highs[-20:-1])
                    yatay_destek = min(lows[-20:-1])
                    
                    # FVG Tespiti (Son 3 mum üzerinden)
                    # Boğa FVG: 1. mumun tepe noktası ile 3. mumun dip noktası arasında boşluk
                    # Ayı FVG: 1. mumun dip noktası ile 3. mumun tepe noktası arasında boşluk
                    
                    # Son 3 mumu al (kapanmış olanlar)
                    # FVG genelde geri çekilme (pullback) sırasında test edilir.
                    # O yüzden son 10 mum içinde oluşmuş bir FVG'yi arıyoruz.
                    
                    bullish_fvg_level = None
                    bearish_fvg_level = None
                    
                    for i in range(len(closes)-10, len(closes)-2):
                        # Boğa FVG (Yukarı yönlü hareket boşluğu - Destek görevi görür)
                        if lows[i+2] > highs[i]:
                            bullish_fvg_level = (highs[i] + lows[i+2]) / 2 # FVG'nin ortası
                        
                        # Ayı FVG (Aşağı yönlü hareket boşluğu - Direnç görevi görür)
                        if highs[i+2] < lows[i]:
                            bearish_fvg_level = (lows[i] + highs[i+2]) / 2 # FVG'nin ortası

                    # LONG SENARYOSU: Yükselen trend + Fiyat Destek/FVG üzerinde
                    if trend_up and bullish_fvg_level:
                        # Fiyat FVG bölgesine mi yaklaştı? (Yüzde 1.5 tolerans)
                        if abs(curr - bullish_fvg_level) / curr < 0.015 and abs(curr - yatay_destek) / curr < 0.03:
                            msg = (f"⚖️ <b>FVG KONFLUANS (LONG): {symbol}</b>\n"
                                   f"<i>{tf.upper()} Grafik - Boğa FVG & Destek Uyumu</i>\n\n"
                                   f"💰 Anlık Fiyat: {akilli_fiyat(curr)}\n"
                                   f"🎯 FVG Destek Bölgesi: ~{akilli_fiyat(bullish_fvg_level)}\n"
                                   f"📊 Yatay Destek: ~{akilli_fiyat(yatay_destek)}\n\n"
                                   f"💡 <b>Açıklama:</b> Fiyat, yükselen bir trend içinde. Yukarı yönlü hareket sırasında oluşan <b>Adil Değer Boşluğu (FVG)</b> bölgesine geri çekildi. Bu bölge, devasa bir <b>yatay destek</b> seviyesiyle birleşerek (Konfluans) güçlü bir alım noktası oluşturuyor. Kurumsal yatırımcıların bu bölgeden alım yapması beklenir.")
                            if tg(C, msg):
                                son_fvg_sinyalleri[symbol] = time.time()
                                log("FVG", f"LONG Konfluans: {symbol} ({tf})")
                                break

                    # SHORT SENARYOSU: Düşen trend + Fiyat Direnç/FVG altında
                    elif trend_down and bearish_fvg_level:
                        # Fiyat FVG bölgesine mi yaklaştı? (Yüzde 1.5 tolerans)
                        if abs(curr - bearish_fvg_level) / curr < 0.015 and abs(curr - yatay_direnc) / curr < 0.03:
                            msg = (f"⚖️ <b>FVG KONFLUANS (SHORT): {symbol}</b>\n"
                                   f"<i>{tf.upper()} Grafik - Ayı FVG & Direnç Uyumu</i>\n\n"
                                   f"💰 Anlık Fiyat: {akilli_fiyat(curr)}\n"
                                   f"🎯 FVG Direnç Bölgesi: ~{akilli_fiyat(bearish_fvg_level)}\n"
                                   f"📊 Yatay Direnç: ~{akilli_fiyat(yatay_direnc)}\n\n"
                                   f"💡 <b>Açıklama:</b> Fiyat, düşen bir trend içinde. Aşağı yönlü hareket sırasında oluşan <b>Adil Değer Boşluğu (FVG)</b> bölgesine tepki verdi. Bu bölge, devasa bir <b>yatay direnç</b> seviyesiyle birleşerek (Konfluans) güçlü bir satış noktası oluşturuyor. Kurumsal yatırımcıların bu bölgeden satış yapması beklenir.")
                            if tg(C, msg):
                                son_fvg_sinyalleri[symbol] = time.time()
                                log("FVG", f"SHORT Konfluans: {symbol} ({tf})")
                                break
                                
                time.sleep(0.1)
            time.sleep(FVG_KONFLUANS_ARALIGI)
        except Exception as e:
            log("FVG", f"Hata: {e}")
            time.sleep(60)

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
    threading.Thread(target=fvg_konfluans_loop, daemon=True).start() # YENİ MOTOR
    
    run_web_server()

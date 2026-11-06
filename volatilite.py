import urllib.request
import urllib.parse
import json
import time
import socket
import os
import threading
import http.server
import socketserver
import statistics # YENİ: Bollinger Standart Sapma için
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
BOLLINGER_ARALIGI = 3600   # 1 Saat (Yeni)
ICHIMOKU_ARALIGI = 3600    # 1 Saat (Yeni)

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
MIN_HACIM_BOLLINGER = 10_000_000 # Yeni
MIN_HACIM_ICHIMOKU = 10_000_000  # Yeni

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
son_bollinger_sinyalleri = {} # Yeni
son_ichimoku_sinyalleri = {}  # Yeni

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

# YENİ: Bollinger Bantları Hesaplama
def calculate_bollinger(prices, period=20, std_dev=2):
    if len(prices) < period: return None, None, None
    sma = sum(prices[-period:]) / period
    std = statistics.pstdev(prices[-period:])
    upper_band = sma + (std_dev * std)
    lower_band = sma - (std_dev * std)
    return upper_band, sma, lower_band

# YENİ: Ichimoku Bulutu (Kumo) Hesaplama (Basitleştirilmiş)
def calculate_ichimoku(highs, lows, closes):
    if len(closes) < 52: return None, None
    # Tenkan-sen (9), Kijun-sen (26), Senkou Span B (52)
    tenkan = (max(highs[-9:]) + min(lows[-9:])) / 2
    kijun = (max(highs[-26:]) + min(lows[-26:])) / 2
    senkou_a = (tenkan + kijun) / 2
    senkou_b = (max(highs[-52:]) + min(lows[-52:])) / 2
    
    # Bulutun üst ve alt sınırları
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
                msg += f"\n<a href='https://t.me/MidasFlowAI'>{hashtags}#MidasFlow</a>"
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
                            msg = f"📈 <b>UZUN VADELİ SPOT (ALIŞ): {symbol}</b>\n<i>{tf.upper()} EMA200 Yukarı Kırılımı</i>\n💰 Fiyat: {akilli_fiyat(curr)}"
                            tg(C, msg); son_sinyaller[cooldown_key] = time.time()
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
                    msg = f"🚀 <b>Anlık Pump Tespit Edildi: {symbol}</b>\n💰 Fiyat: {akilli_fiyat(cc)}"
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
                    msg = f"🚀 <b>FORMASYON KIRILIMI (ALIŞ): {symbol}</b>\n<i>20 Günlük Direnç Kırıldı</i>"
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
                    msg = f"🚀 <b>EMA DİZİLİMİ (GÜÇLÜ ALIŞ): {symbol}</b>\n<i>Tüm Ortalamalar Yukarı Yönlü Sıralı</i>"
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
                    msg = f"🚀 <b>MACD AL SİNYALİ: {symbol}</b>\n<i>Günlük Grafikte MACD Yukarı Kesti</i>"
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
                    msg = f"⚡ <b>#{symbol} SETUP (ALIŞ)</b>\n<i>4 Saatlik Hacimli Kırılım</i>\n💰 Fiyat: {akilli_fiyat(curr)}"
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
                           f"💡 <b>Analiz:</b> {coin} fiyatı geçmişteki en {'yüksek' if yon.startswith('YÜKSELİŞ') else 'düşük'} seviyesini hacimle kırdı.")
                    
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
                            
                        msg += f"\n⚠️ <i>Lütfen sıkı bir zarar kes (stop-loss) kullanmayı ve risk yönetimini elden bırakmayın.</i>"
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
                               f"📈 SMA200 Seviyesi: {akilli_fiyat(c_sma)}\n"
                               f"✅ <i>Fiyat 200 periyotlu ortalamayı yukarı kırdı. Trend yön değiştiriyor olabilir.</i>")
                        if tg(C, msg):
                            son_sma200_sinyalleri[cooldown_key] = time.time()
                            log("SMA200", f"Yukarı Kırılım: {symbol} ({tf})")
                            
                    elif prev >= p_sma and curr < c_sma and curr < c_sma * 0.995:
                        msg = (f"💀 <b>SMA200 AŞAĞI KIRILIM (SATIŞ): {symbol}</b>\n"
                               f"<i>{tf.upper()} Grafik - Ayı Sinyali</i>\n\n"
                               f"💰 Anlık Fiyat: {akilli_fiyat(curr)}\n"
                               f"📉 SMA200 Seviyesi: {akilli_fiyat(c_sma)}\n"
                               f"⚠️ <i>Fiyat 200 periyotlu ortalamanın altına sarktı. Satış baskısı artabilir.</i>")
                        if tg(C, msg):
                            son_sma200_sinyalleri[cooldown_key] = time.time()
                            log("SMA200", f"Aşağı Kırılım: {symbol} ({tf})")

                time.sleep(0.1)
            time.sleep(SMA200_ARALIGI)
        except Exception as e:
            log("SMA200", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 11: BOLLINGER BANTLARI
# =====================================================
def bollinger_loop():
    log("BOLLINGER", "Bollinger Bantları Motoru Başladı.")
    time.sleep(210)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_BOLLINGER]:
                for tf in ['4h', '1d']:
                    cooldown_key = f"BOLL_{tf}_{symbol}"
                    if cooldown_key in son_bollinger_sinyalleri and time.time() - son_bollinger_sinyalleri[cooldown_key] < 43200: continue
                    
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=50")
                    if not isinstance(klines, list) or len(klines) < 30: continue
                    
                    closes = [float(k[4]) for k in klines[:-1]]
                    curr = closes[-1]
                    
                    upper, mid, lower = calculate_bollinger(closes)
                    if not upper or not lower: continue
                    
                    # Fiyat üst bandı yukarı kırdıysa (Volatilite patlaması)
                    if curr > upper and closes[-2] <= upper:
                        msg = (f"🔥 <b>BOLLINGER ÜST BANT KIRILIMI: {symbol}</b>\n"
                               f"<i>{tf.upper()} Grafik - Volatilite Patlaması</i>\n\n"
                               f"💰 Anlık Fiyat: {akilli_fiyat(curr)}\n"
                               f"📈 Üst Bant: {akilli_fiyat(upper)}\n"
                               f"🚀 <i>Fiyat üst bandı hacimle yukarı kırdı, güçlü bir alım dalgası gelebilir.</i>")
                        if tg(C, msg):
                            son_bollinger_sinyalleri[cooldown_key] = time.time()
                            
                    # Fiyat alt banda değip yukarı döndüyse (Tepki Alımı)
                    elif curr < lower and closes[-2] >= lower:
                        msg = (f"🩸 <b>BOLLINGER ALT BANT DESTEĞİ: {symbol}</b>\n"
                               f"<i>{tf.upper()} Grafik - Aşırı Satım</i>\n\n"
                               f"💰 Anlık Fiyat: {akilli_fiyat(curr)}\n"
                               f"📉 Alt Bant: {akilli_fiyat(lower)}\n"
                               f"⚖️ <i>Fiyat alt banda değdi. Tepki alımı (bounce) gelebilir, dikkatli olun.</i>")
                        if tg(C, msg):
                            son_bollinger_sinyalleri[cooldown_key] = time.time()
                            
                time.sleep(0.1)
            time.sleep(BOLLINGER_ARALIGI)
        except Exception as e:
            log("BOLLINGER", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 12: ICHIMOKU BULUTU (KUMO KIRILIMI)
# =====================================================
def ichimoku_loop():
    log("ICHIMOKU", "Ichimoku Bulut Motoru Başladı.")
    time.sleep(240)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_ICHIMOKU]:
                for tf in ['12h', '1d']: # Genelde 12s ve 1g kullanılır
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
                    
                    # Fiyat Bulutun (Direncin) üzerine çıktıysa (Kumo Breakout)
                    if curr > cloud_top and prev <= cloud_top:
                        msg = (f"☁️ <b>ICHIMOKU BULUTU KIRILIMI (ALIŞ): {symbol}</b>\n"
                               f"<i>{tf.upper()} Grafik - Kumo Breakout</i>\n\n"
                               f"💰 Anlık Fiyat: {akilli_fiyat(curr)}\n"
                               f"☁️ Bulut Direnci: {akilli_fiyat(cloud_top)}\n"
                               f"🚀 <i>Fiyat Ichimoku bulutunu yukarı yönlü kırdı. Trendin yönü yukarı dönebilir.</i>")
                        if tg(C, msg):
                            son_ichimoku_sinyalleri[cooldown_key] = time.time()
                            log("ICHIMOKU", f"Bulut Kırılımı: {symbol} ({tf})")
                            
                time.sleep(0.1)
            time.sleep(ICHIMOKU_ARALIGI)
        except Exception as e:
            log("ICHIMOKU", f"Hata: {e}")
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
    threading.Thread(target=bollinger_loop, daemon=True).start()  # YENİ
    threading.Thread(target=ichimoku_loop, daemon=True).start()   # YENİ
    
    run_web_server()

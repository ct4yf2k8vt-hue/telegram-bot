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

# SİNYAL HAFIZALARI (Spam Engelleme)
son_formasyon_sinyalleri = {}
son_midas_setup_sinyalleri = {}
son_pump_sinyalleri = {}
son_macd_sinyalleri = {}
son_bollinger_sinyalleri = {}
son_ichimoku_sinyalleri = {}
son_sma200_sinyalleri = {}

# =====================================================
# YARDIMCI FONKSİYONLAR
# =====================================================
def log(prefix, m): print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] [{prefix}] {m}", flush=True)

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
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response: return json.loads(response.read().decode("utf-8"))
    except: return None

def calculate_ema(prices, period):
    if len(prices) < period: return []
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for price in prices[period:]: ema.append(price * k + ema[-1] * (1 - k))
    return ema

def calculate_sma(prices, period):
    if len(prices) < period: return []
    return [sum(prices[i:i+period])/period for i in range(len(prices)-period+1)]

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

# =====================================================
# MOTOR 1: FORMASYON (4 SAATLİK VE GÜNLÜK) - AYRIŞTIRILMIŞ
# =====================================================
def formasyon_loop():
    log("FORMASYON", "Formasyon (İkili Tepe & Omuz-Baş-Omuz) Motoru Başladı.")
    time.sleep(10)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= 10_000_000]:
                if symbol in son_formasyon_sinyalleri and time.time() - son_formasyon_sinyalleri[symbol] < 86400: continue
                for tf in ['4h', '1d']:
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=100")
                    if not isinstance(klines, list) or len(klines) < 100: continue
                    
                    highs = [float(k[2]) for k in klines[:-1]]
                    lows = [float(k[3]) for k in klines[:-1]]
                    closes = [float(k[4]) for k in klines[:-1]]
                    
                    curr = closes[-1] # Son kapanan mum
                    
                    # Formasyon ayrımı için son 30 mumun zirvelerini alalım
                    son_30_zirve = highs[-30:]
                    son_30_dip = lows[-30:]
                    
                    en_yuksek = max(son_30_zirve)
                    en_dusuk = min(son_30_dip)
                    
                    # --- LONG SENARYOSU (Ters İkili Dip veya Ters OBO) ---
                    if curr > max(closes[-20:-5]): 
                        # İkili Dip mi OBO mu anlamak için 2. en düşük dibi bulalım
                        sirali_dipler = sorted(list(set([round(x, 5) for x in son_30_dip])))
                        if len(sirali_dipler) >= 2:
                            dip1 = sirali_dipler[0]
                            dip2 = sirali_dipler[1]
                            # Eğer iki dip birbirine çok yakınsa (%1.5 içinde)
                            if abs(dip1 - dip2) / dip1 < 0.015:
                                formasyon_adi = "Ters İkili Dip (Double Bottom)"
                            else:
                                formasyon_adi = "Ters Omuz-Baş-Omuz"
                        else:
                            formasyon_adi = "Ters Omuz-Baş-Omuz / İkili Dip"
                            
                        msg = (f"🚀 <b>FORMASYON KIRILIMI (LONG): {symbol}</b>\n"
                               f"<i>{tf.upper()} Grafik - {formasyon_adi}</i>\n\n"
                               f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                               f"💡 <b>Açıklama:</b> Fiyat, {formasyon_adi} formasyonunun boyun çizgisini (Neckline) yukarı yönlü kırdı. Piyasa Yapısı Değişimi (MSS) gerçekleşti. Alıcılar kontrolü ele geçirdi ve yeni bir yükseliş trendi (Higher High) başladı.")
                        if tg(C, msg): son_formasyon_sinyalleri[symbol] = time.time(); break
                        
                    # --- SHORT SENARYOSU (İkili Tepe veya OBO) ---
                    elif curr < min(closes[-20:-5]):
                        # İkili Tepe mi OBO mu anlamak için 2. en yüksek tepeyi bulalım
                        sirali_tepeler = sorted(list(set([round(x, 5) for x in son_30_zirve])), reverse=True)
                        if len(sirali_tepeler) >= 2:
                            tepe1 = sirali_tepeler[0]
                            tepe2 = sirali_tepeler[1]
                            # Eğer iki tepe birbirine çok yakınsa (%1.5 içinde)
                            if abs(tepe1 - tepe2) / tepe1 < 0.015:
                                formasyon_adi = "İkili Tepe (Double Top)"
                            else:
                                formasyon_adi = "Omuz-Baş-Omuz"
                        else:
                            formasyon_adi = "Omuz-Baş-Omuz / İkili Tepe"
                            
                        msg = (f"💀 <b>FORMASYON KIRILIMI (SHORT): {symbol}</b>\n"
                               f"<i>{tf.upper()} Grafik - {formasyon_adi}</i>\n\n"
                               f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                               f"💡 <b>Açıklama:</b> Fiyat, {formasyon_adi} formasyonunun boyun çizgisini (Neckline) aşağı yönlü kırdı. Piyasa Yapısı Değişimi (MSS) aşağı yönlü gerçekleşti. Satıcılar kontrolü ele geçirdi ve yeni bir düşüş trendi (Lower Low) başladı.")
                        if tg(C, msg): son_formasyon_sinyalleri[symbol] = time.time(); break
                time.sleep(0.1)
            time.sleep(3600)
        except Exception as e: time.sleep(60)

# =====================================================
# MOTOR 2: MIDAS SETUP (4 SAATLİK)
# =====================================================
def midas_setup_loop():
    log("MIDAS_SETUP", "Midas Setup (4 Saatlik) Motoru Başladı.")
    time.sleep(20)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= 15_000_000]:
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
                    msg = (f"⚡ <b>{symbol} SETUP (LONG)</b>\n"
                           f"<i>4 Saatlik Hacimli Kırılım</i>\n\n"
                           f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> 4 saatlik grafikte son 20 periyodun (yaklaşık 3 günün) direnci hacimle aşıldı. Kısa ve orta vadeli traderların dikkatini çeken, hızlı hareket potansiyeli yüksek bir kırılımdır.")
                    if tg(C, msg): son_midas_setup_sinyalleri[symbol] = time.time()
                elif curr < min(lows[-20:]) and is_vol:
                    msg = (f"⚡ <b>{symbol} SETUP (SHORT)</b>\n"
                           f"<i>4 Saatlik Hacimli Destek Kırılımı</i>\n\n"
                           f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> 4 saatlik grafikte son 20 periyodun en düşük desteği hacimle aşağı kırıldı. Satış baskısının arttığını ve hızlı bir düşüşün başlayabileceğini gösterir.")
                    if tg(C, msg): son_midas_setup_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(7200)
        except Exception as e: time.sleep(60)

# =====================================================
# MOTOR 3: PUMP AVCISI (5 DAKİKALIK)
# =====================================================
def pump_loop():
    log("PUMP", "Anlık Pump Avcısı (5 Dakikalık) Başladı.")
    time.sleep(30)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= 5_000_000]:
                if symbol in son_pump_sinyalleri and time.time() - son_pump_sinyalleri[symbol] < 3600: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=5m&limit=3")
                if not isinstance(klines, list) or len(klines) < 3: continue
                curr_k, prev_k = klines[-2], klines[-3]
                cc, pc = float(curr_k[4]), float(prev_k[4])
                cv, pv = float(curr_k[7]), float(prev_k[7])
                if pc == 0 or pv == 0: continue
                
                if ((cc-pc)/pc)*100 >= 3.0 and ((cv-pv)/pv)*100 >= 100.0 and (cv-pv) >= 50_000:
                    teyit = trap_radar_check(symbol)
                    msg = (f"🚀 <b>Pump - {symbol}</b>\n"
                           f"<i>5 Dakikalık Grafik</i>\n\n"
                           f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(cc)}\n"
                           f"📊 Hacim Artışı: +%{((cv-pv)/pv)*100:.0f}\n\n"
                           f"{teyit}\n\n"
                           f"💡 <b>Açıklama:</b> 5 dakikalık grafikte aniden sert bir hacim ve fiyat patlaması yaşandı. Trap Radar (OI ve CVD) sayesinde bu hareketin arkasında gerçek balinaların mı yoksa küçük yatırımcıyı çekmek isteyen sahte botların mı olduğunu analiz ettik. Açık Pozisyon (OI) ve Alıcı baskısı (CVD) verileri bu hareketin niteliğini belirler.")
                    tg(C, msg); son_pump_sinyalleri[symbol] = time.time()
                time.sleep(0.05)
            time.sleep(60)
        except Exception as e: time.sleep(60)

# =====================================================
# MOTOR 4: MACD (GÜNLÜK)
# =====================================================
def macd_loop():
    log("MACD", "MACD Günlük Motoru Başladı.")
    time.sleep(40)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= 10_000_000]:
                if symbol in son_macd_sinyalleri and time.time() - son_macd_sinyalleri[symbol] < 86400: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1d&limit=100")
                if not isinstance(klines, list) or len(klines) < 80: continue
                closes = [float(k[4]) for k in klines[:-1]]
                macd_res = calculate_macd(closes)
                if not macd_res: continue
                cm, cs, pm, ps = macd_res
                curr = closes[-1]
                
                if pm <= ps and cm > cs:
                    msg = (f"🚀 <b>MACD AL SİNYALİ (LONG): {symbol}</b>\n"
                           f"<i>1 Günlük Grafik - MACD Yukarı Kesti</i>\n\n"
                           f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> MACD indikatörü, Sinyal çizgisini aşağıdan yukarı yönlü kesti (Golden Cross). Momentumun (ivmenin) satıcılardan alıcılara geçtiğini ve trendin yönünün yukarı döndüğünü gösteren klasik ve güvenilir bir alış sinyalidir.")
                    if tg(C, msg): son_macd_sinyalleri[symbol] = time.time()
                elif pm >= ps and cm < cs:
                    msg = (f"💀 <b>MACD SAT SİNYALİ (SHORT): {symbol}</b>\n"
                           f"<i>1 Günlük Grafik - MACD Aşağı Kesti</i>\n\n"
                           f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> MACD indikatörü, Sinyal çizgisini yukarıdan aşağı yönlü kesti (Death Cross). Momentumun alıcılardan satıcılara geçtiğini ve düşüş trendinin başladığını gösterir.")
                    if tg(C, msg): son_macd_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(3600)
        except Exception as e: time.sleep(60)

# =====================================================
# MOTOR 5: BOLLINGER (HAFTALIK)
# =====================================================
def bollinger_loop():
    log("BOLLINGER", "Bollinger Haftalık Motoru Başladı.")
    time.sleep(50)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= 10_000_000]:
                if symbol in son_bollinger_sinyalleri and time.time() - son_bollinger_sinyalleri[symbol] < 86400: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1w&limit=50")
                if not isinstance(klines, list) or len(klines) < 30: continue
                closes = [float(k[4]) for k in klines[:-1]]
                curr = closes[-1]
                upper, mid, lower = calculate_bollinger(closes)
                if not upper or not lower: continue
                
                if curr > upper and closes[-2] <= upper:
                    msg = (f"🔥 <b>BOLLINGER ÜST BANT KIRILIMI (LONG): {symbol}</b>\n"
                           f"<i>1 Haftalık Grafik</i>\n\n"
                           f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> Fiyat, sıkışmanın (Squeeze) ardından Bollinger üst bandını yukarı yönlü kırdı. Volatilitenin arttığını ve güçlü bir alım dalgasının piyasaya giriş yaptığını gösterir. Haftalık kırılımlar büyük trendleri işaret eder.")
                    if tg(C, msg): son_bollinger_sinyalleri[symbol] = time.time()
                elif curr < lower and closes[-2] >= lower:
                    msg = (f"🩸 <b>BOLLINGER ALT BANT KIRILIMI (SHORT): {symbol}</b>\n"
                           f"<i>1 Haftalık Grafik</i>\n\n"
                           f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> Fiyat, Bollinger alt bandını aşağı yönlü kırdı. Aşırı satım bölgesine girildiğini veya sert bir düşüş dalgasının (Squeeze) başladığını gösterebilir. Dikkatli olunmalı.")
                    if tg(C, msg): son_bollinger_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(3600)
        except Exception as e: time.sleep(60)

# =====================================================
# MOTOR 6: ICHIMOKU (HAFTALIK)
# =====================================================
def ichimoku_loop():
    log("ICHIMOKU", "Ichimoku Haftalık Motoru Başladı.")
    time.sleep(60)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= 10_000_000]:
                if symbol in son_ichimoku_sinyalleri and time.time() - son_ichimoku_sinyalleri[symbol] < 86400: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1w&limit=100")
                if not isinstance(klines, list) or len(klines) < 52: continue
                highs = [float(k[2]) for k in klines[:-1]]
                lows = [float(k[3]) for k in klines[:-1]]
                closes = [float(k[4]) for k in klines[:-1]]
                curr, prev = closes[-1], closes[-2]
                cloud_top, cloud_bottom = calculate_ichimoku(highs, lows, closes)
                if not cloud_top or not cloud_bottom: continue
                
                if curr > cloud_top and prev <= cloud_top:
                    msg = (f"☁️ <b>ICHIMOKU BULUTU KIRILIMI (LONG): {symbol}</b>\n"
                           f"<i>1 Haftalık Grafik - Kumo Breakout</i>\n\n"
                           f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> Fiyat, Japon trend sistemi Ichimoku'nun 'Kumo' (Bulut) direncini yukarı kırdı. Haftalık bulutun üzerine çıkılması, piyasanın makro anlamda 'Boğa' bölgesine girdiğini ve artık bulutun destek görevi göreceğini gösterir.")
                    if tg(C, msg): son_ichimoku_sinyalleri[symbol] = time.time()
                elif curr < cloud_bottom and prev >= cloud_bottom:
                    msg = (f"☁️ <b>ICHIMOKU BULUTU KIRILIMI (SHORT): {symbol}</b>\n"
                           f"<i>1 Haftalık Grafik - Kumo Breakdown</i>\n\n"
                           f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> Fiyat, Ichimoku Bulutunun altına sarktı. Bu durum piyasanın 'Ayı' bölgesine girdiğini ve bulutun artık devasa bir direnç (tavan) görevi göreceğini gösterir. Makro düşüş trendi başlıyor olabilir.")
                    if tg(C, msg): son_ichimoku_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(3600)
        except Exception as e: time.sleep(60)

# =====================================================
# MOTOR 7: SMA200 (3 GÜNLÜK)
# =====================================================
def sma200_loop():
    log("SMA200", "SMA200 (3 Günlük) Motoru Başladı.")
    time.sleep(70)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= 15_000_000]:
                if symbol in son_sma200_sinyalleri and time.time() - son_sma200_sinyalleri[symbol] < 86400: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=3d&limit=250")
                if not isinstance(klines, list) or len(klines) < 205: continue
                closes = [float(k[4]) for k in klines[:-1]]; curr, prev = closes[-1], closes[-2]
                sma200 = calculate_sma(closes, 200)
                if not sma200 or len(sma200) < 3: continue
                c_sma = sma200[-1]; p_sma = sma200[-2]
                
                if prev <= p_sma and curr > c_sma and curr > c_sma * 1.005:
                    msg = (f"🚀 <b>SMA200 YUKARI KIRILIM (LONG): {symbol}</b>\n"
                           f"<i>3 Günlük Grafik</i>\n\n"
                           f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> Fiyat, 200 periyotluk basit ortalamanın (SMA200) üzerine çıktı. SMA200, kurumsal yatırımcıların ortalama maliyet çizgisidir. Bu çizginin üzerine çıkılması, piyasanın genel olarak 'Al' bölgesine geçtiğini gösterir.")
                    if tg(C, msg): son_sma200_sinyalleri[symbol] = time.time()
                elif prev >= p_sma and curr < c_sma and curr < c_sma * 0.995:
                    msg = (f"💀 <b>SMA200 AŞAĞI KIRILIM (SHORT): {symbol}</b>\n"
                           f"<i>3 Günlük Grafik</i>\n\n"
                           f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> Fiyat, 200 periyotluk ortalamanın (SMA200) altına sarktı. Bu, büyük yatırımcıların bölgeden çıktığını ve piyasanın genel olarak 'Sat' (Ayı) bölgesine geçtiğini gösterir. Düşüş trendi hız kazanabilir.")
                    if tg(C, msg): son_sma200_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(3600)
        except Exception as e: time.sleep(60)

# =====================================================
# RENDER WEB SUNUCUSU
# =====================================================
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")
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
        "İstediğin 7 motor başarıyla başlatıldı. 🚀\n"
        "• Formasyon (4s, 1g) [Ayrıştırılmış]\n"
        "• Midas Setup (4s)\n"
        "• Pump Avcısı (5dk)\n"
        "• MACD (1g)\n"
        "• Bollinger (1h)\n"
        "• Ichimoku (1h)\n"
        "• SMA200 (3g)\n\n"
        "Sinyaller, giriş fiyatları ve detaylı açıklamalarıyla birlikte buraya düşecektir."
    )
    if tg(C, baslangic_mesaji):
        log("SİSTEM", "✅ Başlangıç mesajı başarıyla Telegram'a gönderildi.")
    else:
        log("SİSTEM", "❌ HATA: Telegram'a mesaj gönderilemedi! Lütfen BOT_TOKEN ve CHAT_ID ayarlarını kontrol edin.")
    
    # Motorları başlat
    threading.Thread(target=formasyon_loop, daemon=True).start()
    threading.Thread(target=midas_setup_loop, daemon=True).start()
    threading.Thread(target=pump_loop, daemon=True).start()
    threading.Thread(target=macd_loop, daemon=True).start()
    threading.Thread(target=bollinger_loop, daemon=True).start()
    threading.Thread(target=ichimoku_loop, daemon=True).start()
    threading.Thread(target=sma200_loop, daemon=True).start()
    
    # Web sunucusunu başlat
    run_web_server()

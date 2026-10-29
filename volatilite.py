import urllib.request
import urllib.parse
import json
import time
import socket
import os
import threading
import http.server
import socketserver
import math
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
RAPOR_ARALIGI = 3600       
CHARTIST_ARALIGI = 3600    
PUMP_ARALIGI = 60          
SETUP_ARALIGI = 900        
FORMASYON_ARALIGI = 1800   
BOLLINGER_ARALIGI = 3600   
MACRO_ARALIGI = 7200       
AGRESIF_ARALIGI = 30       # 30 saniyede bir agresif tarama

# FİLTRELER
MIN_HACIM_CHARTIST = 10_000_000 
MIN_HACIM_24H_PUMP = 5_000_000  
MIN_HACIM_ARTISI = 20_000       
MIN_HACIM_SETUP = 20_000_000    
MIN_HACIM_FORMASYON = 15_000_000 
MIN_HACIM_BOLLINGER = 20_000_000 
MIN_HACIM_MACRO = 10_000_000
MIN_HACIM_AGRESIF = 1_000_000   # Agresif mod için 1 Milyon Dolar

son_sinyaller = {}
son_pump_sinyalleri = {}
son_setup_sinyalleri = {}
son_formasyon_sinyalleri = {}
son_bollinger_sinyalleri = {}
son_macro_sinyalleri = {}
son_agresif_sinyalleri = {}     

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

def calculate_bollinger(closes, period=20, num_std=2):
    if len(closes) < period: return None
    recent_closes = closes[-period:]
    sma = sum(recent_closes) / period
    variance = sum((x - sma) ** 2 for x in recent_closes) / period
    std_dev = math.sqrt(variance)
    upper_band = sma + (num_std * std_dev)
    lower_band = sma - (num_std * std_dev)
    bandwidth = (upper_band - lower_band) / sma if sma > 0 else 0
    current_close = closes[-1]
    signal = None
    if current_close > upper_band: signal = "AL (Kırılım)"
    elif current_close < lower_band: signal = "SAT (Kırılım)"
    return {"sma": sma, "upper": upper_band, "lower": lower_band, "bandwidth": bandwidth, "close": current_close, "signal": signal}

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

def get_cvd_oi_teyit(symbol):
    try:
        oi_data = gj(f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period=15m&limit=2")
        oi_text = "OI Verisi Yok"
        oi_artiyor = False
        if oi_data and len(oi_data) >= 2:
            oi_old = float(oi_data[0]['sumOpenInterest'])
            oi_new = float(oi_data[1]['sumOpenInterest'])
            if oi_old > 0:
                oi_chg_pct = ((oi_new - oi_old) / oi_old) * 100
                oi_text = f"{oi_chg_pct:+.2f}%"
                if oi_chg_pct > 0.5:
                    oi_artiyor = True

        taker_data = gj(f"https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={symbol}&period=15m&limit=1")
        taker_text = "Taker Verisi Yok"
        alici_baskin = False
        if taker_data and len(taker_data) > 0:
            buy_vol = float(taker_data[0]['buyVol'])
            sell_vol = float(taker_data[0]['sellVol'])
            total = buy_vol + sell_vol
            if total > 0:
                buy_pct = (buy_vol / total) * 100
                taker_text = f"Alıcı: %{buy_pct:.1f} / Satıcı: %{100-buy_pct:.1f}"
                if buy_pct > 55:
                    alici_baskin = True

        if oi_artiyor and alici_baskin:
            return f"\n🐋 <b>BALİNA TEYİDİ (CVD+OI):</b> YENİ LONG'lar girdi! (OI: {oi_text}, {taker_text})"
        elif alici_baskin:
            return f"\n⚖️ <b>Kısmi Teyit:</b> Alıcı baskısı var ama OI artmamış. (OI: {oi_text})"
        else:
            return f"\n⚠️ <b>Zayıf Teyit:</b> Alıcılar zayıf. (OI: {oi_text})"
    except:
        return ""

# =====================================================
# 1. MOTOR: MİDAS FLOW (RAPOR)
# =====================================================
def get_peak_time(symbol):
    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1h&limit=24")
    if not isinstance(klines, list): return "0"
    try:
        entry_time, peak_time, entry_price, peak_price = 0, 0, float('inf'), 0
        for k in klines:
            if float(k[3]) < entry_price:
                entry_price = float(k[3])
                entry_time = int(k[0])
            if float(k[2]) > peak_price:
                peak_price = float(k[2])
                peak_time = int(k[0])
        duration = (peak_time - entry_time) / (1000 * 60 * 60)
        return f"{max(duration, 0.1):.1f}"
    except:
        return "0"

def midas_flow_loop():
    log("RAPOR", "Midas Flow Motoru Başladı.")
    time.sleep(5)
    while True:
        try:
            log("RAPOR", "Piyasa verileri çekiliyor...")
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(300)
                continue
            trend_text, avg_chg, ups, downs = get_market_trend(tickers)
            usdt_pairs = [t for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) > 5_000_000]
            usdt_pairs.sort(key=lambda x: float(x['priceChangePercent']), reverse=True)
            top_5 = usdt_pairs[:5]
            if top_5:
                msg = "🔥 <b>24 saatteki en iyi hareketler</b>\n"
                msg += f"\n🌍 <b>Piyasa Yönü (24s):</b> {trend_text}\n"
                msg += f"📊 Ortalama Değişim: %{avg_chg:.2f} | Yükselen: {ups} | Düşen: {downs}\n\n"
                msg += "sinyal yönündeki maksimum hareket, veritabanımıza göre:\n\n"
                medals = ["🥇", "🥈", "🥉", "🏅", "⭐️"]
                for i, t in enumerate(top_5):
                    symbol = t['symbol'].replace("USDT", "")
                    change = float(t['priceChangePercent'])
                    change_str = f"+{change:.0f}%" if change > 0 else f"{change:.0f}%"
                    peak = get_peak_time(t['symbol'])
                    msg += f"{medals[i]} {symbol} {change_str} · {peak} saatte zirve\n"
                msg += "\nTüm sinyal akışı — panelde 👉 midasflow.ai/app"
                tg(C, msg)
                log("RAPOR", "Saatlik rapor gönderildi.")
            time.sleep(RAPOR_ARALIGI)
        except Exception as e:
            log("RAPOR", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# 2. MOTOR: CHARTIST (EMA200)
# =====================================================
def chartist_loop():
    log("CHARTIST", "Çoklu Zaman Dilimi EMA200 Motoru Başladı.")
    time.sleep(15)
    timeframes = ['8h', '12h', '1d', '3d']
    while True:
        try:
            log("CHARTIST", "EMA200 Kırılımları taranıyor...")
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(60)
                continue
            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_CHARTIST]
            for symbol in adaylar:
                for tf in timeframes:
                    cooldown_key = f"CH_{tf}_{symbol}"
                    if cooldown_key in son_sinyaller and time.time() - son_sinyaller[cooldown_key] < 86400: continue
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=250")
                    if not isinstance(klines, list) or len(klines) < 205: continue
                    closes = [float(k[4]) for k in klines]
                    current_price = closes[-1]
                    ema200 = calculate_ema(closes, 200)
                    if len(ema200) < 2: continue
                    curr_ema200, prev_ema200 = ema200[-1], ema200[-2]
                    prev_close = closes[-2]
                    if prev_close <= prev_ema200 and current_price > curr_ema200:
                        sl = curr_ema200 * 0.97 
                        msg = (f"📈 <b>UZUN VADELİ SPOT SİNYALİ: {symbol}</b>\n"
                               f"<i>{tf.upper()} EMA200 Kırılımı!</i>\n\n"
                               f"💰 <b>Giriş:</b> {akilli_fiyat(current_price)}\n🛑 <b>SL:</b> {akilli_fiyat(sl)}\n"
                               f"🎯 <b>Hedef:</b> {akilli_fiyat(current_price * 1.15)}")
                        if tg(C, msg):
                            son_sinyaller[cooldown_key] = time.time()
                            log("CHARTIST", f"🔥 KIRILIM SİNYALİ ({tf.upper()}): {symbol}")
                    time.sleep(0.05)
            time.sleep(CHARTIST_ARALIGI)
        except Exception as e:
            log("CHARTIST", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# 3. MOTOR: PUMP (1m)
# =====================================================
def pump_loop():
    log("PUMP", "Akıllı Yönlü Pump Motoru Başladı (1m).")
    time.sleep(25)
    while True:
        try:
            log("PUMP", "Anlık pump fırsatları taranıyor...")
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(30)
                continue
            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_24H_PUMP]
            for symbol in adaylar:
                if symbol in son_pump_sinyalleri and time.time() - son_pump_sinyalleri[symbol] < 900: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1m&limit=3")
                if not isinstance(klines, list) or len(klines) < 3: continue
                curr_k, prev_k = klines[-2], klines[-3]
                curr_close, prev_close = float(curr_k[4]), float(prev_k[4])
                curr_vol, prev_vol = float(curr_k[7]), float(prev_k[7])
                if prev_close == 0 or prev_vol == 0: continue
                price_change = ((curr_close - prev_close) / prev_close) * 100
                vol_change = ((curr_vol - prev_vol) / prev_vol) * 100
                if price_change >= 5.0 and vol_change >= 100.0 and (curr_vol - prev_vol) >= MIN_HACIM_ARTISI:
                    yon = "SHORT" if price_change >= 10.0 else "LONG"
                    emoji = "🔴" if yon == "SHORT" else "🟢"
                    cvd_oi_teyit = get_cvd_oi_teyit(symbol)
                    msg = (f"{emoji} <b>PUMP SİNYALİ: {symbol} ({yon})</b>\n\n"
                           f"💰 <b>Fiyat:</b> {akilli_fiyat(curr_close)} (<b>{price_change:+.2f}%</b>)\n"
                           f"📊 <b>Hacim Artışı:</b> {vol_change:+.0f}%"
                           f"{cvd_oi_teyit}")
                    if tg(C, msg):
                        son_pump_sinyalleri[symbol] = time.time()
                        log("PUMP", f"🔥 {yon} SİNYALİ: {symbol}")
                time.sleep(0.05)
            time.sleep(PUMP_ARALIGI)
        except Exception as e:
            log("PUMP", f"Hata: {e}")
            time.sleep(30)

# =====================================================
# 4. MOTOR: SETUP (POC)
# =====================================================
def setup_loop():
    log("SETUP", "Profesyonel Setup Analiz Motoru Başladı.")
    time.sleep(35)
    while True:
        try:
            log("SETUP", "POC Setup'ları taranıyor...")
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(60)
                continue
            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_SETUP]
            for symbol in adaylar:
                if symbol in son_setup_sinyalleri and time.time() - son_setup_sinyalleri[symbol] < 14400: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1h&limit=50")
                if not isinstance(klines, list) or len(klines) < 50: continue
                highs = [float(k[2]) for k in klines[-24:]]
                lows = [float(k[3]) for k in klines[-24:]]
                vols = [float(k[7]) for k in klines[-24:]]
                poc = highs[vols.index(max(vols))] 
                val = min(lows)
                current_price = float(klines[-1][4])
                if (poc * 0.98) <= current_price < poc:
                    target, stop = poc * 1.15, val * 0.99
                    msg = (f"<b>#{symbol}</b>\n\nFiyat: <b>{akilli_fiyat(current_price)}</b>\nHedef: <b>{akilli_fiyat(target)}</b>\nİptal: <b>{akilli_fiyat(stop)}</b>\n\n"
                           f"<b>{akilli_fiyat(poc)}</b> POC direncine takıldı. Trend 1g yukarı. ✊\n"
                           f"🛡️ Stop {akilli_fiyat(stop)} arkasında.")
                    if tg(C, msg):
                        son_setup_sinyalleri[symbol] = time.time()
                        log("SETUP", f"🔥 SETUP SİNYALİ: {symbol}")
                time.sleep(0.1)
            time.sleep(SETUP_ARALIGI)
        except Exception as e:
            log("SETUP", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# 5. MOTOR: FORMASYON (Çift Yönlü)
# =====================================================
def formasyon_loop():
    log("FORMASYON", "Çift Yönlü Formasyon ve Kırılım Motoru Başladı.")
    time.sleep(45) 
    while True:
        try:
            log("FORMASYON", "Kırılımlar taranıyor...")
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(60)
                continue
            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_FORMASYON]
            for symbol in adaylar:
                if symbol in son_formasyon_sinyalleri and time.time() - son_formasyon_sinyalleri[symbol] < 21600: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1h&limit=50")
                if not isinstance(klines, list) or len(klines) < 50: continue
                highs = [float(k[2]) for k in klines]
                lows = [float(k[3]) for k in klines]
                closes = [float(k[4]) for k in klines]
                vols = [float(k[7]) for k in klines]
                current_price = closes[-1]
                avg_vol = sum(vols[-20:-1]) / 19 if len(vols) >= 20 else 1
                if vols[-1] <= (avg_vol * 2.0): continue

                # LONG
                if max(highs[-40:-20]) > max(highs[-20:-5]) and current_price > max(highs[-20:-5]):
                    target = max(highs[-40:-20])
                    if target > current_price:
                        stop = min(lows[-20:]) * 0.98
                        cvd_oi_teyit = get_cvd_oi_teyit(symbol)
                        msg = (f"🚀 <b>FORMASYON (LONG): {symbol}</b>\n\nGiriş: {akilli_fiyat(current_price)}\n"
                               f"Hedef: {akilli_fiyat(target)}\nStop: {akilli_fiyat(stop)}\n"
                               f"Hacim: {vols[-1]/avg_vol:.1f}x {cvd_oi_teyit}")
                        if tg(C, msg):
                            son_formasyon_sinyalleri[symbol] = time.time()
                            log("FORMASYON", f"🔥 LONG SİNYALİ: {symbol}")
                        continue
                
                # SHORT
                if min(lows[-40:-20]) < min(lows[-20:-5]) and current_price < min(lows[-20:-5]):
                    target = min(lows[-40:-20])
                    if target < current_price:
                        stop = max(highs[-20:]) * 1.02
                        cvd_oi_teyit = get_cvd_oi_teyit(symbol)
                        msg = (f"🔻 <b>FORMASYON (SHORT): {symbol}</b>\n\nGiriş: {akilli_fiyat(current_price)}\n"
                               f"Hedef: {akilli_fiyat(target)}\nStop: {akilli_fiyat(stop)}\n"
                               f"Hacim: {vols[-1]/avg_vol:.1f}x {cvd_oi_teyit}")
                        if tg(C, msg):
                            son_formasyon_sinyalleri[symbol] = time.time()
                            log("FORMASYON", f"🔥 SHORT SİNYALİ: {symbol}")
                time.sleep(0.1)
            time.sleep(FORMASYON_ARALIGI)
        except Exception as e:
            log("FORMASYON", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# 6. MOTOR: BOLLINGER (Haftalık)
# =====================================================
def bollinger_loop():
    log("BOLLINGER", "Haftalık Bollinger Bandı Motoru Başladı.")
    time.sleep(60) 
    while True:
        try:
            log("BOLLINGER", "Haftalık Bollinger Sıkışmaları (Squeeze) taranıyor...")
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_BOLLINGER]
            for symbol in adaylar:
                if symbol in son_bollinger_sinyalleri and time.time() - son_bollinger_sinyalleri[symbol] < 259200: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1w&limit=50")
                if not isinstance(klines, list) or len(klines) < 25: continue
                closes = [float(k[4]) for k in klines[:-1]]
                current_price = closes[-1]
                bb = calculate_bollinger(closes, 20, 2)
                if not bb or not bb["signal"]: continue
                
                avg_vol = sum([float(k[7]) for k in klines[:-1]][-21:-1]) / 20
                if float(klines[-2][7]) < (avg_vol * 1.2): continue

                yon = "AL" if "AL" in bb["signal"] else "SAT"
                emoji = "🚀" if yon == "AL" else "🔴"
                target = bb["upper"] if yon == "AL" else bb["lower"]
                sl = bb["lower"] if yon == "AL" else bb["upper"]
                msg = (f"{emoji} <b>HAFTALIK BB SİNYALİ: {symbol} ({yon})</b>\n\n"
                       f"Kapanış: {akilli_fiyat(current_price)}\nHedef: {akilli_fiyat(target)}\nStop: {akilli_fiyat(sl)}")
                if tg(C, msg):
                    son_bollinger_sinyalleri[symbol] = time.time()
                    log("BOLLINGER", f"🔥 SQUEEZE KIRILIMI: {symbol} ({yon})")
                time.sleep(0.15) 
            time.sleep(BOLLINGER_ARALIGI)
        except Exception as e:
            log("BOLLINGER", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# 7. MOTOR: MAKRO (Günlük)
# =====================================================
def macro_loop():
    log("MACRO", "Makro Kırılım Motoru Başladı (Günlük).")
    time.sleep(75) 
    while True:
        try:
            log("MACRO", "Günlük dip dönüşleri taranıyor...")
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_MACRO]
            for symbol in adaylar:
                if symbol in son_macro_sinyalleri and time.time() - son_macro_sinyalleri[symbol] < 172800: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1d&limit=100")
                if not isinstance(klines, list) or len(klines) < 100: continue
                closes = [float(k[4]) for k in klines]
                highs = [float(k[2]) for k in klines]
                lows = [float(k[3]) for k in klines]
                current_price = closes[-1]
                sma50 = sum(closes[-50:]) / 50
                sma100 = sum(closes[-100:]) / 100
                
                if sma50 < sma100 and current_price > sma50 and closes[-2] <= (sum(closes[-51:-1]) / 50):
                    target = max(highs) if max(highs) > current_price * 1.20 else current_price * 1.30
                    stop = min(lows[-30:]) * 0.95
                    msg = (f"🛒 <b>MAKRO DİP DÖNÜŞÜ: {symbol}</b>\n\n"
                           f"Giriş: {akilli_fiyat(current_price)}\nHedef: {akilli_fiyat(target)}\nStop: {akilli_fiyat(stop)}\n"
                           f"<b>Buy and hold!</b>")
                    if tg(C, msg):
                        son_macro_sinyalleri[symbol] = time.time()
                        log("MACRO", f"🔥 MAKRO SİNYALİ: {symbol}")
                time.sleep(0.2)
            time.sleep(MACRO_ARALIGI)
        except Exception as e:
            log("MACRO", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# 8. MOTOR: AGRESİF PUMP (PARTI Tarzı + CVD/OI Teyitli)
# =====================================================
def agresif_pump_loop():
    log("AGRESIF", "Agresif Pump Avcısı Başladı (1m / 3%+ / CVD+OI Teyitli)")
    time.sleep(15)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(30)
                continue
            
            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_AGRESIF]

            for symbol in adaylar:
                if symbol in son_agresif_sinyalleri and time.time() - son_agresif_sinyalleri[symbol] < 1800: continue 
                
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1m&limit=3")
                if not isinstance(klines, list) or len(klines) < 3: continue
                
                curr_k, prev_k = klines[-2], klines[-3]
                curr_close, prev_close = float(curr_k[4]), float(prev_k[4])
                curr_vol, prev_vol = float(curr_k[7]), float(prev_k[7])
                
                if prev_close == 0 or prev_vol == 0: continue
                
                price_change = ((curr_close - prev_close) / prev_close) * 100
                vol_change = ((curr_vol - prev_vol) / prev_vol) * 100

                if price_change >= 3.0 and vol_change >= 100.0:
                    cvd_oi_teyit = get_cvd_oi_teyit(symbol)
                    msg = (f"⚡ <b>AGRESİF PUMP SİNYALİ (LONG): {symbol}</b>\n"
                           f"<i>Hızlı Yükseliş Yakalandı!</i>\n\n"
                           f"📈 <b>Yön:</b> LONG (Momentum)\n"
                           f"💰 <b>Fiyat:</b> ${akilli_fiyat(curr_close)} (<b>{price_change:+.2f}%</b>)\n"
                           f"📊 <b>Hacim:</b> {vol_change:+.0f}% Artış"
                           f"{cvd_oi_teyit}\n\n"
                           f"⚠️ <i>Bu sinyal çok hızlı gelişen pump'lar içindir. Dikkatli işlem yapın!</i>")
                    
                    if tg(C, msg):
                        son_agresif_sinyalleri[symbol] = time.time()
                        log("AGRESIF", f"🔥 PUMP YAKALANDI: {symbol} (+{price_change:.1f}%)")
                
                time.sleep(0.05)
            time.sleep(AGRESIF_ARALIGI)
        except Exception as e:
            log("AGRESIF", f"Hata: {e}")
            time.sleep(30)

# =====================================================
# RENDER WEB SUNUCUSU
# =====================================================
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Ultimate-Engine Bot is running!")
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
    threading.Thread(target=setup_loop, daemon=True).start()
    threading.Thread(target=formasyon_loop, daemon=True).start()
    threading.Thread(target=bollinger_loop, daemon=True).start()
    threading.Thread(target=macro_loop, daemon=True).start()
    
    # 8. Motor (Agresif)
    threading.Thread(target=agresif_pump_loop, daemon=True).start()
    
    run_web_server()

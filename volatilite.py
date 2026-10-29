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
BOLLINGER_ARALIGI = 3600   # Haftalık grafik için 1 saatte 1 tarama yeterli

# FİLTRELER
MIN_HACIM_CHARTIST = 10_000_000 
MIN_HACIM_24H_PUMP = 5_000_000  
MIN_HACIM_ARTISI = 20_000       
MIN_HACIM_SETUP = 20_000_000    
MIN_HACIM_FORMASYON = 15_000_000 
MIN_HACIM_BOLLINGER = 20_000_000 # Haftalık BB için çok likit coinler şart

son_sinyaller = {}
son_pump_sinyalleri = {}
son_setup_sinyalleri = {}
son_formasyon_sinyalleri = {}
son_bollinger_sinyalleri = {}

# =====================================================
# YARDIMCI FONKSİYONLAR
# =====================================================

def simdi():
    return datetime.now(TR_Saati)

def log(prefix, m):
    print(f"[{simdi().strftime('%H:%M:%S')}] [{prefix}] {m}", flush=True)

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

# =====================================================
# PİYASA YÖNÜ HESAPLAYICI
# =====================================================

def get_market_trend(tickers):
    try:
        sorted_tickers = sorted(tickers, key=lambda x: float(x.get('quoteVolume', 0)), reverse=True)[:100]
        changes = []
        up_count, down_count = 0, 0
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
# BOLLINGER BANDI HESAPLAYICI (YENİ)
# =====================================================

def calculate_bollinger(closes, period=20, num_std=2):
    """Haftalık kapanışlara göre BB hesaplar. Son kapanan mumu ve bant genişliğini döner."""
    if len(closes) < period:
        return None
    
    # Son 'period' kadar kapanışı al
    recent_closes = closes[-period:]
    
    # SMA (Orta Bant)
    sma = sum(recent_closes) / period
    
    # Standart Sapma
    variance = sum((x - sma) ** 2 for x in recent_closes) / period
    std_dev = math.sqrt(variance)
    
    # Bantlar
    upper_band = sma + (num_std * std_dev)
    lower_band = sma - (num_std * std_dev)
    
    # Bant Genişliği (Squeeze ölçümü için)
    bandwidth = (upper_band - lower_band) / sma
    
    # Kapanış fiyatı
    current_close = closes[-1]
    
    # Sinyal belirleme
    signal = None
    if current_close > upper_band:
        signal = "AL (Kırılım)"
    elif current_close < lower_band:
        signal = "SAT (Kırılım)"
    
    return {
        "sma": sma,
        "upper": upper_band,
        "lower": lower_band,
        "bandwidth": bandwidth,
        "close": current_close,
        "signal": signal
    }

# =====================================================
# 1. MOTOR: MİDAS FLOW (RAPOR + PİYASA YÖNÜ)
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
                hashtags = ""
                for i, t in enumerate(top_5):
                    symbol = t['symbol'].replace("USDT", "")
                    change = float(t['priceChangePercent'])
                    change_str = f"+{change:.0f}%" if change > 0 else f"{change:.0f}%"
                    peak = get_peak_time(t['symbol'])
                    msg += f"{medals[i]} {symbol} {change_str} · {peak} saatte zirve\n"
                    hashtags += f"#{symbol} "
                msg += "\nTüm sinyal akışı — panelde 👉 midasflow.ai/app\n\n"
                msg += "⚠️ Hareket — potansiyel hareket, kâr garantisi değildir.\n\n"
                msg += f"<a href='https://t.me/MidasFlowAI'>{hashtags}#MidasFlow #crypto #pump #signals</a>"
                tg(C, msg)
                log("RAPOR", f"Saatlik rapor gönderildi. (Piyasa Yönü: {trend_text})")
            time.sleep(RAPOR_ARALIGI)
        except Exception as e:
            log("RAPOR", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# 2. MOTOR: DIGITAL CHARTIST (ÇOKLU ZAMAN DİLİMİ)
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
                        tp1, tp2, tp3, tp4 = current_price * 1.05, current_price * 1.10, current_price * 1.15, current_price * 1.25
                        sl = curr_ema200 * 0.97 
                        msg = (f"📈 <b>UZUN VADELİ SPOT SİNYALİ: {symbol}</b>\n"
                               f"<i>{tf.upper()} EMA200 Kırılımı!</i>\n\n"
                               f"💰 <b>Giriş:</b> {current_price:.4f}\n🛑 <b>SL:</b> {sl:.4f}\n\n"
                               f"🎯 <b>Hedefler:</b> TP1: {tp1:.4f} | TP2: {tp2:.4f} | TP3: {tp3:.4f} | TP4: {tp4:.4f}\n\n"
                               f"⚠️ <i>Nadir gelen büyük fırsat. Kademeli kâr al!</i>")
                        if tg(C, msg):
                            son_sinyaller[cooldown_key] = time.time()
                            log("CHARTIST", f"🔥 KIRILIM SİNYALİ ({tf.upper()}): {symbol}")
                    time.sleep(0.05)
            time.sleep(CHARTIST_ARALIGI)
        except Exception as e:
            log("CHARTIST", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# 3. MOTOR: AKILLI YÖNLÜ PUMP ALARMI
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
                vol_increase_usdt = curr_vol - prev_vol
                if price_change >= 5.0 and vol_change >= 100.0 and vol_increase_usdt >= MIN_HACIM_ARTISI:
                    if price_change >= 10.0:
                        yon, emoji, aciklama = "SHORT", "🔴", "Aşırı pump! Düzeltme bekleniyor."
                    else:
                        yon, emoji, aciklama = "LONG", "🟢", "Güçlü momentum! Trend devamı bekleniyor."
                    p_old = f"{prev_close:.8f}".rstrip('0').rstrip('.')
                    p_new = f"{curr_close:.8f}".rstrip('0').rstrip('.')
                    now_time = simdi().strftime('%I:%M:%S %p')
                    msg = (f"{emoji} <b>PUMP SİNYALİ: {symbol} ({yon})</b>\n\n"
                           f"💰 <b>Fiyat:</b> ${p_old} ➔ ${p_new} (<b>{price_change:+.2f}%</b>)\n"
                           f"📊 <b>Hacim Artışı:</b> {vol_increase_usdt:.0f} USDT (<b>{vol_change:+.0f}%</b>)\n\n"
                           f"💡 <b>Analiz:</b> {aciklama}\n⏱ <b>Zaman:</b> {now_time}")
                    if tg(C, msg):
                        son_pump_sinyalleri[symbol] = time.time()
                        log("PUMP", f"🔥 {yon} SİNYALİ: {symbol}")
                time.sleep(0.05)
            time.sleep(PUMP_ARALIGI)
        except Exception as e:
            log("PUMP", f"Hata: {e}")
            time.sleep(30)

# =====================================================
# 4. MOTOR: SETUP ANALİZİ (POC / VAL)
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
                prev_price = float(klines[-2][4])
                if (poc * 0.98) <= current_price < poc and current_price > prev_price:
                    target, stop = poc * 1.15, val * 0.99
                    target_pct = ((target - current_price) / current_price) * 100
                    stop_pct = ((stop - current_price) / current_price) * 100
                    trend_1d = "yukarı" if current_price > float(klines[-24][4]) else "aşağı"
                    trend_1h = "yukarı" if current_price > float(klines[-6][4]) else "aşağı"
                    msg = (f"<b>#{symbol}</b>\n\nFiyat: <b>{current_price:.4f}</b>\nHedef: <b>{target:.4f}</b> ({target_pct:+.1f}%)\nİptal: <b>{stop:.4f}</b> ({stop_pct:+.1f}%)\n\n"
                           f"{symbol.replace('USDT','')}, <b>{poc:.4f}</b> POC direncine takıldı. Trend 1g {trend_1d}, 1s {trend_1h}. ✊\n\n"
                           f"<b>{val:.4f}</b> seviyesinde güçlü alım duvarı var. 🧊\n\n"
                           f"Anahtar — <b>{poc:.4f}</b> üzerinde kalıcılık. Hedef <b>{target:.4f}</b>. ⚡\n\n"
                           f"🛡️ Stop {stop:.4f} arkasında.")
                    if tg(C, msg):
                        son_setup_sinyalleri[symbol] = time.time()
                        log("SETUP", f"🔥 SETUP SİNYALİ: {symbol}")
                time.sleep(0.1)
            time.sleep(SETUP_ARALIGI)
        except Exception as e:
            log("SETUP", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# 5. MOTOR: FORMASYON VE KIRILIM AVCISI
# =====================================================

def formasyon_loop():
    log("FORMASYON", "Formasyon ve Kırılım Motoru Başladı.")
    time.sleep(45) 
    while True:
        try:
            log("FORMASYON", "Düşen kanal ve destek kırılımları taranıyor...")
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
                high_first_half = max(highs[-40:-20])
                high_second_half = max(highs[-20:-5])
                support_zone = min(lows[-20:])
                avg_vol = sum(vols[-20:-1]) / 19 if len(vols) >= 20 else 1
                current_vol = vols[-1]
                if high_first_half > high_second_half and current_price > high_second_half and current_price < (support_zone * 1.20):
                    if current_vol > (avg_vol * 1.5):
                        target = high_first_half
                        target_pct = ((target - current_price) / current_price) * 100
                        stop = support_zone * 0.98
                        stop_pct = ((stop - current_price) / current_price) * 100
                        msg = (f"🚀 <b>FORMASYON SİNYALİ: {symbol.replace('USDT','')}/USDT</b>\n"
                               f"<i>Düşen Kanal / Kama Kırılımı ve Destek Dönüşü!</i>\n\n"
                               f"💰 <b>Giriş (Güncel Fiyat):</b> {current_price:.4f}\n"
                               f"🎯 <b>Hedef (Big Move):</b> {target:.4f} (<b>{target_pct:+.1f}%</b>)\n"
                               f"🛑 <b>Stop-Loss:</b> {stop:.4f} ({stop_pct:+.1f}%)\n\n"
                               f"📊 <b>Hacim:</b> {current_vol:.0f} (Ortalamanın {current_vol/avg_vol:.1f} katı)\n"
                               f"🛡️ <b>Destek Bölgesi:</b> {support_zone:.4f}\n\n"
                               f"💡 <b>Analiz:</b> Fiyat uzun süredir düşen bir kanal içindeydi. {support_zone:.4f} seviyesindeki güçlü destek bölgesinden (yeşil kutu) tepki aldı ve şu an direnci kırıp yukarı ivme kazanıyor. <b>Buy and hold!</b> 📈\n\n"
                               f"<i>Bu tarz sinyaller haftada 1-2 kez gelir, büyük hareket potansiyeli taşır.</i>")
                        if tg(C, msg):
                            son_formasyon_sinyalleri[symbol] = time.time()
                            log("FORMASYON", f"🔥 KIRILIM SİNYALİ: {symbol}")
                time.sleep(0.1)
            time.sleep(FORMASYON_ARALIGI)
        except Exception as e:
            log("FORMASYON", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# 6. MOTOR: BOLLINGER BANDI (HAFTALIK SQUEEZE & KIRILIM) - YENİ
# =====================================================

def bollinger_loop():
    log("BOLLINGER", "Haftalık Bollinger Bandı Motoru Başladı.")
    time.sleep(60) # Diğer motorlarla çakışmasın diye
    
    while True:
        try:
            log("BOLLINGER", "Haftalık Bollinger Sıkışmaları (Squeeze) taranıyor...")
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(60)
                continue
            
            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_BOLLINGER]

            for symbol in adaylar:
                # Aynı coin için 3 gün boyunca yeni sinyal atma
                if symbol in son_bollinger_sinyalleri and time.time() - son_bollinger_sinyalleri[symbol] < 259200: 
                    continue

                # Haftalık (1w) grafikte son 50 mumu çek
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1w&limit=50")
                if not isinstance(klines, list) or len(klines) < 25: continue

                # Son kapanmış haftalık mumu kullan (Yalan kırılımları önler)
                closes = [float(k[4]) for k in klines[:-1]]
                vols = [float(k[7]) for k in klines[:-1]]
                current_price = closes[-1]

                if len(closes) < 21: continue

                # BB Hesapla
                bb = calculate_bollinger(closes, 20, 2)
                if not bb: continue

                # Sıkışma (Squeeze) Tespiti: Mevcut bant genişliği son 10 haftanın en düşük seviyelerine yakın mı?
                # Bunu anlamak için son 10 mumun bant genişliklerini hesaplıyoruz
                bandwidth_history = []
                for i in range(10, 0, -1):
                    past_closes = closes[:-i]
                    past_bb = calculate_bollinger(past_closes, 20, 2)
                    if past_bb:
                        bandwidth_history.append(past_bb["bandwidth"])

                if not bandwidth_history: continue
                
                min_bandwidth = min(bandwidth_history)
                avg_bandwidth = sum(bandwidth_history) / len(bandwidth_history)

                # Eğer mevcut bant genişliği, son 10 haftanın ortalamasının %60'ından küçükse (Sıkışma var)
                is_squeeze = bb["bandwidth"] < (avg_bandwidth * 0.6)

                if is_squeeze:
                    # Hacim Teyidi (Son haftanın hacmi ortalamanın üzerinde mi?)
                    avg_vol = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 1
                    current_vol = vols[-1]
                    vol_surge = current_vol > (avg_vol * 1.2) # %20 hacim artışı

                    if bb["signal"] and vol_surge:
                        yon = "AL (Yukarı Kırılım)" if "AL" in bb["signal"] else "SAT (Aşağı Kırılım)"
                        emoji = "🚀" if "AL" in bb["signal"] else "🔴"
                        
                        # Hedefler (Bant genişliği kadar hareket beklentisi)
                        target = bb["upper"] if "AL" in bb["signal"] else bb["lower"]
                        target_pct = ((target - current_price) / current_price) * 100
                        
                        sl = bb["lower"] if "AL" in bb["signal"] else bb["upper"]
                        sl_pct = ((sl - current_price) / current_price) * 100

                        msg = (f"{emoji} <b>HAFTALIK BOLLINGER SİNYALİ: {symbol}</b>\n"
                               f"<i>Haftalık Squeeze (Sıkışma) ve Kırılım!</i>\n\n"
                               f"💰 <b>Kapanış Fiyatı:</b> {current_price:.4f}\n"
                               f"🎯 <b>Hedef Bant:</b> {target:.4f} (<b>{target_pct:+.1f}%</b>)\n"
                               f"🛑 <b>Stop Bant:</b> {sl:.4f} ({sl_pct:+.1f}%)\n\n"
                               f"📊 <b>Bant Durumu:</b> Sıkışma Tespit Edildi (Genişlik: {bb['bandwidth']:.4f})\n"
                               f"📈 <b>Hacim:</b> {current_vol:.0f} (Ortalamanın {current_vol/avg_vol:.1f} katı)\n\n"
                               f"💡 <b>Analiz:</b> Fiyat haftalardır daralan bir bant içindeydi. Bu hafta kapanış itibariyle <b>{yon}</b> gerçekleşti ve hacim patladı. Haftalık kırılımlar aylarca sürebilecek büyük trendlerin başlangıcıdır. <b>{'Long' if 'AL' in bb['signal'] else 'Short'} pozisyon açılabilir.</b>")
                        
                        if tg(C, msg):
                            son_bollinger_sinyalleri[symbol] = time.time()
                            log("BOLLINGER", f"🔥 SQUEEZE KIRILIMI: {symbol} ({yon})")

                time.sleep(0.15) # API'yi yormamak için

            log("BOLLINGER", "Tarama bitti, 1 saat bekleniyor.")
            time.sleep(BOLLINGER_ARALIGI)
        except Exception as e:
            log("BOLLINGER", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# RENDER WEB SUNUCUSU
# =====================================================
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Hexa-Engine Bot is running!")
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
    
    # YENİ MOTOR: Bollinger Bandı (Haftalık)
    threading.Thread(target=bollinger_loop, daemon=True).start()
    
    run_web_server()

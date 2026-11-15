import time
import threading
import requests
import traceback
import math

# =====================================================
# AYARLAR VE DEĞİŞKENLER (Kendi API ve Chat ID'lerini buraya yaz)
# =====================================================
BASE = "https://api.binance.com"
C = "TELEGRAM_CHAT_ID_BURAYA" # Kendi Chat ID'ni yaz
MIN_HACIM_SMA200 = 500000 # SMA200 için minimum hacim
SMA200_ARALIGI = 3600 # SMA200 tarama aralığı (saniye)

# Sinyal hafızaları
son_sma200_sinyalleri = {}
son_formasyon_sinyalleri = {}

# =====================================================
# YARDIMCI FONKSİYONLAR (Senin orijinal fonksiyonların)
# =====================================================
def log(modul, mesaj):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [{modul}] {mesaj}")

def gj(url):
    try:
        r = requests.get(url, timeout=10)
        return r.json()
    except Exception as e:
        return None

def tg(chat_id, msg):
    # Telegram mesaj gönderme fonksiyonu (Kendi Bot Token'ını buraya ekle)
    TOKEN = "TELEGRAM_BOT_TOKEN_BURAYA"
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": msg, "parse_mode": "HTML"}
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except:
        return False

def calculate_sma(closes, period):
    if len(closes) < period: return None
    sma_values = []
    for i in range(len(closes) - period + 1):
        sma_values.append(sum(closes[i:i+period]) / period)
    return sma_values

def squeeze_radar_check(symbol, tf):
    # Senin orijinal squeeze radar fonksiyonun (Burayı boş bıraktım, kendi kodunu ekleyebilirsin)
    return "" 

# =====================================================
# MOTOR 10: SMA200 
# =====================================================
def sma200_loop():
    log("SMA200", "SMA200 (3 Günlük ve Haftalık) Motoru Başladı.")
    time.sleep(10)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): 
                time.sleep(60); continue
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
                        sqz = squeeze_radar_check(symbol, tf)
                        msg = (f"🚀 <b>SMA200 YUKARI KIRILIM (LONG): {symbol}</b>\n<i>{tf.upper()} Grafik</i>\n\n"
                               f"💡 <b>Açıklama:</b> Fiyat, 200 periyotluk basit ortalamanın (SMA200) üzerine çıktı.")
                        if sqz: msg += f"\n\n{sqz}"
                        if tg(C, msg): son_sma200_sinyalleri[cooldown_key] = time.time()
                    elif prev >= p_sma and curr < c_sma and curr < c_sma * 0.995:
                        sqz = squeeze_radar_check(symbol, tf)
                        msg = (f"💀 <b>SMA200 AŞAĞI KIRILIM (SHORT): {symbol}</b>\n<i>{tf.upper()} Grafik</i>\n\n"
                               f"💡 <b>Açıklama:</b> Fiyat, 200 periyotluk ortalamanın (SMA200) altına sarktı.")
                        if sqz: msg += f"\n\n{sqz}"
                        if tg(C, msg): son_sma200_sinyalleri[cooldown_key] = time.time()
                time.sleep(0.1)
            time.sleep(SMA200_ARALIGI)
        except Exception as e: 
            time.sleep(60)

# =====================================================
# MOTOR 11: FORMASYON AVCISI (İkili Tepe/Dip, OBO)
# =====================================================
def find_pivots(highs, lows, left=3, right=3):
    pivot_highs, pivot_lows = [], []
    for i in range(left, len(highs) - right):
        if highs[i] == max(highs[i-left:i+right+1]): pivot_highs.append((i, highs[i]))
        if lows[i] == min(lows[i-left:i+right+1]): pivot_lows.append((i, lows[i]))
    return pivot_highs, pivot_lows

def pattern_scanner_loop():
    log("FORMASYON", "Formasyon Avcısı Motoru Başlatıldı!")
    time.sleep(60) 
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): 
                time.sleep(60); continue
                
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= 1000000]: 
                for tf in ['4h', '1d']:
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=100")
                    if not isinstance(klines, list) or len(klines) < 50: continue
                    
                    closes = [float(k[4]) for k in klines]
                    highs = [float(k[2]) for k in klines]
                    lows = [float(k[3]) for k in klines]
                    volumes = [float(k[5]) for k in klines]
                    
                    curr_close = closes[-2]
                    curr_vol = volumes[-2]
                    avg_vol = sum(volumes[-22:-2]) / 20 
                    
                    if curr_vol < avg_vol * 1.2: continue

                    pivot_highs, pivot_lows = find_pivots(highs, lows)
                    signal_type, formasyon_adi, neckline, yapi_yuksekligi = None, "", 0.0, 0.0

                    # İkili Tepe
                    if len(pivot_highs) >= 2:
                        p1_idx, p1_price = pivot_highs[-2]
                        p2_idx, p2_price = pivot_highs[-1]
                        if 5 <= (p2_idx - p1_idx) <= 30 and abs(p1_price - p2_price) / p1_price <= 0.02:
                            neckline = min(lows[p1_idx:p2_idx+1])
                            if curr_close < neckline * 0.99:
                                signal_type, formasyon_adi = "SHORT", "İkili Tepe"
                                yapi_yuksekligi = max(p1_price, p2_price) - neckline

                    # İkili Dip
                    if not signal_type and len(pivot_lows) >= 2:
                        p1_idx, p1_price = pivot_lows[-2]
                        p2_idx, p2_price = pivot_lows[-1]
                        if 5 <= (p2_idx - p1_idx) <= 30 and abs(p1_price - p2_price) / p1_price <= 0.02:
                            neckline = max(highs[p1_idx:p2_idx+1])
                            if curr_close > neckline * 1.01:
                                signal_type, formasyon_adi = "LONG", "İkili Dip"
                                yapi_yuksekligi = neckline - min(p1_price, p2_price)

                    # OBO
                    if not signal_type and len(pivot_highs) >= 3:
                        p1_idx, p1_price = pivot_highs[-3]
                        p2_idx, p2_price = pivot_highs[-2] 
                        p3_idx, p3_price = pivot_highs[-1] 
                        if p2_price > p1_price and p2_price > p3_price and abs(p1_price - p3_price) / p1_price <= 0.03:
                            neckline = min(lows[p1_idx:p3_idx+1])
                            if curr_close < neckline * 0.99:
                                signal_type, formasyon_adi = "SHORT", "OBO"
                                yapi_yuksekligi = p2_price - neckline

                    # Ters OBO
                    if not signal_type and len(pivot_lows) >= 3:
                        p1_idx, p1_price = pivot_lows[-3]
                        p2_idx, p2_price = pivot_lows[-2] 
                        p3_idx, p3_price = pivot_lows[-1] 
                        if p2_price < p1_price and p2_price < p3_price and abs(p1_price - p3_price) / p1_price <= 0.03:
                            neckline = max(highs[p1_idx:p3_idx+1])
                            if curr_close > neckline * 1.01:
                                signal_type, formasyon_adi = "LONG", "Ters OBO"
                                yapi_yuksekligi = neckline - p2_price

                    if signal_type:
                        cooldown_key = f"FORMASYON_{tf}_{formasyon_adi}_{symbol}"
                        if cooldown_key in son_formasyon_sinyalleri and time.time() - son_formasyon_sinyalleri[cooldown_key] < 14400:
                            continue
                            
                        emoji = "🚀" if signal_type == "LONG" else "💀"
                        yon = "YUKARI KIRILIM (LONG)" if signal_type == "LONG" else "AŞAĞI KIRILIM (SHORT)"
                        hedef = neckline + yapi_yuksekligi if signal_type == "LONG" else neckline - yapi_yuksekligi

                        msg = (f"{emoji} <b>{formasyon_adi.upper()} {yon}: {symbol}</b>\n<i>{tf.upper()} Grafik</i>\n\n"
                               f"🎯 <b>Boyun Çizgisi:</b> {neckline:.4f}\n"
                               f"📊 <b>Hacim:</b> Ortalamanın {curr_vol/avg_vol:.1f} katı\n"
                               f"🏁 <b>Hedef:</b> {hedef:.4f}")
                        
                        if tg(C, msg): 
                            son_formasyon_sinyalleri[cooldown_key] = time.time()
                            
                time.sleep(0.2)
            time.sleep(1800) 
        except Exception as e:
            log("FORMASYON", f"Hata: {e}") 
            time.sleep(60)

# =====================================================
# ANA ÇALIŞTIRICI (BOT BURADA AYAKTA KALIR)
# =====================================================
if __name__ == "__main__":
    log("SİSTEM", "Bot başlatılıyor...")
    
    # Motorları arka planda başlat
    threading.Thread(target=sma200_loop, daemon=True).start()
    threading.Thread(target=pattern_scanner_loop, daemon=True).start()
    
    # Ana döngü (Botun kapanmasını engeller)
    while True:
        time.sleep(10)

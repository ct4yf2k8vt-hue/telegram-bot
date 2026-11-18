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
FAPI = "https://fapi.binance.com"

# MOTOR ÇALIŞMA SÜRELERİ
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

# FİLTRELER
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

# YASAKLI VARLIKLAR
YASAKLI_HISSELER = [
    "AAPLUSDT", "TSLAUSDT", "NVDAUSDT", "COINUSDT", "MSTRUSDT", 
    "SQQQUSDT", "TQQQUSDT", "SPYUSDT", "QQQUSDT", "AMZNUSDT", 
    "GOOGLUSDT", "METAUSDT", "NFLXUSDT", "BABAUSDT", "MSFTUSDT", 
    "HOODUSDT", "CRCLUSDT", "XAUUSDT", "XAGUSDT", "PAXGUSDT"
]

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

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1: return 50
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0: return 100
    return 100 - (100 / (1 + (avg_gain / avg_loss)))

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

def trap_radar_check(symbol):
    try:
        oi_data = gj(f"{FAPI}/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=2")
        oi_artiyor = False
        if oi_data and len(oi_data) >= 2:
            oi_old = float(oi_data[0]['sumOpenInterest'])
            oi_new = float(oi_data[1]['sumOpenInterest'])
            if oi_old > 0 and ((oi_new - oi_old) / oi_old) * 100 > 0.2: oi_artiyor = True

        taker_data = gj(f"{FAPI}/futures/data/takerlongshortRatio?symbol={symbol}&period=5m&limit=1")
        alici_baskin = False
        if taker_data and len(taker_data) > 0:
            buy_vol = float(taker_data[0]['buyVol'])
            sell_vol = float(taker_data[0]['sellVol'])
            total = buy_vol + sell_vol
            if total > 0 and (buy_vol / total) * 100 > 55: alici_baskin = True

        if oi_artiyor and alici_baskin: return "🐋 <b>BALİNA TEYİDİ (Gerçek Pump)</b>"
        elif not alici_baskin: return "⚠️ <b>DİKKAT: SAHTE PUMP (TUZAK OLABİLİR)</b>"
        else: return "⚖️ <b>Kısmi Teyit</b>"
    except: return ""

def find_pivots(highs, lows, window=3):
    pivot_highs = []
    pivot_lows = []
    for i in range(window, len(highs) - window):
        if highs[i] == max(highs[i-window:i+window+1]): pivot_highs.append((i, highs[i]))
        if lows[i] == min(lows[i-window:i+window+1]): pivot_lows.append((i, lows[i]))
    return pivot_highs, pivot_lows

# =====================================================
# MOTOR 1: TOP GAINERS (Görsel 1)
# =====================================================
def top_gainers_loop():
    log("TOP_GAINERS", "Günlük En İyi Hareketler Rapor Botu Başladı.")
    time.sleep(10)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(300); continue
            
            temiz_tickers = [t for t in tickers if t['symbol'] not in YASAKLI_HISSELER and not any(x in t['symbol'] for x in ['3L', '3S', '5L', '5S']) and t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) > 5_000_000]
            temiz_tickers.sort(key=lambda x: float(x['priceChangePercent']), reverse=True)
            top_5 = temiz_tickers[:5]

            if top_5:
                msg = "🔥 <b>Günün En İyi Hareketleri (24 Saat)</b>\n"
                msg += "<i>Maksimum yükseliş yönü, hacim teyitli</i>\n\n"
                medals = ["🥇", "🥈", "🥉", "🏅", "⭐️"]
                for i, t in enumerate(top_5):
                    symbol = t['symbol'].replace("USDT", "")
                    change = float(t['priceChangePercent'])
                    price = float(t['lastPrice'])
                    change_str = f"+{change:.0f}%" if change > 0 else f"{change:.0f}%"
                    msg += f"{medals[i]} <b>{symbol}</b> {change_str} | Fiyat: {akilli_fiyat(price)}\n"
                
                msg += "\n👉 <i>En çok kazandıran coinler listelendi.</i>"
                tg(C, msg)
            time.sleep(TOP_GAINERS_ARALIGI)
        except Exception as e:
            log("TOP_GAINERS", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 2: PUMP WINNER (Görsel 2)
# =====================================================
def pump_winner_loop():
    log("PUMP_WINNER", "Günün Kazananı (Pump Dedektör) Rapor Botu Başladı.")
    time.sleep(20)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(300); continue
            
            temiz_tickers = [t for t in tickers if t['symbol'] not in YASAKLI_HISSELER and not any(x in t['symbol'] for x in ['3L', '3S', '5L', '5S']) and t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) > 10_000_000]
            temiz_tickers.sort(key=lambda x: float(x['priceChangePercent']), reverse=True)
            
            if not temiz_tickers: 
                time.sleep(300)
                continue
            
            winner = temiz_tickers[0]
            symbol = winner['symbol']
            change_pct = float(winner['priceChangePercent'])
            
            if change_pct < 15: 
                time.sleep(PUMP_WINNER_ARALIGI)
                continue

            klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1d&limit=1")
            if not isinstance(klines, list) or len(klines) < 1: 
                time.sleep(PUMP_WINNER_ARALIGI)
                continue
                
            day_low = float(klines[0][3])
            day_high = float(klines[0][2])
            max_move = ((day_high - day_low) / day_low) * 100

            msg = (f"🏆 <b>GÜNÜN KAZANANI: {symbol}</b>\n\n"
                   f"🔥 24 saat içinde en çok yükselen coin!\n\n"
                   f"📉 <b>Dip Fiyat:</b> {akilli_fiyat(day_low)}\n"
                   f"🚀 <b>Zirve Fiyat:</b> {akilli_fiyat(day_high)}\n"
                   f"📈 <b>Maksimum Yükseliş:</b> <b>+{max_move:.1f}%</b>\n\n"
                   f"💡 <b>Değerlendirme:</b> {symbol} gün içerisinde muazzam bir hacim ve fiyat patlaması yaşadı. Pump Dedektör verilerimize göre günün en kârlı hareketi bu coinde gerçekleşti.")
            
            tg(C, msg)
            time.sleep(PUMP_WINNER_ARALIGI) 
            
        except Exception as e:
            log("PUMP_WINNER", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 3: CHARTIST (EMA200) - RİSK FİLTRELİ
# =====================================================
def chartist_loop():
    log("CHARTIST", "Zırhlı EMA200 Motoru Başladı.")
    time.sleep(30)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in YASAKLI_HISSELER and not any(x in t['symbol'] for x in ['3L', '3S', '5L', '5S']) and float(t.get('quoteVolume', 0)) >= MIN_HACIM_CHARTIST]:
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
                            sl = ema200[-1] * 0.97 
                            hedef = curr * 1.15
                            
                            sl_pct = ((sl - curr) / curr) * 100
                            hedef_pct = ((hedef - curr) / curr) * 100
                            
                            if abs(sl_pct) > 12.0: continue
                            if abs(sl_pct) > abs(hedef_pct): continue

                            msg = (f"📈 <b>UZUN VADELİ SPOT (LONG): {symbol}</b>\n"
                                   f"<i>{tf.upper()} EMA200 Kırılımı</i>\n\n"
                                   f"💰 <b>Giriş:</b> {akilli_fiyat(curr)}\n"
                                   f"🛑 <b>SL:</b> {akilli_fiyat(sl)} (<b>{sl_pct:+.1f}%</b>)\n"
                                   f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n\n"
                                   f"📊 <b>Teyit:</b> Hacim Onaylı ve %1 üzeri kapanış.\n\n"
                                   f"💡 <b>Açıklama:</b> EMA200, uzun vadeli trend çizgisidir. Fiyatın bu devasa çizgiyi hacimle yukarı kırması, ayı piyasasının sona erip boğa piyasasının başladığını gösterir.")
                            tg(C, msg); son_sinyaller[cooldown_key] = time.time()
                time.sleep(0.05)
            time.sleep(CHARTIST_ARALIGI)
        except: time.sleep(60)

# =====================================================
# MOTOR 4: PUMP AVCISI
# =====================================================
def pump_loop():
    log("PUMP", "Anlık Pump Avcısı Başladı.")
    time.sleep(45)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in YASAKLI_HISSELER and not any(x in t['symbol'] for x in ['3L', '3S', '5L', '5S']) and float(t.get('quoteVolume', 0)) >= MIN_HACIM_PUMP]:
                if symbol in son_pump_sinyalleri and time.time() - son_pump_sinyalleri[symbol] < 900: continue
                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=5m&limit=3")
                if not isinstance(klines, list) or len(klines) < 3: continue
                curr_k, prev_k = klines[-2], klines[-3]
                cc, pc = float(curr_k[4]), float(prev_k[4])
                cv, pv = float(curr_k[7]), float(prev_k[7])
                if pc == 0 or pv == 0: continue
                price_change = ((cc-pc)/pc)*100
                vol_change = ((cv-pv)/pv)*100
                vol_diff = cv-pv
                
                if price_change >= 3.0 and vol_change >= 100.0 and vol_diff >= 50_000:
                    trap_radar = trap_radar_check(symbol)
                    msg = (f"🚀 <b>Pump - {symbol}</b>\n"
                           f"💰 <b>Fiyat:</b> {akilli_fiyat(cc)} (<b>{price_change:+.2f}%</b>)\n"
                           f"📊 <b>Hacim Artışı:</b> {vol_change:+.0f}%\n"
                           f"Volume increased by {vol_diff:.0f} ⬆️\n\n"
                           f"{trap_radar}\n\n"
                           f"💡 <b>Açıklama:</b> 5 dakikalık grafikte aniden sert bir hacim ve fiyat patlaması yaşandı. Trap Radar sayesinde bu hareketin arkasında gerçek balinaların mı yoksa sahte botların mı olduğu analiz edildi.")
                    tg(C, msg); son_pump_sinyalleri[symbol] = time.time()
                time.sleep(0.05)
            time.sleep(PUMP_ARALIGI)
        except: time.sleep(60)

# =====================================================
# MOTOR 5: ANORMAL VOLATİLİTE VE DUMP AVCISI
# =====================================================
def volatilite_loop():
    log("VOLATILITE", "Anormal Volatilite ve Dump Avcısı Başladı (15m).")
    time.sleep(55)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in YASAKLI_HISSELER and not any(x in t['symbol'] for x in ['3L', '3S', '5L', '5S']) and float(t.get('quoteVolume', 0)) >= MIN_HACIM_VOLATILITE]:
                if symbol in son_volatilite_sinyalleri and time.time() - son_volatilite_sinyalleri[symbol] < 1800: continue
                
                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=15m&limit=25")
                if not isinstance(klines, list) or len(klines) < 25: continue
                
                opens = [float(k[1]) for k in klines]
                closes = [float(k[4]) for k in klines]
                vols = [float(k[7]) for k in klines]
                
                curr_open = opens[-2]
                curr_close = closes[-2]
                curr_vol = vols[-2]
                
                avg_vol = sum(vols[-22:-2]) / 20 if len(vols) >= 22 else 1
                
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
                        son_volatilite_sinyalleri[symbol] = time.time()
                        log("VOLATILITE", f"🔥 SİNYAL: {symbol} ({yon})")
                        
                time.sleep(0.1)
            time.sleep(VOLATILITE_ARALIGI)
        except Exception as e:
            log("VOLATILITE", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 6: FORMASYON (MSS) - FİLTRELİ
# =====================================================
def formasyon_loop():
    log("FORMASYON", "Teyitli Formasyon ve Likidite Avcısı Başladı.")
    time.sleep(60)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in YASAKLI_HISSELER and not any(x in t['symbol'] for x in ['3L', '3S', '5L', '5S']) and float(t.get('quoteVolume', 0)) >= MIN_HACIM_FORMASYON]:
                if symbol in son_formasyon_sinyalleri and time.time() - son_formasyon_sinyalleri[symbol] < 86400: continue
                
                klines_5m = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=5m&limit=100")
                klines_15m = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=15m&limit=50")
                
                if not isinstance(klines_5m, list) or not isinstance(klines_15m, list): continue
                if len(klines_5m) < 100 or len(klines_15m) < 50: continue
                
                highs = [float(k[2]) for k in klines_5m]
                lows = [float(k[3]) for k in klines_5m]
                closes = [float(k[4]) for k in klines_5m]
                vols = [float(k[7]) for k in klines_5m]
                
                closes_15m = [float(k[4]) for k in klines_15m]
                ema11 = calculate_ema(closes_15m, 11)[-1]
                ema21 = calculate_ema(closes_15m, 21)[-1]
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
                    if rsi < 50 and ema11 < ema21:
                        sinyal = True; formasyon_adi = "Çift Tepe (M)"; yon = "SHORT"; emoji = "🔻"
                        hedef = last_l[-1][1] - (last_h[1][1] - last_l[-1][1]); stop = last_h[2][1] * 1.02
                        aciklama = "İkinci tepe ilkini geçemedi ve boyun çizgisi hacimle kırıldı. 15m trend ve RSI düşüşü destekliyor."

                elif abs(last_l[0][1] - last_l[2][1]) / last_l[0][1] < 0.03 and current_price > last_h[-1][1] and is_vol_teyit:
                    if rsi > 50 and ema11 > ema21:
                        sinyal = True; formasyon_adi = "Çift Dip (W)"; yon = "LONG"; emoji = "🚀"
                        hedef = last_h[-1][1] + (last_h[-1][1] - last_l[1][1]); stop = last_l[2][1] * 0.98
                        aciklama = "İkinci dip satıcıları tüketti ve boyun çizgisi hacimle kırıldı. 15m trend ve RSI yükselişi destekliyor."

                elif last_h[1][1] > last_h[0][1] and last_h[1][1] > last_h[2][1] and abs(last_h[0][1] - last_h[2][1]) / last_h[0][1] < 0.05 and current_price < last_l[-1][1] and is_vol_teyit:
                    if rsi < 50 and ema11 < ema21:
                        sinyal = True; formasyon_adi = "Omuz-Baş-Omuz"; yon = "SHORT"; emoji = "🔻"
                        hedef = last_l[-1][1] - (last_h[1][1] - last_l[-1][1]); stop = last_h[2][1] * 1.02
                        aciklama = "Klasik dönüş formasyonu. Boyun çizgisi kırıldı, 15m trend aşağı yönlü teyit veriyor."

                elif highs[-2] > max(highs[-20:-2]) and current_price < max(highs[-20:-2]) and is_vol_teyit:
                    if rsi < 50 and ema11 < ema21:
                        sinyal = True; formasyon_adi = "Likidite Avı (Sweep)"; yon = "SHORT"; emoji = "💀"
                        stop = highs[-2] * 1.01; hedef = current_price * 0.97
                        aciklama = "Fiyat eski tepenin üzerine fitil attı (Stop-loss avı) ancak hemen altına kapandı. Akıllı para satışa geçmiş olabilir."

                elif lows[-2] < min(lows[-20:-2]) and current_price > min(lows[-20:-2]) and is_vol_teyit:
                    if rsi > 50 and ema11 > ema21:
                        sinyal = True; formasyon_adi = "Likidite Avı (Sweep)"; yon = "LONG"; emoji = "🚀"
                        stop = lows[-2] * 0.99; hedef = current_price * 1.03
                        aciklama = "Fiyat eski dibin altına fitil attı (Stop-loss avı) ancak hemen üstüne kapandı. Akıllı para alıma geçmiş olabilir."

                if not sinyal:
                    last_20_high = max(highs[-20:])
                    last_20_low = min(lows[-20:])
                    range_pct = ((last_20_high - last_20_low) / last_20_low) * 100
                    
                    if range_pct < 5 and current_price > last_20_high and is_vol_teyit and rsi > 50:
                        sinyal = True; formasyon_adi = "Sıkışma ve Yukarı Kırılım (Bayrak)"; yon = "LONG"; emoji = "🚀"
                        hedef = current_price + (last_20_high - last_20_low); stop = last_20_low * 0.98
                        aciklama = "Fiyat dar bir bantta sıkıştı ve hacimle yukarı kırıldı. Volatilite patlaması başlıyor."
                    elif range_pct < 5 and current_price < last_20_low and is_vol_teyit and rsi < 50:
                        sinyal = True; formasyon_adi = "Sıkışma ve Aşağı Kırılım (Ters Bayrak)"; yon = "SHORT"; emoji = "🔻"
                        hedef = current_price - (last_20_high - last_20_low); stop = last_20_high * 1.02
                        aciklama = "Fiyat dar bir bantta sıkıştı ve hacimle aşağı kırıldı. Volatilite patlaması başlıyor."

                if sinyal:
                    hedef_pct = ((hedef - current_price) / current_price) * 100
                    stop_pct = ((stop - current_price) / current_price) * 100
                    
                    if yon == "SHORT" and hedef >= current_price: continue
                    if yon == "LONG" and hedef <= current_price: continue
                    if abs(stop_pct) > 10.0: continue 
                    if abs(hedef_pct) < 1.0 or abs(hedef_pct) > 20.0: continue

                    msg = (f"{emoji} <b>TEYİTLİ DÖNÜŞ ({yon}): {symbol}</b>\n"
                           f"<i>5m Formasyon - 15m Trend Teyitli</i>\n\n"
                           f"💰 <b>Giriş:</b> {akilli_fiyat(current_price)}\n"
                           f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                           f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                           f"📊 <b>Formasyon:</b> {formasyon_adi}\n"
                           f"📊 <b>Teyitler:</b> Hacim ({current_vol/avg_vol:.1f}x), RSI ({rsi:.1f}), EMA (15m)\n\n"
                           f"💡 <b>Açıklama:</b> {aciklama}")
                    
                    if tg(C, msg):
                        son_formasyon_sinyalleri[symbol] = time.time()
                        log("FORMASYON", f"🔥 SİNYAL: {symbol} ({yon} - {formasyon_adi})")
                
                time.sleep(0.1)
            time.sleep(FORMASYON_ARALIGI)
        except Exception as e:
            log("FORMASYON", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 7: EMA RIBBON
# =====================================================
def ema_ribbon_loop():
    log("EMA_RIBBON", "EMA Dizilim Motoru Başladı.")
    time.sleep(90)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in YASAKLI_HISSELER and not any(x in t['symbol'] for x in ['3L', '3S', '5L', '5S']) and float(t.get('quoteVolume', 0)) >= MIN_HACIM_EMA_RIBBON]:
                if symbol in son_ema_ribbon_sinyalleri and time.time() - son_ema_ribbon_sinyalleri[symbol] < 86400: continue
                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1d&limit=200")
                if not isinstance(klines, list) or len(klines) < 200: continue
                closes = [float(k[4]) for k in klines[:-1]]
                vols = [float(k[7]) for k in klines[:-1]]
                curr = closes[-1]
                curr_vol = vols[-1]
                avg_vol = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 1
                if curr_vol <= (avg_vol * 1.2): continue 
                
                e7, e25, e99, e200 = calculate_ema(closes, 7)[-1], calculate_ema(closes, 25)[-1], calculate_ema(closes, 99)[-1], calculate_ema(closes, 200)[-1]
                
                if curr > e7 > e25 > e99 > e200:
                    msg = (f"🚀 <b>EMA DİZİLİMİ (LONG): {symbol}</b>\n"
                           f"<i>1 Günlük Grafik - Çoklu EMA Dizilimi</i>\n\n"
                           f"💰 <b>Giriş:</b> {akilli_fiyat(curr)}\n"
                           f"🎯 <b>Hedef:</b> {akilli_fiyat(curr * 1.20)} (+20.0%)\n"
                           f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(e25 * 0.95)}\n\n"
                           f"📊 <b>EMA Dizilimi:</b>\n"
                           f"EMA7: {akilli_fiyat(e7)} | EMA25: {akilli_fiyat(e25)}\n"
                           f"EMA99: {akilli_fiyat(e99)} | EMA200: {akilli_fiyat(e200)}\n"
                           f"📈 <b>Hacim Teyidi:</b> Alındı ({curr_vol/avg_vol:.1f}x)\n\n"
                           f"💡 <b>Açıklama:</b> EMA Ribbon dizilimi, piyasanın tüm zaman dilimlerinde mükemmel bir uyum içinde olduğunu gösterir. Trendin en güçlü olduğu 'tatlı nokta'dır.")
                    tg(C, msg); son_ema_ribbon_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(EMA_RIBBON_ARALIGI)
        except: time.sleep(60)

# =====================================================
# MOTOR 8: MACD
# =====================================================
def macd_loop():
    log("MACD", "MACD Motoru Başladı.")
    time.sleep(105)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in YASAKLI_HISSELER and not any(x in t['symbol'] for x in ['3L', '3S', '5L', '5S']) and float(t.get('quoteVolume', 0)) >= MIN_HACIM_MACD]:
                if symbol in son_macd_sinyalleri and time.time() - son_macd_sinyalleri[symbol] < 14400: continue
                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1d&limit=100")
                if not isinstance(klines, list) or len(klines) < 80: continue
                closes = [float(k[4]) for k in klines[:-1]]
                vols = [float(k[7]) for k in klines[:-1]]
                curr_price = closes[-1]
                curr_vol = vols[-1]
                avg_vol = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 1
                if curr_vol <= (avg_vol * 1.1): continue 
                
                macd_res = calculate_macd(closes)
                if not macd_res: continue
                cm, cs, pm, ps = macd_res
                
                if pm <= ps and cm > cs:
                    msg = (f"🚀 <b>MACD SİNYALİ (LONG): {symbol}</b>\n"
                           f"<i>1 Günlük Grafik - MACD Momentum Kırılımı</i>\n\n"
                           f"💰 <b>Giriş:</b> {akilli_fiyat(curr_price)}\n"
                           f"🎯 <b>Hedef:</b> {akilli_fiyat(curr_price * 1.10)} (+10.0%)\n"
                           f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(curr_price * 0.97)} (-3.0%)\n\n"
                           f"📊 <b>MACD Çizgisi:</b> {cm:.5f} | <b>Sinyal Çizgisi:</b> {cs:.5f}\n"
                           f"📈 <b>Hacim Teyidi:</b> Alındı\n\n"
                           f"💡 <b>Açıklama:</b> MACD çizgisinin Sinyal çizgisini yukarı yönlü kesmesine 'Golden Cross' (Altın Kesişim) denir. Bu, satıcıların gücünü kaybettiğini ve yükseliş trendinin başladığını teyit eder.")
                    tg(C, msg); son_macd_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(MACD_ARALIGI)
        except: time.sleep(60)

# =====================================================
# MOTOR 9: TARİHİ ZİRVE / DİP KIRILIMI (ATH / ATL)
# =====================================================
def ath_atl_loop():
    log("ATH_ATL", "Tarihi Zirve/Dip Kırılım Motoru Başladı.")
    time.sleep(135)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in YASAKLI_HISSELER and not any(x in t['symbol'] for x in ['3L', '3S', '5L', '5S']) and float(t.get('quoteVolume', 0)) >= MIN_HACIM_ATH_ATL]
            
            for symbol in adaylar:
                if symbol in son_ath_atl_sinyalleri and time.time() - son_ath_atl_sinyalleri[symbol] < 86400: continue
                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1d&limit=500")
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

                sinyal = None; yon = ""; emoji = ""; stop = 0; hedef = 0; aciklama = ""

                if prev_close >= atl and current_price < atl:
                    sinyal = True; yon = "SHORT"; emoji = "💀"
                    stop = atl * 1.05; hedef = current_price * 0.80
                    aciklama = "Fiyat, geçmişteki en düşük seviyeyi (Tarihi Dibi) hacimle aşağı kırdı. Uzun vadeli bir çöküşün başlangıcı olabilir."
                elif prev_close <= ath and current_price > ath:
                    sinyal = True; yon = "LONG"; emoji = "🚀"
                    stop = ath * 0.95; hedef = current_price * 1.20
                    aciklama = "Fiyat, geçmişteki en yüksek seviyeyi (Tarihi Zirveyi) hacimle yukarı kırdı. Yeni bir boğa trendinin başlangıcı olabilir."

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
                        son_ath_atl_sinyalleri[symbol] = time.time()
                        log("ATH_ATL", f"🔥 SİNYAL: {symbol} ({yon})")
                
                time.sleep(0.1)
            time.sleep(ATH_ATL_ARALIGI)
        except Exception as e:
            log("ATH_ATL", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 10: ICHIMOKU BULUTU
# =====================================================
def ichimoku_loop():
    log("ICHIMOKU", "Zırhlı Ichimoku Bulut Motoru Başladı.")
    time.sleep(75) 
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in YASAKLI_HISSELER and not any(x in t['symbol'] for x in ['3L', '3S', '5L', '5S']) and float(t.get('quoteVolume', 0)) >= MIN_HACIM_ICHIMOKU]
            
            for symbol in adaylar:
                if symbol in son_ichimoku_sinyalleri and time.time() - son_ichimoku_sinyalleri[symbol] < 14400: continue
                
                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1h&limit=100")
                if not isinstance(klines, list) or len(klines) < 80: continue
                
                highs = [float(k[2]) for k in klines]
                lows = [float(k[3]) for k in klines]
                closes = [float(k[4]) for k in klines]
                vols = [float(k[7]) for k in klines]
                
                curr_close = closes[-2]
                prev_close = closes[-3]
                
                avg_vol = sum(vols[-21:-1]) / 20
                curr_vol = vols[-2]
                is_vol_teyit = curr_vol > avg_vol
                
                if not is_vol_teyit: continue 
                
                def calc_tenkan(idx): return (max(highs[idx-8:idx+1]) + min(lows[idx-8:idx+1])) / 2
                def calc_kijun(idx): return (max(highs[idx-25:idx+1]) + min(lows[idx-25:idx+1])) / 2
                
                idx_curr = len(closes) - 2
                
                tenkan_curr = calc_tenkan(idx_curr)
                kijun_curr = calc_kijun(idx_curr)
                
                span_a_curr = (calc_tenkan(idx_curr - 26) + calc_kijun(idx_curr - 26)) / 2
                span_a_prev = (calc_tenkan(idx_curr - 27) + calc_kijun(idx_curr - 27)) / 2
                
                span_b_curr = (max(highs[idx_curr-77:idx_curr-26]) + min(lows[idx_curr-77:idx_curr-26])) / 2
                span_b_prev = (max(highs[idx_curr-78:idx_curr-27]) + min(lows[idx_curr-78:idx_curr-27])) / 2
                
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
                        aciklama = "Fiyat Ichimoku bulutunu yukarı yönlü kırdı. Tenkan-sen'in Kijun-sen'in üzerinde olması kısa vadeli momentumun güçlü olduğunu gösterir."

                elif prev_close >= cloud_bottom_prev and curr_close < cloud_bottom_curr and curr_close < kijun_curr and tenkan_curr < kijun_curr:
                    if cloud_thickness > (curr_close * 0.005):
                        sinyal = True; yon = "SHORT (Bulut Altı Kırılım)"; emoji = "🔻"
                        stop = max(kijun_curr, cloud_top_curr) * 1.01 
                        hedef = curr_close - (cloud_thickness * 2)
                        aciklama = "Fiyat Ichimoku bulutunu aşağı yönlü kırdı. Tenkan-sen'in Kijun-sen'in altında olması satıcıların güçlü olduğunu gösterir."

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
                        son_ichimoku_sinyalleri[symbol] = time.time()
                        log("ICHIMOKU", f"🔥 SİNYAL: {symbol} ({yon})")
                
                time.sleep(0.1)
            time.sleep(ICHIMOKU_ARALIGI)
        except Exception as e:
            log("ICHIMOKU", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 11: GRAFİK FORMASYONLARI (CHART PATTERNS + FINCAN & KULP)
# =====================================================
def chart_patterns_loop():
    log("PATTERN", "Formasyon Avcısı Başladı (İkili Tepe/Dip, OBO, Fincan & Kulp).")
    time.sleep(150)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            
            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in YASAKLI_HISSELER and not any(x in t['symbol'] for x in ['3L', '3S', '5L', '5S']) and float(t.get('quoteVolume', 0)) >= MIN_HACIM_PATTERN]
            
            for symbol in adaylar:
                if symbol in son_chart_pattern_sinyalleri and time.time() - son_chart_pattern_sinyalleri[symbol] < 14400: continue
                
                for tf in ['4h', '1d', '3d']:
                    klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval={tf}&limit=100")
                    if not isinstance(klines, list) or len(klines) < 50: continue
                    
                    highs = [float(k[2]) for k in klines]
                    lows = [float(k[3]) for k in klines]
                    closes = [float(k[4]) for k in klines]
                    vols = [float(k[7]) for k in klines]
                    
                    curr_close = closes[-2] 
                    curr_vol = vols[-2]
                    avg_vol = sum(vols[-22:-2]) / 20 
                    
                    if curr_vol < avg_vol * 1.2: continue

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

                    if not sinyal and len(p_highs) >= 3:
                        p1_idx, p1_price = p_highs[-3]
                        p2_idx, p2_price = p_highs[-2] 
                        p3_idx, p3_price = p_highs[-1] 
                        if p2_price > p1_price and p2_price > p3_price and abs(p1_price - p3_price) / p1_price <= 0.03:
                            neckline = min(lows[p1_idx:p3_idx+1])
                            if curr_close < neckline * 0.99:
                                sinyal = True; formasyon_adi = "Omuz-Baş-Omuz"; yon = "SHORT"; emoji = "💀"
                                stop = p3_price * 1.02
                                hedef = neckline - (p2_price - neckline)

                    if not sinyal and len(p_lows) >= 3:
                        p1_idx, p1_price = p_lows[-3]
                        p2_idx, p2_price = p_lows[-2] 
                        p3_idx, p3_price = p_lows[-1] 
                        if p2_price < p1_price and p2_price < p3_price and abs(p1_price - p3_price) / p1_price <= 0.03:
                            neckline = max(highs[p1_idx:p3_idx+1])
                            if curr_close > neckline * 1.01:
                                sinyal = True; formasyon_adi = "Ters Omuz-Baş-Omuz"; yon = "LONG"; emoji = "🚀"
                                stop = p3_price * 0.98
                                hedef = neckline + (neckline - p2_price)

                    if not sinyal and len(p_highs) >= 2 and len(p_lows) >= 1:
                        left_rim_idx, left_rim = p_highs[-2]
                        cup_low_idx, cup_low = p_lows[-1]
                        right_rim_idx, right_rim = p_highs[-1]
                        if left_rim_idx < cup_low_idx < right_rim_idx and abs(left_rim - right_rim) / left_rim <= 0.05:
                            cup_depth = (left_rim - cup_low) / left_rim
                            if cup_depth > 0.05:
                                if curr_close > right_rim * 1.01:
                                    sinyal = True; formasyon_adi = "Fincan ve Kulp (Cup & Handle)"; yon = "LONG"; emoji = "☕"
                                    neckline = right_rim
                                    hedef = right_rim + (right_rim - cup_low)
                                    stop = cup_low * 1.02

                    if not sinyal and len(p_lows) >= 2 and len(p_highs) >= 1:
                        left_rim_idx, left_rim = p_lows[-2]
                        cup_high_idx, cup_high = p_highs[-1]
                        right_rim_idx, right_rim = p_lows[-1]
                        if left_rim_idx < cup_high_idx < right_rim_idx and abs(left_rim - right_rim) / left_rim <= 0.05:
                            cup_depth = (cup_high - left_rim) / left_rim
                            if cup_depth > 0.05:
                                if curr_close < right_rim * 0.99:
                                    sinyal = True; formasyon_adi = "Ters Fincan ve Kulp"; yon = "SHORT"; emoji = "☕"
                                    neckline = right_rim
                                    hedef = right_rim - (cup_high - right_rim)
                                    stop = cup_high * 0.98

                    if sinyal:
                        hedef_pct = ((hedef - curr_close) / curr_close) * 100
                        stop_pct = ((stop - curr_close) / curr_close) * 100
                        
                        if yon == "SHORT" and hedef >= curr_close: continue
                        if yon == "LONG" and hedef <= curr_close: continue
                        if abs(stop_pct) > 15.0: continue 
                        if abs(hedef_pct) < 1.5 or abs(hedef_pct) > 40.0: continue
                        if yon == "SHORT" and curr_close < hedef * 1.01: continue
                        if yon == "LONG" and curr_close > hedef * 0.99: continue

                        aciklama = "Fiyat, klasik bir dönüş formasyonunu tamamlayarak boyun çizgisini (Neckline) hacimle kırdı."
                        if "OBO" in formasyon_adi: aciklama = "Trendin tepe noktasında oluşan klasik dönüş formasyonu. Boyun çizgisi kırıldı, satıcılar kontrolü ele aldı."
                        if "Dip" in formasyon_adi: aciklama = "Dipte oluşan çift dip formasyonu. Satıcılar tükendi, alıcılar hacimle boyun çizgisini yukarı kırdı."
                        if "Fincan" in formasyon_adi: aciklama = "Uzun süreli bir birikim (fincan) ve ardından gelen kulp (düzeltme) sonrası direnç kırıldı. Güçlü bir boğa/ayı devam trendi başlıyor olabilir."

                        msg = (f"{emoji} <b>GRAFİK FORMASYONU ({yon}): {symbol}</b>\n"
                               f"<i>{tf.upper()} Grafik - {formasyon_adi}</i>\n\n"
                               f"💰 <b>Giriş:</b> {akilli_fiyat(curr_close)}\n"
                               f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                               f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                               f"📊 <b>Boyun Çizgisi:</b> {akilli_fiyat(neckline)}\n"
                               f"📈 <b>Hacim Teyidi:</b> Alındı ({curr_vol/avg_vol:.1f}x)\n\n"
                               f"💡 <b>Açıklama:</b> {aciklama}")
                        
                        if tg(C, msg):
                            son_chart_pattern_sinyalleri[symbol] = time.time()
                            log("PATTERN", f"🔥 SİNYAL: {symbol} ({yon} - {formasyon_adi})")
                            
                    time.sleep(0.1)
            time.sleep(CHART_PATTERN_ARALIGI)
        except Exception as e:
            log("PATTERN", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 12: TOP GAINERS SHORT AVCISI
# =====================================================
def top_gainers_short_loop():
    log("SHORT_GAINERS", "Top Gainers Short Avcısı Başladı (Kâr Realizasyonu).")
    time.sleep(180)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            
            temiz_tickers = [t for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in YASAKLI_HISSELER and not any(x in t['symbol'] for x in ['3L', '3S', '5L', '5S'])]
            temiz_tickers.sort(key=lambda x: float(x['priceChangePercent']), reverse=True)
            top_gainers = temiz_tickers[:5]

            for t in top_gainers:
                symbol = t['symbol']
                change_pct = float(t['priceChangePercent'])
                
                if change_pct < 15: continue
                
                if symbol in son_short_gainers_sinyalleri and time.time() - son_short_gainers_sinyalleri[symbol] < 43200: continue
                
                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=15m&limit=50")
                if not isinstance(klines, list) or len(klines) < 50: continue
                
                closes = [float(k[4]) for k in klines[:-1]]
                highs = [float(k[2]) for k in klines[:-1]]
                vols = [float(k[7]) for k in klines[:-1]]
                
                curr_close = closes[-1]
                curr_vol = vols[-1]
                avg_vol = sum(vols[-21:-1]) / 20
                
                if curr_vol < avg_vol: continue
                
                ema9 = calculate_ema(closes, 9)[-1]
                ema21 = calculate_ema(closes, 21)[-1]
                rsi = calculate_rsi(closes)
                
                recent_high = max(highs[-20:])
                drop_from_top = ((recent_high - curr_close) / recent_high) * 100
                
                if rsi > 55 and ema9 < ema21 and curr_close < closes[-2] and drop_from_top > 2.0:
                    sl = recent_high * 1.01
                    tp1 = curr_close * 0.95
                    tp2 = curr_close * 0.90
                    
                    msg = (f"🩸 <b>TOP GAINERS SHORT FIRSATI: {symbol}</b>\n"
                           f"<i>15m Grafik - Kâr Realizasyonu</i>\n\n"
                           f"⚠️ <i>Coin 24 saatte <b>+{change_pct:.1f}%</b> yükselmiş ve aşırı alım (Overbought) bölgesinde. Balina çıkışı / Short fırsatı başlıyor olabilir!</i>\n\n"
                           f"💰 <b>Giriş (Short):</b> {akilli_fiyat(curr_close)}\n"
                           f"🛑 <b>Stop-Loss (İptal):</b> {akilli_fiyat(sl)}\n"
                           f"🎯 <b>Hedef 1:</b> {akilli_fiyat(tp1)} (-5.0%)\n"
                           f"🎯 <b>Hedef 2:</b> {akilli_fiyat(tp2)} (-10.0%)\n\n"
                           f"📊 <b>RSI:</b> {rsi:.1f} (Aşırı Alımdan Dönüş)\n"
                           f"📉 <b>EMA:</b> 9 Periyot, 21'in altına sarktı (Ölüm Kesişimi)\n"
                           f"🔊 <b>Hacim:</b> Satış hacmi ortalamanın üzerinde\n\n"
                           f"💡 <b>Strateji:</b> En çok yükselenler listesine giren coinlerde, trendin kırıldığı ve kâr satışlarının başladığı noktada kısa pozisyon (Short) alınır.")
                    
                    if tg(C, msg):
                        son_short_gainers_sinyalleri[symbol] = time.time()
                        log("SHORT_GAINERS", f"🔥 SİNYAL: {symbol} (SHORT)")
                        
            time.sleep(SHORT_GAINERS_ARALIGI)
        except Exception as e:
            log("SHORT_GAINERS", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 13: 1 SAATLİK EN ÇOK HAREKET EDENLER (TOP MOVERS)
# =====================================================
def top_movers_loop():
    log("TOP_MOVERS", "Saatlik En Çok Hareket Edenler Raporu Başladı.")
    time.sleep(200)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            
            # Hacmi yüksek olanları al
            temiz_tickers = [t for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in YASAKLI_HISSELER and not any(x in t['symbol'] for x in ['3L', '3S', '5L', '5S']) and float(t.get('quoteVolume', 0)) > MIN_HACIM_MOVERS]
            
            saatlik_veriler = []
            for t in temiz_tickers:
                symbol = t['symbol']
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1h&limit=2")
                if not isinstance(klines, list) or len(klines) < 2: continue
                
                open_price = float(klines[-2][1])
                close_price = float(klines[-2][4])
                
                if open_price == 0: continue
                
                degisim = ((close_price - open_price) / open_price) * 100
                saatlik_veriler.append({'symbol': symbol, 'change': degisim, 'price': close_price})
                
                time.sleep(0.05) # API limitini koru

            if not saatlik_veriler: 
                time.sleep(TOP_MOVERS_ARALIGI)
                continue
                
            saatlik_veriler.sort(key=lambda x: x['change'], reverse=True)
            
            top_3_up = saatlik_veriler[:3]
            top_3_down = saatlik_veriler[-3:]
            top_3_down.reverse() # En çok düşenden en aza doğru sırala

            msg = "⏱️ <b>Son 1 Saatin Hareketlileri</b>\n\n"
            msg += "🟢 <b>En Çok Yükselenler:</b>\n"
            for i, item in enumerate(top_3_up):
                sym = item['symbol'].replace("USDT", "")
                msg += f"{i+1}. <b>{sym}</b> +{item['change']:.2f}% | {akilli_fiyat(item['price'])}\n"
                
            msg += "\n🔴 <b>En Çok Düşenler:</b>\n"
            for i, item in enumerate(top_3_down):
                sym = item['symbol'].replace("USDT", "")
                msg += f"{i+1}. <b>{sym}</b> {item['change']:.2f}% | {akilli_fiyat(item['price'])}\n"
                
            tg(C, msg)
            time.sleep(TOP_MOVERS_ARALIGI)
        except Exception as e:
            log("TOP_MOVERS", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 14: BTC DOMİNANS TAKİBİ (PİYASA YÖNÜ)
# =====================================================
def dominans_loop():
    log("DOMINANS", "Piyasa Yönü ve BTC Dominans Motoru Başladı.")
    time.sleep(240)
    while True:
        try:
            # CoinGecko API'sinden Global verileri çek
            data = gj("https://api.coingecko.com/api/v3/global")
            if not data or 'data' not in data: 
                time.sleep(DOMINANS_ARALIGI)
                continue
                
            btc_d = data['data']['market_cap_percentage']['btc']
            eth_d = data['data']['market_cap_percentage']['eth']
            
            # Dominans yönünü anlamak için basit bir trend kontrolü
            yon = "🟢 Yükselişte"
            aciklama = "Piyasadaki para Bitcoin'e akıyor. Altcoinlerde (küçük coinlerde) dikkatli olunmalı. BTC ağırlıklı işlemler ön planda."
            emoji = "👑"

            # Eğer BTC.D 59'un altındaysa veya düşüş eğilimindeyse genelde altcoin sezonudur.
            if btc_d < 58.0 or (btc_d < 60.0 and eth_d > 12.0):
                yon = "🔴 Düşüşte (Altcoin Sezonu)"
                aciklama = "Bitcoin Dominansı zayıflıyor. Piyasadaki para Altcoinlere kayıyor. Küçük coinlerde sert yükselişler (Pump) görülebilir."
                emoji = "🔥"
                
            msg = (f"{emoji} <b>PİYASA YÖNÜ & DOMİNANS RAPORU</b>\n\n"
                   f"🪙 <b>BTC Dominansı:</b> %{btc_d:.2f}\n"
                   f"💎 <b>ETH Dominansı:</b> %{eth_d:.2f}\n\n"
                   f"📊 <b>Durum:</b> {yon}\n"
                   f"💡 <b>Değerlendirme:</b> {aciklama}")
            
            tg(C, msg)
            time.sleep(DOMINANS_ARALIGI)
        except Exception as e:
            log("DOMINANS", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# RENDER WEB SUNUCUSU
# =====================================================
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Ultimate Futures Bot is running!")
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
    threading.Thread(target=top_gainers_loop, daemon=True).start()
    threading.Thread(target=pump_winner_loop, daemon=True).start()
    
    threading.Thread(target=chartist_loop, daemon=True).start()
    threading.Thread(target=pump_loop, daemon=True).start()
    threading.Thread(target=volatilite_loop, daemon=True).start()
    threading.Thread(target=formasyon_loop, daemon=True).start()
    threading.Thread(target=ema_ribbon_loop, daemon=True).start()
    threading.Thread(target=macd_loop, daemon=True).start()
    threading.Thread(target=ath_atl_loop, daemon=True).start()
    threading.Thread(target=ichimoku_loop, daemon=True).start()
    threading.Thread(target=chart_patterns_loop, daemon=True).start()
    threading.Thread(target=top_gainers_short_loop, daemon=True).start()
    threading.Thread(target=top_movers_loop, daemon=True).start() # YENİ
    threading.Thread(target=dominans_loop, daemon=True).start()   # YENİ
    
    run_web_server()import urllib.request
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
# BINANCE USDT-M FUTURES TELEGRAM SIGNAL BOT
# Yalnızca sinyal üretir; otomatik emir göndermez.
# =====================================================

socket.setdefaulttimeout(20)

T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()
TR_Saati = timezone(timedelta(hours=3))
FAPI = "https://fapi.binance.com"

# Motor aralıkları (saniye)
CHARTIST_ARALIGI = 3600
PUMP_ARALIGI = 60
VOLATILITE_ARALIGI = 60
FORMASYON_ARALIGI = 3600
EMA_RIBBON_ARALIGI = 3600
MACD_ARALIGI = 3600
ATH_ATL_ARALIGI = 3600
ICHIMOKU_ARALIGI = 3600
CHART_PATTERN_ARALIGI = 1800
SHORT_GAINERS_ARALIGI = 300
TOP_MOVERS_ARALIGI = 3600
DOMINANS_ARALIGI = 14400
TOP_GAINERS_ARALIGI = 14400
PUMP_WINNER_ARALIGI = 21600

# Minimum 24 saatlik quote hacimleri (USDT)
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

# Her motor en likit ilk N sözleşmeyi tarar. API limitlerini korur.
MAX_TARAMA = 100
MAX_CHARTIST = 70
MAX_PATTERN = 60
MAX_FORMASYON = 70
MAX_ATH_ATL = 70

YASAKLI_HISSELER = {
    "AAPLUSDT", "TSLAUSDT", "NVDAUSDT", "COINUSDT", "MSTRUSDT",
    "SQQQUSDT", "TQQQUSDT", "SPYUSDT", "QQQUSDT", "AMZNUSDT",
    "GOOGLUSDT", "METAUSDT", "NFLXUSDT", "BABAUSDT", "MSFTUSDT",
    "HOODUSDT", "CRCLUSDT", "XAUUSDT", "XAGUSDT", "PAXGUSDT"
}

# Sinyal hafızaları
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

# Paylaşılan veri önbellekleri ve API hız kontrolü
_api_lock = threading.Lock()
_last_api_call = 0.0
_api_block_until = 0.0
_ticker_cache = {"time": 0.0, "data": None}
_symbol_cache = {"time": 0.0, "symbols": set()}
_kline_cache = {}
_cache_lock = threading.Lock()
API_MIN_GAP = 0.12
KLINE_CACHE_TTL = 45


def simdi():
    return datetime.now(TR_Saati)


def log(prefix, message):
    print(f"[{simdi().strftime('%H:%M:%S')}] [{prefix}] {message}", flush=True)


def akilli_fiyat(p):
    if p is None:
        return "0"
    p = float(p)
    if p >= 1000:
        return f"{p:.2f}"
    if p >= 1:
        return f"{p:.4f}"
    if p >= 0.01:
        return f"{p:.6f}"
    return f"{p:.8f}"


def tg(chat_id, message):
    if not T or not chat_id:
        return False
    try:
        data = urllib.parse.urlencode({
            "chat_id": str(chat_id),
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true"
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
    """Binance JSON isteği. 418/429 durumunda istekleri geçici durdurur."""
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
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        )
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
    """Sadece TRADING durumundaki USDT PERPETUAL sözleşmeleri."""
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
    """Açık olan son mumu çıkarır."""
    return ks[:-1] if isinstance(ks, list) and len(ks) >= 2 else []


def calculate_ema(prices, period):
    if len(prices) < period:
        return []
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for price in prices[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema


def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


def calculate_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow + signal + 2:
        return None, None, None, None
    ef = calculate_ema(prices, fast)
    es = calculate_ema(prices, slow)
    if not ef or not es:
        return None, None, None, None
    offset = slow - fast
    macd_line = [ef[i + offset] - es[i] for i in range(len(es))]
    sig = calculate_ema(macd_line, signal)
    if len(sig) < 2:
        return None, None, None, None
    # MACD çizgisi ve sinyal çizgisini aynı son indekslerde karşılaştır.
    return macd_line[-1], sig[-1], macd_line[-2], sig[-2]


def trap_radar_check(symbol):
    oi = gj(f"{FAPI}/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=2")
    taker = gj(f"{FAPI}/futures/data/takerlongshortRatio?symbol={symbol}&period=5m&limit=1")
    oi_up = False
    buyers = False
    try:
        if isinstance(oi, list) and len(oi) >= 2:
            old = float(oi[-2]["sumOpenInterest"])
            new = float(oi[-1]["sumOpenInterest"])
            oi_up = old > 0 and (new - old) / old * 100 > 0.2
        if isinstance(taker, list) and taker:
            buy = float(taker[-1]["buyVol"])
            sell = float(taker[-1]["sellVol"])
            buyers = buy + sell > 0 and buy / (buy + sell) > 0.55
    except (KeyError, TypeError, ValueError):
        pass
    if oi_up and buyers:
        return "🐋 <b>Açık pozisyon ve alıcı hacmi artıyor</b>"
    if not buyers:
        return "⚠️ <b>Alıcı teyidi yok; pump tuzak olabilir</b>"
    return "⚖️ <b>Kısmi teyit</b>"


def find_pivots(highs, lows, window=3):
    ph, pl = [], []
    for i in range(window, len(highs) - window):
        if highs[i] == max(highs[i-window:i+window+1]):
            ph.append((i, highs[i]))
        if lows[i] == min(lows[i-window:i+window+1]):
            pl.append((i, lows[i]))
    return ph, pl


def cooldown_ok(store, key, seconds):
    return time.time() - store.get(key, 0) >= seconds


def signal_sent(store, key):
    store[key] = time.time()


def make_signal(title, symbol, direction, entry, target, stop, detail=""):
    tp_pct = (target - entry) / entry * 100
    sl_pct = (stop - entry) / entry * 100
    emoji = "🚀" if direction == "LONG" else "🔻"
    return (
        f"{emoji} <b>{title} ({direction}): {symbol}</b>\n\n"
        f"💰 <b>Giriş:</b> {akilli_fiyat(entry)}\n"
        f"🎯 <b>Hedef:</b> {akilli_fiyat(target)} ({tp_pct:+.1f}%)\n"
        f"🛑 <b>Stop:</b> {akilli_fiyat(stop)} ({sl_pct:+.1f}%)\n"
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
# MOTOR 1: TOP GAINERS - FUTURES
# =====================================================
def top_gainers_worker():
    rows = [t for t in futures_tickers() if t["_quote"] > 5_000_000]
    rows.sort(key=lambda x: x["_change"], reverse=True)
    rows = [t for t in rows if t["_change"] > 0][:5]
    if not rows:
        return
    msg = "🔥 <b>Günün En İyi Hareketleri (Binance Futures)</b>\n"
    msg += "<i>24 saatlik yükseliş ve USDT hacmine göre</i>\n\n"
    medals = ["🥇", "🥈", "🥉", "🏅", "⭐"]
    for i, t in enumerate(rows):
        msg += f"{medals[i]} <b>{t['symbol'].replace('USDT','')}</b> +{t['_change']:.2f}% | {akilli_fiyat(t['_price'])}\n"
    tg(C, msg)


def top_gainers_loop():
    safe_loop("TOP_GAINERS", TOP_GAINERS_ARALIGI, top_gainers_worker, 10)


# =====================================================
# MOTOR 2: PUMP WINNER - FUTURES
# =====================================================
def pump_winner_worker():
    rows = [t for t in futures_tickers() if t["_quote"] > 10_000_000]
    rows.sort(key=lambda x: x["_change"], reverse=True)
    if not rows or rows[0]["_change"] < 15:
        return
    t = rows[0]
    ks = tamamlanmis(klines(t["symbol"], "1d", 3))
    if not ks:
        return
    low = min(float(k[3]) for k in ks[-1:])
    high = max(float(k[2]) for k in ks[-1:])
    move = (high - low) / low * 100 if low else 0
    msg = (
        f"🏆 <b>GÜNÜN KAZANANI: {t['symbol']}</b>\n\n"
        f"🔥 Binance USDT-M Futures 24 saatlik en güçlü yükseliş\n"
        f"📈 <b>24s değişim:</b> +{t['_change']:.2f}%\n"
        f"📉 <b>Gün içi dip:</b> {akilli_fiyat(low)}\n"
        f"🚀 <b>Gün içi zirve:</b> {akilli_fiyat(high)}\n"
        f"📊 <b>Dip-zirve hareketi:</b> {move:.1f}%\n"
        f"💰 <b>24s USDT hacmi:</b> {t['_quote']:,.0f}"
    )
    tg(C, msg)


def pump_winner_loop():
    safe_loop("PUMP_WINNER", PUMP_WINNER_ARALIGI, pump_winner_worker, 20)


# =====================================================
# MOTOR 3: CHARTIST - SAATLİK EMA200 LONG / SHORT
# =====================================================
def chartist_worker():
    for t in adaylar(MIN_HACIM_CHARTIST, MAX_CHARTIST):
        symbol = t["symbol"]
        if not cooldown_ok(son_sinyaller, symbol, 86400):
            continue
        ks = tamamlanmis(klines(symbol, "1h", 230))
        if len(ks) < 205:
            continue
        closes = [float(k[4]) for k in ks]
        vols = [float(k[7]) for k in ks]
        ema = calculate_ema(closes, 200)
        if len(ema) < 2:
            continue
        curr, prev = closes[-1], closes[-2]
        e_now, e_prev = ema[-1], ema[-2]
        avg_vol = sum(vols[-21:-1]) / 20
        if avg_vol <= 0 or vols[-1] <= avg_vol:
            continue

        direction = None
        if prev <= e_prev and curr > e_now * 1.01:
            direction = "LONG"
        elif prev >= e_prev and curr < e_now * 0.99:
            direction = "SHORT"
        if not direction:
            continue

        if direction == "LONG":
            stop, target = e_now * 0.97, curr * 1.15
        else:
            stop, target = e_now * 1.03, curr * 0.85
        if abs((stop - curr) / curr * 100) > 12:
            continue
        msg = make_signal(
            "SAATLİK EMA200 KIRILIMI", symbol, direction, curr, target, stop,
            f"\n📊 <b>EMA200:</b> {akilli_fiyat(e_now)}\n"
            f"📈 <b>Hacim:</b> Ortalama hacmin {vols[-1]/avg_vol:.1f}x'i\n"
            f"⏱️ <b>Zaman dilimi:</b> 1 saat"
        )
        if tg(C, msg):
            signal_sent(son_sinyaller, symbol)
            log("CHARTIST", f"{symbol} {direction}")


def chartist_loop():
    safe_loop("CHARTIST", CHARTIST_ARALIGI, chartist_worker, 30)


# =====================================================
# MOTOR 4: PUMP AVCISI
# =====================================================
def pump_worker():
    for t in adaylar(MIN_HACIM_PUMP, MAX_TARAMA):
        symbol = t["symbol"]
        if not cooldown_ok(son_pump_sinyalleri, symbol, 900):
            continue
        ks = tamamlanmis(klines(symbol, "5m", 4))
        if len(ks) < 3:
            continue
        curr_k, prev_k = ks[-1], ks[-2]
        pc, cc = float(prev_k[4]), float(curr_k[4])
        pv, cv = float(prev_k[7]), float(curr_k[7])
        if pc <= 0 or pv <= 0:
            continue
        price_change = (cc - pc) / pc * 100
        vol_change = (cv - pv) / pv * 100
        vol_diff = cv - pv
        if price_change >= 3 and vol_change >= 100 and vol_diff >= 50_000:
            radar = trap_radar_check(symbol)
            msg = (
                f"🚀 <b>PUMP AVCISI: {symbol}</b>\n"
                f"💰 <b>Fiyat:</b> {akilli_fiyat(cc)} ({price_change:+.2f}%)\n"
                f"📊 <b>5m hacim artışı:</b> {vol_change:+.0f}%\n"
                f"💵 <b>Ek hacim:</b> {vol_diff:,.0f} USDT\n\n{radar}\n"
                f"\n⚠️ <i>Bu bir işlem emri değil, hareket uyarısıdır.</i>"
            )
            if tg(C, msg):
                signal_sent(son_pump_sinyalleri, symbol)
                log("PUMP", f"{symbol} pump uyarısı")


def pump_loop():
    safe_loop("PUMP", PUMP_ARALIGI, pump_worker, 45)


# =====================================================
# MOTOR 5: ANORMAL VOLATİLİTE / DUMP
# =====================================================
def volatilite_worker():
    for t in adaylar(MIN_HACIM_VOLATILITE, MAX_TARAMA):
        symbol = t["symbol"]
        if not cooldown_ok(son_volatilite_sinyalleri, symbol, 1800):
            continue
        ks = tamamlanmis(klines(symbol, "15m", 26))
        if len(ks) < 22:
            continue
        curr = ks[-1]
        op, close, volume = float(curr[1]), float(curr[4]), float(curr[7])
        avg = sum(float(k[7]) for k in ks[-21:-1]) / 20
        if op <= 0 or avg <= 0:
            continue
        change = (close - op) / op * 100
        mult = volume / avg
        if abs(change) >= 2.5 and mult >= 3:
            direction = "DUMP (ANI DÜŞÜŞ) 🔻" if change < 0 else "PUMP (ANI YÜKSELİŞ) 🚀"
            msg = (
                f"🚨 <b>ANORMAL VOLATİLİTE UYARISI</b>\n"
                f"<b>Coin:</b> {symbol} (15m)\n"
                f"📉 <b>Hareket:</b> {direction}\n"
                f"📊 <b>Değişim:</b> {change:+.2f}%\n"
                f"📈 <b>Hacim:</b> Ortalama hacmin {mult:.1f}x'i\n"
                f"💰 <b>Kapanış:</b> {akilli_fiyat(close)}"
            )
            if tg(C, msg):
                signal_sent(son_volatilite_sinyalleri, symbol)
                log("VOLATILITE", f"{symbol} {change:+.2f}%")


def volatilite_loop():
    safe_loop("VOLATILITE", VOLATILITE_ARALIGI, volatilite_worker, 55)


# =====================================================
# MOTOR 6: FORMASYON / MSS / LİKİDİTE SWEEP
# =====================================================
def formasyon_worker():
    for t in adaylar(MIN_HACIM_FORMASYON, MAX_FORMASYON):
        symbol = t["symbol"]
        if not cooldown_ok(son_formasyon_sinyalleri, symbol, 86400):
            continue
        k5 = tamamlanmis(klines(symbol, "5m", 105))
        k15 = tamamlanmis(klines(symbol, "15m", 55))
        if len(k5) < 95 or len(k15) < 30:
            continue

        highs = [float(k[2]) for k in k5]
        lows = [float(k[3]) for k in k5]
        closes = [float(k[4]) for k in k5]
        vols = [float(k[7]) for k in k5]
        c15 = [float(k[4]) for k in k15]
        e11, e21 = calculate_ema(c15, 11), calculate_ema(c15, 21)
        if not e11 or not e21:
            continue
        rsi = calculate_rsi(c15)
        price = closes[-1]
        avg_vol = sum(vols[-21:-1]) / 20
        if avg_vol <= 0:
            continue
        vol_ok = vols[-1] > avg_vol * 1.5
        ph, pl = find_pivots(highs, lows)
        if len(ph) < 3 or len(pl) < 3:
            continue
        h, l = ph[-3:], pl[-3:]
        signal = None

        if abs(h[0][1] - h[2][1]) / max(h[0][1], 1e-12) < .03 and price < l[-1][1] and vol_ok and rsi < 50 and e11[-1] < e21[-1]:
            signal = ("SHORT", "Çift Tepe (M)", l[-1][1] - (h[1][1] - l[-1][1]), h[2][1] * 1.02)
        elif abs(l[0][1] - l[2][1]) / max(l[0][1], 1e-12) < .03 and price > h[-1][1] and vol_ok and rsi > 50 and e11[-1] > e21[-1]:
            signal = ("LONG", "Çift Dip (W)", h[-1][1] + (h[-1][1] - l[1][1]), l[2][1] * .98)
        elif highs[-1] > max(highs[-21:-1]) and price < max(highs[-21:-1]) and vol_ok and rsi < 50 and e11[-1] < e21[-1]:
            signal = ("SHORT", "Likidite Sweep", price * .97, highs[-1] * 1.01)
        elif lows[-1] < min(lows[-21:-1]) and price > min(lows[-21:-1]) and vol_ok and rsi > 50 and e11[-1] > e21[-1]:
            signal = ("LONG", "Likidite Sweep", price * 1.03, lows[-1] * .99)

        if not signal:
            hi, lo = max(highs[-21:-1]), min(lows[-21:-1])
            rng = (hi - lo) / lo * 100 if lo else 999
            if rng < 5 and price > hi and vol_ok and rsi > 50:
                signal = ("LONG", "Sıkışma yukarı kırılım", price + (hi-lo), lo * .98)
            elif rng < 5 and price < lo and vol_ok and rsi < 50:
                signal = ("SHORT", "Sıkışma aşağı kırılım", price - (hi-lo), hi * 1.02)

        if not signal:
            continue
        direction, name, target, stop = signal
        tp_pct = (target - price) / price * 100
        sl_pct = (stop - price) / price * 100
        if (direction == "LONG" and (target <= price or stop >= price)) or (direction == "SHORT" and (target >= price or stop <= price)):
            continue
        if abs(sl_pct) > 10 or not 1 <= abs(tp_pct) <= 20:
            continue
        msg = make_signal(
            f"TEYİTLİ DÖNÜŞ - {name}", symbol, direction, price, target, stop,
            f"\n📊 <b>RSI:</b> {rsi:.1f}\n"
            f"📈 <b>Hacim teyidi:</b> {vols[-1]/avg_vol:.1f}x\n"
            f"⏱️ <b>Grafikler:</b> 5m formasyon / 15m trend"
        )
        if tg(C, msg):
            signal_sent(son_formasyon_sinyalleri, symbol)
            log("FORMASYON", f"{symbol} {direction} {name}")


def formasyon_loop():
    safe_loop("FORMASYON", FORMASYON_ARALIGI, formasyon_worker, 60)


# =====================================================
# MOTOR 7: EMA RIBBON - SAATLİK EMA7/25/99/200 LONG / SHORT
# =====================================================
def ema_ribbon_worker():
    for t in adaylar(MIN_HACIM_EMA_RIBBON, MAX_TARAMA):
        symbol = t["symbol"]
        if not cooldown_ok(son_ema_ribbon_sinyalleri, symbol, 86400):
            continue
        # 200 EMA için yeterli kapanmış mum: 230 istenir, açık mum çıkarılır.
        ks = tamamlanmis(klines(symbol, "1h", 230))
        if len(ks) < 205:
            continue
        closes = [float(k[4]) for k in ks]
        vols = [float(k[7]) for k in ks]
        e7, e25 = calculate_ema(closes, 7), calculate_ema(closes, 25)
        e99, e200 = calculate_ema(closes, 99), calculate_ema(closes, 200)
        if not all((e7, e25, e99, e200)):
            continue
        curr = closes[-1]
        avg_vol = sum(vols[-21:-1]) / 20
        if avg_vol <= 0 or vols[-1] <= avg_vol * 1.2:
            continue
        a, b, c, d = e7[-1], e25[-1], e99[-1], e200[-1]
        direction = None
        if curr > a > b > c > d:
            direction = "LONG"
        elif curr < a < b < c < d:
            direction = "SHORT"
        if not direction:
            continue
        if direction == "LONG":
            target, stop = curr * 1.12, b * .97
        else:
            target, stop = curr * .88, b * 1.03
        if (direction == "LONG" and stop >= curr) or (direction == "SHORT" and stop <= curr):
            continue
        msg = make_signal(
            "SAATLİK EMA RIBBON", symbol, direction, curr, target, stop,
            f"\n📊 <b>EMA7:</b> {akilli_fiyat(a)} | <b>EMA25:</b> {akilli_fiyat(b)}\n"
            f"<b>EMA99:</b> {akilli_fiyat(c)} | <b>EMA200:</b> {akilli_fiyat(d)}\n"
            f"📈 <b>Hacim:</b> Ortalama hacmin {vols[-1]/avg_vol:.1f}x'i\n"
            f"⏱️ <b>Zaman dilimi:</b> 1 saat"
        )
        if tg(C, msg):
            signal_sent(son_ema_ribbon_sinyalleri, symbol)
            log("EMA_RIBBON", f"{symbol} {direction}")


def ema_ribbon_loop():
    safe_loop("EMA_RIBBON", EMA_RIBBON_ARALIGI, ema_ribbon_worker, 90)


# =====================================================
# MOTOR 8: MACD - GÜNLÜK LONG / SHORT KESİŞİM
# =====================================================
def macd_worker():
    for t in adaylar(MIN_HACIM_MACD, MAX_TARAMA):
        symbol = t["symbol"]
        if not cooldown_ok(son_macd_sinyalleri, symbol, 14400):
            continue
        ks = tamamlanmis(klines(symbol, "1d", 110))
        if len(ks) < 80:
            continue
        closes = [float(k[4]) for k in ks]
        vols = [float(k[7]) for k in ks]
        avg = sum(vols[-21:-1]) / 20
        if avg <= 0 or vols[-1] <= avg * 1.1:
            continue
        cm, cs, pm, ps = calculate_macd(closes)
        if cm is None:
            continue
        direction = "LONG" if pm <= ps and cm > cs else "SHORT" if pm >= ps and cm < cs else None
        if not direction:
            continue
        price = closes[-1]
        target, stop = (price*1.10, price*.97) if direction == "LONG" else (price*.90, price*1.03)
        msg = make_signal(
            "GÜNLÜK MACD KESİŞİMİ", symbol, direction, price, target, stop,
            f"\n📊 <b>MACD:</b> {cm:.6f}\n<b>Sinyal çizgisi:</b> {cs:.6f}\n"
            f"📈 <b>Hacim:</b> Ortalama hacmin {vols[-1]/avg:.1f}x'i"
        )
        if tg(C, msg):
            signal_sent(son_macd_sinyalleri, symbol)
            log("MACD", f"{symbol} {direction}")


def macd_loop():
    safe_loop("MACD", MACD_ARALIGI, macd_worker, 105)


# =====================================================
# MOTOR 9: ATH / ATL KIRILIMI
# =====================================================
def ath_atl_worker():
    for t in adaylar(MIN_HACIM_ATH_ATL, MAX_ATH_ATL):
        symbol = t["symbol"]
        if not cooldown_ok(son_ath_atl_sinyalleri, symbol, 86400):
            continue
        ks = tamamlanmis(klines(symbol, "1d", 500))
        if len(ks) < 200:
            continue
        highs = [float(k[2]) for k in ks]
        lows = [float(k[3]) for k in ks]
        closes = [float(k[4]) for k in ks]
        vols = [float(k[7]) for k in ks]
        if len(closes) < 22:
            continue
        price, prev = closes[-1], closes[-2]
        # Kırılım seviyesini mevcut mum hariç geçmiş mumlardan hesapla.
        prior_ath = max(highs[:-1])
        prior_atl = min(lows[:-1])
        avg = sum(vols[-21:-1]) / 20
        if avg <= 0 or vols[-1] <= avg * 1.2:
            continue
        direction = "LONG" if prev <= prior_ath and price > prior_ath else "SHORT" if prev >= prior_atl and price < prior_atl else None
        if not direction:
            continue
        if direction == "LONG":
            level, target, stop = prior_ath, price * 1.20, prior_ath * .95
        else:
            level, target, stop = prior_atl, price * .80, prior_atl * 1.05
        msg = make_signal(
            "TARİHİ SEVİYE KIRILIMI", symbol, direction, price, target, stop,
            f"\n📊 <b>Kırılan geçmiş seviye:</b> {akilli_fiyat(level)}\n"
            f"📈 <b>Hacim teyidi:</b> {vols[-1]/avg:.1f}x\n"
            f"⏱️ <b>Zaman dilimi:</b> 1 gün"
        )
        if tg(C, msg):
            signal_sent(son_ath_atl_sinyalleri, symbol)
            log("ATH_ATL", f"{symbol} {direction}")


def ath_atl_loop():
    safe_loop("ATH_ATL", ATH_ATL_ARALIGI, ath_atl_worker, 135)


# =====================================================
# MOTOR 10: ICHIMOKU BULUTU
# =====================================================
def ichimoku_worker():
    for t in adaylar(MIN_HACIM_ICHIMOKU, MAX_TARAMA):
        symbol = t["symbol"]
        if not cooldown_ok(son_ichimoku_sinyalleri, symbol, 14400):
            continue
        ks = tamamlanmis(klines(symbol, "1h", 120))
        if len(ks) < 90:
            continue
        highs = [float(k[2]) for k in ks]
        lows = [float(k[3]) for k in ks]
        closes = [float(k[4]) for k in ks]
        vols = [float(k[7]) for k in ks]
        i = len(ks) - 1
        price, prev = closes[i], closes[i-1]
        avg = sum(vols[-21:-1]) / 20
        if avg <= 0 or vols[-1] <= avg:
            continue

        def mid(period, end):
            start = end - period + 1
            if start < 0:
                return None
            return (max(highs[start:end+1]) + min(lows[start:end+1])) / 2

        tenkan = mid(9, i)
        kijun = mid(26, i)
        tenkan_prev = mid(9, i-1)
        kijun_prev = mid(26, i-1)
        # Bulut, 26 mum ileri ötelenmiş değerlerin bugünkü karşılığıdır.
        a_now = (mid(9, i-26) + mid(26, i-26)) / 2 if i >= 77 else None
        a_prev = (mid(9, i-27) + mid(26, i-27)) / 2 if i >= 78 else None
        b_now = mid(52, i-26) if i >= 77 else None
        b_prev = mid(52, i-27) if i >= 78 else None
        if None in (tenkan, kijun, tenkan_prev, kijun_prev, a_now, a_prev, b_now, b_prev):
            continue
        top, bottom = max(a_now, b_now), min(a_now, b_now)
        ptop, pbottom = max(a_prev, b_prev), min(a_prev, b_prev)
        thickness = top - bottom
        direction = None
        if prev <= ptop and price > top and price > kijun and tenkan > kijun and thickness > price*.005:
            direction = "LONG"
        elif prev >= pbottom and price < bottom and price < kijun and tenkan < kijun and thickness > price*.005:
            direction = "SHORT"
        if not direction:
            continue
        target = price + thickness*2 if direction == "LONG" else price - thickness*2
        stop = min(kijun, bottom)*.99 if direction == "LONG" else max(kijun, top)*1.01
        if (direction == "LONG" and stop >= price) or (direction == "SHORT" and stop <= price):
            continue
        msg = make_signal(
            "ICHIMOKU BULUT KIRILIMI", symbol, direction, price, target, stop,
            f"\n☁️ <b>Kumo:</b> {akilli_fiyat(bottom)} - {akilli_fiyat(top)}\n"
            f"📈 <b>Tenkan:</b> {akilli_fiyat(tenkan)} | <b>Kijun:</b> {akilli_fiyat(kijun)}\n"
            f"📊 <b>Hacim teyidi:</b> {vols[-1]/avg:.1f}x"
        )
        if tg(C, msg):
            signal_sent(son_ichimoku_sinyalleri, symbol)
            log("ICHIMOKU", f"{symbol} {direction}")


def ichimoku_loop():
    safe_loop("ICHIMOKU", ICHIMOKU_ARALIGI, ichimoku_worker, 75)


# =====================================================
# MOTOR 11: GRAFİK FORMASYONLARI
# =====================================================
def chart_patterns_worker():
    for t in adaylar(MIN_HACIM_PATTERN, MAX_PATTERN):
        symbol = t["symbol"]
        if not cooldown_ok(son_chart_pattern_sinyalleri, symbol, 14400):
            continue
        for tf in ("4h", "1d", "3d"):
            ks = tamamlanmis(klines(symbol, tf, 110))
            if len(ks) < 50:
                continue
            highs = [float(k[2]) for k in ks]
            lows = [float(k[3]) for k in ks]
            closes = [float(k[4]) for k in ks]
            vols = [float(k[7]) for k in ks]
            price = closes[-1]
            avg = sum(vols[-21:-1]) / 20
            if avg <= 0 or vols[-1] < avg * 1.2:
                continue
            ph, pl = find_pivots(highs, lows)
            signal = None
            if len(ph) >= 2:
                i1, p1 = ph[-2]
                i2, p2 = ph[-1]
                if 5 <= i2-i1 <= 30 and abs(p1-p2)/p1 <= .02:
                    neckline = min(lows[i1:i2+1])
                    if price < neckline*.99:
                        signal = ("SHORT", "İkili Tepe (M)", neckline-(max(p1,p2)-neckline), max(p1,p2)*1.02, neckline)
            if not signal and len(pl) >= 2:
                i1, p1 = pl[-2]
                i2, p2 = pl[-1]
                if 5 <= i2-i1 <= 30 and abs(p1-p2)/p1 <= .02:
                    neckline = max(highs[i1:i2+1])
                    if price > neckline*1.01:
                        signal = ("LONG", "İkili Dip (W)", neckline+(neckline-min(p1,p2)), min(p1,p2)*.98, neckline)
            if not signal and len(ph) >= 3:
                (i1,p1),(i2,p2),(i3,p3) = ph[-3:]
                if p2 > p1 and p2 > p3 and abs(p1-p3)/p1 <= .03:
                    neckline = min(lows[i1:i3+1])
                    if price < neckline*.99:
                        signal = ("SHORT", "Omuz-Baş-Omuz", neckline-(p2-neckline), p3*1.02, neckline)
            if not signal and len(pl) >= 3:
                (i1,p1),(i2,p2),(i3,p3) = pl[-3:]
                if p2 < p1 and p2 < p3 and abs(p1-p3)/p1 <= .03:
                    neckline = max(highs[i1:i3+1])
                    if price > neckline*1.01:
                        signal = ("LONG", "Ters Omuz-Baş-Omuz", neckline+(neckline-p2), p3*.98, neckline)
            if not signal:
                continue
            direction, name, target, stop, neckline = signal
            tp = (target-price)/price*100
            sl = (stop-price)/price*100
            if (direction == "LONG" and (target <= price or stop >= price)) or (direction == "SHORT" and (target >= price or stop <= price)):
                continue
            if abs(sl) > 15 or not 1.5 <= abs(tp) <= 40:
                continue
            msg = make_signal(
                f"GRAFİK FORMASYONU - {name}", symbol, direction, price, target, stop,
                f"\n📊 <b>Boyun çizgisi:</b> {akilli_fiyat(neckline)}\n"
                f"📈 <b>Hacim:</b> {vols[-1]/avg:.1f}x\n"
                f"⏱️ <b>Zaman dilimi:</b> {tf}"
            )
            if tg(C, msg):
                signal_sent(son_chart_pattern_sinyalleri, symbol)
                log("PATTERN", f"{symbol} {direction} {name}")
            break


def chart_patterns_loop():
    safe_loop("PATTERN", CHART_PATTERN_ARALIGI, chart_patterns_worker, 150)


# =====================================================
# MOTOR 12: TOP GAINERS SHORT AVCISI
# =====================================================
def top_gainers_short_worker():
    rows = [t for t in futures_tickers() if t["_quote"] >= MIN_HACIM_PUMP]
    rows.sort(key=lambda x: x["_change"], reverse=True)
    for t in [x for x in rows if x["_change"] >= 15][:5]:
        symbol = t["symbol"]
        if not cooldown_ok(son_short_gainers_sinyalleri, symbol, 43200):
            continue
        ks = tamamlanmis(klines(symbol, "15m", 55))
        if len(ks) < 30:
            continue
        closes = [float(k[4]) for k in ks]
        highs = [float(k[2]) for k in ks]
        vols = [float(k[7]) for k in ks]
        e9, e21 = calculate_ema(closes, 9), calculate_ema(closes, 21)
        if not e9 or not e21:
            continue
        avg = sum(vols[-21:-1]) / 20
        if avg <= 0 or vols[-1] < avg:
            continue
        rsi = calculate_rsi(closes)
        recent_high = max(highs[-20:])
        drop = (recent_high-closes[-1])/recent_high*100
        if rsi > 55 and e9[-1] < e21[-1] and closes[-1] < closes[-2] and drop > 2:
            price = closes[-1]
            msg = (
                f"🩸 <b>TOP GAINERS SHORT ADAYI: {symbol}</b>\n"
                f"<i>Binance Futures | 15m kâr realizasyonu takibi</i>\n\n"
                f"📈 <b>24s yükseliş:</b> +{t['_change']:.1f}%\n"
                f"💰 <b>Gözlem fiyatı:</b> {akilli_fiyat(price)}\n"
                f"🛑 <b>İptal seviyesi:</b> {akilli_fiyat(recent_high*1.01)}\n"
                f"🎯 <b>Hedef 1:</b> {akilli_fiyat(price*.95)}\n"
                f"🎯 <b>Hedef 2:</b> {akilli_fiyat(price*.90)}\n"
                f"📊 <b>RSI:</b> {rsi:.1f} | EMA9, EMA21 altında\n"
                f"📉 <b>Zirveden geri çekilme:</b> {drop:.2f}%\n"
                f"\n⚠️ <i>Bu bir short emri değil, olası dönüş uyarısıdır.</i>"
            )
            if tg(C, msg):
                signal_sent(son_short_gainers_sinyalleri, symbol)
                log("SHORT_GAINERS", f"{symbol} SHORT adayı")


def top_gainers_short_loop():
    safe_loop("SHORT_GAINERS", SHORT_GAINERS_ARALIGI, top_gainers_short_worker, 180)


# =====================================================
# MOTOR 13: TOP MOVERS - FUTURES SAATLİK HAREKET
# =====================================================
def top_movers_worker():
    rows = [t for t in futures_tickers() if t["_quote"] > MIN_HACIM_MOVERS]
    rows.sort(key=lambda x: x["_quote"], reverse=True)
    # Tüm sözleşmeler için tek tek mum istemek yerine en likit 80 sözleşme.
    results = []
    for t in rows[:80]:
        ks = tamamlanmis(klines(t["symbol"], "1h", 3))
        if not ks:
            continue
        op, close = float(ks[-1][1]), float(ks[-1][4])
        if op > 0:
            results.append({
                "symbol": t["symbol"],
                "change": (close-op)/op*100,
                "price": close
            })
    if not results:
        return
    results.sort(key=lambda x: x["change"], reverse=True)
    up, down = results[:3], list(reversed(results[-3:]))
    msg = "⏱️ <b>Binance Futures - Son Tamamlanan 1 Saat</b>\n\n🟢 <b>En Çok Yükselenler:</b>\n"
    for i, x in enumerate(up, 1):
        msg += f"{i}. <b>{x['symbol'].replace('USDT','')}</b> {x['change']:+.2f}% | {akilli_fiyat(x['price'])}\n"
    msg += "\n🔴 <b>En Çok Düşenler:</b>\n"
    for i, x in enumerate(down, 1):
        msg += f"{i}. <b>{x['symbol'].replace('USDT','')}</b> {x['change']:+.2f}% | {akilli_fiyat(x['price'])}\n"
    tg(C, msg)


def top_movers_loop():
    safe_loop("TOP_MOVERS", TOP_MOVERS_ARALIGI, top_movers_worker, 200)


# =====================================================
# MOTOR 14: FUTURES PİYASA GENİŞLİĞİ (DOMİNANS YERİNE)
# Not: Binance Futures verisi gerçek market-cap dominansı vermez.
# =====================================================
def dominans_worker():
    rows = futures_tickers()
    if not rows:
        return
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
        detail = "USDT sürekli vadeli sözleşmelerin çoğu yükselişte; yükselenlerin hacmi de daha yüksek."
    elif down_pct >= 60 and down_vol > up_vol:
        status = "🔴 GENEL PİYASA NEGATİF"
        detail = "Sözleşmelerin çoğu düşüşte; düşenlerin hacmi daha yüksek."
    else:
        status = "🟡 KARIŞIK / YATAY PİYASA"
        detail = "Yükseliş ve düşüş tarafında belirgin bir piyasa üstünlüğü yok."
    msg = (
        "🌐 <b>BINANCE FUTURES PİYASA GENİŞLİĞİ</b>\n\n"
        f"🟢 <b>Yükselen sözleşmeler:</b> %{up_pct:.1f} ({len(positive)})\n"
        f"🔴 <b>Düşen sözleşmeler:</b> %{down_pct:.1f} ({len(negative)})\n"
        f"💵 <b>Yükselenlerin 24s hacmi:</b> {up_vol:,.0f} USDT\n"
        f"💵 <b>Düşenlerin 24s hacmi:</b> {down_vol:,.0f} USDT\n"
    )
    msg += f"₿ <b>BTC 24s:</b> {btc['_change']:+.2f}%\n" if btc else "₿ <b>BTC:</b> veri yok\n"
    if eth:
        msg += f"♦️ <b>ETH 24s:</b> {eth['_change']:+.2f}%\n"
    msg += f"\n📊 <b>Durum:</b> {status}\n💡 {detail}\n\n<i>Bu gösterge piyasa genişliğidir; BTC/ETH piyasa değeri dominansı değildir.</i>"
    tg(C, msg)


def dominans_loop():
    safe_loop("PIYASA_GENISLIGI", DOMINANS_ARALIGI, dominans_worker, 240)


# =====================================================
# RENDER WEB SUNUCUSU
# =====================================================
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"Binance USDT-M Futures Signal Bot is running."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string, *args):
        return


def run_web_server():
    port = int(os.environ.get("PORT", "10000"))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    log("SİSTEM", f"WEB SUNUCUSU BAŞLADI - PORT: {port}")
    server.serve_forever()


# =====================================================
# BAŞLAT
# =====================================================
if __name__ == "__main__":
    log("SİSTEM", "Binance USDT-M Futures sinyal botu başlatılıyor.")
    if not T:
        log("UYARI", "BOT_TOKEN ortam değişkeni boş. Telegram bildirimleri gönderilemez.")
    if not C:
        log("UYARI", "CHAT_ID boş.")

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
        log("RENDER", f"Web sunucusu başlatılamadı: {e}")
        log("RENDER", traceback.format_exc())
        # Ana süreç kapanmasın; Render loglarında hata görülebilsin.
        while True:
            time.sleep(60)

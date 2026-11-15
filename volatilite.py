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
PUMP_ARALIGI = 60          # 1 Dakika
FORMASYON_ARALIGI = 3600   # 1 Saat
MACD_ARALIGI = 3600        # 1 Saat
MIDAS_SETUP_ARALIGI = 7200 # 2 Saat
BOLLINGER_ARALIGI = 3600   # 1 Saat
ICHIMOKU_ARALIGI = 3600    # 1 Saat
SMA200_ARALIGI = 3600      # 1 Saat

# FİLTRELER
MIN_HACIM_PUMP = 5_000_000     
MIN_HACIM_FORMASYON = 10_000_000 
MIN_HACIM_MACD = 10_000_000 
MIN_HACIM_MIDAS_SETUP = 15_000_000 
MIN_HACIM_BOLLINGER = 10_000_000
MIN_HACIM_ICHIMOKU = 10_000_000
MIN_HACIM_SMA200 = 15_000_000

# SİNYAL HAFIZALARI
son_formasyon_sinyalleri = {}
son_pump_sinyalleri = {}
son_macd_sinyalleri = {}
son_midas_setup_sinyalleri = {}
son_bollinger_sinyalleri = {}
son_ichimoku_sinyalleri = {}
son_sma200_sinyalleri = {}

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
    except: return ""

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
                msg = "🔥 <b>24 saatteki en iyi hareketler</b>\n"
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
# MOTOR 2: PUMP AVCISI (5 DAKİKALIK)
# =====================================================
def pump_loop():
    log("PUMP", "Anlık Pump Avcısı Başladı.")
    time.sleep(30)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_PUMP]:
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
                           f"💡 <b>Açıklama:</b> 5 dakikalık grafikte aniden sert bir hacim ve fiyat patlaması yaşandı. Trap Radar (OI ve CVD) sayesinde bu hareketin arkasında gerçek balinaların mı yoksa küçük yatırımcıyı çekmek isteyen sahte botların mı olduğunu analiz ettik.")
                    tg(C, msg); son_pump_sinyalleri[symbol] = time.time()
                time.sleep(0.05)
            time.sleep(PUMP_ARALIGI)
        except: time.sleep(60)

# =====================================================
# MOTOR 3: FORMASYON (4 SAATLİK VE GÜNLÜK)
# =====================================================
def find_pivots(highs, lows, window=3):
    pivot_highs = []
    pivot_lows = []
    for i in range(window, len(highs) - window):
        if highs[i] == max(highs[i-window:i+window+1]): pivot_highs.append((i, highs[i]))
        if lows[i] == min(lows[i-window:i+window+1]): pivot_lows.append((i, lows[i]))
    return pivot_highs, pivot_lows

def formasyon_loop():
    log("FORMASYON", "Formasyon Motoru Başladı.")
    time.sleep(45)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_FORMASYON]:
                if symbol in son_formasyon_sinyalleri and time.time() - son_formasyon_sinyalleri[symbol] < 86400: continue
                
                for tf in ['4h', '1d']:
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=100")
                    if not isinstance(klines, list) or len(klines) < 100: continue
                    
                    highs = [float(k[2]) for k in klines]
                    lows = [float(k[3]) for k in klines]
                    closes = [float(k[4]) for k in klines]
                    vols = [float(k[7]) for k in klines]
                    
                    current_price = closes[-1]
                    ema99 = calculate_ema(closes, 99)[-1] 
                    avg_vol = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 1
                    current_vol = vols[-1]
                    is_vol_teyit = current_vol > (avg_vol * 1.2)
                    
                    p_highs, p_lows = find_pivots(highs, lows)
                    if len(p_highs) < 3 or len(p_lows) < 3: continue
                    
                    last_h = p_highs[-3:]
                    last_l = p_lows[-3:]
                    
                    sinyal = None; formasyon_adi = ""; yon = ""; emoji = ""; hedef = 0; stop = 0; aciklama = ""

                    # ÇİFT TEPE (M)
                    if abs(last_h[0][1] - last_h[2][1]) / last_h[0][1] < 0.03 and current_price < last_l[-1][1] and current_price < ema99 and is_vol_teyit:
                        sinyal = True; formasyon_adi = "Çift Tepe (M)"; yon = "SHORT"; emoji = "🔻"
                        hedef = last_l[-1][1] - (last_h[1][1] - last_l[-1][1]); stop = last_h[2][1] * 1.02
                        aciklama = "Çift Tepe (M) formasyonu, alıcıların gücünü kaybettiğini ve satıcıların kontrolü ele geçirdiğini gösterir. İkinci tepe onaylandıktan sonra boyun çizgisi (destek) kırıldı."
                    
                    # ÇİFT DİP (W)
                    elif abs(last_l[0][1] - last_l[2][1]) / last_l[0][1] < 0.03 and current_price > last_h[-1][1] and current_price > ema99 and is_vol_teyit:
                        sinyal = True; formasyon_adi = "Çift Dip (W)"; yon = "LONG"; emoji = "🚀"
                        hedef = last_h[-1][1] + (last_h[-1][1] - last_l[1][1]); stop = last_l[2][1] * 0.98
                        aciklama = "Çift Dip (W) formasyonu, satıcıların tükendiğini ve alıcıların güçlendiğini gösterir. İki kez test edilen dip bölgesinden sonra boyun çizgisi (direnç) kırıldı."

                    # OMUZ-BAŞ-OMUZ (OBO)
                    elif last_h[1][1] > last_h[0][1] and last_h[1][1] > last_h[2][1] and abs(last_h[0][1] - last_h[2][1]) / last_h[0][1] < 0.05 and current_price < last_l[-1][1] and current_price < ema99 and is_vol_teyit:
                        sinyal = True; formasyon_adi = "Omuz-Baş-Omuz"; yon = "SHORT"; emoji = "🔻"
                        hedef = last_l[-1][1] - (last_h[1][1] - last_l[-1][1]); stop = last_h[2][1] * 1.02
                        aciklama = "Omuz-Baş-Omuz formasyonu, klasik bir trend dönüş sinyalidir. Ortadaki 'Baş' bölgesi en yüksek seviyeyi görürken, son 'Omuz' bölgesi alıcıların zayıfladığını gösterir."

                    # TERS OMUZ-BAŞ-OMUZ
                    elif last_l[1][1] < last_l[0][1] and last_l[1][1] < last_l[2][1] and abs(last_l[0][1] - last_l[2][1]) / last_l[0][1] < 0.05 and current_price > last_h[-1][1] and current_price > ema99 and is_vol_teyit:
                        sinyal = True; formasyon_adi = "Ters Omuz-Baş-Omuz"; yon = "LONG"; emoji = "🚀"
                        hedef = last_h[-1][1] + (last_h[-1][1] - last_l[1][1]); stop = last_l[2][1] * 0.98
                        aciklama = "Ters Omuz-Baş-Omuz formasyonu, güçlü bir boğa (yükseliş) dönüş sinyalidir. Satıcıların artık dip yapamadığını ve alıcıların piyasaya hakim olduğunu gösterir."

                    # BAYRAK / SIKIŞMA
                    if not sinyal:
                        last_20_high = max(highs[-20:])
                        last_20_low = min(lows[-20:])
                        range_pct = ((last_20_high - last_20_low) / last_20_low) * 100
                        
                        if range_pct < 15 and current_price > last_20_high and current_price > ema99 and is_vol_teyit:
                            sinyal = True; formasyon_adi = "Sıkışma ve Yukarı Kırılım (Bayrak)"; yon = "LONG"; emoji = "🚀"
                            hedef = current_price + (last_20_high - last_20_low); stop = last_20_low * 0.98
                            aciklama = "Fiyat uzun süredir dar bir bantta sıkışıyordu (Bayrak/Kama). Hacimle yukarı kırılım gerçekleşti, sıkışma boyu kadar hedef bekleniyor."
                        elif range_pct < 15 and current_price < last_20_low and current_price < ema99 and is_vol_teyit:
                            sinyal = True; formasyon_adi = "Sıkışma ve Aşağı Kırılım"; yon = "SHORT"; emoji = "🔻"
                            hedef = current_price - (last_20_high - last_20_low); stop = last_20_high * 1.02
                            aciklama = "Fiyat dar bir bantta sıkıştıktan sonra desteği aşağı kırdı. Sıkışma boyu kadar aşağı yönlü bir hareket bekleniyor."

                    if sinyal:
                        hedef_pct = ((hedef - current_price) / current_price) * 100
                        stop_pct = ((stop - current_price) / current_price) * 100
                        
                        if yon == "LONG" and hedef > current_price and stop < current_price: pass
                        elif yon == "SHORT" and hedef < current_price and stop > current_price: pass
                        else: continue

                        msg = (f"{emoji} <b>FORMASYON SİNYALİ ({yon}): {symbol}</b>\n"
                               f"<i>{tf.upper()} Grafik - {formasyon_adi}</i>\n\n"
                               f"💰 <b>Giriş (Kırılım):</b> {akilli_fiyat(current_price)}\n"
                               f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                               f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                               f"📊 <b>Trend Filtresi:</b> {'Yükseliş' if current_price > ema99 else 'Düşüş'}\n"
                               f"📊 <b>Hacim Teyidi:</b> Alındı ({current_vol/avg_vol:.1f}x)\n\n"
                               f"💡 <b>Açıklama:</b> {aciklama}")
                        
                        if tg(C, msg):
                            son_formasyon_sinyalleri[symbol] = time.time()
                            log("FORMASYON", f"🔥 SİNYAL: {symbol} ({yon} - {formasyon_adi})")
                            break
                    
                time.sleep(0.1)
            time.sleep(FORMASYON_ARALIGI)
        except Exception as e:
            log("FORMASYON", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 4: MACD (GÜNLÜK)
# =====================================================
def macd_loop():
    log("MACD", "MACD Günlük Motoru Başladı.")
    time.sleep(105)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_MACD]:
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
                           f"<i>1 Günlük Grafik</i>\n\n"
                           f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> MACD indikatörü, Sinyal çizgisini aşağıdan yukarı yönlü kesti (Golden Cross). Momentumun satıcılardan alıcılara geçtiğini gösterir.")
                    if tg(C, msg): son_macd_sinyalleri[symbol] = time.time()
                elif pm >= ps and cm < cs:
                    msg = (f"💀 <b>MACD SAT SİNYALİ (SHORT): {symbol}</b>\n"
                           f"<i>1 Günlük Grafik</i>\n\n"
                           f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> MACD indikatörü, Sinyal çizgisini yukarıdan aşağı yönlü kesti (Death Cross). Momentumun alıcılardan satıcılara geçtiğini gösterir.")
                    if tg(C, msg): son_macd_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(MACD_ARALIGI)
        except: time.sleep(60)

# =====================================================
# MOTOR 5: MIDAS SETUP (4 SAATLİK)
# =====================================================
def midas_setup_loop():
    log("MIDAS_SETUP", "Midas Setup (4 Saatlik) Motoru Başladı.")
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
                    msg = (f"⚡ <b>{symbol} SETUP (LONG)</b>\n<i>4 Saatlik Grafik</i>\n\n"
                           f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> 4 saatlik grafikte son 20 periyodun (yaklaşık 3 günün) direnci hacimle aşıldı. Kısa ve orta vadeli traderların dikkatini çeken, hızlı hareket potansiyeli yüksek bir kırılımdır.")
                    if tg(C, msg): son_midas_setup_sinyalleri[symbol] = time.time()
                elif curr < min(lows[-20:]) and is_vol:
                    msg = (f"⚡ <b>{symbol} SETUP (SHORT)</b>\n<i>4 Saatlik Grafik</i>\n\n"
                           f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> 4 saatlik grafikte son 20 periyodun en düşük desteği hacimle aşağı kırıldı. Satış baskısının arttığını ve hızlı bir düşüşün başlayabileceğini gösterir.")
                    if tg(C, msg): son_midas_setup_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(MIDAS_SETUP_ARALIGI)
        except: time.sleep(60)

# =====================================================
# MOTOR 6: BOLLINGER (HAFTALIK)
# =====================================================
def bollinger_loop():
    log("BOLLINGER", "Bollinger Haftalık Motoru Başladı.")
    time.sleep(150)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_BOLLINGER]:
                if symbol in son_bollinger_sinyalleri and time.time() - son_bollinger_sinyalleri[symbol] < 86400: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1w&limit=50")
                if not isinstance(klines, list) or len(klines) < 30: continue
                
                closes = [float(k[4]) for k in klines[:-1]]
                curr = closes[-1]
                upper, mid, lower = calculate_bollinger(closes)
                if not upper or not lower: continue
                
                if curr > upper and closes[-2] <= upper:
                    msg = (f"🔥 <b>BOLLINGER ÜST BANT (LONG): {symbol}</b>\n<i>1 Haftalık Grafik</i>\n\n"
                           f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> Fiyat, Bollinger üst bandını yukarı yönlü kırdı. Volatilitenin arttığını ve güçlü bir alım dalgasının piyasaya giriş yaptığını gösterir.")
                    if tg(C, msg): son_bollinger_sinyalleri[symbol] = time.time()
                elif curr < lower and closes[-2] >= lower:
                    msg = (f"🩸 <b>BOLLINGER ALT BANT (SHORT): {symbol}</b>\n<i>1 Haftalık Grafik</i>\n\n"
                           f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> Fiyat, Bollinger alt bandını aşağı yönlü kırdı. Aşırı satım bölgesine girildiğini veya sert bir düşüş dalgasının başladığını gösterebilir.")
                    if tg(C, msg): son_bollinger_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(BOLLINGER_ARALIGI)
        except Exception as e: time.sleep(60)

# =====================================================
# MOTOR 7: ICHIMOKU (HAFTALIK)
# =====================================================
def ichimoku_loop():
    log("ICHIMOKU", "Ichimoku Haftalık Motoru Başladı.")
    time.sleep(160)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_ICHIMOKU]:
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
                    msg = (f"☁️ <b>ICHIMOKU (LONG): {symbol}</b>\n<i>1 Haftalık Grafik</i>\n\n"
                           f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> Fiyat, Ichimoku Bulutunun (Kumo) üzerine çıktı. Piyasa 'Boğa' bölgesine girdi.")
                    if tg(C, msg): son_ichimoku_sinyalleri[symbol] = time.time()
                elif curr < cloud_bottom and prev >= cloud_bottom:
                    msg = (f"☁️ <b>ICHIMOKU (SHORT): {symbol}</b>\n<i>1 Haftalık Grafik</i>\n\n"
                           f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> Fiyat, Ichimoku Bulutunun altına sarktı. Piyasa 'Ayı' bölgesine girdi, düşüş trendi başlayabilir.")
                    if tg(C, msg): son_ichimoku_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(ICHIMOKU_ARALIGI)
        except Exception as e: time.sleep(60)

# =====================================================
# MOTOR 8: SMA200 (3 GÜNLÜK)
# =====================================================
def sma200_loop():
    log("SMA200", "SMA200 (3 Günlük) Motoru Başladı.")
    time.sleep(170)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_SMA200]:
                if symbol in son_sma200_sinyalleri and time.time() - son_sma200_sinyalleri[symbol] < 86400: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=3d&limit=250")
                if not isinstance(klines, list) or len(klines) < 205: continue
                
                closes = [float(k[4]) for k in klines[:-1]]; curr, prev = closes[-1], closes[-2]
                sma200 = calculate_sma(closes, 200)
                if not sma200 or len(sma200) < 3: continue
                c_sma = sma200[-1]; p_sma = sma200[-2]
                
                if prev <= p_sma and curr > c_sma and curr > c_sma * 1.005:
                    msg = (f"🚀 <b>SMA200 YUKARI KIRILIM (LONG): {symbol}</b>\n<i>3 Günlük Grafik</i>\n\n"
                           f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> Fiyat, 200 periyotluk basit ortalamanın (SMA200) üzerine çıktı. Kurumsal yatırımcıların maliyet çizgisi aşıldı, 'Al' bölgesi.")
                    if tg(C, msg): son_sma200_sinyalleri[symbol] = time.time()
                elif prev >= p_sma and curr < c_sma and curr < c_sma * 0.995:
                    msg = (f"💀 <b>SMA200 AŞAĞI KIRILIM (SHORT): {symbol}</b>\n<i>3 Günlük Grafik</i>\n\n"
                           f"💰 <b>Giriş Fiyatı:</b> {akilli_fiyat(curr)}\n\n"
                           f"💡 <b>Açıklama:</b> Fiyat, 200 periyotluk ortalamanın (SMA200) altına sarktı. Kurumsalların bölgeden çıktığını, 'Sat' bölgesine geçildiğini gösterir.")
                    if tg(C, msg): son_sma200_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(SMA200_ARALIGI)
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
        "Bot dünkü çalışan haline geri döndürüldü. 🚀\n"
        "Hafıza dosyası ve yeni mum filtreleri kaldırıldı. Sadece zaman bazlı cooldown aktif.\n\n"
        "Aktif Motorlar:\n"
        "• Midas Flow Rapor (4s)\n"
        "• Formasyon (4s, 1g)\n"
        "• Midas Setup (4s)\n"
        "• Pump Avcısı (5dk)\n"
        "• MACD (1g)\n"
        "• Bollinger (1h)\n"
        "• Ichimoku (1h)\n"
        "• SMA200 (3g)"
    )
    if tg(C, baslangic_mesaji):
        log("SİSTEM", "✅ Başlangıç mesajı başarıyla Telegram'a gönderildi.")
    else:
        log("SİSTEM", "❌ HATA: Telegram'a mesaj gönderilemedi! Lütfen BOT_TOKEN ve CHAT_ID ayarlarını kontrol edin.")
    
    # Motorları başlat
    threading.Thread(target=midas_flow_loop, daemon=True).start()
    threading.Thread(target=pump_loop, daemon=True).start()
    threading.Thread(target=formasyon_loop, daemon=True).start()
    threading.Thread(target=macd_loop, daemon=True).start()
    threading.Thread(target=midas_setup_loop, daemon=True).start()
    threading.Thread(target=bollinger_loop, daemon=True).start()
    threading.Thread(target=ichimoku_loop, daemon=True).start()
    threading.Thread(target=sma200_loop, daemon=True).start()
    
    # Web sunucusunu başlat
    run_web_server()

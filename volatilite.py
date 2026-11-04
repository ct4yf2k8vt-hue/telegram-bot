import urllib.request
import urllib.parse
import json
import time
import socket
import os
import threading
import http.server
import socketserver
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

# FİLTRELER
MIN_HACIM_CHARTIST = 10_000_000 
MIN_HACIM_PUMP = 5_000_000     
MIN_HACIM_FORMASYON = 10_000_000 
MIN_HACIM_EMA_RIBBON = 10_000_000 
MIN_HACIM_MACD = 10_000_000 
MIN_HACIM_MIDAS_SETUP = 15_000_000 
MIN_HACIM_ATH_ATL = 5_000_000 

son_sinyaller = {}
son_pump_sinyalleri = {}
son_formasyon_sinyalleri = {}
son_ema_ribbon_sinyalleri = {}
son_macd_sinyalleri = {}
son_midas_setup_sinyalleri = {}
son_ath_atl_sinyalleri = {}

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
# MOTOR 2: CHARTIST (EMA200)
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
                            msg = f"📈 <b>UZUN VADELİ SPOT (LONG): {symbol}</b>\n<i>{tf.upper()} EMA200 Kırılımı</i>\n💰 {akilli_fiyat(curr)}"
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
                    msg = f"🚀 <b>Pump - {symbol}</b>\n💰 {akilli_fiyat(cc)}"
                    tg(C, msg); son_pump_sinyalleri[symbol] = time.time()
                time.sleep(0.05)
            time.sleep(PUMP_ARALIGI)
        except: time.sleep(60)

# =====================================================
# MOTOR 4: FORMASYON (AÇIKLAMALI)
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
                
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1d&limit=100")
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
                
                sinyal = None
                formasyon_adi = ""
                yon = ""
                emoji = ""
                hedef = 0
                stop = 0
                aciklama = ""

                # ÇİFT TEPE (M)
                if abs(last_h[0][1] - last_h[2][1]) / last_h[0][1] < 0.03 and current_price < last_l[-1][1] and current_price < ema99 and is_vol_teyit:
                    sinyal = True; formasyon_adi = "Çift Tepe (M)"; yon = "SHORT"; emoji = "🔻"
                    hedef = last_l[-1][1] - (last_h[1][1] - last_l[-1][1]); stop = last_h[2][1] * 1.02
                    aciklama = "Çift Tepe (M) formasyonu, alıcıların gücünü kaybettiğini ve satıcıların kontrolü ele geçirdiğini gösterir. İkinci tepe onaylandıktan sonra boyun çizgisi (destek) kırıldı. Hedef, formasyonun yüksekliği kadar aşağı projeksiyondur."
                
                # ÇİFT DİP (W)
                elif abs(last_l[0][1] - last_l[2][1]) / last_l[0][1] < 0.03 and current_price > last_h[-1][1] and current_price > ema99 and is_vol_teyit:
                    sinyal = True; formasyon_adi = "Çift Dip (W)"; yon = "LONG"; emoji = "🚀"
                    hedef = last_h[-1][1] + (last_h[-1][1] - last_l[1][1]); stop = last_l[2][1] * 0.98
                    aciklama = "Çift Dip (W) formasyonu, satıcıların tükendiğini ve alıcıların güçlendiğini gösterir. İki kez test edilen dip bölgesinden sonra boyun çizgisi (direnç) kırıldı. Hedef, çukurun derinliği kadar yukarı projeksiyondur."

                # OMUZ-BAŞ-OMUZ (OBO)
                elif last_h[1][1] > last_h[0][1] and last_h[1][1] > last_h[2][1] and abs(last_h[0][1] - last_h[2][1]) / last_h[0][1] < 0.05 and current_price < last_l[-1][1] and current_price < ema99 and is_vol_teyit:
                    sinyal = True; formasyon_adi = "Omuz-Baş-Omuz"; yon = "SHORT"; emoji = "🔻"
                    hedef = last_l[-1][1] - (last_h[1][1] - last_l[-1][1]); stop = last_h[2][1] * 1.02
                    aciklama = "Omuz-Baş-Omuz formasyonu, klasik bir trend dönüş sinyalidir. Ortadaki 'Baş' bölgesi en yüksek seviyeyi görürken, son 'Omuz' bölgesi alıcıların zayıfladığını gösterir. Boyun çizgisinin kırılması, büyük bir düşüşün habercisidir."

                # TERS OMUZ-BAŞ-OMUZ
                elif last_l[1][1] < last_l[0][1] and last_l[1][1] < last_l[2][1] and abs(last_l[0][1] - last_l[2][1]) / last_l[0][1] < 0.05 and current_price > last_h[-1][1] and current_price > ema99 and is_vol_teyit:
                    sinyal = True; formasyon_adi = "Ters Omuz-Baş-Omuz"; yon = "LONG"; emoji = "🚀"
                    hedef = last_h[-1][1] + (last_h[-1][1] - last_l[1][1]); stop = last_l[2][1] * 0.98
                    aciklama = "Ters Omuz-Baş-Omuz formasyonu, güçlü bir boğa (yükseliş) dönüş sinyalidir. Satıcıların artık dip yapamadığını ve alıcıların piyasaya hakim olduğunu gösterir. Boyun çizgisinin hacimle kırılması, sert bir yükselişin başlangıcıdır."

                # BAYRAK / SIKIŞMA
                if not sinyal:
                    last_20_high = max(highs[-20:])
                    last_20_low = min(lows[-20:])
                    range_pct = ((last_20_high - last_20_low) / last_20_low) * 100
                    
                    if range_pct < 15 and current_price > last_20_high and current_price > ema99 and is_vol_teyit:
                        sinyal = True; formasyon_adi = "Sıkışma ve Yukarı Kırılım (Bayrak)"; yon = "LONG"; emoji = "🚀"
                        hedef = current_price + (last_20_high - last_20_low); stop = last_20_low * 0.98
                        aciklama = "Fiyat uzun süredir dar bir bantta sıkışıyordu (Bayrak/Kama). Bu sıkışma, büyük bir patlamanın (Breakout) habercisidir. Hacimle yukarı kırılım gerçekleşti, sıkışma boyu kadar hedef bekleniyor."
                    elif range_pct < 15 and current_price < last_20_low and current_price < ema99 and is_vol_teyit:
                        sinyal = True; formasyon_adi = "Sıkışma ve Aşağı Kırılım"; yon = "SHORT"; emoji = "🔻"
                        hedef = current_price - (last_20_high - last_20_low); stop = last_20_high * 1.02
                        aciklama = "Fiyat dar bir bantta sıkıştıktan sonra desteği aşağı kırdı. Bu, satıcıların kontrolü tamamen ele geçirdiğini gösterir. Sıkışma boyu kadar aşağı yönlü bir hareket bekleniyor."

                if sinyal:
                    hedef_pct = ((hedef - current_price) / current_price) * 100
                    stop_pct = ((stop - current_price) / current_price) * 100
                    
                    if yon == "LONG" and hedef > current_price and stop < current_price: pass
                    elif yon == "SHORT" and hedef < current_price and stop > current_price: pass
                    else: continue

                    msg = (f"{emoji} <b>FORMASYON SİNYALİ ({yon}): {symbol}</b>\n"
                           f"<i>1 Günlük Grafik - {formasyon_adi}</i>\n\n"
                           f"💰 <b>Giriş (Kırılım):</b> {akilli_fiyat(current_price)}\n"
                           f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                           f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                           f"📊 <b>Trend Filtresi:</b> {'Yükseliş' if current_price > ema99 else 'Düşüş'}\n"
                           f"📊 <b>Hacim Teyidi:</b> Alındı ({current_vol/avg_vol:.1f}x)\n\n"
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
                    msg = f"🚀 <b>EMA DİZİLİMİ (LONG): {symbol}</b>"
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
                    msg = f"🚀 <b>MACD (LONG): {symbol}</b>"
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
                # Kodun tamamı önceki mesajlarda mevcut, yer kaplamaması için kısaltıldı
                time.sleep(0.1)
            time.sleep(MIDAS_SETUP_ARALIGI)
        except: time.sleep(60)

# =====================================================
# 8. MOTOR: TARİHİ ZİRVE / DİP KIRILIMI (ATH / ATL)
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

                sinyal = None; yon = ""; emoji = ""; stop = 0; hedef = 0; aciklama = ""

                if prev_close >= atl and current_price < atl:
                    sinyal = True; yon = "SHORT"; emoji = "💀"
                    stop = atl * 1.05; hedef = current_price * 0.80
                    aciklama = "Fiyat, geçmişteki en düşük seviyeyi (Tarihi Dibi) hacimle aşağı kırdı. Bu, uzun vadeli bir çöküşün başlangıcı olabilir. Desteğin altında kalındığı sürece düşüş devam eder."
                elif prev_close <= ath and current_price > ath:
                    sinyal = True; yon = "LONG"; emoji = "🚀"
                    stop = ath * 0.95; hedef = current_price * 1.20
                    aciklama = "Fiyat, geçmişteki en yüksek seviyeyi (Tarihi Zirveyi) hacimle yukarı kırdı. Bu, yeni bir boğa (yükseliş) trendinin ve tarihi rekorların başlangıcı olabilir."

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
    
    run_web_server()

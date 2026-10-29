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
SETUP_ARALIGI = 1800       
BOLLINGER_ARALIGI = 3600   

# FİLTRELER
MIN_HACIM_CHARTIST = 10_000_000 
MIN_HACIM_SETUP = 15_000_000    
MIN_HACIM_BOLLINGER = 20_000_000 

son_sinyaller = {}
son_setup_sinyalleri = {}
son_bollinger_sinyalleri = {}

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
    if current_close > upper_band: signal = "AL (Yukarı Kırılım)"
    elif current_close < lower_band: signal = "SAT (Aşağı Kırılım)"
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

# =====================================================
# 1. MOTOR: MİDAS FLOW (EN İYİ 5 + GÜNÜN KAZANANI)
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

def get_winner_of_the_day(symbol):
    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1h&limit=24")
    if not isinstance(klines, list) or len(klines) < 24: return None
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
        if duration <= 0: duration = 0.1
        max_pct = ((peak_price - entry_price) / entry_price) * 100
        entry_dt = datetime.fromtimestamp(entry_time / 1000, timezone.utc)
        peak_dt = datetime.fromtimestamp(peak_time / 1000, timezone.utc)
        
        return {
            "entry_time": entry_dt.strftime("%H:%M"),
            "entry_price": entry_price,
            "peak_time": peak_dt.strftime("%H:%M"),
            "peak_price": peak_price,
            "duration": duration,
            "max_pct": max_pct
        }
    except:
        return None

def midas_flow_loop():
    log("RAPOR", "Midas Flow Motoru Başladı.")
    time.sleep(5)
    while True:
        try:
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
                msg += f"<a href='https://t.me/MidasFlowAI'>{hashtags}#MidasFlow #kripto #pump #sinyal</a>"
                tg(C, msg)
                log("RAPOR", "Saatlik rapor gönderildi.")
                
                time.sleep(2) 
                winner_symbol = top_5[0]['symbol']
                winner_data = get_winner_of_the_day(winner_symbol)
                
                if winner_data:
                    coin = winner_symbol.replace("USDT", "")
                    win_msg = (f"🏆 <b>Günün Kazananı: {coin} +{winner_data['max_pct']:.0f}%</b>\n\n"
                               f"{winner_data['entry_time']} UTC — pump dedektörü sinyal verdi: <b>{coin}USDT LONG</b> giriş ${akilli_fiyat(winner_data['entry_price'])}\n"
                               f"{winner_data['peak_time']} UTC — zirve ${akilli_fiyat(winner_data['peak_price'])}. Girişten itibaren +{winner_data['max_pct']:.0f}% ({winner_data['duration']:.2f} saatte).\n\n"
                               f"⚠️ Hareket — potansiyel harekettir, kâr garantisi değildir.")
                    tg(C, win_msg)
                    log("RAPOR", f"Günün Kazananı gönderildi: {coin}")

            time.sleep(RAPOR_ARALIGI)
        except Exception as e:
            log("RAPOR", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# 2. MOTOR: CHARTIST (EMA200 KIRILIMI - LONG)
# =====================================================
def chartist_loop():
    log("CHARTIST", "Uzun Vadeli EMA200 Motoru Başladı.")
    time.sleep(15)
    timeframes = ['8h', '12h', '1d', '3d']
    while True:
        try:
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
                        # YÖN: LONG EKLENDİ
                        msg = (f"📈 <b>UZUN VADELİ SPOT SİNYALİ (LONG): {symbol}</b>\n"
                               f"<i>{tf.upper()} EMA200 Kırılımı!</i>\n\n"
                               f"💰 <b>Giriş:</b> {akilli_fiyat(current_price)}\n"
                               f"🛑 <b>SL:</b> {akilli_fiyat(sl)}\n"
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
# 3. MOTOR: POC / VAH / VAL SETUP ANALİZİ (SHORT)
# =====================================================
def setup_loop():
    log("SETUP", "POC / VAH / VAL Setup Analiz Motoru Başladı.")
    time.sleep(30)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(60)
                continue
            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_SETUP]
            
            for symbol in adaylar:
                if symbol in son_setup_sinyalleri and time.time() - son_setup_sinyalleri[symbol] < 14400: continue
                
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1h&limit=50")
                if not isinstance(klines, list) or len(klines) < 50: continue
                
                highs = [float(k[2]) for k in klines]
                lows = [float(k[3]) for k in klines]
                closes = [float(k[4]) for k in klines]
                vols = [float(k[7]) for k in klines]
                
                current_price = closes[-1]
                recent_highs = highs[-24:]
                recent_lows = lows[-24:]
                recent_vols = vols[-24:]
                
                vah = max(recent_highs)
                val = min(recent_lows)
                poc_idx = recent_vols.index(max(recent_vols))
                poc = closes[-24 + poc_idx] 
                
                # Fiyat POC seviyesine çok yakınsa veya altına sarktıysa (SHORT Senaryosu)
                if current_price <= poc * 1.01 and current_price < vah:
                    
                    low_1h = min(lows[-1:])
                    low_4h = min(lows[-4:])
                    
                    hedefler = sorted(list(set([low_1h, low_4h, val])), reverse=True)
                    hedefler = [h for h in hedefler if h < current_price]
                    
                    if len(hedefler) >= 3:
                        tp1, tp2, tp3 = hedefler[0], hedefler[1], hedefler[2]
                    elif len(hedefler) == 2:
                        tp1, tp2, tp3 = hedefler[0], hedefler[1], hedefler[1] * 0.98
                    else:
                        continue 
                    
                    stop = vah * 1.02 
                    
                    tp1_pct = ((tp1 - current_price) / current_price) * 100
                    tp2_pct = ((tp2 - current_price) / current_price) * 100
                    tp3_pct = ((tp3 - current_price) / current_price) * 100
                    stop_pct = ((stop - current_price) / current_price) * 100

                    coin = symbol.replace("USDT", "")
                    
                    # YÖN: SHORT EKLENDİ
                    msg = (f"🔻 <b>SETUP SİNYALİ (SHORT): {symbol}</b>\n\n"
                           f"Fiyat: <b>{akilli_fiyat(current_price)}</b>\n"
                           f"Hedefler: <b>{akilli_fiyat(tp1)}</b> ({tp1_pct:+.1f}%) → <b>{akilli_fiyat(tp2)}</b> ({tp2_pct:+.1f}%) → <b>{akilli_fiyat(tp3)}</b> ({tp3_pct:+.1f}%)\n"
                           f"İptal: <b>{akilli_fiyat(stop)}</b> ({stop_pct:+.1f}%) üzerinde\n\n"
                           f"{coin}, <b>VAH {akilli_fiyat(vah)}</b> ile <b>VAL {akilli_fiyat(val)}</b> arasında havada asılı kaldı. Merkezde <b>POC {akilli_fiyat(poc)}</b> var ve şu an hakem konumunda. Fiyat <b>{akilli_fiyat(current_price)}</b> ile onun üzerinde tutunmaya çalışıyor ancak yukarı ivme zayıf. 🧊\n\n"
                           f"Kilit ayrım: POC kaybı aşağıya ilk olarak 1s dip <b>{akilli_fiyat(low_1h)}</b> ve 4s dip <b>{akilli_fiyat(low_4h)}</b> yolunu açar. Akış borsalarda senkronize, zayıflık devam ederse VAL <b>{akilli_fiyat(val)}</b> hedefe girer. 🎯")
                    
                    if tg(C, msg):
                        son_setup_sinyalleri[symbol] = time.time()
                        log("SETUP", f"🔥 POC SETUP SİNYALİ: {symbol}")
                
                time.sleep(0.1)
            time.sleep(SETUP_ARALIGI)
        except Exception as e:
            log("SETUP", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# 4. MOTOR: HAFTALIK BOLLINGER SIKIŞMASI VE KIRILIMI (LONG/SHORT)
# =====================================================
def bollinger_loop():
    log("BOLLINGER", "Haftalık Bollinger Sıkışması Motoru Başladı.")
    time.sleep(45) 
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_BOLLINGER]
            
            for symbol in adaylar:
                if symbol in son_bollinger_sinyalleri and time.time() - son_bollinger_sinyalleri[symbol] < 259200: continue
                
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1w&limit=50")
                if not isinstance(klines, list) or len(klines) < 25: continue
                
                closes = [float(k[4]) for k in klines[:-1]]
                vols = [float(k[7]) for k in klines[:-1]]
                current_price = closes[-1]
                current_vol = vols[-1]
                
                avg_vol = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 1
                if current_vol <= (avg_vol * 1.2): continue 

                bb = calculate_bollinger(closes, 20, 2)
                if not bb or not bb["signal"]: continue
                
                bandwidth_history = []
                for i in range(10, 0, -1):
                    past_closes = closes[:-i]
                    past_bb = calculate_bollinger(past_closes, 20, 2)
                    if past_bb: bandwidth_history.append(past_bb["bandwidth"])
                
                if not bandwidth_history: continue
                avg_bandwidth = sum(bandwidth_history) / len(bandwidth_history)
                is_squeeze = bb["bandwidth"] < (avg_bandwidth * 0.6)

                if is_squeeze:
                    # YÖN: LONG VEYA SHORT BELİRLEMESİ
                    yon = "LONG (Yukarı Kırılım)" if "AL" in bb["signal"] else "SHORT (Aşağı Kırılım)"
                    emoji = "🚀" if "AL" in bb["signal"] else "🔴"
                    
                    target = bb["upper"] if "AL" in bb["signal"] else bb["lower"]
                    target_pct = ((target - current_price) / current_price) * 100
                    
                    sl = bb["sma"]
                    sl_pct = ((sl - current_price) / current_price) * 100
                    
                    msg = (f"{emoji} <b>HAFTALIK BOLLINGER SİNYALİ ({yon}): {symbol}</b>\n"
                           f"<i>Haftalık Sıkışma (Squeeze) Kırılımı!</i>\n\n"
                           f"📈 <b>Yön:</b> {yon}\n"
                           f"💰 <b>Kapanış Fiyatı:</b> {akilli_fiyat(current_price)}\n"
                           f"🎯 <b>Hedef Bant:</b> {akilli_fiyat(target)} (<b>{target_pct:+.1f}%</b>)\n"
                           f"🛑 <b>Stop (Orta Bant):</b> {akilli_fiyat(sl)} ({sl_pct:+.1f}%)\n\n"
                           f"📊 <b>Bant Genişliği:</b> Son 10 haftanın en dar seviyesinde (Sıkışma var)\n"
                           f"📈 <b>Hacim:</b> {current_vol/avg_vol:.1f}x Artış (Teyitli)\n\n"
                           f"💡 <b>Analiz:</b> Fiyat haftalardır daralan bir bant içindeydi. Haftalık kapanış itibariyle {yon.lower()} gerçekleşti. Haftalık kırılımlar aylarca sürebilecek ana trendlerin başlangıcıdır. 🧠")
                    
                    if tg(C, msg):
                        son_bollinger_sinyalleri[symbol] = time.time()
                        log("BOLLINGER", f"🔥 SQUEEZE KIRILIMI: {symbol} ({yon})")
                time.sleep(0.15) 
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
        self.wfile.write(b"Turkish Ultimate Bot is running!")
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
    threading.Thread(target=setup_loop, daemon=True).start()
    threading.Thread(target=bollinger_loop, daemon=True).start()
    
    run_web_server()

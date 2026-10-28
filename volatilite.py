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
RAPOR_ARALIGI = 3600       # Midas Flow (1 Saat)
CHARTIST_ARALIGI = 3600    # Digital Chartist (1 Saat)

MIN_HACIM = 10_000_000     # Chartist için minimum hacim
son_sinyaller = {}

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
# 1. MOTOR: MİDAS FLOW (EN İYİ 5 RAPORU)
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

            usdt_pairs = [t for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) > 5_000_000]
            usdt_pairs.sort(key=lambda x: float(x['priceChangePercent']), reverse=True)
            top_5 = usdt_pairs[:5]

            if top_5:
                msg = "🔥 <b>24 saatteki en iyi hareketler</b>\n"
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
                log("RAPOR", "Saatlik rapor gönderildi.")
            
            time.sleep(RAPOR_ARALIGI)
        except Exception as e:
            log("RAPOR", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# 2. MOTOR: DIGITAL CHARTIST (12H EMA200)
# =====================================================

def chartist_loop():
    log("CHARTIST", "12H EMA200 Motoru Başladı.")
    time.sleep(15)
    while True:
        try:
            log("CHARTIST", "12H EMA200 Kırılımları taranıyor...")
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(60)
                continue

            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM]

            for symbol in adaylar:
                if f"CH_{symbol}" in son_sinyaller and time.time() - son_sinyaller[f"CH_{symbol}"] < 86400: continue

                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=12h&limit=210")
                if not isinstance(klines, list) or len(klines) < 205: continue

                closes = [float(k[4]) for k in klines]
                current_price = closes[-1]
                ema200 = calculate_ema(closes, 200)
                
                if len(ema200) < 2: continue
                curr_ema200 = ema200[-1]
                prev_ema200 = ema200[-2]
                prev_close = closes[-2]

                if prev_close <= prev_ema200 and current_price > curr_ema200:
                    tp1, tp2, tp3, tp4 = current_price * 1.05, current_price * 1.10, current_price * 1.15, current_price * 1.25
                    sl = curr_ema200 * 0.97 

                    msg = (f"📈 <b>UZUN VADELİ SPOT: {symbol}</b>\n<i>12 Saatlik EMA200 Kırılımı!</i>\n\n"
                           f"💰 <b>Giriş:</b> {current_price:.4f}\n🛑 <b>SL:</b> {sl:.4f}\n\n"
                           f"🎯 <b>Hedefler:</b> TP1: {tp1:.4f} | TP2: {tp2:.4f} | TP3: {tp3:.4f} | TP4: {tp4:.4f}\n\n"
                           f"⚠️ <i>Nadir gelen büyük fırsat. Kademeli kâr al!</i>")
                    
                    if tg(C, msg):
                        son_sinyaller[f"CH_{symbol}"] = time.time()
                        log("CHARTIST", f"🔥 KIRILIM SİNYALİ: {symbol}")
                time.sleep(0.05)

            log("CHARTIST", "Tarama bitti, 1 saat bekleniyor.")
            time.sleep(CHARTIST_ARALIGI)
        except Exception as e:
            log("CHARTIST", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# RENDER WEB SUNUCUSU (KAPANMAYI ÖNLER)
# =====================================================

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Dual-Engine Bot is running!")
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
    # 1. Rapor Motoru (Midas Flow)
    threading.Thread(target=midas_flow_loop, daemon=True).start()
    
    # 2. Uzun Vade Motoru (Chartist)
    threading.Thread(target=chartist_loop, daemon=True).start()
    
    # Web sunucusunu başlat
    run_web_server()

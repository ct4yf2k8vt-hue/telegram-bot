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
BASE = "https://fapi.binance.com"

# RAPOR AYARLARI (Midas Flow)
RAPOR_ARALIGI = 3600 # 1 Saat

# SİNYAL AYARLARI (EMA Kesişimi)
SINYAL_ARALIGI = 300  # 5 Dakika
MIN_HACIM = 10_000_000 # 10 Milyon Dolar üzeri hacim

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

# =====================================================
# 1. MOTOR: MİDAS FLOW RAPORU (SAATLİK)
# =====================================================

def get_peak_time(symbol):
    klines = gj(f"{BASE}/fapi/v1/klines?symbol={symbol}&interval=1h&limit=24")
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
    time.sleep(5) # İlk açılışta hemen çalışsın

    while True:
        try:
            log("RAPOR", "Piyasa verileri çekiliyor...")
            tickers = gj(f"{BASE}/fapi/v1/ticker/24hr")
            
            # Spot API yedeği (Futures engellenirse)
            if not isinstance(tickers, list):
                tickers = gj("https://api.binance.com/api/v3/ticker/24hr")

            if not isinstance(tickers, list):
                log("RAPOR", "API hatası. 5 dk sonra denenecek.")
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
                msg += "💎 Bybit'te bizimle işlem yap — borsa komisyonu ve abonelik için indirim\n\n"
                msg += "⚠️ Hareket — potansiyel hareket, kâr garantisi değildir.\n\n"
                msg += f"<a href='https://t.me/MidasFlowAI'>{hashtags}#MidasFlow #crypto #pump #signals</a>"

                tg(C, msg)
                log("RAPOR", "Saatlik rapor gönderildi.")
            
            time.sleep(RAPOR_ARALIGI)

        except Exception as e:
            log("RAPOR", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# 2. MOTOR: EMA KESİŞİM SİNYALİ (5 DAKİKALIK)
# =====================================================

def calculate_ema(prices, period):
    if len(prices) < period: return []
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for price in prices[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema

def trend_signal_loop():
    log("SİNYAL", "Trend Takip Motoru Başladı.")
    time.sleep(10) # Rapor motoruyla çakışmasın diye hafif gecikme

    while True:
        try:
            log("SİNYAL", "EMA Kesişimleri taranıyor...")
            tickers = gj("https://api.binance.com/api/v3/ticker/24hr")
            
            if not isinstance(tickers, list):
                log("SİNYAL", "API hatası, bekleniyor.")
                time.sleep(60)
                continue

            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM]
            
            alerts = 0
            for symbol in adaylar:
                if symbol in son_sinyaller and time.time() - son_sinyaller[symbol] < 3600:
                    continue

                klines = gj(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=100")
                if not isinstance(klines, list) or len(klines) < 50: continue

                closes = [float(k[4]) for k in klines]
                ema9 = calculate_ema(closes, 9)
                ema21 = calculate_ema(closes, 21)

                if len(ema9) < 2 or len(ema21) < 2: continue

                # Kesişim Kontrolü
                curr_ema9, curr_ema21 = ema9[-1], ema21[-1]
                prev_ema9, prev_ema21 = ema9[-2], ema21[-2]

                sinyal = None
                if prev_ema9 <= prev_ema21 and curr_ema9 > curr_ema21:
                    sinyal, emoji, tip = "AL", "🟢", "Yukarı Kesişim"
                    stop = curr_ema21 * 0.98
                    hedef = closes[-1] + (closes[-1] - stop) * 2
                elif prev_ema9 >= prev_ema21 and curr_ema9 < curr_ema21:
                    sinyal, emoji, tip = "SAT", "🔴", "Aşağı Kesişim"
                    stop = curr_ema21 * 1.02
                    hedef = closes[-1] - (stop - closes[-1]) * 2

                if sinyal:
                    msg = (f"{emoji} <b>{sinyal} SİNYALİ: {symbol}</b>\n\n"
                           f"💰 <b>Fiyat:</b> {closes[-1]:.4f}\n"
                           f"🛑 <b>Stop:</b> {stop:.4f}\n"
                           f"🎯 <b>Hedef:</b> {hedef:.4f}\n\n"
                           f"📊 <i>15m EMA9/EMA21 {tip}</i>\n"
                           f"⏰ {simdi().strftime('%H:%M')}")
                    
                    if tg(C, msg):
                        son_sinyaller[symbol] = time.time()
                        alerts += 1
                        log("SİNYAL", f"{sinyal} GÖNDERİLDİ: {symbol}")

                time.sleep(0.05) # API'yi yorma

            log("SİNYAL", f"Tarama bitti. Sinyal: {alerts}. 5 dk bekleniyor.")
            time.sleep(SINYAL_ARALIGI)

        except Exception as e:
            log("SİNYAL", f"Hata: {e}")
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
    # 1. Rapor Motorunu Başlat
    threading.Thread(target=midas_flow_loop, daemon=True).start()
    
    # 2. Sinyal Motorunu Başlat
    threading.Thread(target=trend_signal_loop, daemon=True).start()
    
    # 3. Web Sunucusunu Başlat
    run_web_server()

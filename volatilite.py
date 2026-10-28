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
# AYARLAR (UZUN VADELİ SPOT STRATEJİSİ)
# =====================================================

socket.setdefaulttimeout(15)

T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()

TR_Saati = timezone(timedelta(hours=3))
BASE = "https://api.binance.com"

TARAMA_ARALIGI = 3600      # 1 saatte bir tarama yapar (12H grafik için yeterli)
MIN_HACIM = 10_000_000     # 10 Milyon Dolar üzeri hacmi olanlar

son_sinyaller = {}

# =====================================================
# YARDIMCI FONKSİYONLAR
# =====================================================

def simdi():
    return datetime.now(TR_Saati)

def log(m):
    print(f"[{simdi().strftime('%H:%M')}] {m}", flush=True)

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
# EMA200 HESAPLAMA
# =====================================================

def calculate_ema(prices, period):
    if len(prices) < period: return []
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for price in prices[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema

# =====================================================
# STRATEJİ MOTORU (12H EMA200 KIRILIMI)
# =====================================================

def check_signals():
    log("12H EMA200 Kırılımları taranıyor...")
    
    # Spot piyasa verilerini çek
    tickers = gj("https://api.binance.com/api/v3/ticker/24hr")
    if not isinstance(tickers, list): return

    adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM]

    for symbol in adaylar:
        # Aynı coin için 24 saat içinde tekrar sinyal atma
        if symbol in son_sinyaller and time.time() - son_sinyaller[symbol] < 86400:
            continue

        # 12 Saatlik grafikte son 210 mumu çek (EMA200 için en az 200 mum lazım)
        klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=12h&limit=210")
        if not isinstance(klines, list) or len(klines) < 205: 
            continue

        closes = [float(k[4]) for k in klines]
        current_price = closes[-1]
        
        ema200 = calculate_ema(closes, 200)
        if len(ema200) < 2: continue

        curr_ema200 = ema200[-1]
        prev_ema200 = ema200[-2]
        
        prev_close = closes[-2]

        # SİNYAL ŞARTI: Fiyat EMA200'ün altındayken üzerine çıktı mı? (Kırılım anı)
        if prev_close <= prev_ema200 and current_price > curr_ema200:
            
            # Kademeli Hedefler (Görseldeki gibi %5, %10, %15 vb. ama fiyata göre uzaklık)
            tp1 = current_price * 1.05  # %5
            tp2 = current_price * 1.10  # %10
            tp3 = current_price * 1.15  # %15
            tp4 = current_price * 1.25  # %25
            tp5 = current_price * 1.40  # %40
            
            # Stop-loss: Kırılan EMA200'ün hemen altı (Trend dönmezse diye)
            sl = curr_ema200 * 0.97 

            msg = (f"📈 <b>UZUN VADELİ SPOT SİNYALİ: {symbol}</b>\n"
                   f"<i>12 Saatlik EMA200 Kırılımı Gerçekleşti!</i>\n\n"
                   f"💰 <b>Giriş (Fiyat):</b> {current_price:.4f}\n"
                   f"🛑 <b>Stop-Loss:</b> {sl:.4f} (EMA200 Altı)\n\n"
                   f"🎯 <b>Kademeli Hedefler (TP):</b>\n"
                   f"• TP1: {tp1:.4f} (+%5)\n"
                   f"• TP2: {tp2:.4f} (+%10)\n"
                   f"• TP3: {tp3:.4f} (+%15)\n"
                   f"• TP4: {tp4:.4f} (+%25)\n"
                   f"• TP5: {tp5:.4f} (+%40)\n\n"
                   f"⏱ <b>Strateji:</b> Yüksek Zaman Dilimi Trend Takibi\n"
                   f"⚠️ <i>Bu sinyal nadir gelir, hedefi büyüktür. Kademeli kâr almayı unutma!</i>")
            
            if tg(C, msg):
                son_sinyaller[symbol] = time.time()
                log(f"🔥 UZUN VADELİ SİNYAL: {symbol}")

        time.sleep(0.1)

# =====================================================
# ANA DÖNGÜ
# =====================================================

def bot_loop():
    log("--- 12H EMA200 UZUN VADELİ SPOT BOTU BAŞLADI ---")
    tg(C, "✅ <b>Uzun Vadeli Spot Botu Başladı!</b>\n\nStrateji: 12 Saatlik EMA200 Kırılımı.\nBu bot nadir ama büyük sinyaller üretir.")
    
    while True:
        try:
            check_signals()
            time.sleep(TARAMA_ARALIGI)
        except Exception as e:
            log(f"Hata: {e}")
            time.sleep(60)

# =====================================================
# RENDER WEB SUNUCUSU
# =====================================================

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"12H Spot Bot is running!")
    def log_message(self, format_string, *args): return

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    log(f"WEB SUNUCUSU BAŞLADI - PORT: {port}")
    server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    run_web_server()

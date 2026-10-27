import urllib.request
import urllib.parse
import urllib.error
import json
import time
import socket
import os
import threading
import http.server
import socketserver
from datetime import datetime, timedelta, timezone

# =====================================================
# AYARLAR (MİDAS FLOW TARZI RAPOR)
# =====================================================

socket.setdefaulttimeout(15)

T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip()

TR_Saati = timezone(timedelta(hours=3))
BASE = "https://fapi.binance.com"

RAPOR_ARALIGI = 86400 # 24 Saat (Test için 3600 yapabilirsin)

# =====================================================
# YARDIMCI FONKSİYONLAR
# =====================================================

def simdi():
    return datetime.now(TR_Saati)

def log(m):
    print(f"[{simdi().strftime('%H:%M:%S')}] {m}", flush=True)

def tg(chat_id, message):
    if not T or not chat_id: return False
    try:
        data = urllib.parse.urlencode({
            "chat_id": str(chat_id), 
            "text": message, 
            "parse_mode": "HTML", 
            "disable_web_page_preview": "true"
        }).encode("utf-8")
        req = urllib.request.Request(f"https://api.telegram.org/bot{T}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8")).get("ok", False)
    except:
        return False

def gj(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json'
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # EĞER FUTURES ENGELLENDİYSE SPOT API'YE GEÇ
        if "fapi.binance.com" in url and e.code in (403, 451, 418):
            spot_url = url.replace("fapi.binance.com", "api.binance.com").replace("/fapi/v1", "/api/v3")
            try:
                req2 = urllib.request.Request(spot_url, headers=headers)
                with urllib.request.urlopen(req2, timeout=15) as resp2:
                    return json.loads(resp2.read().decode("utf-8"))
            except Exception:
                pass
        return None
    except:
        return None

# =====================================================
# ZİRVE SÜRESİNİ BULMA
# =====================================================

def get_peak_time(symbol):
    klines = gj(f"{BASE}/fapi/v1/klines?symbol={symbol}&interval=1h&limit=24")
    if not isinstance(klines, list):
        klines = gj(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1h&limit=24")
        
    if not isinstance(klines, list) or len(klines) < 24:
        return "0"
    
    try:
        entry_time = 0
        peak_time = 0
        entry_price = float('inf')
        peak_price = 0
        
        for k in klines:
            high = float(k[2])
            low = float(k[3])
            open_time = int(k[0])
            
            if low < entry_price:
                entry_price = low
                entry_time = open_time
                
            if high > peak_price:
                peak_price = high
                peak_time = open_time
                
        duration_hours = (peak_time - entry_time) / (1000 * 60 * 60)
        if duration_hours <= 0: duration_hours = 0.1
        return f"{duration_hours:.1f}"
    except:
        return "0"

# =====================================================
# RAPOR OLUŞTURUCU
# =====================================================

def generate_report():
    log("Piyasa verileri çekiliyor...")
    
    tickers = gj(f"{BASE}/fapi/v1/ticker/24hr")
    if not isinstance(tickers, list):
        tickers = gj("https://api.binance.com/api/v3/ticker/24hr")

    if not isinstance(tickers, list):
        log("HATA: Binance API'lerine erişilemedi.")
        return False

    usdt_pairs = []
    for t in tickers:
        if t['symbol'].endswith('USDT'):
            try:
                vol = float(t['quoteVolume'])
                if vol > 5_000_000: 
                    usdt_pairs.append(t)
            except:
                pass

    # 24 saatlik değişime göre büyükten küçüğe sırala
    usdt_pairs.sort(key=lambda x: float(x['priceChangePercent']), reverse=True)
    top_5 = usdt_pairs[:5]

    if not top_5:
        log("Uygun coin bulunamadı.")
        return False

    # --- MESAJ OLUŞTURMA (Görseldeki Çeviriye Birebir Uyumlu) ---
    msg = "🔥 <b>24 saatteki en iyi hareketler</b>\n"
    msg += "sinyal yönündeki maksimum hareket, veritabanımıza göre:\n\n"

    medals = ["🥇", "🥈", "🥉", "🏅", "⭐️"]
    hashtags = ""
    
    for i, t in enumerate(top_5):
        symbol = t['symbol'].replace("USDT", "")
        change = float(t['priceChangePercent'])
        change_str = f"+{change:.0f}%" if change > 0 else f"{change:.0f}%"
        
        # Her coin için zirve süresini hesapla
        peak_time = get_peak_time(t['symbol'])
        
        msg += f"{medals[i]} {symbol} {change_str} · {peak_time} saatte zirve\n"
        hashtags += f"#{symbol} "

    msg += "\nTüm sinyal akışı — panelde 👉 midasflow.ai/app\n\n"
    msg += "💎 Bybit'te bizimle işlem yap — borsa komisyonu ve abonelik için indirim\n\n"
    msg += "⚠️ Hareket — potansiyel hareket, kâr garantisi değildir.\n\n"
    msg += f"<a href='https://t.me/MidasFlowAI'>{hashtags}#MidasFlow #crypto #pump #signals</a>"

    tg(C, msg)
    log("Rapor başarıyla gönderildi.")
    return True

# =====================================================
# ANA DÖNGÜ
# =====================================================

def bot_loop():
    log("--- MİDAS FLOW RAPOR BOTU BAŞLADI ---")
    tg(C, "✅ <b>Midas Flow Rapor Botu Başladı!</b>\n\nGörseldeki formatta raporlar hazırlanıyor.")
    
    time.sleep(3)
    
    while True:
        try:
            basarili = generate_report()
            if basarili:
                log(f"Bir sonraki rapor için {RAPOR_ARALIGI} saniye bekleniyor...")
                time.sleep(RAPOR_ARALIGI)
            else:
                log("Rapor oluşturulamadı (API engeli). 5 dakika sonra tekrar denenecek...")
                time.sleep(300)
        except Exception as e:
            log(f"Döngü hatası: {e}")
            time.sleep(60)

# =====================================================
# RENDER WEB SUNUCUSU
# =====================================================

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Midas Flow Bot is running!")
    def log_message(self, format_string, *args): return

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    log(f"WEB SUNUCUSU BAŞLADI - PORT: {port}")
    server.serve_forever()

# =====================================================
# BAŞLAT
# =====================================================

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    run_web_server()

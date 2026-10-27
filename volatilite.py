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
# AYARLAR (MİDAS FLOW TARZI RAPOR)
# =====================================================

socket.setdefaulttimeout(15)

T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip()

TR_Saati = timezone(timedelta(hours=3))
BASE = "https://fapi.binance.com"

# RAPOR ARALIĞI (Saniye cinsinden. 86400 = 24 Saat)
# Test etmek için bunu 3600 (1 saat) veya 600 (10 dakika) yapabilirsin.
RAPOR_ARALIGI = 86400 

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
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except:
        return None

# =====================================================
# ZİRVE SAATİNİ BULMA FONKSİYONU
# =====================================================

def get_peak_time(symbol):
    """Coin'in son 24 saat içindeki en yüksek fiyata ulaştığı saati bulur."""
    klines = gj(f"{BASE}/fapi/v1/klines?symbol={symbol}&interval=1h&limit=24")
    if not isinstance(klines, list) or len(klines) < 24:
        return "Bilinmiyor"
    
    try:
        max_price = 0
        peak_index = 0
        
        # 24 mumu gez, en yüksek 'high' değerini bul
        for i, k in enumerate(klines):
            high = float(k[2])
            if high > max_price:
                max_price = high
                peak_index = i
                
        # Zirveye ne kadar sürede ulaştığını hesapla (kabaca saat)
        saat = peak_index + 1
        return f"{saat} saat"
    except:
        return "Bilinmiyor"

# =====================================================
# RAPOR OLUŞTURUCU
# =====================================================

def generate_report():
    log("Piyasa verileri çekiliyor (Midas Flow Raporu)...")
    
    tickers = gj(f"{BASE}/fapi/v1/ticker/24hr")
    if not isinstance(tickers, list):
        log("Binance verisi alınamadı.")
        return

    # Sadece USDT paritelerini al
    usdt_pairs = []
    for t in tickers:
        if t['symbol'].endswith('USDT'):
            try:
                # Hacmi çok düşük olanları ele (manipülasyona açık olanlar)
                vol = float(t['quoteVolume'])
                if vol > 5_000_000: 
                    usdt_pairs.append(t)
            except:
                pass

    # 24 saatlik değişime göre büyükten küçüğe sırala
    usdt_pairs.sort(key=lambda x: float(x['priceChangePercent']), reverse=True)

    # İlk 5 coini al
    top_5 = usdt_pairs[:5]

    # Rapor metnini oluştur
    msg = "🔮 <b>MidasFlow</b>\n"
    msg += "AI-сканер рынка · @MidasFlowAI\n"
    msg += "───────────────────\n\n"
    msg += "📈 <b>En İyi Hareketler (Son 24 Saat)</b>\n"
    msg += "Sinyal yönünde maksimum hareket:\n\n"

    medals = ["🥇", "🥈", "🥉", "🏅", "⭐️"]
    
    for i, t in enumerate(top_5):
        symbol = t['symbol'].replace("USDT", "")
        change = float(t['priceChangePercent'])
        
        # Sadece pozitif (yükselen) olanları al, eğer piyasa tamamen düşüşteyse en az düşenleri alır
        peak_time = get_peak_time(t['symbol'])
        
        # Görseldeki gibi sadece rakam ve yüzdeyi yazdır
        change_str = f"+{change:.0f}%" if change > 0 else f"{change:.0f}%"
        
        msg += f"{medals[i]} <b>{symbol}</b> {change_str} • Zirve: {peak_time}\n"

    msg += "\n───────────────────\n"
    msg += "📊 <i>İstatistikler (Son 24 Saat)</i>\n"
    msg += "Toplam işlem gören: " + str(len(usdt_pairs)) + " parite\n"
    msg += "Zaman: " + simdi().strftime('%Y-%m-%d %H:%M') + " (TR)"

    if tg(C, msg):
        log("Rapor başarıyla Telegram'a gönderildi.")
    else:
        log("Telegram gönderim hatası!")

# =====================================================
# ANA DÖNGÜ
# =====================================================

def bot_loop():
    log("--- MİDAS FLOW TARZI RAPOR BOTU BAŞLADI ---")
    
    # Başlangıçta test mesajı
    tg(C, "✅ <b>Midas Flow Tarzı Rapor Botu Başladı!</b>\n\nSadece günlük en iyi hareket eden 5 coini raporlayacak. Diğer özellikler kapatıldı.")
    
    # İlk raporu hemen gönder (Botun çalıştığını görmen için)
    time.sleep(5)
    generate_report()

    while True:
        try:
            # Belirlenen aralık kadar bekle (Varsayılan 24 saat)
            log(f"Bir sonraki rapor için {RAPOR_ARALIGI} saniye bekleniyor...")
            time.sleep(RAPOR_ARALIGI)
            
            # Raporu oluştur ve gönder
            generate_report()
            
        except Exception as e:
            log(f"Döngü hatası: {e}")
            time.sleep(60)

# =====================================================
# RENDER WEB SUNUCUSU (Kapanmayı önler)
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
    # Web sunucusunu ana thread'de başlat (Render port sorunu için)
    threading.Thread(target=bot_loop, daemon=True).start()
    run_web_server()

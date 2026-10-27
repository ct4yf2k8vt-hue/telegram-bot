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

# RAPOR ARALIĞI (86400 = 24 Saat)
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
# "GÜNÜN KAZANANI" HESAPLAYICI
# =====================================================

def get_winner_of_the_day(symbol, change_24h):
    """En çok yükselen coinin gün içindeki en düşük ve en yüksek noktalarını bulur."""
    klines = gj(f"{BASE}/fapi/v1/klines?symbol={symbol}&interval=1h&limit=24")
    if not isinstance(klines, list) or len(klines) < 24:
        return None
    
    try:
        entry_price = float('inf')
        entry_time = 0
        peak_price = 0
        peak_time = 0
        
        # Son 24 saati tara
        for k in klines:
            high = float(k[2])
            low = float(k[3])
            open_time = int(k[0])
            
            # En düşük fiyatı (giriş sinyali) bul
            if low < entry_price:
                entry_price = low
                entry_time = open_time
                
            # En yüksek fiyatı (zirve) bul
            if high > peak_price:
                peak_price = high
                peak_time = open_time
                
        # Süreyi saat cinsinden hesapla
        duration_hours = (peak_time - entry_time) / (1000 * 60 * 60)
        if duration_hours <= 0:
            duration_hours = 1 # Hata payı
            
        # Kâr oranını hesapla (Girişten zirveye)
        max_pct = ((peak_price - entry_price) / entry_price) * 100
        
        # Saat formatını al (UTC)
        entry_dt = datetime.fromtimestamp(entry_time / 1000, timezone.utc)
        peak_dt = datetime.fromtimestamp(peak_time / 1000, timezone.utc)
        
        return {
            "symbol": symbol.replace("USDT", ""),
            "entry_price": entry_price,
            "entry_time": entry_dt.strftime("%H:%M"),
            "peak_price": peak_price,
            "peak_time": peak_dt.strftime("%H:%M"),
            "duration": duration_hours,
            "max_pct": max_pct
        }
    except Exception as e:
        log(f"Winner hesaplama hatası: {e}")
        return None

# =====================================================
# RAPOR OLUŞTURUCU
# =====================================================

def generate_report():
    log("Piyasa verileri çekiliyor (Midas Flow Raporu)...")
    
    tickers = gj(f"{BASE}/fapi/v1/ticker/24hr")
    if not isinstance(tickers, list):
        log("Binance verisi alınamadı.")
        return

    # Sadece USDT paritelerini al ve hacmi 5M üzeri olanları filtrele
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

    # İlk 5 coini al
    top_5 = usdt_pairs[:5]

    # --- 1. MESAJ: TOP 5 LİSTESİ ---
    msg1 = "🔮 <b>MidasFlow</b>\n"
    msg1 += "AI-сканер рынка · @MidasFlowAI\n"
    msg1 += "───────────────────\n\n"
    msg1 += "📈 <b>Лучшие движения за сутки</b>\n"
    msg1 += "максимальный ход в сторону сигнала:\n\n"

    medals = ["🥇", "🥈", "🥉", "🏅", "⭐️"]
    
    for i, t in enumerate(top_5):
        symbol = t['symbol'].replace("USDT", "")
        change = float(t['priceChangePercent'])
        change_str = f"+{change:.0f}%" if change > 0 else f"{change:.0f}%"
        msg1 += f"{medals[i]} <b>{symbol}</b> {change_str}\n"

    msg1 += "\n───────────────────\n"
    msg1 += f"🕒 {simdi().strftime('%Y-%m-%d %H:%M')} (TR)"
    
    tg(C, msg1)
    log("Top 5 listesi gönderildi.")
    time.sleep(2) # Mesajlar arası 2 saniye bekle

    # --- 2. MESAJ: GÜNÜN KAZANANI (WINNER OF THE DAY) ---
    if top_5:
        winner_ticker = top_5[0]
        winner_data = get_winner_of_the_day(winner_ticker['symbol'], float(winner_ticker['priceChangePercent']))
        
        if winner_data:
            msg2 = f"🏆 <b>Победа дня: {winner_data['symbol']} +{winner_data['max_pct']:.0f}%</b>\n\n"
            msg2 += f"{winner_data['entry_time']} UTC — памп-детектор дал сигнал: <b>{winner_data['symbol']}USDT LONG</b> по ${winner_data['entry_price']:.4f}\n"
            msg2 += f"{winner_data['peak_time']} UTC — пик ${winner_data['peak_price']:.4f}. +{winner_data['max_pct']:.0f}% от входа за {winner_data['duration']:.2f}ч.\n\n"
            msg2 += "⚠️ <i>Ход — потенциал движения, не гарантия профита.</i>"
            
            tg(C, msg2)
            log(f"Günün Kazananı gönderildi: {winner_data['symbol']}")

# =====================================================
# ANA DÖNGÜ
# =====================================================

def bot_loop():
    log("--- MİDAS FLOW RAPOR BOTU BAŞLADI ---")
    
    tg(C, "✅ <b>Midas Flow Rapor Botu Başladı!</b>\n\nTop 5 ve Günün Kazananı raporları hazırlanıyor.")
    
    time.sleep(3)
    generate_report()

    while True:
        try:
            log(f"Bir sonraki rapor için {RAPOR_ARALIGI} saniye bekleniyor...")
            time.sleep(RAPOR_ARALIGI)
            generate_report()
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

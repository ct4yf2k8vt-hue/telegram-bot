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

TARAMA_ARALIGI = 300       # 5 dakikada bir tarama yapar
MIN_HACIM = 15_000_000     # 15 Milyon Dolar üzeri hacmi olan coinler

# Aktif işlemleri takip etmek için (TP1 veya SL vuruldu mu diye)
aktif_islemler = {} 
son_sinyaller = {}

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
# İNDİKATÖRLER (EMA ve ATR)
# =====================================================

def calculate_ema(prices, period):
    if len(prices) < period: return []
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for price in prices[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema

def calculate_atr(klines, period=14):
    """Ortalama Gerçek Aralık (Volatilite ölçüsü)"""
    if len(klines) < period + 1: return 0
    tr_list = []
    for i in range(1, len(klines)):
        high = float(klines[i][2])
        low = float(klines[i][3])
        prev_close = float(klines[i-1][4])
        
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)
    
    # Son 14 ATR'nin ortalaması
    return sum(tr_list[-period:]) / period

# =====================================================
# SİNYAL MOTORU VE TAKİPÇİ
# =====================================================

def check_active_trades():
    """Daha önce sinyal atılan işlemlerin TP1 veya SL'ye ulaşıp ulaşmadığını kontrol eder."""
    if not aktif_islemler: return

    for symbol, trade in list(aktif_islemler.items()):
        ticker = gj(f"{BASE}/api/v3/ticker/price?symbol={symbol}")
        if not ticker: continue
        
        current_price = float(ticker['price'])
        
        # LONG İşlemler
        if trade['yon'] == "LONG":
            if current_price >= trade['tp1']:
                tg(C, f"✅ <b>{symbol} TP1 VURULDU!</b>\n💰 Fiyat: {current_price:.4f}\n📈 Kâr: ~%3\n\n<i>Kârını alabilirsin!</i>")
                del aktif_islemler[symbol] # İşlem bitti, listeden çıkar
            elif current_price <= trade['sl']:
                tg(C, f"❌ <b>{symbol} STOP-LOSS OLDU!</b>\n💰 Fiyat: {current_price:.4f}\n📉 Zarar: ~%1.5\n\n<i>Pozisyonu kapat!</i>")
                del aktif_islemler[symbol]
                
        # SHORT İşlemler
        elif trade['yon'] == "SHORT":
            if current_price <= trade['tp1']:
                tg(C, f"✅ <b>{symbol} TP1 VURULDU!</b>\n💰 Fiyat: {current_price:.4f}\n📈 Kâr: ~%3\n\n<i>Kârını alabilirsin!</i>")
                del aktif_islemler[symbol]
            elif current_price >= trade['sl']:
                tg(C, f"❌ <b>{symbol} STOP-LOSS OLDU!</b>\n💰 Fiyat: {current_price:.4f}\n📉 Zarar: ~%1.5\n\n<i>Pozisyonu kapat!</i>")
                del aktif_islemler[symbol]

def check_signals():
    log("Piyasa taranıyor...")
    
    # Önce aktif işlemleri kontrol et (TP1 veya SL oldu mu?)
    check_active_trades()

    tickers = gj("https://api.binance.com/api/v3/ticker/24hr")
    if not isinstance(tickers, list): return

    adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM]

    for symbol in adaylar:
        if symbol in son_sinyaller and time.time() - son_sinyaller[symbol] < 3600:
            continue

        klines = gj(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=100")
        if not isinstance(klines, list) or len(klines) < 50: continue

        closes = [float(k[4]) for k in klines]
        entry_price = closes[-1]
        
        ema9 = calculate_ema(closes, 9)
        ema21 = calculate_ema(closes, 21)
        atr = calculate_atr(klines, 14)

        if len(ema9) < 2 or len(ema21) < 2 or atr == 0: continue

        curr_ema9, curr_ema21 = ema9[-1], ema21[-1]
        prev_ema9, prev_ema21 = ema9[-2], ema21[-2]

        yon = None
        
        # LONG Sinyali
        if prev_ema9 <= prev_ema21 and curr_ema9 > curr_ema21:
            yon = "LONG"
            sl = entry_price - (atr * 1.0)       # Zarar durdur (1 ATR)
            tp1 = entry_price * 1.03             # Kâr Al 1 (%3)
            tp2 = entry_price * 1.06             # Kâr Al 2 (%6)
            tp3 = entry_price * 1.09             # Kâr Al 3 (%9)
            
        # SHORT Sinyali
        elif prev_ema9 >= prev_ema21 and curr_ema9 < curr_ema21:
            yon = "SHORT"
            sl = entry_price + (atr * 1.0)
            tp1 = entry_price * 0.97
            tp2 = entry_price * 0.94
            tp3 = entry_price * 0.91

        if yon:
            # Aktif işlemlere kaydet (Takip için)
            aktif_islemler[symbol] = {"yon": yon, "tp1": tp1, "sl": sl}

            emoji = "🟢" if yon == "LONG" else "🔴"
            msg = (f"<b>neoshark.</b>\n"
                   f"<b>#{symbol.replace('USDT','')} - 🔒</b>\n\n"
                   f"<b>Yön:</b> {yon} {emoji}\n"
                   f"<b>Entry:</b> {entry_price:.4f}\n\n"
                   f"<b>TP1:</b> {tp1:.4f} (~%3)\n"
                   f"<b>TP2:</b> {tp2:.4f} (~%6)\n"
                   f"<b>TP3:</b> {tp3:.4f} (~%9)\n\n"
                   f"<b>SL:</b> {sl:.4f}\n"
                   f"<b>Leverage:</b> 10x")
            
            if tg(C, msg):
                son_sinyaller[symbol] = time.time()
                log(f"{yon} SİNYALİ GÖNDERİLDİ: {symbol}")

        time.sleep(0.05)

# =====================================================
# ANA DÖNGÜ
# =====================================================

def bot_loop():
    log("--- NEOSHARK TARZI SİNYAL BOTU BAŞLADI ---")
    tg(C, "✅ <b>Neoshark Tarzı Bot Başladı!</b>\n\nSinyal formatı hazır. Hedef: %3 Kâr (TP1).\nBot TP1'e ulaşınca haber verecek!")
    
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
        self.wfile.write(b"Neoshark Style Bot is running!")
    def log_message(self, format_string, *args): return

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    log(f"WEB SUNUCUSU BAŞLADI - PORT: {port}")
    server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    run_web_server()

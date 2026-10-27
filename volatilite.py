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
import re
from datetime import datetime, timedelta, timezone

# =====================================================
# AYARLAR (TÜM PİYASAYI TARAR)
# =====================================================

socket.setdefaulttimeout(15)

T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip()

TR_Saati = timezone(timedelta(hours=3))
BASE = "https://fapi.binance.com"

TARAMA_ARALIGI = 120 
COOLDOWN = 900
MIN_24H_HACIM = 1_000_000  # 1 Milyon Dolar barajı (Çok ölü coinleri eler, geri kalan her şeyi tarar)
GLOBAL_SINYAL_LIMIT = 15   # Piyasa çökerse Telegram'ı spamlamamak için bir turda max 15 sinyal

COOLDOWN_FILE = "/tmp/binance_cooldown.json"

BINANCE_BEKLEME_BITIS = 0
son_sinyaller = {}
cooldown_lock = threading.Lock()
last_update_id = 0
SON_RAPOR_ZAMANI = time.time()

# =====================================================
# YARDIMCI FONKSİYONLAR
# =====================================================

def simdi():
    return datetime.now(TR_Saati)

def log(m):
    print(f"[{simdi().strftime('%Y-%m-%d %H:%M:%S')} TR] {m}", flush=True)

def fiyat(v):
    if v is None: return "N/A"
    v = float(v)
    if v >= 1000: return f"{v:.2f}"
    if v >= 1: return f"{v:.4f}"
    if v >= 0.01: return f"{v:.6f}"
    return f"{v:.8f}"

def beklemeyi_yukle():
    global BINANCE_BEKLEME_BITIS
    try:
        with open(COOLDOWN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        BINANCE_BEKLEME_BITIS = float(data.get("until", 0))
    except:
        BINANCE_BEKLEME_BITIS = 0

def binance_bekleme_ayarla(saniye, neden):
    global BINANCE_BEKLEME_BITIS
    saniye = max(60, int(saniye))
    with cooldown_lock:
        BINANCE_BEKLEME_BITIS = max(BINANCE_BEKLEME_BITIS, time.time() + saniye)
        try:
            with open(COOLDOWN_FILE, "w", encoding="utf-8") as f:
                json.dump({"until": BINANCE_BEKLEME_BITIS}, f)
        except:
            pass
    log(f"{neden}: {int(saniye)} saniye bekleme.")

def binance_bekliyor():
    return time.time() < BINANCE_BEKLEME_BITIS

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
    if "fapi.binance.com" in url and binance_bekliyor(): return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if "fapi.binance.com" in url and e.code in (418, 429):
            binance_bekleme_ayarla(1800 if e.code == 418 else 300, f"Binance HTTP {e.code}")
        return None
    except:
        return None

def futures_api(path, params=None):
    if binance_bekliyor(): return None
    url = BASE + path
    if params: url += "?" + urllib.parse.urlencode(params)
    return gj(url)

# =====================================================
# /RATIO KOMUT DİNLEYİCİSİ
# =====================================================

def command_listener():
    global last_update_id
    while True:
        try:
            updates = gj(f"https://api.telegram.org/bot{T}/getUpdates?offset={last_update_id + 1}&timeout=10")
            if updates and updates.get("ok"):
                for u in updates["result"]:
                    last_update_id = u["update_id"]
                    if "message" in u and "text" in u["message"]:
                        text = u["message"]["text"]
                        chat_id = u["message"]["chat"]["id"]
                        if text.startswith("/ratio"):
                            parts = text.split()
                            symbol = parts[1].upper() if len(parts) > 1 else "BTCUSDT"
                            if not symbol.endswith("USDT"): symbol += "USDT"
                            period = parts[2].lower() if len(parts) > 2 else "1h"
                            data = gj(f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period={period}&limit=1")
                            if data and len(data) > 0:
                                d = data[0]
                                tg(chat_id, f"Long/Short Ratio📊\nBinance - {symbol}\n⏳ <b>Timeframe:</b> {period}\n"
                                            f"📈 <b>Long:</b> {float(d['longAccount'])*100:.2f}%\n"
                                            f"📉 <b>Short:</b> {float(d['shortAccount'])*100:.2f}%\n"
                                            f"⚖️ <b>Ratio:</b> {float(d['longShortRatio']):.2f}")
        except:
            pass
        time.sleep(2)

# =====================================================
# RAPORLAMA VE SELF-PING
# =====================================================

def heartbeat():
    while True:
        time.sleep(7200)
        if T and C:
            tg(C, f"🤖 <b>Bot Aktif:</b> TÜM BİNANCE FUTURES PİYASASI SHORT için taranıyor.\n⏰ {simdi().strftime('%H:%M')}")

def keep_alive():
    if not RENDER_URL: return
    while True:
        try: urllib.request.urlopen(RENDER_URL, timeout=10).read()
        except: pass
        time.sleep(600)

# =====================================================
# GÖSTERGELER
# =====================================================

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1: return None
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0: return 100.0
    return 100 - (100 / (1 + (avg_gain / avg_loss)))

def get_candles(symbol, interval, limit=60):
    data = futures_api("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    if not isinstance(data, list) or len(data) < 22: return None
    return data[:-1]

# =====================================================
# TÜM COİNLERİ SEÇ VE ANALİZ ET (SHORT)
# =====================================================

def get_all_candidates():
    tickers = futures_api("/fapi/v1/ticker/24hr")
    if not isinstance(tickers, list): return []
    
    candidates = []
    for t in tickers:
        try:
            symbol = t["symbol"]
            # Mantık: Tüm USDT paritelerini al, ölü olanları (hacmi 1M altı) çıkar.
            if not symbol.endswith("USDT") or any(x in symbol for x in ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")): 
                continue
            
            volume = float(t["quoteVolume"])
            change = float(t["priceChangePercent"])
            
            # Hacmi 1 Milyonun üzerinde olan TÜM coinler aday.
            # (Piyasa düşerken hepsi kırmızı olacağı için 24s değişimine burada takılmıyoruz)
            if volume >= MIN_24H_HACIM: 
                candidates.append(t)
        except: continue
    
    # Hacme göre büyükten küçüğe sırala (En likit olanları önce tara)
    candidates.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
    return candidates

def analyze_symbol(ticker):
    symbol = ticker["symbol"]
    
    # 1 dakikalık grafik (Anlık düşüşleri yakalamak için)
    one = get_candles(symbol, "1m", 30)
    if not one: return None

    try:
        closes = [float(x[4]) for x in one]
        opens = [float(x[1]) for x in one]
        highs = [float(x[2]) for x in one]
        lows = [float(x[3]) for x in one]
        volumes = [float(x[7]) for x in one]

        entry = float(one[-1][4])
        open_price = float(one[-1][1])
        rsi = calculate_rsi(closes)
        
        # Hacim Analizi
        avg_vol = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else 1
        volume_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 0

        # --- SHORT ŞARTLARI ---
        # 1. Fiyat düşüyor (Açılış > Kapanış)
        # 2. Hacim normalin üzerinde (En az 1.1 katı)
        # 3. RSI 60'ın altında (Yükseliş ivmesi zayıf)
        is_short = (entry < open_price and 
                    volume_ratio >= 1.1 and 
                    rsi is not None and rsi < 60)

        if not is_short: return None

        # Stop ve Hedef Belirleme (Son 3 mumun zirvesi)
        stop = max(highs[-3:]) 
        if stop <= entry: stop = entry * 1.005 
        
        risk = stop - entry
        if risk == 0 or (risk / entry) > 0.04: return None # Risk %4'ten büyükse girme

        hedef1 = entry - risk
        hedef2 = entry - (risk * 2)

        message = (
            f"🔴 <b>SHORT SİNYALİ: {symbol}</b>\n"
            f"⭐ Skor: Kırmızı Mum + RSI + Hacim\n\n"
            f"🏷 <b>Giriş:</b> {fiyat(entry)}\n"
            f"🛑 <b>Stop:</b> {fiyat(stop)} (+%{(risk/entry)*100:.2f})\n"
            f"🎯 <b>Hedef 1:</b> {fiyat(hedef1)}\n"
            f"🎯 <b>Hedef 2:</b> {fiyat(hedef2)}\n\n"
            f"📊 <b>RSI:</b> {rsi:.1f} | <b>Hacim:</b> {volume_ratio:.1f}x\n"
            f"⏱ <b>Zaman:</b> {simdi().strftime('%H:%M')}"
        )
        return {"symbol": symbol, "message": message}

    except Exception as e:
        return None

# =====================================================
# ANA TARAMA DÖNGÜSÜ
# =====================================================

def bot_loop():
    global SON_RAPOR_ZAMANI
    log("Tüm Piyasayı Tarayan SHORT Bot başlatılıyor...")
    beklemeyi_yukle()

    if T and C:
        tg(C, "✅ <b>TÜM PİYASA SHORT BOT BAŞLADI</b>\n\nBinance Futures'taki tüm USDT pariteleri (hacmi 1M üzeri) taranıyor.")

    while True:
        try:
            if binance_bekliyor():
                time.sleep(60)
                continue

            # Tüm piyasadaki adayları çek
            candidates = get_all_candidates()
            if not candidates:
                time.sleep(60)
                continue

            alerts = 0
            taranan = 0
            
            for ticker in candidates:
                if binance_bekliyor(): break
                if alerts >= GLOBAL_SINYAL_LIMIT: break # Spam koruması

                symbol = ticker["symbol"]
                if symbol in son_sinyaller and time.time() - son_sinyaller[symbol] < COOLDOWN:
                    continue

                taranan += 1
                result = analyze_symbol(ticker)
                if result:
                    if tg(C, result["message"]):
                        son_sinyaller[symbol] = time.time()
                        alerts += 1
                        log(f"SHORT SİNYALİ GÖNDERİLDİ: {symbol}")

                time.sleep(0.05) # API'yi hızlı ama güvenli tara

            # Raporlama
            if time.time() - SON_RAPOR_ZAMANI > 1800:
                tg(C, f"📊 <b>Tarama Raporu:</b>\nTüm Piyasa Tarandı.\nİncelenen: {taranan} coin\nSHORT Sinyali: {alerts}\n⏰ {simdi().strftime('%H:%M')}")
                SON_RAPOR_ZAMANI = time.time()

            time.sleep(TARAMA_ARALIGI)

        except Exception as e:
            log(f"Tarama motoru hatası: {e}")
            time.sleep(30)

# =====================================================
# RENDER HEALTH SERVER
# =====================================================

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ALL COINS SHORT Bot is running!")
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
    if T:
        threading.Thread(target=command_listener, daemon=True).start()
        threading.Thread(target=heartbeat, daemon=True).start()
        if RENDER_URL:
            threading.Thread(target=keep_alive, daemon=True).start()
            
    run_web_server()

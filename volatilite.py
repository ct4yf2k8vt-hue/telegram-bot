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

socket.setdefaulttimeout(15)

T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip()

TR_Saati = timezone(timedelta(hours=3))

# ARBİTRAJ AYARLARI
MIN_SPREAD_PCT = 2.5       # En az %2.5 fark olursa sinyal at
MIN_VOLUME_USDT = 500_000  # Hacmi en az 500 Bin Dolar olanları dikkate al

son_sinyaller = {}
last_update_id = 0

# =====================================================
# YARDIMCI FONKSİYONLAR
# =====================================================

def simdi():
    return datetime.now(TR_Saati)

def log(m):
    print(f"[{simdi().strftime('%H:%M:%S')}] {m}", flush=True)

def fiyat(v):
    if v is None: return "N/A"
    v = float(v)
    if v >= 100: return f"{v:.3f}"
    if v >= 1: return f"{v:.4f}"
    return f"{v:.5f}"

def format_vol(v):
    v = float(v)
    if v >= 1_000_000: return f"{v/1_000_000:.1f}M"
    if v >= 1_000: return f"{v/1_000:.1f}K"
    return f"{v:.0f}"

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
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except:
        return None

# =====================================================
# BORSALARDAN VERİ ÇEKME
# =====================================================

def get_binance_tickers():
    data = gj("https://api.binance.com/api/v3/ticker/24hr")
    result = {}
    if data:
        for t in data:
            if t['symbol'].endswith('USDT'):
                result[t['symbol']] = {"price": float(t['lastPrice']), "vol": float(t['quoteVolume'])}
    return result

def get_kucoin_tickers():
    data = gj("https://api.kucoin.com/api/v1/market/allTickers")
    result = {}
    if data and "data" in data and "ticker" in data["data"]:
        for t in data["data"]["ticker"]:
            # KuCoin formata: AIN-USDT -> AINUSDT
            symbol = t['symbol'].replace("-", "")
            if symbol.endswith("USDT"):
                result[symbol] = {"price": float(t['last']), "vol": float(t['volValue'])}
    return result

def get_mexc_tickers():
    data = gj("https://api.mexc.com/api/v3/ticker/24hr")
    result = {}
    if data:
        for t in data:
            if t['symbol'].endswith('USDT'):
                try:
                    result[t['symbol']] = {"price": float(t['lastPrice']), "vol": float(t['quoteVolume'])}
                except:
                    pass
    return result

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
                            data = gj(f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=1h&limit=1")
                            if data and len(data) > 0:
                                d = data[0]
                                tg(chat_id, f"Long/Short Ratio📊\nBinance - {symbol}\n"
                                            f"📈 Long: {float(d['longAccount'])*100:.2f}%\n"
                                            f"📉 Short: {float(d['shortAccount'])*100:.2f}%\n"
                                            f"⚖️ Ratio: {float(d['longShortRatio']):.2f}")
        except:
            pass
        time.sleep(2)

# =====================================================
# KALP ATIŞI VE RENDER UYANIK TUTMA
# =====================================================

def heartbeat():
    while True:
        time.sleep(3600) # 1 saat
        if T and C:
            tg(C, f"🤖 <b>Sistem Aktif:</b> Arbitraj botu piyasaları tarıyor.\n⏰ {simdi().strftime('%H:%M')}")

def keep_alive():
    if not RENDER_URL: return
    while True:
        try: urllib.request.urlopen(RENDER_URL, timeout=10).read()
        except: pass
        time.sleep(600)

# =====================================================
# ARBİTRAJ TARAMA MOTORU
# =====================================================

def bot_loop():
    log("--- BORSALAR ARASI ARBİTRAJ BOTU BAŞLADI ---")
    
    if T and C:
        tg(C, f"✅ <b>ARBİTRAJ BOTU BAŞLADI</b>\n\nBinance, KuCoin ve MEXC arasındaki %{MIN_SPREAD_PCT}+ fiyat farkları taranıyor.\n/ratio komutu aktiftir.")

    while True:
        try:
            log("Borsa verileri çekiliyor (Binance, KuCoin, MEXC)...")
            
            bn_data = get_binance_tickers()
            kc_data = get_kucoin_tickers()
            mx_data = get_mexc_tickers()

            if not bn_data:
                log("Hata: Binance verisi alınamadı.")
                time.sleep(10)
                continue

            log(f"Veriler alındı. Binance: {len(bn_data)} | KuCoin: {len(kc_data)} | MEXC: {len(mx_data)}")
            
            alerts = 0
            
            # Binance'teki tüm coinleri tara, diğer borsalarda var mı diye bak
            for symbol, b_info in bn_data.items():
                try:
                    b_price = b_info["price"]
                    b_vol = b_info["vol"]

                    if b_vol < MIN_VOLUME_USDT or b_price == 0: continue

                    # Diğer borsalardaki fiyatları bul (varsa)
                    kc_info = kc_data.get(symbol)
                    mx_info = mx_data.get(symbol)

                    # Fiyat listesi oluştur
                    prices = {"BINANCE": b_info}
                    if kc_info and kc_info["vol"] >= MIN_VOLUME_USDT: prices["KUCOIN"] = kc_info
                    if mx_info and mx_info["vol"] >= MIN_VOLUME_USDT: prices["MEXC"] = mx_info

                    if len(prices) < 2: continue # Karşılaştıracak başka borsa yok

                    # En yüksek ve en düşük fiyatı bul
                    max_ex = max(prices, key=lambda x: prices[x]["price"])
                    min_ex = min(prices, key=lambda x: prices[x]["price"])
                    
                    max_price = prices[max_ex]["price"]
                    min_price = prices[min_ex]["price"]

                    # Spread hesapla
                    spread = ((max_price - min_price) / min_price) * 100

                    # EĞER FARK YETERİNCE BÜYÜKSE SİNYAL AT
                    if spread >= MIN_SPREAD_PCT:
                        # 30 dakika içinde aynı coin için tekrar sinyal atma
                        if symbol in son_sinyaller and time.time() - son_sinyaller[symbol] < 1800:
                            continue

                        # Görseldeki formatta mesajı hazırla
                        msg = f"🟡 #{symbol} {symbol}\n"
                        msg += f"Spred:[{spread:.1f}%] ⬆️{max_ex}-{min_ex}⬇️\n\n"
                        msg += f"🔴 SHORT {max_ex} @ {fiyat(max_price)} 📍{format_vol(prices[max_ex]['vol'])}\n"
                        msg += f"🟢 LONG {min_ex} @ {fiyat(min_price)} 📍{format_vol(prices[min_ex]['vol'])}\n\n"
                        msg += f"⚠️ Funding rates are not considered.\nCheck funding before trading.\n\n"
                        
                        # Tüm borsaların fiyatlarını listele
                        for ex, info in prices.items():
                            msg += f"{ex}: {fiyat(info['price'])} 📍{format_vol(info['vol'])}\n"
                        
                        msg += f"\n{simdi().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}"
                        
                        if tg(C, msg):
                            son_sinyaller[symbol] = time.time()
                            alerts += 1
                            log(f"🔥 ARBİTRAJ BULUNDU: {symbol} | Fark: %{spread:.2f} ({max_ex} vs {min_ex})")

                except Exception as e:
                    pass

            log(f"Tarama bitti. Sinyal: {alerts}. 60 saniye bekleniyor...")
            time.sleep(60) # API limitini korumak için 60 saniye bekle

        except Exception as e:
            log(f"Ana döngü hatası: {e}")
            time.sleep(15)

# =====================================================
# RENDER WEB SUNUCUSU
# =====================================================

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Arbitrage Bot is running!")
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

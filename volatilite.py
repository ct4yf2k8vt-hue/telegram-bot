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
# AYARLAR
# =====================================================

socket.setdefaulttimeout(15)

T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip()

TR_Saati = timezone(timedelta(hours=3))

# ARBİTRAJ AYARLARI
MIN_SPREAD_PCT = 2.0       # En az %2 fark olursa sinyal at
MIN_VOLUME_USDT = 500_000  # Borsadaki hacmi en az 500 Bin Dolar olsun (Ölü coinleri eler)
TARAMA_ARALIGI = 60        # 1 dakikada bir tarama yapar
COOLDOWN = 1800            # Aynı coin için 30 dk bekleme

son_sinyaller = {}
last_update_id = 0

# =====================================================
# YARDIMCI FONKSİYONLAR
# =====================================================

def simdi():
    return datetime.now(TR_Saati)

def log(m):
    print(f"[{simdi().strftime('%H:%M:%S')}] {m}", flush=True)

def format_vol(v):
    v = float(v)
    if v >= 1_000_000: return f"{v/1_000_000:.1f}M"
    if v >= 1_000: return f"{v/1_000:.1f}M" # Görselde M olarak istenmiş
    return f"{v:.1f}M"

def format_price(p):
    if p >= 100: return f"{p:.2f}"
    if p >= 1: return f"{p:.4f}"
    return f"{p:.5f}"

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
# BORSALARDAN VERİ ÇEKME FONKSİYONLARI
# =====================================================

def get_binance():
    data = gj("https://api.binance.com/api/v3/ticker/24hr")
    res = {}
    if data:
        for t in data:
            if t['symbol'].endswith('USDT'):
                res[t['symbol']] = {"price": float(t['lastPrice']), "vol": float(t['quoteVolume'])}
    return res

def get_kucoin():
    data = gj("https://api.kucoin.com/api/v1/market/allTickers")
    res = {}
    if data and "data" in data and "ticker" in data["data"]:
        for t in data["data"]["ticker"]:
            sym = t['symbol'].replace("-", "")
            if sym.endswith("USDT"):
                res[sym] = {"price": float(t['last']), "vol": float(t['volValue'])}
    return res

def get_mexc():
    data = gj("https://api.mexc.com/api/v3/ticker/24hr")
    res = {}
    if data:
        for t in data:
            if t['symbol'].endswith('USDT'):
                try:
                    res[t['symbol']] = {"price": float(t['lastPrice']), "vol": float(t['quoteVolume'])}
                except: pass
    return res

def get_bybit():
    data = gj("https://api.bybit.com/v5/market/tickers?category=spot")
    res = {}
    if data and data.get("retCode") == 0:
        for t in data["result"]["list"]:
            if t['symbol'].endswith('USDT'):
                res[t['symbol']] = {"price": float(t['lastPrice']), "vol": float(t['turnover24h'])}
    return res

def get_gate():
    data = gj("https://api.gateio.ws/api/v4/spot/tickers")
    res = {}
    if data:
        for t in data:
            if t['currency_pair'].endswith('_USDT'):
                sym = t['currency_pair'].replace("_", "")
                res[sym] = {"price": float(t['last']), "vol": float(t['quote_volume'])}
    return res

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
# RAPORLAMA VE RENDER UYANIK TUTMA
# =====================================================

def heartbeat():
    while True:
        time.sleep(3600)
        if T and C:
            tg(C, f"🤖 <b>Sistem Aktif:</b> Borsalar arası arbitraj taranıyor.\n⏰ {simdi().strftime('%H:%M')}")

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
    log("--- ÇOKLU BORSA ARBİTRAJ BOTU BAŞLADI ---")
    
    if T and C:
        tg(C, "✅ <b>ARBİTRAJ BOTU BAŞLADI</b>\n\nBinance, KuCoin, MEXC, Bybit ve Gate.io arasındaki fiyat farkları taranıyor.\nFark %{MIN_SPREAD_PCT} üzerindeyse sinyal iletilecek.")

    while True:
        try:
            log("Borsa verileri çekiliyor...")
            
            # Tüm borsalardan verileri çek
            bn_data = get_binance()
            kc_data = get_kucoin()
            mx_data = get_mexc()
            by_data = get_bybit()
            gt_data = get_gate()

            # Hangi borsalardan veri başarıyla alındı?
            active_exchanges = {}
            if bn_data: active_exchanges["BINANCE"] = bn_data
            if kc_data: active_exchanges["KUCOIN"] = kc_data
            if mx_data: active_exchanges["MEXC"] = mx_data
            if by_data: active_exchanges["BYBIT"] = by_data
            if gt_data: active_exchanges["GATE"] = gt_data

            log(f"Aktif Borsalar: {', '.join(active_exchanges.keys())}")
            
            alerts = 0
            # Tüm borsalarda ortak olan veya olmayan tüm coinleri bul (Binance baz alalım)
            all_symbols = set()
            for ex_data in active_exchanges.values():
                all_symbols.update(ex_data.keys())

            for symbol in all_symbols:
                # Sadece USDT pariteleri
                if not symbol.endswith("USDT"): continue

                prices = {}
                for ex_name, ex_data in active_exchanges.items():
                    if symbol in ex_data:
                        # Hacim filtresini geçemeyenleri ele
                        if ex_data[symbol]["vol"] >= MIN_VOLUME_USDT:
                            prices[ex_name] = ex_data[symbol]

                if len(prices) < 2: continue # Karşılaştırma için en az 2 borsa lazım

                # En yüksek ve en düşük fiyatlı borsaları bul
                max_ex = max(prices, key=lambda x: prices[x]["price"])
                min_ex = min(prices, key=lambda x: prices[x]["price"])
                
                max_price = prices[max_ex]["price"]
                min_price = prices[min_ex]["price"]

                # Spread hesapla
                if min_price == 0: continue
                spread = ((max_price - min_price) / min_price) * 100

                # EĞER FARK YETERLİNCE BÜYÜKSE SİNYAL AT
                if spread >= MIN_SPREAD_PCT:
                    if symbol in son_sinyaller and time.time() - son_sinyaller[symbol] < COOLDOWN:
                        continue

                    # Görseldeki formatta mesaj
                    msg = f"🟡 <b>#{symbol}</b> {symbol}\n"
                    msg += f"Spred:[{spread:.1f}%] ⬆️ <b>{max_ex}-{min_ex}</b> ⬇️\n\n"
                    
                    msg += f"🔴 SHORT <a href='https://www.{max_ex.lower()}.com'>{max_ex}</a> @ {format_price(max_price)} 📍{format_vol(prices[max_ex]['vol'])}\n"
                    msg += f"🟢 LONG <a href='https://www.{min_ex.lower()}.com'>{min_ex}</a> @ {format_price(min_price)} 📍{format_vol(prices[min_ex]['vol'])}\n\n"
                    
                    msg += f"⚠️ Funding rates are not considered.\nCheck funding before trading.\n\n"
                    
                    # Tüm borsaların fiyatlarını alt alta listele
                    for ex in ["MEXC", "GATE", "BITGET", "BYBIT", "BINANCE", "KUCOIN"]:
                        if ex in prices:
                            msg += f"{ex}: {format_price(prices[ex]['price'])} 📍{format_vol(prices[ex]['vol'])}\n"
                    
                    msg += f"\n{simdi().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}"
                    
                    if tg(C, msg):
                        son_sinyaller[symbol] = time.time()
                        alerts += 1
                        log(f"🔥 SİNYAL: {symbol} | Fark: %{spread:.1f} ({max_ex} vs {min_ex})")

            log(f"Tarama bitti. Sinyal: {alerts}. 60 saniye bekleniyor...")
            time.sleep(TARAMA_ARALIGI)

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

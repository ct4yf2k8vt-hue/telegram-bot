import urllib.request, urllib.parse, json, time
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

T = os.environ.get("BOT_TOKEN")
C = "1623907197"

def tg(m):
    try:
        d = urllib.parse.urlencode({"chat_id": C, "text": m, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
        urllib.request.urlopen("https://api.telegram.org/bot" + T + "/sendMessage", d, timeout=10).read()
    except:
        pass

def gj(u):
    try:
        return json.loads(urllib.request.urlopen(u, timeout=10).read().decode())
    except:
        return None

def send_pump_message(coin, fiyat_degisim, borsalar):
    # Görseldeki gibi sade format
    msg = (f"🟢 <b>#{coin}: +{format(fiyat_degisim, '.2f')}%</b> 🔔 2\n"
           f"<code>{borsalar}</code>")
    tg(msg)

# ==========================================
# MOTOR 1: BINANCE FUTURES
# ==========================================
def binance_loop():
    print("--- BINANCE MOTORU BASLADI ---", flush=True)
    son = {}
    while True:
        try:
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if tickers:
                all_coins = [t["symbol"] for t in tickers if t["symbol"].endswith("USDT")]
                print(f"[{time.strftime('%H:%M:%S')}] BINANCE TARANIYOR | Coin: {len(all_coins)}", flush=True)
                for s in all_coins:
                    try:
                        k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=1m&limit=25")
                        if not k or len(k) < 22: continue
                        
                        canli = k[-1]
                        acilis = float(canli[1]); su_anki = float(canli[4]); hacim = float(canli[7])
                        fiyat_degisim = ((su_anki - acilis) / acilis) * 100
                        
                        v_ort = sum(float(x[7]) for x in k[-22:-2]) / 20
                        if v_ort == 0 or (hacim / v_ort) < 2.5: continue
                        
                        if fiyat_degisim >= 5.0:
                            if time.time() - son.get(s, 0) >= 1800:
                                coin = s.replace("USDT", "")
                                send_pump_message(coin, fiyat_degisim, "BINANCE")
                                son[s] = time.time()
                                print(f">>> BINANCE SINYAL: {s}", flush=True)
                        time.sleep(0.05)
                    except: pass
            time.sleep(60)
        except Exception as e:
            print("BINANCE HATA:", e, flush=True); time.sleep(10)

# ==========================================
# MOTOR 2: MEXC
# ==========================================
def mexc_loop():
    print("--- MEXC MOTORU BASLADI ---", flush=True)
    son = {}
    while True:
        try:
            tickers = gj("https://api.mexc.com/api/v3/ticker/24hr")
            if tickers:
                all_coins = [t["symbol"] for t in tickers if t.get("symbol", "").endswith("USDT")]
                print(f"[{time.strftime('%H:%M:%S')}] MEXC TARANIYOR | Coin: {len(all_coins)}", flush=True)
                for s in all_coins:
                    try:
                        k = gj(f"https://api.mexc.com/api/v3/klines?symbol={s}&interval=1m&limit=25")
                        if not k or len(k) < 22: continue
                        
                        canli = k[-1]
                        acilis = float(canli[1]); su_anki = float(canli[4]); hacim = float(canli[5])
                        fiyat_degisim = ((su_anki - acilis) / acilis) * 100
                        
                        v_ort = sum(float(x[5]) for x in k[-22:-2]) / 20
                        if v_ort == 0 or (hacim / v_ort) < 2.5: continue
                        
                        if fiyat_degisim >= 5.0:
                            if time.time() - son.get(s, 0) >= 1800:
                                coin = s.replace("USDT", "")
                                send_pump_message(coin, fiyat_degisim, "MEXC")
                                son[s] = time.time()
                                print(f">>> MEXC SINYAL: {s}", flush=True)
                        time.sleep(0.2)
                    except: pass
            time.sleep(60)
        except Exception as e:
            print("MEXC HATA:", e, flush=True); time.sleep(10)

# ==========================================
# MOTOR 3: BYBIT
# ==========================================
def bybit_loop():
    print("--- BYBIT MOTORU BASLADI ---", flush=True)
    son = {}
    while True:
        try:
            tickers = gj("https://api.bybit.com/v5/market/tickers?category=linear")
            if tickers and tickers.get("retCode") == 0:
                all_coins = [t["symbol"] for t in tickers["result"]["list"] if t["symbol"].endswith("USDT")]
                print(f"[{time.strftime('%H:%M:%S')}] BYBIT TARANIYOR | Coin: {len(all_coins)}", flush=True)
                for s in all_coins:
                    try:
                        k = gj(f"https://api.bybit.com/v5/market/kline?category=linear&symbol={s}&interval=1&limit=25")
                        if not k or k.get("retCode") != 0 or len(k["result"]["list"]) < 22: continue
                        
                        # Bybit tersten verir (en yeni ilk)
                        k_list = k["result"]["list"]
                        canli = k_list[0]
                        
                        acilis = float(canli[1]); su_anki = float(canli[4]); hacim = float(canli[5])
                        fiyat_degisim = ((su_anki - acilis) / acilis) * 100
                        
                        v_ort = sum(float(x[5]) for x in k_list[2:22]) / 20
                        if v_ort == 0 or (hacim / v_ort) < 2.5: continue
                        
                        if fiyat_degisim >= 5.0:
                            if time.time() - son.get(s, 0) >= 1800:
                                coin = s.replace("USDT", "")
                                send_pump_message(coin, fiyat_degisim, "BYBIT")
                                son[s] = time.time()
                                print(f">>> BYBIT SINYAL: {s}", flush=True)
                        time.sleep(0.1)
                    except: pass
            time.sleep(60)
        except Exception as e:
            print("BYBIT HATA:", e, flush=True); time.sleep(10)

# ==========================================
# MOTOR 4: OKX
# ==========================================
def okx_loop():
    print("--- OKX MOTORU BASLADI ---", flush=True)
    son = {}
    while True:
        try:
            tickers = gj("https://www.okx.com/api/v5/market/tickers?instType=SWAP")
            if tickers and tickers.get("code") == "0":
                all_coins = [t["instId"] for t in tickers["data"] if t["instId"].endswith("-USDT-SWAP")]
                print(f"[{time.strftime('%H:%M:%S')}] OKX TARANIYOR | Coin: {len(all_coins)}", flush=True)
                for s in all_coins:
                    try:
                        k = gj(f"https://www.okx.com/api/v5/market/candles?instId={s}&bar=1m&limit=25")
                        if not k or k.get("code") != "0" or len(k["data"]) < 22: continue
                        
                        # OKX tersten verir
                        k_list = k["data"]
                        canli = k_list[0]
                        
                        acilis = float(canli[1]); su_anki = float(canli[4]); hacim = float(canli[5])
                        fiyat_degisim = ((su_anki - acilis) / acilis) * 100
                        
                        v_ort = sum(float(x[5]) for x in k_list[2:22]) / 20
                        if v_ort == 0 or (hacim / v_ort) < 2.5: continue
                        
                        if fiyat_degisim >= 5.0:
                            if time.time() - son.get(s, 0) >= 1800:
                                coin = s.replace("-USDT-SWAP", "")
                                send_pump_message(coin, fiyat_degisim, "OKX")
                                son[s] = time.time()
                                print(f">>> OKX SINYAL: {s}", flush=True)
                        time.sleep(0.1)
                    except: pass
            time.sleep(60)
        except Exception as e:
            print("OKX HATA:", e, flush=True); time.sleep(10)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"Bot is running!")
    def do_HEAD(self):
        self.send_response(200); self.end_headers()

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print("WEB SUNUCUSU BASLADI - PORT:", port, flush=True)
    server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=binance_loop, daemon=True).start()
    threading.Thread(target=mexc_loop, daemon=True).start()
    threading.Thread(target=bybit_loop, daemon=True).start()
    threading.Thread(target=okx_loop, daemon=True).start()
    run_web_server()

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
        return json.loads(urllib.request.urlopen(u, timeout=15).read().decode())
    except:
        return None

# ==========================================
# MOTOR 1: BINANCE
# ==========================================
def binance_loop():
    print("--- BINANCE MOTORU BASLADI ---", flush=True)
    son = {}
    while True:
        try:
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if tickers:
                all_coins = [t["symbol"] for t in tickers if t["symbol"].endswith("USDT")]
                print("--- BINANCE TARANIYOR:", time.strftime("%H:%M:%S"), "| Coin:", len(all_coins), "---", flush=True)
                for s in all_coins:
                    try:
                        k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=5m&limit=25")
                        if not k or len(k) < 22: continue
                        acilis = float(k[-3][1]); su_anki = float(k[-1][4]); hacim = float(k[-1][7])
                        fiyat_degisim = ((su_anki - acilis) / acilis) * 100
                        if fiyat_degisim < 3.0: continue
                        onceki_20 = [float(x[7]) for x in k[-22:-2]]
                        v_ort = sum(onceki_20) / 20
                        if v_ort == 0 or (hacim / v_ort) < 3.0: continue
                        if (float(k[-2][4]) > float(k[-2][1]) and float(k[-1][4]) > float(k[-1][1])) and time.time() - son.get(s, 0) >= 1800:
                            pair = s.replace("USDT", "/USDT")
                            msg = (f"🚀 <b>Pump - {pair} [Binance]</b>\n"
                                   f"💰 Start Price: <code>{format(acilis, '.4f')}</code>\n"
                                   f"💰 Current Price: <code>{format(su_anki, '.4f')}</code> (+{format(fiyat_degisim, '.1f')}%)\n"
                                   f"📊 Volume: {format(hacim, '.0f')} (+{format((hacim/v_ort - 1) * 100, '.0f')} %)\n"
                                   f"⏱ Time: {time.strftime('%I:%M:%S %p', time.localtime())}\n\n"
                                   f"🔗 <a href='https://www.binance.com/en/futures/{s}'>Pair on Binance.</a>")
                            tg(msg); son[s] = time.time()
                            print(">>> BINANCE SINYAL:", s, flush=True)
                        time.sleep(0.05)
                    except: pass
            time.sleep(120)
        except Exception as e:
            print("BINANCE HATA:", e, flush=True); time.sleep(10)

# ==========================================
# MOTOR 2: KUCOIN
# ==========================================
def kucoin_loop():
    print("--- KUCOIN MOTORU BASLADI ---", flush=True)
    son = {}
    while True:
        try:
            kc_tickers = gj("https://api.kucoin.com/api/v1/market/allTickers")
            if kc_tickers and kc_tickers.get("code") == "200000":
                kc_coins = [t for t in kc_tickers["data"]["ticker"] if t["symbol"].endswith("-USDT")]
                print("--- KUCOIN TARANIYOR:", time.strftime("%H:%M:%S"), "| Coin:", len(kc_coins), "---", flush=True)
                for t in kc_coins:
                    try:
                        sym = t["symbol"]
                        k_kc = gj(f"https://api.kucoin.com/api/v1/market/candles?type=5min&symbol={sym}&limit=25")
                        if not k_kc or k_kc.get("code") != "200000" or len(k_kc["data"]) < 22: continue
                        k_list = k_kc["data"]
                        acilis = float(k_list[2][1]); su_anki = float(k_list[0][2]); hacim = float(k_list[0][6])
                        fiyat_degisim = ((su_anki - acilis) / acilis) * 100
                        if fiyat_degisim < 3.0: continue
                        v_ort = sum(float(x[6]) for x in k_list[3:23]) / 20
                        if v_ort == 0 or (hacim / v_ort) < 3.0: continue
                        if time.time() - son.get(sym, 0) >= 1800:
                            pair = sym.replace("-", "/")
                            msg = (f"🚀 <b>Pump - {pair} [KuCoin]</b>\n"
                                   f"💰 Start Price: <code>{format(acilis, '.4f')}</code>\n"
                                   f"💰 Current Price: <code>{format(su_anki, '.4f')}</code> (+{format(fiyat_degisim, '.1f')}%)\n"
                                   f"📊 Volume: {format(hacim, '.0f')} (+{format((hacim/v_ort - 1) * 100, '.0f')} %)\n"
                                   f"⏱ Time: {time.strftime('%I:%M:%S %p', time.localtime())}\n\n"
                                   f"🔗 <a href='https://www.kucoin.com/trade/{sym}'>Pair on KuCoin.</a>")
                            tg(msg); son[sym] = time.time()
                            print(">>> KUCOIN SINYAL:", sym, flush=True)
                        time.sleep(0.05)
                    except: pass
            time.sleep(120)
        except Exception as e:
            print("KUCOIN HATA:", e, flush=True); time.sleep(10)

# ==========================================
# MOTOR 3: MEXC (YENİ EKLENDİ)
# ==========================================
def mexc_loop():
    print("--- MEXC MOTORU BASLADI ---", flush=True)
    son = {}
    while True:
        try:
            # MEXC anlık ticker verilerini al
            mexc_tickers = gj("https://api.mexc.com/api/v3/ticker/24hr")
            if mexc_tickers:
                mexc_coins = [t for t in mexc_tickers if t.get("symbol", "").endswith("USDT")]
                print("--- MEXC TARANIYOR:", time.strftime("%H:%M:%S"), "| Coin:", len(mexc_coins), "---", flush=True)
                for t in mexc_coins:
                    try:
                        sym = t["symbol"]
                        # MEXC mum verisi (5 dakikalık)
                        k_mexc = gj(f"https://api.mexc.com/api/v3/klines?symbol={sym}&interval=5m&limit=25")
                        if not k_mexc or len(k_mexc) < 22: continue
                        
                        acilis = float(k_mexc[-3][1]); su_anki = float(k_mexc[-1][4]); hacim = float(k_mexc[-1][5])
                        fiyat_degisim = ((su_anki - acilis) / acilis) * 100
                        if fiyat_degisim < 3.0: continue
                        
                        # Önceki 20 mumun ortalama hacmi
                        v_ort = sum(float(x[5]) for x in k_mexc[-22:-2]) / 20
                        if v_ort == 0 or (hacim / v_ort) < 3.0: continue
                        
                        if time.time() - son.get(sym, 0) >= 1800:
                            pair = sym.replace("USDT", "/USDT")
                            msg = (f"🚀 <b>Pump - {pair} [MEXC]</b>\n"
                                   f"💰 Start Price: <code>{format(acilis, '.4f')}</code>\n"
                                   f"💰 Current Price: <code>{format(su_anki, '.4f')}</code> (+{format(fiyat_degisim, '.1f')}%)\n"
                                   f"📊 Volume: {format(hacim, '.0f')} (+{format((hacim/v_ort - 1) * 100, '.0f')} %)\n"
                                   f"⏱ Time: {time.strftime('%I:%M:%S %p', time.localtime())}\n\n"
                                   f"🔗 <a href='https://www.mexc.com/exchange/{sym}'>Pair on MEXC.</a>")
                            tg(msg); son[sym] = time.time()
                            print(">>> MEXC SINYAL:", sym, flush=True)
                        time.sleep(0.05)
                    except: pass
            time.sleep(120)
        except Exception as e:
            print("MEXC HATA:", e, flush=True); time.sleep(10)

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
    threading.Thread(target=kucoin_loop, daemon=True).start()
    threading.Thread(target=mexc_loop, daemon=True).start()
    run_web_server()

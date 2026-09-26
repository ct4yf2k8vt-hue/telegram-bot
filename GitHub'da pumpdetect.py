import urllib.request, urllib.parse, json, time
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

T = os.environ.get("BOT_TOKEN")
C = os.environ.get("CHAT_ID")

def tg(m):
    try:
        d = urllib.parse.urlencode({"chat_id": C, "text": m, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
        urllib.request.urlopen("https://api.telegram.org/bot" + T + "/sendMessage", d, timeout=10).read()
    except Exception as e:
        print("TG HATASI:", e)

def gj(u):
    try:
        return json.loads(urllib.request.urlopen(u, timeout=20).read().decode())
    except:
        return None

def bot_loop():
    print("BOT LOOP BASLADI")
    S = []
    while not S:
        info = gj("https://fapi.binance.com/fapi/v1/exchangeInfo")
        if info and "symbols" in info:
            S = [x["symbol"] for x in info["symbols"] if x["status"] == "TRADING" and x["quoteAsset"] == "USDT" and x["contractType"] == "PERPETUAL"]
        if not S:
            print("Binance API bekleniyor...")
            time.sleep(10)
    print("COIN SAYISI:", len(S))
    tg("PUMP DETECT BOTU AKTIF " + str(len(S)))
    son = {}
    while True:
        try:
            for s in S:
                try:
                    k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=4h&limit=30")
                    if not k or len(k) < 22:
                        continue
                    m = k[-2]
                    o = float(m[1])
                    c = float(m[4])
                    v = float(m[5])
                    if o == 0 or c == 0:
                        continue
                    degisim = ((c - o) / o) * 100
                    v_ort = sum(float(x[5]) for x in k[-22:-2]) / 20
                    if v_ort == 0:
                        continue
                    v_oran = v / v_ort
                    if abs(degisim) >= 3 and v_oran >= 2:
                        if time.time() - son.get(s, 0) >= 1800:
                            v_candle = v * c
                            v24 = sum(float(x[5]) for x in k[-6:]) * c
                            if degisim > 0:
                                emoji = "🚀"
                                yazi = "#" + s + " - PUMP"
                            else:
                                emoji = "⚠️"
                                yazi = "#" + s + " - DUMP"
                            msg = (emoji + " <b>" + yazi + "</b>\n"
                                   "Price: $" + format(o, ".4f") + " ➜ $" + format(c, ".4f") + " (" + ("+" if degisim > 0 else "") + format(degisim, ".2f") + "%)\n"
                                   "Volume Candle: $" + format(v_candle / 1000000, ".4f") + "M\n"
                                   "Volume 24h: $" + format(v24 / 1000000, ".2f") + "M\n\n"
                                   "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                                   "Link: marketowl.eu")
                            tg(msg)
                            son[s] = time.time()
                            print(s, yazi, format(degisim, ".2f"))
                except:
                    pass
            print("Tarama bitti")
            time.sleep(300)
        except Exception as e:
            print("ANA HATA:", e)
            time.sleep(30)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()

if __name__ == "__main__":
    print("BASLATILIYOR...")
    bot_thread = threading.Thread(target=bot_loop)
    bot_thread.daemon = True
    bot_thread.start()
    run_web_server()

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
        return json.loads(urllib.request.urlopen(u, timeout=20).read().decode())
    except:
        return None

def bot_loop():
    S = []
    while not S:
        info = gj("https://fapi.binance.com/fapi/v1/exchangeInfo")
        if info and "symbols" in info:
            S = [x["symbol"] for x in info["symbols"] if x["status"] == "TRADING" and x["quoteAsset"] == "USDT" and x["contractType"] == "PERPETUAL"]
        if not S:
            print("Binance API yanit vermedi, 10 saniye sonra tekrar denenecek...")
            time.sleep(10)
    tg("ANORMAL VOLATILITE BOTU AKTIF " + str(len(S)))
    son = {}
    while True:
        try:
            for s in S:
                try:
                    k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=15m&limit=50")
                    if not k or len(k) < 20:
                        continue
                    # Son kapanmis mumun ani degisimi
                    m = k[-2]  # son kapanmis mum
                    o = float(m[1])
                    c = float(m[4])
                    h = float(m[2])
                    l = float(m[3])
                    if o == 0:
                        continue
                    degisim = ((c - o) / o) * 100
                    # Anormal volatilite esigi: %5 ve uzeri
                    if abs(degisim) >= 5:
                        if time.time() - son.get(s, 0) >= 1800:
                            yon = "PUMP" if degisim > 0 else "DUMP"
                            emoji = "🟢" if degisim > 0 else "🔴"
                            yon_yazi = "ANORMAL YUKSELIS" if degisim > 0 else "ANORMAL DUSUS"
                            msg = (emoji + " <b>" + yon_yazi + " ALARMI</b>\n"
                                   "[" + s + "] (15m)\n\n"
                                   "Fiyat: <code>" + format(o, ".6f") + "</code> -> <code>" + format(c, ".6f") + "</code>\n"
                                   "Degisim: <b>" + format(degisim, ".2f") + "%</b>\n"
                                   "Yuksek: <code>" + format(h, ".6f") + "</code>\n"
                                   "Dusuk: <code>" + format(l, ".6f") + "</code>\n\n"
                                   "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                                   "Link: marketowl.eu")
                            tg(msg)
                            son[s] = time.time()
                            print(s, yon, format(degisim, ".2f"))
                except:
                    pass
            print("Tarama bitti")
            time.sleep(300)
        except Exception as e:
            print("Hata:", e)
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
    bot_thread = threading.Thread(target=bot_loop)
    bot_thread.daemon = True
    bot_thread.start()
    run_web_server()

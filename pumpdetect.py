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
    tg("YILLIK ZIRVE/DIP BOTU AKTIF " + str(len(S)))
    son_zirve = {}
    son_dip = {}
    while True:
        try:
            for s in S:
                try:
                    kd = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=1d&limit=365")
                    if not kd or len(kd) < 30:
                        continue
                    c_d = float(kd[-1][4])
                    h_d = [float(x[2]) for x in kd]
                    l_d = [float(x[3]) for x in kd]
                    yillik_max = max(h_d)
                    yillik_min = min(l_d)
                    tol = 0.005
                    if c_d >= yillik_max * (1 - tol):
                        if time.time() - son_zirve.get(s, 0) >= 1800:
                            msg = ("🔺 <b>YILLIK ZIRVE</b>\n"
                                   "Signal: #" + s + "\n"
                                   "Fiyat: <code>" + format(c_d, ".6f") + "</code>\n"
                                   "Yillik Max: <code>" + format(yillik_max, ".6f") + "</code>\n"
                                   "Yakinlik: %" + format((c_d / yillik_max) * 100, ".2f") + "\n\n"
                                   "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                                   "Link: marketowl.eu")
                            tg(msg)
                            son_zirve[s] = time.time()
                            print(s, "YILLIK ZIRVE")
                    if c_d <= yillik_min * (1 + tol):
                        if time.time() - son_dip.get(s, 0) >= 1800:
                            msg = ("🔻 <b>YILLIK DIP</b>\n"
                                   "Signal: #" + s + "\n"
                                   "Fiyat: <code>" + format(c_d, ".6f") + "</code>\n"
                                   "Yillik Min: <code>" + format(yillik_min, ".6f") + "</code>\n"
                                   "Yakinlik: %" + format((c_d / yillik_min) * 100, ".2f") + "\n\n"
                                   "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                                   "Link: marketowl.eu")
                            tg(msg)
                            son_dip[s] = time.time()
                            print(s, "YILLIK DIP")
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

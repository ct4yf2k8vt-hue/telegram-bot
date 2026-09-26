import urllib.request, urllib.parse, json, time
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

T = os.environ.get("BOT_TOKEN")
C = "1623907197"

def tg(ci, m):
    try:
        d = urllib.parse.urlencode({"chat_id": ci, "text": m, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
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
    tg(C, "ZIRVE/DIP BOTU AKTIF " + str(len(S)))
    son_zirve = {}
    son_dip = {}
    while True:
        try:
            for s in S:
                try:
                    kd = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=1d&limit=365")
                    if kd and len(kd) >= 32:
                        c_d = float(kd[-1][4])
                        h_d = [float(x[2]) for x in kd]
                        l_d = [float(x[3]) for x in kd]
                        aylik_max = max(h_d[-32:])
                        aylik_min = min(l_d[-32:])
                        yillik_max = max(h_d[-365:])
                        yillik_min = min(l_d[-365:])
                        tol = 0.005
                        if c_d >= aylik_max * (1 - tol):
                            if time.time() - son_zirve.get(s + "_A", 0) >= 1800:
                                msg = ("🔺 <b>AYLIK ZIRVE</b>\n"
                                       "Signal: #" + s + "\n"
                                       "Fiyat: <code>" + format(c_d, ".6f") + "</code>\n"
                                       "Aylik Max: <code>" + format(aylik_max, ".6f") + "</code>\n\n"
                                       "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                                       "Link: marketowl.eu")
                                tg(C, msg)
                                son_zirve[s + "_A"] = time.time()
                                print(s, "AYLIK ZIRVE")
                        if c_d <= aylik_min * (1 + tol):
                            if time.time() - son_dip.get(s + "_A", 0) >= 1800:
                                msg = ("🔻 <b>AYLIK DIP</b>\n"
                                       "Signal: #" + s + "\n"
                                       "Fiyat: <code>" + format(c_d, ".6f") + "</code>\n"
                                       "Aylik Min: <code>" + format(aylik_min, ".6f") + "</code>\n\n"
                                       "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                                       "Link: marketowl.eu")
                                tg(C, msg)
                                son_dip[s + "_A"] = time.time()
                                print(s, "AYLIK DIP")
                        if c_d >= yillik_max * (1 - tol):
                            if time.time() - son_zirve.get(s + "_Y", 0) >= 1800:
                                msg = ("🔺 <b>YILLIK ZIRVE</b>\n"
                                       "Signal: #" + s + "\n"
                                       "Fiyat: <code>" + format(c_d, ".6f") + "</code>\n"
                                       "Yillik Max: <code>" + format(yillik_max, ".6f") + "</code>\n\n"
                                       "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                                       "Link: marketowl.eu")
                                tg(C, msg)
                                son_zirve[s + "_Y"] = time.time()
                                print(s, "YILLIK ZIRVE")
                        if c_d <= yillik_min * (1 + tol):
                            if time.time() - son_dip.get(s + "_Y", 0) >= 1800:
                                msg = ("🔻 <b>YILLIK DIP</b>\n"
                                       "Signal: #" + s + "\n"
                                       "Fiyat: <code>" + format(c_d, ".6f") + "</code>\n"
                                       "Yillik Min: <code>" + format(yillik_min, ".6f") + "</code>\n\n"
                                       "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                                       "Link: marketowl.eu")
                                tg(C, msg)
                                son_dip[s + "_Y"] = time.time()
                                print(s, "YILLIK DIP")
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

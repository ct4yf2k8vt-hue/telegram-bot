import os
import time
import threading
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

T = os.environ.get("BOT_TOKEN") or os.environ.get("BOT-TOKEN")
C = os.environ.get("CHAT_ID") or os.environ.get("CHAT-ID")

def tg(m):
    try:
        d = urllib.parse.urlencode({"chat_id": C, "text": m, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
        urllib.request.urlopen("https://api.telegram.org/bot" + T + "/sendMessage", d, timeout=10).read()
        print("MESAJ GONDERILDI")
    except Exception as e:
        print("TG HATASI:", e)

def gj(u):
    try:
        return __import__("json").loads(urllib.request.urlopen(u, timeout=20).read().decode())
    except Exception as e:
        print("GJ HATASI:", e)
        return None

def bot_loop():
    print("BASLATILIYOR...")
    print("TOKEN VAR MI:", T is not None)
    print("CHAT_ID VAR MI:", C is not None)
    tg("YILLIK ZIRVE/DIP BOTU BASLATILDI")
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
    while True:
        try:
            zirveler = []
            dipler = []
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
                    tol = 0.01
                    if c_d >= yillik_max * (1 - tol):
                        zirveler.append((c_d / yillik_max, s, c_d, yillik_max))
                    if c_d <= yillik_min * (1 + tol):
                        dipler.append((c_d / yillik_min, s, c_d, yillik_min))
                except:
                    pass
            zirveler.sort(reverse=True)
            dipler.sort()
            if zirveler:
                msg = "🔺 <b>YILLIK ZIRVE (365 GUN)</b>\n\n"
                for yakinlik, s, fiyat, seviye in zirveler[:20]:
                    msg += ("<b>" + s + "</b>\nFiyat: <code>" + format(fiyat, ".6f") + "</code>\nYillik Max: <code>" + format(seviye, ".6f") + "</code>\nYakinlik: %" + format(yakinlik * 100, ".2f") + "\n\n")
                msg += "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\nLink: marketowl.eu"
                tg(msg)
                print("ZIRVE:", len(zirveler))
            if dipler:
                msg = "🔻 <b>YILLIK DIP (365 GUN)</b>\n\n"
                for yakinlik, s, fiyat, seviye in dipler[:20]:
                    msg += ("<b>" + s + "</b>\nFiyat: <code>" + format(fiyat, ".6f") + "</code>\nYillik Min: <code>" + format(seviye, ".6f") + "</code>\nYakinlik: %" + format(yakinlik * 100, ".2f") + "\n\n")
                msg += "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\nLink: marketowl.eu"
                tg(msg)
                print("DIP:", len(dipler))
            print("Tarama bitti")
            time.sleep(1800)
        except Exception as e:
            print("ANA HATA:", e)
            time.sleep(30)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running!")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    print("WEB SUNUCUSU BASLADI - PORT:", port)
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()

if __name__ == "__main__":
    print("MAIN BASLADI")
    bot_thread = threading.Thread(target=bot_loop)
    bot_thread.daemon = True
    bot_thread.start()
    run_web_server()

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
    except Exception as e:
        print("GJ HATASI:", e)
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
    tg("XDECOW TARZI BOT AKTIF " + str(len(S)))
    while True:
        try:
            sonuclar = []
            for s in S:
                try:
                    k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=15m&limit=2")
                    if not k or len(k) < 2:
                        continue
                    m = k[-2]
                    trades = int(m[8])
                    fiyat = float(m[4])
                    degisim = ((float(m[4]) - float(m[1])) / float(m[1])) * 100
                    sonuclar.append((trades, s, fiyat, degisim))
                except:
                    pass
            sonuclar.sort(reverse=True)
            sonuclar = sonuclar[:15]
            if sonuclar:
                msg = ("📊 <b>XDECOW - TRADES EXECUTED (15m)</b>\n\n"
                       "<pre>Symbol        Trades      Price      Chg%\n")
                msg += "─" * 40 + "\n"
                for trades, s, fiyat, degisim in sonuclar:
                    msg += "{:<12} {:<10} {:<10} {:<8}\n".format(s, trades, format(fiyat, ".4f"), format(degisim, ".2f") + "%")
                msg += "</pre>\n"
                msg += "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                msg += "Link: marketowl.eu"
                tg(msg)
                print("Tablo gonderildi")
            else:
                print("Sonuc yok")
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

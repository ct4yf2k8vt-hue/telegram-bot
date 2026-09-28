import os
import time
import json
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
        return json.loads(urllib.request.urlopen(u, timeout=20).read().decode())
    except Exception as e:
        print("GJ HATASI:", e)
        return None

def bot_loop():
    print("BASLATILIYOR...")
    tg("ARDI ARDA YUKSELIS/DUSUS BOTU BASLATILDI")
    S = []
    while not S:
        info = gj("https://fapi.binance.com/fapi/v1/exchangeInfo")
        if info and "symbols" in info:
            S = [x["symbol"] for x in info["symbols"] if x["status"] == "TRADING" and x["quoteAsset"] == "USDT" and x["contractType"] == "PERPETUAL"]
        if not S:
            print("Binance API bekleniyor...")
            time.sleep(10)
    print("COIN SAYISI:", len(S))
    tg("ARDI ARDA YUKSELIS/DUSUS BOTU AKTIF " + str(len(S)))
    while True:
        try:
            yukselenler = []
            dusenler = []
            for s in S:
                try:
                    # 15 dakikalik son 5 mumu al
                    k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=15m&limit=6")
                    if not k or len(k) < 6:
                        continue
                    kapanmis = k[:-1]  # son kapanmis mumlari al
                    if len(kapanmis) < 5:
                        continue
                    c1 = float(kapanmis[-5][4])
                    c2 = float(kapanmis[-4][4])
                    c3 = float(kapanmis[-3][4])
                    c4 = float(kapanmis[-2][4])
                    c5 = float(kapanmis[-1][4])
                    # Ardi ardina 5 yesil mum (yukselis)
                    if c1 < c2 < c3 < c4 < c5:
                        degisim = ((c5 - c1) / c1) * 100
                        yukselenler.append((degisim, s, c1, c5))
                    # Ardi ardina 5 kirmizi mum (dusus)
                    if c1 > c2 > c3 > c4 > c5:
                        degisim = ((c5 - c1) / c1) * 100
                        dusenler.append((degisim, s, c1, c5))
                except:
                    pass
            yukselenler.sort(reverse=True)
            dusenler.sort()
            if yukselenler:
                msg = "🟢 <b>ARDI ARDA YUKSELIS (5 MUM)</b>\n\n"
                for degisim, s, ilk, son in yukselenler[:15]:
                    msg += ("<b>" + s + "</b>\n"
                            "Ilk: <code>" + format(ilk, ".6f") + "</code>\n"
                            "Son: <code>" + format(son, ".6f") + "</code>\n"
                            "Degisim: <b>+" + format(degisim, ".2f") + "%</b>\n\n")
                msg += "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                msg += "Link: marketowl.eu"
                tg(msg)
                print("YUKSELIS:", len(yukselenler))
            if dusenler:
                msg = "🔴 <b>ARDI ARDA DUSUS (5 MUM)</b>\n\n"
                for degisim, s, ilk, son in dusenler[:15]:
                    msg += ("<b>" + s + "</b>\n"
                            "Ilk: <code>" + format(ilk, ".6f") + "</code>\n"
                            "Son: <code>" + format(son, ".6f") + "</code>\n"
                            "Degisim: <b>" + format(degisim, ".2f") + "%</b>\n\n")
                msg += "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                msg += "Link: marketowl.eu"
                tg(msg)
                print("DUSUS:", len(dusenler))
            print("Tarama bitti")
            time.sleep(300)
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
    bot_thread = threading.Thread(target=bot_loop)
    bot_thread.daemon = True
    bot_thread.start()
    run_web_server()

import urllib.request, urllib.parse, json, time
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

T = os.environ.get("BOT_TOKEN") or os.environ.get("BOT-TOKEN")
C = os.environ.get("CHAT_ID") or os.environ.get("CHAT-ID")

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
    tg(C, "XDECOW TARZI TRADES TABLOSU AKTIF " + str(len(S)))
    while True:
        try:
            sonuclar = []
            for s in S:
                try:
                    k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=15m&limit=3")
                    if not k or len(k) < 3:
                        continue
                    # Son kapanmis mum (15m)
                    m = k[-2]
                    trades = int(m[8])
                    # Onceki kapanmis mum (15m)
                    m_onceki = k[-3]
                    trades_onceki = int(m_onceki[8])
                    # Trades Change
                    trades_change = trades - trades_onceki
                    fiyat = float(m[4])
                    sonuclar.append((trades, s, trades_change, fiyat))
                except:
                    pass
            # Trades Executed'a gore sirala (en yuksekten en dusuge)
            sonuclar.sort(reverse=True)
            sonuclar = sonuclar[:15]
            if sonuclar:
                msg = ("📊 <b>XDECOW - TRADES (15m)</b>\n\n"
                       "<pre>Symbol        Change      Executed     Last Price\n")
                msg += "─" * 50 + "\n"
                for trades, s, change, fiyat in sonuclar:
                    change_str = ("+" if change >= 0 else "") + format(change / 1000, ".2f") + "K"
                    trades_str = format(trades / 1000, ".2f") + "K"
                    msgin += "{:<12}

 {:<12} {:<12}** {:<12}\n".format(s, change_str, trades_str, format(fiyat, ".4f"))
                msg += "</pre>\n"
                msg += "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                msg += "Link: marketowl.eu"
                tg(C, msg)
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

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

def atr(h, l, c, n=14):
    if len(c) < n + 1: return None
    tr = []
    for i in range(1, len(c)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1])))
    if len(tr) < n: return None
    a = sum(tr[:n]) / n
    for x in tr[n:]:
        a = (a * (n - 1) + x) / n
    return a

def bot_loop():
    S = []
    while not S:
        info = gj("https://fapi.binance.com/fapi/v1/exchangeInfo")
        if info and "symbols" in info:
            S = [x["symbol"] for x in info["symbols"] if x["status"] == "TRADING" and x["quoteAsset"] == "USDT" and x["contractType"] == "PERPETUAL"]
        if not S:
            print("Binance API yanit vermedi, 10 saniye sonra tekrar denenecek...")
            time.sleep(10)
    tg("ORACLE EASY BOT AKTIF " + str(len(S)))
    son = {}
    while True:
        try:
            for s in S:
                try:
                    # 1 saatlik veri
                    k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=1h&limit=50")
                    if not k or len(k) < 20:
                        continue
                    m = k[-2]  # son kapanmis mum
                    o = float(m[1])
                    h = float(m[2])
                    l = float(m[3])
                    c = float(m[4])
                    if o == 0:
                        continue
                    # Yon belirleme
                    yon = "LONG" if c > o else "SHORT"
                    # Hacim akisi (son 6 saat)
                    hacim_6s = sum(float(x[5]) for x in k[-7:-1])
                    taker_buy = sum(float(x[9]) for x in k[-7:-1])
                    if hacim_6s == 0:
                        continue
                    buy_pct = (taker_buy / hacim_6s) * 100
                    sell_pct = 100 - buy_pct
                    # ATR hesapla
                    h_list = [float(x[2]) for x in k]
                    l_list = [float(x[3]) for x in k]
                    c_list = [float(x[4]) for x in k]
                    a = atr(h_list, l_list, c_list)
                    if a is None or a == 0:
                        continue
                    # Entry, Stop, TP1
                    if yon == "LONG":
                        entry_low = c - a * 0.2
                        entry_high = c + a * 0.2
                        sl = c - a * 1.5
                        tp1 = c + a * 1.5
                    else:
                        entry_low = c - a * 0.2
                        entry_high = c + a * 0.2
                        sl = c + a * 1.5
                        tp1 = c - a * 1.5
                    sl_pct = ((sl - c) / c) * 100
                    tp1_pct = ((tp1 - c) / c) * 100
                    if time.time() - son.get(s, 0) >= 1800:
                        emoji = "🔴" if yon == "SHORT" else "🟢"
                        msg = (emoji + " <b>" + yon + " $" + s.replace("USDT", "") + "</b> (" + s + ") · 1H\n"
                               "📊 Volume flow 6h · " + format(buy_pct, ".0f") + "% up / " + format(sell_pct, ".0f") + "% down\n"
                               "Entry " + format(entry_low, ".4f") + " — " + format(entry_high, ".4f") + " USDT\n"
                               "🎯 Market " + format(c, ".4f") + " (" + format(((c - c) / c) * 100, ".1f") + "% vs entry)\n"
                               "🛑 SL " + format(sl, ".4f") + " (" + format(sl_pct, ".1f") + "%)\n"
                               "🎯 TP1 " + format(tp1, ".4f") + " (" + format(tp1_pct, ".1f") + "%)\n"
                               "🔒 TP2 & exit alerts → premium\n\n"
                               "t.me/oracle_easy · #" + s.replace("USDT", ""))
                        tg(msg)
                        son[s] = time.time()
                        print(s, yon)
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

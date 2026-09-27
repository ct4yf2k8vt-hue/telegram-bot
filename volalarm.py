import urllib.request, urllib.parse, json, time
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

T = os.environ.get("BOT_TOKEN") or os.environ.get("BOT-TOKEN")
C = os.environ.get("CHAT_ID") or os.environ.get("CHAT-ID")

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

def sma(v, n):
    if len(v) < n: return None
    return sum(v[-n:]) / n

def std(v, n):
    if len(v) < n: return None
    m = sum(v[-n:]) / n
    return (sum((x - m) ** 2 for x in v[-n:]) / n) ** 0.5

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
    tg("BIRLESIK BOT AKTIF " + str(len(S)))
    print("TELEGRAM MESAJI GONDERILDI")
    son_vol = {}
    sayac = 0
    while True:
        try:
            # --- 1. ANORMAL VOLATILITE ---
            for s in S:
                try:
                    k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=15m&limit=50")
                    if k and len(k) >= 30:
                        c = [float(x[4]) for x in k][:-1]
                        h = [float(x[2]) for x in k][:-1]
                        l = [float(x[3]) for x in k][:-1]
                        v = [float(x[5]) for x in k][:-1]
                        sma20 = sma(c, 20)
                        st20 = std(c, 20)
                        if sma20 and st20:
                            bu = sma20 + 2 * st20
                            bl = sma20 - 2 * st20
                            bw = (bu - bl) / sma20 if sma20 else 0
                            bw_list = []
                            for i in range(20, len(c)):
                                s20 = sma(c[:i], 20)
                                t20 = std(c[:i], 20)
                                if s20 and t20 and s20 > 0:
                                    bw_list.append(((s20 + 2 * t20) - (s20 - 2 * t20)) / s20)
                            if len(bw_list) >= 20:
                                bw_ort = sum(bw_list[-20:]) / 20
                                if bw_ort > 0:
                                    squeeze = bw < bw_ort * 0.7
                                    f = c[-1]
                                    breakout = f > bu or f < bl
                                    a = atr(h, l, c)
                                    if a and f > 0:
                                        atr_pct = (a / f) * 100
                                        if squeeze and breakout and atr_pct > 2:
                                            if time.time() - son_vol.get(s, 0) >= 1800:
                                                yon = "LONG" if f > bu else "SHORT"
                                                emoji = "🟢" if yon == "LONG" else "🔴"
                                                chg = ((f - c[-2]) / c[-2]) * 100 if len(c) > 1 else 0
                                                v_son = v[-1]
                                                v_ort = sum(v[-20:]) / 20
                                                v_oran = v_son / v_ort if v_ort > 0 else 0
                                                msg = (emoji + " <b>ANORMAL VOLATILITE ALARMI</b>\n"
                                                       "COIN: <b>" + s + "</b> (15m)\n"
                                                       "YON: <b>" + yon + "</b>\n\n"
                                                       "Fiyat: <code>" + format(f, ".6f") + "</code> (" + format(chg, ".2f") + "%)\n"
                                                       "Bollinger: <code>" + format(bl, ".6f") + "</code> - <code>" + format(bu, ".6f") + "</code>\n"
                                                       "ATR: <code>" + format(a, ".6f") + "</code> (" + format(atr_pct, ".2f") + "%)\n"
                                                       "Hacim: <code>" + format(v_oran, ".2f") + "x</code>\n"
                                                       "Sikisma: <code>" + format(bw / bw_ort * 100, ".0f") + "%</code>\n\n"
                                                       "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                                                       "Link: marketowl.eu")
                                                tg(msg)
                                                son_vol[s] = time.time()
                                                print(s, "VOL", yon)
                except:
                    pass
            # --- 2. XDECOW TRADES TABLOSU (her 5 dongude bir) ---
            sayac += 1
            if sayac >= 1:
                sayac = 0
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

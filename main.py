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
    S = [x["symbol"] for x in gj("https://fapi.binance.com/fapi/v1/exchangeInfo")["symbols"] if x["status"] == "TRADING" and x["quoteAsset"] == "USDT" and x["contractType"] == "PERPETUAL"]
    tg(C, "VOLATILITE + ZIRVE/DIP BOTU AKTIF " + str(len(S)))
    son_vol = {}
    son_zirve = {}
    son_dip = {}
    
    while True:
        try:
            for s in S:
                try:
                    # --- 1. ANORMAL VOLATILITE KONTROLU (Mevcut Sistem) ---
                    k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=15m&limit=100")
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
                                                tg(C, msg)
                                                son_vol[s] = time.time()
                                                print(s, "VOL", yon)

                    # --- 2. AYLIK/YILLIK ZIRVE-DIP KONTROLU (Yeni Sistem) ---
                    kd = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=1d&limit=365")
                    if kd and len(kd) >= 32:
                        c_d = float(kd[-1][4])
                        h_d = [float(x[2]) for x in kd]
                        l_d = [float(x[3]) for x in kd]
                        
                        aylik_max = max(h_d[-32:])
                        aylik_min = min(l_d[-32:])
                        yillik_max = max(h_d[-365:])
                        yillik_min = min(l_d[-365:])
                        
                        # Yakinlik toleransi (%0.5)
                        tol = 0.005
                        
                        # Aylik Zirve
                        if c_d >= acekylik_max * (1 - tol):
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
                                
                        # Aylik Dip
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
                                
                        # Yillik Zirve
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
                                
                        # Yillik Dip
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

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()

if __name__ == "__main__":
    bot_thread = threading.Thread(target=bot_loop)
    bot_thread.daemon = True
    bot_thread.start()
    run_web_server()

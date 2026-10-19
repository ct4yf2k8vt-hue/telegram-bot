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
        return json.loads(urllib.request.urlopen(u, timeout=15).read().decode())
    except:
        return None

def bot_loop():
    print("1. BOT LOOP BASLADI")
    S = []
    while not S:
        info = gj("https://fapi.binance.com/fapi/v1/exchangeInfo")
        if info and "symbols" in info:
            S = [x["symbol"] for x in info["symbols"] if x["status"] == "TRADING" and x["quoteAsset"] == "USDT" and x["contractType"] == "PERPETUAL"]
        if not S:
            print("Coinler alinamadi, 10 sn sonra tekrar denenecek...")
            time.sleep(10)
            
    print("2. COIN SAYISI:", len(S))
    tg(C, "PUMP ALERT BOTU AKTIF " + str(len(S)))
    son = {}

    while True:
        try:
            print("3. TARAMA BASLIYOR...", time.strftime("%H:%M:%S"))
            sayac = 0
            for s in S:
                try:
                    # Son 25 tane 5 dakikalik mum al
                    k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=5m&limit=25")
                    if not k or len(k) < 22:
                        continue
                    
                    son_mum = k[-2]
                    onceki_mum = k[-3]
                    fiyat_simdi = float(son_mum[4])
                    fiyat_onceki = float(onceki_mum[4])
                    hacim_usdt = float(son_mum[7])
                    
                    # Fiyat degisimi
                    fiyat_degisim = ((fiyat_simdi - fiyat_onceki) / fiyat_onceki) * 100
                    
                    # Onceki 20 mumun ortalama hacmi
                    onceki_20 = [float(x[7]) for x in k[-22:-2]]
                    v_ort = sum(onceki_20) / 20
                    if v_ort == 0:
                        continue
                    
                    hacim_orani = hacim_usdt / v_ort
                    hacim_artis_pct = (hacim_orani - 1) * 100
                    
                    # GÖRSELDEKİ GİBİ BÜYÜK PUMP FİLTRESİ (Hacim en az 3x, Fiyat en az %3)
                    if hacim_orani >= 3.0 and fiyat_degisim >= 3.0:
                        # 30 dakika cooldown
                        if time.time() - son.get(s, 0) >= 1800:
                            emoji = "🚀"
                            hacim_farki = hacim_usdt - v_ort
                            pair = s.replace("USDT", "/USDT")
                            coin = s.replace("USDT", "")
                            
                            msg = (emoji + " <b>Pump - " + pair + " [Binance]</b>\n"
                                   "💵 Price: <code>" + format(fiyat_onceki, ".5f") + "</code> ➜ <code>" + format(fiyat_simdi, ".5f") + "</code> (+" + format(fiyat_degisim, ".1f") + "%)\n"
                                   "📊 Volume: " + format(hacim_usdt, ".0f") + " (+" + format(hacim_artis_pct, ".0f") + " %)\n"
                                   "Volume increased by " + format(hacim_farki, ".0f") + " ⬆️\n"
                                   "Time: " + time.strftime("%I:%M:%S %p", time.localtime()) + " 🕒\n\n"
                                   "🔗 <a href='https://www.binance.com/en/futures/" + s + "'>Pair on Binance.</a>\n\n"
                                   "$" + coin + " #" + coin)
                            tg(C, msg)
                            son[s] = time.time()
                            sayac += 1
                            print(">>> SINYAL GONDERILDI:", s)
                except:
                    pass
            print("4. TARAMA BITTI - Sinyal:", sayac, time.strftime("%H:%M:%S"))
            time.sleep(300)
        except Exception as e:
            print("LOOP HATASI (Kendini yeniden baslatacak):", e)
            time.sleep(10)

# Render Web Sunucusu
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
    print("WEB SUNUCUSU BASLADI - PORT:", port)
    server.serve_forever()

if __name__ == "__main__":
    bot_thread = threading.Thread(target=bot_loop)
    bot_thread.daemon = True
    bot_thread.start()
    run_web_server()

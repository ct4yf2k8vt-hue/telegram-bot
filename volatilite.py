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
    print("--- 1. BOT LOOP BASLADI ---", flush=True)
    tg(C, "TAM KAPSAMLI PUMP BOTU AKTIF! (Tum Coinler)")
    
    son = {}

    while True:
        try:
            print("--- 2. TUM BINANCE FUTURES COINLERI ALINIYOR ---", flush=True)
            info = gj("https://fapi.binance.com/fapi/v1/exchangeInfo")
            if not info or "symbols" not in info:
                print("API Hatasi, 10 sn sonra tekrar denenecek...", flush=True)
                time.sleep(10)
                continue
                
            # TUM USDT PERPETUAL COINLERI (Filtresiz)
            all_coins = [x["symbol"] for x in info["symbols"] if x["status"] == "TRADING" and x["quoteAsset"] == "USDT" and x["contractType"] == "PERPETUAL"]
            print("--- 3. TARANACAK COIN SAYISI:", len(all_coins), "---", flush=True)

            print("--- 4. TARAMA BASLIYOR...", time.strftime("%H:%M:%S"), "---", flush=True)
            sayac = 0
            
            for s in all_coins:
                try:
                    # 5 dakikalik mumlar
                    k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=5m&limit=25")
                    if not k or len(k) < 22:
                        continue
                    
                    son_mum = k[-2]
                    onceki_mum = k[-3]
                    fiyat_simdi = float(son_mum[4])
                    fiyat_onceki = float(onceki_mum[4])
                    hacim_usdt = float(son_mum[7])
                    
                    fiyat_degisim = ((fiyat_simdi - fiyat_onceki) / fiyat_onceki) * 100
                    
                    onceki_20 = [float(x[7]) for x in k[-22:-2]]
                    v_ort = sum(onceki_20) / 20
                    
                    # Eger ortalama hacim 0 ise (hic islem yoksa) atla
                    if v_ort == 0:
                        continue
                    
                    hacim_orani = hacim_usdt / v_ort
                    hacim_artis_pct = (hacim_orani - 1) * 100
                    
                    # KOSUL: Hacim 2.5 katı VE Fiyat %2.5
                    if hacim_orani >= 2.5 and fiyat_degisim >= 2.5:
                        if time.time() - son.get(s, 0) >= 1800: # 30 dk cooldown
                            emoji = "🚀"
                            hacim_farki = hacim_usdt - v_ort
                            pair = s.replace("USDT", "/USDT")
                            coin = s.replace("USDT", "")
                            
                            msg = (emoji + " <b>Pump - " + pair + " [Binance Futures]</b>\n"
                                   "💵 Price: <code>" + format(fiyat_onceki, ".5f") + "</code> ➜ <code>" + format(fiyat_simdi, ".5f") + "</code> (+" + format(fiyat_degisim, ".1f") + "%)\n"
                                   "📊 Volume: " + format(hacim_usdt, ".0f") + " (+" + format(hacim_artis_pct, ".0f") + " %)\n"
                                   "Volume increased by " + format(hacim_farki, ".0f") + " ⬆️\n"
                                   "Time: " + time.strftime("%I:%M:%S %p", time.localtime()) + " 🕒\n\n"
                                   "🔗 <a href='https://www.binance.com/en/futures/" + s + "'>Pair on Binance.</a>\n\n"
                                   "$" + coin + " #" + coin)
                            tg(C, msg)
                            son[s] = time.time()
                            sayac += 1
                            print(">>> SINYAL GONDERILDI:", s, flush=True)
                    
                    # API BAN YEMEMEK ICIN HER COIN ARASINDA 0.05 SANIYE BEKLE
                    time.sleep(0.05)
                    
                except Exception as e:
                    pass
                    
            print("--- 5. TARAMA BITTI - Sinyal:", sayac, time.strftime("%H:%M:%S"), "---", flush=True)
            time.sleep(180) # 3 dakikada bir tara
            
        except Exception as e:
            print("--- LOOP HATASI:", e, "---", flush=True)
            time.sleep(10)

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
    print("WEB SUNUCUSU BASLADI - PORT:", port, flush=True)
    server.serve_forever()

if __name__ == "__main__":
    bot_thread = threading.Thread(target=bot_loop)
    bot_thread.daemon = True
    bot_thread.start()
    run_web_server()

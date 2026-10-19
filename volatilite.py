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

# ==========================================
# MOTOR 1: HIZLI PUMP TARAYICI (Her 3 Dakika)
# ==========================================
def pump_loop():
    print("--- 1. PUMP TARAYICI BASLADI ---", flush=True)
    son = {}

    while True:
        try:
            # Tek istekle tum futures istatistiklerini al (Hacim siralamsi icin)
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if not tickers:
                time.sleep(10)
                continue
                
            usdt_tickers = [t for t in tickers if t["symbol"].endswith("USDT")]
            # En hacimli 250 coini al (Hem buyuk hem kucuk pump'lari yakalamak icin)
            usdt_tickers.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
            top_coins = [t["symbol"] for t in usdt_tickers[:250]]
            
            print("--- 2. PUMP TARAMASI BASLIYOR:", time.strftime("%H:%M:%S"), "---", flush=True)
            sayac = 0
            
            for s in top_coins:
                try:
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
                    if v_ort == 0: continue
                    
                    hacim_orani = hacim_usdt / v_ort
                    hacim_artis_pct = (hacim_orani - 1) * 100
                    
                    # NORMAL PUMP (Hacim 2.5x, Fiyat %2.5)
                    if hacim_orani >= 2.5 and fiyat_degisim >= 2.5:
                        if time.time() - son.get(s, 0) >= 1800:
                            pair = s.replace("USDT", "/USDT")
                            coin = s.replace("USDT", "")
                            emoji = "🚀"
                            
                            msg = (emoji + " <b>Pump - " + pair + " [Binance]</b>\n"
                                   "💵 Price: <code>" + format(fiyat_onceki, ".5f") + "</code> ➜ <code>" + format(fiyat_simdi, ".5f") + "</code> (+" + format(fiyat_degisim, ".1f") + "%)\n"
                                   "📊 Volume: " + format(hacim_usdt, ".0f") + " (+" + format(hacim_artis_pct, ".0f") + " %)\n"
                                   "Volume increased by " + format(hacim_usdt - v_ort, ".0f") + " ⬆️\n"
                                   "Time: " + time.strftime("%I:%M:%S %p", time.localtime()) + " 🕒\n\n"
                                   "🔗 <a href='https://www.binance.com/en/futures/" + s + "'>Pair on Binance.</a>\n\n"
                                   "$" + coin + " #" + coin)
                            tg(C, msg)
                            son[s] = time.time()
                            sayac += 1
                            print(">>> PUMP GONDERILDI:", s, flush=True)
                            
                    # MEGA PUMP (Görseldeki gibi dev hareketler - Fiyat %10+ ve Hacim 5x+)
                    elif hacim_orani >= 5.0 and fiyat_degisim >= 10.0:
                        if time.time() - son.get(s, 0) >= 3600: # 1 saat cooldown
                            pair = s.replace("USDT", "/USDT")
                            coin = s.replace("USDT", "")
                            
                            # 24s Istatistiklerini al
                            ticker = gj("https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=" + s)
                            h24 = float(ticker["highPrice"]) if ticker else 0
                            l24 = float(ticker["lowPrice"]) if ticker else 0
                            
                            msg = ("🔥 <b>MEGA PUMP - " + pair + "</b>\n"
                                   "💵 Price: <code>" + format(fiyat_onceki, ".5f") + "</code> ➜ <code>" + format(fiyat_simdi, ".5f") + "</code> (+" + format(fiyat_degisim, ".1f") + "%)\n"
                                   "📊 Volume: " + format(hacim_usdt, ".0f") + " (+" + format(hacim_artis_pct, ".0f") + " %)\n"
                                   "📈 24h High: <code>" + format(h24, ".5f") + "</code>\n"
                                   "📉 24h Low: <code>" + format(l24, ".5f") + "</code>\n"
                                   "Time: " + time.strftime("%I:%M:%S %p", time.localtime()) + " 🕒\n\n"
                                   "🔗 <a href='https://www.binance.com/en/futures/" + s + "'>Pair on Binance.</a>\n\n"
                                   "$" + coin + " #" + coin)
                            tg(C, msg)
                            son[s] = time.time()
                            sayac += 1
                            print(">>> MEGA PUMP GONDERILDI:", s, flush=True)
                            
                    time.sleep(0.05) # API Ban Korumasi
                except:
                    pass
            print("--- 3. PUMP TARAMASI BITTI - Sinyal:", sayac, "---", flush=True)
            time.sleep(180) # 3 dakika bekle
        except Exception as e:
            print("PUMP HATASI:", e, flush=True)
            time.sleep(10)

# ==========================================
# MOTOR 2: YILLIK ZIRVE / DIP TARAYICI (Her 1 Saat)
# ==========================================
def yearly_loop():
    print("--- YILLIK TARAYICI BASLADI ---", flush=True)
    son_zirve = {}
    son_dip = {}

    while True:
        try:
            print("--- YILLIK TARAMA BASLIYOR:", time.strftime("%H:%M:%S"), "---", flush=True)
            info = gj("https://fapi.binance.com/fapi/v1/exchangeInfo")
            if not info: 
                time.sleep(60)
                continue
                
            all_coins = [x["symbol"] for x in info["symbols"] if x["status"] == "TRADING" and x["quoteAsset"] == "USDT" and x["contractType"] == "PERPETUAL"]
            
            zirve_sayaci = 0
            dip_sayaci = 0
            
            for s in all_coins:
                try:
                    # 365 gunluk mumlari al
                    k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=1d&limit=365")
                    if not k or len(k) < 30: 
                        continue
                        
                    km = k[:-1] # Son kapanmis gunu al
                    fiyat = float(km[-1][4])
                    highs = [float(x[2]) for x in km]
                    lows = [float(x[3]) for x in km]
                    
                    yillik_max = max(highs)
                    yillik_min = min(lows)
                    
                    # Yillik Zirveye %1 yakinsa
                    if fiyat >= yillik_max * 0.99:
                        if time.time() - son_zirve.get(s, 0) >= 86400: # Gunde 1 kez bildir
                            msg = ("🔺 <b>YILLIK ZIRVE (365 GUN)</b>\n"
                                   "COIN: <b>" + s + "</b>\n\n"
                                   "Fiyat: <code>" + format(fiyat, ".5f") + "</code>\n"
                                   "Yillik Max: <code>" + format(yillik_max, ".5f") + "</code>\n\n"
                                   "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                                   "Link: marketowl.eu")
                            tg(C, msg)
                            son_zirve[s] = time.time()
                            zirve_sayaci += 1
                            print(">>> YILLIK ZIRVE:", s, flush=True)
                            
                    # Yillik Dibe %1 yakinsa
                    if fiyat <= yillik_min * 1.01:
                        if time.time() - son_dip.get(s, 0) >= 86400: # Gunde 1 kez bildir
                            msg = ("🔻 <b>YILLIK DIP (365 GUN)</b>\n"
                                   "COIN: <b>" + s + "</b>\n\n"
                                   "Fiyat: <code>" + format(fiyat, ".5f") + "</code>\n"
                                   "Yillik Min: <code>" + format(yillik_min, ".5f") + "</code>\n\n"
                                   "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                                   "Link: marketowl.eu")
                            tg(C, msg)
                            son_dip[s] = time.time()
                            dip_sayaci += 1
                            print(">>> YILLIK DIP:", s, flush=True)
                            
                    time.sleep(0.1) # Ban korumasi
                except:
                    pass
                    
            print(f"--- YILLIK TARAMA BITTI. Zirve: {zirve_sayaci}, Dip: {dip_sayaci} ---", flush=True)
            time.sleep(3600) # 1 saat bekle
            
        except Exception as e:
            print("YILLIK HATASI:", e, flush=True)
            time.sleep(60)

# ==========================================
# RENDER WEB SUNUCUSU
# ==========================================
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
    # Iki motoru da arka planda baslat
    threading.Thread(target=pump_loop, daemon=True).start()
    threading.Thread(target=yearly_loop, daemon=True).start()
    # Web sunucusunu on planda tut
    run_web_server()

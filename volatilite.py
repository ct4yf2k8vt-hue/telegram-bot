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
        return json.loads(urllib.request.urlopen(u, timeout=15).read().decode())
    except:
        return None

def bot_loop():
    print("--- 1. BOT LOOP BASLADI ---", flush=True)
    son = {}

    while True:
        try:
            print("--- 2. TUM BINANCE FUTURES COINLERI ALINIYOR ---", flush=True)
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if not tickers: 
                time.sleep(10)
                continue
                
            all_coins = [t["symbol"] for t in tickers if t["symbol"].endswith("USDT")]
            print("--- 3. TARANACAK COIN SAYISI:", len(all_coins), "---", flush=True)
            
            print("--- 4. TARAMA BASLIYOR:", time.strftime("%H:%M:%S"), "---", flush=True)
            sayac = 0
            
            for s in all_coins:
                try:
                    # 15 dakikalik mumlari al (Grafik yerine hizli metin icin)
                    k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=15m&limit=25")
                    if not k or len(k) < 22: continue
                    
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
                    
                    # SADECE BÜYÜK PUMP'LAR (Fiyat %5+, Hacim 3x+)
                    if hacim_orani >= 3.0 and fiyat_degisim >= 5.0:
                        if time.time() - son.get(s, 0) >= 1800: # 30 dk cooldown
                            
                            pair = s.replace("USDT", "/USDT")
                            coin = s.replace("USDT", "")
                            
                            msg = (f"🚀 <b>Pump - {pair} [Binance]</b>\n"
                                   f"💵 Price: <code>{format(fiyat_onceki, '.5f')}</code> ➜ <code>{format(fiyat_simdi, '.5f')}</code> (+{format(fiyat_degisim, '.1f')}%)\n"
                                   f"📊 Volume: {format(hacim_usdt, '.0f')} (+{format(hacim_artis_pct, '.0f')} %)\n"
                                   f"Volume increased by {format(hacim_usdt - v_ort, '.0f')} ⬆️\n"
                                   f"Time: {time.strftime('%I:%M:%S %p', time.localtime())} 🕒\n\n"
                                   f"🔗 <a href='https://www.binance.com/en/futures/{s}'>Pair on Binance.</a>\n\n"
                                   f"${coin} #{coin}")
                            
                            tg(msg)
                            son[s] = time.time()
                            sayac += 1
                            print(">>> SINYAL GONDERILDI:", s, flush=True)
                            
                    time.sleep(0.05) # API Ban Korumasi
                except:
                    pass
                    
            print("--- 5. TARAMA BITTI - Sinyal:", sayac, "---", flush=True)
            time.sleep(180) # 3 dakika bekle
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
    threading.Thread(target=bot_loop, daemon=True).start()
    run_web_server()

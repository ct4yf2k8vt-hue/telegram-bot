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
        return json.loads(urllib.request.urlopen(u, timeout=10).read().decode())
    except:
        return None

def bot_loop():
    print("--- ULTIMATE PUMP BOT BASLADI (SPOT + FUTURES) ---", flush=True)
    son = {}

    while True:
        try:
            print(f"[{time.strftime('%H:%M:%S')}] EN HACIMLI COINLER ARANIYOR...", flush=True)
            
            # 1. HEM SPOT HEM FUTURES İÇİN EN HACİMLİLERİ TEK İSTEKLE ÇEK
            tickers_spot = gj("https://api.binance.com/api/v3/ticker/24hr")
            tickers_fut = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            
            hedef_coinler = []
            
            # Spot için en hacimli 150 coin
            if tickers_spot:
                usdt_spot = [t for t in tickers_spot if t["symbol"].endswith("USDT")]
                usdt_spot.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
                for t in usdt_spot[:150]:
                    hedef_coinler.append({"symbol": t["symbol"], "type": "SPOT"})
            
            # Futures için en hacimli 150 coin
            if tickers_fut:
                usdt_fut = [t for t in tickers_fut if t["symbol"].endswith("USDT")]
                usdt_fut.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
                for t in usdt_fut[:150]:
                    hedef_coinler.append({"symbol": t["symbol"], "type": "FUTURES"})

            print(f"[{time.strftime('%H:%M:%S')}] TARANACAK COIN SAYISI: {len(hedef_coinler)}", flush=True)
            sayac = 0

            # 2. TARAMA BAŞLIYOR
            for c in hedef_coinler:
                s = c["symbol"]
                tip = c["type"]
                
                try:
                    # Coin tipine göre API adresi
                    if tip == "FUTURES":
                        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=5m&limit=25"
                    else:
                        url = f"https://api.binance.com/api/v3/klines?symbol={s}&interval=5m&limit=25"

                    k = gj(url)
                    if not k or len(k) < 22: 
                        time.sleep(0.05)
                        continue
                    
                    canli = k[-1] # Şu an oluşan canlı mum
                    acilis = float(canli[1])
                    su_anki = float(canli[4])
                    
                    # Hacim (USDT)
                    hacim = float(canli[7]) if tip == "FUTURES" else float(canli[5]) * su_anki
                    
                    fiyat_degisim = ((su_anki - acilis) / acilis) * 100
                    
                    # Hacim Oranı
                    v_ort = sum(float(x[7] if tip == "FUTURES" else float(x[5]) * float(x[4])) for x in k[:-1][-20:]) / 20
                    if v_ort == 0: 
                        time.sleep(0.05)
                        continue
                        
                    hacim_orani = hacim / v_ort
                    
                    # 3. FİLTRE VE SİNYAL
                    if fiyat_degisim >= 5.0 and hacim_orani >= 2.0:
                        if time.time() - son.get(s + tip, 0) >= 1800: # 30 dk cooldown
                            
                            pair = s.replace("USDT", "/USDT")
                            hacim_artis = hacim - v_ort
                            hacim_artis_pct = (hacim_orani - 1) * 100
                            
                            msg = (f"🚀 <b>Pump - {pair} [{tip}]</b>\n"
                                   f"💰 Price: <code>{format(acilis, '.4f')}</code> ➜ <code>{format(su_anki, '.4f')}</code> (+{format(fiyat_degisim, '.1f')}%)\n"
                                   f"📊 Volume: {format(hacim, '.0f')} (+{format(hacim_artis_pct, '.0f')} %)\n"
                                   f"Volume increased by {format(hacim_artis, '.0f')} ⬆️\n"
                                   f"Time: {time.strftime('%I:%M:%S %p', time.localtime())} 🕒\n\n"
                                   f"🔗 <a href='https://www.binance.com/en/{'futures/' if tip == 'FUTURES' else 'trade/'}{s}'>Pair on Binance.</a>")
                            tg(msg)
                            son[s + tip] = time.time()
                            sayac += 1
                            print(f">>> SINYAL GONDERILDI: {s} [{tip}]", flush=True)
                            
                    time.sleep(0.05) # API Koruması
                    
                except Exception as e:
                    pass
            
            print(f"[{time.strftime('%H:%M:%S')}] TARAMA BITTI - Sinyal: {sayac}", flush=True)
            time.sleep(60) # 1 dakika bekle
            
        except Exception as e:
            print("ANA HATA:", e, flush=True)
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

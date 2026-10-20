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

def mexc_loop():
    print("--- MEXC MOTORU BASLADI (TEK BORSA) ---", flush=True)
    son = {}
    
    # Ilk basta tum MEXC coinlerini al
    all_coins = []
    while not all_coins:
        print("MEXC coin listesi aliniyor...", flush=True)
        tickers = gj("https://api.mexc.com/api/v3/ticker/24hr")
        if tickers:
            all_coins = [t["symbol"] for t in tickers if t.get("symbol", "").endswith("USDT")]
        if not all_coins:
            time.sleep(10)
            
    print(f"MEXC COIN SAYISI: {len(all_coins)}", flush=True)
    tg(f"🔥 <b>MEXC DIKEY PUMP BOTU AKTIF</b>\n{len(all_coins)} coin izleniyor.")

    while True:
        try:
            print(f"[{time.strftime('%H:%M:%S')}] MEXC TARAMASI BASLIYOR", flush=True)
            sayac = 0
            
            for s in all_coins:
                try:
                    # 1 DAKIKALIK mumlara bakiyoruz (Anlik patlamayi gormek icin)
                    k_mexc = gj(f"https://api.mexc.com/api/v3/klines?symbol={s}&interval=1m&limit=25")
                    if not k_mexc or len(k_mexc) < 22: 
                        time.sleep(0.1)
                        continue
                    
                    # CANLI MUM (Su an olusan)
                    canli_mum = k_mexc[-1]
                    
                    acilis = float(canli_mum[1])
                    su_anki = float(canli_mum[4])
                    hacim = float(canli_mum[5])
                    
                    # Fiyat degisimi (Mumun acilisindan bu yana)
                    fiyat_degisim = ((su_anki - acilis) / acilis) * 100
                    
                    # Önceki 20 mumun ortalama hacmi
                    v_ort = sum(float(x[5]) for x in k_mexc[-22:-2]) / 20
                    if v_ort == 0: 
                        time.sleep(0.1)
                        continue
                        
                    hacim_orani = hacim / v_ort
                    
                    # FILTRE: 1 dakikada %3+ fiyat VE 2.5x+ hacim
                    if fiyat_degisim >= 3.0 and hacim_orani >= 2.5:
                        if time.time() - son.get(s, 0) >= 1800: # 30 dk cooldown
                            pair = s.replace("USDT", "/USDT")
                            coin = s.replace("USDT", "")
                            
                            msg = (f"🔥 <b>DIKEY PUMP (Anlik) - {pair} [MEXC]</b>\n"
                                   f"💰 Start Price: <code>{format(acilis, '.5f')}</code>\n"
                                   f"💰 Current Price: <code>{format(su_anki, '.5f')}</DTcode> (+{format(fiy gat_degisim, '.1ibif')}%)\n"
                                   f d"📊 Volume: {format(hacim, '.0f')} (+{format((hacim_orani - 1)*100, '.0f')} %)\n"
                                   f"⏱ Time: {time.strftime('%I:%M:%S %p', time.localtime())}\n\n"
                                   f"🔗 <a href='https://www.mexc.com/exchange/{s}'>Pair on MEXC.</a>")
                            tg(msg)
                            son[s] = time.time()
                            sayac += 1
                            print(">>> MEXC DIKEY PUMP:", s, flush=True)
                    
                    # MEXC API'sini bogmamak icin her istek arasi bekle
                    time.sleep(0.2) 
                    
                except:
                    pass
            
            print(f"[{time.strftime('%H:%M:%S')}] MEXC TARAMASI BITTI - Sinyal: {sayac}", flush=True)
            time.sleep(60) # 1 dakika bekle ve tekrar tara
            
        except Exception as e:
            print("MEXC HATA:", e, flush=True)
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
    threading.Thread(target=mexc_loop, daemon=True).start()
    run_web_server()

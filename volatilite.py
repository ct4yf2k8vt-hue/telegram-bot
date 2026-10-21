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

def kucoin_loop():
    print("--- SADECE KUCOIN MOTORU BASLADI ---", flush=True)
    son = {}
    
    # Başlangıçta tüm KuCoin USDT paritelerini al
    all_coins = []
    while not all_coins:
        print("KuCoin coin listesi aliniyor...", flush=True)
        tickers = gj("https://api.kucoin.com/api/v1/market/allTickers")
        if tickers and tickers.get("code") == "200000":
            all_coins = [t["symbol"] for t in tickers["data"]["ticker"] if t["symbol"].endswith("-USDT")]
        if not all_coins: time.sleep(10)
            
    print(f"KUCOIN COIN SAYISI: {len(all_coins)}", flush=True)
    tg(f"🔥 <b>KUCOIN DIKEY PUMP BOTU AKTIF</b>\n{len(all_coins)} coin izleniyor.")

    while True:
        try:
            print(f"[{time.strftime('%H:%M:%S')}] KUCOIN TARAMASI BASLIYOR", flush=True)
            sayac = 0
            
            for sym in all_coins:
                try:
                    # 1 DAKIKALIK mumlar (DOODUSDT gibi dikey çubukları anında görmek için)
                    k = gj(f"https://api.kucoin.com/api/v1/market/candles?type=1min&symbol={sym}&limit=25")
                    if not k or k.get("code") != "200000" or len(k["data"]) < 22: 
                        time.sleep(0.15)
                        continue
                    
                    # KuCoin veriyi tersten verir: [0] en son, [1] bir önceki...
                    k_list = k["data"]
                    canli = k_list[0]
                    
                    acilis = float(canli[1])
                    su_anki = float(canli[2])
                    
                    # Anlık fiyat değişimi (Mum açılışından bu yana)
                    fiyat_degisim = ((su_anki - acilis) / acilis) * 100
                    
                    # FİLTRE: DOODUSDT gibi büyük hareketler için %10 ve üzeri artış
                    # (İstersen bu oranı düşürebiliriz ama görseldeki gibi %80'lik artışlar için 10 idealdir)
                    if fiyat_degisim >= 10.0:
                        if time.time() - son.get(sym, 0) >= 1800: # 30 dk cooldown
                            
                            coin = sym.replace("-USDT", "")
                            
                            # GÖRSELDEKİ SADE FORMAT
                            msg = (f"🟢 <b>#{coin}: +{format(fiyat_degisim, '.2f')}%</b> 🔔\n"
                                   f"<b>KUCOIN</b>")
                            tg(msg)
                            son[sym] = time.time()
                            sayac += 1
                            print(f">>> KUCOIN DIKEY PUMP: {sym}", flush=True)
                    
                    time.sleep(0.15) # KuCoin API'sini yormamak icin bekle
                    
                except:
                    pass
            
            print(f"[{time.strftime('%H:%M:%S')}] KUCOIN TARAMASI BITTI - Sinyal: {sayac}", flush=True)
            time.sleep(60) # 1 dakika bekle
            
        except Exception as e:
            print("KUCOIN HATA:", e, flush=True)
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
    threading.Thread(target=kucoin_loop, daemon=True).start()
    run_web_server()

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

def binance_loop():
    print("--- SADECE BINANCE MOTORU BASLADI ---", flush=True)
    son = {}
    
    # Başlangıçta tüm Binance Futures coinlerini al
    all_coins = []
    while not all_coins:
        print("Binance coin listesi aliniyor...", flush=True)
        tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
        if tickers:
            all_coins = [t["symbol"] for t in tickers if t["symbol"].endswith("USDT")]
        if not all_coins: time.sleep(10)
            
    print(f"BINANCE COIN SAYISI: {len(all_coins)}", flush=True)
    tg(f"🔥 <b>BINANCE MEGA PUMP BOTU AKTIF</b>\n{len(all_coins)} coin izleniyor.")

    while True:
        try:
            print(f"[{time.strftime('%H:%M:%S')}] BINANCE TARAMASI BASLIYOR", flush=True)
            sayac = 0
            
            for s in all_coins:
                try:
                    # 1 DAKIKALIK mumlar (Anlık DOODUSDT tarzı pump'ları yakalamak için)
                    k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=1m&limit=25")
                    if not k or len(k) < 22: 
                        time.sleep(0.05)
                        continue
                    
                    canli = k[-1] # Şu an oluşan mum
                    acilis = float(canli[1])
                    su_anki = float(canli[4])
                    hacim = float(canli[7])
                    
                    fiyat_degisim = ((su_anki - acilis) / acilis) * 100
                    
                    # Hacim Oranı
                    v_ort = sum(float(x[7]) for x in k[:-1][-20:]) / 20
                    if v_ort == 0: 
                        time.sleep(0.05)
                        continue
                        
                    hacim_orani = hacim / v_ort
                    
                    # FİLTRE: Sadece %10 ve üzeri artan devasa pump'lar (Görseldeki gibi)
                    if fiyat_degisim >= 10.0 and hacim_orani >= 2.0:
                        if time.time() - son.get(s, 0) >= 1800: # 30 dk cooldown
                            
                            coin = s.replace("USDT", "")
                            
                            # GÖRSELDEKİ SADE FORMAT
                            msg = (f"🟢 <b>#{coin}: +{format(fiyat_degisim, '.2f')}%</b> 🔔\n"
                                   f"<b>BINANCE</b>")
                            tg(msg)
                            son[s] = time.time()
                            sayac += 1
                            print(f">>> BINANCE MEGA PUMP: {s}", flush=True)
                    
                    time.sleep(0.05) 
                    
                except:
                    pass
            
            print(f"[{time.strftime('%H:%M:%S')}] BINANCE TARAMASI BITTI - Sinyal: {sayac}", flush=True)
            time.sleep(60) # 1 dakika bekle
            
        except Exception as e:
            print("BINANCE HATA:", e, flush=True)
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
    threading.Thread(target=binance_loop, daemon=True).start()
    run_web_server()

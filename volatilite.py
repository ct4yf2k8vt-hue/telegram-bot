import urllib.request, urllib.parse, json, time
import os
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

T = os.environ.get("BOT_TOKEN")
C = "1623907197"

# Türkiye Saati (UTC+3)
TR_Saati = timezone(timedelta(hours=3))

def tg(m):
    try:
        d = urllib.parse.urlencode({"chat_id": C, "text": m, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
        urllib.request.urlopen("https://api.telegram.org/bot" + T + "/sendMessage", d, timeout=10).read()
    except:
        pass

def gj(u):
    try:
        # Binance'in botları engellememesi için normal tarayıcı gibi görünüyoruz
        req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    except:
        return None

def bot_loop():
    print("--- PUMP ALERT BOT BASLADI ---", flush=True)
    son = {}

    while True:
        try:
            # Tüm USDT paritelerini çek
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if not tickers: 
                print("Ticker verisi alinamadi, 10 sn bekleniyor...", flush=True)
                time.sleep(10)
                continue
                
            # FİLTRE: Sadece 24 saatlik hacmi 1.000.000 USDT üzeri olanları al (API banını önler)
            all_coins = []
            for t in tickers:
                if t["symbol"].endswith("USDT"):
                    try:
                        hacim24 = float(t["quoteVolume"])
                        if hacim24 > 1000000: # 1 Milyon USDT üzeri
                            all_coins.append(t["symbol"])
                    except:
                        pass
                        
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] TARAMA BASLIYOR | Coin: {len(all_coins)}", flush=True)
            sayac = 0
            
            for s in all_coins:
                try:
                    # Görseldeki gibi 5 dakikalık grafik (5m). Son 3 mum yeterli.
                    k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=5m&limit=3")
                    if not k or len(k) < 3: continue
                    
                    son_mum = k[-2] 
                    onceki_mum = k[-3]
                    
                    simdiki_fiyat = float(son_mum[4])
                    onceki_fiyat = float(onceki_mum[4])
                    simdiki_hacim = float(son_mum[7]) # USDT hacmi
                    onceki_hacim = float(onceki_mum[7])
                    
                    if onceki_fiyat == 0 or onceki_hacim == 0: continue
                    
                    fiyat_degisim = ((simdiki_fiyat - onceki_fiyat) / onceki_fiyat) * 100
                    hacim_farki = simdiki_hacim - onceki_hacim
                    hacim_degisim_pct = (hacim_farki / onceki_hacim) * 100
                    
                    # FİLTRE: Fiyat %2+ VE Hacim %100+ (2 katı) artmışsa
                    if fiyat_degisim >= 2.0 and hacim_degisim_pct >= 100.0:
                        if time.time() - son.get(s, 0) >= 1800: # 30 dk bekleme
                            
                            saat_simdi = datetime.now(TR_Saati).strftime('%I:%M:%S %p')
                            coin = s.replace("USDT", "")
                            
                            # GÖRSELDEKİ TAM FORMAT (Yazım hatası düzeltildi)
                            msg = (f"🚀 <b>Pump - {coin}/USDT [Binance]</b>\n"
                                   f"💲 <b>Price:</b> {format(onceki_fiyat, '.5f')} ➔ {format(simdiki_fiyat, '.5f')} (<b>{format(fiyat_degisim, '+.1f')}%</b>)\n"
                                   f"📊 <b>Volume:</b> {format(simdiki_hacim, '.0f')} (<b>{format(hacim_degisim_pct, '+.0f')} %</b>)\n"
                                   f"Volume increased by {format(hacim_farki, '.0f')} ⬆️\n"
                                   f"Time: {saat_simdi} 🕐\n\n"
                                   f"🔗 <a href='https://www.binance.com/en/futures/{s}'>Pair on Binance.</a>\n\n"
                                   f"<b>${coin} #{coin}</b>")
                            
                            tg(msg)
                            son[s] = time.time()
                            sayac += 1
                            print(f">>> PUMP SINYALI GONDERILDI: {s}", flush=True)
                            
                    time.sleep(0.2) # API BANINI ÖNLEMEK İÇİN HIZI YAVAŞLATTIK (Saniyede 5 istek)
                except:
                    pass
                    
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] TARAMA BITTI - Sinyal: {sayac}", flush=True)
            time.sleep(30) 
        except Exception as e:
            print("HATA:", e, flush=True)
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

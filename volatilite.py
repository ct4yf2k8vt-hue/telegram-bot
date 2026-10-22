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
        return json.loads(urllib.request.urlopen(u, timeout=10).read().decode())
    except:
        return None

def bot_loop():
    print("--- PUMP ALERT BOT BASLADI ---", flush=True)
    son = {}

    while True:
        try:
            # Tüm USDT paritelerini tek seferde çek
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if not tickers: 
                time.sleep(10)
                continue
                
            all_coins = [t["symbol"] for t in tickers if t["symbol"].endswith("USDT")]
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] TARAMA BASLIYOR | Coin: {len(all_coins)}", flush=True)
            sayac = 0
            
            for s in all_coins:
                try:
                    # Görseldeki gibi 5 dakikalık grafik (5m) kullanıyoruz. Son 3 mum yeterli.
                    k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=5m&limit=3")
                    if not k or len(k) < 3: continue
                    
                    # Kapanmış son mumu ve bir önceki mumu alıyoruz
                    son_mum = k[-2] 
                    onceki_mum = k[-3]
                    
                    # Verileri float'a çevir
                    simdiki_fiyat = float(son_mum[4])
                    onceki_fiyat = float(onceki_mum[4])
                    simdiki_hacim = float(son_mum[7]) # USDT hacmi
                    onceki_hacim = float(onceki_mum[7])
                    
                    # Değişim hesaplamaları
                    if onceki_fiyat == 0 or onceki_hacim == 0: continue
                    
                    fiyat_degisim = ((simdiki_fiyat - onceki_fiyat) / onceki_fiyat) * 100
                    hacim_farki = simdiki_hacim - onceki_hacim
                    hacim_degisim_pct = (hacim_farki / onceki_hacim) * 100
                    
                    # GÖRSELDEKİ FİLTRE MANTIĞI:
                    # Fiyat aniden %2'den fazla artmış VE Hacim bir önceki muma göre en az 2 katına (%100 artmış) çıkmışsa
                    if fiyat_degisim >= 2.0 and hacim_degisim_pct >= 100.0:
                        # Aynı coin için 30 dakika içinde tekrar bildirim atmasını engelle
                        if time.time() - son.get(s, 0) >= 1800:
                            
                            # Saat formatı: 10:48:26 AM şeklinde
                            saat_simdi = datetime.now(TR_Saati).strftime('%I:%M:%S %p')
                            coin = s.replace("USDT", "")
                            
                            # GÖRSELDEKİ TAM FORMAT
                            msg = (f"🚀 <b>Pump - {coin}/USDT [Binance]</b>\n"
                                   f"💲 <b>Price:</b> {format(onceki_fiyat, '.5f')} ➔ {format(sim Renderdiki'd_fiyat, '.5f')} (<b>{format(fiyat_degisim, '+.1f')}%</b>)\n"
                                   f"📊 <b>Volume:</b> {format(simdiki_hacim, '.0f')} (<b>{format(hacim_degisim_pct, '+.0f')} %</b>)\n"
                                   f"Volume increased by {format(hacim_farki, '.0f')} ⬆️\n"
                                   f"Time: {saat_simdi} 🕐\n\n"
                                   f"🔗 <a href='https://www.binance.com/en/futures/{s}'>Pair on Binance.</a>\n\n"
                                   f"<b>${coin} #{coin}</b>")
                            
                            tg(msg)
                            son[s] = time.time()
                            sayac += 1
                            print(f">>> PUMP SINYALI GONDERILDI: {s}", flush=True)
                            
                    time.sleep(0.05) # API'yi yormamak için
                except:
                    pass
                    
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] TARAMA BITTI - Sinyal: {sayac}", flush=True)
            time.sleep(30) # 5m grafikler için 30 saniyede bir tarama yeterli ve hızlıdır.
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

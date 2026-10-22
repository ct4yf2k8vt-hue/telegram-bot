import urllib.request, urllib.parse, json, time
import os
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

T = os.environ.get("BOT_TOKEN")
C = "1623907197"
TR_Saati = timezone(timedelta(hours=3))

def tg(m):
    try:
        d = urllib.parse.urlencode({"chat_id": C, "text": m, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
        urllib.request.urlopen("https://api.telegram.org/bot" + T + "/sendMessage", d, timeout=10).read()
    except:
        pass

def gj(u):
    try:
        req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    except:
        return None

def bot_loop():
    print("--- IBMB TARZI PUMP ALERT BOTU BAŞLADI ---", flush=True)
    son = {}

    while True:
        try:
            print(f"\n[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] TARAMA BAŞLIYOR...", flush=True)
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if not tickers: 
                time.sleep(10)
                continue
                
            # Sadece 24 saatlik hacmi 1 Milyon USDT üzeri olanları al (API banını önler)
            all_coins = [t["symbol"] for t in tickers if t["symbol"].endswith("USDT") and float(t.get("quoteVolume", 0)) > 1000000]
            toplam = len(all_coins)
            sayac = 0
            
            for i, s in enumerate(all_coins):
                try:
                    if (i + 1) % 50 == 0:
                        print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Taranıyor... ({i+1}/{toplam})", flush=True)

                    # 15 dakikalık (15m) grafikte son 2 mumu çekiyoruz
                    k = gj(f"https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=15m&limit=2")
                    if not k or len(k) < 2: continue
                    
                    onceki_mum = k[0]
                    simdiki_mum = k[1]
                    
                    # Mum verileri: [4]=Kapanış Fiyatı, [7]=USDT Hacmi
                    simdiki_fiyat = float(simdiki_mum[4])
                    onceki_fiyat = float(onceki_mum[4])
                    simdiki_hacim = float(simdiki_mum[7])
                    onceki_hacim = float(onceki_mum[7])
                    
                    if onceki_fiyat == 0 or onceki_hacim == 0: continue
                    
                    # Hesaplamalar
                    fiyat_degisim = ((simdiki_fiyat - onceki_fiyat) / onceki_fiyat) * 100
                    hacim_farki = simdiki_hacim - onceki_hacim
                    hacim_degisim_pct = (hacim_farki / onceki_hacim) * 100
                    
                    # TETİKLEYİCİ FİLTRE: Görseldeki gibi sert hareketler
                    # Fiyat en az %3 artmış VE Hacim en az %20 artmış VE Hacim farkı 10.000$'dan büyükse
                    if fiyat_degisim >= 3.0 and hacim_degisim_pct >= 20.0 and hacim_farki > 10000:
                        if time.time() - son.get(s, 0) >= 1800: # 30 dk bekleme
                            
                            coin = s.replace("USDT", "")
                            saat_simdi = datetime.now(TR_Saati).strftime('%H:%M:%S')
                            
                            # Hacimleri K (Bin) formatına çevirme
                            hacim_str = f"${simdiki_hacim/1000:.2f}K"
                            hacim_farki_str = f"${hacim_farki/1000:.2f}K"
                            
                            # GÖRSELDEKİ BİREBİR FORMAT
                            msg = (f"🚀 <b>Pump - {coin}/USDT [Binance]</b>\n"
                                   f"Pump Activity on {coin}/USDT 🟢 🟢\n"
                                   f"💰 <b>Price:</b> ${format(onceki_fiyat, '.2f')} ➔ ${format(simdiki_fiyat, '.2f')} (<b>{format(fiyat_degisim, '+.2f')}%</b>)\n"
                                   f"📊 <b>Volume:</b> {hacim_str} (<b>{format(hacim_degisim_pct, '+.2f')}%</b>)\n"
                                   f"Volume increased by {hacim_farki_str} ⬆️\n\n"
                                   f"⏰ {saat_simdi}\n"
                                   f"🔗 <a href='https://www.binance.com/en/futures/{s}'>Pair on Binance.</a>\n"
                                   f"<b>${coin} #{coin}</b>")
                            
                            tg(msg)
                            son[s] = time.time()
                            sayac += 1
                            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] >>> SİNYAL GÖNDERİLDİ: {s}", flush=True)
                            
                    time.sleep(0.2) # API limitini korumak için
                except Exception as e:
                    pass
                    
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] TARAMA BİTTİ | Gönderilen Sinyal: {sayac}", flush=True)
            print(f"--- 15 Saniye Bekleniyor ---", flush=True)
            time.sleep(15) 
            
        except Exception as e:
            print(f"ANA DÖNGÜ HATASI: {e} - 10 sn sonra devam edecek.", flush=True)
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
    print("WEB SUNUCUSU BAŞLADI - PORT:", port, flush=True)
    server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    run_web_server()

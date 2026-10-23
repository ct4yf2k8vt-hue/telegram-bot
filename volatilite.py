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
        return json.loads(urllib.request.urlopen(req, timeout=15).read().decode()) # Timeout süresini 15 yaptık
    except:
        return None

def bot_loop():
    print("--- BINANCE SPOT 5M PUMP ALARM (ÖLÜMSÜZ MOD) BAŞLADI ---", flush=True)
    son = {}

    while True:
        try:
            # 1. ADIM: Binance Spot piyasanın anlık özetini çek
            tickers = gj("https://api.binance.com/api/v3/ticker/24hr")
            
            # Eğer API çökerse veya boş dönerse döngüye devam et, asla takılma
            if not tickers: 
                print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] API yanıt vermedi. 3 saniye sonra tekrar denenecek...", flush=True)
                time.sleep(3)
                continue
                
            # 2. ADIM: Sadece USDT pariteleri ve 24s hacmi 2 Milyon Dolar üzeri olanlar
            all_coins = [t["symbol"] for t in tickers if t["symbol"].endswith("USDT") and float(t.get("quoteVolume", 0)) > 2000000]
            
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Tarama Başladı. ({len(all_coins)} coin kontrol edilecek)", flush=True)
            sayac = 0

            for i, s in enumerate(all_coins):
                try:
                    # Her 20 coinde bir log at ki Render ekranı sürekli aksın
                    if (i + 1) % 20 == 0:
                        print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Taranıyor... ({i+1}/{len(all_coins)})", flush=True)

                    # 3. ADIM: 5 Dakikalık grafikte son 3 mumu çek
                    k = gj(f"https://api.binance.com/api/v3/klines?symbol={s}&interval=5m&limit=3")
                    if not k or len(k) < 3: 
                        continue # Mum verisi gelmezse sıradaki coine geç
                    
                    kapanan_mum = k[-2]
                    onceki_mum = k[-3]
                    
                    fiyat_simdi = float(kapanan_mum[4])
                    fiyat_onceki = float(onceki_mum[4])
                    hacim_simdi = float(kapanan_mum[7])
                    hacim_onceki = float(onceki_mum[7])
                    
                    if fiyat_onceki == 0 or hacim_onceki == 0: continue
                    
                    fiyat_degisim = ((fiyat_simdi - fiyat_onceki) / fiyat_onceki) * 100
                    hacim_farki = hacim_simdi - hacim_onceki
                    hacim_degisim_pct = (hacim_farki / hacim_onceki) * 100 if hacim_onceki > 0 else 0
                    
                    # 4. ADIM: TETİKLEYİCİ (%6+ Fiyat Artışı)
                    if fiyat_degisim >= 6.0:
                        if time.time() - son.get(s, 0) >= 1800: 
                            
                            coin = s.replace("USDT", "")
                            saat_simdi = datetime.now(TR_Saati).strftime('%I:%M:%S %p')
                            
                            msg = (f"🚀 <b>Pump - {coin}/USDT [Binance]</b>\n"
                                   f"💰 <b>Price:</b> {format(fiyat_onceki, '.5f')} ➔ {format(fiyat_simdi, '.5f')} (<b>{format(fiyat_degisim, '+.1f')}%</b>)\n"
                                   f"📊 <b>Volume:</b> {format(hacim_simdi, '.0f')} (<b>{format(hacim_degisim_pct, '+.0f')} %</b>)\n"
                                   f"Volume increased by {format(hacim_farki, '.0f')} ⬆️\n"
                                   f"Time: {saat_simdi} 🕐\n\n"
                                   f"🔗 <a href='https://www.binance.com/en/trade/{s}?type=spot'>Pair on Binance.</a>\n\n"
                                   f"<b>${coin} #{coin}</b>")
                            
                            tg(msg)
                            son[s] = time.time()
                            sayac += 1
                            print(f"🔥 SİNYAL GÖNDERİLDİ: {s} | Fiyat: +%{fiyat_degisim:.1f}", flush=True)
                            
                    time.sleep(0.05) # Hafif bekleme
                except Exception as e:
                    # Tek bir coin hata verse bile döngüyü bozmadan devam et
                    pass
                    
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Tur Bitti. Sinyal: {sayac}. 3 sn sonra yeni tur...\n", flush=True)
            time.sleep(3) 
            
        except Exception as e:
            # Ana döngüde bir şey çökerse ekrana yaz ve devam et
            print(f"ANA DÖNGÜ HATASI: {e} - 5 sn sonra devam edilecek.", flush=True)
            time.sleep(5)

# Thread'in ölüp ölmediğini kontrol eden ve gerekirse yeniden başlatan fonksiyon
def thread_manager():
    while True:
        try:
            # bot_loop thread'i canlı mı diye kontrol et
            if not any(t.name == "BotThread" and t.is_alive() for t in threading.enumerate()):
                print("!!! TARAMA MOTORU DURDU, YENİDEN BAŞLATILIYOR !!!", flush=True)
                bot_thread = threading.Thread(target=bot_loop, daemon=True)
                bot_thread.name = "BotThread"
                bot_thread.start()
        except Exception as e:
            print(f"Thread Manager Hatası: {e}", flush=True)
        time.sleep(30) # Her 30 saniyede bir kontrol et

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
    # 1. Tarama motorunu başlat
    bot_thread = threading.Thread(target=bot_loop, daemon=True)
    bot_thread.name = "BotThread"
    bot_thread.start()
    
    # 2. Motoru denetleyen Bekçi (Manager) thread'ini başlat
    threading.Thread(target=thread_manager, daemon=True).start()
    
    # 3. Web sunucusunu başlat
    run_web_server()

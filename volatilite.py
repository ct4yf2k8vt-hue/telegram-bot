import urllib.request, urllib.parse, json, time
import os
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

T = os.environ.get("BOT_TOKEN")
C = "1623907197"
TR_Saati = timezone(timedelta(hours=3))

# --- WATCHDOG İÇİN GLOBAL DEĞİŞKEN ---
LAST_PING = time.time()
WATCHDOG_LIMIT = 120 # 2 dakika boyunca log gelmezse botu zorla kapat

def tg(m):
    try:
        d = urllib.parse.urlencode({"chat_id": C, "text": m, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
        urllib.request.urlopen("https://api.telegram.org/bot" + T + "/sendMessage", d, timeout=10).read()
    except:
        pass

def gj(u):
    try:
        req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'})
        return json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
    except:
        return None

def format_volume(vol):
    if vol >= 1000000:
        return f"${vol/1000000:.2f}M"
    elif vol >= 1000:
        return f"${vol/1000:.2f}K"
    else:
        return f"${vol:.2f}"

def bot_loop():
    global LAST_PING
    print("--- SAHTE SİNYAL ENGELLEYİCİ: 15M PUMP ALARM BAŞLADI ---", flush=True)
    son = {}

    while True:
        try:
            print(f"\n[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Piyasa özeti çekiliyor...", flush=True)
            
            tickers = gj("https://api.binance.com/api/v3/ticker/24hr")
            if not tickers:
                time.sleep(3)
                continue

            # 1. DEVASA FİLTRE: Sadece USDT pariteleri ve 24s Hacmi 10 Milyon Dolar üzeri olanlar
            adaylar = []
            for t in tickers:
                if t['symbol'].endswith('USDT'):
                    try:
                        hacim = float(t['quoteVolume'])
                        if hacim > 10000000: # 10 MİLYON DOLAR BARAJI
                            adaylar.append(t['symbol'])
                    except:
                        pass

            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] {len(adaylar)} dev hacimli coin bulundu. 15m mumları inceleniyor...", flush=True)

            sayac = 0
            for i, s in enumerate(adaylar):
                try:
                    if (i + 1) % 20 == 0:
                        print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Taranıyor... ({i+1}/{len(adaylar)})", flush=True)

                    # 2. ADIM: 15 Dakikalık grafikte son 21 mumu çek (Hacim ortalaması için)
                    k = gj(f"https://api.binance.com/api/v3/klines?symbol={s}&interval=15m&limit=21")
                    if not k or len(k) < 21: continue
                    
                    kapanan_mum = k[-2] # Tamamlanmış son 15m mumu
                    onceki_mum = k[-3]
                    
                    fiyat_simdi = float(kapanan_mum[4])
                    fiyat_onceki = float(onceki_mum[4])
                    hacim_simdi = float(kapanan_mum[7])
                    hacim_onceki = float(onceki_mum[7])
                    
                    # 3. ADIM: Son 20 mumun ortalama hacmini hesapla
                    son_20_mum_hacimleri = [float(m[7]) for m in k[:-1]]
                    ortalama_hacim = sum(son_20_mum_hacimleri) / 20
                    
                    if fiyat_onceki == 0 or ortalama_hacim == 0: continue
                    
                    fiyat_degisim = ((fiyat_simdi - fiyat_onceki) / fiyat_onceki) * 100
                    hacim_farki = hacim_simdi - hacim_onceki
                    hacim_degisim_pct = (hacim_farki / hacim_onceki) * 100 if hacim_onceki > 0 else 0
                    
                    # Hacim, son 20 mumun ortalamasının kaç katı?
                    hacim_kati = hacim_simdi / ortalama_hacim
                    
                    # 4. ADIM: TETİKLEYİCİ (Fiyat %5+ VE Hacim Ortalamanın En Az 3 Katı)
                    if fiyat_degisim >= 5.0 and hacim_kati >= 3.0:
                        if time.time() - son.get(s, 0) >= 1800: # 30 dk bekleme
                            
                            coin = s.replace("USDT", "")
                            saat_simdi = datetime.now(TR_Saati).strftime('%H:%M:%S')
                            
                            # GÖRSELDEKİ BİREBİR FORMAT
                            msg = (f"🚀 <b>Pump - {coin}/USDT [Binance]</b>\n"
                                   f"Pump Activity on {coin}/USDT 🟢 🟢\n"
                                   f"💰 <b>Price:</b> ${format(fiyat_onceki, '.4f')} ➔ ${format(fiyat_simdi, '.4f')} (<b>{format(fiyat_degisim, '+.2f')}%</b>)\n"
                                   f"📊 <b>Volume:</b> {format_volume(hacim_simdi)} (<b>{format(hacim_degisim_pct, '+.2f')}%</b>)\n"
                                   f"Volume increased by {format_volume(hacim_farki)} ⬆️ (Ortalamanın {hacim_kati:.1f} katı)\n\n"
                                   f"⏰ {saat_simdi}")
                            
                            tg(msg)
                            son[s] = time.time()
                            sayac += 1
                            print(f"🔥 GERÇEK SİNYAL: {s} | Fiyat: +%{fiyat_degisim:.2f} | Hacim Katı: {hacim_kati:.1f}x", flush=True)
                            
                    time.sleep(0.05) # API limitini korumak için
                except Exception as e:
                    pass
                    
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Tur Bitti. Sinyal: {sayac}. 5 sn sonra yeni tur...\n", flush=True)
            
            LAST_PING = time.time()
            time.sleep(5) 
            
        except Exception as e:
            print(f"ANA DÖNGÜ HATASI: {e} - 5 sn sonra devam edilecek.", flush=True)
            time.sleep(5)

# --- WATCHDOG (BEKÇİ) FONKSİYONU ---
def watchdog():
    global LAST_PING
    while True:
        time.sleep(10)
        if time.time() - LAST_PING > WATCHDOG_LIMIT:
            print(f"\n!!! BOT {WATCHDOG_LIMIT} SANİYEDİR DONDU. ZORLA YENİDEN BAŞLATILIYOR !!!", flush=True)
            os._exit(1) # Render'ın botu sıfırdan başlatmasını sağlar

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
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"WEB SUNUCUSU BAŞLADI - PORT: {port}", flush=True)
    server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    
    bot_thread = threading.Thread(target=bot_loop, daemon=True)
    bot_thread.name = "BotThread"
    bot_thread.start()
    
    while True:
        time.sleep(10)

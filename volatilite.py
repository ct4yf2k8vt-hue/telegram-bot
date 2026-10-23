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
        req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'})
        return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    except:
        return None

def bot_loop():
    print("--- SADECE SPOT: GERÇEK PATLAMA (ANORMAL VOLATİLİTE) BOTU BAŞLADI ---", flush=True)
    son = {}

    while True:
        try:
            # 1. ADIM: Tek istekle Spot piyasanın 24 saatlik özetini al
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Spot piyasa özeti çekiliyor...", flush=True)
            
            spot_tickers = gj("https://api.binance.com/api/v3/ticker/24hr")
            
            if not spot_tickers:
                print("Binance API yanıt vermedi, 3 saniye bekleniyor...", flush=True)
                time.sleep(3)
                continue

            adaylar = []

            # Sadece USDT paritelerini ve 24s değişimi %5'ten fazla olanları ön filtreye al (Hız için)
            for t in spot_tickers:
                if t['symbol'].endswith('USDT'):
                    degisim = float(t['priceChangePercent'])
                    if abs(degisim) >= 5.0:
                        adaylar.append(t['symbol'])

            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Ön filtre: {len(adaylar)} hareketli coin bulundu. Mumlar inceleniyor...", flush=True)

            sayac = 0
            for symbol in adaylar:
                try:
                    # 2. ADIM: Son 20 mumu çek (Ortalama hacim hesaplamak için)
                    k = gj(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=21")
                    if not k or len(k) < 21: continue
                    
                    onceki_mumlar = k[:-1] # Son 20 mum
                    son_mum = k[-1]        # Şu anki (kapanmak üzere olan veya yeni kapanan) mum
                    
                    # Hacim Hesaplamaları
                    guncel_hacim = float(son_mum[7])
                    son_20_mum_hacimleri = [float(m[7]) for m in onceki_mumlar]
                    ortalama_hacim = sum(son_20_mum_hacimleri) / 20
                    
                    if ortalama_hacim == 0: continue
                    
                    # Hacim kaç katına çıktı? (Örn: 5.4x)
                    hacim_kati = guncel_hacim / ortalama_hacim
                    
                    # Fiyat ve Mum Gövdesi Hesaplamaları
                    acilis = float(son_mum[1])
                    kapanis = float(son_mum[4])
                    
                    if acilis == 0: continue
                    
                    # Mum gövdesi yüzdesi (Açılış ve kapanış arasındaki fark)
                    mum_govdesi_pct = abs(kapanis - acilis) / acilis * 100
                    
                    # --- 3. ADIM: GERÇEK ANORMAL VOLATİLİTE FİLTRESİ ---
                    # Hacim en az 4 katı olacak VE mum gövdesi en az %3 olacak
                    if hacim_kati >= 4.0 and mum_govdesi_pct >= 3.0:
                        if time.time() - son.get(symbol, 0) >= 1800: # 30 dk bekleme
                            
                            coin = symbol.replace("USDT", "")
                            saat_simdi = datetime.now(TR_Saati).strftime('%H:%M:%S')
                            yon_emoji = "🟢" if kapanis > acilis else "🔴"
                            yon_text = "PUMP" if kapanis > acilis else "DUMP"
                            
                            # GÖRSELDEKİ GİBİ DEVASA MUM FORMATI
                            msg = (f"📊 <b>[{symbol}] Abnormal Volatility Alert (15m)</b>\n"
                                   f"Market: <b>SPOT</b> | Tip: <b>{yon_text} {yon_emoji}</b>\n"
                                   f"💰 Price: ${format(kapanis, '.4f')} (<b>{format(mum_govdesi_pct, '+.2f') if kapanis > acilis else format(-mum_govdesi_pct, '+.2f')}%</b>)\n"
                                   f"📊 Volume: ${format(guncel_hacim/1000, '.2f')}K (<b>{format(hacim_kati, '.1f')}x Ortalama</b>)\n\n"
                                   f"🔗 <a href='https://www.binance.com/en/trade/{symbol}?type=spot'>Binance Spot Grafiği</a>\n"
                                   f"<b>${coin} #{coin}</b>")
                            
                            tg(msg)
                            son[symbol] = time.time()
                            sayac += 1
                            print(f"🔥 GERÇEK PATLAMA: {symbol} | Hacim: {hacim_kati:.1f}x | Mum: %{mum_govdesi_pct:.2f}", flush=True)
                            
                except Exception as e:
                    pass
                    
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Tur tamamlandı. Sinyal: {sayac}. Yeni tur başlıyor...\n", flush=True)
            time.sleep(2) # Loglar akmaya devam etsin
            
        except Exception as e:
            print(f"HATA: {e}", flush=True)
            time.sleep(2)

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

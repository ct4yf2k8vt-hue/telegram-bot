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
    print("--- SADECE SPOT: DEV HACİMLİ GERÇEK PATLAMA BOTU BAŞLADI ---", flush=True)
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

            # FİLTRE 1: Sadece USDT pariteleri, 24s değişimi %3'ten fazla VE 24s Hacmi 10 Milyon Dolar'dan büyük olanlar
            for t in spot_tickers:
                if t['symbol'].endswith('USDT'):
                    degisim = float(t['priceChangePercent'])
                    hacim24 = float(t['quoteVolume'])
                    
                    if abs(degisim) >= 3.0 and hacim24 >= 10000000: # 10 Milyon Dolar Hacim Şartı
                        adaylar.append(t['symbol'])

            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Ön filtre: {len(adaylar)} dev hacimli hareketli coin bulundu. Mumlar inceleniyor...", flush=True)

            sayac = 0
            for symbol in adaylar:
                try:
                    # 2. ADIM: Son 21 mumu çek. (En son mum henüz kapanmadığı için onu değil, ondan önceki bitmiş mumu kullanacağız)
                    k = gj(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=21")
                    if not k or len(k) < 21: continue
                    
                    # k[-1] = Şu an oluşan anlık mum (KULLANMIYORUZ)
                    # k[-2] = YENİ KAPANAN MUM (Sinyal için bunu kullanıyoruz)
                    kapanan_mum = k[-2]
                    onceki_mumlar = k[:-2] # Son 20 kapanmış mum
                    
                    # Hacim Hesaplamaları
                    guncel_hacim = float(kapanan_mum[7])
                    son_20_mum_hacimleri = [float(m[7]) for m in onceki_mumlar[-20:]]
                    ortalama_hacim = sum(son_20_mum_hacimleri) / 20
                    
                    if ortalama_hacim == 0: continue
                    
                    hacim_kati = guncel_hacim / ortalama_hacim
                    
                    # Fiyat ve Mum Gövdesi Hesaplamaları
                    acilis = float(kapanan_mum[1])
                    kapanis = float(kapanan_mum[4])
                    
                    if acilis == 0: continue
                    
                    mum_govdesi_pct = abs(kapanis - acilis) / acilis * 100
                    
                    # --- FİLTRE 2: GERÇEK ANORMAL VOLATİLİTE ---
                    # Hacim en az 5 katı olacak VE mum gövdesi en az %3 olacak
                    if hacim_kati >= 5.0 and mum_govdesi_pct >= 3.0:
                        if time.time() - son.get(symbol, 0) >= 3600: # Aynı coini 1 saatte 1 kez bildir
                            
                            coin = symbol.replace("USDT", "")
                            saat_simdi = datetime.now(TR_Saati).strftime('%H:%M:%S')
                            yon_emoji = "🟢" if kapanis > acilis else "🔴"
                            yon_text = "PUMP" if kapanis > acilis else "DUMP"
                            
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
            time.sleep(2) 
            
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

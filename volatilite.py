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
    print("--- SADECE SPOT: ABNORMAL VOLATILITY BOTU BAŞLADI ---", flush=True)
    son = {}

    while True:
        try:
            # 1. ADIM: Tek istekle Spot piyasanın özetini al
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Spot piyasa özeti çekiliyor...", flush=True)
            
            spot_tickers = gj("https://api.binance.com/api/v3/ticker/24hr")
            
            if not spot_tickers:
                print("Binance API yanıt vermedi, 3 saniye bekleniyor...", flush=True)
                time.sleep(3)
                continue

            adaylar = []

            # Sadece USDT paritelerini ve 24s değişimi %3'ten fazla olanları filtrele
            for t in spot_tickers:
                if t['symbol'].endswith('USDT'):
                    degisim = float(t['priceChangePercent'])
                    if abs(degisim) >= 3.0:
                        adaylar.append((t['symbol'], degisim))

            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Toplam {len(adaylar)} hareketli coin bulundu. Mumlar inceleniyor...", flush=True)

            sayac = 0
            for symbol, anlik_degisim in adaylar:
                try:
                    # 2. ADIM: Sadece adayların 15 dakikalık mumunu çek
                    k = gj(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=2")
                    if not k or len(k) < 2: continue
                    
                    onceki_mum = k[0]
                    simdiki_mum = k[1]
                    
                    simdiki_fiyat = float(simdiki_mum[4])
                    onceki_fiyat = float(onceki_mum[4])
                    simdiki_hacim = float(simdiki_mum[7])
                    onceki_hacim = float(onceki_mum[7])
                    
                    if onceki_fiyat == 0 or onceki_hacim == 0: continue
                    
                    fiyat_degisim_15m = ((simdiki_fiyat - onceki_fiyat) / onceki_fiyat) * 100
                    hacim_degisim_pct = ((simdiki_hacim - onceki_hacim) / onceki_hacim) * 100
                    
                    # 3. ADIM: Son kontrol (15m'de %2+ hareket ve %50+ hacim artışı)
                    if abs(fiyat_degisim_15m) >= 2.0 and hacim_degisim_pct >= 50.0:
                        if time.time() - son.get(symbol, 0) >= 1800: # 30 dk bekleme
                            
                            coin = symbol.replace("USDT", "")
                            saat_simdi = datetime.now(TR_Saati).strftime('%H:%M:%S')
                            
                            # GÖRSELDEKİ FORMAT
                            msg = (f"📊 <b>[{symbol}] Abnormal Volatility Alert (15m)</b>\n"
                                   f"Market: <b>SPOT</b>\n"
                                   f"💰 Price: ${format(simdiki_fiyat, '.4f')} (<b>{format(fiyat_degisim_15m, '+.2f')}%</b>)\n"
                                   f"📊 Volume: ${format(simdiki_hacim/1000, '.2f')}K (<b>{format(hacim_degisim_pct, '+.2f')}%</b>)\n\n"
                                   f"🔗 <a href='https://www.binance.com/en/trade/{symbol}?type=spot'>Binance Spot Grafiği</a>\n"
                                   f"<b>${coin} #{coin}</b>")
                            
                            tg(msg)
                            son[symbol] = time.time()
                            sayac += 1
                            print(f"🔥 SİNYAL: {symbol} | 15m Değişim: %{fiyat_degisim_15m:.2f}", flush=True)
                            
                except Exception as e:
                    pass
                    
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Tur tamamlandı. Sinyal: {sayac}. Yeni tur başlıyor...\n", flush=True)
            time.sleep(2) # 2 saniyede bir log akışı devam eder
            
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

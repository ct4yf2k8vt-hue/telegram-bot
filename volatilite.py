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
    except Exception as e:
        print(f"Telegram Hatası: {e}", flush=True)

def gj(u):
    try:
        req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'})
        return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    except Exception as e:
        print(f"API Hatası ({u}): {e}", flush=True)
        return None

def bot_loop():
    print("--- SADECE SPOT: DEBUG MODUNDA BAŞLADI ---", flush=True)
    son = {}

    while True:
        try:
            print(f"\n[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] 1. ADIM: Binance'e bağlanılıyor...", flush=True)
            
            spot_tickers = gj("https://api.binance.com/api/v3/ticker/24hr")
            
            if not spot_tickers:
                print("HATA: Binance'den veri çekilemedi! (API engeli veya internet sorunu)", flush=True)
                time.sleep(5)
                continue

            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] 2. ADIM: Veri çekildi. {len(spot_tickers)} adet coin filtreleniyor...", flush=True)

            adaylar = []
            for t in spot_tickers:
                if t['symbol'].endswith('USDT'):
                    try:
                        degisim = float(t['priceChangePercent'])
                        hacim24 = float(t['quoteVolume'])
                        
                        # Filtre: 24s'de %3+ değişen ve 5 Milyon Dolar üzeri hacmi olanlar
                        if abs(degisim) >= 3.0 and hacim24 >= 5000000: 
                            adaylar.append(t['symbol'])
                    except:
                        pass

            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] 3. ADIM: Filtreden {len(adaylar)} adet aday geçti. Mumlar kontrol ediliyor...", flush=True)

            if len(adaylar) == 0:
                print("-> Şu an piyasada 24s'de %3'ten fazla hareket eden büyük hacimli coin yok. Bekleniyor...", flush=True)
            else:
                print(f"-> Taranacak Adaylar: {adaylar}", flush=True)

            sayac = 0
            for symbol in adaylar:
                try:
                    # 5 dakikalık mumu çek
                    k = gj(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=5m&limit=3")
                    if not k or len(k) < 3: continue
                    
                    kapanan_mum = k[-2]
                    onceki_mum = k[-3]
                    
                    simdiki_fiyat = float(kapanan_mum[4])
                    onceki_fiyat = float(onceki_mum[4])
                    simdiki_hacim = float(kapanan_mum[7])
                    onceki_hacim = float(onceki_mum[7])
                    
                    if onceki_fiyat == 0 or onceki_hacim == 0: continue
                    
                    fiyat_degisim = ((simdiki_fiyat - onceki_fiyat) / onceki_fiyat) * 100
                    hacim_farki = simdiki_hacim - onceki_hacim
                    hacim_degisim_pct = (hacim_farki / onceki_hacim) * 100 if onceki_hacim > 0 else 0
                    
                    # Kontrol: Fiyat %3+ ve Hacim %500+ (5 Kat)
                    if fiyat_degisim >= 3.0 and hacim_degisim_pct >= 500.0:
                        if time.time() - son.get(symbol, 0) >= 1800: 
                            
                            coin = symbol.replace("USDT", "")
                            saat_simdi = datetime.now(TR_Saati).strftime('%I:%M:%S %p')
                            
                            msg = (f"🚀 <b>Pump - {coin}/USDT [Binance]</b>\n"
                                   f"💰 <b>Price:</b> {format(onceki_fiyat, '.5f')} ➔ {format(simdiki_fiyat, '.5f')} (<b>{format(fiyat_degisim, '+.1f')}%</b>)\n"
                                   f"📊 <b>Volume:</b> {format(simdiki_hacim, '.0f')} (<b>{format(hacim_degisim_pct, '+.0f')} %</b>)\n"
                                   f"Volume increased by {format(hacim_farki, '.0f')} ⬆️\n"
                                   f"Time: {saat_simdi} 🕐\n\n"
                                   f"🔗 <a href='https://www.binance.com/en/trade/{symbol}?type=spot'>Pair on Binance.</a>\n\n"
                                   f"<b>${coin} #{coin}</b>")
                            
                            tg(msg)
                            son[symbol] = time.time()
                            sayac += 1
                            print(f">>> BİNG! SİNYAL GÖNDERİLDİ: {symbol}", flush=True)
                    else:
                        # Sinyal olmasa bile ne kadar hacim/fiyat değişimi olduğunu loga basalım (Debug için)
                        if hacim_degisim_pct > 100: # Sadece hacmi en az 2 katına çıkanları loga basalım ki ekran dolsun
                            print(f"    Kontrol: {symbol} - Fiyat: %{fiyat_degisim:.1f}, Hacim: %{hacim_degisim_pct:.0f} (Kriteri karşılamadı)", flush=True)
                            
                except Exception as e:
                    print(f"Coin İşleme Hatası ({symbol}): {e}", flush=True)
                    
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] TUR BİTTİ | Sinyal: {sayac}. 5 sn sonra tekrar taranacak...", flush=True)
            time.sleep(5) 
            
        except Exception as e:
            print(f"ANA DÖNGÜ HATASI: {e}", flush=True)
            time.sleep(5)

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

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

def get_all_symbols():
    """Binance'teki TÜM Spot ve Futures paritelerini tek seferde çeker."""
    symbols = []
    # Spot pariteleri
    spot_info = gj("https://api.binance.com/api/v3/exchangeInfo")
    if spot_info and 'symbols' in spot_info:
        for s in spot_info['symbols']:
            if s['status'] == 'TRADING' and s['quoteAsset'] == 'USDT':
                symbols.append(('SPOT', s['symbol'], 'https://api.binance.com'))
                
    # Futures pariteleri
    fut_info = gj("https://fapi.binance.com/fapi/v1/exchangeInfo")
    if fut_info and 'symbols' in fut_info:
        for s in fut_info['symbols']:
            if s['status'] == 'TRADING' and s['quoteAsset'] == 'USDT':
                symbols.append(('FUTURES', s['symbol'], 'https://fapi.binance.com'))
    return symbols

def bot_loop():
    print("--- ABNORMAL VOLATILITY BOTU (SPOT + FUTURES) BAŞLADI ---", flush=True)
    son = {}
    all_symbols = get_all_symbols()
    toplam = len(all_symbols)
    print(f"Taranacak Toplam Parite: {toplam}", flush=True)

    while True:
        try:
            print(f"\n[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] YENİ TUR BAŞLIYOR ({toplam} Parite)...", flush=True)
            sayac = 0
            
            for i, (market, symbol, base_url) in enumerate(all_symbols):
                try:
                    if (i + 1) % 50 == 0:
                        print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Taranıyor... ({i+1}/{toplam})", flush=True)

                    # 15 dakikalık grafikte son 2 mumu çek
                    k = gj(f"{base_url}/api/v3/klines?symbol={symbol}&interval=15m&limit=2" if market == 'SPOT' else f"{base_url}/fapi/v1/klines?symbol={symbol}&interval=15m&limit=2")
                    if not k or len(k) < 2: continue
                    
                    onceki_mum = k[0]
                    simdiki_mum = k[1]
                    
                    simdiki_fiyat = float(simdiki_mum[4])
                    onceki_fiyat = float(onceki_mum[4])
                    simdiki_hacim = float(simdiki_mum[7])
                    onceki_hacim = float(onceki_mum[7])
                    
                    if onceki_fiyat == 0 or onceki_hacim == 0: continue
                    
                    fiyat_degisim = ((simdiki_fiyat - onceki_fiyat) / onceki_fiyat) * 100
                    hacim_degisim_pct = ((simdiki_hacim - onceki_hacim) / onceki_hacim) * 100
                    
                    # --- ABNORMAL VOLATILITY MANTIĞI ---
                    # Fiyat en az %5 değişmiş VE Hacim en az %50 artmışsa
                    if abs(fiyat_degisim) >= 5.0 and hacim_degisim_pct >= 50.0:
                        if time.time() - son.get(symbol, 0) >= 1800: # 30 dk bekleme
                            
                            coin = symbol.replace("USDT", "")
                            saat_simdi = datetime.now(TR_Saati).strftime('%H:%M:%S')
                            
                            # GÖRSELDEKİ FORMAT (100eyes tarzı)
                            msg = (f"📊 <b>[{symbol}] Abnormal Volatility Alert (15m)</b>\n"
                                   f"Market: <b>{market}</b>\n"
                                   f"💰 Price: ${format(simdiki_fiyat, '.4f')} (<b>{format(fiyat_degisim, '+.2f')}%</b>)\n"
                                   f"📊 Volume: ${format(simdiki_hacim/1000, '.2f')}K (<b>{format(hacim_degisim_pct, '+.2f')}%</b>)\n\n"
                                   f"🔗 <a href='https://www.binance.com/en/trade/{symbol}?type=spot'>Spot Grafiği</a> | "
                                   f"<a href='https://www.binance.com/en/futures/{symbol}'>Futures Grafiği</a>\n"
                                   f"<b>${coin} #{coin}</b>")
                            
                            tg(msg)
                            son[symbol] = time.time()
                            sayac += 1
                            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] >>> SİNYAL GÖNDERİLDİ: {symbol} ({market})", flush=True)
                            
                    time.sleep(0.1) # API limitini korumak için minimum bekleme
                except Exception as e:
                    pass
                    
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] TUR BİTTİ | Sinyal: {sayac}", flush=True)
            print(f"--- 30 Saniye Bekleniyor ---", flush=True)
            time.sleep(30) 
            
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

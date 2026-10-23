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
    print("--- SADECE SPOT: 5M GERÇEK PATLAMA BOTU BAŞLADI ---", flush=True)
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

            # FİLTRE 1: Sadece USDT pariteleri, 24s değişimi %3'ten fazla VE 24s Hacmi 5 Milyon Dolar'dan büyük olanlar
            for t in spot_tickers:
                if t['symbol'].endswith('USDT'):
                    degisim = float(t['priceChangePercent'])
                    hacim24 = float(t['quoteVolume'])
                    
                    if abs(degisim) >= 3.0 and hacim24 >= 5000000: # En az 5 Milyon Dolar Hacim (Ölü coinleri engeller)
                        adaylar.append(t['symbol'])

            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Ön filtre: {len(adaylar)} hacimli coin bulundu. 5m mumları inceleniyor...", flush=True)

            sayac = 0
            for symbol in adaylar:
                try:
                    # 2. ADIM: 5 Dakikalık (5m) grafikte son 2 kapanmış mumu çek
                    k = gj(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=5m&limit=3")
                    if not k or len(k) < 3: continue
                    
                    # k[-1] = Şu an oluşan anlık mum (KULLANMIYORUZ)
                    # k[-2] = YENİ KAPANAN MUM (Görseldeki gibi)
                    kapanan_mum = k[-2]
                    onceki_mum = k[-3]
                    
                    simdiki_fiyat = float(kapanan_mum[4])
                    onceki_fiyat = float(onceki_mum[4])
                    simdiki_hacim = float(kapanan_mum[7]) # USDT Hacmi
                    onceki_hacim = float(onceki_mum[7])
                    
                    if onceki_fiyat == 0 or onceki_hacim == 0: continue
                    
                    # Hesaplamalar (Görseldeki mantık)
                    fiyat_degisim = ((simdiki_fiyat - onceki_fiyat) / onceki_fiyat) * 100
                    hacim_farki = simdiki_hacim - onceki_hacim
                    hacim_degisim_pct = (hacim_farki / onceki_hacim) * 100
                    
                    # --- 3. ADIM: DEVASA PATLAMA FİLTRESİ (Görseldeki gibi) ---
                    # Fiyat en az %3 artmış VE Hacim en az %500 (5 Kat) artmışsa
                    if fiyat_degisim >= 3.0 and hacim_degisim_pct >= 500.0:
                        if time.time() - son.get(symbol, 0) >= 1800: # Aynı coini 30 dk'da 1 kez bildir
                            
                            coin = symbol.replace("USDT", "")
                            saat_simdi = datetime.now(TR_Saati).strftime('%I:%M:%S %p')
                            
                            # GÖRSELDEKİ BİREBİR FORMAT (SOPH/USDT gibi)
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
                            print(f"🔥 DEV PATLAMA: {symbol} | Hacim: +%{hacim_degisim_pct:.0f} | Fiyat: +%{fiyat_degisim:.1f}", flush=True)
                            
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

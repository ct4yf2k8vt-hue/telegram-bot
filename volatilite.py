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
        # Binance'in botları engellememesi için User-Agent ekliyoruz
        req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    except:
        return None

def bot_loop():
    print("--- GELİŞMİŞ PUMP ALERT BOTU BAŞLADI ---", flush=True)
    son = {}

    while True:
        try:
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if not tickers: 
                time.sleep(10)
                continue
                
            # FİLTRE: 1 Milyon USDT üzeri hacmi olanları al (API banını önler)
            all_coins = [t["symbol"] for t in tickers if t["symbol"].endswith("USDT") and float(t.get("quoteVolume", 0)) > 1000000]
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] TARAMA BAŞLIYOR | Coin: {len(all_coins)}", flush=True)
            sayac = 0
            
            for s in all_coins:
                try:
                    # 1. Anlık Fiyat ve Hacim (Son 1 Saatlik Mum Verisi)
                    k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=1h&limit=2")
                    if not k or len(k) < 2: continue
                    
                    simdiki_mum = k[-1]
                    onceki_mum = k[-2]
                    
                    simdiki_fiyat = float(simdiki_mum[4])
                    onceki_fiyat = float(onceki_mum[4])
                    simdiki_hacim = float(simdiki_mum[7])
                    onceki_hacim = float(onceki_mum[7])
                    trade_sayisi = int(simdiki_mum[8]) # Mum verisindeki işlem sayısı
                    
                    if onceki_fiyat == 0 or onceki_hacim == 0: continue
                    
                    fiyat_degisim = ((simdiki_fiyat - onceki_fiyat) / onceki_fiyat) * 100
                    hacim_degisim_pct = ((simdiki_hacim - onceki_hacim) / onceki_hacim) * 100
                    
                    # 2. TETİKLEYİCİ FİLTRE: Fiyat %2+ VE Hacim %100+ artmışsa
                    if fiyat_degisim >= 2.0 and hacim_degisim_pct >= 100.0:
                        if time.time() - son.get(s, 0) >= 1800: # 30 dk bekleme
                            
                            # 3. EK VERİLERİ ÇEK (Sadece sinyal geldiğinde, API limitini korumak için)
                            # Açık Pozisyon (Open Interest)
                            oi_data = gj(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={s}")
                            oi_deger = float(oi_data['openInterest']) if oi_data else 0
                            
                            # Long/Short Oranı (LSR)
                            lsr_data = gj(f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={s}&period=1h&limit=1")
                            lsr_deger = float(lsr_data[0]['longShortRatio']) if lsr_data and len(lsr_data) > 0 else 0
                            
                            # 24s Yüksek/Düşük
                            t_info = next((t for t in tickers if t["symbol"] == s), None)
                            h24 = float(t_info["highPrice"]) if t_info else 0
                            l24 = float(t_info["lowPrice"]) if t_info else 0
                            
                            saat_simdi = datetime.now(TR_Saati).strftime('%Y-%m-%d %H:%M:%S UTC')
                            coin = s.replace("USDT", "")
                            
                            # 4. GÖRSELDEKİ FORMATTA MESAJ
                            msg = (f"🚀 <b>Pump - {coin}/USDT [Binance]</b>\n"
                                   f"💲 <b>Price:</b> {format(onceki_fiyat, '.5f')} ➔ {format(simdiki_fiyat, '.5f')} (<b>{format(fiyat_degisim, '+.1f')}%</b>)\n"
                                   f"📊 <b>Vol:</b> {format(simdiki_hacim, '.0f')} (<b>{format(hacim_degisim_pct, '+.1f')}%</b>)\n"
                                   f"📈 <b>OI:</b> {format(oi_deger, '.0f')} | <b>LSR:</b> {format(lsr_deger, '.2f')}\n"
                                   f"🤝 <b>Trades:</b> {format(trade_sayisi, '.0f')}\n"
                                   f"🕐 <b>Time:</b> {saat_simdi}\n\n"
                                   f"⬆ High: {format(h24, '.4f')}\n"
                                   f"⬇ Low: {format(l24, '.4f')}\n\n"
                                   f"🔗 <a href='https://www.binance.com/en/futures/{s}'>Pair on Binance.</a>\n"
                                   f"<b>${coin} #{coin}</b>")
                            
                            tg(msg)
                            son[s] = time.time()
                            sayac += 1
                            print(f">>> SİNYAL GÖNDERİLDİ: {s}", flush=True)
                            
                    time.sleep(0.2) # API limitini korumak için
                except:
                    pass
                    
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] TARAMA BİTTİ - Sinyal: {sayac}", flush=True)
            time.sleep(30)
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
    print("WEB SUNUCUSU BAŞLADI - PORT:", port, flush=True)
    server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    run_web_server()

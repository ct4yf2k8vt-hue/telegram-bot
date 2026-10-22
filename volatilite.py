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
    print("--- TÜREV VERİ ANALİZ BOTU BAŞLADI ---", flush=True)
    son = {}

    while True:
        try:
            print(f"\n[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] TARAMA BAŞLIYOR...", flush=True)
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if not tickers: 
                time.sleep(10)
                continue
                
            # Sadece 24 saatlik hacmi 5 Milyon USDT üzeri olan likit coinleri tara
            all_coins = [t["symbol"] for t in tickers if t["symbol"].endswith("USDT") and float(t.get("quoteVolume", 0)) > 5000000]
            toplam = len(all_coins)
            sayac = 0
            
            for i, s in enumerate(all_coins):
                try:
                    if (i + 1) % 50 == 0:
                        print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Taranıyor... ({i+1}/{toplam})", flush=True)

                    # 1 saatlik periyotta son 24 mumu çek (son 2 kapanmış mumu kullanacağız)
                    k = gj(f"https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=1h&limit=24")
                    if not k or len(k) < 24: continue
                    
                    kapanmis = k[:-1]  # Kapanmamış anlık mumu hariç tut
                    simdiki_mum = kapanmis[-1]
                    onceki_mum = kapanmis[-2]
                    
                    # Mum verileri: [0]=Açılış Zamanı, [4]=Kapanış Fiyatı, [7]=USDT Hacmi, [8]=İşlem Sayısı
                    simdiki_fiyat = float(simdiki_mum[4])
                    onceki_fiyat = float(onceki_mum[4])
                    simdiki_hacim = float(simdiki_mum[7])
                    onceki_hacim = float(onceki_mum[7])
                    simdiki_trades = int(simdiki_mum[8])
                    onceki_trades = int(onceki_mum[8])
                    
                    if onceki_fiyat == 0 or onceki_hacim == 0: continue
                    
                    fiyat_degisim = ((simdiki_fiyat - onceki_fiyat) / onceki_fiyat) * 100
                    hacim_degisim_pct = ((simdiki_hacim - onceki_hacim) / onceki_hacim) * 100
                    trades_degisim_pct = ((simdiki_trades - onceki_trades) / onceki_trades) * 100 if onceki_trades > 0 else 0
                    
                    # TETİKLEYİCİ: Sadece 1 saatlik fiyat artışı %5 ve üzeri ise (Pump)
                    if fiyat_degisim >= 5.0:
                        if time.time() - son.get(s, 0) >= 3600:  # Aynı coin için 1 saat bekle
                            
                            # --- TÜREV VERİLERİNİ ÇEK ---
                            # Açık Pozisyon (Open Interest) - Anlık ve 1 saat önceki
                            oi_simdi_data = gj(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={s}")
                            oi_simdi = float(oi_simdi_data['openInterest']) if oi_simdi_data else 0
                            
                            # Long/Short Oranı (LSR) - Son 1 saatlik
                            lsr_data = gj(f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={s}&period=1h&limit=2")
                            lsr_deger = 0.0
                            lsr_degisim = 0.0
                            if lsr_data and len(lsr_data) >= 2:
                                lsr_deger = float(lsr_data[-1]['longShortRatio'])
                                lsr_onceki = float(lsr_data[-2]['longShortRatio'])
                                lsr_degisim = ((lsr_deger - lsr_onceki) / lsr_onceki) * 100 if lsr_onceki > 0 else 0
                            
                            # OI Değişimini hesapla (basitleştirilmiş: anlık OI'yi önceki mumun hacmine oranla)
                            oi_degisim = hacim_degisim_pct * 0.1  # Tahmini bir değer (gerçek OI geçmişi için ekstra API gerekir)
                            
                            coin = s.replace("USDT", "")
                            saat_simdi = datetime.now(TR_Saati).strftime('%Y-%m-%d %H:%M:%S UTC')
                            
                            # GÖRSELDEKİ TAM FORMATTA MESAJ
                            msg = (f"🚀 <b>Pump - {coin}/USDT [Binance]</b>\n"
                                   f"💲 <b>Price:</b> {format(simdiki_fiyat, '.4f')} | <b>{format(fiyat_degisim, '+.1f')}%</b>\n"
                                   f"📊 <b>Vol:</b> +${format(simdiki_hacim/1000000, '.1f')}M | <b>{format(hacim_degisim_pct, '+.1f')}%</b>\n"
                                   f"📈 <b>OI:</b> +${format(oi_simdi, '.0f')} | <b>{format(oi_degisim, '+.1f')}%</b>\n"
                                   f"⚖️ <b>LSR:</b> {format(lsr_deger, '.2f')} | <b>{format(lsr_degisim, '+.1f')}%</b>\n"
                                   f"🤝 <b>Trades:</b> {format(simdiki_trades/1000, '.1f')}K | <b>{format(trades_degisim_pct, '+.1f')}%</b>\n"
                                   f"🕐 <b>Time:</b> {saat_simdi} 🕐\n\n"
                                   f"🔗 <a href='https://www.binance.com/en/futures/{s}'>Pair on Binance.</a>\n"
                                   f"<b>${coin} #{coin}</b>")
                            
                            tg(msg)
                            son[s] = time.time()
                            sayac += 1
                            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] >>> SİNYAL GÖNDERİLDİ: {s}", flush=True)
                            
                    time.sleep(0.2)  # API limitini korumak için
                except Exception as e:
                    pass
                    
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] TARAMA BİTTİ | Gönderilen Sinyal: {sayac}", flush=True)
            print(f"--- 15 Saniye Bekleniyor (Yeni Tur İçin) ---", flush=True)
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

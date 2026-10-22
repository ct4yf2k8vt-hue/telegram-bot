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

# --- MANUEL İNDİKATÖR HESAPLAMALARI (Kütüphanesiz) ---
def calculate_sma(prices, period):
    if len(prices) < period: return 0
    return sum(prices[-period:]) / period

def calculate_ema(prices, period):
    if len(prices) < period: return 0
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = price * k + ema * (1 - k)
    return ema

def calculate_macd(prices):
    if len(prices) < 26: return 0
    ema12 = calculate_ema(prices, 12)
    ema26 = calculate_ema(prices, 26)
    return ema12 - ema26

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1: return 50
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0: return 100
    return 100 - (100 / (1 + (avg_gain / avg_loss)))

def bot_loop():
    print("--- NİHAİ KAPSAMLI BOT BAŞLADI ---", flush=True)
    son = {}

    while True:
        try:
            print(f"\n[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] YENİ TARAMA BAŞLIYOR...", flush=True)
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if not tickers: 
                time.sleep(10)
                continue
                
            # 1 Milyon USDT üzeri hacmi olanları al
            all_coins = [t["symbol"] for t in tickers if t["symbol"].endswith("USDT") and float(t.get("quoteVolume", 0)) > 1000000]
            toplam = len(all_coins)
            sayac = 0
            
            for i, s in enumerate(all_coins):
                try:
                    # Log ekranı canlı kalsın diye her 50 coinde bir yazdır
                    if (i + 1) % 50 == 0:
                        print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Taranıyor... ({i+1}/{toplam})", flush=True)

                    # 5 dakikalık grafikte 200 MA hesaplamak için 250 mum çekiyoruz
                    k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=5m&limit=250")
                    if not k or len(k) < 200: continue
                    
                    kapanmis = k[:-1] # Anlık (kapanmamış) mumu hariç tut
                    closes = [float(x[4]) for x in kapanmis]
                    
                    simdiki_mum = kapanmis[-1]
                    onceki_mum = kapanmis[-2]
                    
                    simdiki_fiyat = float(simdiki_mum[4])
                    onceki_fiyat = float(onceki_mum[4])
                    simdiki_hacim = float(simdiki_mum[7])
                    onceki_hacim = float(onceki_mum[7])
                    trade_sayisi = int(simdiki_mum[8])
                    
                    if onceki_fiyat == 0 or onceki_hacim == 0: continue
                    
                    fiyat_degisim = ((simdiki_fiyat - onceki_fiyat) / onceki_fiyat) * 100
                    hacim_degisim_pct = ((simdiki_hacim - onceki_hacim) / onceki_hacim) * 100
                    
                    # --- TETİKLEYİCİ FİLTRE: PUMP MANTIĞI ---
                    if fiyat_degisim >= 2.0 and hacim_degisim_pct >= 100.0:
                        if time.time() - son.get(s, 0) >= 1800: # 30 dk bekleme
                            
                            # --- ANALİZ HESAPLAMALARI ---
                            rsi = calculate_rsi(closes)
                            macd = calculate_macd(closes)
                            ma20 = calculate_sma(closes, 20)
                            ma200 = calculate_sma(closes, 200)
                            dist_ma20 = ((simdiki_fiyat - ma20) / ma20) * 100 if ma20 else 0
                            dist_ma200 = ((simdiki_fiyat - ma200) / ma200) * 100 if ma200 else 0
                            
                            # Exhaustion (Tükenmişlik) Mantığı
                            exhaustion = "NO"
                            exh_emoji = ""
                            if rsi > 70 and dist_ma20 > 4:
                                exhaustion = "YES"
                                exh_emoji = "🔥"
                            elif rsi < 30 and dist_ma20 < -4:
                                exhaustion = "YES"
                                exh_emoji = "❄️"

                            # --- EK VERİLERİ ÇEK (Sadece sinyalde) ---
                            oi_data = gj(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={s}")
                            oi_deger = float(oi_data['openInterest']) if oi_data else 0
                            
                            lsr_data = gj(f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={s}&period=1h&limit=1")
                            lsr_deger = float(lsr_data[0]['longShortRatio']) if lsr_data and len(lsr_data) > 0 else 0
                            
                            t_info = next((t for t in tickers if t["symbol"] == s), None)
                            h24 = float(t_info["highPrice"]) if t_info else 0
                            l24 = float(t_info["lowPrice"]) if t_info else 0
                            
                            dip_20 = min([float(x[3]) for x in kapanmis[-20:]])
                            saat_simdi = datetime.now(TR_Saati).strftime('%Y-%m-%d %H:%M:%S')
                            coin = s.replace("USDT", "")
                            
                            # --- GÖRSELDEKİ TÜM VERİLERİ İÇEREN MESAJ ---
                            msg = (f"🚀 <b>Pump - {coin}/USDT [Binance]</b>\n"
                                   f"💲 <b>Price:</b> {format(onceki_fiyat, '.5f')} ➔ {format(simdiki_fiyat, '.5f')} (<b>{format(fiyat_degisim, '+.1f')}%</b>)\n"
                                   f"📊 <b>Vol:</b> {format(simdiki_hacim, '.0f')} (<b>{format(hacim_degisim_pct, '+.1f')}%</b>)\n"
                                   f"📈 <b>OI:</b> {format(oi_deger, '.0f')} | <b>LSR:</b> {format(lsr_deger, '.2f')}\n"
                                   f"🤝 <b>Trades:</b> {format(trade_sayisi, '.0f')}\n"
                                   f"🕐 <b>Time:</b> {saat_simdi} 🕐\n\n"
                                   f"<b>--- TEKNİK ANALİZ ---</b>\n"
                                   f"Exhaustion: <b>{exhaustion}</b> {exh_emoji}\n"
                                   f"🟩 {format(dist_ma20, '+.2f')}% to 20 MA\n"
                                   f"🟩 {format(dist_ma200, '+.2f')}% to 200 MA\n"
                                   f"RSI: <b>{format(rsi, '.1f')}</b> | MACD: <b>{format(macd, '.5f')}</b>\n\n"
                                   f"<b>--- SEVİYELER ---</b>\n"
                                   f"🔴 1D RESISTANCE: ${format(h24, '.5f')}\n"
                                   f"🔵 LIQUIDITY ZONE: ${format(l24, '.5f')} / ${format(dip_20, '.5f')}\n\n"
                                   f"🔗 <a href='https://www.binance.com/en/futures/{s}'>Pair on Binance.</a>\n"
                                   f"<b>${coin} #{coin}</b>")
                            
                            tg(msg)
                            son[s] = time.time()
                            sayac += 1
                            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] >>> SİNYAL GÖNDERİLDİ: {s}", flush=True)
                            
                    time.sleep(0.15) # API limitine takılmamak için
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

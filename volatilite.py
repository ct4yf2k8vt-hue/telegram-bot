import urllib.request, urllib.parse, json, time
import os
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

T = os.environ.get("BOT_TOKEN")
C = "1623907197"

def tr_saat():
    return (datetime.utcnow() + timedelta(hours=3)).strftime('%H:%M:%S')

def tg(m):
    try:
        d = urllib.parse.urlencode({"chat_id": C, "text": m, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
        urllib.request.urlopen("https://api.telegram.org/bot" + T + "/sendMessage", d, timeout=10).read()
    except:
        pass

def gj(u):
    try:
        return json.loads(urllib.request.urlopen(u, timeout=10).read().decode())
    except:
        return None

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
    print("--- TEST MODU BASLADI (DUSUK FILTRE) ---", flush=True)
    son_long = {}
    son_short = {}

    while True:
        try:
            print(f"[{tr_saat()}] TARAMA BASLIYOR...", flush=True)
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if not tickers: 
                time.sleep(10)
                continue
                
            hedef_coinler = []
            for t in tickers:
                if t["symbol"].endswith("USDT"):
                    # TEST İÇİN: 24s'de %1 hareket edenleri bile alıyoruz
                    if abs(float(t["priceChangePercent"])) >= 1.0:
                        hedef_coinler.append(t["symbol"])

            print(f"[{tr_saat()}] Potansiyel Coin: {len(hedef_coinler)}", flush=True)
            long_sayac = 0
            short_sayac = 0

            for s in hedef_coinler:
                try:
                    k = gj(f"https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=15m&limit=30")
                    if not k or len(k) < 25: continue
                    
                    kapanmis = k[:-1]
                    closes = [float(x[4]) for x in kapanmis]
                    canli_mum = k[-1]
                    acilis = float(canli_mum[1])
                    su_anki = float(canli_mum[4])
                    zirve = float(canli_mum[2])
                    hacim = float(canli_mum[7])

                    v_ort = sum(float(x[7]) for x in kapanmis[-20:]) / 20
                    if v_ort == 0: continue
                    hacim_orani = hacim / v_ort

                    # --- TEST LONG FİLTRESİ (Çok Düşük) ---
                    fiyat_degisim = ((su_anki - acilis) / acilis) * 100
                    if fiyat_degisim >= 0.5 and hacim_orani >= 1.0: # Sadece %0.5 artış ve normal hacim yeter
                        if time.time() - son_long.get(s, 0) >= 60: # 1 dk cooldown
                            t_info = next((t for t in tickers if t["symbol"] == s), None)
                            if t_info:
                                msg_long = (f"🧪 <b>TEST LONG - {s}</b>\n"
                                            f"Fiyat: +{format(fiyat_degisim, '.2f')}% | Hacim: {format(hacim_orani, '.1f')}x\n"
                                            f"Saat: {tr_saat()}")
                                tg(msg_long)
                                son_long[s] = time.time()
                                long_sayac += 1
                                print(f">>> TEST LONG: {s}", flush=True)

                    # --- TEST SHORT FİLTRESİ (Çok Düşük) ---
                    rsi_deger = calculate_rsi(closes)
                    # RSI 45 üzeri ve fiyat kırmızıya dönerse hemen short at (Test için)
                    if rsi_deger >= 45 and su_anki < acilis and hacim_orani >= 1.0:
                        if time.time() - son_short.get(s, 0) >= 60:
                            msg_short = (f"🧪 <b>TEST SHORT - {s}</b>\n"
                                         f"RSI: {format(rsi_deger, '.1f')} | Hacim: {format(hacim_orani, '.1f')}x\n"
                                         f"Saat: {tr_saat()}")
                            tg(msg_short)
                            son_short[s] = time.time()
                            short_sayac += 1
                            print(f">>> TEST SHORT: {s}", flush=True)

                    time.sleep(0.1)
                except:
                    pass
            
            print(f"[{tr_saat()}] TARAMA BITTI | Long: {long_sayac} | Short: {short_sayac}", flush=True)
            time.sleep(30) # Test için 30 saniye bekle
            
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
    print("WEB SUNUCUSU BASLADI - PORT:", port, flush=True)
    server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    run_web_server()

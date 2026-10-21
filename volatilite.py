import urllib.request, urllib.parse, json, time
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

T = os.environ.get("BOT_TOKEN")
C = "1623907197"

def tg(m):
    try:
        d = urllib.parse.urlencode({"chat_id": C, "text": m, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
        urllib.request.urlopen("https://api.telegram.org/bot" + T + "/sendMessage", d, timeout=10).read()
    except:
        pass

def gj(u):
    try:
        return json.loads(urllib.request.urlopen(u, timeout=15).read().decode())
    except:
        return None

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def mexc_loop():
    print("--- AZCRYPTO TARZI MOTOR BASLADI (MEXC) ---", flush=True)
    son = {}
    
    all_coins = []
    while not all_coins:
        print("MEXC coin listesi aliniyor...", flush=True)
        tickers = gj("https://api.mexc.com/api/v3/ticker/24hr")
        if tickers:
            all_coins = [t["symbol"] for t in tickers if t.get("symbol", "").endswith("USDT")]
        if not all_coins:
            time.sleep(10)
            
    print("MEXC COIN SAYISI: " + str(len(all_coins)), flush=True)
    tg("🔥 <b>MEXC AZCRYPTO TARZI BOT AKTIF</b>\n" + str(len(all_coins)) + " coin izleniyor.")

    while True:
        try:
            print("MEXC TARAMASI BASLIYOR", flush=True)
            sayac = 0
            
            for s in all_coins:
                try:
                    # 5 dakikalik mumlari al (RSI ve Hacim icin)
                    k_mexc = gj("https://api.mexc.com/api/v3/klines?symbol=" + s + "&interval=5m&limit=30")
                    if not k_mexc or len(k_mexc) < 25: 
                        time.sleep(0.1)
                        continue
                    
                    # Kapanmis mumlar (sonuncuyu at)
                    kapanmis = k_mexc[:-1]
                    closes = [float(x[4]) for x in kapanmis]
                    
                    canli_mum = k_mexc[-1]
                    acilis = float(canli_mum[1])
                    su_anki = float(canli_mum[4])
                    hacim = float(canli_mum[5])
                    
                    fiyat_degisim = ((su_anki - acilis) / acilis) * 100
                    
                    # Hacim Orani
                    v_ort = sum(float(x[5]) for x in kapanmis[-20:]) / 20
                    if v_ort == 0: 
                        time.sleep(0.1)
                        continue
                    hacim_orani = hacim / v_ort
                    
                    # FILTRE: %3+ Fiyat ve 2.5x+ Hacim
                    if fiyat_degisim >= 3.0 and hacim_orani >= 2.5:
                        if time.time() - son.get(s, 0) >= 1800:
                            
                            # --- EKSTRA VERILERI CEK ---
                            # 1. Taker Orani (Son 100 islem)
                            trades_data = gj("https://api.mexc.com/api/v3/trades?symbol=" + s + "&limit=100")
                            taker_pct = 50
                            if trades_data and isinstance(trades_data, list):
                                buy_vol = sum(float(t["qty"]) for t in trades_data if t["isBuyerMaker"] == False)
                                total_vol = sum(float(t["qty"]) for t in trades_data)
                                if total_vol > 0:
                                    taker_pct = (buy_vol / total_vol) * 100
                            
                            # 2. 24s Yuksek/Dusuk
                            ticker = gj("https://api.mexc.com/api/v3/ticker/24hr?symbol=" + s)
                            h24 = float(ticker["highPrice"]) if ticker else 0
                            l24 = float(ticker["lowPrice"]) if ticker else 0
                            
                            # 3. RSI
                            rsi = calculate_rsi(closes)
                            
                            # --- MESAJ FORMATI (AzCryptoBot Tarzi) ---
                            pair = s.replace("USDT", "/USDT")
                            coin = s.replace("USDT", "")
                            
                            # Hacim Barı (Kac katı ise o kadar 💰)
                            hacim_bari = "💰" * int(min(hacim_orani, 10))
                            
                            msg = (f"<b>#{s}</b> 💚 <b>Price +{format(fiyat_degisim, '.2f')}% Vol</b>\n"
                                   f"❌ {format(hacim_orani, '.1f')} {hacim_bari} (M5 Spot)\n\n"
                                   f"Total Vol {format(hacim, '.0f')} Taker {format(taker_pct, '.0f')}% Rsi/{format(rsi, '.0f')}\n"
                                   f"────────────────\n\n"
                                   f"🏷 Price: {format(su_anki, '.4f')} 🚀 pump ···\n"
                                   f"🕐 24h: +{format(fiyat_degisim, '.2f')}% Vol {format(hacim, '.0f')}\n"
                                   f"⬆ High: {format(h24, '.4f')} ({format(((h24 - su_anki)/su_anki)*100, '.2f')}%)\n"
                                   f"⬇ Low: {format(l24, '.4f')} (+{format(((su_anki - l24)/l24)*100, '.2f')}%)\n\n"
                                   f"🔗 <a href='https://www.mexc.com/exchange/{s}'>Pair on MEXC.</a>")
                            
                            tg(msg)
                            son[s] = time.time()
                            sayac += 1
                            print(">>> MEXC SINYAL:", s, flush=True)
                    
                    time.sleep(0.2) 
                    
                except:
                    pass
            
            print("MEXC TARAMASI BITTI - Sinyal: " + str(sayac), flush=True)
            time.sleep(60)
            
        except Exception as e:
            print("MEXC HATA:", e, flush=True)
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
    threading.Thread(target=mexc_loop, daemon=True).start()
    run_web_server()

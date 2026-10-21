import urllib.request, urllib.parse, json, time
import os
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

T = os.environ.get("BOT_TOKEN")
C = "1623907197"

# Türkiye Saati (UTC+3)
TR_Saati = timezone(timedelta(hours=3))

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
    print("--- AZCRYPTO TARZI BOT BASLADI ---", flush=True)
    son = {}

    while True:
        try:
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if not tickers: 
                time.sleep(10)
                continue
                
            all_coins = [t["symbol"] for t in tickers if t["symbol"].endswith("USDT")]
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] TARAMA BASLIYOR | Coin: {len(all_coins)}", flush=True)
            sayac = 0
            
            for s in all_coins:
                try:
                    k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=15m&limit=30")
                    if not k or len(k) < 22: continue
                    
                    kapanmis = k[:-1]
                    closes = [float(x[4]) for x in kapanmis]
                    
                    son_mum = kapanmis[-1]
                    fiyat_simdi = float(son_mum[4])
                    fiyat_onceki = float(kapanmis[-2][4])
                    hacim_usdt = float(son_mum[7])
                    
                    fiyat_degisim = ((fiyat_simdi - fiyat_onceki) / fiyat_onceki) * 100
                    
                    onceki_20 = [float(x[7]) for x in kapanmis[-21:]]
                    v_ort = sum(onceki_20) / 20
                    if v_ort == 0: continue
                    hacim_orani = hacim_usdt / v_ort
                    
                    # FİLTRE: Fiyat %3+ VE Hacim 2x+
                    if fiyat_degisim >= 3.0 and hacim_orani >= 2.0:
                        if time.time() - son.get(s, 0) >= 1800:
                            
                            t_info = next((t for t in tickers if t["symbol"] == s), None)
                            if not t_info: continue
                            h24 = float(t_info["highPrice"])
                            l24 = float(t_info["lowPrice"])
                            degisim24 = float(t_info["priceChangePercent"])
                            hacim24 = float(t_info["quoteVolume"])
                            
                            taker_pct = 50
                            taker = gj("https://fapi.binance.com/futures/data/takerlongshortRatio?symbol=" + s + "&period=15m&limit=1")
                            if taker and len(taker) > 0: taker_pct = float(taker[0]["buySellRatio"]) * 100
                            
                            rsi_deger = calculate_rsi(closes)
                            
                            pair = s.replace("USDT", "/USDT")
                            coin = s.replace("USDT", "")
                            hacim_bari = "💰" * int(min(hacim_orani, 10))
                            rsi_emoji = "🔴" if rsi_deger > 70 else "🟢" if rsi_deger < 30 else ""
                            
                            msg = (f"<b>#{s}</b> 💚 <b>Price +{format(fiyat_degisim, '.2f')}% Vol</b>\n"
                                   f"❌ {format(hacim_orani, '.1f')} {hacim_bari} (M15 Futures)\n\n"
                                   f"Total Vol {format(hacim_usdt, '.0f')} Taker {format(taker_pct, '.0f')}% Rsi/{format(rsi_deger, '.0f')}{rsi_emoji}\n"
                                   f"────────────────\n\n"
                                   f"🏷 Price: {format(fiyat_simdi, '.4f')} 🚀 pump ···\n"
                                   f"🕐 24h: {format(degisim24, '+.2f')}% Vol {format(hacim24/1000000, '.2f')}M\n"
                                   f"⬆ High: {format(h24, '.4f')} ({format(((h24 - fiyat_simdi)/fiyat_simdi)*100, '.2f')}%)\n"
                                   f"⬇ Low: {format(l24, '.4f')} ({format(((fiyat_simdi - l24)/l24)*100, '+.2f')}%)\n\n"
                                   f"🔗 <a href='https://www.binance.com/en/futures/{s}'>Pair on Binance.</a>")
                            
                            tg(msg)
                            son[s] = time.time()
                            sayac += 1
                            print(f">>> SINYAL GONDERILDI: {s}", flush=True)
                            
                    time.sleep(0.05)
                except:
                    pass
                    
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] TARAMA BITTI - Sinyal: {sayac}", flush=True)
            time.sleep(180)
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

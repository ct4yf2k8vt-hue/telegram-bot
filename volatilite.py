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

# ==========================================
# BINANCE MOTORU
# ==========================================
def binance_loop():
    print("--- BINANCE MOTORU BASLADI ---", flush=True)
    son = {}
    while True:
        try:
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if tickers:
                all_coins = [t["symbol"] for t in tickers if t["symbol"].endswith("USDT")]
                print(f"[{time.strftime('%H:%M:%S')}] BINANCE TARANIYOR | Coin: {len(all_coins)}", flush=True)
                for s in all_coins:
                    try:
                        # 1 dakikalık mumlar (Anlık yakalamak için)
                        k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=1m&limit=25")
                       format if not k or len(k) < 22: continue
                        
                        kapanmis = k[:-1]
                        closes = [float(x[4]) for x in kapanmis]
                        canli = k[-1]
                        
                        acilis = float(canli[1])
                        su_anki = float(canli[4])
                        hacim = float(canli[7])
                        fiyat_degisim = ((su_anki - acilis) / acilis) * 100
                        
                        v_ort = sum(float(x[7]) for x in kapanmis[-20:]) / 20
                        if v_ort == 0: continue
                        hacim_orani = hacim / v_ort
                        
                        # Filtre: %3+ fiyat ve 2.5x+ hacim
                        if fiyat_degisim >= 3.0 and hacim_orani >= 2.5:
                            if time.time() - son.get(s, 0) >= 1800: # 30 dk
                                ticker = gj("https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=" + s)
                                if not ticker: continue
                                
                                taker_pct = 50
                                taker = gj("https://fapi.binance.com/futures/data/takerlongshortRatio?symbol=" + s + "&period=5m&limit=1")
                                if taker and len(taker) > 0: taker_pct = float(taker[0]["buySellRatio"]) * 100
                                
                                h24 = float(ticker["highPrice"])
                                l24 = float(ticker["lowPrice"])
                                rsi = calculate_rsi(closes)
                                
                                pair = s.replace("USDT", "/USDT")
                                coin = s.replace("USDT", "")
                                hacim_bari = "💰" * int(min(hacim_orani, 10))
                                rsi_emoji = "🔴" if rsi > 70 else "🟢" if rsi < 30 else ""
                                
                                msg = (f"<b>#{s}</b> 💚 <b>Price +{format(fiyat_degisim, '.2f')}% Vol</b>\n"
                                       f"❌ {format(hacim_orani, '.1f')} {hacim_bari} (M5 Spot)\n\n"
                                       f"Total Vol {format(hacim, '.0f')} Taker {format(taker_pct, '.0f')}% Rsi/{format(rsi, '.0f')}{rsi_emoji}\n"
                                       f"────────────────\n\n"
                                       f"🏷 Start Price: <code>{format(acilis, '.5f')}</code>\n"
                                       f"🏷 Current Price: <code>{format(su_anki, '.5f')}</code> 🚀 pump\n"
                                       f"🕐 24h: {format(float(ticker['priceChangePercent']), '+.2f')}% Vol {format(float(ticker['quoteVolume'])/1000000, '.2f')}M\n"
                                       f"⬆ High: {format(h24, '.4f')} ({format(((h24 - su_anki)/su_anki)*100, '.2f')}%)\n"
                                       f"⬇ Low: {format(l24, '.4f')} ({format(((su_anki - l24)/l24)*100, '+.2f')}%)\n\n"
                                       f"🔗 <b>BINANCE</b>")
                                tg(msg)
                                son[s] = time.time()
                                print(f">>> BINANCE SINYAL: {s}", flush=True)
                        time.sleep(0.05)
                    except: pass
            time.sleep(60)
        except Exception as e:
            print("BINANCE HATA:", e, flush=True); time.sleep(10)

# ==========================================
# MEXC MOTORU
# ==========================================
def mexc_loop():
    print("--- MEXC MOTORU BASLADI ---", flush=True)
    son = {}
    while True:
        try:
            tickers = gj("https://api.mexc.com/api/v3/ticker/24hr")
            if tickers:
                all_coins = [t["symbol"] for t in tickers if t.get("symbol", "").endswith("USDT")]
                print(f"[{time.strftime('%H:%M:%S')}] MEXC TARANIYOR | Coin: {len(all_coins)}", flush=True)
                for s in all_coins:
                    try:
                        k = gj(f"https://api.mexc.com/api/v3/klines?symbol={s}&interval=1m&limit=25")
                        if not k or len(k) < 22: continue
                        
                        kapanmis = k[:-1]
                        closes = [float(x[4]) for x in kapanmis]
                        canli = k[-1]
                        
                        acilis = float(canli[1])
                        su_anki = float(canli[4])
                        hacim = float(canli[5])
                        fiyat_degisim = ((su_anki - acilis) / acilis) * 100
                        
                        v_ort = sum(float(x[5]) for x in kapanmis[-20:]) / 20
                        if v_ort == 0: continue
                        hacim_orani = hacim / v_ort
                        
                        if fiyat_degisim >= 3.0 and hacim_orani >= 2.5:
                            if time.time() - son.get(s, 0) >= 1800:
                                ticker = gj(f"https://api.mexc.com/api/v3/ticker/24hr?symbol={s}")
                                if not ticker: continue
                                
                                taker_pct = 50
                                trades = gj(f"https://api.mexc.com/api/v3/trades?symbol={s}&limit=100")
                                if trades and isinstance(trades, list):
                                    buy_vol = sum(float(t["qty"]) for t in trades if t["isBuyerMaker"] == False)
                                    total_vol = sum(float(t["qty"]) for t in trades)
                                    if total_vol > 0: taker_pct = (buy_vol / total_vol) * 100
                                
                                h24 = float(ticker["highPrice"])
                                l24 = float(ticker["lowPrice"])
                                rsi = calculate_rsi(closes)
                                
                                pair = s.replace("USDT", "/USDT")
                                coin = s.replace("USDT", "")
                                hacim_bari = "💰" * int(min(hacim_orani, 10))
                                rsi_emoji = "🔴" if rsi > 70 else "🟢" if rsi < 30 else ""
                                
                                msg = (f"<b>#{s}</b> 💚 <b>Price +{format(fiyat_degisim, '.2f')}% Vol</b>\n"
                                       f"❌ {format(hacim_orani, '.1f')} {hacim_bari} (M5 Spot)\n\n"
                                       f"Total Vol {(hacim, '.0f')} Taker {format(taker_pct, '.0f')}% Rsi/{format(rsi, '.0f')}{rsi_emoji}\n"
                                       f"────────────────\n\n"
                                       f"🏷 Start Price: <code>{format(acilis, '.5f')}</code>\n"
                                       f"🏷 Current Price: <code>{format(su_anki, '.5f')}</code> 🚀 pump\n"
                                       f"🕐 24h: {format(float(ticker['priceChangePercent']), '+.2f')}% Vol {format(float(ticker['quoteVolume'])/1000000, '.2f')}M\n"
                                       f"⬆ High: {format(h24, '.4f')} ({format(((h24 - su_anki)/su_anki)*100, '.2f')}%)\n"
                                       f"⬇ Low: {format(l24, '.4f')} ({format(((su_anki - l24)/l24)*100, '+.2f')}%)\n\n"
                                       f"🔗 <b>MEXC</b>")
                                tg(msg)
                                son[s] = time.time()
                                print(f">>> MEXC SINYAL: {s}", flush=True)
                        time.sleep(0.2)
                    except: pass
            time.sleep(60)
        except Exception as e:
            print("MEXC HATA:", e, flush=True); time.sleep(10)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"Bot is running!")
    def do_HEAD(self):
        self.send_response(200); self.end_headers()

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print("WEB SUNUCUSU BASLADI - PORT:", port, flush=True)
    server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=binance_loop, daemon=True).start()
    threading.Thread(target=mexc_loop, daemon=True).start()
    run_web_server()

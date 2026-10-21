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

def kucoin_loop():
    print("--- KUCOIN MOTORU BASLADI ---", flush=True)
    son = {}
    
    while True:
        try:
            # Tüm KuCoin USDT paritelerini al
            tickers = gj("https://api.kucoin.com/api/v1/market/allTickers")
            if not tickers or tickers.get("code") != "200000":
                print("KuCoin API yanit vermedi, bekleniyor...", flush=True)
                time.sleep(10)
                continue
                
            all_coins = [t["symbol"] for t in tickers["data"]["ticker"] if t["symbol"].endswith("-USDT")]
            print(f"[{time.strftime('%H:%M:%S')}] KUCOIN TARANIYOR | Coin: {len(all_coins)}", flush=True)
            
            for sym in all_coins:
                try:
                    # 1 dakikalık mumlar (Anlık pump yakalamak için)
                    k = gj(f"https://api.kucoin.com/api/v1/market/candles?type=1min&symbol={sym}&limit=25")
                    if not k or k.get("code") != "200000" or len(k["data"]) < 22: 
                        time.sleep(0.15)
                        continue
                    
                    # KuCoin veriyi tersten verir (en yeni ilk sırada)
                    k_list = k["data"]
                    
                    canli = k_list[0]
                    acilis = float(canli[1])
                    su_anki = float(canli[2])
                    hacim = float(canli[5]) # Coin miktarı
                    hacim_usdt = float(canli[6]) # USDT hacmi
                    
                    # Fiyat değişimi
                    fiyat_degisim = ((su_anki - acilis) / acilis) * 100
                    
                    # Hacim Oranı (Son 20 mumun ortalamasına göre)
                    v_ort = sum(float(x[6]) for x in k_list[2:22]) / 20
                    if v_ort == 0: 
                        time.sleep(0.15)
                        continue
                        
                    hacim_orani = hacim_usdt / v_ort
                    
                    # Filtre: 1 dakikada %3+ fiyat ve 2.5x+ hacim
                    if fiyat_degisim >= 3.0 and hacim_orani >= 2.5:
                        if time.time() - son.get(sym, 0) >= 1800: # 30 dk cooldown
                            
                            # --- EK VERILER ---
                            # 24s Yüksek/Düşük
                            t_info = next((t for t in tickers["data"]["ticker"] if t["symbol"] == sym), None)
                            if not t_info: continue
                            
                            h24 = float(t_info["high"])
                            l24 = float(t_info["low"])
                            degisim24 = float(t_info["changeRate"]) * 100
                            
                            # Taker Oranı (Son 100 işlem)
                            trades_data = gj(f"https://api.kucoin.com/api/v1/market/histories?symbol={sym}")
                            taker_pct = 50
                            if trades_data and trades_data.get("code") == "200000":
                                t_list = trades_data["data"][:100]
                                buy_vol = sum(float(t["size"]) for t in t_list if t["side"] == "buy")
                                total_vol = sum(float(t["size"]) for t in t_list)
                                if total_vol > 0: taker_pct = (buy_vol / total_vol) * 100
                            
                            # RSI
                            closes = [float(x[2]) for x in k_list[1:]]
                            rsi_val = calculate_rsi(closes)
                            
                            # --- MESAJ FORMATI (Görseldeki gibi) ---
                            coin = sym.replace("-USDT", "")
                            pair = sym.replace("-", "/")
                            hacim_bari = "💰" * int(min(hacim_orani, 10))
                            rsi_emoji = "🔴" if rsi_val > 70 else "🟢" if rsi_val < 30 else ""
                            
                            msg = (f"<b>#{sym}</b> 💚 <b>Price +{format(fiyat_degisim, '.2f')}% Vol</b>\n"
                                   f"❌ {format(hacim_orani, '.1f')} {hacim_bari} (M5 Spot)\n\n"
                                   f"Total Vol {format(hacim, '.0f')} Taker {format(taker_pct, '.0f')}% Rsi/{format(rsi_val, '.0f')}{rsi_emoji}\n"
                                   f"────────────────\n\n"
                                   f"🏷 Start Price: <code>{format(acilis, '.5f')}</code>\n"
                                   f"🏷 Current Price: <code>{format(su_anki, '.5f')}</code> 🚀 pump\n"
                                   f"🕐 24h: {format(degisim24, '+.2f')}% Vol {format(float(t_info['volValue'])/1000000, '.2f')}M\n"
                                   f"⬆ High: {format(h24, '.4f')} ({format(((h24 - su_anki)/su_anki)*100, '.2f')}%)\n"
                                   f"⬇ Low: {format(l24, '.4f')} ({format(((su_anki - l24)/l24)*100, '+.2f')}%)\n\n"
                                   f"🔗 <a href='https://www.kucoin.com/trade/{sym}'>Pair on KuCoin.</a>")
                            tg(msg)
                            son[sym] = time.time()
                            print(f">>> KUCOIN SINYAL: {sym}", flush=True)
                            
                    time.sleep(0.15) # KuCoin API'sini yormamak icin bekle
                except:
                    pass
            
            print(f"[{time.strftime('%H:%M:%S')}] KUCOIN TARAMASI BITTI", flush=True)
            time.sleep(60) # 1 dakika bekle ve tekrar tara
            
        except Exception as e:
            print("KUCOIN HATA:", e, flush=True)
            time.sleep(10)

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
    threading.Thread(target=kucoin_loop, daemon=True).start()
    run_web_server()

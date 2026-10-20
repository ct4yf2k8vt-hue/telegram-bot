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

def bot_loop():
    print("--- 1. BOT LOOP BASLADI (GELISMIS FILTRE) ---", flush=True)
    son = {}

    while True:
        try:
            # ==========================================
            # 1. BINANCE FUTURES (GERÇEK ZAMANLI)
            # ==========================================
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if tickers:
                all_coins = [t["symbol"] for t in tickers if t["symbol"].endswith("USDT")]
                print("--- 2. BINANCE TARANIYOR:", time.strftime("%H:%M:%S"), "| Coin:", len(all_coins), "---", flush=True)
                
                for s in all_coins:
                    try:
                        # 5 dakikalık mumlar (Son 3 mumu al)
                        k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=5m&limit=25")
                        if not k or len(k) < 22: continue
                        
                        # Kapanmış mumlar
                        m1 = k[-3] # 15 dk önce
                        m2 = k[-2] # 10 dk önce
                        m3 = k[-1] # 5 dk önce (CANLI)
                        
                        acilis = float(m1[1])
                        su_anki = float(m3[4])
                        hacim = float(m3[7])
                        
                        # 1. FİLTRE: Fiyat artışı (En az %3)
                        fiyat_degisim = ((su_anki - acilis) / acilis) * 100
                        if fiyat_degisim < 3.0: continue
                        
                        # 2. FİLTRE: Hacim patlaması (Ortalamanın en az 3 katı)
                        onceki_20 = [float(x[7]) for x in k[-22:-2]]
                        v_ort = sum(onceki_20) / 20
                        if v_ort == 0: continue
                        hacim_orani = hacim / v_ort
                        if hacim_orani < 3.0: continue
                        
                        # 3. FİLTRE: Ardışık Yeşil Mum (En az 2 yeşil mum)
                        yesil = 0
                        for m in [m2, m3]:
                            if float(m[4]) > float(m[1]): yesil += 1
                        if yesil < 2: continue
                        
                        # 4. FİLTRE: Alım Baskısı (Taker Buy > %60)
                        taker_buy = float(m3[9])
                        if hacim > 0:
                            taker_oran = (taker_buy / hacim) * 100
                            if taker_oran < 60: continue
                        else: continue

                        # TÜM FİLTRELER GEÇTİYSE SİNYAL GÖNDER
                        if time.time() - son.get(s, 0) >= 1800:
                            pair = s.replace("USDT", "/USDT")
                            coin = s.replace("USDT", "")
                            hacim_artis = (hacim_orani - 1) * 100
                            
                            msg = (f"🚀 <b>Pump - {pair} [Binance]</b>\n"
                                   f"💰 Price: <code>{format(acilis, '.4f')}</code> ➜ <code>{format(su_anki, '.4f')}</code> (+{format(fiyat_degisim, '.1f')}%)\n"
                                   f"📊 Volume: {format(hacim, '.0f')} (+{format(hacim_artis, '.0f')} %)\n"
                                   f"Volume increased by {format(hacim - v_ort, '.0f')} ⬆️\n"
                                   f"Time: {time.strftime('%I:%M:%S %p', time.localtime())} 🕒\n\n"
                                   f"🔗 <a href='https://www.binance.com/en/futures/{s}'>Pair on Binance.</a>\n\n"
                                   f"${coin} #{coin}")
                            tg(msg)
                            son[s] = time.time()
                            print(">>> BINANCE SINYAL:", s, flush=True)
                            
                        time.sleep(0.05)
                    except:
                        pass

            # ==========================================
            # 2. KUCOIN SPOT (GERÇEK ZAMANLI)
            # ==========================================
            kc_tickers = gj("https://api.kucoin.com/api/v1/market/allTickers")
            if kc_tickers and kc_tickers.get("code") == "200000":
                kc_coins = [t for t in kc_tickers.get("data", {}).get("ticker", []) if t.get("symbol", "").endswith("-USDT")]
                print("--- 3. KUCOIN TARANIYOR:", time.strftime("%H:%M:%S"), "| Coin:", len(kc_coins), "---", flush=True)
                
                for t in kc_coins:
                    try:
                        sym = t["symbol"]
                        k_kc = gj(f"https://api.kucoin.com/api/v1/market/candles?type=5min&symbol={sym}&limit=25")
                        if not k_kc or k_kc.get("code") != "200000": continue
                        
                        k_list = k_kc["data"] # En yeni ilk sırada
                        if len(k_list) < 22: continue
                        
                        # KuCoin verisi tersten gelir: [0] en son, [1] bir önceki
                        m1 = k_list[2] # 15 dk önce
                        m3 = k_list[0] # 5 dk önce (CANLI)
                        
                        acilis = float(m1[1])
                        su_anki = float(m3[2])
                        hacim = float(m3[6])
                        
                        fiyat_degisim = ((su_anki - acilis) / acilis) * 100
                        if fiyat_degisim < 3.0: continue
                        
                        onceki_20 = [float(x[6]) for x in k_list[3:23]]
                        v_ort = sum(onceki_20) / 20
                        if v_ort == 0: continue
                        hacim_orani = hacim / v_ort
                        if hacim_orani < 3.0: continue
                        
                        # KuCoin için ardışık yeşil mum ve taker buy kontrolü
                        yesil = 0
                        for m in [k_list[1], k_list[0]]:
                            if float(m[2]) > float(m[1]): yesil += 1
                        if yesil < 2: continue
                        
                        # KuCoin'de taker buy verisi kline'da yok, bu yüzden bu filtreyi atlıyoruz.

                        if time.time() - son.get("KC_" + sym, 0) >= 1800:
                            pair = sym.replace("-", "/")
                            coin = sym.replace("-USDT", "")
                            hacim_artis = (hacim_orani - 1) * 100
                            
                            msg = (f"🚀 <b>Pump - {pair} [KuCoin]</b>\n"
                                   f"💰 Price: <code>{format(acilis, '.4f')}</code> ➜ <code>{format(su_anki, '.4f')}</code> (+{format(fiyat_degisim, '.1f')}%)\n"
                                   f"📊 Volume: {format(hacim, '.0f')} (+{format(hacim_artis, '.0f')} %)\n"
                                   f"Volume increased by {format(hacim - v_ort, '.0f')} ⬆️\n"
                                   f"Time: {time.strftime('%I:%M:%S %p', time.localtime())} 🕒\n\n"
                                   f"🔗 <a href='https://www.kucoin.com/trade/{sym}'>Pair on KuCoin.</a>\n\n"
                                   f"${coin} #{coin}")
                            tg(msg)
                            son["KC_" + sym] = time.time()
                            print(">>> KUCOIN SINYAL:", sym, flush=True)
                            
                        time.sleep(0.05)
                    except:
                        pass

            print("--- 4. TARAMA BITTI ---", flush=True)
            time.sleep(120) # 2 dakika bekle
        except Exception as e:
            print("--- LOOP HATASI:", e, "---", flush=True)
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

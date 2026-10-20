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
    print("--- 1. BOT LOOP BASLADI (GERCEK ZAMANLI) ---", flush=True)
    son = {}

    while True:
        try:
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if not tickers: 
                time.sleep(10)
                continue
                
            all_coins = [t["symbol"] for t in tickers if t["symbol"].endswith("USDT")]
            print("--- 2. TARAMA BASLIYOR:", time.strftime("%H:%M:%S"), "| Coin:", len(all_coins), "---", flush=True)
            sayac = 0
            
            for s in all_coins:
                try:
                    # 5 DAKİKALIK mumlara bakıyoruz
                    k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=5m&limit=25")
                    if not k or len(k) < 22: continue
                    
                    # DİKKAT: k[-1] henüz kapanmamış, ŞU ANKİ CANLI MUM!
                    canli_mum = k[-1]
                    onceki_mum = k[-2]
                    
                    acilis = float(canli_mum[1])       # 5 dk önceki açılış
                    su_anki_fiyat = float(canli_mum[4]) # Şu anki fiyat
                    canli_hacim = float(canli_mum[7])  # Şu ana kadar yenen hacim
                    
                    # Fiyat Değişimi (Açılıştan şu ana kadar)
                    fiyat_degisim = ((su_anki_fiyat - acilis) / acilis) * 100
                    
                    # Önceki 20 kapanmış mumun ortalama hacmi
                    onceki_20 = [float(x[7]) for x in k[-21:-1]]
                    v_ort = sum(onceki_20) / 20
                    if v_ort == 0: continue
                    
                    # Canlı Hacim Oranı (Şu ana kadar olan hacim / ortalama)
                    hacim_orani = canli_hacim / v_ort
                    hacim_artis_pct = (hacim_orani - 1) * 100
                    
                    # KOSUL: Canlı hacim 2x (veya daha fazla) VE fiyat %3+ artmışsa (Anlık yakalar)
                    if hacim_orani >= 2.0 and fiyat_degisim >= 3.0:
                        if time.time() - son.get(s, 0) >= 1800: # 30 dk cooldown
                            
                            pair = s.replace("USDT", "/USDT")
                            coin = s.replace("USDT", "")
                            
                            msg = (f"🚀 <b>Pump - {pair} [Binance]</b>\n"
                                   f"💰 Price: <code>{format(acilis, '.4f')}</code> ➜ <code>{format(su_anki_fiyat, '.4f')}</code> (+{format(fiyat_degisim, '.1f')}%)\n"
                                   f"📊 Volume: {format(canli_hacim, '.0f')} (+{format(hacim_artis_pct, '.0f')} %)\n"
                                   f"Volume increased by {format(canli_hacim - v_ort, '.0f')} ⬆️\n"
                                   f"Time: {time.strftime('%I:%M:%S %p', time.localtime())} 🕒\n\n"
                                   f"🔗 <a href='https://www.binance.com/en/futures/{s}'>Pair on Binance.</a>\n\n"
                                   f"${coin} #{coin}")
                            
                            tg(msg)
                            son[s] = time.time()
                            sayac += 1
                            print(">>> SINYAL GONDERILDI:", s, flush=True)
                            
                    time.sleep(0.05) # API Ban Korumasi
                except:
                    pass
                    
            print("--- 3. TARAMA BITTI - Sinyal:", sayac, "---", flush=True)
            time.sleep(120) # 2 dakika bekle (Daha sık kontrol)
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

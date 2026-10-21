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
    print("--- PROFESYONEL SHORT BOTU BASLADI ---", flush=True)
    son_sinyal = {}

    while True:
        try:
            print(f"[{tr_saat()}] TARAMA BASLIYOR...", flush=True)
            
            # 1. ÖN FİLTRE: Son 24 saatte %10+ artmış coinleri bul
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if not tickers: 
                time.sleep(10)
                continue
                
            hedef_coinler = []
            for t in tickers:
                if t["symbol"].endswith("USDT"):
                    if float(t["priceChangePercent"]) >= 10.0:
                        hedef_coinler.append(t["symbol"])

            print(f"[{tr_saat()}] Potansiyel Coin: {len(hedef_coinler)}", flush=True)
            sayac = 0

            # 2. DETAYLI ANALİZ
            for s in hedef_coinler:
                try:
                    # 15 Dakikalık mumlar
                    k = gj(f"https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=15m&limit=30")
                    if not k or len(k) < 25: continue
                    
                    kapanmis = k[:-1]
                    closes = [float(x[4]) for x in kapanmis]
                    highs = [float(x[2]) for x in kapanmis]
                    
                    canli_mum = k[-1]
                    acilis = float(canli_mum[1])
                    su_anki = float(canli_mum[4])
                    hacim = float(canli_mum[7])
                    zirve = float(canli_mum[2]) # Anlık en yüksek

                    # ŞART 1: RSI > 70 (Aşırı Alım)
                    rsi_deger = calculate_rsi(closes)
                    if rsi_deger < 70: continue

                    # ŞART 2: Olağandışı Hacim (Ortalamanın 2.5 katı)
                    v_ort = sum(float(x[7]) for x in kapanmis[-20:]) / 20
                    if v_ort == 0: continue
                    hacim_orani = hacim / v_ort
                    if hacim_orani < 2.5: continue

                    # ŞART 3: Dönüş İşareti (Anlık mum kırmızıya dönmüş VE zirveden düşmüş)
                    if su_anki > acilis: continue # Mum hala yeşilse bekle
                    if (zirve - su_anki) < (zirve * 0.005): continue # Zirveden en az %0.5 düşmemişse bekle

                    # ŞART 4: Futures Verisi (Funding Rate Aşırı Pozitif mi?)
                    funding_data = gj(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={s}")
                    funding_rate = float(funding_data["lastFundingRate"]) * 100 if funding_data else 0
                    # Funding aşırı pozitif değilse (%0.01'den küçükse) shortlamak riskli olabilir
                    if funding_rate < 0.01: continue

                    # 3. STOP LOSS VE TAKE PROFIT HESABI (Risk / Ödül)
                    giris = su_anki
                    stop_loss = zirve * 1.005 # Zirvenin %0.5 üstü (Direnç kırılırsa çık)
                    risk = stop_loss - giris
                    
                    if risk <= 0: continue
                    
                    # Hedef: 1.5 Kat Risk (R:R = 1:1.5)
                    take_profit = giris - (risk * 1.5)
                    
                    risk_pct = (risk / giris) * 100
                    tp_pct = ((giris - take_profit) / giris) * 100

                    # Cooldown kontrolü
                    if time.time() - son_sinyal.get(s, 0) >= 3600: # 1 saat
                        
                        # GÖRSELDEKİ GİBİ SHORT FORMATI
                        coin = s.replace("USDT", "")
                        
                        msg = (f"🔴 <b>SHORT SINYALI - {coin}</b>\n\n"
                               f"🚨 <b>Neden Short?</b>\n"
                               f"📈 Fiyat son 24s'de +%{format(float(next((t['priceChangePercent'] for t in tickers if t['symbol']==s), 0)), '.1f')}\n"
                               f"🔥 RSI: {format(rsi_deger, '.1f')} (Aşırı Alım)\n"
                               f"📊 Hacim: {format(hacim_orani, '.1f')}x (Patlama)\n"
                               f"💰 Funding: %{format(funding_rate, '.3f')} (Long Sıkışması)\n"
                               f"🔄 Dönüş: Zirveden reddedilme\n\n"
                               f"🎯 <b>İşlem Planı:</b>\n"
                               f"Entry: <code>{format(giris, '.5f')}</code>\n"
                               f"Stop Loss: <code>{format(stop_loss, '.5f')}</code> (+%{format(risk_pct, '.2f')})\n"
                               f"Take Profit: <code>{format(take_profit, '.5f')}</code> (-%{format(tp_pct, '.2f')})\n"
                               f"⚖️ R:R Oranı: 1:1.5\n\n"
                               f"🕒 {tr_saat()}\n"
                               f"🔗 <a href='https://www.binance.com/en/futures/{s}'>Pair on Binance.</a>")
                        
                        tg(msg)
                        son_sinyal[s] = time.time()
                        sayac += 1
                        print(f">>> SHORT SINYALI GONDERILDI: {s}", flush=True)

                    time.sleep(0.1)
                except:
                    pass
            
            print(f"[{tr_saat()}] TARAMA BITTI - Sinyal: {sayac}", flush=True)
            time.sleep(60) # 1 dakika bekle
            
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

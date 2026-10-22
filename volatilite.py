import urllib.request, urllib.parse, json, time
import os
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

T = os.environ.get("BOT_TOKEN")
C = "1623907197"

# Türkiye Saati
def tr_saat():
    return (datetime.utcnow() + timedelta(hours=3)).strftime('%H:%M:%S')

def tg(m):
    try:
        d = urllib.parse.urlencode({"chat_id": C, "text": m, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
        urllib.request.urlopen("https://api.telegram.org/bot" + T + "/sendMessage", d, timeout=10).read()
    except Exception as e:
        print("TELEGRAM HATASI:", e, flush=True)

def gj(u):
    try:
        req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'})
        return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 418:
            print("BINANCE API BANI (418): 60 saniye bekleniyor...", flush=True)
            time.sleep(60) # Ban yediğinde 60 saniye bekle
            return None
        else:
            print(f"API HATASI ({u[:40]}...): {e}", flush=True)
            return None
    except Exception as e:
        print(f"API HATASI ({u[:40]}...): {e}", flush=True)
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
    print("--- DUZELTILMIS BOT BASLADI (BAN KORUMALI) ---", flush=True)
    son_long = {}
    son_short = {}

    while True:
        try:
            print(f"[{tr_saat()}] TARAMA BASLIYOR...", flush=True)
            
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if not tickers: 
                print("Ticker verisi alinamadi, 10 sn bekleniyor...", flush=True)
                time.sleep(10)
                continue
                
            # Havuzu geniş tutuyoruz (Hareket eden potansiyel coinler)
            hedef_coinler = []
            for t in tickers:
                if t["symbol"].endswith("USDT"):
                    if abs(float(t["priceChangePercent"])) >= 2.0:
                        hedef_coinler.append(t["symbol"])

            print(f"[{tr_saat()}] Potansiyel Coin: {len(hedef_coinler)}", flush=True)
            long_sayac = 0
            short_sayac = 0

            for s in hedef_coinler:
                try:
                    # 5 DAKİKALIK mumlar
                    k = gj(f"https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=5m&limit=30")
                    if not k or len(k) < 25: 
                        time.sleep(0.2)
                        continue
                    
                    kapanmis = k[:-1]
                    closes = [float(x[4]) for x in kapanmis]
                    
                    canli_mum = k[-1]
                    acilis = float(canli_mum[1])
                    su_anki = float(canli_mum[4])
                    zirve = float(canli_mum[2])
                    hacim = float(canli_mum[7])

                    v_ort = sum(float(x[7]) for x in kapanmis[-20:]) / 20
                    if v_ort == 0: 
                        time.sleep(0.2)
                        continue
                    hacim_orani = hacim / v_ort

                    # --- 1. AGRESIF LONG (PUMP) KONTROLÜ ---
                    fiyat_degisim = ((su_anki - acilis) / acilis) * 100
                    if fiyat_degisim >= 2.0 and hacim_orani >= 2.0:
                        if time.time() - son_long.get(s, 0) >= 300: # 5 DAKİKA COOLDOWN
                            t_info = next((t for t in tickers if t["symbol"] == s), None)
                            if t_info:
                                h24 = float(t_info["highPrice"])
                                l24 = float(t_info["lowPrice"])
                                degisim24 = float(t_info["priceChangePercent"])
                                hacim24 = float(t_info["quoteVolume"])
                                
                                taker_pct = 50
                                taker = gj(f"https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={s}&period=5m&limit=1")
                                if taker and len(taker) > 0: 
                                    try: taker_pct = float(taker[0]["buySellRatio"]) * 100
                                    except: pass

                                rsi_deger = calculate_rsi(closes)
                                
                                coin = s.replace("USDT", "")
                                hacim_bari = "💰" * int(min(hacim_orani, 10))
                                rsi_emoji = "🔴" if rsi_deger > 70 else "🟢" if rsi_deger < 30 else ""
                                
                                msg_long = (f"<b>#{s}</b> 💚 <b>Price +{format(fiyat_degisim, '.2f')}% Vol</b>\n"
                                            f"❌ {format(hacim_orani, '.1f')} {hacim_bari} (M5 Futures)\n\n"
                                            f"Total Vol {format(hacim, '.0f')} Taker {format(taker_pct, '.0f')}% Rsi/{format(rsi_deger, '.0f')}{rsi_emoji}\n"
                                            f"────────────────\n\n"
                                            f"🏷 Price: {format(su_anki, '.4f')} 🚀 pump ···\n"
                                            f"🕐 24h: {format(degisim24, '+.2f')}% Vol {format(hacim24/1000000, '.2f')}M\n"
                                            f"⬆ High: {format(h24, '.4f')} ({format(((h24 - su_anki)/su_anki)*100, '.2f')}%)\n"
                                            f"⬇ Low: {format(l24, '.4f')} ({format(((su_anki - l24)/l24)*100, '+.2f')}%)\n\n"
                                            f"🔗 <a href='https://www.binance.com/en/futures/{s}'>Pair on Binance.</a>\n"
                                            f"🕒 {tr_saat()}")
                                tg(msg_long)
                                son_long[s] = time.time()
                                long_sayac += 1
                                print(f">>> LONG SINYAL GONDERILDI: {s}", flush=True)

                    # --- 2. AGRESIF SHORT (REVERSAL) KONTROLÜ ---
                    rsi_deger = calculate_rsi(closes)
                    if rsi_deger >= 65 and hacim_orani >= 2.0 and su_anki < acilis:
                        if (zirve - su_anki) >= (zirve * 0.003): 
                            funding_data = gj(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={s}")
                            funding_rate = float(funding_data["lastFundingRate"]) * 100 if funding_data else 0
                            
                            if funding_rate >= 0.005: 
                                if time.time() - son_short.get(s, 0) >= 300: # 5 DAKİKA COOLDOWN
                                    
                                    giris = su_anki
                                    stop_loss = zirve * 1.005
                                    risk = stop_loss - giris
                                    if risk > 0:
                                        take_profit = giris - (risk * 1.5)
                                        risk_pct = (risk / giris) * 100
                                        tp_pct = ((giris - take_profit) / giris) * 100
                                        coin = s.replace("USDT", "")
                                        
                                        msg_short = (f"🔴 <b>SHORT REVERSAL - {coin}</b>\n\n"
                                                     f"🚨 <b>Neden Short?</b>\n"
                                                     f"🔥 RSI: {format(rsi_deger, '.1f')} (Aşırı Alım)\n"
                                                     f"📊 Hacim: {format(hacim_orani, '.1f')}x (Patlama)\n"
                                                     f"💰 Funding: %{format(funding_rate, '.3f')}\n"
                                                     f"🔄 Dönüş: Zirveden reddedilme\n\n"
                                                     f"🎯 <b>İşlem Planı:</b>\n"
                                                     f"Entry: <code>{format(giris, '.5f')}</code>\n"
                                                     f"Stop Loss: <code>{format(stop_loss, '.5f')}</code> (+%{format(risk_pct, '.2f')})\n"
                                                     f"Take Profit: <code>{format(take_profit, '.5f')}</code> (-%{format(tp_pct, '.2f')})\n"
                                                     f"⚖️ R:R Oranı: 1:1.5\n\n"
                                                     f"🕒 {tr_saat()}\n"
                                                     f"🔗 <a href='https://www.binance.com/en/futures/{s}'>Pair on Binance.</a>")
                                        tg(msg_short)
                                        son_short[s] = time.time()
                                        short_sayac += 1
                                        print(f">>> SHORT SINYAL GONDERILDI: {s}", flush=True)

                    # API BAN YEMEMEK ICIN HER COIN ARASINDA 0.2 SANIYE BEKLE
                    time.sleep(0.2)
                except Exception as e:
                    print(f"Coin Analiz Hatasi ({s}): {e}", flush=True)
                    time.sleep(0.2)
            
            print(f"[{tr_saat()}] TARAMA BITTI | Long: {long_sayac} | Short: {short_sayac}", flush=True)
            time.sleep(60)
            
        except Exception as e:
            print("ANA DONGU HATASI:", e, flush=True)
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

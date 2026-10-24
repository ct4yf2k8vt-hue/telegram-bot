import urllib.request, urllib.parse, json, time, socket
import os
from datetime import datetime, timedelta, timezone
import http.server
import socketserver
import threading

socket.setdefaulttimeout(10)
T = os.environ.get("BOT_TOKEN")
C = "1623907197"
TR_Saati = timezone(timedelta(hours=3))

son_sinyaller = {}
COOLDOWN = 900

# --- RİSK YÖNETİMİ PARAMETRELERİ ---
TAKE_PROFIT_PCT = 3.0   # %3 Kâr Al
STOP_LOSS_PCT = 1.5     # %1.5 Zarar Durdur
RISK_REWARD_RATIO = TAKE_PROFIT_PCT / STOP_LOSS_PCT # 2.0 R/R Oranı

def tg(m):
    try:
        d = urllib.parse.urlencode({"chat_id": C, "text": m, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
        req = urllib.request.Request("https://api.telegram.org/bot" + T + "/sendMessage", data=d)
        urllib.request.urlopen(req).read()
    except:
        pass

def gj(u):
    try:
        req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'})
        return json.loads(urllib.request.urlopen(req).read().decode())
    except:
        return None

def format_number(num):
    if abs(num) >= 1_000_000_000: return f"{num/1_000_000_000:.2f}B"
    elif abs(num) >= 1_000_000: return f"{num/1_000_000:.2f}M"
    elif abs(num) >= 1_000: return f"{num/1_000:.2f}K"
    else: return f"{num:.2f}"

def bot_loop():
    tg(f"🚀 <b>Kâr Odaklı Bot Başladı!</b>\nRisk/Ödül Oranı: 1:{RISK_REWARD_RATIO:.1f}\nHedef: %{TAKE_PROFIT_PCT} Kâr / %{STOP_LOSS_PCT} Zarar")
    
    while True:
        try:
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if not tickers:
                time.sleep(5)
                continue

            adaylar = []
            for t in tickers:
                if t['symbol'].endswith('USDT'):
                    try:
                        vol = float(t['quoteVolume'])
                        chg = float(t['priceChangePercent'])
                        if vol > 20_000_000 and abs(chg) >= 4.0:
                            adaylar.append((t['symbol'], chg))
                    except:
                        pass
            
            for symbol, chg in adaylar:
                if symbol in son_sinyaller and (time.time() - son_sinyaller[symbol] < COOLDOWN):
                    continue

                klines = gj(f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=15m&limit=3")
                if not klines or len(klines) < 3: continue
                
                curr_k, prev_k = klines[-2], klines[-3]
                curr_close, prev_close = float(curr_k[4]), float(prev_k[4])
                curr_vol, prev_vol = float(curr_k[7]), float(prev_k[7])
                
                if prev_close == 0 or prev_vol == 0: continue
                
                price_change_15m = ((curr_close - prev_close) / prev_close) * 100
                
                if abs(price_change_15m) < 3.0: continue 
                
                # --- RİSK YÖNETİMLİ SİNYAL ---
                is_dump = price_change_15m < 0
                action = "SHORT" if is_dump else "LONG"
                emoji = "📉" if is_dump else "📈"
                
                entry_price = curr_close
                if action == "LONG":
                    tp_price = entry_price * (1 + TAKE_PROFIT_PCT / 100)
                    sl_price = entry_price * (1 - STOP_LOSS_PCT / 100)
                else:
                    tp_price = entry_price * (1 - TAKE_PROFIT_PCT / 100)
                    sl_price = entry_price * (1 + STOP_LOSS_PCT / 100)
                
                msg = (f"{emoji} <b>{action} SİNYALİ: {symbol}</b>\n"
                       f"💰 <b>Giriş Fiyatı:</b> {format(entry_price, '.4f')} USDT\n"
                       f"🎯 <b>Kâr Al (TP):</b> {format(tp_price, '.4f')} (+%{TAKE_PROFIT_PCT})\n"
                       f"🛑 <b>Zarar Durdur (SL):</b> {format(sl_price, '.4f')} (-%{STOP_LOSS_PCT})\n"
                       f"⚖️ <b>Risk/Ödül:</b> 1:{RISK_REWARD_RATIO:.1f}\n\n"
                       f"<i>Not: Bu bir yatırım tavsiyesi değildir. Kaldıraç kullanmayın.</i>\n"
                       f"🔗 <a href='https://www.binance.com/en/futures/{symbol}'>Grafiği Aç</a>")
                
                tg(msg)
                son_sinyaller[symbol] = time.time()
                time.sleep(1) 

            time.sleep(15)

        except Exception as e:
            time.sleep(10)

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), Handler) as httpd:
        httpd.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    bot_loop()

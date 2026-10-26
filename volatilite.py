import urllib.request, urllib.parse, json, time, socket
import os
from datetime import datetime, timedelta, timezone
import threading
import http.server
import socketserver

socket.setdefaulttimeout(10)
T = os.environ.get("BOT_TOKEN")
C = os.environ.get("CHAT_ID", "1623907197")
TR_Saati = timezone(timedelta(hours=3))

son_sinyaller = {}
COOLDOWN = 900
last_update_id = 0

def tg(chat_id, m):
    try:
        d = urllib.parse.urlencode({"chat_id": chat_id, "text": m, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
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

def format_vol(v):
    if v >= 1_000_000: return f"{v/1_000_000:.2f}M"
    elif v >= 1_000: return f"{v/1_000:.2f}K"
    return f"{v:.2f}"

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

def command_listener():
    global last_update_id
    while True:
        try:
            updates = gj(f"https://api.telegram.org/bot{T}/getUpdates?offset={last_update_id + 1}&timeout=10")
            if updates and updates.get("ok"):
                for u in updates["result"]:
                    last_update_id = u["update_id"]
                    if "message" in u and "text" in u["message"]:
                        text = u["message"]["text"]
                        chat_id = u["message"]["chat"]["id"]
                        if text.startswith("/ratio"):
                            parts = text.split()
                            if len(parts) < 2:
                                tg(chat_id, "⚠️ Kullanım: /ratio btcusdt")
                                continue
                            symbol = parts[1].upper()
                            if not symbol.endswith("USDT"): symbol += "USDT"
                            period = parts[2].lower() if len(parts) > 2 else "1h"
                            url = f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period={period}&limit=1"
                            data = gj(url)
                            if data and len(data) > 0:
                                d = data[0]
                                msg = (f"Long/Short Ratio📊\nBinance - {symbol}\n⏳ <b>Timeframe:</b> {period}\n"
                                       f"📈 <b>Long:</b> {float(d['longAccount'])*100:.2f}%\n"
                                       f"📉 <b>Short:</b> {float(d['shortAccount'])*100:.2f}%\n"
                                       f"⚖️ <b>Ratio:</b> {float(d['longShortRatio']):.2f}\n\n"
                                       f"<i>Last Updated: {datetime.now(TR_Saati).strftime('%H:%M')} (TR)</i>")
                                tg(chat_id, msg)
                            else:
                                tg(chat_id, f"❌ {symbol} için veri bulunamadı.")
        except:
            pass
        time.sleep(2)

def bot_loop():
    tg(C, "🚀 <b>AzCrypto Bot (Hacim Patlaması) Başlatıldı!</b>\nSadece Binance Spot taranıyor.")
    
    while True:
        try:
            tickers = gj("https://api.binance.com/api/v3/ticker/24hr")
            if not tickers:
                time.sleep(5)
                continue

            adaylar = []
            for t in tickers:
                if t['symbol'].endswith('USDT'):
                    try:
                        vol = float(t['quoteVolume'])
                        chg = float(t['priceChangePercent'])
                        if vol > 5_000_000:
                            adaylar.append((t['symbol'], chg, vol))
                    except:
                        pass

            for symbol, chg, vol in adaylar:
                if symbol in son_sinyaller and (time.time() - son_sinyaller[symbol] < COOLDOWN):
                    continue

                klines = gj(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=5m&limit=22")
                if not klines or len(klines) < 22: continue

                curr_close = float(klines[-1][4])
                curr_vol = float(klines[-1][7])
                prev_close = float(klines[-2][4])
                closes = [float(k[4]) for k in klines[:-1]]
                
                avg_vol = sum(float(k[7]) for k in klines[:-1]) / 20
                if avg_vol == 0: continue
                vol_ratio = curr_vol / avg_vol
                price_change_5m = ((curr_close - prev_close) / prev_close) * 100

                if price_change_5m >= 3.0 and vol_ratio >= 3.0:
                    
                    # Taker Oranı
                    taker = gj(f"https://api.binance.com/api/v3/trades?symbol={symbol}&limit=500")
                    buy_vol = sum(float(t['qty']) for t in taker if t['isBuyerMaker'] == False) if taker else 0
                    total_vol = sum(float(t['qty']) for t in taker) if taker else 1
                    taker_pct = (buy_vol / total_vol) * 100 if total_vol > 0 else 50
                    
                    # RSI
                    rsi = calculate_rsi(closes, 14)
                    rsi_emoji = "🔴" if rsi > 70 else "🟢" if rsi < 30 else ""
                    
                    # 24s Yüksek/Düşük
                    high_24 = float(tickers[0]['highPrice']) if symbol == tickers[0]['symbol'] else 0
                    low_24 = float(tickers[0]['lowPrice']) if symbol == tickers[0]['symbol'] else 0
                    # Ticker içinden doğru olanı bul
                    t_info = next((t for t in tickers if t['symbol'] == symbol), None)
                    if t_info:
                        high_24 = float(t_info['highPrice'])
                        low_24 = float(t_info['lowPrice'])

                    dist_high = ((high_24 - curr_close) / curr_close) * 100
                    dist_low = ((curr_close - low_24) / low_24) * 100

                    msg = (f"#<b>{symbol}</b> 💚 <b>Price {price_change_5m:+.2f}% Vol ❌ {vol_ratio:.1f}</b> 💰\n"
                           f"(M5 Spot)\n\n"
                           f"Total Vol {format_vol(curr_vol)} Taker {taker_pct:.0f}% Rsi/{rsi:.0f}{rsi_emoji}\n"
                           f"────────────────\n"
                           f"🏷 Price: {curr_close:.4f} ...\n"
                           f"🕒 24h: {chg:+.2f}% Vol {format_vol(vol)}\n"
                           f"⬆ High: {high_24:.4f} ({dist_high:+.2f}%)\n"
                           f"⬇ Low: {low_24:.4f} ({dist_low:+.2f}%)")
                    
                    tg(C, msg)
                    son_sinyaller[symbol] = time.time()
                    time.sleep(1)

            time.sleep(10)
        except Exception as e:
            time.sleep(10)

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), Handler) as httpd:
        httpd.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    threading.Thread(target=command_listener, daemon=True).start()
    bot_loop()

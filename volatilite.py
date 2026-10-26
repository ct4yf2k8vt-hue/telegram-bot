import urllib.request, urllib.parse, json, time, socket
import os
from datetime import datetime, timedelta, timezone
import threading
import http.server
import socketserver

# Ağ kilitlenmelerini önlemek için istek zaman aşımı (10 saniye)
socket.setdefaulttimeout(10)

T = os.environ.get("BOT_TOKEN")
C = os.environ.get("CHAT_ID", "1623907197")
TR_Saati = timezone(timedelta(hours=3))

son_sinyaller = {}
COOLDOWN = 900 # 15 dakika
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

# --- TELEGRAM KOMUT DİNLEYİCİSİ ---
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

# --- OTOMATİK TARAMA MOTORU (BINANCE FUTURES) ---
def bot_loop():
    tg(C, "🚀 <b>AzCrypto Futures Bot Başlatıldı!</b>\nSadece Binance Futures taranıyor.")
    
    while True:
        try:
            # Binance FUTURES piyasa özeti
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if not tickers:
                time.sleep(5)
                continue

            adaylar = []
            for t in tickers:
                if t['symbol'].endswith('USDT'):
                    try:
                        vol = float(t['quoteVolume'])
                        if vol > 10_000_000: # 10 Milyon Dolar üzeri likit Futures coinleri
                            adaylar.append((t['symbol'], t['priceChangePercent'], vol, t['highPrice'], t['lowPrice']))
                    except:
                        pass

            for symbol, chg, vol, high_24, low_24 in adaylar:
                if symbol in son_sinyaller and (time.time() - son_sinyaller[symbol] < COOLDOWN):
                    continue

                # 5 dakikalık Futures mum verisi
                klines = gj(f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=5m&limit=22")
                if not klines or len(klines) < 22: continue

                curr_close = float(klines[-1][4])
                curr_vol = float(klines[-1][7])
                prev_close = float(klines[-2][4])
                closes = [float(k[4]) for k in klines[:-1]]
                
                avg_vol = sum(float(k[7]) for k in klines[:-1]) / 20
                if avg_vol == 0: continue
                
                vol_ratio = curr_vol / avg_vol
                price_change_5m = ((curr_close - prev_close) / prev_close) * 100

                # Sinyal Şartı: Fiyat %3+ ve Hacim ortalamanın 3 katı+
                if price_change_5m >= 3.0 and vol_ratio >= 3.0:
                    
                    # Futures Taker Oranı
                    taker = gj(f"https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={symbol}&period=5m&limit=1")
                    taker_pct = 50
                    if taker and len(taker) > 0:
                        buy_vol = float(taker[0]['buyVol'])
                        sell_vol = float(taker[0]['sellVol'])
                        total = buy_vol + sell_vol
                        if total > 0:
                            taker_pct = (buy_vol / total) * 100
                    
                    # RSI
                    rsi = calculate_rsi(closes, 14)
                    rsi_emoji = "🔴" if rsi > 70 else "🟢" if rsi < 30 else ""
                    
                    dist_high = ((float(high_24) - curr_close) / curr_close) * 100
                    dist_low = ((curr_close - float(low_24)) / float(low_24)) * 100

                    # Görseldeki Birebir Format (M5 Futures olarak güncellendi)
                    msg = (f"#<b>{symbol}</b> 💚 <b>Price {price_change_5m:+.2f}% Vol ❌ {vol_ratio:.1f}</b> 💰\n"
                           f"(M5 Futures)\n\n"
                           f"Total Vol {format_vol(curr_vol)} Taker {taker_pct:.0f}% Rsi/{rsi:.0f}{rsi_emoji}\n"
                           f"────────────────\n"
                           f"🏷 Price: {curr_close:.4f} ...\n"
                           f"🕒 24h: {float(chg):+.2f}% Vol {format_vol(float(vol))}\n"
                           f"⬆ High: {float(high_24):.4f} ({dist_high:+.2f}%)\n"
                           f"⬇ Low: {float(low_24):.4f} ({dist_low:+.2f}%)")
                    
                    tg(C, msg)
                    son_sinyaller[symbol] = time.time()
                    time.sleep(1) # API limitini koru

            time.sleep(10)
        except Exception as e:
            time.sleep(10)

# --- RENDER PORT SUNUCUSU ---
def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), Handler) as httpd:
        httpd.serve_forever()

if __name__ == "__main__":
    # 1. Render'ın kapanmaması için web sunucusu (Cronjob'un ping atacağı yer)
    threading.Thread(target=run_dummy_server, daemon=True).start()
    
    # 2. Telegram komut dinleyicisi (/ratio)
    threading.Thread(target=command_listener, daemon=True).start()
    
    # 3. Ana tarama motoru
    bot_loop()

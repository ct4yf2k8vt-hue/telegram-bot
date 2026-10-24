import urllib.request, urllib.parse, json, time, socket
import os
from datetime import datetime, timedelta, timezone

# Ağ donmalarını engellemek için istek zaman aşımı süresi
socket.setdefaulttimeout(10)

T = os.environ.get("BOT_TOKEN")
C = os.environ.get("CHAT_ID", "1623907197")
TR_Saati = timezone(timedelta(hours=3))

# Daha önce sinyal gönderilen semboller (tekrar sinyal engelleme)
son_sinyaller = {}
COOLDOWN = 900  # 15 dakika

def tg(m):
    try:
        d = urllib.parse.urlencode({
            "chat_id": C,
            "text": m,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }).encode()
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
    if abs(num) >= 1_000_000: return f"{num/1_000_000:.2f}M"
    elif abs(num) >= 1_000: return f"{num/1_000:.2f}K"
    else: return f"{num:.2f}"

def bot_loop():
    tg("🚀 <b>MPB ALGOS NEW 2.0 (SHORT) BOTU BAŞLATILDI!</b>\n"
       f"🎯 Sadece Binance Futures piyasasında çalışır.")

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
                        # Likit ve günlük düşüşte olanları aday olarak seç
                        if vol > 10_000_000 and chg <= -3.0:
                            adaylar.append(t['symbol'])
                    except:
                        pass

            for symbol in adaylar:
                if symbol in son_sinyaller and (time.time() - son_sinyaller[symbol] < COOLDOWN):
                    continue

                # 5 dakikalık mum verisini çek
                klines = gj(f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=5m&limit=20")
                if not klines or len(klines) < 20: continue

                # Son 20 mumun en yükseğini bul (direnç / kırılım bölgesi)
                high_20 = max(float(k[2]) for k in klines)
                curr_close = float(klines[-1][4])
                curr_vol = float(klines[-1][7])
                
                # Hacim ortalaması
                avg_vol = sum(float(k[7]) for k in klines[:-1]) / 19

                # Sinyal Şartı: Fiyat 20 mumluk zirvenin belirgin altına sarkmış ve hacim artmışsa
                if curr_close < (high_20 * 0.97) and curr_vol > (avg_vol * 1.5):
                    
                    # Kâr Al ve Zarar Durdur (Kullanıcının istediği gibi %3 / %1.5)
                    tp_price = curr_close * 0.97
                    sl_price = curr_close * 1.015

                    msg = (f"📊 MPB ALGOS NEW 2.0\n"
                           f"🎯 #{symbol}\n"
                           f"🔴 SHORT\n"
                           f"📉 PRECIO: {curr_close:.5f}\n\n"
                           f"🎯 <b>Hedef (TP):</b> {tp_price:.5f} (-%3.0)\n"
                           f"🛑 <b>Zarar Durdur (SL):</b> {sl_price:.5f} (+%1.5)\n"
                           f"⏰ {datetime.now(TR_Saati).strftime('%H:%M')}")

                    tg(msg)
                    son_sinyaller[symbol] = time.time()

            time.sleep(15)

        except Exception as e:
            time.sleep(10)

if __name__ == "__main__":
    import http.server, socketserver, threading
    def run_dummy():
        port = int(os.environ.get("PORT", 10000))
        Handler = http.server.SimpleHTTPRequestHandler
        with socketserver.TCPServer(("", port), Handler) as httpd:
            httpd.serve_forever()

    threading.Thread(target=run_dummy, daemon=True).start()
    bot_loop()

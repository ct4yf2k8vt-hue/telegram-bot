import urllib.request, urllib.parse, json, time, socket
import os
from datetime import datetime, timedelta, timezone

# KÜRESEL SOCKET ZAMAN AŞIMI: API istekleri asla 10 saniyeden fazla bekleyemez!
socket.setdefaulttimeout(10) 

T = os.environ.get("BOT_TOKEN")
C = "1623907197"
TR_Saati = timezone(timedelta(hours=3))

son_sinyaller = {}
COOLDOWN = 900 # 15 Dakika
SON_RAPOR = time.time()

def tg(m):
    try:
        d = urllib.parse.urlencode({"chat_id": C, "text": m, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
        req = urllib.request.Request("https://api.telegram.org/bot" + T + "/sendMessage", data=d)
        urllib.request.urlopen(req, timeout=10).read()
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
    global SON_RAPOR
    
    # Başladığında Telegram'a haber ver (Log ekranına güvenmiyoruz)
    tg("🚀 <b>Bot Başlatıldı!</b> Render loglarına bakmayın, raporlar buraya gelecek.")

    while True:
        try:
            # 1. HER 30 DAKİKADA BİR TELEGRAM'A DURUM RAPORU GÖNDER
            if time.time() - SON_RAPOR > 1800:
                tg(f"🤖 <b>Durum Raporu:</b> Bot sorunsuz çalışıyor.\n⏰ {datetime.now(TR_Saati).strftime('%H:%M')}")
                SON_RAPOR = time.time()

            # 2. PİYASA VERİSİNİ ÇEK
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if not tickers:
                time.sleep(5)
                continue

            # 3. FİLTRELE
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
            
            # 4. KONTROL ET
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
                vol_change_15m = ((curr_vol - prev_vol) / prev_vol) * 100
                
                if abs(price_change_15m) < 3.0: continue 
                
                # Türev verileri
                oi_data = gj(f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period=15m&limit=2")
                oi_change_pct, oi_curr = 0, 0
                if oi_data and len(oi_data) >= 2:
                    oi_curr = float(oi_data[-1]['sumOpenInterestValue'])
                    oi_prev = float(oi_data[-2]['sumOpenInterestValue'])
                    if oi_prev > 0: oi_change_pct = ((oi_curr - oi_prev) / oi_prev) * 100
                
                funding_data = gj(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}")
                funding_rate = float(funding_data['lastFundingRate']) if funding_data else 0
                
                depth = gj(f"https://fapi.binance.com/fapi/v1/depth?symbol={symbol}&limit=100")
                bids, asks = 0, 0
                if depth:
                    bids = sum(float(b[1]) for b in depth['bids'])
                    asks = sum(float(a[1]) for a in depth['asks'])
                
                is_dump = price_change_15m < 0
                emoji = "💀" if is_dump else "🚀"
                action = "Dumping" if is_dump else "Pumping"
                
                msg = (f"{emoji} <b>{symbol} is {action} on Binance-Futures!</b>\n"
                       f"💵 <b>Price:</b> {format(curr_close, '.4f')} USDT ({format(price_change_15m, '+.2f')}%)\n"
                       f"📊 <b>24h Vol:</b> {format_number(curr_vol)} USDT\n"
                       f"📉 <b>Last Vol:</b> {format_number(curr_vol)} ({format(vol_change_15m, '+.2f')}%)\n"
                       f"⬇️ <b>Price:</b> {format(prev_close, '.4f')} USDT\n"
                       f"🔴 <b>24h%:</b> {format(chg, '+.2f')}%\n"
                       f"📥 <b>Vol:</b> {format_number(curr_vol - prev_vol)} USDT\n"
                       f"💰 <b>OI:</b> {format_number(oi_curr)} ({format(oi_change_pct, '+.2f')}%)\n"
                       f"🏦 <b>Funding:</b> {format(funding_rate, '+.4f')}%\n\n"
                       f"<b>Bids & Asks in 20% Range</b>\n"
                       f"🟢 <b>Bids:</b> {format_number(bids)} USDT\n"
                       f"🔴 <b>Asks:</b> {format_number(asks)} USDT\n\n"
                       f"🔗 <a href='https://www.binance.com/en/futures/{symbol}'>Trade {symbol}</a>")
                
                tg(msg)
                son_sinyaller[symbol] = time.time()
                
                time.sleep(1) 

            time.sleep(10)

        except Exception as e:
            # Hata olursa (örneğin API zaman aşımı) 10 saniye bekle ve devam et. ASLA ÇÖKME!
            time.sleep(10)

if __name__ == "__main__":
    # Render'ın kapanmaması için port dinleyen sahte bir sunucu (Ama botun çalışmasına karışmaz)
    import http.server
    import socketserver
    import threading
    
    def run_dummy_server():
        port = int(os.environ.get("PORT", 10000))
        Handler = http.server.SimpleHTTPRequestHandler
        with socketserver.TCPServer(("", port), Handler) as httpd:
            httpd.serve_forever()

    threading.Thread(target=run_dummy_server, daemon=True).start()
    
    # Botu başlat
    bot_loop()

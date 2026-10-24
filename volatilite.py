import urllib.request, urllib.parse, json, time
import os
import threading
import signal
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

T = os.environ.get("BOT_TOKEN")
C = "1623907197"
TR_Saati = timezone(timedelta(hours=3))

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
        return json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
    except:
        return None

def format_number(num):
    if abs(num) >= 1_000_000_000: return f"{num/1_000_000_000:.2f}B"
    elif abs(num) >= 1_000_000: return f"{num/1_000_000:.2f}M"
    elif abs(num) >= 1_000: return f"{num/1_000:.2f}K"
    else: return f"{num:.2f}"

# --- ZAMAN AŞIMI SİNYALİ (KESİN ÇÖZÜM) ---
class TimeoutException(Exception): pass
def timeout_handler(signum, frame):
    raise TimeoutException("İşlem 120 saniyeyi aştı, zorla kapatılıyor!")

def heartbeat():
    """Telegram'a 'Çalışıyorum' mesajı atar. Log ekranı donsa bile buradan görürsün."""
    while True:
        time.sleep(900) # 15 dakika
        tg(f"🤖 <b>Bot Aktif:</b> Log ekranı donmuş olabilir ama ben çalışıyorum.\n⏰ {datetime.now(TR_Saati).strftime('%H:%M:%S')}")

def check_signal(symbol, price_change_24h):
    try:
        klines = gj(f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=15m&limit=3")
        if not klines or len(klines) < 3: return
        
        curr_k, prev_k = klines[-2], klines[-3]
        curr_close, prev_close = float(curr_k[4]), float(prev_k[4])
        curr_vol, prev_vol = float(curr_k[7]), float(prev_k[7])
        
        if prev_close == 0 or prev_vol == 0: return
        
        price_change_15m = ((curr_close - prev_close) / prev_close) * 100
        vol_change_15m = ((curr_vol - prev_vol) / prev_vol) * 100
        
        if abs(price_change_15m) < 3.0: return 
        
        oi_data = gj(f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period=15m&limit=2")
        oi_change_pct, oi_curr = 0, 0
        if oi_data and len(oi_data) >= 2:
            oi_curr = float(oi_data[-1]['sumOpenInterestValue'])
            oi_prev = float(oi_data[-2]['sumOpenInterestValue'])
            if oi_prev > 0: oi_change_pct = ((oi_curr - oi_prev) / oi_prev) * 100
        
        funding_data = gj(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}")
        funding_rate = float(funding_data['lastFundingRate']) if funding_data else 0
        
        depth = gj(f"https://fapi.binance.com/fapi/v1/depth?symbol={symbol}&limit=100")
        bids_total, asks_total = 0, 0
        if depth:
            bids_total = sum(float(b[1]) for b in depth['bids'])
            asks_total = sum(float(a[1]) for a in depth['asks'])
        
        is_dump = price_change_15m < 0
        emoji = "💀" if is_dump else "🚀"
        action = "Dumping" if is_dump else "Pumping"
        
        msg = (f"{emoji} <b>{symbol} is {action} on Binance-Futures!</b>\n"
               f"💵 <b>Price:</b> {format(curr_close, '.4f')} USDT ({format(price_change_15m, '+.2f')}%)\n"
               f"📊 <b>24h Vol:</b> {format_number(curr_vol)} USDT\n"
               f"📉 <b>Last Vol:</b> {format_number(curr_vol)} ({format(vol_change_15m, '+.2f')}%)\n"
               f"⬇️ <b>Price:</b> {format(prev_close, '.4f')} USDT\n"
               f"🔴 <b>24h%:</b> {format(price_change_24h, '+.2f')}%\n"
               f"📥 <b>Vol:</b> {format_number(curr_vol - prev_vol)} USDT\n"
               f"💰 <b>OI:</b> {format_number(oi_curr)} ({format(oi_change_pct, '+.2f')}%)\n"
               f"🏦 <b>Funding:</b> {format(funding_rate, '+.4f')}%\n\n"
               f"<b>Bids & Asks in 20% Range</b>\n"
               f"🟢 <b>Bids:</b> {format_number(bids_total)} USDT\n"
               f"🔴 <b>Asks:</b> {format_number(asks_total)} USDT\n\n"
               f"🔗 <a href='https://www.binance.com/en/futures/{symbol}'>Trade {symbol}</a>")
        tg(msg)
    except:
        pass

def bot_loop():
    # ZAMAN AŞIMI SİNYALİNİ AYARLA (120 saniye)
    signal.signal(signal.SIGALRM, timeout_handler)
    
    while True:
        try:
            # Her turda çalar saati kur. 120 saniye sonra çalarsa TimeoutException fırlatır!
            signal.alarm(120) 
            
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if not tickers:
                signal.alarm(0) # Alarmı kapat
                time.sleep(5)
                continue
            
            candidates = []
            for t in tickers:
                if t['symbol'].endswith('USDT'):
                    try:
                        vol = float(t['quoteVolume'])
                        chg = float(t['priceChangePercent'])
                        if vol > 20_000_000 and abs(chg) >= 4.0:
                            candidates.append((t['symbol'], chg))
                    except:
                        pass
            
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] {len(candidates)} aday bulundu. Taranıyor...", flush=True)
            
            for symbol, chg in candidates:
                check_signal(symbol, chg)
                time.sleep(0.2) 
                
            # Her şey bittiyse çalar saatini kapat (İptal et)
            signal.alarm(0) 
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Tur bitti. 10sn bekleniyor...", flush=True)
            time.sleep(10)
            
        except TimeoutException:
            print("\n!!! BOT 120 SANİYEDİR DONDU. İŞLEM ZORLA KESİLİYOR !!!", flush=True)
            os._exit(1) # Render'ı zorla yeniden başlat
        except Exception as e:
            signal.alarm(0)
            time.sleep(5)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    threading.Thread(target=heartbeat, daemon=True).start()
    threading.Thread(target=bot_loop, daemon=True).start()
    
    while True:
        time.sleep(10)

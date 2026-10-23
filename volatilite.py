import urllib.request, urllib.parse, json, time
import os
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

T = os.environ.get("BOT_TOKEN")
C = "1623907197"
TR_Saati = timezone(timedelta(hours=3))

# --- WATCHDOG (BEKÇİ) İÇİN GLOBAL DEĞİŞKENLER ---
LAST_PING = time.time()
WATCHDOG_LIMIT = 120 # 2 dakika boyunca log gelmezse botu yeniden başlat

def tg(m):
    try:
        d = urllib.parse.urlencode({"chat_id": C, "text": m, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
        urllib.request.urlopen("https://api.telegram.org/bot" + T + "/sendMessage", d, timeout=10).read()
    except:
        pass

def gj(u):
    try:
        req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'})
        return json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
    except:
        return None

def format_number(num):
    if abs(num) >= 1000000:
        return f"{num/1000000:.1f}M"
    elif abs(num) >= 1000:
        return f"{num/1000:.1f}K"
    else:
        return f"{num:.0f}"

def bot_loop():
    global LAST_PING
    print("--- XDECOW TARZI TÜREV ANALİZ BOTU BAŞLADI (WATCHDOG AKTİF) ---", flush=True)
    son = {}

    while True:
        try:
            print(f"\n[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Futures piyasa özeti çekiliyor...", flush=True)
            
            tickers = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
            if not tickers:
                time.sleep(3)
                continue

            adaylar = []
            for t in tickers:
                if t['symbol'].endswith('USDT'):
                    try:
                        hacim = float(t['quoteVolume'])
                        if hacim > 5000000:
                            adaylar.append(t['symbol'])
                    except:
                        pass

            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] {len(adaylar)} likit coin bulundu. 1H mumları inceleniyor...", flush=True)

            sayac = 0
            for i, s in enumerate(adaylar):
                try:
                    # LOGLARIN SÜREKLİ AKMASI İÇİN HER 20 COİNDE BİR EKRANA YAZ
                    if (i + 1) % 20 == 0:
                        print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Taranıyor... ({i+1}/{len(adaylar)})", flush=True)

                    k = gj(f"https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=1h&limit=3")
                    if not k or len(k) < 3: continue
                    
                    kapanan_mum = k[-2]
                    onceki_mum = k[-3]
                    
                    fiyat_simdi = float(kapanan_mum[4])
                    fiyat_onceki = float(onceki_mum[4])
                    hacim_simdi = float(kapanan_mum[7])
                    hacim_onceki = float(onceki_mum[7])
                    trade_simdi = int(kapanan_mum[8])
                    trade_onceki = int(onceki_mum[8])
                    
                    if fiyat_onceki == 0 or hacim_onceki == 0 or trade_onceki == 0: continue
                    
                    fiyat_degisim = ((fiyat_simdi - fiyat_onceki) / fiyat_onceki) * 100
                    hacim_degisim = ((hacim_simdi - hacim_onceki) / hacim_onceki) * 100
                    trade_degisim = ((trade_simdi - trade_onceki) / trade_onceki) * 100
                    
                    if abs(fiyat_degisim) >= 5.0:
                        if time.time() - son.get(s, 0) >= 3600: 
                            
                            oi_hist = gj(f"https://fapi.binance.com/futures/data/openInterestHist?symbol={s}&period=1h&limit=2")
                            oi_fark = 0.0
                            oi_degisim = 0.0
                            if oi_hist and len(oi_hist) == 2:
                                oi_simdi = float(oi_hist[-1]['sumOpenInterestValue'])
                                oi_onceki = float(oi_hist[-2]['sumOpenInterestValue'])
                                oi_fark = oi_simdi - oi_onceki
                                oi_degisim = ((oi_simdi - oi_onceki) / oi_onceki) * 100 if oi_onceki > 0 else 0
                            
                            lsr_hist = gj(f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={s}&period=1h&limit=2")
                            lsr_deger = 0.0
                            lsr_degisim = 0.0
                            if lsr_hist and len(lsr_hist) == 2:
                                lsr_deger = float(lsr_hist[-1]['longShortRatio'])
                                lsr_onceki = float(lsr_hist[-2]['longShortRatio'])
                                lsr_degisim = lsr_deger - lsr_onceki
                                
                            if fiyat_degisim < 0:
                                emoji = "💀"
                                yon_text = "Price Dump"
                            else:
                                emoji = "🚀"
                                yon_text = "Price Pump"
                                
                            saat_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
                            
                            msg = (f"🚨 <b>{s}</b>\n"
                                   f"{emoji} <b>{yon_text} {format(fiyat_degisim, '.1f')}% (1h)</b>\n"
                                   f"⏱ {saat_utc}\n\n"
                                   f"ℹ️ <i>1h changes #{s}</i>\n"
                                   f"───────────────────\n\n"
                                   f"💵 <b>Price</b>   <code>${format(fiyat_simdi, '.4f')} | {format(fiyat_degisim, '+.1f')}%</code>\n"
                                   f"📊 <b>Vol</b>     <code>+${format_number(hacim_simdi)} | {format(hacim_degisim, '+.1f')}%</code>\n"
                                   f"💰 <b>OI</b>      <code>{format(oi_fark/1000000, '+.1f')}M | {format(oi_degisim, '+.1f')}%</code>\n"
                                   f"⚖️ <b>LSR</b>     <code>{format(lsr_deger, '.2f')} | {format(lsr_degisim, '+.1f')}</code>\n"
                                   f"🤝 <b>Trades</b>  <code>{format_number(trade_simdi)} | {format(trade_degisim, '+.1f')}%</code>\n\n"
                                   f"───────────────────\n"
                                   f"📈 <a href='https://www.binance.com/en/futures/{s}'>View chart</a>")
                            
                            tg(msg)
                            son[s] = time.time()
                            sayac += 1
                            print(f"🔥 SİNYAL ({yon_text}): {s} | Değişim: %{fiyat_degisim:.1f}", flush=True)
                            
                    time.sleep(0.1)
                except Exception as e:
                    pass
                    
            print(f"[{datetime.now(TR_Saati).strftime('%H:%M:%S')}] Tur Bitti. Sinyal: {sayac}. 5 sn sonra yeni tur...\n", flush=True)
            
            # TUR BAŞARIYLA BİTTİ, BEKÇİYİ BESLE
            LAST_PING = time.time()
            time.sleep(5) 
            
        except Exception as e:
            print(f"ANA DÖNGÜ HATASI: {e} - 5 sn sonra devam edilecek.", flush=True)
            time.sleep(5)

# --- WATCHDOG (BEKÇİ) FONKSİYONU ---
def watchdog():
    global LAST_PING
    while True:
        time.sleep(10) # Her 10 saniyede bir kontrol et
        if time.time() - LAST_PING > WATCHDOG_LIMIT:
            print(f"\n!!! BOT {WATCHDOG_LIMIT} SANİYEDİR YANIT VERMİYOR. ZORLA YENİDEN BAŞLATILIYOR !!!", flush=True)
            os._exit(1) # Programı tamamen kapat (Render otomatik açacak)

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
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"WEB SUNUCUSU BAŞLADI - PORT: {port}", flush=True)
    server.serve_forever()

if __name__ == "__main__":
    # 1. Web sunucusunu başlat
    threading.Thread(target=run_web_server, daemon=True).start()
    
    # 2. Bekçiyi (Watchdog) başlat
    threading.Thread(target=watchdog, daemon=True).start()
    
    # 3. Tarama motorunu başlat
    bot_thread = threading.Thread(target=bot_loop, daemon=True)
    bot_thread.name = "BotThread"
    bot_thread.start()
    
    # Ana thread'i açık tut
    while True:
        time.sleep(10)

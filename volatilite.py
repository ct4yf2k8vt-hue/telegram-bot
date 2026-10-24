import urllib.request, urllib.parse, json, time, socket
import os
from datetime import datetime, timedelta, timezone

# Ağ donmalarını engellemek için istek zaman aşımı
socket.setdefaulttimeout(10)

T = os.environ.get("BOT_TOKEN")
TR_Saati = timezone(timedelta(hours=3))

# Son işlenen mesaj ID'si (Aynı mesajı tekrar cevaplamamak için)
last_update_id = 0

def tg(chat_id, m):
    try:
        d = urllib.parse.urlencode({
            "chat_id": chat_id,
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

def command_listener():
    global last_update_id
    print("Sadece /ratio komut dinleyicisi başladı...", flush=True)
    
    while True:
        try:
            # Telegram'dan gelen son mesajları çek
            updates = gj(f"https://api.telegram.org/bot{T}/getUpdates?offset={last_update_id + 1}&timeout=10")
            
            if updates and updates.get("ok"):
                for u in updates["result"]:
                    last_update_id = u["update_id"]
                    
                    if "message" in u and "text" in u["message"]:
                        text = u["message"]["text"]
                        chat_id = u["message"]["chat"]["id"]
                        
                        # Eğer mesaj /ratio ile başlıyorsa
                        if text.startswith("/ratio"):
                            parts = text.split()
                            
                            # Parametre kontrolü
                            if len(parts) < 2:
                                tg(chat_id, "⚠️ <b>Hatalı Format!</b>\nKullanım:\n<code>/ratio btcusdt</code>\n<code>/ratio ethusdt 4h</code>")
                                continue
                            
                            symbol = parts[1].upper()
                            if not symbol.endswith("USDT"):
                                symbol += "USDT"
                                
                            # Zaman dilimi (varsayılan 1h)
                            period = parts[2].lower() if len(parts) > 2 else "1h"
                            
                            # Binance Futures'tan Long/Short verisini çek
                            url = f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period={period}&limit=1"
                            data = gj(url)
                            
                            if data and len(data) > 0:
                                d = data[0]
                                long_pct = float(d['longAccount']) * 100
                                short_pct = float(d['shortAccount']) * 100
                                ratio = float(d['longShortRatio'])
                                
                                # Görseldeki birebir format
                                msg = (f"Long/Short Ratio📊\n"
                                       f"Binance - {symbol}\n"
                                       f"⏳ <b>Timeframe:</b> {period}\n"
                                       f"📈 <b>Long:</b> {long_pct:.2f}%\n"
                                       f"📉 <b>Short:</b> {short_pct:.2f}%\n"
                                       f"⚖️ <b>Ratio:</b> {ratio:.2f}\n\n"
                                       f"<i>Last Updated: {datetime.now(TR_Saati).strftime('%H:%M')} (TR)</i>")
                                tg(chat_id, msg)
                            else:
                                tg(chat_id, f"❌ <b>{symbol}</b> için veri bulunamadı. Sembolü veya zaman dilimini kontrol edin.")
                                
        except Exception as e:
            pass
        time.sleep(2)

# --- RENDER'IN PORT İSTEĞİNİ KARŞILAMAK İÇİN (Kapanmayı önler) ---
def run_dummy_server():
    import http.server
    import socketserver
    port = int(os.environ.get("PORT", 10000))
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), Handler) as httpd:
        httpd.serve_forever()

if __name__ == "__main__":
    import threading
    # Render'ın botu kapatmaması için sahte sunucuyu başlat
    threading.Thread(target=run_dummy_server, daemon=True).start()
    
    # Sadece komut dinleyiciyi çalıştır. Tarama falan YOK.
    command_listener()

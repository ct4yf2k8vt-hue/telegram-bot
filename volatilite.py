import urllib.request, urllib.parse, json, time
import os

T = os.environ.get("BOT_TOKEN")
C = "1623907197"

def tg(ci, m):
    try:
        d = urllib.parse.urlencode({"chat_id": ci, "text": m, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
        urllib.request.urlopen("https://api.telegram.org/bot" + T + "/sendMessage", d, timeout=10).read()
    except:
        pass

def gj(u):
    try:
        return json.loads(urllib.request.urlopen(u, timeout=20).read().decode())
    except:
        return None

tg(C, "TOP MOVERS BOTU AKTIF")
print("BOT BASLADI")

while True:
    try:
        # TEK ISTEKLE tum coinlerin 24 saatlik verisini al
        data = gj("https://fapi.binance.com/fapi/v1/ticker/24hr")
        if not data:
            print("Veri alinamadi")
            time.sleep(60)
            continue
        
        # Sadece USDT pariteleri ve yuksek hacimliler
        sonuclar = []
        for x in data:
            s = x["symbol"]
            if not s.endswith("USDT"):
                continue
            try:
                degisim = float(x["priceChangePercent"])
                son_fiyat = float(x["lastPrice"])
                yuksek = float(x["highPrice"])
                hacim = float(x["quoteVolume"])
                # Son fiyat, 24 saatlik zirveye cok yakinsa (yeni zirve)
                if son_fiyat >= yuksek * 0.998:
                    if hacim >= 1000000:  # 1M USDT uzeri hacim
                        sonuclar.append((degisim, s, son_fiyat, yuksek, hacim))
            except:
                pass
        
        # Degisime gore sirala
        sonuclar.sort(reverse=True)
        sonuclar = sonuclar[:15]
        
        if sonuclar:
            msg = "📊 <b>TOP MOVERS - NEW HIGH (24h)</b>\n\n"
            for degisim, s, fiyat, yuksek, hacim in sonuclar:
                emoji = "🟢" if degisim > 0 else "🔴"
                msg += (emoji + " <b>" + s + "</b>\n"
                        "24h Chg: <b>" + format(degisim, "+.2f") + "%</b>\n"
                        "Status: <b>New 24hr High</b>\n"
                        "Fiyat: <code>" + format(fiyat, ".4f") + "</code>\n"
                        "Hacim: <code>$" + format(hacim / 1000000, ".1f") + "M</code>\n\n")
            msg += "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
            msg += "Link: marketowl.eu"
            tg(C, msg)
            print("TABLO GONDERILDI:", len(sonuclar))
        else:
            print("Yeni zirve yok")
        
        print("Tarama bitti")
        time.sleep(300)
    except Exception as e:
        print("Hata:", e)
        time.sleep(30)

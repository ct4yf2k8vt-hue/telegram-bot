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

print("BASLADI")
S = [x["symbol"] for x in gj("https://fapi.binance.com/fapi/v1/exchangeInfo")["symbols"] if x["status"] == "TRADING" and x["quoteAsset"] == "USDT" and x["contractType"] == "PERPETUAL"]
print("COIN:", len(S))
tg(C, "NEW HIGH BOTU AKTIF " + str(len(S)))
print("TG GONDERILDI")

while True:
    try:
        print("--- TARAMA BASLADI ---")
        sonuclar = []
        for i, s in enumerate(S):
            try:
                # Gunluk 31 mum (30 gun once + bugun)
                kd = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=1d&limit=31")
                if not kd or len(kd) < 31:
                    continue
                # Son kapanmis gunun en yuksegi (kd[-2])
                bugun_high = float(kd[-2][2])
                # Son kapanmis gunun kapanisi
                bugun_close = float(kd[-2][4])
                # Onceki gun kapanisi (24h degisim icin)
                onceki_close = float(kd[-3][4])
                degisim = ((bugun_close - onceki_close) / onceki_close) * 100
                # Onceki 29 gunun en yuksegi (kd[-31:-2])
                onceki_max = max([float(x[2]) for x in kd[-31:-2]])
                # Yeni zirve kontrolu
                if bugun_high > onceki_max:
                    sonuclar.append((degisim, s, bugun_close, bugun_high))
            except:
                pass
            # Her 50 coinde bir log
            if i % 50 == 0:
                print("Islenen:", i)
        print("YENI ZIRVE SAYISI:", len(sonuclar))
        # Degisime gore sirala
        sonuclar.sort(reverse=True)
        sonuclar = sonuclar[:15]
        if sonuclar:
            msg = "📊 <b>TOP MOVERS - NEW HIGH</b>\n\n"
            for degisim, s, fiyat, high in sonuclar:
                emoji = "🟢" if degisim > 0 else "🔴"
                msg += (emoji + " <b>" + s + "</b>\n"
                        "24h Chg: <b>" + format(degisim, "+.2f") + "%</b>\n"
                        "Fiyat: <code>" + format(fiyat, ".4f") + "</code>\n"
                        "High: <code>" + format(high, ".4f") + "</code>\n\n")
            msg += "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime())
            tg(C, msg)
            print("TABLO GONDERILDI")
        else:
            print("Yeni zirve bulunamadi")
        print("--- TARAMA BITTI ---")
        time.sleep(300)
    except Exception as e:
        print("Hata:", e)
        time.sleep(30)

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

S = [x["symbol"] for x in gj("https://fapi.binance.com/fapi/v1/exchangeInfo")["symbols"] if x["status"] == "TRADING" and x["quoteAsset"] == "USDT" and x["contractType"] == "PERPETUAL"]
tg(C, "TOP MOVERS - NEW HIGH BOTU AKTIF " + str(len(S)))

while True:
    try:
        sonuclar = []
        for s in S:
            try:
                # Gunluk mumlari al (son 31 gun)
                kd = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=1d&limit=31")
                if not kd or len(kd) < 31:
                    continue
                # Simdiki fiyat (son kapanmis gun)
                simdi = float(kd[-2][4])
                # 24 saatlik degisim (son 2 gunluk)
                onceki_24s = float(kd[-3][4])
                degisim_24s = ((simdi - onceki_24s) / onceki_24s) * 100
                # Son 1 gunun en yuksegi
                son_1g_high = float(kd[-2][2])
                # Son 7 gunun en yuksegi (bugun haric)
                son_7g_high = max([float(x[2]) for x in kd[-8:-1]])
                # Son 24 saatin en yuksegi (bugun haric)
                son_24s_high = max([float(x[2]) for x in kd[-3:-1]])
                # Son 30 gunun en yuksegi (bugun haric)
                son_30g_high = max([float(x[2]) for x in kd[-31:-1]])
                # Yeni zirve kontrolu
                if son_1g_high > son_30g_high:
                    sonuclar.append(("New 30day High", simdi, degisim_24s, s))
                if son_1g_high > son_7g_high:
                    sonuclar.append(("New 7day High", simdi, degisim_24s, s))
                if son_1g_high > son_24s_high:
                    sonuclar.append(("New 24hr High", simdi, degisim_24s, s))
            except:
                pass
        # Degisime gore sirala (en yuksekten en dusuge)
        sonuclar.sort(reverse=True, key=lambda x: x[2])
        sonuclar = sonuclar[:15]
        if sonuclar:
            msg = "📊 <b>TOP MOVERS - NEW HIGH</b>\n\n"
            for durum, fiyat, degisim, s in sonuclar:
                emoji = "🟢" if degisim > 0 else "🔴"
                msg += (emoji + " <b>" + s + "</b>\n"
                        "24h Chg: <b>" + format(degisim, "+.2f") + "%</b>\n"
                        "Status: <b>" + durum + "</b>\n\n")
            msg += "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
            msg += "Link: marketowl.eu"
            tg(C, msg)
            print("Tablo gonderildi:", len(sonuclar))
        else:
            print("Yeni zirve yok")
        print("Tarama bitti")
        time.sleep(300)
    except Exception as e:
        print("Hata:", e)
        time.sleep(30)

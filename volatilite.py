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

# SADECE BINANCE SPOT'ta islem goren USDT pariteleri
info = gj("https://api.binance.com/api/v3/exchangeInfo")
S = []
for x in info["symbols"]:
    if x["status"] == "TRADING" and x["quoteAsset"] == "USDT" and x["isSpotTradingAllowed"] == True:
        S.append(x["symbol"])

tg(C, "BINANCE 1 AYLIK / 1 YILLIK ZIRVE BOTU AKTIF " + str(len(S)) + " COIN")

son = {}

while True:
    try:
        aylik_zirveler = []
        yillik_zirveler = []
        for s in S:
            try:
                # Binance SPOT API'sinden gunluk mumlari al
                k = gj("https://api.binance.com/api/v3/klines?symbol=" + s + "&interval=1d&limit=365")
                if not k or len(k) < 30:
                    continue
                km = k[:-1]  # son kapanmis mumlari al
                if len(km) < 30:
                    continue
                high = [float(x[2]) for x in km]
                fiyat = float(km[-1][4])
                aylik_max = max(high[-30:])
                yillik_max = max(high)
                
                # 1 aylik zirveye %1 yakinsa
                if fiyat >= aylik_max * 0.99:
                    aylik_zirveler.append((fiyat / aylik_max, s, fiyat, aylik_max))
                # 1 yillik zirveye %1 yakinsa
                if fiyat >= yillik_max * 0.99:
                    yillik_zirveler.append((fiyat / yillik_max, s, fiyat, yillik_max))
            except:
                pass
        
        aylik_zirveler.sort(reverse=True)
        yillik_zirveler.sort(reverse=True)
        
        if aylik_zirveler:
            msg = "📅 <b>BINANCE 1 AYLIK ZIRVE (30 GUN)</b>\n\n"
            for oran, s, fiyat, max_f in aylik_zirveler[:15]:
                msg += ("<b>" + s + "</b>\n"
                        "Fiyat: <code>" + format(fiyat, ".6f") + "</code>\n"
                        "1 Aylik Max: <code>" + format(max_f, ".6f") + "</code>\n"
                        "Yakinlik: %" + format(oran * 100, ".2f") + "\n\n")
            msg += "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
            msg += "Link: marketowl.eu"
            tg(C, msg)
            print("AYLIK ZIRVE:", len(aylik_zirveler))
        
        if yillik_zirveler:
            msg = "📅 <b>BINANCE 1 YILLIK ZIRVE (365 GUN)</b>\n\n"
            for oran, s, fiyat, max_f in yillik_zirveler[:15]:
                msg += ("<b>" + s + "</b>\n"
                        "Fiyat: <code>" + format(fiyat, ".6f") + "</code>\n"
                        "1 Yillik Max: <code>" + format(max_f, ".6f") + "</code>\n"
                        "Yakinlik: %" + format(oran * 100, ".2f") + "\n\n")
            msg += "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
            msg += "Link: marketowl.eu"
            tg(C, msg)
            print("YILLIK ZIRVE:", len(yillik_zirveler))
        
        print("Tarama bitti")
        time.sleep(1800)
    except Exception as e:
        print("Hata:", e)
        time.sleep(30)

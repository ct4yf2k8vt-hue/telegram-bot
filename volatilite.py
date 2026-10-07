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

def rsi(c, n=14):
    if len(c) < n + 1: return None
    g = [0] * n
    lo = [0] * n
    for i in range(1, n + 1):
        d = c[i] - c[i-1]
        if d > 0: g[i-1] = d
        else: lo[i-1] = -d
    ag = sum(g) / n
    al = sum(lo) / n
    for i in range(n + 1, len(c)):
        d = c[i] - c[i-1]
        if d > 0:
            ag = (ag * (n-1) + d) / n
            al = (al * (n-1)) / n
        else:
            ag = (ag * (n-1)) / n
            al = (al * (n-1) - d) / n
    if al == 0: return 100
    return 100 - (100 / (1 + ag / al))

print("BOT BASLADI")
S = [x["symbol"] for x in gj("https://fapi.binance.com/fapi/v1/exchangeInfo")["symbols"] if x["status"] == "TRADING" and x["quote mumAsset"] == "USDT" and x["contractType"] ==u "PER alPETUAL"]
tg(C, "VOLUME TRACKER PUMP BOTU AKTIF " + str(len(S)))
print("COIN:", len(S))

son_sinyal = {}
sayac = {}

while True:
    try:
        for s in S:
            try:
                # 5 dakikalik son 30
                k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=5m&limit=30")
                if not k or len(k) < 20:
                    continue
                kapanmis = k[:-1]
                if len(kapanmis) < 15:
                    continue
                c = [float(x[4]) for x in kapanmis]
                v = [float(x[5]) for x in kapanmis]
                taker_buy = [float(x[9]) for x in kapanmis]

                # Son kapanmis mum (M5)
                son_mum = kapanmis[-1]
                fiyat = float(son_mum[4])
                hacim = float(son_mum[5])
                taker_buy_vol = float(son_mum[9])

                # Fiyat degisimi (son 5 dakika)
                onceki_fiyat = float(kapanmis[-2][4])
                fiyat_degisim = ((fiyat - onceki_fiyat) / onceki_fiyat) * 100

                # Hacim patlamasi: son mumun hacmi, son 20 mumun ortalamasinin 3 kati
                v_ort = sum(v[-21:-1]) / 20
                if v_ort == 0: continue
                hacim_oran = hacim / v_ort

                # Taker orani (son mum)
                if hacim == 0: continue
                taker_pct = (taker_buy_vol / hacim) * 100

                # RSI (14 periyot)
                r = rsi(c)
                if r is None: continue

                # Sinyal kosulu: hacim 2x+ VE fiyat %3+ VE taker %55+
                if hacim_oran >= 2 and fiyat_degisim >= 3 and taker_pct >= 55:
                    if time.time() - son_sinyal.get(s, 0) >= 3600:
                        # Son 1 saatteki sinyal sayisi
                        if s not in sayac or (time.time() - sayac[s]["ilk"]) > 3600:
                            sayac[s] = {"ilk": time.time(), "sayi": 1}
                        else:
                            sayac[s]["sayi"] += 1

                        # 24 saatlik veriler
                        ticker = gj("https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=" + s)
                        if not ticker: continue
                        yuksek = float(ticker["highPrice"])
                        dusuk = float(ticker["lowPrice"])
                        hacim24 = float(ticker["quoteVolume"])
                        degisim24 = float(ticker["priceChangePercent"])

                        # Taker orani (genel)
                        taker_data = gj("https://fapi.binance.com/futures/data/takerlongshortRatio?symbol=" + s + "&period=5m&limit=1")
                        taker_genel = float(taker_data[0]["buySellRatio"]) if taker_data else taker_pct / 100

                        emoji = "💚" if fiyat_degisim > 0 else "🔴"
                        msg = ("#" + s + " " + emoji + " <b>Price +" + format(fiyat_degisim, ".2f") + "% Vol "
                               "💵" + format(hacim_oran, ".1f") + " (M5 Spot)</b>\n\n"
                               "Total Vol " + format(hacim / 1000, ".1f") + "K Taker " + format(taker_pct, ".0f") + "% Rsi/" + format(r, ".0f") + "\n"
                               "────────────────\n"
                               "2 notifs in last 1h, " + format((time.time() - sayac[s]["ilk"]) / 60, ".0f") + "m\n\n"
                               "🏷 Price  " + format(fiyat, ".4f") + " ···\n"
                               "🕐 24h  +" + format(degisim24, ".2f") + "% Vol " + format(hacim24 / 1000000, ".2f") + "M\n"
                               "⬆ High  " + format(yuksek, ".4f") + " (" + format(((fiyat - yuksek) / yuksek) * 100, ".2f") + "%)\n"
                               "⬇ Low   " + format(dusuk, ".4f") + " (+" + format(((fiyat - dusuk) / dusuk) * 100, ".2f") + "%)\n\n"
                               "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                               "Link: marketowl.eu")
                        tg(C, msg)
                        son_sinyal[s] = time.time()
                        print(s, "PUMP", format(fiyat_degisim, ".2f"))
            except:
                pass
        print("Tarama bitti")
        time.sleep(60)
    except Exception as e:
        print("Hata:", e)
        time.sleep(30)

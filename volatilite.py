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
tg(C, "ANORMAL VOLATILITE BOTU AKTIF " + str(len(S)))

son = {}

while True:
    try:
        sayac = 0
        for s in S:
            try:
                k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=15m&limit=50")
                if not k or len(k) < 30:
                    continue
                km = k[:-1]
                if len(km) < 25:
                    continue
                c = [float(x[4]) for x in km]
                h = [float(x[2]) for x in km]
                l = [float(x[3]) for x in km]
                v = [float(x[5]) for x in km]
                
                sma20 = sum(c[-20:]) / 20
                var = sum((x - sma20) ** 2 for x in c[-20:]) / 20
                std20 = var ** 0.5
                bu = sma20 + 2 * std20
                bl = sma20 - 2 * std20
                f = c[-1]
                
                if not (f > bu or f < bl):
                    continue
                
                tr_list = []
                for i in range(1, 15):
                    tr = max(h[-i] - l[-i], abs(h[-i] - c[-i-1]), abs(l[-i] - c[-i-1]))
                    tr_list.append(tr)
                atr14 = sum(tr_list) / 14
                atr_pct = (atr14 / f) * 100
                
                if atr_pct < 2:
                    continue
                
                if time.time() - son.get(s, 0) < 1800:
                    continue
                
                yon = "LONG" if f > bu else "SHORT"
                pumpdump = "PUMP" if yon == "LONG" else "DUMP"
                emoji = "🟢" if yon == "LONG" else "🔴"
                chg = ((f - c[-2]) / c[-2]) * 100 if len(c) > 1 else 0
                v_son = v[-1]
                v_ort = sum(v[-21:-1]) / 20
                v_oran = v_son / v_ort if v_ort > 0 else 0
                sikisma = ((bu - bl) / sma20) * 100
                
                msg = (emoji + " <b>ANORMAL VOLATILITE ALARMI</b>\n"
                       "COIN: <b>" + s + "</b> (15m)\n"
                       "YON: <b>" + yon + " (" + pumpdump + ")</b>\n\n"
                       "Fiyat: <code>" + format(f, ".6f") + "</code> (" + format(chg, ".2f") + "%)\n"
                       "Bollinger: <code>" + format(bl, ".6f") + "</code> - <code>" + format(bu, ".6f") + "</code>\n"
                       "ATR: <code>" + format(atr14, ".6f") + "</code> (" + format(atr_pct, ".2f") + "%)\n"
                       "Hacim: <code>" + format(v_oran, ".2f") + "x</code>\n"
                       "Sikisma: <code>" + format(sikisma, ".0f") + "%</code>\n\n"
                       "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                       "Link: marketowl.eu")
                tg(C, msg)
                son[s] = time.time()
                sayac += 1
                print(s, pumpdump, format(atr_pct, ".2f"))
            except:
                pass
        print("Tarama bitti - Sinyal:", sayac)
        time.sleep(300)
    except Exception as e:
        print("Hata:", e)
        time.sleep(30)

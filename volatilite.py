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

def sma(v, n):
    if len(v) < n: return None
    return sum(v[-n:]) / n

def std(v, n):
    if len(v) < n: return None
    m = sum(v[-n:]) / n
    return (sum((x - m) ** 2 for x in v[-n:]) / n) ** 0.5

def atr(h, l, c, n=14):
    if len(c) < n + 1: return None
    tr = []
    for i in range(1, len(c)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1])))
    if len(tr) < n: return None
    a = sum(tr[:n]) / n
    for x in tr[n:]:
        a = (a * (n - 1) + x) / n
    return a

S = [x["symbol"] for x in gj("https://fapi.binance.com/fapi/v1/exchangeInfo")["symbols"] if x["status"] == "TRADING" and x["quoteAsset"] == "USDT" and x["contractType"] == "PERPETUAL"]
tg(C, "ANORMAL VOLATILITE BOTU AKTIF " + str(len(S)))
son = {}

while True:
    try:
        for s in S:
            try:
                k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=15m&limit=100")
                if not k or len(k) < 30:
                    continue
                c = [float(x[4]) for x in k][:-1]
                h = [float(x[2]) for x in k][:-1]
                l = [float(x[3]) for x in k][:-1]
                v = [float(x[5]) for x in k][:-1]
                if len(c) < 30:
                    continue
                sma20 = sma(c, 20)
                st20 = std(c, 20)
                if None in (sma20, st20):
                    continue
                bu = sma20 + 2 * st20
                bl = sma20 - 2 * st20
                bw = (bu - bl) / sma20 if sma20 else 0
                bw_list = []
                for i in range(20, len(c)):
                    s20 = sma(c[:i], 20)
                    t20 = std(c[:i], 20)
                    if s20 and t20 and s20 > 0:
                        bw_list.append(((s20 + 2 * t20) - (s20 - 2 * t20)) / s20)
                if len(bw_list) < 20:
                    continue
                bw_ort = sum(bw_list[-20:]) / 20
                if bw_ort == 0:
                    continue
                squeeze = bw < bw_ort * 0.7
                f = c[-1]
                breakout = f > bu or f < bl
                a = atr(h, l, c)
                if a is None or a == 0:
                    continue
                atr_pct = (a / f) * 100
                if squeeze and breakout and atr_pct > 2:
                    if time.time() - son.get(s, 0) >= 1800:
                        yon = "LONG" if f > bu else "SHORT"
                        emoji = "🟢" if yon == "LONG" else "🔴"
                        chg = ((f - c[-2]) / c[-2]) * 100 if len(c) > 1 else 0
                        v_son = v[-1]
                        v_ort = sum(v[-20:]) / 20
                        v_oran = v_son / v_ort if v_ort > 0 else 0
                        msg = (emoji + " <b>ANORMAL VOLATILITE ALARMI</b>\n"
                               "COIN: <b>" + s + "</b> (15m)\n"
                               "YON: <b>" + yon + "</b>\n\n"
                               "Fiyat: <code>" + format(f, ".6f") + "</code> (" + format(chg, ".2f") + "%)\n"
                               "Bollinger: <code>" + format(bl, ".6f") + "</code> - <code>" + format(bu, ".6f") + "</code>\n"
                               "ATR: <code>" + format(a, ".6f") + "</code> (" + format(atr_pct, ".2f") + "%)\n"
                               "Hacim: <code>" + format(v_oran, ".2f") + "x</code>\n"
                               "Sikisma: <code>" + format(bw / bw_ort * 100, ".0f") + "%</code>\n\n"
                               "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                               "Link: marketowl.eu")
                        tg(C, msg)
                        son[s] = time.time()
                        print(s, yon)
            except:
                pass
        print("Tarama bitti")
        time.sleep(300)
    except Exception as e:
        print("Hata:", e)
        time.sleep(30)

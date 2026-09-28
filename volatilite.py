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

def ema(v, p):
    if len(v) < p: return None
    k = 2 / (p + 1)
    e = sum(v[:p]) / p
    for x in v[p:]:
        e = x * k + e * (1 - k)
    return e

# TUM COINLER
S = [x["symbol"] for x in gj("https://fapi.binance.com/fapi/v1/exchangeInfo")["symbols"] if x["status"] == "TRADING" and x["quoteAsset"] == "USDT" and x["contractType"] == "PERPETUAL"]

tg(C, "EMA 7/25 KESISIM BOTU AKTIF - " + str(len(S)) + " COIN")
son = {}

while True:
    try:
        for s in S:
            try:
                # 15 dakikalik son 50 mumu al
                k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=15m&limit=50")
                if not k or len(k) < 30:
                    continue
                # Son kapanmis mumlari al (sonuncuyu at - henuz kapanmadi)
                kapanmis = k[:-1]
                if len(kapanmis) < 30:
                    continue
                c = [float(x[4]) for x in kapanmis]
                # EMA 7 ve EMA 25 hesapla
                prev_ema7 = ema(c[:-1], 7)
                prev_ema25 = ema(c[:-1], 25)
                now_ema7 = ema(c, 7)
                now_ema25 = ema(c, 25)
                if None in (prev_ema7, prev_ema25, now_ema7, now_ema25):
                    continue
                fiyat = c[-1]
                # YUKARI KESISIM (LONG)
                if prev_ema7 <= prev_ema25 and now_ema7 > now_ema25:
                    if time.time() - son.get(s, 0) >= 3600:
                        msg = ("🟢 <b>EMA 7/25 YUKARI KESISIM (LONG)</b>\n"
                               "COIN: <b>" + s + "</b> (15m)\n\n"
                               "Fiyat: <code>" + format(fiyat, ".6f") + "</code>\n"
                               "EMA 7: <code>" + format(now_ema7, ".6f") + "</code>\n"
                               "EMA 25: <code>" + format(now_ema25, ".6f") + "</code>\n"
                               "Fark: <code>" + format(now_ema7 - now_ema25, ".6f") + "</code>\n\n"
                               "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                               "Link: marketowl.eu")
                        tg(C, msg)
                        son[s] = time.time()
                        print(s, "LONG")
                # ASAGI KESISIM (SHORT)
                if prev_ema7 >= prev_ema25 and now_ema7 < now_ema25:
                    if time.time() - son.get(s, 0) >= 3600:
                        msg = ("🔴 <b>EMA 7/25 ASAGI KESISIM (SHORT)</b>\n"
                               "COIN: <b>" + s + "</b> (15m)\n\n"
                               "Fiyat: <code>" + format(fiyat, ".6f") + "</code>\n"
                               "EMA 7: <code>" + format(now_ema7, ".6f") + "</code>\n"
                               "EMA 25: <code>" + format(now_ema25, ".6f") + "</code>\n"
                               "Fark: <code>" + format(now_ema7 - now_ema25, ".6f") + "</code>\n\n"
                               "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                               "Link: marketowl.eu")
                        tg(C, msg)
                        son[s] = time.time()
                        print(s, "SHORT")
            except:
                pass
        print("Tarama bitti")
        time.sleep(60)
    except Exception as e:
        print("Hata:", e)
        time.sleep(30)

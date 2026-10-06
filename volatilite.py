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
tg(C, "XDECOW TARZI TRADES TABLOSU AKTIF " + str(len(S)))

while True:
    try:
        sonuclar = []
        for s in S:
            try:
                k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=15m&limit=3")
                if not k or len(k) < 3:
                    continue
                m = k[-2]
                trades = int(m[8])
                m_onceki = k[-3]
                trades_onceki = int(m_onceki[8])
                trades_change = trades - trades_onceki
                fiyat = float(m[4])
                sonuclar.append((trades, s, trades_change, fiyat))
            except:
                pass
        sonuclar.sort(reverse=True)
        sonuclar = sonuclar[:15]
        if sonuclar:
            msg = ("📊 <b>XDECOW - TRADES (15m)</b>\n\n"
                   "<pre>Symbol        Change      Executed     Last Price\n")
            msg += "─" * 50 + "\n"
            for trades, s, change, fiyat in sonuclar:
                change_str = ("+" if change >= 0 else "") + format(change / 1000, ".2f") + "K"
                trades_str = format(trades / 1000, ".2f") + "K"
                msg += "{:<12} {:<12} {:<12} {:<12}\n".format(s, change_str, trades_str, format(fiyat, ".4f"))
            msg += "</pre>\n"
            msg += "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
            msg += "Link: marketowl.eu"
            tg(C, msg)
            print("Tablo gonderildi")
        else:
            print("Sonuc yok")
        print("Tarama bitti")
        time.sleep(300)
    except Exception as e:
        print("Hata:", e)
        time.sleep(30)

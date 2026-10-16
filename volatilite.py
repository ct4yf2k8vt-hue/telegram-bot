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

# ===================================================
# SADECE BINANCE API KULLANILIYOR
# ===================================================
# 1. Coin listesi: Binance Futures API
# 2. Mum verileri: Binance Futures API
# Baska hicbir borsa/global kaynak kullanilmiyor.
# ===================================================

S = [x["symbol"] for x in gj("https://fapi.binance.com/fapi/v1/exchangeInfo")["symbols"] if x["status"] == "TRADING" and x["quoteAsset"] == "USDT" and x["contractType"] == "PERPETUAL"]
tg(C, "5 DK HACIM PATLAMASI BOTU AKTIF " + str(len(S)))
print("COIN SAYISI (Binance):", len(S))
son = {}

while True:
    try:
        sayac = 0
        for s in S:
            try:
                # SADECE BINANCE FUTURES API'sinden veri cek
                k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=5m&limit=15")
                if not k or len(k) < 12:
                    continue
                km = k[:-1]
                if len(km) < 11:
                    continue
                
                v_son = float(km[-1][5])
                fiyat = float(km[-1][4])
                
                onceki_10 = [float(x[5]) for x in km[-11:-1]]
                v_ort = sum(onceki_10) / 10
                
                if v_ort == 0:
                    continue
                
                v_oran = v_son / v_ort
                artis_pct = (v_oran - 1) * 100
                
                if artis_pct >= 5:
                    if time.time() - son.get(s, 0) >= 1800:
                        chg = ((fiyat - float(km[-2][4])) / float(km[-2][4])) * 100
                        emoji = "🟢" if chg > 0 else "🔴"
                        msg = ("🔥 <b>HACIM PATLAMASI</b>\n"
                               "COIN: <b>" + s + "</b> (5m)\n"
                               "BORSA: <b>Binance Futures</b>\n\n"
                               "Son Hacim: <code>" + format(v_son, ".2f") + "</code>\n"
                               "10 Mum Ort: <code>" + format(v_ort, ".2f") + "</code>\n"
                               "Artis: <b>+" + format(artis_pct, ".2f") + "%</b>\n\n"
                               "Fiyat: <code>" + format(fiyat, ".6f") + "</code> (" + format(chg, ".2f") + "%)\n"
                               "Hacim Orani: <code>" + format(v_oran, ".2f") + "x</code>\n\n"
                               "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                               "Link: marketowl.eu")
                        tg(C, msg)
                        son[s] = time.time()
                        sayac += 1
                        print(s, "HACIM", format(artis_pct, ".2f") + "%")
            except:
                pass
        print("Tarama bitti - Sinyal:", sayac)
        time.sleep(300)
    except Exception as e:
        print("Hata:", e)
        time.sleep(30)

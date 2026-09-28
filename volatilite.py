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

tg(C, "5 MUM ARDI ARDA BOTU AKTIF - " + str(len(S)) + " COIN")
son = {}

while True:
    try:
        for s in S:
            try:
                # 4 saatlik son 30 mumu al
                k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=4h&limit=30")
                if not k or len(k) < 12:
                    continue
                kapanmis = k[:-1]
                if len(kapanmis) < 10:
                    continue
                
                # Son 5 mumun verileri
                son5 = kapanmis[-5:]
                
                # Yesil mum sayisi (kapanis > acilis)
                yesil = 0
                kirmizi = 0
                for m in son5:
                    o = float(m[1])
                    c = float(m[4])
                    if c > o:
                        yesil += 1
                    elif c < o:
                        kirmizi += 1
                
                # Hacim artis kontrolu (son 5 mumun hacmi artiyor mu?)
                v1 = float(son5[0][5])
                v2 = float(son5[1][5])
                v3 = float(son5[2][5])
                v4 = float(son5[3][5])
                v5 = float(son5[4][5])
                hacim_artiyor = v1 < v2 < v3 < v4 < v5
                
                # Fiyat degisimi (ilk ve son kapanis)
                ilk = float(son5[0][1])
                son_fiyat = float(son5[4][4])
                degisim = ((son_fiyat - ilk) / ilk) * 100
                
                # YUKSELIS: 5 ard arda yesil + hacim artiyor
                if yesil == 5 and hacim_artiyor:
                    if time.time() - son.get(s + "_UP", 0) >= 3600:
                        msg = ("🚀 <b>5 ARDI ARDA YESIL MUM</b>\n"
                               "COIN: <b>" + s + "</b> (4h)\n\n"
                               "Ilk Fiyat: <code>" + format(ilk, ".6f") + "</code>\n"
                               "Son Fiyat: <code>" + format(son_fiyat, ".6f") + "</code>\n"
                               "Degisim: <b>+" + format(degisim, ".2f") + "%</b>\n\n"
                               "Hacim Artisi:\n"
                               "M1: <code>" + format(v1, ".0f") + "</code>\n"
                               "M5: <code>" + format(v5, ".0f") + "</code>\n\n"
                               "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                               "Link: marketowl.eu")
                        tg(C, msg)
                        son[s + "_UP"] = time.time()
                        print(s, "5 YESIL", format(degisim, ".2f"))
                
                # DUSUS: 5 ard arda kirmizi + hacim artiyor
                if kirmizi == 5 and hacim_artiyor:
                    if time.time() - son.get(s + "_DOWN", 0) >= 3600:
                        msg = ("🔻 <b>5 ARDI ARDA KIRMIZI MUM</b>\n"
                               "COIN: <b>" + s + "</b> (4h)\n\n"
                               "Ilk Fiyat: <code>" + format(ilk, ".6f") + "</code>\n"
                               "Son Fiyat: <code>" + format(son_fiyat, ".6f") + "</code>\n"
                               "Degisim: <b>" + format(degisim, ".2f") + "%</b>\n\n"
                               "Hacim Artisi:\n"
                               "M1: <code>" + format(v1, ".0f") + "</code>\n"
                               "M5: <code>" + format(v5, ".0f") + "</code>\n\n"
                               "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                               "Link: marketowl.eu")
                        tg(C, msg)
                        son[s + "_DOWN"] = time.time()
                        print(s, "5 KIRMIZI", format(degisim, ".2f"))
            except:
                pass
        print("Tarama bitti")
        time.sleep(300)
    except Exception as e:
        print("Hata:", e)
        time.sleep(30)

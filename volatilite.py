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

tg(C, "5 MUM ARDI ARDA (SIRALI) BOTU AKTIF - " + str(len(S)) + " COIN")
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
                
                # Son 5 mumun kapanis fiyatlari ve hacimleri
                son5 = kapanmis[-5:]
                c1 = float(son5[0][4])
                c2 = float(son5[1][4])
                c3 = float(son5[2][4])
                c4 = float(son5[3][4])
                c5 = float(son5[4][4])
                
                # Hacimler
                v1 = float(son5[0][5])
                v2 = float(son5[1][5])
                v3 = float(son5[2][5])
                v4 = float(son5[3][5])
                v5 = float(son5[4][5])
                
                # Hacim artis kontrolu
                hacim_artiyor = v1 < v2 < v3 < v4 < v5
                
                # Yesil mum kontrolu (kapanis > acilis)
                yesil = 0
                kirmizi = 0
                for m in son5:
                    o = float(m[1])
                    c = float(m[4])
                    if c > o:
                        yesil += 1
                    elif c < o:
                        kirmizi += 1
                
                # Fiyat degisimi
                ilk = c1
                son_fiyat = c5
                degisim = ((son_fiyat - ilk) / ilk) * 100
                
                # YUKSELIS: 5 yesil mum + fiyatlar KUCUKTEN BUYUGE + hacim artiyor
                if yesil == 5 and c1 < c2 < c3 < c4 < c5 and hacim_artiyor:
                    if time.time() - son.get(s + "_UP", 0) >= 3600:
                        msg = ("🚀 <b>5 ARDI ARDA YESIL MUM (SIRALI)</b>\n"
                               "COIN: <b>" + s + "</b> (4h)\n\n"
                               "Fiyat Siralamasi:\n"
                               "M1: <code>" + format(c1, ".6f") + "</code>\n"
                               "M2: <code>" + format(c2, ".6f") + "</code>\n"
                               "M3: <code>" + format(c3, ".6f") + "</code>\n"
                               "M4: <code>" + format(c4, ".6f") + "</code>\n"
                               "M5: <code>" + format(c5, ".6f") + "</code>\n\n"
                               "Degisim: <b>+" + format(degisim, ".2f") + "%</b>\n"
                               "Hacim Artisi: <b>EVET</b>\n\n"
                               "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                               "Link: marketowl.eu")
                        tg(C, msg)
                        son[s + "_UP"] = time.time()
                        print(s, "5 YESIL SIRALI", format(degisim, ".2f"))
                
                # DUSUS: 5 kirmizi mum + fiyatlar BUYUKTEN KUCUGE + hacim artiyor
                if kirmizi == 5 and c1 > c2 > c3 > c4 > c5 and hacim_artiyor:
                    if time.time() - son.get(s + "_DOWN", 0) >= 3600:
                        msg = ("🔻 <b>5 ARDI ARDA KIRMIZI MUM (SIRALI)</b>\n"
                               "COIN: <b>" + s + "</b> (4h)\n\n"
                               "Fiyat Siralamasi:\n"
                               "M1: <code>" + format(c1, ".6f") + "</code>\n"
                               "M2: <code>" + format(c2, ".6f") + "</code>\n"
                               "M3: <code>" + format(c3, ".6f") + "</code>\n"
                               "M4: <code>" + format(c4, ".6f") + "</code>\n"
                               "M5: <code>" + format(c5, ".6f") + "</code>\n\n"
                               "Degisim: <b>" + format(degisim, ".2f") + "%</b>\n"
                               "Hacim Artisi: <b>EVET</b>\n\n"
                               "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                               "Link: marketowl.eu")
                        tg(C, msg)
                        son[s + "_DOWN"] = time.time()
                        print(s, "5 KIRMIZI SIRALI", format(degisim, ".2f"))
            except:
                pass
        print("Tarama bitti")
        time.sleep(300)
    except Exception as e:
        print("Hata:", e)
        time.sleep(30)

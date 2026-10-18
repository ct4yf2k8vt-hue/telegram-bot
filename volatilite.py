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

print("BOT BASLADI")
info = gj("https://fapi.binance.com/fapi/v1/exchangeInfo")
S = [x["symbol"] for x in info["symbols"] if x["status"] == "TRADING" and x["quoteAsset"] == "USDT" and x["contractType"] == "PERPETUAL"]
print("COIN:", len(S))
tg(C, "PUMP ALERT BOTU AKTIF " + str(len(S)))
son = {}

while True:
    try:
        sayac = 0
        for s in S:
            try:
                # Son 21 tane 5 dakikalik mum al
                k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=5m&limit=21")
                if not k or len(k) < 21:
                    continue
                
                # Son kapanmis mum
                son_mum = k[-2]
                onceki_mum = k[-3]
                
                fiyat_simdi = float(son_mum[4])     # Kapanis
                fiyat_onceki = float(onceki_mum[4]) # Onceki kapanis
                hacim_simdi = float(son_mum[5])     # Son mumun hacmi (Coin miktari)
                hacim_usdt = float(son_mum[7])      # Son mumun USDT hacmi
                
                # Fiyat degisimi
                fiyat_degisim = ((fiyat_simdi - fiyat_onceki) / fiyat_onceki) * 100
                
                # Son 20 mumun ortalama hacmi (USDT)
                onceki_20 = [float(x[7]) for x in k[:-2]]
                v_ort = sum(onceki_20) / 20
                if v_ort == 0:
                    continue
                
                # Hacim artis orani
                hacim_orani = hacim_usdt / v_ort
                hacim_artis_pct = (hacim_orani - 1) * 100
                
                # Filtre: Hacim normalin 2.5 katindan fazla VE fiyat %2'den fazla artmis olmali
                if hacim_orani >= 2.5 and fiyat_degisim >= 2.0:
                    if time.time() - son.get(s, 0) >= 1800: # 30 dakika cooldown
                        
                        emoji = "🚀"
                        hacim_farki = hacim_usdt - v_ort
                        pair = s.replace("USDT", "/USDT")
                        coin = s.replace("USDT", "")
                        
                        msg = (emoji + " <b>Pump - " + pair + " [Binance]</b>\n"
                               "💵 Price: <code>" + format(fiyat_onceki, ".5f") + "</code> ➜ <code>" + format(fiyat_simdi, ".5f") + "</code> (+" + format(fiyat_degisim, ".1f") + "%)\n"
                               "📊 Volume: " + format(hacim_usdt, ".0f") + " (+" + format(hacim_artis_pct, ".0f") + " %)\n"
                               "Volume increased by " + format(hacim_farki, ".0f") + " ⬆️\n"
                               "Time: " + time.strftime("%I:%M:%S %p", time.localtime()) + " 🕒\n\n"
                               "🔗 <a href='https://www.binance.com/en/futures/" + s + "'>Pair on Binance.</a>\n\n"
                               "$" + coin + " #" + coin)
                        tg(C, msg)
                        son[s] = time.time()
                        sayac += 1
                        print(s, "PUMP", format(fiyat_degisim, ".1f") + "%", format(hacim_orani, ".1f") + "x")
            except:
                pass
        print("Tarama bitti - Sinyal:", sayac)
        time.sleep(300)
    except Exception as e:
        print("Hata:", e)
        time.sleep(30)

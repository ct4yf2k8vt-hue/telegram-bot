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

# Sadece Binance Futures USDT Perpetual pariteleri
S = [x["symbol"] for x in gj("https://fapi.binance.com/fapi/v1/exchangeInfo")["symbols"] if x["status"] == "TRADING" and x["quoteAsset"] == "USDT" and x["contractType"] == "PERPETUAL"]
tg(C, "GELISMIS PUMP ALERT BOTU AKTIF " + str(len(S)))
print("COIN SAYISI:", len(S))
son = {}

while True:
    try:
        sayac = 0
        for s in S:
            try:
                # 5 dakikalik son 30 mumu al
                k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=5m&limit=30")
                if not k or len(k) < 25:
                    continue
                km = k[:-1]  # son kapanmis mumlari al
                if len(km) < 20:
                    continue
                
                # Son kapanmis mum ve onceki mum
                son_mum = km[-1]
                onceki_mum = km[-2]
                
                # Fiyat ve Hacim verileri
                fiyat_simdi = float(son_mum[4])
                fiyat_onceki = float(onceki_mum[4])
                hacim_simdi = float(son_mum[5])
                
                # Fiyat degisimi
                fiyat_degisim = ((fiyat_simdi - fiyat_onceki) / fiyat_onceki) * 100
                
                # Hacim karsilastirmasi: Son 1 mumun hacmi, son 20 mumun ortalama hacminin en az 5 kati mi?
                v_ort = sum(float(x[5]) for x in km[-21:-1]) / 20
                if v_ort == 0:
                    continue
                
                hacim_orani = hacim_simdi / v_ort
                hacim_artis_pct = (hacim_orani - 1) * 100
                
                # KOSULLAR: Hacim normalin EN AZ 5 kati VE fiyat %3+ artmis olmali
                if hacim_orani >= 5 and fiyat_degisim >= 3:
                    if time.time() - son.get(s, 0) >= 1800: # 30 dk cooldown
                        
                        # Mesaj formati (Görseldeki gibi)
                        emoji = "🚀"
                        hacim_artis_miktari = hacim_simdi - v_ort
                        msg = (emoji + " <b>Pump - " + s + " [Binance]</b>\n"
                               "💵 Price: <code>" + format(fiyat_onceki, ".5f") + "</code> ➜ <code>" + format(fiyat_simdi, ".5f") + "</code> (+" + format(fiyat_degisim, ".1f") + "%)\n"
                               "📊 Volume: " + format(hacim_simdi, ".0f") + " (+" + format(hacim_artis_pct, ".0f") + " %)\n"
                               "Volume increased by " + format(hacim_artis_miktari, ".0f") + " ⬆️\n"
                               "Time: " + time.strftime("%I:%M:%S %p", time.localtime()) + " 🕒\n\n"
                               "🔗 <a href='https://www.binance.com/en/futures/" + s + "'>Pair on Binance</a>\n\n"
                               "#" + s.replace("USDT", ""))
                        tg(C, msg)
                        son[s] = time.time()
                        sayac += 1
                        print(s, "PUMP", format(fiyat_degisim, ".1f") + "%", "Hacim:", format(hacim_orani, ".1f") + "x")
            except:
                pass
        print("Tarama bitti - Sinyal:", sayac)
        time.sleep(300)
    except Exception as e:
        print("Hata:", e)
        time.sleep(30)

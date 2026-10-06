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
S = [x["symbol"] for x in gj("https://fapi.binance.com/fapi/v1/exchangeInfo")["symbols"] if x["status"] == "TRADING" and x["quoteAsset"] == "USDT" and x["contractType"] == "PERPETUAL"]
print("COIN:", len(S))
tg(C, "XDECOW TARZI ALARM BOTU AKTIF " + str(len(S)))

son_dump = {}
son_spike = {}

while True:
    try:
        for s in S:
            try:
                # --- 1. PRICE DUMP KONTROLU (1 SAATLIK) ---
                k1h = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=1h&limit=2")
                if k1h and len(k1h) >= 2:
                    m = k1h[-2]  # son kapanmis 1h mum
                    onceki = k1h[-3] if len(k1h) >= 3 else k1h[-2]
                    fiyat = float(m[4])
                    onceki_fiyat = float(onceki[4])
                    degisim = ((fiyat - onceki_fiyat) / onceki_fiyat) * 100
                    hacim = float(m[5])
                    trade = int(m[8])
                    
                    # Fiyat %10'dan fazla dustuyse ve hacim 2x'ten fazlaysa
                    if degisim <= -10:
                        if time.time() - son_dump.get(s, 0) >= 3600:
                            # Ek veriler
                            oi_data = gj("https://fapi.binance.com/futures/data/openInterestHist?symbol=" + s + "&period=1h&limit=2")
                            oi_degisim = 0
                            if oi_data and len(oi_data) >= 2:
                                oi_onceki = float(oi_data[-2]["sumOpenInterestValue"])
                                if oi_onceki > 0:
                                    oi_degisim = ((float(oi_data[-1]["sumOpenInterestValue"]) - oi_onceki) / oi_onceki) * 100
                            
                            lsr_data = gj("https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=" + s + "&period=1h&limit=1")
                            lsr = float(lsr_data[0]["longShortRatio"]) if lsr_data and len(lsr_data) > 0 else 0
                            
                            msg = ("💀 <b>" + s + "</b>\n"
                                   "Price Dump <b>" + format(degisim, ".1f") + "%</b> (1h)\n"
                                   "🕐 " + time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()) + "\n\n"
                                   "ℹ️ 1h changes #" + s + "\n"
                                   "────────────────────\n"
                                   "💵 Price: $" + format(fiyat, ".4f") + " | " + format(degisim, ".1f") + "%\n"
                                   "📊 Vol: $" + format(hacim / 1000000, ".1f") + "M | " + format(degisim, ".1f") + "%\n"
                                   "💰 OI: $" + format(oi_degisim, ".1f") + "%\n"
                                   "⚖️ LSR: " + format(lsr, ".2f") + "\n"
                                   "🤝 Trades: " + format(trade / 1000, ".1f") + "K\n\n"
                                   "📈 View chart\n"
                                   "Link: marketowl.eu")
                            tg(C, msg)
                            son_dump[s] = time.time()
                            print(s, "DUMP", format(degisim, ".1f"))
                
                # --- 2. VOLUME SPIKE KONTROLU (30 DAKIKALIK) ---
                k30m = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=30m&limit=3")
                if k30m and len(k30m) >= 3:
                    m30 = k30m[-2]
                    hacim30 = float(m30[5])
                    ort_hacim = (float(k30m[-4][5]) + float(k30m[-3][5]) + float(k30m[-2][5])) / 3
                    if ort_hacim > 0:
                        hacim_oran = hacim30 / ort_hacim
                        if hacim_oran >= 10:
                            if time.time() - son_spike.get(s, 0) >= 3600:
                                fiyat30 = float(m30[4])
                                degisim30 = ((fiyat30 - float(k30m[-3][4])) / float(k30m[-3][4])) * 100
                                
                                oi_data = gj("https://fapi.binance.com/futures/data/openInterestHist?symbol=" + s + "&period=30m&limit=2")
                                oi_degisim = 0
                                if oi_data and len(oi_data) >= 2:
                                    oi_onceki = float(oi_data[-2]["sumOpenInterestValue"])
                                    if oi_onceki > 0:
                                        oi_degisim = ((float(oi_data[-1]["sumOpenInterestValue"]) - oi_onceki) / oi_onceki) * 100
                                
                                lsr_data = gj("https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=" + s + "&period=30m&limit=1")
                                lsr = float(lsr_data[0]["longShortRatio"]) if lsr_data and len(lsr_data) > 0 else 0
                                
                                msg = ("🔥 <b>" + s + "</b>\n"
                                       "Volume Spike <b>+" + format(hacim_oran, ".1f") + "x</b> (30m)\n"
                                       "🕐 " + time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()) + "\n\n"
                                       "ℹ️ 1h changes #" + s + "\n"
                                       "────────────────────\n"
                                       "💵 Price: $" + format(fiyat30, ".4f") + " | " + format(degisim30, ".1f") + "%\n"
                                       "📊 Vol: $" + format(hacim30 / 1000000, ".1f") + "M | +" + format(hacim_oran * 100, ".0f") + "%\n"
                                       "💰 OI: $" + format(oi_degisim, ".1f") + "%\n"
                                       "⚖️ LSR: " + format(lsr, ".2f") + "\n"
                                       "🤝 Trades: " + format(int(m30[8]) / 1000, ".1f") + "K\n\n"
                                       "📈 View chart\n"
                                       "Link: marketowl.eu")
                                tg(C, msg)
                                son_spike[s] = time.time()
                                print(s, "SPIKE", format(hacim_oran, ".1f"))
            except:
                pass
        print("Tarama bitti")
        time.sleep(300)
    except Exception as e:
        print("Hata:", e)
        time.sleep(30)

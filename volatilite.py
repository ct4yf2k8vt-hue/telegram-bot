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
                # 1. 5 dakikalik mumlari al
                k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=5m&limit=15")
                if not k or len(k) < 12:
                    continue
                km = k[:-1]
                if len(km) < 11:
                    continue
                
                # Son kapanmis mum
                son_mum = km[-1]
                fiyat_onceki = float(son_mum[1]) # Open
                fiyat_simdi = float(son_mum[4])  # Close
                hacim = float(son_mum[5])        # Volume
                trade_sayisi = int(son_mum[8])   # Number of trades
                
                # 2. Hacim patlamasi kontrolu (Onceki 10 mumun ortalamasina gore)
                onceki_10 = [float(x[5]) for x in km[-11:-1]]
                v_ort = sum(onceki_10) / 10
                if v_ort == 0:
                    continue
                
                hacim_orani = hacim / v_ort
                artis_pct = (hacim_orani - 1) * 100
                
                # Kosul: Hacim %5+ artmis VE fiyat artmis (pump)
                fiyat_degisim = ((fiyat_simdi - fiyat_onceki) / fiyat_onceki) * 100
                
                if artis_pct >= 5 and fiyat_degisim > 0:
                    if time.time() - son.get(s, 0) >= 1800:
                        
                        # 3. 24 Saatlik Istatistikler (Binance API)
                        ticker = gj("https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=" + s)
                        if ticker:
                            yuksek24 = float(ticker["highPrice"])
                            dusuk24 = float(ticker["lowPrice"])
                            hacim24 = float(ticker["quoteVolume"])
                            degisim24 = float(ticker["priceChangePercent"])
                        else:
                            yuksek24 = dusuk24 = hacim24 = degisim24 = 0
                        
                        # 4. Acik Pozisyon (OI) ve Long/Short Ratio (LSR)
                        oi_degisim = 0
                        lsr = 0
                        oi_data = gj("https://fapi.binance.com/futures/data/openInterestHist?symbol=" + s + "&period=5m&limit=2")
                        if oi_data and len(oi_data) >= 2:
                            oi_onceki = float(oi_data[-2]["sumOpenInterestValue"])
                            oi_simdi = float(oi_data[-1]["sumOpenInterestValue"])
                            if oi_onceki > 0:
                                oi_degisim = ((oi_simdi - oi_onceki) / oi_onceki) * 100
                        
                        lsr_data = gj("https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=" + s + "&period=5m&limit=1")
                        if lsr_data and len(lsr_data) > 0:
                            lsr = float(lsr_data[0]["longShortRatio"])
                        
                        # 5. Fonlama Orani (Funding Rate)
                        fund_data = gj("https://fapi.binance.com/fapi/v1/premiumIndex?symbol=" + s)
                        fonlama = float(fund_data["lastFundingRate"]) * 100 if fund_data else 0

                        # Mesaj Formati (Gelismis)
                        emoji = "🚀"
                        msg = (emoji + " <b>Pump - " + s + " [Binance]</b>\n"
                               "💵 Price: <code>" + format(fiyat_onceki, ".6f") + "</code> ➜ <code>" + format(fiyat_simdi, ".6f") + "</code> (+" + format(fiyat_degisim, ".2f") + "%)\n"
                               "📊 Volume: " + format(hacim, ".0f") + " (+" + format(artis_pct, ".0f") + " %)\n"
                               "Volume increased by " + format(hacim - (v_ort), ".0f") + " ⬆️\n"
                               "Time: " + time.strftime("%I:%M:%S %p", time.localtime()) + " 🕒\n\n"
                               "ℹ️ <b>Gelismis Metrikler</b>\n"
                               "────────────────────\n"
                               "💰 OI: <code>" + format(oi_degisim, "+.1f") + "%</code>\n"
                               "⚖️ LSR: <code>" + format(lsr, ".2f") + "</code>\n"
                               "🔄 Funding: <code>" + format(fonlama, ".4f") + "%</code>\n"
                               "🤝 Trades: <code>" + format(trade_sayisi / 1000, ".1f") + "K</code>\n\n"
                               "📈 <b>24h Stats:</b> High: <code>" + format(yuksek24, ".6f") + "</code> | Low: <code>" + format(dusuk24, ".6f") + "</code>\n"
                               "Vol: <code>$" + format(hacim24 / 1000000, ".2f") + "M</code> | Chg: <code>" + format(degisim24, "+.2f") + "%</code>\n\n"
                               "🔗 <a href='https://www.binance.com/en/futures/" + s + "'>Pair on Binance</a>\n"
                               "#" + s.replace("USDT", ""))
                        
                        tg(C, msg)
                        son[s] = time.time()
                        sayac += 1
                        print(s, "PUMP", format(fiyat_degisim, ".2f") + "%")
            except:
                pass
        print("Tarama bitti - Sinyal:", sayac)
        time.sleep(300)
    except Exception as e:
        print("Hata:", e)
        time.sleep(30)

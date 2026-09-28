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
tg(C, "XDECOW TARZI DETAYLI BOT AKTIF " + str(len(S)))
son = {}

while True:
    try:
        for s in S:
            try:
                # 1. Son 1 saatlik ve 2 saatlik mum verileri
                k = gj("https://fapi.binance.com/fapi/v1/klines?symbol=" + s + "&interval=1h&limit=3")
                if not k or len(k) < 3:
                    continue
                # Son kapanmis mum (1h)
                m = k[-2]
                fiyat = float(m[4])
                onceki_fiyat = float(m[1])
                degisim = ((fiyat - onceki_fiyat) / onceki_fiyat) * 100
                hacim = float(m[5])
                trade_sayisi = int(m[8])
                # Onceki mum (1h)
                m_onceki = k[-3]
                hacim_onceki = float(m_onceki[5])
                trade_onceki = int(m_onceki[8])
                # Hacim ve trade degisimi
                hacim_degisim = ((hacim - hacim_onceki) / hacim_onceki) * 100 if hacim_onceki > 0 else 0
                trade_degisim = ((trade_sayisi - trade_onceki) / trade_onceki) * 100 if trade_onceki > 0 else 0
                # Trades Spike kontrolu: trade sayisi %200'den fazla artmissa
                if trade_degisim < 200:
                    continue
                # 2. Acik Pozisyon (OI) verisi
                oi_data = gj("https://fapi.binance.com/futures/data/openInterestHist?symbol=" + s + "&period=1h&limit=2")
                oi_degisim = 0
                oi_deger = 0
                if oi_data and len(oi_data) >= 2:
                    oi_deger = float(oi_data[-1]["sumOpenInterestValue"])
                    oi_onceki = float(oi_data[-2]["sumOpenInterestValue"])
                    if oi_onceki > 0:
                        oi_degisim = ((oi_deger - oi_onceki) / oi_onceki) * 100
                # 3. Long/Short Ratio (LSR) verisi
                lsr_data = gj("https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=" + s + "&period=1h&limit=1")
                lsr_deger = 0
                if lsr_data and len(lsr_data) > 0:
                    lsr_deger = float(lsr_data[0]["longShortRatio"])
                # 4. Taker Buy/Sell Volume (Taker Flow) verisi
                taker_data = gj("https://fapi.binance.com/futures/data/takerlongshortRatio?symbol=" + s + "&period=1h&limit=1")
                taker_oran = 0
                if taker_data and len(taker_data) > 0:
                    taker_oran = float(taker_data[0]["buySellRatio"])
                
                if time.time() - son.get(s, 0) >= 3600:
                    emoji = "🚀" if degisim > 0 else "⚠️"
                    msg = (emoji + " <b>" + s + "</b>\n"
                           "🔄 <b>Trades Spike +" + format(trade_degisim, ".1f") + "% (1h)</b>\n"
                           "🕐 " + time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()) + "\n\n"
                           "ℹ️ 1h changes #" + s + "\n"
                           "────────────────────\n"
                           "💵 Price: $" + format(fiyat, ".4f") + " | " + format(degisim, "+.1f") + "%\n"
                           "📊 Vol: $" + format(hacim / 1000000, ".1f") + "M | " + format(hacim_degisim, "+.1f") + "%\n"
                           "💰 OI: $" + format(oi_deger / 1000000, ".1f") + "M | " + format(oi_degisim, "+.1f") + "%\n"
                           "⚖️ LSR: " + format(lsr_deger, ".2f") + " | +0.1\n"
                           "🤝 Trades: " + format(trade_sayisi / 1000, ".1f") + "K | " + format(trade_degisim, "+.1f") + "%\n\n"
                           "📈 View chart\n"
                           "Time: " + time.strftime("%d/%m/%Y %H:%M (UTC)", time.gmtime()) + "\n"
                           "Link: marketowl.eu")
                    tg(C, msg)
                    son[s] = time.time()
                    print(s, "SPIKE", format(trade_degisim, ".1f"))
            except:
                pass
        print("Tarama bitti")
        time.sleep(300)
    except Exception as e:
        print("Hata:", e)
        time.sleep(30)

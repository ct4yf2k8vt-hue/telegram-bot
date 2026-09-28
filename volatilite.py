import os
import time
import requests
from datetime import datetime, timezone

# =========================
# AYARLAR
# =========================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

CHECK_SECONDS = 60

SPOT_EXCHANGE_INFO = "https://api.binance.com/api/v3/exchangeInfo"
SPOT_KLINES = "https://api.binance.com/api/v3/klines"

FUTURES_EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"
FUTURES_KLINES = "https://fapi.binance.com/fapi/v1/klines"


# =========================
# TELEGRAM
# =========================

def send_telegram(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("TELEGRAM_TOKEN veya CHAT_ID eksik")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    try:
        requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": message
            },
            timeout=15
        )
    except Exception as e:
        print("Telegram hatası:", e)


# =========================
# SPOT COINLER
# =========================

def get_spot_symbols():

    try:
        data = requests.get(
            SPOT_EXCHANGE_INFO,
            timeout=20
        ).json()

        symbols = []

        for s in data["symbols"]:

            if (
                s["quoteAsset"] == "USDT"
                and s["status"] == "TRADING"
                and s["isSpotTradingAllowed"]
            ):
                symbols.append(s["symbol"])

        return symbols

    except Exception as e:
        print("Spot liste hatası:", e)
        return []


# =========================
# FUTURES COINLER
# =========================

def get_futures_symbols():

    try:
        data = requests.get(
            FUTURES_EXCHANGE_INFO,
            timeout=20
        ).json()

        symbols = []

        for s in data["symbols"]:

            if (
                s["quoteAsset"] == "USDT"
                and s["status"] == "TRADING"
                and s["contractType"] == "PERPETUAL"
            ):
                symbols.append(s["symbol"])

        return symbols

    except Exception as e:
        print("Futures liste hatası:", e)
        return []


# =========================
# GÜNLÜK MUM VERİSİ
# =========================

def get_daily_klines(symbol, futures=False):

    url = FUTURES_KLINES if futures else SPOT_KLINES

    try:

        response = requests.get(
            url,
            params={
                "symbol": symbol,
                "interval": "1d",
                "limit": 370
            },
            timeout=20
        )

        data = response.json()

        if not isinstance(data, list):
            return []

        return data

    except Exception as e:
        print(symbol, "mum hatası:", e)
        return []


# =========================
# AYLIK / YILLIK ANALİZ
# =========================

def analyze_symbol(symbol, futures=False):

    candles = get_daily_klines(symbol, futures)

    if not candles:
        return None

    now = datetime.now(timezone.utc)

    current_year = now.year
    current_month = now.month

    yearly_high = None
    yearly_low = None

    monthly_high = None
    monthly_low = None

    current_price = float(candles[-1][4])

    for candle in candles:

        candle_time = datetime.fromtimestamp(
            candle[0] / 1000,
            timezone.utc
        )

        high = float(candle[2])
        low = float(candle[3])

        # Takvim yılı
        if candle_time.year == current_year:

            if yearly_high is None or high > yearly_high:
                yearly_high = high

            if yearly_low is None or low < yearly_low:
                yearly_low = low

        # İçinde bulunduğumuz takvim ayı
        if (
            candle_time.year == current_year
            and candle_time.month == current_month
        ):

            if monthly_high is None or high > monthly_high:
                monthly_high = high

            if monthly_low is None or low < monthly_low:
                monthly_low = low

    return {
        "price": current_price,
        "year_high": yearly_high,
        "year_low": yearly_low,
        "month_high": monthly_high,
        "month_low": monthly_low
    }


# =========================
# ANA SİSTEM
# =========================

def run_scan():

    spot = get_spot_symbols()
    futures = get_futures_symbols()

    print(
        "Spot:",
        len(spot),
        "| Futures:",
        len(futures)
    )

    # Şimdilik test amacıyla ilk birkaç coin
    # çalıştırıyoruz.
    test_spot = spot[:5]
    test_futures = futures[:5]

    for symbol in test_spot:

        result = analyze_symbol(
            symbol,
            futures=False
        )

        if result:

            print(
                "SPOT",
                symbol,
                "Fiyat:", result["price"],
                "Aylık High:", result["month_high"],
                "Aylık Low:", result["month_low"],
                "Yıllık High:", result["year_high"],
                "Yıllık Low:", result["year_low"]
            )

    for symbol in test_futures:

        result = analyze_symbol(
            symbol,
            futures=True
        )

        if result:

            print(
                "FUTURES",
                symbol,
                "Fiyat:", result["price"],
                "Aylık High:", result["month_high"],
                "Aylık Low:", result["month_low"],
                "Yıllık High:", result["year_high"],
                "Yıllık Low:", result["year_low"]
            )


# =========================
# BAŞLAT
# =========================

print("================================")
print(" BINANCE SPOT + FUTURES BOT")
print(" Aylık / Yıllık High-Low")
print("================================")

while True:

    try:

        run_scan()

    except Exception as e:

        print("Ana hata:", e)

    time.sleep(CHECK_SECONDS)

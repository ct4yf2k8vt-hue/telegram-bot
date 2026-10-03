import os
import time
import requests
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# AYARLAR
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SCAN_INTERVAL = 60
MAX_CANDIDATES = 40

# Aynı coin tekrar alarm vermeden önce bekleme
ALERT_COOLDOWN = 30 * 60

# Minimum şartlar
MIN_24H_VOLUME_USDT = 500000
MIN_VOLUME_SPIKE = 3.0
MIN_PRICE_CHANGE_1H = 3.0

# ============================================================
# BINANCE
# ============================================================

SPOT_BASE = "https://api.binance.com"
FUTURES_BASE = "https://fapi.binance.com"

session = requests.Session()

last_alert = {}
extreme_cache = {}
extreme_cache_time = {}

# ============================================================
# GENEL REQUEST
# ============================================================

def get(url, params=None):
    try:
        r = session.get(url, params=params, timeout=15)

        if r.status_code != 200:
            return None

        return r.json()

    except Exception:
        return None


# ============================================================
# TELEGRAM
# ============================================================

def telegram(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("TELEGRAM_TOKEN veya CHAT_ID bulunamadı")
        return

    url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"

    try:
        session.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": message,
                "disable_web_page_preview": True
            },
            timeout=15
        )
    except Exception as e:
        print("Telegram:", e)


# ============================================================
# SPOT SEMBOLLER
# ============================================================

def spot_symbols():

    data = get(
        SPOT_BASE + "/api/v3/exchangeInfo"
    )

    if not data:
        return []

    result = []

    for s in data.get("symbols", []):

        if (
            s.get("status") == "TRADING"
            and s.get("quoteAsset") == "USDT"
            and s.get("isSpotTradingAllowed")
        ):
            result.append(s["symbol"])

    return result


# ============================================================
# FUTURES SEMBOLLER
# ============================================================

def futures_symbols():

    data = get(
        FUTURES_BASE + "/fapi/v1/exchangeInfo"
    )

    if not data:
        return []

    result = []

    for s in data.get("symbols", []):

        if (
            s.get("status") == "TRADING"
            and s.get("quoteAsset") == "USDT"
            and s.get("contractType") == "PERPETUAL"
        ):
            result.append(s["symbol"])

    return result


# ============================================================
# 24 SAAT TICKER
# ============================================================

def spot_tickers():

    data = get(
        SPOT_BASE + "/api/v3/ticker/24hr"
    )

    if not data:
        return []

    result = []

    for x in data:

        if x["symbol"].endswith("USDT"):

            try:

                volume = float(x["quoteVolume"])
                change = float(x["priceChangePercent"])
                price = float(x["lastPrice"])

                if volume >= MIN_24H_VOLUME_USDT:

                    result.append({
                        "symbol": x["symbol"],
                        "price": price,
                        "change24": change,
                        "volume24": volume
                    })

            except Exception:
                pass

    return result


def futures_tickers():

    data = get(
        FUTURES_BASE + "/fapi/v1/ticker/24hr"
    )

    if not data:
        return []

    result = []

    for x in data:

        if x["symbol"].endswith("USDT"):

            try:

                volume = float(x["quoteVolume"])
                change = float(x["priceChangePercent"])
                price = float(x["lastPrice"])

                if volume >= MIN_24H_VOLUME_USDT:

                    result.append({
                        "symbol": x["symbol"],
                        "price": price,
                        "change24": change,
                        "volume24": volume
                    })

            except Exception:
                pass

    return result


# ============================================================
# KLINE
# ============================================================

def klines(symbol, interval, futures=False, limit=100):

    base = FUTURES_BASE if futures else SPOT_BASE

    endpoint = (
        "/fapi/v1/klines"
        if futures
        else "/api/v3/klines"
    )

    return get(
        base + endpoint,
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
    ) or []


# ============================================================
# EMA
# ============================================================

def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    result = sum(values[:period]) / period

    for price in values[period:]:

        result = (
            (price - result) * multiplier
            + result
        )

    return result


# ============================================================
# RSI
# ============================================================

def rsi(values, period=14):

    if len(values) <= period:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):

        diff = values[i] - values[i - 1]

        if diff >= 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):

        avg_gain = (
            (avg_gain * (period - 1))
            + gains[i]
        ) / period

        avg_loss = (
            (avg_loss * (period - 1))
            + losses[i]
        ) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


# ============================================================
# AYLIK / YILLIK HIGH LOW
# ============================================================

def monthly_yearly(symbol, futures):

    cache_key = ("F" if futures else "S") + symbol

    now = time.time()

    if (
        cache_key in extreme_cache
        and now - extreme_cache_time.get(cache_key, 0) < 1800
    ):
        return extreme_cache[cache_key]

    data = klines(
        symbol,
        "1d",
        futures,
        370
    )

    if not data:
        return None

    dt_now = datetime.now(timezone.utc)

    month_high = None
    month_low = None

    year_high = None
    year_low = None

    for candle in data:

        try:

            dt = datetime.fromtimestamp(
                candle[0] / 1000,
                timezone.utc
            )

            high = float(candle[2])
            low = float(candle[3])

            if dt.year == dt_now.year:

                if year_high is None or high > year_high:
                    year_high = high

                if year_low is None or low < year_low:
                    year_low = low

            if (
                dt.year == dt_now.year
                and dt.month == dt_now.month
            ):

                if month_high is None or high > month_high:
                    month_high = high

                if month_low is None or low < month_low:
                    month_low = low

        except Exception:
            pass

    result = {
        "month_high": month_high,
        "month_low": month_low,
        "year_high": year_high,
        "year_low": year_low
    }

    extreme_cache[cache_key] = result
    extreme_cache_time[cache_key] = now

    return result


# ============================================================
# OPEN INTEREST
# ============================================================

def open_interest(symbol):

    data = get(
        FUTURES_BASE + "/futures/data/openInterestHist",
        {
            "symbol": symbol,
            "period": "15m",
            "limit": 2
        }
    )

    if not data or len(data) < 2:
        return None

    try:

        old = float(data[-2]["sumOpenInterest"])
        new = float(data[-1]["sumOpenInterest"])

        if old == 0:
            return None

        return ((new - old) / old) * 100

    except Exception:
        return None


# ============================================================
# LONG / SHORT
# ============================================================

def long_short(symbol):

    data = get(
        FUTURES_BASE + "/futures/data/globalLongShortAccountRatio",
        {
            "symbol": symbol,
            "period": "15m",
            "limit": 1
        }
    )

    if not data:
        return None

    try:
        return float(data[-1]["longShortRatio"])
    except Exception:
        return None


# ============================================================
# 15 DAKİKA ANALİZ
# ============================================================

def analyze_15m(symbol, futures):

    data = klines(
        symbol,
        "15m",
        futures,
        100
    )

    if len(data) < 30:
        return None

    closes = [float(x[4]) for x in data]
    volumes = [float(x[5]) for x in data]

    current = closes[-1]

    ema11 = ema(closes, 11)
    ema21 = ema(closes, 21)

    rsi_value = rsi(closes, 14)

    if ema11 is None or ema21 is None:
        return None

    # 1 saat = son 4 adet 15 dk mum
    price_1h = closes[-5]

    if price_1h == 0:
        return None

    change_1h = (
        (current - price_1h)
        / price_1h
    ) * 100

    # Son 30 dakika hacmi
    volume_30m = sum(volumes[-2:])

    # Önceki 30 dakikanın hacmi
    previous_30m = sum(volumes[-4:-2])

    if previous_30m == 0:
        volume_spike = 0
    else:
        volume_spike = (
            volume_30m / previous_30m
        )

    # Hacim ortalaması
    average_volume = (
        sum(volumes[-22:-2]) / 20
    )

    if average_volume == 0:
        volume_percent = 0
    else:
        volume_percent = (
            (volume_30m / 2)
            / average_volume
        ) * 100

    # İşlem sayısı
    trades = [float(x[8]) for x in data]

    current_trades = sum(trades[-2:])
    previous_trades = sum(trades[-4:-2])

    if previous_trades == 0:
        trade_change = 0
    else:
        trade_change = (
            (current_trades - previous_trades)
            / previous_trades
        ) * 100

    return {
        "price": current,
        "ema11": ema11,
        "ema21": ema21,
        "rsi": rsi_value,
        "change1h": change_1h,
        "volume_spike": volume_spike,
        "volume_percent": volume_percent,
        "trade_change": trade_change
    }


# ============================================================
# SKOR
# ============================================================

def calculate_score(data, oi, ls):

    score = 0

    if data["ema11"] > data["ema21"]:
        score += 2

    if data["change1h"] >= 3:
        score += 1

    if data["change1h"] >= 5:
        score += 1

    if data["volume_spike"] >= 3:
        score += 1

    if data["volume_spike"] >= 5:
        score += 1

    if data["rsi"] is not None:

        if 50 <= data["rsi"] <= 70:
            score += 1

    if oi is not None and oi > 3:
        score += 1

    if ls is not None and ls > 1:
        score += 1

    return min(score, 10)


# ============================================================
# TREND
# ============================================================

def trend_text(data):

    if (
        data["ema11"] > data["ema21"]
        and data["change1h"] > 5
    ):
        return "STRONG"

    if data["ema11"] > data["ema21"]:
        return "BULLISH"

    if data["ema11"] < data["ema21"]:
        return "BEARISH"

    return "NEUTRAL"


def volume_text(spike):

    if spike >= 10:
        return "EXTREME"

    if spike >= 5:
        return "VERY HIGH"

    if spike >= 3:
        return "HIGH"

    return "NORMAL"


def oi_text(oi):

    if oi is None:
        return "N/A"

    if oi >= 5:
        return "RISING"

    if oi <= -5:
        return "FALLING"

    return "STABLE"


# ============================================================
# ALARM KONTROL
# ============================================================

def can_alert(key):

    now = time.time()

    previous = last_alert.get(key, 0)

    if now - previous < ALERT_COOLDOWN:
        return False

    last_alert[key] = now

    return True


# ============================================================
# ALARM OLUŞTUR
# ============================================================

def process(symbol, futures):

    data = analyze_15m(symbol, futures)

    if not data:
        return

    # Alarm filtresi
    if data["volume_spike"] < MIN_VOLUME_SPIKE:
        return

    if abs(data["change1h"]) < MIN_PRICE_CHANGE_1H:
        return

    oi = None
    ls = None

    if futures:
        oi = open_interest(symbol)
        ls = long_short(symbol)

    extremes = monthly_yearly(
        symbol,
        futures
    )

    if not extremes:
        return

    score = calculate_score(
        data,
        oi,
        ls
    )

    trend = trend_text(data)
    volume_status = volume_text(
        data["volume_spike"]
    )
    oi_status = oi_text(oi)

    market = "FUTURES" if futures else "SPOT"

    key = market + "_" + symbol

    if not can_alert(key):
        return

    chart = (
        "https://www.tradingview.com/symbols/"
        + symbol.replace("USDT", "")
        + "USDT/"
    )

    message = f"""🚨 COIN ALARM

{"🟢" if data["change1h"] >= 0 else "🔴"} {symbol}
📍 {market}

🔥 Volume Spike: {data["volume_spike"]:.1f}x
⏱ 30m

💰 Price: ${data["price"]:.8g}
📈 1h: {data["change1h"]:+.2f}%

📊 Volume: {data["volume_percent"]:+.1f}%
🤝 Trades: {data["trade_change"]:+.1f}%"""

    if futures:

        oi_text_message = (
            "N/A"
            if oi is None
            else f"{oi:+.2f}%"
        )

        ls_text = (
            "N/A"
            if ls is None
            else f"{ls:.2f}"
        )

        message += f"""

💵 Open Interest: {oi_text_message}
⚖️ Long/Short: {ls_text}"""

    message += f"""

📈 EMA11 {" > " if data["ema11"] > data["ema21"] else " < "} EMA21
📊 RSI: {data["rsi"]:.1f}

📅 Monthly:
High: {extremes["month_high"]:.8g}
Low: {extremes["month_low"]:.8g}

📅 Yearly:
High: {extremes["year_high"]:.8g}
Low: {extremes["year_low"]:.8g}

⭐ SIGNAL SCORE: {score}/10

📈 Trend: {trend}
🔥 Volume: {volume_status}
💵 OI: {oi_status}

📊 View Chart:
{chart}
"""

    telegram(message)

    print(
        datetime.now().strftime("%H:%M:%S"),
        market,
        symbol,
        "SCORE",
        score
    )


# ============================================================
# ADAY SEÇİMİ
# ============================================================

def candidates(tickers):

    # Önce hacim + fiyat hareketine göre sırala
    tickers.sort(
        key=lambda x:
        (
            abs(x["change24"]) *
            (x["volume24"] ** 0.15)
        ),
        reverse=True
    )

    return tickers[:MAX_CANDIDATES]


# ============================================================
# ANA TARAMA
# ============================================================

def scan():

    print(
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Tarama başladı..."
    )

    spot = spot_tickers()
    futures = futures_tickers()

    spot_candidates = candidates(spot)
    futures_candidates = candidates(futures)

    print(
        "Spot:",
        len(spot),
        "| Futures:",
        len(futures),
        "| Derin tarama:",
        len(spot_candidates),
        "+",
        len(futures_candidates)
    )

    jobs = []

    for x in spot_candidates:
        jobs.append(
            (x["symbol"], False)
        )

    for x in futures_candidates:
        jobs.append(
            (x["symbol"], True)
        )

    with ThreadPoolExecutor(max_workers=8) as executor:

        futures_list = [
            executor.submit(
                process,
                symbol,
                is_futures
            )
            for symbol, is_futures in jobs
        ]

        for future in as_completed(futures_list):

            try:
                future.result()
            except Exception as e:
                print("Tarama hatası:", e)


# ============================================================
# BAŞLAT
# ============================================================

print("========================================")
print(" BINANCE COIN ALARM BOT")
print(" SPOT + FUTURES")
print(" MONTHLY + YEARLY HIGH / LOW")
print(" EMA + RSI + VOLUME + OI + LONG/SHORT")
print("========================================")

telegram(
    "🟢 COIN ALARM BOT AKTİF\n\n"
    "Spot + Futures tarama başladı.\n"
    "Aylık/Yıllık High-Low aktif.\n"
    "EMA11/EMA21 + RSI + Volume + OI aktif."
)

while True:

    try:
        scan()

    except Exception as e:
        print("ANA HATA:", e)

    time.sleep(SCAN_INTERVAL)

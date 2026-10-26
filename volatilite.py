
import os
import time
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta
from collections import defaultdict, deque

# ============================================================
# BINANCE FUTURES PUMP & DUMP TRACKER
# GitHub + Render + Telegram
# USD-M PERPETUAL FUTURES
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

BINANCE_BASE = os.getenv(
    "BINANCE_BASE",
    "https://fapi.binance.com"
).rstrip("/")

CHECK_SECONDS = int(
    os.getenv("CHECK_SECONDS", "10")
)

WINDOW_SECONDS = int(
    os.getenv("WINDOW_SECONDS", "300")
)

PUMP_PERCENT = float(
    os.getenv("PUMP_PERCENT", "3.0")
)

DUMP_PERCENT = float(
    os.getenv("DUMP_PERCENT", "3.0")
)

VOLUME_MULTIPLIER = float(
    os.getenv("VOLUME_MULTIPLIER", "2.0")
)

COOLDOWN_SECONDS = int(
    os.getenv("COOLDOWN_SECONDS", "600")
)

MIN_24H_VOLUME = float(
    os.getenv("MIN_24H_VOLUME", "1000000")
)

MAX_SYMBOLS = int(
    os.getenv("MAX_SYMBOLS", "100")
)

TURKEY_TZ = timezone(
    timedelta(hours=3)
)

# ============================================================
# BOT VERİLERİ
# ============================================================

price_history = defaultdict(deque)

last_alert = {}

kline_cache = {}

symbol_list = set()

last_symbol_refresh = 0

# ============================================================
# LOG
# ============================================================

def log(message):

    now = datetime.now(
        TURKEY_TZ
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    print(
        f"[{now} TR] {message}",
        flush=True
    )


# ============================================================
# HTTP JSON
# ============================================================

def http_json(url, timeout=20):

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "FuturesPumpTracker/1.0",
            "Accept": "application/json"
        }
    )

    with urllib.request.urlopen(
        req,
        timeout=timeout
    ) as response:

        return json.loads(
            response.read().decode("utf-8")
        )


# ============================================================
# TELEGRAM
# ============================================================

def telegram_request(
    method,
    fields=None,
    photo_path=None
):

    if not BOT_TOKEN or not CHAT_ID:

        log(
            "BOT_TOKEN veya CHAT_ID eksik."
        )

        return None

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/{method}"
    )

    fields = fields or {}

    if photo_path:

        boundary = (
            "----FuturesBoundary"
            + str(int(time.time() * 1000))
        )

        body = bytearray()

        for key, value in fields.items():

            body.extend(
                f"--{boundary}\r\n".encode()
            )

            body.extend(
                (
                    f'Content-Disposition: form-data; '
                    f'name="{key}"\r\n\r\n'
                ).encode()
            )

            body.extend(
                str(value).encode("utf-8")
            )

            body.extend(b"\r\n")

        with open(
            photo_path,
            "rb"
        ) as f:

            photo = f.read()

        body.extend(
            f"--{boundary}\r\n".encode()
        )

        body.extend(
            (
                'Content-Disposition: form-data; '
                'name="photo"; filename="futures_chart.png"\r\n'
            ).encode()
        )

        body.extend(
            b"Content-Type: image/png\r\n\r\n"
        )

        body.extend(photo)

        body.extend(b"\r\n")

        body.extend(
            f"--{boundary}--\r\n".encode()
        )

        req = urllib.request.Request(
            url,
            data=bytes(body),
            headers={
                "Content-Type":
                f"multipart/form-data; boundary={boundary}"
            },
            method="POST"
        )

    else:

        data = urllib.parse.urlencode(
            fields
        ).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type":
                "application/x-www-form-urlencoded"
            },
            method="POST"
        )

    try:

        with urllib.request.urlopen(
            req,
            timeout=30
        ) as response:

            result = json.loads(
                response.read().decode("utf-8")
            )

        if not result.get("ok"):

            log(
                f"Telegram hata: {result}"
            )

        return result

    except Exception as exc:

        log(
            f"Telegram gönderim hatası: {exc}"
        )

        return None


# ============================================================
# TELEGRAM MESAJ
# ============================================================

def send_text(text):

    return telegram_request(
        "sendMessage",
        {
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true"
        }
    )


# ============================================================
# BINANCE FUTURES PARİTELERİ
# ============================================================

def get_futures_symbols():

    global symbol_list
    global last_symbol_refresh

    now = time.time()

    if (
        symbol_list
        and now - last_symbol_refresh < 3600
    ):

        return symbol_list

    exchange = http_json(
        f"{BINANCE_BASE}/fapi/v1/exchangeInfo"
    )

    symbols = set()

    for item in exchange.get(
        "symbols",
        []
    ):

        if (
            item.get("status") == "TRADING"
            and item.get("contractType") == "PERPETUAL"
            and item.get("quoteAsset") == "USDT"
        ):

            symbol = item.get(
                "symbol",
                ""
            )

            if symbol.endswith("USDT"):

                symbols.add(symbol)

    symbol_list = symbols

    last_symbol_refresh = now

    log(
        f"Futures pariteleri yenilendi: "
        f"{len(symbol_list)}"
    )

    return symbol_list


# ============================================================
# FUTURES 24 SAAT TICKER
# ============================================================

def get_futures_tickers():

    symbols = get_futures_symbols()

    tickers = http_json(
        f"{BINANCE_BASE}/fapi/v1/ticker/24hr"
    )

    selected = []

    for ticker in tickers:

        symbol = ticker.get(
            "symbol"
        )

        if symbol not in symbols:

            continue

        try:

            price = float(
                ticker.get(
                    "lastPrice",
                    0
                )
            )

            quote_volume = float(
                ticker.get(
                    "quoteVolume",
                0)
            )

            change_24h = float(
                ticker.get(
                    "priceChangePercent",
                0)
            )

            high_24h = float(
                ticker.get(
                    "highPrice",
                0)
            )

            low_24h = float(
                ticker.get(
                    "lowPrice",
                0)
            )

        except (
            TypeError,
            ValueError
        ):

            continue

        if (
            price <= 0
            or quote_volume < MIN_24H_VOLUME
        ):

            continue

        selected.append(
            {
                "symbol": symbol,
                "price": price,
                "quote_volume": quote_volume,
                "change_24h": change_24h,
                "high_24h": high_24h,
                "low_24h": low_24h
            }
        )

    selected.sort(
        key=lambda x: x["quote_volume"],
        reverse=True
    )

    if MAX_SYMBOLS > 0:

        selected = selected[
            :MAX_SYMBOLS
        ]

    return selected


# ============================================================
# FUTURES 5 DAKİKALIK MUM
#
# Binance Futures kline:
# [0] open time
# [1] open
# [2] high
# [3] low
# [4] close
# [5] base volume
# [6] close time
# [7] quote volume USDT
# [9] taker buy base volume
# [10] taker buy quote volume USDT
# ============================================================

def get_5m_data(symbol):

    now = time.time()

    cached = kline_cache.get(
        symbol
    )

    # Her pariteye 60 saniyede bir mum isteği
    if (
        cached
        and now - cached["time"] < 60
    ):

        return cached["data"]

    url = (
        f"{BINANCE_BASE}/fapi/v1/klines"
        f"?symbol={urllib.parse.quote(symbol)}"
        f"&interval=5m"
        f"&limit=20"
    )

    candles = http_json(
        url
    )

    if len(candles) < 4:

        return None

    # Son mum açık olabilir.
    # -2 son kapanmış mum
    # -3 önceki kapanmış mum

    current = candles[-2]

    previous = candles[-3]

    current_volume = float(
        current[7]
    )

    previous_volume = float(
        previous[7]
    )

    current_taker_buy = float(
        current[10]
    )

    current_taker_sell = (
        current_volume
        - current_taker_buy
    )

    if previous_volume > 0:

        volume_multiplier = (
            current_volume
            / previous_volume
        )

    else:

        volume_multiplier = 0

    if current_volume > 0:

        taker_buy_percent = (
            current_taker_buy
            / current_volume
            * 100
        )

    else:

        taker_buy_percent = 0

    data = {

        "current_volume": current_volume,

        "previous_volume": previous_volume,

        "volume_multiplier": volume_multiplier,

        "volume_change": (
            (volume_multiplier - 1)
            * 100
        ),

        "taker_buy": current_taker_buy,

        "taker_sell": current_taker_sell,

        "taker_buy_percent": taker_buy_percent,

        "taker_sell_percent": (
            100 - taker_buy_percent
        ),

        "closes": [
            float(row[4])
            for row in candles[:-1]
        ],

        "candles": candles[:-1]
    }

    kline_cache[
        symbol
    ] = {

        "time": now,

        "data": data
    }

    return data


# ============================================================
# RSI HESAPLAMA
# ============================================================

def calculate_rsi(
    prices,
    period=14
):

    if len(prices) < period + 1:

        return None

    gains = []

    losses = []

    for i in range(1, len(prices)):

        change = (
            prices[i]
            - prices[i - 1]
        )

        if change > 0:

            gains.append(change)

            losses.append(0)

        else:

            gains.append(0)

            losses.append(
                abs(change)
            )

    recent_gains = gains[-period:]

    recent_losses = losses[-period:]

    average_gain = (
        sum(recent_gains)
        / period
    )

    average_loss = (
        sum(recent_losses)
        / period
    )

    if average_loss == 0:

        return 100.0

    rs = (
        average_gain
        / average_loss
    )

    return (
        100
        - (
            100
            / (1 + rs)
        )
    )


# ============================================================
# FİYAT GEÇMİŞİ
# ============================================================

def clean_history(
    symbol,
    now
):

    history = price_history[
        symbol
    ]

    cutoff = (
        now
        - WINDOW_SECONDS
        - 30
    )

    while (
        history
        and history[0][0] < cutoff
    ):

        history.popleft()


# ============================================================
# REFERANS FİYAT
# ============================================================

def get_reference_price(
    symbol,
    now
):

    history = price_history[
        symbol
    ]

    if not history:

        return None

    target = (
        now
        - WINDOW_SECONDS
    )

    candidates = [
        item
        for item in history
        if item[0] <= target
    ]

    if candidates:

        return candidates[-1][1]

    return history[0][1]


# ============================================================
# GRAFİK
# ============================================================

def make_chart(
    symbol,
    direction,
    current_price
):

    try:

        import matplotlib

        matplotlib.use(
            "Agg"
        )

        import matplotlib.pyplot as plt

        import matplotlib.dates as mdates

        candles = http_json(
            (
                f"{BINANCE_BASE}/fapi/v1/klines"
                f"?symbol={urllib.parse.quote(symbol)}"
                f"&interval=1m"
                f"&limit=30"
            )
        )

        if not candles:

            return None

        times = [
            datetime.fromtimestamp(
                row[0] / 1000,
                TURKEY_TZ
            )
            for row in candles
        ]

        opens = [
            float(row[1])
            for row in candles
        ]

        highs = [
            float(row[2])
            for row in candles
        ]

        lows = [
            float(row[3])
            for row in candles
        ]

        closes = [
            float(row[4])
            for row in candles
        ]

        volumes = [
            float(row[5])
            for row in candles
        ]

        fig, (
            ax_price,
            ax_volume
        ) = plt.subplots(
            2,
            1,
            figsize=(10, 6),
            sharex=True,
            gridspec_kw={
                "height_ratios": [3, 1]
            }
        )

        fig.patch.set_facecolor(
            "#10131c"
        )

        for ax in (
            ax_price,
            ax_volume
        ):

            ax.set_facecolor(
                "#171b26"
            )

            ax.tick_params(
                colors="#d8dce6",
                labelsize=8
            )

            ax.grid(
                True,
                alpha=0.18,
                color="#9aa4b2"
            )

            for spine in ax.spines.values():

                spine.set_color(
                    "#444b5c"
                )

        for i in range(
            len(candles)
        ):

            if closes[i] >= opens[i]:

                color = "#24c6a4"

            else:

                color = "#ff667a"

            ax_price.vlines(
                times[i],
                lows[i],
                highs[i],
                color=color,
                linewidth=1
            )

            ax_price.vlines(
                times[i],
                min(
                    opens[i],
                    closes[i]
                ),
                max(
                    opens[i],
                    closes[i]
                ),
                color=color,
                linewidth=5
            )

            ax_volume.bar(
                times[i],
                volumes[i],
                width=0.0006,
                color=color,
                alpha=0.75
            )

        ax_price.axhline(
            current_price,
            color="#f2c14e",
            linestyle="--",
            linewidth=1
        )

        ax_price.set_title(
            f"{symbol} M1 Binance Futures",
            color="white",
            fontsize=12
        )

        ax_price.set_ylabel(
            "Price",
            color="#d8dce6"
        )

        ax_volume.set_ylabel(
            "Volume",
            color="#d8dce6"
        )

        ax_volume.xaxis.set_major_formatter(
            mdates.DateFormatter(
                "%H:%M",
                tz=TURKEY_TZ
            )
        )

        fig.autofmt_xdate()

        plt.tight_layout()

        path = (
            f"/tmp/{symbol}_"
            f"{int(time.time())}.png"
        )

        fig.savefig(
            path,
            dpi=130,
            facecolor=fig.get_facecolor(),
            bbox_inches="tight"
        )

        plt.close(fig)

        return path

    except Exception as exc:

        log(
            f"Grafik hatası {symbol}: {exc}"
        )

        return None


# ============================================================
# FİYAT FORMAT
# ============================================================

def fmt_price(value):

    if value >= 1000:

        return f"{value:,.2f}"

    if value >= 1:

        return (
            f"{value:.4f}"
            .rstrip("0")
            .rstrip(".")
        )

    return (
        f"{value:.8f}"
        .rstrip("0")
        .rstrip(".")
    )


# ============================================================
# FUTURES ALARM
# ============================================================

def send_alert(
    ticker,
    direction,
    reference,
    current_price,
    change_pct,
    data
):

    symbol = ticker["symbol"]

    if direction == "PUMP":

        arrow = "🚀"

        emoji = "🟢"

    else:

        arrow = "🔻"

        emoji = "🔴"

    volume_multiplier = data[
        "volume_multiplier"
    ]

    volume_change = data[
        "volume_change"
    ]

    current_volume = data[
        "current_volume"
    ]

    previous_volume = data[
        "previous_volume"
    ]

    taker_buy_percent = data[
        "taker_buy_percent"
    ]

    taker_sell_percent = data[
        "taker_sell_percent"
    ]

    rsi = calculate_rsi(
        data["closes"]
    )

    if rsi is None:

        rsi_text = "Hesaplanamadı"

    else:

        rsi_text = f"{rsi:.1f}"

    text = (

        f"{arrow} "
        f"<b>#{symbol} {emoji}</b>\n"

        f"<b>Price {change_pct:+.2f}% "
        f"Vol ×{volume_multiplier:.2f}</b>\n"

        f"(M5 Futures)\n\n"

        f"💰 Price: "
        f"<b>{fmt_price(current_price)}</b>\n"

        f"📊 Total Vol: "
        f"<b>{current_volume:,.0f} USDT</b>\n"

        f"📈 Previous Vol: "
        f"{previous_volume:,.0f} USDT\n"

        f"📊 Volume Change: "
        f"<b>{volume_change:+.2f}%</b>\n\n"

        f"🟢 Taker Buy: "
        f"<b>{taker_buy_percent:.1f}%</b>\n"

        f"🔴 Taker Sell: "
        f"<b>{taker_sell_percent:.1f}%</b>\n"

        f"📉 RSI: "
        f"<b>{rsi_text}</b>\n\n"

        f"💵 24h: "
        f"<b>{ticker['change_24h']:+.2f}%</b>\n"

        f"⬆️ High: "
        f"{fmt_price(ticker['high_24h'])}\n"

        f"⬇️ Low: "
        f"{fmt_price(ticker['low_24h'])}\n\n"

        f"⏱ Price Window: 5m\n"

        f"🕒 "
        f"{datetime.now(TURKEY_TZ).strftime('%d.%m.%Y %H:%M:%S')} "
        f"TR"
    )

    chart_path = make_chart(
        symbol,
        direction,
        current_price
    )

    if chart_path:

        result = telegram_request(
            "sendPhoto",
            {
                "chat_id": CHAT_ID,
                "caption": text,
                "parse_mode": "HTML"
            },
            photo_path=chart_path
        )

        try:

            os.remove(
                chart_path
            )

        except OSError:

            pass

        return result

    return send_text(
        text
    )


# ============================================================
# ANA BOT
# ============================================================

def run():

    send_text(
        "🤖 <b>Binance Futures Tracker Aktif</b>\n\n"

        "📈 Pump ve Dump takip\n"

        "⏱ Kontrol: "
        f"{CHECK_SECONDS} saniye\n"

        "🪟 Fiyat: son 5 dakika\n"

        "📊 Hacim: 5m mum karşılaştırması\n"

        "💹 Taker Buy / Sell\n"

        "📉 RSI 14\n"

        "📊 24h High / Low\n"

        "📷 Grafik: M1 Futures\n\n"

        f"🚀 Pump: +{PUMP_PERCENT}%\n"

        f"🔻 Dump: -{DUMP_PERCENT}%\n"

        f"📊 Hacim: ×{VOLUME_MULTIPLIER}\n"

        f"🔁 Cooldown: "
        f"{COOLDOWN_SECONDS // 60} dakika"
    )

    while True:

        started = time.time()

        try:

            now = time.time()

            tickers = get_futures_tickers()

            alert_count = 0

            for ticker in tickers:

                symbol = ticker[
                    "symbol"
                ]

                price = ticker[
                    "price"
                ]

                history = price_history[
                    symbol
                ]

                history.append(
                    (
                        now,
                        price
                    )
                )

                clean_history(
                    symbol,
                    now
                )

                reference = get_reference_price(
                    symbol,
                    now
                )

                if (
                    reference is None
                    or reference <= 0
                ):

                    continue

                change_pct = (
                    (
                        price
                        - reference
                    )
                    / reference
                    * 100
                )

                direction = None

                if change_pct >= PUMP_PERCENT:

                    direction = "PUMP"

                elif change_pct <= -DUMP_PERCENT:

                    direction = "DUMP"

                if not direction:

                    continue

                if (
                    now
                    - last_alert.get(
                        symbol,
                        0
                    )
                    < COOLDOWN_SECONDS
                ):

                    continue

                try:

                    data = get_5m_data(
                        symbol
                    )

                except Exception as exc:

                    log(
                        f"5m veri hatası "
                        f"{symbol}: {exc}"
                    )

                    continue

                if not data:

                    continue

                volume_multiplier = data[
                    "volume_multiplier"
                ]

                volume_ok = (
                    volume_multiplier
                    >= VOLUME_MULTIPLIER
                )

                strong_move = (
                    abs(change_pct)
                    >= PUMP_PERCENT * 1.5
                )

                if (
                    not volume_ok
                    and not strong_move
                ):

                    continue

                log(
                    f"{direction} {symbol} "
                    f"| Price {change_pct:+.2f}% "
                    f"| Volume ×{volume_multiplier:.2f}"
                )

                send_alert(
                    ticker,
                direction,
                    reference,
                price,
                change_pct,
                data
                )

                last_alert[
                    symbol
                ] = now

                alert_count += 1

            log(
                f"Futures tarama: "
                f"{len(tickers)} parite "
                f"| {alert_count} alarm"
            )

        except urllib.error.HTTPError as exc:

            log(
                f"Binance HTTP: "
                f"{exc.code} {exc.reason}"
            )

        except Exception as exc:

            log(
                f"Bot hata: {exc}"
            )

        elapsed = (
            time.time()
            - started
        )

        time.sleep(
            max(
                1,
                CHECK_SECONDS - elapsed
            )
        )


# ============================================================
# BAŞLAT
# ============================================================

if __name__ == "__main__":

    run()

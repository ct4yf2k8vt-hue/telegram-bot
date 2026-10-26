
import os
import time
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta
from collections import defaultdict, deque

# ============================================================
# BINANCE PUMP & DUMP TELEGRAM BOT
# GitHub + Render
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

BINANCE_BASE = os.getenv(
    "BINANCE_BASE",
    "https://api.binance.com"
).rstrip("/")

CHECK_SECONDS = int(os.getenv("CHECK_SECONDS", "10"))
WINDOW_SECONDS = int(os.getenv("WINDOW_SECONDS", "300"))

PUMP_PERCENT = float(os.getenv("PUMP_PERCENT", "5.0"))
DUMP_PERCENT = float(os.getenv("DUMP_PERCENT", "5.0"))

VOLUME_INCREASE_PERCENT = float(
    os.getenv("VOLUME_INCREASE_PERCENT", "20")
)

COOLDOWN_SECONDS = int(
    os.getenv("COOLDOWN_SECONDS", "600")
)

MIN_QUOTE_VOLUME_USDT = float(
    os.getenv("MIN_QUOTE_VOLUME_USDT", "500000")
)

MAX_SYMBOLS = int(
    os.getenv("MAX_SYMBOLS", "0")
)

TURKEY_TZ = timezone(
    timedelta(hours=3)
)

# ============================================================
# BOT VERİLERİ
# ============================================================

price_history = defaultdict(deque)

last_alert = {}

last_volume = {}

volume_cache = {}

# ============================================================
# LOG
# ============================================================

def log(message):

    now = datetime.now(
        TURKEY_TZ
    ).strftime("%Y-%m-%d %H:%M:%S")

    print(
        f"[{now} TR] {message}",
        flush=True
    )


# ============================================================
# HTTP JSON
# ============================================================

def http_json(url, timeout=15):

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "PumpDumpTelegramBot/2.0",
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
            "----PumpDumpBoundary"
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
                'name="photo"; filename="chart.png"\r\n'
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
            timeout=25
        ) as response:

            result = json.loads(
                response.read().decode("utf-8")
            )

        if not result.get("ok"):

            log(
                f"Telegram API hata: {result}"
            )

        return result

    except Exception as exc:

        log(
            f"Telegram gönderim hatası: {exc}"
        )

        return None


# ============================================================
# TELEGRAM YAZI
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
# BINANCE PARİTELERİ
# ============================================================

def get_symbols_and_tickers():

    exchange = http_json(
        f"{BINANCE_BASE}/api/v3/exchangeInfo"
    )

    eligible = set()

    for item in exchange.get(
        "symbols",
        []
    ):

        if (
            item.get("status") == "TRADING"
            and item.get("quoteAsset") == "USDT"
            and item.get(
                "isSpotTradingAllowed",
                True
            )
        ):

            symbol = item.get(
                "symbol",
                ""
            )

            if not any(
                symbol.endswith(x)
                for x in (
                    "UPUSDT",
                    "DOWNUSDT",
                    "BULLUSDT",
                    "BEARUSDT"
                )
            ):

                eligible.add(symbol)

    tickers = http_json(
        f"{BINANCE_BASE}/api/v3/ticker/24hr"
    )

    selected = []

    for ticker in tickers:

        symbol = ticker.get(
            "symbol"
        )

        if symbol not in eligible:

            continue

        try:

            quote_volume = float(
                ticker.get(
                    "quoteVolume",
                    0
                )
            )

            price = float(
                ticker.get(
                    "lastPrice",
                    0
                )
            )

        except (
            TypeError,
            ValueError
        ):

            continue

        if (
            price <= 0
            or quote_volume < MIN_QUOTE_VOLUME_USDT
        ):

            continue

        selected.append(
            {
                "symbol": symbol,
                "price": price,
                "quote_volume": quote_volume
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
# GERÇEK 5 DAKİKALIK HACİM
# Binance kline:
# row[5] = base asset volume
# row[7] = quote asset volume (USDT)
#
# Son kapanmış 5m mum ile önceki kapanmış 5m mum
# karşılaştırılır.
# ============================================================

def get_volume_data(symbol):

    now = time.time()

    cached = volume_cache.get(
        symbol
    )

    # Aynı parite için 60 saniyede bir hacim sorgusu
    if (
        cached
        and now - cached["time"] < 60
    ):

        return (
            cached["current"],
            cached["previous"],
            cached["change"]
        )

    url = (
        f"{BINANCE_BASE}/api/v3/klines"
        f"?symbol={urllib.parse.quote(symbol)}"
        f"&interval=5m"
        f"&limit=3"
    )

    candles = http_json(
        url
    )

    if len(candles) < 3:

        return None, None, None

    # Son mum henüz kapanmamış olabilir.
    # Bu yüzden sondan ikinci ve üçüncü mum alınır.

    current_candle = candles[-2]

    previous_candle = candles[-3]

    current_volume = float(
        current_candle[7]
    )

    previous_volume = float(
        previous_candle[7]
    )

    if previous_volume > 0:

        volume_change = (
            (
                current_volume
                - previous_volume
            )
            / previous_volume
            * 100
        )

    else:

        volume_change = 0

    volume_cache[
        symbol
    ] = {

        "time": now,

        "current": current_volume,

        "previous": previous_volume,

        "change": volume_change
    }

    return (
        current_volume,
        previous_volume,
        volume_change
    )


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

        url = (
            f"{BINANCE_BASE}/api/v3/klines"
            f"?symbol={urllib.parse.quote(symbol)}"
            f"&interval=1m"
            f"&limit=30"
        )

        raw = http_json(
            url
        )

        if not raw:

            return None

        times = [
            datetime.fromtimestamp(
                row[0] / 1000,
                TURKEY_TZ
            )
            for row in raw
        ]

        closes = [
            float(row[4])
            for row in raw
        ]

        volumes = [
            float(row[5])
            for row in raw
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

        if direction == "PUMP":

            color = "#24c6a4"

        else:

            color = "#ff667a"

        ax_price.plot(
            times,
            closes,
            color=color,
            linewidth=2
        )

        ax_price.axhline(
            current_price,
            color="#f2c14e",
            linestyle="--",
            linewidth=1
        )

        ax_price.set_title(
            (
                f"{symbol} | {direction} | "
                f"${current_price:g}"
            ),
            color="white",
            fontsize=12
        )

        ax_price.set_ylabel(
            "Price",
            color="#d8dce6"
        )

        ax_volume.bar(
            times,
            volumes,
            width=0.0006,
            color=color,
            alpha=0.75
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
# ALARM MESAJI
# ============================================================

def send_alert(
    symbol,
    direction,
    start_price,
    current_price,
    change_pct,
    volume_current,
    volume_previous,
    volume_change
):

    if direction == "PUMP":

        arrow = "🚀"

        color = "🟢🟢"

        title = "Pump Activity"

    else:

        arrow = "🔻"

        color = "🔴🔴"

        title = "Dump Activity"

    volume_text = (
        f"📊 5m Hacim: "
        f"<b>${volume_previous:,.2f}"
        f" → ${volume_current:,.2f}</b>\n"
    )

    volume_text += (
        f"📈 Hacim değişimi: "
        f"<b>{volume_change:+.2f}%</b>\n"
    )

    volume_text += (
        f"💵 Hacim farkı: "
        f"<b>${volume_current - volume_previous:+,.2f}</b>"
    )

    text = (

        f"{arrow} "
        f"<b>{direction} - {symbol} [Binance]</b>\n"

        f"{title} on {symbol} "
        f"{color}\n\n"

        f"💰 Fiyat: "
        f"<b>${fmt_price(start_price)} "
        f"→ ${fmt_price(current_price)} "
        f"({change_pct:+.2f}%)</b>\n\n"

        f"{volume_text}\n\n"

        f"⏱ Fiyat hareketi: "
        f"son {WINDOW_SECONDS // 60} dakika\n"

        f"🕒 "
        f"{datetime.now(TURKEY_TZ).strftime('%d.%m.%Y %H:%M:%S')} "
        f"(Türkiye)"
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

    if not BOT_TOKEN or not CHAT_ID:

        log(
            "BOT_TOKEN ve CHAT_ID Render "
            "Environment Variables içinde olmalı."
        )

    send_text(
        "🤖 <b>Pump & Dump Bot Aktif</b>\n\n"

        f"⏱ Kontrol: {CHECK_SECONDS} saniye\n"

        f"📈 Pump: +{PUMP_PERCENT:.1f}%\n"

        f"📉 Dump: -{DUMP_PERCENT:.1f}%\n"

        f"🪟 Fiyat penceresi: "
        f"{WINDOW_SECONDS // 60} dakika\n"

        f"📊 Hacim: gerçek 5 dakikalık mum\n"

        f"🔁 Alarm bekleme: "
        f"{COOLDOWN_SECONDS // 60} dakika"
    )

    while True:

        loop_started = time.time()

        try:

            now = time.time()

            tickers = get_symbols_and_tickers()

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

                # Alarm cooldown kontrolü
                if (
                    now
                    - last_alert.get(
                        symbol,
                        0
                    )
                    < COOLDOWN_SECONDS
                ):

                    continue

                # Gerçek 5 dakikalık hacim
                try:

                    (
                        volume_current,
                        volume_previous,
                        volume_change
                    ) = get_volume_data(
                        symbol
                    )

                except Exception as exc:

                    log(
                        f"Hacim alınamadı "
                        f"{symbol}: {exc}"
                    )

                    continue

                if (
                    volume_current is None
                    or volume_previous is None
                    or volume_change is None
                ):

                    continue

                # Hacim artış koşulu
                volume_ok = (
                    volume_change
                    >= VOLUME_INCREASE_PERCENT
                )

                # Büyük fiyat hareketinde hacim şartı esnetilir
                strong_move = (
                    abs(change_pct)
                    >= PUMP_PERCENT * 1.5
                )

                if not volume_ok and not strong_move:

                    continue

                log(
                    f"{direction} {symbol} "
                    f"| Fiyat: {change_pct:+.2f}% "
                    f"| 5m Hacim: "
                    f"{volume_change:+.2f}%"
                )

                send_alert(
                    symbol,
                direction,
                    reference,
                    price,
                    change_pct,
                    volume_current,
                    volume_previous,
                    volume_change
                )

                last_alert[
                    symbol
                ] = now

                alert_count += 1

            log(
                f"Tarama tamamlandı: "
                f"{len(tickers)} parite "
                f"| {alert_count} alarm"
            )

        except urllib.error.HTTPError as exc:

            log(
                f"Binance HTTP hata: "
                f"{exc.code} {exc.reason}"
            )

        except Exception as exc:

            log(
                f"Tarama hatası: {exc}"
            )

        elapsed = (
            time.time()
            - loop_started
        )

        time.sleep(
            max(
                1,
                CHECK_SECONDS - elapsed
            )
        )


# ============================================================
# BOT BAŞLAT
# ============================================================

if __name__ == "__main__":

    run()

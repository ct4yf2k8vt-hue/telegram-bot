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

WINDOW_SECONDS = int(
    os.getenv("WINDOW_SECONDS", "300")
)

PUMP_PERCENT = float(
    os.getenv("PUMP_PERCENT", "5.0")
)

DUMP_PERCENT = float(
    os.getenv("DUMP_PERCENT", "5.0")
)

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
# BINANCE VERİ ÇEKME
# ============================================================

def http_json(url, timeout=15):

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "PumpDumpTelegramBot/1.0",
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
# TELEGRAM MESAJ GÖNDERME
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
# TELEGRAM YAZI MESAJI
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
# BINANCE USDT PARİTELERİ
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
                0)
            )

            price = float(
                ticker.get(
                    "lastPrice",
                0)
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
                "quote_volume": quote_volume,
                "change_24h": float(
                    ticker.get(
                        "priceChangePercent",
                        0
                    )
                )
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
# ESKİ FİYATLARI TEMİZLE
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
# GRAFİK OLUŞTURMA
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

        raw = http_json(
            (
                f"{BINANCE_BASE}/api/v3/klines"
                f"?symbol={urllib.parse.quote(symbol)}"
                f"&interval=1m"
                f"&limit=30"
            )
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

            line_color = "#24c6a4"

        else:

            line_color = "#ff667a"

        ax_price.plot(
            times,
            closes,
            color=line_color,
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
                f"Current: {current_price:g}"
            ),
            color="white",
            fontsize=12,
            pad=10
        )

        ax_price.set_ylabel(
            "Price",
            color="#d8dce6"
        )

        ax_volume.bar(
            times,
            volumes,
            width=0.0006,
            color=line_color,
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
            f"Grafik oluşturulamadı "
            f"({symbol}): {exc}"
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
# PUMP / DUMP TELEGRAM ALARMI
# ============================================================

def send_alert(
    symbol,
    direction,
    start_price,
    current_price,
    change_pct,
    quote_volume,
    volume_change
):

    if direction == "PUMP":

        arrow = "🚀"

        title = "Pump Activity"

        color_emoji = "🟢🟢"

    else:

        arrow = "🔻"

        title = "Dump Activity"

        color_emoji = "🔴🔴"

    volume_line = (
        f"📊 24s Hacim: "
        f"<b>${quote_volume:,.0f}</b>"
    )

    if volume_change is not None:

        volume_line += (
            f" ({volume_change:+.2f}%)"
        )

    text = (

        f"{arrow} "
        f"<b>{direction} - {symbol} [Binance]</b>\n"

        f"{title} on {symbol} "
        f"{color_emoji}\n\n"

        f"💰 Fiyat: "
        f"<b>${fmt_price(start_price)} "
        f"→ ${fmt_price(current_price)} "
        f"({change_pct:+.2f}%)</b>\n"

        f"{volume_line}\n"

        f"⏱ Hareket: son "
        f"{WINDOW_SECONDS // 60} dakika\n"

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
# BOT ÇALIŞTIRMA
# ============================================================

def run():

    if not BOT_TOKEN or not CHAT_ID:

        log(
            "UYARI: BOT_TOKEN ve CHAT_ID "
            "Render Environment Variables "
            "içinde tanımlanmalı."
        )

    send_text(
        "🤖 <b>Pump & Dump Alert Bot aktif</b>\n"

        f"⏱ Kontrol: {CHECK_SECONDS} sn\n"

        f"📈 Pump: +{PUMP_PERCENT:.1f}% "
        f"/ 📉 Dump: -{DUMP_PERCENT:.1f}%\n"

        f"🪟 Pencere: "
        f"{WINDOW_SECONDS // 60} dk"
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

                quote_volume = ticker[
                    "quote_volume"
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

                    last_volume[
                        symbol
                    ] = quote_volume

                    continue

                change_pct = (
                    (price - reference)
                    / reference
                    * 100.0
                )

                previous_volume = last_volume.get(
                    symbol
                )

                volume_change = None

                if (
                    previous_volume
                    and previous_volume > 0
                ):

                    volume_change = (
                        (
                            quote_volume
                            - previous_volume
                        )
                        / previous_volume
                        * 100.0
                    )

                direction = None

                if change_pct >= PUMP_PERCENT:

                    direction = "PUMP"

                elif change_pct <= -DUMP_PERCENT:

                    direction = "DUMP"

                cooldown_ok = (
                    now
                    - last_alert.get(
                        symbol,
                        0
                    )
                    >= COOLDOWN_SECONDS
                )

                volume_ok = (

                    volume_change is None

                    or volume_change
                    >= VOLUME_INCREASE_PERCENT

                    or abs(change_pct)
                    >= (
                        PUMP_PERCENT
                        * 1.5
                    )
                )

                if (
                    direction
                    and cooldown_ok
                    and volume_ok
                ):

                    log(
                        f"{direction} "
                        f"{symbol}: "
                        f"{change_pct:+.2f}%"
                    )

                    send_alert(
                        symbol,
                        direction,
                        reference,
                        price,
                        change_pct,
                        quote_volume,
                        volume_change
                    )

                    last_alert[
                        symbol
                    ] = now

                    alert_count += 1

                last_volume[
                    symbol
            ] = quote_volume

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
# BAŞLAT
# ============================================================

if __name__ == "__main__":

    run()

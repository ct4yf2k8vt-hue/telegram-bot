
import os
import time
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

# BINANCE USDT-M FUTURES SHORT SIGNAL BOT

BASE = "https://fapi.binance.com"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "1623907197").strip()

TR = timezone(timedelta(hours=3))

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "180"))
MAX_CANDIDATES = int(os.getenv("MAX_CANDIDATES", "12"))
MIN_QUOTE_VOLUME = float(os.getenv("MIN_QUOTE_VOLUME_USDT", "5000000"))
MIN_24H_PUMP = float(os.getenv("MIN_24H_PUMP_PERCENT", "3"))
SYMBOL_COOLDOWN = int(os.getenv("SYMBOL_COOLDOWN_SECONDS", "1800"))
REQUEST_GAP = float(os.getenv("REQUEST_GAP_SECONDS", "2.0"))

binance_block_until = 0.0
last_request_at = 0.0
last_signal = {}


def log(msg):
    print(
        f"[{datetime.now(TR).strftime('%Y-%m-%d %H:%M:%S TR')}] "
        f"{msg}",
        flush=True
    )


def api(path, params=None):

    global binance_block_until, last_request_at

    now = time.time()

    if now < binance_block_until:
        return None

    wait = REQUEST_GAP - (now - last_request_at)

    if wait > 0:
        time.sleep(wait)

    url = BASE + path

    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "futures-signal-scanner/1.0"
        }
    )

    last_request_at = time.time()

    try:

        with urllib.request.urlopen(req, timeout=15) as r:

            return json.loads(
                r.read().decode("utf-8")
            )

    except urllib.error.HTTPError as e:

        if e.code in (418, 429):

            retry_after = e.headers.get("Retry-After")

            try:

                seconds = (
                    max(60, int(retry_after))
                    if retry_after
                    else 900
                )

            except ValueError:

                seconds = 900

            binance_block_until = time.time() + seconds

            log(
                f"Binance HTTP {e.code}; "
                f"{seconds} saniye bekleme. "
                f"Yeni istek gönderilmeyecek."
            )

        else:

            log(f"Binance HTTP {e.code}")

        return None

    except Exception as e:

        log(f"Binance bağlantı hatası: {e}")

        return None


def telegram(msg):

    if not BOT_TOKEN or not CHAT_ID:

        log("Telegram ayarı eksik: BOT_TOKEN veya CHAT_ID.")

        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/sendMessage"
    )

    data = urllib.parse.urlencode({

        "chat_id": CHAT_ID,
        "text": msg,
        "disable_web_page_preview": "true"

    }).encode()

    try:

        req = urllib.request.Request(
            url,
            data=data,
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=15) as r:

            result = json.loads(
                r.read().decode()
            )

        if result.get("ok"):

            log("Telegram sinyali gönderildi.")

            return True

        log(f"Telegram API hatası: {result}")

    except Exception as e:

        log(f"Telegram gönderim hatası: {e}")

    return False


def futures_symbols():

    info = api("/fapi/v1/exchangeInfo")

    if not info:
        return None

    allowed = set()

    for s in info.get("symbols", []):

        if (
            s.get("status") == "TRADING"
            and s.get("contractType") == "PERPETUAL"
            and s.get("quoteAsset") == "USDT"
        ):

            allowed.add(s.get("symbol"))

    return allowed


def market_candidates(allowed):

    data = api("/fapi/v1/ticker/24hr")

    if not data:
        return None

    found = []

    for x in data:

        symbol = x.get("symbol")

        if symbol not in allowed:
            continue

        try:

            change = float(x["priceChangePercent"])

            volume = float(x["quoteVolume"])

            price = float(x["lastPrice"])

            if price <= 0:
                continue

            if volume < MIN_QUOTE_VOLUME:
                continue

            if change < MIN_24H_PUMP:
                continue

            found.append({

                "symbol": symbol,
                "price": price,
                "change": change,
                "volume": volume

            })

        except (KeyError, ValueError, TypeError):

            continue

    found.sort(
        key=lambda a: (a["change"], a["volume"]),
        reverse=True
    )

    log(
        f"USDT-M Futures adayları: {len(found)} "
        f"| analiz edilecek: "
        f"{min(len(found), MAX_CANDIDATES)}"
    )

    return found[:MAX_CANDIDATES]


def rsi(vals, period=14):

    if len(vals) <= period:
        return None

    changes = [
        vals[i] - vals[i - 1]
        for i in range(1, len(vals))
    ]

    gains = [
        max(v, 0)
        for v in changes[-period:]
    ]

    losses = [
        max(-v, 0)
        for v in changes[-period:]
    ]

    ag = sum(gains) / period

    al = sum(losses) / period

    if al == 0:
        return 100.0

    return 100 - 100 / (1 + ag / al)


def ema(vals, period):

    if len(vals) < period:
        return None

    out = sum(vals[:period]) / period

    k = 2 / (period + 1)

    for v in vals[period:]:

        out = v * k + out * (1 - k)

    return out


def analyze(item):

    symbol = item["symbol"]

    klines = api(
        "/fapi/v1/klines",
        {
            "symbol": symbol,
            "interval": "1m",
            "limit": 30
        }
    )

    if not klines or len(klines) < 25:
        return None

    # Son eleman açık mumdur, analizden çıkar.
    candles = klines[:-1]

    closes = [
        float(c[4])
        for c in candles
    ]

    opens = [
        float(c[1])
        for c in candles
    ]

    highs = [
        float(c[2])
        for c in candles
    ]

    lows = [
        float(c[3])
        for c in candles
    ]

    vols = [
        float(c[5])
        for c in candles
    ]

    price = closes[-1]

    open1 = opens[-1]

    high1 = highs[-1]

    r = rsi(closes)

    e11 = ema(closes, 11)

    e21 = ema(closes, 21)

    if (
        r is None
        or e11 is None
        or e21 is None
        or price <= 0
    ):

        return None

    avg_vol = (
        sum(vols[-21:-1])
        / max(1, len(vols[-21:-1]))
    )

    vol_ratio = (
        vols[-1] / avg_vol
        if avg_vol
        else 0
    )

    change5 = (
        price / closes[-6] - 1
    ) * 100

    change15 = (
        price / closes[-16] - 1
    ) * 100

    support = min(lows[-6:-1])

    score = 0

    reasons = []

    if r >= 60:

        score += 1

        reasons.append(
            f"RSI {r:.1f}"
        )

    if vol_ratio >= 1.5:

        score += 1

        reasons.append(
            f"Hacim {vol_ratio:.1f}x"
        )

    if e11 >= e21:

        score += 1

        reasons.append(
            "EMA11 ≥ EMA21"
        )

    if change5 >= 0.5:

        score += 1

        reasons.append(
            f"5dk yükseliş %{change5:.2f}"
        )

    if price < open1:

        score += 1

        reasons.append(
            "Son kapanan mum kırmızı"
        )

    if price < support:

        score += 1

        reasons.append(
            "Önceki kısa destek kırıldı"
        )

    # Minimum teyit ve kırmızı mum.
    if score < 3 or price >= open1:
        return None

    now = time.time()

    if (
        now - last_signal.get(symbol, 0)
        < SYMBOL_COOLDOWN
    ):

        return None

    entry = price

    stop = high1 * 1.002

    risk = stop - entry

    if (
        risk <= 0
        or entry - risk <= 0
        or entry - 2 * risk <= 0
    ):

        return None

    last_signal[symbol] = now

    tp1 = entry - risk

    tp2 = entry - 2 * risk

    message = (

        f"🔻 SHORT ADAYI — USDT-M FUTURES\n"
        f"#{symbol} · Binance Futures\n"
        f"⭐ Sinyal skoru: {score}/6\n\n"

        f"🏷 Giriş referansı: {entry:.8f}\n"
        f"🛑 Stop-loss: {stop:.8f}\n"
        f"🎯 TP1 (1R): {tp1:.8f}\n"
        f"🎯 TP2 (2R): {tp2:.8f}\n\n"

        f"⏱ Son kapanmış mumlar\n"
        f"• 1dk RSI: {r:.1f}\n"
        f"• 5dk hareket: {change5:+.2f}%\n"
        f"• 15dk hareket: {change15:+.2f}%\n"

        f"📉 EMA11: {e11:.8f}\n"
        f"📉 EMA21: {e21:.8f}\n"

        f"📊 Hacim: {vol_ratio:.1f}x ortalama\n"

        f"📈 24s değişim: "
        f"{item['change']:+.2f}%\n"

        f"💹 24s hacim: "
        f"{item['volume'] / 1_000_000:.2f}M USDT\n\n"

        f"🔎 Teyitler\n"

        + "\n".join(
            "• " + x
            for x in reasons
        )

        + f"\n\n🔗 https://www.binance.com/en/futures/{symbol}\n"

        + datetime.now(TR).strftime(
            "🕒 %Y-%m-%d %H:%M:%S TR"
        )
    )

    return message


def main():

    log(
        "USDT-M Futures SHORT tarayıcı "
        "başlatıldı (sinyal-only)."
    )

    if not BOT_TOKEN or not CHAT_ID:

        log(
            "UYARI: BOT_TOKEN veya CHAT_ID eksik; "
            "tarama yapılabilir ama Telegram gönderimi yapılamaz."
        )

    allowed = None

    last_symbols_refresh = 0

    while True:

        started = time.time()

        if started < binance_block_until:

            log(
                f"Binance bekleme süresi: "
                f"{int(binance_block_until - started)} "
                f"saniye; tarama duraklatıldı."
            )

        else:

            if (
                allowed is None
                or started - last_symbols_refresh >= 21600
            ):

                allowed = futures_symbols()

                if allowed:

                    last_symbols_refresh = started

                    log(
                        f"USDT-M perpetual pariteleri yüklendi: "
                        f"{len(allowed)}"
                    )

            if allowed:

                candidates = market_candidates(allowed)

                if candidates is None:

                    log(
                        "24 saatlik Futures listesi alınamadı."
                    )

                else:

                    checked = 0

                    alarms = 0

                    for item in candidates:

                        if time.time() < binance_block_until:

                            break

                        try:

                            msg = analyze(item)

                            checked += 1

                            if msg and telegram(msg):

                                alarms += 1

                                log(
                                    f"SHORT sinyali gönderildi: "
                                    f"{item['symbol']}"
                                )

                        except Exception as e:

                            log(
                                f"{item['symbol']} analiz hatası: {e}"
                            )

                    log(
                        f"USDT-M Futures tarama tamamlandı: "
                        f"{checked} aday | {alarms} alarm"
                    )

            else:

                log(
                    "Futures parite listesi alınamadı; "
                    "sonraki turda tekrar denenecek."
                )

        elapsed = time.time() - started

        sleep_for = max(
            10,
            SCAN_INTERVAL - elapsed
        )

        log(
            f"Sonraki Futures taraması "
            f"{int(sleep_for)} saniye sonra."
        )

        time.sleep(sleep_for)


if __name__ == "__main__":

    main()

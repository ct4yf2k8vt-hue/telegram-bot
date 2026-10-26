
import os
import time
import json
import urllib.request
import urllib.parse
import urllib.error
import threading
from datetime import datetime, timezone, timedelta

# =====================================================
# BINANCE FUTURES SHORT BOT
# =====================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "1623907197").strip()

BASE = "https://fapi.binance.com"

TR = timezone(timedelta(hours=3))

SCAN_INTERVAL = 180
TOP_SYMBOLS = 100
CANDLE_SYMBOLS = 20

MIN_VOLUME = 5_000_000
MIN_PUMP = 3.0

SIGNAL_COOLDOWN = 1800

BINANCE_COOLDOWN = 0

LAST_SIGNAL = {}
LAST_SCAN = 0

# =====================================================
# LOG
# =====================================================

def log(message):
    now = datetime.now(TR).strftime("%Y-%m-%d %H:%M:%S TR")
    print(f"[{now}] {message}", flush=True)


# =====================================================
# HTTP
# =====================================================

def request(url, timeout=15):

    global BINANCE_COOLDOWN

    if time.time() < BINANCE_COOLDOWN:
        return None

    try:

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json"
            }
        )

        with urllib.request.urlopen(req, timeout=timeout) as response:

            return json.loads(
                response.read().decode("utf-8")
            )

    except urllib.error.HTTPError as e:

        if e.code in (418, 429):

            wait = 3600

            try:
                retry = e.headers.get("Retry-After")

                if retry:
                    wait = max(int(retry), 60)

            except Exception:
                pass

            BINANCE_COOLDOWN = time.time() + wait

            log(
                f"Binance HTTP {e.code}: "
                f"{wait} saniye bekleme."
            )

        else:

            log(f"Binance HTTP: {e.code}")

        return None

    except Exception as e:

        log(f"Binance bağlantı hatası: {e}")

        return None


# =====================================================
# TELEGRAM
# =====================================================

def telegram(message):

    if not BOT_TOKEN or not CHAT_ID:

        log("BOT_TOKEN veya CHAT_ID eksik.")

        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/sendMessage"
    )

    data = urllib.parse.urlencode({

        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True

    }).encode()

    try:

        req = urllib.request.Request(
            url,
            data=data,
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=15) as response:

            result = json.loads(
                response.read().decode("utf-8")
            )

            if result.get("ok"):

                log("Telegram mesajı gönderildi.")

                return True

            log(f"Telegram hata: {result}")

            return False

    except Exception as e:

        log(f"Telegram gönderim hatası: {e}")

        return False


# =====================================================
# FUTURES PARITIES
# =====================================================

def get_symbols():

    data = request(
        BASE + "/fapi/v1/ticker/24hr"
    )

    if not data:

        return []

    symbols = []

    for item in data:

        symbol = item.get("symbol", "")

        if not symbol.endswith("USDT"):
            continue

        if "_" in symbol:
            continue

        try:

            price = float(item["lastPrice"])

            volume = float(item["quoteVolume"])

            change = float(item["priceChangePercent"])

            if price <= 0:
                continue

            if volume < MIN_VOLUME:
                continue

            if change < MIN_PUMP:
                continue

            symbols.append({

                "symbol": symbol,
                "price": price,
                "volume": volume,
                "change": change

            })

        except Exception:

            continue

    symbols.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    log(
        f"Futures pariteleri yenilendi: {len(data)}"
    )

    return symbols[:TOP_SYMBOLS]


# =====================================================
# KLINES
# =====================================================

def get_klines(symbol):

    url = (
        BASE
        + "/fapi/v1/klines?"
        + urllib.parse.urlencode({

            "symbol": symbol,
            "interval": "1m",
            "limit": 35

        })
    )

    return request(url)


# =====================================================
# EMA
# =====================================================

def ema(values, period):

    if len(values) < period:

        return None

    result = sum(values[:period]) / period

    multiplier = 2 / (period + 1)

    for value in values[period:]:

        result = (
            value * multiplier
            + result * (1 - multiplier)
        )

    return result


# =====================================================
# RSI
# =====================================================

def rsi(values, period=14):

    if len(values) <= period:

        return None

    gains = []
    losses = []

    for i in range(1, len(values)):

        change = values[i] - values[i - 1]

        gains.append(max(change, 0))

        losses.append(max(-change, 0))

    avg_gain = sum(gains[-period:]) / period

    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:

        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


# =====================================================
# SIGNAL
# =====================================================

def analyze(symbol_data):

    symbol = symbol_data["symbol"]

    candles = get_klines(symbol)

    if not candles:

        return None

    if len(candles) < 25:

        return None

    # Son kapanmış mumlar
    closed = candles[:-1]

    closes = [
        float(c[4])
        for c in closed
    ]

    highs = [
        float(c[2])
        for c in closed
    ]

    lows = [
        float(c[3])
        for c in closed
    ]

    volumes = [
        float(c[5])
        for c in closed
    ]

    last = closed[-1]

    previous = closed[-2]

    price = float(last[4])

    open_price = float(last[1])

    high = float(last[2])

    low = float(last[3])

    previous_close = float(previous[4])

    if price <= 0:

        return None

    change_5 = (
        (price - closes[-6])
        / closes[-6]
    ) * 100

    change_15 = (
        (price - closes[-16])
        / closes[-16]
    ) * 100

    rsi_value = rsi(closes)

    ema11 = ema(closes, 11)

    ema21 = ema(closes, 21)

    if rsi_value is None:

        return None

    if ema11 is None or ema21 is None:

        return None

    average_volume = (
        sum(volumes[-21:-1]) / 20
    )

    current_volume = volumes[-1]

    volume_ratio = (
        current_volume / average_volume
        if average_volume > 0
        else 0
    )

    # Önceki mumların en düşük seviyesi
    support = min(lows[-6:-1])

    # =================================================
    # SHORT CONDITIONS
    # =================================================

    score = 0

    reasons = []

    # RSI yüksek
    if rsi_value >= 60:

        score += 1

        reasons.append(
            f"RSI: {rsi_value:.1f}"
        )

    # Hacim artışı
    if volume_ratio >= 1.5:

        score += 1

        reasons.append(
            f"Hacim: {volume_ratio:.1f}x"
        )

    # EMA yukarı yönlü ama fiyat dönüş yapıyor
    if ema11 >= ema21:

        score += 1

        reasons.append(
            "EMA11 >= EMA21"
        )

    # Önceki hareket yükseliş
    if change_5 >= 0.5:

        score += 1

        reasons.append(
            f"5dk hareket: %{change_5:.2f}"
        )

    # Kırmızı mum
    if price < open_price:

        score += 1

        reasons.append(
            "Kırmızı mum"
        )

    # Destek kırılımı
    if price < support:

        score += 1

        reasons.append(
            "Destek kırılımı"
        )

    # Minimum teyit
    if score < 3:

        return None

    # Son mum yükseliyorsa SHORT verme
    if price > open_price:

        return None

    # =================================================
    # ENTRY / STOP / TP
    # =================================================

    entry = price

    stop = high * 1.002

    risk = stop - entry

    if risk <= 0:

        return None

    tp1 = entry - risk

    tp2 = entry - (risk * 2)

    if tp1 <= 0 or tp2 <= 0:

        return None

    # =================================================
    # COOLDOWN
    # =================================================

    now = time.time()

    last_signal = LAST_SIGNAL.get(symbol, 0)

    if now - last_signal < SIGNAL_COOLDOWN:

        return None

    LAST_SIGNAL[symbol] = now

    # =================================================
    # MESSAGE
    # =================================================

    message = (

        "🔻 <b>SHORT ADAYI — CANLI TARAMA</b>\n"

        f"#{symbol} · Binance USDT-M Futures\n"

        f"⭐ Sinyal skoru: {score}/6\n\n"

        f"🏷 Giriş referansı: {entry:.8f}\n"

        f"🛑 Stop-loss: {stop:.8f}\n"

        f"🎯 TP1 (1R): {tp1:.8f}\n"

        f"🎯 TP2 (2R): {tp2:.8f}\n\n"

        "⏱ Zaman dilimleri\n"

        f"• 1dk: {change_5:.2f}%\n"

        f"• 5dk: {change_5:.2f}%\n"

        f"• 15dk: {change_15:.2f}%\n\n"

        f"📉 EMA11: {ema11:.8f}\n"

        f"📉 EMA21: {ema21:.8f}\n"

        f"📊 RSI(1dk): {rsi_value:.1f}\n"

        f"📊 Hacim: {volume_ratio:.1f}x\n\n"

        f"📈 24s değişim: "
        f"+{symbol_data['change']:.2f}%\n"

        f"💹 24s hacim: "
        f"{symbol_data['volume']/1000000:.2f}M USDT\n\n"

        "🔎 Dönüş teyitleri\n"

        + "\n".join(
            f"• {reason}"
            for reason in reasons
        )

        + "\n\n"

        + "🔗 Binance Futures paritesi\n"

        + f"https://www.binance.com/en/futures/{symbol}\n\n"

        + datetime.now(TR).strftime(
            "🕒 %Y-%m-%d %H:%M:%S"
        )

    )

    return message


# =====================================================
# MAIN SCANNER
# =====================================================

def scan():

    global LAST_SCAN

    if time.time() < BINANCE_COOLDOWN:

        remaining = int(
            BINANCE_COOLDOWN - time.time()
        )

        log(
            f"Binance bekleme süresi: "
            f"{remaining} saniye; tarama duraklatıldı."
        )

        return

    symbols = get_symbols()

    if not symbols:

        log("Parite listesi alınamadı.")

        return

    candidates = symbols[:CANDLE_SYMBOLS]

    alarms = 0

    for item in candidates:

        if time.time() < BINANCE_COOLDOWN:

            log("Binance beklemesi başladı; tarama durdu.")

            break

        try:

            message = analyze(item)

            if message:

                if telegram(message):

                    alarms += 1

                    log(
                        f"SHORT alarm gönderildi: "
                        f"{item['symbol']}"
                    )

        except Exception as e:

            log(
                f"{item['symbol']} analiz hatası: {e}"
            )

        # İstekleri kontrollü aralıkla gönder
        time.sleep(1.2)

    LAST_SCAN = time.time()

    log(
        f"Futures tarama: {len(candidates)} parite "
        f"| {alarms} alarm"
    )


# =====================================================
# RENDER HEALTH SERVER
# =====================================================

def health_server():

    from http.server import (
        BaseHTTPRequestHandler,
        HTTPServer
    )

    class Handler(BaseHTTPRequestHandler):

        def do_GET(self):

            body = b"Futures bot running"

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "text/plain"
            )

            self.send_header(
                "Content-Length",
                str(len(body))
            )

            self.end_headers()

            self.wfile.write(body)

        def log_message(self, *args):

            pass

    port = int(
        os.environ.get("PORT", "10000")
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        Handler
    )

    log(
        f"Health server port: {port}"
    )

    server.serve_forever()


# =====================================================
# START
# =====================================================

def main():

    log("Binance Futures SHORT bot başlatılıyor.")

    if not BOT_TOKEN:

        log("BOT_TOKEN eksik.")

    if not CHAT_ID:

        log("CHAT_ID eksik.")

    threading.Thread(
        target=health_server,
        daemon=True
    ).start()

    while True:

        try:

            scan()

        except Exception as e:

            log(f"Tarama hatası: {e}")

        log(
            f"Sonraki tarama "
            f"{SCAN_INTERVAL} saniye sonra."
        )

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":

    main()

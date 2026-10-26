
import os
import json
import time
import logging
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta, timezone

# =========================================================
# BINANCE USDT-M FUTURES - SHORT DONUS SINYAL BOTU
# Gercek emir acmaz. Yalnizca Telegram alarmi gonderir.
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "1623907197").strip()

BASE = "https://fapi.binance.com"
TR = timezone(timedelta(hours=3))

SCAN_SECONDS = 180
REQUEST_GAP = 0.15
MIN_24H_VOLUME = 5_000_000
MIN_24H_CHANGE = 5.0
MAX_CANDIDATES = 8
MIN_SCORE = 4
CONFIRMED_SCORE = 5
SYMBOL_COOLDOWN = 1800

STATE_FILE = "short_bot_state.json"
COOLDOWN_FILE = "binance_cooldown.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("shortbot")

last_request = 0.0
binance_pause_until = 0.0
last_signal = {}
daily_cache = {}
daily_cache_time = {}


def now_tr():
    return datetime.now(TR)


def stamp():
    return now_tr().strftime("%Y-%m-%d %H:%M:%S")


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as exc:
        log.warning("Dosya kaydedilemedi %s: %s", path, exc)


def load_state():
    global last_signal, binance_pause_until
    state = load_json(STATE_FILE, {})
    last_signal = state.get("last_signal", {})
    cooldown = load_json(COOLDOWN_FILE, {})
    binance_pause_until = float(cooldown.get("until", 0))


def save_state():
    save_json(STATE_FILE, {"last_signal": last_signal})


def telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        log.error("BOT_TOKEN veya CHAT_ID eksik.")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true"
    }).encode()

    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode())
            if result.get("ok"):
                return True
            log.warning("Telegram yaniti: %s", result)
    except Exception as exc:
        log.warning("Telegram hatasi: %s", exc)

    return False


def api(path, params=None):
    global last_request, binance_pause_until

    if time.time() < binance_pause_until:
        raise RuntimeError("Binance bekleme suresi devam ediyor")

    wait = REQUEST_GAP - (time.time() - last_request)
    if wait > 0:
        time.sleep(wait)

    query = urllib.parse.urlencode(params or {})
    url = BASE + path + (("?" + query) if query else "")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ShortSignalBot/1.0"}
    )

    last_request = time.time()

    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode())

    except urllib.error.HTTPError as exc:
        if exc.code in (418, 429):
            retry = exc.headers.get("Retry-After")
            try:
                pause = max(60, int(retry))
            except (TypeError, ValueError):
                pause = 900

            binance_pause_until = time.time() + pause
            save_json(COOLDOWN_FILE, {"until": binance_pause_until})
            log.error(
                "Binance HTTP %s: %s saniye bekleme.",
                exc.code, pause
            )
        raise


def number(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def fmt(value, digits=6):
    if value is None:
        return "N/A"
    return f"{value:.{digits}f}"


def pct(value):
    return "N/A" if value is None else f"{value:+.2f}%"


def rsi(values, period=14):
    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(len(values) - period, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    return 100 - (100 / (1 + avg_gain / avg_loss))


def ema(values, period):
    if len(values) < period:
        return None

    result = sum(values[:period]) / period
    multiplier = 2 / (period + 1)

    for value in values[period:]:
        result = (value - result) * multiplier + result

    return result


def get_closed_candles(symbol, interval, limit=100):
    data = api("/fapi/v1/klines", {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    })

    if len(data) < 3:
        return []

    # Son mum halen acik olabilir; yalnizca kapanmis mumlari kullan.
    now_ms = int(time.time() * 1000)
    closed = [k for k in data if int(k[6]) < now_ms]
    return closed


def daily_high_low(symbol):
    # Her parite icin gunluk veriyi 12 saatte bir yenile.
    now = time.time()
    cached = daily_cache.get(symbol)
    cached_at = daily_cache_time.get(symbol, 0)

    if cached and now - cached_at < 43200:
        return cached

    try:
        candles = api("/fapi/v1/klines", {
            "symbol": symbol,
            "interval": "1d",
            "limit": 366
        })

        now_ms = int(time.time() * 1000)
        candles = [k for k in candles if int(k[6]) < now_ms]

        if not candles:
            return cached or (None, None, None, None)

        last30 = candles[-30:]
        high30 = max(float(k[2]) for k in last30)
        low30 = min(float(k[3]) for k in last30)

        high365 = max(float(k[2]) for k in candles[-365:])
        low365 = min(float(k[3]) for k in candles[-365:])

        result = (high30, low30, high365, low365)
        daily_cache[symbol] = result
        daily_cache_time[symbol] = now
        return result

    except Exception as exc:
        log.warning("%s gunluk veri alinamadi: %s", symbol, exc)
        return cached or (None, None, None, None)


def open_interest_change(symbol):
    try:
        data = api("/futures/data/openInterestHist", {
            "symbol": symbol,
            "period": "5m",
            "limit": 2
        })

        if len(data) < 2:
            return None

        old = float(data[-2]["sumOpenInterest"])
        new = float(data[-1]["sumOpenInterest"])

        if old == 0:
            return None

        return (new - old) / old * 100

    except Exception as exc:
        log.warning("%s OI verisi: %s", symbol, exc)
        return None


def long_short_ratio(symbol):
    try:
        data = api(
            "/futures/data/globalLongShortAccountRatio",
            {
                "symbol": symbol,
                "period": "5m",
                "limit": 1
            }
        )

        if not data:
            return None

        return float(data[-1]["longShortRatio"])

    except Exception as exc:
        log.warning("%s Long/Short verisi: %s", symbol, exc)
        return None


def get_market_candidates():
    info = api("/fapi/v1/exchangeInfo")
    active = {
        item["symbol"]
        for item in info["symbols"]
        if item.get("status") == "TRADING"
        and item.get("contractType") == "PERPETUAL"
        and item.get("quoteAsset") == "USDT"
    }

    tickers = api("/fapi/v1/ticker/24hr")
    candidates = []

    for item in tickers:
        symbol = item.get("symbol")
        if symbol not in active:
            continue

        volume = number(item.get("quoteVolume"), 0)
        change = number(item.get("priceChangePercent"), 0)

        if volume < MIN_24H_VOLUME or change < MIN_24H_CHANGE:
            continue

        candidates.append({
            "symbol": symbol,
            "volume": volume,
            "change": change,
            "last": number(item.get("lastPrice")),
        })

    candidates.sort(
        key=lambda x: (x["change"], x["volume"]),
        reverse=True
    )

    return candidates[:MAX_CANDIDATES]


def analyse(item):
    symbol = item["symbol"]

    one = get_closed_candles(symbol, "1m", 60)
    five = get_closed_candles(symbol, "5m", 60)
    fifteen = get_closed_candles(symbol, "15m", 40)

    if len(one) < 25 or len(five) < 10 or len(fifteen) < 5:
        log.info("%s mum verisi yetersiz.", symbol)
        return None

    closes = [float(k[4]) for k in one]
    highs = [float(k[2]) for k in one]
    lows = [float(k[3]) for k in one]
    volumes = [float(k[5]) for k in one]

    closes5 = [float(k[4]) for k in five]
    closes15 = [float(k[4]) for k in fifteen]

    entry = closes[-1]
    previous_move = (
        (closes[-2] / closes[-7] - 1) * 100
        if closes[-7] else None
    )
    change1 = (
        (closes[-1] / closes[-2] - 1) * 100
        if closes[-2] else None
    )
    change5 = (
        (closes5[-1] / closes5[-2] - 1) * 100
        if closes5[-2] else None
    )
    change15 = (
        (closes15[-1] / closes15[-2] - 1) * 100
        if closes15[-2] else None
    )

    e11 = ema(closes, 11)
    e21 = ema(closes, 21)
    rsi_now = rsi(closes)
    rsi_before = rsi(closes[:-1])

    avg_volume = sum(volumes[-21:-1]) / 20
    volume_ratio = volumes[-1] / avg_volume if avg_volume else None

    red_candle = closes[-1] < float(one[-1][1])
    support = min(lows[-7:-1])
    support_break = closes[-1] < support

    # RSI'nin onceki degeriyle karsilastirilmasi gerekir.
    rsi_falling = (
        rsi_now is not None
        and rsi_before is not None
        and rsi_now < rsi_before
        and rsi_now >= 55
    )

    ema_bearish = (
        e11 is not None and e21 is not None and e11 < e21
    )

    price_below_ema = e11 is not None and entry < e11

    checks = [
        previous_move is not None and previous_move >= 1.0,
        red_candle,
        support_break,
        rsi_falling,
        volume_ratio is not None and volume_ratio >= 1.5,
        ema_bearish or price_below_ema
    ]

    score = sum(bool(x) for x in checks)

    # Dusus teyidi yoksa sirf yukselmis diye SHORT gonderme.
    if score < MIN_SCORE:
        return None

    if not support_break or not red_candle:
        return None

    stop_base = max(highs[-6:])
    stop = stop_base * 1.001

    if stop <= entry:
        return None

    risk = stop - entry
    tp1 = entry - risk
    tp2 = entry - 2 * risk

    high30, low30, high365, low365 = daily_high_low(symbol)
    oi_change = open_interest_change(symbol)
    ls_ratio = long_short_ratio(symbol)

    confirmed = score >= CONFIRMED_SCORE

    return {
        "symbol": symbol,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "risk_pct": (stop / entry - 1) * 100,
        "score": score,
        "confirmed": confirmed,
        "change1": change1,
        "change5": change5,
        "change15": change15,
        "ema11": e11,
        "ema21": e21,
        "rsi": rsi_now,
        "volume_ratio": volume_ratio,
        "change24": item["change"],
        "volume24": item["volume"],
        "oi": oi_change,
        "ls": ls_ratio,
        "high30": high30,
        "low30": low30,
        "high365": high365,
        "low365": low365,
        "previous_move": previous_move,
        "support_break": support_break,
    }


def signal_message(s):
    symbol = s["symbol"]
    title = (
        "🔻 SHORT ADAYI — TEYİTLİ DÖNÜŞ"
        if s["confirmed"]
        else "🔻 SHORT ADAYI — DÖNÜŞ BEKLENTİSİ"
    )

    ema_text = "N/A"
    if s["ema11"] is not None and s["ema21"] is not None:
        if s["ema11"] >= s["ema21"]:
            ema_text = "EMA11 ≥ EMA21 (yukarı)"
        else:
            ema_text = "EMA11 < EMA21 (aşağı)"

    support_text = (
        "Destek kırılımı"
        if s["support_break"]
        else "Destek kırılımı yok"
    )

    message = (
        f"{title}\n"
        f"#{symbol} · Binance USDT-M Futures\n"
        f"⭐ Sinyal skoru: {s['score']}/6\n\n"
        f"🏷 Giriş referansı: {fmt(s['entry'], 8)}\n"
        f"🛑 Stop-loss: {fmt(s['stop'], 8)} "
        f"(girişten +%{s['risk_pct']:.2f})\n"
        f"🎯 TP1 (1R): {fmt(s['tp1'], 8)}\n"
        f"🎯 TP2 (2R): {fmt(s['tp2'], 8)}\n\n"
        f"⏱ Zaman dilimleri (son kapanmış mum)\n"
        f"• 1dk: {pct(s['change1'])} | "
        f"5dk: {pct(s['change5'])} | "
        f"15dk: {pct(s['change15'])}\n"
        f"📉 EMA: {ema_text}\n"
        f"📊 RSI(1dk): {fmt(s['rsi'], 1)} | "
        f"Hacim: ortalamanın "
        f"{fmt(s['volume_ratio'], 1)} katı\n"
        f"📈 24s değişim: {pct(s['change24'])} | "
        f"24s hacim: {s['volume24']/1_000_000:.2f}M USDT\n"
        f"💹 Açık pozisyon değişimi: "
        f"{pct(s['oi'])} / 5dk\n"
        f"👥 Long/Short hesap oranı: "
        f"{fmt(s['ls'], 2)} (hesap oranı)\n"
        f"🗓 30g High/Low: {fmt(s['high30'], 8)} / "
        f"{fmt(s['low30'], 8)}\n"
        f"📆 365g High/Low: {fmt(s['high365'], 8)} / "
        f"{fmt(s['low365'], 8)}\n\n"
        f"🔎 Dönüş teyitleri\n"
        f"• Önceki 5 mum hareketi: {pct(s['previous_move'])}\n"
        f"• {support_text}\n"
        f"• RSI: {fmt(s['rsi'], 1)}\n"
        f"• Hacim: ortalamanın "
        f"{fmt(s['volume_ratio'], 1)} katı\n\n"
        f"🔗 Binance Futures paritesi:\n"
        f"https://www.binance.com/en/futures/{symbol}\n"
        f"🕒 {stamp()}\n\n"
        f"Bilgilendirme amaçlı sinyaldir; garanti değildir. "
        f"Bot gerçek emir açmaz. Ek piyasa metrikleri Binance "
        f"API yanıtına göre N/A görünebilir. Komisyon, funding "
        f"ve kayma hesaba katılmaz."
    )

    return message


def main():
    load_state()

    if not BOT_TOKEN:
        log.error("Render Environment icinde BOT_TOKEN eksik.")
        return

    if not CHAT_ID:
        log.error("CHAT_ID eksik.")
        return

    telegram(
        "✅ Futures SHORT sinyal botu başladı.\n"
        "Gerçek emir açılmaz; yalnızca alarm gönderilir."
    )

    while True:
        try:
            if time.time() < binance_pause_until:
                remaining = int(binance_pause_until - time.time())
                log.warning(
                    "Binance bekleme süresi: %d saniye.",
                    remaining
                )
                time.sleep(min(max(remaining, 10), 180))
                continue

            candidates = get_market_candidates()
            log.info(
                "Tarama başladı: %d aday parite.",
                len(candidates)
            )

            sent = 0

            for item in candidates:
                if time.time() < binance_pause_until:
                    break

                symbol = item["symbol"]

                if time.time() - float(last_signal.get(symbol, 0)) < SYMBOL_COOLDOWN:
                    continue

                try:
                    signal = analyse(item)
                    if not signal:
                        continue

                    # Telegram gonderimi basarili olmadan cooldown baslatma.
                    if telegram(signal_message(signal)):
                        last_signal[symbol] = time.time()
                        save_state()
                        sent += 1
                        log.info(
                            "%s alarm gonderildi. Skor %s/6",
                            symbol, signal["score"]
                        )

                except RuntimeError as exc:
                    log.warning("%s: %s", symbol, exc)
                    break
                except Exception as exc:
                    log.warning("%s analiz hatasi: %s", symbol, exc)

            log.info(
                "Tarama tamamlandi: %d parite | %d alarm",
                len(candidates), sent
            )

        except Exception as exc:
            log.exception("Tarama hatasi: %s", exc)

        time.sleep(SCAN_SECONDS)


if __name__ == "__main__":
    main()

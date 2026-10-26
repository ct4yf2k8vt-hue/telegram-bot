import os
import json
import time
import threading
import urllib.request
import urllib.parse
import urllib.error
import re

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# =========================================================
# AYARLAR
# =========================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "1623907197").strip()

BASE_URL = "https://fapi.binance.com"
TG_URL = "https://api.telegram.org"

TR = timezone(timedelta(hours=3))

SCAN_INTERVAL = 180
MAX_COINS = 12
MIN_24H_CHANGE = 5.0
MIN_24H_VOLUME = 5_000_000
MIN_SCORE = 4
SIGNAL_COOLDOWN = 1800

STATE_FILE = "short_bot_state.json"
COOLDOWN_FILE = "binance_cooldown.json"

lock = threading.Lock()
last_signals = {}
daily_cache = {}
binance_cooldown_until = 0


# =========================================================
# ZAMAN VE LOG
# =========================================================

def now_tr():
    return datetime.now(TR)


def log(message):
    print(
        f"[{now_tr().strftime('%Y-%m-%d %H:%M:%S')} TR] {message}",
        flush=True
    )


def fmt_price(value):
    if value is None:
        return "N/A"

    value = float(value)

    if value >= 1000:
        return f"{value:.2f}"
    if value >= 1:
        return f"{value:.4f}"
    if value >= 0.01:
        return f"{value:.6f}"

    return f"{value:.8f}"


# =========================================================
# DURUM DOSYALARI
# =========================================================

def load_state():
    global last_signals

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            last_signals = data

    except (OSError, ValueError):
        last_signals = {}


def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(last_signals, f)
    except Exception as e:
        log(f"Sinyal durumu kaydedilemedi: {e}")


def load_cooldown():
    global binance_cooldown_until

    try:
        with open(COOLDOWN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        binance_cooldown_until = float(data.get("until", 0))

    except (OSError, ValueError, TypeError):
        binance_cooldown_until = 0


def save_cooldown():
    try:
        with open(COOLDOWN_FILE, "w", encoding="utf-8") as f:
            json.dump({"until": binance_cooldown_until}, f)
    except Exception as e:
        log(f"Bekleme süresi kaydedilemedi: {e}")


# =========================================================
# TELEGRAM
# =========================================================

def tg(message):
    if not BOT_TOKEN or not CHAT_ID:
        log("TELEGRAM HATASI: BOT_TOKEN veya CHAT_ID eksik.")
        return False

    url = f"{TG_URL}/bot{BOT_TOKEN}/sendMessage"

    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true"
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=data)

        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))

        if result.get("ok"):
            log("Telegram mesajı başarıyla gönderildi.")
            return True

        log(f"Telegram API hatası: {result}")
        return False

    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""

        log(f"Telegram HTTP {e.code}: {body[:300]}")
        return False

    except Exception as e:
        log(f"Telegram bağlantı hatası: {e}")
        return False


def telegram_test():
    if not BOT_TOKEN or not CHAT_ID:
        log(
            "UYARI: Render Environment Variables bölümünde "
            "BOT_TOKEN ve CHAT_ID tanımlanmalı."
        )
        return

    tg(
        "✅ <b>SHORT FUTURES BOT BAŞLADI</b>\n\n"
        "Telegram bağlantısı test ediliyor.\n"
        "Bot gerçek emir açmaz; yalnızca sinyal gönderir."
    )


# =========================================================
# BINANCE API VE 418 / 429 KONTROLÜ
# =========================================================

def set_cooldown(seconds, reason):
    global binance_cooldown_until

    seconds = max(60, int(seconds))

    with lock:
        new_until = time.time() + seconds
        binance_cooldown_until = max(
            binance_cooldown_until,
            new_until
        )
        save_cooldown()

    log(
        f"{reason}: {seconds} saniye bekleme. "
        "Binance istekleri duraklatıldı."
    )


def api_get(path, params=None):
    global binance_cooldown_until

    if time.time() < binance_cooldown_until:
        return None

    url = BASE_URL + path

    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "FuturesSignalBot/1.0"}
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as e:
        body = ""

        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass

        if e.code in (418, 429):
            retry_after = e.headers.get("Retry-After")
            wait_seconds = None

            if retry_after:
                try:
                    wait_seconds = int(float(retry_after))
                except (ValueError, TypeError):
                    pass

            # Binance yanıtında "banned until" zaman damgası varsa
            # bu zamana kadar bekle.
            match = re.search(
                r"banned until (\d+)",
                body,
                re.IGNORECASE
            )

            if match:
                try:
                    timestamp = int(match.group(1))

                    if timestamp > 10**12:
                        timestamp /= 1000

                    wait_seconds = max(
                        wait_seconds or 0,
                        int(timestamp - time.time())
                    )
                except Exception:
                    pass

            if wait_seconds is None:
                wait_seconds = 1800 if e.code == 418 else 300

            set_cooldown(
                wait_seconds,
                f"Binance HTTP {e.code}"
            )

        else:
            log(f"Binance HTTP {e.code}: {body[:250]}")

        return None

    except Exception as e:
        log(f"Binance bağlantı hatası: {e}")
        return None


# =========================================================
# TEKNİK GÖSTERGELER
# =========================================================

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


def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None

    deltas = [
        prices[i] - prices[i - 1]
        for i in range(1, len(prices))
    ]

    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (
            avg_gain * (period - 1) + gains[i]
        ) / period

        avg_loss = (
            avg_loss * (period - 1) + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def get_candles(symbol, interval, limit=60):
    data = api_get(
        "/fapi/v1/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
    )

    if not isinstance(data, list) or len(data) < 22:
        return None

    # Son mum kapanmamış olabileceği için kullanılmaz.
    return data[:-1]


def candle_direction(candles):
    if not candles or len(candles) < 2:
        return "N/A"

    previous = float(candles[-2][4])
    current = float(candles[-1][4])

    if current > previous:
        return "Yukarı"
    if current < previous:
        return "Aşağı"

    return "Yatay"


def get_daily_high_low(symbol):
    cached = daily_cache.get(symbol)

    if cached and time.time() - cached["time"] < 43200:
        return cached["data"]

    candles = api_get(
        "/fapi/v1/klines",
        {
            "symbol": symbol,
            "interval": "1d",
            "limit": 365
        }
    )

    if not isinstance(candles, list) or not candles:
        return None

    try:
        highs = [float(x[2]) for x in candles]
        lows = [float(x[3]) for x in candles]

        count = min(30, len(candles))

        result = {
            "high30": max(highs[-count:]),
            "low30": min(lows[-count:]),
            "high365": max(highs),
            "low365": min(lows)
        }

        daily_cache[symbol] = {
            "time": time.time(),
            "data": result
        }

        return result

    except (ValueError, TypeError, IndexError):
        return None


# =========================================================
# EK PIYASA METRİKLERİ
# =========================================================

def get_open_interest_change(symbol):
    data = api_get(
        "/futures/data/openInterestHist",
        {
            "symbol": symbol,
            "period": "5m",
            "limit": 2
        }
    )

    if not isinstance(data, list) or len(data) < 2:
        return None

    try:
        old = float(data[0]["sumOpenInterest"])
        new = float(data[-1]["sumOpenInterest"])

        if old == 0:
            return None

        return ((new - old) / old) * 100

    except (ValueError, TypeError, KeyError):
        return None


def get_long_short_ratio(symbol):
    data = api_get(
        "/futures/data/globalLongShortAccountRatio",
        {
            "symbol": symbol,
            "period": "5m",
            "limit": 1
        }
    )

    if not isinstance(data, list) or not data:
        return None

    try:
        return float(data[-1]["longShortRatio"])
    except (ValueError, TypeError, KeyError):
        return None


# =========================================================
# ADAY PARİTELER
# =========================================================

def get_candidates():
    tickers = api_get("/fapi/v1/ticker/24hr")

    if not isinstance(tickers, list):
        return None

    candidates = []

    for ticker in tickers:
        try:
            symbol = ticker["symbol"]

            if not symbol.endswith("USDT"):
                continue

            # Kaldıraçlı token benzeri pariteleri ele.
            if any(x in symbol for x in (
                "UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT"
            )):
                continue

            change = float(ticker["priceChangePercent"])
            volume = float(ticker["quoteVolume"])

            if change < MIN_24H_CHANGE:
                continue

            if volume < MIN_24H_VOLUME:
                continue

            candidates.append(ticker)

        except (ValueError, TypeError, KeyError):
            continue

    candidates.sort(
        key=lambda x: float(x.get("quoteVolume", 0)),
        reverse=True
    )

    return candidates[:MAX_COINS]


# =========================================================
# SHORT SİNYAL ANALİZİ
# =========================================================

def analyze_symbol(ticker):
    symbol = ticker["symbol"]

    one = get_candles(symbol, "1m", 60)

    if not one or len(one) < 25:
        return None

    five = get_candles(symbol, "5m", 30)

    if not five:
        return None

    fifteen = get_candles(symbol, "15m", 30)

    if not fifteen:
        return None

    try:
        closes = [float(x[4]) for x in one]
        highs = [float(x[2]) for x in one]
        lows = [float(x[3]) for x in one]
        opens = [float(x[1]) for x in one]
        quote_volumes = [float(x[7]) for x in one]

        current = one[-1]
        entry = float(current[4])
        candle_open = float(current[1])

        if entry <= 0:
            return None

        # Önceki 5 kapanmış mumun toplam fiyat hareketi.
        start_price = float(one[-6][4])

        move5 = (
            (float(one[-1][4]) - start_price)
            / start_price
        ) * 100 if start_price else 0

        rsi_value = calculate_rsi(closes)
        ema11 = ema(closes, 11)
        ema21 = ema(closes, 21)

        # Hacim: son kapanmış 1m mum / önceki 20 mum ortalaması.
        previous_volumes = quote_volumes[-21:-1]
        volume_avg = (
            sum(previous_volumes) / len(previous_volumes)
            if previous_volumes else 0
        )

        volume_ratio = (
            quote_volumes[-1] / volume_avg
            if volume_avg > 0 else 0
        )

        # Son mumun kırmızı olması ve önceki 5 mumun
        # en düşük seviyesinin altında kapanması gerekir.
        previous_support = min(lows[-6:-1])
        red_candle = float(current[4]) < float(current[1])
        support_break = entry < previous_support

        score = 0
        reasons = []

        if move5 >= 1.0:
            score += 1
            reasons.append(
                f"Önceki 5 mum hareketi: %{move5:.2f}"
            )

        if rsi_value is not None and rsi_value >= 60:
            score += 1
            reasons.append(f"RSI: {rsi_value:.1f}")

        if volume_ratio >= 1.5:
            score += 1
            reasons.append(
                f"Hacim: ortalamanın {volume_ratio:.1f} katı"
            )

        if (
            ema11 is not None
            and ema21 is not None
            and ema11 >= ema21
        ):
            score += 1
            ema_text = "EMA11 ≥ EMA21 (yukarı)"
        else:
            ema_text = "EMA11 < EMA21 veya veri yok"

        if support_break:
            score += 1
            reasons.append("Destek kırılımı")

        if red_candle:
            score += 1
            reasons.append("Kırmızı mum")

        # Yanlış yönlü veya teyitsiz sinyalleri ele.
        if score < MIN_SCORE:
            return None

        if not support_break or not red_candle:
            return None

        # Stop: son 5 mumun en yüksek seviyesi.
        stop = max(highs[-5:])

        if stop <= entry:
            stop = entry * 1.005

        risk = stop - entry

        if risk <= 0:
            return None

        risk_pct = (risk / entry) * 100

        # Aşırı geniş stoplu sinyalleri ele.
        if risk_pct > 5:
            return None

        tp1 = entry - risk
        tp2 = entry - (2 * risk)

        change24 = float(ticker["priceChangePercent"])
        volume24 = float(ticker["quoteVolume"])
        high24 = float(ticker["highPrice"])
        low24 = float(ticker["lowPrice"])

        oi_change = get_open_interest_change(symbol)
        ls_ratio = get_long_short_ratio(symbol)
        daily = get_daily_high_low(symbol)

        high30 = daily["high30"] if daily else None
        low30 = daily["low30"] if daily else None
        high365 = daily["high365"] if daily else None
        low365 = daily["low365"] if daily else None

        now_text = now_tr().strftime("%Y-%m-%d %H:%M:%S")

        oi_text = (
            f"{oi_change:+.2f}% / 5dk"
            if oi_change is not None else "N/A"
        )

        ls_text = (
            f"{ls_ratio:.2f} (hesap oranı)"
            if ls_ratio is not None else "N/A"
        )

        rsi_text = (
            f"{rsi_value:.1f}"
            if rsi_value is not None else "N/A"
        )

        msg = (
            f"🔻 <b>SHORT ADAYI — TEYİTLİ DÖNÜŞ</b>\n"
            f"<b>#{symbol}</b> · Binance USDT-M Futures\n"
            f"⭐ Sinyal skoru: {score}/6\n\n"

            f"🏷 Giriş referansı: {fmt_price(entry)}\n"
            f"🛑 Stop-loss: {fmt_price(stop)} "
            f"(girişten +%{risk_pct:.2f})\n"
            f"🎯 TP1 (1R): {fmt_price(tp1)}\n"
            f"🎯 TP2 (2R): {fmt_price(tp2)}\n\n"

            f"⏱ Zaman dilimleri (son kapanmış mum)\n"
            f"• 1dk: {candle_direction(one)} | "
            f"5dk: {candle_direction(five)} | "
            f"15dk: {candle_direction(fifteen)}\n"

            f"📉 EMA: {ema_text}\n"
            f"📊 RSI(1dk): {rsi_text} | "
            f"Hacim: ortalamanın {volume_ratio:.1f} katı\n"

            f"📈 24s değişim: {change24:+.2f}% | "
            f"24s hacim: {volume24 / 1_000_000:.2f}M USDT\n"

            f"💹 Açık pozisyon değişimi: {oi_text}\n"
            f"👥 Long/Short hesap oranı: {ls_text}\n"

            f"🗓 30g High/Low: "
            f"{fmt_price(high30)} / {fmt_price(low30)}\n"
            f"📆 365g High/Low: "
            f"{fmt_price(high365)} / {fmt_price(low365)}\n\n"

            f"🔎 <b>Dönüş teyitleri</b>\n"
            + "".join(f"• {reason}\n" for reason in reasons)
            + f"\n🔗 <a href='https://www.binance.com/en/futures/{symbol}'>"
              f"Binance Futures paritesi</a>\n"
            f"🕒 {now_text}\n\n"

            f"Bilgilendirme amaçlı sinyaldir; garanti değildir. "
            f"Bot gerçek emir açmaz. Ek piyasa metrikleri Binance API "
            f"yanıtına göre N/A görünebilir. Komisyon, funding ve "
            f"kayma hesaba katılmaz."
        )

        return {
            "symbol": symbol,
            "score": score,
            "message": msg
        }

    except Exception as e:
        log(f"{symbol} analiz hatası: {e}")
        return None


# =========================================================
# TARAMA DÖNGÜSÜ
# =========================================================

def bot_loop():
    load_state()
    load_cooldown()

    log("Binance Futures SHORT bot başlatılıyor.")

    telegram_test()

    while True:
        try:
            if time.time() < binance_cooldown_until:
                remaining = int(
                    binance_cooldown_until - time.time()
                )

                log(
                    f"Binance bekleme süresi: {remaining} saniye; "
                    "tarama duraklatıldı."
                )

                time.sleep(min(180, max(5, remaining)))
                continue

            candidates = get_candidates()

            if candidates is None:
                log("Pariteler alınamadı; API bekleniyor.")
                time.sleep(60)
                continue

            log(
                f"Futures aday pariteleri: {len(candidates)}"
            )

            alerts = 0

            for ticker in candidates:
                if time.time() < binance_cooldown_until:
                    break

                symbol = ticker["symbol"]

                last_time = float(last_signals.get(symbol, 0))

                if time.time() - last_time < SIGNAL_COOLDOWN:
                    continue

                result = analyze_symbol(ticker)

                if result is None:
                    time.sleep(0.25)
                    continue

                # Önce Telegram'a gönder; yalnızca başarılıysa
                # sinyal zamanını kaydet.
                if tg(result["message"]):
                    last_signals[symbol] = time.time()
                    save_state()
                    alerts += 1

                    log(
                        f"SİNYAL GÖNDERİLDİ: {symbol} "
                        f"| Skor: {result['score']}/6"
                    )

                time.sleep(0.5)

            log(
                f"Tarama tamamlandı: {len(candidates)} aday | "
                f"{alerts} alarm"
            )

            time.sleep(SCAN_INTERVAL)

        except Exception as e:
            log(f"Tarama döngüsü hatası: {e}")
            time.sleep(30)


# =========================================================
# RENDER WEB HEALTH SERVER
# =========================================================

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"Futures SHORT bot is running."

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format_string, *args):
        # Gereksiz HTTP erişim loglarını azalt.
        return


def run_web_server():
    port = int(os.environ.get("PORT", "10000"))

    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        Handler
    )

    log(f"Health server port: {port}")
    server.serve_forever()


# =========================================================
# BAŞLAT
# =========================================================

if __name__ == "__main__":
    threading.Thread(
        target=bot_loop,
        daemon=True
    ).start()

    run_web_server()

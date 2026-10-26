import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape

# AZCRYPTO SHORT SCANNER
# Sinyal üretir; gerçek Binance emri açmaz.

BASE_URL = "https://fapi.binance.com"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "1623907197").strip()
PORT = int(os.environ.get("PORT", "10000"))

# Daha düşük Binance API trafiği
SCAN_INTERVAL_SECONDS = max(
    60, int(os.environ.get("SCAN_INTERVAL_SECONDS", "90"))
)
MAX_SYMBOLS = max(
    5, min(20, int(os.environ.get("MAX_SYMBOLS", "12")))
)

MIN_QUOTE_VOLUME_USDT = float(
    os.environ.get("MIN_QUOTE_VOLUME_USDT", "5000000")
)
MIN_PUMP_5M_PCT = float(
    os.environ.get("MIN_PUMP_5M_PCT", "1.5")
)
MIN_SINGLE_CANDLE_PCT = float(
    os.environ.get("MIN_SINGLE_CANDLE_PCT", "1.0")
)
MIN_VOLUME_RATIO = float(
    os.environ.get("MIN_VOLUME_RATIO", "1.2")
)
RSI_MIN = float(os.environ.get("RSI_MIN", "60"))
COOLDOWN_SECONDS = int(
    os.environ.get("COOLDOWN_SECONDS", "1200")
)

DATA_FILE = os.environ.get("DATA_FILE", "shortbot_data.json")
COOLDOWN_FILE = os.environ.get(
    "BINANCE_COOLDOWN_FILE", "binance_cooldown.json"
)
DEFAULT_418_WAIT = max(
    900, int(os.environ.get("BINANCE_COOLDOWN_SECONDS", "3600"))
)

TR_TZ = timezone(timedelta(hours=3))

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("azcrypto-short")

BINANCE_COOLDOWN_UNTIL = 0.0
COOLDOWN_LOCK = threading.Lock()


class BinanceRateLimitError(Exception):
    pass


def tr_saat():
    return datetime.now(TR_TZ).strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------
# BINANCE 418 / 429 BEKLEME KORUMASI
# --------------------------------------------------

def load_cooldown():
    global BINANCE_COOLDOWN_UNTIL

    try:
        with open(COOLDOWN_FILE, "r", encoding="utf-8") as f:
            obj = json.load(f)

        until = float(obj.get("until", 0))

        if until > time.time():
            BINANCE_COOLDOWN_UNTIL = until
            log.warning(
                "Kayıtlı Binance beklemesi devam ediyor: yaklaşık %d saniye.",
                int(until - time.time())
            )

    except (OSError, ValueError, TypeError):
        pass


def save_cooldown(until, reason):
    global BINANCE_COOLDOWN_UNTIL

    with COOLDOWN_LOCK:
        BINANCE_COOLDOWN_UNTIL = max(
            BINANCE_COOLDOWN_UNTIL, float(until)
        )

        try:
            with open(COOLDOWN_FILE + ".tmp", "w", encoding="utf-8") as f:
                json.dump({
                    "until": BINANCE_COOLDOWN_UNTIL,
                    "reason": reason
                }, f)

            os.replace(COOLDOWN_FILE + ".tmp", COOLDOWN_FILE)

        except OSError:
            log.exception("Binance bekleme süresi kaydedilemedi")


def retry_after_seconds(exc):
    value = exc.headers.get("Retry-After") if exc.headers else None

    if value:
        try:
            return max(1, int(float(value)))
        except (ValueError, TypeError):
            try:
                dt = parsedate_to_datetime(value)

                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)

                return max(1, int(dt.timestamp() - time.time()))

            except Exception:
                pass

    return DEFAULT_418_WAIT


def api_json(path, params=None, timeout=12):
    if time.time() < BINANCE_COOLDOWN_UNTIL:
        raise BinanceRateLimitError(
            "Binance API bekleme süresi devam ediyor"
        )

    query = urllib.parse.urlencode(params or {})
    url = BASE_URL + path + (("?" + query) if query else "")

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "AZCryptoShortScanner/2.0",
            "Accept": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    except HTTPError as exc:
        if exc.code in (418, 429):
            wait = retry_after_seconds(exc)
            until = time.time() + wait

            save_cooldown(until, f"HTTP {exc.code}")

            log.error(
                "Binance HTTP %s: API istekleri %d saniye durduruldu.",
                exc.code, wait
            )

            raise BinanceRateLimitError(
                f"Binance HTTP {exc.code}; {wait} saniye bekleniyor"
            ) from exc

        raise


# --------------------------------------------------
# TELEGRAM
# --------------------------------------------------

def tg(message):
    if not BOT_TOKEN or not CHAT_ID:
        log.warning(
            "BOT_TOKEN veya CHAT_ID eksik; Telegram mesajı gönderilemedi."
        )
        return False

    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true"
    }).encode("utf-8")

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            result = json.loads(response.read().decode("utf-8"))

        if not result.get("ok"):
            log.error("Telegram API hata yanıtı: %s", result)
            return False

        return True

    except Exception:
        log.exception("Telegram mesajı gönderilemedi")
        return False


# --------------------------------------------------
# BOT DURUMU VE İSTATİSTİKLER
# --------------------------------------------------

def load_state():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}

    state.setdefault("last_signal", {})
    state.setdefault("active", {})
    state.setdefault("history", [])
    state.setdefault("telegram_offset", 0)

    return state


def save_state(state):
    try:
        with open(DATA_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

        os.replace(DATA_FILE + ".tmp", DATA_FILE)

    except OSError:
        log.exception("Bot durumu kaydedilemedi")


def average(values):
    return sum(values) / len(values) if values else 0.0


def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0

    deltas = [
        prices[i] - prices[i - 1]
        for i in range(1, len(prices))
    ]

    gains = [max(x, 0.0) for x in deltas]
    losses = [max(-x, 0.0) for x in deltas]

    gain = average(gains[:period])
    loss = average(losses[:period])

    for i in range(period, len(deltas)):
        gain = (
            gain * (period - 1) + gains[i]
        ) / period

        loss = (
            loss * (period - 1) + losses[i]
        ) / period

    if loss == 0:
        return 100.0

    rs = gain / loss
    return 100.0 - 100.0 / (1.0 + rs)


def ema(values, period):
    if not values:
        return 0.0

    alpha = 2.0 / (period + 1.0)
    result = values[0]

    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result

    return result


def candle(raw):
    return {
        "open_time": int(raw[0]),
        "open": float(raw[1]),
        "high": float(raw[2]),
        "low": float(raw[3]),
        "close": float(raw[4]),
        "volume": float(raw[5]),
        "close_time": int(raw[6]),
        "quote_volume": float(raw[7])
    }


# --------------------------------------------------
# BINANCE PİYASA VERİLERİ
# --------------------------------------------------

def get_market_snapshot():
    # Tek ticker isteğiyle USDT vadeli paritelerini al.
    tickers = api_json("/fapi/v1/ticker/24hr")
    result = {}

    excluded = (
        "UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT"
    )

    for t in tickers:
        symbol = t.get("symbol", "")

        if (
            not symbol.endswith("USDT")
            or symbol.endswith(excluded)
        ):
            continue

        try:
            volume = float(t["quoteVolume"])
            price = float(t["lastPrice"])

            if (
                volume < MIN_QUOTE_VOLUME_USDT
                or price <= 0
            ):
                continue

            result[symbol] = {
                "price": price,
                "quote_volume": volume,
                "change_24h": float(t["priceChangePercent"]),
                "high_24h": float(t["highPrice"]),
                "low_24h": float(t["lowPrice"])
            }

        except (KeyError, TypeError, ValueError):
            continue

    return result


# --------------------------------------------------
# SHORT SİNYAL TESPİTİ
# --------------------------------------------------

def detect_short_setup(raw):
    # Açık olan son mumu kullanma; kapanmış mumları analiz et.
    if not raw or len(raw) < 30:
        return None

    cs = [candle(x) for x in raw[:-1]]
    closes = [x["close"] for x in cs]

    cur = cs[-1]
    prev = cs[-2]

    pump_window = cs[-6:-1]

    if len(pump_window) != 5 or pump_window[0]["open"] <= 0:
        return None

    pump_pct = (
        pump_window[-1]["close"] / pump_window[0]["open"] - 1
    ) * 100

    one_pct = (
        prev["close"] / prev["open"] - 1
    ) * 100 if prev["open"] else 0

    rsi = calculate_rsi(closes, 14)

    prior_volumes = [
        x["quote_volume"] for x in cs[-22:-2]
    ]

    avg_volume = average(prior_volumes)

    vol_ratio = (
        cur["quote_volume"] / avg_volume
        if avg_volume else 0
    )

    body = abs(cur["close"] - cur["open"])

    upper_wick = (
        cur["high"] - max(cur["open"], cur["close"])
    )

    candle_range = cur["high"] - cur["low"]

    rejection = (
        cur["close"] < cur["open"]
        and upper_wick >= max(
            body * 1.2,
            candle_range * 0.25
        )
    )

    breakdown = cur["close"] < prev["low"]

    pumped = (
        pump_pct >= MIN_PUMP_5M_PCT
        or one_pct >= MIN_SINGLE_CANDLE_PCT
    )

    if (
        not pumped
        or not (breakdown or rejection)
        or rsi < RSI_MIN
        or vol_ratio < MIN_VOLUME_RATIO
    ):
        return None

    atr = average([
        x["high"] - x["low"] for x in cs[-15:]
    ])

    recent_high = max(x["high"] for x in cs[-6:])
    entry = cur["close"]

    stop = recent_high + max(
        atr * 0.25,
        entry * 0.001
    )

    risk = stop - entry

    if risk <= 0:
        return None

    return {
        "entry": entry,
        "stop": stop,
        "tp1": entry - risk,
        "tp2": entry - 2 * risk,
        "risk_pct": risk / entry * 100,
        "rsi": rsi,
        "pump_pct": pump_pct,
        "one_pct": one_pct,
        "volume_ratio": vol_ratio,
        "candle_high": cur["high"],
        "candle_low": cur["low"],
        "time": tr_saat(),
        "reasons": [
            f"Önceki 5 mum hareketi: %{pump_pct:.2f}",
            f"Önceki mum hareketi: %{one_pct:.2f}",
            (
                "Destek kırılımı"
                if breakdown
                else "Üst fitil reddi + kırmızı kapanış"
            ),
            f"RSI: {rsi:.1f}",
            f"Hacim: ortalamanın {vol_ratio:.1f} katı"
        ],
        "ema11": ema(closes[-40:], 11),
        "ema21": ema(closes[-60:], 21)
    }


def extra_market_metrics(symbol):
    # Sinyal adayı başına en fazla dört ek istek.
    # 418/429 hatası asla yutulmaz; tarama durdurulur.
    out = {
        "m5": None,
        "m15": None,
        "oi_change": None,
        "long_short": None
    }

    for interval, key in (
        ("5m", "m5"),
        ("15m", "m15")
    ):
        try:
            data = api_json(
                "/fapi/v1/klines",
                {
                    "symbol": symbol,
                    "interval": interval,
                    "limit": 3
                }
            )

            if isinstance(data, list) and len(data) >= 3:
                old = float(data[-3][4])
                new = float(data[-2][4])

                out[key] = (
                    (new / old - 1) * 100
                    if old else None
                )

        except BinanceRateLimitError:
            raise

        except Exception as exc:
            log.debug(
                "%s %s metrik hatası: %s",
                symbol, key, exc
            )

    try:
        oi = api_json(
            "/futures/data/openInterestHist",
            {
                "symbol": symbol,
                "period": "5m",
                "limit": 2
            }
        )

        if isinstance(oi, list) and len(oi) >= 2:
            old = float(
                oi[-2].get("sumOpenInterest", 0)
            )
            new = float(
                oi[-1].get("sumOpenInterest", 0)
            )

            if old > 0:
                out["oi_change"] = (
                    new / old - 1
                ) * 100

    except BinanceRateLimitError:
        raise

    except Exception as exc:
        log.debug("%s OI metrik hatası: %s", symbol, exc)

    try:
        ls = api_json(
            "/futures/data/globalLongShortAccountRatio",
            {
                "symbol": symbol,
                "period": "5m",
                "limit": 1
            }
        )

        if isinstance(ls, list) and ls:
            out["long_short"] = float(
                ls[-1].get("longShortRatio", 0)
            )

    except BinanceRateLimitError:
        raise

    except Exception as exc:
        log.debug(
            "%s Long/Short metrik hatası: %s",
            symbol, exc
        )

    return out


def fmt_price(value):
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:.4f}"
    if value >= 0.01:
        return f"{value:.6f}"
    return f"{value:.8f}"


# --------------------------------------------------
# TELEGRAM SİNYAL MESAJI
# --------------------------------------------------

def send_signal(symbol, market, setup, extra):
    link = (
        "https://www.binance.com/en/futures/"
        + urllib.parse.quote(symbol)
    )

    ema_text = (
        "EMA11 < EMA21 (ayı)"
        if setup["ema11"] < setup["ema21"]
        else "EMA11 ≥ EMA21 (yukarı)"
    )

    def pct(value):
        return "N/A" if value is None else f"{value:+.2f}%"

    oi = (
        "N/A"
        if extra["oi_change"] is None
        else f"{extra['oi_change']:+.2f}% / 5dk"
    )

    ls = (
        "N/A"
        if extra["long_short"] is None
        else f"{extra['long_short']:.2f} (hesap oranı)"
    )

    score = sum([
        extra["m5"] is not None and extra["m5"] < 0,
        extra["m15"] is not None and extra["m15"] < 0,
        setup["ema11"] < setup["ema21"],
        extra["oi_change"] is not None and extra["oi_change"] > 0,
        extra["long_short"] is not None and extra["long_short"] > 1,
        setup["rsi"] >= RSI_MIN
    ])

    reasons = "\n".join(
        "• " + escape(x)
        for x in setup["reasons"]
    )

    return (
        f"🔻 <b>SHORT ADAYI — DÖNÜŞ TEYİDİ</b>\n"
        f"<b>#{escape(symbol)} · Binance USDT-M Futures</b>\n"
        f"⭐ Sinyal skoru: <b>{score}/6</b>\n\n"

        f"🏷 Giriş referansı: "
        f"<code>{fmt_price(setup['entry'])}</code>\n"

        f"🛑 Stop-loss: "
        f"<code>{fmt_price(setup['stop'])}</code> "
        f"(+%{setup['risk_pct']:.2f})\n"

        f"🎯 TP1 (1R): "
        f"<code>{fmt_price(setup['tp1'])}</code>\n"

        f"🎯 TP2 (2R): "
        f"<code>{fmt_price(setup['tp2'])}</code>\n\n"

        f"⏱ <b>Zaman dilimleri (kapanmış mum)</b>\n"
        f"• 1dk sinyal mumu: %{setup['one_pct']:+.2f} | "
        f"5dk: {pct(extra['m5'])} | "
        f"15dk: {pct(extra['m15'])}\n"

        f"📉 EMA: {ema_text}\n"
        f"📊 RSI(1dk): {setup['rsi']:.1f} | "
        f"Hacim: ortalamanın "
        f"{setup['volume_ratio']:.1f} katı\n"

        f"📈 24s değişim: "
        f"{market['change_24h']:+.2f}% | "
        f"24s hacim: "
        f"{market['quote_volume']/1_000_000:.2f}M USDT\n"

        f"💹 Açık pozisyon değişimi: {oi}\n"
        f"👥 Long/Short hesap oranı: {ls}\n"

        f"📍 24s High/Low: "
        f"{fmt_price(market['high_24h'])} / "
        f"{fmt_price(market['low_24h'])}\n\n"

        f"🔎 <b>Dönüş teyitleri</b>\n{reasons}\n\n"

        f"🔗 <a href='{link}'>Binance Futures paritesi</a>\n"
        f"🕒 {escape(tr_saat())}\n\n"

        "<i>Bu bir sinyal/simülasyondur, garanti değildir. "
        "Bot gerçek emir açmaz. Komisyon, funding ve kayma dahil değildir.</i>"
    )


# --------------------------------------------------
# SANAL İŞLEM TAKİBİ
# --------------------------------------------------

def update_active_trade(state, symbol, raw):
    trade = state["active"].get(symbol)

    if not trade or len(raw) < 2:
        return

    last = candle(raw[-2])

    # Aynı mumda hem hedef hem stop görülürse ihtiyatlı olarak stop kabul et.
    if last["high"] >= trade["stop"]:
        outcome, exit_price = "STOP", trade["stop"]

    elif last["low"] <= trade["tp1"]:
        outcome, exit_price = "TP1", trade["tp1"]

    else:
        return

    pnl = (
        (trade["entry"] - exit_price)
        / trade["entry"]
    ) * 100

    state["history"].append({
        "symbol": symbol,
        "entry_time": trade["time"],
        "exit_time": tr_saat(),
        "entry": trade["entry"],
        "exit": exit_price,
        "outcome": outcome,
        "gross_pnl_pct": pnl
    })

    state["history"] = state["history"][-2000:]
    del state["active"][symbol]
    save_state(state)

    emoji = "✅" if outcome == "TP1" else "🛑"

    tg(
        f"{emoji} <b>SHORT SANAL İŞLEM SONUCU</b>\n"
        f"#{escape(symbol)} · <b>{outcome}</b>\n"
        f"Giriş: {fmt_price(trade['entry'])}\n"
        f"Çıkış: {fmt_price(exit_price)}\n"
        f"Brüt fiyat sonucu: {pnl:+.2f}%\n"
        f"<i>Komisyon/funding hariçtir; simülasyondur.</i>"
    )


def stats_message(state):
    hist = state["history"]

    wins = sum(
        x.get("outcome") == "TP1"
        for x in hist
    )

    losses = sum(
        x.get("outcome") == "STOP"
        for x in hist
    )

    total = wins + losses

    gross = sum(
        float(x.get("gross_pnl_pct", 0))
        for x in hist
    )

    win_rate = wins / total * 100 if total else 0

    return (
        "<b>AZCRYPTO SHORT İSTATİSTİKLERİ</b>\n"
        f"Tamamlanan sanal işlemler: {total}\n"
        f"TP1: {wins} | Stop: {losses}\n"
        f"Başarı oranı: %{win_rate:.1f}\n"
        f"Brüt işlem yüzdelerinin basit toplamı: {gross:+.2f}%\n"
        f"Açık sanal işlemler: {len(state['active'])}\n\n"
        "<i>Bileşik getiri değildir; komisyon, funding ve kayma dahil değildir.</i>"
    )


# --------------------------------------------------
# TELEGRAM KOMUTLARI
# --------------------------------------------------

def process_commands(state):
    if not BOT_TOKEN:
        return

    try:
        url = (
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            f"?timeout=1&offset={int(state.get('telegram_offset', 0))}"
        )

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "AZCryptoShortScanner/2.0"}
        )

        with urllib.request.urlopen(req, timeout=4) as response:
            result = json.loads(response.read().decode("utf-8"))

        if not result.get("ok"):
            return

        for update in result.get("result", []):
            state["telegram_offset"] = update["update_id"] + 1

            msg = update.get("message", {})

            if str(msg.get("chat", {}).get("id", "")) != CHAT_ID:
                continue

            cmd = (msg.get("text") or "").strip().lower()

            if cmd == "/stats":
                tg(stats_message(state))

            elif cmd == "/status":
                remaining = max(
                    0, int(BINANCE_COOLDOWN_UNTIL - time.time())
                )

                status = (
                    f"Binance beklemesi: {remaining} sn"
                    if remaining
                    else "Tarama hazır"
                )

                tg(
                    f"<b>AZCRYPTO SHORT BOT</b>\n"
                    f"Durum: {status}\n"
                    f"Sembol üst sınırı: {MAX_SYMBOLS}\n"
                    f"Tarama aralığı: {SCAN_INTERVAL_SECONDS} sn\n"
                    f"Minimum 24s hacim: "
                    f"{MIN_QUOTE_VOLUME_USDT:,.0f} USDT\n"
                    f"Saat: {escape(tr_saat())}"
                )

        save_state(state)

    except Exception as exc:
        log.debug("Telegram komut kontrolü: %s", exc)


# --------------------------------------------------
# ANA TARAMA DÖNGÜSÜ
# --------------------------------------------------

def bot_loop():
    log.info(
        "AZCRYPTO SHORT SCANNER başlatıldı; "
        "tarama=%ss, sembol üst sınırı=%s",
        SCAN_INTERVAL_SECONDS, MAX_SYMBOLS
    )

    load_cooldown()
    state = load_state()
    last_cooldown_log = 0

    while True:
        remaining = BINANCE_COOLDOWN_UNTIL - time.time()

        if remaining > 0:
            if time.time() - last_cooldown_log >= 300:
                log.warning(
                    "Binance 418/429 beklemesi sürüyor: "
                    "yaklaşık %d saniye. Yeni Binance isteği gönderilmiyor.",
                    int(remaining)
                )
                last_cooldown_log = time.time()

            time.sleep(min(remaining, 60))
            continue

        started = time.time()

        try:
            snapshot = get_market_snapshot()

            candidates = sorted(
                snapshot.items(),
                key=lambda x: x[1]["quote_volume"],
                reverse=True
            )[:MAX_SYMBOLS]

            sent = 0

            for symbol, market in candidates:
                try:
                    raw = api_json(
                        "/fapi/v1/klines",
                        {
                            "symbol": symbol,
                            "interval": "1m",
                            "limit": 50
                        }
                    )

                    if not raw or len(raw) < 30:
                        continue

                    update_active_trade(state, symbol, raw)

                    last_signal = float(
                        state["last_signal"].get(symbol, 0)
                    )

                    if time.time() - last_signal < COOLDOWN_SECONDS:
                        continue

                    setup = detect_short_setup(raw)

                    if not setup:
                        continue

                    extra = extra_market_metrics(symbol)
                    message = send_signal(
                        symbol, market, setup, extra
                    )

                    if tg(message):
                        state["last_signal"][symbol] = time.time()

                        state["active"][symbol] = {
                            "entry": setup["entry"],
                            "stop": setup["stop"],
                            "tp1": setup["tp1"],
                            "tp2": setup["tp2"],
                            "time": setup["time"]
                        }

                        save_state(state)
                        sent += 1

                        log.info(
                            "SHORT sinyali gönderildi: %s",
                            symbol
                        )

                    time.sleep(0.25)

                except BinanceRateLimitError:
                    raise

                except Exception:
                    log.exception(
                        "Sembol tarama hatası: %s",
                        symbol
                    )
                    time.sleep(0.5)

            process_commands(state)

            log.info(
                "Tarama tamamlandı | aday coin: %d | "
                "gönderilen sinyal: %d | süre: %.1fs",
                len(candidates),
                sent,
                time.time() - started
            )

        except BinanceRateLimitError as exc:
            log.warning(
                "Binance taraması durduruldu: %s",
                exc
            )

        except Exception:
            log.exception("Ana tarama döngüsü hatası")

        elapsed = time.time() - started
        time.sleep(max(1, SCAN_INTERVAL_SECONDS - elapsed))


# --------------------------------------------------
# RENDER WEB SERVİSİ VE CRON PING
# --------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (
            "AZCRYPTO SHORT SCANNER is running.\n"
            f"Time: {tr_saat()}\n"
            "Signal-only mode; no exchange orders are placed.\n"
        ).encode("utf-8")

        self.send_response(200)
        self.send_header(
            "Content-Type", "text/plain; charset=utf-8"
        )
        self.send_header(
            "Content-Length", str(len(body))
        )
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, fmt, *args):
        log.debug("HTTP: " + fmt, *args)


def run_web_server():
    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        Handler
    )

    log.info("Web health server port %s", PORT)
    server.serve_forever()


if __name__ == "__main__":
    threading.Thread(
        target=bot_loop,
        daemon=True,
        name="scanner"
    ).start()

    run_web_server()

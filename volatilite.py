import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError
import re
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape

# AZCRYPTO SHORT SCANNER — Binance USDT-M Futures
# Yalnızca sinyal gönderir; gerçek emir açmaz.

BASE_URL = "https://fapi.binance.com"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "1623907197").strip()
PORT = int(os.environ.get("PORT", "10000"))

SCAN_INTERVAL_SECONDS = max(
    120, int(os.environ.get("SCAN_INTERVAL_SECONDS", "180"))
)
MAX_SYMBOLS = max(3, min(10, int(os.environ.get("MAX_SYMBOLS", "8"))))
MIN_QUOTE_VOLUME_USDT = float(
    os.environ.get("MIN_QUOTE_VOLUME_USDT", "5000000")
)
MIN_PUMP_5M_PCT = float(os.environ.get("MIN_PUMP_5M_PCT", "1.5"))
MIN_SINGLE_CANDLE_PCT = float(
    os.environ.get("MIN_SINGLE_CANDLE_PCT", "1.0")
)
MIN_VOLUME_RATIO = float(os.environ.get("MIN_VOLUME_RATIO", "1.2"))
RSI_MIN = float(os.environ.get("RSI_MIN", "60"))
COOLDOWN_SECONDS = int(os.environ.get("COOLDOWN_SECONDS", "1200"))
DATA_FILE = os.environ.get("DATA_FILE", "shortbot_data.json")

TR_TZ = timezone(timedelta(hours=3))

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("azcrypto-short")

BINANCE_COOLDOWN_SECONDS = int(
    os.environ.get("BINANCE_COOLDOWN_SECONDS", "7200")
)
COOLDOWN_FILE = os.environ.get(
    "COOLDOWN_FILE", "binance_cooldown.json"
)

_saved_pause = 0.0
try:
    with open(COOLDOWN_FILE, "r", encoding="utf-8") as f:
        _saved_pause = float(json.load(f).get("until", 0))
except Exception:
    pass

BINANCE_COOLDOWN_UNTIL = _saved_pause
TELEGRAM_POLLING_DISABLED = False


class BinanceRateLimitError(Exception):
    pass


def tr_saat():
    return datetime.now(TR_TZ).strftime("%Y-%m-%d %H:%M:%S")


def api_json(path, params=None, timeout=12):
    global BINANCE_COOLDOWN_UNTIL

    remaining = int(BINANCE_COOLDOWN_UNTIL - time.time())
    if remaining > 0:
        raise BinanceRateLimitError(
            f"Binance beklemede: {remaining} saniye"
        )

    query = urllib.parse.urlencode(params or {})
    url = BASE_URL + path + (("?" + query) if query else "")

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "AZCryptoShortScanner/1.0",
            "Accept": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    except HTTPError as exc:
        if exc.code in (418, 429):
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            except Exception:
                pass

            wait_seconds = (
                600 if exc.code == 429
                else BINANCE_COOLDOWN_SECONDS
            )

            retry_after = (
                exc.headers.get("Retry-After")
                if exc.headers else None
            )

            try:
                if retry_after:
                    wait_seconds = max(
                        wait_seconds, int(float(retry_after))
                    )
            except (TypeError, ValueError):
                pass

            match = re.search(r"(\d+)\s*seconds?", body, re.I)
            if match:
                wait_seconds = max(
                    wait_seconds, int(match.group(1))
                )

            BINANCE_COOLDOWN_UNTIL = time.time() + wait_seconds

            try:
                with open(COOLDOWN_FILE, "w", encoding="utf-8") as f:
                    json.dump(
                        {"until": BINANCE_COOLDOWN_UNTIL}, f
                    )
            except Exception as save_exc:
                log.warning(
                    "Bekleme süresi kaydedilemedi: %s", save_exc
                )

            log.error(
                "Binance HTTP %s: %d saniye bekleme; istekler durduruldu.",
                exc.code, wait_seconds
            )

            raise BinanceRateLimitError(
                f"Binance HTTP {exc.code}; "
                f"{wait_seconds} saniye bekleniyor"
            ) from exc

        raise


def tg(message):
    if not BOT_TOKEN or not CHAT_ID:
        log.warning(
            "BOT_TOKEN veya CHAT_ID eksik; Telegram mesajı gönderilmedi."
        )
        return False

    payload = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true"
    }).encode("utf-8")

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))

        if not result.get("ok"):
            log.error("Telegram API hata yanıtı: %s", result)
            return False

        return True

    except Exception:
        log.exception("Telegram mesajı gönderilemedi")
        return False


def load_state():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        state.setdefault("last_signal", {})
        state.setdefault("active", {})
        state.setdefault("history", [])
        return state

    except (OSError, ValueError):
        return {
            "last_signal": {},
            "active": {},
            "history": []
        }


def save_state(state):
    temp_file = DATA_FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    os.replace(temp_file, DATA_FILE)


def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0

    deltas = [
        prices[i] - prices[i - 1]
        for i in range(1, len(prices))
    ]

    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

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
    return 100.0 - (100.0 / (1.0 + rs))


def average(values):
    return sum(values) / len(values) if values else 0.0


def get_trading_symbols():
    info = api_json("/fapi/v1/exchangeInfo")
    symbols = set()

    excluded_suffixes = (
        "UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT"
    )

    for item in info.get("symbols", []):
        symbol = item.get("symbol", "")

        if (
            item.get("status") == "TRADING"
            and item.get("contractType") == "PERPETUAL"
            and item.get("quoteAsset") == "USDT"
            and symbol.endswith("USDT")
            and not symbol.endswith(excluded_suffixes)
        ):
            symbols.add(symbol)

    return symbols


def get_market_snapshot(allowed_symbols):
    tickers = api_json("/fapi/v1/ticker/24hr")
    result = {}

    for ticker in tickers:
        symbol = ticker.get("symbol")

        if symbol not in allowed_symbols:
            continue

        try:
            quote_volume = float(ticker["quoteVolume"])
            last_price = float(ticker["lastPrice"])
            change_24h = float(ticker["priceChangePercent"])

            if (
                quote_volume >= MIN_QUOTE_VOLUME_USDT
                and last_price > 0
            ):
                result[symbol] = {
                    "price": last_price,
                    "quote_volume": quote_volume,
                    "change_24h": change_24h,
                    "high_24h": float(ticker["highPrice"]),
                    "low_24h": float(ticker["lowPrice"])
                }

        except (KeyError, TypeError, ValueError):
            continue

    return result


def candle_data(raw):
    return {
        "open_time": int(raw[0]),
        "open": float(raw[1]),
        "high": float(raw[2]),
        "low": float(raw[3]),
        "close": float(raw[4]),
        "volume": float(raw[5]),
        "quote_volume": float(raw[7]),
        "taker_buy_quote": float(raw[10]),
        "close_time": int(raw[6])
    }


def detect_short_setup(raw_klines):
    # Açık olan son mumu kullanma; yalnızca kapanmış mumları değerlendir.
    if not raw_klines or len(raw_klines) < 30:
        return None

    candles = [candle_data(x) for x in raw_klines[:-1]]
    closes = [c["close"] for c in candles]

    current = candles[-1]
    previous = candles[-2]

    pump_window = candles[-6:-1]

    if len(pump_window) < 5 or pump_window[0]["open"] <= 0:
        return None

    pump_pct = (
        pump_window[-1]["close"] / pump_window[0]["open"] - 1.0
    ) * 100.0

    one_candle_pct = (
        previous["close"] / previous["open"] - 1.0
    ) * 100.0

    rsi = calculate_rsi(closes[:-1], 14)

    prior_volumes = [
        c["quote_volume"] for c in candles[-22:-2]
    ]

    avg_volume = average(prior_volumes)

    volume_ratio = (
        current["quote_volume"] / avg_volume
        if avg_volume > 0 else 0.0
    )

    recent_quote = sum(
        c["quote_volume"] for c in candles[-30:]
    )

    recent_taker_buy = sum(
        c["taker_buy_quote"] for c in candles[-30:]
    )

    taker_buy_pct = (
        recent_taker_buy / recent_quote * 100.0
        if recent_quote else 0.0
    )

    taker_sell_pct = max(0.0, 100.0 - taker_buy_pct)

    body = abs(current["close"] - current["open"])
    upper_wick = (
        current["high"] -
        max(current["open"], current["close"])
    )
    candle_range = current["high"] - current["low"]

    upper_wick_rejection = (
        current["close"] < current["open"]
        and upper_wick >= max(body * 1.2, candle_range * 0.25)
    )

    support_break = current["close"] < previous["low"]
    bearish_close = current["close"] < current["open"]

    pump_detected = (
        pump_pct >= MIN_PUMP_5M_PCT
        or one_candle_pct >= MIN_SINGLE_CANDLE_PCT
    )

    reversal_confirmed = (
        support_break
        or (bearish_close and upper_wick_rejection)
    )

    if not pump_detected or not reversal_confirmed:
        return None

    if rsi < RSI_MIN:
        return None

    if volume_ratio < MIN_VOLUME_RATIO:
        return None

    ranges = [
        c["high"] - c["low"]
        for c in candles[-15:]
    ]

    atr = average(ranges)
    recent_high = max(c["high"] for c in candles[-6:])

    entry = current["close"]
    stop = recent_high + max(atr * 0.25, entry * 0.001)
    risk = stop - entry

    if risk <= 0 or stop <= entry:
        return None

    tp1 = entry - risk
    tp2 = entry - 2.0 * risk

    if tp2 <= 0:
        return None

    reasons = [
        f"Önceki 5 mum hareketi: %{pump_pct:.2f}"
    ]

    if one_candle_pct >= MIN_SINGLE_CANDLE_PCT:
        reasons.append(
            f"Önceki mum yükselişi: %{one_candle_pct:.2f}"
        )

    reasons.append(
        "Destek kırılımı"
        if support_break
        else "Üst fitil reddi + kırmızı kapanış"
    )

    reasons.append(f"RSI: {rsi:.1f}")
    reasons.append(f"Hacim: ortalamanın {volume_ratio:.1f} katı")

    return {
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "rsi": rsi,
        "pump_pct": pump_pct,
        "volume_ratio": volume_ratio,
        "taker_buy_pct": taker_buy_pct,
        "taker_sell_pct": taker_sell_pct,
        "risk_pct": (risk / entry) * 100.0,
        "reasons": reasons,
        "candle_high": current["high"],
        "candle_low": current["low"],
        "time": tr_saat(),
        "candle_close_time": current["close_time"]
    }


def ema(values, period):
    if not values:
        return 0.0

    k = 2.0 / (period + 1.0)
    value = values[0]

    for item in values[1:]:
        value = item * k + value * (1.0 - k)

    return value


def pct_change_from_klines(symbol, interval, candles_back):
    try:
        raw = api_json(
            "/fapi/v1/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "limit": candles_back + 3
            }
        )

        if not isinstance(raw, list) or len(raw) < candles_back + 2:
            return None

        closed = raw[:-1]
        old = float(closed[-(candles_back + 1)][4])
        new = float(closed[-1][4])

        return (new / old - 1.0) * 100.0 if old else None

    except BinanceRateLimitError:
        raise

    except Exception:
        return None


def extra_market_metrics(symbol, current_price):
    result = {
        "m1": None,
        "m5": None,
        "m15": None,
        "ema11": None,
        "ema21": None,
        "oi_change": None,
        "long_short": None,
        "month_high": None,
        "month_low": None,
        "year_high": None,
        "year_low": None,
        "score": 0
    }

    try:
        result["m1"] = pct_change_from_klines(symbol, "1m", 1)
        result["m5"] = pct_change_from_klines(symbol, "5m", 1)
        result["m15"] = pct_change_from_klines(symbol, "15m", 1)

        raw = api_json(
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": "1m", "limit": 80}
        )

        if isinstance(raw, list) and len(raw) >= 25:
            closes = [float(x[4]) for x in raw[:-1]]
            result["ema11"] = ema(closes[-40:], 11)
            result["ema21"] = ema(closes[-60:], 21)

        oi = api_json(
            "/fapi/v1/openInterest",
            {"symbol": symbol}
        )

        oi_hist = api_json(
            "/futures/data/openInterestHist",
            {
                "symbol": symbol,
                "period": "5m",
                "limit": 2
            }
        )

        if isinstance(oi_hist, list) and len(oi_hist) >= 2:
            old_oi = float(oi_hist[-2].get("sumOpenInterest", 0))
            new_oi = float(oi_hist[-1].get("sumOpenInterest", 0))

            if old_oi > 0:
                result["oi_change"] = (
                    new_oi / old_oi - 1.0
                ) * 100.0

        ls = api_json(
            "/futures/data/globalLongShortAccountRatio",
            {
                "symbol": symbol,
                "period": "5m",
                "limit": 1
            }
        )

        if isinstance(ls, list) and ls:
            result["long_short"] = float(
                ls[-1].get("longShortRatio", 0)
            )

        daily = api_json(
            "/fapi/v1/klines",
            {
                "symbol": symbol,
                "interval": "1d",
                "limit": 365
            }
        )

        if isinstance(daily, list) and daily:
            highs = [float(x[2]) for x in daily]
            lows = [float(x[3]) for x in daily]

            result["month_high"] = max(highs[-30:])
            result["month_low"] = min(lows[-30:])
            result["year_high"] = max(highs)
            result["year_low"] = min(lows)

        score = 0

        if result["m1"] is not None and result["m1"] < 0:
            score += 1

        if result["m5"] is not None and result["m5"] < 0:
            score += 1

        if result["m15"] is not None and result["m15"] < 0:
            score += 1

        if (
            result["ema11"] is not None
            and result["ema21"] is not None
            and result["ema11"] < result["ema21"]
        ):
            score += 1

        if result["oi_change"] is not None and result["oi_change"] > 0:
            score += 1

        if (
            result["long_short"] is not None
            and result["long_short"] > 1
        ):
            score += 1

        result["score"] = score

    except BinanceRateLimitError:
        raise

    except Exception as exc:
        log.debug(
            "Ek piyasa verisi alınamadı %s: %s", symbol, exc
        )

    return result


def fmt_price(price):
    if price >= 1000:
        return f"{price:,.2f}"

    if price >= 1:
        return f"{price:.4f}"

    if price >= 0.01:
        return f"{price:.6f}"

    return f"{price:.8f}"


def update_active_trade(state, symbol, candles):
    trade = state["active"].get(symbol)

    if not trade or len(candles) < 2:
        return

    # Aynı mumda stop ve hedefe değerse temkinli olarak stop kabul edilir.
    last = candle_data(candles[-2])

    if last["high"] >= trade["stop"]:
        outcome = "STOP"
        exit_price = trade["stop"]

    elif last["low"] <= trade["tp1"]:
        outcome = "TP1"
        exit_price = trade["tp1"]

    else:
        return

    entry = trade["entry"]
    pnl_pct = ((entry - exit_price) / entry) * 100.0

    record = {
        "symbol": symbol,
        "entry_time": trade["time"],
        "exit_time": tr_saat(),
        "entry": entry,
        "exit": exit_price,
        "outcome": outcome,
        "gross_pnl_pct": pnl_pct
    }

    state["history"].append(record)
    state["history"] = state["history"][-2000:]

    del state["active"][symbol]
    save_state(state)

    emoji = "✅" if outcome == "TP1" else "🛑"

    tg(
        f"{emoji} <b>SHORT SANAL İŞLEM SONUCU</b>\n"
        f"#{escape(symbol)} · Sonuç: <b>{outcome}</b>\n"
        f"Giriş: {fmt_price(entry)}\n"
        f"Çıkış: {fmt_price(exit_price)}\n"
        f"Brüt fiyat sonucu: {pnl_pct:+.2f}%\n"
        f"<i>Komisyon/funding düşülmemiştir. Bu kayıt simülasyondur.</i>"
    )


def stats_message(state):
    history = state.get("history", [])

    wins = sum(
        1 for item in history if item.get("outcome") == "TP1"
    )
    losses = sum(
        1 for item in history if item.get("outcome") == "STOP"
    )

    closed = wins + losses

    gross = sum(
        float(item.get("gross_pnl_pct", 0.0))
        for item in history
    )

    win_rate = wins / closed * 100.0 if closed else 0.0

    return (
        "<b>AZCRYPTO SHORT İSTATİSTİKLERİ</b>\n"
        f"Tamamlanan sanal işlemler: {closed}\n"
        f"TP1: {wins} | Stop: {losses}\n"
        f"Başarı oranı: %{win_rate:.1f}\n"
        f"İşlem yüzdelerinin basit toplamı: {gross:+.2f}%\n"
        f"Açık sanal işlemler: {len(state.get('active', {}))}\n\n"
        "<i>Basit brüt fiyat ölçümüdür; bileşik getiri değildir. "
        "Komisyon, funding, kayma ve gerçek emir gerçekleşmesi dahil değildir.</i>"
    )


def send_signal(symbol, snapshot, setup):
    link = (
        f"https://www.binance.com/en/futures/"
        f"{urllib.parse.quote(symbol)}"
    )

    reasons = "\n".join(
        "• " + escape(reason)
        for reason in setup["reasons"]
    )

    extra = setup.get("extra", {})

    def pct(key):
        value = extra.get(key)
        return "N/A" if value is None else f"{value:+.2f}%"

    def price_or_na(key):
        value = extra.get(key)
        return "N/A" if value is None else fmt_price(value)

    if extra.get("ema11") is None or extra.get("ema21") is None:
        ema_text = "N/A"
    elif extra["ema11"] < extra["ema21"]:
        ema_text = "EMA11 < EMA21 (ayı)"
    else:
        ema_text = "EMA11 ≥ EMA21 (yukarı)"

    oi_text = (
        "N/A"
        if extra.get("oi_change") is None
        else f"{extra['oi_change']:+.2f}% / 5dk"
    )

    ls_text = (
        "N/A"
        if extra.get("long_short") is None
        else f"{extra['long_short']:.2f} (hesap oranı)"
    )

    return (
        f"🔻 <b>SHORT ADAYI — TEYİTLİ DÖNÜŞ</b>\n"
        f"<b>#{escape(symbol)} · Binance USDT-M Futures</b>\n"
        f"⭐ Sinyal skoru: <b>{extra.get('score', 0)}/6</b>\n\n"
        f"🏷 Giriş referansı: <code>{fmt_price(setup['entry'])}</code>\n"
        f"🛑 Stop-loss: <code>{fmt_price(setup['stop'])}</code> "
        f"(girişten +%{setup['risk_pct']:.2f})\n"
        f"🎯 TP1 (1R): <code>{fmt_price(setup['tp1'])}</code>\n"
        f"🎯 TP2 (2R): <code>{fmt_price(setup['tp2'])}</code>\n\n"
        f"⏱ <b>Zaman dilimleri (son kapanmış mum)</b>\n"
        f"• 1dk: {pct('m1')} | 5dk: {pct('m5')} | 15dk: {pct('m15')}\n"
        f"📉 EMA: {ema_text}\n"
        f"📊 RSI(1dk): {setup['rsi']:.1f} | "
        f"Hacim: ortalamanın {setup['volume_ratio']:.1f} katı\n"
        f"⚖️ Taker alış: {setup['taker_buy_pct']:.1f}% | "
        f"satış: {setup['taker_sell_pct']:.1f}% (30dk)\n"
        f"📈 24s değişim: {snapshot['change_24h']:+.2f}% | "
        f"24s hacim: {snapshot['quote_volume']/1_000_000:.2f}M USDT\n"
        f"⬆️ 24s High: {fmt_price(snapshot['high_24h'])} | "
        f"⬇️ Low: {fmt_price(snapshot['low_24h'])}\n"
        f"💹 Açık pozisyon değişimi: {oi_text}\n"
        f"👥 Long/Short hesap oranı: {ls_text}\n"
        f"🗓 30g High/Low: "
        f"{price_or_na('month_high')} / {price_or_na('month_low')}\n"
        f"📆 365g High/Low: "
        f"{price_or_na('year_high')} / {price_or_na('year_low')}\n\n"
        f"🔎 <b>Dönüş teyitleri</b>\n{reasons}\n\n"
        f"🔗 <a href='{link}'>Binance Futures paritesi</a>\n"
        f"🕒 {escape(tr_saat())}\n\n"
        "<i>Bilgilendirme amaçlı sinyaldir; garanti değildir. "
        "Bot gerçek emir açmaz. Ek piyasa metrikleri Binance API "
        "yanıtına göre N/A görünebilir. Komisyon, funding ve kayma "
        "hesaba katılmaz.</i>"
    )


def process_commands(state):
    global TELEGRAM_POLLING_DISABLED

    if not BOT_TOKEN or TELEGRAM_POLLING_DISABLED:
        return

    offset = state.get("telegram_offset", 0)

    try:
        url = (
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            f"?timeout=1&offset={int(offset)}"
        )

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "AZCryptoShortScanner/1.0"}
        )

        with urllib.request.urlopen(req, timeout=3) as response:
            updates = json.loads(response.read().decode("utf-8"))

        if not updates or not updates.get("ok"):
            return

        for item in updates.get("result", []):
            state["telegram_offset"] = item["update_id"] + 1

            message = item.get("message", {})
            chat_id = str(message.get("chat", {}).get("id", ""))
            command = (message.get("text") or "").strip().lower()

            if chat_id != CHAT_ID:
                continue

            if command == "/stats":
                tg(stats_message(state))

            elif command == "/status":
                tg(
                    "<b>AZCRYPTO SHORT BOT AKTİF</b>\n"
                    f"Sembol üst sınırı: {MAX_SYMBOLS}\n"
                    f"Tarama aralığı: {SCAN_INTERVAL_SECONDS} saniye\n"
                    f"Minimum 24s hacim: "
                    f"{MIN_QUOTE_VOLUME_USDT:,.0f} USDT\n"
                    f"Zaman: {escape(tr_saat())}"
                )

        save_state(state)

    except HTTPError as exc:
        if exc.code == 409:
            TELEGRAM_POLLING_DISABLED = True
            log.error(
                "Telegram 409: başka bir süreç aynı BOT_TOKEN ile "
                "getUpdates kullanıyor. Yalnızca tek servis bırakın."
            )
        else:
            log.warning("Telegram komut HTTP hatası: %s", exc)

    except Exception as exc:
        log.debug("Telegram komut kontrolü: %s", exc)


def bot_loop():
    log.info("AZCRYPTO SHORT SCANNER başlatıldı")

    if not BOT_TOKEN:
        log.warning(
            "BOT_TOKEN ortam değişkeni ayarlanmamış; "
            "Telegram'a sinyal gönderilemez."
        )

    state = load_state()
    allowed_symbols = set()

    while True:
        if time.time() < BINANCE_COOLDOWN_UNTIL:
            remaining = max(
                1, int(BINANCE_COOLDOWN_UNTIL - time.time())
            )

            log.warning(
                "Binance bekleme süresi: %d saniye; tarama duraklatıldı.",
                remaining
            )

            time.sleep(min(remaining, 60))
            continue

        started = time.time()

        try:
            if not allowed_symbols:
                allowed_symbols = get_trading_symbols()

                log.info(
                    "İşlem gören USDT perpetual pariteleri: %d",
                    len(allowed_symbols)
                )

            snapshot = get_market_snapshot(allowed_symbols)

            candidates = sorted(
                snapshot.items(),
                key=lambda item: item[1]["quote_volume"],
                reverse=True
            )[:MAX_SYMBOLS]

            signals_sent = 0

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

                    if (
                        time.time()
                        - float(state["last_signal"].get(symbol, 0))
                        < COOLDOWN_SECONDS
                    ):
                        continue

                    setup = detect_short_setup(raw)

                    if not setup:
                        continue

                    setup["extra"] = extra_market_metrics(
                        symbol, setup["entry"]
                    )

                    message = send_signal(symbol, market, setup)

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
                        signals_sent += 1

                        log.info(
                            "SHORT adayı gönderildi: %s", symbol
                        )

                    time.sleep(0.08)

                except BinanceRateLimitError:
                    break

                except Exception:
                    log.exception(
                        "Sembol tarama hatası: %s", symbol
                    )

            process_commands(state)

            log.info(
                "Tarama tamamlandı | aday coin: %d | "
                "gönderilen sinyal: %d | süre: %.1fs",
                len(candidates),
                signals_sent,
                time.time() - started
            )

        except BinanceRateLimitError as exc:
            log.warning("Binance taraması bekletildi: %s", exc)

        except Exception:
            log.exception("Ana tarama döngüsü hatası")
            allowed_symbols = set()

        elapsed = time.time() - started
        time.sleep(max(1, SCAN_INTERVAL_SECONDS - elapsed))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (
            "AZCRYPTO SHORT SCANNER is running.\n"
            f"Time: {tr_saat()}\n"
            f"Binance cooldown: "
            f"{max(0, int(BINANCE_COOLDOWN_UNTIL - time.time()))} seconds\n"
            "Signal-only mode; no exchange orders are placed.\n"
        ).encode("utf-8")

        self.send_response(200)
        self.send_header(
            "Content-Type", "text/plain; charset=utf-8"
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format_string, *args):
        log.debug("HTTP: " + format_string, *args)


def run_web_server():
    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT), Handler
    )

    log.info("Web health server started on port %d", PORT)
    server.serve_forever()


if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    run_web_server()

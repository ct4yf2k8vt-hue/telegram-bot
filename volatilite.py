import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape

# AZCRYPTO SHORT SCANNER — Binance USDT-M Futures
# Sadece analiz/sinyal üretir; gerçek emir açmaz.
BASE_URL = "https://fapi.binance.com"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "1623907197").strip()
PORT = int(os.environ.get("PORT", "10000"))
SCAN_INTERVAL_SECONDS = max(180, int(os.environ.get("SCAN_INTERVAL_SECONDS", "240")))
MAX_SYMBOLS = max(3, min(6, int(os.environ.get("MAX_SYMBOLS", "5"))))
MIN_QUOTE_VOLUME_USDT = float(os.environ.get("MIN_QUOTE_VOLUME_USDT", "5000000"))
MIN_PUMP_5M_PCT = float(os.environ.get("MIN_PUMP_5M_PCT", "1.5"))
MIN_SINGLE_CANDLE_PCT = float(os.environ.get("MIN_SINGLE_CANDLE_PCT", "1.0"))
MIN_VOLUME_RATIO = float(os.environ.get("MIN_VOLUME_RATIO", "1.2"))
RSI_MIN = float(os.environ.get("RSI_MIN", "60"))
COOLDOWN_SECONDS = int(os.environ.get("COOLDOWN_SECONDS", "1200"))
BINANCE_COOLDOWN_SECONDS = int(os.environ.get("BINANCE_COOLDOWN_SECONDS", "7200"))
DATA_FILE = os.environ.get("DATA_FILE", "shortbot_data.json")
COOLDOWN_FILE = os.environ.get("COOLDOWN_FILE", "binance_cooldown.json")
TR_TZ = timezone(timedelta(hours=3))

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("azcrypto-short")
_state_lock = threading.Lock()
_last_signal = {}
BINANCE_COOLDOWN_UNTIL = 0.0
try:
    with open(COOLDOWN_FILE, "r", encoding="utf-8") as f:
        BINANCE_COOLDOWN_UNTIL = float(json.load(f).get("until", 0))
except Exception:
    pass


def tr_saat():
    return datetime.now(TR_TZ).strftime("%Y-%m-%d %H:%M:%S")


def load_state():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"signals": [], "stats": {}}
    except Exception:
        return {"signals": [], "stats": {}}


def save_state(data):
    try:
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)
    except Exception as exc:
        log.warning("Bot verisi kaydedilemedi: %s", exc)


def api_json(path, params=None, timeout=12):
    global BINANCE_COOLDOWN_UNTIL
    remaining = int(BINANCE_COOLDOWN_UNTIL - time.time())
    if remaining > 0:
        raise RuntimeError(f"Binance bekleme süresi: {remaining} saniye")
    query = urllib.parse.urlencode(params or {})
    req = urllib.request.Request(BASE_URL + path + ("?" + query if query else ""),
        headers={"User-Agent": "AZCryptoShortScanner/2.0", "Accept": "application/json"})
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
            wait = 600 if exc.code == 429 else BINANCE_COOLDOWN_SECONDS
            try:
                retry = exc.headers.get("Retry-After") if exc.headers else None
                if retry:
                    wait = max(wait, int(float(retry)))
            except (TypeError, ValueError):
                pass
            match = re.search(r"(\d+)\s*seconds?", body, re.I)
            if match:
                wait = max(wait, int(match.group(1)))
            BINANCE_COOLDOWN_UNTIL = time.time() + wait
            try:
                with open(COOLDOWN_FILE, "w", encoding="utf-8") as f:
                    json.dump({"until": BINANCE_COOLDOWN_UNTIL}, f)
            except Exception as save_exc:
                log.warning("Bekleme süresi kaydedilemedi: %s", save_exc)
            log.error("Binance HTTP %s: %d saniye bekleme; API çağrıları durduruldu.", exc.code, wait)
            raise RuntimeError(f"Binance HTTP {exc.code}; {wait} saniye bekleniyor") from exc
        raise


def tg(message):
    if not BOT_TOKEN or not CHAT_ID:
        log.error("BOT_TOKEN veya CHAT_ID eksik; Telegram mesajı gönderilemedi.")
        return False
    payload = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": message,
        "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok"):
            log.error("Telegram API hatası: %s", result)
            return False
        return True
    except Exception as exc:
        log.warning("Telegram mesajı gönderilemedi: %s", exc)
        return False


def rsi(values, period=14):
    if len(values) < period + 1:
        return 50.0
    gains, losses = 0.0, 0.0
    for i in range(-period, 0):
        change = values[i] - values[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain, avg_loss = gains / period, losses / period
    if avg_loss == 0:
        return 100.0 if avg_gain else 50.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


def pct_change(old, new):
    return ((new - old) / old * 100) if old else 0.0


def scan_once():
    tickers = api_json("/fapi/v1/ticker/24hr", timeout=15)
    candidates = []
    for t in tickers:
        symbol = t.get("symbol", "")
        if not symbol.endswith("USDT") or "_" in symbol:
            continue
        try:
            qv = float(t.get("quoteVolume", 0))
            change24 = float(t.get("priceChangePercent", 0))
            price = float(t.get("lastPrice", 0))
            high24, low24 = float(t.get("highPrice", 0)), float(t.get("lowPrice", 0))
        except (TypeError, ValueError):
            continue
        if qv >= MIN_QUOTE_VOLUME_USDT and price > 0 and change24 > 0.5:
            candidates.append((qv, symbol, price, change24, high24, low24))
    candidates.sort(reverse=True)
    sent = 0
    for qv, symbol, last_price, change24, high24, low24 in candidates[:MAX_SYMBOLS]:
        try:
            candles = api_json("/fapi/v1/klines", {"symbol": symbol, "interval": "1m", "limit": 35}, timeout=12)
            if not isinstance(candles, list) or len(candles) < 20:
                continue
            # Binance candle: open time, open, high, low, close, volume, close time, quote volume, ... taker buy base, taker buy quote
            closes = [float(c[4]) for c in candles[:-1]]  # exclude current unfinished candle
            finished = candles[:-1]
            if len(closes) < 16:
                continue
            current = closes[-1]
            five_min_old = closes[-6] if len(closes) >= 6 else closes[0]
            one_min_old = closes[-2]
            pump5 = pct_change(five_min_old, current)
            change1 = pct_change(one_min_old, current)
            rsi_value = rsi(closes)
            last_candle = finished[-1]
            candle_open, candle_close = float(last_candle[1]), float(last_candle[4])
            candle_pct = pct_change(candle_open, candle_close)
            recent_vol = sum(float(c[5]) for c in finished[-5:]) / 5
            previous_vols = [float(c[5]) for c in finished[-15:-5]]
            prev_vol = sum(previous_vols) / max(1, len(previous_vols))
            vol_ratio = recent_vol / prev_vol if prev_vol else 0
            taker_buy_quote = sum(float(c[10]) for c in finished[-5:])
            total_quote = sum(float(c[7]) for c in finished[-5:])
            taker_sell_quote = max(0.0, total_quote - taker_buy_quote)
            if pump5 < MIN_PUMP_5M_PCT or candle_pct < MIN_SINGLE_CANDLE_PCT or rsi_value < RSI_MIN or vol_ratio < MIN_VOLUME_RATIO:
                continue
            with _state_lock:
                last_time = _last_signal.get(symbol, 0)
                if time.time() - last_time < COOLDOWN_SECONDS:
                    continue
                _last_signal[symbol] = time.time()
            entry = current
            sl = entry * 1.012
            tp1 = entry * 0.988
            tp2 = entry * 0.978
            score = min(99, int(45 + min(pump5, 5) * 5 + min(max(rsi_value - 60, 0), 25) * 0.5 + min(vol_ratio, 3) * 5))
            message = (
                f"<b>🔻 AZCRYPTO SHORT SİNYALİ</b>\n"
                f"<b>Parite:</b> <a href=\"https://www.binance.com/en/futures/{escape(symbol)}\">{escape(symbol)}</a>\n"
                f"<b>Giriş (izleme):</b> {entry:.8g}\n"
                f"<b>Stop Loss:</b> {sl:.8g} (+1.2%)\n"
                f"<b>TP1:</b> {tp1:.8g} (-1.2%) | <b>TP2:</b> {tp2:.8g} (-2.2%)\n"
                f"<b>Skor:</b> {score}/100\n\n"
                f"<b>1 dk:</b> {change1:+.2f}% | <b>5 dk:</b> {pump5:+.2f}%\n"
                f"<b>RSI(14):</b> {rsi_value:.1f} | <b>Hacim oranı:</b> {vol_ratio:.2f}x\n"
                f"<b>Taker alım/satım (5 dk):</b> {taker_buy_quote:,.0f} / {taker_sell_quote:,.0f} USDT\n"
                f"<b>24s değişim:</b> {change24:+.2f}% | <b>24s hacim:</b> {qv:,.0f} USDT\n"
                f"<b>24s yüksek/düşük:</b> {high24:.8g} / {low24:.8g}\n"
                f"<b>Saat:</b> {tr_saat()}\n\n"
                f"<i>Bu bir izleme sinyalidir, emir açmaz. Stop/TP garanti değildir; riski kendin değerlendir.</i>"
            )
            if tg(message):
                sent += 1
                data = load_state()
                data.setdefault("signals", []).append({"symbol": symbol, "time": tr_saat(), "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "score": score, "status": "OPEN"})
                data["signals"] = data["signals"][-300:]
                save_state(data)
                log.info("SHORT sinyali gönderildi: %s score=%s", symbol, score)
        except RuntimeError as exc:
            log.warning("Tarama durdu: %s", exc)
            break
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError, IndexError) as exc:
            log.warning("%s taranamadı: %s", symbol, exc)
        except Exception:
            log.exception("%s taramasında beklenmeyen hata", symbol)
    log.info("Tarama tamamlandı; gönderilen sinyal: %d", sent)


def scanner_loop():
    # Telegram getUpdates polling kullanılmaz; bu nedenle botun kendisi 409 Conflict üretmez.
    while True:
        try:
            if time.time() < BINANCE_COOLDOWN_UNTIL:
                remaining = int(BINANCE_COOLDOWN_UNTIL - time.time())
                log.warning("Binance beklemesi sürüyor: %d saniye", remaining)
            else:
                scan_once()
        except RuntimeError as exc:
            log.warning("Tarayıcı beklemede: %s", exc)
        except Exception:
            log.exception("Tarama döngüsü hatası")
        time.sleep(SCAN_INTERVAL_SECONDS)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/health", "/status"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return
        data = load_state()
        payload = {"ok": True, "service": "AZCrypto Short Scanner", "time": tr_saat(),
                   "binance_cooldown_seconds": max(0, int(BINANCE_COOLDOWN_UNTIL - time.time())),
                   "saved_signals": len(data.get("signals", [])),
                   "scan_interval_seconds": SCAN_INTERVAL_SECONDS}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        log.info("HTTP %s - %s", self.address_string(), fmt % args)


def main():
    if not BOT_TOKEN:
        log.error("Render Environment içinde BOT_TOKEN tanımlı değil.")
    if not CHAT_ID:
        log.error("Render Environment içinde CHAT_ID tanımlı değil.")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=scanner_loop, name="scanner", daemon=True)
    thread.start()
    log.info("HTTP sağlık sunucusu 0.0.0.0:%s üzerinde başladı.", PORT)
    log.info("Tarama aralığı: %s sn; maksimum sembol: %s", SCAN_INTERVAL_SECONDS, MAX_SYMBOLS)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Kapatma isteği alındı.")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()

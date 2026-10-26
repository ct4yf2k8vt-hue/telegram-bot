
import os
import json
import time
import math
import logging
import threading
import urllib.request
import urllib.parse
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ================== AYARLAR ==================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "1623907197").strip()

BINANCE = "https://fapi.binance.com"
SCAN_INTERVAL = 180          # 3 dakikada bir tarama
MAX_SYMBOLS = 6              # REST isteklerini düşük tut
MIN_QUOTE_VOLUME = 10_000_000
SIGNAL_COOLDOWN = 1800       # Aynı coine 30 dk yeni sinyal yok

COOLDOWN_FILE = "binance_cooldown.json"
STATE_FILE = "bot_state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

lock = threading.RLock()
binance_pause_until = 0
last_signals = {}
active_trades = {}
closed_trades = []
scan_count = 0
last_scan_time = "Henüz tarama yapılmadı"
last_error = ""
last_update_id = 0


# ================== KAYIT / BEKLEME ==================
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
    except Exception as e:
        logging.warning("Kayıt yapılamadı: %s", e)


def load_cooldown():
    global binance_pause_until
    data = load_json(COOLDOWN_FILE, {})
    try:
        binance_pause_until = max(0, float(data.get("until", 0)))
    except Exception:
        binance_pause_until = 0


def set_cooldown(seconds, reason):
    global binance_pause_until, last_error
    seconds = max(60, min(int(seconds), 172800))
    binance_pause_until = max(binance_pause_until, time.time() + seconds)
    last_error = reason
    save_json(COOLDOWN_FILE, {"until": binance_pause_until})
    logging.error(
        "Binance beklemeye alındı: %s. Kalan süre yaklaşık %d saniye.",
        reason, int(binance_pause_until - time.time())
    )


def cooldown_remaining():
    return max(0, int(binance_pause_until - time.time()))


load_cooldown()


# ================== TELEGRAM ==================
def telegram(method, data=None, timeout=25):
    if not BOT_TOKEN:
        logging.error("BOT_TOKEN Render Environment Variables içinde yok.")
        return None

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    encoded = urllib.parse.urlencode(data or {}).encode("utf-8")
    req = urllib.request.Request(url, data=encoded)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result if result.get("ok") else None
    except Exception as e:
        logging.warning("Telegram hatası: %s", e)
        return None


def send(text):
    return telegram("sendMessage", {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true"
    })


# ================== BINANCE REST ==================
class BinancePaused(Exception):
    pass


def api_get(path, params=None):
    remaining = cooldown_remaining()
    if remaining > 0:
        raise BinancePaused(f"{remaining} saniye bekleniyor")

    query = urllib.parse.urlencode(params or {})
    url = BINANCE + path + (("?" + query) if query else "")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="ignore")[:1000]
        except Exception:
            pass

        if e.code == 418:
            # Binance'in Retry-After bilgisine öncelik ver.
            retry = e.headers.get("Retry-After")
            try:
                wait = int(float(retry)) if retry else 7200
            except Exception:
                wait = 7200

            # Bazı yanıtlar kalan bekleme süresini gövdede verir.
            import re
            match = re.search(r"(\d+)\s*seconds?", body, re.I)
            if match:
                wait = max(wait, int(match.group(1)))

            set_cooldown(wait, "HTTP 418 IP/API kısıtlaması")
            raise BinancePaused("HTTP 418")

        if e.code == 429:
            set_cooldown(600, "HTTP 429 istek sınırı")
            raise BinancePaused("HTTP 429")

        raise RuntimeError(f"Binance HTTP {e.code}: {body}")

    except urllib.error.URLError as e:
        raise RuntimeError(f"Binance bağlantı hatası: {e}")


# ================== GÖSTERGELER ==================
def rsi(values, period=14):
    if len(values) <= period:
        return 50.0

    gains = []
    losses = []
    for i in range(len(values) - period, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0 if avg_gain else 50.0

    return 100 - (100 / (1 + avg_gain / avg_loss))


def analyze_symbol(symbol):
    candles = api_get(
        "/fapi/v1/klines",
        {"symbol": symbol, "interval": "1m", "limit": 20}
    )

    if not candles or len(candles) < 16:
        return None

    # Son mum kapanmamış olabileceği için kapanmış mumları kullan.
    candles = candles[:-1]
    opens = [float(c[1]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]
    volumes = [float(c[5]) for c in candles]

    if len(closes) < 15:
        return None

    price = closes[-1]
    if price <= 0:
        return None

    # Son 5 dakikadaki yükseliş
    pump_pct = (closes[-1] / closes[-6] - 1) * 100

    # Kapanan son mumun gövdesi ve üst fitili
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    candle_range = max(h - l, price * 0.000001)
    upper_wick = h - max(o, c)
    red_candle = c < o
    upper_wick_reversal = upper_wick / candle_range >= 0.35

    previous_volumes = volumes[-11:-1]
    avg_volume = sum(previous_volumes) / max(1, len(previous_volumes))
    volume_ratio = volumes[-1] / avg_volume if avg_volume > 0 else 0

    current_rsi = rsi(closes)
    reversal = red_candle or upper_wick_reversal

    # Sert yükseliş + dönüş işareti + hacim + RSI teyidi
    if (
        pump_pct >= 1.0
        and reversal
        and volume_ratio >= 1.15
        and current_rsi >= 55
    ):
        return {
            "symbol": symbol,
            "price": price,
            "pump": pump_pct,
            "rsi": current_rsi,
            "volume_ratio": volume_ratio,
            "red": red_candle,
            "wick": upper_wick_reversal
        }

    return None


# ================== SANAL İŞLEMLER ==================
def fmt_price(value):
    if value >= 1000:
        return f"{value:.2f}"
    if value >= 1:
        return f"{value:.4f}"
    return f"{value:.8f}"


def open_simulated_trade(signal):
    symbol = signal["symbol"]
    entry = signal["price"]

    with lock:
        if symbol in active_trades:
            return
        last_signals[symbol] = time.time()
        active_trades[symbol] = {
            "entry": entry,
            "sl": entry * 1.012,
            "tp1": entry * 0.990,
            "tp2": entry * 0.980,
            "opened": time.time(),
            "tp1_hit": False
        }
        save_state()

    text = (
        f"🔻 <b>SHORT SİNYALİ</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"Coin: <b>{symbol}</b>\n"
        f"Giriş: <code>{fmt_price(entry)}</code>\n"
        f"Stop Loss: <code>{fmt_price(entry * 1.012)}</code> (+%1,2)\n"
        f"TP1: <code>{fmt_price(entry * 0.990)}</code> (-%1)\n"
        f"TP2: <code>{fmt_price(entry * 0.980)}</code> (-%2)\n"
        f"━━━━━━━━━━━━━━\n"
        f"5 dk yükseliş: %{signal['pump']:.2f}\n"
        f"RSI: {signal['rsi']:.1f}\n"
        f"Hacim oranı: {signal['volume_ratio']:.2f}x\n"
        f"Dönüş: {'Kırmızı mum' if signal['red'] else 'Üst fitil'}\n"
        f"Mod: <b>Sanal işlem — gerçek emir yok</b>"
    )
    send(text)


def save_state():
    save_json(STATE_FILE, {
        "last_signals": last_signals,
        "active_trades": active_trades,
        "closed_trades": closed_trades[-200:]
    })


def load_state():
    global last_signals, active_trades, closed_trades
    data = load_json(STATE_FILE, {})
    try:
        last_signals = {
            k: float(v) for k, v in data.get("last_signals", {}).items()
        }
        active_trades = data.get("active_trades", {})
        closed_trades = data.get("closed_trades", [])
    except Exception:
        last_signals, active_trades, closed_trades = {}, {}, []


load_state()


def update_trades(ticker_prices):
    messages = []

    with lock:
        for symbol in list(active_trades.keys()):
            trade = active_trades[symbol]
            try:
                price = ticker_prices.get(symbol)
                if price is None:
                    continue
                price = float(price)
                entry = float(trade["entry"])

                if price >= float(trade["sl"]):
                    pnl = (entry - price) / entry * 100
                    messages.append(
                        f"🛑 <b>SHORT SL</b> {symbol}\n"
                        f"Son fiyat: {fmt_price(price)}\n"
                        f"Sanal sonuç: %{pnl:.2f}"
                    )
                    closed_trades.append({
                        "symbol": symbol, "result": "SL",
                        "pnl": pnl, "time": time.time()
                    })
                    del active_trades[symbol]
                    continue

                if not trade.get("tp1_hit") and price <= float(trade["tp1"]):
                    trade["tp1_hit"] = True
                    messages.append(
                        f"✅ <b>TP1 gerçekleşti</b> {symbol}\n"
                        f"Fiyat: {fmt_price(price)}\n"
                        f"Sanal sonuç: yaklaşık +%1"
                    )

                if price <= float(trade["tp2"]):
                    pnl = (entry - price) / entry * 100
                    messages.append(
                        f"🎯 <b>TP2 gerçekleşti</b> {symbol}\n"
                        f"Fiyat: {fmt_price(price)}\n"
                        f"Sanal sonuç: +%{pnl:.2f}"
                    )
                    closed_trades.append({
                        "symbol": symbol, "result": "TP2",
                        "pnl": pnl, "time": time.time()
                    })
                    del active_trades[symbol]

            except Exception as e:
                logging.warning("Sanal işlem kontrolü %s: %s", symbol, e)

        save_state()

    for message in messages:
        send(message)


# ================== TARAMA ==================
def scan_once():
    global scan_count, last_scan_time, last_error

    tickers = api_get("/fapi/v1/ticker/24hr")
    if not isinstance(tickers, list):
        raise RuntimeError("Binance ticker yanıtı beklenen formatta değil.")

    ticker_prices = {}
    candidates = []

    for t in tickers:
        symbol = t.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue

        # Yalnızca USDT sürekli vadeli sözleşmelerinin yaygın isim biçimi.
        if "_" in symbol:
            continue

        try:
            price = float(t["lastPrice"])
            quote_volume = float(t["quoteVolume"])
            if price <= 0:
                continue

            ticker_prices[symbol] = price

            if quote_volume >= MIN_QUOTE_VOLUME:
                candidates.append((quote_volume, symbol))
        except (ValueError, KeyError, TypeError):
            continue

    update_trades(ticker_prices)

    candidates.sort(reverse=True)
    symbols = [symbol for _, symbol in candidates[:MAX_SYMBOLS]]

    logging.info("Taramaya alınan coin sayısı: %d", len(symbols))

    for symbol in symbols:
        with lock:
            if symbol in active_trades:
                continue
            if time.time() - float(last_signals.get(symbol, 0)) < SIGNAL_COOLDOWN:
                continue

        try:
            signal = analyze_symbol(symbol)
            if signal:
                logging.info("SHORT sinyali: %s", symbol)
                open_simulated_trade(signal)

        except BinancePaused:
            raise
        except Exception as e:
            logging.warning("%s tarama hatası: %s", symbol, e)

        time.sleep(1.5)

    scan_count += 1
    last_scan_time = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    last_error = ""
    logging.info("Tarama tamamlandı. Tur: %d", scan_count)


def bot_loop():
    global last_error

    logging.info("Bot tarama döngüsü başladı.")

    while True:
        remaining = cooldown_remaining()
        if remaining > 0:
            logging.warning(
                "Binance beklemesi sürüyor: %d saniye.", remaining
            )
            # Beklerken Binance'e istek gönderme.
            time.sleep(min(60, remaining))
            continue

        try:
            scan_once()
        except BinancePaused as e:
            last_error = str(e)
            logging.warning("Binance taraması beklemeye alındı: %s", e)
        except Exception as e:
            last_error = str(e)
            logging.exception("Tarama hatası")
            time.sleep(30)

        time.sleep(SCAN_INTERVAL)


# ================== TELEGRAM KOMUTLARI ==================
def get_status():
    remaining = cooldown_remaining()
    with lock:
        active = len(active_trades)
        closed = len(closed_trades)

    if remaining > 0:
        binance_status = f"⏸ Binance beklemede: {remaining} saniye"
    else:
        binance_status = "🟢 Binance bekleme engeli görünmüyor"

    return (
        "🤖 <b>BOT DURUMU</b>\n"
        f"{binance_status}\n"
        f"Tarama turu: {scan_count}\n"
        f"Açık sanal işlemler: {active}\n"
        f"Kapanan sanal işlemler: {closed}\n"
        f"Son tarama: {last_scan_time}\n"
        f"Son hata: {last_error or 'Yok'}"
    )


def get_stats():
    with lock:
        trades = list(closed_trades)
        active = dict(active_trades)

    wins = sum(1 for t in trades if float(t.get("pnl", 0)) > 0)
    losses = sum(1 for t in trades if float(t.get("pnl", 0)) <= 0)
    pnl = sum(float(t.get("pnl", 0)) for t in trades)

    return (
        "📊 <b>SANAL İŞLEM İSTATİSTİĞİ</b>\n"
        f"Kapanan işlem: {len(trades)}\n"
        f"Kârlı: {wins}\n"
        f"Zararlı: {losses}\n"
        f"Toplam basit sonuç: %{pnl:.2f}\n"
        f"Açık işlemler: {len(active)}\n"
        "Not: Komisyon, kayma ve kaldıraç hesaba katılmaz."
    )


def command_loop():
    global last_update_id

    while True:
        try:
            result = telegram("getUpdates", {
                "offset": last_update_id + 1,
                "timeout": 20,
                "allowed_updates": json.dumps(["message"])
            }, timeout=30)

            if not result:
                time.sleep(3)
                continue

            for update in result.get("result", []):
                last_update_id = max(last_update_id, update.get("update_id", 0))
                message = update.get("message", {})
                chat = message.get("chat", {})
                text_in = message.get("text", "").strip().lower()

                if str(chat.get("id")) != str(CHAT_ID):
                    continue

                if text_in in ("/start", "/help"):
                    send(
                        "Bot komutları:\n"
                        "/status — bot durumu\n"
                        "/stats — sanal işlem sonuçları"
                    )
                elif text_in == "/status":
                    send(get_status())
                elif text_in == "/stats":
                    send(get_stats())

        except Exception as e:
            logging.warning("Telegram komut döngüsü: %s", e)
            time.sleep(5)


# ================== RENDER SAĞLIK KONTROLÜ ==================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (
            "Bot service is alive.\n"
            f"Scan count: {scan_count}\n"
            f"Binance cooldown seconds: {cooldown_remaining()}\n"
        ).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logging.info("Health server port %d üzerinde başladı.", port)


def main():
    start_health_server()

    if not BOT_TOKEN:
        logging.error("BOT_TOKEN tanımlı değil. Render Environment kontrol edilmeli.")
    else:
        send("🤖 <b>SHORT sinyal botu başlatıldı.</b>\n/status ve /stats komutları hazır.")

    threading.Thread(target=command_loop, daemon=True).start()
    bot_loop()


if __name__ == "__main__":
    main()

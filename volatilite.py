
import os
import json
import time
import socket
import threading
import urllib.request
import urllib.parse
import urllib.error
import http.server
import re
from datetime import datetime, timedelta, timezone

import websocket

socket.setdefaulttimeout(20)

# =====================================================
# AYARLAR
# =====================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "1623907197").strip()

TR = timezone(timedelta(hours=3))

FAPI = "https://fapi.binance.com"
WS_URL = "wss://fstream.binance.com/stream?streams="

INTERVAL = "5m"

MAX_PAIRS = 60
MIN_VOLUME = 5_000_000

COOLDOWN = 900
SIGNALS_FILE = "signals.json"

# REST koruması
REST_PAUSE_UNTIL = 0
REST_LOCK = threading.Lock()

# Bot verileri
DATA_LOCK = threading.RLock()
SIGNAL_LOCK = threading.RLock()

histories = {}
last_signal_time = {}
signals = []

ws_connected = False
ws_last_message = 0
ws_lock = threading.Lock()


# =====================================================
# LOG
# =====================================================

def log(message):
    print(
        f"[{datetime.now(TR).strftime('%Y-%m-%d %H:%M:%S')} TR] "
        f"{message}",
        flush=True
    )


# =====================================================
# TELEGRAM
# =====================================================

def tg(message, chat_id=None):

    if not BOT_TOKEN:
        log("BOT_TOKEN bulunamadı.")
        return False

    try:

        data = urllib.parse.urlencode({
            "chat_id": str(chat_id or CHAT_ID),
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true"
        }).encode()

        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data=data
        )

        with urllib.request.urlopen(req, timeout=15) as response:

            result = json.loads(
                response.read().decode()
            )

        if result.get("ok"):
            return True

        log(f"Telegram hatası: {result}")

    except Exception as exc:

        log(f"Telegram gönderim hatası: {exc}")

    return False


def telegram_api(method, params=None):

    try:

        url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

        if params:

            url += "?" + urllib.parse.urlencode(params)

        req = urllib.request.Request(url)

        with urllib.request.urlopen(req, timeout=20) as response:

            return json.loads(
                response.read().decode()
            )

    except urllib.error.HTTPError as exc:

        body = exc.read().decode(errors="replace")

        log(f"Telegram HTTP {exc.code}: {body}")

        return None

    except Exception as exc:

        log(f"Telegram bağlantı hatası: {exc}")

        return None


# =====================================================
# BINANCE REST KORUMASI
# =====================================================

def api_json(url):

    global REST_PAUSE_UNTIL

    with REST_LOCK:

        now = time.time()

        if now < REST_PAUSE_UNTIL:

            remaining = int(REST_PAUSE_UNTIL - now)

            log(
                f"Binance REST beklemede: "
                f"{remaining} saniye"
            )

            return None

        try:

            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0"
                }
            )

            with urllib.request.urlopen(
                req,
                timeout=20
            ) as response:

                return json.loads(
                    response.read().decode()
                )

        except urllib.error.HTTPError as exc:

            body = exc.read().decode(errors="replace")

            if exc.code in (418, 429):

                wait_seconds = 120

                retry_after = exc.headers.get(
                    "Retry-After"
                )

                if retry_after:

                    try:

                        wait_seconds = max(
                            wait_seconds,
                            int(float(retry_after))
                        )

                    except ValueError:
                        pass

                match = re.search(
                    r"banned until (\d+)",
                    body
                )

                if match:

                    ban_until = (
                        int(match.group(1)) / 1000
                    )

                    REST_PAUSE_UNTIL = max(
                        REST_PAUSE_UNTIL,
                        ban_until + 10
                    )

                else:

                    REST_PAUSE_UNTIL = max(
                        REST_PAUSE_UNTIL,
                        time.time() + wait_seconds
                    )

                remaining = int(
                    REST_PAUSE_UNTIL - time.time()
                )

                log(
                    f"BINANCE HTTP {exc.code}: "
                    f"REST DURDURULDU. "
                    f"Bekleme {remaining} saniye. "
                    f"{body}"
                )

            else:

                log(
                    f"Binance HTTP {exc.code}: {body}"
                )

            return None

        except Exception as exc:

            log(f"Binance bağlantı hatası: {exc}")

            return None


# =====================================================
# FORMAT
# =====================================================

def fmt_price(value):

    value = float(value)

    if value >= 1000:
        return f"{value:.2f}"

    if value >= 1:
        return f"{value:.5f}".rstrip("0").rstrip(".")

    if value >= 0.01:
        return f"{value:.6f}".rstrip("0").rstrip(".")

    return f"{value:.10f}".rstrip("0").rstrip(".")


def fmt_volume(value):

    value = float(value)

    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"

    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"

    if value >= 1000:
        return f"{value / 1000:.2f}K"

    return f"{value:.2f}"


# =====================================================
# RSI
# =====================================================

def calculate_rsi(prices, period=14):

    if len(prices) < period + 1:
        return 50.0

    changes = [
        prices[i] - prices[i - 1]
        for i in range(1, len(prices))
    ]

    gains = [
        max(change, 0)
        for change in changes
    ]

    losses = [
        max(-change, 0)
        for change in changes
    ]

    avg_gain = sum(gains[:period]) / period

    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(changes)):

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


# =====================================================
# SİNYAL DOSYASI
# =====================================================

def load_signals():

    global signals

    try:

        with open(
            SIGNALS_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        if isinstance(data, list):

            signals = data[-100:]

    except Exception:

        signals = []


def save_signals():

    try:

        with open(
            SIGNALS_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                signals[-100:],
                file,
                ensure_ascii=False,
                indent=2
            )

    except Exception as exc:

        log(f"Sinyal kayıt hatası: {exc}")


# =====================================================
# FUTURES PARİTE LİSTESİ
# =====================================================

def get_futures_symbols():

    log("Binance Futures pariteleri alınıyor...")

    while True:

        if time.time() < REST_PAUSE_UNTIL:

            remaining = int(
                REST_PAUSE_UNTIL - time.time()
            )

            log(
                f"Binance engeli aktif. "
                f"Bekleme: {remaining} saniye"
            )

            time.sleep(
                min(60, max(10, remaining))
            )

            continue

        data = api_json(
            f"{FAPI}/fapi/v1/ticker/24hr"
        )

        if isinstance(data, list):

            candidates = []

            for item in data:

                symbol = item.get("symbol", "")

                if not symbol.endswith("USDT"):
                    continue

                try:

                    volume = float(
                        item.get("quoteVolume", 0)
                    )

                    change = float(
                        item.get("priceChangePercent", 0)
                    )

                    if volume >= MIN_VOLUME:

                        candidates.append({
                            "symbol": symbol,
                            "volume": volume,
                            "change": change
                        })

                except Exception:

                    continue

            candidates.sort(
                key=lambda item: item["volume"],
                reverse=True
            )

            result = [
                item["symbol"]
                for item in candidates[:MAX_PAIRS]
            ]

            log(
                f"Futures pariteleri hazır: "
                f"{len(result)}"
            )

            return result

        log(
            "Futures ticker verisi alınamadı. "
            "Yeni REST isteği bekleniyor."
        )

        time.sleep(30)


# =====================================================
# GEÇMİŞ MUM YÜKLEME
# =====================================================

def seed_history(symbol):

    if time.time() < REST_PAUSE_UNTIL:
        return False

    url = (
        f"{FAPI}/fapi/v1/klines?"
        f"symbol={symbol}"
        f"&interval={INTERVAL}"
        f"&limit=30"
    )

    data = api_json(url)

    if not isinstance(data, list):
        return False

    now_ms = int(time.time() * 1000)

    candles = []

    for item in data:

        if int(item[6]) >= now_ms:
            continue

        candles.append({
            "t": int(item[0]),
            "o": float(item[1]),
            "h": float(item[2]),
            "l": float(item[3]),
            "c": float(item[4]),
            "qv": float(item[7])
        })

    if not candles:
        return False

    with DATA_LOCK:

        histories[symbol] = candles[-40:]

    return True


# =====================================================
# SHORT SİNYAL ANALİZİ
# =====================================================

def evaluate_signal(symbol):

    with DATA_LOCK:

        candles = list(
            histories.get(symbol, [])
        )

    if len(candles) < 20:
        return

    current = candles[-1]

    previous = candles[-2]

    closes = [
        candle["c"]
        for candle in candles
    ]

    rsi = calculate_rsi(closes, 14)

    # Son 3 mumda yükseliş
    pump_start = candles[-4]["c"]

    recent_peak = max(
        candle["h"]
        for candle in candles[-4:-1]
    )

    if pump_start <= 0:
        return

    pump_pct = (
        recent_peak / pump_start - 1
    ) * 100

    distance_from_peak = (
        (recent_peak - current["c"])
        / recent_peak
    ) * 100

    volumes = [
        candle["qv"]
        for candle in candles[-21:-1]
    ]

    avg_volume = (
        sum(volumes) / len(volumes)
        if volumes else 0
    )

    volume_ratio = (
        current["qv"] / avg_volume
        if avg_volume > 0 else 0
    )

    red_candle = (
        current["c"] < current["o"]
    )

    lower_close = (
        current["c"] < previous["c"]
    )

    score = 0

    if pump_pct >= 2:
        score += 1

    if rsi >= 60:
        score += 1

    if red_candle:
        score += 1

    if lower_close:
        score += 1

    if volume_ratio >= 1.2:
        score += 1

    if distance_from_peak <= 1.5:
        score += 1

    if score < 4:
        return

    if pump_pct < 2:
        return

    if not red_candle:
        return

    if not lower_close:
        return

    now = time.time()

    if (
        now - last_signal_time.get(symbol, 0)
        < COOLDOWN
    ):
        return

    with SIGNAL_LOCK:

        active = any(
            item["symbol"] == symbol
            and item["status"] in (
                "OPEN",
                "TP1_HIT"
            )
            for item in signals
        )

    if active:
        return

    entry = current["c"]

    recent_high = max(
        candle["h"]
        for candle in candles[-5:]
    )

    stop = recent_high * 1.0015

    if stop <= entry:
        return

    risk = stop - entry

    risk_pct = risk / entry * 100

    if risk_pct <= 0 or risk_pct > 3.5:
        return

    tp1 = entry - risk

    tp2 = entry - (2 * risk)

    msg = (
        f"🔻 <b>SHORT ADAYI — TEYİTLİ DÖNÜŞ</b>\n"
        f"#{symbol} · Binance USDT-M Futures\n"
        f"⭐ Sinyal skoru: {score}/6\n\n"
        f"🏷 Giriş referansı: {fmt_price(entry)}\n"
        f"🛑 Stop-loss: {fmt_price(stop)} "
        f"(+%{risk_pct:.2f})\n"
        f"🎯 TP1 (1R): {fmt_price(tp1)}\n"
        f"🎯 TP2 (2R): {fmt_price(tp2)}\n\n"
        f"📈 Önceki yükseliş: +%{pump_pct:.2f}\n"
        f"📉 RSI(14): {rsi:.1f}\n"
        f"📊 Hacim / ortalama: {volume_ratio:.2f}x\n"
        f"💰 Mum hacmi: {fmt_volume(current['qv'])} USDT\n"
        f"🕒 {datetime.now(TR).strftime('%H:%M:%S')} TR\n\n"
        f"⚠️ Analiz sinyalidir. Otomatik emir açılmaz.\n"
        f"Stop ve hedefler referans değerleridir."
    )

    if not tg(msg):
        return

    record = {
        "symbol": symbol,
        "time": datetime.now(TR).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "score": score,
        "status": "OPEN"
    }

    with SIGNAL_LOCK:

        signals.append(record)

        signals[:] = signals[-100:]

        save_signals()

    last_signal_time[symbol] = now

    log(
        f"SHORT sinyali: {symbol} "
        f"| Skor {score}/6"
    )


# =====================================================
# SİNYAL SONUÇ TAKİBİ
# =====================================================

def update_outcomes(symbol, candle):

    messages = []

    with SIGNAL_LOCK:

        for item in reversed(signals):

            if item["symbol"] != symbol:
                continue

            if item["status"] not in (
                "OPEN",
                "TP1_HIT"
            ):
                continue

            high = candle["h"]

            low = candle["l"]

            if item["status"] == "OPEN":

                if high >= item["stop"]:

                    item["status"] = "SL"

                    messages.append(
                        f"🛑 STOP-LOSS\n"
                        f"#{symbol}\n"
                        f"Stop: {fmt_price(item['stop'])}"
                    )

                    break

                if low <= item["tp1"]:

                    item["status"] = "TP1_HIT"

                    messages.append(
                        f"✅ TP1 ULAŞILDI\n"
                        f"#{symbol}\n"
                        f"TP1: {fmt_price(item['tp1'])}\n"
                        f"TP2 takibi devam ediyor."
                    )

                    break

            elif item["status"] == "TP1_HIT":

                if high >= item["stop"]:

                    item["status"] = "TP1_THEN_SL"

                    messages.append(
                        f"⚠️ TP1 SONRASI STOP\n"
                        f"#{symbol}\n"
                        f"TP1 görüldü, ardından stop."
                    )

                    break

                if low <= item["tp2"]:

                    item["status"] = "TP2"

                    messages.append(
                        f"🎯 TP2 ULAŞILDI\n"
                        f"#{symbol}\n"
                        f"TP2: {fmt_price(item['tp2'])}"
                    )

                    break

        save_signals()

    for message in messages:

        tg(message)

        log(
            f"Sinyal sonucu: {symbol}"
        )


# =====================================================
# KAPANAN MUM
# =====================================================

def process_candle(symbol, candle):

    with DATA_LOCK:

        candles = histories.setdefault(
            symbol,
            []
        )

        if candles and candle["t"] <= candles[-1]["t"]:
            return

        candles.append(candle)

        if len(candles) > 40:

            del candles[:-40]

    update_outcomes(symbol, candle)

    evaluate_signal(symbol)


# =====================================================
# WEBSOCKET
# =====================================================

def on_ws_message(ws, message):

    global ws_last_message

    try:

        packet = json.loads(message)

        data = packet.get("data", packet)

        if data.get("e") != "kline":
            return

        k = data.get("k", {})

        # Mum kapanmadıysa işlem yapma
        if not k.get("x"):
            return

        symbol = (
            data.get("s")
            or k.get("s")
        )

        if not symbol:
            return

        candle = {
            "t": int(k["t"]),
            "o": float(k["o"]),
            "h": float(k["h"]),
            "l": float(k["l"]),
            "c": float(k["c"]),
            "qv": float(k["q"])
        }

        ws_last_message = time.time()

        process_candle(
            symbol,
            candle
        )

    except Exception as exc:

        log(
            f"WebSocket mesaj hatası: {exc}"
        )


def on_ws_open(ws):

    global ws_connected

    ws_connected = True

    ws_last_message = time.time()

    log(
        "Binance Futures WebSocket bağlantısı kuruldu."
    )

    tg(
        "🟢 <b>Binance Futures bağlantısı aktif</b>\n"
        "5 dakikalık mumlar WebSocket üzerinden izleniyor.\n"
        "REST taraması yerine canlı akış kullanılıyor."
    )


def on_ws_error(ws, error):

    log(
        f"WebSocket hata: {error}"
    )


def on_ws_close(ws, status_code, message):

    global ws_connected

    ws_connected = False

    log(
        f"WebSocket kapandı: "
        f"{status_code} {message}"
    )


# =====================================================
# TELEGRAM KOMUTLARI
# =====================================================

def command_listener():

    offset = 0

    telegram_api(
        "deleteWebhook",
        {
            "drop_pending_updates": "false"
        }
    )

    while True:

        try:

            result = telegram_api(
                "getUpdates",
                {
                    "offset": offset + 1,
                    "timeout": 10,
                    "allowed_updates": json.dumps(
                        ["message"]
                    )
                }
            )

            if not result or not result.get("ok"):

                time.sleep(10)

                continue

            for update in result.get("result", []):

                offset = update["update_id"]

                message = update.get(
                    "message",
                    {}
                )

                text = message.get(
                    "text",
                    ""
                ).strip()

                chat_id = message.get(
                    "chat",
                    {}
                ).get("id")

                if not text or not chat_id:
                    continue

                if text.startswith("/ratio"):

                    parts = text.split()

                    if len(parts) < 2:

                        tg(
                            "Kullanım: /ratio BTCUSDT 1h",
                        chat_id
                        )

                        continue

                    symbol = parts[1].upper()

                    if not symbol.endswith("USDT"):

                        symbol += "USDT"

                    period = (
                        parts[2].lower()
                        if len(parts) > 2
                        else "1h"
                    )

                    allowed = {
                        "5m", "15m", "30m",
                        "1h", "2h", "4h",
                        "6h", "12h", "1d"
                    }

                    if period not in allowed:

                        tg(
                            "Zaman aralığı: "
                            "5m, 15m, 30m, 1h, "
                            "2h, 4h, 6h, 12h, 1d",
                            chat_id
                        )

                        continue

                    url = (
                        f"{FAPI}/futures/data/"
                        f"globalLongShortAccountRatio?"
                        f"symbol={symbol}"
                        f"&period={period}&limit=1"
                    )

                    data = api_json(url)

                    if data and isinstance(data, list):

                        d = data[-1]

                        tg(
                            f"📊 <b>Long/Short Ratio</b>\n"
                            f"Binance Futures · {symbol}\n"
                            f"⏳ {period}\n"
                            f"📈 Long: "
                            f"{float(d['longAccount']) * 100:.2f}%\n"
                            f"📉 Short: "
                            f"{float(d['shortAccount']) * 100:.2f}%\n"
                            f"⚖️ Oran: "
                            f"{float(d['longShortRatio']):.2f}\n"
                            f"🕒 {datetime.now(TR).strftime('%H:%M')} TR",
                            chat_id
                        )

                    else:

                        tg(
                            "Veri alınamadı. Binance REST "
                            "bekleme koruması aktif olabilir.",
                            chat_id
                        )

                elif text.startswith("/sonuc"):

                    with SIGNAL_LOCK:

                        recent = list(
                            reversed(signals[-10:])
                        )

                    if not recent:

                        tg(
                            "Henüz kayıtlı sinyal yok.",
                            chat_id
                        )

                        continue

                    labels = {
                        "OPEN": "Açık takip",
                        "TP1_HIT": "TP1 görüldü",
                        "TP2": "TP2 görüldü",
                        "SL": "Stop görüldü",
                        "TP1_THEN_SL": "TP1 sonrası stop"
                    }

                    lines = [
                        "📋 <b>Son 10 sinyal</b>"
                    ]

                    for item in recent:

                        lines.append(
                            f"#{item['symbol']} "
                            f"· {item['time']}\n"
                            f"Skor: {item['score']}/6\n"
                            f"{labels.get(item['status'], item['status'])}"
                        )

                    tg(
                        "\n\n".join(lines),
                        chat_id
                    )

        except Exception as exc:

            log(
                f"Telegram komut hatası: {exc}"
            )

            time.sleep(10)


# =====================================================
# RENDER HEALTH CHECK
# =====================================================

class HealthHandler(
    http.server.BaseHTTPRequestHandler
):

    def do_GET(self):

        body = (
            "Futures signal bot is running.\n"
            "Market data: Binance WebSocket.\n"
        ).encode()

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

        self.end_headers()

        self.wfile.write(body)

    def log_message(self, format_string, *args):

        pass


def health_server():

    port = int(
        os.environ.get("PORT", "10000")
    )

    try:

        server = http.server.ThreadingHTTPServer(
            ("0.0.0.0", port),
            HealthHandler
        )

        log(
            f"Render sağlık sunucusu hazır: {port}"
        )

        server.serve_forever()

    except Exception as exc:

        log(
            f"Render sağlık sunucusu hatası: {exc}"
        )


# =====================================================
# WEBSOCKET ANA DÖNGÜSÜ
# =====================================================

def market_loop():

    while True:

        try:

            symbols = get_futures_symbols()

            if not symbols:

                log(
                    "Parite listesi alınamadı."
                )

                time.sleep(60)

                continue

            # İlk bağlantı öncesi geçmiş mumları yükle.
            # REST engeli varsa yeni isteği başlatma.
            seeded = 0

            for index, symbol in enumerate(symbols):

                if time.time() < REST_PAUSE_UNTIL:

                    log(
                        "Binance REST engeli aktif. "
                        "Geçmiş mum yükleme durduruldu."
                    )

                    break

                if seed_history(symbol):

                    seeded += 1

                # İstekleri seyrek gönder.
                time.sleep(1.5)

            stream_names = [
                f"{symbol.lower()}@kline_{INTERVAL}"
                for symbol in symbols
            ]

            stream_string = "/".join(
                stream_names
            )

            url = WS_URL + stream_string

            log(
                f"WebSocket hazırlanıyor: "
                f"{len(symbols)} parite | "
                f"Geçmiş: {seeded}"
            )

            tg(
                f"🔄 <b>Futures tarama hazır</b>\n"
                f"📊 Parite: {len(symbols)}\n"
                f"📚 Geçmiş yüklenen: {seeded}\n"
                f"⏱ Zaman: 5m\n"
                f"📡 Veri: WebSocket\n"
                f"🛑 Otomatik emir: Kapalı"
            )

            app = websocket.WebSocketApp(
                url,
                on_open=on_ws_open,
                on_message=on_ws_message,
                on_error=on_ws_error,
                on_close=on_ws_close
            )

            app.run_forever(
                ping_interval=120,
                ping_timeout=30
            )

            log(
                "WebSocket kapandı. "
                "15 saniye sonra tekrar bağlanılacak."
            )

            time.sleep(15)

        except Exception as exc:

            log(
                f"Market döngüsü hatası: {exc}"
            )

            time.sleep(30)


# =====================================================
# BAŞLAT
# =====================================================

if __name__ == "__main__":

    load_signals()

    threading.Thread(
        target=health_server,
        daemon=True
    ).start()

    threading.Thread(
        target=command_listener,
        daemon=True
    ).start()

    tg(
        "🚀 <b>Futures SHORT botu başlatıldı</b>\n"
        "Piyasa: Binance USDT-M Futures\n"
        "Veri: WebSocket\n"
        "Zaman aralığı: 5 dakika\n"
        "Emir modu: Yalnızca sinyal\n"
        "418/429 koruması: Aktif"
    )

    market_loop()

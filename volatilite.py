
import os
import json
import time
import threading
import urllib.request
import urllib.parse
import urllib.error
import socket
from collections import deque
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import websocket

# =========================================================
# AYARLAR
# =========================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "1623907197").strip()

TR = timezone(timedelta(hours=3))

WS_URL = "wss://fstream.binance.com/ws/!ticker@arr"

MAX_CANDIDATES = int(os.environ.get("MAX_CANDIDATES", "12"))
MIN_QUOTE_VOLUME = float(os.environ.get("MIN_QUOTE_VOLUME", "5000000"))
MIN_SCORE = int(os.environ.get("MIN_SCORE", "3"))
COOLDOWN_SECONDS = 1800

# Geçmiş mumlar yalnızca WebSocket ile toplanır.
# İlk sinyaller için en az 22 adet kapanmış 1 dakikalık mum gerekir.
HISTORY_SIZE = 100
MIN_CANDLES = 22

socket.setdefaulttimeout(20)

lock = threading.RLock()
candles = {}
tickers = {}
subscribed = set()
signals = {}
signal_history = deque(maxlen=500)
last_signal = {}
last_market_message = 0
last_status_log = 0
ws_ref = None
subscription_id = 1
telegram_offset = 0
running = True


def log(message):
    now = datetime.now(TR).strftime("%Y-%m-%d %H:%M:%S TR")
    print(f"[{now}] {message}", flush=True)


def telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        log("Telegram ayarı eksik: BOT_TOKEN veya CHAT_ID.")
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
            if not result.get("ok"):
                log(f"Telegram gönderim hatası: {result}")
                return False
            return True
    except Exception as exc:
        log(f"Telegram gönderim hatası: {exc}")
        return False


def ema(values, period):
    if len(values) < period:
        return None
    result = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    for value in values[period:]:
        result = value * multiplier + result * (1 - multiplier)
    return result


def rsi(values, period=14):
    if len(values) < period + 1:
        return None

    changes = [values[i] - values[i - 1]
               for i in range(1, len(values))]
    gains = [max(x, 0) for x in changes[-period:]]
    losses = [max(-x, 0) for x in changes[-period:]]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    return 100 - (100 / (1 + avg_gain / avg_loss))


def calculate_signal(symbol):
    with lock:
        data = list(candles.get(symbol, []))

    if len(data) < MIN_CANDLES:
        return None

    closes = [x["c"] for x in data]
    highs = [x["h"] for x in data]
    lows = [x["l"] for x in data]
    volumes = [x["v"] for x in data]

    current = data[-1]
    previous = data[-2]

    price = current["c"]
    open_price = current["o"]

    if price <= 0 or open_price <= 0:
        return None

    score = 0
    reasons = []

    # 1. RSI yüksek: yükseliş sonrası geri dönüş adayı
    current_rsi = rsi(closes)
    if current_rsi is not None and current_rsi >= 60:
        score += 1
        reasons.append(f"RSI {current_rsi:.1f}")

    # 2. Hacim önceki 20 mum ortalamasının üzerinde
    avg_volume = sum(volumes[-21:-1]) / 20
    if avg_volume > 0 and volumes[-1] >= avg_volume * 1.5:
        score += 1
        reasons.append("Hacim patlaması")

    # 3. Kısa EMA uzun EMA üzerinde: yükseliş sonrası dönüş izlemi
    ema11 = ema(closes[-30:], 11)
    ema21 = ema(closes[-30:], 21)
    if ema11 is not None and ema21 is not None and ema11 >= ema21:
        score += 1
        reasons.append("EMA yükseliş bölgesi")

    # 4. Son 5 dakika fiyat değişimi en az %0,5
    if len(closes) >= 6 and closes[-6] > 0:
        change5 = (price / closes[-6] - 1) * 100
    else:
        change5 = 0

    if change5 >= 0.5:
        score += 1
        reasons.append(f"5 dk değişim +{change5:.2f}%")

    # 5. Son kapanmış mum kırmızı olmalı
    red_candle = current["c"] < current["o"]
    if red_candle:
        score += 1
        reasons.append("Kırmızı kapanış")

    # 6. Fiyat önceki 5 mumun en düşük seviyesinin altında olmalı
    previous_support = min(lows[-6:-1])
    below_support = price < previous_support
    if below_support:
        score += 1
        reasons.append("5 mum desteği kırıldı")

    # Teyitsiz yükseliş mumunda SHORT sinyali gönderme.
    if not red_candle or score < MIN_SCORE:
        return None

    # Stop, sinyal mumunun tepesinin %0,2 üstünde.
    stop = current["h"] * 1.002
    risk = stop - price

    if risk <= 0 or stop <= price:
        return None

    tp1 = price - risk
    tp2 = price - 2 * risk

    return {
        "symbol": symbol,
        "entry": price,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "score": score,
        "rsi": current_rsi,
        "reasons": reasons,
        "time": datetime.now(TR).strftime("%d.%m.%Y %H:%M:%S"),
        "status": "AÇIK",
        "tp1_hit": False
    }


def price_fmt(value):
    if value >= 100:
        return f"{value:.3f}"
    if value >= 1:
        return f"{value:.5f}"
    if value >= 0.01:
        return f"{value:.6f}"
    return f"{value:.8f}"


def evaluate_closed_candle(symbol, candle):
    # Önce önceki sinyalin SL/TP durumunu kontrol et.
    with lock:
        active = signals.get(symbol)

    if active and active["status"] == "AÇIK":
        hit_sl = candle["h"] >= active["stop"]
        hit_tp1 = candle["l"] <= active["tp1"]
        hit_tp2 = candle["l"] <= active["tp2"]

        # Aynı mumda hem stop hem hedef görünüyorsa,
        # mum içi sıra bilinmediğinden temkinli olarak SL say.
        if hit_sl:
            active["status"] = "STOP"
            telegram(
                f"🛑 STOP-LOSS\n{symbol}\n"
                f"Stop: {price_fmt(active['stop'])}\n"
                f"Sinyal sonucu: STOP"
            )
        elif hit_tp2:
            active["status"] = "TP2"
            active["tp1_hit"] = True
            telegram(
                f"🎯 TP2 GERÇEKLEŞTİ\n{symbol}\n"
                f"TP1: {price_fmt(active['tp1'])}\n"
                f"TP2: {price_fmt(active['tp2'])}"
            )
        elif hit_tp1 and not active["tp1_hit"]:
            active["tp1_hit"] = True
            telegram(
                f"🎯 TP1 GERÇEKLEŞTİ\n{symbol}\n"
                f"TP1: {price_fmt(active['tp1'])}\n"
                f"Stop: {price_fmt(active['stop'])}"
            )

    # Aynı sembolde bekleme süresi.
    now = time.time()
    if now - last_signal.get(symbol, 0) < COOLDOWN_SECONDS:
        return

    candidate = calculate_signal(symbol)
    if not candidate:
        return

    with lock:
        # Eski sinyal hâlâ açıksa yenisini açma.
        previous_signal = signals.get(symbol)
        if previous_signal and previous_signal["status"] == "AÇIK":
            return

        signals[symbol] = candidate
        signal_history.append(candidate.copy())
        last_signal[symbol] = now

    reasons = "\n".join(f"• {r}" for r in candidate["reasons"])

    message = (
        f"🔻 SHORT ADAYI — TEYİTLİ DÖNÜŞ\n"
        f"#{symbol} · Binance USDT-M Futures\n"
        f"⭐ Sinyal skoru: {candidate['score']}/6\n\n"
        f"🏷 Giriş referansı: {price_fmt(candidate['entry'])}\n"
        f"🛑 Stop-loss: {price_fmt(candidate['stop'])}\n"
        f"🎯 TP1 (1R): {price_fmt(candidate['tp1'])}\n"
        f"🎯 TP2 (2R): {price_fmt(candidate['tp2'])}\n\n"
        f"📊 Teyitler:\n{reasons}\n\n"
        f"🕒 {candidate['time']}\n"
        f"ℹ️ Bilgilendirme sinyalidir; otomatik işlem açılmaz."
    )

    if telegram(message):
        log(f"SHORT sinyali gönderildi: {symbol} | skor {candidate['score']}/6")
    else:
        log(f"Telegram sinyali gönderilemedi: {symbol}")


def subscribe_streams(ws, symbols):
    global subscription_id

    with lock:
        wanted = set(symbols)
        add = wanted - subscribed
        remove = subscribed - wanted

        if remove:
            ws.send(json.dumps({
                "method": "UNSUBSCRIBE",
                "params": [f"{s.lower()}@kline_1m" for s in remove],
                "id": subscription_id
            }))
            subscription_id += 1
            subscribed.difference_update(remove)

        if add:
            # Mesaj başına en fazla 50 stream gönder.
            streams = [f"{s.lower()}@kline_1m" for s in sorted(add)]
            for start in range(0, len(streams), 50):
                ws.send(json.dumps({
                    "method": "SUBSCRIBE",
                    "params": streams[start:start + 50],
                    "id": subscription_id
                }))
                subscription_id += 1
            subscribed.update(add)

    if add or remove:
        log(f"WebSocket aboneliği güncellendi: {len(subscribed)} parite")


def on_open(ws):
    global last_market_message
    last_market_message = time.time()
    log("Binance WebSocket bağlandı; piyasa akışı bekleniyor.")


def on_message(ws, raw):
    global last_market_message, last_status_log

    last_market_message = time.time()

    try:
        payload = json.loads(raw)
    except Exception:
        return

    # İlk bağlantıda ticker akışı; REST kullanılmaz.
    if isinstance(payload, list):
        with lock:
            for item in payload:
                symbol = item.get("s", "")
                if not symbol.endswith("USDT") or "_" in symbol:
                    continue
                try:
                    tickers[symbol] = {
                        "quote": float(item.get("q", 0)),
                        "price": float(item.get("c", 0)),
                        "change": float(item.get("P", 0))
                    }
                except (TypeError, ValueError):
                    continue

            eligible = [
                (symbol, info)
                for symbol, info in tickers.items()
                if info["quote"] >= MIN_QUOTE_VOLUME
            ]
            eligible.sort(key=lambda x: x[1]["quote"], reverse=True)
            top_symbols = [symbol for symbol, _ in eligible[:MAX_CANDIDATES]]

        if top_symbols:
            try:
                subscribe_streams(ws, top_symbols)
            except Exception as exc:
                log(f"Abonelik güncelleme hatası: {exc}")

        now = time.time()
        if now - last_status_log >= 60:
            last_status_log = now
            log(
                f"WebSocket aktif | uygun parite: {len(eligible)} | "
                f"izlenen: {len(top_symbols)} | REST isteği: 0"
            )
        return

    # Kline stream verisi
    if not isinstance(payload, dict):
        return

    data = payload.get("data", payload)
    if not isinstance(data, dict) or data.get("e") != "kline":
        return

    symbol = data.get("s", "")
    k = data.get("k", {})

    if not symbol or not k.get("x", False):
        return  # Mum kapanmadı; sinyal hesaplama.

    try:
        candle = {
            "t": int(k["t"]),
            "o": float(k["o"]),
            "h": float(k["h"]),
            "l": float(k["l"]),
            "c": float(k["c"]),
            "v": float(k["v"])
        }
    except (KeyError, TypeError, ValueError):
        return

    with lock:
        history = candles.setdefault(symbol, deque(maxlen=HISTORY_SIZE))
        if not history or history[-1]["t"] != candle["t"]:
            history.append(candle)

    evaluate_closed_candle(symbol, candle)


def on_error(ws, error):
    log(f"WebSocket hata: {error}")


def on_close(ws, code, message):
    log(f"WebSocket kapandı: {code} {message}")


def websocket_worker():
    while running:
        try:
            log("Binance WebSocket bağlantısı başlatılıyor.")
            ws = websocket.WebSocketApp(
                WS_URL,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close
            )
            ws.run_forever(
                ping_interval=120,
                ping_timeout=20,
                reconnect=0
            )
        except Exception as exc:
            log(f"WebSocket çalışma hatası: {exc}")

        if running:
            log("10 saniye sonra WebSocket yeniden bağlanacak.")
            time.sleep(10)


def command_worker():
    global telegram_offset

    if not BOT_TOKEN:
        log("BOT_TOKEN yok; Telegram komutları kapalı.")
        return

    while running:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            params = urllib.parse.urlencode({
                "offset": telegram_offset,
                "timeout": 15,
                "allowed_updates": json.dumps(["message"])
            })
            req = urllib.request.Request(url + "?" + params)

            with urllib.request.urlopen(req, timeout=20) as response:
                result = json.loads(response.read().decode())

            if not result.get("ok"):
                time.sleep(5)
                continue

            for update in result.get("result", []):
                telegram_offset = update["update_id"] + 1
                msg = update.get("message", {})
                chat = msg.get("chat", {})
                text = msg.get("text", "").strip().lower()

                if str(chat.get("id")) != str(CHAT_ID):
                    continue

                if text in ("/start", "/durum"):
                    with lock:
                        watching = len(subscribed)
                        loaded = sum(
                            1 for symbol in subscribed
                            if len(candles.get(symbol, [])) >= MIN_CANDLES
                        )
                    telegram(
                        "🤖 Futures SHORT botu aktif.\n"
                        "📡 Veri: Binance WebSocket\n"
                        "🔌 REST istekleri: 0\n"
                        f"👀 İzlenen parite: {watching}\n"
                        f"📚 Yeterli geçmiş mumu olan: {loaded}\n"
                        f"⭐ Minimum skor: {MIN_SCORE}/6\n"
                        "⚠️ Bot otomatik işlem açmaz."
                    )

                elif text == "/sonuc":
                    with lock:
                        history = list(signal_history)[-15:]

                    if not history:
                        telegram("Henüz sinyal geçmişi oluşmadı.")
                    else:
                        lines = ["📊 SON 15 SİNYAL"]
                        for item in reversed(history):
                            lines.append(
                                f"{item['symbol']} | {item['score']}/6 | "
                                f"{item['status']} | "
                                f"Giriş {price_fmt(item['entry'])}"
                            )
                        telegram("\n".join(lines))

                elif text == "/ratio":
                    with lock:
                        open_count = sum(
                            1 for item in signals.values()
                            if item["status"] == "AÇIK"
                        )
                        tp_count = sum(
                            1 for item in signal_history
                            if item["status"] in ("TP1", "TP2")
                        )
                        stop_count = sum(
                            1 for item in signal_history
                            if item["status"] == "STOP"
                        )

                    telegram(
                        "📈 SİNYAL DURUMU\n"
                        f"Açık: {open_count}\n"
                        f"TP sonuçları: {tp_count}\n"
                        f"Stop: {stop_count}\n"
                        "Not: Bu sayılar gerçek emir/kâr kaydı değildir."
                    )

        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                log("Telegram 409 Conflict: aynı tokenla başka poller çalışıyor olabilir.")
                time.sleep(20)
            else:
                log(f"Telegram komut hatası: HTTP {exc.code}")
                time.sleep(5)
        except Exception as exc:
            log(f"Telegram komut bağlantısı: {exc}")
            time.sleep(5)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (
            "Futures signal bot is running.\n"
            "Market data: Binance WebSocket.\n"
            "Binance REST requests: 0.\n"
        ).encode()

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"Health server çalışıyor: port {port}")


def main():
    if not BOT_TOKEN:
        log("HATA: Render Environment içine BOT_TOKEN eklenmeli.")
        while True:
            time.sleep(60)

    start_health_server()

    threading.Thread(target=command_worker, daemon=True).start()
    websocket_worker()


if __name__ == "__main__":
    main()

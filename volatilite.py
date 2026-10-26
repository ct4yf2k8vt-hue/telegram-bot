
import os
import json
import time
import socket
import threading
import urllib.request
import urllib.parse
import urllib.error
import http.server
import socketserver
from datetime import datetime, timedelta, timezone

socket.setdefaulttimeout(15)

# =====================================================
# AYARLAR
# =====================================================

T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()

TR_Saati = timezone(timedelta(hours=3))

FAPI = "https://fapi.binance.com"
COOLDOWN = 900
MIN_VOLUME = 5_000_000
MAX_PAIRS = 70
SCAN_DELAY = 20

son_sinyaller = {}
stop_event = threading.Event()


def log(text):
    now = datetime.now(TR_Saati).strftime("%Y-%m-%d %H:%M:%S TR")
    print(f"[{now}] {text}", flush=True)


# =====================================================
# HTTP / TELEGRAM
# =====================================================

def api_json(url, timeout=12, data=None):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        req = urllib.request.Request(
            url, data=data, headers=headers
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8")[:250]
        except Exception:
            detail = str(e)
        log(f"HTTP {e.code}: {detail}")
        return None
    except Exception as e:
        log(f"Bağlantı hatası: {e}")
        return None


def tg(chat_id, message):
    if not T:
        log("Telegram mesajı gönderilemedi: BOT_TOKEN eksik.")
        return False

    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }).encode()

    result = api_json(
        f"https://api.telegram.org/bot{T}/sendMessage",
        timeout=15,
        data=payload
    )

    if result and result.get("ok"):
        return True

    log("Telegram mesajı gönderilemedi.")
    return False


def prepare_telegram():
    # getUpdates kullanabilmek için webhook kapatılır.
    # Bekleyen güncellemeler silinmez.
    result = api_json(
        f"https://api.telegram.org/bot{T}/deleteWebhook"
        "?drop_pending_updates=false",
        timeout=15
    )

    if result and result.get("ok"):
        log("Telegram webhook kapatıldı; getUpdates hazır.")
        return True

    log("Webhook ayarı doğrulanamadı. Telegram API yanıtını kontrol et.")
    return False


# =====================================================
# YARDIMCI FONKSİYONLAR
# =====================================================

def format_vol(value):
    value = float(value)
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.2f}K"
    return f"{value:.2f}"


def fmt_price(price):
    price = float(price)
    if price >= 1000:
        return f"{price:.2f}"
    if price >= 1:
        return f"{price:.4f}"
    if price >= 0.01:
        return f"{price:.5f}"
    if price >= 0.0001:
        return f"{price:.7f}"
    return f"{price:.9f}"


def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0

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


# =====================================================
# TELEGRAM KOMUTLARI
# =====================================================

last_update_id = 0


def command_listener():
    global last_update_id

    while not stop_event.is_set():
        try:
            url = (
                f"https://api.telegram.org/bot{T}/getUpdates"
                f"?offset={last_update_id + 1}&timeout=10"
                "&allowed_updates=%5B%22message%22%5D"
            )

            updates = api_json(url, timeout=15)

            if not updates or not updates.get("ok"):
                time.sleep(5)
                continue

            for update in updates.get("result", []):
                last_update_id = update["update_id"]

                msg_data = update.get("message", {})
                text = msg_data.get("text", "").strip()
                chat_id = str(msg_data.get("chat", {}).get("id", ""))

                # Yalnızca tanımlı sohbetten gelen komutları kabul et.
                if chat_id != C:
                    continue

                if not text:
                    continue

                if text.startswith("/start"):
                    tg(
                        chat_id,
                        "✅ <b>Futures SHORT tarama botu aktif.</b>\n\n"
                        "/ratio BTCUSDT 1h\n"
                        "/status"
                    )

                elif text.startswith("/status"):
                    tg(
                        chat_id,
                        "✅ Bot komut dinleyicisi aktif.\n"
                        "📊 Piyasa: Binance USDT-M Futures\n"
                        "⏱ Sinyal aralığı: 5 dakika\n"
                        "🔔 Bildirim: Telegram"
                    )

                elif text.startswith("/ratio"):
                    parts = text.split()

                    if len(parts) < 2:
                        tg(chat_id, "Kullanım: /ratio BTCUSDT 1h")
                        continue

                    symbol = parts[1].upper()
                    if not symbol.endswith("USDT"):
                        symbol += "USDT"

                    period = parts[2].lower() if len(parts) > 2 else "1h"

                    valid_periods = {
                        "5m", "15m", "30m", "1h",
                        "2h", "4h", "6h", "12h", "1d"
                    }

                    if period not in valid_periods:
                        tg(chat_id, "Geçersiz zaman aralığı.")
                        continue

                    endpoint = (
                        f"{FAPI}/futures/data/"
                        f"globalLongShortAccountRatio"
                        f"?symbol={urllib.parse.quote(symbol)}"
                        f"&period={period}&limit=1"
                    )

                    data = api_json(endpoint)

                    if data and isinstance(data, list):
                        d = data[0]
                        tg(
                            chat_id,
                            f"📊 <b>Futures Long/Short Ratio</b>\n"
                            f"Coin: {symbol}\n"
                            f"Zaman: {period}\n"
                            f"🟢 Long: "
                            f"{float(d['longAccount']) * 100:.2f}%\n"
                            f"🔴 Short: "
                            f"{float(d['shortAccount']) * 100:.2f}%\n"
                            f"⚖️ Oran: "
                            f"{float(d['longShortRatio']):.2f}\n"
                            f"🕒 {datetime.now(TR_Saati):%H:%M} TR"
                        )
                    else:
                        tg(
                            chat_id,
                            f"{symbol} için oran verisi alınamadı."
                        )

        except Exception as e:
            log(f"Komut dinleyicisi hatası: {e}")
            time.sleep(5)


# =====================================================
# FUTURES SHORT SİNYAL MOTORU
# =====================================================

def get_futures_tickers():
    data = api_json(f"{FAPI}/fapi/v1/ticker/24hr")

    if not isinstance(data, list):
        return []

    candidates = []

    for item in data:
        symbol = item.get("symbol", "")

        # USDT teminatlı, işlem gören sözleşmeler.
        if not symbol.endswith("USDT"):
            continue

        try:
            volume = float(item["quoteVolume"])
            change = float(item["priceChangePercent"])

            if volume < MIN_VOLUME:
                continue

            # SHORT dönüş ararken yükselen ve likit coinlere öncelik.
            if change < 2:
                continue

            candidates.append({
                "symbol": symbol,
                "change": change,
                "volume": volume,
                "high": float(item["highPrice"]),
                "low": float(item["lowPrice"]),
                "last": float(item["lastPrice"])
            })

        except (ValueError, KeyError, TypeError):
            continue

    candidates.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    return candidates[:MAX_PAIRS]


def analyze_pair(pair):
    symbol = pair["symbol"]

    klines = api_json(
        f"{FAPI}/fapi/v1/klines"
        f"?symbol={symbol}&interval=5m&limit=30"
    )

    if not isinstance(klines, list) or len(klines) < 22:
        return None

    # Son mum henüz kapanmamış olabilir; yalnızca kapanmış mumları kullan.
    closed = klines[:-1]

    try:
        last = closed[-1]
        previous = closed[-2]
        old = closed[-5]

        candle_open = float(last[1])
        candle_high = float(last[2])
        candle_low = float(last[3])
        candle_close = float(last[4])
        candle_volume = float(last[7])

        previous_close = float(previous[4])
        old_close = float(old[4])

        if min(candle_open, candle_close, previous_close, old_close) <= 0:
            return None

        # Dönüş mumundan önceki yaklaşık 15 dakikalık yükseliş.
        pump_pct = (
            (previous_close - old_close) / old_close
        ) * 100

        # Son kapanmış mumun kırmızı olması gerekir.
        red_candle = candle_close < candle_open

        # Mumun önceki kapanışın altına inmesi gerekir.
        downside_confirmation = candle_close < previous_close

        closes = [float(k[4]) for k in closed]
        rsi = calculate_rsi(closes, 14)

        volumes = [float(k[7]) for k in closed[-21:-1]]
        avg_volume = sum(volumes) / len(volumes) if volumes else 0

        volume_ratio = (
            candle_volume / avg_volume if avg_volume > 0 else 0
        )

        # Sadece sert yükseliş değil, aşağı dönüş teyidi de aranır.
        if pump_pct < 2.0:
            return None

        if not red_candle or not downside_confirmation:
            return None

        if rsi < 58:
            return None

        if volume_ratio < 1.1:
            return None

        # Yakın mumların tepesinin üzerine stop referansı.
        recent_high = max(float(k[2]) for k in closed[-6:])
        entry = candle_close
        stop = max(recent_high, candle_high) * 1.001

        if stop <= entry:
            return None

        risk_pct = ((stop - entry) / entry) * 100

        # Aşırı geniş stoplu adayları ele.
        if risk_pct <= 0 or risk_pct > 4.0:
            return None

        risk = stop - entry
        tp1 = entry - risk
        tp2 = entry - (2 * risk)

        # Basit, açıklanabilir kontrol puanı.
        score = 0
        if pump_pct >= 3:
            score += 1
        if rsi >= 65:
            score += 1
        if volume_ratio >= 1.5:
            score += 1
        if candle_close < previous_close:
            score += 1
        if pair["change"] >= 5:
            score += 1
        if candle_close < candle_open:
            score += 1

        return {
            "symbol": symbol,
            "entry": entry,
            "stop": stop,
            "tp1": tp1,
            "tp2": tp2,
            "risk_pct": risk_pct,
            "pump": pump_pct,
            "rsi": rsi,
            "volume_ratio": volume_ratio,
            "score": score,
            "change": pair["change"],
            "volume": pair["volume"],
            "high24": pair["high"],
            "low24": pair["low"]
        }

    except (ValueError, TypeError, IndexError, ZeroDivisionError):
        return None


def build_signal(s):
    symbol = s["symbol"]

    return (
        f"🔻 <b>SHORT ADAYI — TEYİTLİ DÖNÜŞ</b>\n"
        f"#{symbol} · Binance USDT-M Futures\n"
        f"⭐ Sinyal skoru: {s['score']}/6\n\n"
        f"🏷 Giriş referansı: {fmt_price(s['entry'])}\n"
        f"🛑 Stop-loss: {fmt_price(s['stop'])} "
        f"(+%{s['risk_pct']:.2f})\n"
        f"🎯 TP1 (1R): {fmt_price(s['tp1'])}\n"
        f"🎯 TP2 (2R): {fmt_price(s['tp2'])}\n\n"
        f"📈 Önceki yükseliş: +%{s['pump']:.2f}\n"
        f"📉 RSI(14): {s['rsi']:.1f}\n"
        f"📊 Hacim / ortalama: {s['volume_ratio']:.2f}x\n"
        f"🕒 24s değişim: {s['change']:+.2f}%\n"
        f"💰 24s hacim: {format_vol(s['volume'])} USDT\n\n"
        f"⚠️ Bu bir analiz sinyalidir, emir açılmaz.\n"
        f"Stop ve hedefler referans değerleridir."
    )


def bot_loop():
    tg(C, "🚀 <b>Futures SHORT tarama botu başlatıldı.</b>\n"
          "Piyasa: Binance USDT-M Futures\n"
          "Zaman aralığı: 5m\n"
          "Emir modu: Yalnızca sinyal, otomatik emir yok.")

    while not stop_event.is_set():
        try:
            pairs = get_futures_tickers()

            if not pairs:
                log("Futures ticker verisi alınamadı.")
                time.sleep(SCAN_DELAY)
                continue

            scanned = 0
            signals = 0

            for pair in pairs:
                if stop_event.is_set():
                    break

                symbol = pair["symbol"]

                if time.time() - son_sinyaller.get(symbol, 0) < COOLDOWN:
                    continue

                result = analyze_pair(pair)
                scanned += 1

                if result:
                    message = build_signal(result)

                    if tg(C, message):
                        son_sinyaller[symbol] = time.time()
                        signals += 1
                        log(
                            f"SHORT adayı: {symbol} | "
                            f"Skor {result['score']}/6"
                        )

                # İstekleri gereksiz yere sıklaştırma.
                time.sleep(0.12)

            log(
                f"Tarama tamamlandı: {scanned} parite | "
                f"{signals} yeni alarm"
            )

            time.sleep(SCAN_DELAY)

        except Exception as e:
            log(f"Tarama hatası: {e}")
            time.sleep(10)


# =====================================================
# RENDER WEB SUNUCUSU
# =====================================================

class QuietHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"Futures signal bot is running."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def run_dummy_server():
    port = int(os.environ.get("PORT", "10000"))

    class ReusableServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    try:
        with ReusableServer(
            ("0.0.0.0", port), QuietHandler
        ) as server:
            log(f"Render web sunucusu port {port} üzerinde hazır.")
            server.serve_forever()
    except Exception as e:
        log(f"Web sunucusu hatası: {e}")


# =====================================================
# BAŞLAT
# =====================================================

if __name__ == "__main__":
    if not T:
        raise SystemExit(
            "BOT_TOKEN bulunamadı. Render Environment Variables "
            "bölümünü kontrol et."
        )

    if not C:
        raise SystemExit("CHAT_ID boş.")

    # Önce webhook'u kaldır; ardından getUpdates başlat.
    if not prepare_telegram():
        raise SystemExit(
            "Telegram webhook ayarı doğrulanamadı. "
            "Bot güvenli şekilde başlatılmadı."
        )

    threading.Thread(
        target=run_dummy_server,
        daemon=True
    ).start()

    threading.Thread(
        target=command_listener,
        daemon=True
    ).start()

    bot_loop()

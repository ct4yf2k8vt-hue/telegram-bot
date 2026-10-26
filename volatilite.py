
import urllib.request
import urllib.parse
import urllib.error
import json
import time
import socket
import os
import threading
import http.server
import socketserver

from datetime import datetime, timedelta, timezone

socket.setdefaulttimeout(10)

# =====================================================
# AYARLAR — RENDER ENVIRONMENT DEĞİŞKENLERİ
# =====================================================

T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()

TR_Saati = timezone(timedelta(hours=3))
BASE = "https://fapi.binance.com"

TARAMA_ARALIGI = 180
COOLDOWN = 900
MIN_24H_DEGISIM = 5.0
MIN_24H_HACIM = 5_000_000
MAX_ADAY = 12
MIN_SKOR = 4
BINANCE_BEKLEME_BITIS = 0

son_sinyaller = {}
son_gunluk_veriler = {}
last_update_id = 0

api_lock = threading.Lock()
cooldown_lock = threading.Lock()


# =====================================================
# ZAMAN VE LOG
# =====================================================

def simdi():
    return datetime.now(TR_Saati)


def log(m):
    print(
        f"[{simdi().strftime('%Y-%m-%d %H:%M:%S')} TR] {m}",
        flush=True
    )


def fiyat(v):
    if v is None:
        return "N/A"

    v = float(v)

    if v >= 1000:
        return f"{v:.2f}"
    if v >= 1:
        return f"{v:.4f}"
    if v >= 0.01:
        return f"{v:.6f}"

    return f"{v:.8f}"


def format_vol(v):
    v = float(v)

    if v >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"{v / 1_000:.2f}K"

    return f"{v:.2f}"


# =====================================================
# TELEGRAM — MEVCUT BAĞLANTI KORUNDU
# =====================================================

def tg(chat_id, message):
    if not T:
        log("Telegram gönderilemedi: BOT_TOKEN eksik.")
        return False

    try:
        data = urllib.parse.urlencode({
            "chat_id": str(chat_id),
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true"
        }).encode("utf-8")

        req = urllib.request.Request(
            f"https://api.telegram.org/bot{T}/sendMessage",
            data=data
        )

        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(
                response.read().decode("utf-8")
            )

        if result.get("ok"):
            return True

        log(f"Telegram API hatası: {result}")
        return False

    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""

        log(f"Telegram HTTP {e.code}: {detail[:250]}")
        return False

    except Exception as e:
        log(f"Telegram bağlantı hatası: {e}")
        return False


# =====================================================
# BINANCE API — 418 / 429 BEKLEME KONTROLÜ
# =====================================================

def binance_bekleme_ayarla(saniye, neden):
    global BINANCE_BEKLEME_BITIS

    saniye = max(60, int(saniye))

    with cooldown_lock:
        BINANCE_BEKLEME_BITIS = max(
            BINANCE_BEKLEME_BITIS,
            time.time() + saniye
        )

    log(
        f"{neden}: {saniye} saniye. "
        "Binance taraması geçici olarak durduruldu."
    )


def gj(url):
    global BINANCE_BEKLEME_BITIS

    if "fapi.binance.com" in url:
        if time.time() < BINANCE_BEKLEME_BITIS:
            return None

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"}
        )

        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(
                response.read().decode("utf-8")
            )

    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""

        if "fapi.binance.com" in url and e.code in (418, 429):
            wait = None

            retry = e.headers.get("Retry-After")
            if retry:
                try:
                    wait = int(float(retry))
                except (ValueError, TypeError):
                    pass

            # Binance geçici engel bitiş zamanını bildirmişse kullan.
            import re
            match = re.search(
                r"banned until (\d+)",
                body,
                re.IGNORECASE
            )

            if match:
                try:
                    stamp = int(match.group(1))
                    if stamp > 10**12:
                        stamp //= 1000

                    wait = max(
                        wait or 0,
                        int(stamp - time.time())
                    )
                except Exception:
                    pass

            if wait is None:
                wait = 1800 if e.code == 418 else 300

            binance_bekleme_ayarla(
                wait,
                f"Binance HTTP {e.code}"
            )

        else:
            log(f"HTTP {e.code}: {body[:200]}")

        return None

    except Exception as e:
        log(f"API bağlantı hatası: {e}")
        return None


def futures_api(path, params=None):
    url = BASE + path

    if params:
        url += "?" + urllib.parse.urlencode(params)

    return gj(url)


# =====================================================
# GÖSTERGELER
# =====================================================

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


def calculate_ema(values, period):
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


def get_candles(symbol, interval, limit=60):
    data = futures_api(
        "/fapi/v1/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
    )

    if not isinstance(data, list) or len(data) < 22:
        return None

    # Açık olan son mum kullanılmaz.
    return data[:-1]


def mum_yonu(candles):
    if not candles or len(candles) < 2:
        return "N/A"

    a = float(candles[-2][4])
    b = float(candles[-1][4])

    if b > a:
        return "Yukarı"
    if b < a:
        return "Aşağı"

    return "Yatay"


# =====================================================
# 30 GÜN / 365 GÜN HIGH-LOW
# =====================================================

def get_daily_levels(symbol):
    cached = son_gunluk_veriler.get(symbol)

    if cached and time.time() - cached["time"] < 43200:
        return cached["data"]

    data = futures_api(
        "/fapi/v1/klines",
        {
            "symbol": symbol,
            "interval": "1d",
            "limit": 365
        }
    )

    if not isinstance(data, list) or not data:
        return None

    try:
        highs = [float(x[2]) for x in data]
        lows = [float(x[3]) for x in data]
        n = min(30, len(data))

        levels = {
            "high30": max(highs[-n:]),
            "low30": min(lows[-n:]),
            "high365": max(highs),
            "low365": min(lows)
        }

        son_gunluk_veriler[symbol] = {
            "time": time.time(),
            "data": levels
        }

        return levels

    except (ValueError, TypeError, IndexError):
        return None


# =====================================================
# FUTURES EK METRİKLERİ
# =====================================================

def get_oi_change(symbol):
    data = futures_api(
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

        return (new - old) / old * 100

    except (ValueError, TypeError, KeyError):
        return None


def get_long_short(symbol):
    data = futures_api(
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
        return {
            "ratio": float(data[-1]["longShortRatio"]),
            "long": float(data[-1]["longAccount"]) * 100,
            "short": float(data[-1]["shortAccount"]) * 100
        }
    except (ValueError, TypeError, KeyError):
        return None


# =====================================================
# ADAYLAR — BINANCE USDT-M FUTURES
# =====================================================

def get_candidates():
    tickers = futures_api("/fapi/v1/ticker/24hr")

    if not isinstance(tickers, list):
        return None

    candidates = []

    for t in tickers:
        try:
            symbol = t["symbol"]

            if not symbol.endswith("USDT"):
                continue

            # Kaldıraçlı token isimlerini ele.
            if any(x in symbol for x in (
                "UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT"
            )):
                continue

            change = float(t["priceChangePercent"])
            volume = float(t["quoteVolume"])

            if change < MIN_24H_DEGISIM:
                continue

            if volume < MIN_24H_HACIM:
                continue

            candidates.append(t)

        except (ValueError, TypeError, KeyError):
            continue

    candidates.sort(
        key=lambda x: float(x.get("quoteVolume", 0)),
        reverse=True
    )

    return candidates[:MAX_ADAY]


# =====================================================
# SHORT DÖNÜŞ ANALİZİ
# =====================================================

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
        opens = [float(x[1]) for x in one]
        highs = [float(x[2]) for x in one]
        lows = [float(x[3]) for x in one]
        volumes = [float(x[7]) for x in one]

        last = one[-1]
        entry = float(last[4])
        open_price = float(last[1])

        if entry <= 0:
            return None

        # Önceki 5 kapanmış mumun fiyat hareketi.
        start = float(one[-6][4])
        move5 = (
            (entry - start) / start * 100
            if start else 0
        )

        rsi = calculate_rsi(closes)
        ema11 = calculate_ema(closes, 11)
        ema21 = calculate_ema(closes, 21)

        previous_volumes = volumes[-21:-1]
        average_volume = (
            sum(previous_volumes) / len(previous_volumes)
            if previous_volumes else 0
        )

        volume_ratio = (
            volumes[-1] / average_volume
            if average_volume > 0 else 0
        )

        # Kapanış önceki 5 mumun destek seviyesinin altında olmalı.
        support = min(lows[-6:-1])
        support_break = entry < support
        red_candle = entry < open_price

        score = 0
        reasons = []

        if move5 >= 1:
            score += 1
            reasons.append(
                f"Önceki 5 mum hareketi: %{move5:.2f}"
            )

        if rsi is not None and rsi >= 60:
            score += 1
            reasons.append(f"RSI: {rsi:.1f}")

        if volume_ratio >= 1.5:
            score += 1
            reasons.append(
                f"Hacim: ortalamanın {volume_ratio:.1f} katı"
            )

        if ema11 is not None and ema21 is not None:
            if ema11 >= ema21:
                score += 1
                ema_text = "EMA11 ≥ EMA21 (yukarı)"
            else:
                ema_text = "EMA11 < EMA21 (aşağı)"
        else:
            ema_text = "N/A"

        if support_break:
            score += 1
            reasons.append("Destek kırılımı")

        if red_candle:
            score += 1
            reasons.append("Kırmızı mum")

        # En az 4/6 teyit, destek kırılımı ve kırmızı mum.
        if score < MIN_SKOR:
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

        risk_pct = risk / entry * 100

        # Aşırı geniş stoplu sinyalleri ele.
        if risk_pct > 5:
            return None

        tp1 = entry - risk
        tp2 = entry - 2 * risk

        change24 = float(ticker["priceChangePercent"])
        volume24 = float(ticker["quoteVolume"])
        high24 = float(ticker["highPrice"])
        low24 = float(ticker["lowPrice"])

        oi = get_oi_change(symbol)
        ls = get_long_short(symbol)
        levels = get_daily_levels(symbol)

        oi_text = (
            f"{oi:+.2f}% / 5dk"
            if oi is not None else "N/A"
        )

        if ls:
            ls_text = (
                f"{ls['ratio']:.2f} (hesap oranı)"
            )
        else:
            ls_text = "N/A"

        high30 = levels["high30"] if levels else None
        low30 = levels["low30"] if levels else None
        high365 = levels["high365"] if levels else None
        low365 = levels["low365"] if levels else None

        rsi_text = f"{rsi:.1f}" if rsi is not None else "N/A"
        now_text = simdi().strftime("%Y-%m-%d %H:%M:%S")

        message = (
            f"🔻 <b>SHORT ADAYI — TEYİTLİ DÖNÜŞ</b>\n"
            f"<b>#{symbol}</b> · Binance USDT-M Futures\n"
            f"⭐ Sinyal skoru: {score}/6\n\n"

            f"🏷 Giriş referansı: {fiyat(entry)}\n"
            f"🛑 Stop-loss: {fiyat(stop)} "
            f"(girişten +%{risk_pct:.2f})\n"
            f"🎯 TP1 (1R): {fiyat(tp1)}\n"
            f"🎯 TP2 (2R): {fiyat(tp2)}\n\n"

            f"⏱ Zaman dilimleri (son kapanmış mum)\n"
            f"• 1dk: {mum_yonu(one)} | "
            f"5dk: {mum_yonu(five)} | "
            f"15dk: {mum_yonu(fifteen)}\n"

            f"📉 EMA: {ema_text}\n"
            f"📊 RSI(1dk): {rsi_text} | "
            f"Hacim: ortalamanın {volume_ratio:.1f} katı\n"

            f"📈 24s değişim: {change24:+.2f}% | "
            f"24s hacim: {volume24 / 1_000_000:.2f}M USDT\n"

            f"💹 Açık pozisyon değişimi: {oi_text}\n"
            f"👥 Long/Short hesap oranı: {ls_text}\n"

            f"🗓 30g High/Low: "
            f"{fiyat(high30)} / {fiyat(low30)}\n"
            f"📆 365g High/Low: "
            f"{fiyat(high365)} / {fiyat(low365)}\n\n"

            f"🔎 <b>Dönüş teyitleri</b>\n"
            + "".join(f"• {r}\n" for r in reasons)
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
            "message": message
        }

    except Exception as e:
        log(f"{symbol} analiz hatası: {e}")
        return None


# =====================================================
# TELEGRAM /ratio KOMUTU — MEVCUT ÖZELLİK KORUNDU
# =====================================================

def command_listener():
    global last_update_id

    if not T:
        log("UYARI: BOT_TOKEN eksik; /ratio dinleyicisi başlatılamadı.")
        return

    while True:
        try:
            url = (
                f"https://api.telegram.org/bot{T}/getUpdates"
                f"?offset={last_update_id + 1}&timeout=10"
            )

            updates = gj(url)

            if updates and updates.get("ok"):
                for u in updates.get("result", []):
                    last_update_id = u["update_id"]

                    if "message" not in u:
                        continue

                    message_data = u["message"]
                    text = message_data.get("text", "")
                    chat_id = message_data["chat"]["id"]

                    if not text.startswith("/ratio"):
                        continue

                    parts = text.split()

                    if len(parts) < 2:
                        tg(chat_id, "⚠️ Kullanım: /ratio btcusdt")
                        continue

                    symbol = parts[1].upper()

                    if not symbol.endswith("USDT"):
                        symbol += "USDT"

                    period = (
                        parts[2].lower()
                        if len(parts) > 2 else "1h"
                    )

                    allowed_periods = {
                        "5m", "15m", "30m", "1h", "2h",
                        "4h", "6h", "12h", "1d"
                    }

                    if period not in allowed_periods:
                        tg(
                            chat_id,
                            "⚠️ Zaman dilimi: 5m, 15m, 30m, "
                            "1h, 2h, 4h, 6h, 12h veya 1d"
                        )
                        continue

                    data = futures_api(
                        "/futures/data/globalLongShortAccountRatio",
                        {
                            "symbol": symbol,
                            "period": period,
                            "limit": 1
                        }
                    )

                    if isinstance(data, list) and data:
                        d = data[0]

                        reply = (
                            f"Long/Short Ratio 📊\n"
                            f"Binance Futures - {symbol}\n"
                            f"⏳ <b>Timeframe:</b> {period}\n"
                            f"📈 <b>Long:</b> "
                            f"{float(d['longAccount']) * 100:.2f}%\n"
                            f"📉 <b>Short:</b> "
                            f"{float(d['shortAccount']) * 100:.2f}%\n"
                            f"⚖️ <b>Ratio:</b> "
                            f"{float(d['longShortRatio']):.2f}\n\n"
                            f"<i>Last Updated: "
                            f"{simdi().strftime('%H:%M')} (TR)</i>"
                        )

                        tg(chat_id, reply)
                    else:
                        tg(
                            chat_id,
                            f"❌ {symbol} için veri bulunamadı "
                            "veya Binance API beklemede."
                        )

        except Exception as e:
            log(f"Telegram komut dinleyicisi hatası: {e}")

        time.sleep(2)


# =====================================================
# FUTURES OTOMATİK TARAMA
# =====================================================

def bot_loop():
    log("Binance Futures SHORT bot başlatılıyor.")

    if not T:
        log("UYARI: Render BOT_TOKEN değişkeni bulunamadı.")

    if not C:
        log("UYARI: CHAT_ID değişkeni boş.")

    if T and C:
        if tg(
            C,
            "✅ <b>SHORT FUTURES BOT BAŞLADI</b>\n\n"
            "Telegram bağlantısı test ediliyor.\n"
            "Bot gerçek emir açmaz; yalnızca sinyal gönderir."
        ):
            log("Telegram başlangıç mesajı gönderildi.")
        else:
            log("Telegram başlangıç mesajı gönderilemedi.")

    while True:
        try:
            if time.time() < BINANCE_BEKLEME_BITIS:
                remaining = int(
                    BINANCE_BEKLEME_BITIS - time.time()
                )
                log(
                    f"Binance bekleme süresi: {remaining} saniye; "
                    "tarama duraklatıldı."
                )
                time.sleep(min(180, max(5, remaining)))
                continue

            candidates = get_candidates()

            if candidates is None:
                log("Futures adayları alınamadı; 60 saniye sonra tekrar.")
                time.sleep(60)
                continue

            log(
                f"Futures adayları: {len(candidates)} "
                f"| En az 24s değişim: %{MIN_24H_DEGISIM:.1f}"
            )

            alerts = 0

            for ticker in candidates:
                if time.time() < BINANCE_BEKLEME_BITIS:
                    break

                symbol = ticker["symbol"]

                if (
                    symbol in son_sinyaller
                    and time.time() - son_sinyaller[symbol] < COOLDOWN
                ):
                    continue

                result = analyze_symbol(ticker)

                if result:
                    if tg(C, result["message"]):
                        son_sinyaller[symbol] = time.time()
                        alerts += 1
                        log(
                            f"SİNYAL GÖNDERİLDİ: {symbol} "
                            f"| Skor: {result['score']}/6"
                        )
                    else:
                        log(f"Telegram sinyali gönderemedi: {symbol}")

                # Binance API isteklerini gereksiz sıklaştırma.
                time.sleep(1)

            log(
                f"Tarama tamamlandı: {len(candidates)} aday "
                f"| {alerts} alarm"
            )

            time.sleep(TARAMA_ARALIGI)

        except Exception as e:
            log(f"Tarama motoru hatası: {e}")
            time.sleep(30)


# =====================================================
# RENDER PORT SUNUCUSU — MEVCUT YAPI KORUNDU
# =====================================================

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"Futures SHORT bot is running!"

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format_string, *args):
        return


def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))

    class ReusableServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with ReusableServer(("0.0.0.0", port), Handler) as server:
        log(f"Health server port: {port}")
        server.serve_forever()


# =====================================================
# BAŞLAT
# =====================================================

if __name__ == "__main__":
    threading.Thread(
        target=run_dummy_server,
        daemon=True
    ).start()

    threading.Thread(
        target=command_listener,
        daemon=True
    ).start()

    bot_loop()

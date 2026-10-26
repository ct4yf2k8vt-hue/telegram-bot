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
import re
from datetime import datetime, timedelta, timezone

# =====================================================
# AYARLAR
# =====================================================

socket.setdefaulttimeout(15)

T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip() # 7/24 uyanık kalmak için

TR_Saati = timezone(timedelta(hours=3))
BASE = "https://fapi.binance.com"

TARAMA_ARALIGI = 120 # 2 dakika (Daha hızlı tarama için düşürdüm, API'yi yormaz)
COOLDOWN = 900
MIN_24H_DEGISIM = 5.0
MIN_24H_HACIM = 5_000_000
MAX_ADAY = 5
MIN_SKOR = 4

COOLDOWN_FILE = "/tmp/binance_cooldown.json"

BINANCE_BEKLEME_BITIS = 0
son_sinyaller = {}
son_gunluk_veriler = {}
cooldown_lock = threading.Lock()
last_update_id = 0

# =====================================================
# ZAMAN / LOG / YARDIMCI
# =====================================================

def simdi():
    return datetime.now(TR_Saati)

def log(m):
    print(f"[{simdi().strftime('%Y-%m-%d %H:%M:%S')} TR] {m}", flush=True)

def fiyat(v):
    if v is None: return "N/A"
    v = float(v)
    if v >= 1000: return f"{v:.2f}"
    if v >= 1: return f"{v:.4f}"
    if v >= 0.01: return f"{v:.6f}"
    return f"{v:.8f}"

def format_vol(v):
    v = float(v)
    if v >= 1_000_000: return f"{v / 1_000_000:.2f}M"
    if v >= 1_000: return f"{v / 1_000:.2f}K"
    return f"{v:.2f}"

# =====================================================
# BINANCE BEKLEME SÜRESİ
# =====================================================

def beklemeyi_yukle():
    global BINANCE_BEKLEME_BITIS
    try:
        with open(COOLDOWN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        BINANCE_BEKLEME_BITIS = float(data.get("until", 0))
        if BINANCE_BEKLEME_BITIS > time.time():
            kalan = int(BINANCE_BEKLEME_BITIS - time.time())
            log(f"Kayıtlı Binance beklemesi yüklendi: {kalan} saniye")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        BINANCE_BEKLEME_BITIS = 0

def binance_bekleme_ayarla(saniye, neden):
    global BINANCE_BEKLEME_BITIS
    saniye = max(60, int(saniye))
    with cooldown_lock:
        BINANCE_BEKLEME_BITIS = max(BINANCE_BEKLEME_BITIS, time.time() + saniye)
        bitis = BINANCE_BEKLEME_BITIS
        try:
            with open(COOLDOWN_FILE, "w", encoding="utf-8") as f:
                json.dump({"until": bitis}, f)
        except OSError as e:
            log(f"Bekleme dosyası yazılamadı: {e}")
    log(f"{neden}: yaklaşık {int(bitis - time.time())} saniye. Binance istekleri durduruldu.")

def binance_bekliyor():
    return time.time() < BINANCE_BEKLEME_BITIS

# =====================================================
# TELEGRAM & API
# =====================================================

def tg(chat_id, message):
    if not T or not chat_id: return False
    try:
        data = urllib.parse.urlencode({
            "chat_id": str(chat_id),
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true"
        }).encode("utf-8")
        req = urllib.request.Request(f"https://api.telegram.org/bot{T}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
        return result.get("ok", False)
    except Exception as e:
        log(f"Telegram hatası: {e}")
        return False

def gj(url):
    if "fapi.binance.com" in url and binance_bekliyor(): return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
        if "fapi.binance.com" in url and binance_bekliyor(): return None
        return result
    except urllib.error.HTTPError as e:
        try: body = e.read().decode("utf-8", errors="replace")
        except: body = ""
        if "fapi.binance.com" in url and e.code in (418, 429):
            wait = 1800 if e.code == 418 else 300
            retry = e.headers.get("Retry-After")
            if retry:
                try: wait = int(float(retry))
                except: pass
            match = re.search(r"banned until (\d+)", body, re.IGNORECASE)
            if match:
                try:
                    stamp = int(match.group(1))
                    if stamp > 10**12: stamp //= 1000
                    wait = max(wait, int(stamp - time.time()))
                except: pass
            binance_bekleme_ayarla(wait, f"Binance HTTP {e.code}")
        return None
    except Exception:
        return None

def futures_api(path, params=None):
    if binance_bekliyor(): return None
    url = BASE + path
    if params: url += "?" + urllib.parse.urlencode(params)
    return gj(url)

# =====================================================
# /RATIO KOMUT DİNLEYİCİSİ
# =====================================================

def command_listener():
    global last_update_id
    log("Telegram Komut Dinleyicisi (/ratio) başladı.")
    while True:
        try:
            updates = gj(f"https://api.telegram.org/bot{T}/getUpdates?offset={last_update_id + 1}&timeout=10")
            if updates and updates.get("ok"):
                for u in updates["result"]:
                    last_update_id = u["update_id"]
                    if "message" in u and "text" in u["message"]:
                        text = u["message"]["text"]
                        chat_id = u["message"]["chat"]["id"]
                        if text.startswith("/ratio"):
                            parts = text.split()
                            if len(parts) < 2:
                                tg(chat_id, "⚠️ Kullanım: /ratio btcusdt")
                                continue
                            symbol = parts[1].upper()
                            if not symbol.endswith("USDT"): symbol += "USDT"
                            period = parts[2].lower() if len(parts) > 2 else "1h"
                            url = f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period={period}&limit=1"
                            data = gj(url)
                            if data and len(data) > 0:
                                d = data[0]
                                msg = (f"Long/Short Ratio📊\nBinance - {symbol}\n⏳ <b>Timeframe:</b> {period}\n"
                                       f"📈 <b>Long:</b> {float(d['longAccount'])*100:.2f}%\n"
                                       f"📉 <b>Short:</b> {float(d['shortAccount'])*100:.2f}%\n"
                                       f"⚖️ <b>Ratio:</b> {float(d['longShortRatio']):.2f}\n\n"
                                       f"<i>Last Updated: {simdi().strftime('%H:%M')} (TR)</i>")
                                tg(chat_id, msg)
                            else:
                                tg(chat_id, f"❌ {symbol} için veri bulunamadı.")
        except:
            pass
        time.sleep(2)

# =====================================================
# KALP ATIŞI VE SELF-PING (7/24 UYANIK KALMA)
# =====================================================

def heartbeat():
    while True:
        time.sleep(1800) # 30 dakika
        if T and C:
            tg(C, f"🤖 <b>Sistem Raporu:</b> Bot sorunsuz çalışıyor. (Log ekranı donmuş olabilir, Telegram aktiftir.)\n⏰ {simdi().strftime('%H:%M')}")

def keep_alive():
    if not RENDER_URL: return
    while True:
        try:
            urllib.request.urlopen(RENDER_URL, timeout=10).read()
        except: pass
        time.sleep(600) # 10 dakika

# =====================================================
# GÖSTERGELER
# =====================================================

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1: return None
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0: return 100.0
    return 100 - (100 / (1 + (avg_gain / avg_loss)))

def calculate_ema(values, period):
    if len(values) < period: return None
    result = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    for value in values[period:]:
        result = value * multiplier + result * (1 - multiplier)
    return result

def get_candles(symbol, interval, limit=60):
    data = futures_api("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    if binance_bekliyor() or not isinstance(data, list) or len(data) < 22: return None
    return data[:-1]

def mum_yonu(candles):
    if not candles or len(candles) < 2: return "N/A"
    a = float(candles[-2][4])
    b = float(candles[-1][4])
    if b > a: return "Yukarı"
    if b < a: return "Aşağı"
    return "Yatay"

# =====================================================
# VERİ ÇEKME FONKSİYONLARI
# =====================================================

def get_daily_levels(symbol):
    cached = son_gunluk_veriler.get(symbol)
    if cached and time.time() - cached["time"] < 43200: return cached["data"]
    data = futures_api("/fapi/v1/klines", {"symbol": symbol, "interval": "1d", "limit": 365})
    if binance_bekliyor() or not isinstance(data, list) or not data: return None
    try:
        highs = [float(x[2]) for x in data]
        lows = [float(x[3]) for x in data]
        n = min(30, len(data))
        levels = {"high30": max(highs[-n:]), "low30": min(lows[-n:]), "high365": max(highs), "low365": min(lows)}
        son_gunluk_veriler[symbol] = {"time": time.time(), "data": levels}
        return levels
    except: return None

def get_oi_change(symbol):
    data = futures_api("/futures/data/openInterestHist", {"symbol": symbol, "period": "5m", "limit": 2})
    if binance_bekliyor() or not isinstance(data, list) or len(data) < 2: return None
    try:
        old = float(data[0]["sumOpenInterest"])
        new = float(data[-1]["sumOpenInterest"])
        return (new - old) / old * 100 if old != 0 else None
    except: return None

def get_long_short(symbol):
    data = futures_api("/futures/data/globalLongShortAccountRatio", {"symbol": symbol, "period": "5m", "limit": 1})
    if binance_bekliyor() or not isinstance(data, list) or not data: return None
    try:
        return {"ratio": float(data[-1]["longShortRatio"]), "long": float(data[-1]["longAccount"]) * 100, "short": float(data[-1]["shortAccount"]) * 100}
    except: return None

# =====================================================
# ADAY SEÇİMİ VE ANALİZ
# =====================================================

def get_candidates():
    tickers = futures_api("/fapi/v1/ticker/24hr")
    if binance_bekliyor() or not isinstance(tickers, list): return None
    candidates = []
    for t in tickers:
        try:
            symbol = t["symbol"]
            if not symbol.endswith("USDT") or any(x in symbol for x in ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")): continue
            change = float(t["priceChangePercent"])
            volume = float(t["quoteVolume"])
            if change < MIN_24H_DEGISIM or volume < MIN_24H_HACIM: continue
            candidates.append(t)
        except: continue
    candidates.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
    return candidates[:MAX_ADAY]

def analyze_symbol(ticker):
    symbol = ticker["symbol"]
    one = get_candles(symbol, "1m", 60)
    five = get_candles(symbol, "5m", 30)
    fifteen = get_candles(symbol, "15m", 30)
    if not one or not five or not fifteen: return None

    try:
        closes = [float(x[4]) for x in one]
        opens = [float(x[1]) for x in one]
        highs = [float(x[2]) for x in one]
        lows = [float(x[3]) for x in one]
        volumes = [float(x[7]) for x in one]

        last = one[-1]
        entry = float(last[4])
        open_price = float(last[1])
        if entry <= 0: return None

        start = float(one[-6][4])
        move5 = (entry - start) / start * 100 if start else 0
        rsi = calculate_rsi(closes)
        ema11 = calculate_ema(closes, 11)
        ema21 = calculate_ema(closes, 21)

        previous_volumes = volumes[-21:-1]
        average_volume = sum(previous_volumes) / len(previous_volumes) if previous_volumes else 0
        volume_ratio = volumes[-1] / average_volume if average_volume > 0 else 0

        support = min(lows[-6:-1])
        support_break = entry < support
        red_candle = entry < open_price

        score = 0
        reasons = []

        if move5 >= 1: score += 1; reasons.append(f"Önceki 5 mum hareketi: %{move5:.2f}")
        if rsi is not None and rsi >= 60: score += 1; reasons.append(f"RSI: {rsi:.1f}")
        if volume_ratio >= 1.5: score += 1; reasons.append(f"Hacim: ortalamanın {volume_ratio:.1f} katı")
        if ema11 is not None and ema21 is not None:
            if ema11 >= ema21: score += 1; ema_text = "EMA11 ≥ EMA21 (yukarı)"
            else: ema_text = "EMA11 < EMA21 (aşağı)"
        else: ema_text = "N/A"
        if support_break: score += 1; reasons.append("Destek kırılımı")
        if red_candle: score += 1; reasons.append("Kırmızı mum")

        if score < MIN_SKOR or not support_break or not red_candle: return None

        stop = max(highs[-5:])
        if stop <= entry: stop = entry * 1.005
        risk = stop - entry
        if risk <= 0: return None
        risk_pct = risk / entry * 100
        if risk_pct > 5: return None

        tp1 = entry - risk
        tp2 = entry - 2 * risk
        change24 = float(ticker["priceChangePercent"])
        volume24 = float(ticker["quoteVolume"])

        oi = get_oi_change(symbol)
        ls = get_long_short(symbol)
        levels = get_daily_levels(symbol)
        if binance_bekliyor(): return None

        oi_text = f"{oi:+.2f}% / 5dk" if oi is not None else "N/A"
        ls_text = f"{ls['ratio']:.2f} (hesap oranı)" if ls else "N/A"
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
            f"🛑 Stop-loss: {fiyat(stop)} (girişten +%{risk_pct:.2f})\n"
            f"🎯 TP1 (1R): {fiyat(tp1)}\n"
            f"🎯 TP2 (2R): {fiyat(tp2)}\n\n"
            f"⏱ Zaman dilimleri (son kapanmış mum)\n"
            f"• 1dk: {mum_yonu(one)} | 5dk: {mum_yonu(five)} | 15dk: {mum_yonu(fifteen)}\n"
            f"📉 EMA: {ema_text}\n"
            f"📊 RSI(1dk): {rsi_text} | Hacim: ortalamanın {volume_ratio:.1f} katı\n"
            f"📈 24s değişim: {change24:+.2f}% | 24s hacim: {volume24 / 1_000_000:.2f}M USDT\n"
            f"💹 Açık pozisyon değişimi: {oi_text}\n"
            f"👥 Long/Short hesap oranı: {ls_text}\n"
            f"🗓 30g High/Low: {fiyat(high30)} / {fiyat(low30)}\n"
            f"📆 365g High/Low: {fiyat(high365)} / {fiyat(low365)}\n\n"
            f"🔎 <b>Dönüş teyitleri</b>\n"
            + "".join(f"• {r}\n" for r in reasons)
            + f"\n🔗 <a href='https://www.binance.com/en/futures/{symbol}'>Binance Futures paritesi</a>\n"
            f"🕒 {now_text}\n\n"
            f"Bilgilendirme amaçlı sinyaldir; garanti değildir. Bot gerçek emir açmaz."
        )
        return {"symbol": symbol, "score": score, "message": message}

    except Exception as e:
        log(f"{symbol} analiz hatası: {e}")
        return None

# =====================================================
# ANA TARAMA DÖNGÜSÜ
# =====================================================

def bot_loop():
    log("Binance Futures SHORT bot başlatılıyor.")
    beklemeyi_yukle()

    if T and C:
        tg(C, "✅ <b>SHORT FUTURES BOT BAŞLADI</b>\n\nTelegram bağlantısı test ediliyor.\nBinance USDT-M Futures botu aktif.\n/ratio komutu desteklenmektedir.")

    while True:
        try:
            if binance_bekliyor():
                kalan = int(BINANCE_BEKLEME_BITIS - time.time())
                log(f"Binance beklemesi sürüyor: {kalan} saniye.")
                time.sleep(min(180, max(5, kalan)))
                continue

            candidates = get_candidates()
            if binance_bekliyor(): continue
            if candidates is None:
                log("Aday listesi alınamadı; 60 saniye sonra denenecek.")
                time.sleep(60)
                continue

            log(f"Futures adayları: {len(candidates)} | En az 24s değişim: %{MIN_24H_DEGISIM:.1f}")

            alerts = 0
            tarama_durdu = False

            for ticker in candidates:
                if binance_bekliyor():
                    tarama_durdu = True
                    break

                symbol = ticker["symbol"]
                if symbol in son_sinyaller and time.time() - son_sinyaller[symbol] < COOLDOWN:
                    continue

                result = analyze_symbol(ticker)
                if binance_bekliyor():
                    tarama_durdu = True
                    break

                if result:
                    if tg(C, result["message"]):
                        son_sinyaller[symbol] = time.time()
                        alerts += 1
                        log(f"SİNYAL GÖNDERİLDİ: {symbol} | Skor: {result['score']}/6")

                time.sleep(2)

            if tarama_durdu:
                log("Binance kısıtlaması nedeniyle mevcut tarama kesildi.")
                continue

            log(f"Tarama tamamlandı: {len(candidates)} aday | {alerts} alarm")
            time.sleep(TARAMA_ARALIGI)

        except Exception as e:
            log(f"Tarama motoru hatası: {e}")
            time.sleep(30)

# =====================================================
# RENDER HEALTH SERVER
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

    def log_message(self, format_string, *args): return

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
    threading.Thread(target=run_dummy_server, daemon=True).start()
    
    if T:
        threading.Thread(target=command_listener, daemon=True).start()
        threading.Thread(target=heartbeat, daemon=True).start()
        
        if RENDER_URL:
            threading.Thread(target=keep_alive, daemon=True).start()
            log("Self-Ping (Uyanık Kalma) aktif.")
            
    bot_loop()

import urllib.request
import urllib.parse
import urllib.error
import json
import re
import collections
import time
import socket
import os
import threading
import http.server
from datetime import datetime, timedelta, timezone

# =====================================================
# AYARLAR
# =====================================================

socket.setdefaulttimeout(15)

T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()

TR_Saati = timezone(timedelta(hours=3))
BASE = "https://api.binance.com"
FAPI = "https://fapi.binance.com"

# MOTOR ÇALIŞMA SÜRELERİ
CHARTIST_ARALIGI = 3600
PUMP_ARALIGI = 60
VOLATILITE_ARALIGI = 60
FORMASYON_ARALIGI = 3600
EMA_RIBBON_ARALIGI = 3600
MACD_ARALIGI = 3600
MIDAS_SETUP_ARALIGI = 7200
ATH_ATL_ARALIGI = 3600
ICHIMOKU_ARALIGI = 3600
CHART_PATTERN_ARALIGI = 1800
SHORT_GAINERS_ARALIGI = 300
TOP_GAINERS_ARALIGI = 14400   # 4 Saat
PUMP_WINNER_ARALIGI = 21600   # 6 Saat

# FİLTRELER
MIN_HACIM_CHARTIST = 10_000_000
MIN_HACIM_PUMP = 5_000_000
MIN_HACIM_VOLATILITE = 10_000_000
MIN_HACIM_FORMASYON = 15_000_000
MIN_HACIM_EMA_RIBBON = 10_000_000
MIN_HACIM_MACD = 10_000_000
MIN_HACIM_MIDAS_SETUP = 15_000_000
MIN_HACIM_ATH_ATL = 5_000_000
MIN_HACIM_ICHIMOKU = 10_000_000
MIN_HACIM_PATTERN = 15_000_000

YASAKLI_HISSELER = [
    "AAPLUSDT", "TSLAUSDT", "NVDAUSDT", "COINUSDT", "MSTRUSDT",
    "SQQQUSDT", "TQQQUSDT", "SPYUSDT", "QQQUSDT", "AMZNUSDT",
    "GOOGLUSDT", "METAUSDT", "NFLXUSDT", "BABAUSDT", "MSFTUSDT",
    "HOODUSDT", "CRCLUSDT", "XAUUSDT", "XAGUSDT", "PAXGUSDT"
]

# Sinyal hafızaları
son_sinyaller = {}
son_pump_sinyalleri = {}
son_volatilite_sinyalleri = {}
son_formasyon_sinyalleri = {}
son_ema_ribbon_sinyalleri = {}
son_macd_sinyalleri = {}
son_midas_setup_sinyalleri = {}
son_ath_atl_sinyalleri = {}
son_ichimoku_sinyalleri = {}
son_chart_pattern_sinyalleri = {}
son_short_gainers_sinyalleri = {}

# =====================================================
# YARDIMCI FONKSİYONLAR
# =====================================================

def simdi():
    return datetime.now(TR_Saati)

def log(prefix, m):
    print(f"[{simdi().strftime('%H:%M:%S')}] [{prefix}] {m}", flush=True)

def akilli_fiyat(p):
    if p is None: return "0"
    p = float(p)
    if p >= 1000: return f"{p:.2f}"
    if p >= 1: return f"{p:.4f}"
    if p >= 0.01: return f"{p:.6f}"
    return f"{p:.8f}"

def tg(chat_id, message):
    if not T or not chat_id:
        log("TG", "BOT_TOKEN veya CHAT_ID boş, mesaj gönderilmedi!")
        return False
    try:
        data = urllib.parse.urlencode({
            "chat_id": str(chat_id), "text": message, "parse_mode": "HTML", "disable_web_page_preview": "true"
        }).encode("utf-8")
        req = urllib.request.Request(f"https://api.telegram.org/bot{T}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8")).get("ok", False)
    except urllib.error.HTTPError as e:
        try: body = e.read().decode("utf-8")[:200]
        except Exception: body = ""
        log("TG", f"HTTP {e.code}: {body}")
        return False
    except Exception as e:
        log("TG", f"Hata: {type(e).__name__}: {e}")
        return False

son_uyari = {"t": 0}
ban = {"fapi": 0, "spot": 0}
istek_kuyrugu = collections.deque()
kuyruk_kilit = threading.Lock()
ticker_kilit = threading.Lock()
ticker_cache = {"t": 0, "data": None}
BUTCE = 1000  # dakikada en fazla bu kadar Binance ağırlığı

IPUCLARI = {
    "HTTP 451": "Binance bu sunucunun bölgesini engelliyor. Render'da servisi Frankfurt veya Singapore bölgesinde aç.",
    "HTTP 403": "Binance erişimi reddetti (IP/bölge engeli olabilir).",
    "HTTP 429": "İstek limiti aşıldı.",
    "HTTP 418": "IP geçici olarak banlandı.",
}

def api_uyari(neden, url, ek=""):
    if time.time() - son_uyari["t"] < 1800: return
    son_uyari["t"] = time.time()
    tg(C, f"⚠️ <b>Binance bağlantı sorunu</b>\nNeden: {neden}\nAdres: ...{url.split('?')[0][-40:]}\n{IPUCLARI.get(neden, '')} {ek}")

def ban_ayarla(e, host):
    ra = e.headers.get("Retry-After") if e.headers else None
    sure = int(ra) if ra and ra.isdigit() else (300 if e.code == 418 else 60)
    ban[host] = max(ban[host], time.time() + sure + 5)
    return sure

def agirlik(url):
    if "ticker/24hr" in url and "symbol=" not in url: return 80 if url.startswith(BASE) else 40
    if "/klines" in url:
        m = re.search(r"limit=(\d+)", url)
        l = int(m.group(1)) if m else 500
        return 1 if l < 100 else 2 if l < 500 else 5 if l <= 1000 else 10
    return 1

def butce_bekle(w):
    while True:
        with kuyruk_kilit:
            now = time.time()
            while istek_kuyrugu and now - istek_kuyrugu[0][0] > 60: istek_kuyrugu.popleft()
            if sum(x[1] for x in istek_kuyrugu) + w <= BUTCE:
                istek_kuyrugu.append((now, w))
                return
        time.sleep(1)

def _istek(url):
    host = "fapi" if url.startswith(FAPI) else "spot"
    # Futures banlıysa ticker/mum verisini otomatik Spot'tan çek
    if host == "fapi" and time.time() < ban["fapi"]:
        yol = url[len(FAPI):]
        if yol.startswith("/fapi/v1/ticker/24hr") or yol.startswith("/fapi/v1/klines"):
            url = BASE + yol.replace("/fapi/v1/", "/api/v3/", 1)
            host = "spot"
        else:
            return None
    if time.time() < ban[host]:
        time.sleep(1)
        return None
    butce_bekle(agirlik(url))
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        log("API", f"HTTP {e.code} -> {url.split('?')[0][-45:]}")
        ek = ""
        if e.code in (418, 429):
            sure = ban_ayarla(e, host)
            ek = f"Bot {sure // 60 + 1} dk boyunca {'Futures' if host == 'fapi' else 'Spot'} isteklerini durdurdu."
            if host == "fapi": ek += " Spot verisiyle devam edecek."
        if e.code not in (400, 404): api_uyari(f"HTTP {e.code}", url, ek)
        return None
    except Exception as e:
        log("API", f"{type(e).__name__} -> {url.split('?')[0][-45:]}")
        api_uyari(type(e).__name__, url)
        return None

def gj(url):
    # Ticker verisi 60 sn boyunca ortak kullanılır (tüm motorlar tek istek paylaşır)
    if url.endswith("/fapi/v1/ticker/24hr"):
        with ticker_kilit:
            if ticker_cache["data"] is not None and time.time() - ticker_cache["t"] < 55:
                return ticker_cache["data"]
            data = _istek(url)
            if data is not None:
                ticker_cache["data"] = data
                ticker_cache["t"] = time.time()
            return data
    return _istek(url)

def baglanti_testi(host="fapi"):
    url = f"{FAPI}/fapi/v1/ticker/24hr" if host == "fapi" else f"{BASE}/api/v3/ticker/24hr"
    ad = "Futures" if host == "fapi" else "Spot"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        n = len(hacim_filtre(data, MIN_HACIM_PUMP))
        return True, f"✅ Binance {ad}: bağlı ({len(data)} çift, {n} tanesi taranıyor)"
    except urllib.error.HTTPError as e:
        ek = ""
        if e.code in (418, 429):
            ek = f" Yaklaşık {ban_ayarla(e, host) // 60 + 1} dk sonra tekrar denenecek."
        return False, f"❌ Binance {ad}: HTTP {e.code}\n{IPUCLARI.get(f'HTTP {e.code}', '')}{ek}"
    except Exception as e:
        return False, f"❌ Binance {ad}: {type(e).__name__}"

def hacim_filtre(tickers, min_hacim):
    """USDT çiftleri, yasaklı varlıklar hariç, minimum hacim üstü."""
    out = []
    for t in tickers:
        try:
            s = t['symbol']
            if s.endswith('USDT') and s not in YASAKLI_HISSELER and float(t.get('quoteVolume', 0)) >= min_hacim:
                out.append(s)
        except Exception:
            continue
    return out

def calculate_ema(prices, period):
    if len(prices) < period: return []
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for price in prices[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1: return 50
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0: return 100
    return 100 - (100 / (1 + (avg_gain / avg_loss)))

def calculate_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow + signal: return None, None, None, None
    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)
    if not ema_fast or not ema_slow: return None, None, None, None
    min_len = min(len(ema_fast), len(ema_slow))
    macd_line = [ema_fast[-min_len+i] - ema_slow[-min_len+i] for i in range(min_len)]
    signal_line = calculate_ema(macd_line, signal)
    if len(signal_line) < 2: return None, None, None, None
    return macd_line[-1], signal_line[-1], macd_line[-2], signal_line[-2]

def get_market_trend(tickers):
    try:
        sorted_tickers = sorted(tickers, key=lambda x: float(x.get('quoteVolume', 0)), reverse=True)[:100]
        changes, up_count, down_count = [], 0, 0
        for t in sorted_tickers:
            change = float(t.get('priceChangePercent', 0))
            changes.append(change)
            if change > 0: up_count += 1
            elif change < 0: down_count += 1
        if not changes: return "Bilinmiyor", 0, 0, 0
        avg_change = sum(changes) / len(changes)
        if avg_change > 1.5 and up_count > down_count: trend = "🟢 YUKARI (Boğa)"
        elif avg_change < -1.5 and down_count > up_count: trend = "🔴 AŞAĞI (Ayı)"
        else: trend = "🟡 YATAY (Kararsız)"
        return trend, avg_change, up_count, down_count
    except Exception:
        return "Bilinmiyor", 0, 0, 0

def trap_radar_check(symbol):
    try:
        oi_data = gj(f"{FAPI}/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=2")
        oi_artiyor = False
        if oi_data and len(oi_data) >= 2:
            oi_old = float(oi_data[0]['sumOpenInterest'])
            oi_new = float(oi_data[1]['sumOpenInterest'])
            if oi_old > 0 and ((oi_new - oi_old) / oi_old) * 100 > 0.2: oi_artiyor = True

        taker_data = gj(f"{FAPI}/futures/data/takerlongshortRatio?symbol={symbol}&period=5m&limit=1")
        alici_baskin = False
        if taker_data and len(taker_data) > 0:
            buy_vol = float(taker_data[0]['buyVol'])
            sell_vol = float(taker_data[0]['sellVol'])
            total = buy_vol + sell_vol
            if total > 0 and (buy_vol / total) * 100 > 55: alici_baskin = True

        if not oi_data and not taker_data: return ""
        if oi_artiyor and alici_baskin: return "🐋 <b>BALİNA TEYİDİ (Gerçek Pump)</b>"
        elif not alici_baskin: return "⚠️ <b>DİKKAT: SAHTE PUMP (TUZAK OLABİLİR)</b>"
        else: return "⚖️ <b>Kısmi Teyit</b>"
    except Exception:
        return ""

def find_pivots(highs, lows, window=3):
    pivot_highs = []
    pivot_lows = []
    for i in range(window, len(highs) - window):
        if highs[i] == max(highs[i-window:i+window+1]): pivot_highs.append((i, highs[i]))
        if lows[i] == min(lows[i-window:i+window+1]): pivot_lows.append((i, lows[i]))
    return pivot_highs, pivot_lows

# =====================================================
# MOTOR 1: TOP GAINERS RAPORU (+ piyasa yönü)
# =====================================================
def top_gainers_loop():
    log("TOP_GAINERS", "Günlük En İyi Hareketler Rapor Botu Başladı (Futures).")
    time.sleep(10)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(300); continue

            temiz = [t for t in tickers if t['symbol'] not in YASAKLI_HISSELER and t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) > 5_000_000]
            temiz.sort(key=lambda x: float(x['priceChangePercent']), reverse=True)
            top_5 = temiz[:5]

            if top_5:
                msg = f"🔥 <b>Günün En İyi Hareketleri (Binance {'Spot' if time.time() < ban['fapi'] else 'Futures'})</b>\n"
                msg += "<i>24 saatlik yükseliş ve USDT hacmine göre</i>\n\n"
                medals = ["🥇", "🥈", "🥉", "🏅", "⭐️"]
                for i, t in enumerate(top_5):
                    symbol = t['symbol'].replace("USDT", "")
                    change = float(t['priceChangePercent'])
                    price = float(t['lastPrice'])
                    msg += f"{medals[i]} {symbol} {change:+.2f}% | {akilli_fiyat(price)}\n"
                tg(C, msg)
            time.sleep(TOP_GAINERS_ARALIGI)
        except Exception as e:
            log("TOP_GAINERS", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 2: PUMP WINNER (GÜNÜN KAZANANI)
# =====================================================
def pump_winner_loop():
    log("PUMP_WINNER", "Günün Kazananı Rapor Botu Başladı.")
    time.sleep(20)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(300); continue

            temiz = [t for t in tickers if t['symbol'] not in YASAKLI_HISSELER and t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) > 10_000_000]
            temiz.sort(key=lambda x: float(x['priceChangePercent']), reverse=True)
            if not temiz:
                time.sleep(300); continue

            winner = temiz[0]
            symbol = winner['symbol']
            change_pct = float(winner['priceChangePercent'])
            if change_pct < 15:
                time.sleep(PUMP_WINNER_ARALIGI); continue

            klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1d&limit=1")
            if not isinstance(klines, list) or len(klines) < 1:
                time.sleep(PUMP_WINNER_ARALIGI); continue

            day_low = float(klines[0][3])
            day_high = float(klines[0][2])
            max_move = ((day_high - day_low) / day_low) * 100

            msg = (f"🏆 <b>GÜNÜN KAZANANI: {symbol}</b>\n\n"
                   f"🔥 24 saat içinde en çok yükselen coin!\n\n"
                   f"📉 <b>Dip Fiyat:</b> {akilli_fiyat(day_low)}\n"
                   f"🚀 <b>Zirve Fiyat:</b> {akilli_fiyat(day_high)}\n"
                   f"📈 <b>Maksimum Yükseliş:</b> <b>+{max_move:.1f}%</b>\n\n"
                   f"💡 <b>Değerlendirme:</b> {symbol} gün içerisinde muazzam bir hacim ve fiyat patlaması yaşadı. Pump Dedektör verilerimize göre günün en kârlı hareketi bu coinde gerçekleşti.")
            tg(C, msg)
            time.sleep(PUMP_WINNER_ARALIGI)
        except Exception as e:
            log("PUMP_WINNER", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 3: CHARTIST (EMA200)
# =====================================================
def chartist_loop():
    log("CHARTIST", "Zırhlı EMA200 Motoru Başladı.")
    time.sleep(30)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in hacim_filtre(tickers, MIN_HACIM_CHARTIST):
                for tf in ['8h', '12h', '1d', '3d']:
                    cooldown_key = f"CH_{tf}_{symbol}"
                    if cooldown_key in son_sinyaller and time.time() - son_sinyaller[cooldown_key] < 86400: continue
                    klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval={tf}&limit=260")
                    if not isinstance(klines, list) or len(klines) < 205: continue
                    closes = [float(k[4]) for k in klines[:-1]]
                    curr, prev = closes[-1], closes[-2]
                    ema200 = calculate_ema(closes, 200)
                    if len(ema200) >= 2 and prev <= ema200[-2] and curr > ema200[-1] * 1.01:
                        vol = [float(k[7]) for k in klines[:-1]]
                        if vol[-1] > sum(vol[-21:-1]) / 20:
                            sl = ema200[-1] * 0.97
                            msg = (f"📈 <b>UZUN VADELİ (LONG): {symbol}</b>\n"
                                   f"<i>{tf.upper()} EMA200 Kırılımı</i>\n\n"
                                   f"💰 <b>Giriş:</b> {akilli_fiyat(curr)}\n"
                                   f"🛑 <b>SL:</b> {akilli_fiyat(sl)}\n"
                                   f"🎯 <b>Hedef:</b> {akilli_fiyat(curr * 1.15)}\n\n"
                                   f"📊 <b>Teyit:</b> Hacim Onaylı ve %1 üzeri kapanış.\n\n"
                                   f"💡 <b>Açıklama:</b> EMA200, uzun vadeli trend çizgisidir. Fiyatın bu devasa çizgiyi hacimle yukarı kırması, ayı piyasasının sona erip boğa piyasasının başladığını gösterir.")
                            if tg(C, msg): son_sinyaller[cooldown_key] = time.time()
                    time.sleep(0.05)
            time.sleep(CHARTIST_ARALIGI)
        except Exception as e:
            log("CHARTIST", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 4: PUMP AVCISI
# =====================================================
def pump_loop():
    log("PUMP", "Anlık Pump Avcısı Başladı.")
    time.sleep(45)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in hacim_filtre(tickers, MIN_HACIM_PUMP):
                if symbol in son_pump_sinyalleri and time.time() - son_pump_sinyalleri[symbol] < 900: continue
                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=5m&limit=3")
                if not isinstance(klines, list) or len(klines) < 3: continue
                curr_k, prev_k = klines[-2], klines[-3]
                cc, pc = float(curr_k[4]), float(prev_k[4])
                cv, pv = float(curr_k[7]), float(prev_k[7])
                if pc == 0 or pv == 0: continue
                price_change = ((cc - pc) / pc) * 100
                vol_change = ((cv - pv) / pv) * 100
                vol_diff = cv - pv

                if price_change >= 3.0 and vol_change >= 100.0 and vol_diff >= 50_000:
                    trap_radar = trap_radar_check(symbol)
                    msg = (f"🚀 <b>Pump - {symbol}</b>\n"
                           f"💰 <b>Fiyat:</b> {akilli_fiyat(cc)} (<b>{price_change:+.2f}%</b>)\n"
                           f"📊 <b>Hacim Artışı:</b> {vol_change:+.0f}%\n"
                           f"Volume increased by {vol_diff:.0f} ⬆️\n\n"
                           f"{trap_radar}\n\n"
                           f"💡 <b>Açıklama:</b> 5 dakikalık grafikte aniden sert bir hacim ve fiyat patlaması yaşandı. Trap Radar sayesinde bu hareketin arkasında gerçek balinaların mı yoksa sahte botların mı olduğu analiz edildi.")
                    if tg(C, msg): son_pump_sinyalleri[symbol] = time.time()
                time.sleep(0.05)
            time.sleep(PUMP_ARALIGI)
        except Exception as e:
            log("PUMP", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 5: ANORMAL VOLATİLİTE / DUMP AVCISI
# =====================================================
def volatilite_loop():
    log("VOLATILITE", "Anormal Volatilite ve Dump Avcısı Başladı (15m).")
    time.sleep(55)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in hacim_filtre(tickers, MIN_HACIM_VOLATILITE):
                if symbol in son_volatilite_sinyalleri and time.time() - son_volatilite_sinyalleri[symbol] < 1800: continue
                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=15m&limit=25")
                if not isinstance(klines, list) or len(klines) < 25: continue

                opens = [float(k[1]) for k in klines]
                closes = [float(k[4]) for k in klines]
                vols = [float(k[7]) for k in klines]

                curr_open = opens[-2]
                curr_close = closes[-2]
                curr_vol = vols[-2]
                avg_vol = sum(vols[-22:-2]) / 20
                if curr_open == 0 or avg_vol == 0: continue

                price_change_pct = ((curr_close - curr_open) / curr_open) * 100
                vol_multiplier = curr_vol / avg_vol

                if abs(price_change_pct) >= 2.5 and vol_multiplier >= 3.0:
                    yon = "DUMP (ANI DÜŞÜŞ) 🔻" if price_change_pct < 0 else "PUMP (ANI YÜKSELİŞ) 🚀"
                    emoji = "🚨" if price_change_pct < 0 else "⚡"
                    msg = (f"{emoji} <b>ANORMAL VOLATİLİTE UYARISI</b> {emoji}\n"
                           f"<b>Coin:</b> {symbol} (15m Grafik)\n\n"
                           f"📉 <b>Hareket:</b> {yon}\n"
                           f"📊 <b>Değişim:</b> <b>{price_change_pct:+.2f}%</b>\n"
                           f"📈 <b>Hacim Patlaması:</b> Ortalamanın <b>{vol_multiplier:.1f}x</b> katı\n"
                           f"💰 <b>Kapanış Fiyatı:</b> {akilli_fiyat(curr_close)}\n\n"
                           f"⚠️ <i>Piyasada ani bir likidite değişimi veya büyük bir emir gerçekleşmiş olabilir. Dikkatli olun!</i>")
                    if tg(C, msg):
                        son_volatilite_sinyalleri[symbol] = time.time()
                        log("VOLATILITE", f"🔥 SİNYAL: {symbol} ({yon})")
                time.sleep(0.1)
            time.sleep(VOLATILITE_ARALIGI)
        except Exception as e:
            log("VOLATILITE", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 6: FORMASYON (MSS) - KAPANMIŞ MUM, FİLTRELİ
# =====================================================
def formasyon_loop():
    log("FORMASYON", "Teyitli Formasyon ve Likidite Avcısı Başladı.")
    time.sleep(60)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in hacim_filtre(tickers, MIN_HACIM_FORMASYON):
                if symbol in son_formasyon_sinyalleri and time.time() - son_formasyon_sinyalleri[symbol] < 86400: continue

                klines_5m = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=5m&limit=100")
                klines_15m = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=15m&limit=50")
                if not isinstance(klines_5m, list) or not isinstance(klines_15m, list): continue
                if len(klines_5m) < 100 or len(klines_15m) < 50: continue

                # Son (henüz kapanmamış) mumu at
                klines_5m = klines_5m[:-1]
                klines_15m = klines_15m[:-1]

                highs = [float(k[2]) for k in klines_5m]
                lows = [float(k[3]) for k in klines_5m]
                closes = [float(k[4]) for k in klines_5m]
                vols = [float(k[7]) for k in klines_5m]

                closes_15m = [float(k[4]) for k in klines_15m]
                e11 = calculate_ema(closes_15m, 11)
                e21 = calculate_ema(closes_15m, 21)
                if not e11 or not e21: continue
                ema11, ema21 = e11[-1], e21[-1]
                rsi = calculate_rsi(closes_15m)

                current_price = closes[-1]
                avg_vol = sum(vols[-21:-1]) / 20
                current_vol = vols[-1]
                if avg_vol == 0: continue
                is_vol_teyit = current_vol > (avg_vol * 1.5)

                p_highs, p_lows = find_pivots(highs, lows)
                if len(p_highs) < 3 or len(p_lows) < 3: continue

                last_h = p_highs[-3:]
                last_l = p_lows[-3:]

                sinyal = None; formasyon_adi = ""; yon = ""; emoji = ""; hedef = 0; stop = 0; aciklama = ""

                if abs(last_h[0][1] - last_h[2][1]) / last_h[0][1] < 0.03 and current_price < last_l[-1][1] and is_vol_teyit:
                    if rsi < 50 and ema11 < ema21:
                        sinyal = True; formasyon_adi = "Çift Tepe (M)"; yon = "SHORT"; emoji = "🔻"
                        hedef = last_l[-1][1] - (last_h[1][1] - last_l[-1][1]); stop = last_h[2][1] * 1.02
                        aciklama = "İkinci tepe ilkini geçemedi ve boyun çizgisi hacimle kırıldı. 15m trend ve RSI düşüşü destekliyor."

                elif abs(last_l[0][1] - last_l[2][1]) / last_l[0][1] < 0.03 and current_price > last_h[-1][1] and is_vol_teyit:
                    if rsi > 50 and ema11 > ema21:
                        sinyal = True; formasyon_adi = "Çift Dip (W)"; yon = "LONG"; emoji = "🚀"
                        hedef = last_h[-1][1] + (last_h[-1][1] - last_l[1][1]); stop = last_l[2][1] * 0.98
                        aciklama = "İkinci dip satıcıları tüketti ve boyun çizgisi hacimle kırıldı. 15m trend ve RSI yükselişi destekliyor."

                elif last_h[1][1] > last_h[0][1] and last_h[1][1] > last_h[2][1] and abs(last_h[0][1] - last_h[2][1]) / last_h[0][1] < 0.05 and current_price < last_l[-1][1] and is_vol_teyit:
                    if rsi < 50 and ema11 < ema21:
                        sinyal = True; formasyon_adi = "Omuz-Baş-Omuz"; yon = "SHORT"; emoji = "🔻"
                        hedef = last_l[-1][1] - (last_h[1][1] - last_l[-1][1]); stop = last_h[2][1] * 1.02
                        aciklama = "Klasik dönüş formasyonu. Boyun çizgisi kırıldı, 15m trend aşağı yönlü teyit veriyor."

                elif highs[-2] > max(highs[-20:-2]) and current_price < max(highs[-20:-2]) and is_vol_teyit:
                    if rsi < 50 and ema11 < ema21:
                        sinyal = True; formasyon_adi = "Likidite Avı (Sweep)"; yon = "SHORT"; emoji = "💀"
                        stop = highs[-2] * 1.01; hedef = current_price * 0.97
                        aciklama = "Fiyat eski tepenin üzerine fitil attı (Stop-loss avı) ancak hemen altına kapandı. Akıllı para satışa geçmiş olabilir."

                elif lows[-2] < min(lows[-20:-2]) and current_price > min(lows[-20:-2]) and is_vol_teyit:
                    if rsi > 50 and ema11 > ema21:
                        sinyal = True; formasyon_adi = "Likidite Avı (Sweep)"; yon = "LONG"; emoji = "🚀"
                        stop = lows[-2] * 0.99; hedef = current_price * 1.03
                        aciklama = "Fiyat eski dibin altına fitil attı (Stop-loss avı) ancak hemen üstüne kapandı. Akıllı para alıma geçmiş olabilir."

                if not sinyal:
                    last_20_high = max(highs[-20:])
                    last_20_low = min(lows[-20:])
                    range_pct = ((last_20_high - last_20_low) / last_20_low) * 100

                    if range_pct < 5 and current_price > last_20_high and is_vol_teyit and rsi > 50:
                        sinyal = True; formasyon_adi = "Sıkışma ve Yukarı Kırılım (Bayrak)"; yon = "LONG"; emoji = "🚀"
                        hedef = current_price + (last_20_high - last_20_low); stop = last_20_low * 0.98
                        aciklama = "Fiyat dar bir bantta sıkıştı ve hacimle yukarı kırıldı. Volatilite patlaması başlıyor."
                    elif range_pct < 5 and current_price < last_20_low and is_vol_teyit and rsi < 50:
                        sinyal = True; formasyon_adi = "Sıkışma ve Aşağı Kırılım (Ters Bayrak)"; yon = "SHORT"; emoji = "🔻"
                        hedef = current_price - (last_20_high - last_20_low); stop = last_20_high * 1.02
                        aciklama = "Fiyat dar bir bantta sıkıştı ve hacimle aşağı kırıldı. Volatilite patlaması başlıyor."

                if sinyal:
                    hedef_pct = ((hedef - current_price) / current_price) * 100
                    stop_pct = ((stop - current_price) / current_price) * 100

                    if yon == "SHORT" and hedef >= current_price: continue
                    if yon == "LONG" and hedef <= current_price: continue
                    if abs(stop_pct) > 10.0: continue
                    if abs(hedef_pct) < 1.0 or abs(hedef_pct) > 20.0: continue

                    msg = (f"{emoji} <b>TEYİTLİ DÖNÜŞ ({yon}): {symbol}</b>\n"
                           f"<i>5m Formasyon - 15m Trend Teyitli</i>\n\n"
                           f"💰 <b>Giriş:</b> {akilli_fiyat(current_price)}\n"
                           f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                           f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                           f"📊 <b>Formasyon:</b> {formasyon_adi}\n"
                           f"📊 <b>Teyitler:</b> Hacim ({current_vol/avg_vol:.1f}x), RSI ({rsi:.1f}), EMA (15m)\n\n"
                           f"💡 <b>Açıklama:</b> {aciklama}")
                    if tg(C, msg):
                        son_formasyon_sinyalleri[symbol] = time.time()
                        log("FORMASYON", f"🔥 SİNYAL: {symbol} ({yon} - {formasyon_adi})")
                time.sleep(0.1)
            time.sleep(FORMASYON_ARALIGI)
        except Exception as e:
            log("FORMASYON", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 7: EMA RIBBON
# =====================================================
def ema_ribbon_loop():
    log("EMA_RIBBON", "EMA Dizilim Motoru Başladı.")
    time.sleep(90)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in hacim_filtre(tickers, MIN_HACIM_EMA_RIBBON):
                if symbol in son_ema_ribbon_sinyalleri and time.time() - son_ema_ribbon_sinyalleri[symbol] < 86400: continue
                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1d&limit=200")
                if not isinstance(klines, list) or len(klines) < 200: continue
                closes = [float(k[4]) for k in klines[:-1]]
                vols = [float(k[7]) for k in klines[:-1]]
                curr = closes[-1]
                curr_vol = vols[-1]
                avg_vol = sum(vols[-21:-1]) / 20
                if avg_vol == 0 or curr_vol <= (avg_vol * 1.2): continue

                l7, l25, l99, l200 = calculate_ema(closes, 7), calculate_ema(closes, 25), calculate_ema(closes, 99), calculate_ema(closes, 200)
                if not (l7 and l25 and l99 and l200): continue
                e7, e25, e99, e200 = l7[-1], l25[-1], l99[-1], l200[-1]

                if curr > e7 > e25 > e99 > e200:
                    msg = (f"🚀 <b>EMA DİZİLİMİ (LONG): {symbol}</b>\n"
                           f"<i>1 Günlük Grafik - Çoklu EMA Dizilimi</i>\n\n"
                           f"💰 <b>Giriş:</b> {akilli_fiyat(curr)}\n"
                           f"🎯 <b>Hedef:</b> {akilli_fiyat(curr * 1.20)} (+20.0%)\n"
                           f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(e25 * 0.95)}\n\n"
                           f"📊 <b>EMA Dizilimi:</b>\n"
                           f"EMA7: {akilli_fiyat(e7)} | EMA25: {akilli_fiyat(e25)}\n"
                           f"EMA99: {akilli_fiyat(e99)} | EMA200: {akilli_fiyat(e200)}\n"
                           f"📈 <b>Hacim Teyidi:</b> Alındı ({curr_vol/avg_vol:.1f}x)\n\n"
                           f"💡 <b>Açıklama:</b> EMA Ribbon dizilimi, piyasanın tüm zaman dilimlerinde mükemmel bir uyum içinde olduğunu gösterir. Trendin en güçlü olduğu 'tatlı nokta'dır.")
                    if tg(C, msg): son_ema_ribbon_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(EMA_RIBBON_ARALIGI)
        except Exception as e:
            log("EMA_RIBBON", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 8: MACD
# =====================================================
def macd_loop():
    log("MACD", "MACD Motoru Başladı.")
    time.sleep(105)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in hacim_filtre(tickers, MIN_HACIM_MACD):
                if symbol in son_macd_sinyalleri and time.time() - son_macd_sinyalleri[symbol] < 14400: continue
                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1d&limit=100")
                if not isinstance(klines, list) or len(klines) < 80: continue
                closes = [float(k[4]) for k in klines[:-1]]
                vols = [float(k[7]) for k in klines[:-1]]
                curr_price = closes[-1]
                curr_vol = vols[-1]
                avg_vol = sum(vols[-21:-1]) / 20
                if avg_vol == 0 or curr_vol <= (avg_vol * 1.1): continue

                cm, cs, pm, ps = calculate_macd(closes)
                if cm is None: continue

                if pm <= ps and cm > cs:
                    msg = (f"🚀 <b>MACD SİNYALİ (LONG): {symbol}</b>\n"
                           f"<i>1 Günlük Grafik - MACD Momentum Kırılımı</i>\n\n"
                           f"💰 <b>Giriş:</b> {akilli_fiyat(curr_price)}\n"
                           f"🎯 <b>Hedef:</b> {akilli_fiyat(curr_price * 1.10)} (+10.0%)\n"
                           f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(curr_price * 0.97)} (-3.0%)\n\n"
                           f"📊 <b>MACD Çizgisi:</b> {cm:.5f} | <b>Sinyal Çizgisi:</b> {cs:.5f}\n"
                           f"📈 <b>Hacim Teyidi:</b> Alındı\n\n"
                           f"💡 <b>Açıklama:</b> MACD çizgisinin Sinyal çizgisini yukarı yönlü kesmesine 'Golden Cross' (Altın Kesişim) denir. Bu, satıcıların gücünü kaybettiğini ve yükseliş trendinin başladığını teyit eder.")
                    if tg(C, msg): son_macd_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(MACD_ARALIGI)
        except Exception as e:
            log("MACD", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 9: MIDAS SETUP (POC / VAH / VAL) - SIKILAŞTIRILMIŞ
# =====================================================
def midas_setup_loop():
    log("MIDAS_SETUP", "Midas Setup (POC/VAH/VAL) Motoru Başladı.")
    time.sleep(120)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in hacim_filtre(tickers, MIN_HACIM_MIDAS_SETUP):
                if symbol in son_midas_setup_sinyalleri and time.time() - son_midas_setup_sinyalleri[symbol] < 14400: continue

                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1h&limit=50")
                if not isinstance(klines, list) or len(klines) < 50: continue
                klines = klines[:-1]  # kapanmamış mumu at

                highs = [float(k[2]) for k in klines]
                lows = [float(k[3]) for k in klines]
                closes = [float(k[4]) for k in klines]
                vols = [float(k[7]) for k in klines]

                current_price = closes[-1]
                recent_highs = highs[-24:]
                recent_lows = lows[-24:]
                recent_vols = vols[-24:]

                vah = max(recent_highs)
                val = min(recent_lows)
                poc_idx = recent_vols.index(max(recent_vols))
                poc = closes[-24 + poc_idx]

                avg_vol = sum(vols[-21:-1]) / 20
                orta = (vah + val) / 2
                aralik_pct = ((vah - val) / val) * 100 if val > 0 else 0

                # SIKI KOŞUL: kapanış POC'un en az %0.3 altında, aralığın alt yarısında,
                # hacim ortalamanın üstünde ve aralık en az %2 geniş
                if (current_price < poc * 0.997 and current_price < orta
                        and vols[-1] > avg_vol and aralik_pct >= 2.0):

                    low_1h = min(lows[-1:])
                    low_4h = min(lows[-4:])

                    hedefler = sorted(list(set([low_1h, low_4h, val])), reverse=True)
                    hedefler = [h for h in hedefler if h < current_price]

                    if len(hedefler) >= 3:
                        tp1, tp2, tp3 = hedefler[0], hedefler[1], hedefler[2]
                    elif len(hedefler) == 2:
                        tp1, tp2, tp3 = hedefler[0], hedefler[1], hedefler[1] * 0.98
                    else:
                        continue

                    stop = vah * 1.02
                    tp1_pct = ((tp1 - current_price) / current_price) * 100
                    tp2_pct = ((tp2 - current_price) / current_price) * 100
                    tp3_pct = ((tp3 - current_price) / current_price) * 100
                    stop_pct = ((stop - current_price) / current_price) * 100
                    if stop_pct > 10.0: continue

                    coin = symbol.replace("USDT", "")
                    msg = (f"🔻 <b>SETUP SİNYALİ (SHORT): {symbol}</b>\n\n"
                           f"Fiyat: <b>{akilli_fiyat(current_price)}</b>\n"
                           f"Hedefler: <b>{akilli_fiyat(tp1)}</b> ({tp1_pct:+.1f}%) → <b>{akilli_fiyat(tp2)}</b> ({tp2_pct:+.1f}%) → <b>{akilli_fiyat(tp3)}</b> ({tp3_pct:+.1f}%)\n"
                           f"İptal: <b>{akilli_fiyat(stop)}</b> ({stop_pct:+.1f}%) üzerinde\n\n"
                           f"{coin}, VAH <b>{akilli_fiyat(vah)}</b> ile VAL <b>{akilli_fiyat(val)}</b> arasında havada asılı kaldı. Merkezde POC <b>{akilli_fiyat(poc)}</b> var ve şu an hakem konumunda. Fiyat <b>{akilli_fiyat(current_price)}</b> ile onun altında kapandı, yukarı ivme zayıf. 🧊\n\n"
                           f"Kilit ayrım: POC kaybı aşağıya ilk olarak 1s dip <b>{akilli_fiyat(low_1h)}</b> ve 4s dip <b>{akilli_fiyat(low_4h)}</b> yolunu açar. Zayıflık devam ederse VAL <b>{akilli_fiyat(val)}</b> hedefe girer. 🎯")
                    if tg(C, msg):
                        son_midas_setup_sinyalleri[symbol] = time.time()
                        log("MIDAS_SETUP", f"🔥 SİNYAL: {symbol} (SHORT)")
                time.sleep(0.1)
            time.sleep(MIDAS_SETUP_ARALIGI)
        except Exception as e:
            log("MIDAS_SETUP", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 10: TARİHİ ZİRVE / DİP KIRILIMI (ATH / ATL)
# =====================================================
def ath_atl_loop():
    log("ATH_ATL", "Tarihi Zirve/Dip Kırılım Motoru Başladı.")
    time.sleep(135)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in hacim_filtre(tickers, MIN_HACIM_ATH_ATL):
                if symbol in son_ath_atl_sinyalleri and time.time() - son_ath_atl_sinyalleri[symbol] < 86400: continue
                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1d&limit=500")
                if not isinstance(klines, list) or len(klines) < 200: continue
                highs = [float(k[2]) for k in klines[:-1]]
                lows = [float(k[3]) for k in klines[:-1]]
                closes = [float(k[4]) for k in klines[:-1]]
                vols = [float(k[7]) for k in klines[:-1]]
                current_price = closes[-1]
                prev_close = closes[-2]
                current_vol = vols[-1]
                # DÜZELTME: son mum hariç zirve/dip (aksi halde kırılım asla oluşmazdı)
                ath = max(highs[:-1])
                atl = min(lows[:-1])
                avg_vol = sum(vols[-21:-1]) / 20
                if avg_vol == 0 or current_vol <= (avg_vol * 1.2): continue

                sinyal = None; yon = ""; emoji = ""; stop = 0; hedef = 0; aciklama = ""

                if prev_close >= atl and current_price < atl:
                    sinyal = True; yon = "SHORT"; emoji = "💀"
                    stop = atl * 1.05; hedef = current_price * 0.80
                    aciklama = "Fiyat, geçmişteki en düşük seviyeyi (Tarihi Dibi) hacimle aşağı kırdı. Uzun vadeli bir çöküşün başlangıcı olabilir."
                elif prev_close <= ath and current_price > ath:
                    sinyal = True; yon = "LONG"; emoji = "🚀"
                    stop = ath * 0.95; hedef = current_price * 1.20
                    aciklama = "Fiyat, geçmişteki en yüksek seviyeyi (Tarihi Zirveyi) hacimle yukarı kırdı. Yeni bir boğa trendinin başlangıcı olabilir."

                if sinyal:
                    hedef_pct = ((hedef - current_price) / current_price) * 100
                    stop_pct = ((stop - current_price) / current_price) * 100
                    msg = (f"{emoji} <b>TARİHİ SEVİYE KIRILIMI ({yon}): {symbol}</b>\n"
                           f"<i>1 Günlük Grafik</i>\n\n"
                           f"💰 <b>Giriş:</b> {akilli_fiyat(current_price)}\n"
                           f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                           f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                           f"📊 <b>Kırılan Seviye:</b> {akilli_fiyat(ath if yon == 'LONG' else atl)}\n"
                           f"📈 <b>Hacim:</b> {current_vol/avg_vol:.1f}x\n\n"
                           f"💡 <b>Açıklama:</b> {aciklama}")
                    if tg(C, msg):
                        son_ath_atl_sinyalleri[symbol] = time.time()
                        log("ATH_ATL", f"🔥 SİNYAL: {symbol} ({yon})")
                time.sleep(0.1)
            time.sleep(ATH_ATL_ARALIGI)
        except Exception as e:
            log("ATH_ATL", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 11: ICHIMOKU BULUTU
# =====================================================
def ichimoku_loop():
    log("ICHIMOKU", "Zırhlı Ichimoku Bulut Motoru Başladı.")
    time.sleep(75)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in hacim_filtre(tickers, MIN_HACIM_ICHIMOKU):
                if symbol in son_ichimoku_sinyalleri and time.time() - son_ichimoku_sinyalleri[symbol] < 14400: continue

                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1h&limit=100")
                if not isinstance(klines, list) or len(klines) < 80: continue

                highs = [float(k[2]) for k in klines]
                lows = [float(k[3]) for k in klines]
                closes = [float(k[4]) for k in klines]
                vols = [float(k[7]) for k in klines]

                curr_close = closes[-2]
                prev_close = closes[-3]

                avg_vol = sum(vols[-22:-2]) / 20
                curr_vol = vols[-2]
                if curr_vol <= avg_vol: continue

                def calc_tenkan(idx): return (max(highs[idx-8:idx+1]) + min(lows[idx-8:idx+1])) / 2
                def calc_kijun(idx): return (max(highs[idx-25:idx+1]) + min(lows[idx-25:idx+1])) / 2

                idx_curr = len(closes) - 2

                tenkan_curr = calc_tenkan(idx_curr)
                kijun_curr = calc_kijun(idx_curr)

                span_a_curr = (calc_tenkan(idx_curr - 26) + calc_kijun(idx_curr - 26)) / 2
                span_a_prev = (calc_tenkan(idx_curr - 27) + calc_kijun(idx_curr - 27)) / 2

                span_b_curr = (max(highs[idx_curr-77:idx_curr-26]) + min(lows[idx_curr-77:idx_curr-26])) / 2
                span_b_prev = (max(highs[idx_curr-78:idx_curr-27]) + min(lows[idx_curr-78:idx_curr-27])) / 2

                cloud_top_curr = max(span_a_curr, span_b_curr)
                cloud_bottom_curr = min(span_a_curr, span_b_curr)
                cloud_thickness = cloud_top_curr - cloud_bottom_curr

                cloud_top_prev = max(span_a_prev, span_b_prev)
                cloud_bottom_prev = min(span_a_prev, span_b_prev)

                sinyal = None; yon = ""; emoji = ""; stop = 0; hedef = 0; aciklama = ""

                if prev_close <= cloud_top_prev and curr_close > cloud_top_curr and curr_close > kijun_curr and tenkan_curr > kijun_curr:
                    if cloud_thickness > (curr_close * 0.005):
                        sinyal = True; yon = "LONG (Bulut Üstü Kırılım)"; emoji = "🚀"
                        stop = min(kijun_curr, cloud_bottom_curr) * 0.99
                        hedef = curr_close + (cloud_thickness * 2)
                        aciklama = "Fiyat Ichimoku bulutunu yukarı yönlü kırdı. Tenkan-sen'in Kijun-sen'in üzerinde olması kısa vadeli momentumun güçlü olduğunu gösterir."

                elif prev_close >= cloud_bottom_prev and curr_close < cloud_bottom_curr and curr_close < kijun_curr and tenkan_curr < kijun_curr:
                    if cloud_thickness > (curr_close * 0.005):
                        sinyal = True; yon = "SHORT (Bulut Altı Kırılım)"; emoji = "🔻"
                        stop = max(kijun_curr, cloud_top_curr) * 1.01
                        hedef = curr_close - (cloud_thickness * 2)
                        aciklama = "Fiyat Ichimoku bulutunu aşağı yönlü kırdı. Tenkan-sen'in Kijun-sen'in altında olması satıcıların güçlü olduğunu gösterir."

                if sinyal:
                    hedef_pct = ((hedef - curr_close) / curr_close) * 100
                    stop_pct = ((stop - curr_close) / curr_close) * 100
                    if abs(hedef_pct) < 1.0: continue

                    msg = (f"{emoji} <b>ICHIMOKU SİNYALİ ({yon}): {symbol}</b>\n"
                           f"<i>1 Saatlik Grafik - Ichimoku Bulut Kırılımı</i>\n\n"
                           f"💰 <b>Kapanış Fiyatı:</b> {akilli_fiyat(curr_close)}\n"
                           f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                           f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                           f"☁️ <b>Bulut (Kumo) Bölgesi:</b> {akilli_fiyat(cloud_bottom_curr)} - {akilli_fiyat(cloud_top_curr)}\n"
                           f"📈 <b>Tenkan-sen:</b> {akilli_fiyat(tenkan_curr)} | <b>Kijun-sen:</b> {akilli_fiyat(kijun_curr)}\n"
                           f"📊 <b>Hacim Teyidi:</b> Alındı\n\n"
                           f"💡 <b>Açıklama:</b> {aciklama}")
                    if tg(C, msg):
                        son_ichimoku_sinyalleri[symbol] = time.time()
                        log("ICHIMOKU", f"🔥 SİNYAL: {symbol} ({yon})")
                time.sleep(0.1)
            time.sleep(ICHIMOKU_ARALIGI)
        except Exception as e:
            log("ICHIMOKU", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 12: GRAFİK FORMASYONLARI (+ FİNCAN & KULP)
# =====================================================
def chart_patterns_loop():
    log("PATTERN", "Formasyon Avcısı Başladı (İkili Tepe/Dip, OBO, Fincan & Kulp).")
    time.sleep(150)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue

            for symbol in hacim_filtre(tickers, MIN_HACIM_PATTERN):
                if symbol in son_chart_pattern_sinyalleri and time.time() - son_chart_pattern_sinyalleri[symbol] < 14400: continue

                for tf in ['4h', '1d', '3d']:
                    klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval={tf}&limit=100")
                    if not isinstance(klines, list) or len(klines) < 50: continue

                    highs = [float(k[2]) for k in klines]
                    lows = [float(k[3]) for k in klines]
                    closes = [float(k[4]) for k in klines]
                    vols = [float(k[7]) for k in klines]

                    curr_close = closes[-2]
                    curr_vol = vols[-2]
                    avg_vol = sum(vols[-22:-2]) / 20
                    if avg_vol == 0 or curr_vol < avg_vol * 1.2: continue

                    p_highs, p_lows = find_pivots(highs, lows)

                    sinyal = None; formasyon_adi = ""; yon = ""; emoji = ""; hedef = 0; stop = 0; neckline = 0

                    # 1. İKİLİ TEPE (M)
                    if len(p_highs) >= 2:
                        p1_idx, p1_price = p_highs[-2]
                        p2_idx, p2_price = p_highs[-1]
                        if 5 <= (p2_idx - p1_idx) <= 30 and abs(p1_price - p2_price) / p1_price <= 0.02:
                            neckline = min(lows[p1_idx:p2_idx+1])
                            if curr_close < neckline * 0.99:
                                sinyal = True; formasyon_adi = "İkili Tepe (M)"; yon = "SHORT"; emoji = "🔻"
                                stop = max(p1_price, p2_price) * 1.02
                                hedef = neckline - (max(p1_price, p2_price) - neckline)

                    # 2. İKİLİ DİP (W)
                    if not sinyal and len(p_lows) >= 2:
                        p1_idx, p1_price = p_lows[-2]
                        p2_idx, p2_price = p_lows[-1]
                        if 5 <= (p2_idx - p1_idx) <= 30 and abs(p1_price - p2_price) / p1_price <= 0.02:
                            neckline = max(highs[p1_idx:p2_idx+1])
                            if curr_close > neckline * 1.01:
                                sinyal = True; formasyon_adi = "İkili Dip (W)"; yon = "LONG"; emoji = "🚀"
                                stop = min(p1_price, p2_price) * 0.98
                                hedef = neckline + (neckline - min(p1_price, p2_price))

                    # 3. OMUZ-BAŞ-OMUZ
                    if not sinyal and len(p_highs) >= 3:
                        p1_idx, p1_price = p_highs[-3]
                        p2_idx, p2_price = p_highs[-2]
                        p3_idx, p3_price = p_highs[-1]
                        if p2_price > p1_price and p2_price > p3_price and abs(p1_price - p3_price) / p1_price <= 0.03:
                            neckline = min(lows[p1_idx:p3_idx+1])
                            if curr_close < neckline * 0.99:
                                sinyal = True; formasyon_adi = "Omuz-Baş-Omuz"; yon = "SHORT"; emoji = "💀"
                                stop = p3_price * 1.02
                                hedef = neckline - (p2_price - neckline)

                    # 4. TERS OBO
                    if not sinyal and len(p_lows) >= 3:
                        p1_idx, p1_price = p_lows[-3]
                        p2_idx, p2_price = p_lows[-2]
                        p3_idx, p3_price = p_lows[-1]
                        if p2_price < p1_price and p2_price < p3_price and abs(p1_price - p3_price) / p1_price <= 0.03:
                            neckline = max(highs[p1_idx:p3_idx+1])
                            if curr_close > neckline * 1.01:
                                sinyal = True; formasyon_adi = "Ters Omuz-Baş-Omuz"; yon = "LONG"; emoji = "🚀"
                                stop = p3_price * 0.98
                                hedef = neckline + (neckline - p2_price)

                    # 5. FİNCAN VE KULP
                    if not sinyal and len(p_highs) >= 2 and len(p_lows) >= 1:
                        left_rim_idx, left_rim = p_highs[-2]
                        cup_low_idx, cup_low = p_lows[-1]
                        right_rim_idx, right_rim = p_highs[-1]
                        if left_rim_idx < cup_low_idx < right_rim_idx and abs(left_rim - right_rim) / left_rim <= 0.05:
                            cup_depth = (left_rim - cup_low) / left_rim
                            if cup_depth > 0.05 and curr_close > right_rim * 1.01:
                                sinyal = True; formasyon_adi = "Fincan ve Kulp (Cup & Handle)"; yon = "LONG"; emoji = "☕"
                                neckline = right_rim
                                hedef = right_rim + (right_rim - cup_low)
                                stop = cup_low * 1.02

                    # 6. TERS FİNCAN VE KULP
                    if not sinyal and len(p_lows) >= 2 and len(p_highs) >= 1:
                        left_rim_idx, left_rim = p_lows[-2]
                        cup_high_idx, cup_high = p_highs[-1]
                        right_rim_idx, right_rim = p_lows[-1]
                        if left_rim_idx < cup_high_idx < right_rim_idx and abs(left_rim - right_rim) / left_rim <= 0.05:
                            cup_depth = (cup_high - left_rim) / left_rim
                            if cup_depth > 0.05 and curr_close < right_rim * 0.99:
                                sinyal = True; formasyon_adi = "Ters Fincan ve Kulp"; yon = "SHORT"; emoji = "☕"
                                neckline = right_rim
                                hedef = right_rim - (cup_high - right_rim)
                                stop = cup_high * 0.98

                    if sinyal:
                        hedef_pct = ((hedef - curr_close) / curr_close) * 100
                        stop_pct = ((stop - curr_close) / curr_close) * 100

                        if yon == "SHORT" and hedef >= curr_close: continue
                        if yon == "LONG" and hedef <= curr_close: continue
                        if abs(stop_pct) > 15.0: continue
                        if abs(hedef_pct) < 1.5 or abs(hedef_pct) > 40.0: continue
                        if yon == "SHORT" and curr_close < hedef * 1.01: continue
                        if yon == "LONG" and curr_close > hedef * 0.99: continue

                        aciklama = "Fiyat, klasik bir dönüş formasyonunu tamamlayarak boyun çizgisini (Neckline) hacimle kırdı."
                        if "Omuz" in formasyon_adi: aciklama = "Trendin tepe/dip noktasında oluşan klasik dönüş formasyonu. Boyun çizgisi kırıldı, kontrol yön değiştirdi."
                        if "Dip" in formasyon_adi: aciklama = "Dipte oluşan çift dip formasyonu. Satıcılar tükendi, alıcılar hacimle boyun çizgisini yukarı kırdı."
                        if "Fincan" in formasyon_adi: aciklama = "Uzun süreli bir birikim (fincan) ve ardından gelen kulp (düzeltme) sonrası direnç/destek kırıldı. Güçlü bir devam trendi başlıyor olabilir."

                        msg = (f"{emoji} <b>GRAFİK FORMASYONU ({yon}): {symbol}</b>\n"
                               f"<i>{tf.upper()} Grafik - {formasyon_adi}</i>\n\n"
                               f"💰 <b>Giriş:</b> {akilli_fiyat(curr_close)}\n"
                               f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                               f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                               f"📊 <b>Boyun Çizgisi:</b> {akilli_fiyat(neckline)}\n"
                               f"📈 <b>Hacim Teyidi:</b> Alındı ({curr_vol/avg_vol:.1f}x)\n\n"
                               f"💡 <b>Açıklama:</b> {aciklama}")
                        if tg(C, msg):
                            son_chart_pattern_sinyalleri[symbol] = time.time()
                            log("PATTERN", f"🔥 SİNYAL: {symbol} ({yon} - {formasyon_adi})")
                            break
                    time.sleep(0.1)
            time.sleep(CHART_PATTERN_ARALIGI)
        except Exception as e:
            log("PATTERN", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 13: TOP GAINERS SHORT AVCISI
# =====================================================
def top_gainers_short_loop():
    log("SHORT_GAINERS", "Top Gainers Short Avcısı Başladı (Kâr Realizasyonu).")
    time.sleep(180)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue

            temiz = [t for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in YASAKLI_HISSELER]
            temiz.sort(key=lambda x: float(x['priceChangePercent']), reverse=True)
            top_gainers = temiz[:5]

            for t in top_gainers:
                symbol = t['symbol']
                change_pct = float(t['priceChangePercent'])
                if change_pct < 15: continue
                if symbol in son_short_gainers_sinyalleri and time.time() - son_short_gainers_sinyalleri[symbol] < 43200: continue

                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=15m&limit=50")
                if not isinstance(klines, list) or len(klines) < 50: continue

                closes = [float(k[4]) for k in klines[:-1]]
                highs = [float(k[2]) for k in klines[:-1]]
                vols = [float(k[7]) for k in klines[:-1]]

                curr_close = closes[-1]
                curr_vol = vols[-1]
                avg_vol = sum(vols[-21:-1]) / 20
                if curr_vol < avg_vol: continue

                ema9 = calculate_ema(closes, 9)[-1]
                ema21 = calculate_ema(closes, 21)[-1]
                rsi = calculate_rsi(closes)

                recent_high = max(highs[-20:])
                drop_from_top = ((recent_high - curr_close) / recent_high) * 100

                if rsi > 55 and ema9 < ema21 and curr_close < closes[-2] and drop_from_top > 2.0:
                    sl = recent_high * 1.01
                    tp1 = curr_close * 0.95
                    tp2 = curr_close * 0.90

                    msg = (f"🩸 <b>TOP GAINERS SHORT FIRSATI: {symbol}</b>\n"
                           f"<i>15m Grafik - Kâr Realizasyonu</i>\n\n"
                           f"⚠️ <i>Coin 24 saatte <b>+{change_pct:.1f}%</b> yükselmiş ve aşırı alım (Overbought) bölgesinde. Balina çıkışı / Short fırsatı başlıyor olabilir!</i>\n\n"
                           f"💰 <b>Giriş (Short):</b> {akilli_fiyat(curr_close)}\n"
                           f"🛑 <b>Stop-Loss (İptal):</b> {akilli_fiyat(sl)}\n"
                           f"🎯 <b>Hedef 1:</b> {akilli_fiyat(tp1)} (-5.0%)\n"
                           f"🎯 <b>Hedef 2:</b> {akilli_fiyat(tp2)} (-10.0%)\n\n"
                           f"📊 <b>RSI:</b> {rsi:.1f} (Aşırı Alımdan Dönüş)\n"
                           f"📉 <b>EMA:</b> 9 Periyot, 21'in altına sarktı (Ölüm Kesişimi)\n"
                           f"🔊 <b>Hacim:</b> Satış hacmi ortalamanın üzerinde\n\n"
                           f"💡 <b>Strateji:</b> En çok yükselenler listesine giren coinlerde, trendin kırıldığı ve kâr satışlarının başladığı noktada kısa pozisyon (Short) alınır.")
                    if tg(C, msg):
                        son_short_gainers_sinyalleri[symbol] = time.time()
                        log("SHORT_GAINERS", f"🔥 SİNYAL: {symbol} (SHORT)")
                time.sleep(0.1)
            time.sleep(SHORT_GAINERS_ARALIGI)
        except Exception as e:
            log("SHORT_GAINERS", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# RENDER WEB SUNUCUSU
# =====================================================
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Ultimate Futures Bot is running!")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, format_string, *args): return

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    log("SİSTEM", f"WEB SUNUCUSU BAŞLADI - PORT: {port}")
    server.serve_forever()

# =====================================================
# BAŞLAT
# =====================================================
if __name__ == "__main__":
    motorlar = [
        top_gainers_loop, pump_winner_loop, chartist_loop, pump_loop,
        volatilite_loop, formasyon_loop, ema_ribbon_loop, macd_loop,
        midas_setup_loop, ath_atl_loop, ichimoku_loop,
        chart_patterns_loop, top_gainers_short_loop,
    ]
    for m in motorlar:
        threading.Thread(target=m, daemon=True).start()

    ok, binance_durum = baglanti_testi("fapi")
    if ok:
        bilgi = "İlk rapor birkaç saniye içinde gelecek."
    else:
        ok2, spot_durum = baglanti_testi("spot")
        binance_durum += "\n" + spot_durum
        bilgi = ("Futures banlı, bot Spot verisiyle çalışıyor. Ban bitince otomatik Futures'a döner."
                 if ok2 else "Binance verisi alınamıyor, sinyal gelmez.")
    if tg(C, f"🤖 <b>Bot başladı</b> ({simdi().strftime('%d.%m %H:%M')})\n✅ Telegram: bağlı\n{binance_durum}\n\n{bilgi}"):
        log("SİSTEM", "Telegram bağlantısı OK.")
    else:
        log("SİSTEM", "⚠️ Telegram mesajı gönderilemedi! BOT_TOKEN / CHAT_ID kontrol et.")

    run_web_server()

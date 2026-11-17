import urllib.request
import urllib.parse
import json
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

CHARTIST_ARALIGI = 3600
PUMP_ARALIGI = 60
VOLATILITE_ARALIGI = 60
FORMASYON_ARALIGI = 3600
EMA_RIBBON_ARALIGI = 3600
MACD_ARALIGI = 3600
ATH_ATL_ARALIGI = 3600
ICHIMOKU_ARALIGI = 3600
CHART_PATTERN_ARALIGI = 1800
SHORT_GAINERS_ARALIGI = 300
TOP_MOVERS_ARALIGI = 3600
DOMINANS_ARALIGI = 14400
TOP_GAINERS_ARALIGI = 14400
PUMP_WINNER_ARALIGI = 21600
SMA200_ARALIGI = 3600

MIN_HACIM_CHARTIST = 10_000_000
MIN_HACIM_PUMP = 5_000_000
MIN_HACIM_VOLATILITE = 10_000_000
MIN_HACIM_FORMASYON = 15_000_000
MIN_HACIM_EMA_RIBBON = 10_000_000
MIN_HACIM_MACD = 10_000_000
MIN_HACIM_ATH_ATL = 5_000_000
MIN_HACIM_ICHIMOKU = 10_000_000
MIN_HACIM_PATTERN = 15_000_000
MIN_HACIM_MOVERS = 5_000_000

YASAKLI_HISSELER = [
    "AAPLUSDT", "TSLAUSDT", "NVDAUSDT", "COINUSDT", "MSTRUSDT",
    "SQQQUSDT", "TQQQUSDT", "SPYUSDT", "QQQUSDT", "AMZNUSDT",
    "GOOGLUSDT", "METAUSDT", "NFLXUSDT", "BABAUSDT", "MSFTUSDT",
    "HOODUSDT", "CRCLUSDT", "XAUUSDT", "XAGUSDT", "PAXGUSDT"
]

son_sinyaller = {}
son_pump_sinyalleri = {}
son_volatilite_sinyalleri = {}
son_formasyon_sinyalleri = {}
son_ema_ribbon_sinyalleri = {}
son_macd_sinyalleri = {}
son_ath_atl_sinyalleri = {}
son_ichimoku_sinyalleri = {}
son_chart_pattern_sinyalleri = {}
son_short_gainers_sinyalleri = {}
son_movers_sinyalleri = {}
son_dominans_sinyalleri = {}
son_sma200_sinyalleri = {}

# =====================================================
# YARDIMCI FONKSÄ°YONLAR
# =====================================================

def simdi():
    return datetime.now(TR_Saati)


def log(prefix, m):
    print(f"[{simdi().strftime('%H:%M:%S')}] [{prefix}] {m}", flush=True)


def akilli_fiyat(p):
    if p is None:
        return "0"
    p = float(p)
    if p >= 1000:
        return f"{p:.2f}"
    if p >= 1:
        return f"{p:.4f}"
    if p >= 0.01:
        return f"{p:.6f}"
    return f"{p:.8f}"


def tg(chat_id, message):
    if not T or not chat_id:
        return False
    try:
        data = urllib.parse.urlencode({
            "chat_id": str(chat_id),
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true"
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{T}/sendMessage", data=data
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8")).get("ok", False)
    except Exception as e:
        log("TELEGRAM", f"GÃ¶nderim hatasÄ±: {e}")
        return False


def gj(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        # Binance/API geÃ§ici hatalarÄ±nda ilgili motor bir sonraki dÃ¶ngÃ¼de tekrar dener.
        log("API", f"Ä°stek baÅarÄ±sÄ±z: {e}")
        return None


def calculate_ema(prices, period):
    if len(prices) < period:
        return []
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for price in prices[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema


def calculate_sma(prices, period):
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 50 if avg_gain == 0 else 100
    if avg_gain == 0:
        return 0
    return 100 - (100 / (1 + (avg_gain / avg_loss)))


def calculate_macd(prices, fast=12, slow=26, signal=9):
    """MACD serilerini aynÄ± mum indekslerine hizalayarak hesaplar."""
    if len(prices) < slow + signal + 1:
        return None, None, None, None

    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)
    if not ema_fast or not ema_slow:
        return None, None, None, None

    # EMA dizileri farklÄ± baÅlangÄ±Ã§ indekslerine sahip olduÄundan
    # slow EMA'nÄ±n baÅlangÄ±Ã§ noktasÄ±na gÃ¶re hÄ±zlÄ± EMA hizalanÄ±r.
    fast_offset = slow - fast
    macd_line = [
        ema_fast[fast_offset + i] - ema_slow[i]
        for i in range(len(ema_slow))
    ]

    signal_line = calculate_ema(macd_line, signal)
    if len(signal_line) < 2:
        return None, None, None, None

    signal_offset = signal - 1
    current_macd = macd_line[-1]
    previous_macd = macd_line[-2]
    current_signal = signal_line[-1]
    previous_signal = signal_line[-2]
    return current_macd, current_signal, previous_macd, previous_signal


def trap_radar_check(symbol):
    try:
        oi_data = gj(
            f"{FAPI}/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=2"
        )
        oi_artiyor = False
        if isinstance(oi_data, list) and len(oi_data) >= 2:
            oi_old = float(oi_data[0]["sumOpenInterest"])
            oi_new = float(oi_data[1]["sumOpenInterest"])
            if oi_old > 0 and ((oi_new - oi_old) / oi_old) * 100 > 0.2:
                oi_artiyor = True

        taker_data = gj(
            f"{FAPI}/futures/data/takerlongshortRatio?symbol={symbol}&period=5m&limit=1"
        )
        alici_baskin = False
        if isinstance(taker_data, list) and taker_data:
            buy_vol = float(taker_data[0]["buyVol"])
            sell_vol = float(taker_data[0]["sellVol"])
            total = buy_vol + sell_vol
            if total > 0 and (buy_vol / total) * 100 > 55:
                alici_baskin = True

        if oi_artiyor and alici_baskin:
            return "ð <b>BALÄ°NA TEYÄ°DÄ° (GerÃ§ek Pump)</b>"
        if not alici_baskin:
            return "â ï¸ <b>DÄ°KKAT: SAHTE PUMP (TUZAK OLABÄ°LÄ°R)</b>"
        return "âï¸ <b>KÄ±smi Teyit</b>"
    except Exception:
        return ""


def find_pivots(highs, lows, window=3):
    pivot_highs = []
    pivot_lows = []
    for i in range(window, len(highs) - window):
        if highs[i] == max(highs[i-window:i+window+1]):
            pivot_highs.append((i, highs[i]))
        if lows[i] == min(lows[i-window:i+window+1]):
            pivot_lows.append((i, lows[i]))
    return pivot_highs, pivot_lows


def uygun_symbol(t):
    symbol = t.get("symbol", "")
    return (
        symbol.endswith("USDT")
        and symbol not in YASAKLI_HISSELER
        and not any(x in symbol for x in ("3L", "3S", "5L", "5S"))
    )


# =====================================================
# MOTOR 1: TOP GAINERS
# =====================================================

def top_gainers_loop():
    log("TOP_GAINERS", "GÃ¼nlÃ¼k En Ä°yi Hareketler Rapor Botu BaÅladÄ±.")
    time.sleep(10)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(300)
                continue

            temiz = [
                t for t in tickers
                if uygun_symbol(t) and float(t.get("quoteVolume", 0)) > 5_000_000
            ]
            temiz.sort(key=lambda x: float(x["priceChangePercent"]), reverse=True)
            top_5 = temiz[:5]

            if top_5:
                msg = "ð¥ <b>GÃ¼nÃ¼n En Ä°yi Hareketleri (24 Saat)</b>\n"
                msg += "<i>Maksimum yÃ¼kseliÅ yÃ¶nÃ¼, hacim teyitli</i>\n\n"
                medals = ["ð¥", "ð¥", "ð¥", "ð", "â­ï¸"]
                for i, t in enumerate(top_5):
                    symbol = t["symbol"].replace("USDT", "")
                    change = float(t["priceChangePercent"])
                    price = float(t["lastPrice"])
                    change_str = f"+{change:.0f}%" if change > 0 else f"{change:.0f}%"
                    msg += f"{medals[i]} <b>{symbol}</b> {change_str} | Fiyat: {akilli_fiyat(price)}\n"
                msg += "\nð <i>En Ã§ok kazandÄ±ran coinler listelendi.</i>"
                tg(C, msg)

            time.sleep(TOP_GAINERS_ARALIGI)
        except Exception as e:
            log("TOP_GAINERS", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 2: PUMP WINNER
# =====================================================

def pump_winner_loop():
    log("PUMP_WINNER", "GÃ¼nÃ¼n KazananÄ± (Pump DedektÃ¶r) Rapor Botu BaÅladÄ±.")
    time.sleep(20)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(300)
                continue

            temiz = [
                t for t in tickers
                if uygun_symbol(t) and float(t.get("quoteVolume", 0)) > 10_000_000
            ]
            temiz.sort(key=lambda x: float(x["priceChangePercent"]), reverse=True)

            if not temiz:
                time.sleep(300)
                continue

            winner = temiz[0]
            symbol = winner["symbol"]
            change_pct = float(winner["priceChangePercent"])
            if change_pct < 15:
                time.sleep(PUMP_WINNER_ARALIGI)
                continue

            klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1d&limit=1")
            if not isinstance(klines, list) or not klines:
                time.sleep(PUMP_WINNER_ARALIGI)
                continue

            day_low = float(klines[0][3])
            day_high = float(klines[0][2])
            max_move = ((day_high - day_low) / day_low) * 100 if day_low else 0

            msg = (
                f"ð <b>GÃNÃN KAZANANI: {symbol}</b>\n\n"
                f"ð¥ 24 saat iÃ§inde en Ã§ok yÃ¼kselen coin!\n\n"
                f"ð <b>Dip Fiyat:</b> {akilli_fiyat(day_low)}\n"
                f"ð <b>Zirve Fiyat:</b> {akilli_fiyat(day_high)}\n"
                f"ð <b>Maksimum YÃ¼kseliÅ:</b> <b>+{max_move:.1f}%</b>\n\n"
                f"ð¡ <b>DeÄerlendirme:</b> {symbol} gÃ¼n iÃ§erisinde gÃ¼Ã§lÃ¼ bir hacim ve fiyat hareketi yaÅadÄ±."
            )
            tg(C, msg)
            time.sleep(PUMP_WINNER_ARALIGI)
        except Exception as e:
            log("PUMP_WINNER", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 3: CHARTIST (EMA200)
# =====================================================

def chartist_loop():
    log("CHARTIST", "ZÄ±rhlÄ± EMA200 Motoru BaÅladÄ±.")
    time.sleep(30)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(60)
                continue

            symbols = [
                t["symbol"] for t in tickers
                if uygun_symbol(t) and float(t.get("quoteVolume", 0)) >= MIN_HACIM_CHARTIST
            ]
            for symbol in symbols:
                for tf in ["8h", "12h", "1d", "3d"]:
                    cooldown_key = f"CH_{tf}_{symbol}"
                    if cooldown_key in son_sinyaller and time.time() - son_sinyaller[cooldown_key] < 86400:
                        continue

                    klines = gj(
                        f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=500"
                    )
                    if not isinstance(klines, list) or len(klines) < 205:
                        continue

                    closed = klines[:-1]
                    closes = [float(k[4]) for k in closed]
                    curr, prev = closes[-1], closes[-2]
                    ema200 = calculate_ema(closes, 200)

                    if len(ema200) >= 2 and prev <= ema200[-2] and curr > ema200[-1] and curr > ema200[-1] * 1.01:
                        vol = [float(k[7]) for k in closed]
                        if vol[-1] > sum(vol[-21:-1]) / 20:
                            sl = ema200[-1] * 0.97
                            hedef = curr * 1.15
                            sl_pct = ((sl - curr) / curr) * 100
                            hedef_pct = ((hedef - curr) / curr) * 100

                            if abs(sl_pct) > 12.0 or abs(sl_pct) > abs(hedef_pct):
                                continue

                            msg = (
                                f"ð <b>UZUN VADELÄ° SPOT (LONG): {symbol}</b>\n"
                                f"<i>{tf.upper()} EMA200 KÄ±rÄ±lÄ±mÄ±</i>\n\n"
                                f"ð° <b>GiriÅ:</b> {akilli_fiyat(curr)}\n"
                                f"ð <b>SL:</b> {akilli_fiyat(sl)} (<b>{sl_pct:+.1f}%</b>)\n"
                                f"ð¯ <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n\n"
                                f"ð <b>Teyit:</b> Hacim OnaylÄ± ve %1 Ã¼zeri kapanÄ±Å.\n\n"
                                f"ð¡ <b>AÃ§Ä±klama:</b> FiyatÄ±n EMA200 Ã¼zerine hacimle kapanmasÄ± yukarÄ± yÃ¶nlÃ¼ trend deÄiÅimine iÅaret edebilir."
                            )
                            if tg(C, msg):
                                son_sinyaller[cooldown_key] = time.time()
                    time.sleep(0.05)
            time.sleep(CHARTIST_ARALIGI)
        except Exception as e:
            log("CHARTIST", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 4: PUMP AVCISI
# =====================================================

def pump_loop():
    log("PUMP", "AnlÄ±k Pump AvcÄ±sÄ± BaÅladÄ±.")
    time.sleep(45)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(60)
                continue

            symbols = [
                t["symbol"] for t in tickers
                if uygun_symbol(t) and float(t.get("quoteVolume", 0)) >= MIN_HACIM_PUMP
            ]
            for symbol in symbols:
                if symbol in son_pump_sinyalleri and time.time() - son_pump_sinyalleri[symbol] < 900:
                    continue

                klines = gj(
                    f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=5m&limit=3"
                )
                if not isinstance(klines, list) or len(klines) < 3:
                    continue

                curr_k, prev_k = klines[-2], klines[-3]
                cc, pc = float(curr_k[4]), float(prev_k[4])
                cv, pv = float(curr_k[7]), float(prev_k[7])
                if pc == 0 or pv == 0:
                    continue

                price_change = ((cc - pc) / pc) * 100
                vol_change = ((cv - pv) / pv) * 100
                vol_diff = cv - pv

                if price_change >= 3.0 and vol_change >= 100.0 and vol_diff >= 50_000:
                    trap_radar = trap_radar_check(symbol)
                    msg = (
                        f"ð <b>Pump - {symbol}</b>\n"
                        f"ð° <b>Fiyat:</b> {akilli_fiyat(cc)} (<b>{price_change:+.2f}%</b>)\n"
                        f"ð <b>Hacim ArtÄ±ÅÄ±:</b> {vol_change:+.0f}%\n"
                        f"Volume increased by {vol_diff:.0f} â¬ï¸\n\n"
                        f"{trap_radar}\n\n"
                        f"ð¡ <b>AÃ§Ä±klama:</b> 5 dakikalÄ±k grafikte hacim ve fiyat artÄ±ÅÄ± tespit edildi."
                    )
                    if tg(C, msg):
                        son_pump_sinyalleri[symbol] = time.time()
                time.sleep(0.05)
            time.sleep(PUMP_ARALIGI)
        except Exception as e:
            log("PUMP", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 5: ANORMAL VOLATÄ°LÄ°TE VE DUMP AVCISI
# =====================================================

def volatilite_loop():
    log("VOLATILITE", "Anormal Volatilite ve Dump AvcÄ±sÄ± BaÅladÄ± (15m).")
    time.sleep(55)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(60)
                continue

            symbols = [
                t["symbol"] for t in tickers
                if uygun_symbol(t) and float(t.get("quoteVolume", 0)) >= MIN_HACIM_VOLATILITE
            ]
            for symbol in symbols:
                if symbol in son_volatilite_sinyalleri and time.time() - son_volatilite_sinyalleri[symbol] < 1800:
                    continue

                klines = gj(
                    f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=15m&limit=25"
                )
                if not isinstance(klines, list) or len(klines) < 25:
                    continue

                opens = [float(k[1]) for k in klines]
                closes = [float(k[4]) for k in klines]
                vols = [float(k[7]) for k in klines]
                curr_open, curr_close, curr_vol = opens[-2], closes[-2], vols[-2]
                avg_vol = sum(vols[-22:-2]) / 20
                if curr_open == 0 or avg_vol == 0:
                    continue

                price_change_pct = ((curr_close - curr_open) / curr_open) * 100
                vol_multiplier = curr_vol / avg_vol

                if abs(price_change_pct) >= 2.5 and vol_multiplier >= 3.0:
                    yon = "DUMP (ANI DÃÅÃÅ) ð»" if price_change_pct < 0 else "PUMP (ANI YÃKSELÄ°Å) ð"
                    emoji = "ð¨" if price_change_pct < 0 else "â¡"
                    msg = (
                        f"{emoji} <b>ANORMAL VOLATÄ°LÄ°TE UYARISI</b> {emoji}\n"
                        f"<b>Coin:</b> {symbol} (15m Grafik)\n\n"
                        f"ð <b>Hareket:</b> {yon}\n"
                        f"ð <b>DeÄiÅim:</b> <b>{price_change_pct:+.2f}%</b>\n"
                        f"ð <b>Hacim PatlamasÄ±:</b> OrtalamanÄ±n <b>{vol_multiplier:.1f}x</b> katÄ±\n"
                        f"ð° <b>KapanÄ±Å FiyatÄ±:</b> {akilli_fiyat(curr_close)}\n\n"
                        f"â ï¸ <i>Piyasada ani likidite deÄiÅimi veya bÃ¼yÃ¼k emir gerÃ§ekleÅmiÅ olabilir.</i>"
                    )
                    if tg(C, msg):
                        son_volatilite_sinyalleri[symbol] = time.time()
                        log("VOLATILITE", f"ð¥ SÄ°NYAL: {symbol} ({yon})")
                time.sleep(0.1)
            time.sleep(VOLATILITE_ARALIGI)
        except Exception as e:
            log("VOLATILITE", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 6: FORMASYON (MSS)
# =====================================================

def formasyon_loop():
    log("FORMASYON", "Teyitli Formasyon ve Likidite AvcÄ±sÄ± BaÅladÄ±.")
    time.sleep(60)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(60)
                continue

            symbols = [
                t["symbol"] for t in tickers
                if uygun_symbol(t) and float(t.get("quoteVolume", 0)) >= MIN_HACIM_FORMASYON
            ]
            for symbol in symbols:
                if symbol in son_formasyon_sinyalleri and time.time() - son_formasyon_sinyalleri[symbol] < 86400:
                    continue

                k5 = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=5m&limit=101")
                k15 = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=15m&limit=51")
                if not isinstance(k5, list) or not isinstance(k15, list):
                    continue
                if len(k5) < 101 or len(k15) < 51:
                    continue

                k5 = k5[:-1]
                k15 = k15[:-1]
                highs = [float(k[2]) for k in k5]
                lows = [float(k[3]) for k in k5]
                closes = [float(k[4]) for k in k5]
                vols = [float(k[7]) for k in k5]
                closes_15m = [float(k[4]) for k in k15]

                ema11 = calculate_ema(closes_15m, 11)[-1]
                ema21 = calculate_ema(closes_15m, 21)[-1]
                rsi = calculate_rsi(closes_15m)
                current_price = closes[-1]
                avg_vol = sum(vols[-21:-1]) / 20
                current_vol = vols[-1]
                is_vol_teyit = current_vol > avg_vol * 1.5

                p_highs, p_lows = find_pivots(highs, lows)
                if len(p_highs) < 3 or len(p_lows) < 3:
                    continue

                last_h = p_highs[-3:]
                last_l = p_lows[-3:]
                sinyal = False
                formasyon_adi = yon = emoji = aciklama = ""
                hedef = stop = 0

                if abs(last_h[0][1] - last_h[2][1]) / last_h[0][1] < 0.03 and current_price < last_l[-1][1] and is_vol_teyit:
                    if rsi < 50 and ema11 < ema21:
                        sinyal = True
                        formasyon_adi, yon, emoji = "Ãift Tepe (M)", "SHORT", "ð»"
                        hedef = last_l[-1][1] - (last_h[1][1] - last_l[-1][1])
                        stop = last_h[2][1] * 1.02
                        aciklama = "Ä°kinci tepe ilkini geÃ§emedi ve boyun Ã§izgisi aÅaÄÄ± kÄ±rÄ±ldÄ±."

                elif abs(last_l[0][1] - last_l[2][1]) / last_l[0][1] < 0.03 and current_price > last_h[-1][1] and is_vol_teyit:
                    if rsi > 50 and ema11 > ema21:
                        sinyal = True
                        formasyon_adi, yon, emoji = "Ãift Dip (W)", "LONG", "ð"
                        hedef = last_h[-1][1] + (last_h[-1][1] - last_l[1][1])
                        stop = last_l[2][1] * 0.98
                        aciklama = "Ä°kinci dip sonrasÄ± boyun Ã§izgisi yukarÄ± kÄ±rÄ±ldÄ±."

                elif last_h[1][1] > last_h[0][1] and last_h[1][1] > last_h[2][1] and abs(last_h[0][1] - last_h[2][1]) / last_h[0][1] < 0.05 and current_price < last_l[-1][1] and is_vol_teyit:
                    if rsi < 50 and ema11 < ema21:
                        sinyal = True
                        formasyon_adi, yon, emoji = "Omuz-BaÅ-Omuz", "SHORT", "ð»"
                        hedef = last_l[-1][1] - (last_h[1][1] - last_l[-1][1])
                        stop = last_h[2][1] * 1.02
                        aciklama = "Omuz-baÅ-omuz formasyonunun boyun Ã§izgisi aÅaÄÄ± kÄ±rÄ±ldÄ±."

                # Likidite sÃ¼pÃ¼rme: son kapanmÄ±Å mum, kendisinden Ã¶nceki 20 mumla kÄ±yaslanÄ±r.
                prior_high = max(highs[-21:-1])
                prior_low = min(lows[-21:-1])
                if not sinyal and highs[-1] > prior_high and current_price < prior_high and is_vol_teyit:
                    if rsi < 50 and ema11 < ema21:
                        sinyal = True
                        formasyon_adi, yon, emoji = "Likidite AvÄ± (Sweep)", "SHORT", "ð"
                        stop, hedef = highs[-1] * 1.01, current_price * 0.97
                        aciklama = "Fiyat Ã¶nceki tepenin Ã¼zerine fitil attÄ± ancak altÄ±nda kapandÄ±."

                elif not sinyal and lows[-1] < prior_low and current_price > prior_low and is_vol_teyit:
                    if rsi > 50 and ema11 > ema21:
                        sinyal = True
                        formasyon_adi, yon, emoji = "Likidite AvÄ± (Sweep)", "LONG", "ð"
                        stop, hedef = lows[-1] * 0.99, current_price * 1.03
                        aciklama = "Fiyat Ã¶nceki dibin altÄ±na fitil attÄ± ancak Ã¼zerinde kapandÄ±."

                if not sinyal:
                    last_20_high = prior_high
                    last_20_low = prior_low
                    range_pct = ((last_20_high - last_20_low) / last_20_low) * 100 if last_20_low else 0
                    if range_pct < 5 and current_price > last_20_high and is_vol_teyit and rsi > 50:
                        sinyal = True
                        formasyon_adi, yon, emoji = "SÄ±kÄ±Åma ve YukarÄ± KÄ±rÄ±lÄ±m (Bayrak)", "LONG", "ð"
                        hedef = current_price + (last_20_high - last_20_low)
                        stop = last_20_low * 0.98
                        aciklama = "Fiyat dar banttan hacimle yukarÄ± kÄ±rÄ±ldÄ±."
                    elif range_pct < 5 and current_price < last_20_low and is_vol_teyit and rsi < 50:
                        sinyal = True
                        formasyon_adi, yon, emoji = "SÄ±kÄ±Åma ve AÅaÄÄ± KÄ±rÄ±lÄ±m (Ters Bayrak)", "SHORT", "ð»"
                        hedef = current_price - (last_20_high - last_20_low)
                        stop = last_20_high * 1.02
                        aciklama = "Fiyat dar banttan hacimle aÅaÄÄ± kÄ±rÄ±ldÄ±."

                if sinyal and current_price > 0:
                    hedef_pct = ((hedef - current_price) / current_price) * 100
                    stop_pct = ((stop - current_price) / current_price) * 100
                    if yon == "SHORT" and hedef >= current_price:
                        continue
                    if yon == "LONG" and hedef <= current_price:
                        continue
                    if abs(stop_pct) > 10 or abs(hedef_pct) < 1 or abs(hedef_pct) > 20:
                        continue

                    msg = (
                        f"{emoji} <b>TEYÄ°TLÄ° DÃNÃÅ ({yon}): {symbol}</b>\n"
                        f"<i>5m Formasyon - 15m Trend Teyitli</i>\n\n"
                        f"ð° <b>GiriÅ:</b> {akilli_fiyat(current_price)}\n"
                        f"ð¯ <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                        f"ð <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                        f"ð <b>Formasyon:</b> {formasyon_adi}\n"
                        f"ð <b>Teyitler:</b> Hacim ({current_vol/avg_vol:.1f}x), RSI ({rsi:.1f}), EMA (15m)\n\n"
                        f"ð¡ <b>AÃ§Ä±klama:</b> {aciklama}"
                    )
                    if tg(C, msg):
                        son_formasyon_sinyalleri[symbol] = time.time()
                        log("FORMASYON", f"ð¥ SÄ°NYAL: {symbol} ({yon} - {formasyon_adi})")
                time.sleep(0.1)
            time.sleep(FORMASYON_ARALIGI)
        except Exception as e:
            log("FORMASYON", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 7: EMA RIBBON
# =====================================================

def ema_ribbon_loop():
    log("EMA_RIBBON", "EMA Dizilim Motoru BaÅladÄ±.")
    time.sleep(90)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(60)
                continue

            symbols = [
                t["symbol"] for t in tickers
                if uygun_symbol(t) and float(t.get("quoteVolume", 0)) >= MIN_HACIM_EMA_RIBBON
            ]
            for symbol in symbols:
                if symbol in son_ema_ribbon_sinyalleri and time.time() - son_ema_ribbon_sinyalleri[symbol] < 86400:
                    continue

                # 200 kapanmÄ±Å gÃ¼nlÃ¼k mum + halen aÃ§Ä±k olan gÃ¼nlÃ¼k mum.
                klines = gj(
                    f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1d&limit=201"
                )
                if not isinstance(klines, list) or len(klines) < 201:
                    continue

                closed = klines[:-1]
                closes = [float(k[4]) for k in closed]
                vols = [float(k[7]) for k in closed]
                curr, curr_vol = closes[-1], vols[-1]
                avg_vol = sum(vols[-21:-1]) / 20
                if curr_vol <= avg_vol * 1.2:
                    continue

                e7 = calculate_ema(closes, 7)[-1]
                e25 = calculate_ema(closes, 25)[-1]
                e99 = calculate_ema(closes, 99)[-1]
                e200 = calculate_ema(closes, 200)[-1]

                if curr > e7 > e25 > e99 > e200:
                    msg = (
                        f"ð <b>EMA DÄ°ZÄ°LÄ°MÄ° (LONG): {symbol}</b>\n"
                        f"<i>1 GÃ¼nlÃ¼k Grafik - Ãoklu EMA Dizilimi</i>\n\n"
                        f"ð° <b>GiriÅ:</b> {akilli_fiyat(curr)}\n"
                        f"ð¯ <b>Hedef:</b> {akilli_fiyat(curr * 1.20)} (+20.0%)\n"
                        f"ð <b>Stop-Loss:</b> {akilli_fiyat(e25 * 0.95)}\n\n"
                        f"ð <b>EMA Dizilimi:</b>\n"
                        f"EMA7: {akilli_fiyat(e7)} | EMA25: {akilli_fiyat(e25)}\n"
                        f"EMA99: {akilli_fiyat(e99)} | EMA200: {akilli_fiyat(e200)}\n"
                        f"ð <b>Hacim Teyidi:</b> AlÄ±ndÄ± ({curr_vol/avg_vol:.1f}x)\n\n"
                        f"ð¡ <b>AÃ§Ä±klama:</b> EMA Ribbon dizilimi yÃ¼kseliÅ yÃ¶nÃ¼nde gÃ¼Ã§lÃ¼ bir sÄ±ralama gÃ¶steriyor."
                    )
                    if tg(C, msg):
                        son_ema_ribbon_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(EMA_RIBBON_ARALIGI)
        except Exception as e:
            log("EMA_RIBBON", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 8: MACD
# =====================================================

def macd_loop():
    log("MACD", "MACD Motoru BaÅladÄ±.")
    time.sleep(105)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(60)
                continue

            symbols = [
                t["symbol"] for t in tickers
                if uygun_symbol(t) and float(t.get("quoteVolume", 0)) >= MIN_HACIM_MACD
            ]
            for symbol in symbols:
                if symbol in son_macd_sinyalleri and time.time() - son_macd_sinyalleri[symbol] < 14400:
                    continue

                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1d&limit=100")
                if not isinstance(klines, list) or len(klines) < 80:
                    continue
                closes = [float(k[4]) for k in klines[:-1]]
                vols = [float(k[7]) for k in klines[:-1]]
                curr_price, curr_vol = closes[-1], vols[-1]
                avg_vol = sum(vols[-21:-1]) / 20
                if curr_vol <= avg_vol * 1.1:
                    continue

                cm, cs, pm, ps = calculate_macd(closes)
                if cm is None:
                    continue

                if pm <= ps and cm > cs:
                    msg = (
                        f"ð <b>MACD SÄ°NYALÄ° (LONG): {symbol}</b>\n"
                        f"<i>1 GÃ¼nlÃ¼k Grafik - MACD Momentum KÄ±rÄ±lÄ±mÄ±</i>\n\n"
                        f"ð° <b>GiriÅ:</b> {akilli_fiyat(curr_price)}\n"
                        f"ð¯ <b>Hedef:</b> {akilli_fiyat(curr_price * 1.10)} (+10.0%)\n"
                        f"ð <b>Stop-Loss:</b> {akilli_fiyat(curr_price * 0.97)} (-3.0%)\n\n"
                        f"ð <b>MACD Ãizgisi:</b> {cm:.5f} | <b>Sinyal Ãizgisi:</b> {cs:.5f}\n"
                        f"ð <b>Hacim Teyidi:</b> AlÄ±ndÄ±\n\n"
                        f"ð¡ <b>AÃ§Ä±klama:</b> MACD Ã§izgisi sinyal Ã§izgisini yukarÄ± kesti."
                    )
                    if tg(C, msg):
                        son_macd_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(MACD_ARALIGI)
        except Exception as e:
            log("MACD", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 9: TARÄ°HÄ° ZÄ°RVE / DÄ°P KIRILIMI (ATH / ATL)
# =====================================================

def ath_atl_loop():
    log("ATH_ATL", "Tarihi Zirve/Dip KÄ±rÄ±lÄ±m Motoru BaÅladÄ±.")
    time.sleep(135)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(60)
                continue

            adaylar = [
                t["symbol"] for t in tickers
                if uygun_symbol(t) and float(t.get("quoteVolume", 0)) >= MIN_HACIM_ATH_ATL
            ]
            for symbol in adaylar:
                if symbol in son_ath_atl_sinyalleri and time.time() - son_ath_atl_sinyalleri[symbol] < 86400:
                    continue

                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1d&limit=500")
                if not isinstance(klines, list) or len(klines) < 200:
                    continue

                closed = klines[:-1]
                if len(closed) < 2:
                    continue

                # KÄ±rÄ±lÄ±m mumunun kendi high/low deÄerleri tarihsel seviyeye dahil edilmez.
                historical = closed[:-1]
                highs = [float(k[2]) for k in historical]
                lows = [float(k[3]) for k in historical]
                closes = [float(k[4]) for k in closed]
                vols = [float(k[7]) for k in closed]

                current_price, prev_close = closes[-1], closes[-2]
                current_vol = vols[-1]
                ath, atl = max(highs), min(lows)
                avg_vol = sum(vols[-21:-1]) / 20
                is_vol_teyit = current_vol > avg_vol * 1.2
                if not is_vol_teyit:
                    continue

                sinyal = False
                yon = emoji = aciklama = ""
                stop = hedef = 0

                if prev_close >= atl and current_price < atl:
                    sinyal, yon, emoji = True, "SHORT", "ð"
                    stop, hedef = atl * 1.05, current_price * 0.80
                    aciklama = "Fiyat geÃ§miÅteki tarihi dip seviyesini aÅaÄÄ± kÄ±rdÄ±."
                elif prev_close <= ath and current_price > ath:
                    sinyal, yon, emoji = True, "LONG", "ð"
                    stop, hedef = ath * 0.95, current_price * 1.20
                    aciklama = "Fiyat geÃ§miÅteki tarihi zirve seviyesini yukarÄ± kÄ±rdÄ±."

                if sinyal:
                    hedef_pct = ((hedef - current_price) / current_price) * 100
                    stop_pct = ((stop - current_price) / current_price) * 100
                    level = ath if yon == "LONG" else atl
                    msg = (
                        f"{emoji} <b>TARÄ°HÄ° SEVÄ°YE KIRILIMI ({yon}): {symbol}</b>\n"
                        f"<i>1 GÃ¼nlÃ¼k Grafik</i>\n\n"
                        f"ð° <b>GiriÅ:</b> {akilli_fiyat(current_price)}\n"
                        f"ð¯ <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                        f"ð <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                        f"ð <b>KÄ±rÄ±lan Seviye:</b> {akilli_fiyat(level)}\n"
                        f"ð <b>Hacim:</b> {current_vol/avg_vol:.1f}x\n\n"
                        f"ð¡ <b>AÃ§Ä±klama:</b> {aciklama}"
                    )
                    if tg(C, msg):
                        son_ath_atl_sinyalleri[symbol] = time.time()
                        log("ATH_ATL", f"ð¥ SÄ°NYAL: {symbol} ({yon})")
                time.sleep(0.1)
            time.sleep(ATH_ATL_ARALIGI)
        except Exception as e:
            log("ATH_ATL", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 10: ICHIMOKU BULUTU
# =====================================================

def ichimoku_loop():
    log("ICHIMOKU", "ZÄ±rhlÄ± Ichimoku Bulut Motoru BaÅladÄ±.")
    time.sleep(75)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(60)
                continue

            adaylar = [
                t["symbol"] for t in tickers
                if uygun_symbol(t) and float(t.get("quoteVolume", 0)) >= MIN_HACIM_ICHIMOKU
            ]
            for symbol in adaylar:
                if symbol in son_ichimoku_sinyalleri and time.time() - son_ichimoku_sinyalleri[symbol] < 14400:
                    continue

                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1h&limit=100")
                if not isinstance(klines, list) or len(klines) < 80:
                    continue

                highs = [float(k[2]) for k in klines]
                lows = [float(k[3]) for k in klines]
                closes = [float(k[4]) for k in klines]
                vols = [float(k[7]) for k in klines]
                curr_close, prev_close = closes[-2], closes[-3]

                # Son kapanmÄ±Å mum hacmi, kendi ortalamasÄ±na dahil edilmez.
                avg_vol = sum(vols[-22:-2]) / 20
                curr_vol = vols[-2]
                if curr_vol <= avg_vol:
                    continue

                def calc_tenkan(idx):
                    return (max(highs[idx-8:idx+1]) + min(lows[idx-8:idx+1])) / 2

                def calc_kijun(idx):
                    return (max(highs[idx-25:idx+1]) + min(lows[idx-25:idx+1])) / 2

                idx_curr = len(closes) - 2
                tenkan_curr = calc_tenkan(idx_curr)
                kijun_curr = calc_kijun(idx_curr)
                span_a_curr = (calc_tenkan(idx_curr - 26) + calc_kijun(idx_curr - 26)) / 2
                span_a_prev = (calc_tenkan(idx_curr - 27) + calc_kijun(idx_curr - 27)) / 2

                # Senkou Span B: 52 mumluk pencere.
                span_b_curr = (
                    max(highs[idx_curr-77:idx_curr-25])
                    + min(lows[idx_curr-77:idx_curr-25])
                ) / 2
                span_b_prev = (
                    max(highs[idx_curr-78:idx_curr-26])
                    + min(lows[idx_curr-78:idx_curr-26])
                ) / 2

                cloud_top_curr = max(span_a_curr, span_b_curr)
                cloud_bottom_curr = min(span_a_curr, span_b_curr)
                cloud_thickness = cloud_top_curr - cloud_bottom_curr
                cloud_top_prev = max(span_a_prev, span_b_prev)
                cloud_bottom_prev = min(span_a_prev, span_b_prev)

                sinyal = False
                yon = emoji = aciklama = ""
                stop = hedef = 0

                if (
                    prev_close <= cloud_top_prev
                    and curr_close > cloud_top_curr
                    and curr_close > kijun_curr
                    and tenkan_curr > kijun_curr
                    and cloud_thickness > curr_close * 0.005
                ):
                    sinyal, yon, emoji = True, "LONG (Bulut ÃstÃ¼ KÄ±rÄ±lÄ±m)", "ð"
                    stop = min(kijun_curr, cloud_bottom_curr) * 0.99
                    hedef = curr_close + cloud_thickness * 2
                    aciklama = "Fiyat Ichimoku bulutunu yukarÄ± kÄ±rdÄ±; Tenkan-sen, Kijun-sen Ã¼zerinde."
                elif (
                    prev_close >= cloud_bottom_prev
                    and curr_close < cloud_bottom_curr
                    and curr_close < kijun_curr
                    and tenkan_curr < kijun_curr
                    and cloud_thickness > curr_close * 0.005
                ):
                    sinyal, yon, emoji = True, "SHORT (Bulut AltÄ± KÄ±rÄ±lÄ±m)", "ð»"
                    stop = max(kijun_curr, cloud_top_curr) * 1.01
                    hedef = curr_close - cloud_thickness * 2
                    aciklama = "Fiyat Ichimoku bulutunu aÅaÄÄ± kÄ±rdÄ±; Tenkan-sen, Kijun-sen altÄ±nda."

                if sinyal:
                    hedef_pct = ((hedef - curr_close) / curr_close) * 100
                    stop_pct = ((stop - curr_close) / curr_close) * 100
                    if abs(hedef_pct) < 1:
                        continue

                    msg = (
                        f"{emoji} <b>ICHIMOKU SÄ°NYALÄ° ({yon}): {symbol}</b>\n"
                        f"<i>1 Saatlik Grafik - Ichimoku Bulut KÄ±rÄ±lÄ±mÄ±</i>\n\n"
                        f"ð° <b>KapanÄ±Å FiyatÄ±:</b> {akilli_fiyat(curr_close)}\n"
                        f"ð¯ <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                        f"ð <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                        f"âï¸ <b>Bulut BÃ¶lgesi:</b> {akilli_fiyat(cloud_bottom_curr)} - {akilli_fiyat(cloud_top_curr)}\n"
                        f"ð <b>Tenkan-sen:</b> {akilli_fiyat(tenkan_curr)} | <b>Kijun-sen:</b> {akilli_fiyat(kijun_curr)}\n"
                        f"ð <b>Hacim Teyidi:</b> AlÄ±ndÄ±\n\n"
                        f"ð¡ <b>AÃ§Ä±klama:</b> {aciklama}"
                    )
                    if tg(C, msg):
                        son_ichimoku_sinyalleri[symbol] = time.time()
                        log("ICHIMOKU", f"ð¥ SÄ°NYAL: {symbol} ({yon})")
                time.sleep(0.1)
            time.sleep(ICHIMOKU_ARALIGI)
        except Exception as e:
            log("ICHIMOKU", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 11: GRAFÄ°K FORMASYONLARI
# =====================================================

def chart_patterns_loop():
    log("PATTERN", "Formasyon AvcÄ±sÄ± BaÅladÄ± (Ä°kili Tepe/Dip, OBO, Fincan & Kulp).")
    time.sleep(150)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(60)
                continue

            adaylar = [
                t["symbol"] for t in tickers
                if uygun_symbol(t) and float(t.get("quoteVolume", 0)) >= MIN_HACIM_PATTERN
            ]
            for symbol in adaylar:
                if symbol in son_chart_pattern_sinyalleri and time.time() - son_chart_pattern_sinyalleri[symbol] < 14400:
                    continue

                for tf in ["4h", "1d", "3d"]:
                    klines = gj(
                        f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval={tf}&limit=100"
                    )
                    if not isinstance(klines, list) or len(klines) < 50:
                        continue

                    # Formasyon ve kÄ±rÄ±lÄ±m analizinde yalnÄ±zca kapanmÄ±Å mumlar kullanÄ±lÄ±r.
                    closed = klines[:-1]
                    highs = [float(k[2]) for k in closed]
                    lows = [float(k[3]) for k in closed]
                    closes = [float(k[4]) for k in closed]
                    vols = [float(k[7]) for k in closed]
                    curr_close, curr_vol = closes[-1], vols[-1]
                    avg_vol = sum(vols[-21:-1]) / 20
                    if curr_vol < avg_vol * 1.2:
                        continue

                    p_highs, p_lows = find_pivots(highs, lows)
                    sinyal = False
                    formasyon_adi = yon = emoji = ""
                    hedef = stop = neckline = 0

                    if len(p_highs) >= 2:
                        p1_idx, p1_price = p_highs[-2]
                        p2_idx, p2_price = p_highs[-1]
                        if 5 <= (p2_idx - p1_idx) <= 30 and abs(p1_price - p2_price) / p1_price <= 0.02:
                            neckline = min(lows[p1_idx:p2_idx+1])
                            if curr_close < neckline * 0.99:
                                sinyal, formasyon_adi, yon, emoji = True, "Ä°kili Tepe (M)", "SHORT", "ð»"
                                stop = max(p1_price, p2_price) * 1.02
                                hedef = neckline - (max(p1_price, p2_price) - neckline)

                    if not sinyal and len(p_lows) >= 2:
                        p1_idx, p1_price = p_lows[-2]
                        p2_idx, p2_price = p_lows[-1]
                        if 5 <= (p2_idx - p1_idx) <= 30 and abs(p1_price - p2_price) / p1_price <= 0.02:
                            neckline = max(highs[p1_idx:p2_idx+1])
                            if curr_close > neckline * 1.01:
                                sinyal, formasyon_adi, yon, emoji = True, "Ä°kili Dip (W)", "LONG", "ð"
                                stop = min(p1_price, p2_price) * 0.98
                                hedef = neckline + (neckline - min(p1_price, p2_price))

                    if not sinyal and len(p_highs) >= 3:
                        p1_idx, p1_price = p_highs[-3]
                        p2_idx, p2_price = p_highs[-2]
                        p3_idx, p3_price = p_highs[-1]
                        if p2_price > p1_price and p2_price > p3_price and abs(p1_price - p3_price) / p1_price <= 0.03:
                            neckline = min(lows[p1_idx:p3_idx+1])
                            if curr_close < neckline * 0.99:
                                sinyal, formasyon_adi, yon, emoji = True, "Omuz-BaÅ-Omuz", "SHORT", "ð"
                                stop = p3_price * 1.02
                                hedef = neckline - (p2_price - neckline)

                    if not sinyal and len(p_lows) >= 3:
                        p1_idx, p1_price = p_lows[-3]
                        p2_idx, p2_price = p_lows[-2]
                        p3_idx, p3_price = p_lows[-1]
                        if p2_price < p1_price and p2_price < p3_price and abs(p1_price - p3_price) / p1_price <= 0.03:
                            neckline = max(highs[p1_idx:p3_idx+1])
                            if curr_close > neckline * 1.01:
                                sinyal, formasyon_adi, yon, emoji = True, "Ters Omuz-BaÅ-Omuz", "LONG", "ð"
                                stop = p3_price * 0.98
                                hedef = neckline + (neckline - p2_price)

                    if not sinyal and len(p_highs) >= 2 and len(p_lows) >= 1:
                        left_idx, left_rim = p_highs[-2]
                        cup_idx, cup_low = p_lows[-1]
                        right_idx, right_rim = p_highs[-1]
                        if left_idx < cup_idx < right_idx and abs(left_rim - right_rim) / left_rim <= 0.05:
                            if (left_rim - cup_low) / left_rim > 0.05 and curr_close > right_rim * 1.01:
                                sinyal, formasyon_adi, yon, emoji = True, "Fincan ve Kulp (Cup & Handle)", "LONG", "â"
                                neckline = right_rim
                                hedef = right_rim + (right_rim - cup_low)
                                stop = cup_low * 1.02

                    if not sinyal and len(p_lows) >= 2 and len(p_highs) >= 1:
                        left_idx, left_rim = p_lows[-2]
                        cup_idx, cup_high = p_highs[-1]
                        right_idx, right_rim = p_lows[-1]
                        if left_idx < cup_idx < right_idx and abs(left_rim - right_rim) / left_rim <= 0.05:
                            if (cup_high - left_rim) / left_rim > 0.05 and curr_close < right_rim * 0.99:
                                sinyal, formasyon_adi, yon, emoji = True, "Ters Fincan ve Kulp", "SHORT", "â"
                                neckline = right_rim
                                hedef = right_rim - (cup_high - right_rim)
                                stop = cup_high * 0.98

                    if sinyal and curr_close > 0:
                        hedef_pct = ((hedef - curr_close) / curr_close) * 100
                        stop_pct = ((stop - curr_close) / curr_close) * 100
                        if yon == "SHORT" and hedef >= curr_close:
                            continue
                        if yon == "LONG" and hedef <= curr_close:
                            continue
                        if abs(stop_pct) > 15 or abs(hedef_pct) < 1.5 or abs(hedef_pct) > 40:
                            continue

                        aciklama = "Klasik grafik formasyonu ve boyun Ã§izgisi kÄ±rÄ±lÄ±mÄ± tespit edildi."
                        if "OBO" in formasyon_adi or "Omuz" in formasyon_adi:
                            aciklama = "Omuz-baÅ-omuz yapÄ±sÄ±nda boyun Ã§izgisi kÄ±rÄ±lÄ±mÄ± gÃ¶rÃ¼ldÃ¼."
                        if "Dip" in formasyon_adi:
                            aciklama = "Ãift dip formasyonunda boyun Ã§izgisi yukarÄ± kÄ±rÄ±ldÄ±."
                        if "Fincan" in formasyon_adi:
                            aciklama = "Fincan yapÄ±sÄ±nÄ±n direnÃ§ seviyesi kÄ±rÄ±ldÄ±."

                        msg = (
                            f"{emoji} <b>GRAFÄ°K FORMASYONU ({yon}): {symbol}</b>\n"
                            f"<i>{tf.upper()} Grafik - {formasyon_adi}</i>\n\n"
                            f"ð° <b>GiriÅ:</b> {akilli_fiyat(curr_close)}\n"
                            f"ð¯ <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                            f"ð <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                            f"ð <b>Boyun Ãizgisi:</b> {akilli_fiyat(neckline)}\n"
                            f"ð <b>Hacim Teyidi:</b> AlÄ±ndÄ± ({curr_vol/avg_vol:.1f}x)\n\n"
                            f"ð¡ <b>AÃ§Ä±klama:</b> {aciklama}"
                        )
                        if tg(C, msg):
                            son_chart_pattern_sinyalleri[symbol] = time.time()
                            log("PATTERN", f"ð¥ SÄ°NYAL: {symbol} ({yon} - {formasyon_adi})")
                    time.sleep(0.1)
            time.sleep(CHART_PATTERN_ARALIGI)
        except Exception as e:
            log("PATTERN", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 12: TOP GAINERS SHORT AVCISI
# =====================================================

def top_gainers_short_loop():
    log("SHORT_GAINERS", "Top Gainers Short AvcÄ±sÄ± BaÅladÄ± (KÃ¢r Realizasyonu).")
    time.sleep(180)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(60)
                continue

            temiz = [t for t in tickers if uygun_symbol(t)]
            temiz.sort(key=lambda x: float(x["priceChangePercent"]), reverse=True)
            top_gainers = temiz[:5]

            for t in top_gainers:
                symbol = t["symbol"]
                change_pct = float(t["priceChangePercent"])
                if change_pct < 15:
                    continue
                if symbol in son_short_gainers_sinyalleri and time.time() - son_short_gainers_sinyalleri[symbol] < 43200:
                    continue

                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=15m&limit=50")
                if not isinstance(klines, list) or len(klines) < 50:
                    continue

                closes = [float(k[4]) for k in klines[:-1]]
                highs = [float(k[2]) for k in klines[:-1]]
                vols = [float(k[7]) for k in klines[:-1]]
                curr_close, curr_vol = closes[-1], vols[-1]
                avg_vol = sum(vols[-21:-1]) / 20
                if curr_vol < avg_vol:
                    continue

                ema9 = calculate_ema(closes, 9)[-1]
                ema21 = calculate_ema(closes, 21)[-1]
                rsi = calculate_rsi(closes)
                recent_high = max(highs[-20:])
                drop_from_top = ((recent_high - curr_close) / recent_high) * 100 if recent_high else 0

                if rsi > 55 and ema9 < ema21 and curr_close < closes[-2] and drop_from_top > 2:
                    sl = recent_high * 1.01
                    tp1, tp2 = curr_close * 0.95, curr_close * 0.90
                    msg = (
                        f"ð©¸ <b>TOP GAINERS SHORT FIRSATI: {symbol}</b>\n"
                        f"<i>15m Grafik - KÃ¢r Realizasyonu</i>\n\n"
                        f"â ï¸ <i>Coin 24 saatte <b>+{change_pct:.1f}%</b> yÃ¼kselmiÅ ve geri Ã§ekilme iÅaretleri gÃ¶steriyor.</i>\n\n"
                        f"ð° <b>GiriÅ (Short):</b> {akilli_fiyat(curr_close)}\n"
                        f"ð <b>Stop-Loss:</b> {akilli_fiyat(sl)}\n"
                        f"ð¯ <b>Hedef 1:</b> {akilli_fiyat(tp1)} (-5.0%)\n"
                        f"ð¯ <b>Hedef 2:</b> {akilli_fiyat(tp2)} (-10.0%)\n\n"
                        f"ð <b>RSI:</b> {rsi:.1f}\n"
                        f"ð <b>EMA:</b> 9, 21'in altÄ±na indi\n"
                        f"ð <b>Hacim:</b> Ortalama Ã¼zerinde"
                    )
                    if tg(C, msg):
                        son_short_gainers_sinyalleri[symbol] = time.time()
                        log("SHORT_GAINERS", f"ð¥ SÄ°NYAL: {symbol} (SHORT)")
            time.sleep(SHORT_GAINERS_ARALIGI)
        except Exception as e:
            log("SHORT_GAINERS", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 13: TOP MOVERS
# =====================================================

def top_movers_loop():
    log("TOP_MOVERS", "Saatlik En Ãok Hareket Edenler Raporu BaÅladÄ±.")
    time.sleep(200)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(60)
                continue

            temiz = [
                t for t in tickers
                if uygun_symbol(t) and float(t.get("quoteVolume", 0)) > MIN_HACIM_MOVERS
            ]
            saatlik_veriler = []
            for t in temiz:
                symbol = t["symbol"]
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1h&limit=2")
                if not isinstance(klines, list) or len(klines) < 2:
                    continue
                open_price = float(klines[-2][1])
                close_price = float(klines[-2][4])
                if open_price == 0:
                    continue
                degisim = ((close_price - open_price) / open_price) * 100
                saatlik_veriler.append({
                    "symbol": symbol, "change": degisim, "price": close_price
                })
                time.sleep(0.05)

            if not saatlik_veriler:
                time.sleep(TOP_MOVERS_ARALIGI)
                continue

            saatlik_veriler.sort(key=lambda x: x["change"], reverse=True)
            top_3_up = saatlik_veriler[:3]
            top_3_down = list(reversed(saatlik_veriler[-3:]))

            msg = "â±ï¸ <b>Son 1 Saatin Hareketlileri</b>\n\n"
            msg += "ð¢ <b>En Ãok YÃ¼kselenler:</b>\n"
            for i, item in enumerate(top_3_up):
                msg += f"{i+1}. <b>{item['symbol'].replace('USDT', '')}</b> +{item['change']:.2f}% | {akilli_fiyat(item['price'])}\n"
            msg += "\nð´ <b>En Ãok DÃ¼Åenler:</b>\n"
            for i, item in enumerate(top_3_down):
                msg += f"{i+1}. <b>{item['symbol'].replace('USDT', '')}</b> {item['change']:.2f}% | {akilli_fiyat(item['price'])}\n"

            tg(C, msg)
            time.sleep(TOP_MOVERS_ARALIGI)
        except Exception as e:
            log("TOP_MOVERS", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 14: BTC DOMÄ°NANS TAKÄ°BÄ°
# =====================================================

def dominans_loop():
    log("DOMINANS", "Piyasa YÃ¶nÃ¼ ve BTC Dominans Motoru BaÅladÄ±.")
    time.sleep(240)
    while True:
        try:
            data = gj("https://api.coingecko.com/api/v3/global")
            if not data or "data" not in data:
                time.sleep(DOMINANS_ARALIGI)
                continue

            btc_d = float(data["data"]["market_cap_percentage"]["btc"])
            eth_d = float(data["data"]["market_cap_percentage"]["eth"])
            yon = "ð¢ YÃ¼kseliÅte"
            aciklama = "Piyasadaki para Bitcoin'e akÄ±yor. Altcoinlerde dikkatli olunmalÄ±."
            emoji = "ð"

            if btc_d < 58.0 or (btc_d < 60.0 and eth_d > 12.0):
                yon = "ð´ DÃ¼ÅÃ¼Åte (Altcoin Sezonu)"
                aciklama = "Bitcoin DominansÄ± zayÄ±flÄ±yor. Para altcoinlere kayÄ±yor olabilir."
                emoji = "ð¥"

            msg = (
                f"{emoji} <b>PÄ°YASA YÃNÃ & DOMÄ°NANS RAPORU</b>\n\n"
                f"ðª <b>BTC DominansÄ±:</b> %{btc_d:.2f}\n"
                f"ð <b>ETH DominansÄ±:</b> %{eth_d:.2f}\n\n"
                f"ð <b>Durum:</b> {yon}\n"
                f"ð¡ <b>DeÄerlendirme:</b> {aciklama}"
            )
            tg(C, msg)
            time.sleep(DOMINANS_ARALIGI)
        except Exception as e:
            log("DOMINANS", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# MOTOR 15: 200 GÃNLÃK SMA KIRILIMI (LONG / SHORT)
# =====================================================

def sma200_loop():
    log("SMA200", "200 GÃ¼nlÃ¼k SMA KÄ±rÄ±lÄ±m Motoru BaÅladÄ±.")
    time.sleep(255)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(60)
                continue

            symbols = [
                t["symbol"] for t in tickers
                if uygun_symbol(t)
            ]

            for symbol in symbols:
                klines = gj(
                    f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1d&limit=202"
                )
                if not isinstance(klines, list) or len(klines) < 202:
                    continue

                # Son mum aÃ§Ä±k olabileceÄi iÃ§in Ã§Ä±karÄ±lÄ±r.
                closed = klines[:-1]
                closes = [float(k[4]) for k in closed]

                # Ä°ki ardÄ±ÅÄ±k kapanÄ±Å ve iki ayrÄ± 200 gÃ¼nlÃ¼k SMA gerekir.
                if len(closes) < 201:
                    continue

                previous_close = closes[-2]
                current_close = closes[-1]
                previous_sma = calculate_sma(closes[-201:-1], 200)
                current_sma = calculate_sma(closes[-200:], 200)
                if previous_sma is None or current_sma is None:
                    continue

                # Sadece gerÃ§ek gÃ¼nlÃ¼k kapanÄ±Å kesiÅiminde bildirim.
                direction = None
                if previous_close <= previous_sma and current_close > current_sma:
                    direction = "LONG"
                elif previous_close >= previous_sma and current_close < current_sma:
                    direction = "SHORT"

                if not direction:
                    continue

                candle_time = int(closed[-1][0])
                if son_sma200_sinyalleri.get(symbol) == candle_time:
                    continue

                if direction == "LONG":
                    emoji = "ð"
                    explanation = "GÃ¼nlÃ¼k kapanÄ±Å 200 gÃ¼nlÃ¼k basit hareketli ortalamanÄ±n Ã¼zerine Ã§Ä±ktÄ±."
                else:
                    emoji = "ð»"
                    explanation = "GÃ¼nlÃ¼k kapanÄ±Å 200 gÃ¼nlÃ¼k basit hareketli ortalamanÄ±n altÄ±na indi."

                fark_pct = ((current_close - current_sma) / current_sma) * 100
                msg = (
                    f"{emoji} <b>200 GÃNLÃK SMA KIRILIMI ({direction}): {symbol}</b>\n"
                    f"<i>Binance Futures - 1 GÃ¼nlÃ¼k Grafik</i>\n\n"
                    f"ð° <b>GÃ¼nlÃ¼k KapanÄ±Å:</b> {akilli_fiyat(current_close)}\n"
                    f"ð <b>Ãnceki KapanÄ±Å:</b> {akilli_fiyat(previous_close)}\n"
                    f"ð <b>Ãnceki 200 SMA:</b> {akilli_fiyat(previous_sma)}\n"
                    f"ð <b>GÃ¼ncel 200 SMA:</b> {akilli_fiyat(current_sma)}\n"
                    f"ð <b>Ortalamaya GÃ¶re Fark:</b> {fark_pct:+.2f}%\n\n"
                    f"ð¡ <b>AÃ§Ä±klama:</b> {explanation}\n"
                    f"â¹ï¸ <i>Sinyal yalnÄ±zca kapanmÄ±Å gÃ¼nlÃ¼k mumla oluÅturulmuÅtur.</i>"
                )

                if tg(C, msg):
                    son_sma200_sinyalleri[symbol] = candle_time
                    log("SMA200", f"ð¥ SÄ°NYAL: {symbol} ({direction})")

                time.sleep(0.05)

            time.sleep(SMA200_ARALIGI)
        except Exception as e:
            log("SMA200", f"Hata: {e}")
            time.sleep(60)


# =====================================================
# RENDER WEB SUNUCUSU
# =====================================================

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Ultimate Futures Bot is running!")

    def log_message(self, format_string, *args):
        return


def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    log("SÄ°STEM", f"WEB SUNUCUSU BAÅLADI - PORT: {port}")
    server.serve_forever()


# =====================================================
# BAÅLAT
# =====================================================

if __name__ == "__main__":
    threading.Thread(target=top_gainers_loop, daemon=True).start()
    threading.Thread(target=pump_winner_loop, daemon=True).start()

    threading.Thread(target=chartist_loop, daemon=True).start()
    threading.Thread(target=pump_loop, daemon=True).start()
    threading.Thread(target=volatilite_loop, daemon=True).start()
    threading.Thread(target=formasyon_loop, daemon=True).start()
    threading.Thread(target=ema_ribbon_loop, daemon=True).start()
    threading.Thread(target=macd_loop, daemon=True).start()
    threading.Thread(target=ath_atl_loop, daemon=True).start()
    threading.Thread(target=ichimoku_loop, daemon=True).start()
    threading.Thread(target=chart_patterns_loop, daemon=True).start()
    threading.Thread(target=top_gainers_short_loop, daemon=True).start()
    threading.Thread(target=top_movers_loop, daemon=True).start()
    threading.Thread(target=dominans_loop, daemon=True).start()
    threading.Thread(target=sma200_loop, daemon=True).start()

    run_web_server()

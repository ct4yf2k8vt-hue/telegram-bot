import urllib.request
import urllib.parse
import urllib.error
import base64
import ssl
import struct
import zlib
import json
import re
import collections
import math
import time
import socket
import os
import threading
import http.server
from datetime import datetime, timedelta, timezone

socket.setdefaulttimeout(15)

T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()

TR_Saati = timezone(timedelta(hours=3))
BASE = "https://api.binance.com"
FAPI = "https://fapi.binance.com"

PUMP_WINNER_ARALIGI = 21600
MIN_HACIM_PUMP = 2_000_000
PUMP_TEKRAR_SAAT = float(os.environ.get("PUMP_REPEAT_HOURS", "6"))

YASAKLI_HISSELER = [
    "AAPLUSDT", "TSLAUSDT", "NVDAUSDT", "COINUSDT", "MSTRUSDT",
    "SQQQUSDT", "TQQQUSDT", "SPYUSDT", "QQQUSDT", "AMZNUSDT",
    "GOOGLUSDT", "METAUSDT", "NFLXUSDT", "BABAUSDT", "MSFTUSDT",
    "HOODUSDT", "CRCLUSDT", "XAUUSDT", "XAGUSDT", "PAXGUSDT"
]

def fmt_price(p):
    if not p or p <= 0 or math.isnan(p) or math.isinf(p): return "0"
    if p >= 1000: return f"{p:,.2f}"
    if p >= 10: return f"{p:.3f}".rstrip("0").rstrip(".")
    if p >= 1: return f"{p:.4f}".rstrip("0").rstrip(".")
    if p >= 0.01: return f"{p:.5f}".rstrip("0").rstrip(".")
    if p >= 0.0001: return f"{p:.6f}".rstrip("0").rstrip(".")
    return f"{p:.8f}".rstrip("0").rstrip(".")

def simdi(): return datetime.now(TR_Saati)

def log(prefix, m):
    print(f"[{simdi().strftime('%H:%M:%S')}] [{prefix}] {m}", flush=True)

def akilli_fiyat(p):
    if p is None: return "0"
    p = float(p)
    if p >= 1000: return f"{p:.2f}"
    if p >= 1: return f"{p:.4f}"
    if p >= 0.01: return f"{p:.6f}"
    return f"{p:.8f}"

# =====================================================
# P0 #1: ATR tabanlı dinamik eşik için ATR(%)
# =====================================================
def atr_pct(klines, period=14):
    if not klines or len(klines) < period + 1: return None
    trs = []
    for i in range(1, len(klines)):
        try:
            h = float(klines[i][2]); l = float(klines[i][3]); pc = float(klines[i-1][4])
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        except (ValueError, IndexError, TypeError):
            continue
    if len(trs) < period: return None
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    last_close = float(klines[-1][4])
    if last_close <= 0: return None
    return atr / last_close * 100

# =====================================================
# P0 #2: EWMA hacim istatistikleri
# =====================================================
def ewma_stats(values, alpha=0.15):
    if not values: return 0.0, 0.0
    ewma = float(values[0])
    ewvar = 0.0
    for v in values[1:]:
        diff = v - ewma
        ewma = alpha * v + (1 - alpha) * ewma
        ewvar = alpha * diff * diff + (1 - alpha) * ewvar
    return ewma, math.sqrt(ewvar)

tg_kilit = threading.Lock()
tg_son = {"t": 0.0}

def _tg_istek(req):
    with tg_kilit:
        bekle = 1.1 - (time.time() - tg_son["t"])
        if bekle > 0: time.sleep(bekle)
        try:
            for deneme in (1, 2):
                try:
                    with urllib.request.urlopen(req, timeout=20) as response:
                        return json.loads(response.read().decode("utf-8")).get("ok", False)
                except urllib.error.HTTPError as e:
                    try: body = e.read().decode("utf-8")
                    except Exception: body = ""
                    if e.code == 429 and deneme == 1:
                        try: ra = int(json.loads(body).get("parameters", {}).get("retry_after", 5))
                        except Exception: ra = 5
                        time.sleep(min(ra + 1, 60)); continue
                    log("TG", f"HTTP {e.code}: {body[:200]}")
                    return False
                except Exception as e:
                    log("TG", f"Hata: {type(e).__name__}: {e}")
                    return False
            return False
        finally:
            tg_son["t"] = time.time()

def tg(chat_id, message):
    if not T or not chat_id:
        log("TG", "BOT_TOKEN veya CHAT_ID boş"); return False
    data = urllib.parse.urlencode({
        "chat_id": str(chat_id), "text": message, "parse_mode": "HTML", "disable_web_page_preview": "true"
    }).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{T}/sendMessage", data=data)
    return _tg_istek(req)

def tg_foto(png, caption):
    if not T or not C:
        log("TG", "BOT_TOKEN veya CHAT_ID boş"); return False
    if not png:
        log("TG", "PNG boş, metin gönderiliyor"); return tg(C, caption)
    if len(caption) > 1000: caption = caption[:997] + "..."
    for deneme in (1, 2):
        boundary = f"----PumpBoundary{os.urandom(4).hex()}"
        parts = []
        for k, v in (("chat_id", str(C)), ("caption", caption), ("parse_mode", "HTML")):
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"chart.png\"\r\nContent-Type: image/png\r\n\r\n".encode("utf-8") + png + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        req = urllib.request.Request(f"https://api.telegram.org/bot{T}/sendPhoto", data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        if _tg_istek(req): return True
        log("TG", f"Foto deneme {deneme} başarısız (boyut={len(png)}B)")
        if deneme == 1: time.sleep(2)
    log("TG", "Foto 2 denemede başarısız, metin fallback")
    return tg(C, caption)

# =====================================================
# BINANCE İSTEK KATMANI
# =====================================================
son_uyari = {"t": 0}
ban = {"fapi": 0, "spot": 0}
istek_kuyrugu = collections.deque()
kuyruk_kilit = threading.Lock()
ticker_kilit = threading.Lock()
ticker_cache = {}
BUTCE = 1000

IPUCLARI = {
    "HTTP 451": "Binance bu sunucunun bölgesini engelliyor.",
    "HTTP 403": "Binance erişimi reddetti.",
    "HTTP 429": "İstek limiti aşıldı.",
    "HTTP 418": "IP geçici olarak banlandı.",
}

def api_uyari(neden, url, ek=""):
    if time.time() - son_uyari["t"] < 1800: return
    son_uyari["t"] = time.time()
    tg(C, f"⚠️ <b>Binance bağlantı sorunu</b>\nNeden: {neden}\n{IPUCLARI.get(neden, '')} {ek}")

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
                istek_kuyrugu.append((now, w)); return
        time.sleep(1)

def adres_sec(url):
    host = "fapi" if url.startswith(FAPI) else "spot"
    if time.time() >= ban[host]: return url, host
    diger = "spot" if host == "fapi" else "fapi"
    if time.time() < ban[diger]: return None, "ikisi"
    if host == "fapi":
        yol = url[len(FAPI):]
        if yol.startswith("/fapi/v1/ticker/24hr") or yol.startswith("/fapi/v1/klines"):
            return BASE + yol.replace("/fapi/v1/", "/api/v3/", 1), "spot"
    else:
        yol = url[len(BASE):]
        if yol.startswith("/api/v3/ticker/24hr") or yol.startswith("/api/v3/klines"):
            return FAPI + yol.replace("/api/v3/", "/fapi/v1/", 1), "fapi"
    return None, "yok"

def _istek(url):
    url, host = adres_sec(url)
    if url is None:
        if host == "ikisi": time.sleep(1)
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
            ek = f"Bot {sure // 60 + 1} dk durdu."
        if e.code not in (400, 404): api_uyari(f"HTTP {e.code}", url, ek)
        return None
    except Exception as e:
        log("API", f"{type(e).__name__} -> {url.split('?')[0][-45:]}")
        api_uyari(type(e).__name__, url)
        return None

def gj(url):
    if url.endswith("/ticker/24hr"):
        canli = ticker_listesi(url.startswith(FAPI))
        if canli: return canli
        with ticker_kilit:
            c = ticker_cache.get(url)
            if c and time.time() - c[0] < 55: return c[1]
            data = _istek(url)
            if data is not None: ticker_cache[url] = (time.time(), data)
            return data
    return _istek(url)

def hacim_filtre(tickers, min_hacim):
    out = []
    for t in tickers:
        try:
            s = t['symbol']
            if s.endswith('USDT') and s not in YASAKLI_HISSELER and float(t.get('quoteVolume', 0)) >= min_hacim:
                out.append(s)
        except Exception: continue
    return out

def baglanti_testi(host="fapi"):
    url = f"{FAPI}/fapi/v1/ticker/24hr" if host == "fapi" else f"{BASE}/api/v3/ticker/24hr"
    ad = "Futures" if host == "fapi" else "Spot"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        n = len(hacim_filtre(data, MIN_HACIM_PUMP))
        return True, f"✅ Binance {ad}: bağlı ({len(data)} çift, {n} taranıyor)"
    except urllib.error.HTTPError as e:
        ek = ""
        if e.code in (418, 429): ek = f" {ban_ayarla(e, host) // 60 + 1} dk sonra denenecek."
        return False, f"❌ Binance {ad}: HTTP {e.code}\n{IPUCLARI.get(f'HTTP {e.code}', '')}{ek}"
    except Exception as e:
        return False, f"❌ Binance {ad}: {type(e).__name__}"

# =====================================================
# PUMP WINNER
# =====================================================
def pump_winner_loop():
    log("PUMP_WINNER", "Günün Kazananı Botu Başladı.")
    time.sleep(20)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(300); continue
            temiz = [t for t in tickers if t['symbol'] not in YASAKLI_HISSELER and t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) > 10_000_000]
            temiz.sort(key=lambda x: float(x['priceChangePercent']), reverse=True)
            if not temiz: time.sleep(300); continue
            w = temiz[0]; symbol = w['symbol']; change_pct = float(w['priceChangePercent'])
            if change_pct < 15: time.sleep(PUMP_WINNER_ARALIGI); continue
            klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1d&limit=45")
            if not isinstance(klines, list) or len(klines) < 1: time.sleep(PUMP_WINNER_ARALIGI); continue
            day_low = float(klines[-1][3]); day_high = float(klines[-1][2]); day_open = float(klines[-1][1])
            max_move = ((day_high - day_low) / day_low) * 100
            msg = (f"🏆 <b>GÜNÜN KAZANANI: {symbol}</b>\n\n"
                   f"📉 Dip: {akilli_fiyat(day_low)}\n"
                   f"🚀 Zirve: {akilli_fiyat(day_high)}\n"
                   f"📈 Maks. Yükseliş: <b>+{max_move:.1f}%</b>\n\n"
                   f"💡 {symbol} gün içinde güçlü bir hareket yaptı.")
            try:
                fiyat = float(w['lastPrice'])
                mumlar = [{"openTime": int(k[0]) // 1000, "open": float(k[1]), "high": float(k[2]),
                           "low": float(k[3]), "close": float(k[4]), "volume": float(k[7])} for k in klines]
                png = render_png(f"{symbol} | 1G | GUNUN KAZANANI | ${fmt_price(fiyat)}",
                                 mumlar, fiyat, day_open, zaman=simdi().strftime("%d.%m %H:%M:%S TR"))
            except Exception as e:
                log("PUMP_WINNER", f"grafik: {e}"); png = b""
            tg_foto(png, msg)
            time.sleep(PUMP_WINNER_ARALIGI)
        except Exception as e:
            log("PUMP_WINNER", f"Hata: {e}"); time.sleep(60)

# =====================================================
# WEBSOCKET
# =====================================================
STABIL_ONEKLER = ("USDC", "FDUSD", "TUSD", "USDP", "EUR", "USDE", "USD1")
KALDIRACLI_SONEKLER = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

def dislanmis(s):
    return s.startswith(STABIL_ONEKLER) or s.endswith(KALDIRAC

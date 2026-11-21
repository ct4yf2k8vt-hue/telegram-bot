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

# =====================================================
# AYARLAR
# =====================================================
socket.setdefaulttimeout(15)

T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()

TR_Saati = timezone(timedelta(hours=3))
BASE = "https://api.binance.com"
FAPI = "https://fapi.binance.com"

PUMP_WINNER_ARALIGI = 21600   # Günün Kazananı: 6 saatte bir
MIN_HACIM_PUMP = 2_000_000    # açılış testi için
# Aynı coin için pump tekrar bildirimi (saat). 0 = bot yeniden başlayana kadar bir kez.
PUMP_TEKRAR_SAAT = float(os.environ.get("PUMP_REPEAT_HOURS", "6"))

YASAKLI_HISSELER = [
    "AAPLUSDT", "TSLAUSDT", "NVDAUSDT", "COINUSDT", "MSTRUSDT",
    "SQQQUSDT", "TQQQUSDT", "SPYUSDT", "QQQUSDT", "AMZNUSDT",
    "GOOGLUSDT", "METAUSDT", "NFLXUSDT", "BABAUSDT", "MSFTUSDT",
    "HOODUSDT", "CRCLUSDT", "XAUUSDT", "XAGUSDT", "PAXGUSDT"
]

# =====================================================
# YARDIMCI FONKSİYONLAR
# =====================================================
def fmt_price(p):
    if not p or p <= 0 or math.isnan(p) or math.isinf(p):
        return "0"
    if p >= 1000: return f"{p:,.2f}"
    if p >= 10: return f"{p:.3f}".rstrip("0").rstrip(".")
    if p >= 1: return f"{p:.4f}".rstrip("0").rstrip(".")
    if p >= 0.01: return f"{p:.5f}".rstrip("0").rstrip(".")
    if p >= 0.0001: return f"{p:.6f}".rstrip("0").rstrip(".")
    return f"{p:.8f}".rstrip("0").rstrip(".")

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
                        time.sleep(min(ra + 1, 60))
                        continue
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
        log("TG", "BOT_TOKEN veya CHAT_ID boş, mesaj gönderilmedi!")
        return False
    data = urllib.parse.urlencode({
        "chat_id": str(chat_id), "text": message, "parse_mode": "HTML", "disable_web_page_preview": "true"
    }).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{T}/sendMessage", data=data)
    return _tg_istek(req)

def tg_foto(png, caption):
    if not T or not C:
        log("TG", "BOT_TOKEN veya CHAT_ID boş, mesaj gönderilmedi!")
        return False
    if not png: return tg(C, caption)
    if len(caption) > 1000: caption = caption[:997] + "..."
    boundary = "----PumpBoundary7e8a9b"
    parts = []
    for k, v in (("chat_id", str(C)), ("caption", caption), ("parse_mode", "HTML")):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"chart.png\"\r\nContent-Type: image/png\r\n\r\n".encode("utf-8") + png + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    req = urllib.request.Request(f"https://api.telegram.org/bot{T}/sendPhoto", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    if _tg_istek(req): return True
    return tg(C, caption)

# -----------------------------------------------------
# BINANCE İSTEK KATMANI
# - Dakikalık istek bütçesi (IP ban'ı önler)
# - Ticker verisi 55 sn ortak kullanılır
# - 418/429 gelince ban süresi kadar istek atmaz
# - Futures banlıysa Spot'tan, Spot banlıysa Futures'tan çeker
# =====================================================
son_uyari = {"t": 0}
ban = {"fapi": 0, "spot": 0}
istek_kuyrugu = collections.deque()
kuyruk_kilit = threading.Lock()
ticker_kilit = threading.Lock()
ticker_cache = {}
BUTCE = 1000  # dakikada en fazla bu kadar Binance ağırlığı (limit 2400)

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

def adres_sec(url):
    """Banlı olmayan kaynağı seçer. (url, host) döner; kullanılabilir yoksa url=None."""
    host = "fapi" if url.startswith(FAPI) else "spot"
    if time.time() >= ban[host]:
        return url, host
    diger = "spot" if host == "fapi" else "fapi"
    if time.time() < ban[diger]:
        return None, "ikisi"
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
            ek = f"Bot {sure // 60 + 1} dk boyunca {'Futures' if host == 'fapi' else 'Spot'} isteklerini durdurdu, diğer kaynaktan devam edecek."
        if e.code not in (400, 404): api_uyari(f"HTTP {e.code}", url, ek)
        return None
    except Exception as e:
        log("API", f"{type(e).__name__} -> {url.split('?')[0][-45:]}")
        api_uyari(type(e).__name__, url)
        return None

def gj(url):
    # Ticker verisi 55 sn boyunca ortak kullanılır (tüm motorlar tek istek paylaşır)
    if url.endswith("/ticker/24hr"):
        canli = ticker_listesi(url.startswith(FAPI))
        if canli: return canli
        with ticker_kilit:
            c = ticker_cache.get(url)
            if c and time.time() - c[0] < 55:
                return c[1]
            data = _istek(url)
            if data is not None:
                ticker_cache[url] = (time.time(), data)
            return data
    return _istek(url)

def hacim_filtre(tickers, min_hacim):
    out = []
    for t in tickers:
        try:
            s = t['symbol']
            if s.endswith('USDT') and s not in YASAKLI_HISSELER and float(t.get('quoteVolume', 0)) >= min_hacim:
                out.append(s)
        except Exception:
            continue
    return out

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

# =====================================================
# MOTOR 2: PUMP WINNER
# =====================================================
def pump_winner_loop():
    log("PUMP_WINNER", "Günün Kazananı (Pump Dedektör) Rapor Botu Başladı.")
    time.sleep(20)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(300); continue

            temiz_tickers = [t for t in tickers if t['symbol'] not in YASAKLI_HISSELER and t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) > 10_000_000]
            temiz_tickers.sort(key=lambda x: float(x['priceChangePercent']), reverse=True)

            if not temiz_tickers:
                time.sleep(300)
                continue

            winner = temiz_tickers[0]
            symbol = winner['symbol']
            change_pct = float(winner['priceChangePercent'])

            if change_pct < 15:
                time.sleep(PUMP_WINNER_ARALIGI)
                continue

            klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1d&limit=1")
            if not isinstance(klines, list) or len(klines) < 1:
                time.sleep(PUMP_WINNER_ARALIGI)
                continue

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
# CANLI AKIŞ (WEBSOCKET): Binance'e REST isteği atmadan fiyat/hacim
# =====================================================
STABIL_ONEKLER = ("USDC", "FDUSD", "TUSD", "USDP", "EUR", "USDE", "USD1")
KALDIRACLI_SONEKLER = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

def dislanmis(s):
    return s.startswith(STABIL_ONEKLER) or s.endswith(KALDIRACLI_SONEKLER)

class WSFeed:
    def __init__(self, ad, adresler):
        self.ad = ad
        self.adresler = adresler
        self.veri = {}       # sembol -> (fiyat, acilis, usdt_hacim, zaman)
        self.son = 0.0
        self.bagli = False
        self.son_hata = ""
        self.idx = 0
        self.dinleyici = None

    @staticmethod
    def _recv_exact(sock, buf, n):
        while len(buf) < n:
            chunk = sock.recv(65536)
            if not chunk: raise EOFError("WS kapandı")
            buf.extend(chunk)
        out = bytes(buf[:n])
        del buf[:n]
        return out

    @staticmethod
    def _send(sock, data, opcode):
        mask = os.urandom(4)
        n = len(data)
        head = bytearray([0x80 | opcode])
        if n < 126: head.append(0x80 | n)
        elif n < 65536: head.append(0x80 | 126); head.extend(struct.pack("!H", n))
        else: head.append(0x80 | 127); head.extend(struct.pack("!Q", n))
        head.extend(mask)
        sock.sendall(bytes(head) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _mesaj(self, sock, buf):
        parca = bytearray()
        while True:
            b1, b2 = self._recv_exact(sock, buf, 2)
            fin = b1 & 0x80
            op = b1 & 0x0F
            ln = b2 & 0x7F
            if ln == 126: ln = struct.unpack("!H", self._recv_exact(sock, buf, 2))[0]
            elif ln == 127: ln = struct.unpack("!Q", self._recv_exact(sock, buf, 8))[0]
            if b2 & 0x80: self._recv_exact(sock, buf, 4)
            payload = self._recv_exact(sock, buf, ln)
            if op == 0x8: raise EOFError("WS close")
            if op == 0x9:
                self._send(sock, payload, 0xA)
                continue
            if op == 0xA: continue
            if op in (0x1, 0x2): parca = bytearray(payload)
            elif op == 0x0: parca.extend(payload)
            if fin: return bytes(parca)

    def _baglan(self):
        host, port, path = self.adresler[self.idx % len(self.adresler)]
        ctx = ssl.create_default_context()
        sock = socket.create_connection((host, port), timeout=15)
        ssock = ctx.wrap_socket(sock, server_hostname=host)
        ssock.settimeout(45)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nUser-Agent: Mozilla/5.0\r\n\r\n")
        ssock.sendall(req.encode("ascii"))
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = ssock.recv(4096)
            if not chunk: raise EOFError("handshake kapandı")
            buf += chunk
        ilk = buf.split(b"\r\n", 1)[0].decode("utf-8", "ignore")
        if " 101" not in ilk:
            ssock.close()
            raise RuntimeError(f"handshake reddedildi: {ilk}")
        return ssock, bytearray(buf.split(b"\r\n\r\n", 1)[1])

    def calis(self):
        while True:
            ssock = None
            try:
                ssock, buf = self._baglan()
                self.bagli = True
                self.son_hata = ""
                self.son = time.time()
                log("WS", f"{self.ad} akışı bağlandı.")
                while True:
                    ham = self._mesaj(ssock, buf)
                    arr = json.loads(ham.decode("utf-8"))
                    if isinstance(arr, dict) and "data" in arr: arr = arr["data"]
                    if not isinstance(arr, list): continue
                    now = time.time()
                    for it in arr:
                        try:
                            self.veri[it["s"]] = (float(it["c"]), float(it["o"]), float(it["q"]), now)
                        except Exception:
                            continue
                    self.son = now
                    if self.dinleyici:
                        try: self.dinleyici(self, arr, now)
                        except Exception as e: log("WS", f"dinleyici hata: {e}")
            except Exception as e:
                self.bagli = False
                self.son_hata = f"{type(e).__name__}: {e}"[:120]
                log("WS", f"{self.ad} akışı koptu: {self.son_hata}")
                self.idx += 1
                time.sleep(5)
            finally:
                try:
                    if ssock: ssock.close()
                except Exception:
                    pass

FUT = WSFeed("Futures", [("fstream.binance.com", 443, "/market/ws/!miniTicker@arr"),
                         ("fstream.binance.com", 443, "/ws/!miniTicker@arr")])
SPOT = WSFeed("Spot", [("stream.binance.com", 9443, "/ws/!miniTicker@arr"),
                       ("data-stream.binance.com", 9443, "/ws/!miniTicker@arr")])

def besleme_taze(f):
    return f.bagli and time.time() - f.son < 25

def aktif_besleme():
    if besleme_taze(FUT): return FUT
    if besleme_taze(SPOT): return SPOT
    return None

def pump_besleme():
    if besleme_taze(SPOT): return SPOT
    if besleme_taze(FUT): return FUT
    return None

def ticker_listesi(futures):
    """REST ticker yerine canlı akıştan aynı formatta liste döner. Akış yoksa None."""
    sirali = (FUT, SPOT) if futures else (SPOT, FUT)
    now = time.time()
    for f in sirali:
        if besleme_taze(f) and len(f.veri) > 50:
            out = []
            for s, (c, o, q, t) in list(f.veri.items()):
                if now - t > 10800 or o <= 0: continue
                if s in YASAKLI_HISSELER or dislanmis(s): continue
                out.append({"symbol": s, "lastPrice": c, "quoteVolume": q, "priceChangePercent": (c - o) / o * 100})
            return out
    return None

# =====================================================
# PNG MUM GRAFİĞİ (kütüphanesiz)
# =====================================================
FONT_5X7 = {
    "0": [0x3E, 0x51, 0x49, 0x45, 0x3E], "1": [0x00, 0x42, 0x7F, 0x40, 0x00],
    "2": [0x42, 0x61, 0x51, 0x49, 0x46], "3": [0x21, 0x41, 0x45, 0x4B, 0x31],
    "4": [0x18, 0x14, 0x12, 0x7F, 0x10], "5": [0x27, 0x45, 0x45, 0x45, 0x39],
    "6": [0x3C, 0x4A, 0x49, 0x49, 0x30], "7": [0x01, 0x71, 0x09, 0x05, 0x03],
    "8": [0x36, 0x49, 0x49, 0x49, 0x36], "9": [0x06, 0x49, 0x49, 0x29, 0x1E],
    "A": [0x7E, 0x11, 0x11, 0x11, 0x7E], "B": [0x7F, 0x49, 0x49, 0x49, 0x36],
    "C": [0x3E, 0x41, 0x41, 0x41, 0x22], "D": [0x7F, 0x41, 0x41, 0x22, 0x1C],
    "E": [0x7F, 0x49, 0x49, 0x49, 0x41], "F": [0x7F, 0x09, 0x09, 0x09, 0x01],
    "G": [0x3E, 0x41, 0x49, 0x49, 0x7A], "H": [0x7F, 0x08, 0x08, 0x08, 0x7F],
    "I": [0x00, 0x41, 0x7F, 0x41, 0x00], "J": [0x20, 0x40, 0x41, 0x3F, 0x01],
    "K": [0x7F, 0x08, 0x14, 0x22, 0x41], "L": [0x7F, 0x40, 0x40, 0x40, 0x40],
    "M": [0x7F, 0x02, 0x0C, 0x02, 0x7F], "N": [0x7F, 0x04, 0x08, 0x10, 0x7F],
    "O": [0x3E, 0x41, 0x41, 0x41, 0x3E], "P": [0x7F, 0x09, 0x09, 0x09, 0x06],
    "Q": [0x3E, 0x41, 0x51, 0x21, 0x5E], "R": [0x7F, 0x09, 0x19, 0x29, 0x46],
    "S": [0x46, 0x49, 0x49, 0x49, 0x31], "T": [0x01, 0x01, 0x7F, 0x01, 0x01],
    "U": [0x3F, 0x40, 0x40, 0x40, 0x3F], "V": [0x1F, 0x20, 0x40, 0x20, 0x1F],
    "W": [0x3F, 0x40, 0x38, 0x40, 0x3F], "X": [0x63, 0x14, 0x08, 0x14, 0x63],
    "Y": [0x07, 0x08, 0x70, 0x08, 0x07], "Z": [0x61, 0x51, 0x49, 0x45, 0x43],
    ".": [0x00, 0x60, 0x60, 0x00, 0x00], ",": [0x00, 0x50, 0x30, 0x00, 0x00],
    ":": [0x00, 0x36, 0x36, 0x00, 0x00], "-": [0x08, 0x08, 0x08, 0x08, 0x08],
    "+": [0x08, 0x08, 0x3E, 0x08, 0x08], "/": [0x20, 0x10, 0x08, 0x04, 0x02],
    "%": [0x23, 0x13, 0x08, 0x64, 0x62], "$": [0x24, 0x2A, 0x7F, 0x2A, 0x12],
    " ": [0x00, 0x00, 0x00, 0x00, 0x00], "|": [0x00, 0x00, 0x7F, 0x00, 0x00],
}

def render_png(baslik, candles, live_price, start_price):
    W, H = 860, 440
    BG = (9, 13, 22); GRID = (28, 38, 56)
    BULL = (16, 185, 129); BEAR = (244, 63, 94)
    TEXT = (226, 232, 240); MUTED = (148, 163, 184)
    START_COL = (245, 158, 11); LIVE_COL = (56, 189, 248)

    bars = [dict(b) for b in candles[-75:]]
    if not bars: return b""
    last = bars[-1]
    last["close"] = live_price
    last["high"] = max(last["high"], live_price)
    last["low"] = min(last["low"], live_price)

    buf = bytearray(bytes(BG) * (W * H))

    def set_px(x, y, col):
        if 0 <= x < W and 0 <= y < H:
            i = (y * W + x) * 3
            buf[i] = col[0]; buf[i + 1] = col[1]; buf[i + 2] = col[2]

    def fill_rect(x0, y0, x1, y1, col):
        x0, x1 = max(0, min(x0, x1)), min(W - 1, max(x0, x1))
        y0, y1 = max(0, min(y0, y1)), min(H - 1, max(y0, y1))
        if x1 < x0 or y1 < y0: return
        row = bytes(col) * (x1 - x0 + 1)
        for y in range(y0, y1 + 1):
            st = (y * W + x0) * 3
            buf[st:st + len(row)] = row

    def draw_line(x0, y0, x1, y1, col, dashed=False):
        dx = abs(x1 - x0); dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1; sy = 1 if y0 < y1 else -1
        err = dx + dy; n = 0
        while True:
            if not dashed or (n % 8) < 5: set_px(x0, y0, col)
            if x0 == x1 and y0 == y1: break
            e2 = 2 * err
            if e2 >= dy: err += dy; x0 += sx
            if e2 <= dx: err += dx; y0 += sy
            n += 1

    def draw_text(x, y, text, col, scale=1):
        cx = x
        for ch in text.upper():
            g = FONT_5X7.get(ch, FONT_5X7[" "])
            for gx in range(5):
                for gy in range(7):
                    if g[gx] & (1 << gy):
                        fill_rect(cx + gx * scale, y + gy * scale, cx + gx * scale + scale - 1, y + gy * scale + scale - 1, col)
            cx += 6 * scale

    pad_l, pad_r, pad_t, pad_b = 18, 118, 42, 24
    vol_h, gap = 62, 12
    plot_w = W - pad_l - pad_r
    price_h = H - pad_t - pad_b - vol_h - gap

    allp = [b["high"] for b in bars] + [b["low"] for b in bars] + [live_price]
    if start_price and start_price > 0: allp.append(start_price)
    lo_p = min(allp) * 0.994
    hi_p = max(allp) * 1.006
    rng_p = max(hi_p - lo_p, lo_p * 0.005)
    max_vol = max([b["volume"] for b in bars] + [1.0])

    def y_of(p): return int(pad_t + ((hi_p - p) / rng_p) * price_h)

    draw_text(pad_l, 14, baslik, TEXT, scale=2)
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        gy = int(pad_t + price_h * frac)
        draw_line(pad_l, gy, pad_l + plot_w, gy, GRID, dashed=True)
        draw_text(pad_l + plot_w + 8, gy - 3, fmt_price(hi_p - rng_p * frac), MUTED)

    if start_price and start_price > 0:
        sy = y_of(start_price)
        if pad_t <= sy <= pad_t + price_h:
            draw_line(pad_l, sy, pad_l + plot_w, sy, START_COL, dashed=True)
            draw_text(pad_l + plot_w + 8, sy - 3, "BAS " + fmt_price(start_price), START_COL)

    n = len(bars)
    step = plot_w / max(1, n)
    bw = max(2, int(step * 0.62))
    vol_top = pad_t + price_h + gap
    for i, b in enumerate(bars):
        cx = int(pad_l + (i + 0.5) * step)
        col = BULL if b["close"] >= b["open"] else BEAR
        yo, yc, yh, yl = y_of(b["open"]), y_of(b["close"]), y_of(b["high"]), y_of(b["low"])
        draw_line(cx, yh, cx, yl, col)
        fill_rect(cx - bw // 2, min(yo, yc), cx + bw // 2, max(min(yo, yc) + 1, max(yo, yc)), col)
        vh = max(1, int((b["volume"] / max_vol) * vol_h))
        fill_rect(cx - bw // 2, vol_top + vol_h - vh, cx + bw // 2, vol_top + vol_h, (col[0] // 2, col[1] // 2, col[2] // 2))

    ly = y_of(live_price)
    draw_line(pad_l, ly, pad_l + plot_w, ly, LIVE_COL, dashed=True)
    fill_rect(pad_l + plot_w + 4, ly - 7, W - 4, ly + 7, (2, 132, 199))
    draw_text(pad_l + plot_w + 8, ly - 3, fmt_price(live_price), (248, 250, 252))

    raw = bytearray()
    stride = W * 3
    for y in range(H):
        raw.append(0)
        raw.extend(buf[y * stride:(y + 1) * stride])
    comp = zlib.compress(bytes(raw), 6)

    def chunk(tag, payload):
        return struct.pack("!I", len(payload)) + tag + payload + struct.pack("!I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", W, H, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", comp) + chunk(b"IEND", b""))

# =====================================================
# MOTOR 4+5: CANLI PUMP / DUMP AVCISI (olay tabanlı)
# Fiyatı canlı akıştan izler, sadece tetiklenince 1 dakikalık mumla doğrular.
# =====================================================
PUMP_MIN_HACIM = 2_000_000                  # 24s USDT hacmi alt sınırı
PUMP_PENCERELER = ((420, 4.5),)  # (saniye, minimum % hareket)
PUMP_MIN_CARPAN = 2.0                       # son 5 dk hacmi / önceki 5 dk ortalaması
PUMP_MIN_SON5 = 25_000                      # son 5 dk en az bu kadar USDT hacim

pump_gecmis = {}
pump_son_ornek = {}
pump_cd = {}
pump_aktif = set()
pump_kilit = threading.Lock()
pump_sem = threading.Semaphore(3)
pump_durum = {"feed": None}
toplu = {"t": [], "sessiz": 0.0}
sayac = {"PUMP": 0, "DUMP": 0}

def toplu_kontrol(now):
    """Piyasa genelinde aynı anda çok sinyal gelirse (örn. BTC çöküşü) tek uyarıya indirir."""
    if now < toplu["sessiz"]: return True
    toplu["t"] = [x for x in toplu["t"] if now - x < 120] + [now]
    if len(toplu["t"]) > 6:
        toplu["sessiz"] = now + 300
        log("PUMP", "Piyasa genelinde sert hareket: tekil sinyaller 5 dk durduruldu")
        return True
    return False

def pump_isle(feed, arr, now):
    if pump_besleme() is not feed: return
    if pump_durum["feed"] is not feed:
        pump_durum["feed"] = feed
        pump_gecmis.clear(); pump_son_ornek.clear()
        return
    for it in arr:
        try:
            s = it["s"]
            if not s.endswith("USDT") or s in YASAKLI_HISSELER or dislanmis(s): continue
            q = float(it["q"])
            if q < PUMP_MIN_HACIM: continue
            if now - pump_son_ornek.get(s, 0) < 5: continue
            p = float(it["c"]); o = float(it["o"])
            if p <= 0: continue
            pump_son_ornek[s] = now
            h = pump_gecmis.setdefault(s, [])
            h.append((now, p))
            kes = 0
            while kes < len(h) and h[kes][0] < now - 1200: kes += 1
            if kes: del h[:kes]
            if len(h) < 8 or h[0][0] > now - 90: continue
            pump_kontrol(feed, s, p, o, q, h, now)
        except Exception:
            continue

def pump_kontrol(feed, s, p, o, q, h, now):
    for pencere, esik in PUMP_PENCERELER:
        pts = [x[1] for x in h if x[0] >= now - pencere]
        if len(pts) < 4: continue
        lo, hi = min(pts), max(pts)
        if lo <= 0 or hi <= 0: continue
        up = (p - lo) / lo * 100
        down = (hi - p) / hi * 100
        if up >= esik and p >= hi * 0.985: yon = "PUMP"
        else: continue
        key = (s, yon)
        with pump_kilit:
            if key in pump_aktif or now < pump_cd.get(key, 0): return
            pump_aktif.add(key)
        deg24 = (p - o) / o * 100 if o > 0 else 0.0
        threading.Thread(target=pump_dogrula, args=(feed.ad, s, yon, p, pencere, esik, q, deg24), daemon=True).start()
        return

def pump_dogrula(feed_ad, s, yon, p, pencere, esik, q24, deg24):
    key = (s, yon)
    bitis = time.time() + 300
    alindi = pump_sem.acquire(timeout=30)
    try:
        if not alindi: return
        fut = feed_ad == "Futures"
        url = (f"{FAPI}/fapi/v1/klines?symbol={s}&interval=1m&limit=45" if fut
               else f"{BASE}/api/v3/klines?symbol={s}&interval=1m&limit=45")
        kl = gj(url)
        if not isinstance(kl, list) or len(kl) < 25:
            log("PUMP", f"{s} {yon}: mum verisi alınamadı, iptal")
            return
        zirveler = [float(k[2]) for k in kl]
        dipler = [float(k[3]) for k in kl]
        hacimler = [float(k[7]) for k in kl]
        up = yon == "PUMP"
        n_son = pencere // 60 + 1
        if up:
            ref = min(dipler[-n_son:]); kp = (p - ref) / ref * 100
        else:
            ref = max(zirveler[-n_son:]); kp = (ref - p) / ref * 100
        if kp < esik * 0.8:
            log("PUMP", f"{s} {yon}: mum teyidi yok ({kp:.2f}%), iptal")
            return
        son5 = sum(hacimler[-5:])
        onceki = hacimler[:-5]
        ort5 = (sum(onceki) / len(onceki)) * 5 if onceki else 0
        if ort5 <= 0: return
        carpan = son5 / ort5
        if carpan < PUMP_MIN_CARPAN or son5 < PUMP_MIN_SON5:
            log("PUMP", f"{s} {yon}: hacim teyidi yok ({carpan:.1f}x), iptal")
            return

        bitis = time.time() + (PUMP_TEKRAR_SAAT * 3600 if PUMP_TEKRAR_SAAT > 0 else 10 ** 9)
        sayac[yon] += 1
        if toplu_kontrol(time.time()): return

        kaynak = feed_ad.upper()
        tur = "PUMP"
        msg = (f"⚡ <b>{kaynak} ANLIK PUMP DEDEKTORU — #{s}</b>\n\n"
               f"📈 Degisim: <b>+{kp:.2f}%</b> ({pencere // 60} dk icinde!)\n"
               f"💰 Baslangic: {fmt_price(ref)} ➡️ Anlik: <b>{fmt_price(p)}</b>\n"
               f"💧 24s Parite Hacmi: {q24:,.0f} (USDT)\n"
               f"🔒 Tekil coin kilidi aktif.")

        mumlar = [{"openTime": int(k[0]) // 1000, "open": float(k[1]), "high": float(k[2]),
                   "low": float(k[3]), "close": float(k[4]), "volume": float(k[7])} for k in kl]
        try:
            png = render_png(f"{s} | 1DK CANLI | {kaynak} {tur} | ANLIK ${fmt_price(p)}", mumlar, p, ref)
        except Exception as e:
            log("PUMP", f"grafik hatası: {e}")
            png = b""
        if tg_foto(png, msg):
            log("PUMP", f"🔥 SİNYAL: {s} (+{kp:.2f}%)")
    except Exception as e:
        log("PUMP", f"{s} doğrulama hatası: {e}")
    finally:
        if alindi: pump_sem.release()
        with pump_kilit:
            pump_aktif.discard(key)
            pump_cd[key] = bitis

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
# GÖZETMEN, AKIŞ İZLEME, KEEP-ALIVE
# =====================================================
def gozetmen(fn):
    while True:
        try:
            fn()
            return
        except Exception as e:
            log("GÖZETMEN", f"{getattr(fn, '__name__', 'motor')} çöktü, yeniden başlatılıyor: {e}")
            time.sleep(30)

def durum_loop():
    baslangic = time.time()
    son_uyari_ws = 0.0
    while True:
        time.sleep(30)
        try:
            now = time.time()
            if now - baslangic > 300 and now - son_uyari_ws > 1800:
                kopuk = [f for f in (FUT, SPOT) if not (f.bagli and now - f.son < 120)]
                if len(kopuk) == 2:
                    son_uyari_ws = now
                    tg(C, "⚠️ <b>Canlı akış kesildi</b>\n" + "\n".join(f"{f.ad}: {f.son_hata or 'veri gelmiyor'}" for f in kopuk)
                       + "\nBot yeniden bağlanmayı deniyor.")
        except Exception as e:
            log("DURUM", f"Hata: {e}")

def keepalive_loop():
    url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    if not url: return
    while True:
        time.sleep(600)
        try:
            urllib.request.urlopen(url, timeout=15).read()
        except Exception:
            pass

# =====================================================
# BAŞLAT
# =====================================================
if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()

    FUT.dinleyici = pump_isle
    SPOT.dinleyici = pump_isle
    for f in (FUT, SPOT):
        threading.Thread(target=gozetmen, args=(f.calis,), daemon=True).start()
    for m in (pump_winner_loop, durum_loop, keepalive_loop):
        threading.Thread(target=gozetmen, args=(m,), daemon=True).start()

    time.sleep(10)
    satirlar = []
    for f in (FUT, SPOT):
        if besleme_taze(f): satirlar.append(f"✅ {f.ad} canlı akış ({len(f.veri)} coin)")
        else: satirlar.append(f"❌ {f.ad} canlı akış: {f.son_hata or 'bağlanıyor'}")
    if aktif_besleme():
        bilgi = "Pump takibi canlı akıştan çalışıyor, Binance REST'e ihtiyaç duymuyor."
    else:
        ok, durum = baglanti_testi("fapi")
        if not ok:
            ok2, durum2 = baglanti_testi("spot")
            durum += "\n" + durum2
            ok = ok2
        satirlar.append(durum)
        bilgi = "Canlı akış yok, REST ile devam ediliyor." if ok else "Binance verisi alınamıyor, ban bitene kadar sinyal gelmez."
    if tg(C, f"🤖 <b>Bot başladı</b> ({simdi().strftime('%d.%m %H:%M')})\n✅ Telegram: bağlı\n" + "\n".join(satirlar) + f"\n\n{bilgi}"):
        log("SİSTEM", "Telegram bağlantısı OK.")
    else:
        log("SİSTEM", "⚠️ Telegram mesajı gönderilemedi! BOT_TOKEN / CHAT_ID kontrol et.")

    while True:
        time.sleep(3600)
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
CHARTIST_ARALIGI = 3600    # 1 Saat
PUMP_ARALIGI = 60          # 1 Dakika
VOLATILITE_ARALIGI = 60    # 1 Dakika
FORMASYON_ARALIGI = 3600   # 1 Saat
EMA_RIBBON_ARALIGI = 3600  # 1 Saat
MACD_ARALIGI = 3600        # 1 Saat
ATH_ATL_ARALIGI = 3600     # 1 Saat
ICHIMOKU_ARALIGI = 3600    # 1 Saat
CHART_PATTERN_ARALIGI = 1800 # 30 Dakika
SHORT_GAINERS_ARALIGI = 300  # 5 Dakika

# RAPOR MOTORLARI SÜRELERİ
TOP_GAINERS_ARALIGI = 14400  # 4 Saat
PUMP_WINNER_ARALIGI = 21600  # 6 Saat

# FİLTRELER
MIN_HACIM_CHARTIST = 10_000_000
MIN_HACIM_PUMP = 5_000_000
MIN_HACIM_VOLATILITE = 10_000_000
MIN_HACIM_FORMASYON = 15_000_000
MIN_HACIM_EMA_RIBBON = 10_000_000
MIN_HACIM_MACD = 10_000_000
MIN_HACIM_ATH_ATL = 5_000_000
MIN_HACIM_ICHIMOKU = 10_000_000
MIN_HACIM_PATTERN = 15_000_000

# YASAKLI VARLIKLAR
YASAKLI_HISSELER = [
    "AAPLUSDT", "TSLAUSDT", "NVDAUSDT", "COINUSDT", "MSTRUSDT",
    "SQQQUSDT", "TQQQUSDT", "SPYUSDT", "QQQUSDT", "AMZNUSDT",
    "GOOGLUSDT", "METAUSDT", "NFLXUSDT", "BABAUSDT", "MSFTUSDT",
    "HOODUSDT", "CRCLUSDT", "XAUUSDT", "XAGUSDT", "PAXGUSDT"
]

# Sinyal Hafızaları
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
                        time.sleep(min(ra + 1, 60))
                        continue
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
        log("TG", "BOT_TOKEN veya CHAT_ID boş, mesaj gönderilmedi!")
        return False
    data = urllib.parse.urlencode({
        "chat_id": str(chat_id), "text": message, "parse_mode": "HTML", "disable_web_page_preview": "true"
    }).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{T}/sendMessage", data=data)
    return _tg_istek(req)

def tg_foto(png, caption):
    if not T or not C:
        log("TG", "BOT_TOKEN veya CHAT_ID boş, mesaj gönderilmedi!")
        return False
    if not png: return tg(C, caption)
    if len(caption) > 1000: caption = caption[:997] + "..."
    boundary = "----PumpBoundary7e8a9b"
    parts = []
    for k, v in (("chat_id", str(C)), ("caption", caption), ("parse_mode", "HTML")):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"chart.png\"\r\nContent-Type: image/png\r\n\r\n".encode("utf-8") + png + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    req = urllib.request.Request(f"https://api.telegram.org/bot{T}/sendPhoto", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    if _tg_istek(req): return True
    return tg(C, caption)

# -----------------------------------------------------
# BINANCE İSTEK KATMANI
# - Dakikalık istek bütçesi (IP ban'ı önler)
# - Ticker verisi 55 sn ortak kullanılır
# - 418/429 gelince ban süresi kadar istek atmaz
# - Futures banlıysa Spot'tan, Spot banlıysa Futures'tan çeker
# =====================================================
son_uyari = {"t": 0}
ban = {"fapi": 0, "spot": 0}
istek_kuyrugu = collections.deque()
kuyruk_kilit = threading.Lock()
ticker_kilit = threading.Lock()
ticker_cache = {}
BUTCE = 1000  # dakikada en fazla bu kadar Binance ağırlığı (limit 2400)

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

def adres_sec(url):
    """Banlı olmayan kaynağı seçer. (url, host) döner; kullanılabilir yoksa url=None."""
    host = "fapi" if url.startswith(FAPI) else "spot"
    if time.time() >= ban[host]:
        return url, host
    diger = "spot" if host == "fapi" else "fapi"
    if time.time() < ban[diger]:
        return None, "ikisi"
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
            ek = f"Bot {sure // 60 + 1} dk boyunca {'Futures' if host == 'fapi' else 'Spot'} isteklerini durdurdu, diğer kaynaktan devam edecek."
        if e.code not in (400, 404): api_uyari(f"HTTP {e.code}", url, ek)
        return None
    except Exception as e:
        log("API", f"{type(e).__name__} -> {url.split('?')[0][-45:]}")
        api_uyari(type(e).__name__, url)
        return None

def gj(url):
    # Ticker verisi 55 sn boyunca ortak kullanılır (tüm motorlar tek istek paylaşır)
    if url.endswith("/ticker/24hr"):
        canli = ticker_listesi(url.startswith(FAPI))
        if canli: return canli
        with ticker_kilit:
            c = ticker_cache.get(url)
            if c and time.time() - c[0] < 55:
                return c[1]
            data = _istek(url)
            if data is not None:
                ticker_cache[url] = (time.time(), data)
            return data
    return _istek(url)

def hacim_filtre(tickers, min_hacim):
    out = []
    for t in tickers:
        try:
            s = t['symbol']
            if s.endswith('USDT') and s not in YASAKLI_HISSELER and float(t.get('quoteVolume', 0)) >= min_hacim:
                out.append(s)
        except Exception:
            continue
    return out

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

# -----------------------------------------------------
# İNDİKATÖRLER
# -----------------------------------------------------
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

        if not oi_data and not taker_data: return ""  # veri alınamadıysa yanlış teyit verme
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
# MOTOR 1: TOP GAINERS
# =====================================================
def top_gainers_loop():
    log("TOP_GAINERS", "Günlük En İyi Hareketler Rapor Botu Başladı.")
    time.sleep(10)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(300); continue

            temiz_tickers = [t for t in tickers if t['symbol'] not in YASAKLI_HISSELER and t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) > 5_000_000]
            temiz_tickers.sort(key=lambda x: float(x['priceChangePercent']), reverse=True)
            top_5 = temiz_tickers[:5]

            if top_5:
                msg = "🔥 <b>Günün En İyi Hareketleri (24 Saat)</b>\n"
                msg += "<i>Maksimum yükseliş yönü, hacim teyitli</i>\n\n"
                medals = ["🥇", "🥈", "🥉", "🏅", "⭐️"]
                for i, t in enumerate(top_5):
                    symbol = t['symbol'].replace("USDT", "")
                    change = float(t['priceChangePercent'])
                    price = float(t['lastPrice'])
                    change_str = f"+{change:.0f}%" if change > 0 else f"{change:.0f}%"
                    msg += f"{medals[i]} <b>{symbol}</b> {change_str} | Fiyat: {akilli_fiyat(price)}\n"

                msg += "\n👉 <i>En çok kazandıran coinler listelendi.</i>"
                tg(C, msg)
            time.sleep(TOP_GAINERS_ARALIGI)
        except Exception as e:
            log("TOP_GAINERS", f"Hata: {e}")
            time.sleep(60)

# =====================================================
# MOTOR 2: PUMP WINNER
# =====================================================
def pump_winner_loop():
    log("PUMP_WINNER", "Günün Kazananı (Pump Dedektör) Rapor Botu Başladı.")
    time.sleep(20)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(300); continue

            temiz_tickers = [t for t in tickers if t['symbol'] not in YASAKLI_HISSELER and t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) > 10_000_000]
            temiz_tickers.sort(key=lambda x: float(x['priceChangePercent']), reverse=True)

            if not temiz_tickers:
                time.sleep(300)
                continue

            winner = temiz_tickers[0]
            symbol = winner['symbol']
            change_pct = float(winner['priceChangePercent'])

            if change_pct < 15:
                time.sleep(PUMP_WINNER_ARALIGI)
                continue

            klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1d&limit=1")
            if not isinstance(klines, list) or len(klines) < 1:
                time.sleep(PUMP_WINNER_ARALIGI)
                continue

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
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in YASAKLI_HISSELER and float(t.get('quoteVolume', 0)) >= MIN_HACIM_CHARTIST]:
                for tf in ['8h', '12h', '1d', '3d']:
                    cooldown_key = f"CH_{tf}_{symbol}"
                    if cooldown_key in son_sinyaller and time.time() - son_sinyaller[cooldown_key] < 86400: continue
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=260")
                    if not isinstance(klines, list) or len(klines) < 205: continue
                    closes = [float(k[4]) for k in klines[:-1]]
                    curr, prev = closes[-1], closes[-2]
                    ema200 = calculate_ema(closes, 200)
                    if len(ema200) >= 2 and prev <= ema200[-2] and curr > ema200[-1] and curr > ema200[-1]*1.01:
                        vol = [float(k[7]) for k in klines[:-1]]
                        if vol[-1] > sum(vol[-21:-1])/20:
                            sl = ema200[-1] * 0.97
                            msg = (f"📈 <b>UZUN VADELİ SPOT (LONG): {symbol}</b>\n"
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
# CANLI AKIŞ (WEBSOCKET): Binance'e REST isteği atmadan fiyat/hacim
# =====================================================
STABIL_ONEKLER = ("USDC", "FDUSD", "TUSD", "USDP", "EUR", "USDE", "USD1")
KALDIRACLI_SONEKLER = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

def dislanmis(s):
    return s.startswith(STABIL_ONEKLER) or s.endswith(KALDIRACLI_SONEKLER)

class WSFeed:
    def __init__(self, ad, adresler):
        self.ad = ad
        self.adresler = adresler
        self.veri = {}       # sembol -> (fiyat, acilis, usdt_hacim, zaman)
        self.son = 0.0
        self.bagli = False
        self.son_hata = ""
        self.idx = 0
        self.dinleyici = None

    @staticmethod
    def _recv_exact(sock, buf, n):
        while len(buf) < n:
            chunk = sock.recv(65536)
            if not chunk: raise EOFError("WS kapandı")
            buf.extend(chunk)
        out = bytes(buf[:n])
        del buf[:n]
        return out

    @staticmethod
    def _send(sock, data, opcode):
        mask = os.urandom(4)
        n = len(data)
        head = bytearray([0x80 | opcode])
        if n < 126: head.append(0x80 | n)
        elif n < 65536: head.append(0x80 | 126); head.extend(struct.pack("!H", n))
        else: head.append(0x80 | 127); head.extend(struct.pack("!Q", n))
        head.extend(mask)
        sock.sendall(bytes(head) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _mesaj(self, sock, buf):
        parca = bytearray()
        while True:
            b1, b2 = self._recv_exact(sock, buf, 2)
            fin = b1 & 0x80
            op = b1 & 0x0F
            ln = b2 & 0x7F
            if ln == 126: ln = struct.unpack("!H", self._recv_exact(sock, buf, 2))[0]
            elif ln == 127: ln = struct.unpack("!Q", self._recv_exact(sock, buf, 8))[0]
            if b2 & 0x80: self._recv_exact(sock, buf, 4)
            payload = self._recv_exact(sock, buf, ln)
            if op == 0x8: raise EOFError("WS close")
            if op == 0x9:
                self._send(sock, payload, 0xA)
                continue
            if op == 0xA: continue
            if op in (0x1, 0x2): parca = bytearray(payload)
            elif op == 0x0: parca.extend(payload)
            if fin: return bytes(parca)

    def _baglan(self):
        host, port, path = self.adresler[self.idx % len(self.adresler)]
        ctx = ssl.create_default_context()
        sock = socket.create_connection((host, port), timeout=15)
        ssock = ctx.wrap_socket(sock, server_hostname=host)
        ssock.settimeout(45)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nUser-Agent: Mozilla/5.0\r\n\r\n")
        ssock.sendall(req.encode("ascii"))
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = ssock.recv(4096)
            if not chunk: raise EOFError("handshake kapandı")
            buf += chunk
        ilk = buf.split(b"\r\n", 1)[0].decode("utf-8", "ignore")
        if " 101" not in ilk:
            ssock.close()
            raise RuntimeError(f"handshake reddedildi: {ilk}")
        return ssock, bytearray(buf.split(b"\r\n\r\n", 1)[1])

    def calis(self):
        while True:
            ssock = None
            try:
                ssock, buf = self._baglan()
                self.bagli = True
                self.son_hata = ""
                self.son = time.time()
                log("WS", f"{self.ad} akışı bağlandı.")
                while True:
                    ham = self._mesaj(ssock, buf)
                    arr = json.loads(ham.decode("utf-8"))
                    if isinstance(arr, dict) and "data" in arr: arr = arr["data"]
                    if not isinstance(arr, list): continue
                    now = time.time()
                    for it in arr:
                        try:
                            self.veri[it["s"]] = (float(it["c"]), float(it["o"]), float(it["q"]), now)
                        except Exception:
                            continue
                    self.son = now
                    if self.dinleyici:
                        try: self.dinleyici(self, arr, now)
                        except Exception as e: log("WS", f"dinleyici hata: {e}")
            except Exception as e:
                self.bagli = False
                self.son_hata = f"{type(e).__name__}: {e}"[:120]
                log("WS", f"{self.ad} akışı koptu: {self.son_hata}")
                self.idx += 1
                time.sleep(5)
            finally:
                try:
                    if ssock: ssock.close()
                except Exception:
                    pass

FUT = WSFeed("Futures", [("fstream.binance.com", 443, "/market/ws/!miniTicker@arr"),
                         ("fstream.binance.com", 443, "/ws/!miniTicker@arr")])
SPOT = WSFeed("Spot", [("stream.binance.com", 9443, "/ws/!miniTicker@arr"),
                       ("data-stream.binance.com", 9443, "/ws/!miniTicker@arr")])

def besleme_taze(f):
    return f.bagli and time.time() - f.son < 25

def aktif_besleme():
    if besleme_taze(FUT): return FUT
    if besleme_taze(SPOT): return SPOT
    return None

def ticker_listesi(futures):
    """REST ticker yerine canlı akıştan aynı formatta liste döner. Akış yoksa None."""
    sirali = (FUT, SPOT) if futures else (SPOT, FUT)
    now = time.time()
    for f in sirali:
        if besleme_taze(f) and len(f.veri) > 50:
            out = []
            for s, (c, o, q, t) in list(f.veri.items()):
                if now - t > 10800 or o <= 0: continue
                if s in YASAKLI_HISSELER or dislanmis(s): continue
                out.append({"symbol": s, "lastPrice": c, "quoteVolume": q, "priceChangePercent": (c - o) / o * 100})
            return out
    return None

# =====================================================
# PNG MUM GRAFİĞİ (kütüphanesiz)
# =====================================================
FONT_5X7 = {
    "0": [0x3E, 0x51, 0x49, 0x45, 0x3E], "1": [0x00, 0x42, 0x7F, 0x40, 0x00],
    "2": [0x42, 0x61, 0x51, 0x49, 0x46], "3": [0x21, 0x41, 0x45, 0x4B, 0x31],
    "4": [0x18, 0x14, 0x12, 0x7F, 0x10], "5": [0x27, 0x45, 0x45, 0x45, 0x39],
    "6": [0x3C, 0x4A, 0x49, 0x49, 0x30], "7": [0x01, 0x71, 0x09, 0x05, 0x03],
    "8": [0x36, 0x49, 0x49, 0x49, 0x36], "9": [0x06, 0x49, 0x49, 0x29, 0x1E],
    "A": [0x7E, 0x11, 0x11, 0x11, 0x7E], "B": [0x7F, 0x49, 0x49, 0x49, 0x36],
    "C": [0x3E, 0x41, 0x41, 0x41, 0x22], "D": [0x7F, 0x41, 0x41, 0x22, 0x1C],
    "E": [0x7F, 0x49, 0x49, 0x49, 0x41], "F": [0x7F, 0x09, 0x09, 0x09, 0x01],
    "G": [0x3E, 0x41, 0x49, 0x49, 0x7A], "H": [0x7F, 0x08, 0x08, 0x08, 0x7F],
    "I": [0x00, 0x41, 0x7F, 0x41, 0x00], "J": [0x20, 0x40, 0x41, 0x3F, 0x01],
    "K": [0x7F, 0x08, 0x14, 0x22, 0x41], "L": [0x7F, 0x40, 0x40, 0x40, 0x40],
    "M": [0x7F, 0x02, 0x0C, 0x02, 0x7F], "N": [0x7F, 0x04, 0x08, 0x10, 0x7F],
    "O": [0x3E, 0x41, 0x41, 0x41, 0x3E], "P": [0x7F, 0x09, 0x09, 0x09, 0x06],
    "Q": [0x3E, 0x41, 0x51, 0x21, 0x5E], "R": [0x7F, 0x09, 0x19, 0x29, 0x46],
    "S": [0x46, 0x49, 0x49, 0x49, 0x31], "T": [0x01, 0x01, 0x7F, 0x01, 0x01],
    "U": [0x3F, 0x40, 0x40, 0x40, 0x3F], "V": [0x1F, 0x20, 0x40, 0x20, 0x1F],
    "W": [0x3F, 0x40, 0x38, 0x40, 0x3F], "X": [0x63, 0x14, 0x08, 0x14, 0x63],
    "Y": [0x07, 0x08, 0x70, 0x08, 0x07], "Z": [0x61, 0x51, 0x49, 0x45, 0x43],
    ".": [0x00, 0x60, 0x60, 0x00, 0x00], ",": [0x00, 0x50, 0x30, 0x00, 0x00],
    ":": [0x00, 0x36, 0x36, 0x00, 0x00], "-": [0x08, 0x08, 0x08, 0x08, 0x08],
    "+": [0x08, 0x08, 0x3E, 0x08, 0x08], "/": [0x20, 0x10, 0x08, 0x04, 0x02],
    "%": [0x23, 0x13, 0x08, 0x64, 0x62], "$": [0x24, 0x2A, 0x7F, 0x2A, 0x12],
    " ": [0x00, 0x00, 0x00, 0x00, 0x00], "|": [0x00, 0x00, 0x7F, 0x00, 0x00],
}

def render_png(baslik, candles, live_price, start_price):
    W, H = 860, 440
    BG = (9, 13, 22); GRID = (28, 38, 56)
    BULL = (16, 185, 129); BEAR = (244, 63, 94)
    TEXT = (226, 232, 240); MUTED = (148, 163, 184)
    START_COL = (245, 158, 11); LIVE_COL = (56, 189, 248)

    bars = [dict(b) for b in candles[-75:]]
    if not bars: return b""
    last = bars[-1]
    last["close"] = live_price
    last["high"] = max(last["high"], live_price)
    last["low"] = min(last["low"], live_price)

    buf = bytearray(bytes(BG) * (W * H))

    def set_px(x, y, col):
        if 0 <= x < W and 0 <= y < H:
            i = (y * W + x) * 3
            buf[i] = col[0]; buf[i + 1] = col[1]; buf[i + 2] = col[2]

    def fill_rect(x0, y0, x1, y1, col):
        x0, x1 = max(0, min(x0, x1)), min(W - 1, max(x0, x1))
        y0, y1 = max(0, min(y0, y1)), min(H - 1, max(y0, y1))
        if x1 < x0 or y1 < y0: return
        row = bytes(col) * (x1 - x0 + 1)
        for y in range(y0, y1 + 1):
            st = (y * W + x0) * 3
            buf[st:st + len(row)] = row

    def draw_line(x0, y0, x1, y1, col, dashed=False):
        dx = abs(x1 - x0); dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1; sy = 1 if y0 < y1 else -1
        err = dx + dy; n = 0
        while True:
            if not dashed or (n % 8) < 5: set_px(x0, y0, col)
            if x0 == x1 and y0 == y1: break
            e2 = 2 * err
            if e2 >= dy: err += dy; x0 += sx
            if e2 <= dx: err += dx; y0 += sy
            n += 1

    def draw_text(x, y, text, col, scale=1):
        cx = x
        for ch in text.upper():
            g = FONT_5X7.get(ch, FONT_5X7[" "])
            for gx in range(5):
                for gy in range(7):
                    if g[gx] & (1 << gy):
                        fill_rect(cx + gx * scale, y + gy * scale, cx + gx * scale + scale - 1, y + gy * scale + scale - 1, col)
            cx += 6 * scale

    pad_l, pad_r, pad_t, pad_b = 18, 118, 42, 24
    vol_h, gap = 62, 12
    plot_w = W - pad_l - pad_r
    price_h = H - pad_t - pad_b - vol_h - gap

    allp = [b["high"] for b in bars] + [b["low"] for b in bars] + [live_price]
    if start_price and start_price > 0: allp.append(start_price)
    lo_p = min(allp) * 0.994
    hi_p = max(allp) * 1.006
    rng_p = max(hi_p - lo_p, lo_p * 0.005)
    max_vol = max([b["volume"] for b in bars] + [1.0])

    def y_of(p): return int(pad_t + ((hi_p - p) / rng_p) * price_h)

    draw_text(pad_l, 14, baslik, TEXT, scale=2)
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        gy = int(pad_t + price_h * frac)
        draw_line(pad_l, gy, pad_l + plot_w, gy, GRID, dashed=True)
        draw_text(pad_l + plot_w + 8, gy - 3, akilli_fiyat(hi_p - rng_p * frac), MUTED)

    if start_price and start_price > 0:
        sy = y_of(start_price)
        if pad_t <= sy <= pad_t + price_h:
            draw_line(pad_l, sy, pad_l + plot_w, sy, START_COL, dashed=True)
            draw_text(pad_l + plot_w + 8, sy - 3, "BAS " + akilli_fiyat(start_price), START_COL)

    n = len(bars)
    step = plot_w / max(1, n)
    bw = max(2, int(step * 0.62))
    vol_top = pad_t + price_h + gap
    for i, b in enumerate(bars):
        cx = int(pad_l + (i + 0.5) * step)
        col = BULL if b["close"] >= b["open"] else BEAR
        yo, yc, yh, yl = y_of(b["open"]), y_of(b["close"]), y_of(b["high"]), y_of(b["low"])
        draw_line(cx, yh, cx, yl, col)
        fill_rect(cx - bw // 2, min(yo, yc), cx + bw // 2, max(min(yo, yc) + 1, max(yo, yc)), col)
        vh = max(1, int((b["volume"] / max_vol) * vol_h))
        fill_rect(cx - bw // 2, vol_top + vol_h - vh, cx + bw // 2, vol_top + vol_h, (col[0] // 2, col[1] // 2, col[2] // 2))

    ly = y_of(live_price)
    draw_line(pad_l, ly, pad_l + plot_w, ly, LIVE_COL, dashed=True)
    fill_rect(pad_l + plot_w + 4, ly - 7, W - 4, ly + 7, (2, 132, 199))
    draw_text(pad_l + plot_w + 8, ly - 3, akilli_fiyat(live_price), (248, 250, 252))

    raw = bytearray()
    stride = W * 3
    for y in range(H):
        raw.append(0)
        raw.extend(buf[y * stride:(y + 1) * stride])
    comp = zlib.compress(bytes(raw), 6)

    def chunk(tag, payload):
        return struct.pack("!I", len(payload)) + tag + payload + struct.pack("!I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", W, H, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", comp) + chunk(b"IEND", b""))

# =====================================================
# MOTOR 4+5: CANLI PUMP / DUMP AVCISI (olay tabanlı)
# Fiyatı canlı akıştan izler, sadece tetiklenince 1 dakikalık mumla doğrular.
# =====================================================
PUMP_MIN_HACIM = 3_000_000                  # 24s USDT hacmi alt sınırı
PUMP_PENCERELER = ((300, 3.0), (900, 5.0))  # (saniye, minimum % hareket)
PUMP_MIN_CARPAN = 2.0                       # son 5 dk hacmi / önceki 5 dk ortalaması
PUMP_MIN_SON5 = 25_000                      # son 5 dk en az bu kadar USDT hacim

pump_gecmis = {}
pump_son_ornek = {}
pump_cd = {}
pump_aktif = set()
pump_kilit = threading.Lock()
pump_sem = threading.Semaphore(3)
pump_durum = {"feed": None}
toplu = {"t": [], "sessiz": 0.0}
sayac = {"PUMP": 0, "DUMP": 0}

def toplu_kontrol(now):
    """Piyasa genelinde aynı anda çok sinyal gelirse (örn. BTC çöküşü) tek uyarıya indirir."""
    if now < toplu["sessiz"]: return True
    toplu["t"] = [x for x in toplu["t"] if now - x < 120] + [now]
    if len(toplu["t"]) > 6:
        toplu["sessiz"] = now + 300
        tg(C, "⚠️ <b>Piyasa genelinde sert hareket</b>\nKısa sürede çok sayıda coin aynı yönde hareket etti (BTC/genel piyasa kaynaklı olabilir). Tekil sinyaller 5 dakika durduruldu.")
        return True
    return False

def pump_isle(feed, arr, now):
    if aktif_besleme() is not feed: return
    if pump_durum["feed"] is not feed:
        pump_durum["feed"] = feed
        pump_gecmis.clear(); pump_son_ornek.clear()
        return
    for it in arr:
        try:
            s = it["s"]
            if not s.endswith("USDT") or s in YASAKLI_HISSELER or dislanmis(s): continue
            q = float(it["q"])
            if q < PUMP_MIN_HACIM: continue
            if now - pump_son_ornek.get(s, 0) < 5: continue
            p = float(it["c"]); o = float(it["o"])
            if p <= 0: continue
            pump_son_ornek[s] = now
            h = pump_gecmis.setdefault(s, [])
            h.append((now, p))
            kes = 0
            while kes < len(h) and h[kes][0] < now - 1200: kes += 1
            if kes: del h[:kes]
            if len(h) < 8 or h[0][0] > now - 90: continue
            pump_kontrol(feed, s, p, o, q, h, now)
        except Exception:
            continue

def pump_kontrol(feed, s, p, o, q, h, now):
    for pencere, esik in PUMP_PENCERELER:
        pts = [x[1] for x in h if x[0] >= now - pencere]
        if len(pts) < 4: continue
        lo, hi = min(pts), max(pts)
        if lo <= 0 or hi <= 0: continue
        up = (p - lo) / lo * 100
        down = (hi - p) / hi * 100
        if up >= esik and p >= hi * 0.985: yon = "PUMP"
        elif down >= esik and p <= lo * 1.015: yon = "DUMP"
        else: continue
        key = (s, yon)
        with pump_kilit:
            if key in pump_aktif or now < pump_cd.get(key, 0): return
            pump_aktif.add(key)
        deg24 = (p - o) / o * 100 if o > 0 else 0.0
        threading.Thread(target=pump_dogrula, args=(feed.ad, s, yon, p, pencere, esik, q, deg24), daemon=True).start()
        return

def pump_dogrula(feed_ad, s, yon, p, pencere, esik, q24, deg24):
    key = (s, yon)
    bitis = time.time() + 300
    alindi = pump_sem.acquire(timeout=30)
    try:
        if not alindi: return
        fut = feed_ad == "Futures"
        url = (f"{FAPI}/fapi/v1/klines?symbol={s}&interval=1m&limit=45" if fut
               else f"{BASE}/api/v3/klines?symbol={s}&interval=1m&limit=45")
        kl = gj(url)
        if not isinstance(kl, list) or len(kl) < 25:
            log("PUMP", f"{s} {yon}: mum verisi alınamadı, iptal")
            return
        zirveler = [float(k[2]) for k in kl]
        dipler = [float(k[3]) for k in kl]
        hacimler = [float(k[7]) for k in kl]
        up = yon == "PUMP"
        n_son = pencere // 60 + 1
        if up:
            ref = min(dipler[-n_son:]); kp = (p - ref) / ref * 100
        else:
            ref = max(zirveler[-n_son:]); kp = (ref - p) / ref * 100
        if kp < esik * 0.8:
            log("PUMP", f"{s} {yon}: mum teyidi yok ({kp:.2f}%), iptal")
            return
        son5 = sum(hacimler[-5:])
        onceki = hacimler[:-5]
        ort5 = (sum(onceki) / len(onceki)) * 5 if onceki else 0
        if ort5 <= 0: return
        carpan = son5 / ort5
        if carpan < PUMP_MIN_CARPAN or son5 < PUMP_MIN_SON5:
            log("PUMP", f"{s} {yon}: hacim teyidi yok ({carpan:.1f}x), iptal")
            return

        bitis = time.time() + 3600
        sayac[yon] += 1
        if toplu_kontrol(time.time()): return

        trap = trap_radar_check(s) if (up and fut) else ""
        emoji = "⚡" if up else "🚨"
        isaret = "+" if up else "-"
        tur = "PUMP" if up else "DUMP"
        kaynak = feed_ad.upper()
        msg = (f"{emoji} <b>{kaynak} ANLIK {tur} DEDEKTÖRÜ — #{s}</b>\n\n"
               f"{'📈' if up else '📉'} Değişim: <b>{isaret}{kp:.2f}%</b> ({pencere // 60} dk içinde!)\n"
               f"💰 Başlangıç: {akilli_fiyat(ref)} ➡️ Anlık: <b>{akilli_fiyat(p)}</b>\n"
               f"📊 Hacim: Son 5 dk ortalamanın <b>{carpan:.1f}x</b> katı ({son5 / 1000:.0f}K USDT)\n"
               f"💧 24s Parite Hacmi: {q24:,.0f} (USDT) | 24s: {deg24:+.1f}%\n")
        if trap: msg += f"\n{trap}\n"
        msg += "\n🔒 Aynı coin için 1 saat tekrar bildirim yok."

        mumlar = [{"openTime": int(k[0]) // 1000, "open": float(k[1]), "high": float(k[2]),
                   "low": float(k[3]), "close": float(k[4]), "volume": float(k[7])} for k in kl]
        try:
            png = render_png(f"{s} | 1DK CANLI | {kaynak} {tur} | ANLIK ${akilli_fiyat(p)}", mumlar, p, ref)
        except Exception as e:
            log("PUMP", f"grafik hatası: {e}")
            png = b""
        if tg_foto(png, msg):
            log("PUMP", f"🔥 SİNYAL: {s} ({yon} {isaret}{kp:.2f}%)")
    except Exception as e:
        log("PUMP", f"{s} doğrulama hatası: {e}")
    finally:
        if alindi: pump_sem.release()
        with pump_kilit:
            pump_aktif.discard(key)
            pump_cd[key] = bitis

# =====================================================
# MOTOR 6: FORMASYON (MSS) - FİLTRELİ
# =====================================================
def formasyon_loop():
    log("FORMASYON", "Teyitli Formasyon ve Likidite Avcısı Başladı.")
    time.sleep(60)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in YASAKLI_HISSELER and float(t.get('quoteVolume', 0)) >= MIN_HACIM_FORMASYON]:
                if symbol in son_formasyon_sinyalleri and time.time() - son_formasyon_sinyalleri[symbol] < 86400: continue

                klines_5m = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=5m&limit=100")
                klines_15m = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=15m&limit=50")

                if not isinstance(klines_5m, list) or not isinstance(klines_15m, list): continue
                if len(klines_5m) < 100 or len(klines_15m) < 50: continue

                highs = [float(k[2]) for k in klines_5m]
                lows = [float(k[3]) for k in klines_5m]
                closes = [float(k[4]) for k in klines_5m]
                vols = [float(k[7]) for k in klines_5m]

                closes_15m = [float(k[4]) for k in klines_15m]
                ema11 = calculate_ema(closes_15m, 11)[-1]
                ema21 = calculate_ema(closes_15m, 21)[-1]
                rsi = calculate_rsi(closes_15m)

                current_price = closes[-1]
                avg_vol = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 1
                current_vol = vols[-1]
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
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in YASAKLI_HISSELER and float(t.get('quoteVolume', 0)) >= MIN_HACIM_EMA_RIBBON]:
                if symbol in son_ema_ribbon_sinyalleri and time.time() - son_ema_ribbon_sinyalleri[symbol] < 86400: continue
                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1d&limit=200")
                if not isinstance(klines, list) or len(klines) < 200: continue
                closes = [float(k[4]) for k in klines[:-1]]
                vols = [float(k[7]) for k in klines[:-1]]
                curr = closes[-1]
                curr_vol = vols[-1]
                avg_vol = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 1
                if curr_vol <= (avg_vol * 1.2): continue

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
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in YASAKLI_HISSELER and float(t.get('quoteVolume', 0)) >= MIN_HACIM_MACD]:
                if symbol in son_macd_sinyalleri and time.time() - son_macd_sinyalleri[symbol] < 14400: continue
                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1d&limit=100")
                if not isinstance(klines, list) or len(klines) < 80: continue
                closes = [float(k[4]) for k in klines[:-1]]
                vols = [float(k[7]) for k in klines[:-1]]
                curr_price = closes[-1]
                curr_vol = vols[-1]
                avg_vol = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 1
                if curr_vol <= (avg_vol * 1.1): continue

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
# MOTOR 9: TARİHİ ZİRVE / DİP KIRILIMI (ATH / ATL)
# =====================================================
def ath_atl_loop():
    log("ATH_ATL", "Tarihi Zirve/Dip Kırılım Motoru Başladı.")
    time.sleep(135)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in YASAKLI_HISSELER and float(t.get('quoteVolume', 0)) >= MIN_HACIM_ATH_ATL]

            for symbol in adaylar:
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
                ath = max(highs[:-1])   # son mum hariç (aksi halde kırılım asla oluşmaz)
                atl = min(lows[:-1])
                avg_vol = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else 1
                is_vol_teyit = current_vol > (avg_vol * 1.2)

                if not is_vol_teyit: continue

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
# MOTOR 10: ICHIMOKU BULUTU
# =====================================================
def ichimoku_loop():
    log("ICHIMOKU", "Zırhlı Ichimoku Bulut Motoru Başladı.")
    time.sleep(75)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in YASAKLI_HISSELER and float(t.get('quoteVolume', 0)) >= MIN_HACIM_ICHIMOKU]

            for symbol in adaylar:
                if symbol in son_ichimoku_sinyalleri and time.time() - son_ichimoku_sinyalleri[symbol] < 14400: continue

                klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1h&limit=100")
                if not isinstance(klines, list) or len(klines) < 80: continue

                highs = [float(k[2]) for k in klines]
                lows = [float(k[3]) for k in klines]
                closes = [float(k[4]) for k in klines]
                vols = [float(k[7]) for k in klines]

                curr_close = closes[-2]
                prev_close = closes[-3]

                avg_vol = sum(vols[-21:-1]) / 20
                curr_vol = vols[-2]
                is_vol_teyit = curr_vol > avg_vol

                if not is_vol_teyit: continue

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
# MOTOR 11: GRAFİK FORMASYONLARI (CHART PATTERNS)
# =====================================================
def chart_patterns_loop():
    log("PATTERN", "Grafik Formasyon Avcısı Başladı (İkili Tepe/Dip, OBO).")
    time.sleep(150)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue

            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in YASAKLI_HISSELER and float(t.get('quoteVolume', 0)) >= MIN_HACIM_PATTERN]

            for symbol in adaylar:
                if symbol in son_chart_pattern_sinyalleri and time.time() - son_chart_pattern_sinyalleri[symbol] < 14400: continue

                for tf in ['4h', '1d']:
                    klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval={tf}&limit=100")
                    if not isinstance(klines, list) or len(klines) < 50: continue

                    highs = [float(k[2]) for k in klines]
                    lows = [float(k[3]) for k in klines]
                    closes = [float(k[4]) for k in klines]
                    vols = [float(k[7]) for k in klines]

                    curr_close = closes[-2]
                    curr_vol = vols[-2]
                    avg_vol = sum(vols[-22:-2]) / 20

                    if curr_vol < avg_vol * 1.2: continue

                    p_highs, p_lows = find_pivots(highs, lows)

                    sinyal = None; formasyon_adi = ""; yon = ""; emoji = ""; hedef = 0; stop = 0; neckline = 0

                    if len(p_highs) >= 2:
                        p1_idx, p1_price = p_highs[-2]
                        p2_idx, p2_price = p_highs[-1]
                        if 5 <= (p2_idx - p1_idx) <= 30 and abs(p1_price - p2_price) / p1_price <= 0.02:
                            neckline = min(lows[p1_idx:p2_idx+1])
                            if curr_close < neckline * 0.99:
                                sinyal = True; formasyon_adi = "İkili Tepe (M)"; yon = "SHORT"; emoji = "🔻"
                                stop = max(p1_price, p2_price) * 1.02
                                hedef = neckline - (max(p1_price, p2_price) - neckline)

                    if not sinyal and len(p_lows) >= 2:
                        p1_idx, p1_price = p_lows[-2]
                        p2_idx, p2_price = p_lows[-1]
                        if 5 <= (p2_idx - p1_idx) <= 30 and abs(p1_price - p2_price) / p1_price <= 0.02:
                            neckline = max(highs[p1_idx:p2_idx+1])
                            if curr_close > neckline * 1.01:
                                sinyal = True; formasyon_adi = "İkili Dip (W)"; yon = "LONG"; emoji = "🚀"
                                stop = min(p1_price, p2_price) * 0.98
                                hedef = neckline + (neckline - min(p1_price, p2_price))

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
                        if "Omuz" in formasyon_adi: aciklama = "Trendin tepe noktasında oluşan klasik dönüş formasyonu. Boyun çizgisi kırıldı, satıcılar kontrolü ele aldı."
                        if "Dip" in formasyon_adi: aciklama = "Dipte oluşan çift dip formasyonu. Satıcılar tükendi, alıcılar hacimle boyun çizgisini yukarı kırdı."

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
# MOTOR 12: TOP GAINERS SHORT AVCISI
# =====================================================
def top_gainers_short_loop():
    log("SHORT_GAINERS", "Top Gainers Short Avcısı Başladı (Kâr Realizasyonu).")
    time.sleep(180)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue

            temiz_tickers = [t for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in YASAKLI_HISSELER]
            temiz_tickers.sort(key=lambda x: float(x['priceChangePercent']), reverse=True)
            top_gainers = temiz_tickers[:5]

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
# GÖZETMEN, DURUM RAPORU, KEEP-ALIVE
# =====================================================
def gozetmen(fn):
    while True:
        try:
            fn()
            return
        except Exception as e:
            log("GÖZETMEN", f"{getattr(fn, '__name__', 'motor')} çöktü, yeniden başlatılıyor: {e}")
            time.sleep(30)

def rest_durumu():
    now = time.time()
    parca = []
    for ad, anahtar in (("Futures", "fapi"), ("Spot", "spot")):
        kalan = ban[anahtar] - now
        if kalan > 0: parca.append(f"{ad} ban ({int(kalan // 60) + 1} dk)")
    return ", ".join(parca) if parca else "normal"

def durum_metni(baslangic):
    now = time.time()
    satir = []
    for f in (FUT, SPOT):
        if besleme_taze(f): satir.append(f"✅ {f.ad} ({len(f.veri)} coin)")
        else: satir.append(f"❌ {f.ad}")
    sa = (now - baslangic) / 3600
    return (f"🤖 <b>Bot çalışıyor</b> ({simdi().strftime('%d.%m %H:%M')})\n"
            f"📡 Canlı akış: {' | '.join(satir)}\n"
            f"🌐 REST: {rest_durumu()}\n"
            f"📊 Son 6 saat: {sayac['PUMP']} pump, {sayac['DUMP']} dump\n"
            f"⏱ Çalışma süresi: {sa:.1f} saat")

def durum_loop():
    baslangic = time.time()
    son_durum = time.time()
    son_uyari_ws = 0.0
    while True:
        time.sleep(30)
        try:
            now = time.time()
            if now - baslangic > 300 and now - son_uyari_ws > 1800:
                kopuk = [f for f in (FUT, SPOT) if not (f.bagli and now - f.son < 120)]
                if kopuk:
                    son_uyari_ws = now
                    tg(C, "⚠️ <b>Canlı akış kesildi</b>\n" + "\n".join(f"{f.ad}: {f.son_hata or 'veri gelmiyor'}" for f in kopuk)
                       + "\nBot yeniden bağlanmayı deniyor.")
            if now - son_durum >= 21600:
                son_durum = now
                tg(C, durum_metni(baslangic))
                sayac["PUMP"] = 0; sayac["DUMP"] = 0
        except Exception as e:
            log("DURUM", f"Hata: {e}")

def keepalive_loop():
    url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    if not url: return
    while True:
        time.sleep(600)
        try:
            urllib.request.urlopen(url, timeout=15).read()
        except Exception:
            pass

# =====================================================
# BAŞLAT
# =====================================================
if __name__ == "__main__":
    BASLANGIC = time.time()
    # Render portu hemen açılsın
    threading.Thread(target=run_web_server, daemon=True).start()

    FUT.dinleyici = pump_isle
    SPOT.dinleyici = pump_isle
    for f in (FUT, SPOT):
        threading.Thread(target=gozetmen, args=(f.calis,), daemon=True).start()

    motorlar = [
        top_gainers_loop, pump_winner_loop, chartist_loop, formasyon_loop,
        ema_ribbon_loop, macd_loop, ath_atl_loop, ichimoku_loop,
        chart_patterns_loop, top_gainers_short_loop, durum_loop, keepalive_loop,
    ]
    for m in motorlar:
        threading.Thread(target=gozetmen, args=(m,), daemon=True).start()

    # Açılış raporu: akışlar bağlanana kadar kısa bekle
    time.sleep(10)
    satirlar = []
    for f in (FUT, SPOT):
        if besleme_taze(f): satirlar.append(f"✅ {f.ad} canlı akış ({len(f.veri)} coin)")
        else: satirlar.append(f"❌ {f.ad} canlı akış: {f.son_hata or 'bağlanıyor'}")
    if aktif_besleme():
        bilgi = "Pump/Dump takibi canlı akıştan çalışıyor, Binance REST'e ihtiyaç duymuyor."
    else:
        ok, durum = baglanti_testi("fapi")
        if not ok:
            ok2, durum2 = baglanti_testi("spot")
            durum += "\n" + durum2
            ok = ok2
        satirlar.append(durum)
        bilgi = "Canlı akış yok, REST ile devam ediliyor." if ok else "Binance verisi alınamıyor, ban bitene kadar sinyal gelmez."
    if tg(C, f"🤖 <b>Bot başladı</b> ({simdi().strftime('%d.%m %H:%M')})\n✅ Telegram: bağlı\n" + "\n".join(satirlar) + f"\n\n{bilgi}"):
        log("SİSTEM", "Telegram bağlantısı OK.")
    else:
        log("SİSTEM", "⚠️ Telegram mesajı gönderilemedi! BOT_TOKEN / CHAT_ID kontrol et.")

    while True:
        time.sleep(3600)

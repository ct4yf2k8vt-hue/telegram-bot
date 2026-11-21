
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SPOT AKIS TERMINALI - RENDER
Python 3 / Zero Dependency

- Binance Spot WebSocket miniTicker
- 0-7 dakika anlik PUMP tespiti
- Tekil coin kilidi
- Telegram mesaj + PNG grafik
- Gunluk MA200
- 3 Gunluk MA200
- 21:00 Gunun Kazanani
- 23:00 Top 24 Saat
- 60 saniye Binance duyurulari
- Otomatik AL-SAT YOK
"""

import base64
import datetime as dt
import http.server
import json
import math
import os
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib


# =============================================================================
# AYARLAR
# =============================================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "1623907197").strip()
PORT = int(os.environ.get("PORT", "10000"))
STATE_FILE = os.environ.get("STATE_FILE", "/tmp/spot_bot_state.json")

PUMP_REPEAT_HOURS = float(os.environ.get("PUMP_REPEAT_HOURS", "0"))
PUMP_WINDOW_SECONDS = int(os.environ.get("PUMP_WINDOW_SECONDS", "420"))
PUMP_MIN_CHANGE = float(os.environ.get("PUMP_MIN_CHANGE", "4.5"))
PUMP_MIN_QUOTE_VOLUME = float(
    os.environ.get("PUMP_MIN_QUOTE_VOLUME", "150000")
)
PUMP_MIN_TICKS = int(os.environ.get("PUMP_MIN_TICKS", "4"))
PUMP_CONFIRM_SECONDS = int(os.environ.get("PUMP_CONFIRM_SECONDS", "3"))

MAX_TRACKED_PAIRS = int(os.environ.get("MAX_TRACKED_PAIRS", "2500"))
MAX_SIGNALS = 500
NEWS_INTERVAL = 60

BINANCE_WS_HOST = "stream.binance.com"
BINANCE_WS_PORT = 9443
BINANCE_WS_PATH = "/ws/!miniTicker@arr"
BINANCE_REST = "https://api.binance.com"

EXCLUDED_PREFIXES = (
    "USDC", "FDUSD", "TUSD", "USDP",
    "EUR", "USDE", "USD1"
)

EXCLUDED_SUFFIXES = (
    "UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT"
)

TR_TZ = dt.timezone(dt.timedelta(hours=3))

# MA200 AYARLARI
MA200_CACHE_SECONDS = 21600       # 6 saat
MA200_REQUEST_INTERVAL = 1.2      # REST istekleri arasi bekleme

state_lock = threading.RLock()
ma200_lock = threading.Lock()

tickers = {}
price_history = {}
signals_log = []

news_seen_ids = set()
news_seeded = False

ws_connected = False
last_ws_message = 0.0
rest_backoff_until = 0.0
last_ma200_request = 0.0

ma200_cache = {}

locks = {
    "pump": {},
    "reports": {
        "victory_day": "",
        "top24_day": ""
    },
}


# =============================================================================
# GENEL YARDIMCILAR
# =============================================================================

def tr_now():
    return dt.datetime.now(TR_TZ)


def safe_float(value, default=0.0):
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def fmt_price(p):
    p = safe_float(p)

    if p <= 0:
        return "0"

    if p >= 1000:
        return f"{p:,.2f}"

    if p >= 10:
        return f"{p:.3f}".rstrip("0").rstrip(".")

    if p >= 1:
        return f"{p:.4f}".rstrip("0").rstrip(".")

    if p >= 0.01:
        return f"{p:.5f}".rstrip("0").rstrip(".")

    if p >= 0.0001:
        return f"{p:.6f}".rstrip("0").rstrip(".")

    return f"{p:.8f}".rstrip("0").rstrip(".")


def extract_base_quote(symbol):
    for quote in ("USDT", "BTC", "BNB"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[:-len(quote)], quote

    return None, None


def is_excluded(symbol):
    return (
        any(symbol.startswith(p) for p in EXCLUDED_PREFIXES)
        or any(symbol.endswith(s) for s in EXCLUDED_SUFFIXES)
    )


# =============================================================================
# KALICI DURUM
# =============================================================================

def load_state():
    global signals_log

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)

        raw_locks = raw.get("locks", {})

        if isinstance(raw_locks.get("pump"), dict):
            locks["pump"] = raw_locks["pump"]

        if isinstance(raw_locks.get("reports"), dict):
            locks["reports"].update(raw_locks["reports"])

        raw_sigs = raw.get("signals", [])

        if isinstance(raw_sigs, list):
            signals_log = [
                s for s in raw_sigs
                if isinstance(s, dict)
                and s.get("engine") == "PUMP"
            ][-MAX_SIGNALS:]

        raw_news = raw.get("news_seen_ids", [])

        if isinstance(raw_news, list):
            news_seen_ids.update(
                str(x) for x in raw_news[-500:]
            )

    except FileNotFoundError:
        pass

    except Exception as exc:
        print(f"[STATE load] {exc}")


def save_state():
    try:
        with state_lock:
            payload = {
                "locks": locks,
                "signals": signals_log[-MAX_SIGNALS:],
                "news_seen_ids": list(news_seen_ids)[-500:],
            }

            encoded = json.dumps(
                payload,
                ensure_ascii=False
            )

        tmp = STATE_FILE + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            f.write(encoded)

        os.replace(tmp, STATE_FILE)

    except Exception as exc:
        print(f"[STATE save] {exc}")


# =============================================================================
# HTTP / BINANCE REST
# =============================================================================

def http_get_json(url, timeout=8):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 SpotAkisTerminal/2.0",
            "Accept": "application/json"
        },
    )

    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(
            response.read().decode("utf-8")
        )


def binance_klines(symbol, limit=60):
    """Grafik icin opsiyonel 1 dakikalik mum verisi."""

    global rest_backoff_until

    if time.time() < rest_backoff_until:
        return []

    query = urllib.parse.urlencode({
        "symbol": symbol,
        "interval": "1m",
        "limit": min(100, max(5, limit))
    })

    try:
        data = http_get_json(
            f"{BINANCE_REST}/api/v3/klines?{query}",
            timeout=5
        )

        if isinstance(data, list):
            return [
                {
                    "t": int(k[0]) // 1000,
                    "o": safe_float(k[1]),
                    "h": safe_float(k[2]),
                    "l": safe_float(k[3]),
                    "c": safe_float(k[4]),
                    "v": safe_float(k[5])
                }
                for k in data
            ]

    except urllib.error.HTTPError as exc:

        if exc.code in (418, 429):
            retry = 900

            try:
                retry = max(
                    60,
                    int(exc.headers.get("Retry-After", "900"))
                )
            except Exception:
                pass

            rest_backoff_until = time.time() + retry

            print(
                f"[BINANCE REST] HTTP {exc.code}; "
                f"{retry} sn bekleme"
            )

    except Exception as exc:
        print(f"[BINANCE klines] {symbol}: {exc}")

    return []


# =============================================================================
# GUNLUK VE 3 GUNLUK MA200
# =============================================================================

def binance_ma200(symbol, interval):
    """
    Binance Spot Kline verisinden SMA200 hesaplar.

    1d = Gunluk MA200
    3d = 3 Gunluk MA200

    Yalnizca tamamlanmis mumlar kullanilir.
    201 mum istenir; boylece devam eden mum
    disarida birakildiginda 200 mum kalir.
    """

    global rest_backoff_until
    global last_ma200_request

    cache_key = f"{symbol}_{interval}"
    now = time.time()

    cached = ma200_cache.get(cache_key)

    if cached and now - cached["time"] < MA200_CACHE_SECONDS:
        return cached["value"]

    with ma200_lock:

        now = time.time()

        cached = ma200_cache.get(cache_key)

        if cached and now - cached["time"] < MA200_CACHE_SECONDS:
            return cached["value"]

        if now < rest_backoff_until:
            return None

        wait = MA200_REQUEST_INTERVAL - (
            now - last_ma200_request
        )

        if wait > 0:
            time.sleep(wait)

        if time.time() < rest_backoff_until:
            return None

        query = urllib.parse.urlencode({
            "symbol": symbol,
            "interval": interval,
            "limit": 201
        })

        try:
            last_ma200_request = time.time()

            data = http_get_json(
                f"{BINANCE_REST}/api/v3/klines?{query}",
                timeout=10
            )

            if not isinstance(data, list):
                return None

            now_ms = int(time.time() * 1000)

            closes = [
                safe_float(k[4])
                for k in data
                if len(k) > 6
                and int(k[6]) < now_ms
                and safe_float(k[4]) > 0
            ]

            if len(closes) < 200:
                print(
                    f"[MA200] {symbol} {interval}: "
                    "200 tamamlanmis mum bulunamadi."
                )
                return None

            ma = sum(closes[-200:]) / 200

            ma200_cache[cache_key] = {
                "value": ma,
                "time": time.time()
            }

            print(
                f"[MA200] {symbol} {interval}: "
                f"{fmt_price(ma)}"
            )

            return ma

        except urllib.error.HTTPError as exc:

            if exc.code in (418, 429):

                try:
                    retry = max(
                        60,
                        int(exc.headers.get("Retry-After", "900"))
                    )
                except Exception:
                    retry = 900

                rest_backoff_until = time.time() + retry

                print(
                    f"[MA200] HTTP {exc.code}; "
                    f"{retry} sn bekleme"
                )

            else:
                print(
                    f"[MA200] {symbol} {interval}: {exc}"
                )

        except Exception as exc:
            print(f"[MA200] {symbol} {interval}: {exc}")

    return None


def get_ma200_status(price, ma):
    if ma is None or ma <= 0:
        return "VERI ALINAMADI"

    if price > ma:
        return "MA200 USTU"

    return "MA200 ALTI"


# =============================================================================
# WEBSOCKET
# =============================================================================

def ws_send_frame(sock, payload, opcode=1):
    mask = os.urandom(4)
    n = len(payload)

    header = bytearray([0x80 | opcode])

    if n < 126:
        header.append(0x80 | n)

    elif n < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", n))

    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", n))

    header.extend(mask)

    masked = bytes(
        b ^ mask[i % 4]
        for i, b in enumerate(payload)
    )

    sock.sendall(bytes(header) + masked)


def ws_recv_exact(sock, buffer, n):
    while len(buffer) < n:
        chunk = sock.recv(max(4096, n - len(buffer)))

        if not chunk:
            raise EOFError(
                "WebSocket baglantisi kapandi"
            )

        buffer.extend(chunk)

    result = bytes(buffer[:n])
    del buffer[:n]

    return result


def ws_read_frame(sock, buffer):
    b1, b2 = ws_recv_exact(sock, buffer, 2)

    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    length = b2 & 0x7F

    if length == 126:
        length = struct.unpack(
            "!H",
            ws_recv_exact(sock, buffer, 2)
        )[0]

    elif length == 127:
        length = struct.unpack(
            "!Q",
            ws_recv_exact(sock, buffer, 8)
        )[0]

    mask = (
        ws_recv_exact(sock, buffer, 4)
        if masked else None
    )

    payload = ws_recv_exact(sock, buffer, length)

    if mask:
        payload = bytes(
            b ^ mask[i % 4]
            for i, b in enumerate(payload)
        )

    return opcode, payload


def connect_binance_ws():
    raw = socket.create_connection(
        (BINANCE_WS_HOST, BINANCE_WS_PORT),
        timeout=15
    )

    sock = ssl.create_default_context().wrap_socket(
        raw,
        server_hostname=BINANCE_WS_HOST
    )

    key = base64.b64encode(
        os.urandom(16)
    ).decode("ascii")

    request = (
        f"GET {BINANCE_WS_PATH} HTTP/1.1\r\n"
        f"Host: {BINANCE_WS_HOST}:{BINANCE_WS_PORT}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )

    sock.sendall(request.encode("ascii"))

    response = bytearray()

    while b"\r\n\r\n" not in response:

        chunk = sock.recv(4096)

        if not chunk:
            raise EOFError(
                "WebSocket handshake EOF"
            )

        response.extend(chunk)

        if len(response) > 16384:
            raise RuntimeError(
                "WebSocket handshake yaniti cok buyuk"
            )

    response_bytes = bytes(response)
    header, remainder = response_bytes.split(
        b"\r\n\r\n", 1
    )

    first_line = header.split(b"\r\n", 1)[0]

    if b" 101 " not in first_line:
        raise RuntimeError(
            f"WebSocket handshake reddedildi: {first_line!r}"
        )

    sock.settimeout(75)

    return sock, bytearray(remainder)


# =============================================================================
# PNG GRAFIK
# =============================================================================

FONT = {
    "0": [62,81,73,69,62],
    "1": [0,66,127,64,0],
    "2": [66,97,81,73,70],
    "3": [33,65,69,75,49],
    "4": [24,20,18,127,16],
    "5": [39,69,69,69,57],
    "6": [60,74,73,73,48],
    "7": [1,113,9,5,3],
    "8": [54,73,73,73,54],
    "9": [6,73,73,41,30],
    "A": [126,17,17,17,126],
    "B": [127,73,73,73,54],
    "C": [62,65,65,65,34],
    "D": [127,65,65,34,28],
    "E": [127,73,73,73,65],
    "F": [127,9,9,9,1],
    "G": [62,65,73,73,122],
    "H": [127,8,8,8,127],
    "I": [0,65,127,65,0],
    "J": [32,64,65,63,1],
    "K": [127,8,20,34,65],
    "L": [127,64,64,64,64],
    "M": [127,2,12,2,127],
    "N": [127,4,8,16,127],
    "O": [62,65,65,65,62],
    "P": [127,9,9,9,6],
    "Q": [62,65,81,33,94],
    "R": [127,9,25,41,70],
    "S": [70,73,73,73,49],
    "T": [1,1,127,1,1],
    "U": [63,64,64,64,63],
    "V": [31,32,64,32,31],
    "W": [63,64,56,64,63],
    "X": [99,20,8,20,99],
    "Y": [7,8,112,8,7],
    "Z": [97,81,73,69,67],
    ".": [0,96,96,0,0],
    ":": [0,54,54,0,0],
    "-": [8,8,8,8,8],
    "+": [8,8,62,8,8],
    "/": [32,16,8,4,2],
    "%": [35,19,8,100,98],
    " ": [0,0,0,0,0],
    "|": [0,0,127,0,0]
}


def render_pump_png(symbol, candles, live_price, start_price):
    W, H = 860, 440

    bg = (9, 13, 22)
    grid = (35, 45, 62)
    green = (16, 185, 129)
    red = (244, 63, 94)
    white = (226, 232, 240)
    muted = (148, 163, 184)
    orange = (245, 158, 11)
    cyan = (56, 189, 248)

    bars = candles[-65:]

    if not bars:
        return b""

    pixels = bytearray(bytes(bg) * (W * H))

    def px(x, y, c):
        if 0 <= x < W and 0 <= y < H:
            i = (y * W + x) * 3
            pixels[i:i+3] = bytes(c)

    def rect(x0, y0, x1, y1, c):
        x0 = max(0, min(W - 1, x0))
        x1 = max(0, min(W - 1, x1))
        y0 = max(0, min(H - 1, y0))
        y1 = max(0, min(H - 1, y1))

        if x1 < x0 or y1 < y0:
            return

        line_data = bytes(c) * (x1 - x0 + 1)

        for yy in range(y0, y1 + 1):
            i = (yy * W + x0) * 3
            pixels[i:i+len(line_data)] = line_data

    def line(x0, y0, x1, y1, c, dash=False):
        dx = abs(x1 - x0)
        sx = 1 if x0 < x1 else -1

        dy = -abs(y1 - y0)
        sy = 1 if y0 < y1 else -1

        err = dx + dy
        step = 0

        while True:
            if not dash or step % 8 < 5:
                px(x0, y0, c)

            if x0 == x1 and y0 == y1:
                break

            e = 2 * err

            if e >= dy:
                err += dy
                x0 += sx

            if e <= dx:
                err += dx
                y0 += sy

            step += 1

    def txt(x, y, s, c, scale=1):
        cur = x

        for ch in str(s).upper():
            glyph = FONT.get(ch, FONT[" "])

            for gx, bits in enumerate(glyph):
                for gy in range(7):
                    if bits & (1 << gy):
                        rect(
                            cur + gx * scale,
                            y + gy * scale,
                            cur + (gx + 1) * scale - 1,
                            y + (gy + 1) * scale - 1,
                            c
                        )

            cur += 6 * scale

    left, right, top, bottom = 18, 118, 42, 24
    vh, gap = 58, 12

    pw = W - left - right
    ph = H - top - bottom - vh - gap

    vals = (
        [b["h"] for b in bars]
        + [b["l"] for b in bars]
        + [live_price, start_price]
    )

    vals = [v for v in vals if v > 0]

    lo = min(vals) * 0.994
    hi = max(vals) * 1.006
    span = max(hi - lo, lo * 0.005)

    def yy(v):
        return int(top + (hi - v) / span * ph)

    txt(
        left,
        13,
        f"{symbol} | SPOT PUMP | LIVE {fmt_price(live_price)}",
        white,
        2
    )

    for f in (0, 0.25, 0.5, 0.75, 1):
        y = int(top + ph * f)

        line(left, y, left + pw, y, grid, True)

        txt(
            left + pw + 7,
            y - 3,
            fmt_price(hi - span * f),
            muted
        )

    if start_price > 0:
        y = yy(start_price)
        line(left, y, left + pw, y, orange, True)

    maxv = max(
        [b["v"] for b in bars] + [1]
    )

    step = pw / len(bars)
    bw = max(2, int(step * 0.62))

    voltop = top + ph + gap

    for i, b in enumerate(bars):
        x = int(left + (i + 0.5) * step)

        col = green if b["c"] >= b["o"] else red

        line(
            x,
            yy(b["h"]),
            x,
            yy(b["l"]),
            col
        )

        rect(
            x - bw // 2,
            min(yy(b["o"]), yy(b["c"])),
            x + bw // 2,
            max(
                min(yy(b["o"]), yy(b["c"])) + 1,
                max(yy(b["o"]), yy(b["c"]))
            ),
            col
        )

        hv = max(
            1,
            int(b["v"] / maxv * vh)
        )

        rect(
            x - bw // 2,
            voltop + vh - hv,
            x + bw // 2,
            voltop + vh,
            (
                col[0] // 2,
                col[1] // 2,
                col[2] // 2
            )
        )

    y = yy(live_price)

    line(
        left,
        y,
        left + pw,
        y,
        cyan,
        True
    )

    rect(
        left + pw + 3,
        y - 7,
        W - 4,
        y + 7,
        (2, 132, 199)
    )

    txt(
        left + pw + 7,
        y - 3,
        fmt_price(live_price),
        white
    )

    raw = bytearray()
    stride = W * 3

    for y in range(H):
        raw.append(0)
        raw.extend(
            pixels[y * stride:(y + 1) * stride]
        )

    def chunk(tag, data):
        return (
            struct.pack("!I", len(data))
            + tag
            + data
            + struct.pack(
                "!I",
                zlib.crc32(tag + data) & 0xffffffff
            )
        )

    ihdr = struct.pack(
        "!IIBBBBB",
        W, H, 8, 2, 0, 0, 0
    )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


# =============================================================================
# TELEGRAM
# =============================================================================

def telegram_send_message(message):
    if not BOT_TOKEN:
        print("[TG MESSAGE]\n" + message)
        return False

    body = json.dumps({
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }).encode("utf-8")

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data=body,
        headers={
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            result = json.loads(
                r.read().decode("utf-8")
            )

        if not result.get("ok"):
            print(f"[TG sendMessage] {result}")
            return False

        return True

    except Exception as exc:
        print(f"[TG sendMessage hata] {exc}")
        return False


def telegram_send_photo(png, caption):
    if len(caption) > 1000:
        caption = caption[:997] + "..."

    if not BOT_TOKEN:
        print(
            f"[TG PHOTO {len(png)} bytes]\n{caption}"
        )
        return False

    if not png:
        return telegram_send_message(caption)

    boundary = "----SpotAkisBoundary9f31"

    parts = []

    for key, value in (
        ("chat_id", CHAT_ID),
        ("caption", caption),
        ("parse_mode", "HTML")
    ):
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"'
                f"\r\n\r\n{value}\r\n"
            ).encode("utf-8")
        )

    parts.append(
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; '
            'name="photo"; filename="pump.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode("utf-8")
        + png
        + b"\r\n"
    )

    parts.append(
        f"--{boundary}--\r\n".encode("utf-8")
    )

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
        data=b"".join(parts),
        headers={
            "Content-Type": (
                f"multipart/form-data; boundary={boundary}"
            )
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=18) as r:
            result = json.loads(
                r.read().decode("utf-8")
            )

        if not result.get("ok"):
            print(f"[TG sendPhoto] {result}")
            return telegram_send_message(caption)

        return True

    except Exception as exc:
        print(f"[TG sendPhoto hata] {exc}")
        return telegram_send_message(caption)


# =============================================================================
# PUMP MOTORU
# =============================================================================

def is_pump_locked(base, now):
    rec = locks["pump"].get(base)

    if not rec:
        return False

    try:
        locked_at = safe_float(
            rec.get("locked_at")
        )
    except Exception:
        return False

    if PUMP_REPEAT_HOURS <= 0:
        return True

    return (
        now - locked_at
        < PUMP_REPEAT_HOURS * 3600
    )


def build_synthetic_candles(history, interval=60):
    buckets = {}

    for ts, price in history:
        key = int(ts // interval) * interval

        if key not in buckets:
            buckets[key] = {
                "t": key,
                "o": price,
                "h": price,
                "l": price,
                "c": price,
                "v": 1.0
            }

        else:
            b = buckets[key]

            b["h"] = max(b["h"], price)
            b["l"] = min(b["l"], price)
            b["c"] = price
            b["v"] += 1.0

    return [
        buckets[k]
        for k in sorted(buckets)
    ]


def record_pump_and_notify(
    symbol,
    base,
    entry,
    caption,
    png
):
    now = time.time()

    with state_lock:
        signals_log.append({
            "id": f"PUMP-{symbol}-{int(now)}",
            "engine": "PUMP",
            "symbol": symbol,
            "base": base,
            "direction": "LONG",
            "entry": entry,
            "current": entry,
            "peak_price": entry,
            "peak_pct": 0.0,
            "created_at": now
        })

        del signals_log[:-MAX_SIGNALS]

    save_state()

    telegram_send_photo(png, caption)


def evaluate_pump(symbol, price, quote_volume, now):
    base, quote = extract_base_quote(symbol)

    if price <= 0 or not base or is_excluded(symbol):
        return

    min_volume = (
        PUMP_MIN_QUOTE_VOLUME
        if quote == "USDT"
        else (
            3.0 if quote == "BTC"
            else 250.0 if quote == "BNB"
            else float("inf")
        )
    )

    min_change = (
        PUMP_MIN_CHANGE
        if quote == "USDT"
        else 4.0
    )

    if (
        quote not in ("USDT", "BTC", "BNB")
        or quote_volume < min_volume
    ):
        return

    should_verify = False

    with state_lock:
        hist = price_history.setdefault(
            symbol,
            []
        )

        hist.append((now, price))

        cutoff = now - PUMP_WINDOW_SECONDS

        while hist and hist[0][0] < cutoff:
            hist.pop(0)

        if len(hist) > 500:
            del hist[:-500]

        if (
            len(hist) < PUMP_MIN_TICKS
            or is_pump_locked(base, now)
        ):
            return

        low_ts, low_price = min(
            hist,
            key=lambda item: item[1]
        )

        change = (
            (price - low_price) / low_price * 100
            if low_price > 0 else 0
        )

        if change < min_change:
            return

        locks["pump"][base] = {
            "symbol": symbol,
            "locked_at": now,
            "pending": True
        }

        snapshot = list(hist)
        should_verify = True

    if should_verify:
        threading.Thread(
            target=verify_and_emit_pump,
            args=(
                symbol,
                base,
                price,
                quote_volume,
                low_ts,
                low_price,
                snapshot
            ),
            daemon=True
        ).start()


def verify_and_emit_pump(
    symbol,
    base,
    approx_price,
    quote_volume,
    low_ts,
    low_price,
    history
):
    try:
        time.sleep(
            max(0, PUMP_CONFIRM_SECONDS)
        )

        with state_lock:
            latest = tickers.get(
                symbol,
                {}
            ).copy()

        current = safe_float(
            latest.get("price"),
            approx_price
        )

        live_volume = safe_float(
            latest.get("volume24h"),
            quote_volume
        )

        if current <= 0 or low_price <= 0:
            raise RuntimeError(
                "Gecersiz dogrulama verisi"
            )

        change = (
            (current - low_price)
            / low_price * 100
        )

        min_change = (
            PUMP_MIN_CHANGE
            if symbol.endswith("USDT")
            else 4.0
        )

        if change < min_change:
            with state_lock:
                rec = locks["pump"].get(base, {})

                if rec.get("pending"):
                    locks["pump"].pop(base, None)

            return

        # Grafik WebSocket fiyat gecmisinden uretilir.
        with state_lock:
            live_hist = list(
                price_history.get(symbol, history)
            )

        candles = build_synthetic_candles(
            live_hist,
            60
        )

        if len(candles) < 2:
            candles = build_synthetic_candles(
                live_hist,
                15
            )
            tf_label = "15SN CANLI"

        else:
            tf_label = "1DK CANLI"

        png = render_pump_png(
            symbol,
            candles,
            current,
            low_price
        )

        mins = max(
            1,
            round((time.time() - low_ts) / 60)
        )

        vol_str = (
            f"{round(live_volume):,}"
            if symbol.endswith("USDT")
            else f"{live_volume:.4f}"
        )

        quote = extract_base_quote(symbol)[1]

        # MA200 HESAPLAMALARI
        ma200_1d = binance_ma200(
            symbol,
            "1d"
        )

        ma200_3d = binance_ma200(
            symbol,
            "3d"
        )

        ma1d_status = get_ma200_status(
            current,
            ma200_1d
        )

        ma3d_status = get_ma200_status(
            current,
            ma200_3d
        )

        ma_text = (
            "\n\n📊 <b>UZUN VADELI TREND</b>\n"
            f"🔹 Günlük MA200: "
            f"{fmt_price(ma200_1d) if ma200_1d else 'Veri alınamadı'}\n"
            f"🔹 3 Günlük MA200: "
            f"{fmt_price(ma200_3d) if ma200_3d else 'Veri alınamadı'}\n\n"
            f"📈 Günlük: {ma1d_status}\n"
            f"📈 3 Günlük: {ma3d_status}"
        )

        # SABIT EMOJI VE DUZGUN TURKCE MESAJ
        caption = (
            f"⚡ <b>SPOT ANLIK PUMP DEDEKTÖRÜ — #{symbol}</b>\n\n"
            f"📈 Değişim: <b>+{change:.2f}%</b> "
            f"({mins} dk içinde!)\n"
            f"💰 Başlangıç: {fmt_price(low_price)} "
            f"➡️ Anlık: <b>{fmt_price(current)}</b>\n"
            f"💧 24s Parite Hacmi: {vol_str} ({quote})\n"
            f"📊 Grafik: {tf_label}"
            f"{ma_text}\n\n"
            f"🔒 Tekil coin kilidi aktif."
        )

        with state_lock:
            locks["pump"][base] = {
                "symbol": symbol,
                "locked_at": time.time(),
                "pending": False
            }

            price_history.pop(symbol, None)

        record_pump_and_notify(
            symbol,
            base,
            current,
            caption,
            png
        )

    except Exception as exc:
        print(
            f"[PUMP verify] {symbol}: {exc}"
        )

        with state_lock:
            rec = locks["pump"].get(base, {})

            if rec.get("pending"):
                locks["pump"].pop(base, None)


# =============================================================================
# SINYAL PIK TAKIBI
# =============================================================================

def update_signal_peaks():
    changed = False

    with state_lock:

        for signal in signals_log:
            ticker = tickers.get(
                signal.get("symbol")
            )

            if not ticker:
                continue

            p = safe_float(
                ticker.get("price")
            )

            entry = safe_float(
                signal.get("entry")
            )

            if p <= 0 or entry <= 0:
                continue

            signal["current"] = p

            pct = (
                (p - entry) / entry * 100
            )

            if pct > safe_float(
                signal.get("peak_pct")
            ):

                signal["peak_pct"] = round(
                    pct,
                    2
                )

                signal["peak_price"] = p
                changed = True

    if changed:
        save_state()


# =============================================================================
# 21:00 GUNUN KAZANANI
# =============================================================================

def build_victory_report(now_ts=None):
    cutoff = (
        now_ts or time.time()
    ) - 86400

    with state_lock:
        recent = [
            s.copy()
            for s in signals_log
            if safe_float(
                s.get("created_at")
            ) >= cutoff
        ]

    best = {}

    for s in recent:
        symbol = s["symbol"]

        if (
            symbol not in best
            or safe_float(s.get("peak_pct"))
            > safe_float(
                best[symbol].get("peak_pct")
            )
        ):
            best[symbol] = s

    ranked = sorted(
        best.values(),
        key=lambda x: safe_float(
            x.get("peak_pct")
        ),
        reverse=True
    )

    if not ranked:
        return (
            "🏆 <b>GÜNÜN KAZANANI</b>\n\n"
            "Son 24 saatte pump sinyali bulunmuyor."
        )

    winner = ranked[0]

    symbol = winner["symbol"]
    dip = safe_float(winner.get("entry"))
    peak = safe_float(winner.get("peak_price"))
    pct = safe_float(winner.get("peak_pct"))

    message = (
        f"🏆 <b>GÜNÜN KAZANANI: {symbol}</b>\n\n"
        f"🔥 24 saat içinde en çok yükselen coin!\n\n"
        f"📉 Dip Fiyat: <b>{fmt_price(dip)}</b>\n"
        f"🚀 Zirve Fiyat: <b>{fmt_price(peak)}</b>\n"
        f"📈 Maksimum Yükseliş: <b>+{pct:.1f}%</b>\n\n"
        f"💡 <b>Değerlendirme:</b> {symbol} gün içerisinde "
        f"muazzam bir hacim ve fiyat patlaması yaşadı. "
        f"Pump Dedektör verilerimize göre günün en güçlü "
        f"hareketi bu coinde gerçekleşti."
    )

    if len(ranked) > 1:
        message += (
            "\n\n📊 <b>Diğer güçlü pump hareketleri:</b>\n"
        )

        for i, s in enumerate(ranked[1:6], 2):
            message += (
                f"\n{i}. #{s['symbol']} "
                f"— +{safe_float(s.get('peak_pct')):.2f}%"
            )

    return message


# =============================================================================
# 23:00 TOP 24 SAAT
# =============================================================================

def build_top24_report(now_ts=None):
    cutoff = (
        now_ts or time.time()
    ) - 86400

    with state_lock:
        recent = [
            s.copy()
            for s in signals_log
            if safe_float(
                s.get("created_at")
            ) >= cutoff
        ]

    best = {}

    for s in recent:
        symbol = s["symbol"]

        if (
            symbol not in best
            or safe_float(
                s.get("peak_pct")
            ) > safe_float(
                best[symbol].get("peak_pct")
            )
        ):
            best[symbol] = s

    ranked = sorted(
        best.values(),
        key=lambda x: safe_float(
            x.get("peak_pct")
        ),
        reverse=True
    )

    if not ranked:
        return (
            "📊 <b>TOP 24 SAAT ÖZETİ (23:00 TR)</b>\n\n"
            "Son 24 saatte kaydedilen Spot Pump "
            "sinyali bulunmuyor."
        )

    lines = [
        "📊 <b>TOP 24 SAAT SPOT PUMP ÖZETİ (23:00 TR)</b>\n"
    ]

    for i, s in enumerate(ranked[:20], 1):
        lines.append(
            f"{i}. <b>#{s['symbol']}</b> "
            f"· Pik: <b>+{safe_float(s.get('peak_pct')):.2f}%</b> "
            f"| Anlık: ${fmt_price(s.get('current'))}"
        )

    return "\n".join(lines)


# =============================================================================
# BINANCE DUYURULARI
# =============================================================================

def translate_binance_title_tr(title):
    replacements = (
        ("Binance Will List", "Binance Listeliyor:"),
        ("Binance Will Add", "Binance Ekliyor:"),
        ("Binance Will Support", "Binance Destekliyor:"),
        ("Binance Has Completed the", "Binance Tamamladı:"),
        ("Binance Futures Will Launch", "Binance Vadeli Başlatıyor:"),
        ("Token Merge", "Token Birleşmesi (Merge)"),
        ("Contract Swap", "Kontrat Değişimi (Swap)"),
        ("Withdrawals Reopened", "Çekim İşlemleri Yeniden Açıldı"),
        (
            "Notice of Removal of Spot Trading Pairs",
            "Spot İşlem Çiftleri Kaldırma (Delist)"
        ),
        ("Binance Will Delist", "Binance Delist Ediyor:"),
        ("Network Upgrade", "Ağ Güncellemesi"),
    )

    for old, new in replacements:
        title = title.replace(old, new)

    return title.strip()


def poll_binance_announcements():
    global news_seeded

    catalogs = (
        (48, "Yeni Listeleme"),
        (49, "Binance Duyuru"),
        (161, "Delist / Kaldırma")
    )

    found = []

    for cat_id, label in catalogs:

        url = (
            "https://www.binance.com/bapi/composite/v1/public/cms/article/"
            f"catalog/list/query?catalogId={cat_id}&pageNo=1&pageSize=8"
        )

        try:
            data = http_get_json(
                url,
                timeout=8
            )

            articles = (
                data.get("data", {})
                .get("articles", [])
            )

            for article in articles:
                aid = str(
                    article.get("id")
                    or article.get("code")
                    or article.get("title")
                    or ""
                )

                if not aid:
                    continue

                with state_lock:
                    if aid in news_seen_ids:
                        continue

                    news_seen_ids.add(aid)

                title = translate_binance_title_tr(
                    str(article.get("title") or "")
                )

                code = str(
                    article.get("code") or ""
                )

                link = (
                    f"https://www.binance.com/en/support/announcement/detail/{code}"
                    if code
                    else "https://www.binance.com/en/support/announcement"
                )

                found.append(
                    (label, title, link)
                )

        except Exception as exc:
            print(
                f"[NEWS] catalog {cat_id}: {exc}"
            )

    # Ilk tarama eski haberleri gondermez.
    if news_seeded:

        for label, title, link in found[:5]:
            telegram_send_message(
                f"📰 <b>Binance | {label}</b>\n"
                f"{title}\n"
                f"{link}"
            )

    news_seeded = True

    save_state()


# =============================================================================
# WEBSOCKET MINI TICKER ISLEME
# =============================================================================

def process_mini_ticker(item, now):
    symbol = str(
        item.get("s") or ""
    )

    if not symbol or is_excluded(symbol):
        return

    price = safe_float(
        item.get("c")
    )

    quote_volume = safe_float(
        item.get("q")
    )

    if price <= 0:
        return

    base, quote = extract_base_quote(
        symbol
    )

    if not base or quote not in (
        "USDT", "BTC", "BNB"
    ):
        return

    with state_lock:

        tickers[symbol] = {
            "symbol": symbol,
            "base": base,
            "quote": quote,
            "price": price,
            "volume24h": quote_volume,
            "updated_at": now
        }

        # Bellek siniri
        if len(tickers) > MAX_TRACKED_PAIRS:

            oldest = sorted(
                tickers.items(),
                key=lambda kv: kv[1].get(
                    "updated_at", 0
                )
            )

            remove_count = (
                len(tickers) - MAX_TRACKED_PAIRS
            )

            for old_symbol, _ in oldest[:remove_count]:
                tickers.pop(old_symbol, None)
                price_history.pop(old_symbol, None)

    evaluate_pump(
        symbol,
        price,
        quote_volume,
        now
    )


# =============================================================================
# WEBSOCKET ANA DONGU
# =============================================================================

def ws_stream_loop():
    global ws_connected
    global last_ws_message

    delay = 2

    while True:
        sock = None

        try:
            print(
                "[WS] Binance Spot baglantisi kuruluyor..."
            )

            sock, buffer = connect_binance_ws()

            ws_connected = True
            delay = 2

            print(
                "[WS] Baglanti aktif."
            )

            while True:
                opcode, payload = ws_read_frame(
                    sock,
                    buffer
                )

                if opcode == 8:
                    raise EOFError(
                        "WS close frame"
                    )

                if opcode == 9:
                    ws_send_frame(
                        sock,
                        payload,
                        opcode=10
                    )
                    continue

                if opcode not in (1, 2):
                    continue

                obj = json.loads(
                    payload.decode("utf-8")
                )

                if not isinstance(obj, list):
                    continue

                now = time.time()
                last_ws_message = now

                for item in obj:
                    process_mini_ticker(
                        item,
                        now
                    )

                update_signal_peaks()

        except Exception as exc:
            print(
                f"[WS] Baglanti koptu: {exc}"
            )

            ws_connected = False

            time.sleep(delay)

            delay = min(
                60,
                delay * 2
            )

        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass


# =============================================================================
# WEBSOCKET SAGLIK KONTROLU
# =============================================================================

def stale_data_watchdog():
    global ws_connected

    while True:

        if (
            ws_connected
            and last_ws_message
            and time.time() - last_ws_message > 90
        ):
            print(
                "[WS] 90 sn veri gelmedi; baglanti yenilenecek."
            )

            ws_connected = False

        time.sleep(15)


# =============================================================================
# RAPOR VE DUYURU ZAMANLAYICI
# =============================================================================

def scheduler_and_news_loop():
    last_news = 0

    while True:
        now = time.time()

        if now - last_news >= NEWS_INTERVAL:
            poll_binance_announcements()
            last_news = now

        local = tr_now()

        today = local.strftime(
            "%Y-%m-%d"
        )

        # 21:00 GUNUN KAZANANI
        if (
            local.hour == 21
            and locks["reports"].get("victory_day") != today
        ):

            with state_lock:
                locks["reports"]["victory_day"] = today

            save_state()

            telegram_send_message(
                build_victory_report(now)
            )

        # 23:00 TOP 24 SAAT
        if (
            local.hour == 23
            and locks["reports"].get("top24_day") != today
        ):

            with state_lock:
                locks["reports"]["top24_day"] = today

            save_state()

            telegram_send_message(
                build_top24_report(now)
            )

        time.sleep(10)


# =============================================================================
# RENDER HEALTH CHECK
# =============================================================================

class HealthHandler(
    http.server.BaseHTTPRequestHandler
):

    def do_GET(self):

        with state_lock:
            pair_count = len(tickers)
            signal_count = len(signals_log)

        body = json.dumps({
            "ok": True,
            "ws": ws_connected,
            "last_ws_message": (
                int(last_ws_message)
                if last_ws_message else None
            ),
            "pairs": pair_count,
            "signals": signal_count,
            "engines": ["PUMP"],
            "mode": "SPOT_SIGNAL_ONLY",
            "ma200": [
                "1d",
                "3d"
            ]
        }).encode("utf-8")

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

        self.end_headers()

        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


# =============================================================================
# MAIN
# =============================================================================

def main():
    load_state()

    print(
        f"[Spot Akis Terminali] Baslatiliyor "
        f"| Port={PORT} "
        f"| ChatID={CHAT_ID}"
    )

    print(
        "[SISTEM] Yalnizca SPOT PUMP motoru aktif."
    )

    print(
        "[MA200] Gunluk ve 3 Gunluk MA200 aktif."
    )

    print(
        "[GRAFIK] WebSocket canli fiyat gecmisi."
    )

    if not BOT_TOKEN:
        print(
            "[UYARI] BOT_TOKEN tanimli degil; "
            "Telegram yerine konsola yazilacak."
        )

    targets = (
        ws_stream_loop,
        stale_data_watchdog,
        scheduler_and_news_loop
    )

    for target in targets:
        threading.Thread(
            target=target,
            daemon=True
        ).start()

    server = http.server.ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    print(
        f"[HTTP] Health server aktif: {PORT}"
    )

    server.serve_forever()


if __name__ == "__main__":
    main()

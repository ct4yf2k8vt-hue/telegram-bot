#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spot Akış Terminali — 7/24 Kesintisiz Render Python 3 Telegram Botu (Sıfır Bağımlılık)
Aktif Sinyal Motoru (Sadece 1 Numaralı Bot):
  1. 0-7 dk Spot Anlık Pump (+%4.0 USDT / +%3.8 BTC-BNB, RVOL engelsiz ani sıçramalar)
     + 15 dk Hacimli Yeşil Kırılım (+%3.2, RVOL >= 1.4x)
  - Tekil / 2 Saatlik Dalga Kilidi (PUMP_REPEAT_HOURS=2)
  - Canlı 1DK CANLI / 15SN CANLI mum grafik PNG (BAS turuncu kesik + ANLIK mavi etiket)
Otomatik Özet & Duyurular:
  - Günün Zaferi (21:00 TR)
  - Top 24 Saat Özeti (23:00 TR)
  - Binance Resmi Duyuruları (60 sn)
7/24 Dayanıklılık & Komutlar:
  - WebSocket (!miniTicker@arr) + REST + Binance WS-API + Bybit Klines yedeği
  - data-api.binance.vision öncelikli mirror + aktif TRADING parite filtresi
  - Thread Watchdog + Fatal Recovery + HEAD/GET 200 OK /health (UptimeRobot/Render uyumlu)
  - Komutlar: /start, /test, /24s, /piyasa, /piyasagorsel, /durum, /sifirla
"""

import base64
import datetime
import html
import http.server
import json
import math
import os
import re
import socket
import socketserver
import ssl
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

# ==============================================================================
# KONFİGÜRASYON
# ==============================================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "1623907197").strip()
PORT = int(os.environ.get("PORT", "10000"))
try:
    PUMP_REPEAT_HOURS = max(0.0, float(os.environ.get("PUMP_REPEAT_HOURS", "2")))
except ValueError:
    PUMP_REPEAT_HOURS = 2.0

RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
STATE_FILE = os.environ.get("STATE_FILE", "/tmp/spot_bot_state.json")

BINANCE_MIRRORS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]

EXCLUDED_PREFIXES = ("USDC", "FDUSD", "TUSD", "USDP", "EUR", "AEUR", "USDE", "USD1", "RLUSD")
EXCLUDED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

TR_TZ = datetime.timezone(datetime.timedelta(hours=3))

# ==============================================================================
# DURUM & KİLİT YÖNETİMİ
# ==============================================================================
state_lock = threading.RLock()
bot_running = True
started_at = time.time()
pump_loops = 0
active_trading_symbols = set()
tickers = {}          # sym -> dict
price_history = {}    # sym -> list[(ts_sec, price)]
verify_cooldown = {}  # sym -> retry_after_ts
signals_log = []      # list[dict] (24s pik takibi ve raporlar)
subscribed_chats = []
news_seen_ids = set()
news_seeded = False
ws_connected = False
ban_until_ts = 0.0
preferred_mirror_idx = 0
bot_username = ""
telegram_ok = False
last_tg_error = ""
last_tick_ts = time.time()

if CHAT_ID:
    subscribed_chats.append(CHAT_ID)

locks = {
    "pump": {},   # base -> {"symbol": str, "locked_at": float}
    "reports": {"victory_day": "", "top24_day": ""},
}


def log(msg: str):
    ts = datetime.datetime.now(TR_TZ).strftime("%H:%M:%S")
    print(f"[{ts} TR] {msg}", flush=True)


def load_state():
    global signals_log
    try:
        if not os.path.exists(STATE_FILE):
            return
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        with state_lock:
            raw_locks = raw.get("locks", {})
            if isinstance(raw_locks.get("pump"), dict):
                locks["pump"] = raw_locks["pump"]
            if isinstance(raw_locks.get("reports"), dict):
                locks["reports"].update(raw_locks["reports"])
            raw_sigs = raw.get("signals", [])
            if isinstance(raw_sigs, list):
                signals_log = [s for s in raw_sigs if isinstance(s, dict) and s.get("engine") == "PUMP"][-300:]
            chats = raw.get("subscribed_chats", [])
            if isinstance(chats, list):
                for c in chats:
                    cs = str(c).strip()
                    if cs and cs not in subscribed_chats:
                        subscribed_chats.append(cs)
            seen = raw.get("news_seen_ids", [])
            if isinstance(seen, list):
                for x in seen[-300:]:
                    news_seen_ids.add(str(x))
    except Exception as exc:
        log(f"[UYARI] State okunamadı: {exc}")


def save_state():
    try:
        with state_lock:
            payload = {
                "locks": locks,
                "signals": signals_log[-300:],
                "subscribed_chats": subscribed_chats,
                "news_seen_ids": list(news_seen_ids)[-300:],
            }
        tmp_path = STATE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, STATE_FILE)
    except Exception:
        pass


def get_target_chats():
    with state_lock:
        out = []
        if CHAT_ID:
            out.append(CHAT_ID)
        for c in subscribed_chats:
            if c and c not in out:
                out.append(c)
        return out


def register_chat_id(cid):
    cs = str(cid or "").strip()
    if not cs:
        return
    with state_lock:
        if cs not in subscribed_chats:
            subscribed_chats.append(cs)
            save_state()


# ==============================================================================
# YARDIMCI FONKSİYONLAR
# ==============================================================================
def extract_base_quote(sym):
    for q in ("USDT", "BTC", "BNB"):
        if sym.endswith(q) and len(sym) > len(q):
            return sym[:-len(q)], q
    return None, None


def is_excluded(sym):
    if any(sym.startswith(p) for p in EXCLUDED_PREFIXES):
        return True
    if any(k in sym for k in EXCLUDED_SUFFIXES):
        return True
    return False


def fmt_price(p):
    if not p or p <= 0 or math.isnan(p) or math.isinf(p):
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


def fmt_vol(v, quote="USDT"):
    if quote != "USDT":
        return f"{v:.2f} {quote}"
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:.0f}"


def strip_html(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def tr_now():
    return datetime.datetime.now(TR_TZ)


def update_live_candle(candles, live_price):
    """Son mumu anlık canlı fiyatla günceller (görselin daima canlı olmasını sağlar)."""
    if not candles or not live_price or live_price <= 0:
        return candles
    out = [dict(b) for b in candles]
    last = out[-1]
    last["close"] = live_price
    if live_price > last["high"]:
        last["high"] = live_price
    if live_price < last["low"]:
        last["low"] = live_price
    return out


# ==============================================================================
# HTTP / BINANCE REST + WS-API + BYBIT FALLBACK
# ==============================================================================
def http_get_json(url, timeout=9):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; SpotPumpBot/3.0)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def binance_rest_get(path_and_query, timeout=9):
    global ban_until_ts, preferred_mirror_idx
    now = time.time()
    if now < ban_until_ts:
        raise RuntimeError("REST rate limit active")
    n = len(BINANCE_MIRRORS)
    last_err = None
    for off in range(n):
        idx = (preferred_mirror_idx + off) % n
        url = BINANCE_MIRRORS[idx] + path_and_query
        try:
            data = http_get_json(url, timeout=timeout)
            preferred_mirror_idx = idx
            return data
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (418, 429):
                ban_until_ts = time.time() + 900
                raise
        except Exception as e:
            last_err = e
    raise last_err or RuntimeError("Binance mirror unreachable")


def _ws_send_frame(sock, data, opcode=0x1):
    mask = os.urandom(4)
    header = bytearray([0x80 | opcode])
    n = len(data)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", n))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", n))
    header.extend(mask)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(bytes(header) + masked)


def _ws_recv_exact(sock, buf, n):
    while len(buf) < n:
        chunk = sock.recv(max(4096, n - len(buf)))
        if not chunk:
            raise EOFError("WS EOF")
        buf.extend(chunk)
    out = bytes(buf[:n])
    del buf[:n]
    return out


def _ws_read_frame(sock, buf):
    b1, b2 = _ws_recv_exact(sock, buf, 2)
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack("!H", _ws_recv_exact(sock, buf, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _ws_recv_exact(sock, buf, 8))[0]
    mask = _ws_recv_exact(sock, buf, 4) if masked else None
    payload = _ws_recv_exact(sock, buf, length)
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


def ws_api_request(method, params, timeout=9):
    """Binance WebSocket API (REST 418/451 kotasından etkilenmez)."""
    host = "ws-api.binance.com"
    path = "/ws-api/v3"
    ctx = ssl.create_default_context()
    sock = socket.create_connection((host, 443), timeout=timeout)
    ssock = ctx.wrap_socket(sock, server_hostname=host)
    try:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        ssock.sendall(handshake.encode("ascii"))
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = ssock.recv(4096)
            if not chunk:
                raise RuntimeError("WS-API handshake closed")
            buf += chunk
        if b"101 " not in buf.split(b"\r\n", 1)[0]:
            raise RuntimeError("WS-API handshake rejected")
        req_id = f"r_{int(time.time() * 1000)}"
        payload = json.dumps({"id": req_id, "method": method, "params": params}).encode("utf-8")
        _ws_send_frame(ssock, payload)

        deadline = time.time() + timeout
        extra = buf.split(b"\r\n\r\n", 1)[1]
        frame_buf = bytearray(extra)
        while time.time() < deadline:
            opcode, msg_bytes = _ws_read_frame(ssock, frame_buf)
            if opcode == 0x8:
                break
            if opcode == 0x9:
                _ws_send_frame(ssock, msg_bytes, opcode=0xA)
                continue
            if opcode in (0x1, 0x2):
                obj = json.loads(msg_bytes.decode("utf-8"))
                if obj.get("id") == req_id:
                    if obj.get("status") != 200:
                        raise RuntimeError(f"WS-API error {obj.get('status')}")
                    return obj.get("result")
        raise TimeoutError("WS-API timeout")
    finally:
        try:
            ssock.close()
        except Exception:
            pass


def _parse_binance_klines(raw):
    return [
        {
            "openTime": int(float(k[0]) // 1000),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "quoteVol": float(k[7]) if len(k) > 7 else float(k[5]) * float(k[4]),
        }
        for k in raw
    ]


def fetch_klines(sym, interval="1m", limit=45):
    try:
        raw = binance_rest_get(
            f"/api/v3/klines?symbol={urllib.parse.quote(sym)}&interval={urllib.parse.quote(interval)}&limit={limit}",
            timeout=7,
        )
        if isinstance(raw, list) and raw:
            return _parse_binance_klines(raw)
    except Exception:
        pass
    try:
        raw = ws_api_request("klines", {"symbol": sym, "interval": interval, "limit": limit}, timeout=7)
        if isinstance(raw, list) and raw:
            return _parse_binance_klines(raw)
    except Exception:
        pass
    if interval in ("1m", "15m"):
        bybit_iv = "1" if interval == "1m" else "15"
        try:
            url = f"https://api.bybit.com/v5/market/kline?category=spot&symbol={urllib.parse.quote(sym)}&interval={bybit_iv}&limit={limit}"
            d = http_get_json(url, timeout=6)
            rows = d.get("result", {}).get("list", [])
            if rows:
                return [
                    {
                        "openTime": int(r[0]) // 1000,
                        "open": float(r[1]),
                        "high": float(r[2]),
                        "low": float(r[3]),
                        "close": float(r[4]),
                        "volume": float(r[5]),
                        "quoteVol": float(r[6]) if len(r) > 6 else float(r[5]) * float(r[4]),
                    }
                    for r in reversed(rows)
                ]
        except Exception:
            pass
    return []


def refresh_active_trading_symbols():
    global active_trading_symbols
    try:
        info = binance_rest_get("/api/v3/exchangeInfo", timeout=12)
        next_set = set()
        for s in info.get("symbols", []):
            sym = s.get("symbol", "")
            if sym and s.get("status") == "TRADING" and s.get("isSpotTradingAllowed") is not False:
                next_set.add(sym)
        if len(next_set) > 50:
            with state_lock:
                active_trading_symbols = next_set
                for sym in list(tickers.keys()):
                    if sym not in next_set:
                        tickers.pop(sym, None)
                        price_history.pop(sym, None)
            log(f"[Binance] Aktif TRADING spot parite: {len(next_set)}")
    except Exception as exc:
        log(f"[UYARI] exchangeInfo güncellenemedi: {exc}")


# ==============================================================================
# SIFIR BAĞIMLILIKLI PNG MUM GRAFİĞİ OLUŞTURUCU (CANLI 1DK / 15SN PUMP)
# ==============================================================================
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


def _encode_png(W, H, buf):
    raw_rows = bytearray()
    stride = W * 3
    for y in range(H):
        raw_rows.append(0)
        raw_rows.extend(buf[y * stride:(y + 1) * stride])
    compressed = zlib.compress(bytes(raw_rows), 6)

    def png_chunk(tag, payload):
        return struct.pack("!I", len(payload)) + tag + payload + struct.pack("!I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    ihdr = struct.pack("!IIBBBBB", W, H, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", ihdr) + png_chunk(b"IDAT", compressed) + png_chunk(b"IEND", b"")


def _draw_text_5x7(buf, W, H, x, y, text, col, scale=1):
    tr_map = str.maketrans("ÇĞİÖŞÜçğıöşü", "CGIOSUCGIOSU")
    clean = text.translate(tr_map).upper()
    cx = x
    for ch in clean:
        glyph = FONT_5X7.get(ch, FONT_5X7[" "])
        for gx in range(5):
            bits = glyph[gx]
            for gy in range(7):
                if bits & (1 << gy):
                    x0, y0 = cx + gx * scale, y + gy * scale
                    for dy in range(scale):
                        yy = y0 + dy
                        if 0 <= yy < H:
                            for dx in range(scale):
                                xx = x0 + dx
                                if 0 <= xx < W:
                                    idx = (yy * W + xx) * 3
                                    buf[idx] = col[0]
                                    buf[idx + 1] = col[1]
                                    buf[idx + 2] = col[2]
        cx += 6 * scale


def render_pump_png(symbol, tf_label, candles, live_price, start_price):
    """Saf Python PNG mum grafiği (canlı fiyat + BAS turuncu çizgisi + alt hacim)."""
    W, H = 860, 440
    BG = (9, 13, 22)
    GRID = (28, 38, 56)
    BULL = (16, 185, 129)
    BEAR = (244, 63, 94)
    TEXT = (226, 232, 240)
    MUTED = (148, 163, 184)
    START_COL = (245, 158, 11)
    LIVE_COL = (56, 189, 248)

    bars = update_live_candle(candles[-75:], live_price)
    if not bars:
        return b""

    buf = bytearray(bytes(BG) * (W * H))

    def set_px(x, y, col):
        if 0 <= x < W and 0 <= y < H:
            i = (y * W + x) * 3
            buf[i] = col[0]
            buf[i + 1] = col[1]
            buf[i + 2] = col[2]

    def fill_rect(x0, y0, x1, y1, col):
        x0, x1 = max(0, min(x0, x1)), min(W - 1, max(x0, x1))
        y0, y1 = max(0, min(y0, y1)), min(H - 1, max(y0, y1))
        row_bytes = bytes(col) * (x1 - x0 + 1)
        for y in range(y0, y1 + 1):
            start = (y * W + x0) * 3
            buf[start:start + len(row_bytes)] = row_bytes

    def draw_line(x0, y0, x1, y1, col, dashed=False):
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        step_n = 0
        while True:
            if not dashed or (step_n % 8) < 5:
                set_px(x0, y0, col)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy
            step_n += 1

    pad_l, pad_r, pad_t, pad_b = 18, 118, 42, 24
    vol_h = 62
    gap = 12
    plot_w = W - pad_l - pad_r
    price_h = H - pad_t - pad_b - vol_h - gap

    all_p = [b["high"] for b in bars] + [b["low"] for b in bars]
    if live_price and live_price > 0:
        all_p.append(live_price)
    if start_price and start_price > 0:
        all_p.append(start_price)

    lo_p = min(all_p) * 0.994
    hi_p = max(all_p) * 1.006
    rng_p = max(hi_p - lo_p, lo_p * 0.005)
    max_vol = max([b["volume"] for b in bars] + [1.0])

    def y_of(p):
        return int(pad_t + ((hi_p - p) / rng_p) * price_h)

    _draw_text_5x7(buf, W, H, pad_l, 14, f"{symbol} | {tf_label} | SPOT PUMP | ANLIK ${fmt_price(live_price)}", TEXT, scale=2)

    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        gy = int(pad_t + price_h * frac)
        draw_line(pad_l, gy, pad_l + plot_w, gy, GRID, dashed=True)
        pval = hi_p - rng_p * frac
        _draw_text_5x7(buf, W, H, pad_l + plot_w + 8, gy - 3, fmt_price(pval), MUTED, scale=1)

    if start_price and start_price > 0:
        sy = y_of(start_price)
        if pad_t <= sy <= pad_t + price_h:
            draw_line(pad_l, sy, pad_l + plot_w, sy, START_COL, dashed=True)
            _draw_text_5x7(buf, W, H, pad_l + plot_w + 8, sy - 3, f"BAS {fmt_price(start_price)}", START_COL, scale=1)

    n = len(bars)
    step = plot_w / max(1, n)
    bw = max(2, int(step * 0.62))
    vol_top = pad_t + price_h + gap
    for idx, b in enumerate(bars):
        cx = int(pad_l + (idx + 0.5) * step)
        col = BULL if b["close"] >= b["open"] else BEAR
        yo = y_of(b["open"])
        yc = y_of(b["close"])
        yh = y_of(b["high"])
        yl = y_of(b["low"])
        draw_line(cx, yh, cx, yl, col)
        fill_rect(cx - bw // 2, min(yo, yc), cx + bw // 2, max(min(yo, yc) + 1, max(yo, yc)), col)
        vh = max(1, int((b["volume"] / max_vol) * vol_h))
        fill_rect(cx - bw // 2, vol_top + vol_h - vh, cx + bw // 2, vol_top + vol_h, (col[0] // 2, col[1] // 2, col[2] // 2))

    if live_price and live_price > 0:
        ly = y_of(live_price)
        draw_line(pad_l, ly, pad_l + plot_w, ly, LIVE_COL, dashed=True)
        fill_rect(pad_l + plot_w + 4, ly - 7, W - 4, ly + 7, (2, 132, 199))
        _draw_text_5x7(buf, W, H, pad_l + plot_w + 8, ly - 3, fmt_price(live_price), (248, 250, 252), scale=1)

    return _encode_png(W, H, buf)


def render_market_radar_png(rows, title="ANLIK SPOT PIYASA - EN YUKSEGE GIDENLER (24S)"):
    top_rows = rows[:14]
    W = 900
    H = 92 + max(len(top_rows), 1) * 34
    BG = (11, 15, 25)
    buf = bytearray(bytes(BG) * (W * H))

    def fill_rect_wh(x0, y0, w, h, col):
        for y in range(max(0, y0), min(H, y0 + h)):
            s = (y * W + max(0, x0)) * 3
            e = (y * W + min(W, x0 + w)) * 3
            if e > s:
                buf[s:e] = bytes(col) * ((e - s) // 3)

    fill_rect_wh(0, 0, W, 62, (16, 22, 36))
    fill_rect_wh(0, 62, W, 1, (34, 48, 72))
    _draw_text_5x7(buf, W, H, 22, 18, title, (245, 248, 252), scale=2)
    _draw_text_5x7(buf, W, H, W - 180, 20, tr_now().strftime("%H:%M:%S TR"), (148, 163, 184), scale=2)

    y = 78
    for idx, t in enumerate(top_rows, 1):
        if idx % 2 == 0:
            fill_rect_wh(14, y - 4, W - 28, 30, (17, 24, 39))
        _draw_text_5x7(buf, W, H, 24, y, f"{idx:02d}. {t['symbol']}", (240, 244, 248), scale=2)
        _draw_text_5x7(buf, W, H, 270, y, f"${fmt_price(t['price'])}", (203, 213, 225), scale=2)
        _draw_text_5x7(buf, W, H, 470, y, f"24S {t.get('change24h', 0.0):+.1f}%", (34, 211, 153), scale=2)
        c7 = t.get("win7m", 0.0)
        _draw_text_5x7(buf, W, H, 640, y, f"7DK {c7:+.1f}%", (56, 189, 248) if c7 >= 0 else (248, 113, 113), scale=2)
        _draw_text_5x7(buf, W, H, 780, y, fmt_vol(t.get("volume24h", 0.0), t.get("quote", "USDT")), (148, 163, 184), scale=2)
        y += 34
    return _encode_png(W, H, buf)


# ==============================================================================
# TELEGRAM İSTEMCİSİ (HTML -> PLAIN TEXT YEDEĞİ + ÇOKLU SOHBET)
# ==============================================================================
def tg_request(method, payload, timeout=15):
    global telegram_ok, last_tg_error
    if not BOT_TOKEN:
        last_tg_error = "BOT_TOKEN boş"
        return {"ok": False, "description": last_tg_error}
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            if res.get("ok"):
                telegram_ok = True
                last_tg_error = ""
            return res
    except urllib.error.HTTPError as e:
        err_text = e.read().decode("utf-8", errors="ignore")
        desc = err_text
        try:
            desc = json.loads(err_text).get("description", err_text)
        except Exception:
            pass
        if e.code == 400 and "parse entities" in str(desc).lower() and payload.get("parse_mode"):
            fb = dict(payload)
            fb.pop("parse_mode", None)
            if "text" in fb:
                fb["text"] = strip_html(fb["text"])
            return tg_request(method, fb, timeout=timeout)
        last_tg_error = f"{method} HTTP {e.code}: {desc}"
        if e.code != 409:
            log(f"[TG {method} HATA] {last_tg_error}")
        return {"ok": False, "error_code": e.code, "description": desc}
    except Exception as e:
        last_tg_error = f"{method} ağ hatası: {e}"
        log(f"[TG {method} Bağlantı HATA] {last_tg_error}")
        return {"ok": False, "description": str(e)}


def tg_send_photo_to(chat_id, png_bytes, caption, reply_markup=None):
    global telegram_ok, last_tg_error
    if not BOT_TOKEN:
        return {"ok": False, "description": "BOT_TOKEN boş"}
    boundary = f"----SpotPumpBoundary{int(time.time() * 1000)}"
    parts = []
    fields = [("chat_id", str(chat_id)), ("caption", caption[:1000]), ("parse_mode", "HTML")]
    if reply_markup:
        fields.append(("reply_markup", json.dumps(reply_markup)))
    for k, v in fields:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8"))
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="chart.png"\r\nContent-Type: image/png\r\n\r\n'.encode("utf-8")
        + png_bytes
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=18) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            if res.get("ok"):
                telegram_ok = True
                last_tg_error = ""
            return res
    except urllib.error.HTTPError as e:
        err_text = e.read().decode("utf-8", errors="ignore")
        desc = err_text
        try:
            desc = json.loads(err_text).get("description", err_text)
        except Exception:
            pass
        last_tg_error = f"sendPhoto HTTP {e.code}: {desc}"
        log(f"[TG sendPhoto HATA] {last_tg_error}")
        return {"ok": False, "error_code": e.code, "description": desc}
    except Exception as e:
        last_tg_error = f"sendPhoto ağ hatası: {e}"
        log(f"[TG sendPhoto Bağlantı HATA] {last_tg_error}")
        return {"ok": False, "description": str(e)}


def telegram_send_message(text, reply_markup=None):
    if not BOT_TOKEN:
        print(f"[SİNYAL/MESAJ]\n{text}\n")
        return False
    any_ok = False
    for cid in get_target_chats():
        payload = {
            "chat_id": cid,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        res = tg_request("sendMessage", payload)
        if res.get("ok"):
            any_ok = True
    return any_ok


def telegram_send_photo(png_bytes, caption, reply_markup=None):
    if not BOT_TOKEN:
        print(f"[SİNYAL/GÖRSEL {len(png_bytes)}B]\n{caption}\n")
        return False
    if not png_bytes:
        return telegram_send_message(caption, reply_markup=reply_markup)
    any_ok = False
    for cid in get_target_chats():
        res = tg_send_photo_to(cid, png_bytes, caption, reply_markup=reply_markup)
        if res.get("ok"):
            any_ok = True
        else:
            r2 = tg_request("sendMessage", {
                "chat_id": cid,
                "text": caption,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
            if r2.get("ok"):
                any_ok = True
    return any_ok


def record_pump_and_notify(symbol, base, entry_price, caption, png_bytes=b""):
    now = time.time()
    with state_lock:
        signals_log.append({
            "id": f"PUMP-{symbol}-{int(now * 1000)}",
            "engine": "PUMP",
            "symbol": symbol,
            "base": base,
            "direction": "LONG",
            "entry": entry_price,
            "current": entry_price,
            "peak_price": entry_price,
            "peak_pct": 0.0,
            "created_at": now,
        })
        if len(signals_log) > 300:
            del signals_log[:-300]
    save_state()
    if png_bytes:
        ok = telegram_send_photo(png_bytes, caption)
    else:
        ok = telegram_send_message(caption)
    log(f"[SİNYAL] {symbol} -> Telegram: {'OK' if ok else 'HATA'}")


# ==============================================================================
# MOTOR 1: 0-7 DK SPOT ANLIK PUMP + 15 DK HACİMLİ YEŞİL KIRILIM
# ==============================================================================
def is_pump_locked(base, now):
    rec = locks["pump"].get(base)
    if not rec:
        return False
    if PUMP_REPEAT_HOURS <= 0:
        return True
    return (now - float(rec.get("locked_at", 0))) < PUMP_REPEAT_HOURS * 3600.0


def evaluate_pump(sym, price, vol24h, now):
    base, quote = extract_base_quote(sym)
    if not base:
        return
    min_vol = 150_000 if quote == "USDT" else (3.0 if quote == "BTC" else 250.0)
    fast_th_pct = 4.0 if quote == "USDT" else 3.8
    breakout_th_pct = 3.2
    if price <= 0 or vol24h < min_vol:
        return

    with state_lock:
        hist = price_history.setdefault(sym, [])
        hist.append((now, price))
        cutoff_15m = now - 900.0
        cutoff_7m = now - 420.0
        while hist and hist[0][0] < cutoff_15m:
            hist.pop(0)
        if len(hist) < 2:
            return

        min15m = hist[0]
        min7m = None
        for pt in hist:
            if pt[1] < min15m[1]:
                min15m = pt
            if pt[0] >= cutoff_7m and (min7m is None or pt[1] < min7m[1]):
                min7m = pt
        if min7m is None:
            min7m = min15m

        pct7m = ((price - min7m[1]) / min7m[1]) * 100.0 if min7m[1] > 0 else 0.0
        pct15m = ((price - min15m[1]) / min15m[1]) * 100.0 if min15m[1] > 0 else 0.0

        if sym in tickers:
            tickers[sym]["win7m"] = round(pct7m, 2)

        if not bot_running:
            return
        if pct7m < fast_th_pct and pct15m < breakout_th_pct:
            return
        if is_pump_locked(base, now):
            return
        if now < verify_cooldown.get(sym, 0.0):
            return

        locks["pump"][base] = {"symbol": sym, "locked_at": now}
        hist_copy = list(hist)

    threading.Thread(
        target=_verify_and_emit_pump,
        args=(sym, base, quote, price, vol24h, min7m, min15m, hist_copy),
        daemon=True,
    ).start()


def _verify_and_emit_pump(sym, base, quote, approx_cur, approx_vol, min7m_pt, min15m_pt, hist_copy):
    fast_th_pct = 4.0 if quote == "USDT" else 3.8
    breakout_th_pct = 3.2
    try:
        live_cur, live_vol = approx_cur, approx_vol
        try:
            tk = binance_rest_get(f"/api/v3/ticker/24hr?symbol={urllib.parse.quote(sym)}", timeout=4)
            if isinstance(tk, dict):
                live_cur = float(tk.get("lastPrice") or approx_cur)
                live_vol = float(tk.get("quoteVolume") or approx_vol)
        except Exception:
            pass

        c1m = fetch_klines(sym, "1m", limit=45)
        c15m = fetch_klines(sym, "15m", limit=24)

        recent_1m_7 = c1m[-7:] if c1m else []
        recent_1m_15 = c1m[-15:] if c1m else []
        bar_min_7 = min((b["low"] for b in recent_1m_7 if b["low"] > 0), default=min7m_pt[1])
        verified_min_7 = min(min7m_pt[1], bar_min_7)
        verified_pct_7 = ((live_cur - verified_min_7) / verified_min_7) * 100.0 if verified_min_7 > 0 else 0.0

        cur_15m = c15m[-1] if c15m else None
        prev_15m = c15m[-2] if len(c15m) > 1 else None
        bar_min_15 = min(
            [b["low"] for b in recent_1m_15 if b["low"] > 0] + ([cur_15m["low"]] if cur_15m else []),
            default=min15m_pt[1],
        )
        verified_min_15 = min(min15m_pt[1], bar_min_15)
        verified_pct_15 = ((live_cur - verified_min_15) / verified_min_15) * 100.0 if verified_min_15 > 0 else 0.0

        def qvol(b):
            return b.get("quoteVol", 0.0) if b.get("quoteVol", 0.0) > 0 else b["volume"] * b["close"]

        base_15m = c15m[-11:-1] if len(c15m) > 1 else []
        avg_15m_q = (sum(qvol(b) for b in base_15m) / len(base_15m)) if len(base_15m) >= 3 else 0.0
        now_sec = int(time.time())
        elapsed_15m = max(90, min(900, now_sec - cur_15m["openTime"])) if cur_15m else 900
        cur_15m_paced_q = (qvol(cur_15m) * (900.0 / elapsed_15m)) if cur_15m else 0.0
        prev_15m_green_q = (
            qvol(prev_15m)
            if prev_15m and prev_15m["open"] > 0 and ((prev_15m["close"] - prev_15m["open"]) / prev_15m["open"]) * 100.0 >= 2.0
            else 0.0
        )
        active_15m_q = max(cur_15m_paced_q, prev_15m_green_q) if cur_15m else 0.0
        rvol_15m = (active_15m_q / avg_15m_q) if avg_15m_q > 0 else 1.0

        last_3m = c1m[-3:] if c1m else []
        base_1m = c1m[-13:-3] if len(c1m) >= 6 else []
        avg_1m_recent = (sum(qvol(b) for b in last_3m) / len(last_3m)) if last_3m else 0.0
        max_1m_recent = max((qvol(b) for b in last_3m), default=0.0)
        avg_1m_base = (sum(qvol(b) for b in base_1m) / len(base_1m)) if len(base_1m) >= 3 else 0.0
        rvol_1m_avg = (avg_1m_recent / avg_1m_base) if avg_1m_base > 0 else 1.0
        rvol_1m_peak = ((max_1m_recent / avg_1m_base) * 0.75) if avg_1m_base > 0 else 1.0
        rvol = round(max(rvol_15m, rvol_1m_avg, rvol_1m_peak), 1)

        candle_high = max(live_cur, cur_15m["high"] if cur_15m else live_cur)
        candle_low = min(verified_min_15, cur_15m["low"] if cur_15m else verified_min_15)
        hold_ratio = (live_cur - candle_low) / max(candle_high - candle_low, 1e-12)
        is_green_15m = True
        if cur_15m:
            is_green_15m = (live_cur > cur_15m["open"]) or (
                bool(prev_15m) and live_cur > prev_15m["open"] and prev_15m["close"] > prev_15m["open"]
            )

        fast_ok = verified_pct_7 >= fast_th_pct and hold_ratio >= 0.35
        breakout_15m_ok = verified_pct_15 >= breakout_th_pct and rvol >= 1.4 and is_green_15m and hold_ratio >= 0.45

        if not fast_ok and not breakout_15m_ok:
            with state_lock:
                locks["pump"].pop(base, None)
                verify_cooldown[sym] = time.time() + 20.0
            return

        use_fast = fast_ok and (not breakout_15m_ok or verified_pct_7 >= fast_th_pct)
        verified_min = verified_min_7 if use_fast else verified_min_15
        verified_pct = verified_pct_7 if use_fast else verified_pct_15
        ref_ts = min7m_pt[0] if use_fast else min15m_pt[0]
        mins = max(1, min(7 if use_fast else 15, round((time.time() - ref_ts) / 60.0)))

        with state_lock:
            price_history.pop(sym, None)
            verify_cooldown.pop(sym, None)

        vol_str = f"{round(live_vol):,}" if quote == "USDT" else f"{live_vol:.2f}"
        tf_label = "1DK CANLI"
        chart_bars = c1m
        if not chart_bars or len(chart_bars) < 5:
            tf_label = "15SN CANLI"
            buckets = {}
            for ts_p, pr in hist_copy:
                bk = (int(ts_p) // 15) * 15
                if bk not in buckets:
                    buckets[bk] = {"openTime": bk, "open": pr, "high": pr, "low": pr, "close": pr, "volume": 1.0}
                else:
                    b = buckets[bk]
                    b["high"] = max(b["high"], pr)
                    b["low"] = min(b["low"], pr)
                    b["close"] = pr
                    b["volume"] += 1.0
            chart_bars = [buckets[k] for k in sorted(buckets.keys())]

        lock_desc = f"{int(PUMP_REPEAT_HOURS)} saatlik dalga kilidi aktif." if PUMP_REPEAT_HOURS > 0 else "Tekil coin kilidi aktif."
        png = render_pump_png(sym, tf_label, chart_bars, live_cur, verified_min)
        caption = (
            f"⚡ <b>SPOT ANLIK PUMP DEDEKTÖRÜ — #{html.escape(sym)}</b>\n\n"
            f"📈 <b>Değişim:</b> +{verified_pct:.2f}% ({mins} dk içinde!)\n"
            f"💰 <b>Başlangıç:</b> {fmt_price(verified_min)} ➡️ <b>Anlık:</b> {fmt_price(live_cur)}\n"
            f"💧 <b>24s Parite Hacmi:</b> {vol_str} ({quote})\n"
            f"🔒 {lock_desc}"
        )
        record_pump_and_notify(sym, base, live_cur, caption, png)
    except Exception as exc:
        with state_lock:
            locks["pump"].pop(base, None)
        log(f"[HATA] {sym} doğrulama hatası: {exc}")


# ==============================================================================
# PİK TAKİBİ, GÜNÜN ZAFERİ (21:00 TR), TOP 24 SAAT (23:00 TR) & DUYURULAR (60 SN)
# ==============================================================================
def update_signal_peaks():
    changed = False
    with state_lock:
        for s in signals_log:
            tk = tickers.get(s["symbol"])
            if not tk or tk["price"] <= 0:
                continue
            p = tk["price"]
            s["current"] = p
            pct = ((p - s["entry"]) / s["entry"]) * 100.0
            if pct > s.get("peak_pct", 0.0):
                s["peak_pct"] = round(pct, 2)
                s["peak_price"] = p
                changed = True
    if changed:
        save_state()


def build_victory_report(now_ts=None):
    now_ts = now_ts or time.time()
    cutoff = now_ts - 86400
    with state_lock:
        recent = [s for s in signals_log if s["created_at"] >= cutoff and s.get("peak_pct", 0.0) >= 3.0]
    best_by_sym = {}
    for s in recent:
        prev = best_by_sym.get(s["symbol"])
        if not prev or s["peak_pct"] > prev["peak_pct"]:
            best_by_sym[s["symbol"]] = s
    ranked = sorted(best_by_sym.values(), key=lambda x: x["peak_pct"], reverse=True)
    if not ranked:
        return "🏆 <b>GÜNÜN ZAFERİ (21:00 TR)</b>\n\nSon 24 saatte +%3 ve üzeri pik yapan Spot Pump sinyali henüz oluşmadı."
    lines = ["🏆 <b>GÜNÜN ZAFERİ — SPOT PUMP SIRALAMASI (21:00 TR)</b>\n"]
    for i, s in enumerate(ranked[:15], 1):
        lines.append(
            f"{i}. <b>#{html.escape(s['symbol'])}</b> · <b>+{s['peak_pct']:.2f}%</b> | ${fmt_price(s['entry'])} ➡️ ${fmt_price(s['peak_price'])}"
        )
    return "\n".join(lines)


def build_top24_report(now_ts=None):
    now_ts = now_ts or time.time()
    cutoff = now_ts - 86400
    with state_lock:
        recent = [s for s in signals_log if s["created_at"] >= cutoff]
    best_by_sym = {}
    for s in recent:
        prev = best_by_sym.get(s["symbol"])
        if not prev or s["peak_pct"] > prev["peak_pct"]:
            best_by_sym[s["symbol"]] = s
    ranked = sorted(best_by_sym.values(), key=lambda x: x["peak_pct"], reverse=True)
    if not ranked:
        return "📊 <b>TOP 24 SAAT ÖZETİ (23:00 TR)</b>\n\nSon 24 saatte kaydedilen Spot Pump sinyali bulunmuyor."
    lines = ["📊 <b>TOP 24 SAAT SPOT PUMP ÖZETİ (23:00 TR)</b>\n"]
    for i, s in enumerate(ranked[:20], 1):
        lines.append(
            f"{i}. <b>#{html.escape(s['symbol'])}</b> · Pik: <b>+{s['peak_pct']:.2f}%</b> | Anlık: ${fmt_price(s['current'])}"
        )
    return "\n".join(lines)


def translate_binance_title_tr(title):
    tr = title.strip()
    pairs = (
        ("Binance Will List", "Binance Listeliyor:"),
        ("Binance Will Add", "Binance Ekliyor:"),
        ("Binance Will Support", "Binance Destekliyor:"),
        ("Binance Has Completed the", "Binance Tamamladı:"),
        ("Binance Futures Will Launch", "Binance Vadeli Başlatıyor:"),
        ("Token Merge", "Token Birleşmesi (Merge)"),
        ("Contract Swap", "Kontrat Değişimi (Swap)"),
        ("Withdrawals Reopened", "Çekim İşlemleri Yeniden Açıldı"),
        ("Notice of Removal of Spot Trading Pairs", "Spot İşlem Çiftleri Kaldırma (Delist)"),
        ("Binance Will Delist", "Binance Delist Ediyor:"),
        ("Network Upgrade", "Ağ Güncellemesi"),
    )
    for eng, tur in pairs:
        tr = re.sub(re.escape(eng), tur, tr, flags=re.IGNORECASE)
    return tr


def poll_binance_announcements():
    global news_seeded
    catalogs = ((48, "Yeni Listeleme"), (49, "Binance Duyuru"), (161, "Delist / Kaldırma"))
    newly = []
    for cat_id, cat_label in catalogs:
        try:
            url = f"https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query?catalogId={cat_id}&pageNo=1&pageSize=8"
            data = http_get_json(url, timeout=8)
            arts = data.get("data", {}).get("articles", []) or []
            for art in arts:
                aid = str(art.get("id") or art.get("code") or art.get("title") or "")
                if not aid or aid in news_seen_ids:
                    continue
                news_seen_ids.add(aid)
                title_orig = str(art.get("title") or "").strip()
                code = str(art.get("code") or "")
                link = f"https://www.binance.com/en/support/announcement/detail/{code}" if code else "https://www.binance.com/en/support/announcement"
                newly.append((cat_label, translate_binance_title_tr(title_orig), link))
        except Exception:
            pass
    if news_seeded and newly:
        for cat_label, title_tr, link in newly[:5]:
            telegram_send_message(f"📰 <b>Binance | {html.escape(cat_label)}</b>\n{html.escape(title_tr)}\n{link}")
    if newly:
        save_state()
    news_seeded = True


# ==============================================================================
# TELEGRAM KOMUTLARI & ETKİLEŞİM (/start, /test, /24s, /piyasa, /durum, /sifirla)
# ==============================================================================
def build_main_keyboard():
    toggle_label = "⏸ Taramayı Duraklat" if bot_running else "▶️ Taramayı Başlat"
    return {
        "inline_keyboard": [
            [
                {"text": "📈 24s En Yükseğe Gidenler", "callback_data": "market_24h"},
                {"text": "🖼 Piyasa Görseli", "callback_data": "market_img"},
            ],
            [
                {"text": "🧪 Test Bildirimi Gönder", "callback_data": "send_test"},
                {"text": "📊 Durum", "callback_data": "status"},
            ],
            [
                {"text": "🔓 Kilitleri Sıfırla", "callback_data": "reset_locks"},
                {"text": toggle_label, "callback_data": "toggle_bot"},
            ],
        ]
    }


def get_sorted_market(mode="24h"):
    with state_lock:
        rows = [t for t in tickers.values() if t["quote"] == "USDT" and t.get("change24h", 0.0) > 0]
    if mode == "7m":
        rows.sort(key=lambda x: x.get("win7m", 0.0), reverse=True)
    elif mode == "vol":
        rows.sort(key=lambda x: x.get("volume24h", 0.0), reverse=True)
    else:
        rows.sort(key=lambda x: x.get("change24h", 0.0), reverse=True)
    return rows


def format_market_text(mode="24h"):
    rows = get_sorted_market(mode)[:15]
    titles = {"24h": "24s En Yükseğe Gidenler", "7m": "7dk İvme %", "vol": "24s Hacim (Yükselenler)"}
    lines = [f"📈 <b>ANLIK SPOT PİYASA ({titles.get(mode, '24s %')})</b>\n"]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{i:02d}. <b>{html.escape(r['symbol'])}</b> · ${fmt_price(r['price'])} | "
            f"7dk: {r.get('win7m', 0.0):+.2f}% | 24s: <b>{r.get('change24h', 0.0):+.2f}%</b> | {fmt_vol(r.get('volume24h', 0.0))}"
        )
    if not rows:
        lines.append("Veri henüz yükleniyor...")
    return "\n".join(lines)


def send_test_notification(target_cid=None):
    c1m = fetch_klines("BTCUSDT", "1m", limit=45)
    cur_p = c1m[-1]["close"] if c1m else 65000.0
    bas_p = round(cur_p / 1.0464, 4)
    msg = (
        f"⚡ <b>SPOT ANLIK PUMP DEDEKTÖRÜ — #BTCUSDT (TEST)</b>\n\n"
        f"📈 <b>Değişim:</b> +4.64% (7 dk içinde!)\n"
        f"💰 <b>Başlangıç:</b> {fmt_price(bas_p)} ➡️ <b>Anlık:</b> {fmt_price(cur_p)}\n"
        f"💧 <b>24s Parite Hacmi:</b> 1,077,624 (USDT)\n"
        f"🔒 Tekil coin kilidi aktif."
    )
    png = render_pump_png("BTCUSDT", "1DK CANLI", c1m, cur_p, bas_p) if c1m else b""
    if target_cid:
        if png:
            res = tg_send_photo_to(target_cid, png, msg)
            if res.get("ok"):
                return
        tg_request("sendMessage", {"chat_id": target_cid, "text": msg, "parse_mode": "HTML"})
    else:
        telegram_send_photo(png, msg) if png else telegram_send_message(msg)


def handle_telegram_command(cid, text):
    global bot_running
    register_chat_id(cid)
    cmd = text.strip().split()[0].split("@")[0].lower()

    if cmd in ("/start", "/help", "/menu"):
        msg = (
            f"⚡ <b>SPOT ANLIK PUMP DEDEKTÖRÜ (7/24 Aktif)</b>\n"
            f"🆔 Sohbet ID: <code>{cid}</code> (kaydedildi)\n"
            f"Komutlar: /test · /24s · /piyasa · /piyasagorsel · /durum · /sifirla"
        )
        tg_request("sendMessage", {"chat_id": cid, "text": msg, "parse_mode": "HTML", "reply_markup": build_main_keyboard()})
    elif cmd == "/test":
        send_test_notification(cid)
    elif cmd == "/durum":
        uptime_m = int((time.time() - started_at) // 60)
        msg = (
            f"📊 <b>Bot Durumu</b>\n"
            f"• Durum: {'🟢 7/24 Çalışıyor' if bot_running else '🟡 Duraklatıldı'}\n"
            f"• Çalışma Süresi: {uptime_m} dk\n"
            f"• WebSocket: {'🟢 Bağlı' if ws_connected else '🟡 REST Yedek'}\n"
            f"• Aktif Spot Çift: {len(tickers)}\n"
            f"• Tarama Turu: {pump_loops}\n"
            f"• Aktif Kilit: {len(locks['pump'])}\n"
            f"• Kayıtlı Sohbetler: {', '.join(get_target_chats())}\n"
            f"• Mirror: {BINANCE_MIRRORS[preferred_mirror_idx]}"
        )
        tg_request("sendMessage", {"chat_id": cid, "text": msg, "parse_mode": "HTML", "reply_markup": build_main_keyboard()})
    elif cmd in ("/piyasa", "/24s"):
        kb = {
            "inline_keyboard": [[
                {"text": "24s %", "callback_data": "market_24h"},
                {"text": "7dk %", "callback_data": "market_7m"},
                {"text": "Hacim", "callback_data": "market_vol"},
                {"text": "🖼 Görsel", "callback_data": "market_img"},
            ]]
        }
        tg_request("sendMessage", {"chat_id": cid, "text": format_market_text("24h"), "parse_mode": "HTML", "reply_markup": kb})
    elif cmd == "/piyasagorsel":
        png = render_market_radar_png(get_sorted_market("24h"))
        tg_send_photo_to(cid, png, "🖼 <b>Anlık Spot Piyasa — 24s En Yükseğe Gidenler</b>")
    elif cmd == "/sifirla":
        with state_lock:
            locks["pump"] = {}
            save_state()
        tg_request("sendMessage", {"chat_id": cid, "text": "🔓 Tüm pump kilitleri sıfırlandı."})


def telegram_commands_loop():
    global bot_running
    if not BOT_TOKEN:
        return
    offset = 0
    while True:
        try:
            res = tg_request("getUpdates", {"offset": offset, "timeout": 25}, timeout=32)
            if not res.get("ok"):
                if res.get("error_code") == 409:
                    log("[KRİTİK UYARI] Telegram 409 Conflict! Aynı BOT_TOKEN şu anda başka bir yerde daha çalışıyor.")
                    time.sleep(15)
                else:
                    time.sleep(5)
                continue
            for upd in res.get("result", []):
                offset = max(offset, upd["update_id"] + 1)
                if "message" in upd and "text" in upd["message"]:
                    cid = str(upd["message"]["chat"]["id"])
                    handle_telegram_command(cid, upd["message"]["text"])
                elif "callback_query" in upd:
                    cq = upd["callback_query"]
                    msg_obj = cq.get("message") or {}
                    chat_obj = msg_obj.get("chat") or {}
                    cid = str(chat_obj.get("id") or "")
                    data = str(cq.get("data", ""))
                    if cid:
                        register_chat_id(cid)
                    tg_request("answerCallbackQuery", {"callback_query_id": cq["id"]})
                    if not cid:
                        continue
                    if data in ("market_24h", "market_7m", "market_vol"):
                        mode = data.split("_")[1]
                        tg_request("sendMessage", {"chat_id": cid, "text": format_market_text(mode), "parse_mode": "HTML"})
                    elif data == "market_img":
                        handle_telegram_command(cid, "/piyasagorsel")
                    elif data == "send_test":
                        send_test_notification(cid)
                    elif data == "status":
                        handle_telegram_command(cid, "/durum")
                    elif data == "reset_locks":
                        handle_telegram_command(cid, "/sifirla")
                    elif data == "toggle_bot":
                        bot_running = not bot_running
                        tg_request(
                            "sendMessage",
                            {
                                "chat_id": cid,
                                "text": "🟢 Tarama başlatıldı." if bot_running else "🟡 Tarama duraklatıldı.",
                                "reply_markup": build_main_keyboard(),
                            },
                        )
        except Exception as exc:
            log(f"[TG Polling HATA] {exc}")
            time.sleep(5)


# ==============================================================================
# CANLI PİYASA DÖNGÜLERİ (WEBSOCKET + REST + ZAMANLAYICI)
# ==============================================================================
def process_ticker(sym, price, vol24h, now, change24h=None):
    if price <= 0 or is_excluded(sym):
        return
    if active_trading_symbols and sym not in active_trading_symbols:
        return
    base, quote = extract_base_quote(sym)
    if not base:
        return
    with state_lock:
        prev = tickers.get(sym, {})
        chg_val = change24h if change24h is not None else prev.get("change24h", 0.0)
        tickers[sym] = {
            "symbol": sym,
            "base": base,
            "quote": quote,
            "price": price,
            "volume24h": vol24h,
            "change24h": round(float(chg_val), 2),
            "win7m": prev.get("win7m", 0.0),
            "updated_at": now,
        }
    evaluate_pump(sym, price, vol24h, now)


def ws_stream_loop():
    global ws_connected, last_tick_ts
    host = "stream.binance.com"
    path = "/ws/!miniTicker@arr"
    while True:
        ssock = None
        try:
            ctx = ssl.create_default_context()
            sock = socket.create_connection((host, 9443), timeout=15)
            ssock = ctx.wrap_socket(sock, server_hostname=host)
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            req = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}:9443\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n\r\n"
            )
            ssock.sendall(req.encode("ascii"))
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = ssock.recv(4096)
                if not chunk:
                    raise EOFError("WS handshake EOF")
                buf += chunk
            ws_connected = True
            frame_buf = bytearray(buf.split(b"\r\n\r\n", 1)[1])
            while True:
                opcode, payload = _ws_read_frame(ssock, frame_buf)
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    _ws_send_frame(ssock, payload, opcode=0xA)
                    continue
                if opcode in (0x1, 0x2):
                    arr = json.loads(payload.decode("utf-8"))
                    if isinstance(arr, list):
                        now = time.time()
                        for item in arr:
                            c_p = float(item.get("c") or 0)
                            o_p = float(item.get("o") or 0)
                            chg = ((c_p - o_p) / o_p) * 100.0 if o_p > 0 else 0.0
                            process_ticker(
                                item.get("s", ""),
                                c_p,
                                float(item.get("q") or 0),
                                now,
                                change24h=chg,
                            )
                        last_tick_ts = now
                        update_signal_peaks()
        except Exception:
            ws_connected = False
            time.sleep(5)
        finally:
            try:
                if ssock:
                    ssock.close()
            except Exception:
                pass


def rest_seed_once():
    global pump_loops, last_tick_ts
    raw = binance_rest_get("/api/v3/ticker/24hr", timeout=10)
    if not isinstance(raw, list):
        return
    now = time.time()
    now_ms = int(now * 1000)
    for item in raw:
        sym = str(item.get("symbol", ""))
        if not sym or is_excluded(sym):
            continue
        if active_trading_symbols and sym not in active_trading_symbols:
            continue
        bid = float(item.get("bidPrice") or 0.0)
        ask = float(item.get("askPrice") or 0.0)
        cnt = int(float(item.get("count") or 0))
        close_ms = int(float(item.get("closeTime") or 0))
        is_fresh = bid > 0 and ask > 0 and cnt > 0 and (close_ms <= 0 or (now_ms - close_ms) <= 1_800_000)
        if not is_fresh:
            with state_lock:
                tickers.pop(sym, None)
                price_history.pop(sym, None)
            continue
        process_ticker(
            sym,
            float(item.get("lastPrice") or 0),
            float(item.get("quoteVolume") or 0),
            now,
            change24h=float(item.get("priceChangePercent") or 0),
        )
    with state_lock:
        pump_loops += 1
        last_tick_ts = now
    update_signal_peaks()


def rest_seed_loop():
    last_ex_refresh = 0.0
    while True:
        try:
            now = time.time()
            if now - last_ex_refresh > 900.0 or not active_trading_symbols:
                refresh_active_trading_symbols()
                last_ex_refresh = now
            rest_seed_once()
        except Exception as exc:
            log(f"[REST Tarama HATA] {exc}")
        time.sleep(25 if ws_connected else 4)


def scheduler_and_news_loop():
    last_news_ts = 0.0
    last_ping_ts = 0.0
    while True:
        try:
            now = time.time()
            if now - last_news_ts >= 60:
                poll_binance_announcements()
                last_news_ts = now

            dt = tr_now()
            today = dt.strftime("%Y-%m-%d")
            if dt.hour == 21 and locks["reports"].get("victory_day") != today:
                locks["reports"]["victory_day"] = today
                save_state()
                telegram_send_message(build_victory_report(now))
            if dt.hour == 23 and locks["reports"].get("top24_day") != today:
                locks["reports"]["top24_day"] = today
                save_state()
                telegram_send_message(build_top24_report(now))

            if RENDER_EXTERNAL_URL and (now - last_ping_ts) > 480:
                last_ping_ts = now
                try:
                    urllib.request.urlopen(f"{RENDER_EXTERNAL_URL.rstrip('/')}/health", timeout=5).read()
                except Exception:
                    pass
        except Exception as exc:
            log(f"[Zamanlayıcı HATA] {exc}")
        time.sleep(15)


# ==============================================================================
# HEALTH CHECK HTTP SUNUCUSU (GET + HEAD 200 OK — UPTIMEROBOT / RENDER)
# ==============================================================================
class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        body = json.dumps({
            "ok": True,
            "running": bot_running,
            "ws": ws_connected,
            "bot_token_present": bool(BOT_TOKEN),
            "bot_username": bot_username,
            "telegram_ok": telegram_ok,
            "last_tg_error": last_tg_error,
            "subscribed_chats": get_target_chats(),
            "pairs": len(tickers),
            "pump_loops": pump_loops,
            "signals_count": len(signals_log),
            "last_tick_age_sec": round(time.time() - last_tick_ts),
            "uptime_sec": round(time.time() - started_at),
            "engines": ["PUMP"],
        }, ensure_ascii=False).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def startup_telegram_check():
    global bot_username
    if not BOT_TOKEN:
        log("[KRİTİK] BOT_TOKEN tanımlı değil! Render Environment Variables kısmına BOT_TOKEN ekleyin.")
        return
    me = tg_request("getMe", {})
    if not me.get("ok"):
        log(f"[KRİTİK] Telegram getMe başarısız: {me.get('description')}")
        return
    bot_username = str(me.get("result", {}).get("username", ""))
    log(f"[Telegram] 7/24 Bağlandı -> @{bot_username} | Hedefler: {get_target_chats()}")
    tg_request("deleteWebhook", {"drop_pending_updates": False})
    telegram_send_message(f"⚡ <b>Spot Anlık Pump Dedektörü 7/24 aktif</b> (@{html.escape(bot_username)} · Test: /test)")


def main():
    load_state()
    log(f"[Spot Pump Bot] Başlatılıyor... Port={PORT}, ChatID={CHAT_ID}")
    srv = ReusableTCPServer(("0.0.0.0", PORT), HealthHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"[HTTP] Health sunucusu 0.0.0.0:{PORT} üzerinde aktif (GET & HEAD 200 OK)")

    startup_telegram_check()

    threads = {}
    targets = {
        "ws": ws_stream_loop,
        "rest": rest_seed_loop,
        "sched": scheduler_and_news_loop,
    }
    if BOT_TOKEN:
        targets["tg"] = telegram_commands_loop

    for name, fn in targets.items():
        t = threading.Thread(target=fn, daemon=True)
        t.start()
        threads[name] = t

    # 7/24 Watchdog: herhangi bir iş parçacığı durursa otomatik yeniden başlatır
    while True:
        time.sleep(15)
        for name, fn in targets.items():
            if not threads[name].is_alive():
                log(f"[WATCHDOG] '{name}' döngüsü yeniden başlatılıyor...")
                t = threading.Thread(target=fn, daemon=True)
                t.start()
                threads[name] = t


if __name__ == "__main__":
    while True:
        try:
            main()
        except Exception as fatal:
            log(f"[7/24 RECOVER] Beklenmeyen hata: {fatal} — 5 sn sonra yeniden başlatılıyor...")
            time.sleep(5)

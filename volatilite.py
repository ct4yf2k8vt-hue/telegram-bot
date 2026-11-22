#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spot Akis Terminali - Render Python 3 Telegram Botu (Zero-Dependency)
Aktif Sinyal Motorlari:
  1. 0-7 dk Spot Anlik Pump (PUMP) - Tekil Coin Kilidi + Canli 1dk/15sn Grafik
  2. 15 dk Yapi Kirilimi (YAPI) - Onceki tepe/dip kirilimi (MSS) + Canli 15dk Grafik
  3. Gunun Kazanani (6 saatte bir) - 24s en cok yukselen coin + Canli 1G Grafik
Otomatik Ozet & Duyurular:
  - Gunun Zaferi (21:00 TR)
  - Top 24 Saat Ozeti (23:00 TR)
  - Binance Resmi Duyurulari (60 sn)
"""

import base64
import datetime
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

# ==============================================================================
# KONFIGURASYON
# ==============================================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "1623907197").strip()
PORT = int(os.environ.get("PORT", "10000"))
PUMP_REPEAT_HOURS = float(os.environ.get("PUMP_REPEAT_HOURS", "0"))
STATE_FILE = os.environ.get("STATE_FILE", "/tmp/spot_bot_state.json")

# 15 dk yapi kirilimi ayarlari
YAPI_MIN_HACIM = float(os.environ.get("YAPI_MIN_HACIM", "5000000"))   # 24s USDT hacim alt siniri
YAPI_MAX_COIN = int(os.environ.get("YAPI_MAX_COIN", "250"))           # hacme gore en buyuk N coin
YAPI_TEKRAR_SAAT = float(os.environ.get("YAPI_TEKRAR_SAAT", "4"))     # ayni coin + ayni yon icin bekleme
YAPI_SADECE_DONUS = os.environ.get("YAPI_SADECE_DONUS", "1") != "0"   # 1: sadece trend donusu (MSS), 0: devam kirilimlari da (BOS)
YAPI_MARJ = 0.0015                                                    # seviyeyi %0.15 asinca kirilim sayilir

BINANCE_MIRRORS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://data-api.binance.vision",
]

EXCLUDED_PREFIXES = ("USDC", "FDUSD", "TUSD", "USDP", "EUR", "USDE", "USD1")
EXCLUDED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

TR_TZ = datetime.timezone(datetime.timedelta(hours=3))

# ==============================================================================
# DURUM & KILIT YONETIMI
# ==============================================================================
state_lock = threading.Lock()
tickers = {}          # sym -> dict
price_history = {}    # sym -> list[(ts_sec, price)]
signals_log = []      # list[dict] (24s pik takibi ve raporlar)
news_seen_ids = set()
news_seeded = False
ws_connected = False
ban_until_ts = 0.0
preferred_mirror_idx = 0

struct_levels = {}    # sym -> 15dk yapi seviyeleri (YAPI motoru)
yapi_cd = {}          # (sym, yon) -> bekleme bitis zamani
yapi_zamanlari = []   # son sinyal zamanlari (toplu hareket siniri)

locks = {
    "pump": {},   # base -> {"symbol": str, "locked_at": float}
    "reports": {"victory_day": "", "top24_day": ""},
}


def load_state():
    global signals_log
    try:
        if not os.path.exists(STATE_FILE):
            return
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        raw_locks = raw.get("locks", {})
        if isinstance(raw_locks.get("pump"), dict):
            locks["pump"] = raw_locks["pump"]
        if isinstance(raw_locks.get("reports"), dict):
            locks["reports"].update(raw_locks["reports"])
        raw_sigs = raw.get("signals", [])
        if isinstance(raw_sigs, list):
            signals_log = [s for s in raw_sigs if isinstance(s, dict) and s.get("engine") == "PUMP"][-300:]
    except Exception:
        pass


def save_state():
    try:
        with state_lock:
            payload = {
                "locks": locks,
                "signals": signals_log[-300:],
            }
        tmp_path = STATE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, STATE_FILE)
    except Exception:
        pass


# ==============================================================================
# YARDIMCI FONKSIYONLAR
# ==============================================================================
def extract_base_quote(sym):
    for q in ("USDT", "BTC", "BNB"):
        if sym.endswith(q) and len(sym) > len(q):
            return sym[:-len(q)], q
    return None, None


def is_excluded(sym):
    if any(sym.startswith(p) for p in EXCLUDED_PREFIXES):
        return True
    if any(sym.endswith(s) for s in EXCLUDED_SUFFIXES):
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


def _chg_from_open(item):
    o = float(item.get("o") or 0)
    c = float(item.get("c") or 0)
    return (c - o) / o * 100.0 if o > 0 else 0.0


def tr_now():
    return datetime.datetime.now(TR_TZ)


def update_live_candle(candles, live_price):
    """Son mumu anlik canli fiyatla gunceller (gorselin daima canli olmasini saglar)."""
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
            "User-Agent": "Mozilla/5.0 (compatible; SpotPumpBot/1.0)",
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
    """Binance WebSocket API (REST 418 kotasindan etkilenmez)."""
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
            "openTime": int(k[0]) // 1000,
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        }
        for k in raw
    ]


def fetch_1m_klines(sym, limit=45):
    try:
        raw = binance_rest_get(
            f"/api/v3/klines?symbol={urllib.parse.quote(sym)}&interval=1m&limit={limit}",
            timeout=7,
        )
        if isinstance(raw, list) and raw:
            return _parse_binance_klines(raw)
    except Exception:
        pass
    try:
        raw = ws_api_request("klines", {"symbol": sym, "interval": "1m", "limit": limit}, timeout=7)
        if isinstance(raw, list) and raw:
            return _parse_binance_klines(raw)
    except Exception:
        pass
    try:
        url = f"https://api.bybit.com/v5/market/kline?category=spot&symbol={urllib.parse.quote(sym)}&interval=1&limit={limit}"
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
                }
                for r in reversed(rows)
            ]
    except Exception:
        pass
    return []


def fetch_klines(sym, interval, limit):
    """Genel mum cekici: Binance REST -> Binance WS-API -> Bybit (ayni yedek zinciri)."""
    q = urllib.parse.quote(sym)
    try:
        raw = binance_rest_get(f"/api/v3/klines?symbol={q}&interval={interval}&limit={limit}", timeout=7)
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
    try:
        by_iv = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}.get(interval)
        if by_iv:
            url = f"https://api.bybit.com/v5/market/kline?category=spot&symbol={q}&interval={by_iv}&limit={limit}"
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
                    }
                    for r in reversed(rows)
                ]
    except Exception:
        pass
    return []


# ==============================================================================
# SIFIR BAGIMLILIKLI PNG MUM GRAFIGI OLUSTURUCU (CANLI 1DK / 15SN / 15DK)
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


def render_pump_png(symbol, tf_label, candles, live_price, start_price, tur="SPOT PUMP", cizgi="BAS"):
    """Saf Python PNG mum grafigi (canli fiyat + baslangic/seviye cizgisi + hacim)."""
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

    def draw_text(x, y, text, col, scale=1):
        cx = x
        for ch in text.upper():
            glyph = FONT_5X7.get(ch, FONT_5X7[" "])
            for gx in range(5):
                bits = glyph[gx]
                for gy in range(7):
                    if bits & (1 << gy):
                        if scale == 1:
                            set_px(cx + gx, y + gy, col)
                        else:
                            fill_rect(cx + gx * scale, y + gy * scale, cx + gx * scale + scale - 1, y + gy * scale + scale - 1, col)
            cx += 6 * scale

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

    draw_text(pad_l, 14, f"{symbol} | {tf_label} | {tur} | ANLIK ${fmt_price(live_price)}", TEXT, scale=2)

    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        gy = int(pad_t + price_h * frac)
        draw_line(pad_l, gy, pad_l + plot_w, gy, GRID, dashed=True)
        pval = hi_p - rng_p * frac
        draw_text(pad_l + plot_w + 8, gy - 3, fmt_price(pval), MUTED, scale=1)

    if start_price and start_price > 0:
        sy = y_of(start_price)
        if pad_t <= sy <= pad_t + price_h:
            draw_line(pad_l, sy, pad_l + plot_w, sy, START_COL, dashed=True)
            draw_text(pad_l + plot_w + 8, sy - 3, f"{cizgi} {fmt_price(start_price)}", START_COL, scale=1)

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
        draw_text(pad_l + plot_w + 8, ly - 3, fmt_price(live_price), (248, 250, 252), scale=1)

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


# ==============================================================================
# TELEGRAM GONDERICILER (MESAJ & FOTOGRAF)
# ==============================================================================
def telegram_send_message(text):
    if not BOT_TOKEN:
        print(f"[SINYAL/MESAJ]\n{text}\n")
        return
    try:
        body = json.dumps({
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=12).read()
    except Exception as e:
        print(f"[TG sendMessage hata] {e}")


def telegram_send_photo(png_bytes, caption):
    if len(caption) > 1000:
        caption = caption[:997] + "..."
    if not BOT_TOKEN:
        print(f"[SINYAL/GORSEL {len(png_bytes)}B]\n{caption}\n")
        return
    if not png_bytes:
        telegram_send_message(caption)
        return
    try:
        boundary = "----SpotPumpBoundary7e8a9b"
        parts = []
        for k, v in (("chat_id", CHAT_ID), ("caption", caption), ("parse_mode", "HTML")):
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8")
            )
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"chart.png\"\r\nContent-Type: image/png\r\n\r\n".encode("utf-8")
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
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        print(f"[TG sendPhoto hata] {e}")
        telegram_send_message(caption)


def record_pump_and_notify(symbol, base, entry_price, caption, png_bytes=b""):
    now = time.time()
    with state_lock:
        signals_log.append({
            "id": f"PUMP-{symbol}-{int(now)}",
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
        telegram_send_photo(png_bytes, caption)
    else:
        telegram_send_message(caption)


# ==============================================================================
# MOTOR 1: 0-7 DK SPOT ANLIK PUMP (TEKIL COIN KILIDI + CANLI 1DK/15SN GRAFIK)
# ==============================================================================
def is_pump_locked(base, now):
    rec = locks["pump"].get(base)
    if not rec:
        return False
    if PUMP_REPEAT_HOURS <= 0:
        return True
    return (now - rec["locked_at"]) < PUMP_REPEAT_HOURS * 3600


def evaluate_pump(sym, price, vol24h, now):
    base, quote = extract_base_quote(sym)
    if not base:
        return
    min_vol = 150_000 if quote == "USDT" else (3.0 if quote == "BTC" else 250.0)
    th_pct = 4.5 if quote == "USDT" else 4.0
    if price <= 0 or vol24h < min_vol:
        return

    with state_lock:
        hist = price_history.setdefault(sym, [])
        hist.append((now, price))
        cutoff = now - 420
        while hist and hist[0][0] < cutoff:
            hist.pop(0)
        if len(hist) < 2:
            return
        min_pt = min(hist, key=lambda x: x[1])
        pct = ((price - min_pt[1]) / min_pt[1]) * 100.0 if min_pt[1] > 0 else 0.0
        if pct < th_pct or is_pump_locked(base, now):
            return
        locks["pump"][base] = {"symbol": sym, "locked_at": now}
        hist_copy = list(hist)

    threading.Thread(
        target=_verify_and_emit_pump,
        args=(sym, base, quote, price, vol24h, min_pt, hist_copy),
        daemon=True,
    ).start()


def _verify_and_emit_pump(sym, base, quote, approx_cur, approx_vol, min_pt, hist_copy):
    try:
        live_cur, live_vol = approx_cur, approx_vol
        try:
            tk = binance_rest_get(f"/api/v3/ticker/24hr?symbol={urllib.parse.quote(sym)}", timeout=4)
            if isinstance(tk, dict):
                live_cur = float(tk.get("lastPrice") or approx_cur)
                live_vol = float(tk.get("quoteVolume") or approx_vol)
        except Exception:
            pass

        c1m = fetch_1m_klines(sym, limit=45)
        recent_bars = c1m[-7:] if c1m else []
        bar_min = min((b["low"] for b in recent_bars if b["low"] > 0), default=min_pt[1])
        verified_min = min(min_pt[1], bar_min)
        verified_pct = ((live_cur - verified_min) / verified_min) * 100.0
        th_pct = 4.5 if quote == "USDT" else 4.0
        if verified_pct < th_pct:
            with state_lock:
                locks["pump"].pop(base, None)
            return

        with state_lock:
            price_history.pop(sym, None)

        mins = max(1, round((time.time() - min_pt[0]) / 60))
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

        png = render_pump_png(sym, tf_label, chart_bars, live_cur, verified_min)
        caption = (
            f"⚡ <b>SPOT ANLIK PUMP DEDEKTORU — #{sym}</b>\n\n"
            f"📈 Degisim: <b>+{verified_pct:.2f}%</b> ({mins} dk icinde!)\n"
            f"💰 Baslangic: {fmt_price(verified_min)} ➡️ Anlik: <b>{fmt_price(live_cur)}</b>\n"
            f"💧 24s Parite Hacmi: {vol_str} ({quote})\n"
            f"🔒 Tekil coin kilidi aktif."
        )
        record_pump_and_notify(sym, base, live_cur, caption, png)
    except Exception:
        with state_lock:
            locks["pump"].pop(base, None)


# ==============================================================================
# MOTOR 2: 15 DK YAPI KIRILIMI (ONCEKI TEPE / DIP KIRILIMI - MSS)
#  - Dusus yapisi (alcalan tepe + alcalan dip) iken onceki tepe yukari kirilirsa -> YUKARI sinyal
#  - Yukselis yapisi (yukselen tepe + yukselen dip) iken onceki dip asagi kirilirsa -> ASAGI sinyal
#  Seviyeler 15dk kapanmis mumlardan 15 dakikada bir hesaplanir (REST yuku cok dusuk),
#  kirilim ise canli fiyat akisindan anlik yakalanir.
# ==============================================================================
def find_pivots(highs, lows, window=3):
    pivot_highs = []
    pivot_lows = []
    for i in range(window, len(highs) - window):
        if highs[i] == max(highs[i - window:i + window + 1]):
            pivot_highs.append((i, highs[i]))
        if lows[i] == min(lows[i - window:i + window + 1]):
            pivot_lows.append((i, lows[i]))
    return pivot_highs, pivot_lows


def analyze_structure(candles, now):
    """Kapanmis 15dk mumlardan izlenecek tepe/dip seviyelerini cikarir."""
    if len(candles) < 30:
        return None
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    n = len(candles)
    ph, pl = find_pivots(highs, lows, 3)
    ph = [x for x in ph if x[0] >= n - 62]
    pl = [x for x in pl if x[0] >= n - 62]
    if len(ph) < 2 or len(pl) < 2:
        return None
    if ph[-1][0] < n - 45 or pl[-1][0] < n - 45:
        return None  # son tepe/dip cok eski, yapi guncel degil
    h1, h2 = ph[-2][1], ph[-1][1]
    l1, l2 = pl[-2][1], pl[-1][1]
    last, prev = closes[-1], closes[-2]
    if last <= 0 or (h2 - l2) / last < 0.01:
        return None  # cok kucuk yapi, gurultu

    down = h2 < h1 and l2 < l1
    up = h2 > h1 and l2 > l1
    up_lv = down_lv = None
    if down:
        up_lv = (h2, "MSS")
        if not YAPI_SADECE_DONUS:
            down_lv = (l2, "BOS")
    elif up:
        down_lv = (l2, "MSS")
        if not YAPI_SADECE_DONUS:
            up_lv = (h2, "BOS")

    fresh_up = fresh_down = False
    if up_lv:
        if last > up_lv[0] * (1 + 0.0005) and prev <= up_lv[0]:
            fresh_up = True      # son kapanista kirildi
        elif last > up_lv[0]:
            up_lv = None         # kirilim cok once olmus
    if down_lv:
        if last < down_lv[0] * (1 - 0.0005) and prev >= down_lv[0]:
            fresh_down = True
        elif last < down_lv[0]:
            down_lv = None

    if not up_lv and not down_lv:
        return None
    return {
        "up": up_lv, "down": down_lv, "h2": h2, "l2": l2,
        "fresh_up": fresh_up, "fresh_down": fresh_down,
        "fired_up": False, "fired_down": False,
        "candles": candles[-70:], "updated": now,
    }


def yapi_tetikle(sym, yon, level, tur, price, lv):
    now = time.time()
    key = (sym, yon)
    with state_lock:
        if now < yapi_cd.get(key, 0):
            return
        tk = tickers.get(sym)
        if not tk or tk["volume24h"] < YAPI_MIN_HACIM:
            return
        yapi_zamanlari[:] = [t for t in yapi_zamanlari if now - t < 600]
        if len(yapi_zamanlari) >= 8:
            return  # piyasa genelinde toplu hareket: spam engeli
        yapi_zamanlari.append(now)
        yapi_cd[key] = now + YAPI_TEKRAR_SAAT * 3600
        vol = tk["volume24h"]
    threading.Thread(target=_yapi_gonder, args=(sym, yon, level, tur, price, lv, vol), daemon=True).start()


def _yapi_gonder(sym, yon, level, tur, price, lv, vol):
    try:
        mumlar = list(lv["candles"])
        son = mumlar[-1]
        mumlar.append({
            "openTime": son["openTime"] + 900, "open": son["close"],
            "high": max(son["close"], price), "low": min(son["close"], price),
            "close": price, "volume": 0.0,
        })
        png = render_pump_png(sym, "15DK CANLI", mumlar, price, level, tur="YAPI KIRILIMI", cizgi="SEVIYE")
        pct = abs(price - level) / level * 100.0
        if yon == "YUKARI":
            baslik = f"🟢 <b>15DK YAPI KIRILIMI (YUKARI) — #{sym}</b>"
            kirilan = f"📈 Onceki tepe kirildi: <b>{fmt_price(level)}</b> (+{pct:.2f}%)"
            aciklama = ("Dusen tepe/dip yapisi bozuldu: yukari donus (MSS)" if tur == "MSS"
                        else "Yukselis trendi devam ediyor (BOS)")
            iptal = f"Iptal: {fmt_price(lv['l2'])} alti"
        else:
            baslik = f"🔴 <b>15DK YAPI KIRILIMI (ASAGI) — #{sym}</b>"
            kirilan = f"📉 Onceki dip kirildi: <b>{fmt_price(level)}</b> (-{pct:.2f}%)"
            aciklama = ("Yukselen tepe/dip yapisi bozuldu: asagi donus (MSS)" if tur == "MSS"
                        else "Dusus trendi devam ediyor (BOS)")
            iptal = f"Iptal: {fmt_price(lv['h2'])} ustu"
        caption = (
            f"{baslik}\n\n"
            f"{kirilan}\n"
            f"🔄 {aciklama}\n"
            f"💰 Anlik: <b>{fmt_price(price)}</b> | {iptal}\n"
            f"💧 24s Parite Hacmi: {vol:,.0f} (USDT)\n"
            f"🔒 Ayni yon icin {YAPI_TEKRAR_SAAT:g} saat tekrar bildirim yok."
        )
        telegram_send_photo(png, caption)
        print(f"[YAPI] {sym} {yon} ({tur}) {fmt_price(level)} -> {fmt_price(price)}", flush=True)
    except Exception as e:
        print(f"[YAPI gonderim hata] {e}", flush=True)


def check_structure_break(sym, price, now):
    """Her canli fiyat guncellemesinde izlenen seviyenin kirilip kirilmadigini kontrol eder."""
    try:
        lv = struct_levels.get(sym)
        if not lv or now - lv["updated"] > 2400:
            return
        up = lv["up"]
        if up and not lv["fired_up"] and price > up[0] * (1 + YAPI_MARJ):
            lv["fired_up"] = True
            yapi_tetikle(sym, "YUKARI", up[0], up[1], price, lv)
        dn = lv["down"]
        if dn and not lv["fired_down"] and price < dn[0] * (1 - YAPI_MARJ):
            lv["fired_down"] = True
            yapi_tetikle(sym, "ASAGI", dn[0], dn[1], price, lv)
    except Exception:
        pass


def yapi_loop():
    print("[YAPI] 15dk Yapi Kirilimi motoru basladi.", flush=True)
    time.sleep(45)
    while True:
        try:
            with state_lock:
                adaylar = [(s, tk["volume24h"]) for s, tk in tickers.items()
                           if tk["quote"] == "USDT" and tk["volume24h"] >= YAPI_MIN_HACIM]
            adaylar.sort(key=lambda x: x[1], reverse=True)
            for sym, _ in adaylar[:YAPI_MAX_COIN]:
                try:
                    kl = fetch_klines(sym, "15m", 70)
                    if len(kl) < 40:
                        continue
                    kapanmis = kl[:-1]  # son mum henuz kapanmadi
                    lv = analyze_structure(kapanmis, time.time())
                    if not lv:
                        struct_levels.pop(sym, None)
                    else:
                        struct_levels[sym] = lv
                        tk = tickers.get(sym)
                        px = tk["price"] if tk and tk["price"] > 0 else kapanmis[-1]["close"]
                        if lv["fresh_up"] and lv["up"]:
                            lv["fired_up"] = True
                            yapi_tetikle(sym, "YUKARI", lv["up"][0], lv["up"][1], px, lv)
                        if lv["fresh_down"] and lv["down"]:
                            lv["fired_down"] = True
                            yapi_tetikle(sym, "ASAGI", lv["down"][0], lv["down"][1], px, lv)
                except Exception as e:
                    print(f"[YAPI] {sym} hata: {e}", flush=True)
                time.sleep(0.25)
        except Exception as e:
            print(f"[YAPI] dongu hata: {e}", flush=True)
        # bir sonraki 15dk mum kapanisindan 20 sn sonra tekrar
        time.sleep(max(30.0, 900 - (time.time() % 900) + 20))


# ==============================================================================
# MOTOR 3: GUNUN KAZANANI (24s en cok yukselen coin + canli 1G mum grafigi)
# ==============================================================================
WINNER_ARALIGI = 21600        # 6 saatte bir
WINNER_MIN_DEGISIM = 15.0     # 24s en az %15 yukselis yoksa mesaj yok
WINNER_MIN_HACIM = 10_000_000


def winner_loop():
    print("[KAZANAN] Gunun Kazanani motoru basladi.", flush=True)
    time.sleep(60)
    while True:
        try:
            with state_lock:
                adaylar = [dict(tk) for tk in tickers.values()
                           if tk["quote"] == "USDT" and tk["volume24h"] >= WINNER_MIN_HACIM]
            if len(adaylar) < 20:
                time.sleep(30)
                continue
            adaylar.sort(key=lambda x: x.get("chg", 0.0), reverse=True)
            w = adaylar[0]
            if w.get("chg", 0.0) < WINNER_MIN_DEGISIM:
                time.sleep(WINNER_ARALIGI)
                continue
            sym = w["symbol"]
            kl = fetch_klines(sym, "1d", 45)
            if len(kl) < 5:
                time.sleep(300)
                continue
            day_low, day_high, day_open = kl[-1]["low"], kl[-1]["high"], kl[-1]["open"]
            live = w["price"]
            max_move = ((day_high - day_low) / day_low) * 100 if day_low > 0 else 0.0
            msg = (f"🏆 <b>GÜNÜN KAZANANI: {sym}</b>\n\n"
                   f"🔥 24 saat içinde en çok yükselen coin!\n\n"
                   f"📉 <b>Dip Fiyat:</b> {akilli_fiyat(day_low)}\n"
                   f"🚀 <b>Zirve Fiyat:</b> {akilli_fiyat(day_high)}\n"
                   f"📈 <b>Maksimum Yükseliş:</b> <b>+{max_move:.1f}%</b>\n\n"
                   f"💡 <b>Değerlendirme:</b> {sym} gün içerisinde muazzam bir hacim ve fiyat patlaması yaşadı. "
                   f"Pump Dedektör verilerimize göre günün en kârlı hareketi bu coinde gerçekleşti.")
            try:
                png = render_pump_png(sym, "1G CANLI", kl, live, day_open, tur="GUNUN KAZANANI", cizgi="BAS")
            except Exception as e:
                print(f"[KAZANAN] grafik hatasi: {e}", flush=True)
                png = b""
            telegram_send_photo(png, msg)
            time.sleep(WINNER_ARALIGI)
        except Exception as e:
            print(f"[KAZANAN] hata: {e}", flush=True)
            time.sleep(60)


# ==============================================================================
# PIK TAKIBI, GUNUN ZAFERI (21:00 TR), TOP 24 SAAT (23:00 TR) & DUYURULAR (60 SN)
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
        return "🏆 <b>GUNUN ZAFERI (21:00 TR)</b>\n\nSon 24 saatte +%3 ve uzeri pik yapan Spot Pump sinyali henuz olusmadi."
    lines = ["🏆 <b>GUNUN ZAFERI — SPOT PUMP SIRALAMASI (21:00 TR)</b>\n"]
    for i, s in enumerate(ranked[:15], 1):
        lines.append(
            f"{i}. <b>#{s['symbol']}</b> · <b>+{s['peak_pct']:.2f}%</b> | ${fmt_price(s['entry'])} ➡️ ${fmt_price(s['peak_price'])}"
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
        return "📊 <b>TOP 24 SAAT OZETI (23:00 TR)</b>\n\nSon 24 saatte kaydedilen Spot Pump sinyali bulunmuyor."
    lines = ["📊 <b>TOP 24 SAAT SPOT PUMP OZETI (23:00 TR)</b>\n"]
    for i, s in enumerate(ranked[:20], 1):
        lines.append(
            f"{i}. <b>#{s['symbol']}</b> · Pik: <b>+{s['peak_pct']:.2f}%</b> | Anlik: ${fmt_price(s['current'])}"
        )
    return "\n".join(lines)


def translate_binance_title_tr(title):
    tr = title.strip()
    pairs = (
        ("Binance Will List", "Binance Listeliyor:"),
        ("Binance Will Add", "Binance Ekliyor:"),
        ("Binance Will Support", "Binance Destekliyor:"),
        ("Binance Has Completed the", "Binance Tamamladi:"),
        ("Binance Futures Will Launch", "Binance Vadeli Baslatiyor:"),
        ("Token Merge", "Token Birlesmesi (Merge)"),
        ("Contract Swap", "Kontrat Degisimi (Swap)"),
        ("Withdrawals Reopened", "Cekim Islemleri Yeniden Acildi"),
        ("Notice of Removal of Spot Trading Pairs", "Spot Islem Ciftleri Kaldirma (Delist)"),
        ("Binance Will Delist", "Binance Delist Ediyor:"),
        ("Network Upgrade", "Ag Guncellemesi"),
    )
    for eng, tur in pairs:
        tr = tr.replace(eng, tur)
    return tr


def poll_binance_announcements():
    global news_seeded
    catalogs = ((48, "Yeni Listeleme"), (49, "Binance Duyuru"), (161, "Delist / Kaldirma"))
    newly = []
    for cat_id, cat_label in catalogs:
        try:
            url = f"https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query?catalogId={cat_id}&pageNo=1&pageSize=8"
            data = http_get_json(url, timeout=8)
            arts = data.get("data", {}).get("articles", [])
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
            telegram_send_message(f"📰 <b>Binance | {cat_label}</b>\n{title_tr}\n{link}")
    news_seeded = True


# ==============================================================================
# CANLI PIYASA DONGULERI (WEBSOCKET + REST + ZAMANLAYICI)
# ==============================================================================
def process_ticker(sym, price, vol24h, now, chg=0.0):
    if price <= 0 or is_excluded(sym):
        return
    base, quote = extract_base_quote(sym)
    if not base:
        return
    with state_lock:
        tickers[sym] = {
            "symbol": sym,
            "base": base,
            "quote": quote,
            "price": price,
            "volume24h": vol24h,
            "chg": chg,
            "updated_at": now,
        }
    evaluate_pump(sym, price, vol24h, now)
    if quote == "USDT":
        check_structure_break(sym, price, now)


def ws_stream_loop():
    global ws_connected
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
                            process_ticker(
                                item.get("s", ""),
                                float(item.get("c") or 0),
                                float(item.get("q") or 0),
                                now,
                                _chg_from_open(item),
                            )
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


def rest_seed_loop():
    while True:
        try:
            raw = binance_rest_get("/api/v3/ticker/24hr", timeout=10)
            if isinstance(raw, list):
                now = time.time()
                for item in raw:
                    process_ticker(
                        item.get("symbol", ""),
                        float(item.get("lastPrice") or 0),
                        float(item.get("quoteVolume") or 0),
                        now,
                        float(item.get("priceChangePercent") or 0.0),
                    )
                update_signal_peaks()
        except Exception:
            pass
        time.sleep(25 if ws_connected else 5)


def scheduler_and_news_loop():
    last_news_ts = 0.0
    while True:
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
        time.sleep(15)


class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        body = json.dumps({
            "ok": True,
            "ws": ws_connected,
            "pairs": len(tickers),
            "yapi_izlenen": len(struct_levels),
            "engines": ["PUMP", "YAPI", "KAZANAN"],
        }).encode("utf-8")
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        return


def main():
    load_state()
    print(f"[Spot Pump Bot] Baslatiliyor... Port={PORT}, ChatID={CHAT_ID}")
    for fn in (ws_stream_loop, rest_seed_loop, scheduler_and_news_loop, yapi_loop, winner_loop):
        threading.Thread(target=fn, daemon=True).start()

    srv = http.server.HTTPServer(("0.0.0.0", PORT), HealthHandler)
    srv.serve_forever()


if __name__ == "__main__":
    main()
